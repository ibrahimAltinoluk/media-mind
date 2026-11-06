"""Yüz kümeleme ve kişiler.

Akış:
  1. Adı verilmiş kişilere yakın (benzerlik ≥ ASSIGN) yeni yüzler o kişiye otomatik atanır.
  2. Kalan adsız yüzler kendi aralarında kümelenir (ortalama bağlantılı hiyerarşik kümeleme, kosinüs).
     Küme kimliği, kümedeki en küçük yüz kimliğidir (yeniden çalıştırınca kararlı kalır).
  3. Adsız küme merkezleri kişilere SUGGEST eşiği üstünde yakınsa birleştirme önerisi çıkar.
"""

from __future__ import annotations

import numpy as np

from ..paths import nfc
from ..repo import Repo

# Eşikler gerçek bir arşivde (823 yüz) ölçülerek seçildi: bu ayarla karışık küme (en düşük ikili
# benzerlik < 0.25) kalmadı. Belirsiz yüzler (profil, bulanık, küçük) kümelemeye girmez; elle adlandırılabilir.
ASSIGN = 0.55             # adlı kişiye otomatik atama (merkez benzerliği)
CLUSTER = 0.55            # aynı kümeye girme (ortalama bağlantı)
PRUNE = 0.55              # küme merkezine bundan uzak üyeler kümeden çıkarılır
SUGGEST = 0.40            # birleştirme önerisi
MIN_QUALITY = 0.25        # kişi merkezine ve otomatik atamaya katılım
CLUSTER_QUALITY = 0.40    # kümelemeye katılım için kalite
CLUSTER_SCORE = 0.70      # ... tespit güveni
CLUSTER_MIN_PX = 48       # ... önizlemede (uzun kenar 1024) en az yüz genişliği

_FACE_PX = "((f.x2 - f.x1) * COALESCE(a.width, 1024) * 1024.0 / MAX(COALESCE(a.width, 1024), COALESCE(a.height, 1024)))"


def _vecs(rows) -> np.ndarray:
    if not rows:
        return np.zeros((0, 512), np.float32)
    return np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), 512)


def _centroids(conn) -> dict[int, np.ndarray]:
    out: dict[int, list] = {}
    rows = conn.execute("SELECT person_id, vec FROM faces WHERE person_id IS NOT NULL AND quality>=? AND length(vec)=2048",
                        (MIN_QUALITY,)).fetchall()
    for r in rows:
        out.setdefault(r["person_id"], []).append(np.frombuffer(r["vec"], dtype=np.float32))
    cents = {}
    for pid, vs in out.items():
        c = np.mean(vs, axis=0)
        cents[pid] = c / np.linalg.norm(c)
    return cents


def assign_known(repo: Repo) -> int:
    """Adsız yüzleri bilinen kişilerin merkezlerine ata (hızlı: tek matris çarpımı)."""
    with repo.lock(), repo.db as conn:
        cents = _centroids(conn)
        assigned = 0
        free = conn.execute(
            "SELECT id, vec, rejected_person_id FROM faces WHERE person_id IS NULL AND excluded IS NULL AND quality>=? AND length(vec)=2048",
            (MIN_QUALITY,)).fetchall()
        if cents and free:
            pids = list(cents)
            C = np.stack([cents[p] for p in pids])
            S = _vecs(free) @ C.T
            for r, s in zip(free, S):
                j = int(s.argmax())
                if s[j] >= ASSIGN and pids[j] != r["rejected_person_id"]:
                    conn.execute("UPDATE faces SET person_id=?, assigned_by='auto', cluster_id=NULL WHERE id=?", (pids[j], r["id"]))
                    assigned += 1
        return assigned


KNN_ABOVE = 2000  # bu kadar adsız yüzden fazlasında komşuluk grafiği kullanılır
KNN_K = 15


