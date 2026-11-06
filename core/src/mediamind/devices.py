"""Telefon / fotoğraf makinesi / hafıza kartı kaynakları.

* iPhone, iPad ve PTP ("Fotoğraf aktarımı") kipindeki Android telefonlar macOS ImageCaptureCore ile okunur.
  ImageCaptureCore bir NSRunLoop ister; sunucunun asyncio döngüsüyle karışmasın diye ayrı bir yardımcı süreçte
  (``python -m mediamind.devices``) çalışır ve satır satır JSON ile konuşur.
* SD kart ve diske bağlanan fotoğraf makineleri /Volumes altında DCIM klasörüyle görünür; dosya sisteminden okunur.

Ortak öğe biçimi: {key, name, size, created (ISO, yerel), kind: photo|video, folder}
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .kinds import SIDECAR_EXTS, kind_of
from .paths import MM_DIR

# ------------------------------------------------------------------ yardımcı süreç (ImageCaptureCore)


def _iso(d) -> str | None:
    if d is None:
        return None
    try:
        return datetime.fromtimestamp(d.timeIntervalSince1970()).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


# Telefonun tüm depolaması görünür (Android PTP): uygulama önbellekleri, küçük resim klasörleri, çöp kutusu...
# Yalnızca kullanıcının gerçek fotoğraf/videoları içe aktarma listesine girer.
_SKIP_TOP = {"android/data", "android/obb"}
MIN_PHOTO_BYTES = 6 * 1024  # simge/önizleme kırıntıları


def device_folder(parts: list[str]) -> str:
    return "/".join(p for p in parts if p)


def is_user_media(folder: str, name: str, size: int) -> str | None:
    """Aygıttaki bir dosya içe aktarılmaya değer gerçek medya mı? Değerse türünü döndürür."""
    kind = kind_of(name)
    if kind is None or name.startswith("."):
        return None
    parts = [p for p in folder.split("/") if p]
    if any(p.startswith(".") for p in parts):  # .thumbnails, .Trash, WhatsApp .Links/.Statuses ...
        return None
    low = [p.lower() for p in parts]
    if any(f"{a}/{b}" in _SKIP_TOP for a, b in zip(low, low[1:])):  # uygulama önbellekleri (kök depolama adı olsa da olmasa da)
        return None
    if kind == "photo" and 0 < size < MIN_PHOTO_BYTES:
        return None
    return kind


def helper_main() -> None:  # pragma: no cover - gerçek aygıt gerektirir
    import ImageCaptureCore as IC
    from Foundation import NSURL, NSDate, NSObject, NSRunLoop

    out_lock = threading.Lock()

    def emit(obj: dict) -> None:
        with out_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    devices: dict[str, object] = {}
    ready: set[str] = set()
    files: dict[str, dict[str, object]] = {}

    def dev_id(d) -> str:
        return str(d.persistentIDString() or d.UUIDString())

    class Delegate(NSObject):
        def deviceBrowser_didAddDevice_moreComing_(self, b, d, more):
            i = dev_id(d)
            devices[i] = d
            d.setDelegate_(self)
            d.requestOpenSession()
            emit({"event": "added", "id": i})

        def deviceBrowser_didRemoveDevice_moreGoing_(self, b, d, more):
            i = dev_id(d)
            devices.pop(i, None)
            ready.discard(i)
            files.pop(i, None)
            emit({"event": "removed", "id": i})

        def deviceBrowserDidEnumerateLocalDevices_(self, b):
            emit({"event": "enumerated"})

        def device_didOpenSessionWithError_(self, d, err):
            emit({"event": "session", "id": dev_id(d), "error": str(err) if err else None})

        def deviceDidBecomeReadyWithCompleteContentCatalog_(self, d):
            i = dev_id(d)
            ready.add(i)
            emit({"event": "ready", "id": i})

        def didRemoveDevice_(self, d):
            pass

        def device_didCloseSessionWithError_(self, d, err):
            pass

        def device_didReceiveStatusInformation_(self, d, info):
            pass

        def cameraDevice_didAddItems_(self, d, items):
            files.pop(dev_id(d), None)

        def cameraDevice_didRemoveItems_(self, d, items):
            files.pop(dev_id(d), None)

        def cameraDevice_didRenameItems_(self, d, items):
            pass

        def cameraDeviceDidChangeCapability_(self, d):
            pass

        def cameraDevice_didReceiveThumbnail_forItem_error_(self, d, t, i, e):
            pass

        def cameraDevice_didReceiveMetadata_forItem_error_(self, d, m, i, e):
            pass

        def cameraDevice_didReceivePTPEvent_(self, d, e):
            pass

        def cameraDeviceDidRemoveAccessRestriction_(self, d):
            emit({"event": "unlocked", "id": dev_id(d)})

        def cameraDeviceDidEnableAccessRestriction_(self, d):
            emit({"event": "locked", "id": dev_id(d)})

    delegate = Delegate.alloc().init()
    browser = IC.ICDeviceBrowser.alloc().init()
    browser.setDelegate_(delegate)
    browser.setBrowsedDeviceTypeMask_(IC.ICDeviceTypeMaskCamera | IC.ICDeviceLocationTypeMaskLocal)
    browser.start()

    def file_map(i: str) -> dict[str, object]:
        raw = devices[i].mediaFiles() or []
        # Katalog hazır olduktan sonra da dolmaya devam edebilir (Android): ham sayı değişince yeniden kur
        if i not in files or files[i][0] != len(raw):
            m = {}
            for f in raw:
                name = str(f.name())
                parts, p = [], f.parentFolder()
                while p is not None:
                    parts.append(str(p.name() or ""))
                    p = p.parentFolder()
                folder = device_folder(list(reversed(parts)))
                size = int(f.fileSize())
                kind = is_user_media(folder, name, size)
                if kind is None:
                    continue
                created = _iso(f.exifCreationDate() or f.creationDate())
                m[f"{name}|{size}|{created}"] = (f, folder, kind, created)
            files[i] = (len(raw), m)
        return files[i][1]

    def describe(i: str) -> dict:
        d = devices[i]
        locked = bool(d.isLocked()) if hasattr(d, "isLocked") else False
        return {"id": i, "name": str(d.name() or "Aygıt"), "product": str(d.productKind() or ""), "ready": i in ready,
                "transport": str(d.transportType() or ""),
                "locked": locked, "count": len(file_map(i)) if i in ready else None}

    commands: queue.Queue = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            try:
                commands.put(json.loads(line))
            except ValueError:
                pass
        os._exit(0)  # sunucu kapandı

    threading.Thread(target=reader, daemon=True).start()
    pending: dict[int, dict] = {}

    def handle(c: dict) -> None:
        rid, op = c.get("rid"), c.get("op")
        try:
            if op == "list":
                emit({"rid": rid, "ok": True, "devices": [describe(i) for i in devices]})
            elif op == "items":
                i = c["device"]
                if i not in ready:
                    emit({"rid": rid, "ok": False, "error": "Aygıt hazır değil"})
                    return
                items = []
                for key, (f, folder, kind, created) in file_map(i).items():
                    dur = None
                    if kind == "video":
                        try:
                            dur = int(float(f.duration()) * 1000) or None
                        except Exception:  # noqa: BLE001
                            pass
                    items.append({"key": key, "name": str(f.name()), "size": int(f.fileSize()),
                                  "created": created, "kind": kind, "folder": folder, "duration_ms": dur})
                emit({"rid": rid, "ok": True, "items": items})
            elif op == "thumb":
                entry = file_map(c["device"]).get(c["key"])
                if entry is None:
                    emit({"rid": rid, "ok": False, "error": "Öğe yok"})
                    return
                f = entry[0]

                def got(data, err):
                    emit({"rid": rid, "ok": data is not None, "data": base64.b64encode(bytes(data)).decode() if data else None})
                f.requestThumbnailDataWithOptions_completion_({}, got)
            elif op == "read":
                # Android (PTP) küçük resim vermez: dosyanın istenen parçaları okunup seyrek bir dosyaya yazılır
                entry = file_map(c["device"]).get(c["key"])
                if entry is None:
                    emit({"rid": rid, "ok": False, "error": "Öğe yok"})
                    return
                f = entry[0]
                size = int(f.fileSize())
                ranges = [(max(0, int(o)), min(int(n), size - max(0, int(o)))) for o, n in c["ranges"]]
                ranges = [(o, n) for o, n in ranges if n > 0]
                with open(c["dest"], "wb") as fh:
                    fh.truncate(size)
                st = {"left": len(ranges), "err": None}
                if not ranges:
                    emit({"rid": rid, "ok": True, "size": size})

                def chunk(data, err, off=0):
                    if data is not None:
                        with open(c["dest"], "r+b") as fh:
                            fh.seek(off)
                            fh.write(bytes(data))
                    elif err is not None:
                        st["err"] = str(err)
                    st["left"] -= 1
                    if st["left"] == 0:
                        emit({"rid": rid, "ok": st["err"] is None, "size": size, "error": st["err"]})
                for o, n in ranges:
                    f.requestReadDataAtOffset_length_completion_(o, n, lambda d, e, o=o: chunk(d, e, o))
            elif op == "download":
                fm = file_map(c["device"])
                keys, dest = c["keys"], c["dest"]
                state = {"left": len(keys), "results": {}}
                pending[rid] = state
                if not keys:
                    emit({"rid": rid, "ok": True, "results": {}})
                for k in keys:
                    f = (fm.get(k) or (None,))[0]
                    if f is None:
                        state["results"][k] = {"error": "Öğe artık aygıtta değil"}
                        state["left"] -= 1
                        continue
                    opts = {IC.ICDownloadsDirectoryURL: NSURL.fileURLWithPath_(dest), IC.ICOverwrite: True,
                            IC.ICDownloadSidecarFiles: True}

                    def done(filename, err, k=k):
                        state["results"][k] = {"file": str(filename) if filename else None, "error": str(err) if err else None}
                        state["left"] -= 1
                        emit({"event": "progress", "rid": rid, "done": len(keys) - state["left"], "total": len(keys)})
                        if state["left"] <= 0:
                            emit({"rid": rid, "ok": True, "results": state["results"]})
                            pending.pop(rid, None)
                    f.requestDownloadWithOptions_completion_(opts, done)
                if keys and state["left"] <= 0:
                    emit({"rid": rid, "ok": True, "results": state["results"]})
            elif op == "delete":
                fm = file_map(c["device"])
                fs = [fm[k][0] for k in c["keys"] if k in fm]
                d = devices[c["device"]]

                def failed(items):
                    emit({"event": "delete_failed", "rid": rid, "count": len(items or [])})

                def finished(err):
                    files.pop(c["device"], None)
                    emit({"rid": rid, "ok": err is None, "deleted": len(fs), "error": str(err) if err else None})
                d.requestDeleteFiles_deleteFailed_completion_(fs, failed, finished)
            else:
                emit({"rid": rid, "ok": False, "error": f"Bilinmeyen komut: {op}"})
        except Exception as e:  # noqa: BLE001
            emit({"rid": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})

    loop = NSRunLoop.currentRunLoop()
    while True:
        try:
            while True:
                handle(commands.get_nowait())
        except queue.Empty:
            pass
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))


class DeviceHelper:
    """Sunucu tarafı: yardımcı süreci başlatır, istek gönderir, yanıtı bekler."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._waiters: dict[int, tuple[threading.Event, list]] = {}
        self._progress: dict[int, callable] = {}
        self._mx = threading.Lock()

    def _ensure(self) -> None:
        with self._mx:
            if self._proc and self._proc.poll() is None:
                return
            self._proc = subprocess.Popen([sys.executable, "-m", "mediamind.devices"], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()
            time.sleep(0.8)  # tarayıcının yerel aygıtları sayması için kısa süre

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            rid = msg.get("rid")
            if msg.get("event") == "progress" and rid in self._progress:
                self._progress[rid](msg["done"], msg["total"])
            elif rid is not None and "event" not in msg and rid in self._waiters:
                ev, box = self._waiters[rid]
                box.append(msg)
                ev.set()

    def request(self, op: str, timeout: float = 30, progress=None, **kw) -> dict:
        self._ensure()
        rid = next(self._ids)
        ev, box = threading.Event(), []
        self._waiters[rid] = (ev, box)
        if progress:
            self._progress[rid] = progress
        try:
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps({"rid": rid, "op": op, **kw}) + "\n")
            self._proc.stdin.flush()
            if not ev.wait(timeout):
                raise TimeoutError("Aygıt yanıt vermedi")
            msg = box[0]
            if not msg.get("ok"):
                raise RuntimeError(msg.get("error") or "Aygıt işlemi başarısız")
            return msg
        finally:
            self._waiters.pop(rid, None)
            self._progress.pop(rid, None)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


