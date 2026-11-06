"""Yeni kütüphane özellikleri: Türkçe yer adları, OCR araması, temizlik, anılar, albümler, düzeltmeler,
dışa aktarma, harita ve kalıcı silmede türetilmiş dosyaların temizlenmesi."""

import json
import subprocess
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from mediamind import analyze, library, plans, scan
from mediamind.api.server import create_app
from mediamind.media import geo
from mediamind.ml import ocr
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, make_image

pytestmark = pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")

IZMIR = (38.4237, 27.1428)
BODRUM = (37.0344, 27.4305)
ANKARA = (39.9334, 32.8597)


def _ready(archive) -> Repo:
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo, smart=False)
    return repo


def _exif_date(d: date, hour: int = 12) -> str:
    return f"{d.year:04d}:{d.month:02d}:{d.day:02d} {hour:02d}:00:00"


def test_geo_proper_names_and_fold():
    assert geo.proper("Zeytindag", "Izmir", "TR") == ("Zeytindağ", "İzmir")
    assert geo.fold("İzmir Şişli Ağaç") == "izmir sisli agac"
    assert geo.lookup([IZMIR])[0]["admin1"] == "İzmir"


def test_place_names_turkish_and_search(archive):
    make_image(archive / "a.jpg", 1, date="2024:08:14 19:42:00", gps=IZMIR)
    repo = _ready(archive)
    place = repo.db.execute("SELECT p.name, p.admin1 FROM assets a JOIN places p ON p.id=a.place_id").fetchone()
    assert place["admin1"] == "İzmir"
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    # Türkçe karakter yazmadan da bulunur
    assert c.get(f"/repos/{rid}/assets", params={"q": "izmir"}).json()["total"] == 1
    assert c.get(f"/repos/{rid}/assets", params={"q": "İZMİR"}).json()["total"] == 1


@pytest.mark.skipif(not ocr.available(), reason="macOS Vision gerekli")
def test_ocr_text_search(archive):
    im = Image.new("RGB", (1600, 900), "white")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
    d.text((80, 120), "Elektrik Faturası", fill="black", font=f)
    d.text((80, 260), "Ödeme tarihi: 15.08.2024", fill="black", font=f)
    im.save(archive / "belge.jpg", quality=92)
    make_image(archive / "manzara.jpg", 3)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)  # OCR akıllı analizin parçası
    text = repo.db.execute("SELECT text FROM asset_text").fetchall()
    assert len(text) == 1 and "Fatura" in text[0]["text"]
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    hits = c.get(f"/repos/{rid}/assets", params={"q": "faturasi"}).json()["items"]
    assert [h["rel_path"] for h in hits] == ["belge.jpg"]


def test_cleanup_suggestions_and_keep(archive):
    Image.new("RGB", (800, 600), (3, 3, 3)).save(archive / "karanlik.jpg")
    make_image(archive / "Ekran Resmi 2024-05-01.png", 5, fmt="PNG")
    make_image(archive / "normal.jpg", 6, date="2024:01:01 10:00:00")
    repo = _ready(archive)
    cats = {c["key"]: [i["rel_path"] for i in c["items"]] for c in library.cleanup(repo)}
    assert cats["accidental"] == ["karanlik.jpg"]
    assert cats["screenshots"] == ["Ekran Resmi 2024-05-01.png"]
    assert all("normal.jpg" not in v for v in cats.values())
    aid = repo.db.execute("SELECT asset_id FROM files WHERE rel_path='karanlik.jpg'").fetchone()[0]
    library.cleanup_keep(repo, "accidental", [aid])
    assert "accidental" not in {c["key"] for c in library.cleanup(repo)}


