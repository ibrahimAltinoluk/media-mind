"""Yerel HTTP API (arayüz ↔ çekirdek). Yalnızca 127.0.0.1 üzerinde, oturum token'ı ile çalışır."""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import __version__, analyze, dedup, library, organize, plans, registry, scan, tools
from .. import settings as settings_mod
from ..assistant import Assistant, AssistantError, list_models
from ..kinds import kind_of
from ..media import thumbs
from ..paths import MM_DIR
from ..repo import Repo, RepoError, RepoLockedError
from .jobs import JobManager

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("video/mp4", ".m4v")


class RepoPool:
    def __init__(self):
        self._repos: dict[str, Repo] = {}
        self._mx = threading.Lock()
        self.recluster_scheduler = None  # create_app bağlar: yüz gruplama arka plan işi

    def opened(self) -> list[Repo]:
        with self._mx:
            return list(self._repos.values())

    def get(self, repo_id: str) -> Repo:
        with self._mx:
            repo = self._repos.get(repo_id)
            if repo is not None and (repo.mm / "repo.toml").exists():
                return repo
            entry = registry.find(repo_id)
            if entry is None:
                raise HTTPException(404, "Repo bulunamadı")
            try:
                repo = Repo.open(entry.id)
            except RepoError as e:
                raise HTTPException(409, str(e))
            repo.lock_timeout = 15.0
            repo.recluster_scheduler = self.recluster_scheduler
            self._repos[repo_id] = repo
            plans.recover_interrupted(repo)  # önceki oturumda yarım kalan planlar
            return repo

    def drop(self, repo_id: str) -> None:
        with self._mx:
            self._repos.pop(repo_id, None)


SLOW_SECONDS = 2.0


def _log_slow(method: str, path: str, seconds: float, inflight: int) -> None:
    """Yavaş istekleri <MEDIAMIND_HOME>/logs/slow.log dosyasına yazar (takılma teşhisi için; dosya küçük tutulur)."""
    try:
        d = registry.home() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "slow.log"
        if f.exists() and f.stat().st_size > 512_000:
            f.replace(d / "slow.old.log")
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {seconds:6.2f}s {method} {path.split('?')[0]} (eşzamanlı {inflight})\n")
    except OSError:
        pass


