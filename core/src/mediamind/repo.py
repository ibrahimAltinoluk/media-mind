"""Repo: bir klasörün kökündeki .mediamind/ dizini ve onun etrafındaki işlemler."""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
import threading
import time
import tomllib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import tomli_w

from . import db, registry
from .paths import MM_DIR

DEFAULT_CONFIG: dict[str, Any] = {
    "analysis": {
        "metadata": True,
        "dedup": True,
        "content": True,
        "faces": True,
        "video_scenes": False,
        "transcripts": False,
        "ocr": True,
        "pets": True,
    },
    "dedup": {
        "keep_rule": "quality",
        "primary_folders": ["DCIM"],
        "near_threshold": 6,
    },
    "scan": {"auto_track": False, "watch": True},
    "trash": {"auto_days": 0},  # >0: bu kadar günden eski silinenler kendiliğinden kalıcı silinir
}

SUBDIRS = ["thumbs", "proxies", "faces", "trash", "logs"]


class RepoError(Exception):
    pass


class RepoLockedError(RepoError):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Repo:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.mm = self.root / MM_DIR
        toml = self.mm / "repo.toml"
        if not toml.exists():
            raise RepoError(f"{self.root} bir MediaMind reposu değil.")
        raw = tomllib.loads(toml.read_text("utf-8"))
        self.config: dict[str, Any] = _deep_merge(DEFAULT_CONFIG, raw)
        # Bağlantı ve kilit iş parçacığı başınadır (API birden çok iş parçacığından erişir)
        self._local = threading.local()
        self.lock_timeout: float | None = None  # API'de kilit beklemesi için ayarlanır

    # ---- kimlik -------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.config["id"]

    @property
    def name(self) -> str:
        return self.config.get("name") or self.root.name

    # ---- oluşturma ve bulma ----------------------------------------------------------
    @classmethod
    def init(cls, path: Path, name: str | None = None, register: bool = True) -> "Repo":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise RepoError(f"Klasör bulunamadı: {root}")
        if (root / MM_DIR).exists():
            raise RepoError(f"{root} zaten bir repo.")
        parent = cls.find_root(root.parent)
        if parent is not None:
            raise RepoError(f"{root}, {parent} reposunun içinde. İç içe repo oluşturulamaz.")
        mm = root / MM_DIR
        mm.mkdir()
        for d in SUBDIRS:
            (mm / d).mkdir()
        config = {
            "id": str(uuid.uuid4()),
            "name": name or root.name,
            "schema_version": db.SCHEMA_VERSION,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **DEFAULT_CONFIG,
        }
        (mm / "repo.toml").write_text(tomli_w.dumps(config), "utf-8")
        repo = cls(root)
        repo.db  # şemayı oluştur
        if register:
            registry.register(repo.id, repo.name, root)
        return repo

    @staticmethod
    def find_root(start: Path) -> Path | None:
        p = Path(start).expanduser().resolve()
        for d in [p, *p.parents]:
            if (d / MM_DIR / "repo.toml").exists():
                return d
        return None

    @classmethod
    def discover(cls, start: Path | None = None) -> "Repo":
        root = cls.find_root(start or Path.cwd())
        if root is None:
            raise RepoError("Bu klasörde ya da üstlerinde MediaMind reposu yok. Önce `mediamind init` çalıştırın.")
        return cls(root)

    @classmethod
    def open(cls, ref: str | None = None) -> "Repo":
        """ref: yol, kayıtlı repo adı ya da kimliği. None ise çalışma dizininden bulunur."""
        if ref is None:
            return cls.discover()
        p = Path(ref).expanduser()
        if p.exists():
            return cls.discover(p)
        entry = registry.find(ref)
        if entry is None:
            raise RepoError(f"Repo bulunamadı: {ref}")
        if not entry.online:
            moved = registry.relocate(entry)
            if moved is None:
                raise RepoError(f"'{entry.name}' çevrimdışı (disk takılı değil): {entry.last_path}")
            registry.register(entry.id, entry.name, moved)
            return cls(moved)
        return cls(Path(entry.last_path))

    # ---- veritabanı ve kilit ---------------------------------------------------------------
    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = db.connect(self.mm / "index.db")
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextlib.contextmanager
    def lock(self, timeout: float | None = None) -> Iterator[None]:
        """Yazma işlemleri için repo kilidi (aynı iş parçacığında iç içe kullanılabilir).

        timeout: kilit meşgulse en fazla bu kadar saniye bekle (None = hiç bekleme).
        """
        loc = self._local
        depth = getattr(loc, "depth", 0)
        if depth == 0:
            fh = open(self.mm / "lock", "a+")
            wait = self.lock_timeout if timeout is None else timeout
            deadline = time.monotonic() + (wait or 0)
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        fh.close()
                        raise RepoLockedError("Repo şu anda başka bir işlem tarafından kullanılıyor.")
                    time.sleep(0.1)
            loc.fh = fh
        loc.depth = depth + 1
        try:
            yield
        finally:
            loc.depth -= 1
            if loc.depth == 0 and getattr(loc, "fh", None) is not None:
                fcntl.flock(loc.fh, fcntl.LOCK_UN)
                loc.fh.close()
                loc.fh = None

    # ---- yollar ---------------------------------------------------------------------------
    def thumb_path(self, blake3: str, size: str) -> Path:
        return self.mm / "thumbs" / blake3[:2] / blake3[2:4] / f"{blake3}_{size}.webp"

    def trash_rel(self, plan_id: int, rel: str) -> str:
        return f"{MM_DIR}/trash/{plan_id}/{rel}"

    def save_config(self) -> None:
        (self.mm / "repo.toml").write_text(tomli_w.dumps(self.config), "utf-8")