def test_memories_on_this_day_and_trip(archive):
    today = date(2026, 11, 5)
    for i in range(3):
        make_image(archive / f"gecen/{i}.jpg", 10 + i, date=_exif_date(date(2024, 11, 4 + i)))
    for i in range(6):  # ev
        make_image(archive / f"ev/{i}.jpg", 20 + i, date=_exif_date(date(2025, 3, 1) + timedelta(days=i * 7)), gps=ANKARA)
    for i in range(12):  # gezi: iki gün Bodrum
        make_image(archive / f"gezi/{i}.jpg", 40 + i, date=_exif_date(date(2025, 7, 10 + i // 6), 9 + i % 6), gps=BODRUM)
    repo = _ready(archive)
    mem = library.memories(repo, today=today)
    kinds = {m["kind"]: m for m in mem}
    assert kinds["on_this_day"]["title"] == "2 yıl önce bugün" and kinds["on_this_day"]["count"] == 3
    trip = kinds["trip"]
    assert trip["title"].startswith("Bodrum") and trip["count"] == 12
    assert (trip["date_from"], trip["date_to"]) == ("2025-07-10", "2025-07-11") and trip["cover"]


def test_albums_manual_and_smart(archive):
    make_image(archive / "a.jpg", 1, date="2023:05:01 10:00:00")
    make_image(archive / "b.jpg", 2, date="2024:05:01 10:00:00")
    _ready(archive)
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    items = {i["rel_path"]: i for i in c.get(f"/repos/{rid}/assets").json()["items"]}
    man = c.post(f"/repos/{rid}/albums", json={"name": "Tatil", "asset_ids": [items["a.jpg"]["asset_id"]]}).json()["id"]
    smart = c.post(f"/repos/{rid}/albums", json={"name": "2024", "kind": "smart", "rules": {"year": "2024"}}).json()["id"]
    lst = {a["name"]: a for a in c.get(f"/repos/{rid}/albums").json()["albums"]}
    assert lst["Tatil"]["count"] == 1 and lst["Tatil"]["cover"] == items["a.jpg"]["blake3"]
    assert [i["rel_path"] for i in c.get(f"/repos/{rid}/assets", params={"album": man}).json()["items"]] == ["a.jpg"]
    assert [i["rel_path"] for i in c.get(f"/repos/{rid}/assets", params={"album": smart}).json()["items"]] == ["b.jpg"]
    c.post(f"/repos/{rid}/albums/{man}/remove", json={"asset_ids": [items["a.jpg"]["asset_id"]]})
    assert c.get(f"/repos/{rid}/assets", params={"album": man}).json()["total"] == 0
    assert c.post(f"/repos/{rid}/albums/{smart}/items", json={"asset_ids": [1]}).status_code == 400


def test_date_and_location_edits_survive_reanalysis(archive):
    make_image(archive / "a.jpg", 1, date="2020:01:01 10:00:00")
    repo = _ready(archive)
    aid = repo.db.execute("SELECT id FROM assets").fetchone()[0]
    library.set_dates(repo, [aid], shift_seconds=3 * 3600)
    res = library.set_location(repo, [aid], *IZMIR)
    assert res["region"] == "İzmir"
    analyze.analyze(repo, force=True, smart=False)  # yeniden okuma düzeltmeyi ezmez
    a = repo.db.execute("SELECT taken_at, taken_at_source, lat FROM assets WHERE id=?", (aid,)).fetchone()
    assert (a["taken_at"], a["taken_at_source"]) == ("2020-01-01T13:00:00", "user") and abs(a["lat"] - IZMIR[0]) < 1e-6
    library.revert_edits(repo, [aid])
    a = repo.db.execute("SELECT taken_at, lat FROM assets WHERE id=?", (aid,)).fetchone()
    assert a["taken_at"] == "2020-01-01T10:00:00" and a["lat"] is None


def test_export_resize_and_strip_location(archive, tmp_path):
    make_image(archive / "a.jpg", 1, size=(3000, 2000), date="2024:08:14 19:42:00", gps=IZMIR)
    repo = _ready(archive)
    fid = repo.db.execute("SELECT id FROM files").fetchone()[0]
    out = tmp_path / "disa"
    res = library.export(repo, [fid], out, "small", strip_location=True)
    assert res["written"] == 1 and not res["failed"]
    f = out / "a.jpg"
    with Image.open(f) as im:
        assert max(im.size) == 1280
    tags = json.loads(subprocess.run(["exiftool", "-j", "-n", "-GPSLatitude", "-DateTimeOriginal", str(f)],
                                     capture_output=True, text=True).stdout)[0]
    assert "GPSLatitude" not in tags and tags["DateTimeOriginal"] == "2024:08:14 19:42:00"
    assert (archive / "a.jpg").exists()  # orijinale dokunulmadı


def test_map_points_and_purge_removes_derived_files(archive):
    make_image(archive / "a.jpg", 1, date="2024:08:14 19:42:00", gps=IZMIR)
    make_image(archive / "b.jpg", 2)
    repo = _ready(archive)
    pts = library.map_points(repo)
    assert len(pts) == 1 and pts[0][5] == "İzmir"
    row = repo.db.execute("SELECT f.id, f.rel_path, a.blake3 FROM files f JOIN assets a ON a.id=f.asset_id "
                          "WHERE f.rel_path='b.jpg'").fetchone()
    assert repo.thumb_path(row["blake3"], "s").exists()
    pid = plans.create(repo, "trash", [("trash", row["id"], row["rel_path"], None)])
    plans.apply(repo, pid)
    plans.empty_trash(repo)
    assert not repo.thumb_path(row["blake3"], "s").exists() and not repo.thumb_path(row["blake3"], "m").exists()


@pytest.mark.skipif(subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout.find("hevc_videotoolbox") < 0,
                    reason="VideoToolbox HEVC kodlayıcı gerekli")
def test_compress_video_replaces_and_undo_restores(archive):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "4", "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-c:a", "aac",
                    str(archive / "VID_1.mp4")], check=True)
    repo = _ready(archive)
    old = repo.db.execute("SELECT f.size, a.id FROM files f JOIN assets a ON a.id=f.asset_id").fetchone()
    with repo.db as c:
        c.execute("UPDATE assets SET favorite=1 WHERE id=?", (old["id"],))
    fid = repo.db.execute("SELECT id FROM files").fetchone()[0]
    res = library.compress_videos(repo, [fid])
    assert res["compressed"] == 1 and res["saved_bytes"] > old["size"] * 0.2, res
    new = repo.db.execute("SELECT f.size, f.tracked, a.favorite, a.id FROM files f JOIN assets a ON a.id=f.asset_id "
                          "WHERE f.rel_path='VID_1.mp4' AND f.state='present'").fetchone()
    assert new["tracked"] == 1 and new["favorite"] == 1 and new["id"] != old["id"]
    assert (archive / "VID_1.mp4").stat().st_size == new["size"] < old["size"]
    assert plans.trash_contents(repo)  # orijinal Son silinenler'de
    plans.undo(repo, res["plan_id"])
    assert (archive / "VID_1.mp4").stat().st_size == old["size"]
    rows = repo.db.execute("SELECT asset_id FROM files WHERE rel_path='VID_1.mp4' AND state='present'").fetchall()
    assert [r[0] for r in rows] == [old["id"]]
    assert not scan.scan(repo).missing


def test_multi_token_filters(archive):
    make_image(archive / "a.jpg", 1, date="2024:07:14 12:00:00", gps=IZMIR)
    make_image(archive / "b.jpg", 2, date="2023:07:02 12:00:00", gps=BODRUM)
    make_image(archive / "c.jpg", 3, date="2024:01:05 12:00:00", gps=ANKARA)
    repo = _ready(archive)
    ids = {r["rel_path"]: r["asset_id"] for r in repo.db.execute("SELECT rel_path, asset_id FROM files")}
    conn = repo.db
    with conn:
        for name in ("Ali", "Ayşe"):
            conn.execute("INSERT INTO persons(name) VALUES (?)", (name,))
        pid = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM persons")}
        dummy = b"\0" * 2048
        for f, who in (("a.jpg", "Ali"), ("a.jpg", "Ayşe"), ("b.jpg", "Ali")):
            conn.execute("INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec, person_id) VALUES (?,0,0,1,1,0.9,0.9,?,?)",
                         (ids[f], dummy, pid[who]))
        for t in ("kumsal", "deniz"):
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (t,))
        tid = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM tags")}
        for f, t in (("a.jpg", "kumsal"), ("a.jpg", "deniz"), ("b.jpg", "deniz")):
            conn.execute("INSERT INTO asset_tags(asset_id, tag_id, source) VALUES (?,?, 'user')", (ids[f], tid[t]))
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]

    def names(**p):
        return sorted(i["rel_path"] for i in c.get(f"/repos/{rid}/assets", params=p).json()["items"])

    assert names(person=str(pid["Ali"])) == ["a.jpg", "b.jpg"]
    assert names(person=f"{pid['Ali']},{pid['Ayşe']}") == ["a.jpg"]  # ikisi birlikte
    assert names(tag="deniz") == ["a.jpg", "b.jpg"]
    assert names(tag="deniz|kumsal") == ["a.jpg"]
    assert names(month="07") == ["a.jpg", "b.jpg"]
    assert names(month="07", year="2024") == ["a.jpg"]
    assert names(region="Muğla") == ["b.jpg"]


