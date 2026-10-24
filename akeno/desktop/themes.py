"""Theme Workshop: a desktop theme as one file (``.akenotheme``, a ZIP archive) that others can import.

    theme.json      name, author, description, license, the wallpaper playlist, dock style, widget layout
    preview.png     optional picture for the library
    media/...       the wallpaper files (images, GIFs, videos, web wallpaper folders)

A theme is data, never a program. Import accepts only media, web wallpaper files (which run in the sandboxed
renderer without network or file access outside their folder) and fonts; executables, scripts for Windows,
shortcuts, links outside the archive, absolute paths, ``..`` and archives that unpack to far more than they
weigh are refused before anything is written. Dock pins are personal paths and never part of a theme.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from akeno.desktop import config as cfgmod
from akeno.desktop import media
from akeno.paths import sub

FORMAT = "akeno-theme"
VERSION = 1
SUFFIX = ".akenotheme"
MAX_FILES = 5000
MAX_TOTAL = 4 * 2 ** 30
MAX_RATIO = 200
WEB_EXTS = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".wasm", ".glsl", ".frag", ".vert", ".txt", ".md",
            ".svg", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".ogg", ".wav", ".m4a", ".flac"}
MEDIA_EXTS = set().union(*media.KINDS.values()) | WEB_EXTS
TOP_LEVEL = {"theme.json", "preview.png", "LICENSE", "LICENSE.txt", "README.md", "README.txt"}
DOCK_STYLE = ("edge", "floating", "autohide", "opacity", "blur", "icon_size")
WALLPAPER_KEYS = ("mode", "schedule", "fit", "volume", "fps_limit", "interactive", "audio_reactive")
TEXT_LIMITS = {"name": 60, "author": 60, "description": 600, "license": 60, "version": 20}


class ThemeError(ValueError):
    pass


@dataclass
class Theme:
    id: str
    name: str
    author: str = ""
    description: str = ""
    license: str = ""
    version: str = "1.0"
    created: str = ""
    folder: Path | None = None
    preview: Path | None = None
    manifest: dict = field(default_factory=dict)

    @property
    def files(self) -> int:
        return len(self.manifest.get("wallpaper", {}).get("playlist", []))


def library_dir() -> Path:
    return sub("themes")


def slug(name: str, salt: str = "") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "theme"
    return f"{base}-{hashlib.sha256((name + salt).encode()).hexdigest()[:6]}"


# -- export -----------------------------------------------------------------------------------------------------------
def _media_files(item_path: Path) -> list[tuple[Path, str]]:
    """(file on disk, path inside media/<folder>) for one playlist entry."""
    if item_path.is_dir():
        out = []
        for f in sorted(item_path.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.suffix.lower() in MEDIA_EXTS:
                out.append((f, f.relative_to(item_path).as_posix()))
        return out
    return [(item_path, item_path.name)]


def export(cfg: dict, monitor: str, out_file: Path, name: str, author: str = "", description: str = "",
           license: str = "", preview_png: bytes | None = None, include_widgets: bool = True,
           include_dock: bool = True) -> Path:
    """Write the monitor's wallpapers (with their files), the dock style and the widget layout as a theme."""
    name = name.strip()[:TEXT_LIMITS["name"]]
    if not name:
        raise ThemeError("A theme needs a name.")
    wcfg = cfgmod.wallpaper(cfg, monitor)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {"format": FORMAT, "version": VERSION, "id": slug(name, author + created), "name": name,
                "author": author.strip()[:60], "description": description.strip()[:600],
                "license": license.strip()[:60], "theme_version": "1.0", "created": created,
                "wallpaper": {k: copy.deepcopy(wcfg[k]) for k in WALLPAPER_KEYS}, "preview": None}
    manifest["wallpaper"]["playlist"] = []
    entries: list[tuple[Path, str]] = []
    total = 0
    for i, item in enumerate(wcfg.get("playlist", [])):
        src = Path(item["path"])
        kind = media.check(src)
        folder = f"media/{i + 1:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '_', src.stem)[:40]}"
        files = _media_files(src)
        if not files:
            raise ThemeError(f"{src}: nothing to export")
        for f, rel in files:
            total += f.stat().st_size
            entries.append((f, f"{folder}/{rel}"))
        target = folder if src.is_dir() else f"{folder}/{src.name}"
        manifest["wallpaper"]["playlist"].append({"file": target, "kind": kind,
                                                  "minutes": int(item.get("minutes", 10))})
    if len(entries) > MAX_FILES or total > MAX_TOTAL:
        raise ThemeError("The wallpapers are too large for a theme (at most 5000 files and 4 GB).")
    if include_dock:
        manifest["dock"] = {k: cfg.get("dock", {}).get(k) for k in DOCK_STYLE if k in cfg.get("dock", {})}
    if include_widgets:
        manifest["widgets"] = [dict(w, options={} if w.get("type") in ("notes", "todo", "shortcuts", "downloads")
                                    else w.get("options", {})) for w in cfg.get("widgets", [])]
    if preview_png:
        manifest["preview"] = "preview.png"
    out_file = Path(out_file)
    if out_file.suffix.lower() != SUFFIX:
        out_file = out_file.with_suffix(SUFFIX)
    tmp = out_file.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("theme.json", json.dumps(manifest, indent=1, ensure_ascii=False))
        if preview_png:
            z.writestr("preview.png", preview_png)
        for f, arc in entries:
            compress = zipfile.ZIP_STORED if f.suffix.lower() in media.KINDS["video"] | {".jpg", ".jpeg", ".png",
                                                                                         ".webp", ".gif"} \
                else zipfile.ZIP_DEFLATED
            z.write(f, arc, compress_type=compress)
    tmp.replace(out_file)
    return out_file


