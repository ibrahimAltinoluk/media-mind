"""İçerik analizi aşamaları: görsel vektörü + model etiketleri, yüzler; anlamsal arama ve seri çekim tespiti."""

from __future__ import annotations

import json
import threading
from typing import Callable

import numpy as np
from PIL import Image

from ..repo import Repo
from . import faces as faces_mod
from . import siglip, vocab

Progress = Callable[[str, int, int], None]

_label_cache: dict[str, tuple[list[str], np.ndarray]] = {}


def label_matrix(version: str | None = None) -> tuple[list[str], np.ndarray]:
    """Etiket sözlüğünün metin vektörleri (model sürümü başına bir kez hesaplanır)."""
    m = siglip.get(version)
    if m.version not in _label_cache:
        names = list(vocab.VOCAB)
        rows = []
        for n in names:
            v = m.encode_texts(vocab.VOCAB[n][1]).mean(axis=0)
            rows.append(v / np.linalg.norm(v))
        _label_cache[m.version] = (names, np.stack(rows))
    return _label_cache[m.version]


def pick_tags(sims: np.ndarray, names: list[str], version: str | None = None) -> list[tuple[str, float]]:
    mu, sd = float(sims.mean()), float(sims.std()) or 1e-6
    order = np.argsort(-sims)
    abs_min = siglip.thresholds(version)["tag_abs"] if version else vocab.ABS_MIN
    out = []
    for i in order[: vocab.MAX_TAGS]:
        z = (sims[i] - mu) / sd
        if z >= vocab.Z_MIN and sims[i] >= abs_min:
            out.append((names[i], float(round(z, 2))))
    return out


def _load_m(repo: Repo, row) -> Image.Image | None:
    p = repo.thumb_path(row["blake3"], "m")
    if not p.exists():
        return None
    with Image.open(p) as im:
        return im.convert("RGB")


def _set_model_tags(conn, asset_id: int, tags: list[tuple[str, float]], version: str = siglip.MODEL_VERSION) -> None:
    conn.execute("DELETE FROM asset_tags WHERE asset_id=? AND source='model'", (asset_id,))
    for name, conf in tags:
        kind = vocab.VOCAB[name][0]
        conn.execute("INSERT INTO tags(name, kind) VALUES (?, ?) ON CONFLICT(name, kind) DO NOTHING", (name, kind))
        tid = conn.execute("SELECT id FROM tags WHERE name=? AND kind=?", (name, kind)).fetchone()["id"]
        conn.execute("INSERT OR REPLACE INTO asset_tags(asset_id, tag_id, source, confidence, model_ver) VALUES (?,?,'model',?,?)",
                     (asset_id, tid, conf, version))


def run_content(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None, batch: int = 16) -> int:
    """Görsel vektörü ve model etiketleri (önizleme 'm' görselinden; video için temsil karesi)."""
    if not todo:
        return 0
    m = siglip.get()  # seçili kademenin modeli
    names, L = label_matrix(m.version)
    conn = repo.db
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        ims, rows = [], []
        for r in part:
            im = _load_m(repo, r)
            if im is None:
                with conn:
                    mark(conn, r["id"], "content", "önizleme yok")
                continue
            ims.append(im)
            rows.append(r)
        vecs = m.encode_images(ims)
        sims = vecs @ L.T
        with conn:
            for r, v, s in zip(rows, vecs, sims):
                conn.execute("INSERT OR REPLACE INTO asset_vectors(asset_id, model, vec) VALUES (?,?,?)",
                             (r["id"], m.version, v.astype(np.float32).tobytes()))
                _set_model_tags(conn, r["id"], pick_tags(s, names, m.version), m.version)
                mark(conn, r["id"], "content")
        done += len(part)
        if progress:
            progress("content", done, len(todo))
    _vec_cache.pop(str(repo.root), None)
    return done


def run_faces(repo: Repo, todo: list, mark: Callable, progress: Progress | None = None) -> int:
    if not todo:
        return 0
    fm = faces_mod.get()
    conn = repo.db
    for n, r in enumerate(todo, 1):
        im = _load_m(repo, r)
        if im is None:
            with conn:
                mark(conn, r["id"], "faces", "önizleme yok")
            continue
        w, h = im.size
        found = [f for f in fm.analyze(im) if f.embedding is not None]
        with conn:
            conn.execute("DELETE FROM faces WHERE asset_id=? AND person_id IS NULL AND manual=0 AND animal IS NULL", (r["id"],))
            if not conn.execute("SELECT 1 FROM faces WHERE asset_id=? AND manual=0 AND animal IS NULL LIMIT 1", (r["id"],)).fetchone():
                conn.executemany(
                    "INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec) VALUES (?,?,?,?,?,?,?,?)",
                    [(r["id"], f.bbox[0] / w, f.bbox[1] / h, f.bbox[2] / w, f.bbox[3] / h, f.score, f.quality,
                      f.embedding.tobytes()) for f in found])
            mark(conn, r["id"], "faces")
        if progress:
            progress("faces", n, len(todo))
    return len(todo)


