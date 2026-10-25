"""The desktop runtime (host): ``AKENO.exe --desktop``, one per Windows user, independent of the suite's window.

It starts a wallpaper renderer for each monitor that has a wallpaper, restarts one that crashes (after 2, 5, 15
and 60 seconds; after five crashes in ten minutes that monitor stays on the Windows wallpaper and the suite shows
the error), pauses and resumes them by the pause rules, and shows the dock and the widgets. It follows
``desktop.json``: the suite (or the tray menu) edits that file and the host applies the change.

* A renderer that cannot sit behind the desktop icons exits with ``EMBED_FAILED`` and is tried again after 3, 10
  and 30 seconds (Explorer may still be starting); then that monitor keeps the Windows wallpaper and the suite shows
  the reason. Nothing is ever shown in front of the desktop instead.
* After an Explorer restart a renderer exits with ``DESKTOP_LOST`` and is started again two seconds later.
* The dock steps aside while a game or a fullscreen program runs; widgets pause then, and come above the desktop
  while it is in front (Win+D), the way Rainmeter keeps its skins on the desktop.
* A tray icon offers pause, "stop wallpaper", dock, widgets, open AKENO and "quit Desktop Runtime".

It ends by itself when nothing is switched on, when told to quit, or when the license no longer unlocks AKENO;
Windows' own wallpaper then shows again.
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
EMBED_RETRY_S = (3, 10, 30)          # a renderer that could not attach: Explorer may still be starting
LOST_DELAY_S = 2                     # after an Explorer restart
LOST_GIVE_UP = 8                     # Explorer restarts within CRASH_WINDOW_S before a monitor gives up
NO_HOST, EMBED_FAILED, DESKTOP_LOST = 2, 3, 4      # renderer exit codes (renderer.py)


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
        self.embed_failures = 0
        self.losses: list[float] = []
        self.layer = ""                         # where the wallpaper sits (raised | workerw | ...)
        self.embedded = False
        self.hwnd = 0                           # the renderer's window (diagnostics, the CI desktop check)
        self.exits: list[int] = []              # exit codes of renderers that ended on their own (diagnostics)
        self.replaced = 0                       # native windows Qt replaced and the renderer attached again
        self.replaced_shown = 0                 # ... of them already visible when the renderer got them

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
        self.embedded = False
        if self.stopping:
            self.state = "stopped"
            return
        self.exits = (self.exits + [code])[-20:]
        if code == EMBED_FAILED:
            self.embed_failures += 1
            if self.embed_failures > len(EMBED_RETRY_S):
                self.state = "failed"
                self.error = self.error or "the wallpaper could not be placed behind the desktop icons"
                log.warning("wallpaper for %s gives up: %s", self.monitor, self.error)
                return
            self._again(EMBED_RETRY_S[self.embed_failures - 1])
            return
        if code == DESKTOP_LOST:
            now = time.monotonic()
            self.losses = [t for t in self.losses if now - t < CRASH_WINDOW_S] + [now]
            if len(self.losses) >= LOST_GIVE_UP:
                self.state = "failed"
                self.error = "Explorer restarted too often; the wallpaper stays off until you try again"
                return
            self._again(LOST_DELAY_S)
            return
        delay = self.crashes.record()
        log.warning("wallpaper renderer for %s ended (exit %s)", self.monitor, code)
        if delay is None:
            self.state = "failed"
            self.error = self.error or "the wallpaper renderer stopped repeatedly"
            winapi.refresh_wallpaper()
            return
        self._again(delay)

    def _again(self, delay: float) -> None:
        self.state = "waiting"
        QTimer.singleShot(int(delay * 1000), lambda: self.host.renderer_wanted(self.monitor) and self.start())

    def retry(self) -> None:
        """A fresh chance (the suite's "Try again"): counters back to zero."""
        if self.state != "failed":
            return
        self.crashes = Crashes()
        self.embed_failures = 0
        self.losses = []
        self.error = ""
        self.start()

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
        self.user_paused = False                    # the tray menu's "Pause wallpapers"
        self.tray = None
        self.started_at = time.time()
        self._desktop_front = False
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
                       (3000, self._scan_games), (10 * 60_000, self._check_license), (500, self._desktop_focus)):
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
            app.screenAdded.connect(self._screen_added)
            app.screenRemoved.connect(lambda _s: self.apply())
            for screen in app.screens():
                screen.geometryChanged.connect(self._screens_changed)
        self.apply()
        return True

    def _screen_added(self, screen) -> None:
        screen.geometryChanged.connect(self._screens_changed)
        self.apply()

    def _screens_changed(self, *_):
        """Resolution or scaling changed: every wallpaper covers its monitor again, the dock and widgets move."""
        for r in self.renderers.values():
            r.send({"cmd": "place"})
        if self.dock is not None:
            self.dock.place()
        if self.widgets is not None:
            for win in self.widgets.windows.values():
                win.place()

    def quit(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
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
        if self.tray is not None:
            self.tray.hide()
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
        return not (self.monitors_wanted() or self.cfg["dock"].get("enabled") or cfgmod.widgets_on(self.cfg))

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
        screens = QGuiApplication.screens()
        for screen in screens:
            key = winapi.screen_key(screen, screens)
            w = cfgmod.wallpaper(self.cfg, key)
            if w.get("enabled") and w.get("playlist"):
                out.append(key)
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
        if self.tray is not None:
            self.tray.refresh()

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
        if not cfgmod.widgets_on(self.cfg):
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
        mon = winapi.monitor_of(monitor, QGuiApplication.screens())
        return pause.State(game_running=self.game_running(), fullscreen_app=winapi.fullscreen_app(),
                           on_battery=winapi.on_battery(), remote_session=winapi.remote_session(),
                           covered=mon is not None and winapi.covered(mon),
                           cpu_percent=psutil.cpu_percent(None), user_paused=self.user_paused)

    def evaluate(self) -> None:
        rules = self.cfg["pause"]
        any_paused = False
        gaming = self.game_running() or winapi.fullscreen_app()
        if self.dock is not None:
            # a game or fullscreen program keeps the screen: the always-on-top dock steps aside meanwhile
            self.dock.set_game_mode(gaming and bool(self.cfg["dock"].get("hide_in_games", True)))
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

    def _desktop_focus(self) -> None:
        """Win+D or a click on the desktop: widgets come just above it; any other window: back to the bottom."""
        front = winapi.desktop_in_front()
        if front != self._desktop_front:
            self._desktop_front = front
            if self.widgets is not None:
                self.widgets.desktop_in_front(front)

    def set_user_paused(self, paused: bool) -> None:
        self.user_paused = bool(paused)
        self.evaluate()
        if self.tray is not None:
            self.tray.refresh()

    def edit(self, change: Callable[[dict], None]) -> None:
        """A change from the tray menu: written to desktop.json (the suite follows it) and applied."""
        change(self.cfg)
        self.save()
        self.apply()

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
                r.error = str(message.get("error") or "")[:300]
                r.embedded = bool(message.get("embedded", True))
                r.layer = str(message.get("layer") or "")[:40]
                if str(message.get("hwnd", "")).isdigit():
                    r.hwnd = int(message["hwnd"])
                r.replaced = int(message.get("replaced") or 0)
                r.replaced_shown = int(message.get("replaced_shown") or 0)
                if r.embedded:
                    r.state = "running"
                    r.embed_failures = 0
                r.paused = None
                self.evaluate()
        elif cmd == "retry":
            for r in self.renderers.values():
                r.retry()
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
                "started": self.started_at, "user_paused": self.user_paused,
                "renderers": {m: {"state": r.state, "error": r.error, "paused": bool(r.paused),
                                  "embedded": r.embedded, "layer": r.layer, "hwnd": r.hwnd, "exits": r.exits,
                                  "replaced": r.replaced, "replaced_shown": r.replaced_shown,
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
    code = 0
    if host.start():
        from akeno.desktop.tray import RuntimeTray
        if host.cfg.get("runtime", {}).get("tray", True) and RuntimeTray.available():
            host.tray = RuntimeTray(host)
            host.tray.show()
        code = app.exec()
    host.deleteLater()                  # Qt objects go before the application object (no crash on exit)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return code
