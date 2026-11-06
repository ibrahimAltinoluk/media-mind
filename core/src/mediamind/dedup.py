"""Tekrar tespiti ve hangi kopyanın tutulacağına karar verme.

Türler:
  exact  aynı içerik hash'i (tek asset, birden çok dosya)
  near   algısal hash (pHash) Hamming mesafesi eşiğin altında olan farklı fotoğraflar
  video  süre farkı %1'den az ve kare pHash'i yakın olan videolar
Seri çekim tespiti CLIP gerektirdiği için Aşama 4'te gelecek.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .kinds import FORMAT_RANK, ext_of
from .repo import Repo

MESSENGER_RE = re.compile(r"whatsapp|telegram|signal|messenger|viber|wechat|bip", re.I)


@dataclass
class Member:
    file_id: int
    asset_id: int
    rel_path: str
    size: int
    kind: str
    width: int | None
    height: int | None
    taken_at: str | None
    taken_at_source: str | None
    has_gps: bool
    bitrate: int | None
    first_seen: str
    tracked: bool
    blur: float | None = None
    role: str = "delete"
    reason: str = ""


@dataclass
class Group:
    kind: str
    members: list[Member] = field(default_factory=list)
    id: int | None = None

    @property
    def keep(self) -> Member:
        return next(m for m in self.members if m.role == "keep")

    @property
    def gain_bytes(self) -> int:
        return sum(m.size for m in self.members if m.role == "delete")


class _UF:
    def __init__(self):
        self.p: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _near_pairs(ids: list[int], hashes: list[str], threshold: int, block: int = 1024) -> list[tuple[int, int]]:
    if len(ids) < 2:
        return []
    arr = np.array([int(h, 16) for h in hashes], dtype=np.uint64)
    pairs = []
    for start in range(0, len(arr), block):
        a = arr[start : start + block]
        rest = arr[start:]
        dist = np.bitwise_count(a[:, None] ^ rest[None, :])
        ii, jj = np.nonzero(dist <= threshold)
        for i, j in zip(ii.tolist(), jj.tolist()):
            gi, gj = start + i, start + j
            if gj > gi:
                pairs.append((ids[gi], ids[gj]))
    return pairs


def _criteria(m: Member, rule: str, primary: list[str]) -> tuple:
    pixels = (m.width or 0) * (m.height or 0)
    fmt = FORMAT_RANK.get(ext_of(m.rel_path), 1) if m.kind == "photo" else (m.bitrate or 0)
    exif_ok = int(m.taken_at_source in ("exif", "quicktime")) + int(m.has_gps)
    not_msg = int(not MESSENGER_RE.search(m.rel_path))
    in_primary = int(any(m.rel_path == p or m.rel_path.startswith(p.rstrip("/") + "/") for p in primary))
    # Daha eski tarih daha iyi → ters sıralanabilir sayı
    older = -int((m.taken_at or "99999999999999").replace("-", "").replace(":", "").replace("T", "")[:14] or 0)
    first = -int(m.first_seen.replace("-", "").replace(":", "").replace("T", "")[:14] or 0)
    shorter = -len(m.rel_path)
    quality = (pixels, fmt, exif_ok, not_msg)
    if rule == "oldest":
        return (older, first, *quality, in_primary, shorter)
    if rule == "primary_folder":
        return (in_primary, *quality, older, first, shorter)
    return (*quality, in_primary, older, first, shorter)


_LABELS = ["çözünürlük", "format/kalite", "EXIF eksiksizliği", "mesajlaşma kopyası", "ana klasör", "tarih", "ilk görülme", "yol"]


def _reason(keep: Member, other: Member, rule: str, primary: list[str]) -> str:
    ck, co = _criteria(keep, rule, primary), _criteria(other, rule, primary)
    order = {
        "quality": [0, 1, 2, 3, 4, 5, 6, 7],
        "oldest": [5, 6, 0, 1, 2, 3, 4, 7],
        "primary_folder": [4, 0, 1, 2, 3, 5, 6, 7],
    }.get(rule, [0, 1, 2, 3, 4, 5, 6, 7])
    for idx, (a, b) in enumerate(zip(ck, co)):
        if a != b:
            label = _LABELS[order[idx]]
            if label == "çözünürlük":
                return f"düşük çözünürlük ({other.width}×{other.height})"
            if label == "mesajlaşma kopyası":
                return "mesajlaşma uygulaması kopyası"
            if label == "EXIF eksiksizliği":
                return "eksik EXIF (tarih/konum)"
            if label == "format/kalite":
                return "daha düşük kalite formatı" if other.kind == "photo" else "daha düşük bit hızı"
            if label == "ana klasör":
                return "ana klasör dışında"
            if label == "tarih":
                return "daha yeni"
            return "aynı dosya"
    return "aynı dosya"


def find(repo: Repo, threshold: int | None = None, rule: str | None = None, persist: bool = True) -> list[Group]:
    cfg = repo.config["dedup"]
    threshold = cfg.get("near_threshold", 6) if threshold is None else threshold
    rule = rule or cfg.get("keep_rule", "quality")
    primary = [p.strip("/") for p in cfg.get("primary_folders", [])]
    conn = repo.db
    rows = conn.execute(
        "SELECT f.id AS file_id, f.asset_id, f.rel_path, f.size, f.first_seen, f.tracked, a.kind, a.width, a.height, "
        "a.taken_at, a.taken_at_source, a.lat, a.bitrate, a.phash, a.duration_ms, a.blur_score "
        "FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.state='present'"
    ).fetchall()
    members_by_asset: dict[int, list[Member]] = {}
    asset_info: dict[int, object] = {}
    for r in rows:
        members_by_asset.setdefault(r["asset_id"], []).append(Member(
            file_id=r["file_id"], asset_id=r["asset_id"], rel_path=r["rel_path"], size=r["size"], kind=r["kind"],
            width=r["width"], height=r["height"], taken_at=r["taken_at"], taken_at_source=r["taken_at_source"],
            has_gps=r["lat"] is not None, bitrate=r["bitrate"], first_seen=r["first_seen"], tracked=bool(r["tracked"]),
            blur=r["blur_score"],
        ))
        asset_info[r["asset_id"]] = r

    uf = _UF()
    for aid in members_by_asset:
        uf.find(aid)
    for kind in ("photo", "video"):
        cand = [(aid, r["phash"]) for aid, r in asset_info.items() if r["kind"] == kind and r["phash"]]
        # Videolarda süre eşleşmesi zaten güçlü bir filtre; yeniden kodlama farkı için biraz daha toleranslı ol
        t = threshold + 4 if kind == "video" else threshold
        pairs = _near_pairs([c[0] for c in cand], [c[1] for c in cand], t)
        for a, b in pairs:
            if kind == "video":
                da, db_ = asset_info[a]["duration_ms"], asset_info[b]["duration_ms"]
                if not da or not db_ or abs(da - db_) > 0.01 * max(da, db_) + 50:
                    continue
            uf.union(a, b)

    comps: dict[int, list[int]] = {}
    for aid in members_by_asset:
        comps.setdefault(uf.find(aid), []).append(aid)

    groups: list[Group] = []
    for aids in comps.values():
        members = [m for aid in aids for m in members_by_asset[aid]]
        if len(members) < 2:
            continue
        kind = "exact" if len(aids) == 1 else ("video" if members[0].kind == "video" else "near")
        members.sort(key=lambda m: _criteria(m, rule, primary), reverse=True)
        keep = members[0]
        keep.role = "keep"
        keep.reason = {"oldest": "önerilen: en eski", "primary_folder": "önerilen: ana klasörde"}.get(
            rule, "önerilen: en yüksek kalite")
        for m in members[1:]:
            m.role = "delete"
            m.reason = "aynı dosya" if m.asset_id == keep.asset_id else _reason(keep, m, rule, primary)
        groups.append(Group(kind=kind, members=members))

    # Seri çekimler (görsel vektörler varsa): tekrar gruplarına girmemiş fotoğraflar arasında
    grouped_assets = {m.asset_id for g in groups for m in g.members}
    try:
        from .ml import content

        bursts = content.burst_groups(repo, exclude=grouped_assets)
    except Exception:  # noqa: BLE001 - model/vektör yoksa seri çekim tespiti atlanır
        bursts = []
    for aids in bursts:
        members = [m for aid in aids for m in members_by_asset.get(aid, [])]
        if len(members) < 2:
            continue
        # En net (bulanıklık skoru yüksek) ve en yüksek çözünürlüklü kare önerilir; diğerleri isteğe bağlı
        members.sort(key=lambda m: ((m.blur or 0), (m.width or 0) * (m.height or 0)), reverse=True)
        members[0].role, members[0].reason = "keep", "önerilen: en net kare"
        for m in members[1:]:
            m.role, m.reason = "optional", "seri çekimden benzer kare"
        groups.append(Group(kind="burst", members=members))

    order = {"exact": 0, "near": 1, "video": 2, "burst": 3}
    groups.sort(key=lambda g: (order[g.kind], -g.gain_bytes))

    if persist:
        with repo.lock(), conn:
            conn.execute("DELETE FROM dup_groups")
            for g in groups:
                g.id = conn.execute("INSERT INTO dup_groups(kind) VALUES (?)", (g.kind,)).lastrowid
                conn.executemany(
                    "INSERT INTO dup_members(group_id, file_id, role, reason) VALUES (?,?,?,?)",
                    [(g.id, m.file_id, m.role, m.reason) for m in g.members],
                )
    return groups


def plan_items(groups: list[Group], kinds: set[str] | None = None) -> list[tuple[str, int, str, str | None]]:
    """Silinmesi önerilen dosyalar için çöp planı öğeleri."""
    items = []
    for g in groups:
        if kinds and g.kind not in kinds:
            continue
        for m in g.members:
            if m.role == "delete":
                items.append(("trash", m.file_id, m.rel_path, None))
    return items
