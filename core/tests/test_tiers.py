"""Model kalitesi: cihaz uygunluğu; görsel model sürümü değişirse geçiş sırasında eski modelle arama,
geçiş bitince eski vektörlerin silinmesi."""

import numpy as np
import pytest

from mediamind import analyze, scan, settings
from mediamind.ml import content, siglip, tiers
from mediamind.repo import Repo

from .conftest import make_image

HIGH = "test-model-v2"
STD = siglip.PROFILES["standard"]["version"]


def test_hardware_check_blocks_unsuitable_macs():
    weak = {"chip": "Intel", "apple_silicon": False, "ram_gb": 32, "cores": 8, "free_disk": 10**12}
    small = {"chip": "Apple M1", "apple_silicon": True, "ram_gb": 8, "cores": 8, "free_disk": 10**12}
    full = {"chip": "Apple M3", "apple_silicon": True, "ram_gb": 16, "cores": 8, "free_disk": 10**9}
    good = {"chip": "Apple M5", "apple_silicon": True, "ram_gb": 24, "cores": 10, "free_disk": 10**12}
    assert tiers.check("speech", "high", weak)[0] is False
    ok, why = tiers.check("speech", "high", small)
    assert not ok and "16 GB" in why
    assert tiers.check("speech", "high", full)[0] is False  # disk
    assert tiers.check("speech", "high", good) == (True, None)
    assert tiers.check("speech", "standard", weak) == (True, None)
    assert list(tiers.status()["kinds"]) == ["speech"]  # görsel modelde seçim sunulmuyor


def test_settings_reject_unknown_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAMIND_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        settings.update({"models": {"speech": "ultra"}})
    assert settings.update({"models": {"speech": "high"}})["models"]["speech"] == "high"


def _vec(rng, dim):
    v = rng.normal(size=dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_switch_keeps_old_model_until_done(archive, monkeypatch):
    for i in range(3):
        make_image(archive / f"t{i}.jpg", i + 1)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo, smart=False)
    ids = [r[0] for r in repo.db.execute("SELECT id FROM assets ORDER BY id")]
    rng = np.random.default_rng(0)
    with repo.db as conn:
        for a in ids:
            conn.execute("INSERT INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)", (a, STD, _vec(rng, 768).tobytes()))
            st = '{"thumbs": 1, "content": 1}'
            conn.execute("UPDATE assets SET analysis_state=? WHERE id=?", (st, a))

    # Görsel model sürümü değişti (ileride olası): arama, yeni modelle analiz bitene kadar eski modelle sürer
    monkeypatch.setattr(siglip, "available", lambda version=None: True)
    monkeypatch.setattr(siglip, "target_version", lambda: HIGH)
    monkeypatch.setitem(siglip.BY_VERSION, HIGH, {**siglip.PROFILES["standard"], "version": HIGH, "dim": 1152})
    assert content.effective_version(repo) == STD
    with repo.db as conn:
        for a in ids[:2]:
            conn.execute("INSERT INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)", (a, HIGH, _vec(rng, 1152).tobytes()))
    assert content.effective_version(repo) == STD  # biri eksik
    assert content.finish_switch(repo) == 0
    with repo.db as conn:
        conn.execute("INSERT INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)", (ids[2], HIGH, _vec(rng, 1152).tobytes()))
    assert content.effective_version(repo) == HIGH
    ids_, mat = content._vectors(repo)
    assert mat.shape == (3, 1152)
    assert content.finish_switch(repo) == 3  # eski vektörler silindi
    assert repo.db.execute("SELECT COUNT(*) FROM asset_vectors WHERE model=?", (STD,)).fetchone()[0] == 0


def test_intel_and_arm_vectors_coexist(archive):
    """Aynı kütüphane Apple Silicon (int8) ve eski Intel (uint8) Mac'te açılınca birbirinin vektörleri silinmez,
    bu Mac'te çalışmayan sürümün vektörleri aramada kullanılmaz."""
    for i in range(2):
        make_image(archive / f"t{i}.jpg", i + 1)
    repo = Repo.init(archive)
    scan.scan(repo)
    analyze.track(repo)
    analyze.analyze(repo, smart=False)
    ids = [r[0] for r in repo.db.execute("SELECT id FROM assets ORDER BY id")]
    other = siglip.UINT8_VERSION if siglip.MODEL_VERSION == siglip.INT8_VERSION else siglip.INT8_VERSION
    rng = np.random.default_rng(0)
    with repo.db as conn:
        for a in ids:
            for m in (siglip.MODEL_VERSION, other):
                conn.execute("INSERT INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)", (a, m, _vec(rng, 768).tobytes()))
    assert not siglip.available(other)
    assert content.effective_version(repo) == siglip.MODEL_VERSION
    assert content.finish_switch(repo) == 0
    assert repo.db.execute("SELECT COUNT(*) FROM asset_vectors WHERE model=?", (other,)).fetchone()[0] == 2
