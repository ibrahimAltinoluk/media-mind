"""Video analizi: kare örnekleme → kare vektörleri, sahneler, video vektörü/etiketleri, zamanlı yüzler."""

from __future__ import annotations

from typing import Callable

import numpy as np
from PIL import Image

from ..media import video
from ..repo import Repo
from . import content, faces as faces_mod, siglip

Progress = Callable[[str, int, int], None]

SCENE_SIM = 0.82        # ardışık kareler bundan az benziyorsa yeni sahne
SCENE_MAX_MS = 60_000   # tek sahne en fazla bu kadar uzun gösterilir
FRAME_W = 320           # sahne şeridi küçük resmi


def frame_thumb(repo: Repo, blake3: str, ms: int):
    return repo.mm / "frames" / blake3[:2] / blake3 / f"{ms}.webp"


def analyze_video(repo: Repo, row, mark: Callable) -> dict:
    """Tek video: sahneler, kare vektörleri, video vektörü + model etiketleri, zamanlı yüzler."""
    src = repo.root / row["fs_path"]
    frames = video.sample_frames(src, row["duration_ms"])
    if not frames:
        raise RuntimeError("Videodan kare alınamadı")
    conn = repo.db
    use_content = siglip.available() and repo.config["analysis"].get("content", True)
    use_faces = faces_mod.available() and repo.config["analysis"].get("faces", True)
    vecs = None
    m = None
    if use_content:
        m = siglip.get()  # seçili kademenin modeli
        vecs = np.concatenate([m.encode_images([im for _, im in frames[i:i + 16]]) for i in range(0, len(frames), 16)])

    # Sahneler: kare vektörleri arasında belirgin değişim ya da süre sınırı
    scenes: list[list[int]] = [[0]]
    for i in range(1, len(frames)):
        cur = scenes[-1]
        change = vecs is not None and float(vecs[i] @ vecs[i - 1]) < (siglip.thresholds(m.version)["scene"] if m else SCENE_SIM)
        too_long = frames[i][0] - frames[cur[0]][0] > SCENE_MAX_MS
        if change or too_long:
            scenes.append([i])
        else:
            cur.append(i)
    dur = row["duration_ms"] or frames[-1][0]

    found_faces = []
    if use_faces:
        fm = faces_mod.get()
        for ms, im in frames:
            w, h = im.size
            for f in fm.analyze(im):
                if f.embedding is not None:
                    found_faces.append((ms, f, w, h))

    blake = row["blake3"]
    for sc in scenes:
        ms, im = frames[sc[0]]
        t = frame_thumb(repo, blake, ms)
        t.parent.mkdir(parents=True, exist_ok=True)
        th = im.copy()
        th.thumbnail((FRAME_W, FRAME_W))
        th.save(t, "WEBP", quality=78)

    with conn:
        conn.execute("DELETE FROM video_scenes WHERE asset_id=?", (row["id"],))
        for i, sc in enumerate(scenes):
            start = frames[sc[0]][0]
            end = frames[scenes[i + 1][0]][0] if i + 1 < len(scenes) else dur
            conn.execute("INSERT INTO video_scenes(asset_id, idx, start_ms, end_ms, key_ms) VALUES (?,?,?,?,?)",
                         (row["id"], i, start, end, start))
        if vecs is not None:
            conn.execute("DELETE FROM video_frames WHERE asset_id=? AND model=?", (row["id"], m.version))
            conn.executemany("INSERT INTO video_frames(asset_id, model, ms, vec) VALUES (?,?,?,?)",
                             [(row["id"], m.version, ms, v.astype(np.float32).tobytes()) for (ms, _), v in zip(frames, vecs)])
            mean = vecs.mean(axis=0)
            mean /= np.linalg.norm(mean)
            conn.execute("INSERT OR REPLACE INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)",
                         (row["id"], m.version, mean.astype(np.float32).tobytes()))
            names, L = content.label_matrix(m.version)
            # Etiketler: kareler arasında en az ~%20'sinde güçlü çıkan etiketler + ortalama vektörün etiketleri
            per = [set(n for n, _ in content.pick_tags(s, names, m.version)) for s in vecs @ L.T]
            counts: dict[str, int] = {}
            for st in per:
                for n in st:
                    counts[n] = counts.get(n, 0) + 1
            tags = dict(content.pick_tags(mean @ L.T, names, m.version))
            for n, c in counts.items():
                if c >= max(2, len(frames) * 0.2):
                    tags.setdefault(n, 3.0)
            content._set_model_tags(conn, row["id"], sorted(tags.items(), key=lambda x: -x[1])[:8], m.version)
            mark(conn, row["id"], "content")
        if use_faces:
            # Elle eklenenler ve kullanıcının ad verdiği yüzler korunur; otomatik bulunanlar yenilenir
            conn.execute("DELETE FROM faces WHERE asset_id=? AND manual=0 AND animal IS NULL AND (person_id IS NULL OR assigned_by='auto')",
                         (row["id"],))
            conn.executemany(
                "INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec, frame_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                [(row["id"], f.bbox[0] / w, f.bbox[1] / h, f.bbox[2] / w, f.bbox[3] / h, f.score, f.quality,
                  f.embedding.tobytes(), ms) for ms, f, w, h in found_faces])
            mark(conn, row["id"], "faces")
        mark(conn, row["id"], "video")
    content._vec_cache.pop(str(repo.root), None)
    return {"frames": len(frames), "scenes": len(scenes), "faces": len(found_faces)}


