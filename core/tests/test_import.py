"""Telefondan/karttan içe aktarma: şablon, yeni olanları belirleme, kopyalama, tekrarları atlama, karttan silme."""

import time

import pytest
from fastapi.testclient import TestClient

from mediamind import devices, importer, scan
from mediamind.api.server import create_app
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, make_image

pytestmark = pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")


def test_render_template():
    assert importer.render(importer.DEFAULT_TEMPLATE, "2025-07-14T10:00:00", "IMG_1.HEIC", "Pixel") == "2025/07-Temmuz/IMG_1.HEIC"
    assert importer.render("{cihaz}/{yıl}/{dosya}", None, "a.jpg", "iPhone/X") == "iPhone_X/_tarihsiz/a.jpg"
    with pytest.raises(Exception):
        importer.render("../{dosya}", None, "a.jpg", "x")


def _card(tmp_path):
    dcim = tmp_path / "SDKART" / "DCIM" / "100CANON"
    make_image(dcim / "IMG_0001.JPG", 1, date="2025:07:14 10:00:00")
    make_image(dcim / "IMG_0002.JPG", 2, date="2025:08:02 18:30:00")
    (dcim / "IMG_0002.AAE").write_text("<plist/>")
    return tmp_path / "SDKART" / "DCIM"


def test_import_from_card_skips_existing_and_deletes(archive, tmp_path):
    dcim = _card(tmp_path)
    make_image(archive / "eski" / "zaten.jpg", 1, date="2025:07:14 10:00:00")  # IMG_0001 ile aynı içerik
    repo = Repo.init(archive)
    scan.scan(repo)
    src = {"kind": "card", "id": "vol:test", "name": "SDKART", "path": str(dcim)}
    items = importer.mark_new(repo, src["id"], devices.card_items(dcim))
    assert sorted(i["name"] for i in items) == ["IMG_0001.JPG", "IMG_0002.JPG"]
    res = importer.import_items(repo, src, [i["key"] for i in items], delete_after=True)
    assert res["imported"] == 1 and res["duplicates"] == 1 and not res["failed"]
    assert res["paths"][0].endswith("IMG_0002.JPG")
    placed = archive / res["paths"][0]
    assert placed.exists() and placed.with_suffix(".AAE").exists()  # yan dosya da geldi
    row = repo.db.execute("SELECT tracked FROM files WHERE rel_path=?", (res["paths"][0],)).fetchone()
    assert row and row[0] == 1  # kütüphaneye eklendi
    assert not list(dcim.rglob("*.JPG"))  # doğrulananlar karttan silindi
    # Aynı kart yeniden takılınca hepsi "aktarılmış" görünür
    make_image(dcim / "100CANON" / "IMG_0002.JPG", 2, date="2025:08:02 18:30:00")
    again = importer.mark_new(repo, src["id"], devices.card_items(dcim))
    assert all(i["imported"] for i in again)


def test_import_api_with_card(archive, tmp_path, monkeypatch):
    dcim = _card(tmp_path)
    fake = [{"id": "vol:SDKART", "name": "SDKART", "product": "Hafıza kartı", "ready": True, "locked": False,
             "count": None, "kind": "card", "path": str(dcim)}]
    monkeypatch.setattr(devices, "card_volumes", lambda *a, **k: fake)
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    assert any(d["id"] == "vol:SDKART" for d in c.get("/devices").json()["devices"])
    items = c.get("/devices/items", params={"device": "vol:SDKART", "repo": rid}).json()["items"]
    assert len(items) == 2 and not any(i["imported"] for i in items)
    assert c.get("/devices/thumb", params={"device": "vol:SDKART", "key": items[0]["key"]}).headers["content-type"] == "image/jpeg"
    job = c.post(f"/repos/{rid}/import", json={"device": "vol:SDKART", "keys": [i["key"] for i in items],
                                               "template": "{yıl}/{dosya}"}).json()
    for _ in range(200):
        j = c.get(f"/jobs/{job['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    assert j["result"]["imported"] == 2 and sorted(j["result"]["paths"]) == ["2025/IMG_0001.JPG", "2025/IMG_0002.JPG"]
    assert all(i["imported"] for i in c.get("/devices/items", params={"device": "vol:SDKART", "repo": rid}).json()["items"])


def test_phone_media_filter():
    from mediamind.devices import is_user_media
    ok = [("DCIM/Camera", "20240101_101010.jpg", 3_000_000), ("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images", "IMG-1.jpg", 90_000),
          ("Internal storage/DCIM/Camera", "v.mp4", 9_000_000), ("Pictures/Screenshots", "s.png", 300_000)]
    bad = [("Android/data/com.ovelin.guitartuna/files/HTTPCache", "528.v3.exo", 843),
           ("Android/data/ru.yandex.yandexnavi/cache", "a.jpg", 50_000), ("Pictures/.thumbnails", "1.jpg", 20_000),
           ("Android/.Trash/com.sec.android.gallery3d", "x.jpg", 2_000_000), ("Android/media/com.whatsapp/WhatsApp/Media/.Links", "l.jpg", 40_000),
           ("Card/Android/data/x", "a.jpg", 50_000), ("DCIM/Camera", "tiny.jpg", 800), ("Download", "log.txt", 5000)]
    assert [is_user_media(*a) for a in ok] == ["photo", "photo", "video", "photo"]
    assert all(is_user_media(*b) is None for b in bad)
