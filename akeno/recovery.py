"""Putting back what AKENO changed - available on the welcome screen, also when the license has ended.

Someone whose license expired must still be able to get their games back to the original files. This module can
only *undo*: restore the original game files of an AKENO installation (the same transactional restore as Installed
Mods), take back FPS Booster changes, and resume programs Network Gaming Mode paused. It has no way to install,
apply or start anything, so it unlocks no feature of the suite.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class Recovery:
    def __init__(self, services):
        self.s = services

    def items(self) -> dict[str, Any]:
        """What there is to put back: AKENO installations, FPS Booster changes, paused programs."""
        installs = []
        for tx in self.s.stack.installed():
            installs.append({"game_id": tx["game_id"], "game": tx["game_name"], "folder": tx["target_dir"],
                             "backend": tx["backend_id"],
                             "installed": (tx.get("finished_at") or tx.get("created_at") or "")[:16].replace("T", " ")})
        boost = []
        state = self.s.boost.state
        for game_id in sorted(set(state.get("games", {})) | set(state.get("sessions", {}))):
            g = self.s.library.get(game_id)
            boost.append({"game_id": game_id, "game": g.name if g else
                          (state.get("sessions", {}).get(game_id) or {}).get("name") or game_id})
        paused = list(self.s.system_tools.store.get("network.gaming.paused", []) or [])
        return {"installs": installs, "boost": boost, "paused": paused}

    def restore_install(self, game_id: str, force: bool = False):
        """The original files of one game, through the mod engine's own transactional restore."""
        stack = self.s.stack
        if stack.active(game_id) is None:
            return stack.remove_legacy(game_id)
        return stack.uninstall(game_id, force=force)

    def restore_boost(self, game_id: str) -> list[str]:
        g = self.s.library.get(game_id)
        if g is None:                                   # the game is gone from the library: settings still go back
            g = type("RemovedGame", (), {"id": game_id, "exe_path": None, "install_dir": None, "name": game_id})()
        return self.s.boost.restore(g)

    def resume_paused(self) -> dict[str, Any]:
        return self.s.system_tools.network.gaming.restore()


def resume_paused_processes() -> dict[str, Any]:
    """At every start, before the license check: programs a Network Gaming Mode paused (AKENO closed unexpectedly)
    run again. Needs nothing but AKENO's own system store."""
    from akeno.system.store import SystemStore
    store = SystemStore()
    try:
        if not store.get("network.gaming.paused"):
            return {}
        from akeno.system.network.gaming import GamingMode
        out = GamingMode(store).restore()
        log.info("resumed %d programs paused by Network Gaming Mode", len(out.get("resumed", [])))
        return out
    finally:
        store.close()
