"""Tarama ve durum tespiti (git status benzeri).

Sınıflar:
  new       yol bilinmiyor, içerik bilinmiyor
  moved     yol bilinmiyor, içerik artık yerinde olmayan bir dosyayla aynı
  copy      yol bilinmiyor, içerik hâlâ yerinde duran bir dosyayla aynı (birebir tekrar)
  modified  yol biliniyor, içerik değişmiş
  missing   DB'de var, diskte yok
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import blake3
import pathspec

from .kinds import kind_of
from .paths import MM_DIR, nfc
from .repo import Repo

DEFAULT_IGNORE = [
    f"{MM_DIR}/",
    ".DS_Store",
    "._*",
    ".Spotlight-V100/",
    ".Trashes/",
    ".fseventsd/",
    ".TemporaryItems/",
    ".DocumentRevisions-V100/",
    "*.photoslibrary/",
    "*.photolibrary/",
    ".git/",
    "node_modules/",
    "$RECYCLE.BIN/",
    "System Volume Information/",
    "@eaDir/",
]

Progress = Callable[[str, int, int], None]


@dataclass
class ScanReport:
    new: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)  # (eski, yeni)
    copies: list[tuple[str, str]] = field(default_factory=list)  # (yeni, mevcut kopya)
    modified: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # bu taramada kaybolanlar
    reappeared: list[str] = field(default_factory=list)
    hashed_bytes: int = 0

    @property
    def changed(self) -> bool:
        return any([self.new, self.moved, self.copies, self.modified, self.missing, self.reappeared])


@dataclass
class WalkItem:
    rel: str  # NFC
    fs_rel: str  # diskteki yazım
    size: int
    mtime_ns: int


def load_ignore(repo: Repo) -> pathspec.PathSpec:
    lines = list(DEFAULT_IGNORE)
    f = repo.root / ".mediamindignore"
    if f.exists():
        lines += f.read_text("utf-8").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def walk(repo: Repo) -> Iterator[WalkItem]:
    spec = load_ignore(repo)
    root = repo.root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if spec.match_file(rel + "/"):
                continue
            if rel_dir and (Path(dirpath) / d / MM_DIR).exists():
                continue  # iç içe başka bir repo
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            if kind_of(name) is None:
                continue
            fs_rel = f"{rel_dir}/{name}" if rel_dir else name
            if spec.match_file(fs_rel):
                continue
            try:
                st = os.stat(os.path.join(dirpath, name), follow_symlinks=False)
            except OSError:
                continue
            if not os.path.isfile(os.path.join(dirpath, name)) or os.path.islink(os.path.join(dirpath, name)):
                continue
            yield WalkItem(rel=nfc(fs_rel), fs_rel=fs_rel, size=st.st_size, mtime_ns=st.st_mtime_ns)


def hash_file(path: Path) -> str:
    h = blake3.blake3(max_threads=blake3.blake3.AUTO)
    if path.stat().st_size == 0:
        return h.hexdigest()
    h.update_mmap(path)
    return h.hexdigest()


def _get_or_create_asset(conn, h: str, size: int, kind: str) -> tuple[int, bool]:
    row = conn.execute("SELECT id FROM assets WHERE blake3=?", (h,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute("INSERT INTO assets(blake3, size, kind) VALUES (?,?,?)", (h, size, kind))
    return cur.lastrowid, True


SCAN_BATCH = 300  # bu kadar dosya okununca veritabanına yazılır


def scan(repo: Repo, progress: Progress | None = None, workers: int | None = None) -> ScanReport:
    """Klasörü tarar. Dolaşma ve özet (hash) hesabı kilitsiz yapılır; repo kilidi yalnız her parça
    veritabanına yazılırken kısa süre tutulur. Böylece büyük/yavaş disklerde tarama sürerken kişi adlandırma,
    albüm, favori gibi işlemler beklemez. (Dosya taşıyan planlar aynı iş şeridinde sırayla çalışır.)"""
    report = ScanReport()
    conn = repo.db
    known = {
        r["rel_path"]: r
        for r in conn.execute(
            "SELECT f.id, f.rel_path, f.fs_path, f.size, f.mtime_ns, f.state, f.tracked, a.blake3 "
            "FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.state IN ('present','missing')"
        )
    }
    seen: set[str] = set()
    to_hash: list[tuple[WalkItem, object]] = []
    back: list[tuple[str, int]] = []  # geri gelen kayıplar (kilit altında yazılır)
    n_walked = 0
    for item in walk(repo):
        n_walked += 1
        if progress and n_walked % 500 == 0:
            progress("walk", n_walked, 0)
        seen.add(item.rel)
        row = known.get(item.rel)
        if row is not None and row["size"] == item.size and row["mtime_ns"] == item.mtime_ns:
            if row["state"] == "missing":
                report.reappeared.append(item.rel)
                back.append((item.fs_rel, row["id"]))
            continue
        to_hash.append((item, row))
    if progress:
        progress("walk", n_walked, n_walked)
    if back:
        with repo.lock(), conn:
            conn.executemany("UPDATE files SET state='present', fs_path=?, last_seen=datetime('now','localtime') WHERE id=?", back)

    disappeared = {rel: r for rel, r in known.items() if rel not in seen}
    # Taşınma adayları: bu taramada kaybolanlar + daha önce kayıp olanlar
    missing_by_hash: dict[str, list] = {}
    for r in disappeared.values():
        missing_by_hash.setdefault(r["blake3"], []).append(r)

    total_bytes = sum(i.size for i, _ in to_hash)
    done_bytes = 0
    auto_track = 1 if repo.config["scan"].get("auto_track") else 0

    def write(batch: list[tuple[WalkItem, object, str | None]]) -> None:
        with repo.lock(), conn:
            for item, row, h in batch:
                if h is None:
                    continue
                kind = kind_of(item.rel) or "photo"
                if row is None:
                    # Okuma sırasında başka bir işlem (ör. ayrı süreçteki bir plan) bu yola kayıt eklemiş olabilir
                    row = conn.execute(
                        "SELECT f.id, f.rel_path, f.size, f.mtime_ns, f.state, f.tracked, a.blake3 FROM files f "
                        "JOIN assets a ON a.id=f.asset_id WHERE f.rel_path=? AND f.state IN ('present','missing')",
                        (item.rel,)).fetchone()
                if row is not None:
                    if row["blake3"] == h:
                        conn.execute(
                            "UPDATE files SET size=?, mtime_ns=?, fs_path=?, state='present', "
                            "last_seen=datetime('now','localtime') WHERE id=?",
                            (item.size, item.mtime_ns, item.fs_rel, row["id"]),
                        )
                        if row["state"] == "missing":
                            report.reappeared.append(item.rel)
                    else:
                        asset_id, _ = _get_or_create_asset(conn, h, item.size, kind)
                        conn.execute(
                            "UPDATE files SET asset_id=?, size=?, mtime_ns=?, fs_path=?, state='present', "
                            "last_seen=datetime('now','localtime') WHERE id=?",
                            (asset_id, item.size, item.mtime_ns, item.fs_rel, row["id"]),
                        )
                        report.modified.append(item.rel)
                    continue

                candidates = missing_by_hash.get(h)
                if candidates:
                    old = candidates.pop(0)
                    disappeared.pop(old["rel_path"], None)
                    conn.execute(
                        "UPDATE files SET rel_path=?, fs_path=?, size=?, mtime_ns=?, state='present', "
                        "last_seen=datetime('now','localtime') WHERE id=?",
                        (item.rel, item.fs_rel, item.size, item.mtime_ns, old["id"]),
                    )
                    report.moved.append((old["rel_path"], item.rel))
                    continue

                asset_id, created = _get_or_create_asset(conn, h, item.size, kind)
                existing = None
                if not created:
                    existing = conn.execute(
                        "SELECT rel_path FROM files WHERE asset_id=? AND state='present' ORDER BY id LIMIT 1",
                        (asset_id,),
                    ).fetchone()
                conn.execute(
                    "INSERT INTO files(asset_id, rel_path, fs_path, size, mtime_ns, state, tracked) "
                    "VALUES (?,?,?,?,?,'present',?)",
                    (asset_id, item.rel, item.fs_rel, item.size, item.mtime_ns, auto_track),
                )
                if existing:
                    report.copies.append((item.rel, existing["rel_path"]))
                else:
                    report.new.append(item.rel)

    # Okunan dosyalar parça parça kaydedilir: tarama yarıda kalırsa (uygulama kapandı, disk çıkarıldı)
    # bir sonraki tarama yalnızca kalanları okur.
    batch: list[tuple[WalkItem, object, str | None]] = []
    last_flush = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=workers or min(8, (os.cpu_count() or 4)))
    try:
        futures = [(item, row, ex.submit(hash_file, repo.root / item.fs_rel)) for item, row in to_hash]
        for item, row, fut in futures:
            try:
                h = fut.result()
            except OSError:
                h = None
            done_bytes += item.size
            batch.append((item, row, h))
            if len(batch) >= SCAN_BATCH or time.monotonic() - last_flush > 10:
                write(batch)
                batch, last_flush = [], time.monotonic()
            if progress:
                progress("hash", done_bytes, total_bytes)
        write(batch)
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    report.hashed_bytes = done_bytes

    with repo.lock(), conn:
        gone_untracked = []
        for rel, r in disappeared.items():
            if r["state"] != "present":
                continue
            if (repo.root / (r["fs_path"] or rel)).exists():
                continue  # tarama sürerken geri gelmiş/taşınmış; bir sonraki taramada doğru işlenir
            if not r["tracked"]:
                # Kütüphaneye hiç eklenmemiş dosya kayboldu: saklanacak bilgisi yok, kaydı sessizce silinir
                gone_untracked.append(r["id"])
                continue
            conn.execute("UPDATE files SET state='missing' WHERE id=?", (r["id"],))
            report.missing.append(rel)
        if gone_untracked:
            q = ",".join("?" * len(gone_untracked))
            asset_ids = [x[0] for x in conn.execute(f"SELECT DISTINCT asset_id FROM files WHERE id IN ({q})", gone_untracked)]
            conn.execute(f"DELETE FROM files WHERE id IN ({q})", gone_untracked)
            if asset_ids:
                qa = ",".join("?" * len(asset_ids))
                conn.execute(f"DELETE FROM assets WHERE id IN ({qa}) AND id NOT IN (SELECT asset_id FROM files)", asset_ids)
    return report


@dataclass
class RepoCounts:
    tracked: int
    untracked: int
    missing: int
    trashed: int
    photos: int
    videos: int
    bytes: int


def counts(repo: Repo) -> RepoCounts:
    c = repo.db
    q = lambda sql: c.execute(sql).fetchone()[0] or 0  # noqa: E731
    return RepoCounts(
        tracked=q("SELECT COUNT(*) FROM files WHERE state='present' AND tracked=1"),
        untracked=q("SELECT COUNT(*) FROM files WHERE state='present' AND tracked=0"),
        missing=q("SELECT COUNT(*) FROM files WHERE state='missing'"),
        trashed=q("SELECT COUNT(*) FROM files WHERE state='trashed'"),
        photos=q("SELECT COUNT(*) FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.state='present' AND a.kind='photo'"),
        videos=q("SELECT COUNT(*) FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.state='present' AND a.kind='video'"),
        bytes=q("SELECT SUM(size) FROM files WHERE state='present'"),
    )


def forget_missing(repo: Repo) -> int:
    """Kayıp dosyaları DB'den siler (içerik başka kopyada yaşıyorsa asset kalır)."""
    with repo.lock(), repo.db as conn:
        n = conn.execute("DELETE FROM files WHERE state='missing'").rowcount
        conn.execute("DELETE FROM assets WHERE id NOT IN (SELECT DISTINCT asset_id FROM files)")
    return n
