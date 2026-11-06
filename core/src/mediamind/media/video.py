"""Video yardımcıları: oynatılabilirlik, önizleme kopyası (proxy), kare örnekleme, kare/klip çıkarma."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image

Progress = Callable[[str, int, int], None]

# WebKit (macOS) doğrudan oynatabildiği kapsayıcı + codec'ler
PLAYABLE_EXT = {".mp4", ".mov", ".m4v"}
PLAYABLE_CODECS = {"avc1", "avc3", "h264", "hvc1", "hev1", "hevc", "mp4v", "apcn", "apch", "apcs", "apco", "ap4h"}

MAX_FRAMES = 150


def playable(rel_path: str, codec: str | None) -> bool:
    ext = rel_path[rel_path.rfind("."):].lower()
    if ext not in PLAYABLE_EXT:
        return False
    return codec is None or codec.strip().lower() in PLAYABLE_CODECS


def probe(path: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                          capture_output=True, text=True, timeout=60)
    return json.loads(proc.stdout or "{}")


def _run_with_progress(cmd: list[str], duration_ms: int | None, stage: str, progress: Progress | None) -> None:
    """ffmpeg'i -progress çıktısıyla çalıştırır, ilerlemeyi bildirir."""
    proc = subprocess.Popen([*cmd[:1], "-progress", "pipe:1", "-nostats", *cmd[1:]], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    total = duration_ms or 0
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and progress and total:
            try:
                done = int(line.split("=", 1)[1]) // 1000
                progress(stage, min(done, total), total)
            except ValueError:
                pass
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg hatası: {err.strip()[-300:]}")


def make_proxy(src: Path, dst: Path, duration_ms: int | None, progress: Progress | None = None) -> Path:
    """Tarayıcıda oynatılabilen hafif kopya: H.264 + AAC, en fazla 1080p, hızlı başlatma."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part.mp4")
    _run_with_progress(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-map", "0:v:0", "-map", "0:a:0?",
         "-vf", "scale='min(1920,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)],
        duration_ms, "proxy", progress)
    tmp.replace(dst)
    return dst


_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def sample_frames(src: Path, duration_ms: int | None, width: int = 512) -> list[tuple[int, Image.Image]]:
    """Analiz için kareler: anahtar kareler çözülür (hızlı), aralarından eşit aralıklı en fazla MAX_FRAMES seçilir."""
    dur = (duration_ms or 0) / 1000
    out: list[tuple[int, Image.Image]] = []
    with tempfile.TemporaryDirectory(prefix="mm-frames-") as td:
        proc = subprocess.run(
            ["ffmpeg", "-v", "info", "-skip_frame", "nokey", "-i", str(src), "-an",
             "-vf", f"scale={width}:-2,showinfo", "-fps_mode", "passthrough", "-q:v", "4", f"{td}/%06d.jpg"],
            capture_output=True, text=True, timeout=3600)
        times = [float(m.group(1)) for m in _PTS_RE.finditer(proc.stderr)]
        files = sorted(Path(td).glob("*.jpg"))
        pairs = list(zip(times, files))
        if dur and pairs:
            step = max(1.0, dur / MAX_FRAMES)
            last = -1e9
            for t, f in pairs:
                if t - last >= step * 0.9:
                    with Image.open(f) as im:
                        out.append((int(t * 1000), im.convert("RGB")))
                    last = t
        elif pairs:
            for t, f in pairs[:MAX_FRAMES]:
                with Image.open(f) as im:
                    out.append((int(t * 1000), im.convert("RGB")))
    # Anahtar kare çok azsa (kısa video) birkaç noktadan doğrudan kare al
    if dur and len(out) < 4:
        have = {ms for ms, _ in out}
        for frac in (0.1, 0.35, 0.6, 0.85):
            ms = int(dur * frac * 1000)
            if any(abs(ms - h) < 500 for h in have):
                continue
            im = frame_at(src, ms, width)
            if im is not None:
                out.append((ms, im))
        out.sort(key=lambda x: x[0])
    return out


def frame_at(src: Path, ms: int, width: int | None = None) -> Image.Image | None:
    import io

    vf = ["-vf", f"scale={width}:-2"] if width else []
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ms / 1000:.3f}", "-i", str(src), "-frames:v", "1", *vf,
                           "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        return None
    im = Image.open(io.BytesIO(proc.stdout))
    im.load()
    return im.convert("RGB")


def save_snapshot(src: Path, ms: int, dst: Path, duration_ms: int | None = None) -> Path:
    """Videodaki kareyi tam çözünürlükte JPEG olarak kaydeder (videonun sonu istenirse son kareye çekilir)."""
    if duration_ms:
        ms = max(0, min(ms, duration_ms - 100))
    im = frame_at(src, ms)
    for back in (500, 1500):
        if im is not None:
            break
        im = frame_at(src, max(0, ms - back))
    if im is None:
        raise RuntimeError("Kare alınamadı")
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "JPEG", quality=94)
    return dst


def cut_clip(src: Path, start_ms: int, end_ms: int, dst: Path, progress: Progress | None = None) -> Path:
    """Klibi kare-doğru keser (yeniden kodlar): H.264 + AAC."""
    if end_ms <= start_ms:
        raise ValueError("Bitiş başlangıçtan sonra olmalı")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    _run_with_progress(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{start_ms / 1000:.3f}", "-i", str(src), "-t", f"{(end_ms - start_ms) / 1000:.3f}",
         "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)],
        end_ms - start_ms, "clip", progress)
    tmp.replace(dst)
    return dst


def fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}-{s % 3600 // 60:02d}-{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}-{s % 60:02d}"
