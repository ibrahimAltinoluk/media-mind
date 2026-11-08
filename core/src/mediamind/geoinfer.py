"""Konumsuz fotoğraflara konum önerisi.

İki kaynak:
  * Yakın zamanda çekilmiş konumlu fotoğraflar (ör. telefonun GPS'li fotoğrafları, aynı gün fotoğraf makinesinin
    konumsuz fotoğrafları). Önce ve sonra çekilmiş konumlu fotoğraflar birbirine yakınsa aradaki konum zamana göre
    oranlanır; tek taraf varsa yalnız kısa aralıkta öneri yapılır.
  * Bir GPX kaydı (saat, telefon, yürüyüş uygulaması). Fotoğrafın saati UTC'ye çevrilip izdeki noktalar arasında
    oranlanır.

Öneriler gün ve yer adına göre gruplanır; kullanıcı onaylar ya da atlar. Hiçbir şey kendiliğinden yazılmaz.
"""

from __future__ import annotations

import bisect
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .media import geo
from .repo import Repo

BOTH_SIDES = 3 * 3600      # önce ve sonra konumlu fotoğraf bu aralıktaysa ...
BOTH_MAX_KM = 5.0          # ... ve birbirlerine bu kadar yakınsa aradaki konum oranlanır
ONE_SIDE = 45 * 60         # yalnız bir taraf varsa en fazla bu kadar uzaklıktaki fotoğrafa güvenilir
GPX_GAP = 15 * 60          # izde iki nokta arası boşluk bundan fazlaysa oranlama yapılmaz
GPX_EDGE = 5 * 60          # izin başı/sonu dışındaki fotoğraflar için en yakın noktaya en fazla uzaklık


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _local_ts(taken_at: str) -> float:
    """Yerel (saat dilimsiz) çekim zamanı → karşılaştırma için saniye. Aynı arşivde tutarlı olması yeter."""
    return datetime.fromisoformat(taken_at).replace(tzinfo=timezone.utc).timestamp()


def _utc_ts(taken_at: str, tz_offset: str | None) -> float:
    """Çekim zamanının gerçek UTC karşılığı (GPX ile eşlemek için). Saat dilimi bilinmiyorsa bu Mac'in dilimi."""
    dt = datetime.fromisoformat(taken_at)
    if tz_offset:
        try:
            return datetime.fromisoformat(f"{dt.isoformat()}{tz_offset}").timestamp()
        except ValueError:
            pass
    return dt.astimezone().timestamp()  # saat dilimsiz → yerel saat kabul edilir (yaz saati dahil)