# -- import -----------------------------------------------------------------------------------------------------------
def _safe_name(name: str) -> PurePosixPath:
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        raise ThemeError(f"refused path in the theme: {name}")
    path = PurePosixPath(name)
    if any(part in ("..", "") for part in path.parts) or len(name) > 400:
        raise ThemeError(f"refused path in the theme: {name}")
    return path


def _text(manifest: dict, key: str) -> str:
    value = manifest.get(key, "")
    if not isinstance(value, str):
        raise ThemeError(f"theme.json: {key} must be text")
    return value.strip()[:TEXT_LIMITS.get(key, 200)]


def check_archive(file: Path) -> dict:
    """Validate a theme file without unpacking it; returns its theme.json. Raises ThemeError."""
    if not zipfile.is_zipfile(file):
        raise ThemeError("Not an AKENO theme (no ZIP archive).")
    with zipfile.ZipFile(file) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES + len(TOP_LEVEL):
            raise ThemeError("The theme has too many files.")
        names = set()
        total = 0
        for info in infos:
            path = _safe_name(info.filename)
            if info.is_dir():
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ThemeError(f"refused link in the theme: {info.filename}")
            top = len(path.parts) == 1
            if top and info.filename not in TOP_LEVEL:
                raise ThemeError(f"refused file in the theme: {info.filename}")
            if not top and (path.parts[0] != "media" or path.suffix.lower() not in MEDIA_EXTS):
                raise ThemeError(f"refused file type in the theme: {info.filename}")
            total += info.file_size
            if total > MAX_TOTAL:
                raise ThemeError("The theme unpacks to more than 4 GB.")
            if info.file_size > 10 * 2 ** 20 and info.compress_size and \
                    info.file_size / info.compress_size > MAX_RATIO:
                raise ThemeError(f"refused file in the theme (unusual compression): {info.filename}")
            names.add(path.as_posix())
        if "theme.json" not in names:
            raise ThemeError("The theme has no theme.json.")
        info = z.getinfo("theme.json")
        if info.file_size > 2 ** 20:
            raise ThemeError("theme.json is too large.")
        try:
            manifest = json.loads(z.read("theme.json").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ThemeError(f"theme.json is not valid: {exc}") from exc
    return validate_manifest(manifest, names)


def validate_manifest(manifest, names: set[str]) -> dict:
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ThemeError("theme.json is not an AKENO theme.")
    if not isinstance(manifest.get("version"), int) or manifest["version"] > VERSION:
        raise ThemeError("This theme needs a newer AKENO.")
    clean = {"format": FORMAT, "version": VERSION}
    for key in ("name", "author", "description", "license", "created"):
        clean[key] = _text(manifest, key)
    clean["theme_version"] = _text(manifest, "theme_version") if "theme_version" in manifest else "1.0"
    if not clean["name"]:
        raise ThemeError("theme.json: the theme has no name.")
    clean["id"] = slug(clean["name"], clean["author"] + clean["created"])
    wp = manifest.get("wallpaper") or {}
    if not isinstance(wp, dict):
        raise ThemeError("theme.json: wallpaper must be an object")
    out_wp = {}
    defaults = cfgmod.WALLPAPER_DEFAULTS
    out_wp["mode"] = wp.get("mode") if wp.get("mode") in ("single", "rotate", "schedule") else "single"
    out_wp["fit"] = wp.get("fit") if wp.get("fit") in ("fill", "fit", "stretch", "center") else "fill"
    for key, low, high in (("volume", 0, 100), ("fps_limit", 5, 144)):
        value = wp.get(key, defaults[key])
        out_wp[key] = max(low, min(high, int(value))) if isinstance(value, (int, float)) else defaults[key]
    for key in ("interactive", "audio_reactive"):
        out_wp[key] = bool(wp.get(key, False))
    playlist = []
    for item in (wp.get("playlist") or [])[:200]:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ThemeError("theme.json: a playlist entry has no file")
        rel = _safe_name(item["file"]).as_posix()
        if not rel.startswith("media/"):
            raise ThemeError(f"theme.json: {rel} is not inside media/")
        is_dir = f"{rel}/index.html" in names
        if not is_dir and rel not in names:
            raise ThemeError(f"theme.json: {rel} is missing from the theme")
        kind = "web" if is_dir else media.kind_of(rel)
        if kind is None or item.get("kind", kind) != kind:
            raise ThemeError(f"theme.json: {rel} is not a supported wallpaper")
        minutes = item.get("minutes", 10)
        playlist.append({"file": rel, "kind": kind,
                         "minutes": max(1, min(24 * 60, int(minutes))) if isinstance(minutes, (int, float)) else 10})
    out_wp["playlist"] = playlist
    schedule = []
    for entry in (wp.get("schedule") or [])[:48]:
        if isinstance(entry, dict) and isinstance(entry.get("from"), str) and \
                re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", entry["from"]) and \
                isinstance(entry.get("index"), int) and 0 <= entry["index"] < len(playlist):
            schedule.append({"from": entry["from"], "index": entry["index"]})
    out_wp["schedule"] = schedule
    clean["wallpaper"] = out_wp
    dock = manifest.get("dock")
    if isinstance(dock, dict):
        out_dock = {}
        if dock.get("edge") in ("bottom", "top", "left", "right"):
            out_dock["edge"] = dock["edge"]
        for key in ("floating", "autohide", "blur"):
            if isinstance(dock.get(key), bool):
                out_dock[key] = dock[key]
        for key, low, high in (("opacity", 30, 100), ("icon_size", 24, 96)):
            if isinstance(dock.get(key), int):
                out_dock[key] = max(low, min(high, dock[key]))
        clean["dock"] = out_dock
    widgets = manifest.get("widgets")
    if isinstance(widgets, list):
        from akeno.desktop.widgets import import_layout
        with tempfile.TemporaryDirectory() as tmp:
            layout = Path(tmp) / "w.json"
            layout.write_text(json.dumps({"format": "akeno-widgets", "widgets": widgets}), encoding="utf-8")
            clean["widgets"] = import_layout(layout)
    preview = manifest.get("preview")
    clean["preview"] = "preview.png" if preview == "preview.png" and "preview.png" in names else None
    return clean


def install(file: Path, replace: bool = False, root: Path | None = None) -> Theme:
    """Check the theme, unpack it into the library and return it."""
    root = Path(root or library_dir())
    manifest = check_archive(file)
    target = root / manifest["id"]
    if target.exists() and not replace:
        raise FileExistsError(manifest["id"])
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
    try:
        with zipfile.ZipFile(file) as z:
            for info in z.infolist():
                if info.is_dir() or info.filename == "theme.json":
                    continue
                dest = staging.joinpath(*_safe_name(info.filename).parts)
                if not dest.resolve().is_relative_to(staging.resolve()):
                    raise ThemeError(f"refused path in the theme: {info.filename}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        (staging / "theme.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return load(target)


def load(folder: Path) -> Theme:
    manifest = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
    preview = folder / "preview.png" if manifest.get("preview") and (folder / "preview.png").is_file() else None
    return Theme(id=folder.name, name=manifest.get("name", folder.name), author=manifest.get("author", ""),
                 description=manifest.get("description", ""), license=manifest.get("license", ""),
                 version=manifest.get("theme_version", "1.0"), created=manifest.get("created", ""),
                 folder=folder, preview=preview, manifest=manifest)


def library(root: Path | None = None) -> list[Theme]:
    root = Path(root or library_dir())
    out = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if folder.is_dir() and not folder.name.startswith(".") and (folder / "theme.json").is_file():
            try:
                out.append(load(folder))
            except (OSError, ValueError):
                continue
    return out


def remove(theme: Theme, root: Path | None = None) -> None:
    """Delete an imported theme (only a folder of the theme library)."""
    root = Path(root or library_dir()).resolve()
    if theme.folder and theme.folder.resolve().parent == root and (theme.folder / "theme.json").is_file():
        shutil.rmtree(theme.folder)


def apply(theme: Theme, cfg: dict, monitors: list[str], widgets: bool = True, dock: bool = True) -> dict:
    """Use the theme: its playlist (files from the library folder) on these monitors, its dock style (the pins
    stay yours) and, if wanted, its widget layout."""
    wp = theme.manifest.get("wallpaper", {})
    playlist = [{"path": str(theme.folder.joinpath(*PurePosixPath(i["file"]).parts)), "kind": i["kind"],
                 "minutes": i.get("minutes", 10)} for i in wp.get("playlist", [])]
    for monitor in monitors:
        current = cfgmod.wallpaper(cfg, monitor)
        current.update({k: copy.deepcopy(wp[k]) for k in WALLPAPER_KEYS if k in wp})
        current.update(playlist=copy.deepcopy(playlist), enabled=bool(playlist), per_virtual_desktop={})
        cfg.setdefault("wallpapers", {})[monitor] = current
    if dock and theme.manifest.get("dock"):
        cfg["dock"] = dict(cfg.get("dock", {}), **theme.manifest["dock"])
    if widgets and theme.manifest.get("widgets"):
        cfg["widgets"] = copy.deepcopy(theme.manifest["widgets"])
    cfg["theme"] = theme.id
    return cfg
