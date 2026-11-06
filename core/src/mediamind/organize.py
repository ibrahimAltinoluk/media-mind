"""Şablonla dizinlere yerleştirme. Sonuç her zaman bir taşıma planıdır."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from . import plans
from .paths import nfc
from .repo import Repo

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
TUR = {"photo": "Fotoğraf", "video": "Video"}

PRESETS = {
    "yil-ay": "{yıl}/{ay}-{ay_adı}/{dosya}",
    "yil": "{yıl}/{dosya}",
    "yil-konum": "{yıl}/{şehir}/{dosya}",
    "tur-yil-ay": "{tür}/{yıl}/{ay}-{ay_adı}/{dosya}",
    "kamera-yil": "{kamera}/{yıl}/{dosya}",
}

# Türkçe ve İngilizce adlar aynı belirteci verir
ALIASES = {
    "year": "yıl", "yil": "yıl", "month": "ay", "month_name": "ay_adı", "ay_adi": "ay_adı",
    "day": "gün", "gun": "gün", "city": "şehir", "sehir": "şehir", "region": "il", "country": "ülke",
    "ulke": "ülke", "camera": "kamera", "kind": "tür", "tur": "tür", "file": "dosya", "name": "ad",
    "ext": "uzantı", "uzanti": "uzantı", "person": "kişi", "kisi": "kişi", "tag": "etiket",
}

TOKEN_RE = re.compile(r"\{([^{}]+)\}")
_BAD = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


class TemplateError(ValueError):
    pass


def _clean(seg: str) -> str:
    seg = _BAD.sub("_", seg).strip().strip(".")
    return nfc(seg[:120]) or "_"


def values(row) -> dict[str, str]:
    taken = row["taken_at"]
    name = PurePosixPath(row["rel_path"]).name
    stem = name[: name.rfind(".")] if "." in name else name
    v = {
        "dosya": name,
        "ad": stem,
        "uzantı": name[name.rfind(".") + 1 :].lower() if "." in name else "",
        "tür": TUR.get(row["kind"], row["kind"]),
        "şehir": row["place_name"] or "_konumsuz",
        "il": row["admin1"] or "_konumsuz",
        "ülke": row["country_code"] or "_konumsuz",
        "kamera": " ".join(x for x in [row["camera_model"]] if x) or "_bilinmeyen",
        "kişi": row["person"] if "person" in row.keys() and row["person"] else "_kişisiz",
        "etiket": row["first_tag"] or "_etiketsiz",
    }
    if taken:
        y, m, d = taken[:4], taken[5:7], taken[8:10]
        v.update({"yıl": y, "ay": m, "ay_adı": AYLAR[int(m) - 1], "gün": d})
    else:
        v.update({"yıl": "_tarihsiz", "ay": "_tarihsiz", "ay_adı": "_tarihsiz", "gün": "_tarihsiz"})
    return v


def render(template: str, row) -> str:
    vals = values(row)
    parts = []
    for seg in template.strip("/").split("/"):
        def sub(m):
            key = m.group(1).strip()
            key = ALIASES.get(key, key)
            if key not in vals:
                raise TemplateError(f"Bilinmeyen belirteç: {{{m.group(1)}}}")
            return vals[key]

        rendered = TOKEN_RE.sub(sub, seg)
        if rendered.startswith("_tarihsiz") and seg.strip() in ("{ay}", "{gün}", "{ay}-{ay_adı}", "{month}"):
            continue  # tarihsiz dosyada ay/gün klasörlerini tekrar etme
        parts.append(_clean(rendered))
    if not parts:
        raise TemplateError("Şablon boş")
    if "{" in template and not any(k in template for k in ("{dosya}", "{file}", "{ad}", "{name}")):
        parts.append(_clean(vals["dosya"]))
    return "/".join(parts)


def resolve_template(t: str) -> str:
    return PRESETS.get(t, t)


def _rows(repo: Repo, scope: str, under: str | None):
    cond = {"tracked": "f.tracked=1", "untracked": "f.tracked=0", "all": "1=1"}[scope]
    sql = (
        "SELECT f.id, f.rel_path, a.kind, a.taken_at, a.camera_model, p.name AS place_name, p.admin1, p.country_code, "
        "(SELECT pr.name FROM faces fc JOIN persons pr ON pr.id=fc.person_id WHERE fc.asset_id=a.id "
        " ORDER BY (fc.x2-fc.x1)*(fc.y2-fc.y1) DESC LIMIT 1) AS person, "
        "(SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND at.source='user' "
        " ORDER BY t.name LIMIT 1) AS first_tag "
        "FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
        f"WHERE f.state='present' AND {cond}"
    )
    params: list = []
    if under:
        u = under.strip("/")
        sql += " AND (f.rel_path LIKE ? ESCAPE '\\')"
        params.append(u.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")
    return repo.db.execute(sql + " ORDER BY a.taken_at, f.rel_path", params).fetchall()


def preview(repo: Repo, template: str, scope: str = "tracked", under: str | None = None, base: str = "") -> list[tuple[int, str, str]]:
    template = resolve_template(template)
    base = base.strip("/")
    out = []
    for r in _rows(repo, scope, under):
        dst = render(template, r)
        if base:
            dst = f"{base}/{dst}"
        if dst != r["rel_path"]:
            out.append((r["id"], r["rel_path"], dst))
    return out


def build_plan(repo: Repo, template: str, scope: str = "tracked", under: str | None = None, base: str = "",
               source: str = "cli") -> int:
    moves = preview(repo, template, scope, under, base)
    t = resolve_template(template)
    return plans.create(repo, "organize", [("move", fid, src, dst) for fid, src, dst in moves],
                        title=f"Düzenle: {t}", source=source)
