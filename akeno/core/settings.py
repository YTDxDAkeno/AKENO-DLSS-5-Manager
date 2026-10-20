"""Typed access to persisted user settings with defaults."""

from __future__ import annotations

from typing import Any, Callable

from akeno.core.db import Database

DEFAULTS: dict[str, Any] = {
    "language": "auto",                 # auto | en | de
    "theme_accent": "violet",           # violet | blue | cyan
    "animations": True,
    "ui_scale": 1.0,
    "launch_with_windows": False,
    "check_app_updates": False,         # off by default: AKENO does not phone home unless asked
    "auto_update_compat_db": False,
    "compat_db_url": "https://raw.githubusercontent.com/YTDxDAkeno/AKENO-DLSS-5-Manager/main/akeno/data/compatibility.json",
    "cache_limit_mb": 1024,
    "backup_keep_per_game": 10,
    "verify_backups_after_create": True,
    "benchmark_duration_s": 60,
    "benchmark_delay_s": 5,
    "target_fps": 60,
    "notifications": True,
    "debug_logging": False,
    "rescan_on_start": True,
    "block_unknown_conflicts": True,    # stop install when an unknown proxy DLL is present
    "selected_game_id": None,
    "selected_gpu_index": None,
}


class Settings:
    def __init__(self, db: Database):
        self.db = db
        self._listeners: list[Callable[[str, Any], None]] = []

    def get(self, key: str) -> Any:
        return self.db.get_setting(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self.db.set_setting(key, value)
        for cb in list(self._listeners):
            cb(key, value)

    def on_change(self, cb: Callable[[str, Any], None]) -> None:
        self._listeners.append(cb)

    def as_dict(self) -> dict[str, Any]:
        d = dict(DEFAULTS)
        d.update(self.db.all_settings())
        return d

    def factory_reset(self) -> None:
        """Reset AKENO's own settings. Games, backups and install records are deliberately kept so that
        installed mods can still be restored afterwards."""
        self.db.clear_settings()
        for cb in list(self._listeners):
            cb("*", None)
