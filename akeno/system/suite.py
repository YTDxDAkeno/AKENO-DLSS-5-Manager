"""The system tools as one object on ``AppServices`` (``services.system_tools``), created on first use so AKENO's
start and the mod engine are unaffected."""

from __future__ import annotations

import os
from functools import cached_property

from akeno.system.store import SystemStore


class SystemTools:
    def __init__(self, services, store: SystemStore | None = None):
        self.s = services
        self.store = store or SystemStore()

    # -- what the modules need from the rest of AKENO ----------------------------------------------------------
    def game_dirs(self) -> dict[str, str]:
        out = {}
        for g in self.s.library.games():
            if g.install_dir:
                out[g.install_dir] = g.name
        return out

    def running_game_pids(self) -> set[int]:
        """Processes started from a library game's folder (excluded from "background" load)."""
        import psutil
        dirs = [os.path.normcase(os.path.abspath(d)).rstrip("\\/") + os.sep for d in self.game_dirs()]
        if not dirs:
            return set()
        out = set()
        for p in psutil.process_iter(["pid", "exe"]):
            exe = p.info.get("exe")
            if exe and any(os.path.normcase(exe).startswith(d) for d in dirs):
                out.add(p.pid)
        return out

    # -- modules -------------------------------------------------------------------------------------------------
    @cached_property
    def doctor(self):
        from akeno.system.doctor.service import DoctorService
        return DoctorService(self.store, system_info=lambda: self.s.system, game_pids=self.running_game_pids,
                             game_dirs=self.game_dirs)

    @cached_property
    def cleaner(self):
        from akeno.system.cleaner.service import CleanerService, steam_libraries
        return CleanerService(self.store, games=self.s.library.games,
                              steam_dirs=lambda: steam_libraries(self.s.library.games()))
