"""Planlar: dosya sistemini değiştiren her işlem plan → onay → uygula → (geri al) yolundan geçer.

Güvenlik:
  * Yollar repo kökü dışına çıkamaz; .mediamind/ içine yalnızca çöp yazılır.
  * Her öğe önce 'running' olarak işaretlenir, sonra işlem yapılır, sonra 'done' yazılır.
    Uygulama yarıda kesilirse apply() tekrar çağrıldığında kaldığı yerden devam eder.
  * Silme = .mediamind/trash/<plan_id>/<göreli yol> konumuna taşıma (aynı birimde anlık).
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .kinds import SIDECAR_EXTS
from .paths import MM_DIR, check_rel, join, nfc
from .repo import Repo

Progress = Callable[[str, int, int], None]
ItemSpec = tuple[str, int | None, str, str | None]  # (op, file_id, src, dst)


class PlanError(Exception):
    pass


@dataclass
class ApplyResult:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    status: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sidecars(repo: Repo, src_rel: str) -> list[str]:
    p = PurePosixPath(src_rel)
    d = repo.root.joinpath(*p.parent.parts) if p.parent.parts else repo.root
    stem = p.stem.casefold()
    out = []
    try:
        for child in d.iterdir():
            name = child.name
            dot = name.rfind(".")
            if dot <= 0:
                continue
            if name[dot:].lower() in SIDECAR_EXTS and name[:dot].casefold() == stem and child.is_file():
                out.append(nfc(str(p.parent / name)) if p.parent.parts else nfc(name))
    except OSError:
        pass
    return out


def _with_stem(rel: str, new_stem_rel: str) -> str:
    """Yan dosyanın hedefi: asıl dosyanın hedef adı + yan dosyanın uzantısı."""
    ext = rel[rel.rfind("."):]
    base = new_stem_rel[: new_stem_rel.rfind(".")] if "." in PurePosixPath(new_stem_rel).name else new_stem_rel
    return base + ext


def _unique(repo: Repo, dst: str, taken: set[str]) -> str:
    conn = repo.db

    def busy(rel: str) -> bool:
        if rel.casefold() in taken:
            return True
        if (repo.root / rel).exists():
            return True
        return conn.execute("SELECT 1 FROM files WHERE rel_path=? AND state='present'", (rel,)).fetchone() is not None

    if not busy(dst):
        return dst
    p = PurePosixPath(dst)
    stem, suffix = p.stem, p.suffix
    for i in range(1, 10000):
        cand = str(p.with_name(f"{stem}-{i}{suffix}"))
        if not busy(cand):
            return cand
    raise PlanError(f"Benzersiz ad bulunamadı: {dst}")


def create(repo: Repo, kind: str, items: Iterable[ItemSpec], title: str = "", source: str = "cli") -> int:
    with repo.lock(), repo.db as conn:
        plan_id = conn.execute(
            "INSERT INTO plans(kind, source, status, title) VALUES (?,?,'pending',?)", (kind, source, title)
        ).lastrowid
        taken: set[str] = set()
        seen_src: set[str] = set()
        seq = 0
        n_files = total = renamed = 0
        freed: set[str] = set()  # bu planda çöpe giden yollar (aynı plandaki taşıma oraya yazabilir)
        for op, file_id, src, dst in items:
            src = nfc(src)
            # .mediamind/tmp/ yalnız MediaMind'ın ürettiği dosyalar için kaynak olabilir (ör. küçültülmüş video)
            check_rel(src, allow_internal=op == "move" and file_id is None and src.startswith(f"{MM_DIR}/tmp/"))
            if src in seen_src:
                continue
            row = None
            if file_id is not None:
                row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
                if row is None or row["state"] != "present" or row["rel_path"] != src:
                    continue
            if op == "trash":
                final = repo.trash_rel(plan_id, src)
                freed.add(src)
            elif op == "move":
                if not dst:
                    raise PlanError("Taşıma öğesinde hedef yok")
                dst = nfc(dst).rstrip("/")
                check_rel(dst)
                if dst == src:
                    continue
                final = dst if dst in freed and dst.casefold() not in taken else _unique(repo, dst, taken)
                renamed += final != dst
                taken.add(final.casefold())
            else:
                raise PlanError(f"Bilinmeyen işlem: {op}")
            size = row["size"] if row is not None else 0
            seen_src.add(src)
            conn.execute(
                "INSERT INTO plan_items(plan_id, seq, op, file_id, src, dst, size) VALUES (?,?,?,?,?,?,?)",
                (plan_id, seq, op, file_id, src, final, size),
            )
            seq += 1
            n_files += 1
            total += size
            for sc in _sidecars(repo, src):
                if sc in seen_src:
                    continue
                seen_src.add(sc)
                sc_dst = repo.trash_rel(plan_id, sc) if op == "trash" else _unique(repo, _with_stem(sc, final), taken)
                taken.add(sc_dst.casefold())
                try:
                    sc_size = (repo.root / sc).stat().st_size
                except OSError:
                    sc_size = 0
                conn.execute(
                    "INSERT INTO plan_items(plan_id, seq, op, file_id, src, dst, size) VALUES (?,?,?,?,?,?,?)",
                    (plan_id, seq, op, None, sc, sc_dst, sc_size),
                )
                seq += 1
        summary = {"files": n_files, "items": seq, "bytes": total, "renamed": renamed}
        conn.execute("UPDATE plans SET summary=? WHERE id=?", (json.dumps(summary), plan_id))
        if seq == 0:
            conn.execute("UPDATE plans SET status='rejected' WHERE id=?", (plan_id,))
    return plan_id


def get(repo: Repo, plan_id: int):
    row = repo.db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise PlanError(f"Plan bulunamadı: #{plan_id}")
    return row


def items(repo: Repo, plan_id: int) -> list:
    return repo.db.execute("SELECT * FROM plan_items WHERE plan_id=? ORDER BY seq", (plan_id,)).fetchall()


def list_plans(repo: Repo, limit: int = 50) -> list:
    return repo.db.execute("SELECT * FROM plans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.move(str(src), str(dst))


def _prune_empty(repo: Repo, dirs: set[Path]) -> None:
    """Taşıma sonrası boş kalan klasörleri (yalnız .DS_Store içerenler dahil) kaldırır."""
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        cur = d
        protected = {repo.root, repo.mm, repo.mm / "trash"}
        while cur not in protected and repo.root in cur.parents:
            try:
                # .DS_Store ve sahipsiz AppleDouble (._ad) dosyaları klasörü "dolu" saymaz (exFAT disklerde kalırlar)
                junk = [e for e in cur.iterdir() if e.name == ".DS_Store"
                        or (e.name.startswith("._") and not (cur / e.name[2:]).exists())]
                entries = [e for e in cur.iterdir() if e not in junk]
                if entries:
                    break
                for j in junk:
                    j.unlink(missing_ok=True)
                cur.rmdir()
            except OSError:
                break
            cur = cur.parent


def _apply_db(conn, item, rel: str, state: str) -> None:
    if item["file_id"] is not None:
        # Aynı yolda kalmış "kayıp" bir kayıt varsa benzersizlik kısıtını bozmasın
        conn.execute("DELETE FROM files WHERE rel_path=? AND id<>? AND state='missing'", (rel, item["file_id"]))
        conn.execute(
            "UPDATE files SET rel_path=?, fs_path=?, state=?, last_seen=datetime('now','localtime') WHERE id=?",
            (rel, rel, state, item["file_id"]),
        )


def _resolve(repo: Repo, rel: str) -> Path:
    return join(repo.root, rel, allow_internal=rel.startswith((f"{MM_DIR}/trash/", f"{MM_DIR}/tmp/")))


def apply(repo: Repo, plan_id: int, progress: Progress | None = None) -> ApplyResult:
    try:
        return _apply(repo, plan_id, progress)
    except PlanError:
        raise
    except BaseException:
        # Durduruldu ya da beklenmedik hata: yarım kalan plan kaldığı yerden sürdürülebilir
        try:
            with repo.db as conn:
                conn.execute("UPDATE plans SET status='interrupted' WHERE id=? AND status='running'", (plan_id,))
        except Exception:  # noqa: BLE001
            pass
        raise


def _apply(repo: Repo, plan_id: int, progress: Progress | None = None) -> ApplyResult:
    res = ApplyResult()
    with repo.lock():
        conn = repo.db
        plan = get(repo, plan_id)
        if plan["status"] not in ("pending", "running", "partial", "failed", "interrupted"):
            raise PlanError(f"Plan #{plan_id} uygulanamaz (durum: {plan['status']})")
        with conn:
            conn.execute("UPDATE plans SET status='running' WHERE id=?", (plan_id,))
        todo = [i for i in items(repo, plan_id) if i["status"] in ("pending", "running", "failed")]
        touched_dirs: set[Path] = set()
        for n, it in enumerate(todo, 1):
            src, dst = _resolve(repo, it["src"]), _resolve(repo, it["dst"])
            final_state = "trashed" if it["op"] == "trash" else "present"
            # Yarıda kalmış öğe: işlem diskte yapılmış ama DB'ye yazılamamış olabilir
            if it["status"] == "running" and not src.exists() and dst.exists():
                with conn:
                    _apply_db(conn, it, it["dst"], final_state)
                    conn.execute("UPDATE plan_items SET status='done', error=NULL WHERE id=?", (it["id"],))
                res.done += 1
                continue
            if not src.exists():
                with conn:
                    conn.execute("UPDATE plan_items SET status='skipped', error='kaynak dosya yok' WHERE id=?", (it["id"],))
                res.skipped += 1
                continue
            dst_rel = it["dst"]
            if dst.exists():
                if it["op"] == "trash":
                    with conn:
                        conn.execute("UPDATE plan_items SET status='failed', error='çöpte aynı adlı dosya var' WHERE id=?", (it["id"],))
                    res.failed += 1
                    continue
                dst_rel = _unique(repo, it["dst"], set())
                dst = _resolve(repo, dst_rel)
            with conn:
                conn.execute("UPDATE plan_items SET status='running', dst=? WHERE id=?", (dst_rel, it["id"]))
            try:
                _move(src, dst)
            except OSError as e:
                with conn:
                    conn.execute("UPDATE plan_items SET status='failed', error=? WHERE id=?", (str(e)[:300], it["id"]))
                res.failed += 1
                continue
            with conn:
                _apply_db(conn, it, dst_rel, final_state)
                conn.execute("UPDATE plan_items SET status='done', error=NULL WHERE id=?", (it["id"],))
            touched_dirs.add(src.parent)
            res.done += 1
            if progress:
                progress("apply", n, len(todo))
        _prune_empty(repo, touched_dirs)
        failed = conn.execute(
            "SELECT COUNT(*) FROM plan_items WHERE plan_id=? AND status='failed'", (plan_id,)
        ).fetchone()[0]
        res.status = "partial" if failed else "applied"
        with conn:
            conn.execute("UPDATE plans SET status=?, applied_at=? WHERE id=?", (res.status, _now(), plan_id))
    return res


def undo(repo: Repo, plan_id: int, progress: Progress | None = None) -> ApplyResult:
    res = ApplyResult()
    with repo.lock():
        conn = repo.db
        plan = get(repo, plan_id)
        if plan["status"] not in ("applied", "partial", "interrupted"):
            raise PlanError(f"Plan #{plan_id} geri alınamaz (durum: {plan['status']})")
        all_items = items(repo, plan_id)
        done = [i for i in all_items if i["status"] == "done"]
        res.skipped += sum(1 for i in all_items if i["status"] == "purged")  # çöp boşaltılmış: geri gelemez
        touched: set[Path] = set()
        for n, it in enumerate(reversed(done), 1):
            src, dst = _resolve(repo, it["src"]), _resolve(repo, it["dst"])
            if not dst.exists():
                with conn:
                    conn.execute("UPDATE plan_items SET error='geri alınamadı: dosya artık yok' WHERE id=?", (it["id"],))
                res.skipped += 1
                continue
            if src.exists():
                with conn:
                    conn.execute("UPDATE plan_items SET error='geri alınamadı: eski konumda başka dosya var' WHERE id=?", (it["id"],))
                res.skipped += 1
                continue
            try:
                _move(dst, src)
            except OSError as e:
                with conn:
                    conn.execute("UPDATE plan_items SET error=? WHERE id=?", (f"geri alınamadı: {e}"[:300], it["id"]))
                res.failed += 1
                continue
            with conn:
                _apply_db(conn, it, it["src"], "present")
                conn.execute("UPDATE plan_items SET status='undone', error=NULL WHERE id=?", (it["id"],))
                if it["file_id"] is None and it["src"].startswith(f"{MM_DIR}/tmp/"):
                    # MediaMind'ın koyduğu dosya (ör. küçültülmüş video) geri alındı: taramanın eklediği kaydı da kaldır
                    conn.execute("DELETE FROM files WHERE rel_path=? AND state='present'", (it["dst"],))
            touched.add(dst.parent)
            res.done += 1
            if progress:
                progress("undo", n, len(done))
        _prune_empty(repo, touched)
        trash_dir = repo.mm / "trash" / str(plan_id)
        if trash_dir.exists() and not any(p.is_file() for p in trash_dir.rglob("*")):
            shutil.rmtree(trash_dir, ignore_errors=True)
        res.status = "undone" if not (res.skipped or res.failed) else "partial"
        with conn:
            conn.execute("UPDATE plans SET status=?, undone_at=? WHERE id=?", (res.status, _now(), plan_id))
    return res


def recover_interrupted(repo: Repo) -> int:
    """Uygulama kapanınca 'running' kalmış planları 'interrupted' yapar (kimse uygulamıyorsa).

    Repo kilidi alınabiliyorsa o anda hiçbir süreç plan uygulamıyordur. Yarım kalan plan
    apply() ile kaldığı yerden sürdürülebilir ya da undo() ile yapılan kısmı geri alınabilir.
    """
    try:
        with repo.lock(timeout=0.0), repo.db as conn:
            return conn.execute("UPDATE plans SET status='interrupted' WHERE status='running'").rowcount
    except Exception:  # noqa: BLE001 - kilit meşgulse dokunma
        return 0


def reject(repo: Repo, plan_id: int) -> None:
    with repo.lock(), repo.db as conn:
        plan = get(repo, plan_id)
        if plan["status"] != "pending":
            raise PlanError(f"Yalnızca bekleyen planlar reddedilebilir (durum: {plan['status']})")
        conn.execute("UPDATE plans SET status='rejected' WHERE id=?", (plan_id,))


def trash_contents(repo: Repo, older_than_days: int | None = None) -> list:
    sql = (
        "SELECT i.*, p.applied_at FROM plan_items i JOIN plans p ON p.id=i.plan_id "
        "WHERE i.op='trash' AND i.status='done'"
    )
    rows = repo.db.execute(sql).fetchall()
    if older_than_days is None:
        return rows
    limit = datetime.now() - timedelta(days=older_than_days)
    return [r for r in rows if r["applied_at"] and datetime.fromisoformat(r["applied_at"]) <= limit]


def _with_sidecars(repo: Repo, rows: list) -> list:
    """Seçilen çöp öğelerine aynı plandaki yan dosyalarını (.AAE, .XMP; file_id NULL) ekler."""
    out = {r["id"]: r for r in rows}
    for r in rows:
        if r["file_id"] is None:
            continue
        p = PurePosixPath(r["src"])
        for sc in repo.db.execute(
            "SELECT * FROM plan_items WHERE plan_id=? AND file_id IS NULL AND op='trash' AND status='done'", (r["plan_id"],)
        ):
            q = PurePosixPath(sc["src"])
            if q.parent == p.parent and q.stem.casefold() == p.stem.casefold():
                out[sc["id"]] = sc
    return list(out.values())


def _trash_rows(repo: Repo, item_ids: Iterable[int]) -> list:
    ids = [int(i) for i in item_ids]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = repo.db.execute(
        f"SELECT * FROM plan_items WHERE id IN ({q}) AND op='trash' AND status='done'", ids).fetchall()
    return _with_sidecars(repo, rows)


def _refresh_plan_status(conn, plan_ids: set[int]) -> None:
    for pid in plan_ids:
        left = conn.execute("SELECT COUNT(*) FROM plan_items WHERE plan_id=? AND status='done'", (pid,)).fetchone()[0]
        if not left:
            conn.execute("UPDATE plans SET status='undone', undone_at=? WHERE id=? AND status IN ('applied','partial')",
                         (_now(), pid))


def restore_items(repo: Repo, item_ids: Iterable[int]) -> ApplyResult:
    """Çöpteki seçili dosyaları eski yerlerine geri getirir. Eski yerde başka dosya varsa ada numara eklenir."""
    res = ApplyResult()
    with repo.lock():
        conn = repo.db
        rows = _trash_rows(repo, item_ids)
        touched: set[Path] = set()
        for it in rows:
            cur = _resolve(repo, it["dst"])
            if not cur.exists():
                res.skipped += 1
                continue
            rel = it["src"]
            if _resolve(repo, rel).exists():
                rel = _unique(repo, rel, set())
            try:
                _move(cur, _resolve(repo, rel))
            except OSError as e:
                with conn:
                    conn.execute("UPDATE plan_items SET error=? WHERE id=?", (f"geri getirilemedi: {e}"[:300], it["id"]))
                res.failed += 1
                continue
            with conn:
                _apply_db(conn, it, rel, "present")
                conn.execute("UPDATE plan_items SET status='undone', error=NULL WHERE id=?", (it["id"],))
            touched.add(cur.parent)
            res.done += 1
        _prune_empty(repo, touched)
        with conn:
            _refresh_plan_status(conn, {r["plan_id"] for r in rows})
    return res


def purge_items(repo: Repo, item_ids: Iterable[int]) -> tuple[int, int]:
    """Çöpteki seçili dosyaları KALICI olarak siler. Geri alınamaz."""
    with repo.lock():
        return _purge(repo, _trash_rows(repo, item_ids))


def _purge(repo: Repo, rows: list) -> tuple[int, int]:
    n = freed = 0
    conn = repo.db
    for it in rows:
        p = _resolve(repo, it["dst"])
        try:
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
        except OSError:
            continue
        with conn:
            conn.execute("UPDATE plan_items SET status='purged' WHERE id=?", (it["id"],))
            if it["file_id"] is not None:
                conn.execute("DELETE FROM files WHERE id=?", (it["file_id"],))
        n += 1
    with conn:
        orphans = [r[0] for r in conn.execute("SELECT blake3 FROM assets WHERE id NOT IN (SELECT DISTINCT asset_id FROM files)")]
        conn.execute("DELETE FROM assets WHERE id NOT IN (SELECT DISTINCT asset_id FROM files)")
    _remove_derived(repo, orphans)
    trash_root = repo.mm / "trash"
    for d in sorted(trash_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
    return n, freed


def _remove_derived(repo: Repo, blakes: list[str]) -> None:
    """Kalıcı silinen içeriğin önizlemelerini, yüz kırpıntılarını, video karelerini ve kopyalarını temizler."""
    if not blakes:
        return
    want = set(blakes)
    for b in blakes:
        for size in ("s", "m", "l"):
            repo.thumb_path(b, size).unlink(missing_ok=True)
        shutil.rmtree(repo.mm / "frames" / b[:2] / b, ignore_errors=True)
        (repo.mm / "proxies" / b[:2] / f"{b}.mp4").unlink(missing_ok=True)
        (repo.mm / "cache" / "full" / f"{b}.jpg").unlink(missing_ok=True)
    faces_dir = repo.mm / "faces"
    if faces_dir.is_dir():
        short = {b[:10] for b in want}
        for p in faces_dir.iterdir():  # ad: <yüz no>_<blake3[:10]>_<x>.webp
            parts = p.name.split("_")
            if len(parts) >= 3 and parts[1] in short:
                p.unlink(missing_ok=True)


def empty_trash(repo: Repo, older_than_days: int | None = None) -> tuple[int, int]:
    """Çöpteki dosyaları KALICI olarak siler. Geri alınamaz."""
    with repo.lock():
        return _purge(repo, list(trash_contents(repo, older_than_days)))
