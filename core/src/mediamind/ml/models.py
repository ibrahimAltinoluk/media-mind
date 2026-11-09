"""Offline model dosyaları: tanım, indirme ve konum.

Modeller repolar arasında ortaktır: <MEDIAMIND_HOME>/models/<ad>/...
Depo sürümleri sabit bir commit'e kilitlidir; dosyalar habersiz değişmez.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import registry


@dataclass(frozen=True)
class ModelSpec:
    name: str
    label: str
    repo: str
    revision: str
    files: dict[str, int]  # depo içi yol → boyut (bayt)
    license: str
    purpose: str
    urls: tuple[tuple[str, str], ...] = ()  # Hugging Face dışında barınan dosyalar: (depo içi yol, sabit adres)

    @property
    def size(self) -> int:
        return sum(self.files.values())


def int8_conv_supported() -> bool:
    """Intel paketindeki eski onnxruntime (1.19; macOS 10.15/11 için son sürüm) int8 ağırlıklı ConvInteger'ı
    çalıştıramıyor; o durumda görsel kodlayıcının uint8 nicelenmiş sürümü kullanılır (vektörleri ayrı sürüm sayılır)."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    if platform.machine() != "x86_64":
        return True
    try:
        return tuple(int(x) for x in version("onnxruntime").split(".")[:2]) >= (1, 20)
    except (PackageNotFoundError, ValueError):
        return True


SIGLIP_VISION = "onnx/vision_model_int8.onnx" if int8_conv_supported() else "onnx/vision_model_uint8.onnx"

SIGLIP = ModelSpec(
    name="siglip2-base",
    label="SigLIP2 (çok dilli görsel-metin)",
    repo="onnx-community/siglip2-base-patch16-224-ONNX",
    revision="ba1f3b0843f24bc5417d38e19c37b287d719b2f4",
    files={
        "config.json": 435,
        "preprocessor_config.json": 394,
        "special_tokens_map.json": 636,
        "tokenizer_config.json": 47240,
        "tokenizer.json": 34363039,
        SIGLIP_VISION: 94553333,  # int8 ve uint8 sürümleri aynı boyutta
        "onnx/text_model_int8.onnx": 283438275,
    },
    license="Apache-2.0",
    purpose="İçerik etiketleme, anlamsal arama, seri çekim tespiti",
)

FACES = ModelSpec(
    name="buffalo_l",
    label="InsightFace buffalo_l (yüz tespit + tanıma)",
    repo="immich-app/buffalo_l",
    revision="d09715916a0778919a770c343533641e250b8699",
    files={
        "detection/model.onnx": 16923827,
        "recognition/model.onnx": 174383860,
    },
    license="InsightFace: yalnızca ticari olmayan kullanım",
    purpose="Yüz tespiti ve kişi kümeleme",
)

WHISPER = ModelSpec(
    name="whisper-small",
    label="Whisper small (konuşma dökümü)",
    repo="Systran/faster-whisper-small",
    revision="536b0662742c02347bc0e980a01041f333bce120",
    files={
        "config.json": 2370,
        "model.bin": 483546902,
        "tokenizer.json": 2203239,
        "vocabulary.txt": 459861,
    },
    license="MIT",
    purpose="Videolarda konuşma dökümü ve dökümde arama",
)

PETS = ModelSpec(
    name="pets",
    label="Evcil hayvan tanıma (YOLOX-tiny + DINOv2-small)",
    repo="onnx-community/dinov2-small",
    revision="8b1f705a3a7f6f062f6bdd21986c1583d3ef105d",
    files={
        "preprocessor_config.json": 436,
        "onnx/model.onnx": 88532934,
        "yolox_tiny.onnx": 20219662,
    },
    license="Apache-2.0",
    purpose="Fotoğraflardaki kedi, köpek, kuş, at gibi hayvanları bulup tek tek ayırt etme",
    urls=(("yolox_tiny.onnx", "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"),),
)

