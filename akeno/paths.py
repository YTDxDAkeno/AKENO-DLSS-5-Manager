"""Filesystem locations used by AKENO.

All user state lives under one per-user directory (``%LOCALAPPDATA%\\AKENO`` on Windows).
Nothing is written next to the executable, so the app works from Program Files without
administrator rights. ``AKENO_HOME`` overrides the location (used by tests and portable mode).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def resource_dir() -> Path:
    """Directory holding bundled read-only data (works from source and from a PyInstaller build)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "akeno" / "data"
    return Path(__file__).resolve().parent / "data"


def app_home() -> Path:
    override = os.environ.get("AKENO_HOME")
    if override:
        root = Path(override)
    elif is_windows():
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AKENO"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "akeno"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sub(name: str) -> Path:
    p = app_home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_home() / "akeno.sqlite3"


def logs_dir() -> Path:
    return sub("logs")


def cache_dir() -> Path:
    return sub("cache")


def backups_dir() -> Path:
    return sub("backups")


def runtimes_dir() -> Path:
    """User-imported NVIDIA runtime files (never redistributed by AKENO)."""
    return sub("runtimes")


def tools_dir() -> Path:
    return sub("tools")


def benchmarks_dir() -> Path:
    return sub("benchmarks")


def compat_override_path() -> Path:
    """A verified, downloaded compatibility database replaces the bundled one only after validation."""
    return app_home() / "compatibility.json"
