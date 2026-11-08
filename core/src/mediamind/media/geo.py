"""Offline reverse geocoding (GeoNames cities1000, reverse_geocoder paketiyle gelir).

reverse_geocoder adları ASCII'ye çevrilmiş verir ("Izmir", "Zeytindag"). data/place_names.tsv.gz tablosu bunları
GeoNames'teki asıl yazımlarına ("İzmir", "Zeytindağ") döndürür; Türkiye illeri ayrıca elle eşlenmiştir.
Arama için fold() aksan/Türkçe karakter farkını yok sayan bir biçim üretir ("izmir" → "İzmir" eşleşir).
"""

from __future__ import annotations

import contextlib
import gzip
import io
import unicodedata
from pathlib import Path

_rg = None
_names: dict[tuple[str, str, str], str] | None = None

TR_ADMIN1 = {
    "Adiyaman": "Adıyaman", "Agri": "Ağrı", "Aydin": "Aydın", "Balikesir": "Balıkesir", "Bartin": "Bartın",
    "Bingoel": "Bingöl", "Canakkale": "Çanakkale", "Cankiri": "Çankırı", "Corum": "Çorum", "Diyarbakir": "Diyarbakır",
    "Duzce": "Düzce", "Elazig": "Elazığ", "Eskisehir": "Eskişehir", "Gumushane": "Gümüşhane", "Igdir": "Iğdır",
    "Istanbul": "İstanbul", "Izmir": "İzmir", "Kahramanmaras": "Kahramanmaraş", "Karabuk": "Karabük",
    "Kirikkale": "Kırıkkale", "Kirklareli": "Kırklareli", "Kirsehir": "Kırşehir", "Kuetahya": "Kütahya",
    "Mugla": "Muğla", "Mus": "Muş", "Nevsehir": "Nevşehir", "Nigde": "Niğde", "Sanliurfa": "Şanlıurfa",
    "Sirnak": "Şırnak", "Tekirdag": "Tekirdağ", "Usak": "Uşak",
}

_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c",
                       "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "â": "a", "î": "i", "û": "u"})


def fold(s: str | None) -> str:
    """Aramada kullanılan sade biçim: küçük harf, Türkçe karakterler ve aksanlar ASCII."""
    if not s:
        return ""
    s = s.translate(_FOLD).lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _load():
    global _rg
    if _rg is None:
        import reverse_geocoder

        _rg = reverse_geocoder
    return _rg


def _name_table() -> dict[tuple[str, str, str], str]:
    global _names
    if _names is None:
        _names = {}
        p = Path(__file__).resolve().parent.parent / "data" / "place_names.tsv.gz"
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    cc, a1, ascii_name, name = line.rstrip("\n").split("\t")
                    _names[(cc, a1, ascii_name)] = name
    return _names


def proper(name: str | None, admin1: str | None, cc: str | None) -> tuple[str | None, str | None]:
    """ASCII yer adı ve il adını asıl yazımına çevirir (bilinmiyorsa olduğu gibi)."""
    nm = _name_table().get((cc or "", admin1 or "", name or ""), name)
    a1 = TR_ADMIN1.get(admin1 or "", admin1) if cc == "TR" else admin1
    return nm, a1


def lookup(coords: list[tuple[float, float]]) -> list[dict]:
    """[(lat, lon)] → [{name, admin1, admin2, cc}] (aynı sırayla, asıl yazımlarla)."""
    if not coords:
        return []
    rg = _load()
    with contextlib.redirect_stdout(io.StringIO()):  # kütüphane ilk yüklemede stdout'a yazıyor
        res = rg.search(coords, mode=1, verbose=False)
    out = []
    for r in res:
        name, admin1 = proper(r.get("name"), r.get("admin1"), r.get("cc"))
        out.append({"name": name, "admin1": admin1, "admin2": r.get("admin2"), "cc": r.get("cc")})
    return out