def parse_gpx(path: str | Path) -> list[tuple[float, float, float]]:
    """GPX → [(utc_ts, lat, lon)] zamana göre sıralı. İz, rota ve işaret noktalarının zamanlı olanları."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise ValueError(f"GPX dosyası okunamadı: {e}") from e
    pts = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] not in ("trkpt", "rtept", "wpt"):
            continue
        t = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "time" and c.text), None)
        if not t:
            continue
        try:
            ts = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            pts.append((ts.timestamp(), float(el.get("lat")), float(el.get("lon"))))
        except (TypeError, ValueError):
            continue
    if not pts:
        raise ValueError("GPX dosyasında zaman bilgisi olan nokta yok")
    pts.sort()
    return pts


def _missing(conn) -> list:
    return conn.execute(
        "SELECT a.id, a.blake3, a.kind, a.taken_at, a.tz_offset FROM assets a "
        "WHERE a.lat IS NULL AND a.taken_at IS NOT NULL AND COALESCE(a.taken_at_source,'') NOT IN ('mtime') "
        "AND NOT EXISTS (SELECT 1 FROM geo_skip s WHERE s.asset_id=a.id) "
        "AND EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1) "
        "ORDER BY a.taken_at").fetchall()


def _from_neighbors(conn, missing: list) -> list[dict]:
    known = conn.execute(
        "SELECT taken_at, lat, lon FROM assets WHERE lat IS NOT NULL AND lon IS NOT NULL AND taken_at IS NOT NULL "
        "AND COALESCE(taken_at_source,'') NOT IN ('mtime') ORDER BY taken_at").fetchall()
    if not known:
        return []
    ts = [_local_ts(r["taken_at"]) for r in known]
    out = []
    for r in missing:
        t = _local_ts(r["taken_at"])
        i = bisect.bisect_left(ts, t)
        before = (ts[i - 1], known[i - 1]) if i > 0 else None
        after = (ts[i], known[i]) if i < len(ts) else None
        if before and after and t - before[0] <= BOTH_SIDES and after[0] - t <= BOTH_SIDES:
            a, b = (before[1]["lat"], before[1]["lon"]), (after[1]["lat"], after[1]["lon"])
            if _km(a, b) <= BOTH_MAX_KM:
                span = after[0] - before[0]
                w = (t - before[0]) / span if span else 0.5
                out.append({"r": r, "lat": a[0] + (b[0] - a[0]) * w, "lon": a[1] + (b[1] - a[1]) * w,
                            "gap": int(max(t - before[0], after[0] - t))})
                continue
        near = min((x for x in (before, after) if x), key=lambda x: abs(x[0] - t))
        if abs(near[0] - t) <= ONE_SIDE:
            out.append({"r": r, "lat": near[1]["lat"], "lon": near[1]["lon"], "gap": int(abs(near[0] - t))})
    return out


def _from_gpx(missing: list, pts: list[tuple[float, float, float]]) -> list[dict]:
    ts = [p[0] for p in pts]
    out = []
    for r in missing:
        t = _utc_ts(r["taken_at"], r["tz_offset"])
        i = bisect.bisect_left(ts, t)
        if 0 < i < len(ts) and ts[i] - ts[i - 1] <= GPX_GAP:
            (t0, la0, lo0), (t1, la1, lo1) = pts[i - 1], pts[i]
            w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            out.append({"r": r, "lat": la0 + (la1 - la0) * w, "lon": lo0 + (lo1 - lo0) * w, "gap": int(max(t - t0, t1 - t))})
            continue
        near = min((pts[j] for j in (i - 1, i) if 0 <= j < len(pts)), key=lambda p: abs(p[0] - t))
        if abs(near[0] - t) <= GPX_EDGE:
            out.append({"r": r, "lat": near[1], "lon": near[2], "gap": int(abs(near[0] - t))})
    return out


def suggestions(repo: Repo, gpx: str | None = None, limit_groups: int = 200) -> dict:
    """Konum önerileri, gün + yer adına göre gruplanmış (en kalabalık önce)."""
    conn = repo.db
    missing = _missing(conn)
    source = "gpx" if gpx else "nearby"
    found = _from_gpx(missing, parse_gpx(gpx)) if gpx else _from_neighbors(conn, missing)
    names = geo.lookup([(f["lat"], f["lon"]) for f in found])
    groups: dict[tuple, dict] = {}
    for f, g in zip(found, names):
        day = f["r"]["taken_at"][:10]
        key = (day, g["name"], g["cc"])
        grp = groups.setdefault(key, {"key": f"{day}|{g['name']}|{g['cc']}", "day": day, "place": g["name"],
                                      "region": g["admin1"], "country_code": g["cc"], "source": source, "items": []})
        grp["items"].append({"asset_id": f["r"]["id"], "blake3": f["r"]["blake3"], "kind": f["r"]["kind"],
                             "taken_at": f["r"]["taken_at"], "lat": round(f["lat"], 6), "lon": round(f["lon"], 6),
                             "gap_s": f["gap"]})
    out = sorted(groups.values(), key=lambda g: (-len(g["items"]), g["day"]))
    for g in out:
        g["count"] = len(g["items"])
        g["lat"] = sum(i["lat"] for i in g["items"]) / g["count"]
        g["lon"] = sum(i["lon"] for i in g["items"]) / g["count"]
    skipped = conn.execute("SELECT COUNT(*) FROM geo_skip").fetchone()[0]
    return {"groups": out[:limit_groups], "total": sum(g["count"] for g in out), "missing": len(missing), "skipped": skipped}


def skip(repo: Repo, asset_ids: list[int]) -> int:
    with repo.lock(), repo.db as conn:
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO geo_skip(asset_id) VALUES (?)", [(a,) for a in asset_ids])
        return conn.total_changes - before


def clear_skips(repo: Repo) -> int:
    with repo.lock(), repo.db as conn:
        return conn.execute("DELETE FROM geo_skip").rowcount
