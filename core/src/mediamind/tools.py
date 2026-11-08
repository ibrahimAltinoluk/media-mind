"""Ajan araçları: MCP sunucusu ve yerleşik asistan aynı tanımları ve aynı izin kurallarını kullanır.

Kurallar:
  * Dosya sistemini değiştiren her şey bir plan olarak oluşturulur (create_plan).
  * apply_plan, ayarlarda "onayımı iste" açıksa planı uygulamaz; kullanıcı MediaMind'da onaylar.
  * Etiket/puan/favori gibi hafif değişiklikler "tag" izniyle doğrudan yapılır.
  * Önizleme görseli yalnızca "send_previews" izni açıksa döner.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable

from .media.geo import fold
from . import dedup, organize, plans, registry, scan
from . import settings as settings_mod
from .paths import nfc
from .repo import Repo, RepoError


class ToolError(Exception):
    """Ajana hata olarak döndürülecek, beklenen hata."""


@dataclass
class ToolContext:
    source: str  # "assistant" | "mcp:<istemci>"
    repo_resolver: Callable[[str], Repo] | None = None
    default_repo: str | None = None
    settings: dict = field(default_factory=settings_mod.load)
    created_plans: list[int] = field(default_factory=list)
    shown_files: list[dict] = field(default_factory=list)

    def repo(self, ref: str | None) -> Repo:
        ref = ref or self.default_repo
        if not ref:
            entries = [e for e in registry.load() if e.online]
            if len(entries) == 1:
                ref = entries[0].id
            else:
                raise ToolError("Birden çok repo var; 'repo' parametresiyle hangisi olduğunu belirtin (list_repos).")
        if self.repo_resolver:
            return self.repo_resolver(ref)
        try:
            return Repo.open(ref)
        except RepoError as e:
            raise ToolError(str(e))

    def need(self, perm: str) -> None:
        if not self.settings["agents"].get(perm, False):
            labels = {"read": "okuma", "tag": "etiketleme", "send_previews": "önizleme gönderme"}
            raise ToolError(f"Bu işlem için izin yok ({labels.get(perm, perm)}). Kullanıcı MediaMind ayarlarından açabilir.")


# ---------------------------------------------------------------- yardımcılar

def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _asset_ids(repo: Repo, file_ids: list[int]) -> dict[int, int]:
    if not file_ids:
        return {}
    rows = repo.db.execute(
        f"SELECT id, asset_id FROM files WHERE state='present' AND id IN ({','.join('?' * len(file_ids))})",
        [int(i) for i in file_ids],
    ).fetchall()
    return {r["id"]: r["asset_id"] for r in rows}


def tags_for(repo: Repo, asset_ids: list[int]) -> dict[int, list[str]]:
    if not asset_ids:
        return {}
    out: dict[int, list[str]] = {}
    for r in repo.db.execute(
        f"SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id "
        f"WHERE at.asset_id IN ({','.join('?' * len(asset_ids))}) ORDER BY t.name", asset_ids
    ):
        out.setdefault(r["asset_id"], []).append(r["name"])
    return out


def people_of(repo: Repo, asset_id: int) -> list[str]:
    return [r["name"] for r in repo.db.execute(
        "SELECT DISTINCT p.name FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.asset_id=? ORDER BY p.name", (asset_id,))]


def change_tags(repo: Repo, asset_ids: list[int], add: list[str], remove: list[str]) -> int:
    add = [nfc(t.strip())[:60] for t in add if t and t.strip()]
    remove = [nfc(t.strip()) for t in remove if t and t.strip()]
    with repo.lock(), repo.db as c:
        for name in add:
            c.execute("INSERT INTO tags(name, kind) VALUES (?, 'user') ON CONFLICT(name, kind) DO NOTHING", (name,))
            tid = c.execute("SELECT id FROM tags WHERE name=? AND kind='user'", (name,)).fetchone()["id"]
            c.executemany("INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, source) VALUES (?,?,'user')",
                          [(a, tid) for a in asset_ids])
        for name in remove:
            row = c.execute("SELECT id FROM tags WHERE name=? AND kind='user'", (name,)).fetchone()
            if row:
                c.executemany("DELETE FROM asset_tags WHERE asset_id=? AND tag_id=?", [(a, row["id"]) for a in asset_ids])
        c.execute("DELETE FROM tags WHERE kind='user' AND id NOT IN (SELECT tag_id FROM asset_tags)")
    return len(asset_ids)


def search(repo: Repo, a: dict) -> dict:
    where = ["f.state='present'"]
    p: list[Any] = []
    scope = a.get("scope", "tracked")
    if scope == "tracked":
        where.append("f.tracked=1")
    elif scope == "untracked":
        where.append("f.tracked=0")
    if a.get("kind") in ("photo", "video"):
        where.append("a.kind=?"); p.append(a["kind"])
    if a.get("year"):
        where.append("substr(a.taken_at,1,4)=?"); p.append(str(a["year"]))
    if a.get("month"):
        where.append("substr(a.taken_at,6,2)=?"); p.append(str(a["month"]).zfill(2))
    if a.get("date_from"):
        where.append("a.taken_at>=?"); p.append(a["date_from"])
    if a.get("date_to"):
        to = a["date_to"]
        where.append("a.taken_at<=?"); p.append(to + "T23:59:59" if len(to) == 10 else to)
    if a.get("place"):
        where.append("(p.name_fold LIKE ? ESCAPE '\\' OR p.admin1_fold LIKE ? ESCAPE '\\' OR p.admin2 LIKE ? ESCAPE '\\')")
        flike = f"%{_like(fold(a['place']))}%"; p += [flike, flike, f"%{_like(a['place'])}%"]
    if a.get("camera"):
        where.append("a.camera_model LIKE ? ESCAPE '\\'"); p.append(f"%{_like(a['camera'])}%")
    if a.get("folder"):
        where.append("f.rel_path LIKE ? ESCAPE '\\'"); p.append(_like(a["folder"].strip("/")) + "/%")
    if a.get("favorite"):
        where.append("a.favorite=1")
    if a.get("min_rating"):
        where.append("a.rating>=?"); p.append(int(a["min_rating"]))
    if a.get("has_location") is not None:
        where.append("a.lat IS NOT NULL" if a["has_location"] else "a.lat IS NULL")
    if a.get("album"):
        alb = _album(repo, a["album"])
        if alb["kind"] == "manual":
            where.append("a.id IN (SELECT asset_id FROM album_items WHERE album_id=?)"); p.append(alb["id"])
        else:
            raise ToolError("Akıllı albüm süzgeci search_media parametreleriyle birebir uygulanmalı: " + (alb["rules"] or "{}"))
    if a.get("date_source"):
        src = a["date_source"]
        if src == "estimated":  # çekim tarihi meta veriden gelmeyenler (dosya adı/dosya tarihi ya da hiç yok)
            where.append("(a.taken_at_source IS NULL OR a.taken_at_source IN ('filename','mtime'))")
        else:
            where.append("a.taken_at_source=?")
            p.append(src)
    if a.get("tag"):
        where.append("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name=?)")
        p.append(nfc(a["tag"]))
    if a.get("person"):
        where.append("a.id IN (SELECT fc.asset_id FROM faces fc JOIN persons pr ON pr.id=fc.person_id WHERE pr.name LIKE ? ESCAPE '\\')")
        p.append(f"%{_like(a['person'])}%")
    if a.get("duplicates_only"):
        where.append("EXISTS (SELECT 1 FROM dup_members dm WHERE dm.file_id=f.id)")
    if a.get("text"):
        for w in str(a["text"]).split():
            like = f"%{_like(w)}%"
            flike = f"%{_like(fold(w))}%"
            where.append("(f.rel_path LIKE ? ESCAPE '\\' OR p.name_fold LIKE ? ESCAPE '\\' OR a.camera_model LIKE ? ESCAPE '\\' "
                         "OR EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name LIKE ? ESCAPE '\\') "
                         "OR EXISTS (SELECT 1 FROM transcripts tr WHERE tr.asset_id=a.id AND tr.text LIKE ? ESCAPE '\\') "
                         "OR a.id IN (SELECT asset_id FROM asset_text WHERE fold LIKE ? ESCAPE '\\'))")
            p += [like, flike, like, like, like, flike]
    base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE {' AND '.join(where)}"
    order = "a.taken_at ASC" if a.get("sort") == "oldest" else "a.taken_at DESC"
    limit = max(1, min(int(a.get("limit") or 50), 500))
    offset = int(a.get("offset") or 0)
    cols = ("f.id AS file_id, f.rel_path, f.size, f.tracked, a.id AS asset_id, a.blake3, a.kind, a.taken_at, "
            "a.duration_ms, a.width, a.height, a.camera_model, a.rating, a.favorite, p.name AS place, p.admin1 AS region")
    scores: dict[int, float] = {}
    if a.get("semantic"):
        from .ml import content, siglip

        if not siglip.available():
            raise ToolError("Anlamsal arama için içerik modeli kurulu değil (Ayarlar › Akıllı analiz).")
        cand = {r[0] for r in repo.db.execute(f"SELECT DISTINCT a.id {base}", p)}
        ranked = content.semantic_rank(repo, str(a["semantic"]), cand, limit=2000)
        scores = dict(ranked)
        all_rows = repo.db.execute(f"SELECT {cols} {base}", p).fetchall()
        all_rows = sorted((r for r in all_rows if r["asset_id"] in scores), key=lambda r: -scores[r["asset_id"]])
        total = (len(all_rows), sum(r["size"] for r in all_rows))
        rows = all_rows[offset:offset + limit]
    else:
        total = repo.db.execute(f"SELECT COUNT(*), COALESCE(SUM(f.size),0) {base}", p).fetchone()
        rows = repo.db.execute(f"SELECT {cols} {base} ORDER BY {order}, f.rel_path LIMIT ? OFFSET ?",
                               [*p, limit, offset]).fetchall()
    tg = tags_for(repo, [r["asset_id"] for r in rows])
    items = []
    for r in rows:
        d = {k: r[k] for k in ("file_id", "rel_path", "kind", "taken_at", "place", "region", "camera_model", "size", "rating")}
        d["favorite"] = bool(r["favorite"])
        if r["kind"] == "video":
            d["duration_s"] = round((r["duration_ms"] or 0) / 1000)
        if r["width"]:
            d["resolution"] = f"{r['width']}x{r['height']}"
        if tg.get(r["asset_id"]):
            d["tags"] = tg[r["asset_id"]]
        if r["asset_id"] in scores:
            d["match_score"] = scores[r["asset_id"]]
        ppl = people_of(repo, r["asset_id"])
        if ppl:
            d["people"] = ppl
        if not r["tracked"]:
            d["untracked"] = True
        d["_blake3"] = r["blake3"]
        d["_asset_id"] = r["asset_id"]
        d["_duration_ms"] = r["duration_ms"]
        items.append(d)
    return {"total": total[0], "total_bytes": total[1], "returned": len(items), "items": items}


def _strip_private(obj):
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(x) for x in obj]
    return obj


# ---------------------------------------------------------------- araçlar

def t_list_repos(ctx: ToolContext, a: dict) -> dict:
    out = []
    for e in registry.load():
        d = {"id": e.id, "name": e.name, "path": e.last_path, "online": e.online}
        if e.online:
            try:
                c = scan.counts(ctx.repo(e.id))
                d.update(tracked=c.tracked, untracked=c.untracked, photos=c.photos, videos=c.videos, bytes=c.bytes)
            except ToolError:
                d["online"] = False
        out.append(d)
    return {"repos": out}


def t_repo_status(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    c = scan.counts(repo)
    pending = repo.db.execute("SELECT id, title, source FROM plans WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    return {"name": repo.name, "path": str(repo.root), "tracked": c.tracked, "untracked": c.untracked, "missing": c.missing,
            "in_trash": c.trashed, "photos": c.photos, "videos": c.videos, "bytes": c.bytes,
            "pending_plans": [dict(r) for r in pending]}


def t_search_media(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    res = search(repo, a)
    ctx.shown_files = [{"file_id": i["file_id"], "asset_id": i["_asset_id"], "blake3": i["_blake3"], "kind": i["kind"],
                        "rel_path": i["rel_path"], "taken_at": i["taken_at"], "duration_ms": i["_duration_ms"],
                        "place": i["place"], "region": i["region"]} for i in res["items"][:60]]
    return res


def t_get_media(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    ids = [int(i) for i in a.get("file_ids", [])][:50]
    if not ids:
        raise ToolError("file_ids boş")
    rows = repo.db.execute(
        f"SELECT a.*, f.id AS file_id, a.id AS asset_id, f.rel_path, f.size, f.tracked, f.first_seen, p.name AS place, p.admin1 AS region, "
        f"p.country_code FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
        f"WHERE f.id IN ({','.join('?' * len(ids))})", ids).fetchall()
    tg = tags_for(repo, [r["asset_id"] for r in rows])
    out = []
    for r in rows:
        copies = [x["rel_path"] for x in repo.db.execute(
            "SELECT rel_path FROM files WHERE asset_id=? AND state='present' AND id<>?", (r["asset_id"], r["file_id"]))]
        out.append({
            "file_id": r["file_id"], "rel_path": r["rel_path"], "kind": r["kind"], "size": r["size"],
            "taken_at": r["taken_at"], "taken_at_source": r["taken_at_source"], "tz_offset": r["tz_offset"],
            "place": r["place"], "region": r["region"], "country_code": r["country_code"], "lat": r["lat"], "lon": r["lon"],
            "camera": " ".join(x for x in [r["camera_make"], r["camera_model"]] if x) or None, "lens": r["lens"],
            "resolution": f"{r['width']}x{r['height']}" if r["width"] else None,
            "duration_s": round(r["duration_ms"] / 1000, 1) if r["duration_ms"] else None, "video_codec": r["video_codec"],
            "rating": r["rating"], "favorite": bool(r["favorite"]), "tags": tg.get(r["asset_id"], []),
            "identical_copies": copies, "tracked": bool(r["tracked"]), "blur_score": r["blur_score"],
        })
    return {"items": out}


def t_list_facets(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    base = ("FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
            "WHERE f.state='present' AND f.tracked=1")
    c = repo.db
    return {
        "years": [dict(r) for r in c.execute(f"SELECT substr(a.taken_at,1,4) AS year, COUNT(*) AS n {base} AND a.taken_at IS NOT NULL GROUP BY 1 ORDER BY 1")],
        "places": [dict(r) for r in c.execute(f"SELECT p.name AS place, p.admin1 AS region, p.country_code AS country, COUNT(*) AS n {base} AND p.name IS NOT NULL GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 100")],
        "cameras": [dict(r) for r in c.execute(f"SELECT a.camera_model AS camera, COUNT(*) AS n {base} AND a.camera_model IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 50")],
        "tags": [dict(r) for r in c.execute("SELECT t.name AS tag, COUNT(*) AS n FROM asset_tags at JOIN tags t ON t.id=at.tag_id GROUP BY 1 ORDER BY 2 DESC LIMIT 200")],
        "people": [dict(r) for r in c.execute("SELECT p.name AS person, COUNT(DISTINCT f.asset_id) AS n FROM persons p JOIN faces f ON f.person_id=p.id GROUP BY p.id ORDER BY 2 DESC LIMIT 200")],
        "unnamed_face_clusters": c.execute("SELECT COUNT(DISTINCT cluster_id) FROM faces WHERE person_id IS NULL AND cluster_id IS NOT NULL").fetchone()[0],
        "content_analysis": _smart_state(repo),
        "top_folders": [dict(r) for r in c.execute(
            "SELECT CASE WHEN instr(rel_path,'/')>0 THEN substr(rel_path,1,instr(rel_path,'/')-1) ELSE '(kök)' END AS folder, "
            "COUNT(*) AS n FROM files WHERE state='present' GROUP BY 1 ORDER BY 2 DESC LIMIT 50")],
    }


def _smart_state(repo: Repo) -> dict:
    from .ml import content, faces as faces_mod, siglip

    st = content.state_counts(repo)
    return {"content_model": siglip.available(), "face_model": faces_mod.available(), "assets": st["total"],
            "content_done": st["content"], "faces_done": st["faces"], "faces_found": st["faces_found"]}


def t_list_people(ctx: ToolContext, a: dict) -> dict:
    from .ml import people

    repo = ctx.repo(a.get("repo"))
    return {"people": [{"person_id": p["id"], "name": p["name"], "photos": p["photos"]} for p in people.persons(repo)],
            "unnamed_clusters": [{"cluster_id": c["cluster_id"], "photos": c["photos"],
                                  "possibly": c["suggestion"]["name"] if c["suggestion"] else None}
                                 for c in people.clusters(repo, 30)]}


def t_get_transcript(ctx: ToolContext, a: dict) -> dict:
    from .ml import transcribe, videoan

    repo = ctx.repo(a.get("repo"))
    aid = _asset_ids(repo, [int(a["file_id"])]).get(int(a["file_id"]))
    if aid is None:
        raise ToolError("Dosya bulunamadı")
    tr = transcribe.transcript(repo, aid)
    info = videoan.video_info(repo, aid)
    return {"language": tr["lang"], "segments": tr["segments"][:400],
            "scenes": [{"start_ms": s["start_ms"], "end_ms": s["end_ms"]} for s in info["scenes"]],
            "people": [{"name": p["name"], "segments_ms": p["segments"]} for p in info["people"]]}


def t_find_duplicates(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    groups = dedup.find(repo)
    kinds = set(a.get("kinds") or ["exact", "near", "video", "burst"])
    groups = [g for g in groups if g.kind in kinds]
    if a.get("folder"):
        pre = a["folder"].strip("/") + "/"
        groups = [g for g in groups if any(m.rel_path.startswith(pre) for m in g.members)]
    limit = max(1, min(int(a.get("limit") or 30), 200))
    return {
        "groups_total": len(groups),
        "files_suggested_for_deletion": sum(1 for g in groups for m in g.members if m.role == "delete"),
        "bytes_freed": sum(g.gain_bytes for g in groups),
        "groups": [{"group_id": g.id, "kind": g.kind,
                    "members": [{"file_id": m.file_id, "rel_path": m.rel_path, "suggestion": m.role, "reason": m.reason,
                                 "resolution": f"{m.width}x{m.height}" if m.width else None, "size": m.size}
                                for m in g.members]} for g in groups[:limit]],
    }


def t_preview_organize(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    try:
        moves = organize.preview(repo, a["template"], a.get("scope", "tracked"), a.get("folder"), a.get("base", ""))
    except organize.TemplateError as e:
        raise ToolError(str(e))
    return {"template": organize.resolve_template(a["template"]), "files_to_move": len(moves),
            "sample": [{"from": s, "to": d} for _, s, d in moves[:25]], "presets": organize.PRESETS}


def t_update_media(ctx: ToolContext, a: dict) -> dict:
    ctx.need("tag")
    repo = ctx.repo(a.get("repo"))
    amap = _asset_ids(repo, a.get("file_ids", []))
    if not amap:
        raise ToolError("Geçerli dosya yok")
    assets = sorted(set(amap.values()))
    if a.get("add_tags") or a.get("remove_tags"):
        change_tags(repo, assets, a.get("add_tags") or [], a.get("remove_tags") or [])
    with repo.lock(), repo.db as c:
        if a.get("rating") is not None:
            c.executemany("UPDATE assets SET rating=? WHERE id=?", [(max(0, min(5, int(a["rating"]))), x) for x in assets])
        if a.get("favorite") is not None:
            c.executemany("UPDATE assets SET favorite=? WHERE id=?", [(int(bool(a["favorite"])), x) for x in assets])
    return {"updated_files": len(amap)}


def t_create_plan(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    kind = a.get("kind")
    title = a.get("title") or ""
    ids = [int(i) for i in a.get("file_ids") or []]
    rows = {}
    if ids:
        rows = {r["id"]: r["rel_path"] for r in repo.db.execute(
            f"SELECT id, rel_path FROM files WHERE state='present' AND id IN ({','.join('?' * len(ids))})", ids)}
    try:
        if kind == "trash":
            if not rows:
                raise ToolError("Çöpe taşınacak geçerli dosya yok (file_ids)")
            pid = plans.create(repo, "trash", [("trash", i, rows[i], None) for i in ids if i in rows],
                               title=title or "Çöpe taşı", source=ctx.source)
        elif kind == "move":
            dest = (a.get("destination_folder") or "").strip().strip("/")
            if not dest or not rows:
                raise ToolError("Taşıma için file_ids ve destination_folder gerekli")
            pid = plans.create(repo, "move", [("move", i, rows[i], f"{dest}/{PurePosixPath(rows[i]).name}") for i in ids if i in rows],
                               title=title or f"{dest}/ klasörüne taşı", source=ctx.source)
        elif kind == "organize":
            if not a.get("template"):
                raise ToolError("Düzenleme planı için template gerekli")
            pid = organize.build_plan(repo, a["template"], a.get("scope", "tracked"), a.get("folder"),
                                      a.get("base", ""), source=ctx.source)
        elif kind == "dedup":
            groups = dedup.find(repo)
            kinds = set(a.get("dup_kinds") or ["exact", "near", "video"])
            pid = plans.create(repo, "dedup", dedup.plan_items(groups, kinds), title=title or "Tekrarları çöpe taşı",
                               source=ctx.source)
        else:
            raise ToolError("kind: trash | move | organize | dedup olmalı")
    except (plans.PlanError, organize.TemplateError, ValueError) as e:
        raise ToolError(str(e))
    p = plans.get(repo, pid)
    s = json.loads(p["summary"])
    ctx.created_plans.append(pid)
    its = plans.items(repo, pid)
    return {"plan_id": pid, "status": p["status"], "title": p["title"], "files": s.get("files", 0),
            "items": s.get("items", 0), "bytes": s.get("bytes", 0), "renamed_conflicts": s.get("renamed", 0),
            "sample": [{"from": i["src"], "to": "çöp" if i["op"] == "trash" else i["dst"]} for i in its[:15]],
            "next_step": ("Plan kullanıcı onayı bekliyor; MediaMind uygulamasında onaylanınca uygulanır."
                          if ctx.settings["agents"]["require_approval"] else "apply_plan ile uygulanabilir.")}


def t_apply_plan(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    pid = int(a["plan_id"])
    p = plans.get(repo, pid)
    if ctx.settings["agents"]["require_approval"]:
        return {"plan_id": pid, "status": p["status"], "applied": False,
                "message": "Taşıma ve silme planları kullanıcı onayı gerektiriyor. Kullanıcı planı MediaMind'da "
                           "inceleyip onaylayacak; bunu kullanıcıya söyle."}
    try:
        r = plans.apply(repo, pid)
    except plans.PlanError as e:
        raise ToolError(str(e))
    return {"plan_id": pid, "status": r.status, "done": r.done, "skipped": r.skipped, "failed": r.failed}


def t_undo_plan(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    pid = int(a["plan_id"])
    if ctx.settings["agents"]["require_approval"]:
        return {"plan_id": pid, "undone": False,
                "message": "Geri alma işlemi kullanıcı tarafından MediaMind > İşlem geçmişi ekranından yapılır."}
    try:
        r = plans.undo(repo, pid)
    except plans.PlanError as e:
        raise ToolError(str(e))
    return {"plan_id": pid, "status": r.status, "done": r.done, "skipped": r.skipped}


def t_list_plans(ctx: ToolContext, a: dict) -> dict:
    repo = ctx.repo(a.get("repo"))
    rows = plans.list_plans(repo, int(a.get("limit") or 20))
    return {"plans": [{"plan_id": r["id"], "kind": r["kind"], "title": r["title"], "source": r["source"],
                       "status": r["status"], "summary": json.loads(r["summary"]), "created_at": r["created_at"]}
                      for r in rows if not a.get("status") or r["status"] == a["status"]]}


def t_get_preview_image(ctx: ToolContext, a: dict) -> dict:
    ctx.need("send_previews")
    repo = ctx.repo(a.get("repo"))
    row = repo.db.execute(
        "SELECT a.blake3, a.kind, a.duration_ms, f.fs_path FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.id=?",
        (int(a["file_id"]),)).fetchone()
    if row is None:
        raise ToolError("Dosya bulunamadı")
    target = repo.thumb_path(row["blake3"], "m")
    if not target.exists():
        from .media import thumbs

        thumbs.render(repo.root / row["fs_path"], row["kind"], lambda s: repo.thumb_path(row["blake3"], s), row["duration_ms"])
    from PIL import Image

    with Image.open(target) as im:
        im = im.convert("RGB")
        im.thumbnail((768, 768))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    return {"__image__": buf.getvalue(), "media_type": "image/jpeg"}


REPO_PROP = {"type": "string", "description": "Repo adı ya da kimliği. Tek repo varsa ya da arayüzde bir repo açıksa boş bırakılabilir."}
def _album(repo: Repo, ref) -> Any:
    """Albümü kimliğe ya da ada (büyük/küçük harf duyarsız) göre bulur."""
    row = None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        row = repo.db.execute("SELECT * FROM albums WHERE id=?", (int(ref),)).fetchone()
    if row is None:
        row = repo.db.execute("SELECT * FROM albums WHERE name=? COLLATE NOCASE", (str(ref),)).fetchone()
    if row is None:
        raise ToolError(f"Albüm bulunamadı: {ref} (list_albums)")
    return row


def t_list_albums(ctx: ToolContext, a: dict) -> dict:
    from . import library

    repo = ctx.repo(a.get("repo"))
    return {"albums": [{k: x[k] for k in ("id", "name", "kind", "count", "rules")} for x in library.albums(repo)]}


def t_create_album(ctx: ToolContext, a: dict) -> dict:
    from . import library

    ctx.need("tag")
    repo = ctx.repo(a.get("repo"))
    kind = a.get("kind", "manual")
    rules = None
    if kind == "smart":
        rules = {k: str(v) for k, v in (a.get("rules") or {}).items() if v not in (None, "")}
        if rules.get("person") and not rules["person"].isdigit():  # kişi adı → kimlik
            row = repo.db.execute("SELECT id FROM persons WHERE name=? COLLATE NOCASE", (rules["person"],)).fetchone()
            if row is None:
                raise ToolError(f"Kişi bulunamadı: {rules['person']} (list_people)")
            rules["person"] = str(row["id"])
        if not rules:
            raise ToolError("Akıllı albüm için en az bir kural gerekli (year, place, person, tag, camera, kind, date_from, date_to, search)")
    try:
        aid = library.album_create(repo, a["name"], kind, rules)
        added = 0
        if kind == "manual" and a.get("file_ids"):
            added = library.album_add(repo, aid, sorted(set(_asset_ids(repo, a["file_ids"]).values())))
    except ValueError as e:
        raise ToolError(str(e))
    return {"album_id": aid, "name": a["name"], "kind": kind, "added": added}


def t_add_to_album(ctx: ToolContext, a: dict) -> dict:
    from . import library

    ctx.need("tag")
    repo = ctx.repo(a.get("repo"))
    alb = _album(repo, a["album"])
    assets = sorted(set(_asset_ids(repo, a.get("file_ids", [])).values()))
    if not assets:
        raise ToolError("Geçerli dosya yok")
    try:
        if a.get("remove"):
            return {"removed": library.album_remove(repo, alb["id"], assets), "album": alb["name"]}
        return {"added": library.album_add(repo, alb["id"], assets), "album": alb["name"]}
    except ValueError as e:
        raise ToolError(str(e))


def t_list_memories(ctx: ToolContext, a: dict) -> dict:
    from . import library

    repo = ctx.repo(a.get("repo"))
    return {"memories": [{k: m[k] for k in ("kind", "title", "subtitle", "count", "date_from", "date_to")}
                         for m in library.memories(repo)]}


def t_cleanup_suggestions(ctx: ToolContext, a: dict) -> dict:
    from . import library

    repo = ctx.repo(a.get("repo"))
    out = []
    for c in library.cleanup(repo):
        out.append({"category": c["key"], "title": c["title"], "count": c["count"], "bytes": c["bytes"],
                    "file_ids": [i["file_id"] for i in c["items"][:200]],
                    "examples": [i["rel_path"] for i in c["items"][:8]]})
    return {"categories": out, "note": "Silmek için create_plan(kind='trash', file_ids=...) kullan; kullanıcı onaylar."}


def t_fix_dates(ctx: ToolContext, a: dict) -> dict:
    from . import library

    ctx.need("tag")
    repo = ctx.repo(a.get("repo"))
    assets = sorted(set(_asset_ids(repo, a.get("file_ids", [])).values()))
    if not assets:
        raise ToolError("Geçerli dosya yok")
    if a.get("revert"):
        return {"reverted": library.revert_edits(repo, assets)}
    shift = int(a.get("shift_days", 0)) * 86400 + int(a.get("shift_hours", 0)) * 3600 + int(a.get("shift_minutes", 0)) * 60
    try:
        if a.get("set_to"):
            n = library.set_dates(repo, assets, value=a["set_to"])
        elif shift:
            n = library.set_dates(repo, assets, shift_seconds=shift)
        else:
            raise ToolError("shift_days/shift_hours/shift_minutes ya da set_to gerekli")
    except ValueError as e:
        raise ToolError(str(e))
    return {"updated": n, "note": "Yalnız MediaMind kaydı değişti; dosyalara yazılmadı. revert=true ile geri alınır."}


def t_set_location(ctx: ToolContext, a: dict) -> dict:
    from . import library

    ctx.need("tag")
    repo = ctx.repo(a.get("repo"))
    assets = sorted(set(_asset_ids(repo, a.get("file_ids", [])).values()))
    if not assets:
        raise ToolError("Geçerli dosya yok")
    lat, lon = a.get("lat"), a.get("lon")
    if (lat is None or lon is None) and a.get("place"):
        # Arşivde bilinen bir yer: o yerdeki fotoğrafların orta noktası
        rows = repo.db.execute("SELECT a.lat, a.lon FROM assets a JOIN places p ON p.id=a.place_id "
                               "WHERE (p.name_fold=? OR p.admin1_fold=?) AND a.lat IS NOT NULL",
                               (fold(a["place"]), fold(a["place"]))).fetchall()
        if not rows:
            raise ToolError(f"Arşivde '{a['place']}' konumlu fotoğraf yok; lat/lon ver.")
        lats, lons = sorted(r["lat"] for r in rows), sorted(r["lon"] for r in rows)
        lat, lon = lats[len(lats) // 2], lons[len(lons) // 2]
    if lat is None or lon is None:
        raise ToolError("place ya da lat/lon gerekli")
    try:
        r = library.set_location(repo, assets, float(lat), float(lon))
    except ValueError as e:
        raise ToolError(str(e))
    from . import settings as settings_mod

    if settings_mod.load()["files"].get("write_location", True):
        w = library.write_location_to_files(repo, assets)
        r["files_written"] = w["written"]
        if w["failed"]:
            r["file_errors"] = w["errors"][:5]
    return r


FILE_IDS = {"type": "array", "items": {"type": "integer"}, "description": "search_media'dan dönen file_id değerleri"}

TOOLS: list[dict] = [
    {"name": "list_repos", "fn": t_list_repos,
     "description": "Kayıtlı MediaMind repolarını (arşivleri), bağlı olup olmadıklarını ve öğe sayılarını listeler.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "repo_status", "fn": t_repo_status,
     "description": "Bir reponun özetini verir: takipteki/takipsiz/eksik dosya sayıları, boyut ve onay bekleyen planlar.",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "search_media", "fn": t_search_media,
     "description": ("Fotoğraf ve videoları meta verilere göre arar. Tarihler yerel saat ve ISO biçiminde (YYYY-MM-DD). "
                     "Yer adı şehir/il/ilçe üzerinde kısmi eşleşir (Türkçe karakter farkı gözetilmez: 'izmir' → İzmir). "
                     "'text' dosya adı, yer, kamera, etiketler, videolarda konuşulanlar (döküm) ve fotoğrafların içindeki yazılarda (OCR: fatura, tabela, belge) arar. 'semantic' ise görsel içeriği doğal dille arar "
                     "(ör. 'kumsalda köpek', 'doğum günü pastası', 'karlı dağ'); sonuçlar benzerliğe göre sıralanır ve diğer "
                     "filtrelerle birleşir. 'person' adı verilmiş kişileri yüz tanımaya göre filtreler (list_people). "
                     "Etiketlerde model etiketleri de vardır (deniz, köpek, ekran görüntüsü...). Sonuç sayısı 'total' alanındadır."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP,
         "text": {"type": "string"},
         "semantic": {"type": "string", "description": "Görsel içerik için doğal dil sorgusu (Türkçe ya da İngilizce)"},
         "person": {"type": "string", "description": "Kişi adı (yüz tanıma)"},
         "kind": {"type": "string", "enum": ["photo", "video"]},
         "date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "date_to": {"type": "string", "description": "YYYY-MM-DD (dahil)"},
         "year": {"type": "integer"}, "month": {"type": "integer", "minimum": 1, "maximum": 12},
         "place": {"type": "string"}, "camera": {"type": "string"},
         "folder": {"type": "string", "description": "Bu klasörün altındakiler (repo köküne göre)"},
         "tag": {"type": "string"}, "favorite": {"type": "boolean"},
         "min_rating": {"type": "integer", "minimum": 1, "maximum": 5},
         "has_location": {"type": "boolean"}, "duplicates_only": {"type": "boolean"},
         "album": {"type": "string", "description": "Elle oluşturulmuş albümün adı ya da kimliği (list_albums)"},
         "date_source": {"type": "string", "enum": ["exif", "quicktime", "filename", "mtime", "estimated"],
                         "description": "Çekim tarihinin kaynağı. 'estimated': EXIF/video tarihi olmayanlar (dosya adından ya da dosya tarihinden tahmin edilenler)"},
         "scope": {"type": "string", "enum": ["tracked", "untracked", "all"], "description": "Varsayılan tracked"},
         "sort": {"type": "string", "enum": ["newest", "oldest"]},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Varsayılan 50"},
         "offset": {"type": "integer", "minimum": 0}}}},
    {"name": "get_media", "fn": t_get_media,
     "description": "Verilen dosyaların tüm meta verilerini döndürür (tarih, konum, kamera, çözünürlük, etiketler, birebir kopyalar).",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP, "file_ids": FILE_IDS}, "required": ["file_ids"]}},
    {"name": "list_facets", "fn": t_list_facets,
     "description": "Arşivdeki yılları, yerleri, kameraları, etiketleri ve üst klasörleri sayılarıyla listeler. Sorguya başlamadan önce neyin var olduğunu görmek için kullan.",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "list_people", "fn": t_list_people,
     "description": "Yüz tanımayla bulunan, adı verilmiş kişileri ve henüz adlandırılmamış yüz kümelerini listeler.",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "get_video_details", "fn": t_get_transcript,
     "description": "Bir videonun konuşma dökümünü (zaman damgalı), sahnelerini ve kişilerin hangi aralıklarda göründüğünü döndürür.",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP, "file_id": {"type": "integer"}}, "required": ["file_id"]}},
    {"name": "find_duplicates", "fn": t_find_duplicates,
     "description": "Birebir aynı (exact), neredeyse aynı (near: yeniden boyut/WhatsApp kopyası), video kopyaları ve seri çekimleri (burst: art arda benzer kareler) gruplar; her grupta tutulacak ve silinmesi önerilen dosyaları gerekçesiyle verir. Seri çekimlerde diğer kareler yalnızca 'optional' önerilir.",
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP,
         "kinds": {"type": "array", "items": {"type": "string", "enum": ["exact", "near", "video", "burst"]}},
         "folder": {"type": "string", "description": "Yalnız bu klasördeki dosyaları içeren gruplar"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}}}},
    {"name": "preview_organize", "fn": t_preview_organize,
     "description": ("Klasör şablonuna göre dosyaların nereye taşınacağını gösterir, hiçbir şeyi değiştirmez. "
                     "Belirteçler: {yıl} {ay} {ay_adı} {gün} {şehir} {il} {ülke} {kamera} {tür} {dosya} {ad} {uzantı}. "
                     "Hazır şablonlar: yil-ay, yil, yil-konum, tur-yil-ay, kamera-yil."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "template": {"type": "string"},
         "scope": {"type": "string", "enum": ["tracked", "untracked", "all"]},
         "folder": {"type": "string", "description": "Yalnız bu klasördeki dosyalar"},
         "base": {"type": "string", "description": "Hedef kök klasör (ör. Arsiv)"}}, "required": ["template"]}},
    {"name": "update_media", "fn": t_update_media,
     "description": "Dosyalara etiket ekler/çıkarır, puan (0-5) ya da favori ayarlar. Dosyaları taşımaz veya silmez.",
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "file_ids": FILE_IDS,
         "add_tags": {"type": "array", "items": {"type": "string"}}, "remove_tags": {"type": "array", "items": {"type": "string"}},
         "rating": {"type": "integer", "minimum": 0, "maximum": 5}, "favorite": {"type": "boolean"}}, "required": ["file_ids"]}},
    {"name": "create_plan", "fn": t_create_plan,
     "description": ("Dosya sistemini değiştiren bir plan oluşturur; hiçbir dosyayı hemen değiştirmez. "
                     "kind=trash: file_ids çöpe (geri alınabilir). kind=move: file_ids destination_folder'a. "
                     "kind=organize: template ile (scope/folder/base isteğe bağlı). kind=dedup: tekrarların silinmesi önerilenleri çöpe. "
                     "Plan kullanıcı onayı bekler; kullanıcıya planın ne yapacağını kısaca anlat."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP,
         "kind": {"type": "string", "enum": ["trash", "move", "organize", "dedup"]},
         "file_ids": FILE_IDS,
         "destination_folder": {"type": "string", "description": "move için repo köküne göre klasör, ör. 'Bodrum 2024'"},
         "template": {"type": "string"}, "scope": {"type": "string", "enum": ["tracked", "untracked", "all"]},
         "folder": {"type": "string"}, "base": {"type": "string"},
         "dup_kinds": {"type": "array", "items": {"type": "string", "enum": ["exact", "near", "video"]}},
         "title": {"type": "string", "description": "Kullanıcının göreceği kısa başlık"}}, "required": ["kind"]}},
    {"name": "apply_plan", "fn": t_apply_plan,
     "description": "Bekleyen bir planı uygular. Kullanıcı onayı zorunluysa uygulamaz ve bunu bildirir.",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP, "plan_id": {"type": "integer"}}, "required": ["plan_id"]}},
    {"name": "undo_plan", "fn": t_undo_plan,
     "description": "Uygulanmış bir planı geri alır (onay zorunluysa kullanıcıya bırakır).",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP, "plan_id": {"type": "integer"}}, "required": ["plan_id"]}},
    {"name": "list_plans", "fn": t_list_plans,
     "description": "Son planları (işlem geçmişi) durumlarıyla listeler.",
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "status": {"type": "string", "enum": ["pending", "applied", "undone", "rejected", "partial"]},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    {"name": "get_preview_image", "fn": t_get_preview_image,
     "description": "Bir dosyanın küçük önizleme görselini döndürür (yalnızca kullanıcı izin verdiyse).",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP, "file_id": {"type": "integer"}}, "required": ["file_id"]}},
    {"name": "list_albums", "fn": t_list_albums,
     "description": "Albümleri listeler (elle oluşturulan 'manual' ve süzgece dayalı 'smart' albümler, öğe sayılarıyla).",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "create_album", "fn": t_create_album,
     "description": ("Albüm oluşturur. kind=manual: file_ids eklenir. kind=smart: rules süzgecine uyan fotoğraflar kendiliğinden girer "
                     "(rules: year, place, person (ad), tag, camera, kind, date_from, date_to (YYYY-MM-DD), search). Dosyalara dokunmaz."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "name": {"type": "string"}, "kind": {"type": "string", "enum": ["manual", "smart"]},
         "file_ids": FILE_IDS, "rules": {"type": "object"}}, "required": ["name"]}},
    {"name": "add_to_album", "fn": t_add_to_album,
     "description": "Dosyaları elle oluşturulmuş bir albüme ekler (remove=true ile çıkarır). Dosyalara dokunmaz.",
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "album": {"type": "string", "description": "Albüm adı ya da kimliği"}, "file_ids": FILE_IDS,
         "remove": {"type": "boolean"}}, "required": ["album", "file_ids"]}},
    {"name": "list_memories", "fn": t_list_memories,
     "description": "Kendiliğinden çıkarılan anılar: geçmiş yıllarda bugün, geziler ve kalabalık günler (tarih aralıklarıyla). Fotoğrafları için search_media(date_from, date_to).",
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "cleanup_suggestions", "fn": t_cleanup_suggestions,
     "description": ("Silinebilecek öğe önerileri: ekran görüntüleri, kazara çekilmiş (karanlık/tek renk), bulanık, çok kısa videolar, "
                     "çok büyük videolar. Favori/puanlı/albümdekiler dahil edilmez. Silmez; silmek için create_plan kullan."),
     "schema": {"type": "object", "properties": {"repo": REPO_PROP}}},
    {"name": "fix_dates", "fn": t_fix_dates,
     "description": ("Çekim tarihini düzeltir: shift_days/shift_hours/shift_minutes ile kaydırır (eksi değer geri) ya da set_to (ISO) ile "
                     "hepsine aynı tarihi verir. revert=true önceki düzeltmeleri geri alır. Yalnız MediaMind kaydında; dosyalara yazmaz."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "file_ids": FILE_IDS, "shift_days": {"type": "integer"}, "shift_hours": {"type": "integer"},
         "shift_minutes": {"type": "integer"}, "set_to": {"type": "string", "description": "YYYY-MM-DDTHH:MM:SS"},
         "revert": {"type": "boolean"}}, "required": ["file_ids"]}},
    {"name": "set_location", "fn": t_set_location,
     "description": ("Dosyalara konum verir: place (arşivde zaten konumlu fotoğrafı olan bir yer adı, ör. 'Bodrum') ya da lat/lon. "
                     "Yalnız MediaMind kaydında; dosyalara yazmaz. fix_dates(revert=true) ile geri alınır."),
     "schema": {"type": "object", "properties": {
         "repo": REPO_PROP, "file_ids": FILE_IDS, "place": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}},
         "required": ["file_ids"]}},
]

TOOL_MAP = {t["name"]: t for t in TOOLS}


def run(ctx: ToolContext, name: str, args: dict) -> dict:
    """Aracı çalıştırır. Beklenen hatalar ToolError, izin kontrolü burada."""
    tool = TOOL_MAP.get(name)
    if tool is None:
        raise ToolError(f"Bilinmeyen araç: {name}")
    ctx.need("read")
    try:
        res = tool["fn"](ctx, args or {})
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"Geçersiz parametre: {e}")
    return res


def to_json(res: dict, max_chars: int = 60_000) -> str:
    s = json.dumps(_strip_private(res), ensure_ascii=False, default=str)
    if len(s) > max_chars:
        s = s[:max_chars] + '… [kısaltıldı; daha dar bir sorgu ya da limit/offset kullan]'
    return s