# ---------------------------------------------------------------- anlamsal arama

# SigLIP2 (int8) ölçümleri: doğru eşleşmeler ≳ 0.10, ilgisiz görseller ≲ 0.05.
SEM_ABS_MIN = 0.075   # bunun altı sonuç sayılmaz (karşılığı olmayan sorgular boş döner)
SEM_REL_GAP = 0.05    # en iyi sonuçtan bu kadar uzak olanlar elenir

_vec_cache: dict[str, tuple[tuple, np.ndarray, np.ndarray]] = {}
_vec_lock = threading.Lock()


def effective_version(repo: Repo) -> str:
    """Aramada kullanılacak model. Kademe değişmişse ve yeni modelle analiz henüz bitmediyse eski modelin
    vektörleri kullanılır (arama geçiş boyunca çalışmaya devam eder)."""
    target = siglip.target_version()
    counts = dict(repo.db.execute("SELECT model, COUNT(*) FROM asset_vectors GROUP BY model").fetchall())
    others = {m: n for m, n in counts.items() if m != target and siglip.available(m)}
    if not others:
        return target
    lacking = repo.db.execute(
        "SELECT COUNT(*) FROM asset_vectors o WHERE o.model<>? AND NOT EXISTS "
        "(SELECT 1 FROM asset_vectors t WHERE t.asset_id=o.asset_id AND t.model=?)", (target, target)).fetchone()[0]
    if lacking == 0:
        return target
    return max(others, key=lambda m: others[m])


def finish_switch(repo: Repo) -> int:
    """Yeni modelle analiz tamamlandıysa eski modelin vektörlerini ve video karelerini sil."""
    target = siglip.target_version()
    conn = repo.db
    lacking = conn.execute(
        "SELECT COUNT(*) FROM asset_vectors o WHERE o.model<>? AND NOT EXISTS "
        "(SELECT 1 FROM asset_vectors t WHERE t.asset_id=o.asset_id AND t.model=?)", (target, target)).fetchone()[0]
    if lacking:
        return 0
    with repo.lock(), conn:
        n = conn.execute("DELETE FROM asset_vectors WHERE model<>?", (target,)).rowcount
        conn.execute("DELETE FROM video_frames WHERE model<>?", (target,))
    _vec_cache.pop(str(repo.root), None)
    return n


def _vectors(repo: Repo, version: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    key = str(repo.root)
    version = version or effective_version(repo)
    sig = (version, *repo.db.execute("SELECT COUNT(*), COALESCE(MAX(asset_id), 0) FROM asset_vectors WHERE model=?",
                                     (version,)).fetchone())
    with _vec_lock:
        hit = _vec_cache.get(key)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
        rows = repo.db.execute("SELECT asset_id, vec FROM asset_vectors WHERE model=?", (version,)).fetchall()
        dim = siglip.profile(version)["dim"]
        ids = np.array([r["asset_id"] for r in rows], dtype=np.int64)
        mat = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), dim) \
            if rows else np.zeros((0, dim), np.float32)
        _vec_cache[key] = (sig, ids, mat)
        return ids, mat


def semantic_rank(repo: Repo, query: str, candidates: set[int] | None = None, limit: int = 300) -> list[tuple[int, float]]:
    """Metin sorgusuna en benzeyen asset'ler (asset_id, skor). Göreli eşik: en iyi sonuca yakın olanlar."""
    ver = effective_version(repo)
    if not siglip.available(ver):
        return []
    ids, mat = _vectors(repo, ver)
    if not len(ids):
        return []
    th = siglip.thresholds(ver)
    q = siglip.get(ver).encode_texts([query])[0]
    sims = mat @ q
    if candidates is not None:
        mask = np.isin(ids, np.fromiter(candidates, dtype=np.int64)) if candidates else np.zeros(len(ids), bool)
        sims = np.where(mask, sims, -1.0)
    order = np.argsort(-sims)
    top = float(sims[order[0]]) if len(order) else 0.0
    floor = max(th["sem_abs"], top - th["sem_gap"])
    out = []
    for i in order[:limit]:
        s = float(sims[i])
        if s < floor:
            break
        out.append((int(ids[i]), round(s, 4)))
    return out


VIS_MIN = 0.62  # görselle aramada bunun altı benzer sayılmaz (SigLIP2 görsel-görsel benzerliği)


