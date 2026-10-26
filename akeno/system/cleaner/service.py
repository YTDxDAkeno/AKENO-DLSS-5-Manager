"""Smart Cleaner service (no Qt) and Smart Maintenance.

Smart Maintenance is off by default. When the user turns it on, AKENO scans (read-only) at the chosen interval
while it runs anyway and shows how much could be cleaned. It never removes anything by itself, needs no start with
Windows and no administrator rights.
"""

from __future__ import annotations

import os
import time
from typing import Any

from akeno.system.cleaner import diskspace, engine, rules
from akeno.system.tasks import TaskContext

INTERVALS = {0: "Off", 7: "Every week", 30: "Every month"}
MAINTENANCE_KEY = "cleaner.maintenance"


class CleanerService:
    def __init__(self, store, games=None, steam_dirs=None, env=None, akeno_home: str | None = None):
        self.store = store
        self._games = games or (lambda: [])
        self._steam_dirs = steam_dirs or (lambda: [])
        self.env = env
        self.akeno_home = akeno_home
        self.last: engine.ScanResult | None = None

    def categories(self) -> list[rules.Category]:
        return rules.categories(self.env, self._steam_dirs(), self.akeno_home)

    # -- junk scan and clean-up --------------------------------------------------------------------------------
    def scan(self, ctx: TaskContext | None = None, only: set[str] | None = None) -> engine.ScanResult:
        cats = [c for c in self.categories() if only is None or c.id in only]
        res = engine.scan(cats, ctx, env=self.env)
        self.last = res
        self.store.add_history("cleaner.scan", {"categories": [c.summary() for c in res.categories],
                                                "duration_s": res.duration_s})
        self.store.set(MAINTENANCE_KEY + ".last_scan", time.time())
        return res

    def preview(self, selected: list[str], permanent: bool = False) -> engine.Preview:
        if self.last is None:
            raise ValueError("Scan first.")
        return engine.preview(self.last, selected, permanent)

    def clean(self, selected: list[str], ctx: TaskContext | None = None, permanent: bool = False,
              confirm_shaders: bool = False, remover=None) -> engine.CleanReport:
        if self.last is None:
            raise ValueError("Scan first.")
        return engine.clean(self.last, selected, ctx, self.store, remover, permanent, confirm_shaders, env=self.env)

    # -- disk space ----------------------------------------------------------------------------------------------
    def folder(self, root: str, ctx: TaskContext | None = None) -> diskspace.FolderReport:
        return diskspace.folder_sizes(root, ctx)

    def games(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        return diskspace.game_sizes(self._games(), ctx)

    def downloads(self, ctx: TaskContext | None = None) -> diskspace.FolderReport | None:
        folder = diskspace.downloads_folder()
        return diskspace.folder_sizes(folder, ctx) if folder else None

    def duplicates(self, root: str, ctx: TaskContext | None = None):
        return diskspace.duplicates(root, ctx)

    # -- Smart Maintenance -----------------------------------------------------------------------------------------
    def maintenance(self) -> dict[str, Any]:
        return {"interval_days": int(self.store.get(MAINTENANCE_KEY + ".interval", 0) or 0),
                "last_scan": self.store.get(MAINTENANCE_KEY + ".last_scan")}

    def set_maintenance(self, interval_days: int) -> None:
        if interval_days not in INTERVALS:
            raise ValueError(interval_days)
        self.store.set(MAINTENANCE_KEY + ".interval", interval_days)

    def maintenance_due(self, now: float | None = None) -> bool:
        m = self.maintenance()
        if not m["interval_days"]:
            return False
        last = m["last_scan"] or 0
        return (now or time.time()) - last >= m["interval_days"] * 86400

    def maintenance_scan(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        """The scheduled check: a read-only scan of the low-risk categories, and how much they hold."""
        low = {c.id for c in self.categories() if c.preselect}
        res = self.scan(ctx, only=low)
        return {"bytes": res.cleanable_bytes, "categories": [c.summary() for c in res.categories if c.files]}


def steam_libraries(games) -> list[str]:
    """Steam installation folders from the library's Steam games (…/steamapps/common/<game> → the library)."""
    out = []
    for g in games:
        if getattr(g, "launcher", "") != "steam" or not getattr(g, "install_dir", None):
            continue
        parts = os.path.normpath(g.install_dir).split(os.sep)
        low = [p.lower() for p in parts]
        if "steamapps" in low:
            lib = os.sep.join(parts[:low.index("steamapps")])
            if lib and lib not in out:
                out.append(lib)
    return out
