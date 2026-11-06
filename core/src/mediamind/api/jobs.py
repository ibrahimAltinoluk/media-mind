"""Arka plan işleri (tarama, analiz, plan uygulama) ve ilerleme durumu."""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: int
    type: str
    repo_id: str
    title: str
    status: str = "queued"  # queued | running | done | failed | cancelled
    lane: str = "main"
    stage: str = ""
    done: int = 0
    total: int = 0
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class JobCancelled(Exception):
    """İş kullanıcı isteğiyle durduruldu (ilerleme bildirimi sırasında, güvenli bir noktada)."""


class JobManager:
    """Repo başına sıralı çalışan işler (aynı repoya iki yazma işi aynı anda girmez)."""

    def __init__(self):
        self._ids = itertools.count(1)
        self._jobs: dict[int, Job] = {}
        self._repo_locks: dict[str, threading.Lock] = {}
        self._mx = threading.Lock()

    def submit(self, type_: str, repo_id: str, title: str, fn: Callable[[Callable[[str, int, int], None]], Any],
               lane: str = "main") -> Job:
        """Aynı repo + şerit (lane) içindeki işler sırayla çalışır; farklı şeritler eşzamanlı.

        Şeritler: main (tarama, planlar, dosya üretme) · analysis (uzun ML analizi) · media (önizleme kopyaları)
        """
        with self._mx:
            job = Job(id=next(self._ids), type=type_, repo_id=repo_id, title=title, lane=lane)
            self._jobs[job.id] = job
            lock = self._repo_locks.setdefault(f"{repo_id}:{lane}", threading.Lock())
            # Eski bitmiş işleri temizle
            done = [j for j in self._jobs.values() if j.finished_at and time.time() - j.finished_at > 600]
            for j in done:
                self._jobs.pop(j.id, None)

        def progress(stage: str, d: int, t: int) -> None:
            # İlerleme her zaman bir kaydın ardından bildirilir; durdurma burada güvenle yapılır
            if job.cancel_requested:
                raise JobCancelled()
            job.stage, job.done, job.total = stage, d, t

        def run() -> None:
            with lock:
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.finished_at = time.time()
                    return
                job.status = "running"
                job.started_at = time.time()
                try:
                    job.result = fn(progress)
                    job.status = "done"
                except JobCancelled:
                    job.status = "cancelled"
                except Exception as e:  # noqa: BLE001
                    job.status = "failed"
                    job.error = f"{e}"
                    traceback.print_exc()
                finally:
                    job.finished_at = time.time()

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def submit_coalesced(self, type_: str, repo_id: str, title: str, fn: Callable[[Callable[[str, int, int], None]], Any],
                         lane: str = "main") -> Job:
        """Aynı türden bekleyen (henüz başlamamış) bir iş varsa yenisini eklemez; o iş zaten son durumu işleyecektir.
        Çalışan iş varsa yenisi sıraya girer (çalışan iş, isteği gelmeden önceki veriyi görmüş olabilir)."""
        with self._mx:
            for j in self._jobs.values():
                if j.repo_id == repo_id and j.type == type_ and j.lane == lane and j.status == "queued" and not j.cancel_requested:
                    return j
        return self.submit(type_, repo_id, title, fn, lane)

    def cancel(self, job_id: int) -> Job | None:
        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_requested = True
        return job

    def active(self, repo_id: str, lane: str) -> bool:
        return any(j.repo_id == repo_id and j.lane == lane and j.status in ("queued", "running") for j in self._jobs.values())

    def get(self, job_id: int) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, repo_id: str | None = None) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)
        return [j for j in jobs if repo_id is None or j.repo_id == repo_id]

    def busy(self, repo_id: str) -> bool:
        return any(j.repo_id == repo_id and j.status in ("queued", "running") for j in self._jobs.values())
