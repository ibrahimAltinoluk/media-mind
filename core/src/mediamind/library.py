"""Kütüphane özellikleri: temizlik önerileri, anılar, albümler, tarih/konum düzeltme, dışa aktarma, harita noktaları.

Hepsi yalnızca MediaMind veritabanı üzerinde çalışır; dosyalara dokunan tek işlem dışa aktarmadır (kopya üretir).
Silme her zaman plan + onay yolundan gider.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .media import geo, thumbs
from .repo import Repo

Progress = Callable[[str, int, int], None]

# Kütüphane listeleriyle aynı sütunlar (arayüz aynı kartları kullanır)
ASSET_COLS = (
    "f.id AS file_id, f.rel_path, f.tracked, f.size, a.id AS asset_id, a.blake3, a.kind, a.mime, a.width, a.height, "
    "a.duration_ms, a.taken_at, a.taken_at_source, a.camera_model, a.rating, a.favorite, a.lat, a.lon, "
    "p.name AS place, p.admin1 AS region, p.country_code, 1 AS copies, 0 AS in_dup"
)
BASE = ("FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
        "WHERE f.state='present' AND f.tracked=1")
ONE_FILE = "AND f.id=(SELECT MIN(f2.id) FROM files f2 WHERE f2.asset_id=a.id AND f2.state='present' AND f2.tracked=1)"

# ------------------------------------------------------------------ temizlik önerileri

SCREENSHOT_NAMES = ("screenshot", "screen shot", "ekran resmi", "ekran görüntüsü", "ekran goruntusu", "scr_", "screen_")

CLEANUP = {
    "screenshots": ("Ekran görüntüleri", "Telefon ve bilgisayardan alınmış ekran görüntüleri. Çoğu bir kez bakılıp unutulur."),
    "accidental": ("Kazara çekilmiş olabilir", "Neredeyse tamamen karanlık ya da tek renk fotoğraflar (cep, yer, kapalı lens)."),
    "blurry": ("Bulanık fotoğraflar", "Odak dışı ya da sarsıntılı çekimler."),
    "short_videos": ("Çok kısa videolar", "2 saniyeden kısa videolar; çoğu yanlışlıkla başlatılmış kayıttır."),
    "compressible": ("Küçültülebilecek videolar", "Eski biçimde (H.264 vb.) kaydedilmiş, 100 MB'tan büyük videolar. Küçültünce çoğu zaman yarı yarıya yer açılır."),
    "large_videos": ("Çok yer kaplayan videolar", "300 MB'tan büyük videolar. Silmek yerine gözden geçirmek isteyebilirsin."),
}
BLUR_MAX = 18.0       # Laplacian varyansı (512 px gri); bunun altı bulanık sayılır
DARK_MAX = 14.0       # ortalama parlaklık (0–255)
FLAT_MAX = 5.0        # parlaklık sapması: tek renk


def cleanup(repo: Repo, limit: int = 600) -> list[dict]:
    """Silinebilecek öğe önerileri (kategori başına). Favoriler, puanlılar, albümdekiler ve "kalsın" denenler hariç."""
    conn = repo.db
    keep = ("AND a.favorite=0 AND a.rating=0 AND a.id NOT IN (SELECT asset_id FROM album_items) "
            "AND a.id NOT IN (SELECT asset_id FROM cleanup_keep WHERE category=?)")
    names = " OR ".join(["lower(f.rel_path) LIKE ?"] * len(SCREENSHOT_NAMES))
    conds = {
        # Dosya adı açıkça ekran görüntüsü diyorsa; ya da içerik modeli öyle görüyor VE kamera bilgisi yoksa
        # (gerçek fotoğrafların kamera bilgisi olur; modelin tek başına yanılma payı yüksek)
        "screenshots": (f"a.kind='photo' AND ({names} OR (a.camera_model IS NULL AND a.id IN (SELECT at.asset_id FROM asset_tags at "
                        "JOIN tags t ON t.id=at.tag_id WHERE t.name IN ('ekran görüntüsü','screenshot'))))",
                        [f"%{n}%" for n in SCREENSHOT_NAMES]),
        "accidental": ("a.kind='photo' AND (a.luma < ? OR a.contrast < ?)", [DARK_MAX, FLAT_MAX]),
        "blurry": ("a.kind='photo' AND a.blur_score IS NOT NULL AND a.blur_score < ? AND COALESCE(a.contrast, 99) >= ? "
                   "AND COALESCE(a.luma, 99) >= ?", [BLUR_MAX, FLAT_MAX, DARK_MAX]),
        "short_videos": ("a.kind='video' AND a.duration_ms IS NOT NULL AND a.duration_ms < 2000", []),
        "compressible": ("a.kind='video' AND a.size > ? AND lower(COALESCE(a.video_codec,'')) NOT IN ('hevc','hvc1','hev1','av1','av01')",
                         [100 * 1024 * 1024]),
        "large_videos": ("a.kind='video' AND a.size > ?", [300 * 1024 * 1024]),
    }
    out = []
    seen: set[int] = set()  # bir öğe yalnız ilk uyduğu kategoride gösterilir
    for key, (cond, params) in conds.items():
        rows = conn.execute(f"SELECT {ASSET_COLS} {BASE} {ONE_FILE} AND {cond} {keep} "
                            f"ORDER BY {'a.size DESC' if key in ('large_videos', 'compressible') else 'a.taken_at DESC'}",
                            [*params, key]).fetchall()
        items = [dict(r) for r in rows if r["asset_id"] not in seen]
        seen.update(i["asset_id"] for i in items)
        if items:
            title, hint = CLEANUP[key]
            out.append({"key": key, "title": title, "hint": hint, "count": len(items),
                        "bytes": sum(i["size"] for i in items), "items": items[:limit]})
    return out


def cleanup_keep(repo: Repo, category: str, asset_ids: Iterable[int]) -> int:
    ids = list(asset_ids)
    with repo.lock(), repo.db as conn:
        conn.executemany("INSERT OR IGNORE INTO cleanup_keep(asset_id, category) VALUES (?,?)", [(i, category) for i in ids])
    return len(ids)


# ------------------------------------------------------------------ anılar

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _fmt_day(d: date) -> str:
    return f"{d.day} {AYLAR[d.month - 1]} {d.year}"


def memories(repo: Repo, today: date | None = None, limit: int = 40) -> list[dict]:
    """Kendiliğinden toplanmış anılar: "geçmiş yıllarda bugün", geziler ve kalabalık günler."""
    today = today or date.today()
    conn = repo.db
    rows = conn.execute(
        f"SELECT a.id, a.blake3, a.kind, substr(a.taken_at,1,10) AS day, a.place_id, p.name AS place, p.admin1 "
        f"{BASE} {ONE_FILE} AND a.taken_at IS NOT NULL AND COALESCE(a.taken_at_source,'') NOT IN ('mtime') "
        f"ORDER BY a.taken_at").fetchall()
    if not rows:
        return []
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    # "Ev": en çok farklı günde fotoğraf çekilen yer (gezilerde çok fotoğraf çekilir, gün sayısı az olur)
    place_days = Counter(pid for pid, _day in {(r["place_id"], r["day"]) for r in rows if r["place_id"]})
    home = place_days.most_common(1)[0][0] if place_days else None

    def cover(items: list) -> list[str]:
        photos = [i["blake3"] for i in items if i["kind"] == "photo"] or [i["blake3"] for i in items]
        step = max(1, len(photos) // 4)
        return photos[::step][:4]

    def main_place(items: list) -> str | None:
        c = Counter(i["place"] for i in items if i["place"])
        return c.most_common(1)[0][0] if c else None

    out: list[dict] = []
    # 1) Geçmiş yıllarda bugün (±3 gün)
    for back in range(1, 40):
        y = today.year - back
        try:
            center = today.replace(year=y)
        except ValueError:  # 29 Şubat
            center = today.replace(year=y, day=28)
        days = [(center + timedelta(days=k)).isoformat() for k in range(-3, 4)]
        items = [i for d in days for i in by_day.get(d, [])]
        if len(items) >= 3:
            pl = main_place(items)
            out.append({"key": f"otd-{y}", "kind": "on_this_day", "title": f"{back} yıl önce bugün",
                        "subtitle": ", ".join(filter(None, [_fmt_day(center), pl])), "count": len(items),
                        "date_from": days[0], "date_to": days[-1], "cover": cover(items), "sort": 1e9 - back})

    # 2) Geziler (evden uzakta, art arda günler) ve kalabalık günler
    day_keys = sorted(by_day)
    groups: list[list[str]] = []
    for d in day_keys:
        if groups and (date.fromisoformat(d) - date.fromisoformat(groups[-1][-1])).days <= 1:
            groups[-1].append(d)
        else:
            groups.append([d])
    for g in groups:
        items = [i for d in g for i in by_day[d]]
        away = [i for i in items if i["place_id"] and i["place_id"] != home]
        d0, d1 = date.fromisoformat(g[0]), date.fromisoformat(g[-1])
        if len(g) >= 2 and len(items) >= 10 and len(away) >= len(items) * 0.6:
            pl = main_place(away) or "Gezi"
            when = f"{AYLAR[d0.month - 1]} {d0.year}"
            out.append({"key": f"trip-{g[0]}", "kind": "trip", "title": f"{pl}, {when}",
                        "subtitle": f"{_fmt_day(d0)} – {_fmt_day(d1)} · {len(g)} gün", "count": len(items),
                        "date_from": g[0], "date_to": g[-1], "cover": cover(items), "sort": d1.toordinal()})
        else:
            for d in g:
                its = by_day[d]
                if len(its) >= 25:
                    dd = date.fromisoformat(d)
                    pl = main_place(its)
                    out.append({"key": f"day-{d}", "kind": "day", "title": pl or _fmt_day(dd),
                                "subtitle": _fmt_day(dd) if pl else f"{len(its)} öğe", "count": len(its),
                                "date_from": d, "date_to": d, "cover": cover(its), "sort": dd.toordinal()})
    out.sort(key=lambda m: -m["sort"])
    for m in out:
        m.pop("sort")
    return out[:limit]


# ------------------------------------------------------------------ albümler

def albums(repo: Repo) -> list[dict]:
    conn = repo.db
    out = []
    for a in conn.execute("SELECT * FROM albums ORDER BY name COLLATE NOCASE").fetchall():
        d = dict(a)
        d["rules"] = json.loads(a["rules"]) if a["rules"] else None
        if a["kind"] == "manual":
            d["count"] = conn.execute(
                "SELECT COUNT(*) FROM album_items ai WHERE ai.album_id=? AND EXISTS (SELECT 1 FROM files f "
                "WHERE f.asset_id=ai.asset_id AND f.state='present')", (a["id"],)).fetchone()[0]
            cov = conn.execute(
                "SELECT a.blake3 FROM assets a WHERE a.id=COALESCE(?, (SELECT ai.asset_id FROM album_items ai WHERE "
                "ai.album_id=? ORDER BY ai.added_at DESC LIMIT 1))", (a["cover_asset_id"], a["id"])).fetchone()
            d["cover"] = cov["blake3"] if cov else None
        else:
            d["count"], d["cover"] = None, None  # akıllı albüm: sayı süzgeçle hesaplanır (arayüz sorar)
        out.append(d)
    return out


def album_create(repo: Repo, name: str, kind: str = "manual", rules: dict | None = None) -> int:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Albüm adı boş olamaz")
    if kind not in ("manual", "smart"):
        raise ValueError("Albüm türü manual ya da smart olmalı")
    with repo.lock(), repo.db as conn:
        return conn.execute("INSERT INTO albums(name, kind, rules) VALUES (?,?,?)",
                            (name, kind, json.dumps(rules or {}, ensure_ascii=False) if kind == "smart" else None)).lastrowid


def album_update(repo: Repo, album_id: int, name: str | None = None, rules: dict | None = None) -> None:
    with repo.lock(), repo.db as conn:
        if name is not None:
            if not name.strip():
                raise ValueError("Albüm adı boş olamaz")
            conn.execute("UPDATE albums SET name=? WHERE id=?", (name.strip()[:80], album_id))
        if rules is not None:
            conn.execute("UPDATE albums SET rules=? WHERE id=? AND kind='smart'", (json.dumps(rules, ensure_ascii=False), album_id))


def album_delete(repo: Repo, album_id: int) -> None:
    with repo.lock(), repo.db as conn:
        conn.execute("DELETE FROM albums WHERE id=?", (album_id,))


def album_add(repo: Repo, album_id: int, asset_ids: Iterable[int]) -> int:
    with repo.lock(), repo.db as conn:
        if conn.execute("SELECT kind FROM albums WHERE id=?", (album_id,)).fetchone()["kind"] != "manual":
            raise ValueError("Akıllı albüme elle öğe eklenemez")
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO album_items(album_id, asset_id) VALUES (?,?)", [(album_id, a) for a in asset_ids])
        return conn.total_changes - before


def album_remove(repo: Repo, album_id: int, asset_ids: Iterable[int]) -> int:
    ids = list(asset_ids)
    with repo.lock(), repo.db as conn:
        return conn.execute(f"DELETE FROM album_items WHERE album_id=? AND asset_id IN ({','.join('?' * len(ids)) or 'NULL'})",
                            [album_id, *ids]).rowcount


def album_get(repo: Repo, album_id: int):
    a = repo.db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if a is None:
        raise ValueError("Albüm bulunamadı")
    return a


# ------------------------------------------------------------------ tarih / konum düzeltme

def _ensure_edit_row(conn, asset_id: int) -> None:
    if conn.execute("SELECT 1 FROM asset_edits WHERE asset_id=?", (asset_id,)).fetchone():
        return
    a = conn.execute("SELECT taken_at, taken_at_source, lat, lon, place_id FROM assets WHERE id=?", (asset_id,)).fetchone()
    conn.execute("INSERT INTO asset_edits(asset_id, orig_taken_at, orig_taken_at_source, orig_lat, orig_lon, orig_place_id) "
                 "VALUES (?,?,?,?,?,?)", (asset_id, a["taken_at"], a["taken_at_source"], a["lat"], a["lon"], a["place_id"]))


def set_dates(repo: Repo, asset_ids: list[int], shift_seconds: int | None = None, value: str | None = None) -> int:
    """Tarihi kaydır (shift_seconds) ya da hepsine aynı tarihi ver (value, ISO). Yalnız MediaMind kaydında; geri alınabilir."""
    if (shift_seconds is None) == (value is None):
        raise ValueError("Kaydırma ya da tarih değerinden yalnızca biri verilmeli")
    if value is not None:
        value = datetime.fromisoformat(value).replace(microsecond=0).isoformat()
    n = 0
    with repo.lock(), repo.db as conn:
        for aid in asset_ids:
            cur = conn.execute("SELECT taken_at FROM assets WHERE id=?", (aid,)).fetchone()
            if cur is None:
                continue
            if shift_seconds is not None:
                if not cur["taken_at"]:
                    continue
                new = (datetime.fromisoformat(cur["taken_at"]) + timedelta(seconds=shift_seconds)).isoformat()
            else:
                new = value
            _ensure_edit_row(conn, aid)
            conn.execute("UPDATE asset_edits SET taken_at=?, edited_at=strftime('%Y-%m-%dT%H:%M:%S','now','localtime') "
                         "WHERE asset_id=?", (new, aid))
            conn.execute("UPDATE assets SET taken_at=?, taken_at_source='user' WHERE id=?", (new, aid))
            n += 1
    return n


def set_locations(repo: Repo, items: list[tuple[int, float, float]]) -> int:
    """Her öğeye kendi konumu (asset_id, lat, lon). Yalnız MediaMind kaydında; geri alınabilir."""
    for _, lat, lon in items:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Geçersiz koordinat")
    if not items:
        return 0
    gs = geo.lookup([(lat, lon) for _, lat, lon in items])
    with repo.lock(), repo.db as conn:
        pids: dict[tuple, int] = {}
        for (aid, lat, lon), g in zip(items, gs):
            key = (g["name"], g["admin1"], g["cc"])
            if key not in pids:
                conn.execute("INSERT INTO places(name, admin1, admin2, country_code) VALUES (?,?,?,?) "
                             "ON CONFLICT(name, admin1, country_code) DO NOTHING", (g["name"], g["admin1"], g["admin2"], g["cc"]))
                pids[key] = conn.execute("SELECT id FROM places WHERE name=? AND admin1 IS ? AND country_code IS ?", key).fetchone()["id"]
            _ensure_edit_row(conn, aid)
            conn.execute("UPDATE asset_edits SET lat=?, lon=?, edited_at=strftime('%Y-%m-%dT%H:%M:%S','now','localtime') "
                         "WHERE asset_id=?", (lat, lon, aid))
            conn.execute("UPDATE assets SET lat=?, lon=?, place_id=? WHERE id=?", (lat, lon, pids[key], aid))
    geo.fix_place_names(repo.db)
    return len(items)


def set_location(repo: Repo, asset_ids: list[int], lat: float, lon: float) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Geçersiz koordinat")
    g = geo.lookup([(lat, lon)])[0]
    set_locations(repo, [(aid, lat, lon) for aid in asset_ids])
    return {"updated": len(asset_ids), "place": g["name"], "region": g["admin1"]}


def write_location_to_files(repo: Repo, asset_ids: list[int], progress: Callable[[str, int, int], None] | None = None) -> dict:
    """Öğelerin MediaMind'daki konumunu özgün dosyalara yazar (exiftool, yerinde; tarihler ve diğer meta korunur).

    Dosya içeriği değiştiği için içerik özeti (blake3) yenilenir: öğe kimliği, yüzler, etiketler, albümler aynı kalır;
    önizlemeler yeni özetin adına taşınır, eski özet takma ad olarak saklanır."""
    from . import scan as scan_mod
    from .media import writemeta

    out = {"written": 0, "skipped": 0, "failed": 0, "errors": []}
    if not writemeta.available():
        out["errors"].append("exiftool bulunamadı")
        out["failed"] = len(asset_ids)
        return out
    for i, aid in enumerate(asset_ids):
        if progress:
            progress("write", i, len(asset_ids))
        a = repo.db.execute("SELECT id, blake3, lat, lon FROM assets WHERE id=?", (aid,)).fetchone()
        if a is None:
            continue
        files = repo.db.execute("SELECT id, rel_path, fs_path FROM files WHERE asset_id=? AND state='present'", (aid,)).fetchall()
        targets = [f for f in files if writemeta.writable(Path(f["fs_path"]))]
        if not targets:
            out["skipped"] += 1
            continue
        with repo.lock():
            done: list[tuple[int, str, int, int]] = []  # (dosya no, yeni özet, boyut, mtime_ns)
            for f in targets:
                path = repo.root / f["fs_path"]
                try:
                    cur = writemeta.read_gps(path)
                    if a["lat"] is not None and cur[0] is not None and abs(cur[0] - a["lat"]) < 1e-6 and abs(cur[1] - a["lon"]) < 1e-6:
                        continue  # dosyada zaten bu konum var
                    if a["lat"] is None and cur[0] is None:
                        continue
                    writemeta.write_gps(path, a["lat"], a["lon"])
                except (writemeta.WriteError, OSError, subprocess.SubprocessError) as e:
                    out["failed"] += 1
                    out["errors"].append(f"{f['rel_path']}: {e}")
                    continue
                st = path.stat()
                done.append((f["id"], scan_mod.hash_file(path), st.st_size, st.st_mtime_ns))
            if not done:
                continue
            _rehash_asset(repo, a["id"], a["blake3"], done)
            out["written"] += 1
    if progress:
        progress("write", len(asset_ids), len(asset_ids))
    return out


def _rehash_asset(repo: Repo, asset_id: int, old: str, done: list[tuple[int, str, int, int]]) -> None:
    """Yazılan dosyaların yeni özetini kaydeder; önizlemeleri yeni ada taşır. (Kilit çağıran tarafta.)"""
    new = done[0][1]
    clash = repo.db.execute("SELECT id FROM assets WHERE blake3=? AND id<>?", (new, asset_id)).fetchone()
    with repo.db as conn:
        for fid, h, size, mtime in done:
            if h == new and clash is None:
                conn.execute("UPDATE files SET size=?, mtime_ns=? WHERE id=?", (size, mtime, fid))
            # Farklı çıkan kopya (beklenmez) ya da çakışma: kayıt eski kalır, sonraki tarama doğru öğeye bağlar
        if clash is not None or new == old:
            return
        conn.execute("UPDATE assets SET blake3=?, size=? WHERE id=?", (new, done[0][2], asset_id))
        conn.execute("INSERT OR REPLACE INTO asset_aliases(blake3, asset_id) VALUES (?,?)", (old, asset_id))
        conn.execute("DELETE FROM asset_aliases WHERE blake3=?", (new,))
    _move_derived(repo, old, new)


def _move_derived(repo: Repo, old: str, new: str) -> None:
    def mv(src: Path, dst: Path) -> None:
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)

    for size in ("s", "m", "l"):
        mv(repo.thumb_path(old, size), repo.thumb_path(new, size))
    mv(repo.mm / "frames" / old[:2] / old, repo.mm / "frames" / new[:2] / new)
    mv(repo.mm / "proxies" / old[:2] / f"{old}.mp4", repo.mm / "proxies" / new[:2] / f"{new}.mp4")
    mv(repo.mm / "cache" / "full" / f"{old}.jpg", repo.mm / "cache" / "full" / f"{new}.jpg")
    faces = repo.mm / "faces"
    if faces.is_dir():  # yüz kırpıntıları istendiğinde yeniden üretilir
        for p in faces.glob(f"*_{old[:10]}_*.webp"):
            p.unlink(missing_ok=True)


def resolve_blake(repo: Repo, blake: str) -> str:
    """Eski (dosyaya konum yazılmadan önceki) özet verilirse öğenin güncel özetini döndürür."""
    r = repo.db.execute("SELECT a.blake3 FROM asset_aliases x JOIN assets a ON a.id=x.asset_id WHERE x.blake3=?", (blake,)).fetchone()
    return r["blake3"] if r else blake


def location_edit_ids(repo: Repo, asset_ids: list[int]) -> list[int]:
    """Konumu düzeltilmiş olanlar (geri almada dosyaya da yazılacaklar)."""
    if not asset_ids:
        return []
    q = ",".join("?" * len(asset_ids))
    return [r[0] for r in repo.db.execute(
        f"SELECT asset_id FROM asset_edits WHERE asset_id IN ({q}) AND lat IS NOT NULL", asset_ids)]


def revert_edits(repo: Repo, asset_ids: list[int]) -> int:
    n = 0
    with repo.lock(), repo.db as conn:
        for aid in asset_ids:
            e = conn.execute("SELECT * FROM asset_edits WHERE asset_id=?", (aid,)).fetchone()
            if e is None:
                continue
            conn.execute("UPDATE assets SET taken_at=?, taken_at_source=?, lat=?, lon=?, place_id=? WHERE id=?",
                         (e["orig_taken_at"], e["orig_taken_at_source"], e["orig_lat"], e["orig_lon"], e["orig_place_id"], aid))
            conn.execute("DELETE FROM asset_edits WHERE asset_id=?", (aid,))
            n += 1
    return n


# ------------------------------------------------------------------ dışa aktarma

EXPORT_SIZES = {"original": None, "large": 2048, "small": 1280}


def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    i = 1
    while True:
        c = dst.with_name(f"{dst.stem}-{i}{dst.suffix}")
        if not c.exists():
            return c
        i += 1


def export(repo: Repo, file_ids: list[int], dest: Path, size: str = "original", strip_location: bool = False,
           progress: Progress | None = None) -> dict:
    """Seçilen dosyaların kopyalarını dest klasörüne yazar. Orijinallere dokunulmaz.

    size: original | large (2048 px) | small (1280 px) — küçültülenler JPEG olur (videolar her zaman olduğu gibi kopyalanır).
    strip_location: kopyalardaki GPS bilgisi silinir (exiftool).
    """
    if size not in EXPORT_SIZES:
        raise ValueError("Geçersiz boyut")
    dest = Path(dest).expanduser()
    if not dest.is_absolute():
        raise ValueError("Hedef klasör tam yol olmalı")
    dest.mkdir(parents=True, exist_ok=True)
    rows = repo.db.execute(
        f"SELECT f.id, f.rel_path, f.fs_path, a.kind FROM files f JOIN assets a ON a.id=f.asset_id "
        f"WHERE f.state='present' AND f.id IN ({','.join('?' * len(file_ids)) or 'NULL'})", file_ids).fetchall()
    written, failed = [], []
    for n, r in enumerate(rows, 1):
        src = repo.root / r["fs_path"]
        try:
            px = EXPORT_SIZES[size]
            if px is None or r["kind"] == "video":
                out = _unique(dest / Path(r["rel_path"]).name)
                shutil.copy2(src, out)
            else:
                im = thumbs.open_visual(src, r["kind"], full=True)
                im.thumbnail((px, px))
                if im.mode != "RGB":
                    im = im.convert("RGB")
                out = _unique(dest / (Path(r["rel_path"]).stem + ".jpg"))
                im.save(out, "JPEG", quality=90)
                # Tarih, kamera vb. bilgileri kopyaya taşı (yön zaten uygulandı)
                subprocess.run(["exiftool", "-q", "-q", "-overwrite_original", "-TagsFromFile", str(src), "-all:all",
                                "-Orientation=", "-ThumbnailImage=", str(out)], capture_output=True, timeout=60)
            if strip_location:
                subprocess.run(["exiftool", "-q", "-q", "-overwrite_original", "-gps:all=", "-XMP:GPS*=",
                                "-Keys:GPSCoordinates=", "-UserData:GPSCoordinates=", str(out)], capture_output=True, timeout=120)
            written.append(out.name)
        except Exception as e:  # noqa: BLE001 - bir dosyanın hatası diğerlerini durdurmaz
            failed.append({"file": r["rel_path"], "error": str(e)[:200]})
        if progress:
            progress("export", n, len(rows))
    return {"written": len(written), "failed": failed, "dest": str(dest), "files": written}


# ------------------------------------------------------------------ harita

def map_points(repo: Repo) -> list[list]:
    """Konumlu öğeler: [enlem, boylam, blake3, dosya no, tarih, yer, tür, öğe no] (öğe başına bir dosya)."""
    rows = repo.db.execute(
        f"SELECT a.lat, a.lon, a.blake3, f.id AS file_id, a.taken_at, p.name AS place, a.kind, a.id AS asset_id, f.rel_path {BASE} {ONE_FILE} "
        "AND a.lat IS NOT NULL AND a.lon IS NOT NULL ORDER BY a.taken_at DESC").fetchall()
    return [[round(r["lat"], 5), round(r["lon"], 5), r["blake3"], r["file_id"], r["taken_at"], r["place"], r["kind"],
             r["asset_id"], r["rel_path"]] for r in rows]


# ------------------------------------------------------------------ video küçültme

COMPRESS_QUALITY = 55     # VideoToolbox HEVC kalite (0–100); testlerde SSIM ≈ 0.994: gözle ayırt edilmez
COMPRESS_MIN_GAIN = 0.20  # en az %20 küçülmüyorsa orijinal olduğu gibi kalır


def _encode_hevc(src: Path, dst: Path, duration_ms: int | None, progress: Progress | None) -> None:
    from .media import video as video_mod

    info = video_mod.probe(src)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    acopy = bool(audio) and audio[0].get("codec_name") in ("aac", "alac")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
           "-c:v", "hevc_videotoolbox", "-q:v", str(COMPRESS_QUALITY), "-tag:v", "hvc1",
           *(["-c:a", "copy"] if acopy else ["-c:a", "aac", "-b:a", "192k"]),
           "-map_metadata", "0", "-movflags", "+faststart+use_metadata_tags", str(dst)]
    video_mod._run_with_progress(cmd, duration_ms, "compress", progress)
    # Tarih, konum, cihaz bilgisi (QuickTime anahtarları) ve dosya tarihi korunur
    subprocess.run(["exiftool", "-q", "-q", "-overwrite_original", "-api", "LargeFileSupport=1", "-TagsFromFile", str(src),
                    "-all:all", str(dst)], capture_output=True, timeout=300)
    st = src.stat()
    import os

    os.utime(dst, (st.st_atime, st.st_mtime))


def _duration_ms(path: Path) -> int:
    from .media import video as video_mod

    try:
        return int(float(video_mod.probe(path).get("format", {}).get("duration", 0)) * 1000)
    except (TypeError, ValueError):
        return 0


def compress_videos(repo: Repo, file_ids: list[int], progress: Progress | None = None,
                    after_replace: Callable[[], None] | None = None) -> dict:
    """Videoları HEVC'ye çevirir. Kazanç yeterliyse orijinal Son silinenler'e gider, küçültülmüş hali aynı yere konur.

    Tek bir plan olarak uygulanır: Geçmiş'ten geri alınınca orijinaller geri gelir. Etiket, puan, favori, albüm ve
    tarih/konum düzeltmeleri yeni dosyaya taşınır.
    """
    from . import plans, scan

    rows = repo.db.execute(
        f"SELECT f.id, f.rel_path, f.fs_path, f.size, a.id AS asset_id, a.blake3, a.duration_ms FROM files f "
        f"JOIN assets a ON a.id=f.asset_id WHERE f.state='present' AND a.kind='video' "
        f"AND f.id IN ({','.join('?' * len(file_ids)) or 'NULL'})", file_ids).fetchall()
    tmp_dir = repo.mm / "tmp"
    done, skipped, failed = [], [], []
    for n, r in enumerate(rows, 1):
        src = repo.root / r["fs_path"]
        ext = Path(r["rel_path"]).suffix.lower()
        out_ext = ext if ext in (".mov", ".mp4", ".m4v") else ".mp4"
        tmp = tmp_dir / f"{r['blake3'][:16]}{out_ext}"
        try:
            _encode_hevc(src, tmp, r["duration_ms"], (lambda st, d, t: progress(st, d, t)) if progress else None)
            new_size = tmp.stat().st_size
            if abs(_duration_ms(tmp) - _duration_ms(src)) > 1500:
                raise RuntimeError("Süre tutmuyor, orijinal korunuyor")
            if new_size > r["size"] * (1 - COMPRESS_MIN_GAIN):
                tmp.unlink(missing_ok=True)
                skipped.append(r["rel_path"])
                continue
            new_rel = r["rel_path"] if out_ext == ext else str(Path(r["rel_path"]).with_suffix(out_ext))
            done.append((r, tmp, new_rel, new_size))
        except Exception as e:  # noqa: BLE001 - bir videonun hatası diğerlerini durdurmaz
            tmp.unlink(missing_ok=True)
            failed.append({"file": r["rel_path"], "error": str(e)[:200]})
        if progress:
            progress("compress_files", n, len(rows))
    saved = 0
    plan_id = None
    if done:
        items = []
        for r, tmp, new_rel, _ in done:
            items.append(("trash", r["id"], r["rel_path"], None))
            items.append(("move", None, f"{repo.mm.name}/tmp/{tmp.name}", new_rel))
        plan_id = plans.create(repo, "compress", items, title=f"{len(done)} video küçültüldü", source="ui")
        res = plans.apply(repo, plan_id)
        if res.failed:
            failed.append({"file": "plan", "error": f"{res.failed} öğe uygulanamadı (Geçmiş › #{plan_id})"})
        scan.scan(repo)  # yeni dosyalar kayda girer
        with repo.lock(), repo.db as conn:
            for r, _tmp, new_rel, new_size in done:
                new = conn.execute("SELECT f.id, f.asset_id FROM files f WHERE f.rel_path=? AND f.state='present'", (new_rel,)).fetchone()
                if new is None:
                    continue
                saved += r["size"] - new_size
                _carry_metadata(conn, r["asset_id"], new["asset_id"])
                conn.execute("UPDATE files SET tracked=1 WHERE id=?", (new["id"],))
        if after_replace:
            after_replace()
    return {"compressed": len(done), "skipped": skipped, "failed": failed, "saved_bytes": saved, "plan_id": plan_id}


def _carry_metadata(conn, old: int, new: int) -> None:
    """Kullanıcının verdiği bilgiler (favori, puan, etiket, albüm, tarih/konum düzeltmesi) yeni içeriğe taşınır."""
    o = conn.execute("SELECT favorite, rating FROM assets WHERE id=?", (old,)).fetchone()
    conn.execute("UPDATE assets SET favorite=MAX(favorite, ?), rating=MAX(rating, ?) WHERE id=?", (o["favorite"], o["rating"], new))
    conn.execute("INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, source, confidence) "
                 "SELECT ?, tag_id, source, confidence FROM asset_tags WHERE asset_id=? AND source='user'", (new, old))
    conn.execute("INSERT OR IGNORE INTO album_items(album_id, asset_id) SELECT album_id, ? FROM album_items WHERE asset_id=?", (new, old))
    e = conn.execute("SELECT * FROM asset_edits WHERE asset_id=?", (old,)).fetchone()
    if e is not None:
        conn.execute("INSERT OR REPLACE INTO asset_edits(asset_id, taken_at, orig_taken_at, orig_taken_at_source, lat, lon, "
                     "orig_lat, orig_lon, orig_place_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (new, e["taken_at"], e["orig_taken_at"], e["orig_taken_at_source"], e["lat"], e["lon"],
                      e["orig_lat"], e["orig_lon"], e["orig_place_id"]))
