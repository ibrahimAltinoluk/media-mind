"""Yarım kalan işlerin kaldığı yerden sürmesi: tarama, plan uygulama, analiz; iş durdurma."""

import time

import pytest
from fastapi.testclient import TestClient

from mediamind import plans, scan
from mediamind.api.jobs import JobManager
from mediamind.api.server import create_app
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, make_image

TOKEN = "t"


class Stop(Exception):
    pass


def test_interrupted_scan_keeps_progress(archive, monkeypatch):
    for i in range(7):
        make_image(archive / f"IMG_{i}.jpg", i + 1)
    repo = Repo.init(archive)
    monkeypatch.setattr(scan, "SCAN_BATCH", 2)
    calls = {"n": 0}

    def pr(stage, d, t):
        if stage == "hash":
            calls["n"] += 1
            if calls["n"] == 5:
                raise Stop()

    with pytest.raises(Stop):
        scan.scan(repo, pr)
    saved = repo.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert saved == 4  # iki parça kaydedildi, yarım parça kayboldu (sonra yeniden okunur)
    r = scan.scan(repo)
    assert len(r.new) == 3 and repo.db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 7


def test_job_cancel():
    jm = JobManager()

    def fn(pr):
        for i in range(200):
            pr("x", i, 200)
            time.sleep(0.01)
        return "bitti"

    j = jm.submit("t", "r", "deneme", fn)
    time.sleep(0.05)
    jm.cancel(j.id)
    for _ in range(100):
        if jm.get(j.id).status not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert jm.get(j.id).status == "cancelled"


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_interrupted_plan_resumes(archive):
    for i in range(3):
        make_image(archive / "a" / f"IMG_{i}.jpg", i + 1)
    repo = Repo.init(archive)
    scan.scan(repo)
    ids = [r["id"] for r in repo.db.execute("SELECT id, rel_path FROM files ORDER BY rel_path")]
    pid = plans.create(repo, "move", [("move", fid, f"a/IMG_{i}.jpg", f"b/IMG_{i}.jpg") for i, fid in enumerate(ids)])
    # Uygulama ilk dosyadan sonra kapanmış gibi: plan 'running', ilk öğe yapıldı
    with repo.db as conn:
        conn.execute("UPDATE plans SET status='running' WHERE id=?", (pid,))
    (archive / "b").mkdir()
    (archive / "a" / "IMG_0.jpg").rename(archive / "b" / "IMG_0.jpg")
    first = plans.items(repo, pid)[0]["id"]
    with repo.db as conn:
        conn.execute("UPDATE plan_items SET status='running' WHERE id=?", (first,))
    assert plans.recover_interrupted(repo) == 1
    assert plans.get(repo, pid)["status"] == "interrupted"
    res = plans.apply(repo, pid)
    assert res.status == "applied" and res.done == 3
    assert sorted(p.name for p in (archive / "b").iterdir() if not p.name.startswith("._")) == ["IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg"]


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_scan_resumes_pending_analysis(archive):
    make_image(archive / "IMG_1.jpg", 1, date="2024:08:14 19:42:00")
    c = TestClient(create_app(TOKEN), headers={"x-mm-token": TOKEN})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]

    def wait(job):
        for _ in range(400):
            j = c.get(f"/jobs/{job['id']}").json()
            if j["status"] not in ("queued", "running"):
                return j
            time.sleep(0.05)
        raise AssertionError("iş bitmedi")

    wait(c.post(f"/repos/{rid}/scan").json())
    # Takibe al ama analiz etme (analiz yarıda kalmış gibi)
    wait(c.post(f"/repos/{rid}/track", json={"analyze_after": False}).json())
    assert c.get(f"/repos/{rid}/summary").json()["analysis_pending"] == 1
    wait(c.post(f"/repos/{rid}/scan").json())
    an = [j for j in c.get("/jobs").json() if j["type"] == "analyze" and j["repo_id"] == rid]
    assert an, "tarama sonrası bekleyen analiz kendiliğinden başlamalı"
    wait(an[0])
    assert c.get(f"/repos/{rid}/summary").json()["analysis_pending"] == 0


def test_scan_does_not_hold_lock_while_hashing(archive, monkeypatch):
    """Özet hesaplanırken repo kilidi serbest: başka bir yazma işlemi beklemeden kilidi alabilir."""
    import threading

    from mediamind import scan as scan_mod
    from mediamind.repo import Repo

    from .conftest import make_image

    for i in range(3):
        make_image(archive / f"p{i}.jpg", i + 1)
    repo = Repo.init(archive)
    got = []
    real = scan_mod.hash_file

    def slow_hash(p):
        t = threading.Thread(target=lambda: got.append(_try_lock(archive)))
        t.start()
        t.join()
        return real(p)

    monkeypatch.setattr(scan_mod, "hash_file", slow_hash)
    scan_mod.scan(repo, workers=1)
    assert got and all(got)


def _try_lock(archive) -> bool:
    import fcntl

    with open(archive / ".mediamind" / "lock", "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