# ------------------------------------------------------------------ hafıza kartları (dosya sistemi)


THUMB_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "mediamind-device-thumbs"
PHOTO_FULL_READ_MAX = 60 * 1024 * 1024
VIDEO_HEAD, VIDEO_TAIL = 8 * 1024 * 1024, 6 * 1024 * 1024
_NO_ICC_THUMBS: set[str] = set()  # küçük resim vermeyen aygıtlar (Android PTP)


def phone_thumb(helper: "DeviceHelper", device: str, key: str, size: int = 480) -> bytes | None:
    """Telefon öğesinin küçük resmi (JPEG). Önce aygıtın kendi küçük resmi; Android PTP vermezse dosyanın
    okunur: fotoğrafta dosyanın tamamı (EXIF yönü uygulanır), videoda baş + son (moov) ile ilk kare.
    Sonuç diskte önbelleğe alınır."""
    import hashlib
    import io

    from PIL import Image

    from .media import thumbs

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    cache = THUMB_DIR / (hashlib.sha1(f"v2|{device}|{key}|{size}".encode()).hexdigest() + ".jpg")
    if cache.exists():
        return cache.read_bytes()

    def finish(im: Image.Image) -> bytes:
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        data = buf.getvalue()
        cache.write_bytes(data)
        return data

    if device not in _NO_ICC_THUMBS:
        try:
            r = helper.request("thumb", device=device, key=key, timeout=20)
            if r.get("data"):
                from PIL import ImageOps

                return finish(ImageOps.exif_transpose(Image.open(io.BytesIO(base64.b64decode(r["data"])))))
        except Exception:  # noqa: BLE001 - Android'de beklenen; parça okumaya geç
            _NO_ICC_THUMBS.add(device)
    name, fsize = key.split("|")[0], int(key.split("|")[1])
    kind = kind_of(name)
    tmp = THUMB_DIR / (cache.stem + Path(name).suffix.lower())
    try:
        if kind == "photo":
            # Gömülü EXIF küçük resmi kullanılmaz: çoğu telefonda sabit 4:3 ve düşük çözünürlüklüdür (esnemiş/ters görünür).
            # Dosyanın tamamı okunur (USB'de ~20 MB/sn) ve asıl görüntü, EXIF yönü uygulanarak küçültülür.
            if fsize > PHOTO_FULL_READ_MAX:
                return None
            helper.request("read", device=device, key=key, dest=str(tmp), ranges=[[0, fsize]], timeout=120)
            return finish(thumbs.open_visual(tmp, "photo"))
        if kind == "video":
            ranges = [[0, VIDEO_HEAD]] + ([[fsize - VIDEO_TAIL, VIDEO_TAIL]] if fsize > VIDEO_HEAD else [])
            helper.request("read", device=device, key=key, dest=str(tmp), ranges=ranges, timeout=120)
            return finish(thumbs.open_visual(tmp, "video"))
    except Exception:  # noqa: BLE001
        return None
    finally:
        tmp.unlink(missing_ok=True)
    return None


