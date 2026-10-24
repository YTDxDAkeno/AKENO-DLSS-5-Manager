"""Intelligent game mode: notices when a game from the library starts and stops (process polling via psutil).

On start the game's session changes are applied (power plan, priorities, background programs) and listeners can
pause wallpapers; on exit everything is restored and the playtime is recorded. A crash of the game is an exit too.
Polling reads only process paths, every few seconds, in a worker thread.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

import psutil

log = logging.getLogger(__name__)


@dataclass
class RunningGame:
    game_id: str
    pid: int
    started_at: float          # wall clock (UTC seconds)
    started_mono: float


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class GameWatcher:
    def __init__(self, games: Callable[[], list]):
        self._games = games
        self.running: dict[str, RunningGame] = {}

    def _index(self) -> dict[str, str]:
        out = {}
        for g in self._games():
            for path in [getattr(g, "exe_path", None), *getattr(g, "exe_candidates", [])]:
                if path:
                    out[_norm(path)] = g.id
        return out

    def scan(self) -> dict[str, int]:
        """{game id: pid} of library games running now (the oldest process of a game counts)."""
        index = self._index()
        found: dict[str, tuple[float, int]] = {}
        if not index:
            return {}
        for p in psutil.process_iter(["pid", "exe", "create_time"]):
            exe = p.info.get("exe")
            if not exe:
                continue
            game_id = index.get(_norm(exe))
            if game_id:
                created = p.info.get("create_time") or 0
                if game_id not in found or created < found[game_id][0]:
                    found[game_id] = (created, p.pid)
        return {g: pid for g, (_, pid) in found.items()}

    def poll(self) -> tuple[list[RunningGame], list[tuple[RunningGame, int]]]:
        """(games that started, (games that stopped, seconds played)) since the last poll."""
        now = self.scan()
        started, stopped = [], []
        for game_id, pid in now.items():
            if game_id not in self.running:
                run = RunningGame(game_id, pid, time.time(), time.monotonic())
                self.running[game_id] = run
                started.append(run)
        for game_id in [g for g in self.running if g not in now]:
            run = self.running.pop(game_id)
            stopped.append((run, int(time.monotonic() - run.started_mono)))
        return started, stopped