def _knn_graph(X: np.ndarray, k: int = KNN_K, block: int = 2048):
    """Her yüzün en benzer k yüzüyle bağlandığı seyrek komşuluk grafiği (bloklar halinde, düşük bellek)."""
    from scipy.sparse import csr_matrix

    n = len(X)
    k = min(k, n - 1)
    rows, cols = [], []
    for i in range(0, n, block):
        S = X[i:i + block] @ X.T
        idx = np.argpartition(-S, k, axis=1)[:, :k + 1]
        rows.append(np.repeat(np.arange(i, i + len(S)), k + 1))
        cols.append(idx.ravel())
    r, c = np.concatenate(rows), np.concatenate(cols)
    A = csr_matrix((np.ones(len(r), dtype=np.float32), (r, c)), shape=(n, n))
    return A.maximum(A.T)


def _cluster_free(repo: Repo) -> tuple[int, int]:
    """Adsız yüzleri gruplara ayır. Hesap kilitsiz yapılır (büyük arşivde saniyeler sürebilir); yazarken yalnız hâlâ
    adsız olan yüzler güncellenir, böylece bu sırada kullanıcının verdiği adlar ezilmez."""
    # (elle eklenen etiketler her zaman bir kişiye bağlıdır; kümelemeye girmez)
    free = repo.db.execute(
        f"SELECT f.id, f.vec FROM faces f JOIN assets a ON a.id=f.asset_id WHERE f.person_id IS NULL AND f.excluded IS NULL "
        f"AND f.manual=0 AND length(f.vec)=2048 "
        f"AND f.quality>=? AND f.score>=? AND {_FACE_PX}>=? ORDER BY f.id",
        (CLUSTER_QUALITY, CLUSTER_SCORE, CLUSTER_MIN_PX)).fetchall()
    found: list[list[int]] = []
    if len(free) >= 2:
        from sklearn.cluster import AgglomerativeClustering

        X = _vecs(free)
        # Büyük arşivde tam uzaklık matrisi (n²) hem yavaş hem bellek dostu değil: yalnız en yakın komşular
        # arasında birleştirme yapılır (8 bin yüzde ~10 kat hızlı, gruplar aynı kalitede)
        conn_graph = _knn_graph(X) if len(free) > KNN_ABOVE else None
        import warnings

        with warnings.catch_warnings():  # ayrık komşuluk bileşenleri beklenen durum (farklı kişiler)
            warnings.simplefilter("ignore", UserWarning)
            labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                             distance_threshold=1 - CLUSTER, connectivity=conn_graph).fit_predict(X)
        groups: dict[int, list[int]] = {}
        for i, lab in enumerate(labels):
            groups.setdefault(int(lab), []).append(i)
        for idx in groups.values():
            # Merkeze uzak üyeleri ayıkla (zincirleme birleşmeleri önler)
            for _ in range(3):
                if len(idx) < 2:
                    break
                c = X[idx].mean(axis=0)
                c /= np.linalg.norm(c)
                kept = [i for i in idx if float(X[i] @ c) >= PRUNE]
                if len(kept) == len(idx):
                    break
                idx = kept
            if len(idx) >= 2:
                found.append([free[i]["id"] for i in idx])
    with repo.lock(), repo.db as conn:
        conn.execute("UPDATE faces SET cluster_id=NULL WHERE person_id IS NULL AND animal IS NULL")
        clusters = 0
        for ids in found:
            cid = min(ids)
            n = sum(conn.execute("UPDATE faces SET cluster_id=? WHERE id=? AND person_id IS NULL AND excluded IS NULL",
                                 (cid, i)).rowcount for i in ids)
            clusters += n >= 2
    return clusters, len(free)


def recluster(repo: Repo) -> dict:
    assigned = assign_known(repo)
    clusters, candidates = _cluster_free(repo)
    return {"auto_assigned": assigned, "clusters": clusters, "candidates": candidates}


def request_recluster(repo: Repo, animal: bool = False) -> None:
    """Yeniden gruplamayı iste. Sunucuda arka plan işi olarak (birleştirilerek) çalışır; tıklama beklemez.
    Sunucu dışında (CLI, testler) hemen çalışır."""
    sched = getattr(repo, "recluster_scheduler", None)
    if sched is not None:
        sched(repo, animal)
    else:
        _recluster_for(repo, animal)


