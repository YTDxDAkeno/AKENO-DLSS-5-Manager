"""Intelligent game mode in the running app: polls for library games, runs their boost session, records
playtime and tells other parts (Desktop Studio pauses wallpapers) when a game starts or stops."""

from __future__ import annotations

import logging

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from akeno.ui import workers

log = logging.getLogger(__name__)
POLL_MS = 3000


class GameSessions(QObject):
    started = Signal(str, int)              # game id, pid
    stopped = Signal(str, int)              # game id, seconds played

    def __init__(self, services, parent=None):
        super().__init__(parent)
        from akeno.performance.watcher import GameWatcher
        self.s = services
        self.watcher = GameWatcher(services.library.games)
        self._busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        try:
            restored = self.s.boost.recover({p.pid for p in psutil.process_iter()})
            if restored:
                log.info("restored boost sessions left by an unexpected exit: %s", restored)
        except (OSError, ValueError) as exc:
            log.warning("boost recovery failed: %s", exc)

    def enabled(self) -> bool:
        return bool(self.s.settings.get("intelligent_game_mode"))

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def poll(self) -> None:
        if self._busy or not self.enabled():
            return
        self._busy = True

        def work():
            started, stopped = self.watcher.poll()
            for run in started:
                game = self.s.library.get(run.game_id)
                if game is not None:
                    self.s.boost.start_session(game, run.pid)
            for run, seconds in stopped:
                game = self.s.library.get(run.game_id)
                if game is not None:
                    self.s.boost.end_session(game)
                self.s.db.add_playtime(run.game_id, seconds)
            return started, stopped

        def done(result):
            self._busy = False
            started, stopped = result
            for run in started:
                self.started.emit(run.game_id, run.pid)
            for run, seconds in stopped:
                self.stopped.emit(run.game_id, seconds)

        def failed(exc):
            self._busy = False
            log.warning("game watch failed: %s", exc)
        workers.run(work, done, failed)

    def running(self) -> dict[str, int]:
        return {g: r.pid for g, r in self.watcher.running.items()}
