"""Gerçek modellerle içerik ve yüz analizi. Modeller yüklü değilse atlanır.

Model dizini: MEDIAMIND_MODELS ya da ~/Library/Application Support/MediaMind/models
"""

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mediamind import analyze, dedup, scan, tools, settings
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL

REAL_MODELS = Path(os.environ.get("MEDIAMIND_MODELS", Path.home() / "Library/Application Support/MediaMind/models"))
skimage = pytest.importorskip("skimage")


def _have_models():
    from mediamind.ml import models

    os.environ["MEDIAMIND_MODELS"] = str(REAL_MODELS)
    try:
        return all(models.installed(s) for s in models.SPECS.values())
    finally:
        del os.environ["MEDIAMIND_MODELS"]


pytestmark = pytest.mark.skipif(not (HAVE_EXIFTOOL and _have_models()), reason="modeller ve exiftool gerekli")


def save(im: Image.Image, path: Path, date: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"quality": 92}
    if date:
        ex = Image.Exif()
        ex.get_ifd(0x8769)[0x9003] = date
        ex[0x0110] = "Test Kamera"
        kw["exif"] = ex.tobytes()
    im.convert("RGB").save(path, **kw)


@pytest.fixture
def smart_repo(archive, monkeypatch):
    from skimage import data

    monkeypatch.setenv("MEDIAMIND_MODELS", str(REAL_MODELS))
    ast = Image.fromarray(data.astronaut())
    save(ast, archive / "uzay" / "astronot.jpg", "2024:01:10 10:00:00")
    save(ast.crop((60, 0, 460, 400)).resize((800, 800)), archive / "uzay" / "astronot-yakin.jpg", "2024:01:11 12:00:00")
    save(Image.fromarray(data.camera()), archive / "eski" / "kameraman.jpg", "1990:05:01 09:00:00")
    save(Image.fromarray(data.chelsea()), archive / "evcil" / "kedi.jpg", "2023:03:03 08:00:00")
    coffee = Image.fromarray(data.coffee())
    save(coffee, archive / "seri" / "kahve-1.jpg", "2023:06:01 08:00:00")
    save(coffee.crop((40, 20, 600, 400)).resize((600, 400)), archive / "seri" / "kahve-2.jpg", "2023:06:01 08:00:01")
    repo = Repo.init(archive, "Akıllı")
    scan.scan(repo)
    analyze.track(repo)
    res = analyze.analyze(repo)
    assert res["content"] == 6 and res["faces"] == 6
    return repo


def test_content_tags_and_semantic(smart_repo):
    ctx = tools.ToolContext(source="assistant", default_repo=smart_repo.id, settings=settings.load())
    r = tools.run(ctx, "search_media", {"semantic": "bir kedi"})
    assert r["items"][0]["rel_path"] == "evcil/kedi.jpg"
    r = tools.run(ctx, "search_media", {"semantic": "a cup of coffee"})
    assert r["items"][0]["rel_path"].startswith("seri/kahve")
    cat = tools.run(ctx, "search_media", {"tag": "kedi"})
    assert [i["rel_path"] for i in cat["items"]] == ["evcil/kedi.jpg"]
    assert tools.run(ctx, "search_media", {"semantic": "kumsalda köpek"})["total"] == 0  # karşılığı yok
    facets = tools.run(ctx, "list_facets", {})
    assert facets["content_analysis"]["content_done"] == 6