def exclude_face(repo: Repo, face_id: int, reason: str) -> None:
    """reason: 'removed' (bu kümeye/kişiye ait değil, kümelenmesin) | 'not_face' (yüz değil, gizle) | None (geri al)."""
    if reason not in ("removed", "not_face", None):
        raise ValueError("Geçersiz neden")
    with repo.lock(), repo.db as conn:
        conn.execute("UPDATE faces SET excluded=?, cluster_id=NULL, "
                     "rejected_person_id=COALESCE(person_id, rejected_person_id), "
                     "person_id=CASE WHEN ? IS NULL THEN person_id ELSE NULL END WHERE id=?", (reason, reason, face_id))


def dissolve_cluster(repo: Repo, cluster_id: int) -> int:
    """'Hepsi farklı kişiler': kümedeki yüzler bir daha otomatik kümelenmez (elle adlandırılabilir)."""
    with repo.lock(), repo.db as conn:
        return conn.execute("UPDATE faces SET excluded='removed', cluster_id=NULL WHERE cluster_id=? AND person_id IS NULL",
                            (cluster_id,)).rowcount


def persons(repo: Repo) -> list[dict]:
    rows = repo.db.execute(
        "SELECT p.id, p.name, p.kind, p.cover_face_id, p.hidden, COUNT(DISTINCT f.asset_id) AS photos, COUNT(f.id) AS faces, "
        "(SELECT f2.id FROM faces f2 WHERE f2.person_id=p.id ORDER BY f2.quality DESC LIMIT 1) AS best_face, "
        "(SELECT f3.id FROM faces f3 WHERE f3.id=p.cover_face_id AND f3.person_id=p.id) AS chosen_face "
        "FROM persons p LEFT JOIN faces f ON f.person_id=p.id GROUP BY p.id ORDER BY photos DESC, p.name").fetchall()
    # Seçilen kapak yüzü artık bu kişiye ait değilse (düzeltildi/silindi) en net yüze dönülür
    return [{**{k: r[k] for k in r.keys() if k != "chosen_face"}, "cover": r["chosen_face"] or r["best_face"],
             "cover_custom": r["chosen_face"] is not None} for r in rows]


def set_cover(repo: Repo, person_id: int, face_id: int | None) -> dict:
    """Kişinin kapak fotoğrafı (yüzü). None: otomatik (en net yüz)."""
    with repo.lock(), repo.db as conn:
        if conn.execute("SELECT 1 FROM persons WHERE id=?", (person_id,)).fetchone() is None:
            raise ValueError("Kişi bulunamadı")
        if face_id is not None and conn.execute(
                "SELECT 1 FROM faces WHERE id=? AND person_id=?", (face_id, person_id)).fetchone() is None:
            raise ValueError("Bu yüz bu kişiye ait değil")
        conn.execute("UPDATE persons SET cover_face_id=? WHERE id=?", (face_id, person_id))
    return {"person_id": person_id, "cover_face_id": face_id}


