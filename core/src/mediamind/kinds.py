"""Desteklenen medya türleri."""

from __future__ import annotations

RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf", ".srw", ".pef"}
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".avif",
} | RAW_EXTS
VIDEO_EXTS = {
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm", ".mpg", ".mpeg",
}
SIDECAR_EXTS = {".aae", ".xmp"}

# Taşıma/silmede kaliteyi belirlemek için kaba format sıralaması (yüksek = iyi)
FORMAT_RANK = {**{e: 3 for e in RAW_EXTS}, ".heic": 2, ".heif": 2, ".png": 2, ".tif": 2, ".tiff": 2}


def ext_of(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i > 0 else ""


def kind_of(name: str) -> str | None:
    if name.rsplit("/", 1)[-1].startswith("._"):  # macOS AppleDouble (exFAT/FAT disklerde her dosyanın yanında oluşur), medya değil
        return None
    e = ext_of(name)
    if e in PHOTO_EXTS:
        return "photo"
    if e in VIDEO_EXTS:
        return "video"
    return None


def is_raw(name: str) -> bool:
    return ext_of(name) in RAW_EXTS