def test_faces_cluster_name_and_search(smart_repo):
    from mediamind.ml import people

    n = smart_repo.db.execute("SELECT COUNT(*) FROM faces WHERE animal IS NULL").fetchone()[0]
    assert n >= 3  # iki astronot + kameraman (kedide insan yüzü yok)
    assert smart_repo.db.execute(
        "SELECT COUNT(*) FROM faces f JOIN files fl ON fl.asset_id=f.asset_id WHERE fl.rel_path='evcil/kedi.jpg' AND f.animal IS NULL").fetchone()[0] == 0
    cl = people.clusters(smart_repo)
    assert len(cl) == 1 and cl[0]["photos"] == 2  # aynı kişinin iki fotoğrafı tek kümede
    res = people.name_cluster(smart_repo, cl[0]["cluster_id"], "Eileen")
    assert res["faces"] == 2
    # Kapak fotoğrafı: kullanıcı seçer; yüz kişiden çıkarılırsa otomatiğe döner
    fids = [r[0] for r in smart_repo.db.execute("SELECT id FROM faces WHERE person_id=? ORDER BY quality", (res["person_id"],))]
    auto = next(p for p in people.persons(smart_repo) if p["id"] == res["person_id"])
    assert auto["cover"] == fids[-1] and not auto["cover_custom"]
    people.set_cover(smart_repo, res["person_id"], fids[0])
    chosen = next(p for p in people.persons(smart_repo) if p["id"] == res["person_id"])
    assert chosen["cover"] == fids[0] and chosen["cover_custom"]
    with pytest.raises(ValueError):
        people.set_cover(smart_repo, res["person_id"], 999999)
    people.assign_face(smart_repo, fids[0], None)
    assert next(p for p in people.persons(smart_repo) if p["id"] == res["person_id"])["cover"] == fids[-1]
    people.assign_face(smart_repo, fids[0], "Eileen")
    ctx = tools.ToolContext(source="assistant", default_repo=smart_repo.id, settings=settings.load())
    found = tools.run(ctx, "search_media", {"person": "eileen"})
    assert {i["rel_path"] for i in found["items"]} == {"uzay/astronot.jpg", "uzay/astronot-yakin.jpg"}
    assert found["items"][0]["people"] == ["Eileen"]
    from mediamind import organize

    moves = dict((s, d) for _, s, d in organize.preview(smart_repo, "{kişi}/{dosya}"))
    assert moves["uzay/astronot.jpg"] == "Eileen/astronot.jpg"
    assert moves["evcil/kedi.jpg"] == "_kişisiz/kedi.jpg"

    # "Bu o değil": kişiden çıkarılan yüz yeniden otomatik atanmaz
    fid = smart_repo.db.execute("SELECT id FROM faces WHERE person_id IS NOT NULL LIMIT 1").fetchone()[0]
    people.assign_face(smart_repo, fid, None)
    people.recluster(smart_repo)
    assert smart_repo.db.execute("SELECT person_id FROM faces WHERE id=?", (fid,)).fetchone()[0] is None


def test_pets_detected_named_and_matched(smart_repo):
    from mediamind.ml import people, pets

    if not pets.available():
        pytest.skip("evcil hayvan modeli yok")
    rows = smart_repo.db.execute("SELECT f.id, f.animal, fl.rel_path FROM faces f JOIN files fl ON fl.asset_id=f.asset_id "
                                 "WHERE f.animal IS NOT NULL").fetchall()
    assert [(r["animal"], r["rel_path"]) for r in rows] == [("kedi", "evcil/kedi.jpg")]
    # İnsan kümelerinde hayvan görünmez; hayvan grubu ayrı listelenir
    assert all(c["cluster_id"] != rows[0]["id"] for c in people.clusters(smart_repo))
    # Tek fotoğraflık hayvan grubu oluşmaz (en az bir eşi yok) ama ad verilebilir
    res = people.assign_face(smart_repo, rows[0]["id"], "Pamuk")
    kind = smart_repo.db.execute("SELECT kind FROM persons WHERE id=?", (res["person_id"],)).fetchone()[0]
    assert kind == "pet"


def test_bursts_are_optional(smart_repo):
    groups = [g for g in dedup.find(smart_repo) if g.kind == "burst"]
    assert len(groups) == 1
    roles = sorted(m.role for m in groups[0].members)
    assert roles == ["keep", "optional"]
    assert dedup.plan_items(groups) == []  # seri çekimler otomatik silme planına girmez


