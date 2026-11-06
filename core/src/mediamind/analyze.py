"""Takibe alma ve analiz hattı (Aşama 1: meta veri, konum, önizleme + algısal hash)."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from .media import dates, exif, geo, thumbs
from .repo import Repo

# Aşama sürümleri: bir aşamanın mantığı değişirse sürümü artırılır, yalnız o aşama yeniden çalışır.
STAGES = {"meta": 1, "geo": 1, "thumbs": 1, "quality": 1, "content": 1, "faces": 1, "video": 1, "transcript": 1, "ocr": 1, "pets": 1}

Progress = Callable[[str, int, int], None]


def stage_version(stage: str) -> int | str:
    """Aşama sürümü. Konuşma dökümünde seçili model kademesi de sürüme girer: kademe değişince
    dökümler yeniden yapılacak işe döner."""
    if stage == "transcript":
        from .ml import tiers

        v = tiers.stage_version("speech")
        return STAGES[stage] if v == 1 else v
    return STAGES[stage]


def track(repo: Repo, rels: Iterable[str] | None = None) -> int:
    """Takipsiz dosyaları takibe alır. rels None ise hepsi; klasör yolu verilirse altındakiler."""
    with repo.lock(), repo.db as conn:
        if rels is None:
            return conn.execute("UPDATE files SET tracked=1 WHERE state='present' AND tracked=0").rowcount
        n = 0
        for rel in rels:
            rel = rel.strip("/")
            n += conn.execute(
                "UPDATE files SET tracked=1 WHERE state='present' AND tracked=0 AND (rel_path=? OR rel_path LIKE ? ESCAPE '\\')",
                (rel, rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"),
            ).rowcount
        return n


def _pending(conn, stage: str, force: bool) -> list:
    rows = conn.execute(
        "SELECT a.*, (SELECT f.fs_path FROM files f WHERE f.asset_id=a.id AND f.state='present' "
        "             ORDER BY f.id LIMIT 1) AS fs_path, "
        "            (SELECT f.mtime_ns FROM files f WHERE f.asset_id=a.id AND f.state='present' "
        "             ORDER BY f.id LIMIT 1) AS mtime_ns "
        "FROM assets a WHERE EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1)"
    ).fetchall()
    out = []
    for r in rows:
        st = json.loads(r["analysis_state"] or "{}")
        if force or st.get(stage) != stage_version(stage):
            out.append(r)
    return out


def _mark(conn, asset_id: int, stage: str, error: str | None = None) -> None:
    row = conn.execute("SELECT analysis_state FROM assets WHERE id=?", (asset_id,)).fetchone()
    st = json.loads(row["analysis_state"] or "{}")
    st[stage] = stage_version(stage)
    errs = st.setdefault("errors", {})
    if error:
        errs[stage] = error[:300]
    else:
        errs.pop(stage, None)
    if not errs:
        st.pop("errors")
    conn.execute("UPDATE assets SET analysis_state=? WHERE id=?", (json.dumps(st), asset_id))


def run_meta(repo: Repo, progress: Progress | None = None, force: bool = False) -> int:
    conn = repo.db
    todo = _pending(conn, "meta", force)
    if not todo:
        return 0
    paths = [repo.root / r["fs_path"] for r in todo]
    done = 0
    chunk = 200
    for i in range(0, len(todo), chunk):
        part = todo[i : i + chunk]
        raws = exif.read_raw([repo.root / r["fs_path"] for r in part])
        with conn:
            for r in part:
                raw = dict(raws.get(repo.root / r["fs_path"]) or {})
                raw["_video"] = r["kind"] == "video"
                norm = exif.normalize(raw, r["kind"])
                taken_at, tz, src = dates.infer(raw, os.path.basename(r["fs_path"]), r["mtime_ns"])
                conn.execute(
                    "UPDATE assets SET mime=?, width=?, height=?, duration_ms=?, lat=?, lon=?, alt=?, "
                    "camera_make=?, camera_model=?, lens=?, orientation=?, video_codec=?, bitrate=?, "
                    "taken_at=?, tz_offset=?, taken_at_source=? WHERE id=?",
                    (norm["mime"], norm["width"], norm["height"], norm["duration_ms"], norm["lat"], norm["lon"],
                     norm["alt"], norm["camera_make"], norm["camera_model"], norm["lens"], norm["orientation"],
                     norm["video_codec"], norm["bitrate"], taken_at, tz, src, r["id"]),
                )
                _mark(conn, r["id"], "meta", None if raws.get(repo.root / r["fs_path"]) else "meta veri okunamadı")
        with conn:
            apply_edits(conn, [r["id"] for r in part])
        done += len(part)
        if progress:
            progress("meta", done, len(paths))
    return done


def apply_edits(conn, asset_ids: list[int] | None = None) -> None:
    """Kullanıcının tarih/konum düzeltmeleri yeniden okumada (meta) ezilmesin."""
    q = "SELECT * FROM asset_edits"
    rows = conn.execute(q).fetchall() if asset_ids is None else conn.execute(
        f"SELECT * FROM asset_edits WHERE asset_id IN ({','.join('?' * len(asset_ids)) or 'NULL'})", asset_ids).fetchall()
    for e in rows:
        if e["taken_at"]:
            conn.execute("UPDATE assets SET taken_at=?, taken_at_source='user' WHERE id=?", (e["taken_at"], e["asset_id"]))
        if e["lat"] is not None:
            conn.execute("UPDATE assets SET lat=?, lon=? WHERE id=?", (e["lat"], e["lon"], e["asset_id"]))


def run_geo(repo: Repo, progress: Progress | None = None, force: bool = False) -> int:
    conn = repo.db
    geo.fix_place_names(conn)  # eski ASCII adları düzelt (hızlı)
    todo = [r for r in _pending(conn, "geo", force) if json.loads(r["analysis_state"]).get("meta")]
    with_coords = [r for r in todo if r["lat"] is not None and r["lon"] is not None]
    results = geo.lookup([(r["lat"], r["lon"]) for r in with_coords]) if with_coords else []
    with conn:
        for r, g in zip(with_coords, results):
            conn.execute(
                "INSERT INTO places(name, admin1, admin2, country_code) VALUES (?,?,?,?) "
                "ON CONFLICT(name, admin1, country_code) DO NOTHING",
                (g["name"], g["admin1"], g["admin2"], g["cc"]),
            )
            pid = conn.execute(
                "SELECT id FROM places WHERE name=? AND admin1 IS ? AND country_code IS ?",
                (g["name"], g["admin1"], g["cc"]),
            ).fetchone()["id"]
            conn.execute("UPDATE assets SET place_id=? WHERE id=?", (pid, r["id"]))
        for r in todo:
            _mark(conn, r["id"], "geo")
    if with_coords:
        geo.fix_place_names(conn)  # yeni yerlerin arama biçimleri
    if progress:
        progress("geo", len(todo), len(todo))
    return len(todo)


def run_quality(repo: Repo, progress: Progress | None = None, force: bool = False) -> int:
    """Parlaklık ve kontrast (küçük önizlemeden): kazara çekilmiş karanlık/tek renkli fotoğraf önerileri için."""
    from PIL import Image, ImageStat

    conn = repo.db
    todo = [r for r in _pending(conn, "quality", force) if json.loads(r["analysis_state"]).get("thumbs")]
    batch = []
    for n, r in enumerate(todo, 1):
        luma = contrast = None
        p = repo.thumb_path(r["blake3"], "s")
        try:
            with Image.open(p) as im:
                st = ImageStat.Stat(im.convert("L"))
                luma, contrast = float(st.mean[0]), float(st.stddev[0])
        except OSError:
            pass
        batch.append((luma, contrast, r["id"]))
        if len(batch) >= 300 or n == len(todo):
            with conn:
                for lu, co, aid in batch:
                    conn.execute("UPDATE assets SET luma=?, contrast=? WHERE id=?", (lu, co, aid))
                    _mark(conn, aid, "quality")
            batch = []
            if progress:
                progress("quality", n, len(todo))
    return len(todo)


def run_thumbs(repo: Repo, progress: Progress | None = None, force: bool = False, workers: int | None = None) -> int:
    conn = repo.db
    todo = [r for r in _pending(conn, "thumbs", force) if json.loads(r["analysis_state"]).get("meta")]
    if not todo:
        return 0

    def work(r):
        try:
            res = thumbs.render(
                repo.root / r["fs_path"], r["kind"], lambda s: repo.thumb_path(r["blake3"], s), r["duration_ms"]
            )
            return r, res, None
        except Exception as e:  # bozuk dosya, desteklenmeyen codec vb.
            return r, None, f"{type(e).__name__}: {e}"

    done = 0
    with ThreadPoolExecutor(max_workers=workers or min(6, os.cpu_count() or 4)) as ex:
        batch = []
        for r, res, err in ex.map(work, todo):
            batch.append((r, res, err))
            done += 1
            if len(batch) >= 100 or done == len(todo):
                with conn:
                    for br, bres, berr in batch:
                        if bres:
                            conn.execute(
                                "UPDATE assets SET phash=?, dhash=?, blur_score=?, "
                                "width=COALESCE(width, ?), height=COALESCE(height, ?) WHERE id=?",
                                (bres.phash, bres.dhash, bres.blur, bres.width, bres.height, br["id"]),
                            )
                        _mark(conn, br["id"], "thumbs", berr)
                batch = []
            if progress:
                progress("thumbs", done, len(todo))
    return done


def _smart_enabled(repo: Repo, stage: str) -> bool:
    from .ml import faces as faces_mod
    from .ml import siglip

    cfg = repo.config.get("analysis", {})
    if stage == "content":
        return bool(cfg.get("content", True)) and siglip.available()
    if stage == "faces":
        return bool(cfg.get("faces", True)) and faces_mod.available()
    if stage == "pets":
        from .ml import pets

        return bool(cfg.get("pets", True)) and pets.available()
    if stage == "ocr":
        from .ml import ocr

        return bool(cfg.get("ocr", True)) and ocr.available()
    if stage == "transcript":
        from .ml import transcribe

        # Yavaş olduğu için varsayılan kapalı (repo ayarı: analysis.transcripts)
        return bool(cfg.get("transcripts", False)) and transcribe.available() and exif.have_ffmpeg()
    # video: sahneler + kare bazlı içerik/yüz; en az bir model ve ffmpeg gerekir
    return (bool(cfg.get("video", True)) and exif.have_ffmpeg()
            and (_smart_enabled(repo, "content") or _smart_enabled(repo, "faces")))


def _smart_todo(repo: Repo, force: bool = False) -> dict[str, list]:
    """Akıllı analiz aşamalarında işlenecek öğeler (önizlemesi hazır olanlar)."""
    conn = repo.db

    def ready(r) -> bool:
        st = json.loads(r["analysis_state"])
        return bool(st.get("thumbs")) and "thumbs" not in st.get("errors", {})

    out: dict[str, list] = {}
    video_on = _smart_enabled(repo, "video")
    if video_on:
        out["video"] = [r for r in _pending(conn, "video", force) if r["kind"] == "video" and ready(r)]
    if _smart_enabled(repo, "transcript"):
        out["transcript"] = [r for r in _pending(conn, "transcript", force) if r["kind"] == "video" and ready(r)]
    if _smart_enabled(repo, "ocr"):
        out["ocr"] = [r for r in _pending(conn, "ocr", force) if r["kind"] == "photo" and ready(r)]
    if _smart_enabled(repo, "pets"):
        out["pets"] = [r for r in _pending(conn, "pets", force) if r["kind"] == "photo" and ready(r)]
    for stage in ("content", "faces"):
        if _smart_enabled(repo, stage):
            # Videolar video aşamasında (çok kareli) işlenir
            out[stage] = [r for r in _pending(conn, stage, force) if ready(r) and not (video_on and r["kind"] == "video")]
    return out


def pending_work(repo: Repo) -> dict[str, int]:
    """Yarım kalmış ya da hiç yapılmamış analiz işi (aşama → öğe sayısı). Boşsa her şey güncel."""
    conn = repo.db
    out = {stage: len(_pending(conn, stage, False)) for stage in ("meta", "geo", "thumbs")}
    out["quality"] = len([r for r in _pending(conn, "quality", False) if json.loads(r["analysis_state"]).get("thumbs")])
    out.update({k: len(v) for k, v in _smart_todo(repo).items()})
    return {k: v for k, v in out.items() if v}


def run_smart(repo: Repo, progress: Progress | None = None, force: bool = False) -> dict[str, int]:
    """Video (sahne + kareler), içerik (SigLIP2) ve yüz (SCRFD+ArcFace) aşamaları; önizlemesi hazır asset'ler üzerinde."""
    from .ml import content, people, videoan

    out = {"video": 0, "transcript": 0, "content": 0, "faces": 0}
    todo = _smart_todo(repo, force)
    if "video" in todo:
        out["video"] = videoan.run_video(repo, todo["video"], _mark, progress)
    if "transcript" in todo:
        from .ml import transcribe

        out["transcript"] = transcribe.run_transcripts(repo, todo["transcript"], _mark, progress)
    if "content" in todo:
        out["content"] = content.run_content(repo, todo["content"], _mark, progress)
    if "faces" in todo:
        out["faces"] = content.run_faces(repo, todo["faces"], _mark, progress)
    if "ocr" in todo:
        from .ml import ocr

        out["ocr"] = ocr.run_ocr(repo, todo["ocr"], _mark, progress)
    if "pets" in todo:
        from .ml import pets

        out["pets"] = pets.run_pets(repo, todo["pets"], _mark, progress)
    if out["faces"] or out["video"]:
        people.recluster(repo)
    if (out["content"] or out["video"]) and not any(_smart_todo(repo).get(k) for k in ("content", "video")):
        content.finish_switch(repo)  # kademe değiştiyse ve yeni modelle her şey bittiyse eski vektörleri bırak
    return out


def analyze(repo: Repo, progress: Progress | None = None, force: bool = False, smart: bool = True) -> dict[str, int]:
    """Repo kilidi yalnız kısa meta/konum yazımında tutulur; önizleme ve ML aşamaları öğe başına yazar ve
    uzun sürebildiği için kilidi tutmaz (bu sırada tarama, plan, kare/klip işleri bekletilmez)."""
    with repo.lock():
        res = {"meta": run_meta(repo, progress, force), "geo": run_geo(repo, progress, force)}
    res["thumbs"] = run_thumbs(repo, progress, force)
    res["quality"] = run_quality(repo, progress, force)
    if smart:
        res.update(run_smart(repo, progress, force))
    return res
