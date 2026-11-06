"""Meta veri okuma: exiftool (toplu) ya da Pillow/ffprobe yedeği."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..kinds import kind_of

EXIFTOOL_TAGS = [
    "-DateTimeOriginal", "-CreateDate", "-CreationDate", "-MediaCreateDate",
    "-OffsetTimeOriginal", "-OffsetTime",
    "-Composite:GPSLatitude", "-Composite:GPSLongitude", "-Composite:GPSAltitude", "-GPSCoordinates",
    "-Make", "-Model", "-LensModel", "-Orientation", "-Rotation",
    "-ImageWidth", "-ImageHeight", "-Duration", "-MIMEType", "-CompressorID", "-AvgBitrate",
]


def have_exiftool() -> bool:
    return shutil.which("exiftool") is not None


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def read_raw(paths: list[Path], chunk: int = 200) -> dict[Path, dict]:
    """Dosya → ham etiket sözlüğü."""
    if not paths:
        return {}
    if not have_exiftool():
        return {p: _fallback(p) for p in paths}
    out: dict[Path, dict] = {}
    for i in range(0, len(paths), chunk):
        part = paths[i : i + chunk]
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(str(p) for p in part))
            argfile = fh.name
        try:
            proc = subprocess.run(
                ["exiftool", "-json", "-n", "-fast", "-charset", "filename=utf8", "-api", "QuickTimeUTC=1",
                 *EXIFTOOL_TAGS, "-@", argfile],
                capture_output=True, text=True, timeout=600,
            )
        finally:
            Path(argfile).unlink(missing_ok=True)
        try:
            items = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            items = []
        by_src = {it.get("SourceFile"): it for it in items}
        for p in part:
            out[p] = by_src.get(str(p)) or _fallback(p)
    return out


def _fallback(p: Path) -> dict:
    kind = kind_of(p.name)
    if kind == "video":
        return _ffprobe(p)
    try:
        from PIL import Image

        from .thumbs import ensure_heif

        ensure_heif()
        with Image.open(p) as im:
            exif = im.getexif()
            sub = exif.get_ifd(0x8769)
            gps = exif.get_ifd(0x8825)
            d = {
                "ImageWidth": im.width,
                "ImageHeight": im.height,
                "MIMEType": Image.MIME.get(im.format or "", None),
                "Make": exif.get(0x010F),
                "Model": exif.get(0x0110),
                "Orientation": exif.get(0x0112),
                "DateTimeOriginal": sub.get(0x9003) or exif.get(0x0132),
                "OffsetTimeOriginal": sub.get(0x9011),
                "LensModel": sub.get(0xA434),
            }
            if gps and 2 in gps and 4 in gps:
                def dms(v):
                    return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                lat, lon = dms(gps[2]), dms(gps[4])
                if gps.get(1) == "S":
                    lat = -lat
                if gps.get(3) == "W":
                    lon = -lon
                d["GPSLatitude"], d["GPSLongitude"] = lat, lon
            return {k: v for k, v in d.items() if v is not None}
    except Exception:
        return {}


def _ffprobe(p: Path) -> dict:
    if not have_ffmpeg():
        return {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    d: dict = {}
    fmt = data.get("format", {})
    if fmt.get("duration"):
        d["Duration"] = float(fmt["duration"])
    if fmt.get("bit_rate"):
        d["AvgBitrate"] = int(fmt["bit_rate"])
    tags = fmt.get("tags", {})
    if tags.get("creation_time"):
        d["CreateDate"] = tags["creation_time"].replace("T", " ").replace("-", ":", 2)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            d["ImageWidth"], d["ImageHeight"] = s.get("width"), s.get("height")
            d["CompressorID"] = s.get("codec_name")
            break
    return d


def normalize(raw: dict, kind: str) -> dict:
    """Ham etiketleri assets tablosu alanlarına çevirir."""
    lat, lon, alt = raw.get("GPSLatitude"), raw.get("GPSLongitude"), raw.get("GPSAltitude")
    if (lat is None or lon is None) and isinstance(raw.get("GPSCoordinates"), str):
        parts = raw["GPSCoordinates"].split()
        try:
            lat, lon = float(parts[0]), float(parts[1])
            alt = float(parts[2]) if len(parts) > 2 else alt
        except (ValueError, IndexError):
            pass
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None
    if lat is not None and lon is not None and (abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0)):
        lat = lon = None

    w, h = raw.get("ImageWidth"), raw.get("ImageHeight")
    w = int(w) if isinstance(w, (int, float)) else None
    h = int(h) if isinstance(h, (int, float)) else None
    orientation = raw.get("Orientation") if isinstance(raw.get("Orientation"), int) else None
    rotation = raw.get("Rotation") if isinstance(raw.get("Rotation"), (int, float)) else 0
    # Görünen boyut: 90/270 döndürülmüşse genişlik ve yükseklik yer değiştirir
    if w and h and ((orientation in (5, 6, 7, 8)) or int(rotation) % 180 == 90):
        w, h = h, w
    duration = raw.get("Duration")
    s = lambda v: str(v).strip() if v not in (None, "") else None  # noqa: E731
    return {
        "mime": s(raw.get("MIMEType")),
        "width": w,
        "height": h,
        "duration_ms": int(float(duration) * 1000) if isinstance(duration, (int, float)) and kind == "video" else None,
        "lat": lat,
        "lon": lon,
        "alt": float(alt) if isinstance(alt, (int, float)) and lat is not None else None,
        "camera_make": s(raw.get("Make")),
        "camera_model": s(raw.get("Model")),
        "lens": s(raw.get("LensModel")),
        "orientation": orientation,
        "video_codec": s(raw.get("CompressorID")) if kind == "video" else None,
        "bitrate": int(raw["AvgBitrate"]) if isinstance(raw.get("AvgBitrate"), (int, float)) else None,
    }
