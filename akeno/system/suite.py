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

    def running_game(self):
        """The library game whose folder a running process was started from (the first one found), or None."""
        import psutil
        games = [(os.path.normcase(os.path.abspath(g.install_dir)).rstrip("\\/") + os.sep, g)
                 for g in self.s.library.games() if g.install_dir]
        if not games:
            return None
        for p in psutil.process_iter(["exe"]):
            exe = p.info.get("exe")
            if not exe:
                continue
            low = os.path.normcase(exe)
            for folder, g in games:
                if low.startswith(folder):
                    return g
        return None

    def recover(self) -> dict:
        """At start and exit: resume processes a Network Gaming Mode left paused (same process only)."""
        if "network" in self.__dict__ or self.store.get("network.gaming.paused"):
            return self.network.gaming.restore()
        return {}

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

    @cached_property
    def network(self):
        from akeno.system.network.service import NetworkService

        def launcher():
            g = self.running_game()
            return g.launcher if g is not None else None
        return NetworkService(self.store, running_launcher=launcher)

    @cached_property
    def mods(self):
        from akeno.system.mods.service import ModScanner
        return ModScanner(self.s, self.store)

    @cached_property
    def security(self):
        if not self.s.components.active():
            from akeno.components.service import ComponentError
            raise ComponentError("Security Center is disabled or unavailable.")
        from akeno.system.securitycenter.service import SecurityService
        return SecurityService(self.store)