def test_similar_search_by_asset(archive):
    import numpy as np

    from mediamind.ml import siglip

    for i in range(4):
        make_image(archive / f"s{i}.jpg", i + 1)
    repo = _ready(archive)
    ids = {r["rel_path"]: r["asset_id"] for r in repo.db.execute("SELECT rel_path, asset_id FROM files")}
    rng = np.random.default_rng(3)
    base = rng.normal(size=siglip.DIM)
    base /= np.linalg.norm(base)
    other = rng.normal(size=siglip.DIM)
    other /= np.linalg.norm(other)

    def near(v, eps):
        x = v + rng.normal(scale=eps, size=siglip.DIM)
        return (x / np.linalg.norm(x)).astype(np.float32)

    vecs = {"s0.jpg": near(base, 0.001), "s1.jpg": near(base, 0.02), "s2.jpg": near(base, 0.03), "s3.jpg": other.astype(np.float32)}
    with repo.db as conn:
        for n, v in vecs.items():
            conn.execute("INSERT OR REPLACE INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)", (ids[n], siglip.MODEL_VERSION, v.tobytes()))
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    r = c.get(f"/repos/{rid}/assets", params={"similar": f"a:{ids['s0.jpg']}"}).json()
    names = [i["rel_path"] for i in r["items"]]
    assert names[0] == "s0.jpg" and set(names) == {"s0.jpg", "s1.jpg", "s2.jpg"}  # ilgisiz s3 yok
    assert all(i["match"] == "visual" for i in r["items"])
    assert c.get(f"/repos/{rid}/assets", params={"similar": "q:yok"}).status_code == 404


def test_share_prepare_original_and_small(archive):
    make_image(archive / "x.jpg", 1, gps=IZMIR)
    repo = _ready(archive)
    fid = repo.db.execute("SELECT id FROM files").fetchone()[0]
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    orig = c.post(f"/repos/{rid}/share/prepare", json={"file_ids": [fid]}).json()["paths"]
    assert orig == [str(archive / "x.jpg")]
    small = c.post(f"/repos/{rid}/share/prepare", json={"file_ids": [fid], "size": "large"}).json()["paths"]
    assert len(small) == 1 and small[0].endswith("x.jpg") and "Caches/MediaMind/share" in small[0]
    import os
    assert os.path.exists(small[0])
    gps = subprocess.run(["exiftool", "-s3", "-GPSLatitude", small[0]], capture_output=True, text=True).stdout.strip()
    assert gps == ""  # konum silindi
    import shutil
    shutil.rmtree(os.path.dirname(small[0]), ignore_errors=True)
