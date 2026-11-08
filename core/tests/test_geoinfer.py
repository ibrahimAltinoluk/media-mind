"""Konumsuz fotoğraflara konum önerisi: yakın zamanlı konumlu fotoğraflardan ve GPX kaydından."""

from mediamind import geoinfer, library
from mediamind.repo import Repo


def _add(conn, aid, taken_at, lat=None, lon=None, tz=None):
    conn.execute("INSERT INTO assets(id, blake3, size, kind, taken_at, taken_at_source, lat, lon, tz_offset) "
                 "VALUES (?,?,1,'photo',?,'exif',?,?,?)", (aid, f"b{aid}", taken_at, lat, lon, tz))
    conn.execute("INSERT INTO files(asset_id, rel_path, fs_path, size, mtime_ns, state, tracked) VALUES (?,?,?,1,0,'present',1)",
                 (aid, f"p{aid}.jpg", f"p{aid}.jpg"))


def test_neighbors_interpolate_skip_and_apply(archive):
    repo = Repo.init(archive)
    with repo.db as conn:
        _add(conn, 1, "2024-05-12T10:00:00", 38.6431, 34.8289)   # Göreme (telefon)
        _add(conn, 2, "2024-05-12T11:00:00", 38.6531, 34.8389)
        _add(conn, 3, "2024-05-12T10:30:00")                       # arada, konumsuz (fotoğraf makinesi)
        _add(conn, 4, "2024-05-12T11:20:00")                       # yalnız öncesi var, 20 dk
        _add(conn, 5, "2024-05-12T15:00:00")                       # çok uzak: önerilmez
        _add(conn, 6, "2023-01-01T09:00:00")                       # o gün konumlu fotoğraf yok

    s = geoinfer.suggestions(repo)
    assert s["missing"] == 4 and s["total"] == 2 and len(s["groups"]) == 1
    items = {i["asset_id"]: i for i in s["groups"][0]["items"]}
    assert set(items) == {3, 4}
    assert abs(items[3]["lat"] - 38.6481) < 1e-4                    # zamana göre ortada
    assert items[4]["lat"] == 38.6531

    geoinfer.skip(repo, [4])
    assert geoinfer.suggestions(repo)["total"] == 1
    library.set_locations(repo, [(3, items[3]["lat"], items[3]["lon"])])
    s = geoinfer.suggestions(repo)
    assert s["total"] == 0 and s["skipped"] == 1
    geoinfer.clear_skips(repo)
    assert geoinfer.suggestions(repo)["total"] == 1


def test_gpx_matching_uses_utc(archive, tmp_path):
    repo = Repo.init(archive)
    with repo.db as conn:
        _add(conn, 1, "2024-05-12T13:05:00", tz="+03:00")          # UTC 10:05
        _add(conn, 2, "2024-05-12T18:00:00", tz="+03:00")          # iz dışında
    gpx = tmp_path / "iz.gpx"
    gpx.write_text("""<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
      <trkpt lat="41.0" lon="29.0"><time>2024-05-12T10:00:00Z</time></trkpt>
      <trkpt lat="41.1" lon="29.1"><time>2024-05-12T10:10:00Z</time></trkpt>
    </trkseg></trk></gpx>""")
    s = geoinfer.suggestions(repo, str(gpx))
    assert s["total"] == 1
    it = s["groups"][0]["items"][0]
    assert it["asset_id"] == 1 and abs(it["lat"] - 41.05) < 1e-6 and s["groups"][0]["source"] == "gpx"