def fix_place_names(conn) -> int:
    """Önceden ASCII kaydedilmiş yer adlarını düzeltir ve arama biçimlerini doldurur (hızlı, tekrar çalıştırılabilir)."""
    n = 0
    rows = conn.execute("SELECT id, name, admin1, country_code, name_fold FROM places").fetchall()
    with conn:
        for r in rows:
            name, admin1 = proper(r["name"], r["admin1"], r["country_code"])
            if (name, admin1) != (r["name"], r["admin1"]):
                dup = conn.execute("SELECT id FROM places WHERE name=? AND admin1 IS ? AND country_code IS ? AND id<>?",
                                   (name, admin1, r["country_code"], r["id"])).fetchone()
                if dup:  # düzeltilmiş adla zaten kayıt varsa birleştir
                    conn.execute("UPDATE assets SET place_id=? WHERE place_id=?", (dup["id"], r["id"]))
                    conn.execute("DELETE FROM places WHERE id=?", (r["id"],))
                    n += 1
                    continue
                conn.execute("UPDATE places SET name=?, admin1=? WHERE id=?", (name, admin1, r["id"]))
                n += 1
            if r["name_fold"] is None or (name, admin1) != (r["name"], r["admin1"]):
                conn.execute("UPDATE places SET name_fold=?, admin1_fold=? WHERE id=?", (fold(name), fold(admin1), r["id"]))
    return n


# ------------------------------------------------------------------ ileri arama (ad → koordinat)

_index: list[tuple[str, str, str | None, str, float, float]] | None = None


def _search_index():
    """GeoNames cities1000 (çevrimdışı): (sade ad, ad, il, ülke, enlem, boylam)."""
    global _index
    if _index is None:
        import csv
        import os

        rg = _load()
        path = os.path.join(os.path.dirname(rg.__file__), "rg_cities1000.csv")
        rows = []
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                name, admin1 = proper(r["name"], r["admin1"], r["cc"])
                try:
                    rows.append((fold(name), name or r["name"], admin1, r["cc"], float(r["lat"]), float(r["lon"])))
                except ValueError:
                    continue
        _index = rows
    return _index


def search(q: str, limit: int = 8, prefer_cc: str = "TR") -> list[dict]:
    """Şehir/ilçe/kasaba adında arama (çevrimdışı). "kadıköy istanbul" gibi ikinci sözcük ili süzer."""
    words = fold(q).replace(",", " ").split()
    if not words:
        return []
    head, rest = words[0], words[1:]
    hits = []
    for f, name, admin1, cc, lat, lon in _search_index():
        if not f.startswith(head):
            continue
        full = f"{f} {fold(admin1)} {cc.lower()}"
        if rest and not all(w in full for w in rest):
            continue
        # Tam ad eşleşmesi > tercih edilen ülke > kısa ad
        exact = f == " ".join(words[: len(f.split())])
        hits.append(((0 if exact else 1), (0 if cc == prefer_cc else 1), len(f), name, admin1, cc, lat, lon))
    hits.sort(key=lambda h: h[:3])
    out, seen = [], set()
    for *_, name, admin1, cc, lat, lon in hits:
        key = (name, admin1, cc)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": name, "sub": ", ".join(x for x in (admin1, cc) if x and x != name), "lat": lat, "lon": lon,
                    "source": "offline"})
        if len(out) >= limit:
            break
    return out


def search_online(q: str, limit: int = 6, timeout: float = 4.0, near: tuple[float, float] | None = None) -> list[dict]:
    """Adres araması (Photon / OpenStreetMap). Yalnız yazılan metin gönderilir; hata olursa boş liste."""
    import json as _json
    import urllib.parse
    import urllib.request

    params: dict = {"q": q, "limit": limit}
    if near:  # kütüphanenin ağırlık merkezine yakın sonuçlar öne çıksın
        params.update(lat=f"{near[0]:.2f}", lon=f"{near[1]:.2f}")
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "MediaMind (offline photo manager)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
        except (KeyError, ValueError, TypeError):
            continue
        street = " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
        name = p.get("name") or street or p.get("city") or p.get("county")
        if not name:
            continue
        parts = [street if street != name else None, p.get("district"), p.get("city") or p.get("county"), p.get("state"),
                 p.get("country")]
        sub, seen = [], {name}
        for x in parts:
            if x and x not in seen:
                sub.append(x)
                seen.add(x)
        out.append({"label": name, "sub": ", ".join(sub), "lat": float(lat), "lon": float(lon), "source": "online"})
    return out
