"""Adsız kümelerde ad önerisi olanlar önce gelir; 'Bu o değil' denen öneri tekrar çıkmaz."""

import numpy as np
import pytest

from mediamind.ml import people
from mediamind.repo import Repo


def _vec(i: int, noise: float = 0.0, seed: int = 0) -> bytes:
    v = np.zeros(512, np.float32)
    v[i] = 1.0
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, 512).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


def test_suggested_clusters_first_and_reject(archive):
    repo = Repo.init(archive)
    conn = repo.db
    with conn:
        for a in range(1, 9):
            conn.execute("INSERT INTO assets(id, blake3, size, kind) VALUES (?,?,1,'photo')", (a, f"b{a}"))
        pid = conn.execute("INSERT INTO persons(name) VALUES ('Ayşe')").lastrowid
        face = lambda aid, vec, cid=None, person=None: conn.execute(  # noqa: E731
            "INSERT INTO faces(asset_id,x1,y1,x2,y2,score,quality,vec,cluster_id,person_id) VALUES (?,0,0,1,1,0.9,0.9,?,?,?)",
            (aid, vec, cid, person))
        face(1, _vec(0), person=pid)                       # Ayşe
        face(2, _vec(0, 0.02, 1), cid=100)                 # Ayşe'ye benzeyen küçük küme (2 fotoğraf)
        face(3, _vec(0, 0.02, 2), cid=100)
        for a in (4, 5, 6, 7):                             # benzemeyen büyük küme (4 fotoğraf)
            face(a, _vec(5, 0.02, a), cid=200)

    cl = people.clusters(repo)
    assert [c["cluster_id"] for c in cl] == [100, 200]     # öneri olan, fotoğrafı az olsa da önce
    assert cl[0]["suggestion"]["name"] == "Ayşe" and cl[1]["suggestion"] is None

    people.reject_suggestion(repo, 100, pid)
    cl = people.clusters(repo)
    assert [c["cluster_id"] for c in cl] == [200, 100] and cl[1]["suggestion"] is None


def test_merge_suggestions_and_dismiss(archive):
    repo = Repo.init(archive)
    conn = repo.db
    with conn:
        for a in range(1, 6):
            conn.execute("INSERT INTO assets(id, blake3, size, kind) VALUES (?,?,1,'photo')", (a, f"b{a}"))
        ids = [conn.execute("INSERT INTO persons(name) VALUES (?)", (n,)).lastrowid for n in ("Ayşe", "Ayşe Y.", "Mehmet")]
        add = lambda aid, vec, pid: conn.execute(  # noqa: E731
            "INSERT INTO faces(asset_id,x1,y1,x2,y2,score,quality,vec,person_id) VALUES (?,0,0,1,1,0.9,0.9,?,?)", (aid, vec, pid))
        add(1, _vec(0, 0.02, 1), ids[0]); add(2, _vec(0, 0.02, 2), ids[0]); add(3, _vec(0, 0.02, 3), ids[1])
        add(4, _vec(7, 0.02, 4), ids[2])
    s = people.merge_suggestions(repo)
    assert len(s) == 1 and s[0]["into"]["name"] == "Ayşe" and s[0]["other"]["name"] == "Ayşe Y."
    people.dismiss_merge(repo, ids[1], ids[0])
    assert people.merge_suggestions(repo) == []


def _v384(i: int, noise: float = 0.0, seed: int = 0) -> bytes:
    v = np.zeros(384, np.float32)
    v[i] = 1.0
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, 384).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


