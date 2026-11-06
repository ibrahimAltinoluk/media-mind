"""Çekim tarihinin çıkarımı.

Öncelik: EXIF DateTimeOriginal (+OffsetTimeOriginal) → QuickTime CreationDate (Apple, ofsetli)
→ QuickTime CreateDate (exiftool QuickTimeUTC ile yerel saate çevrilmiş) → dosya adı → mtime.
"""

from __future__ import annotations

import re
from datetime import datetime

_EXIF_RE = re.compile(
    r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+\-]\d{2}:?\d{2})?"
)

# IMG_20240815_194211, IMG-20240815-WA0012, PXL_20240815_194211123, 2024-08-15 19.42.11, Screenshot 2024-08-15 at 19.42.11
_NAME_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[ _T\-]+(?:at[ _])?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?(?!\d{3,})"
)


def _norm_offset(s: str | None) -> str | None:
    if not s:
        return None
    if s == "Z":
        return "+00:00"
    s = s.replace(":", "")
    return f"{s[:3]}:{s[3:5]}"


def parse_exif_date(value) -> tuple[str, str | None] | None:
    """'2024:08:14 19:42:00+03:00' → ('2024-08-14T19:42:00', '+03:00')"""
    if not value or not isinstance(value, str):
        return None
    m = _EXIF_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, off = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    except ValueError:
        return None
    if dt.year < 1900:
        return None
    return dt.isoformat(), _norm_offset(off)


def date_from_filename(name: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    for m in _NAME_RE.finditer(name):
        y, mo, d, h, mi, s = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
        except ValueError:
            continue
        if 1990 <= dt.year <= now.year + 1:
            return dt.isoformat()
    return None


def infer(raw: dict, filename: str, mtime_ns: int | None) -> tuple[str | None, str | None, str | None]:
    """→ (taken_at, tz_offset, kaynak)"""
    p = parse_exif_date(raw.get("DateTimeOriginal"))
    if p:
        off = p[1] or _norm_offset(raw.get("OffsetTimeOriginal") or raw.get("OffsetTime"))
        return p[0], off, "exif"
    for key in ("CreationDate", "CreateDate", "MediaCreateDate"):
        p = parse_exif_date(raw.get(key))
        if p:
            return p[0], p[1], "quicktime" if key != "CreateDate" or raw.get("_video") else "exif"
    fn = date_from_filename(filename)
    if fn:
        return fn, None, "filename"
    if mtime_ns:
        return datetime.fromtimestamp(mtime_ns / 1e9).replace(microsecond=0).isoformat(), None, "mtime"
    return None, None, None
