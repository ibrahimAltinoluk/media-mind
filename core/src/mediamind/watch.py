"""Canlı izleme: kütüphane klasörlerindeki değişiklikleri macOS FSEvents ile yakalar.

Olaylar (ekleme, silme, taşıma, değişiklik) toplanır; klasör birkaç saniye sakinleşince ve değişen dosyaların
boyutu sabitlenince (kopyalama bitmiş) tek bir artımlı tarama tetiklenir. .mediamind/ içindeki değişiklikler
(önizlemeler, veritabanı, çöp) yok sayılır. Disk çıkarılırsa izleme durur, takılınca kendiliğinden yeniden başlar.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import registry
from .kinds import SIDECAR_EXTS, kind_of
from .paths import MM_DIR

QUIET_SECONDS = 3.0      # son olaydan sonra bu kadar sessizlik beklenir
MIN_INTERVAL = 5.0       # aynı kütüphane için iki otomatik tarama arası en az
SYNC_SECONDS = 10.0      # kayıtlı kütüphaneler / takılı diskler bu aralıkla yoklanır


def _relevant(root: Path, path: str, is_dir: bool) -> bool:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return False
    if rel.startswith("..") or rel == ".":
        return False
    first = rel.split(os.sep, 1)[0]
    if first == MM_DIR:
        return False
    name = os.path.basename(path)
    if name.startswith("._") or name == ".DS_Store":
        return False
    if is_dir:
        return not name.startswith(".")
    return kind_of(name) is not None or os.path.splitext(name)[1].lower() in SIDECAR_EXTS


@dataclass
class _Watch:
    repo_id: str
    root: Path
    observer: object
    lock: threading.Lock = field(default_factory=threading.Lock)
    changed: dict[str, int] = field(default_factory=dict)  # yol → olay anındaki boyut (-1: yok/klasör)
    last_event: float = 0.0
    last_trigger: float = 0.0

    def add(self, path: str, is_dir: bool) -> None:
        size = -1
        if not is_dir:
            try:
                size = os.stat(path).st_size
            except OSError:
                pass
        with self.lock:
            self.changed[path] = size
            self.last_event = time.monotonic()

    def settled(self) -> bool:
        """Değişen dosyaların boyutu son olaydan beri değişmediyse (kopyalama bitti) True."""
        with self.lock:
            items = list(self.changed.items())
        for path, size in items:
            if size < 0:
                continue
            try:
                now = os.stat(path).st_size
            except OSError:
                continue  # silinmiş: tarama "kayıp" olarak görür
            if now != size:
                with self.lock:
                    self.changed[path] = now
                    self.last_event = time.monotonic()
                return False
        return True


class WatchService:
    """Kayıtlı ve bağlı her kütüphaneyi izler; değişiklikte on_change(repo_id) çağrılır."""

    def __init__(self, on_change: Callable[[str], bool | None], quiet: float = QUIET_SECONDS,
                 min_interval: float = MIN_INTERVAL, sync: float = SYNC_SECONDS):
        self.on_change = on_change
        self.quiet, self.min_interval, self.sync_every = quiet, min_interval, sync
        self._watches: dict[str, _Watch] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sync = 0.0

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="mm-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        for w in list(self._watches.values()):
            self._unwatch(w.repo_id)

    def watching(self) -> list[str]:
        return list(self._watches)

    # ------------------------------------------------------------------ iç işleyiş
    def _watch(self, repo_id: str, root: Path) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        holder: dict[str, _Watch] = {}

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: ANN001
                if event.event_type in ("opened", "closed_no_write"):
                    return
                w = holder.get("w")
                if w is None:
                    return
                for p in (event.src_path, getattr(event, "dest_path", "") or ""):
                    p = os.fsdecode(p) if p else ""
                    if p and _relevant(root, p, event.is_directory):
                        w.add(p, event.is_directory)

        obs = Observer()
        obs.schedule(Handler(), str(root), recursive=True)
        obs.daemon = True
        obs.start()
        holder["w"] = self._watches[repo_id] = _Watch(repo_id, root, obs)

    def _unwatch(self, repo_id: str) -> None:
        w = self._watches.pop(repo_id, None)
        if w is None:
            return
        try:
            w.observer.stop()  # type: ignore[attr-defined]
            w.observer.join(timeout=2)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _sync(self) -> None:
        """Kayıtlı kütüphanelerle izlenenleri eşitle (eklenen/kaldırılan kütüphane, takılan/çıkarılan disk)."""
        want: dict[str, Path] = {}
        for e in registry.load():
            root = Path(e.last_path)
            if (root / MM_DIR / "repo.toml").exists():
                want[e.id] = root
        for rid in list(self._watches):
            w = self._watches[rid]
            if rid not in want or want[rid] != w.root or not w.observer.is_alive():  # type: ignore[attr-defined]
                self._unwatch(rid)
        for rid, root in want.items():
            if rid not in self._watches:
                try:
                    self._watch(rid, root)
                except Exception:  # noqa: BLE001 - izlenemeyen disk tarama ile yine güncellenir
                    pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            if now - self._last_sync >= self.sync_every:
                self._last_sync = now
                try:
                    self._sync()
                except Exception:  # noqa: BLE001
                    pass
            for w in list(self._watches.values()):
                with w.lock:
                    pending = bool(w.changed)
                    quiet = time.monotonic() - w.last_event
                if not pending or quiet < self.quiet or time.monotonic() - w.last_trigger < self.min_interval:
                    continue
                if not w.settled():
                    continue
                with w.lock:
                    batch = dict(w.changed)
                w.last_trigger = time.monotonic()
                try:
                    accepted = self.on_change(w.repo_id) is not False
                except Exception:  # noqa: BLE001
                    accepted = False
                if accepted:  # kabul edilmediyse (kütüphane meşgul) değişiklikler bekletilir, sonra yeniden denenir
                    with w.lock:
                        for k, v in batch.items():
                            if w.changed.get(k) == v:
                                del w.changed[k]
            self._stop.wait(0.5)
