"""Model kalitesi (standart / yüksek): seçili kademe, cihaz uygunluğu ve sürüm etiketleri.

Yüksek kademe büyük model indirir ve kütüphanenin yeniden analizini gerektirir. Bu yüzden:
* cihaz (Apple Silicon + yeterli bellek + disk) uygun değilse seçilemez,
* seçmeden önce kütüphanedeki birkaç öğeyle denenip süre tahmini gösterilir,
* geçiş bitene kadar arama eski modelle çalışmaya devam eder.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from typing import Any

from .. import settings
from . import models

# Yalnız konuşma dökümünde kalite seçimi var. (Görsel modelin büyük sürümü denendi; Türkçe aramalarda
# standart modelden kötü sonuç verdiği için kaldırıldı.)
KINDS = ("speech",)
SPECS = {
    "speech": {"standard": models.WHISPER, "high": models.WHISPER_HIGH},
}
LABELS = {"speech": "Konuşma dökümü"}
MIN_RAM_GB = {"speech": 16}
DISK_SLACK = 2 * 1024**3  # indirmenin üstüne bırakılacak boş alan

_cache: dict[str, Any] = {"t": 0.0, "v": None}


def current(kind: str) -> str:
    """Ayarlarda seçili kademe (2 sn önbellekli; sık çağrılır)."""
    now = time.monotonic()
    if _cache["v"] is None or now - _cache["t"] > 2:
        _cache["v"] = settings.load().get("models", {})
        _cache["t"] = now
    tier = _cache["v"].get(kind, "standard")
    return tier if kind in SPECS and tier in SPECS[kind] else "standard"


def invalidate() -> None:
    _cache["v"] = None


def spec(kind: str, tier: str | None = None) -> models.ModelSpec:
    return SPECS[kind][tier or current(kind)]


def stage_version(kind: str) -> int | str:
    """analysis_state'e yazılan sürüm: kademe değişince ilgili aşama yeniden yapılacak işe döner."""
    return 1 if current(kind) == "standard" else "1:high"


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def hardware() -> dict:
    ram = int(_sysctl("hw.memsize") or 0)
    chip = _sysctl("machdep.cpu.brand_string") or platform.processor()
    root = models.models_root()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "chip": chip,
        "apple_silicon": platform.machine() == "arm64",
        "ram_gb": round(ram / 1024**3) if ram else None,
        "cores": os.cpu_count(),
        "free_disk": shutil.disk_usage(root).free,
    }


def check(kind: str, tier: str, hw: dict | None = None) -> tuple[bool, str | None]:
    """Bu Mac bu kademeyi çalıştırabilir mi? → (uygun, neden değil)"""
    if tier == "standard":
        return True, None
    hw = hw or hardware()
    sp = spec(kind, tier)
    if not hw["apple_silicon"]:
        return False, "Apple Silicon (M serisi) işlemci gerekir"
    if (hw["ram_gb"] or 0) < MIN_RAM_GB[kind]:
        return False, f"En az {MIN_RAM_GB[kind]} GB bellek gerekir (bu Mac: {hw['ram_gb']} GB)"
    need = max(0, sp.size - _downloaded(sp)) + DISK_SLACK
    if hw["free_disk"] < need:
        return False, f"Yeterli boş disk alanı yok ({need / 1024**3:.1f} GB gerekir)"
    return True, None


def _downloaded(sp: models.ModelSpec) -> int:
    d = models.model_dir(sp)
    return sum((d / r).stat().st_size for r in sp.files if (d / r).exists())


def status() -> dict:
    hw = hardware()
    out: dict[str, Any] = {"hardware": hw, "kinds": {}}
    for kind in KINDS:
        opts = []
        for tier in ("standard", "high"):
            sp = spec(kind, tier)
            ok, why = check(kind, tier, hw)
            opts.append({"tier": tier, "model": sp.label, "size": sp.size, "downloaded": min(_downloaded(sp), sp.size),
                         "installed": models.installed(sp), "allowed": ok, "reason": why, "license": sp.license})
        out["kinds"][kind] = {"label": LABELS[kind], "current": current(kind), "options": opts}
    return out


# ---------------------------------------------------------------- deneme (süre tahmini)

def _workload(repo, kind: str) -> dict:
    c = repo.db
    live = "EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1)"
    photos = c.execute(f"SELECT COUNT(*) FROM assets a WHERE a.kind='photo' AND {live}").fetchone()[0]
    videos, secs = c.execute(f"SELECT COUNT(*), COALESCE(SUM(a.duration_ms), 0) / 1000.0 FROM assets a WHERE a.kind='video' AND {live}").fetchone()
    frames = c.execute("SELECT COUNT(*) FROM video_frames").fetchone()[0]
    analyzed_videos = c.execute("SELECT COUNT(DISTINCT asset_id) FROM video_frames").fetchone()[0]
    per_video = frames / analyzed_videos if analyzed_videos else 24
    return {"photos": photos, "videos": videos, "video_seconds": secs, "frames": int(per_video * videos),
            "transcripts_on": bool(repo.config.get("analysis", {}).get("transcripts", False))}


def trial(repo, kind: str, tier: str, progress=None) -> dict:
    """Seçilen kademeyi bu kütüphanenin birkaç öğesiyle dener; öğe başı süre ve toplam tahmini döndürür.
    Model yükleme süresi ölçüme katılmaz."""
    cur = current(kind)
    work = _workload(repo, kind)
    # konuşma: en uzun sesli videonun ilk 30 saniyesi
    from . import transcribe

    vids = repo.db.execute(
        "SELECT a.id, (SELECT f.fs_path FROM files f WHERE f.asset_id=a.id AND f.state='present' ORDER BY f.id LIMIT 1) AS fs "
        "FROM assets a WHERE a.kind='video' AND a.duration_ms >= 15000 ORDER BY a.duration_ms DESC LIMIT 5").fetchall()
    src = next((repo.root / v["fs"] for v in vids if v["fs"] and transcribe.has_audio(repo.root / v["fs"])), None)
    out = {"kind": kind, "tier": tier, "videos": work["videos"], "video_seconds": work["video_seconds"],
           "transcripts_on": work["transcripts_on"], "rtf": None, "rtf_current": None, "estimate_s": None}
    if src is None:
        return out
    sample = 30.0

    def rtf(t: str) -> float:
        transcribe._get(t)  # yükleme ölçüme girmesin
        s = time.perf_counter()
        transcribe.transcribe_file(src, tier=t, max_seconds=sample)
        return (time.perf_counter() - s) / sample

    if progress:
        progress("trial", 0, 2)
    out["rtf"] = rtf(tier)
    if progress:
        progress("trial", 1, 2)
    if models.installed(spec("speech", cur)):
        out["rtf_current"] = rtf(cur)
    out["estimate_s"] = out["rtf"] * work["video_seconds"]
    return out


def switch_progress(repo) -> dict:
    """Seçili kademeyle yeniden analizin ilerlemesi (bu kütüphanede)."""
    import json

    from ..analyze import stage_version

    c = repo.db
    live = "EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1)"
    rows = c.execute(f"SELECT a.kind, a.analysis_state FROM assets a WHERE {live} AND a.kind='video'").fetchall()
    tv = stage_version("transcript")
    total = done = 0
    for r in rows:
        if r["kind"] == "video":
            total += 1
            done += json.loads(r["analysis_state"] or "{}").get("transcript") == tv
    return {"speech": {"done": done, "total": total}}
