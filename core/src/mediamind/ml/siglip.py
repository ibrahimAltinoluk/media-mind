"""SigLIP2 görsel/metin kodlayıcısı (ONNX Runtime). Vektörler L2 normalize edilmiş 768 boyutlu float32'dir."""

from __future__ import annotations

import threading

import numpy as np
from PIL import Image

from . import models

MODEL_VERSION = "siglip2-base-224-int8"  # standart kademe (geriye uyumluluk için sabitler korunur)
DIM = 768
_MAX_LEN = 64
_lock = threading.Lock()
_inst: dict[str, "SigLIP"] = {}

# Model sürümü, giriş boyutu ve eşikler. Eşikler modele özgüdür (benzerlik ölçekleri farklı):
#   sem_abs/sem_gap: metinle arama · vis_min: görselle arama · similar: "benzer fotoğraflar"
#   burst: seri çekim · scene: video sahne değişimi · tag_abs: etiket için en düşük benzerlik
PROFILES: dict[str, dict] = {
    "standard": {"version": MODEL_VERSION, "spec": models.SIGLIP, "size": 224, "dim": 768,
                 "th": {"sem_abs": 0.075, "sem_gap": 0.05, "vis_min": 0.62, "similar": 0.75, "burst": 0.90, "scene": 0.82, "tag_abs": 0.065}},
}
BY_VERSION = {p["version"]: p for p in PROFILES.values()}


def _session(path):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    # int8 nicelenmiş modeller CoreML'de hızlanmıyor; CPU sağlayıcısı daha kararlı
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def target_version() -> str:
    """Yeni analizde kullanılacak model. (Daha büyük SigLIP2 So400m denendi; Türkçe aramalarda standart modelden
    kötü sonuç verdiği için sunulmuyor. Sürüm geçiş altyapısı — yan yana vektörler — ileride model değişirse kullanılır.)"""
    return MODEL_VERSION


def profile(version: str | None = None) -> dict:
    return BY_VERSION.get(version or target_version(), PROFILES["standard"])


def thresholds(version: str | None = None) -> dict:
    return profile(version)["th"]


class SigLIP:
    def __init__(self, version: str = MODEL_VERSION):
        from tokenizers import Tokenizer

        prof = profile(version)
        self.version = prof["version"]
        self.size = prof["size"]
        spec = prof["spec"]
        if not models.installed(spec):
            raise RuntimeError(f"{spec.label} kurulu değil")
        self.vision = _session(models.path(spec, "onnx/vision_model_int8.onnx"))
        self.text = _session(models.path(spec, "onnx/text_model_int8.onnx"))
        self.tok = Tokenizer.from_file(str(models.path(spec, "tokenizer.json")))
        pad_id = self.tok.token_to_id("<pad>")
        self.tok.enable_padding(length=_MAX_LEN, pad_id=pad_id, pad_token="<pad>")
        self.tok.enable_truncation(max_length=_MAX_LEN)
        self._txt_cache: dict[str, np.ndarray] = {}
        self.dim = prof["dim"]

    def preprocess(self, im: Image.Image) -> np.ndarray:
        im = im.convert("RGB").resize((self.size, self.size), Image.Resampling.BILINEAR)
        a = np.asarray(im, dtype=np.float32) / 255.0
        a = (a - 0.5) / 0.5
        return a.transpose(2, 0, 1)

    def encode_images(self, ims: list[Image.Image]) -> np.ndarray:
        if not ims:
            return np.zeros((0, self.dim), np.float32)
        batch = np.stack([self.preprocess(im) for im in ims])
        out = self.vision.run(["pooler_output"], {"pixel_values": batch})[0]
        return _norm(out)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        missing = [t for t in texts if t not in self._txt_cache]
        if missing:
            # SigLIP2 metinleri küçük harfle eğitildi; Türkçe İ/I için casefold yerine yerel dönüşüm
            enc = self.tok.encode_batch([_lower(t) for t in missing])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            out = _norm(self.text.run(["pooler_output"], {"input_ids": ids})[0])
            for t, v in zip(missing, out):
                self._txt_cache[t] = v
        return np.stack([self._txt_cache[t] for t in texts])


def _lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def _norm(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)


def get(version: str | None = None) -> SigLIP:
    """Modeli yükle (sürüm başına bir kez). Kademe geçişinde eski ve yeni model birlikte bellekte olabilir."""
    v = version or target_version()
    with _lock:
        if v not in _inst:
            _inst[v] = SigLIP(v)
        return _inst[v]


def unload(version: str) -> None:
    with _lock:
        _inst.pop(version, None)


def available(version: str | None = None) -> bool:
    return models.installed(profile(version)["spec"])
