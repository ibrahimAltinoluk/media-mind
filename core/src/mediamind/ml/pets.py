"""Evcil hayvan tanıma: YOLOX-tiny ile hayvanı bul, DINOv2-small ile "parmak izi" çıkar, aynı hayvanları grupla.

Hayvan kayıtları faces tablosunda tutulur (animal sütunu dolu, vektör 384 boyutlu); böylece görüntüleyicideki kutular,
adlandırma, kapak fotoğrafı ve süzme kişilerle aynı yolu kullanır. İnsan yüzü kodu yalnız 512 boyutlu vektörlere bakar.
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np
from PIL import Image

from ..repo import Repo
from . import models

MODEL_VERSION = "yolox-tiny+dinov2-small"
DIM = 384
VEC_BYTES = DIM * 4

# COCO sınıfları → Türkçe ad
ANIMALS = {14: "kuş", 15: "kedi", 16: "köpek", 17: "at", 18: "koyun", 19: "inek"}
DET_SCORE = 0.45      # hayvan bulma eşiği
MIN_AREA = 0.015      # fotoğrafın bu oranından küçük hayvanlar (uzaktakiler) alınmaz
ASSIGN = 0.70         # adlandırılmış hayvana otomatik atama (aynı tür içinde)
CLUSTER = 0.72        # adsız hayvanları gruplama
SUGGEST = 0.60        # "Pamuk olabilir mi?" önerisi

Progress = Callable[[str, int, int], None]
_lock = threading.Lock()
_sess: dict[str, object] = {}


def available() -> bool:
    return models.installed(models.PETS)


def _session(name: str):
    import onnxruntime as ort

    with _lock:
        if name not in _sess:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 4
            _sess[name] = ort.InferenceSession(str(models.path(models.PETS, name)), opts, providers=["CPUExecutionProvider"])
        return _sess[name]


# ------------------------------------------------------------------ bulma (YOLOX)

def _grids(size: int = 416) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for s in (8, 16, 32):
        n = size // s
        yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


_GRID, _STRIDE = _grids()


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float = 0.45) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        iou = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][iou < thr]
    return keep


def detect(im: Image.Image) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    """→ [(tür, skor, (x1, y1, x2, y2) 0..1)] — yalnız hayvanlar."""
    W, H = im.size
    r = min(416 / W, 416 / H)
    rs = im.convert("RGB").resize((max(1, int(W * r)), max(1, int(H * r))), Image.Resampling.BILINEAR)
    pad = np.full((416, 416, 3), 114, np.float32)
    arr = np.asarray(rs, dtype=np.float32)[:, :, ::-1]  # YOLOX BGR bekler, normalleştirme yok
    pad[: arr.shape[0], : arr.shape[1]] = arr
    out = _session("yolox_tiny.onnx").run(None, {"images": pad.transpose(2, 0, 1)[None]})[0][0]
    xy = (out[:, :2] + _GRID) * _STRIDE
    wh = np.exp(out[:, 2:4]) * _STRIDE
    cls_scores = out[:, 5:] * out[:, 4:5]
    res = []
    for cid, name in ANIMALS.items():
        sc = cls_scores[:, cid]
        idx = np.where(sc >= DET_SCORE)[0]
        if not idx.size:
            continue
        b = np.concatenate([xy[idx] - wh[idx] / 2, xy[idx] + wh[idx] / 2], 1) / r
        for k in _nms(b, sc[idx]):
            x1, y1, x2, y2 = b[k]
            x1, y1, x2, y2 = max(0, x1) / W, max(0, y1) / H, min(W, x2) / W, min(H, y2) / H
            if (x2 - x1) * (y2 - y1) >= MIN_AREA:
                res.append((name, float(sc[idx][k]), (float(x1), float(y1), float(x2), float(y2))))
    return res


# ------------------------------------------------------------------ parmak izi (DINOv2)

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def embed(crops: list[Image.Image]) -> np.ndarray:
    """Kırpıntılar → L2 normalize 384 boyutlu vektörler (CLS)."""
    batch = []
    for c in crops:
        c = c.convert("RGB")
        s = 256 / min(c.size)
        c = c.resize((max(224, round(c.width * s)), max(224, round(c.height * s))), Image.Resampling.BICUBIC)
        left, top = (c.width - 224) // 2, (c.height - 224) // 2
        a = (np.asarray(c.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255.0 - MEAN) / STD
        batch.append(a.transpose(2, 0, 1))
    out = _session("onnx/model.onnx").run(None, {"pixel_values": np.stack(batch)})[0][:, 0, :]
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def _crop(im: Image.Image, box) -> Image.Image:
    W, H = im.size
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * 0.08, (y2 - y1) * 0.08
    return im.crop((int(max(0, x1 - mx) * W), int(max(0, y1 - my) * H), int(min(1, x2 + mx) * W), int(min(1, y2 + my) * H)))


# ------------------------------------------------------------------ analiz aşaması

def run_pets(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None) -> int:
    conn = repo.db
    for n, r in enumerate(todo, 1):
        err = None
        try:
            p = repo.thumb_path(r["blake3"], "m")
            with Image.open(p) as im:
                im.load()
                dets = detect(im)
                vecs = embed([_crop(im, d[2]) for d in dets]) if dets else np.zeros((0, DIM), np.float32)
            with conn:
                conn.execute("DELETE FROM faces WHERE asset_id=? AND animal IS NOT NULL AND manual=0 AND person_id IS NULL", (r["id"],))
                for (name, score, (x1, y1, x2, y2)), v in zip(dets, vecs):
                    conn.execute("INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec, animal) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (r["id"], x1, y1, x2, y2, score, score, v.astype(np.float32).tobytes(), name))
        except Exception as e:  # noqa: BLE001 - okunamayan önizleme analizi durdurmaz
            err = f"{type(e).__name__}: {e}"
        with conn:
            mark(conn, r["id"], "pets", err)
        if progress:
            progress("pets", n, len(todo))
    if todo:
        adopt_manual_tags(repo)
        recluster(repo)
    return len(todo)


def adopt_manual_tags(repo: Repo) -> int:
    """Daha önce elle etiketlenmiş evcil hayvanları tanır: yalnız elle (yüzsüz) etiketi olan bir "kişi"nin fotoğraflarında
    tek bir hayvan bulunmuş ve insan yüzü yoksa, o hayvan bu ada bağlanır ve kişi evcil hayvan olarak işaretlenir.
    Böylece önceden "Pamuk" diye etiketlediğin kedinin benzer fotoğrafları da bulunur."""
    n = 0
    with repo.lock(), repo.db as conn:
        cands = conn.execute(
            "SELECT p.id FROM persons p WHERE p.kind<>'pet' AND EXISTS (SELECT 1 FROM faces f WHERE f.person_id=p.id) "
            "AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.person_id=p.id AND length(f.vec)>0)").fetchall()
        for c in cands:
            assets = [r[0] for r in conn.execute("SELECT DISTINCT asset_id FROM faces WHERE person_id=?", (c["id"],))]
            hits = []
            for aid in assets:
                animals = conn.execute(f"SELECT id FROM faces WHERE asset_id=? AND animal IS NOT NULL AND length(vec)={VEC_BYTES} "
                                       "AND person_id IS NULL", (aid,)).fetchall()
                humans = conn.execute("SELECT 1 FROM faces WHERE asset_id=? AND animal IS NULL AND length(vec)=2048", (aid,)).fetchone()
                if len(animals) == 1 and humans is None:
                    hits.append(animals[0]["id"])
            if hits and len(hits) * 2 >= len(assets):  # fotoğraflarının çoğunda tek başına bir hayvan var
                conn.executemany("UPDATE faces SET person_id=?, assigned_by='user', cluster_id=NULL WHERE id=?", [(c["id"], h) for h in hits])
                conn.execute("UPDATE persons SET kind='pet' WHERE id=?", (c["id"],))
                n += 1
    return n


def _vecs(rows) -> np.ndarray:
    return np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), DIM)


def recluster(repo: Repo) -> dict:
    """Adsız hayvanları adlandırılmış hayvanlara ata, kalanları aynı tür içinde grupla."""
    with repo.lock(), repo.db as conn:
        named = conn.execute(f"SELECT person_id, animal, vec FROM faces WHERE animal IS NOT NULL AND person_id IS NOT NULL "
                             f"AND length(vec)={VEC_BYTES}").fetchall()
        cents: dict[tuple[int, str], np.ndarray] = {}
        for pid_animal in {(r["person_id"], r["animal"]) for r in named}:
            vs = _vecs([r for r in named if (r["person_id"], r["animal"]) == pid_animal])
            c = vs.mean(0)
            cents[pid_animal] = c / np.linalg.norm(c)
        free = conn.execute(f"SELECT id, animal, vec, rejected_person_id FROM faces WHERE animal IS NOT NULL AND person_id IS NULL "
                            f"AND excluded IS NULL AND length(vec)={VEC_BYTES}").fetchall()
        assigned = 0
        rest = []
        for r in free:
            v = np.frombuffer(r["vec"], dtype=np.float32)
            best, bs = None, 0.0
            for (pid, animal), c in cents.items():
                if animal != r["animal"] or pid == r["rejected_person_id"]:
                    continue
                s = float(c @ v)
                if s > bs:
                    best, bs = pid, s
            if best is not None and bs >= ASSIGN:
                conn.execute("UPDATE faces SET person_id=?, assigned_by='auto', cluster_id=NULL WHERE id=?", (best, r["id"]))
                assigned += 1
            else:
                rest.append(r)
        # Aynı tür içinde basit tek bağlantılı gruplama (küçük sayılar için yeterli)
        conn.execute("UPDATE faces SET cluster_id=NULL WHERE animal IS NOT NULL AND person_id IS NULL")
        groups = 0
        by_animal: dict[str, list] = {}
        for r in rest:
            by_animal.setdefault(r["animal"], []).append(r)
        from .people import absorb_ignored

        for animal_name, rows in by_animal.items():
            V = _vecs(rows)
            S = V @ V.T
            parent = list(range(len(rows)))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    if S[i, j] >= CLUSTER:
                        parent[find(i)] = find(j)
            comp: dict[int, list[int]] = {}
            for i in range(len(rows)):
                comp.setdefault(find(i), []).append(rows[i]["id"])
            vecs = {r["id"]: r["vec"] for r in rows}
            for ids in absorb_ignored(conn, list(comp.values()), vecs, animal=animal_name):
                cid = min(ids)
                conn.executemany("UPDATE faces SET cluster_id=? WHERE id=?", [(cid, i) for i in ids])
                groups += 1
    return {"assigned": assigned, "clusters": groups}


def centroids(conn) -> dict[int, tuple[str, np.ndarray]]:
    rows = conn.execute(f"SELECT person_id, animal, vec FROM faces WHERE animal IS NOT NULL AND person_id IS NOT NULL "
                        f"AND length(vec)={VEC_BYTES}").fetchall()
    out: dict[int, tuple[str, np.ndarray]] = {}
    for pid in {r["person_id"] for r in rows}:
        rs = [r for r in rows if r["person_id"] == pid]
        c = _vecs(rs).mean(0)
        out[pid] = (rs[0]["animal"], c / np.linalg.norm(c))
    return out
