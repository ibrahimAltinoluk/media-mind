"""Yol yardımcıları: NFC normalleştirme ve repo kökü dışına çıkmayı engelleme."""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath

MM_DIR = ".mediamind"


def nfc(s: str) -> str:
    """macOS dosya adlarını NFD verebilir; DB'de her zaman NFC saklarız."""
    return unicodedata.normalize("NFC", s)


def to_rel(root: Path, path: Path) -> str:
    return nfc(path.relative_to(root).as_posix())


class UnsafePathError(ValueError):
    pass


def check_rel(rel: str, *, allow_internal: bool = False) -> PurePosixPath:
    """Göreli yolun repo içinde kaldığını doğrular."""
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or any(part in ("..", "") for part in p.parts):
        raise UnsafePathError(f"Geçersiz göreli yol: {rel!r}")
    if not allow_internal and p.parts[0] == MM_DIR:
        raise UnsafePathError(f"{MM_DIR}/ içine dosya yazılamaz: {rel!r}")
    return p


def join(root: Path, rel: str, *, allow_internal: bool = False) -> Path:
    p = check_rel(rel, allow_internal=allow_internal)
    full = root.joinpath(*p.parts)
    # Sembolik bağlantılarla dışarı kaçmayı da engelle (var olan üst dizin üzerinden).
    parent = full.parent
    while not parent.exists() and parent != root:
        parent = parent.parent
    resolved_root = root.resolve()
    resolved_parent = parent.resolve()
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise UnsafePathError(f"Repo kökü dışına çıkan yol: {rel!r}")
    return full


def human_bytes(n: int | float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if abs(f) < 1024 or u == units[-1]:
            if u == "B":
                return f"{int(f)} B"
            return f"{f:.1f} {u}".replace(".", ",")
        f /= 1024
    return f"{f:.1f} TB"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")
