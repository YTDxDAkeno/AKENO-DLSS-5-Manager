"""Network Gaming Mode: find what uses the connection in the background, pause the programs the user picks while
playing, measure before and after, and put everything back.

Pausing uses the same mechanism as FPS Booster's background programs (``performance.system.suspend`` /
``resume``) and its protection list: Windows, security software, anti-cheat, drivers and AKENO are never paused, and
neither is the launcher of the game that is running (a Steam game needs Steam). Every paused process is written to
the journal first; *Restore* resumes them, and AKENO resumes them on exit and on its next start if it was closed
unexpectedly. Nothing is changed in Windows' network settings: no registry "ping tweaks", no TCP changes.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import psutil

from akeno.i18n import msg
from akeno.performance import background
from akeno.performance import system as perf_system

STATE_KEY = "network.gaming.paused"
LAUNCHERS = {"steam": {"steam.exe", "steamwebhelper.exe", "steamservice.exe"},
             "epic": {"epicgameslauncher.exe", "epiconlineservices.exe"},
             "ea": {"eadesktop.exe", "eabackgroundservice.exe"}, "ubisoft": {"upc.exe", "uplay.exe"},
             "gog": {"galaxyclient.exe"}, "battlenet": {"battle.net.exe", "agent.exe"},
             "riot": {"riotclientservices.exe"}, "xbox": set(), "fivem": set(), "manual": set()}


def candidates(sample: dict[str, Any], running_game_launcher: str | None = None) -> list[dict[str, Any]]:
    """Programs with internet connections that may be paused: known background downloaders first, never protected
    processes or the running game's launcher."""
    keep = LAUNCHERS.get(running_game_launcher or "", set())
    out = []
    for p in sample.get("processes") or []:
        name = (p.get("name") or "").lower()
        if background.protected(name) or name in keep or name == "svchost.exe":
            continue
        out.append({**p, "pausable": True})
    return sorted(out, key=lambda p: (not p.get("known"), -(p.get("io_bps") or 0)))


class GamingMode:
    def __init__(self, store, suspend: Callable[[int], bool] | None = None,
                 resume: Callable[[int], bool] | None = None):
        self.store = store
        self._suspend = suspend or perf_system.suspend
        self._resume = resume or perf_system.resume

    def paused(self) -> list[dict[str, Any]]:
        return list(self.store.get(STATE_KEY, []) or [])

    def pause(self, procs: list[dict[str, Any]], before: dict[str, Any] | None = None) -> dict[str, Any]:
        """Pause the chosen processes (each journalled first). ``before`` is the measurement taken before."""
        done, refused = [], []
        state = self.paused()
        for p in procs:
            name = (p.get("name") or "").lower()
            pid = int(p["pid"])
            if background.protected(name):
                refused.append({"pid": pid, "name": p.get("name"), "why": str(msg("protected"))})
                continue
            try:
                created = psutil.Process(pid).create_time()
            except psutil.Error:
                refused.append({"pid": pid, "name": p.get("name"), "why": str(msg("already ended"))})
                continue
            aid = self.store.begin("network", "gaming.pause", str(pid), p.get("name") or "",
                                   {"pid": pid, "created": created})
            ok = self._suspend(pid)
            self.store.finish(aid, ok, None if ok else "refused by Windows")
            if ok:
                entry = {"pid": pid, "name": p.get("name"), "created": created, "action": aid}
                state.append(entry)
                done.append(entry)
            else:
                refused.append({"pid": pid, "name": p.get("name"), "why": str(msg("Windows refused (an elevated or "
                                                                                 "protected process)"))})
        self.store.set(STATE_KEY, state)
        if before is not None:
            self.store.set(STATE_KEY + ".before", before)
        return {"paused": done, "refused": refused}

    def restore(self) -> dict[str, Any]:
        """Resume every process the gaming mode paused that still is the same process (pid and start time)."""
        resumed, gone = [], []
        for e in self.paused():
            try:
                same = abs(psutil.Process(e["pid"]).create_time() - e["created"]) < 0.01
            except psutil.Error:
                same = False
            if same and self._resume(e["pid"]):
                resumed.append(e)
            else:
                gone.append(e)
            if e.get("action"):
                self.store.mark_undone(e["action"])
        self.store.set(STATE_KEY, [])
        return {"resumed": resumed, "gone": gone}

    def active(self) -> bool:
        return bool(self.paused())
