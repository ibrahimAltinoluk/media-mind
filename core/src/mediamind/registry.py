"""Global repo listesi (menüde görünen repolar).

Konum: ~/Library/Application Support/MediaMind/repos.json
Testlerde ve özel kurulumlarda MEDIAMIND_HOME ile değiştirilebilir.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .paths import MM_DIR


def home() -> Path:
    env = os.environ.get("MEDIAMIND_HOME")
    base = Path(env) if env else Path.home() / "Library" / "Application Support" / "MediaMind"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class RepoEntry:
    id: str
    name: str
    last_path: str
    last_seen: str
    volume_name: str | None = None

    @property
    def online(self) -> bool:
        return (Path(self.last_path) / MM_DIR / "repo.toml").exists()


def _file() -> Path:
    return home() / "repos.json"


def load() -> list[RepoEntry]:
    f = _file()
    if not f.exists():
        return []
    data = json.loads(f.read_text("utf-8"))
    return [RepoEntry(**r) for r in data.get("repos", [])]


def save(entries: list[RepoEntry]) -> None:
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"repos": [asdict(e) for e in entries]}, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)


def _volume_name(path: Path) -> str | None:
    parts = path.resolve().parts
    if len(parts) > 2 and parts[1] == "Volumes":
        return parts[2]
    return None


def register(repo_id: str, name: str, root: Path) -> RepoEntry:
    entries = [e for e in load() if e.id != repo_id]
    entry = RepoEntry(
        id=repo_id,
        name=name,
        last_path=str(root.resolve()),
        last_seen=datetime.now().isoformat(timespec="seconds"),
        volume_name=_volume_name(root),
    )
    entries.append(entry)
    save(entries)
    return entry


def unregister(ref: str) -> RepoEntry | None:
    """Repoyu yalnızca listeden çıkarır; dosyalara ve .mediamind'e dokunmaz."""
    entries = load()
    hit = find(ref, entries)
    if hit:
        save([e for e in entries if e.id != hit.id])
    return hit


def find(ref: str, entries: list[RepoEntry] | None = None) -> RepoEntry | None:
    entries = load() if entries is None else entries
    for e in entries:
        if ref in (e.id, e.name) or e.id.startswith(ref) and len(ref) >= 6:
            return e
    lowered = ref.casefold()
    for e in entries:
        if e.name.casefold() == lowered:
            return e
    return None


def relocate(entry: RepoEntry, search_roots: list[Path] | None = None, max_depth: int = 3) -> Path | None:
    """Disk farklı bir yola bağlandıysa aynı kimlikli repoyu /Volumes altında arar."""
    roots = search_roots if search_roots is not None else [Path("/Volumes")]
    for base in roots:
        if not base.exists():
            continue
        stack = [(base, 0)]
        while stack:
            d, depth = stack.pop()
            toml = d / MM_DIR / "repo.toml"
            if toml.exists():
                try:
                    if tomllib.loads(toml.read_text("utf-8")).get("id") == entry.id:
                        return d
                except (OSError, tomllib.TOMLDecodeError):
                    pass
            if depth >= max_depth:
                continue
            try:
                for child in d.iterdir():
                    if child.is_dir() and not child.name.startswith(".") and not child.is_symlink():
                        stack.append((child, depth + 1))
            except OSError:
                continue
    return None
