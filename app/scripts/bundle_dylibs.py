#!/usr/bin/env python3
"""Homebrew'dan gelen bir aracı (ffmpeg, ffprobe) bağımlı dylib'leriyle birlikte taşınabilir hale getirir.

Kullanım: bundle_dylibs.py <hedef_kök> <ikili> [<ikili> ...]
  <hedef_kök>/bin/<ikili>  ve  <hedef_kök>/lib/*.dylib  üretilir; yollar @executable_path/../lib ve
  @loader_path'e çevrilir, her dosya ad-hoc imzalanır. Sistem kütüphanelerine (/usr/lib, /System) dokunulmaz.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM = ("/usr/lib/", "/System/")


def deps(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    return [line.strip().split(" (")[0] for line in out if line.strip()]


def rpaths(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True).stdout.splitlines()
    res = []
    for i, line in enumerate(out):
        if "LC_RPATH" in line:
            for j in range(i, min(i + 4, len(out))):
                if out[j].strip().startswith("path "):
                    res.append(out[j].strip().split(" ")[1])
    return res


def resolve(ref: str, owner: Path) -> Path | None:
    if ref.startswith(SYSTEM):
        return None
    if ref.startswith("@loader_path/") or ref.startswith("@executable_path/"):
        return (owner.parent / ref.split("/", 1)[1]).resolve()
    if ref.startswith("@rpath/"):
        for rp in rpaths(owner):
            base = rp.replace("@loader_path", str(owner.parent)).replace("@executable_path", str(owner.parent))
            cand = Path(base) / ref.split("/", 1)[1]
            if cand.exists():
                return cand.resolve()
        cand = Path("/opt/homebrew/lib") / ref.split("/", 1)[1]
        return cand.resolve() if cand.exists() else None
    p = Path(ref)
    return p.resolve() if p.exists() else None


def main() -> None:
    root = Path(sys.argv[1])
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "lib").mkdir(parents=True, exist_ok=True)
    todo: list[tuple[Path, Path]] = []  # (kaynak, hedef)
    for b in sys.argv[2:]:
        src = Path(shutil.which(b) or b).resolve()
        todo.append((src, root / "bin" / b))
    seen: dict[Path, Path] = {}
    changes: dict[Path, list[tuple[str, str]]] = {}
    i = 0
    while i < len(todo):
        src, dst = todo[i]
        i += 1
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        changes[dst] = []
        is_bin = dst.parent.name == "bin"
        for ref in deps(src):
            real = resolve(ref, src)
            if real is None:
                continue
            name = real.name
            if real not in seen:
                seen[real] = root / "lib" / name
                todo.append((real, root / "lib" / name))
            new = f"@executable_path/../lib/{name}" if is_bin else f"@loader_path/{name}"
            if ref != new:
                changes[dst].append((ref, new))
    for dst, ch in changes.items():
        args = ["install_name_tool"]
        if dst.parent.name == "lib":
            args += ["-id", f"@loader_path/{dst.name}"]
        for old, new in ch:
            args += ["-change", old, new]
        for rp in rpaths(dst):
            args += ["-delete_rpath", rp]
        if len(args) > 1:
            subprocess.run([*args, str(dst)], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dst)], check=True, capture_output=True)
    print(f"{len(changes)} dosya paketlendi → {root}")


if __name__ == "__main__":
    main()
