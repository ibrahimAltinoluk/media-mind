import shutil

import pytest

from mediamind import analyze, dedup, organize, plans, scan
from mediamind.paths import UnsafePathError
from mediamind.repo import Repo

from .conftest import HAVE_EXIFTOOL, HAVE_FFMPEG, make_image, make_video, resized_copy


def _setup(archive):
    make_image(archive / "DCIM" / "IMG_4821.jpg", 1, date="2024:08:14 19:42:00")
    shutil.copy2(archive / "DCIM" / "IMG_4821.jpg", archive / "Downloads" / "IMG_4821 (1).jpg") if (archive / "Downloads").mkdir() is None else None
    resized_copy(archive / "DCIM" / "IMG_4821.jpg", archive / "WhatsApp Images" / "IMG-20240815-WA0012.jpg")
    make_image(archive / "DCIM" / "IMG_5000.jpg", 50, date="2024:08:20 10:00:00")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    return repo


def _present(repo):
    return {r["rel_path"] for r in repo.db.execute("SELECT rel_path FROM files WHERE state='present'")}


def test_dedup_groups_and_keep_rule(archive):
    repo = _setup(archive)
    groups = dedup.find(repo)
    assert len(groups) == 1  # birebir kopya + küçültülmüş WhatsApp kopyası tek grupta
    g = groups[0]
    assert g.kind == "near"
    assert g.keep.rel_path == "DCIM/IMG_4821.jpg"
    reasons = {m.rel_path: m.reason for m in g.members if m.role == "delete"}
    assert reasons["Downloads/IMG_4821 (1).jpg"] == "aynı dosya"
    assert "çözünürlük" in reasons["WhatsApp Images/IMG-20240815-WA0012.jpg"]
    # Farklı fotoğraf gruba girmez
    assert all(m.rel_path != "DCIM/IMG_5000.jpg" for m in g.members)


def test_trash_plan_apply_undo_and_empty(archive):
    repo = _setup(archive)
    groups = dedup.find(repo)
    pid = plans.create(repo, "dedup", dedup.plan_items(groups), title="test")
    assert plans.get(repo, pid)["status"] == "pending"
    assert _present(repo) >= {"Downloads/IMG_4821 (1).jpg"}  # plan oluşturmak bir şey değiştirmez

    res = plans.apply(repo, pid)
    assert (res.done, res.failed, res.status) == (2, 0, "applied")
    assert not (archive / "Downloads" / "IMG_4821 (1).jpg").exists()
    assert (archive / ".mediamind" / "trash" / str(pid) / "Downloads" / "IMG_4821 (1).jpg").exists()
    assert not (archive / "Downloads").exists()  # boş kalan klasör kaldırıldı
    assert _present(repo) == {"DCIM/IMG_4821.jpg", "DCIM/IMG_5000.jpg"}
    assert not scan.scan(repo).changed  # çöp taramaya girmez

    plans.undo(repo, pid)
    assert (archive / "Downloads" / "IMG_4821 (1).jpg").exists()
    assert "WhatsApp Images/IMG-20240815-WA0012.jpg" in _present(repo)
    assert not (archive / ".mediamind" / "trash" / str(pid)).exists()
    assert (archive / ".mediamind" / "trash").is_dir()  # çöp kökü korunur
    assert not scan.scan(repo).changed

    pid2 = plans.create(repo, "dedup", dedup.plan_items(dedup.find(repo)))
    plans.apply(repo, pid2)
    n, freed = plans.empty_trash(repo)
    assert n == 2 and freed > 0
    with pytest.raises(plans.PlanError):
        plans.apply(repo, pid2)
    assert plans.undo(repo, pid2).skipped == 2  # kalıcı silinenler geri gelmez


def test_organize_with_sidecar_and_undo(archive):
    make_image(archive / "DCIM" / "IMG_1.jpg", 1, date="2024:08:14 19:42:00")
    (archive / "DCIM" / "IMG_1.AAE").write_text("<plist/>")
    make_image(archive / "DCIM" / "IMG_2.jpg", 2, date="2023:01:05 10:00:00")
    make_image(archive / "karisik" / "ekran.png", 3, fmt="PNG")  # tarih yok → mtime
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)

    moves = dict((src, dst) for _, src, dst in organize.preview(repo, "yil-ay"))
    assert moves["DCIM/IMG_1.jpg"] == "2024/08-Ağustos/IMG_1.jpg"
    assert moves["DCIM/IMG_2.jpg"] == "2023/01-Ocak/IMG_2.jpg"

    pid = organize.build_plan(repo, "yil-ay")
    srcs = [i["src"] for i in plans.items(repo, pid)]
    assert "DCIM/IMG_1.AAE" in srcs  # yan dosya otomatik eklendi
    plans.apply(repo, pid)
    assert (archive / "2024" / "08-Ağustos" / "IMG_1.jpg").exists()
    assert (archive / "2024" / "08-Ağustos" / "IMG_1.AAE").exists()
    assert not (archive / "DCIM").exists()
    assert not scan.scan(repo).changed

    plans.undo(repo, pid)
    assert (archive / "DCIM" / "IMG_1.jpg").exists() and (archive / "DCIM" / "IMG_1.AAE").exists()
    assert not (archive / "2024").exists()
    assert not scan.scan(repo).changed


def test_organize_conflict_renames(archive):
    make_image(archive / "a" / "IMG_1.jpg", 1, date="2024:08:14 19:42:00")
    make_image(archive / "b" / "IMG_1.jpg", 2, date="2024:08:20 19:42:00")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    pid = organize.build_plan(repo, "{yıl}/{dosya}")
    dsts = sorted(i["dst"] for i in plans.items(repo, pid))
    assert dsts == ["2024/IMG_1-1.jpg", "2024/IMG_1.jpg"]
    plans.apply(repo, pid)
    assert len([p for p in (archive / "2024").iterdir() if not p.name.startswith("._")]) == 2


