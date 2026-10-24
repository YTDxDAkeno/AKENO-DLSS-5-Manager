"""The desktop host: ``AKENO.exe --desktop``, one per Windows user.

It starts a wallpaper renderer for each monitor that has a wallpaper, restarts one that crashes (after 2, 5, 15
and 60 seconds; after five crashes in ten minutes that monitor stays on the Windows wallpaper and the suite shows
the error), pauses and resumes them by the pause rules, and shows the dock and the widgets. It follows
``desktop.json``: the suite edits that file and the host applies the change. It ends by itself when nothing is
switched on, when the suite tells it to, or when the license no longer unlocks AKENO; Windows' own wallpaper then
shows again.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import psutil
from PySide6.QtCore import QEvent, QFileSystemWatcher, QLockFile, QObject, QProcess, QTimer
from PySide6.QtGui import QGuiApplication

from akeno.desktop import config as cfgmod
from akeno.desktop import ipc, pause, winapi

log = logging.getLogger(__name__)

RESTART_DELAYS = (2, 5, 15, 60)
CRASH_WINDOW_S = 600
GIVE_UP_AFTER = 5


def self_command(*args: str) -> tuple[str, list[str]]:
    """How this program starts itself again (the built AKENO.exe, or Python with the source)."""
    if getattr(sys, "frozen", False):
        return sys.executable, list(args)
    return sys.executable, ["-m", "akeno", *args]


class Crashes:
    def __init__(self):
        self.times: list[float] = []

    def record(self, now: float | None = None) -> float | None:
        """Seconds to wait before the next start, or None to give up."""
        now = time.monotonic() if now is None else now
        self.times = [t for t in self.times if now - t < CRASH_WINDOW_S] + [now]
        if len(self.times) >= GIVE_UP_AFTER:
            return None
        return RESTART_DELAYS[min(len(self.times), len(RESTART_DELAYS)) - 1]


class Renderer(QObject):
    def __init__(self, host: "DesktopHost", monitor: str):
        super().__init__(host)
        self.host = host
        self.monitor = monitor
        self.process: QProcess | None = None
        self.channel: ipc.Channel | None = None
        self.crashes = Crashes()
        self.state = "stopped"                  # starting | running | waiting | failed | stopped
        self.error = ""
        self.paused: bool | None = None
        self.config = ""
        self.stopping = False

    def start(self) -> None:
        if self.process is not None:
            return
        self.stopping = False
        program, args = self.host.spawn("--wallpaper-renderer", "--monitor", self.monitor, "--server",
                                        self.host.server.name)
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedErrorChannel)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        self.process = proc
        self.state = "starting"
        self.paused = None
        proc.start()

    def _error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.error = "the wallpaper renderer could not be started"
            self._finished(-1, None)

    def _finished(self, code: int, _status) -> None:
        self.process = None
        self.channel = None
        if self.stopping:
            self.state = "stopped"
            return
        delay = self.crashes.record()
        log.warning("wallpaper renderer for %s ended (exit %s)", self.monitor, code)
        if delay is None:
            self.state = "failed"
            self.error = self.error or "the wallpaper renderer stopped repeatedly"
            winapi.refresh_wallpaper()
            return
        self.state = "waiting"
        QTimer.singleShot(int(delay * 1000), lambda: self.host.renderer_wanted(self.monitor) and self.start())

    def attach(self, channel: ipc.Channel) -> None:
        self.channel = channel

    def send(self, message: dict) -> None:
        if self.channel is not None:
            self.channel.send(message)

    def stop(self) -> None:
        self.stopping = True
        if self.process is None:
            self.state = "stopped"
            return
        self.send({"cmd": "quit"})
        proc = self.process
        QTimer.singleShot(3000, lambda: proc.state() != QProcess.ProcessState.NotRunning and proc.kill())


def library_games(db_file: Path) -> list:
    """The suite's game list (read only), enough to notice a running game while the suite is closed."""
    if not db_file.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = conn.execute("SELECT id, data FROM games").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = []
    for game_id, data in rows:
        try:
            d = json.loads(data)
        except ValueError:
            continue
        out.append(SimpleNamespace(id=game_id, exe_path=d.get("exe_path"), exe_candidates=d.get("exe_candidates") or []))
    return out