def test_pet_grouping_assignment_and_suggestion(archive):
    from mediamind.ml import pets

    repo = Repo.init(archive)
    conn = repo.db
    with conn:
        for a in range(1, 9):
            conn.execute("INSERT INTO assets(id, blake3, size, kind) VALUES (?,?,1,'photo')", (a, f"b{a}"))
        add = lambda aid, vec, animal: conn.execute(  # noqa: E731
            "INSERT INTO faces(asset_id,x1,y1,x2,y2,score,quality,vec,animal) VALUES (?,0,0,1,1,0.9,0.9,?,?)", (aid, vec, animal)).lastrowid
        k1 = add(1, _v384(0, 0.01, 1), "kedi"); add(2, _v384(0, 0.01, 2), "kedi")   # aynı kedi
        add(3, _v384(9, 0.01, 3), "kedi"); add(4, _v384(9, 0.01, 4), "kedi")         # başka kedi
        add(5, _v384(0, 0.01, 5), "köpek")                                           # benzer vektör ama köpek
    pets.recluster(repo)
    groups = people.pet_clusters(repo)
    assert sorted(len(g["members"]) for g in groups if g["animal"] == "kedi") == [2, 2]
    people.assign_face(repo, k1, "Pamuk")  # bir kediye ad ver → aynı kedinin diğer fotoğrafı da Pamuk olur
    named = conn.execute("SELECT f.asset_id FROM faces f JOIN persons p ON p.id=f.person_id WHERE p.name='Pamuk' ORDER BY 1").fetchall()
    assert [r[0] for r in named] == [1, 2]
    assert conn.execute("SELECT person_id FROM faces WHERE asset_id=5").fetchone()[0] is None  # köpek karışmaz
    assert conn.execute("SELECT kind FROM persons WHERE name='Pamuk'").fetchone()[0] == "pet"


def test_manual_pet_tag_is_adopted(archive):
    from mediamind.ml import pets

    repo = Repo.init(archive)
    conn = repo.db
    with conn:
        for a in (1, 2, 3):
            conn.execute("INSERT INTO assets(id, blake3, size, kind) VALUES (?,?,1,'photo')", (a, f"b{a}"))
        pid = conn.execute("INSERT INTO persons(name) VALUES ('Pamuk')").lastrowid
        # Önceden elle (yüzsüz) etiketlenmiş: 1. fotoğraf
        conn.execute("INSERT INTO faces(asset_id,x1,y1,x2,y2,score,quality,vec,person_id,assigned_by,manual) "
                     "VALUES (1,0,0,1,1,0,0,x'',?,'user',1)", (pid,))
        for a, seed in ((1, 1), (2, 2), (3, 3)):  # aynı kedi üç fotoğrafta
            conn.execute("INSERT INTO faces(asset_id,x1,y1,x2,y2,score,quality,vec,animal) VALUES (?,0,0,1,1,0.9,0.9,?,'kedi')",
                         (a, _v384(0, 0.01, seed)))
    assert pets.adopt_manual_tags(repo) == 1
    pets.recluster(repo)
    assert conn.execute("SELECT kind FROM persons WHERE id=?", (pid,)).fetchone()[0] == "pet"
    got = [r[0] for r in conn.execute("SELECT DISTINCT asset_id FROM faces WHERE person_id=? AND animal IS NOT NULL ORDER BY 1", (pid,))]
    assert got == [1, 2, 3]  # benzer fotoğraflar da Pamuk oldu


@pytest.mark.filterwarnings("ignore:the number of connected components")
def test_knn_graph_clusters_large_sets():
    """Büyük adsız yüz kümelerinde komşuluk grafiği ile gruplama: aynı kişinin yüzleri birlikte kalır."""
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    from mediamind.ml import people

    rng = np.random.default_rng(1)
    C = rng.normal(size=(40, 512))
    C /= np.linalg.norm(C, axis=1, keepdims=True)
    lab = rng.integers(0, 40, 2500)
    X = C[lab] + rng.normal(scale=0.02, size=(2500, 512))
    X = (X / np.linalg.norm(X, axis=1, keepdims=True)).astype(np.float32)
    g = people._knn_graph(X)
    assert g.shape == (2500, 2500) and g.nnz <= 2500 * 2 * (people.KNN_K + 1)
    out = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                  distance_threshold=1 - people.CLUSTER, connectivity=g).fit_predict(X)
    # her gerçek kişi tek gruba düşer
    for p in range(40):
        assert len(set(out[lab == p])) == 1
