"""Önizleme üretimi ve algısal hash.

Boyutlar (uzun kenar): s=256 (ızgara), m=1024 (görüntüleyici ilk kare).
l (2560) disk tasarrufu için görüntüleyicide talep üzerine üretilecek (Aşama 2).
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ..kinds import is_raw

SIZES = {"s": 256, "m": 1024, "l": 2560}
EAGER_SIZES = ("s", "m")

_heif_ready = False


def ensure_heif() -> None:
    global _heif_ready
    if not _heif_ready:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        _heif_ready = True


@dataclass
class ThumbResult:
    phash: str | None
    dhash: str | None
    blur: float | None
    width: int | None
    height: int | None


def _raw_preview(path: Path) -> Image.Image:
    for tag in ("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"):
        proc = subprocess.run(["exiftool", "-b", tag, str(path)], capture_output=True, timeout=60)
        if proc.returncode == 0 and len(proc.stdout) > 1000:
            im = Image.open(io.BytesIO(proc.stdout))
            im.load()
            return im
    raise ValueError("RAW dosyasında gömülü önizleme bulunamadı")


def _video_frame(path: Path, duration_ms: int | None) -> Image.Image:
    t = 1.0
    if duration_ms:
        t = min(max(duration_ms / 1000 * 0.1, 0.0), 5.0)
    for ts in (t, 0.0):
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{ts:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale='min(1024,iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, timeout=120,
        )
        if proc.returncode == 0 and proc.stdout:
            im = Image.open(io.BytesIO(proc.stdout))
            im.load()
            return im
    raise ValueError("Videodan kare alınamadı")


def open_visual(path: Path, kind: str, duration_ms: int | None = None, full: bool = False) -> Image.Image:
    """Dosyayı doğru yönde (EXIF yönü uygulanmış), RGB bir görüntü olarak açar. full=False iken JPEG hızlı küçültülür."""
    ensure_heif()
    if kind == "video":
        im = _video_frame(path, duration_ms)
    elif is_raw(path.name):
        im = _raw_preview(path)
    else:
        im = Image.open(path)
        if not full:
            im.draft("RGB", (SIZES["m"] * 2, SIZES["m"] * 2))  # JPEG için hızlı küçültme
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    return im


def blur_score(im: Image.Image) -> float:
    """Laplacian varyansı: düşük değer = bulanık."""
    g = im.convert("L")
    g.thumbnail((512, 512))
    lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(np.asarray(lap, dtype=np.float32).var())


def render(src: Path, kind: str, out_for: callable, duration_ms: int | None = None,
           sizes: tuple[str, ...] = EAGER_SIZES) -> ThumbResult:
    """Önizlemeleri üretir ve hash'leri hesaplar. out_for(size) → hedef yol."""
    im = open_visual(src, kind, duration_ms)
    w, h = im.size
    for size in sizes:
        target = out_for(size)
        target.parent.mkdir(parents=True, exist_ok=True)
        t = im.copy()
        t.thumbnail((SIZES[size], SIZES[size]), Image.Resampling.LANCZOS)
        if t.mode == "RGBA":
            bg = Image.new("RGB", t.size, (0, 0, 0))
            bg.paste(t, mask=t.split()[3])
            t = bg
        tmp = target.with_suffix(".tmp")
        t.save(tmp, "WEBP", quality=80, method=4)
        tmp.replace(target)
    rgb = im.convert("RGB")
    return ThumbResult(
        phash=str(imagehash.phash(rgb)),
        dhash=str(imagehash.dhash(rgb)),
        blur=blur_score(rgb),
        width=w if kind == "video" or is_raw(src.name) else None,
        height=h if kind == "video" or is_raw(src.name) else None,
    )
