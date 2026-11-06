import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mediamind import analyze, scan
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, HAVE_FFMPEG
from .test_smart import REAL_MODELS, _have_models

skimage = pytest.importorskip("skimage")
pytestmark = pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_EXIFTOOL), reason="ffmpeg ve exiftool gerekli")
TOKEN = "t"


def two_scene_video(path: Path, tmp: Path):
    """4 sn astronot + 4 sn kahve; tarih ve kamera bilgisi yazılır."""
    from skimage import data

    a, c = tmp / "a.png", tmp / "c.png"
    Image.fromarray(data.astronaut()).save(a)
    Image.fromarray(data.coffee()).resize((512, 400)).save(c)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "4", "-i", str(a), "-loop", "1", "-t", "4", "-i", str(c),
                    "-filter_complex", "[0]scale=640:640,setsar=1[x];[1]scale=640:640,setsar=1[y];[x][y]concat=n=2:v=1:a=0",
                    "-r", "25", "-g", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-api", "QuickTimeUTC=1", "-CreateDate=2024:08:15 18:10:00+03:00", str(path)], check=True)


@pytest.mark.skipif(not _have_models(), reason="modeller gerekli")
def test_video_scenes_people_and_moments(archive, tmp_path, monkeypatch):
    from mediamind.ml import people, videoan

    monkeypatch.setenv("MEDIAMIND_MODELS", str(REAL_MODELS))
    two_scene_video(archive / "VID_1.mp4", tmp_path)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    res = analyze.analyze(repo)
    assert res["video"] == 1
    aid = repo.db.execute("SELECT asset_id FROM files").fetchone()[0]
    info = videoan.video_info(repo, aid)
    assert len(info["scenes"]) == 2
    assert 3500 <= info["scenes"][1]["start_ms"] <= 4500
    assert videoan.frame_thumb(repo, repo.db.execute("SELECT blake3 FROM assets").fetchone()[0], info["scenes"][0]["key_ms"]).exists()
    faces = repo.db.execute("SELECT id, frame_ms FROM faces WHERE asset_id=?", (aid,)).fetchall()
    assert faces and all(f["frame_ms"] < 4500 for f in faces)  # yüz yalnız ilk sahnede
    people.assign_face(repo, faces[0]["id"], "Eileen")
    people.recluster(repo)
    info = videoan.video_info(repo, aid)
    seg = info["people"][0]["segments"]
    assert info["people"][0]["name"] == "Eileen" and seg[0][0] < 1000 and 3000 <= seg[-1][1] <= 6000
    m = videoan.moments(repo, aid, "bir fincan kahve")
    assert len(m) == 1 and 3500 <= m[0]["ms"] <= 4500 and m[0]["end_ms"] >= 7000  # tek aralık: kahve sahnesi
    tags = {r[0] for r in repo.db.execute("SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=?", (aid,))}
    assert "insan" in tags or "içecek" in tags


def wait(client, job):
    for _ in range(600):
        j = client.get(f"/jobs/{job['id']}").json()
        if j["status"] in ("done", "failed"):
            assert j["status"] == "done", j["error"]
            return j
        time.sleep(0.05)
    raise AssertionError("iş bitmedi")