def clusters(repo: Repo, limit: int = 200) -> list[dict]:
    """Adsız yüz kümeleri. Ad önerisi olanlar (bilinen bir kişiye benzeyenler) önce, sonra fotoğraf sayısına göre.

    Öneri tüm kümeler için hesaplanır (yalnızca ilk sayfa için değil); kullanıcının "Bu o değil" dediği kişi
    o küme için bir daha önerilmez.
    """
    conn = repo.db
    stats = {r["cluster_id"]: r for r in conn.execute(
        "SELECT cluster_id, COUNT(*) AS faces, COUNT(DISTINCT asset_id) AS photos FROM faces "
        "WHERE person_id IS NULL AND cluster_id IS NOT NULL AND animal IS NULL GROUP BY cluster_id")}
    if not stats:
        return []
    cents = _centroids(conn)
    pids = list(cents)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM persons")}
    sugs: dict[int, dict] = {}
    if pids:
        C = np.stack([cents[p] for p in pids])
        # Küme merkezleri tek geçişte
        acc: dict[int, np.ndarray] = {}
        count: dict[int, int] = {}
        rejected: dict[int, dict[int, int]] = {}
        for r in conn.execute("SELECT cluster_id, vec, rejected_person_id FROM faces "
                              "WHERE person_id IS NULL AND cluster_id IS NOT NULL AND length(vec)=2048"):
            v = np.frombuffer(r["vec"], dtype=np.float32)
            cid = r["cluster_id"]
            acc[cid] = acc[cid] + v if cid in acc else v.copy()
            count[cid] = count.get(cid, 0) + 1
            if r["rejected_person_id"] is not None:
                d = rejected.setdefault(cid, {})
                d[r["rejected_person_id"]] = d.get(r["rejected_person_id"], 0) + 1
        for cid, v in acc.items():
            c = v / (np.linalg.norm(v) or 1.0)
            sims = C @ c
            for j in np.argsort(-sims):
                if sims[j] < SUGGEST:
                    break
                # Kümenin en az yarısı için "bu o değil" denmişse o kişi önerilmez (tek tük düzeltme engellemez)
                if rejected.get(cid, {}).get(pids[j], 0) * 2 >= count[cid]:
                    continue
                sugs[cid] = {"person_id": pids[j], "name": names.get(pids[j]), "similarity": round(float(sims[j]), 3)}
                break
    order = sorted(stats, key=lambda cid: (cid not in sugs, -(sugs[cid]["similarity"] if cid in sugs else 0), -stats[cid]["photos"]))
    out = []
    for cid in order[:limit]:
        fs = conn.execute("SELECT id, asset_id FROM faces WHERE cluster_id=? AND person_id IS NULL AND length(vec)=2048 "
                          "ORDER BY quality DESC LIMIT 60", (cid,)).fetchall()
        out.append({"cluster_id": cid, "faces": stats[cid]["faces"], "photos": stats[cid]["photos"],
                    "sample_faces": [f["id"] for f in fs[:6]],
                    "members": [{"face_id": f["id"], "asset_id": f["asset_id"]} for f in fs], "suggestion": sugs.get(cid)})
    return out


MERGE_SUGGEST = 0.50  # iki kişinin yüz merkezleri bu kadar benzerse "aynı kişi olabilir" önerilir


def merge_suggestions(repo: Repo, limit: int = 20) -> list[dict]:
    """Aynı kişi olabilecek adlandırılmış kişi çiftleri (ör. "Ayşe" ve "Ayşe Yılmaz" ayrı açılmış)."""
    conn = repo.db
    cents = _centroids(conn)
    pids = list(cents)
    if len(pids) < 2:
        return []
    M = np.stack([cents[p] for p in pids])
    S = M @ M.T
    dismissed = {(r["a"], r["b"]) for r in conn.execute("SELECT a, b FROM person_not_same")}
    info = {p["id"]: p for p in persons(repo)}
    out = []
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            if S[i, j] < MERGE_SUGGEST:
                continue
            a, b = sorted((pids[i], pids[j]))
            if (a, b) in dismissed or a not in info or b not in info:
                continue
            # Fotoğrafı çok olan "hedef" olsun (az olan ona katılır)
            big, small = (info[a], info[b]) if info[a]["photos"] >= info[b]["photos"] else (info[b], info[a])
            pick = lambda p: {k: p[k] for k in ("id", "name", "cover", "photos")}  # noqa: E731
            out.append({"into": pick(big), "other": pick(small), "similarity": round(float(S[i, j]), 3)})
    out.sort(key=lambda x: -x["similarity"])
    return out[:limit]


def dismiss_merge(repo: Repo, a: int, b: int) -> None:
    a, b = sorted((a, b))
    with repo.lock(), repo.db as conn:
        conn.execute("INSERT OR IGNORE INTO person_not_same(a, b) VALUES (?,?)", (a, b))


