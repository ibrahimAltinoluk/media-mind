import json

import pytest

from mediamind import analyze, scan
from mediamind.media import dates
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, HAVE_FFMPEG, make_image, make_video


def test_filename_dates():
    assert dates.date_from_filename("IMG-20240815-WA0012.jpg") == "2024-08-15T00:00:00"
    assert dates.date_from_filename("IMG_20240815_194211.jpg") == "2024-08-15T19:42:11"
    assert dates.date_from_filename("2023-07-02 18.05.33.png") == "2023-07-02T18:05:33"
    assert dates.date_from_filename("Screenshot 2024-01-03 at 09.15.00.png") == "2024-01-03T09:15:00"
    assert dates.date_from_filename("DSC_0042.NEF") is None
    assert dates.date_from_filename("19991340.jpg") is None


def test_exif_date_parse():
    assert dates.parse_exif_date("2024:08:14 19:42:00+03:00") == ("2024-08-14T19:42:00", "+03:00")
    assert dates.parse_exif_date("0000:00:00 00:00:00") is None


def _asset(repo, rel):
    return repo.db.execute(
        "SELECT a.*, p.name AS place FROM files f JOIN assets a ON a.id=f.asset_id "
        "LEFT JOIN places p ON p.id=a.place_id WHERE f.rel_path=?", (rel,)
    ).fetchone()


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_track_and_analyze_photo(archive):
    make_image(archive / "DCIM" / "IMG_4821.jpg", 1, date="2024:08:14 19:42:00", gps=(37.0344, 27.4305))
    make_image(archive / "WhatsApp Images" / "IMG-20230702-WA0004.jpg", 2)
    repo = Repo.init(archive)
    scan.scan(repo)
    assert analyze.track(repo, ["DCIM"]) == 1
    res = analyze.analyze(repo)
    assert res["meta"] == 1

    a = _asset(repo, "DCIM/IMG_4821.jpg")
    assert a["taken_at"] == "2024-08-14T19:42:00" and a["taken_at_source"] == "exif"
    assert (a["width"], a["height"]) == (1600, 1200)
    assert a["camera_model"] == "iPhone 15 Pro"
    assert abs(a["lat"] - 37.0344) < 1e-3 and a["place"]  # Bodrum çevresi
    assert a["phash"] and len(a["phash"]) == 16
    assert repo.thumb_path(a["blake3"], "s").exists()
    assert repo.thumb_path(a["blake3"], "m").exists()

    analyze.track(repo)
    analyze.analyze(repo)
    w = _asset(repo, "WhatsApp Images/IMG-20230702-WA0004.jpg")
    assert w["taken_at"] == "2023-07-02T00:00:00" and w["taken_at_source"] == "filename"
    # İkinci çalıştırma bir şey yapmaz
    assert set(analyze.analyze(repo).values()) == {0}


def test_heic_thumbnail(archive):
    pytest.importorskip("pillow_heif")
    from mediamind.media.thumbs import ensure_heif

    ensure_heif()
    make_image(archive / "foto.heic", 5, fmt="HEIF")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    a = _asset(repo, "foto.heic")
    assert "errors" not in json.loads(a["analysis_state"])
    assert repo.thumb_path(a["blake3"], "s").exists()


def test_corrupt_file_is_recorded_not_fatal(archive):
    (archive / "bozuk.jpg").write_bytes(b"\xff\xd8\xff not really a jpeg")
    make_image(archive / "iyi.jpg", 1)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    st = json.loads(_asset(repo, "bozuk.jpg")["analysis_state"])
    assert "thumbs" in st.get("errors", {})
    assert "errors" not in json.loads(_asset(repo, "iyi.jpg")["analysis_state"])


@pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_EXIFTOOL), reason="ffmpeg ve exiftool gerekli")
def test_video_metadata_and_thumb(archive):
    make_video(archive / "VID_0091.mp4", seconds=2.0)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    a = _asset(repo, "VID_0091.mp4")
    assert a["kind"] == "video"
    assert 1800 <= a["duration_ms"] <= 2200
    assert (a["width"], a["height"]) == (320, 240)
    assert a["phash"]
    assert repo.thumb_path(a["blake3"], "s").exists()
