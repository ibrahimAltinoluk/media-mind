from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

HAVE_EXIFTOOL = shutil.which("exiftool") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture(autouse=True)
def mm_home(tmp_path, monkeypatch):
    home = tmp_path / "_mmhome"
    monkeypatch.setenv("MEDIAMIND_HOME", str(home))
    monkeypatch.setenv("MEDIAMIND_NO_KEYCHAIN", "1")  # testler gerçek Anahtar Zinciri'ne dokunmaz
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return home


@pytest.fixture
def archive(tmp_path) -> Path:
    d = tmp_path / "Arsiv"
    d.mkdir()
    return d


def make_image(path: Path, seed: int, size=(1600, 1200), date: str | None = None, quality: int = 92,
               gps: tuple[float, float] | None = None, fmt: str | None = None) -> Path:
    """Tohuma göre farklı, yapılı (pHash için anlamlı) bir görüntü üretir."""
    rng = np.random.default_rng(seed)
    w, h = 64, 48
    base = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    im = Image.fromarray(base).resize(size, Image.Resampling.BICUBIC)
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if date:
        exif.get_ifd(0x8769)[0x9003] = date
        exif[0x0110] = "iPhone 15 Pro"
        exif[0x010F] = "Apple"
    kwargs = {"quality": quality}
    if date:
        kwargs["exif"] = exif.tobytes()
    im.save(path, fmt or None, **kwargs)
    if gps and HAVE_EXIFTOOL:
        lat, lon = gps
        subprocess.run(
            ["exiftool", "-q", "-overwrite_original", f"-GPSLatitude={abs(lat)}", f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
             f"-GPSLongitude={abs(lon)}", f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}", str(path)],
            check=True,
        )
    return path


def resized_copy(src: Path, dst: Path, size=(800, 600), quality: int = 60) -> Path:
    with Image.open(src) as im:
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(dst, quality=quality)
    return dst


def make_video(path: Path, seconds: float = 2.0, size: str = "320x240", bitrate: str = "800k", seed_color: str = "testsrc") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{seed_color}=size={size}:rate=25", "-t", str(seconds),
         "-c:v", "libx264", "-b:v", bitrate, "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def tracked_repo(archive):
    """Tarihli, konumlu birkaç fotoğraf + bir kopya; takibe alınmış ve analiz edilmiş repo."""
    import shutil as _sh

    from mediamind import analyze, scan
    from mediamind.repo import Repo

    make_image(archive / "DCIM" / "IMG_1.jpg", 1, date="2024:08:14 19:42:00", gps=(37.0344, 27.4305))
    make_image(archive / "DCIM" / "IMG_2.jpg", 2, date="2024:08:15 10:00:00", gps=(37.0344, 27.4305))
    make_image(archive / "DCIM" / "IMG_3.jpg", 3, date="2023:07:02 18:05:00", gps=(38.4237, 27.1428))
    (archive / "Downloads").mkdir()
    _sh.copy2(archive / "DCIM" / "IMG_1.jpg", archive / "Downloads" / "IMG_1 (1).jpg")
    repo = Repo.init(archive, "Aile")
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    return repo
