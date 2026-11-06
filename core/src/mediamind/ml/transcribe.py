"""Konuşma dökümü: faster-whisper (CTranslate2, CPU int8) ile tamamen yerel."""

from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..media import video
from ..repo import Repo
from . import models

MODEL_VERSION = "whisper-small-int8"  # standart kademe
VERSIONS = {"standard": MODEL_VERSION, "high": "whisper-large-v3-turbo-int8"}
_lock = threading.Lock()
_models: dict[str, object] = {}

Progress = Callable[[str, int, int], None]


def _tier() -> str:
    from . import tiers

    return tiers.current("speech")


def current_version() -> str:
    return VERSIONS[_tier()]


def available() -> bool:
    from . import tiers

    return models.installed(tiers.spec("speech"))


def _get(tier: str | None = None):
    from . import tiers

    tier = tier or _tier()
    with _lock:
        if tier not in _models:
            from faster_whisper import WhisperModel

            _models.clear()  # büyük model: aynı anda tek model bellekte kalsın
            _models[tier] = WhisperModel(str(models.model_dir(tiers.spec("speech", tier))), device="cpu", compute_type="int8")
        return _models[tier]


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in video.probe(path).get("streams", []))


def transcribe_file(path: Path, tier: str | None = None, max_seconds: float | None = None) -> tuple[str | None, list[tuple[int, int, str]]]:
    """→ (dil, [(başlangıç_ms, bitiş_ms, metin)]). Ses ffmpeg ile 16 kHz mono WAV'a çevrilir (her format çalışır)."""
    with tempfile.TemporaryDirectory(prefix="mm-audio-") as td:
        wav = Path(td) / "a.wav"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                               *(["-t", str(max_seconds)] if max_seconds else []), str(wav)],
                              capture_output=True, timeout=3600)
        if proc.returncode != 0 or not wav.exists() or wav.stat().st_size < 2000:
            return None, []
        # WAV'ı doğrudan oku (faster-whisper'ın PyAV ile çözme yolunu kullanma)
        import wave

        import numpy as np

        with wave.open(str(wav), "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audio = pcm.astype(np.float32) / 32768.0
        segments, info = _get(tier).transcribe(audio, vad_filter=True, beam_size=5)
        out = [(int(s.start * 1000), int(s.end * 1000), s.text.strip()) for s in segments if s.text.strip()]
        return info.language, out


def run_transcripts(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None) -> int:
    conn = repo.db
    for n, r in enumerate(todo, 1):
        src = repo.root / r["fs_path"]
        err = None
        try:
            if has_audio(src):
                lang, segs = transcribe_file(src)
            else:
                lang, segs = None, []
            with conn:
                conn.execute("DELETE FROM transcripts WHERE asset_id=?", (r["id"],))
                conn.executemany("INSERT INTO transcripts(asset_id, idx, start_ms, end_ms, text, lang) VALUES (?,?,?,?,?,?)",
                                 [(r["id"], i, a, b, t, lang) for i, (a, b, t) in enumerate(segs)])
        except Exception as e:  # noqa: BLE001 - bozuk ses analizi durdurmaz
            err = f"{type(e).__name__}: {e}"
        with conn:
            mark(conn, r["id"], "transcript", err)
        if progress:
            progress("transcript", n, len(todo))
    return len(todo)


def transcript(repo: Repo, asset_id: int) -> dict:
    rows = repo.db.execute("SELECT start_ms, end_ms, text, lang FROM transcripts WHERE asset_id=? ORDER BY idx", (asset_id,)).fetchall()
    return {"lang": rows[0]["lang"] if rows else None, "segments": [{"start_ms": r["start_ms"], "end_ms": r["end_ms"], "text": r["text"]} for r in rows]}
