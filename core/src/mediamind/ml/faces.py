"""Yüz tespiti (SCRFD) ve yüz vektörü (ArcFace), ONNX Runtime ile.

InsightFace'in yayımladığı ön/son işleme adımlarının numpy/Pillow ile yeniden yazımıdır
(OpenCV bağımlılığı yok: faster-whisper'ın PyAV'ı ile aynı süreçte çakışan ffmpeg kütüphaneleri yüklenmesin diye).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from . import models

MODEL_VERSION = "buffalo_l-scrfd10g-w600k"
DIM = 512
_DET_SIZE = 640
_STRIDES = (8, 16, 32)
_ANCHORS = 2

# ArcFace 112x112 hizalama için referans 5 nokta (gözler, burun, ağız köşeleri)
_ARC_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

_lock = threading.Lock()
_inst: "FaceModel | None" = None


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= thr]
    return keep


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """5 noktadan benzerlik dönüşümü (Umeyama): src → dst, 2x3 matris."""
    n = src.shape[0]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s_c, d_c = src - mu_s, dst - mu_d
    cov = d_c.T @ s_c / n
    u, sv, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[-1] = -1
    r = u @ np.diag(d) @ vt
    var = (s_c ** 2).sum() / n
    scale = (sv * d).sum() / var
    t = mu_d - scale * r @ mu_s
    return np.hstack([scale * r, t[:, None]])


def _warp(rgb: np.ndarray, m: np.ndarray, size: int = 112) -> np.ndarray:
    """Afin dönüşümle 112x112 kırpım (PIL; ters matris ister)."""
    a = np.vstack([m, [0, 0, 1]])
    inv = np.linalg.inv(a)[:2].reshape(-1)
    im = Image.fromarray(rgb).transform((size, size), Image.Transform.AFFINE, tuple(inv), resample=Image.Resampling.BILINEAR)
    return np.asarray(im)


def _sharpness(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


@dataclass
class Face:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (orijinal görüntü pikseli)
    score: float
    kps: np.ndarray  # 5x2
    embedding: np.ndarray | None = None  # 512, L2 normalize
    quality: float = 0.0


def _session(path):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


class FaceModel:
    def __init__(self):
        spec = models.FACES
        if not models.installed(spec):
            raise RuntimeError("Yüz modelleri kurulu değil")
        self.det = _session(models.path(spec, "detection/model.onnx"))
        self.rec = _session(models.path(spec, "recognition/model.onnx"))
        self.det_in = self.det.get_inputs()[0].name
        self.rec_in = self.rec.get_inputs()[0].name
        self._anchor_cache: dict[tuple[int, int], np.ndarray] = {}

    def _anchors(self, h: int, w: int, stride: int) -> np.ndarray:
        key = (h, w, stride)
        if key not in self._anchor_cache:
            grid = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            centers = (grid * stride).reshape(-1, 2)
            self._anchor_cache[key] = np.repeat(centers, _ANCHORS, axis=0)
        return self._anchor_cache[key]

    def detect(self, rgb: np.ndarray, threshold: float = 0.5, max_faces: int = 30) -> list[Face]:
        h0, w0 = rgb.shape[:2]
        scale = _DET_SIZE / max(h0, w0)
        nh, nw = int(round(h0 * scale)), int(round(w0 * scale))
        canvas = np.zeros((_DET_SIZE, _DET_SIZE, 3), np.uint8)
        canvas[:nh, :nw] = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR))
        blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outs = self.det.run(None, {self.det_in: blob})
        n = len(_STRIDES)
        scores_all, boxes_all, kps_all = [], [], []
        for i, stride in enumerate(_STRIDES):
            scores = outs[i].reshape(-1)
            deltas = outs[i + n] * stride
            kps = outs[i + 2 * n] * stride
            fh, fw = _DET_SIZE // stride, _DET_SIZE // stride
            anchors = self._anchors(fh, fw, stride)
            keep = np.where(scores >= threshold)[0]
            if not len(keep):
                continue
            a = anchors[keep]
            d = deltas[keep]
            boxes = np.stack([a[:, 0] - d[:, 0], a[:, 1] - d[:, 1], a[:, 0] + d[:, 2], a[:, 1] + d[:, 3]], axis=1)
            k = kps[keep].reshape(-1, 5, 2) + a[:, None, :]
            scores_all.append(scores[keep])
            boxes_all.append(boxes)
            kps_all.append(k)
        if not scores_all:
            return []
        scores = np.concatenate(scores_all)
        boxes = np.concatenate(boxes_all) / scale
        kps = np.concatenate(kps_all) / scale
        idx = _nms(boxes, scores, 0.4)[:max_faces]
        faces = []
        for i in idx:
            x1, y1, x2, y2 = boxes[i]
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(w0), x2), min(float(h0), y2)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            faces.append(Face(bbox=(float(x1), float(y1), float(x2), float(y2)), score=float(scores[i]), kps=kps[i]))
        return faces

    def embed(self, rgb: np.ndarray, faces: list[Face]) -> None:
        if not faces:
            return
        crops = []
        for f in faces:
            m = _similarity(f.kps.astype(np.float64), _ARC_DST.astype(np.float64))
            crops.append(_warp(rgb, m))
        blob = ((np.stack(crops).astype(np.float32) - 127.5) / 127.5).transpose(0, 3, 1, 2)
        vecs = np.concatenate([self.rec.run(None, {self.rec_in: blob[i:i + 1]})[0] for i in range(len(crops))])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.maximum(norms, 1e-8)
        for f, v, crop in zip(faces, vecs, crops):
            f.embedding = v.astype(np.float32)
            # Kalite: tespit güveni × yüz boyutu × netlik (küçük/bulanık yüzler kümelemeyi bozar)
            w = f.bbox[2] - f.bbox[0]
            sharp = _sharpness(np.asarray(Image.fromarray(crop).convert("L")))
            f.quality = float(f.score * min(1.0, w / 80.0) * min(1.0, sharp / 60.0))

    def analyze(self, im: Image.Image, threshold: float = 0.5) -> list[Face]:
        rgb = np.asarray(im.convert("RGB"))
        faces = self.detect(rgb, threshold)
        self.embed(rgb, faces)
        return faces


def get() -> FaceModel:
    global _inst
    with _lock:
        if _inst is None:
            _inst = FaceModel()
        return _inst


def available() -> bool:
    return models.installed(models.FACES)