def asset_vector(repo: Repo, asset_id: int) -> np.ndarray | None:
    ids, mat = _vectors(repo)
    hit = np.where(ids == asset_id)[0]
    return mat[hit[0]] if len(hit) else None


def vector_rank(repo: Repo, q: np.ndarray, candidates: set[int] | None = None, limit: int = 1000,
                exclude: int | None = None) -> list[tuple[int, float]]:
    """Bir görsel vektörüne en çok benzeyen asset'ler (asset_id, skor), benzerliğe göre."""
    ver = effective_version(repo)
    ids, mat = _vectors(repo, ver)
    if not len(ids) or len(q) != mat.shape[1]:
        return []
    vis_min = siglip.thresholds(ver)["vis_min"]
    sims = mat @ q
    if candidates is not None:
        mask = np.isin(ids, np.fromiter(candidates, dtype=np.int64)) if candidates else np.zeros(len(ids), bool)
        sims = np.where(mask, sims, -1.0)
    out = []
    for i in np.argsort(-sims)[:limit + 1]:
        s = float(sims[i])
        if s < vis_min:
            break
        if exclude is not None and int(ids[i]) == exclude:
            continue
        out.append((int(ids[i]), round(s, 4)))
    return out[:limit]


def image_vector(path_or_bytes, version: str | None = None) -> np.ndarray:
    """Kütüphane dışındaki bir görselin vektörü (sürükle-bırak ile arama)."""
    import io

    from PIL import ImageOps

    from ..media.thumbs import ensure_heif

    ensure_heif()
    im = Image.open(io.BytesIO(path_or_bytes)) if isinstance(path_or_bytes, (bytes, bytearray)) else Image.open(path_or_bytes)
    im.draft("RGB", (768, 768))
    im = ImageOps.exif_transpose(im).convert("RGB")
    return siglip.get(version).encode_images([im])[0]


def similar_assets(repo: Repo, asset_id: int, limit: int = 24) -> list[tuple[int, float]]:
    ids, mat = _vectors(repo)
    hit = np.where(ids == asset_id)[0]
    if not len(hit):
        return []
    sims = mat @ mat[hit[0]]
    order = np.argsort(-sims)
    floor = siglip.thresholds(effective_version(repo))["similar"]
    return [(int(ids[i]), round(float(sims[i]), 4)) for i in order[1:limit + 1] if sims[i] > floor]


# ---------------------------------------------------------------- seri çekimler

def burst_groups(repo: Repo, seconds: float = 3.0, min_sim: float | None = None, exclude: set[int] | None = None) -> list[list[int]]:
    """Kısa aralıkla çekilmiş ve görsel olarak çok benzeyen fotoğraf asset grupları."""
    ver = effective_version(repo)
    min_sim = siglip.thresholds(ver)["burst"] if min_sim is None else min_sim
    ids, mat = _vectors(repo, ver)
    if not len(ids):
        return []
    index = {int(a): i for i, a in enumerate(ids)}
    rows = repo.db.execute(
        "SELECT a.id, a.taken_at, a.camera_model FROM assets a WHERE a.kind='photo' AND a.taken_at IS NOT NULL "
        "AND a.taken_at_source IN ('exif','quicktime') AND EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present') "
        "ORDER BY a.taken_at").fetchall()
    from datetime import datetime

    groups: list[list[int]] = []
    cur: list[int] = []
    prev = None
    for r in rows:
        if exclude and r["id"] in exclude or r["id"] not in index:
            continue
        t = datetime.fromisoformat(r["taken_at"])
        ok = False
        if prev is not None and cur:
            dt = (t - prev[1]).total_seconds()
            if 0 <= dt <= seconds and r["camera_model"] == prev[2]:
                sim = float(mat[index[r["id"]]] @ mat[index[cur[-1]]])
                ok = sim >= min_sim
        if ok:
            cur.append(r["id"])
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [r["id"]]
        prev = (r["id"], t, r["camera_model"])
    if len(cur) > 1:
        groups.append(cur)
    return groups


def state_counts(repo: Repo) -> dict:
    c = repo.db
    total = c.execute("SELECT COUNT(*) FROM assets a WHERE EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1)").fetchone()[0]
    out = {"total": total}
    for st in ("content", "faces"):
        out[st] = c.execute(
            "SELECT COUNT(*) FROM assets a WHERE EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1) "
            f"AND json_extract(a.analysis_state, '$.{st}') IS NOT NULL").fetchone()[0]
    out["faces_found"] = c.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    return out


def model_tags_json(conn, asset_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT t.name, t.kind, at.source, at.confidence FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=? "
        "ORDER BY at.source DESC, at.confidence DESC", (asset_id,))]


__all__ = ["run_content", "run_faces", "semantic_rank", "similar_assets", "burst_groups", "state_counts", "json"]