def run_video(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None) -> int:
    for n, r in enumerate(todo, 1):
        try:
            analyze_video(repo, r, mark)
        except Exception as e:  # noqa: BLE001 - bozuk video analizi durdurmaz
            with repo.db as conn:
                mark(conn, r["id"], "video", f"{type(e).__name__}: {e}")
        if progress:
            progress("video", n, len(todo))
    return len(todo)


def video_info(repo: Repo, asset_id: int) -> dict:
    conn = repo.db
    scenes = [dict(r) for r in conn.execute(
        "SELECT idx, start_ms, end_ms, key_ms FROM video_scenes WHERE asset_id=? ORDER BY idx", (asset_id,))]
    # Kişi zaman çizelgesi: aynı kişinin ardışık karelerdeki görünmeleri birleştirilir
    rows = conn.execute(
        "SELECT f.frame_ms, f.person_id, p.name FROM faces f JOIN persons p ON p.id=f.person_id "
        "WHERE f.asset_id=? AND f.frame_ms IS NOT NULL ORDER BY f.person_id, f.frame_ms", (asset_id,)).fetchall()
    frames = [r[0] for r in conn.execute("SELECT DISTINCT ms FROM video_frames WHERE asset_id=? ORDER BY ms", (asset_id,))]
    gap = (frames[1] - frames[0]) * 2.5 if len(frames) > 1 else 5000
    people: dict[int, dict] = {}
    for r in rows:
        p = people.setdefault(r["person_id"], {"person_id": r["person_id"], "name": r["name"], "segments": []})
        segs = p["segments"]
        if segs and r["frame_ms"] - segs[-1][1] <= gap:
            segs[-1][1] = r["frame_ms"]
        else:
            segs.append([r["frame_ms"], r["frame_ms"]])
    step = int(gap / 2.5) if gap else 2000
    for p in people.values():
        p["segments"] = [[s, e + step] for s, e in p["segments"]]
    unknown = conn.execute("SELECT COUNT(*) FROM faces WHERE asset_id=? AND frame_ms IS NOT NULL AND person_id IS NULL "
                           "AND COALESCE(excluded,'')<>'not_face'", (asset_id,)).fetchone()[0]
    return {"scenes": scenes, "people": sorted(people.values(), key=lambda p: -sum(e - s for s, e in p["segments"])),
            "unknown_faces": unknown, "analyzed": bool(scenes)}


def moments(repo: Repo, asset_id: int, query: str, limit: int = 8) -> list[dict]:
    """Video içinde metne en çok benzeyen anlar."""
    ver = content.effective_version(repo)
    if not siglip.available(ver):
        return []
    rows = repo.db.execute("SELECT ms, vec FROM video_frames WHERE asset_id=? AND model=? ORDER BY ms", (asset_id, ver)).fetchall()
    if not rows:
        return []
    M = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), siglip.profile(ver)["dim"])
    th = siglip.thresholds(ver)
    q = siglip.get(ver).encode_texts([query])[0]
    s = M @ q
    top = float(s.max())
    floor = max(th["sem_abs"], top - th["sem_gap"])
    times = [int(r["ms"]) for r in rows]
    step = (times[-1] - times[0]) / max(1, len(times) - 1) if len(times) > 1 else 2000
    # Eşiği geçen kareleri zaman sırasıyla bitişik aralıklara birleştir
    out: list[dict] = []
    for i in range(len(rows)):
        if s[i] < floor:
            continue
        if out and times[i] - out[-1]["end_ms"] <= step * 1.6:
            out[-1]["end_ms"] = times[i]
            out[-1]["score"] = max(out[-1]["score"], round(float(s[i]), 4))
        else:
            out.append({"ms": times[i], "end_ms": times[i], "score": round(float(s[i]), 4)})
    for m in out:
        m["end_ms"] = int(m["end_ms"] + step)
    return sorted(out, key=lambda m: -m["score"])[:limit]


__all__ = ["analyze_video", "run_video", "video_info", "moments", "frame_thumb", "Image"]