def pet_clusters(repo: Repo, limit: int = 200) -> list[dict]:
    """Adsız hayvan grupları; adlandırılmış bir hayvana benziyorsa ad önerisiyle (aynı tür içinde)."""
    from . import pets

    conn = repo.db
    stats = conn.execute(
        f"SELECT cluster_id, animal, COUNT(*) AS faces, COUNT(DISTINCT asset_id) AS photos FROM faces "
        f"WHERE animal IS NOT NULL AND person_id IS NULL AND cluster_id IS NOT NULL AND length(vec)={pets.VEC_BYTES} "
        f"GROUP BY cluster_id ORDER BY photos DESC LIMIT ?", (limit,)).fetchall()
    cents = pets.centroids(conn)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM persons")}
    out = []
    for st in stats:
        fs = conn.execute("SELECT id, asset_id, vec, rejected_person_id FROM faces WHERE cluster_id=? AND person_id IS NULL "
                          "ORDER BY quality DESC", (st["cluster_id"],)).fetchall()
        c = pets._vecs(fs).mean(0)
        c /= np.linalg.norm(c) or 1.0
        rej = {}
        for f in fs:
            if f["rejected_person_id"] is not None:
                rej[f["rejected_person_id"]] = rej.get(f["rejected_person_id"], 0) + 1
        sug = None
        best = 0.0
        for pid, (animal, pc) in cents.items():
            if animal != st["animal"] or rej.get(pid, 0) * 2 >= len(fs):
                continue
            s_ = float(pc @ c)
            if s_ >= pets.SUGGEST and s_ > best:
                best, sug = s_, {"person_id": pid, "name": names.get(pid), "similarity": round(s_, 3)}
        out.append({"cluster_id": st["cluster_id"], "animal": st["animal"], "faces": st["faces"], "photos": st["photos"],
                    "sample_faces": [f["id"] for f in fs[:6]], "members": [{"face_id": f["id"], "asset_id": f["asset_id"]} for f in fs[:60]],
                    "suggestion": sug})
    out.sort(key=lambda c: (c["suggestion"] is None, -c["photos"]))
    return out


def reject_suggestion(repo: Repo, cluster_id: int, person_id: int) -> int:
    """"Bu o değil": kümedeki yüzler bu kişiye bir daha önerilmez ya da otomatik atanmaz."""
    with repo.lock(), repo.db as conn:
        return conn.execute("UPDATE faces SET rejected_person_id=? WHERE cluster_id=? AND person_id IS NULL",
                            (person_id, cluster_id)).rowcount


def name_cluster(repo: Repo, cluster_id: int, name: str) -> dict:
    name = nfc(name.strip())[:80]
    if not name:
        raise ValueError("Ad boş olamaz")
    with repo.lock(), repo.db as conn:
        if not conn.execute("SELECT 1 FROM faces WHERE cluster_id=? AND person_id IS NULL LIMIT 1", (cluster_id,)).fetchone():
            # Arka planda yeniden gruplama bu sırada grubu değiştirmiş olabilir
            raise ValueError("Bu grup az önce yeniden düzenlendi; liste yenilendi, lütfen tekrar dene.")
        animal = conn.execute("SELECT animal FROM faces WHERE cluster_id=? AND animal IS NOT NULL LIMIT 1", (cluster_id,)).fetchone()
        pid = _person_id(conn, name, "pet" if animal else "human")
        n = conn.execute("UPDATE faces SET person_id=?, assigned_by='user', cluster_id=NULL, rejected_person_id=NULL "
                         "WHERE cluster_id=? AND person_id IS NULL", (pid, cluster_id)).rowcount
    if not animal:
        assign_known(repo)  # yeni merkez benzer adsızları hemen çeker
    request_recluster(repo, bool(animal))  # kalan grupların yenilenmesi arka planda
    return {"person_id": pid, "name": name, "faces": n}


def _recluster_for(repo: Repo, animal: bool) -> None:
    if animal:
        from . import pets

        pets.recluster(repo)
    else:
        recluster(repo)


def assign_face(repo: Repo, face_id: int, name: str | None) -> dict:
    """Tek bir yüzü kişiye ata (name) ya da kişiden çıkar (None → 'bu o değil')."""
    with repo.lock(), repo.db as conn:
        f = conn.execute("SELECT person_id FROM faces WHERE id=?", (face_id,)).fetchone()
        if f is None:
            raise ValueError("Yüz bulunamadı")
        an = None
        if name is None:
            conn.execute("UPDATE faces SET rejected_person_id=person_id, person_id=NULL, assigned_by=NULL WHERE id=?", (face_id,))
            return {"face_id": face_id, "person_id": None}
        an = conn.execute("SELECT animal FROM faces WHERE id=?", (face_id,)).fetchone()["animal"]
        pid = _person_id(conn, name, "pet" if an else "human")
        conn.execute("UPDATE faces SET person_id=?, assigned_by='user', cluster_id=NULL, excluded=NULL WHERE id=?", (pid, face_id))
    if an:
        request_recluster(repo, True)  # adlandırılan hayvana benzeyenler de bulunur
    return {"face_id": face_id, "person_id": pid}