def test_proxy_snapshot_and_clip_api(archive, tmp_path):
    from mediamind.api.server import create_app

    two_scene_video(archive / "kamera" / "VID_1.mp4", tmp_path)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "3",
                    "-c:v", "mpeg4", str(archive / "eski.avi")], check=True)
    c = TestClient(create_app(TOKEN), headers={"x-mm-token": TOKEN})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    wait(c, c.post(f"/repos/{rid}/scan").json())
    wait(c, c.post(f"/repos/{rid}/track", json={}).json())
    items = {i["rel_path"]: i for i in c.get(f"/repos/{rid}/assets").json()["items"]}
    avi, mp4 = items["eski.avi"], items["kamera/VID_1.mp4"]

    # mp4 doğrudan oynar (Range destekli); avi önizleme kopyası ister
    r = c.get(f"/repos/{rid}/play/{mp4['blake3']}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert c.get(f"/repos/{rid}/play/{avi['blake3']}").status_code == 409
    assert c.get(f"/repos/{rid}/assets/{avi['asset_id']}/video").json()["playable"] is False
    wait(c, c.post(f"/repos/{rid}/proxy/{avi['blake3']}").json())
    r = c.get(f"/repos/{rid}/play/{avi['blake3']}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"

    # Kırpma şeridi: n kare yan yana, önbelleğe alınır
    r = c.get(f"/repos/{rid}/strip/{mp4['blake3']}", params={"n": 8})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(r.content)).size == (160 * 8, 90)

    # Kareyi fotoğraf yap: yeni dosya kaynağın yanında, tarih karenin anına göre, takipte
    j = wait(c, c.post(f"/repos/{rid}/assets/{mp4['asset_id']}/snapshot", json={"ms": 5000}).json())
    rel = j["result"]["rel_path"]
    assert rel == "kamera/VID_1_kare_00-05.jpg"
    snap = next(i for i in c.get(f"/repos/{rid}/assets").json()["items"] if i["rel_path"] == rel)
    assert snap["kind"] == "photo" and snap["taken_at"] == "2024-08-15T18:10:05"

    # Videonun sonundaki an istenirse son kare alınır
    j = wait(c, c.post(f"/repos/{rid}/assets/{mp4['asset_id']}/snapshot", json={"ms": 8000}).json())
    assert j["result"]["rel_path"].startswith("kamera/VID_1_kare_00-0")

    # Klip
    j = wait(c, c.post(f"/repos/{rid}/assets/{mp4['asset_id']}/clip", json={"start_ms": 1000, "end_ms": 3500}).json())
    clip = next(i for i in c.get(f"/repos/{rid}/assets", params={"kind": "video"}).json()["items"]
                if i["rel_path"] == j["result"]["rel_path"])
    assert 2300 <= clip["duration_ms"] <= 2700
    assert c.post(f"/repos/{rid}/assets/{mp4['asset_id']}/clip", json={"start_ms": 1000, "end_ms": 1100}).status_code == 400


def speech_video(path: Path, tmp: Path, text: str):
    """macOS Türkçe sesiyle (Yelda) konuşmalı video."""
    import shutil

    if not shutil.which("say"):
        pytest.skip("say komutu yok")
    aiff = tmp / "s.aiff"
    subprocess.run(["say", "-v", "Yelda", "-o", str(aiff), text], check=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=10", "-i", str(aiff),
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_transcript_search(archive, tmp_path, monkeypatch):
    from mediamind.ml import models, transcribe
    from mediamind import tools, settings

    monkeypatch.setenv("MEDIAMIND_MODELS", str(REAL_MODELS))
    if not models.installed(models.WHISPER):
        pytest.skip("Whisper modeli yok")
    speech_video(archive / "dogum-gunu.mp4", tmp_path, "Doğum günün kutlu olsun Ayşe. Pastayı şimdi kesiyoruz.")
    repo = Repo.init(archive)
    repo.config["analysis"]["transcripts"] = True
    repo.save_config()
    repo = Repo(archive)
    scan.scan(repo)
    analyze.track(repo)
    res = analyze.analyze(repo)
    assert res["transcript"] == 1
    aid = repo.db.execute("SELECT asset_id FROM files").fetchone()[0]
    tr = transcribe.transcript(repo, aid)
    text = " ".join(s["text"] for s in tr["segments"]).lower()
    assert tr["lang"] == "tr" and "doğum" in text and "pasta" in text
    ctx = tools.ToolContext(source="assistant", default_repo=repo.id, settings=settings.load())
    assert tools.run(ctx, "search_media", {"text": "pasta"})["total"] == 1
    det = tools.run(ctx, "get_video_details", {"file_id": repo.db.execute("SELECT id FROM files").fetchone()[0]})
    assert det["language"] == "tr" and det["segments"]