# Yüksek kalite (isteğe bağlı): yalnız kullanıcı seçerse, cihaz uygunsa ve denemeden sonra indirilir/kullanılır
WHISPER_HIGH = ModelSpec(
    name="whisper-large-v3-turbo",
    label="Whisper large-v3-turbo (yüksek kalite konuşma dökümü)",
    repo="dropbox-dash/faster-whisper-large-v3-turbo",
    revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    files={
        "config.json": 2263,
        "model.bin": 1617884929,
        "preprocessor_config.json": 340,
        "tokenizer.json": 2710337,
        "vocabulary.json": 1068114,
    },
    license="MIT",
    purpose="Türkçede çok daha az hatalı konuşma dökümü",
)

SPECS = {s.name: s for s in (SIGLIP, FACES, WHISPER, PETS)}  # temel modeller ("eksikleri indir")
OPTIONAL = {s.name: s for s in (WHISPER_HIGH,)}
ALL_SPECS = {**SPECS, **OPTIONAL}


def models_root() -> Path:
    env = os.environ.get("MEDIAMIND_MODELS")
    return Path(env) if env else registry.home() / "models"


def model_dir(spec: ModelSpec) -> Path:
    return models_root() / spec.name


def path(spec: ModelSpec, rel: str) -> Path:
    return model_dir(spec) / rel


def installed(spec: ModelSpec) -> bool:
    d = model_dir(spec)
    return all((d / rel).exists() and (d / rel).stat().st_size == size for rel, size in spec.files.items())


def remove(spec: ModelSpec) -> None:
    import shutil

    shutil.rmtree(model_dir(spec), ignore_errors=True)


def status() -> list[dict]:
    out = []
    for s in SPECS.values():
        d = model_dir(s)
        have = sum((d / r).stat().st_size for r in s.files if (d / r).exists())
        out.append({"name": s.name, "label": s.label, "repo": s.repo, "license": s.license, "purpose": s.purpose,
                    "size": s.size, "downloaded": min(have, s.size), "installed": installed(s)})
    return out


def download(spec: ModelSpec, progress: Callable[[str, int, int], None] | None = None) -> Path:
    """Eksik dosyaları indirir (kaldığı yerden devam eder) ve boyutlarını doğrular."""
    from huggingface_hub import hf_hub_download

    d = model_dir(spec)
    d.mkdir(parents=True, exist_ok=True)
    done = 0
    for rel, size in spec.files.items():
        target = d / rel
        if not (target.exists() and target.stat().st_size == size):
            url = dict(spec.urls).get(rel)
            if url:
                got = _fetch(url, target)
            else:
                bar = _bar(lambda part, base=done: progress and progress(f"model:{spec.name}", base + min(part, size), spec.size))
                got = Path(hf_hub_download(spec.repo, rel, revision=spec.revision, local_dir=d, tqdm_class=bar))
            if got.stat().st_size != size:
                got.unlink(missing_ok=True)
                raise RuntimeError(f"{spec.repo}/{rel} beklenen boyutta değil")
        done += size
        if progress:
            progress(f"model:{spec.name}", done, spec.size)
    return d


def _bar(report: Callable[[int], object]):
    """İndirme sırasında bayt bayt ilerleme (büyük tek dosyalarda çubuk %0'da beklemesin)."""
    import io

    from tqdm import tqdm

    class Bar(tqdm):
        def __init__(self, *a, **k):
            k["disable"] = False
            k["file"] = io.StringIO()
            super().__init__(*a, **k)

        def update(self, n=1):
            r = super().update(n)
            report(int(self.n))
            return r

    return Bar


def _fetch(url: str, target: Path) -> Path:
    """Sabit bir adresten dosya indirir (önce geçici dosyaya, sonra yerine)."""
    import urllib.request

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "MediaMind/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        while chunk := r.read(1 << 20):
            fh.write(chunk)
    tmp.replace(target)
    return target
