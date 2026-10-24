"""The wallpaper library: every wallpaper AKENO knows, with title, favourite, collections and media facts.

``%LOCALAPPDATA%\\AKENO\\desktop\\library.json`` (written atomically). Files stay where they are; imports from the
Workshop, themes and packages live in the library folder. The per-monitor playlists in ``desktop.json`` refer to
files by path, so the library can be rebuilt from them at any time (:meth:`Library.sync_from_config`).

* Duplicates are recognised by size and a SHA-256 of the first and last 256 KiB (fast, also for 4K videos).
* Missing files are kept and marked, so a disconnected drive does not wipe the library.
* Folders are imported with their pictures, GIFs, videos and web wallpapers (a folder with ``index.html``).
* Lively wallpaper packages (a ZIP with ``LivelyInfo.json``) are imported when they hold media or a web page
  (types ``picture``, ``gif``, ``video``, ``web``, ``webaudio``); programs, Unity/Godot builds, links and streams are
  refused. Nothing in a package is ever run; ZIP entries are checked like theme archives.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from akeno.desktop import media
from akeno.paths import sub

VERSION = 1
PROBE = 256 * 1024
MAX_PACKAGE_FILES = 2000
MAX_PACKAGE_BYTES = 4 * 2 ** 30
LIVELY_TYPES = {1: "web", 2: "web", 7: "video", 8: "gif", 11: "image",
                "web": "web", "webaudio": "web", "video": "video", "gif": "gif", "picture": "image"}
SORTS = ("recent", "name", "favorites", "kind", "size")


class LibraryError(ValueError):
    pass


@dataclass
class Item:
    id: str
    path: str
    title: str
    kind: str
    added: float = 0.0
    favorite: bool = False
    collections: list = field(default_factory=list)
    size: int = 0
    mtime: float = 0.0
    fingerprint: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0              # seconds (videos, GIFs)
    source: str = "local"              # local | workshop | theme | lively
    author: str = ""
    license: str = ""
    preview: str = ""                  # a picture the package brought along (web wallpapers)

    @property
    def missing(self) -> bool:
        return not Path(self.path).exists()

    @property
    def format(self) -> str:
        p = Path(self.path)
        if self.kind == "web":
            return "HTML"
        return p.suffix.lstrip(".").upper() or self.kind.upper()

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width and self.height else ""


def item_id(path: str | Path) -> str:
    return hashlib.sha1(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()[:16]


def fingerprint(path: Path) -> str:
    """Size plus a hash of the start and the end of the file (a folder: its index.html and file list)."""
    if path.is_dir():
        names = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())[:500]
        h = hashlib.sha256("\n".join(names).encode("utf-8"))
        entry = path / "index.html"
        if entry.is_file():
            h.update(entry.read_bytes()[:PROBE])
        return "dir:" + h.hexdigest()[:24]
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(PROBE))
        if size > 2 * PROBE:
            f.seek(-PROBE, os.SEEK_END)
            h.update(f.read(PROBE))
    return f"{size}:{h.hexdigest()[:24]}"


def default_file() -> Path:
    return sub("desktop") / "library.json"


def library_dir() -> Path:
    return sub("desktop") / "library"


@dataclass
class AddReport:
    added: list = field(default_factory=list)          # Items
    duplicates: list = field(default_factory=list)     # (path, existing Item)
    refused: list = field(default_factory=list)        # (path, reason)


class Library:
    def __init__(self, file: Path | None = None):
        self.file = Path(file or default_file())
        self._items: dict[str, Item] = {}
        self.load()

    # -- storage ---------------------------------------------------------------------------------------------------
    def load(self) -> None:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._items = {}
        known = set(Item.__dataclass_fields__)
        for raw in data.get("items", []) if isinstance(data, dict) else []:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("path"):
                continue
            item = Item(**{k: v for k, v in raw.items() if k in known})
            if item.kind not in media.KINDS:
                continue
            item.collections = [str(c)[:40] for c in item.collections if isinstance(c, str)][:20]
            self._items[item.id] = item

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "items": [asdict(i) for i in self._items.values()]},
                                  indent=1), encoding="utf-8")
        os.replace(tmp, self.file)

    # -- reading ---------------------------------------------------------------------------------------------------
    def items(self) -> list[Item]:
        return list(self._items.values())

    def get(self, item_id_: str) -> Item | None:
        return self._items.get(item_id_)

    def by_path(self, path: str | Path) -> Item | None:
        return self._items.get(item_id(path))

    def collections(self) -> list[str]:
        return sorted({c for i in self._items.values() for c in i.collections}, key=str.casefold)

    def query(self, text: str = "", kind: str = "all", favorites: bool = False, collection: str = "",
              sort: str = "recent") -> list[Item]:
        words = [w for w in text.casefold().split() if w]
        out = []
        for item in self._items.values():
            if kind != "all" and item.kind != kind:
                continue
            if favorites and not item.favorite:
                continue
            if collection and collection not in item.collections:
                continue
            hay = f"{item.title} {Path(item.path).name} {item.author} {' '.join(item.collections)}".casefold()
            if words and not all(w in hay for w in words):
                continue
            out.append(item)
        keys = {"recent": lambda i: -i.added, "name": lambda i: i.title.casefold(),
                "favorites": lambda i: (not i.favorite, -i.added), "kind": lambda i: (i.kind, i.title.casefold()),
                "size": lambda i: -i.size}
        return sorted(out, key=keys.get(sort, keys["recent"]))

    # -- adding ----------------------------------------------------------------------------------------------------
    def add(self, path: str | Path, title: str = "", source: str = "local", author: str = "", license: str = "",
            preview: str = "", report: AddReport | None = None, save: bool = True) -> Item | None:
        report = report if report is not None else AddReport()
        p = Path(path)
        try:
            kind = media.check(p)
        except media.MediaError as exc:
            report.refused.append((str(p), str(exc)))
            return None
        known = self.by_path(p)
        if known is not None:
            report.duplicates.append((str(p), known))
            return known
        try:
            fp = fingerprint(p)
            stat = p.stat()
        except OSError as exc:
            report.refused.append((str(p), str(exc)))
            return None
        same = next((i for i in self._items.values() if i.fingerprint == fp), None)
        if same is not None:
            report.duplicates.append((str(p), same))
            return same
        item = Item(item_id(p), str(p.resolve()), (title or p.stem.replace("_", " ").replace("-", " ")).strip()[:120],
                    kind, time.time(), size=stat.st_size if p.is_file() else 0, mtime=stat.st_mtime,
                    fingerprint=fp, source=source, author=author[:80], license=license[:80], preview=preview)
        self._items[item.id] = item
        report.added.append(item)
        if save:
            self.save()
        return item

    def add_many(self, paths, source: str = "local") -> AddReport:
        report = AddReport()
        for path in paths:
            self.add(path, source=source, report=report, save=False)
        self.save()
        return report

    def add_folder(self, folder: str | Path, recursive: bool = True, limit: int = 2000) -> AddReport:
        """Every wallpaper in a folder; a folder with index.html is one web wallpaper."""
        root = Path(folder)
        if (root / "index.html").is_file():
            return self.add_many([root])
        found: list[Path] = []
        walker = os.walk(root) if recursive else [(str(root), [d.name for d in root.iterdir() if d.is_dir()],
                                                    [f.name for f in root.iterdir() if f.is_file()])]
        for here, dirs, files in walker:
            here_p = Path(here)
            for d in list(dirs):
                if (here_p / d / "index.html").is_file():
                    found.append(here_p / d)
                    dirs.remove(d)                       # its assets are part of the web wallpaper
            for name in files:
                if media.kind_of(name) in ("image", "gif", "video"):
                    found.append(here_p / name)
            if len(found) >= limit:
                break
        return self.add_many(sorted(found)[:limit])

    def import_lively(self, package: str | Path, into: Path | None = None) -> Item:
        """A Lively wallpaper package (ZIP with LivelyInfo.json) holding media or a web page."""
        package = Path(package)
        try:
            z = zipfile.ZipFile(package)
        except (OSError, zipfile.BadZipFile) as exc:
            raise LibraryError(f"{package.name}: not a ZIP archive") from exc
        with z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_PACKAGE_FILES or sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
                raise LibraryError(f"{package.name}: the package is too large")
            names = {}
            for info in infos:
                name = PurePosixPath(info.filename.replace("\\", "/"))
                if name.is_absolute() or ".." in name.parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise LibraryError(f"{package.name}: refused path {info.filename}")
                names[str(name)] = info
            meta_name = next((n for n in names if PurePosixPath(n).name.lower() == "livelyinfo.json"), None)
            if meta_name is None:
                raise LibraryError(f"{package.name}: no LivelyInfo.json (not a Lively wallpaper package)")
            try:
                meta = json.loads(z.read(names[meta_name]).decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise LibraryError(f"{package.name}: LivelyInfo.json is not readable") from exc
            if not isinstance(meta, dict):
                raise LibraryError(f"{package.name}: LivelyInfo.json is not readable")
            raw_type = meta.get("Type")
            kind = LIVELY_TYPES.get(raw_type.lower() if isinstance(raw_type, str) else raw_type)
            if kind is None:
                raise LibraryError(f"{package.name}: this Lively wallpaper type runs a program or a link; AKENO only "
                                   f"imports pictures, GIFs, videos and web pages")
            if meta.get("IsAbsolutePath"):
                raise LibraryError(f"{package.name}: the package points to a file outside itself")
            base = PurePosixPath(meta_name).parent
            entry = str(PurePosixPath(base, str(meta.get("FileName")))) if meta.get("FileName") else ""
            if entry not in names:
                raise LibraryError(f"{package.name}: the wallpaper file named in LivelyInfo.json is missing")
            exts = set().union(*media.KINDS.values()) | {".css", ".js", ".mjs", ".json", ".wasm", ".svg", ".glsl",
                                                         ".frag", ".vert", ".txt", ".md", ".woff", ".woff2",
                                                         ".ttf", ".otf", ".mp3", ".ogg", ".wav"}
            for name in names:
                if PurePosixPath(name).suffix.lower() not in exts:
                    raise LibraryError(f"{package.name}: refused file {name} (only media and web files)")
            if kind != "web" and media.kind_of(entry) != kind:
                raise LibraryError(f"{package.name}: {entry} is not a {kind}")
            target = (into or library_dir()) / f"lively-{hashlib.sha1(package.read_bytes()[:PROBE]).hexdigest()[:10]}"
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for name, info in names.items():
                rel = PurePosixPath(name).relative_to(base) if base != PurePosixPath(".") and \
                    PurePosixPath(name).is_relative_to(base) else PurePosixPath(name)
                dest = target.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
        rel_entry = PurePosixPath(entry).relative_to(base) if base != PurePosixPath(".") else PurePosixPath(entry)
        path = target.joinpath(*rel_entry.parts)
        if kind == "web":
            if path.name.lower() != "index.html":
                (target / "index.html").write_text(
                    f'<!doctype html><meta http-equiv="refresh" content="0; url={rel_entry.as_posix()}">',
                    encoding="utf-8")
            path = target
        preview = ""
        for key in ("Preview", "Thumbnail"):
            name = meta.get(key)
            if isinstance(name, str) and name and not Path(name).is_absolute():
                candidate = target / PurePosixPath(name).name
                if candidate.is_file() and media.kind_of(candidate) in ("image", "gif"):
                    preview = str(candidate)
                    break
        report = AddReport()
        item = self.add(path, str(meta.get("Title") or "")[:120], "lively", str(meta.get("Author") or ""),
                        str(meta.get("License") or ""), preview, report)
        if item is None:
            shutil.rmtree(target, ignore_errors=True)
            raise LibraryError(report.refused[0][1] if report.refused else f"{package.name}: not imported")
        return item

    # -- editing ---------------------------------------------------------------------------------------------------
    def update(self, item_id_: str, **values) -> Item | None:
        item = self._items.get(item_id_)
        if item is None:
            return None
        for key, value in values.items():
            if key in ("title", "favorite", "collections", "width", "height", "duration", "author", "license",
                       "preview"):
                setattr(item, key, value)
        if "title" in values:
            item.title = str(item.title).strip()[:120] or Path(item.path).stem
        self.save()
        return item

    def remove(self, item_id_: str, delete_imported: bool = False) -> None:
        item = self._items.pop(item_id_, None)
        if item is None:
            return
        self.save()
        if delete_imported and Path(item.path).is_relative_to(library_dir()):
            p = Path(item.path)
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)

    def sync_from_config(self, cfg: dict) -> int:
        """Wallpapers that are in a playlist but not yet in the library (also the migration from AKENO 3.0)."""
        n = 0
        for w in cfg.get("wallpapers", {}).values():
            for entry in (w.get("playlist") or []) if isinstance(w, dict) else []:
                path = entry.get("path") if isinstance(entry, dict) else None
                if not path or self.by_path(path) is not None:
                    continue
                p = Path(path)
                kind = entry.get("kind") or media.kind_of(p)
                if kind not in media.KINDS:
                    continue
                if p.exists():
                    if self.add(p, save=False) is not None:
                        n += 1
                else:                                  # kept, marked missing
                    self._items[item_id(p)] = Item(item_id(p), str(p), p.stem, kind, time.time())
                    n += 1
        if n:
            self.save()
        return n
