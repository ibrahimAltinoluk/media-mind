"""Telefondan / hafıza kartından kütüphaneye içe aktarma.

Akış: öğeleri listele → yeni olanları belirle → geçici klasöre indir/kopyala → içerik özeti (blake3) kütüphanede
zaten varsa atla → şablona göre yerine koy → kaydet, kütüphaneye ekle → (istenirse) aygıttan sil.
Aygıttan silme yalnızca kopyanın kütüphanede doğrulandığı öğeler için yapılır.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import analyze, scan
from .devices import DeviceHelper, card_items, card_sidecars
from .kinds import SIDECAR_EXTS
from .paths import MM_DIR, check_rel, nfc
from .repo import Repo

Progress = Callable[[str, int, int], None]
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
DEFAULT_TEMPLATE = "{yıl}/{ay}-{ay_adı}/{dosya}"
_BAD = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def render(template: str, created: str | None, name: str, device: str) -> str:
    """Şablon belirteçleri: {yıl} {ay} {ay_adı} {gün} {cihaz} {dosya}. Tarihsizler _tarihsiz/ altına."""
    try:
        d = datetime.fromisoformat(created) if created else None
    except ValueError:
        d = None
    vals = {"dosya": name, "cihaz": _BAD.sub("_", device).strip() or "Aygıt"}
    if d:
        vals.update({"yıl": f"{d.year:04d}", "ay": f"{d.month:02d}", "ay_adı": AYLAR[d.month - 1], "gün": f"{d.day:02d}"})
    else:
        vals.update({"yıl": "_tarihsiz", "ay": "", "ay_adı": "", "gün": ""})
    out = re.sub(r"\{([^{}]+)\}", lambda m: vals.get(m.group(1), m.group(0)), template)
    parts = [p.strip(" -") for p in out.split("/")]
    parts = [p for p in parts if p]
    rel = nfc("/".join(parts) or name)
    check_rel(rel)
    if rel.split("/", 1)[0] == MM_DIR:
        raise ValueError("Şablon .mediamind içine yazamaz")
    return rel


def _unique(root: Path, rel: str) -> str:
    p = Path(rel)
    cand, i = rel, 1
    while (root / cand).exists():
        cand = str(p.with_name(f"{p.stem}-{i}{p.suffix}"))
        i += 1
    return cand


def imported_keys(repo: Repo, device: str) -> set[str]:
    return {r[0] for r in repo.db.execute("SELECT key FROM device_imports WHERE device=?", (device,))}


def mark_new(repo: Repo, device: str, items: list[dict]) -> list[dict]:
    """Öğelere 'imported' bayrağı ekler: bu aygıttan daha önce aktarıldıysa ya da kütüphanede aynı ad+boyutta dosya varsa."""
    done = imported_keys(repo, device)
    have = {(Path(r[0]).name.casefold(), r[1]) for r in repo.db.execute("SELECT rel_path, size FROM files WHERE state='present'")}
    for it in items:
        it["imported"] = it["key"] in done or (it["name"].casefold(), it["size"]) in have
    return items


def import_items(repo: Repo, source: dict, keys: list[str], template: str = DEFAULT_TEMPLATE, delete_after: bool = False,
                 helper: DeviceHelper | None = None, progress: Progress | None = None) -> dict:
    from .scan import hash_file

    kind, dev_id, dev_name = source["kind"], source["id"], source.get("name") or "Aygıt"
    items = {i["key"]: i for i in (card_items(Path(source["path"])) if kind == "card" else helper.request("items", device=dev_id, timeout=120)["items"])}
    keys = [k for k in keys if k in items]
    tmp = repo.mm / "tmp" / f"import-{int(time.time() * 1000)}"
    tmp.mkdir(parents=True, exist_ok=True)
    got: dict[str, list[Path]] = {}  # key → [ana dosya, yan dosyalar...]
    failed: list[dict] = []
    try:
        # 1) Geçici klasöre al
        if kind == "card":
            for n, k in enumerate(keys, 1):
                src = Path(source["path"]) / k
                sub = tmp / str(n)
                sub.mkdir()
                try:
                    files = [Path(shutil.copy2(src, sub / src.name))]
                    files += [Path(shutil.copy2(sc, sub / sc.name)) for sc in card_sidecars(src)]
                    got[k] = files
                except OSError as e:
                    failed.append({"file": items[k]["name"], "error": str(e)[:200]})
                if progress:
                    progress("import_copy", n, len(keys))
        else:
            for start in range(0, len(keys), 25):
                batch = keys[start:start + 25]
                sub = tmp / f"b{start}"
                sub.mkdir()
                res = helper.request("download", device=dev_id, keys=batch, dest=str(sub), timeout=1800,
                                     progress=(lambda d, t, s=start: progress("import_copy", s + d, len(keys))) if progress else None)
                for k, r in res["results"].items():
                    if r.get("error") or not r.get("file"):
                        failed.append({"file": items[k]["name"], "error": r.get("error") or "indirilemedi"})
                        continue
                    main = sub / r["file"]
                    side = [p for p in sub.iterdir() if p.suffix.lower() in SIDECAR_EXTS and p.stem.casefold() == main.stem.casefold()]
                    got[k] = [main, *side]
        # 2) Kütüphaneye yerleştir (aynı içerik zaten varsa atla)
        known = {r[0] for r in repo.db.execute("SELECT a.blake3 FROM assets a JOIN files f ON f.asset_id=a.id WHERE f.state='present'")}
        # Klasör için tarih: indirilen dosyanın kendi EXIF/video bilgisi (aygıtın bildirdiği tarihten daha güvenilir)
        from .media import dates, exif

        raws = exif.read_raw([f[0] for f in got.values()])
        for k, files in got.items():
            raw = dict(raws.get(files[0]) or {})
            raw["_video"] = items[k]["kind"] == "video"
            try:
                taken, _tz, src_ = dates.infer(raw, files[0].name, None)
                if taken and src_ != "mtime":
                    items[k]["created"] = taken
            except Exception:  # noqa: BLE001
                pass
        placed, dup, verified = [], 0, []
        for n, (k, files) in enumerate(got.items(), 1):
            main = files[0]
            h = hash_file(main)
            if h in known:
                dup += 1
                verified.append(k)
                rel = None
            else:
                rel = _unique(repo.root, render(template, items[k]["created"], main.name, dev_name))
                dst = repo.root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(main), dst)
                for sc in files[1:]:
                    shutil.move(str(sc), dst.with_name(dst.stem + sc.suffix))
                known.add(h)
                placed.append(rel)
                verified.append(k)
            with repo.db as conn:
                conn.execute("INSERT OR REPLACE INTO device_imports(device, key, blake3, rel_path) VALUES (?,?,?,?)", (dev_id, k, h, rel))
            if progress:
                progress("import_place", n, len(got))
        # 3) Kütüphaneye ekle
        if placed:
            scan.scan(repo)
            analyze.track(repo, placed)
        # 4) İstenirse aygıttan sil (yalnız kopyası doğrulananlar)
        deleted = 0
        if delete_after and verified:
            if kind == "card":
                for k in verified:
                    src = Path(source["path"]) / k
                    for p in [src, *card_sidecars(src), src.with_name("._" + src.name)]:
                        try:
                            p.unlink()
                            deleted += p == src
                        except OSError:
                            pass
            else:
                deleted = int(helper.request("delete", device=dev_id, keys=verified, timeout=600).get("deleted", 0))
        return {"imported": len(placed), "duplicates": dup, "failed": failed, "deleted": deleted, "paths": placed[:50]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = ["render", "mark_new", "import_items", "DEFAULT_TEMPLATE", "os"]
