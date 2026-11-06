"""Arka plan işleri: aynı türden bekleyen iş varsa yenisi eklenmez (ör. ardışık adlandırmalarda tek yeniden gruplama)."""

import threading
import time

from mediamind.api.jobs import JobManager


def test_submit_coalesced_merges_queued_jobs():
    jm = JobManager()
    gate = threading.Event()
    runs = []

    def slow(pr):
        gate.wait(5)
        runs.append("slow")

    def work(pr):
        runs.append("work")

    first = jm.submit_coalesced("people", "r", "grupla", slow, lane="people")
    time.sleep(0.1)  # ilk iş çalışmaya başladı
    second = jm.submit_coalesced("people", "r", "grupla", work, lane="people")
    third = jm.submit_coalesced("people", "r", "grupla", work, lane="people")
    other_repo = jm.submit_coalesced("people", "x", "grupla", work, lane="people")
    assert first.id != second.id and second.id == third.id  # çalışan işin ardından yalnız bir tane sırada
    assert other_repo.id != second.id
    gate.set()
    for _ in range(50):
        if all(j.status == "done" for j in (first, second, other_repo)):
            break
        time.sleep(0.05)
    assert runs.count("work") == 2 and runs.count("slow") == 1