def _person_id(conn, name: str, kind: str = "human") -> int:
    name = nfc(name.strip())[:80]
    if not name:
        raise ValueError("Ad boş olamaz")
    row = conn.execute("SELECT id FROM persons WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    return row["id"] if row else conn.execute("INSERT INTO persons(name, kind) VALUES (?,?)", (name, kind)).lastrowid


def tag_person(repo: Repo, asset_id: int, name: str, box: tuple[float, float, float, float] | None = None) -> dict:
    """Kullanıcının elle eklediği kişi etiketi.

    box (0..1, görünen yönde) verilirse önce o bölgede yüz aranır; bulunursa yüz vektörü de kaydedilir ve kişinin
    tanınmasına katkı sağlar. Yüz bulunamazsa (arkadan, uzaktan) ya da box yoksa (tüm fotoğraf) yalnızca etiket kaydedilir.
    """
    from PIL import Image

    from . import faces as faces_mod

    row = repo.db.execute("SELECT blake3, kind FROM assets WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        raise ValueError("Öğe bulunamadı")
    vec, quality, score = b"", 0.0, 0.0
    detected = False
    if box is not None:
        x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in box)
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if x2 - x1 < 0.005 or y2 - y1 < 0.005:
            raise ValueError("Seçilen alan çok küçük")
        box = (x1, y1, x2, y2)
        src = repo.thumb_path(row["blake3"], "l")
        if not src.exists():
            src = repo.thumb_path(row["blake3"], "m")
        if src.exists() and faces_mod.available():
            with Image.open(src) as im:
                W, H = im.size
                # Kutunun etrafına pay bırakarak kırp, düşük eşikle yüz ara
                mx, my = (x2 - x1) * 0.25, (y2 - y1) * 0.25
                cx1, cy1 = max(0.0, x1 - mx), max(0.0, y1 - my)
                cx2, cy2 = min(1.0, x2 + mx), min(1.0, y2 + my)
                crop = im.convert("RGB").crop((int(cx1 * W), int(cy1 * H), int(cx2 * W), int(cy2 * H)))
                found = [f for f in faces_mod.get().analyze(crop, threshold=0.3) if f.embedding is not None]
            if found:
                cw, ch = crop.size
                # Kullanıcının kutusunun merkezine en yakın yüz
                bx, by = ((x1 + x2) / 2 - cx1) * W, ((y1 + y2) / 2 - cy1) * H
                f = min(found, key=lambda f: ((f.bbox[0] + f.bbox[2]) / 2 - bx) ** 2 + ((f.bbox[1] + f.bbox[3]) / 2 - by) ** 2)
                box = (cx1 + f.bbox[0] / W, cy1 + f.bbox[1] / H, cx1 + f.bbox[2] / W, cy1 + f.bbox[3] / H)
                vec, quality, score, detected = f.embedding.tobytes(), f.quality, f.score, True
        if not detected and src.exists():
            pet = _pet_in_box(src, box)
            if pet is not None:
                animal_name, box, vec, score = pet
                with repo.lock(), repo.db as conn:
                    pid = _person_id(conn, name, "pet")
                    fid = conn.execute(
                        "INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec, person_id, assigned_by, manual, animal) "
                        "VALUES (?,?,?,?,?,?,?,?,?,'user',1,?)", (asset_id, *box, score, score, vec, pid, animal_name)).lastrowid
                request_recluster(repo, True)
                return {"face_id": fid, "person_id": pid, "detected": True, "animal": animal_name}
    else:
        box = (0.0, 0.0, 1.0, 1.0)
    with repo.lock(), repo.db as conn:
        pid = _person_id(conn, name)
        if not detected and box == (0.0, 0.0, 1.0, 1.0):
            dup = conn.execute("SELECT id FROM faces WHERE asset_id=? AND person_id=? AND manual=1 AND x1=0 AND y1=0 AND x2=1 AND y2=1",
                               (asset_id, pid)).fetchone()
            if dup:
                return {"face_id": dup["id"], "person_id": pid, "detected": False}
        fid = conn.execute(
            "INSERT INTO faces(asset_id, x1, y1, x2, y2, score, quality, vec, person_id, assigned_by, manual) "
            "VALUES (?,?,?,?,?,?,?,?,?,'user',1)", (asset_id, *box, score, quality, vec, pid)).lastrowid
    return {"face_id": fid, "person_id": pid, "detected": detected}


def _pet_in_box(src, box):
    """Kullanıcının çizdiği kutuda hayvan ara; bulunursa (tür, kutu, vektör, skor)."""
    from PIL import Image

    from . import pets

    if not pets.available():
        return None
    x1, y1, x2, y2 = box
    with Image.open(src) as im:
        im = im.convert("RGB")
        W, H = im.size
        mx, my = (x2 - x1) * 0.15, (y2 - y1) * 0.15
        cx1, cy1, cx2, cy2 = max(0.0, x1 - mx), max(0.0, y1 - my), min(1.0, x2 + mx), min(1.0, y2 + my)
        crop = im.crop((int(cx1 * W), int(cy1 * H), int(cx2 * W), int(cy2 * H)))
        found = pets.detect(crop)
        if found:
            animal, score, (a1, b1, a2, b2) = max(found, key=lambda d: d[1])
            nb = (cx1 + a1 * (cx2 - cx1), cy1 + b1 * (cy2 - cy1), cx1 + a2 * (cx2 - cx1), cy1 + b2 * (cy2 - cy1))
        else:
            # Bulucu tanımasa da kullanıcı hayvan olarak etiketlediyse kutunun kendisini kullan (tür bilinmiyor)
            return None
        vec = pets.embed([pets._crop(im, nb)])[0].astype(np.float32).tobytes()
    return animal, nb, vec, score


def remove_manual(repo: Repo, face_id: int) -> None:
    with repo.lock(), repo.db as conn:
        conn.execute("DELETE FROM faces WHERE id=? AND manual=1", (face_id,))


def merge_persons(repo: Repo, source_id: int, target_id: int) -> None:
    if source_id == target_id:
        return
    with repo.lock(), repo.db as conn:
        conn.execute("UPDATE faces SET person_id=? WHERE person_id=?", (target_id, source_id))
        conn.execute("DELETE FROM persons WHERE id=?", (source_id,))


def rename_person(repo: Repo, person_id: int, name: str) -> None:
    name = nfc(name.strip())[:80]
    with repo.lock(), repo.db as conn:
        other = conn.execute("SELECT id FROM persons WHERE name=? COLLATE NOCASE AND id<>?", (name, person_id)).fetchone()
        if other:
            conn.execute("UPDATE faces SET person_id=? WHERE person_id=?", (other["id"], person_id))
            conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
        else:
            conn.execute("UPDATE persons SET name=? WHERE id=?", (name, person_id))


def delete_person(repo: Repo, person_id: int) -> None:
    with repo.lock(), repo.db as conn:
        conn.execute("UPDATE faces SET person_id=NULL, assigned_by=NULL WHERE person_id=?", (person_id,))
        conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    request_recluster(repo)


def faces_of_asset(repo: Repo, asset_id: int) -> list[dict]:
    return [dict(r) for r in repo.db.execute(
        "SELECT f.id, f.x1, f.y1, f.x2, f.y2, f.score, f.quality, f.person_id, f.cluster_id, f.assigned_by, f.manual, f.frame_ms, p.name "
        "FROM faces f LEFT JOIN persons p ON p.id=f.person_id WHERE f.asset_id=? AND COALESCE(f.excluded, '')<>'not_face' "
        "ORDER BY f.x1", (asset_id,))]


def main_person(conn, asset_id: int) -> str | None:
    """Fotoğraftaki en büyük adlı yüzün sahibi ({kişi} belirteci için)."""
    r = conn.execute(
        "SELECT p.name FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.asset_id=? "
        "ORDER BY (f.x2-f.x1)*(f.y2-f.y1) DESC LIMIT 1", (asset_id,)).fetchone()
    return r["name"] if r else None