def card_volumes(exclude_roots: list[Path], volumes_dir: Path = Path("/Volumes")) -> list[dict]:
    """DCIM klasörü olan bağlı diskler (SD kart, fotoğraf makinesi). Kütüphane olarak eklenmiş diskler hariç."""
    out = []
    try:
        vols = [v for v in volumes_dir.iterdir() if v.is_dir() and not v.is_symlink()]
    except OSError:
        return out
    for v in vols:
        dcim = v / "DCIM"
        if not dcim.is_dir():
            continue
        if any(r == v or v in r.parents or r in v.parents for r in exclude_roots):
            continue
        out.append({"id": f"vol:{v}", "name": v.name, "product": "Hafıza kartı", "ready": True, "locked": False,
                    "count": None, "kind": "card", "path": str(dcim)})
    return out


def card_items(dcim: Path, with_dates: bool = True) -> list[dict]:
    """Karttaki medya; çekim tarihi EXIF/video bilgisinden (yoksa dosya tarihinden)."""
    items = []
    for dirpath, dirnames, filenames in os.walk(dcim):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for n in filenames:
            k = kind_of(n)
            if not k:
                continue
            p = Path(dirpath) / n
            try:
                st = p.stat()
            except OSError:
                continue
            created = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            items.append({"key": str(p.relative_to(dcim)), "name": n, "size": st.st_size, "created": created, "kind": k,
                          "_path": p, "_mtime_ns": st.st_mtime_ns})
    if with_dates and items:
        from .media import dates, exif

        raws = exif.read_raw([i["_path"] for i in items])
        for i in items:
            raw = dict(raws.get(i["_path"]) or {})
            raw["_video"] = i["kind"] == "video"
            try:
                taken, _tz, src = dates.infer(raw, i["name"], i["_mtime_ns"])
                if taken and src != "mtime":
                    i["created"] = taken
            except Exception:  # noqa: BLE001
                pass
    for i in items:
        i.pop("_path"), i.pop("_mtime_ns")
    return items


def card_sidecars(src: Path) -> list[Path]:
    stem = src.stem.casefold()
    return [p for p in src.parent.iterdir() if p.suffix.lower() in SIDECAR_EXTS and p.stem.casefold() == stem]


def is_internal(path: str) -> bool:
    return f"/{MM_DIR}/" in path


if __name__ == "__main__":  # pragma: no cover
    helper_main()
