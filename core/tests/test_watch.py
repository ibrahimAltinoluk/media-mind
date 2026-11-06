"""Canlı izleme: klasör değişince artımlı tarama tetiklenir; .mediamind içi yok sayılır."""

import time

import pytest
from fastapi.testclient import TestClient

from mediamind import registry, watch
from mediamind.api.server import create_app
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, make_image


def _wait(pred, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_watch_service_triggers_on_media_changes(archive):
    repo = Repo.init(archive)
    registry.register(repo.id, "W", repo.root)
    calls: list[str] = []
    svc = watch.WatchService(calls.append, quiet=0.6, min_interval=0.2, sync=0.2)
    svc.start()
    try:
        assert _wait(lambda: repo.id in svc.watching())
        time.sleep(0.5)  # FSEvents akışı otursun
        # .mediamind içi ve ilgisiz dosyalar tetiklemez
        (archive / ".mediamind" / "thumbs").mkdir(exist_ok=True)
        (archive / ".mediamind" / "thumbs" / "x.webp").write_bytes(b"x")
        (archive / "notlar.txt").write_text("merhaba")
        time.sleep(2.0)
        assert calls == []
        make_image(archive / "yeni" / "IMG_1.jpg", 1)
        assert _wait(lambda: calls == [repo.id])
        (archive / "yeni" / "IMG_1.jpg").unlink()
        assert _wait(lambda: len(calls) == 2)
    finally:
        svc.stop()


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_new_file_appears_without_manual_scan(archive, monkeypatch):
    monkeypatch.setattr(watch, "QUIET_SECONDS", 0.6)
    monkeypatch.setattr(watch, "SYNC_SECONDS", 0.2)
    make_image(archive / "IMG_0.jpg", 1)
    with TestClient(create_app("t", watch=True), headers={"x-mm-token": "t"}) as c:
        rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
        c.patch(f"/repos/{rid}/config", json={"scan": {"auto_track": True}})
        assert _wait(lambda: rid in c.app.state.watcher.watching())
        time.sleep(0.5)
        make_image(archive / "Tatil" / "IMG_9.jpg", 9, date="2024:08:14 19:42:00")

        def tracked():
            items = c.get(f"/repos/{rid}/assets", params={"scope": "all"}).json()["items"]
            return any(i["rel_path"] == "Tatil/IMG_9.jpg" and i["tracked"] for i in items)

        assert _wait(tracked, 20)
        # Kendiliğinden eklendi ve analiz edildi (tarih okundu)
        assert _wait(lambda: any(i["rel_path"] == "Tatil/IMG_9.jpg" and i["taken_at"]
                                 for i in c.get(f"/repos/{rid}/assets").json()["items"]), 20)