def create_app(token: str | None = None, watch: bool = False) -> FastAPI:
    from contextlib import asynccontextmanager

    services: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app):
        w = services.get("watcher")
        if w:
            w.start()
        try:
            yield
        finally:
            if w:
                w.stop()
            h = services.get("devices")
            if h:
                h.stop()

    app = FastAPI(title="MediaMind", version=__version__, lifespan=lifespan)
    pool = RepoPool()
    jobs = JobManager()

    def schedule_recluster(repo: Repo, animal: bool) -> None:
        from ..ml import people as people_mod

        title = "Benzer hayvanlar eşleştiriliyor" if animal else "Benzer yüzler gruplanıyor"
        jobs.submit_coalesced("people_pets" if animal else "people", repo.id, title,
                              lambda pr: people_mod._recluster_for(repo, animal), lane="people")

    pool.recluster_scheduler = schedule_recluster
    image_queries: dict[str, Any] = {}  # sürüklenen görsellerin vektörleri (bellekte, son 20)

    @app.post("/repos/{repo_id}/search/image")
    async def search_image(repo_id: str, request: Request):
        """Kütüphane dışından bir görsel: gövde ham dosya baytları ya da {"path": ...} (Finder'dan bırakılan dosya)."""
        import hashlib

        from ..ml import content, siglip

        pool.get(repo_id)
        if not siglip.available():
            raise HTTPException(409, "Görselle arama için akıllı analiz modeli gerekli (Ayarlar › Akıllı analiz)")
        body = await request.body()
        name = "görsel"
        if request.headers.get("content-type", "").startswith("application/json"):
            path = Path(json.loads(body or b"{}").get("path", ""))
            if not path.is_file():
                raise HTTPException(400, "Dosya bulunamadı")
            body, name = path.read_bytes(), path.name
        try:
            vec = await run_in_threadpool(content.image_vector, body, content.effective_version(pool.get(repo_id)))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(415, f"Görsel açılamadı: {e}")
        key = hashlib.sha1(body).hexdigest()[:16]
        image_queries[key] = vec
        while len(image_queries) > 20:
            image_queries.pop(next(iter(image_queries)))
        return {"key": key, "name": name}
    thumb_locks: dict[str, threading.Lock] = {}
    thumb_mx = threading.Lock()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                       "http://localhost:1420", "http://127.0.0.1:1420"],
        allow_methods=["*"], allow_headers=["*"],
    )

    inflight = [0]

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if token and request.method != "OPTIONS" and request.url.path != "/health":
            given = request.headers.get("x-mm-token") or request.query_params.get("t")
            if given != token:
                return JSONResponse({"detail": "Yetkisiz"}, status_code=401)
        inflight[0] += 1
        t0 = time.monotonic()
        try:
            return await call_next(request)
        finally:
            inflight[0] -= 1
            dt = time.monotonic() - t0
            if dt > SLOW_SECONDS:
                _log_slow(request.method, request.url.path, dt, inflight[0])

    @app.exception_handler(RepoLockedError)
    async def locked(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(plans.PlanError)
    async def plan_err(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ genel
    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__}

    # ------------------------------------------------------------------ repolar
    def entry_dict(e: registry.RepoEntry) -> dict:
        d = asdict(e)
        d["online"] = e.online
        if e.online:
            try:
                c = scan.counts(pool.get(e.id))
                d["counts"] = asdict(c)
            except HTTPException:
                d["online"] = False
        return d

    @app.get("/repos")
    def repos_list():
        return [entry_dict(e) for e in registry.load()]

    @app.get("/probe")
    def probe(path: str):
        """Klasör seçildiğinde ön tarama: repo mu, kaç medya dosyası var."""
        p = Path(path).expanduser()
        if not p.is_dir():
            raise HTTPException(400, "Klasör bulunamadı")
        root = Repo.find_root(p)
        photos = videos = size = 0
        deadline = time.monotonic() + 4.0
        complete = True
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not d.endswith(".photoslibrary")]
            for f in filenames:
                k = kind_of(f)
                if k:
                    photos += k == "photo"
                    videos += k == "video"
                    try:
                        size += os.stat(os.path.join(dirpath, f)).st_size
                    except OSError:
                        pass
            if time.monotonic() > deadline:
                complete = False
                break
        return {
            "path": str(p.resolve()),
            "name": p.resolve().name,
            "is_repo": root is not None and root == p.resolve(),
            "inside_repo": str(root) if root is not None and root != p.resolve() else None,
            "photos": photos, "videos": videos, "bytes": size, "complete": complete,
        }

    @app.post("/repos")
    def repos_add(path: str = Body(...), name: str | None = Body(None)):
        p = Path(path).expanduser()
        try:
            if (p / MM_DIR / "repo.toml").exists():
                repo = Repo(p)
                registry.register(repo.id, repo.name, repo.root)
            else:
                repo = Repo.init(p, name)
        except RepoError as e:
            raise HTTPException(400, str(e))
        e = registry.find(repo.id)
        return entry_dict(e)

    @app.delete("/repos/{repo_id}")
    def repos_remove(repo_id: str):
        e = registry.unregister(repo_id)
        pool.drop(repo_id)
        if e is None:
            raise HTTPException(404, "Repo bulunamadı")
        return {"ok": True}

    @app.patch("/repos/{repo_id}")
    def repos_rename(repo_id: str, name: str = Body(..., embed=True)):
        repo = pool.get(repo_id)
        repo.config["name"] = name
        repo.save_config()
        registry.register(repo.id, name, repo.root)
        return entry_dict(registry.find(repo.id))

    @app.get("/repos/{repo_id}/summary")
    def summary(repo_id: str):
        repo = pool.get(repo_id)
        c = scan.counts(repo)
        conn = repo.db
        pending = conn.execute(
            "SELECT COUNT(*) FROM assets a WHERE EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND "
            "f.state='present' AND f.tracked=1) AND (a.analysis_state NOT LIKE '%\"thumbs\"%')"
        ).fetchone()[0]
        dup_files = conn.execute("SELECT COUNT(*) FROM dup_members WHERE role='delete'").fetchone()[0]
        trash = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM plan_items WHERE op='trash' AND status='done'").fetchone()
        pending_rows = conn.execute(
            "SELECT id, kind, title, source, summary, created_at FROM plans WHERE status='pending' ORDER BY id DESC LIMIT 20"
        ).fetchall()
        pending_plans = len(pending_rows)
        return {"repo": entry_dict(registry.find(repo.id)), "counts": asdict(c), "analysis_pending": pending,
                "dup_files": dup_files, "trash_files": trash[0], "trash_bytes": trash[1],
                "pending_plans": pending_plans, "busy": jobs.busy(repo.id), "config": repo.config,
                "pending_plan_list": [{**dict(r), "summary": json.loads(r["summary"] or "{}")} for r in pending_rows]}

    @app.patch("/repos/{repo_id}/config")
    def config_update(repo_id: str, patch: dict = Body(...)):
        repo = pool.get(repo_id)
        for section, values in patch.items():
            if section in ("dedup", "scan", "analysis", "trash") and isinstance(values, dict):
                repo.config.setdefault(section, {}).update(values)
        repo.save_config()
        return repo.config

    # ------------------------------------------------------------------ işler
    def submit(repo: Repo, type_: str, title: str, fn, lane: str = "main"):
        return jobs.submit(type_, repo.id, title, fn, lane).to_dict()

    def report_dict(r: scan.ScanReport, limit: int = 200) -> dict:
        return {
            "new": len(r.new), "moved": r.moved[:limit], "copies": r.copies[:limit], "modified": r.modified[:limit],
            "missing": r.missing[:limit], "reappeared": len(r.reappeared),
            "counts": {"new": len(r.new), "moved": len(r.moved), "copies": len(r.copies),
                       "modified": len(r.modified), "missing": len(r.missing)},
        }

    def resume_analysis(repo: Repo) -> None:
        """Yarım kalmış ya da bekleyen analiz varsa (ör. uygulama kapanmıştı) kaldığı yerden sürdür."""
        if jobs.active(repo.id, "analysis") or not analyze.pending_work(repo):
            return
        submit(repo, "analyze", "Analiz", lambda pr: analyze.analyze(repo, pr), lane="analysis")

    def start_scan(repo: Repo, auto: bool = False):
        def run(pr):
            days = int(repo.config.get("trash", {}).get("auto_days") or 0)
            if days > 0:
                try:
                    plans.empty_trash(repo, days)  # süresi dolan silinenler kalıcı silinir
                except RepoLockedError:
                    pass
            try:
                res = report_dict(scan.scan(repo, pr))
            except RepoLockedError:
                if not auto:
                    raise
                # Başka bir süreç (ör. MCP) kütüphaneyi kullanıyor; izleyici bir sonraki değişiklikte yeniden tarar
                return {"skipped": "busy", "counts": {}}
            resume_analysis(repo)
            return res

        return submit(repo, "scan", "Tarama (değişiklik algılandı)" if auto else "Tarama", run)

    @app.post("/repos/{repo_id}/scan")
    def scan_start(repo_id: str):
        return start_scan(pool.get(repo_id))

    def on_folder_change(repo_id: str) -> bool:
        """Canlı izleme: klasörde değişiklik oldu → artımlı tarama.

        Kütüphanede tarama, taşıma/silme gibi bir iş sürüyorsa False döner; izleyici değişiklikleri bekletip
        iş bitince yeniden dener (taşıma işinin kendi değişiklikleri zaten kayıtlıdır, tarama bir şey bulmaz).
        """
        try:
            repo = pool.get(repo_id)
        except HTTPException:
            return True
        if not repo.config["scan"].get("watch", True):
            return True
        if jobs.active(repo_id, "main"):
            return False
        start_scan(repo, auto=True)
        return True

    if watch and not os.environ.get("MEDIAMIND_NO_WATCH"):
        from ..watch import WatchService

        app.state.watcher = services["watcher"] = WatchService(on_folder_change)

    @app.post("/repos/{repo_id}/track")
    def track_start(repo_id: str, paths: list[str] | None = Body(None, embed=True), analyze_after: bool = Body(True, embed=True)):
        repo = pool.get(repo_id)

        def run(pr):
            n = analyze.track(repo, paths)
            res = analyze.analyze(repo, pr) if analyze_after else {}
            return {"tracked": n, **res}

        return submit(repo, "track", "Takibe alma ve analiz", run, lane="analysis")

    @app.post("/repos/{repo_id}/analyze")
    def analyze_start(repo_id: str, force: bool = Body(False, embed=True)):
        repo = pool.get(repo_id)
        return submit(repo, "analyze", "Analiz", lambda pr: analyze.analyze(repo, pr, force), lane="analysis")

    @app.post("/repos/{repo_id}/forget-missing")
    def forget_missing(repo_id: str):
        return {"forgotten": scan.forget_missing(pool.get(repo_id))}

    @app.get("/jobs")
    def jobs_list(repo: str | None = None):
        return [j.to_dict() for j in jobs.list(repo)]

    @app.post("/jobs/{job_id}/cancel")
    def job_cancel(job_id: int):
        j = jobs.cancel(job_id)
        if j is None:
            raise HTTPException(404, "İş bulunamadı")
        return j.to_dict()

    @app.get("/jobs/{job_id}")
    def job_get(job_id: int):
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "İş bulunamadı")
        return j.to_dict()

    # ------------------------------------------------------------------ medya listeleri
    ASSET_COLS = (
        "f.id AS file_id, f.rel_path, f.tracked, f.size, a.id AS asset_id, a.blake3, a.kind, a.mime, a.width, a.height, "
        "a.duration_ms, a.taken_at, a.taken_at_source, a.camera_model, a.rating, a.favorite, a.lat, a.lon, "
        "p.name AS place, p.admin1 AS region, p.country_code, "
        "(SELECT COUNT(*) FROM files f2 WHERE f2.asset_id=a.id AND f2.state='present') AS copies, "
        "EXISTS (SELECT 1 FROM dup_members dm WHERE dm.file_id=f.id) AS in_dup"
    )

    def _filters(q: str | None, kind: str | None, year: str | None, month: str | None, place: str | None,
                 camera: str | None, scope: str, favorite: bool, folder: str | None, tag: str | None = None,
                 region: str | None = None):
        where = ["f.state='present'"]
        params: list[Any] = []
        if scope == "tracked":
            where.append("f.tracked=1")
        elif scope == "untracked":
            where.append("f.tracked=0")
        if kind in ("photo", "video"):
            where.append("a.kind=?")
            params.append(kind)
        if year:
            where.append("substr(a.taken_at,1,4)=?")
            params.append(year)
        if month:
            where.append("substr(a.taken_at,6,2)=?")
            params.append(month.zfill(2))
        if place:
            where.append("p.name=?")
            params.append(place)
        if camera:
            where.append("a.camera_model=?")
            params.append(camera)
        if favorite:
            where.append("a.favorite=1")
        if folder and folder.strip("/"):
            pre = folder.strip("/") + "/"
            where.append("substr(f.rel_path, 1, ?)=?")  # LIKE büyük/küçük harfe duyarsız; tam önek karşılaştır
            params += [len(pre), pre]
        for t1 in [t for t in (tag or "").split("|") if t.strip()]:
            # Birden çok etiket "|" ile: hepsini taşıyanlar (VE)
            where.append("a.id IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE t.name=?)")
            params.append(t1)
        if region:
            where.append("p.admin1=?")
            params.append(region)
        if q:
            from ..media.geo import fold

            for word in q.split():
                # Dosya adı, yer (Türkçe karakter farkı gözetmeden), kamera, etiket, konuşma dökümü, fotoğraftaki yazı
                where.append("(f.rel_path LIKE ? OR p.name_fold LIKE ? OR p.admin1_fold LIKE ? OR a.camera_model LIKE ? OR EXISTS "
                             "(SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name LIKE ?) "
                             "OR EXISTS (SELECT 1 FROM transcripts tr WHERE tr.asset_id=a.id AND tr.text LIKE ?) "
                             "OR a.id IN (SELECT asset_id FROM asset_text WHERE fold LIKE ?))")
                like, flike = f"%{word}%", f"%{fold(word)}%"
                params += [like, flike, flike, like, like, like, flike]
        return " AND ".join(where), params

    @app.get("/repos/{repo_id}/assets")
    def assets(repo_id: str, q: str | None = None, kind: str | None = None, year: str | None = None,
               month: str | None = None, place: str | None = None, camera: str | None = None,
               scope: str = "tracked", favorite: bool = False, folder: str | None = None, tag: str | None = None,
               person: str | None = None, semantic: str | None = None, search: str | None = None, region: str | None = None,
               direct: bool = False, album: int | None = None, date_from: str | None = None, date_to: str | None = None,
               sort: str = "taken_desc", offset: int = 0, limit: int = Query(500, le=5000), layout: str | None = None,
               similar: str | None = None):
        repo = pool.get(repo_id)
        extra, extra_p = "", []
        if album:
            try:
                alb = library.album_get(repo, album)
            except ValueError as e:
                raise HTTPException(404, str(e))
            if alb["kind"] == "smart":
                # Akıllı albüm: kayıtlı süzgeç (açıkça verilen parametreler önceliklidir)
                r = json.loads(alb["rules"] or "{}")
                q, kind, year, place = q or r.get("q") or None, kind or r.get("kind") or None, year or r.get("year") or None, place or r.get("place") or None
                camera, tag = camera or r.get("camera") or None, tag or r.get("tag") or None
                person = person or (str(r["person"]) if r.get("person") else None)
                region, month = region or r.get("region") or None, month or r.get("month") or None
                favorite = favorite or bool(r.get("favorite"))
                date_from, date_to = date_from or r.get("date_from") or None, date_to or r.get("date_to") or None
                if not date_from and str(r.get("last_days") or "").isdigit():
                    # "Son N gün": her açılışta bugüne göre hesaplanır
                    date_from = (datetime.now() - timedelta(days=int(r["last_days"]))).date().isoformat()
                search = search or r.get("search") or None
            else:
                extra += " AND a.id IN (SELECT asset_id FROM album_items WHERE album_id=?)"
                extra_p.append(album)
        if date_from:
            extra += " AND a.taken_at >= ?"
            extra_p.append(date_from)
        if date_to:
            extra += " AND a.taken_at <= ?"
            extra_p.append(date_to + "T23:59:59" if len(date_to) == 10 else date_to)
        where, params = _filters(q, kind, year, month, place, camera, scope, favorite, folder, tag, region)
        where += extra
        params += extra_p
        if direct:
            # Yalnız klasörün kendi dosyaları (alt klasörler hariç); folder boşsa kök
            pre = (folder or "").strip("/")
            where += " AND instr(substr(f.rel_path, ?), '/')=0"
            params.append(len(pre) + 2 if pre else 1)
        for pid in [x for x in str(person or "").split(",") if x.strip().isdigit()]:
            # Birden çok kişi "," ile: hepsinin birlikte göründüğü öğeler (VE)
            where += " AND a.id IN (SELECT fc.asset_id FROM faces fc WHERE fc.person_id=?)"
            params.append(int(pid))
        if similar:
            # Görselle arama: "a:<asset_id>" (kütüphanedeki bir fotoğraf) ya da "q:<anahtar>" (sürüklenen dosya)
            from ..ml import content

            kind_, _, ref = similar.partition(":")
            qv, exclude = None, None
            if kind_ == "a" and ref.isdigit():
                qv, exclude = content.asset_vector(repo, int(ref)), None
            elif kind_ == "q":
                qv = image_queries.get(ref)
            if qv is None:
                raise HTTPException(404, "Benzerlik araması için görsel bulunamadı (görsel analiz yapılmamış olabilir)")
            base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE {where}"
            cand = {r[0] for r in repo.db.execute(f"SELECT DISTINCT a.id {base}", params)}
            scores = dict(content.vector_rank(repo, qv, cand, limit=2000, exclude=exclude))
            rows = [dict(r) for r in repo.db.execute(f"SELECT {ASSET_COLS} {base}", params) if r["asset_id"] in scores]
            rows.sort(key=lambda r: -scores[r["asset_id"]])
            for r in rows:
                r["match"] = "visual"
                r["match_score"] = scores[r["asset_id"]]
            return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit], "search": True}
        if search and search.strip():
            # Tek arama kutusu: metin eşleşmeleri (dosya adı, yer, kamera, etiket, döküm) üstte,
            # ardından görsel içerik benzerliği (model varsa). Diğer filtrelerle birleşir.
            from ..ml import content, siglip

            base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE {where}"
            t_where, t_params = _filters(search, kind, year, month, place, camera, scope, favorite, folder, tag, region)
            t_where += extra
            t_params += extra_p
            for pid in [x for x in str(person or "").split(",") if x.strip().isdigit()]:
                t_where += " AND a.id IN (SELECT fc.asset_id FROM faces fc WHERE fc.person_id=?)"
                t_params.append(int(pid))
            text_rows = [dict(r) for r in repo.db.execute(
                f"SELECT {ASSET_COLS} FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
                f"WHERE {t_where} ORDER BY a.taken_at DESC", t_params)]
            seen = {r["file_id"] for r in text_rows}
            for r in text_rows:
                r["match"] = "text"
            sem_rows: list[dict] = []
            if siglip.available():
                cand = {r[0] for r in repo.db.execute(f"SELECT DISTINCT a.id {base}", params)}
                scores = dict(content.semantic_rank(repo, search.strip(), cand, limit=3000))
                if scores:
                    sem_rows = [dict(r) for r in repo.db.execute(f"SELECT {ASSET_COLS} {base}", params)
                                if r["asset_id"] in scores and r["file_id"] not in seen]
                    sem_rows.sort(key=lambda r: -scores[r["asset_id"]])
                    for r in sem_rows:
                        r["match"] = "visual"
                        r["match_score"] = scores[r["asset_id"]]
            rows = text_rows + sem_rows
            return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit], "search": True,
                    "text_hits": len(text_rows)}
        if semantic:
            from ..ml import content

            base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE {where}"
            cand = {r[0] for r in repo.db.execute(f"SELECT DISTINCT a.id {base}", params)}
            scores = dict(content.semantic_rank(repo, semantic, cand, limit=3000))
            rows = [dict(r) for r in repo.db.execute(f"SELECT {ASSET_COLS} {base}", params) if r["asset_id"] in scores]
            rows.sort(key=lambda r: -scores[r["asset_id"]])
            for r in rows:
                r["match_score"] = scores[r["asset_id"]]
            return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit], "semantic": True}
        order = {"taken_desc": "a.taken_at IS NULL, a.taken_at DESC, f.rel_path",
                 "taken_asc": "a.taken_at IS NULL, a.taken_at ASC, f.rel_path",
                 "path": "f.rel_path", "size_desc": "f.size DESC"}.get(sort, "a.taken_at DESC")
        base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE {where}"
        if layout:
            # Izgara iskeleti: tüm sonucun yalnız kimlikleri ve grup dizileri (ay/klasör) — ayrıntılar görünür
            # oldukça sayfa sayfa yüklenir. 100 bin öğede bile birkaç yüz KB.
            ids: list[int] = []
            runs: list[dict] = []
            places: dict[str, int] = {}

            def close() -> None:
                if runs:
                    top = sorted(places.items(), key=lambda x: -x[1])[:2]
                    runs[-1]["p"] = ", ".join(k for k, _ in top)

            for r in repo.db.execute(f"SELECT f.id, a.taken_at, f.rel_path, p.name AS place, p.admin1 AS region {base} ORDER BY {order}", params):
                if layout == "month":
                    k = r["taken_at"][:7] if r["taken_at"] else "?"
                elif layout == "folder":
                    k = r["rel_path"].rsplit("/", 1)[0] if "/" in r["rel_path"] else ""
                else:
                    k = "_"
                if not runs or runs[-1]["k"] != k:
                    close()
                    runs.append({"k": k, "n": 0})
                    places = {}
                runs[-1]["n"] += 1
                if r["place"]:
                    pl = r["place"] if not r["region"] or r["region"] == r["place"] else f"{r['place']}, {r['region']}"
                    places[pl] = places.get(pl, 0) + 1
                ids.append(r["id"])
            close()
            return {"total": len(ids), "ids": ids, "runs": runs}
        total = repo.db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        rows = repo.db.execute(f"SELECT {ASSET_COLS} {base} ORDER BY {order} LIMIT ? OFFSET ?",
                               [*params, limit, offset]).fetchall()
        return {"total": total, "offset": offset, "items": [dict(r) for r in rows]}

    @app.get("/repos/{repo_id}/folders")
    def folders(repo_id: str, path: str = "", scope: str = "all"):
        """Bir klasörün doğrudan alt klasörleri (içlerindeki medya sayısı, boyut ve kapak önizlemeleriyle)."""
        repo = pool.get(repo_id)
        pre = path.strip("/")
        prefix = f"{pre}/" if pre else ""
        cond = {"tracked": " AND f.tracked=1", "untracked": " AND f.tracked=0"}.get(scope, "")
        rows = repo.db.execute(
            "SELECT f.rel_path, f.size, f.tracked, a.blake3, a.kind, a.taken_at FROM files f JOIN assets a ON a.id=f.asset_id "
            f"WHERE f.state='present'{cond} AND substr(f.rel_path, 1, ?)=? ORDER BY a.taken_at IS NULL, a.taken_at DESC",
            (len(prefix), prefix)).fetchall()
        subs: dict[str, dict] = {}
        files = 0
        for r in rows:
            rest = r["rel_path"][len(prefix):]
            if "/" not in rest:
                files += 1
                continue
            name = rest.split("/", 1)[0]
            d = subs.get(name)
            if d is None:
                d = subs[name] = {"name": name, "path": prefix + name, "count": 0, "bytes": 0, "new": 0, "covers": [], "latest": None}
            d["count"] += 1
            d["bytes"] += r["size"] or 0
            d["new"] += 0 if r["tracked"] else 1
            if len(d["covers"]) < 4 and r["kind"] == "photo" and r["blake3"] not in d["covers"]:
                d["covers"].append(r["blake3"])
            if r["taken_at"] and (d["latest"] is None or r["taken_at"] > d["latest"]):
                d["latest"] = r["taken_at"]
        for d in subs.values():
            if not d["covers"]:
                d["covers"] = [r["blake3"] for r in rows if r["rel_path"].startswith(d["path"] + "/")][:1]
        out = sorted(subs.values(), key=lambda d: d["name"].casefold())
        return {"path": pre, "folders": out, "files": files, "total": len(rows)}

    @app.get("/repos/{repo_id}/facets")
    def facets(repo_id: str, scope: str = "tracked"):
        repo = pool.get(repo_id)
        tr = {"tracked": "AND f.tracked=1", "untracked": "AND f.tracked=0"}.get(scope, "")
        base = f"FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id WHERE f.state='present' {tr}"
        c = repo.db
        return {
            "years": [dict(r) for r in c.execute(f"SELECT substr(a.taken_at,1,4) AS year, COUNT(*) AS n {base} AND a.taken_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC")],
            "places": [dict(r) for r in c.execute(f"SELECT p.name AS place, p.admin1 AS region, COUNT(*) AS n {base} AND p.name IS NOT NULL GROUP BY 1,2 ORDER BY 3 DESC LIMIT 200")],
            "cameras": [dict(r) for r in c.execute(f"SELECT a.camera_model AS camera, COUNT(*) AS n {base} AND a.camera_model IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 100")],
            "kinds": [dict(r) for r in c.execute(f"SELECT a.kind, COUNT(*) AS n {base} GROUP BY 1")],
            "tags": [dict(r) for r in c.execute("SELECT t.name AS tag, COUNT(*) AS n FROM asset_tags at JOIN tags t ON t.id=at.tag_id GROUP BY 1 ORDER BY 2 DESC LIMIT 200")],
            "people": [dict(r) for r in c.execute("SELECT pr.id, pr.name, COUNT(DISTINCT fc.asset_id) AS n FROM persons pr JOIN faces fc ON fc.person_id=pr.id GROUP BY pr.id ORDER BY 3 DESC")],
        }

    @app.get("/repos/{repo_id}/assets/{asset_id}")
    def asset_detail(repo_id: str, asset_id: int):
        repo = pool.get(repo_id)
        a = repo.db.execute(
            "SELECT a.*, p.name AS place, p.admin1 AS region, p.admin2 AS district, p.country_code "
            "FROM assets a LEFT JOIN places p ON p.id=a.place_id WHERE a.id=?", (asset_id,)
        ).fetchone()
        if a is None:
            raise HTTPException(404, "Öğe bulunamadı")
        files = [dict(r) for r in repo.db.execute(
            "SELECT id, rel_path, size, state, tracked, first_seen FROM files WHERE asset_id=? ORDER BY id", (asset_id,))]
        d = dict(a)
        d["analysis_state"] = json.loads(d["analysis_state"] or "{}")
        dup = repo.db.execute(
            "SELECT g.id, g.kind, COUNT(*) AS n FROM dup_members m JOIN dup_groups g ON g.id=m.group_id "
            "WHERE m.group_id IN (SELECT group_id FROM dup_members dm JOIN files f ON f.id=dm.file_id WHERE f.asset_id=?) "
            "GROUP BY g.id", (asset_id,)).fetchone()
        d["files"] = files
        d["dup_group"] = dict(dup) if dup else None
        from ..ml import content as content_mod
        from ..ml import people as people_mod

        all_tags = content_mod.model_tags_json(repo.db, asset_id)
        d["tags"] = [t["name"] for t in all_tags if t["source"] == "user"]
        d["model_tags"] = [{"name": t["name"], "kind": t["kind"], "confidence": t["confidence"]} for t in all_tags if t["source"] == "model"]
        d["faces"] = people_mod.faces_of_asset(repo, asset_id)
        t = repo.db.execute("SELECT text FROM asset_text WHERE asset_id=?", (asset_id,)).fetchone()
        d["text"] = t["text"] if t else None  # fotoğraftaki yazı (OCR)
        return d

    @app.get("/repos/{repo_id}/assets/{asset_id}/similar")
    def asset_similar(repo_id: str, asset_id: int, limit: int = 24):
        from ..ml import content as content_mod

        repo = pool.get(repo_id)
        sims = dict(content_mod.similar_assets(repo, asset_id, limit))
        if not sims:
            return {"items": []}
        rows = repo.db.execute(
            f"SELECT {ASSET_COLS} FROM files f JOIN assets a ON a.id=f.asset_id LEFT JOIN places p ON p.id=a.place_id "
            f"WHERE f.state='present' AND a.id IN ({','.join('?' * len(sims))}) GROUP BY a.id", list(sims)).fetchall()
        items = sorted((dict(r) for r in rows), key=lambda r: -sims[r["asset_id"]])
        return {"items": items}

    @app.post("/repos/{repo_id}/assets/{asset_id}/tags")
    def asset_tags(repo_id: str, asset_id: int, add: list[str] = Body(default_factory=list), remove: list[str] = Body(default_factory=list)):
        repo = pool.get(repo_id)
        tools.change_tags(repo, [asset_id], add, remove)
        return {"tags": [r["name"] for r in repo.db.execute(
            "SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=? AND at.source='user' ORDER BY t.name",
            (asset_id,))]}

    @app.patch("/repos/{repo_id}/assets/{asset_id}")
    def asset_update(repo_id: str, asset_id: int, rating: int | None = Body(None), favorite: bool | None = Body(None)):
        repo = pool.get(repo_id)
        with repo.db as c:
            if rating is not None:
                c.execute("UPDATE assets SET rating=? WHERE id=?", (max(0, min(5, rating)), asset_id))
            if favorite is not None:
                c.execute("UPDATE assets SET favorite=? WHERE id=?", (int(favorite), asset_id))
        return {"ok": True}

    # ------------------------------------------------------------------ medya dosyaları
    def _asset_source(repo: Repo, blake: str):
        blake = library.resolve_blake(repo, blake)
        row = repo.db.execute(
            "SELECT a.id, a.kind, a.duration_ms, f.fs_path FROM assets a JOIN files f ON f.asset_id=a.id "
            "WHERE a.blake3=? AND f.state IN ('present','trashed') "
            "ORDER BY f.state='present' DESC, f.tracked DESC, f.id LIMIT 1", (blake,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Dosya bulunamadı")
        return row

    @app.get("/repos/{repo_id}/thumb/{blake}/{size}")
    def thumb(repo_id: str, blake: str, size: str):
        if size not in thumbs.SIZES:
            raise HTTPException(400, "Geçersiz boyut")
        repo = pool.get(repo_id)
        target = repo.thumb_path(blake, size)
        if not target.exists():
            blake = library.resolve_blake(repo, blake)  # dosyaya konum yazıldıysa eski adres
            target = repo.thumb_path(blake, size)
        if not target.exists():
            with thumb_mx:
                lk = thumb_locks.setdefault(blake, threading.Lock())
            with lk:
                if not target.exists():
                    src = _asset_source(repo, blake)
                    try:
                        sizes = (size,) if size == "l" else ("s", "m")
                        thumbs.render(repo.root / src["fs_path"], src["kind"],
                                      lambda s: repo.thumb_path(blake, s), src["duration_ms"], sizes=sizes)
                    except Exception as e:  # noqa: BLE001
                        raise HTTPException(415, f"Önizleme üretilemedi: {e}")
        return FileResponse(target, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/repos/{repo_id}/original/{blake}")
    def original(repo_id: str, blake: str):
        repo = pool.get(repo_id)
        src = _asset_source(repo, blake)
        path = repo.root / src["fs_path"]
        mt = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=mt, headers={"Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------------------------ temizlik, anılar, albümler, düzeltme, dışa aktarma, harita
    def _bad(e: Exception):
        return HTTPException(400, str(e))

    @app.get("/repos/{repo_id}/cleanup")
    def cleanup_list(repo_id: str):
        return {"categories": library.cleanup(pool.get(repo_id))}

    @app.post("/repos/{repo_id}/cleanup/keep")
    def cleanup_keep(repo_id: str, category: str = Body(...), asset_ids: list[int] = Body(...)):
        return {"kept": library.cleanup_keep(pool.get(repo_id), category, asset_ids)}

    @app.get("/repos/{repo_id}/memories")
    def memories_list(repo_id: str):
        return {"memories": library.memories(pool.get(repo_id))}

    @app.get("/repos/{repo_id}/albums")
    def albums_list(repo_id: str):
        out = library.albums(pool.get(repo_id))
        for a in out:
            # Akıllı albüm: sayı ve kapak süzgeçle hesaplanır (anlamsal arama içerenler yavaş olduğundan sayılmaz)
            if a["kind"] == "smart" and not (a["rules"] or {}).get("search"):
                try:
                    r = assets(repo_id, album=a["id"], limit=1, offset=0)
                    a["count"] = r["total"]
                    a["cover"] = r["items"][0]["blake3"] if r["items"] else None
                except Exception:  # noqa: BLE001 — bozuk kural listeyi düşürmesin
                    pass
        return {"albums": out}

    @app.post("/repos/{repo_id}/albums")
    def album_new(repo_id: str, name: str = Body(...), kind: str = Body("manual"), rules: dict | None = Body(None),
                  asset_ids: list[int] | None = Body(None)):
        repo = pool.get(repo_id)
        try:
            aid = library.album_create(repo, name, kind, rules)
            if asset_ids and kind == "manual":
                library.album_add(repo, aid, asset_ids)
        except ValueError as e:
            raise _bad(e)
        return {"id": aid}

    @app.patch("/repos/{repo_id}/albums/{album_id}")
    def album_patch(repo_id: str, album_id: int, name: str | None = Body(None), rules: dict | None = Body(None)):
        try:
            library.album_update(pool.get(repo_id), album_id, name, rules)
        except ValueError as e:
            raise _bad(e)
        return {"ok": True}

    @app.delete("/repos/{repo_id}/albums/{album_id}")
    def album_del(repo_id: str, album_id: int):
        library.album_delete(pool.get(repo_id), album_id)
        return {"ok": True}

    @app.post("/repos/{repo_id}/albums/{album_id}/items")
    def album_items_add(repo_id: str, album_id: int, asset_ids: list[int] = Body(..., embed=True)):
        try:
            return {"added": library.album_add(pool.get(repo_id), album_id, asset_ids)}
        except ValueError as e:
            raise _bad(e)

    @app.post("/repos/{repo_id}/albums/{album_id}/remove")
    def album_items_remove(repo_id: str, album_id: int, asset_ids: list[int] = Body(..., embed=True)):
        return {"removed": library.album_remove(pool.get(repo_id), album_id, asset_ids)}

    @app.post("/repos/{repo_id}/edits/date")
    def edit_date(repo_id: str, asset_ids: list[int] = Body(...), shift_seconds: int | None = Body(None), value: str | None = Body(None)):
        try:
            return {"updated": library.set_dates(pool.get(repo_id), asset_ids, shift_seconds, value)}
        except ValueError as e:
            raise _bad(e)

    @app.post("/repos/{repo_id}/geo/suggest")
    def geo_suggest(repo_id: str, gpx: str | None = Body(None, embed=True)):
        from .. import geoinfer

        try:
            return geoinfer.suggestions(pool.get(repo_id), gpx)
        except ValueError as e:
            raise _bad(e)

    @app.post("/repos/{repo_id}/geo/apply")
    def geo_apply(repo_id: str, items: list[dict] = Body(..., embed=True)):
        repo = pool.get(repo_id)
        try:
            rows = [(int(i["asset_id"]), float(i["lat"]), float(i["lon"])) for i in items]
            n = library.set_locations(repo, rows)
        except (KeyError, TypeError, ValueError) as e:
            raise _bad(ValueError(str(e)))
        return {"updated": n, "job": write_location_job(repo, [r[0] for r in rows])}

    @app.post("/repos/{repo_id}/geo/skip")
    def geo_skip(repo_id: str, asset_ids: list[int] = Body(..., embed=True)):
        from .. import geoinfer

        return {"skipped": geoinfer.skip(pool.get(repo_id), asset_ids)}

    @app.delete("/repos/{repo_id}/geo/skip")
    def geo_skip_clear(repo_id: str):
        from .. import geoinfer

        return {"cleared": geoinfer.clear_skips(pool.get(repo_id))}

    def write_location_job(repo: Repo, ids: list[int]) -> dict | None:
        """Ayar açıksa konumu özgün dosyalara yazan arka plan işi (dosya başına kısa kilit)."""
        if not ids or not settings_mod.load()["files"].get("write_location", True):
            return None
        title = "Konum dosyaya yazılıyor" if len(ids) == 1 else f"Konum {len(ids)} dosyaya yazılıyor"
        return submit(repo, "write_meta", title, lambda pr: library.write_location_to_files(repo, ids, pr), lane="media")

    @app.post("/repos/{repo_id}/edits/location")
    def edit_location(repo_id: str, asset_ids: list[int] = Body(...), lat: float = Body(...), lon: float = Body(...)):
        repo = pool.get(repo_id)
        try:
            r = library.set_location(repo, asset_ids, lat, lon)
        except ValueError as e:
            raise _bad(e)
        r["job"] = write_location_job(repo, asset_ids)
        return r

    @app.post("/repos/{repo_id}/edits/revert")
    def edit_revert(repo_id: str, asset_ids: list[int] = Body(..., embed=True)):
        repo = pool.get(repo_id)
        loc = library.location_edit_ids(repo, asset_ids)
        n = library.revert_edits(repo, asset_ids)
        return {"reverted": n, "job": write_location_job(repo, loc)}

    @app.post("/repos/{repo_id}/export")
    def export_start(repo_id: str, file_ids: list[int] = Body(...), dest: str = Body(...), size: str = Body("original"),
                     strip_location: bool = Body(False)):
        repo = pool.get(repo_id)
        if size not in library.EXPORT_SIZES:
            raise HTTPException(400, "Geçersiz boyut")
        if not Path(dest).expanduser().is_absolute():
            raise HTTPException(400, "Hedef klasör tam yol olmalı")
        return submit(repo, "export", f"{len(file_ids)} öğe dışa aktarılıyor",
                      lambda pr: library.export(repo, file_ids, Path(dest), size, strip_location, pr), lane="media")

    @app.post("/repos/{repo_id}/share/prepare")
    def share_prepare(repo_id: str, file_ids: list[int] = Body(...), size: str = Body("original")):
        """Paylaşım menüsüne verilecek dosya yolları: asılları ya da küçültülmüş, konumu silinmiş kopyalar
        (geçici klasörde; bir günden eski paylaşım kopyaları temizlenir)."""
        import shutil
        import time as _t

        repo = pool.get(repo_id)
        q = ",".join("?" * len(file_ids)) or "NULL"
        rows = repo.db.execute(f"SELECT id, fs_path FROM files WHERE state='present' AND id IN ({q})", file_ids).fetchall()
        if size == "original":
            return {"paths": [str(repo.root / r["fs_path"]) for r in rows]}
        if size not in library.EXPORT_SIZES:
            raise HTTPException(400, "Geçersiz boyut")
        root = Path.home() / "Library" / "Caches" / "MediaMind" / "share"
        root.mkdir(parents=True, exist_ok=True)
        for old in root.iterdir():
            if old.is_dir() and _t.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
        dest = root / f"{int(_t.time() * 1000)}"
        res = library.export(repo, [r["id"] for r in rows], dest, size, strip_location=True)
        return {"paths": [str(dest / name) for name in res["files"]]}

    @app.post("/repos/{repo_id}/compress")
    def compress_start(repo_id: str, file_ids: list[int] = Body(..., embed=True)):
        repo = pool.get(repo_id)
        return submit(repo, "compress", f"{len(file_ids)} video küçültülüyor",
                      lambda pr: library.compress_videos(repo, file_ids, pr, after_replace=lambda: resume_analysis(repo)),
                      lane="media")

    @app.get("/geo/search")
    def geo_search(q: str, online: bool = False, lat: float | None = None, lon: float | None = None):
        """Konum atarken yer/adres arama: çevrimdışı şehir listesi ya da (online=1) OpenStreetMap adres araması."""
        from ..media import geo as geo_mod

        q = q.strip()[:200]
        if len(q) < 2:
            return {"results": []}
        near = (lat, lon) if lat is not None and lon is not None else None
        return {"results": geo_mod.search_online(q, near=near) if online else geo_mod.search(q)}

    @app.get("/repos/{repo_id}/map")
    def map_list(repo_id: str):
        return {"points": library.map_points(pool.get(repo_id))}

    # ------------------------------------------------------------------ telefon / hafıza kartı içe aktarma
    def device_helper():
        from ..devices import DeviceHelper

        if "devices" not in services:
            services["devices"] = DeviceHelper()
        return services["devices"]

    def find_source(device: str) -> dict:
        from ..devices import card_volumes

        for v in card_volumes([Path(e.last_path) for e in registry.load()]):
            if v["id"] == device:
                return v
        if device.startswith("vol:"):
            raise HTTPException(404, "Kart artık bağlı değil")
        for d in device_helper().request("list")["devices"]:
            if d["id"] == device:
                return {**d, "kind": "device"}
        raise HTTPException(404, "Aygıt artık bağlı değil")

    @app.get("/devices")
    def devices_list():
        from ..devices import card_volumes

        out = []
        try:
            # Disk olarak bağlanan kartlar/makineler dosya sisteminden okunur (aşağıda); ImageCaptureCore kopyası gizlenir
            out += [{**d, "kind": "device"} for d in device_helper().request("list", timeout=10)["devices"]
                    if "MassStorage" not in (d.get("transport") or "")]
        except Exception:  # noqa: BLE001 - ImageCaptureCore yoksa ya da yanıt vermezse yalnız kartlar
            pass
        out += card_volumes([Path(e.last_path) for e in registry.load()])
        return {"devices": out}

    @app.get("/devices/items")
    def device_items(device: str, repo: str | None = None):
        from .. import importer
        from ..devices import card_items

        src = find_source(device)
        if src["kind"] == "card":
            items = card_items(Path(src["path"]))
        else:
            if src.get("locked"):
                raise HTTPException(423, "Telefonun kilidini aç")
            if not src.get("ready"):
                raise HTTPException(409, "Aygıt hazırlanıyor, birkaç saniye sonra yeniden dene")
            items = device_helper().request("items", device=device, timeout=180)["items"]
        if repo:
            importer.mark_new(pool.get(repo), device, items)
        items.sort(key=lambda i: i.get("created") or "", reverse=True)
        return {"device": {k: src.get(k) for k in ("id", "name", "product", "kind")}, "items": items}

    @app.get("/devices/thumb")
    def device_thumb(device: str, key: str):
        import base64
        import io

        src = find_source(device)
        if src["kind"] == "card":
            p = (Path(src["path"]) / key).resolve()
            if Path(src["path"]).resolve() not in p.parents:
                raise HTTPException(400, "Geçersiz yol")
            try:
                im = thumbs.open_visual(p, kind_of(p.name) or "photo")
                im.thumbnail((320, 320))
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=80)
                data = buf.getvalue()
            except Exception:  # noqa: BLE001
                raise HTTPException(415, "Önizleme yok")
        else:
            from .. import devices as devmod

            data = devmod.phone_thumb(device_helper(), device, key)
            if not data:
                raise HTTPException(404, "Önizleme yok")
        from fastapi.responses import Response

        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/repos/{repo_id}/import")
    def import_start(repo_id: str, device: str = Body(...), keys: list[str] = Body(...), template: str | None = Body(None),
                     delete_after: bool = Body(False)):
        from .. import importer

        repo = pool.get(repo_id)
        src = find_source(device)
        tpl = template or importer.DEFAULT_TEMPLATE
        try:
            importer.render(tpl, "2024-01-01T00:00:00", "x.jpg", "Aygıt")
        except ValueError as e:
            raise HTTPException(400, f"Geçersiz şablon: {e}")

        def run(pr):
            res = importer.import_items(repo, src, keys, tpl, delete_after, device_helper(), pr)
            resume_analysis(repo)
            return res

        return submit(repo, "import", f"{src.get('name') or 'Aygıt'}: {len(keys)} öğe içe aktarılıyor", run, lane="media")

    WEB_SAFE = {"image/jpeg", "image/png", "image/webp", "image/gif"}

    @app.get("/repos/{repo_id}/display/{blake}")
    def display(repo_id: str, blake: str):
        """Yakınlaştırma için tam çözünürlüklü, DOĞRU YÖNDE görüntü.

        Yönü düz (EXIF Orientation 1/yok) web biçimleri olduğu gibi gönderilir; döndürülmüş, HEIC/RAW gibi
        dosyalar sunucuda çevrilip JPEG olarak önbelleğe alınır (WebKit'in EXIF yönünü yorumlamasına güvenilmez).
        """
        repo = pool.get(repo_id)
        src = _asset_source(repo, blake)
        meta = repo.db.execute("SELECT mime, orientation FROM assets WHERE blake3=?", (blake,)).fetchone()
        path = repo.root / src["fs_path"]
        if src["kind"] == "photo" and meta and meta["mime"] in WEB_SAFE and (meta["orientation"] or 1) == 1:
            return FileResponse(path, media_type=meta["mime"], headers={"Cache-Control": "private, max-age=3600"})
        cache = repo.mm / "cache" / "full"
        target = cache / f"{blake}.jpg"
        if not target.exists():
            with thumb_mx:
                lk = thumb_locks.setdefault(f"full:{blake}", threading.Lock())
            with lk:
                if not target.exists():
                    try:
                        im = thumbs.open_visual(path, src["kind"], src["duration_ms"], full=True)
                        if im.mode != "RGB":
                            im = im.convert("RGB")
                        cache.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_suffix(".tmp")
                        im.save(tmp, "JPEG", quality=90)
                        tmp.replace(target)
                    except Exception as e:  # noqa: BLE001
                        raise HTTPException(415, f"Görüntü hazırlanamadı: {e}")
                    # Önbellek küçük kalsın: en yeni 300 görüntü tutulur
                    old = sorted(cache.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
                    for p in old[:-300]:
                        p.unlink(missing_ok=True)
        return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    # ------------------------------------------------------------------ video
    def _video_row(repo: Repo, asset_id: int):
        r = repo.db.execute(
            "SELECT a.*, f.fs_path, f.rel_path, f.tracked FROM assets a JOIN files f ON f.asset_id=a.id "
            "WHERE a.id=? AND f.state='present' ORDER BY f.tracked DESC, f.id LIMIT 1", (asset_id,)).fetchone()
        if r is None or r["kind"] != "video":
            raise HTTPException(404, "Video bulunamadı")
        return r

    def _proxy_path(repo: Repo, blake: str) -> Path:
        return repo.mm / "proxies" / blake[:2] / f"{blake}.mp4"

    @app.get("/repos/{repo_id}/play/{blake}")
    def play(repo_id: str, blake: str):
        from ..media import video as video_mod

        repo = pool.get(repo_id)
        r = repo.db.execute(
            "SELECT a.video_codec, f.fs_path, f.rel_path FROM assets a JOIN files f ON f.asset_id=a.id "
            "WHERE a.blake3=? AND f.state='present' LIMIT 1", (blake,)).fetchone()
        if r is None:
            raise HTTPException(404, "Video bulunamadı")
        if video_mod.playable(r["rel_path"], r["video_codec"]):
            path = repo.root / r["fs_path"]
            return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "video/mp4")
        px = _proxy_path(repo, blake)
        if px.exists():
            return FileResponse(px, media_type="video/mp4")
        return JSONResponse({"detail": "Önizleme kopyası gerekli", "proxy_needed": True}, status_code=409)

    @app.post("/repos/{repo_id}/proxy/{blake}")
    def proxy_make(repo_id: str, blake: str):
        from ..media import video as video_mod

        repo = pool.get(repo_id)
        for j in jobs.list(repo.id):
            if j.type == "proxy" and j.title.endswith(blake[:8]) and j.status in ("queued", "running"):
                return j.to_dict()
        r = repo.db.execute("SELECT a.duration_ms, f.fs_path FROM assets a JOIN files f ON f.asset_id=a.id "
                            "WHERE a.blake3=? AND f.state='present' LIMIT 1", (blake,)).fetchone()
        if r is None:
            raise HTTPException(404, "Video bulunamadı")
        dst = _proxy_path(repo, blake)
        return jobs.submit("proxy", repo.id, f"Önizleme kopyası · {blake[:8]}",
                           lambda pr: str(video_mod.make_proxy(repo.root / r["fs_path"], dst, r["duration_ms"], pr)),
                           lane="media").to_dict()

    @app.get("/repos/{repo_id}/assets/{asset_id}/video")
    def video_info(repo_id: str, asset_id: int):
        from ..media import video as video_mod
        from ..ml import videoan

        repo = pool.get(repo_id)
        r = _video_row(repo, asset_id)
        info = videoan.video_info(repo, asset_id)
        info["playable"] = video_mod.playable(r["rel_path"], r["video_codec"])
        info["proxy_ready"] = _proxy_path(repo, r["blake3"]).exists()
        return info

    @app.get("/repos/{repo_id}/frame/{blake}/{ms}")
    def video_frame(repo_id: str, blake: str, ms: int):
        from ..ml import videoan

        repo = pool.get(repo_id)
        p = videoan.frame_thumb(repo, blake, ms)
        if not p.exists():
            raise HTTPException(404, "Kare yok")
        return FileResponse(p, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/repos/{repo_id}/strip/{blake}")
    def video_strip(repo_id: str, blake: str, n: int = Query(10, ge=2, le=20)):
        """Kırpma çizelgesi için videodan eşit aralıklı n karelik yatay şerit (önbelleğe alınır)."""
        from PIL import Image

        from ..media import video as video_mod

        repo = pool.get(repo_id)
        src = _asset_source(repo, blake)
        if src["kind"] != "video":
            raise HTTPException(400, "Video değil")
        out = repo.mm / "frames" / blake[:2] / blake / f"strip_{n}.webp"
        if not out.exists():
            with thumb_mx:
                lk = thumb_locks.setdefault(f"strip:{blake}", threading.Lock())
            with lk:
                if not out.exists():
                    w, h = 160, 90
                    sheet = Image.new("RGB", (w * n, h))
                    dur = src["duration_ms"] or 0
                    for i in range(n):
                        im = video_mod.frame_at(repo.root / src["fs_path"], int(dur * (i + 0.5) / n), 320) if dur else None
                        if im is None:
                            continue
                        sc = max(w / im.width, h / im.height)
                        im = im.resize((max(w, round(im.width * sc)), max(h, round(im.height * sc))))
                        x, y = (im.width - w) // 2, (im.height - h) // 2
                        sheet.paste(im.crop((x, y, x + w, y + h)), (i * w, 0))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    sheet.save(out, "WEBP", quality=70)
        return FileResponse(out, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/repos/{repo_id}/assets/{asset_id}/transcript")
    def video_transcript(repo_id: str, asset_id: int):
        from ..ml import transcribe

        return transcribe.transcript(pool.get(repo_id), asset_id)

    @app.get("/repos/{repo_id}/assets/{asset_id}/moments")
    def video_moments(repo_id: str, asset_id: int, q: str):
        from ..ml import videoan

        return {"moments": videoan.moments(pool.get(repo_id), asset_id, q)}

    def _derived(repo: Repo, r, suffix: str) -> tuple[Path, str]:
        """Kaynağın yanında benzersiz yeni dosya adı."""
        rel = r["rel_path"]
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        stem = Path(rel).stem
        i = 0
        while True:
            name = f"{stem}_{suffix}{'' if not i else f'-{i}'}"
            cand = f"{folder}/{name}" if folder else name
            ext = ".jpg" if suffix.startswith("kare") else ".mp4"
            if not (repo.root / (cand + ext)).exists():
                return repo.root / (cand + ext), cand + ext
            i += 1

    def _copy_meta(src: Path, dst: Path, taken_at: str | None, offset_ms: int, photo: bool) -> None:
        """Tarih (kareye denk gelen an) ve konumu yeni dosyaya taşır (en iyi çaba)."""
        import shutil
        import subprocess as sp
        from datetime import datetime, timedelta

        if not shutil.which("exiftool"):
            return
        args = ["exiftool", "-q", "-overwrite_original", "-api", "QuickTimeUTC=1", "-tagsFromFile", str(src),
                "-GPSLatitude<Composite:GPSLatitude", "-GPSLongitude<Composite:GPSLongitude",
                "-GPSLatitudeRef<Composite:GPSLatitudeRef", "-GPSLongitudeRef<Composite:GPSLongitudeRef", "-Make", "-Model"]
        if not photo:
            args += ["-GPSCoordinates"]
        if taken_at:
            t = datetime.fromisoformat(taken_at) + timedelta(milliseconds=offset_ms)
            stamp = t.strftime("%Y:%m:%d %H:%M:%S")
            args += [f"-DateTimeOriginal={stamp}", f"-CreateDate={stamp}"] if photo else [f"-CreateDate={stamp}"]
        sp.run([*args, str(dst)], capture_output=True, timeout=60)

    def _after_create(repo: Repo, rel: str, pr) -> None:
        # Yeni dosya hemen görünsün: tara, takibe al, hızlı aşamalar (ML analizi analiz şeridinde/sonraki çalıştırmada)
        scan.scan(repo)
        analyze.track(repo, [rel])
        analyze.analyze(repo, pr, smart=False)

    @app.post("/repos/{repo_id}/assets/{asset_id}/snapshot")
    def video_snapshot(repo_id: str, asset_id: int, ms: int = Body(..., embed=True)):
        from ..media import video as video_mod

        repo = pool.get(repo_id)
        r = _video_row(repo, asset_id)
        dst, rel = _derived(repo, r, f"kare_{video_mod.fmt_ms(ms)}")

        def run(pr):
            video_mod.save_snapshot(repo.root / r["fs_path"], ms, dst, r["duration_ms"])
            _copy_meta(repo.root / r["fs_path"], dst, r["taken_at"], ms, photo=True)
            _after_create(repo, rel, pr)
            return {"rel_path": rel}

        return submit(repo, "snapshot", "Kare kaydediliyor", run)

    @app.post("/repos/{repo_id}/assets/{asset_id}/clip")
    def video_clip(repo_id: str, asset_id: int, start_ms: int = Body(...), end_ms: int = Body(...)):
        from ..media import video as video_mod

        repo = pool.get(repo_id)
        r = _video_row(repo, asset_id)
        if end_ms - start_ms < 300:
            raise HTTPException(400, "Klip en az 0,3 saniye olmalı")
        dst, rel = _derived(repo, r, f"klip_{video_mod.fmt_ms(start_ms)}_{video_mod.fmt_ms(end_ms)}")

        def run(pr):
            video_mod.cut_clip(repo.root / r["fs_path"], start_ms, end_ms, dst, pr)
            _copy_meta(repo.root / r["fs_path"], dst, r["taken_at"], start_ms, photo=False)
            _after_create(repo, rel, pr)
            return {"rel_path": rel}

        return submit(repo, "clip", "Klip çıkarılıyor", run)

    # ------------------------------------------------------------------ tekrarlar
    @app.get("/repos/{repo_id}/dups")
    def dups(repo_id: str, threshold: int | None = None, rule: str | None = None):
        repo = pool.get(repo_id)
        groups = dedup.find(repo, threshold, rule)
        out = []
        for g in groups:
            blakes = {r["id"]: r["blake3"] for r in repo.db.execute(
                f"SELECT f.id, a.blake3 FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.id IN ({','.join('?' * len(g.members))})",
                [m.file_id for m in g.members])}
            out.append({"id": g.id, "kind": g.kind, "gain_bytes": g.gain_bytes,
                        "members": [{**asdict(m), "blake3": blakes.get(m.file_id)} for m in g.members]})
        totals = {k: {"groups": 0, "files": 0, "bytes": 0} for k in ("exact", "near", "video", "burst")}
        for g in out:
            t = totals[g["kind"]]
            t["groups"] += 1
            t["files"] += sum(1 for m in g["members"] if m["role"] in ("delete", "optional"))
            t["bytes"] += g["gain_bytes"] or sum(m["size"] for m in g["members"] if m["role"] == "optional")
        return {"groups": out, "totals": totals}

    # ------------------------------------------------------------------ düzenleme
    @app.get("/repos/{repo_id}/organize/preview")
    def organize_preview(repo_id: str, template: str, scope: str = "tracked", under: str | None = None,
                         base: str = "", limit: int = 200):
        repo = pool.get(repo_id)
        try:
            moves = organize.preview(repo, template, scope, under, base)
        except organize.TemplateError as e:
            raise HTTPException(400, str(e))
        tree: dict[str, int] = {}
        for _, _, dst in moves:
            parts = dst.split("/")
            key = "/".join(parts[:2]) + "/" if len(parts) > 2 else (parts[0] + "/" if len(parts) > 1 else "./")
            tree[key] = tree.get(key, 0) + 1
        total_tracked = repo.db.execute(
            "SELECT COUNT(*) FROM files WHERE state='present' AND (?='all' OR tracked=(?='tracked'))", (scope, scope)
        ).fetchone()[0]
        return {"template": organize.resolve_template(template), "total": len(moves),
                "unchanged": max(0, total_tracked - len(moves)),
                "moves": [{"file_id": f, "src": s, "dst": d} for f, s, d in moves[:limit]],
                "tree": sorted(({"folder": k, "n": v} for k, v in tree.items()), key=lambda x: x["folder"])[:200],
                "presets": organize.PRESETS}

    # ------------------------------------------------------------------ planlar
    def plan_dict(repo: Repo, pid: int, limit: int = 500) -> dict:
        p = dict(plans.get(repo, pid))
        p["summary"] = json.loads(p["summary"] or "{}")
        its = plans.items(repo, pid)
        p["items"] = [dict(i) for i in its[:limit]]
        p["item_count"] = len(its)
        return p

    @app.post("/repos/{repo_id}/plans")
    def plan_create(repo_id: str, body: dict = Body(...)):
        repo = pool.get(repo_id)
        kind = body.get("kind")
        source = body.get("source", "ui")
        if kind == "organize":
            try:
                pid = organize.build_plan(repo, body["template"], body.get("scope", "tracked"), body.get("under"),
                                          body.get("base", ""), source=source)
            except organize.TemplateError as e:
                raise HTTPException(400, str(e))
        elif kind in ("trash", "dedup"):
            ids = [int(i) for i in body.get("file_ids", [])]
            if not ids:
                raise HTTPException(400, "Dosya seçilmedi")
            rows = repo.db.execute(
                f"SELECT id, rel_path FROM files WHERE state='present' AND id IN ({','.join('?' * len(ids))})", ids).fetchall()
            if not rows:
                raise HTTPException(400, "Seçilen dosyalar zaten silinmiş ya da artık klasörde değil")
            title = body.get("title") or ("Tekrarları çöpe taşı" if kind == "dedup" else "Çöpe taşı")
            pid = plans.create(repo, kind, [("trash", r["id"], r["rel_path"], None) for r in rows], title=title, source=source)
        elif kind == "move":
            moves = body.get("moves", [])
            rows = {r["id"]: r["rel_path"] for r in repo.db.execute(
                f"SELECT id, rel_path FROM files WHERE state='present' AND id IN ({','.join('?' * len(moves)) or 'NULL'})",
                [m["file_id"] for m in moves])}
            if not rows:
                raise HTTPException(400, "Taşınacak dosya bulunamadı")
            pid = plans.create(repo, "move", [("move", m["file_id"], rows[m["file_id"]], m["dst"]) for m in moves
                                              if m["file_id"] in rows], title=body.get("title", "Taşı"), source=source)
        else:
            raise HTTPException(400, "Bilinmeyen plan türü")
        return plan_dict(repo, pid)

    @app.get("/repos/{repo_id}/plans")
    def plan_list(repo_id: str):
        repo = pool.get(repo_id)
        out = []
        for p in plans.list_plans(repo, 200):
            d = dict(p)
            d["summary"] = json.loads(d["summary"] or "{}")
            out.append(d)
        return out

    @app.get("/repos/{repo_id}/plans/{pid}")
    def plan_get(repo_id: str, pid: int, limit: int = 500):
        return plan_dict(pool.get(repo_id), pid, limit)

    @app.post("/repos/{repo_id}/plans/{pid}/apply")
    def plan_apply(repo_id: str, pid: int):
        repo = pool.get(repo_id)
        plans.get(repo, pid)
        return submit(repo, "apply", f"Plan #{pid} uygulanıyor", lambda pr: asdict(plans.apply(repo, pid, pr)))

    @app.post("/repos/{repo_id}/plans/{pid}/undo")
    def plan_undo(repo_id: str, pid: int):
        repo = pool.get(repo_id)
        plans.get(repo, pid)
        return submit(repo, "undo", f"Plan #{pid} geri alınıyor", lambda pr: asdict(plans.undo(repo, pid, pr)))

    @app.post("/repos/{repo_id}/plans/{pid}/reject")
    def plan_reject(repo_id: str, pid: int):
        repo = pool.get(repo_id)
        plans.reject(repo, pid)
        return plan_dict(repo, pid, 0)

    # ------------------------------------------------------------------ çöp
    @app.get("/repos/{repo_id}/trash")
    def trash(repo_id: str):
        repo = pool.get(repo_id)
        rows = plans.trash_contents(repo)
        media = [r for r in rows if r["file_id"] is not None]
        meta = {}
        if media:
            ids = [r["file_id"] for r in media]
            q = ",".join("?" * len(ids))
            meta = {m["file_id"]: dict(m) for m in repo.db.execute(
                f"SELECT f.id AS file_id, f.tracked, a.id AS asset_id, a.blake3, a.kind, a.mime, a.width, a.height, "
                f"a.duration_ms, a.taken_at, a.favorite FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.id IN ({q})", ids)}
        items = []
        for r in sorted(media, key=lambda r: (r["applied_at"] or "", r["id"]), reverse=True)[:3000]:
            m = meta.get(r["file_id"])
            if not m:
                continue
            items.append({"id": r["id"], "plan_id": r["plan_id"], "src": r["src"], "size": r["size"],
                          "applied_at": r["applied_at"], **m})
        return {"files": len(media), "bytes": sum(r["size"] for r in rows), "items": items}

    @app.post("/repos/{repo_id}/trash/restore")
    def trash_restore(repo_id: str, item_ids: list[int] = Body(..., embed=True)):
        r = plans.restore_items(pool.get(repo_id), item_ids)
        return {"restored": r.done, "skipped": r.skipped, "failed": r.failed}

    @app.post("/repos/{repo_id}/trash/delete")
    def trash_delete(repo_id: str, item_ids: list[int] = Body(..., embed=True)):
        n, freed = plans.purge_items(pool.get(repo_id), item_ids)
        return {"deleted": n, "bytes": freed}

    @app.post("/repos/{repo_id}/trash/empty")
    def trash_empty(repo_id: str, older_than: int | None = Body(None, embed=True)):
        n, freed = plans.empty_trash(pool.get(repo_id), older_than)
        return {"deleted": n, "bytes": freed}

    # ------------------------------------------------------------------ akıllı analiz ve modeller
    @app.get("/models")
    def models_status():
        from ..ml import models as models_mod

        return {"models": models_mod.status(), "root": str(models_mod.models_root())}

    @app.post("/models/download")
    def models_download(name: str | None = Body(None, embed=True)):
        from ..ml import models as models_mod

        specs = [models_mod.SPECS[name]] if name else list(models_mod.SPECS.values())

        def run(pr):
            for sp in specs:
                models_mod.download(sp, pr)
            return {"installed": [sp.name for sp in specs]}

        return jobs.submit("models", "global", "Modeller indiriliyor", run).to_dict()

    # Model kalitesi (standart / yüksek)
    @app.get("/models/tiers")
    def model_tiers(repo_id: str | None = None):
        from ..ml import tiers

        out = tiers.status()
        if repo_id:
            out["progress"] = tiers.switch_progress(pool.get(repo_id))
        return out

    @app.post("/models/tiers/try")
    def model_tier_try(kind: str = Body(...), tier: str = Body(...), repo_id: str = Body(...)):
        """Gerekirse modeli indirir, sonra bu kütüphanenin birkaç öğesiyle dener (süre tahmini)."""
        from ..ml import models as models_mod
        from ..ml import tiers

        if kind not in tiers.KINDS or tier not in ("standard", "high"):
            raise HTTPException(400, "Geçersiz seçim")
        ok, why = tiers.check(kind, tier)
        if not ok:
            raise HTTPException(409, why)
        repo = pool.get(repo_id)
        sp = tiers.spec(kind, tier)

        def run(pr):
            if not models_mod.installed(sp):
                models_mod.download(sp, pr)
            return tiers.trial(repo, kind, tier, pr)

        return jobs.submit("models", "global", f"{sp.label} deneniyor", run, lane="models").to_dict()

    @app.post("/models/tiers/apply")
    def model_tier_apply(kind: str = Body(...), tier: str = Body(...)):
        from .. import settings as settings_mod
        from ..ml import models as models_mod
        from ..ml import tiers

        if kind not in tiers.KINDS or tier not in ("standard", "high"):
            raise HTTPException(400, "Geçersiz seçim")
        ok, why = tiers.check(kind, tier)
        if not ok:
            raise HTTPException(409, why)
        if not models_mod.installed(tiers.spec(kind, tier)):
            raise HTTPException(409, "Model henüz indirilmedi")
        settings_mod.update({"models": {kind: tier}})
        tiers.invalidate()
        for repo in pool.opened():
            resume_analysis(repo)  # yeni modelle yeniden analiz arka planda başlar
        return tiers.status()

    @app.post("/models/tiers/remove")
    def model_tier_remove(kind: str = Body(...), tier: str = Body(...)):
        """Kullanılmayan yüksek kalite modelini siler (yer açmak için)."""
        from ..ml import models as models_mod
        from ..ml import tiers

        if tier != "high" or kind not in tiers.KINDS:
            raise HTTPException(400, "Yalnız yüksek kalite modeli silinebilir")
        if tiers.current(kind) == tier:
            raise HTTPException(409, "Kullanımdaki model silinemez; önce Standart'a geç")
        models_mod.remove(tiers.spec(kind, tier))
        return tiers.status()

    @app.get("/repos/{repo_id}/smart")
    def smart_state(repo_id: str):
        from ..ml import content as content_mod
        from ..ml import faces as faces_mod
        from ..ml import siglip

        repo = pool.get(repo_id)
        videos = repo.db.execute(
            "SELECT COUNT(*), SUM(json_extract(a.analysis_state, '$.video') IS NOT NULL) FROM assets a WHERE a.kind='video' "
            "AND EXISTS (SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.state='present' AND f.tracked=1)").fetchone()
        return {**content_mod.state_counts(repo), "content_model": siglip.available(), "face_model": faces_mod.available(),
                "videos": videos[0] or 0, "videos_done": videos[1] or 0,
                "transcript_model": __import__("mediamind.ml.transcribe", fromlist=["x"]).available(),
                "transcripts_done": repo.db.execute("SELECT COUNT(*) FROM assets WHERE kind='video' AND json_extract(analysis_state, '$.transcript') IS NOT NULL").fetchone()[0],
                "enabled": {"content": repo.config["analysis"].get("content", True), "faces": repo.config["analysis"].get("faces", True),
                            "video": repo.config["analysis"].get("video", True),
                            "transcripts": repo.config["analysis"].get("transcripts", False),
                            "ocr": repo.config["analysis"].get("ocr", True),
                            "pets": repo.config["analysis"].get("pets", True)},
                "pet_model": __import__("mediamind.ml.pets", fromlist=["x"]).available(),
                "pets_found": repo.db.execute("SELECT COUNT(*) FROM faces WHERE animal IS NOT NULL").fetchone()[0],
                "ocr_model": __import__("mediamind.ml.ocr", fromlist=["x"]).available(),
                "ocr_done": repo.db.execute("SELECT COUNT(*) FROM assets WHERE kind='photo' AND json_extract(analysis_state, '$.ocr') IS NOT NULL").fetchone()[0],
                "with_text": repo.db.execute("SELECT COUNT(*) FROM asset_text").fetchone()[0]}

    @app.post("/repos/{repo_id}/smart/run")
    def smart_run(repo_id: str, force: bool = Body(False, embed=True)):
        repo = pool.get(repo_id)

        return submit(repo, "smart", "İçerik, yüz ve video analizi", lambda pr: analyze.run_smart(repo, pr, force), lane="analysis")

    @app.get("/repos/{repo_id}/people")
    def people_list(repo_id: str):
        from ..ml import people as people_mod

        repo = pool.get(repo_id)
        return {"persons": people_mod.persons(repo), "clusters": people_mod.clusters(repo), "pet_clusters": people_mod.pet_clusters(repo)}

    @app.post("/repos/{repo_id}/people/recluster")
    def people_recluster(repo_id: str):
        from ..ml import people as people_mod

        repo = pool.get(repo_id)
        return submit(repo, "people", "Yüzler yeniden gruplanıyor", lambda pr: people_mod.recluster(repo), lane="people")

    @app.post("/repos/{repo_id}/people/clusters/{cluster_id}/name")
    def cluster_name(repo_id: str, cluster_id: int, name: str = Body(..., embed=True)):
        from ..ml import people as people_mod

        try:
            return people_mod.name_cluster(pool.get(repo_id), cluster_id, name)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/repos/{repo_id}/faces/{face_id}/assign")
    def face_assign(repo_id: str, face_id: int, name: str | None = Body(None, embed=True)):
        from ..ml import people as people_mod

        try:
            return people_mod.assign_face(pool.get(repo_id), face_id, name)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/repos/{repo_id}/assets/{asset_id}/people")
    def asset_tag_person(repo_id: str, asset_id: int, name: str = Body(...), box: list[float] | None = Body(None)):
        from ..ml import people as people_mod

        if box is not None and len(box) != 4:
            raise HTTPException(400, "box: [x1, y1, x2, y2] (0..1)")
        try:
            return people_mod.tag_person(pool.get(repo_id), asset_id, name, tuple(box) if box else None)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/repos/{repo_id}/faces/{face_id}")
    def face_delete_manual(repo_id: str, face_id: int):
        from ..ml import people as people_mod

        people_mod.remove_manual(pool.get(repo_id), face_id)
        return {"ok": True}

    @app.post("/repos/{repo_id}/faces/{face_id}/exclude")
    def face_exclude(repo_id: str, face_id: int, reason: str | None = Body(None, embed=True)):
        from ..ml import people as people_mod

        try:
            people_mod.exclude_face(pool.get(repo_id), face_id, reason)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/repos/{repo_id}/people/merge-suggestions")
    def people_merge_suggestions(repo_id: str):
        from ..ml import people as people_mod

        return {"suggestions": people_mod.merge_suggestions(pool.get(repo_id))}

    @app.post("/repos/{repo_id}/people/merge-suggestions/dismiss")
    def people_merge_dismiss(repo_id: str, a: int = Body(...), b: int = Body(...)):
        from ..ml import people as people_mod

        people_mod.dismiss_merge(pool.get(repo_id), a, b)
        return {"ok": True}

    @app.post("/repos/{repo_id}/people/clusters/{cluster_id}/reject")
    def cluster_reject(repo_id: str, cluster_id: int, person_id: int = Body(..., embed=True)):
        from ..ml import people as people_mod

        return {"faces": people_mod.reject_suggestion(pool.get(repo_id), cluster_id, person_id)}

    @app.post("/repos/{repo_id}/people/clusters/{cluster_id}/dissolve")
    def cluster_dissolve(repo_id: str, cluster_id: int):
        from ..ml import people as people_mod

        return {"faces": people_mod.dissolve_cluster(pool.get(repo_id), cluster_id)}

    @app.post("/repos/{repo_id}/people/clusters/{cluster_id}/ignore")
    def cluster_ignore(repo_id: str, cluster_id: int):
        from ..ml import people as people_mod

        return {"faces": people_mod.ignore_cluster(pool.get(repo_id), cluster_id)}

    @app.get("/repos/{repo_id}/people/ignored")
    def people_ignored(repo_id: str):
        from ..ml import people as people_mod

        return people_mod.ignored(pool.get(repo_id))

    @app.post("/repos/{repo_id}/people/ignored/{group}/restore")
    def people_ignored_restore(repo_id: str, group: int):
        from ..ml import people as people_mod

        return {"faces": people_mod.restore_ignored(pool.get(repo_id), group)}

    @app.patch("/repos/{repo_id}/people/{person_id}")
    def person_rename(repo_id: str, person_id: int, name: str = Body(..., embed=True)):
        from ..ml import people as people_mod

        people_mod.rename_person(pool.get(repo_id), person_id, name)
        return {"ok": True}

    @app.post("/repos/{repo_id}/people/{person_id}/merge")
    def person_merge(repo_id: str, person_id: int, into: int = Body(..., embed=True)):
        from ..ml import people as people_mod

        people_mod.merge_persons(pool.get(repo_id), person_id, into)
        return {"ok": True}

    @app.delete("/repos/{repo_id}/people/{person_id}")
    def person_delete(repo_id: str, person_id: int):
        from ..ml import people as people_mod

        people_mod.delete_person(pool.get(repo_id), person_id)
        return {"ok": True}

    @app.get("/repos/{repo_id}/people/{person_id}/faces")
    def person_faces(repo_id: str, person_id: int, limit: int = Query(60, le=2000)):
        repo = pool.get(repo_id)
        # Fotoğraf başına en net yüz; videolarda aynı kişinin onlarca karesi listeyi doldurmasın
        return [dict(r) for r in repo.db.execute(
            "SELECT f.id, f.asset_id, f.quality, f.assigned_by, f.frame_ms, a.taken_at FROM faces f JOIN assets a ON a.id=f.asset_id "
            "WHERE f.person_id=? AND f.id=(SELECT f2.id FROM faces f2 WHERE f2.asset_id=f.asset_id AND f2.person_id=f.person_id "
            "ORDER BY f2.quality DESC LIMIT 1) ORDER BY f.quality DESC LIMIT ?",
            (person_id, limit))]

    @app.get("/repos/{repo_id}/people/{person_id}/timeline")
    def person_timeline(repo_id: str, person_id: int):
        repo = pool.get(repo_id)
        p = repo.db.execute("SELECT id, name, kind, birth_date FROM persons WHERE id=?", (person_id,)).fetchone()
        if p is None:
            raise HTTPException(404, "Kişi bulunamadı")
        # Fotoğraf başına en net yüz, çekim tarihine göre (tarihi yalnız dosya saatinden gelenler güvenilmez)
        faces = [dict(r) for r in repo.db.execute(
            "SELECT f.id, f.asset_id, f.quality, a.taken_at FROM faces f JOIN assets a ON a.id=f.asset_id "
            "WHERE f.person_id=? AND f.frame_ms IS NULL AND a.taken_at IS NOT NULL AND COALESCE(a.taken_at_source,'')<>'mtime' "
            "AND EXISTS (SELECT 1 FROM files fl WHERE fl.asset_id=a.id AND fl.state='present') "
            "AND f.id=(SELECT f2.id FROM faces f2 WHERE f2.asset_id=f.asset_id AND f2.person_id=f.person_id "
            "ORDER BY f2.quality DESC LIMIT 1) ORDER BY a.taken_at", (person_id,))]
        return {"person": dict(p), "faces": faces}

    @app.put("/repos/{repo_id}/people/{person_id}/birth")
    def person_birth(repo_id: str, person_id: int, birth_date: str | None = Body(None, embed=True)):
        if birth_date:
            try:
                birth_date = datetime.fromisoformat(birth_date).date().isoformat()
            except ValueError:
                raise HTTPException(400, "Geçersiz tarih")
        repo = pool.get(repo_id)
        with repo.lock(), repo.db as conn:
            if not conn.execute("UPDATE persons SET birth_date=? WHERE id=?", (birth_date or None, person_id)).rowcount:
                raise HTTPException(404, "Kişi bulunamadı")
        return {"birth_date": birth_date or None}

    @app.put("/repos/{repo_id}/people/{person_id}/cover")
    def person_cover(repo_id: str, person_id: int, face_id: int | None = Body(None, embed=True)):
        from ..ml import people as people_mod

        try:
            return people_mod.set_cover(pool.get(repo_id), person_id, face_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/repos/{repo_id}/faces/{face_id}/crop")
    def face_crop(repo_id: str, face_id: int, size: int = 160):
        from PIL import Image as _Image

        repo = pool.get(repo_id)
        r = repo.db.execute("SELECT f.*, a.blake3 FROM faces f JOIN assets a ON a.id=f.asset_id WHERE f.id=?", (face_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Yüz bulunamadı")
        # Yeniden analizde yüz kimlikleri tekrar kullanılabilir; önbellek adı içeriğe ve konuma bağlı
        size = 400 if size > 160 else 160  # büyük boy: zaman çizelgesi gösterimi (önizlemenin büyüğünden kesilir)
        suffix = "" if size == 160 else f"_{size}"
        target = repo.mm / "faces" / f"{face_id}_{r['blake3'][:10]}_{int(r['x1'] * 1000)}{suffix}.webp"
        if not target.exists():
            src = repo.thumb_path(r["blake3"], "l" if size > 160 else "m")
            if not src.exists():
                src = repo.thumb_path(r["blake3"], "m")
            if not src.exists():
                raise HTTPException(404, "Önizleme yok")
            with _Image.open(src) as im:
                w, h = im.size
                cx, cy = (r["x1"] + r["x2"]) / 2 * w, (r["y1"] + r["y2"]) / 2 * h
                side = max((r["x2"] - r["x1"]) * w, (r["y2"] - r["y1"]) * h) * 1.5
                box = (max(0, cx - side / 2), max(0, cy - side / 2), min(w, cx + side / 2), min(h, cy + side / 2))
                crop = im.convert("RGB").crop(tuple(int(v) for v in box)).resize((size, size))
                target.parent.mkdir(parents=True, exist_ok=True)
                crop.save(target, "WEBP", quality=85)
        return FileResponse(target, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})

    # ------------------------------------------------------------------ ayarlar ve asistan
    assistant = Assistant(pool.get)

    def mcp_info() -> dict:
        # Tek parça uygulamada başlatıcı betik; geliştirmede sanal ortamdaki komut
        cmd = os.environ.get("MEDIAMIND_LAUNCHER") or str(Path(sys.executable).parent / "mediamind")
        return {"command": cmd, "config": {"mcpServers": {"mediamind": {"command": cmd, "args": ["mcp"]}}},
                "claude_code": f"claude mcp add mediamind -- {cmd} mcp",
                "tools": [{"name": t["name"], "description": t["description"]} for t in tools.TOOLS]}

    def settings_dict() -> dict:
        cfg = settings_mod.load()
        provider, model = settings_mod.active_model(cfg)
        return {
            "settings": cfg,
            "models": settings_mod.MODELS,
            "providers": {k: {"label": v["label"], "key_url": v["key_url"], "key_prefix": v["key_prefix"]}
                          for k, v in settings_mod.PROVIDERS.items()},
            "api_keys": {k: settings_mod.masked_key(k) for k in settings_mod.PROVIDERS},
            "api_key": settings_mod.masked_key(provider),  # etkin sağlayıcının anahtarı
            "active": {"provider": provider, "model": model},
            "mcp": mcp_info(),
        }

    @app.get("/settings")
    def settings_get():
        return settings_dict()

    @app.patch("/settings")
    def settings_patch(patch: dict = Body(...)):
        try:
            settings_mod.update(patch)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return settings_dict()

    def _provider(p: str | None) -> str:
        p = p or "anthropic"
        if p not in settings_mod.PROVIDERS:
            raise HTTPException(400, f"Bilinmeyen sağlayıcı: {p}")
        return p

    @app.put("/settings/api-key")
    def api_key_set(key: str = Body(...), provider: str | None = Body(None)):
        prov = _provider(provider)
        try:
            settings_mod.set_api_key(settings_mod.validate_key(prov, key), prov)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return settings_dict()

    @app.delete("/settings/api-key")
    def api_key_delete(provider: str | None = None):
        settings_mod.set_api_key(None, _provider(provider))
        return settings_dict()

    @app.get("/settings/models")
    def models(provider: str):
        try:
            return {"provider": provider, "models": list_models(_provider(provider))}
        except AssistantError as e:
            raise HTTPException(400, str(e))

    @app.post("/repos/{repo_id}/assistant")
    def assistant_chat(repo_id: str, message: str = Body(...), conversation_id: str | None = Body(None),
                       context: dict | None = Body(None)):
        repo = pool.get(repo_id)
        try:
            res = assistant.chat(repo.id, message, conversation_id, context)
        except AssistantError as e:
            raise HTTPException(400, str(e))
        res["plans"] = [plan_dict(repo, pid, 50) for pid in res["plans"]]
        return res

    @app.delete("/assistant/{conversation_id}")
    def assistant_reset(conversation_id: str):
        assistant.reset(conversation_id)
        return {"ok": True}

    return app


def serve(host: str = "127.0.0.1", port: int = 7341, token: str | None = None) -> None:
    import uvicorn

    level = os.environ.get("MEDIAMIND_LOG", "warning")
    uvicorn.run(create_app(token, watch=True), host=host, port=port, log_level=level, access_log=level in ("info", "debug"))