def test_apply_resumes_after_crash(archive):
    make_image(archive / "x" / "a.jpg", 1, date="2024:08:14 19:42:00")
    make_image(archive / "x" / "b.jpg", 2, date="2024:08:14 19:43:00")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    pid = organize.build_plan(repo, "yil")
    first = plans.items(repo, pid)[0]
    # Çökmeyi taklit et: dosya taşınmış, DB 'running' kalmış
    (archive / "2024").mkdir()
    (archive / first["src"]).rename(archive / first["dst"])
    with repo.db as c:
        c.execute("UPDATE plan_items SET status='running' WHERE id=?", (first["id"],))
        c.execute("UPDATE plans SET status='running' WHERE id=?", (pid,))
    res = plans.apply(repo, pid)
    assert res.status == "applied" and res.done == 2
    assert not scan.scan(repo).changed


def test_paths_cannot_escape(archive):
    make_image(archive / "a.jpg", 1)
    repo = Repo.init(archive)
    scan.scan(repo)
    fid = repo.db.execute("SELECT id FROM files").fetchone()[0]
    for bad in ("../disari.jpg", "/etc/x.jpg", ".mediamind/x.jpg"):
        with pytest.raises(UnsafePathError):
            plans.create(repo, "move", [("move", fid, "a.jpg", bad)])
    with pytest.raises(organize.TemplateError):
        organize.preview(repo, "{bilinmeyen}/{dosya}", scope="all")


@pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_EXIFTOOL), reason="ffmpeg ve exiftool gerekli")
def test_video_duplicates(archive):
    make_video(archive / "DCIM" / "VID_0091.mp4", seconds=3, size="640x480", bitrate="2000k")
    (archive / "Export").mkdir()
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(archive / "DCIM" / "VID_0091.mp4"), "-vf", "scale=320:240",
                    "-c:v", "libx264", "-b:v", "300k", str(archive / "Export" / "VID_0091-kucuk.mp4")], check=True)
    make_video(archive / "baska.mp4", seconds=6, size="320x240", seed_color="mandelbrot")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo)
    groups = [g for g in dedup.find(repo) if g.kind == "video"]
    assert len(groups) == 1
    assert groups[0].keep.rel_path == "DCIM/VID_0091.mp4"


def test_restore_and_purge_single_items(archive):
    make_image(archive / "DCIM" / "IMG_1.jpg", 1, date="2024:08:14 19:42:00")
    (archive / "DCIM" / "IMG_1.AAE").write_text("<plist/>")
    make_image(archive / "DCIM" / "IMG_2.jpg", 2, date="2024:08:15 10:00:00")
    make_image(archive / "DCIM" / "IMG_3.jpg", 3, date="2024:08:16 10:00:00")
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    ids = {r["rel_path"]: r["id"] for r in repo.db.execute("SELECT id, rel_path FROM files WHERE state='present'")}
    pid = plans.create(repo, "trash", [("trash", ids[k], k, None) for k in ("DCIM/IMG_1.jpg", "DCIM/IMG_2.jpg", "DCIM/IMG_3.jpg")])
    plans.apply(repo, pid)
    assert not (archive / "DCIM" / "IMG_1.AAE").exists()  # yan dosya da çöpe gitti
    item = {r["src"]: r["id"] for r in plans.trash_contents(repo)}

    # Eski yerde aynı adlı yeni bir dosya varsa geri gelen dosyaya numara eklenir
    make_image(archive / "DCIM" / "IMG_2.jpg", 9)
    r = plans.restore_items(repo, [item["DCIM/IMG_1.jpg"], item["DCIM/IMG_2.jpg"]])
    assert (r.done, r.failed) == (3, 0)  # 2 fotoğraf + 1 yan dosya
    assert (archive / "DCIM" / "IMG_1.jpg").exists() and (archive / "DCIM" / "IMG_1.AAE").exists()
    restored = {x["rel_path"] for x in repo.db.execute("SELECT rel_path FROM files WHERE state='present'")}
    assert "DCIM/IMG_1.jpg" in restored and any(p.startswith("DCIM/IMG_2") and p != "DCIM/IMG_2.jpg" for p in restored)
    assert plans.get(repo, pid)["status"] == "applied"  # IMG_3 hâlâ çöpte

    n, _ = plans.purge_items(repo, [item["DCIM/IMG_3.jpg"]])
    assert n == 1 and not plans.trash_contents(repo)
    assert plans.get(repo, pid)["status"] == "applied"
    assert not list((archive / ".mediamind" / "trash").rglob("*.jpg"))


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_trash_plan_rejects_already_trashed(archive):
    from fastapi.testclient import TestClient

    from mediamind.api.server import create_app

    make_image(archive / "IMG_1.jpg", 1)
    c = TestClient(create_app("t"), headers={"x-mm-token": "t"})
    rid = c.post("/repos", json={"path": str(archive)}).json()["id"]
    repo = Repo.discover(archive)
    scan.scan(repo)
    fid = repo.db.execute("SELECT id FROM files").fetchone()[0]
    p = c.post(f"/repos/{rid}/plans", json={"kind": "trash", "file_ids": [fid]}).json()
    plans.apply(repo, p["id"])
    r = c.post(f"/repos/{rid}/plans", json={"kind": "trash", "file_ids": [fid]})
    assert r.status_code == 400 and "zaten silinmiş" in r.json()["detail"]
