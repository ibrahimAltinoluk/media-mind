"""Özgün dosyaya konum (GPS) yazma.

Kurallar (özgün dosyaya dokunulan tek yer burası):
- exiftool `-overwrite_original_in_place`: dosya yerinde güncellenir; macOS oluşturma tarihi, Finder etiketleri,
  genişletilmiş öznitelikler, izinler ve inode korunur (geçici dosya + yeniden adlandırma yapılmaz).
- `-P`: değiştirilme tarihi korunur. Ayrıca yazımdan önce alınan erişim/değiştirilme zamanları nanosaniye
  hassasiyetiyle geri yüklenir (exiftool'un saniye yuvarlamasına karşı).
- Yalnız GPS etiketleri değişir; diğer meta veriye ve görüntü verisine dokunulmaz.
- Yazımdan sonra konum dosyadan geri okunur; tutmazsa hata verilir.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".webp", ".dng"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp"}


class WriteError(Exception):
    pass


def available() -> bool:
    return shutil.which("exiftool") is not None


def writable(path: Path) -> bool:
    ext = path.suffix.lower()
    return ext in PHOTO_EXT or ext in VIDEO_EXT


def _args(path: Path, lat: float | None, lon: float | None) -> list[str]:
    video = path.suffix.lower() in VIDEO_EXT
    if lat is None or lon is None:
        a = ["-GPS:all=", "-XMP-exif:GPSLatitude=", "-XMP-exif:GPSLongitude=",
             "-XMP-exif:GPSLatitudeRef=", "-XMP-exif:GPSLongitudeRef="]
        if video:
            a += ["-Keys:GPSCoordinates=", "-UserData:GPSCoordinates=", "-ItemList:GPSCoordinates="]
        return a
    if video:
        c = f"{lat:.6f} {lon:.6f} 0"
        return [f"-Keys:GPSCoordinates={c}", f"-UserData:GPSCoordinates={c}"]
    return [f"-EXIF:GPSLatitude={abs(lat):.7f}", f"-EXIF:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-EXIF:GPSLongitude={abs(lon):.7f}", f"-EXIF:GPSLongitudeRef={'E' if lon >= 0 else 'W'}"]


def read_gps(path: Path) -> tuple[float | None, float | None]:
    p = subprocess.run(["exiftool", "-json", "-n", "-api", "LargeFileSupport=1", "-Composite:GPSLatitude",
                        "-Composite:GPSLongitude", "-GPSCoordinates", str(path)],
                       capture_output=True, text=True, timeout=120)
    try:
        d = (json.loads(p.stdout) or [{}])[0]
    except (json.JSONDecodeError, IndexError):
        return None, None
    lat, lon = d.get("GPSLatitude"), d.get("GPSLongitude")
    if (lat is None or lon is None) and d.get("GPSCoordinates"):
        parts = str(d["GPSCoordinates"]).replace(",", " ").split()
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            pass
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def write_gps(path: Path, lat: float | None, lon: float | None) -> None:
    """Konumu yazar (lat/lon None → konumu siler). Başarısızlıkta WriteError; dosya o durumda değişmemiş olur."""
    if not available():
        raise WriteError("exiftool bulunamadı")
    if not writable(path):
        raise WriteError(f"{path.suffix or 'Bu'} biçimine konum yazılamıyor")
    st = os.stat(path)
    try:
        proc = subprocess.run(
            ["exiftool", "-q", "-m", "-P", "-overwrite_original_in_place", "-n", "-charset", "filename=utf8",
             "-api", "LargeFileSupport=1", *_args(path, lat, lon), str(path)],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise WriteError(lines[-1][:300] if lines else "exiftool dosyayı güncelleyemedi")
        got = read_gps(path)
    finally:
        _restore_times(path, st)
    if lat is None:
        if got[0] is not None:
            raise WriteError("Konum dosyadan silinemedi")
    elif got[0] is None or abs(got[0] - lat) > 1e-4 or abs(got[1] - lon) > 1e-4:
        raise WriteError("Yazılan konum dosyadan doğrulanamadı")


def _restore_times(path: Path, st: os.stat_result) -> None:
    """Oluşturma, değiştirilme ve erişim zamanlarını yazımdan önceki haline (nanosaniye) getirir."""
    birth = getattr(st, "st_birthtime", None)
    if birth is not None and sys.platform == "darwin":
        _set_birthtime(path, int(round(birth * 1e9)) if not hasattr(st, "st_birthtime_ns") else st.st_birthtime_ns)
    try:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


class _AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16), ("commonattr", ctypes.c_uint32),
                ("volattr", ctypes.c_uint32), ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


class _Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


def _set_birthtime(path: Path, ns: int) -> None:
    """macOS oluşturma tarihini setattrlist ile ayarlar (en iyi çaba)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        al = _AttrList(5, 0, 0x00000200, 0, 0, 0, 0)  # ATTR_CMN_CRTIME
        ts = _Timespec(ns // 1_000_000_000, ns % 1_000_000_000)
        libc.setattrlist(os.fsencode(str(path)), ctypes.byref(al), ctypes.byref(ts), ctypes.sizeof(ts), 0)
    except (OSError, AttributeError):
        pass
