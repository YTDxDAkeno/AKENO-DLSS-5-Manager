"""UI controller: owns AppServices, runs long operations in the background and broadcasts changes."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from akeno.core.models import Game
from akeno.services import AppServices
from akeno.ui import workers

log = logging.getLogger(__name__)


class Controller(QObject):
    hardware_changed = Signal()
    games_changed = Signal()
    selection_changed = Signal()
    installs_changed = Signal()
    benchmarks_changed = Signal()
    settings_changed = Signal(str)
    busy_changed = Signal(str, bool)          # task key, running
    status = Signal(str)                      # status-bar message
    navigate = Signal(str)                    # page key

    def __init__(self, services: AppServices):
        super().__init__()
        self.s = services
        self.busy: set[str] = set()

    # -- helpers -------------------------------------------------------------------------------
    def _start(self, key: str) -> bool:
        if key in self.busy:
            return False
        self.busy.add(key)
        self.busy_changed.emit(key, True)
        return True

    def _end(self, key: str) -> None:
        self.busy.discard(key)
        self.busy_changed.emit(key, False)

    def is_busy(self, key: str) -> bool:
        return key in self.busy

    def run(self, key: str, fn, on_done=None, on_error=None, on_progress=None) -> bool:
        if not self._start(key):
            return False

        def done(res):
            self._end(key)
            if on_done:
                _safe_call(on_done, res)

        def err(exc):
            self._end(key)
            self.status.emit(f"{exc}")
            if on_error:
                _safe_call(on_error, exc)

        workers.run(fn, done, err, on_progress)
        return True

    # -- shared operations ---------------------------------------------------------------------
    def scan_hardware(self, on_done=None) -> bool:
        def done(info):
            self.hardware_changed.emit()
            if on_done:
                on_done(info)
        return self.run("hardware", self.s.scan_hardware, done)

    def scan_games(self, on_done=None) -> bool:
        def done(games):
            self.games_changed.emit()
            self.selection_changed.emit()
            if on_done:
                on_done(games)
        return self.run("games", lambda: self.s.library.scan(), done)

    def analyze_game(self, game: Game | None, on_done=None) -> bool:
        if game is None:
            return False

        def done(g):
            self.games_changed.emit()
            self.selection_changed.emit()
            if on_done:
                on_done(g)
        return self.run("analyze", lambda: self.s.library.reanalyze(game.id), done)

    def select_game(self, game_id: str) -> None:
        self.s.select_game(game_id)
        self.selection_changed.emit()

    def set_setting(self, key: str, value) -> None:
        self.s.settings.set(key, value)
        self.settings_changed.emit(key)


def _safe_call(fn, arg) -> None:
    """Deliver a result unless the receiving page was rebuilt (e.g. after a language switch) meanwhile."""
    try:
        fn(arg)
    except RuntimeError as exc:
        if "already deleted" not in str(exc):
            raise
        log.debug("result for a deleted widget dropped: %s", exc)