class DesktopHost(QObject):
    def __init__(self, config_path: Path | None = None, server_name: str | None = None,
                 license_ok: Callable[[], bool] | None = None,
                 spawn: Callable[..., tuple[str, list[str]]] = self_command, db_file: Path | None = None,
                 exit_when_idle: bool = True):
        super().__init__()
        self.config_path = Path(config_path or cfgmod.path())
        self.server = ipc.Server(server_name)
        self.server.setParent(self)
        self.server.connected.connect(self._connected)
        self.license_ok = license_ok or (lambda: True)
        self.spawn = spawn
        if db_file is None:
            from akeno.paths import db_path
            db_file = db_path()
        self.db_file = Path(db_file)
        self.exit_when_idle = exit_when_idle
        self.cfg = cfgmod.load(self.config_path)
        self.renderers: dict[str, Renderer] = {}
        self.apps: list[ipc.Channel] = []
        self.app_game: bool | None = None          # what the suite reports; None: the suite is not running
        self.own_game = False
        self.reasons: dict[str, list] = {}
        self.dock = None
        self.widgets = None
        self._written = ""
        self._mtime = 0.0
        self._games_cache: tuple[float, list] = (0.0, [])
        self._scanning = False
        self.virtual_desktop = winapi.virtual_desktop_id()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self.reload())
        self.watcher.directoryChanged.connect(lambda _: self.reload())
        self.timers = []
        for ms, fn in ((2000, self.evaluate), (1500, self._check_virtual_desktop), (2000, self._poll_config),
                       (3000, self._scan_games), (10 * 60_000, self._check_license)):
            timer = QTimer(self)
            timer.setInterval(ms)
            timer.timeout.connect(fn)
            self.timers.append(timer)

    # -- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(self.config_path.parent / "host.lock"))     # one host, also when two start at once
        if not self.lock.tryLock(0) or not self.server.listen():
            log.info("another desktop host is already running")
            return False
        if not self.license_ok():
            log.info("desktop host: AKENO is not unlocked")
            return False
        self.watcher.addPath(str(self.config_path.parent))
        if self.config_path.exists():
            self.watcher.addPath(str(self.config_path))
        for timer in self.timers:
            timer.start()
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _s: self.apply())
            app.screenRemoved.connect(lambda _s: self.apply())
        self.apply()
        return True

    def quit(self) -> None:
        for timer in self.timers:
            timer.stop()
        for r in self.renderers.values():
            r.stop()
        if self.dock is not None:
            self.dock.close()
            self.dock = None
        if self.widgets is not None:
            self.widgets.close()
            self.widgets = None
        for ch in self.apps:
            ch.send({"cmd": "bye"})
        QTimer.singleShot(3500, self._finish)

    def _finish(self) -> None:
        self.server.close()
        if getattr(self, "lock", None) is not None:
            self.lock.unlock()
        winapi.refresh_wallpaper()
        app = QGuiApplication.instance()
        if app is not None:
            app.quit()

    def idle(self) -> bool:
        if not self.cfg.get("enabled", True):
            return True
        return not (self.monitors_wanted() or self.cfg["dock"].get("enabled") or self.cfg.get("widgets"))

    # -- configuration -----------------------------------------------------------------------------------------------
    def _poll_config(self) -> None:
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._mtime:
            self.reload()

    def reload(self) -> None:
        try:
            self._mtime = self.config_path.stat().st_mtime
            text = self.config_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text and text == self._written:
            return                                  # our own write (dock or widget moved)
        if str(self.config_path) not in self.watcher.files() and self.config_path.exists():
            self.watcher.addPath(str(self.config_path))    # an atomic replace drops the watch
        self.cfg = cfgmod.load(self.config_path)
        self.apply()

    def save(self) -> None:
        cfgmod.save(self.cfg, self.config_path)
        try:
            self._written = self.config_path.read_text(encoding="utf-8")
            self._mtime = self.config_path.stat().st_mtime
        except OSError:
            pass

    def monitors_wanted(self) -> list[str]:
        out = []
        for screen in QGuiApplication.screens():
            w = cfgmod.wallpaper(self.cfg, screen.name())
            if w.get("enabled") and w.get("playlist"):
                out.append(screen.name())
        return out

    def renderer_wanted(self, monitor: str) -> bool:
        return monitor in self.monitors_wanted() and monitor in self.renderers

    def apply(self) -> None:
        if self.exit_when_idle and self.idle():
            log.info("desktop host: nothing is switched on, ending")
            self.quit()
            return
        wanted = self.monitors_wanted()
        for monitor in list(self.renderers):
            if monitor not in wanted:
                self.renderers.pop(monitor).stop()
        for monitor in wanted:
            r = self.renderers.get(monitor)
            text = json.dumps(cfgmod.wallpaper(self.cfg, monitor), sort_keys=True)
            if r is None:
                r = self.renderers[monitor] = Renderer(self, monitor)
                r.config = text
                r.start()
            elif text != r.config:
                r.config = text
                if r.state == "failed":
                    r.crashes = Crashes()           # a new wallpaper gets a fresh chance
                    r.error = ""
                    r.start()
                else:
                    r.send({"cmd": "reload"})
        self._apply_dock()
        self._apply_widgets()
        self.evaluate()

    def _apply_dock(self) -> None:
        dcfg = self.cfg["dock"]
        if not dcfg.get("enabled"):
            if self.dock is not None:
                self.dock.close()
                self.dock.deleteLater()
                self.dock = None
            return
        from akeno.desktop.dock import DockWindow

        def changed(new: dict) -> None:
            self.cfg["dock"] = new
            self.save()
        if self.dock is None:
            self.dock = DockWindow(dcfg, changed)
            self.dock.show()
        elif self.dock.cfg != dcfg:
            self.dock.cfg = copy.deepcopy(dcfg)
            self.dock.rebuild()

    def _apply_widgets(self) -> None:
        if not self.cfg.get("widgets"):
            if self.widgets is not None:
                self.widgets.sync([], False)
            return
        if self.widgets is None:
            from akeno.desktop.widgets import Context, WidgetManager

            def changed(widgets: list[dict], locked: bool) -> None:
                self.cfg["widgets"] = widgets
                self.cfg["widgets_locked"] = locked
                self.save()
            self.widgets = WidgetManager(Context(db_file=self.db_file, license_view=self._license_view), changed)
        self.widgets.sync(self.cfg["widgets"], self.cfg.get("widgets_locked", False))

    @staticmethod
    def _license_view():
        from akeno.licensing import manager
        return manager().view()

    # -- pausing -----------------------------------------------------------------------------------------------------
    def game_running(self) -> bool:
        return bool(self.app_game) or self.own_game

    def _scan_games(self) -> None:
        """While the suite runs it reports games itself; otherwise the host looks for library games."""
        if self.app_game is not None or self._scanning or not self.cfg["pause"].get("game", True):
            return
        if time.monotonic() - self._games_cache[0] > 300:
            self._games_cache = (time.monotonic(), library_games(self.db_file))
        games = self._games_cache[1]
        if not games:
            self.own_game = False
            return
        self._scanning = True
        from akeno.performance.watcher import GameWatcher
        from akeno.ui import workers

        def done(found):
            self._scanning = False
            self.own_game = bool(found)

        def failed(exc):
            self._scanning = False
        workers.run(lambda: GameWatcher(lambda: games).scan(), done, failed)

    def state_for(self, monitor: str) -> pause.State:
        mons = {m.name: m for m in winapi.monitors()}
        return pause.State(game_running=self.game_running(), fullscreen_app=winapi.fullscreen_app(),
                           on_battery=winapi.on_battery(), remote_session=winapi.remote_session(),
                           covered=monitor in mons and winapi.covered(mons[monitor]),
                           cpu_percent=psutil.cpu_percent(None))

    def evaluate(self) -> None:
        rules = self.cfg["pause"]
        any_paused = False
        for monitor, r in self.renderers.items():
            decision = pause.decide(rules, self.state_for(monitor))
            self.reasons[monitor] = decision.reasons
            any_paused = any_paused or decision.paused
            if r.channel is not None and decision.paused != r.paused:
                r.paused = decision.paused
                r.send({"cmd": "pause" if decision.paused else "resume"})
        if self.widgets is not None:
            # widgets keep still while a game or a fullscreen program runs (no wasted CPU time)
            self.widgets.set_paused(self.game_running() or winapi.fullscreen_app())

    def _check_virtual_desktop(self) -> None:
        current = winapi.virtual_desktop_id()
        if current != self.virtual_desktop:
            self.virtual_desktop = current
            for r in self.renderers.values():
                r.send({"cmd": "vdesk", "id": current})

    def _check_license(self) -> None:
        if not self.license_ok():
            log.info("desktop host: the license no longer unlocks AKENO, ending")
            self.quit()

    # -- messages ----------------------------------------------------------------------------------------------------
    def _connected(self, ch: ipc.Channel) -> None:
        ch.message.connect(lambda m, c=ch: self.on_message(c, m))
        ch.closed.connect(lambda c=ch: self._closed(c))

    def _closed(self, ch: ipc.Channel) -> None:
        if ch in self.apps:
            self.apps.remove(ch)
            if not self.apps:
                self.app_game = None
        for r in self.renderers.values():
            if r.channel is ch:
                r.channel = None

    def on_message(self, ch: ipc.Channel, message: dict) -> None:
        cmd = message.get("cmd")
        if cmd == "hello":
            if message.get("role") == "renderer" and message.get("monitor") in self.renderers:
                self.renderers[message["monitor"]].attach(ch)
            elif message.get("role") == "app":
                self.apps.append(ch)
        elif cmd == "ready":
            r = self.renderers.get(message.get("monitor"))
            if r is not None:
                r.state = "running"
                r.error = str(message.get("error") or "")[:300]
                r.paused = None
                self.evaluate()
        elif cmd == "reload":
            self.reload()
        elif cmd == "game":
            self.app_game = bool(message.get("running"))
            self.evaluate()
        elif cmd == "status":
            ch.send(self.status())
        elif cmd == "quit":
            ch.send({"cmd": "bye"})
            self.quit()

    def status(self) -> dict:
        return {"cmd": "status", "pid": os.getpid(), "game_running": self.game_running(),
                "renderers": {m: {"state": r.state, "error": r.error, "paused": bool(r.paused),
                                  "reasons": [str(x) for x in self.reasons.get(m, [])]}
                              for m, r in self.renderers.items()},
                "dock": self.dock is not None,
                "widgets": len(self.widgets.windows) if self.widgets is not None else 0}


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("AKENO Desktop")
    from akeno.ui import theme
    theme.apply(app, None)                               # menus and dialogs of the dock and widgets

    def licensed() -> bool:
        from akeno.licensing import manager
        try:
            return manager().view().unlocked
        except Exception as exc:                  # an unreadable license store locks, it never crashes the host
            log.warning("license check failed: %s", exc)
            return False
    host = DesktopHost(license_ok=licensed)
    code = app.exec() if host.start() else 0
    host.deleteLater()                  # Qt objects go before the application object (no crash on exit)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return code
