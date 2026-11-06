"""Fotoğraftaki yazı (OCR): macOS Vision çerçevesi ile tamamen yerel ve hızlı.

Sonuç asset_text tablosuna yazılır; Kütüphane araması, asistan ve "fatura", "pasaport" gibi sorgular bunu kullanır.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..kinds import is_raw
from ..media.geo import fold
from ..repo import Repo

MODEL_VERSION = "vision-accurate-tr-en"
LANGS = ["tr-TR", "en-US"]
Progress = Callable[[str, int, int], None]


def available() -> bool:
    try:
        import Vision  # noqa: F401
    except Exception:  # noqa: BLE001 - macOS dışı ya da pyobjc yok
        return False
    return True


def recognize(path: Path) -> str:
    """Görüntüdeki yazıyı satır satır döndürür (yoksa boş metin). EXIF yönü Vision tarafından uygulanır."""
    import Vision
    from Foundation import NSURL

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(LANGS)
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    ok, _err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError("Yazı tanıma başarısız")
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if cands and cands[0].confidence() >= 0.3:
            lines.append(str(cands[0].string()))
    return "\n".join(lines)


def run_ocr(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None) -> int:
    conn = repo.db
    for n, r in enumerate(todo, 1):
        err = None
        try:
            src = repo.root / r["fs_path"]
            if is_raw(src.name):  # RAW: gömülü önizlemeden üretilen büyük önizleme
                src = repo.thumb_path(r["blake3"], "m")
            text = recognize(src).strip()
            with conn:
                if text:
                    conn.execute("INSERT OR REPLACE INTO asset_text(asset_id, text, fold) VALUES (?,?,?)",
                                 (r["id"], text, fold(text)))
                else:
                    conn.execute("DELETE FROM asset_text WHERE asset_id=?", (r["id"],))
        except Exception as e:  # noqa: BLE001 - okunamayan dosya analizi durdurmaz
            err = f"{type(e).__name__}: {e}"
        with conn:
            mark(conn, r["id"], "ocr", err)
        if progress:
            progress("ocr", n, len(todo))
    return len(todo)
