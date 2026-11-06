import os
import shutil
import unicodedata

import pytest

from mediamind import registry, scan
from mediamind.repo import Repo, RepoError, RepoLockedError

from .conftest import make_image


def test_init_registers_and_refuses_nested(archive):
    repo = Repo.init(archive, "Aile Arşivi")
    assert (archive / ".mediamind" / "index.db").exists()
    assert (archive / ".mediamind" / "trash").is_dir()
    assert registry.find("Aile Arşivi").id == repo.id
    with pytest.raises(RepoError):
        Repo.init(archive)
    (archive / "alt").mkdir()
    with pytest.raises(RepoError):
        Repo.init(archive / "alt")
    # Alt klasörden keşif
    assert Repo.discover(archive / "alt").root == archive.resolve()
    # Listeden kaldırma dosyalara dokunmaz
    registry.unregister("Aile Arşivi")
    assert registry.find("Aile Arşivi") is None
    assert (archive / ".mediamind" / "repo.toml").exists()


def test_open_by_name(archive):
    Repo.init(archive, "Telefon")
    assert Repo.open("Telefon").root == archive.resolve()


def test_scan_states(archive):
    make_image(archive / "DCIM" / "a.jpg", 1)
    make_image(archive / "DCIM" / "b.jpg", 2)
    (archive / "notlar.txt").write_text("medya değil")
    (archive / ".DS_Store").write_bytes(b"x")
    repo = Repo.init(archive)

    r = scan.scan(repo)
    assert sorted(r.new) == ["DCIM/a.jpg", "DCIM/b.jpg"]
    c = scan.counts(repo)
    assert (c.untracked, c.tracked) == (2, 0)

    # İkinci tarama değişiklik yok
    r = scan.scan(repo)
    assert not r.changed

    # Kopya, taşıma, silme, değiştirme
    shutil.copy2(archive / "DCIM" / "a.jpg", archive / "yedek-a.jpg")
    (archive / "Tatil").mkdir()
    os.rename(archive / "DCIM" / "b.jpg", archive / "Tatil" / "b.jpg")
    r = scan.scan(repo)
    assert r.copies == [("yedek-a.jpg", "DCIM/a.jpg")]
    assert r.moved == [("DCIM/b.jpg", "Tatil/b.jpg")]

    from mediamind import analyze

    analyze.track(repo)  # takipteki dosyalar kaybolunca "kayıp" olur (bilgileri saklanır)
    os.remove(archive / "yedek-a.jpg")
    make_image(archive / "DCIM" / "a.jpg", 99)  # içerik değişti
    r = scan.scan(repo)
    assert r.missing == ["yedek-a.jpg"]
    assert r.modified == ["DCIM/a.jpg"]

    # Eksik dosya başka yerde geri belirirse "taşınmış" sayılır
    shutil.copy2(archive / "Tatil" / "b.jpg", archive / "tmp.jpg")
    old_a = archive / "eski-a.jpg"
    make_image(old_a, 1)  # yedek-a.jpg ile aynı içerik
    r = scan.scan(repo)
    assert ("yedek-a.jpg", "eski-a.jpg") in r.moved
    assert ("tmp.jpg", "Tatil/b.jpg") in r.copies

    # Kütüphaneye hiç eklenmemiş dosya kaybolursa "kayıp" sayılmaz, kaydı sessizce silinir
    make_image(archive / "gecici.jpg", 55)
    scan.scan(repo)
    os.remove(archive / "gecici.jpg")
    r = scan.scan(repo)
    assert r.missing == [] and not repo.db.execute("SELECT 1 FROM files WHERE rel_path='gecici.jpg'").fetchone()


def test_nfd_names_stored_as_nfc(archive):
    nfd = unicodedata.normalize("NFD", "Güzel Şehir.jpg")
    make_image(archive / nfd, 3)
    repo = Repo.init(archive)
    r = scan.scan(repo)
    assert r.new == ["Güzel Şehir.jpg"]
    assert unicodedata.is_normalized("NFC", r.new[0])
    assert not scan.scan(repo).changed


def test_ignore_file_and_nested_repo(archive):
    make_image(archive / "a.jpg", 1)
    make_image(archive / "gizli" / "b.jpg", 2)
    make_image(archive / "Kutuphane.photoslibrary" / "c.jpg", 3)
    (archive / ".mediamindignore").write_text("gizli/\n")
    repo = Repo.init(archive)
    assert scan.scan(repo).new == ["a.jpg"]


def test_lock_blocks_second_process(archive):
    repo = Repo.init(archive)
    other = Repo(archive)
    with repo.lock():
        with pytest.raises(RepoLockedError):
            with other.lock():
                pass


def test_appledouble_files_ignored_and_pruned(archive):
    """exFAT/FAT disklerde macOS her dosyanın yanına '._ad' dosyası koyar: medya sayılmaz, boş klasörü dolu göstermez."""
    from mediamind import plans
    from mediamind.kinds import kind_of

    assert kind_of("._IMG_1.jpg") is None and kind_of("Tatil/._IMG_1.jpg") is None and kind_of("IMG_1.jpg") == "photo"
    make_image(archive / "eski" / "IMG_1.jpg", 1)
    (archive / "eski" / "._IMG_1.jpg").write_bytes(b"\0" * 4096)
    (archive / "eski" / "._IMG_2.jpg").write_bytes(b"\0" * 4096)  # sahipsiz
    repo = Repo.init(archive)
    r = scan.scan(repo)
    assert r.new == ["eski/IMG_1.jpg"]
    os.rename(archive / "eski" / "IMG_1.jpg", archive / "IMG_1.jpg")
    plans._prune_empty(repo, {archive / "eski"})
    assert not (archive / "eski").exists()