def test_user_corrections_split_wrong_cluster(smart_repo):
    """Farklı kişiler aynı kümeye düşerse: tek yüzü çıkarma, kümeyi dağıtma, 'yüz değil'."""
    from mediamind.ml import people

    conn = smart_repo.db
    faces = conn.execute("SELECT f.id, fl.rel_path FROM faces f JOIN files fl ON fl.asset_id=f.asset_id ORDER BY f.id").fetchall()
    ast = [f["id"] for f in faces if f["rel_path"].startswith("uzay/")]
    cam = [f["id"] for f in faces if f["rel_path"].startswith("eski/")]
    assert ast and cam
    # Hatalı bir kümeyi taklit et: astronot + kameraman aynı kümede
    with conn:
        conn.executemany("UPDATE faces SET cluster_id=? WHERE id=?", [(ast[0], i) for i in ast + cam])
    cid = ast[0]
    # Tek yüzü kümeden çıkar: bir daha otomatik kümelenmez ama fotoğrafta görünür
    people.exclude_face(smart_repo, cam[0], "removed")
    people.recluster(smart_repo)
    row = conn.execute("SELECT cluster_id, excluded FROM faces WHERE id=?", (cam[0],)).fetchone()
    assert row["cluster_id"] is None and row["excluded"] == "removed"
    aid = conn.execute("SELECT asset_id FROM faces WHERE id=?", (cam[0],)).fetchone()[0]
    assert any(f["id"] == cam[0] for f in people.faces_of_asset(smart_repo, aid))
    # Elle ad verilince dışlama kalkar
    people.assign_face(smart_repo, cam[0], "Kameraman")
    assert conn.execute("SELECT excluded, person_id FROM faces WHERE id=?", (cam[0],)).fetchone()["excluded"] is None

    # "Hepsi farklı": küme dağılır, üyeler yeniden kümelenmez
    cl = people.clusters(smart_repo)
    assert cl and all(len(c["members"]) >= 2 for c in cl)
    n = people.dissolve_cluster(smart_repo, cl[0]["cluster_id"])
    assert n >= 2
    people.recluster(smart_repo)
    assert all(c["cluster_id"] != cl[0]["cluster_id"] for c in people.clusters(smart_repo))

    # "Yüz değil": fotoğraftaki yüz listesinden de gizlenir
    people.exclude_face(smart_repo, ast[0], "not_face")
    aid = conn.execute("SELECT asset_id FROM faces WHERE id=?", (ast[0],)).fetchone()[0]
    assert all(f["id"] != ast[0] for f in people.faces_of_asset(smart_repo, aid))


def test_manual_person_tags(smart_repo):
    from mediamind import analyze
    from mediamind.ml import people

    conn = smart_repo.db
    aid = conn.execute("SELECT asset_id FROM files WHERE rel_path='uzay/astronot.jpg'").fetchone()[0]
    cat = conn.execute("SELECT asset_id FROM files WHERE rel_path='evcil/kedi.jpg'").fetchone()[0]
    # Yüzün olduğu bölgeye kutu (astronot yüzü ~ x 0.35-0.53, y 0.11-0.35): yüz bulunur, vektör kaydedilir
    r = people.tag_person(smart_repo, aid, "Eileen", (0.30, 0.05, 0.58, 0.40))
    assert r["detected"] is True
    assert len(conn.execute("SELECT vec FROM faces WHERE id=?", (r["face_id"],)).fetchone()[0]) == 2048
    # Yüz olmayan bölge: etiket yine kaydedilir, vektörsüz
    r2 = people.tag_person(smart_repo, cat, "Pamuk", (0.1, 0.1, 0.4, 0.4))
    assert r2["detected"] is False
    # Tüm fotoğrafa etiket (kutu yok); aynı etiket iki kez eklenmez
    r3 = people.tag_person(smart_repo, cat, "Ayşe")
    assert people.tag_person(smart_repo, cat, "ayşe")["face_id"] == r3["face_id"]
    ctx = tools.ToolContext(source="assistant", default_repo=smart_repo.id, settings=settings.load())
    assert [i["rel_path"] for i in tools.run(ctx, "search_media", {"person": "Pamuk"})["items"]] == ["evcil/kedi.jpg"]
    names = {p["name"] for p in people.persons(smart_repo)}
    assert {"Eileen", "Pamuk", "Ayşe"} <= names
    # Vektörsüz etiketler kümelemeyi ve kişi merkezini bozmaz
    people.recluster(smart_repo)
    # Yeniden analizde elle eklenen etiketler korunur
    analyze.run_smart(smart_repo, force=True)
    assert conn.execute("SELECT COUNT(*) FROM faces WHERE manual=1").fetchone()[0] == 3
    people.remove_manual(smart_repo, r2["face_id"])
    assert conn.execute("SELECT COUNT(*) FROM faces WHERE manual=1").fetchone()[0] == 2


def test_unified_search_api(smart_repo):
    from fastapi.testclient import TestClient

    from mediamind import registry
    from mediamind.api.server import create_app

    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = registry.find("Akıllı").id
    r = c.get(f"/repos/{rid}/assets", params={"search": "kedi"}).json()
    # Hem dosya yolu ('evcil/kedi.jpg' metin) hem görsel içerik; metin eşleşmesi üstte, tekrar yok
    assert r["items"][0]["rel_path"] == "evcil/kedi.jpg" and r["items"][0]["match"] == "text"
    assert len({i["file_id"] for i in r["items"]}) == len(r["items"])
    r = c.get(f"/repos/{rid}/assets", params={"search": "a cup of coffee"}).json()
    assert r["text_hits"] == 0 and r["items"][0]["rel_path"].startswith("seri/kahve") and r["items"][0]["match"] == "visual"
