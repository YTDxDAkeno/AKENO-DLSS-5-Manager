"""Desktop Studio configuration: wallpapers per monitor (and per virtual desktop), the dock, widgets and saved
desktop profiles. One JSON file, ``%LOCALAPPDATA%\\AKENO\\desktop\\desktop.json``, written atomically; the desktop
host process watches it and applies changes live."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from akeno.paths import sub

VERSION = 1

WALLPAPER_DEFAULTS = {
    "enabled": False,
    "playlist": [],             # [{"path": ..., "kind": image|gif|video|web, "minutes": 10}]
    "mode": "single",           # single | rotate | schedule
    "schedule": [],             # [{"from": "06:00", "index": 0}, ...] for mode schedule
    "fit": "fill",              # fill | fit | stretch | center
    "volume": 0,                # 0..100, video and web wallpapers
    "fps_limit": 30,            # web wallpapers: animation frames per second
    "interactive": False,       # web wallpapers follow the mouse pointer on the desktop
    "audio_reactive": False,    # web wallpapers get the playback level (livelyAudioListener / akenoAudio)
    "per_virtual_desktop": {},  # {virtual desktop GUID: playlist index}
}
PAUSE_DEFAULTS = {"game": True, "fullscreen": True, "battery": True, "remote": True, "covered": True,
                  "cpu_percent": 85}
DOCK_DEFAULTS = {"enabled": False, "edge": "bottom", "screen": 0, "floating": False, "position": None,
                 "autohide": False, "opacity": 92, "blur": True, "icon_size": 48, "items": []}
# dock item: {"type": "app", "path": ..., "name": ...} or {"type": "group", "name": ..., "items": [...]}
DEFAULTS = {"version": VERSION, "enabled": True, "wallpapers": {}, "pause": PAUSE_DEFAULTS, "dock": DOCK_DEFAULTS,
            "widgets": [], "widgets_locked": False, "profiles": {}}
# "enabled": False is the Stop button: everything stays configured, nothing is shown until Start
# widget: {"id": "w1", "type": "clock", "x": 40, "y": 40, "w": 260, "h": 140, "screen": 0, "options": {}}
WIDGET_TYPES = ("clock", "calendar", "cpu", "gpu", "temperature", "ram", "vram", "network", "disk", "fps",
                "shortcuts", "music", "visualizer", "notes", "todo", "weather", "uptime", "downloads", "license")


def path() -> Path:
    return sub("desktop") / "desktop.json"


def _merge(defaults: dict, value) -> dict:
    out = copy.deepcopy(defaults)
    if isinstance(value, dict):
        for k, v in value.items():
            if k in defaults and isinstance(defaults[k], dict) and isinstance(v, dict) and defaults[k]:
                out[k] = _merge(defaults[k], v)
            else:
                out[k] = copy.deepcopy(v)          # a copy: editing it never changes the loaded configuration
    return out


def wallpaper(cfg: dict, monitor: str) -> dict:
    return _merge(WALLPAPER_DEFAULTS, cfg.get("wallpapers", {}).get(monitor))


def load(file: Path | None = None) -> dict:
    file = file or path()
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = _merge(DEFAULTS, data if isinstance(data, dict) else {})
    cfg["widgets"] = [w for w in cfg.get("widgets", []) if isinstance(w, dict) and w.get("type") in WIDGET_TYPES]
    return cfg


def save(cfg: dict, file: Path | None = None) -> None:
    file = Path(file or path())
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, file)


def save_profile(cfg: dict, name: str) -> dict:
    """A desktop profile: wallpapers, dock and widgets as they are now (for example "Gaming", "Work")."""
    cfg.setdefault("profiles", {})[name] = {k: copy.deepcopy(cfg[k]) for k in ("wallpapers", "dock", "widgets")}
    return cfg


def apply_profile(cfg: dict, name: str) -> dict:
    profile = cfg.get("profiles", {}).get(name)
    if profile:
        for k in ("wallpapers", "dock", "widgets"):
            cfg[k] = copy.deepcopy(profile.get(k, cfg[k]))
    return cfg
