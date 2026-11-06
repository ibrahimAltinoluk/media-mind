import shutil
import time

import pytest
from fastapi.testclient import TestClient

from mediamind.api.server import create_app

from .conftest import HAVE_EXIFTOOL, HAVE_FFMPEG, make_image, make_video, resized_copy

TOKEN = "test-token"


@pytest.fixture
def client():
    return TestClient(create_app(TOKEN), headers={"x-mm-token": TOKEN})


def wait(client, job):
    for _ in range(300):
        j = client.get(f"/jobs/{job['id']}").json()
        if j["status"] in ("done", "failed"):
            assert j["status"] == "done", j["error"]
            return j
        time.sleep(0.05)
    raise AssertionError("iş bitmedi")


def test_auth_required():
    c = TestClient(create_app(TOKEN))
    assert c.get("/health").status_code == 200
    assert c.get("/repos").status_code == 401
    assert c.get("/repos", params={"t": TOKEN}).status_code == 200


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_full_flow(client, archive):
    make_image(archive / "DCIM" / "IMG_1.jpg", 1, date="2024:08:14 19:42:00", gps=(37.0344, 27.4305))
    shutil.copy2(archive / "DCIM" / "IMG_1.jpg", archive / "kopya.jpg")
    resized_copy(archive / "DCIM" / "IMG_1.jpg", archive / "WhatsApp Images" / "IMG-20240815-WA0012.jpg")
    make_image(archive / "DCIM" / "IMG_2.jpg", 2, date="2023:01:05 10:00:00")
    if HAVE_FFMPEG:
        make_video(archive / "DCIM" / "VID_1.mp4", seconds=2)

    pr = client.get("/probe", params={"path": str(archive)}).json()
    assert pr["photos"] == 4 and not pr["is_repo"]
    repo = client.post("/repos", json={"path": str(archive), "name": "Test"}).json()
    rid = repo["id"]
    assert client.get("/repos").json()[0]["name"] == "Test"

    res = wait(client, client.post(f"/repos/{rid}/scan").json())["result"]
    assert res["counts"]["copies"] == 1
    # Takipsizken de önizleme talep üzerine üretilir
    items = client.get(f"/repos/{rid}/assets", params={"scope": "untracked"}).json()["items"]
    assert client.get(f"/repos/{rid}/thumb/{items[0]['blake3']}/s").status_code == 200

    wait(client, client.post(f"/repos/{rid}/track", json={}).json())
    page = client.get(f"/repos/{rid}/assets").json()
    assert page["total"] == (5 if HAVE_FFMPEG else 4)
    first = next(i for i in page["items"] if i["rel_path"] == "DCIM/IMG_1.jpg")
    assert first["taken_at"].startswith("2024-08-14") and first["place"]

    detail = client.get(f"/repos/{rid}/assets/{first['asset_id']}").json()
    assert detail["camera_model"] == "iPhone 15 Pro"
    big = client.get(f"/repos/{rid}/thumb/{first['blake3']}/l")
    assert big.status_code == 200 and big.headers["content-type"] == "image/webp"
    rng = client.get(f"/repos/{rid}/original/{first['blake3']}", headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100

    f = client.get(f"/repos/{rid}/facets").json()
    assert {y["year"] for y in f["years"]} >= {"2024", "2023"}
    assert client.get(f"/repos/{rid}/assets", params={"year": "2023"}).json()["total"] == 1

    d = client.get(f"/repos/{rid}/dups").json()
    assert d["totals"]["near"]["files"] == 2
    del_ids = [m["file_id"] for g in d["groups"] for m in g["members"] if m["role"] == "delete"]
    plan = client.post(f"/repos/{rid}/plans", json={"kind": "dedup", "file_ids": del_ids}).json()
    assert plan["status"] == "pending" and plan["item_count"] == 2
    wait(client, client.post(f"/repos/{rid}/plans/{plan['id']}/apply").json())
    assert client.get(f"/repos/{rid}/trash").json()["files"] == 2
    wait(client, client.post(f"/repos/{rid}/plans/{plan['id']}/undo").json())
    assert client.get(f"/repos/{rid}/trash").json()["files"] == 0

    prev = client.get(f"/repos/{rid}/organize/preview", params={"template": "yil-ay"}).json()
    assert any(m["dst"] == "2024/08-Ağustos/IMG_1.jpg" for m in prev["moves"])
    plan = client.post(f"/repos/{rid}/plans", json={"kind": "organize", "template": "yil-ay"}).json()
    wait(client, client.post(f"/repos/{rid}/plans/{plan['id']}/apply").json())
    assert (archive / "2024" / "08-Ağustos" / "IMG_1.jpg").exists()
    plans = client.get(f"/repos/{rid}/plans").json()
    assert [p["status"] for p in plans] == ["applied", "undone"]

    assert client.patch(f"/repos/{rid}/assets/{first['asset_id']}", json={"favorite": True, "rating": 4}).status_code == 200
    assert client.get(f"/repos/{rid}/assets", params={"favorite": True}).json()["total"] == 2  # aynı içerikli 2 dosya

    assert client.delete(f"/repos/{rid}").json()["ok"]
    assert client.get("/repos").json() == []
    assert (archive / ".mediamind").exists()


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_folder_browsing(client, archive):
    make_image(archive / "kok.jpg", 1, date="2024:01:01 10:00:00")
    make_image(archive / "Tatil" / "a.jpg", 2, date="2024:08:14 19:42:00")
    make_image(archive / "Tatil" / "Bodrum" / "b.jpg", 3, date="2024:08:15 19:42:00")
    make_image(archive / "tatil2" / "c.jpg", 4, date="2023:08:15 19:42:00")
    rid = client.post("/repos", json={"path": str(archive), "name": "K"}).json()["id"]
    wait(client, client.post(f"/repos/{rid}/scan").json())

    root = client.get(f"/repos/{rid}/folders").json()
    assert [f["name"] for f in root["folders"]] == ["Tatil", "tatil2"]
    assert root["files"] == 1 and root["folders"][0]["count"] == 2 and root["folders"][0]["covers"]
    sub = client.get(f"/repos/{rid}/folders", params={"path": "Tatil"}).json()
    assert [f["path"] for f in sub["folders"]] == ["Tatil/Bodrum"] and sub["files"] == 1

    def names(**q):
        return sorted(i["rel_path"] for i in client.get(f"/repos/{rid}/assets", params={"scope": "all", **q}).json()["items"])
    assert names(direct=True) == ["kok.jpg"]
    assert names(folder="Tatil", direct=True) == ["Tatil/a.jpg"]
    assert names(folder="Tatil") == ["Tatil/Bodrum/b.jpg", "Tatil/a.jpg"]  # büyük/küçük harf ayrımı: tatil2 dahil değil


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_display_applies_orientation(client, archive):
    import io

    from PIL import Image

    im = Image.new("RGB", (300, 200), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # 90° saat yönünde döndürülmüş çekim
    im.save(archive / "dik.jpg", "JPEG", exif=exif)
    make_image(archive / "duz.jpg", 3)
    rid = client.post("/repos", json={"path": str(archive)}).json()["id"]
    wait(client, client.post(f"/repos/{rid}/scan").json())
    wait(client, client.post(f"/repos/{rid}/track", json={}).json())
    items = {i["rel_path"]: i for i in client.get(f"/repos/{rid}/assets").json()["items"]}

    r = client.get(f"/repos/{rid}/display/{items['dik.jpg']['blake3']}")
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (200, 300)  # dik gösterilir
    r = client.get(f"/repos/{rid}/display/{items['duz.jpg']['blake3']}")
    assert r.content == (archive / "duz.jpg").read_bytes()  # yönü düz: orijinal olduğu gibi
