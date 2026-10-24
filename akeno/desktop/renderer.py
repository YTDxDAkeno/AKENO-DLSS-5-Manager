"""The wallpaper renderer: one process per monitor, started and watched by the desktop host.

    AKENO.exe --wallpaper-renderer --monitor "\\\\.\\DISPLAY1" --server AKENO-Desktop-<user>

It shows the monitor's playlist entry (image, GIF, video or web page) behind the desktop icons, follows the host's
pause/resume commands and exits when the host goes away. A crash here costs one wallpaper, never the suite: the
host restarts it, and stops trying after repeated crashes.

Web wallpapers run in a sandboxed Chromium page (Qt WebEngine). The page is served from its own folder through the
``akeno-wallpaper://`` scheme: it can read the files of that folder and nothing else on the PC, every network
request is blocked, pop-ups and plug-ins are off, and an animation frame limit applies. Optionally it receives the
playback level (``livelyAudioListener(array)`` / ``akenoAudio(array)``, the call Lively's web wallpapers use) and
the mouse position (``akenoMouse(x, y)`` plus ordinary ``mousemove`` events).
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QStackedLayout, QWidget

from akeno.desktop import config as cfgmod
from akeno.desktop import media, playlist, winapi

log = logging.getLogger(__name__)

RAF_LIMIT = """(function () {
  var fps = %d, last = 0, native = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (cb) {
    return native(function step(t) {
      if (t - last < 1000 / fps - 1) { return native(step); }
      last = t; cb(t);
    });
  };
})();"""


class ImageView(QWidget):
    def __init__(self, path: str, fit: str):
        super().__init__()
        self.pm = QPixmap(path)
        self.fit = fit

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.pm.isNull():
            return
        target = self.rect()
        if self.fit == "stretch":
            p.drawPixmap(target, self.pm)
            return
        mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fit == "fill" else \
            Qt.AspectRatioMode.KeepAspectRatio
        size = self.pm.size() if self.fit == "center" else self.pm.size().scaled(target.size(), mode)
        x, y = (target.width() - size.width()) // 2, (target.height() - size.height()) // 2
        p.drawPixmap(QRect(x, y, size.width(), size.height()), self.pm)

    def set_paused(self, paused: bool) -> None:
        pass


class GifView(QLabel):
    def __init__(self, path: str, fit: str):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: black;")
        self.movie = QMovie(path)
        self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self.fit = fit
        self.setMovie(self.movie)
        self.movie.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        frame = self.movie.currentPixmap().size()
        if frame.isEmpty():
            return
        if self.fit == "stretch":
            self.movie.setScaledSize(self.size())
        elif self.fit in ("fill", "fit"):
            mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fit == "fill" else \
                Qt.AspectRatioMode.KeepAspectRatio
            self.movie.setScaledSize(frame.scaled(self.size(), mode))

    def set_paused(self, paused: bool) -> None:
        self.movie.setPaused(paused)


class VideoView(QWidget):
    def __init__(self, path: str, fit: str, volume: int):
        super().__init__()
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(max(0, min(100, volume)) / 100)
        self.audio.setMuted(volume <= 0)
        self.player.setAudioOutput(self.audio)
        self.view = QVideoWidget(self)
        self.view.setAspectRatioMode({"fill": Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      "stretch": Qt.AspectRatioMode.IgnoreAspectRatio}.get(
            fit, Qt.AspectRatioMode.KeepAspectRatio))
        self.player.setVideoOutput(self.view)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def resizeEvent(self, e):
        self.view.setGeometry(self.rect())

    def set_paused(self, paused: bool) -> None:
        (self.player.pause if paused else self.player.play)()


def web_available() -> bool:
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


SCHEME = b"akeno-wallpaper"
MIME = {".html": "text/html", ".htm": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
        ".css": "text/css", ".json": "application/json", ".wasm": "application/wasm", ".svg": "image/svg+xml",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
        ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf"}


def register_scheme() -> bool:
    """Must run before the QApplication exists (Qt WebEngine's rule for custom schemes)."""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401 - loaded before the application, as WebEngine requires
        from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    except ImportError:
        return False
    if QWebEngineUrlScheme.schemeByName(SCHEME).name() == SCHEME:
        return True
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setDefaultPort(-1)                                  # PortUnspecified
    # a secure origin of its own (WebGL, audio and fetch of its own files work); file:// stays out of reach
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.CorsEnabled |
                    QWebEngineUrlScheme.Flag.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)
    return True


def resolve(root: Path, url_path: str) -> Path | None:
    """The file a wallpaper asks for, if it lies inside the wallpaper's folder."""
    rel = urllib.parse.unquote(url_path).lstrip("/")
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    return target if target.is_relative_to(root.resolve()) and target.is_file() else None


class WebView(QWidget):
    def __init__(self, path: str, fps: int):
        super().__init__()
        from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript,
                                             QWebEngineSettings, QWebEngineUrlRequestInterceptor,
                                             QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler)
        from PySide6.QtWebEngineWidgets import QWebEngineView
        entry = media.web_entry(path)
        root = entry.parent.resolve()

        class Folder(QWebEngineUrlSchemeHandler):
            def requestStarted(self, job):
                target = resolve(root, job.requestUrl().path())
                if target is None:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                f = QFile(str(target), job)
                if not f.open(QIODevice.OpenModeFlag.ReadOnly):
                    job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                    return
                mime = MIME.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or \
                    "application/octet-stream"
                job.reply(mime.encode(), f)

        class Offline(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                if info.requestUrl().scheme() not in (SCHEME.decode(), "data", "blob"):
                    info.block(True)

        self.profile = QWebEngineProfile(self)                         # off the record: no cookies or cache kept
        self.handler = Folder(self)
        self.interceptor = Offline(self)
        self.profile.installUrlSchemeHandler(SCHEME, self.handler)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        settings = self.profile.settings()
        for attr, on in ((QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
                         (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False),
                         (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
                         (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
                         (QWebEngineSettings.WebAttribute.WebGLEnabled, True),
                         (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)):
            settings.setAttribute(attr, on)
        script = QWebEngineScript()
        script.setName("akeno-fps-limit")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode(RAF_LIMIT % max(5, min(144, int(fps or 30))))
        self.profile.scripts().insert(script)
        self.page = QWebEnginePage(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.load(QUrl(f"{SCHEME.decode()}://wallpaper/{urllib.parse.quote(entry.name)}"))
        self.Page = QWebEnginePage

    def resizeEvent(self, e):
        self.view.setGeometry(self.rect())

    def set_paused(self, paused: bool) -> None:
        self.page.setLifecycleState(self.Page.LifecycleState.Frozen if paused else self.Page.LifecycleState.Active)

    def dispose(self) -> None:
        """The page goes before its profile (Qt WebEngine warns, and may crash, otherwise)."""
        self.page.deleteLater()
        self.profile.deleteLater()

    def send_audio(self, values: list[float]) -> None:
        data = json.dumps([round(v, 3) for v in values])
        self.page.runJavaScript(f"(window.livelyAudioListener||window.akenoAudio||function(){{}})({data});")

    def send_mouse(self, x: int, y: int) -> None:
        self.page.runJavaScript(
            f"(function(){{var e=new MouseEvent('mousemove',{{clientX:{x},clientY:{y},bubbles:true}});"
            f"document.dispatchEvent(e);(window.akenoMouse||function(){{}})({x},{y});}})();")


def make_view(item: dict, wcfg: dict) -> QWidget:
    path = item["path"]
    kind = item.get("kind") or media.kind_of(path)
    if kind == "image":
        return ImageView(path, wcfg["fit"])
    if kind == "gif":
        return GifView(path, wcfg["fit"])
    if kind == "video":
        return VideoView(path, wcfg["fit"], int(wcfg.get("volume", 0)))
    if kind == "web":
        if not web_available():
            raise media.MediaError("web wallpapers need Qt WebEngine, which is not in this build")
        return WebView(path, int(wcfg.get("fps_limit", 30)))
    raise media.MediaError(f"unsupported wallpaper {path}")


class WallpaperWindow(QWidget):
    def __init__(self, monitor: str, config_path: Path | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnBottomHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("AKENO wallpaper")
        self.setStyleSheet("background: black;")
        self.monitor = monitor
        self.config_path = config_path
        self.layout_ = QStackedLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.view: QWidget | None = None
        self.index: int | None = None
        self.paused = False
        self.virtual_desktop = winapi.virtual_desktop_id()
        self.started = datetime.now()
        self.error = ""
        from akeno.desktop.audio import AudioMeter
        self.meter = AudioMeter()
        self.tick = QTimer(self)
        self.tick.setInterval(30_000)
        self.tick.timeout.connect(self.check_playlist)
        self.fast = QTimer(self)
        self.fast.setInterval(33)
        self.fast.timeout.connect(self._fast_tick)
        self.reload()

    def wcfg(self) -> dict:
        return cfgmod.wallpaper(cfgmod.load(self.config_path), self.monitor)

    def place(self) -> None:
        mons = {m.name: m for m in winapi.monitors()}
        if self.monitor in mons and winapi.WINDOWS:
            self.show()
            if not winapi.attach(int(self.winId()), mons[self.monitor]):
                self.setGeometry(self._qt_geometry())
            return
        self.setGeometry(self._qt_geometry())
        self.show()

    def _qt_geometry(self) -> QRect:
        for screen in QGuiApplication.screens():
            if screen.name() == self.monitor:
                return screen.geometry()
        return QGuiApplication.primaryScreen().geometry()

    def reload(self) -> None:
        wcfg = self.wcfg()
        self.index = None
        self.check_playlist(wcfg)
        self.tick.start()
        self._update_fast(wcfg)

    def check_playlist(self, wcfg: dict | None = None) -> None:
        wcfg = wcfg or self.wcfg()
        index = playlist.current_index(wcfg, virtual_desktop=self.virtual_desktop, started=self.started)
        if index is None or index == self.index:
            return
        self.index = index
        item = wcfg["playlist"][index]
        old = self.view
        try:
            media.check(item["path"])
            self.view = make_view(item, wcfg)
            self.error = ""
        except (media.MediaError, OSError, ImportError) as exc:
            self.error = str(exc)
            log.warning("wallpaper %s: %s", item.get("path"), exc)
            self.view = QLabel()
            self.view.setStyleSheet("background: black;")
        self.layout_.addWidget(self.view)
        self.layout_.setCurrentWidget(self.view)
        if old is not None:
            self.layout_.removeWidget(old)
            if hasattr(old, "dispose"):
                old.dispose()
            old.deleteLater()
        if self.paused and hasattr(self.view, "set_paused"):
            self.view.set_paused(True)

    def _update_fast(self, wcfg: dict) -> None:
        web = isinstance(self.view, WebView)
        if web and not self.paused and (wcfg.get("audio_reactive") or wcfg.get("interactive")):
            self.fast.start()
        else:
            self.fast.stop()
        self._fast_cfg = wcfg

    def _fast_tick(self) -> None:
        if not isinstance(self.view, WebView) or self.paused:
            return
        wcfg = self._fast_cfg
        if wcfg.get("audio_reactive") and self.meter.peak() is not None:
            self.view.send_audio(self.meter.values())
        if wcfg.get("interactive") and winapi.desktop_in_front():
            pos = winapi.cursor_pos()
            if pos:
                origin = self.mapToGlobal(self.rect().topLeft())
                self.view.send_mouse(pos[0] - origin.x(), pos[1] - origin.y())

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        if self.view is not None and hasattr(self.view, "set_paused"):
            self.view.set_paused(paused)
        self._update_fast(getattr(self, "_fast_cfg", self.wcfg()))

    def set_virtual_desktop(self, desktop: str | None) -> None:
        self.virtual_desktop = desktop
        self.check_playlist()

    def closeEvent(self, e):
        if hasattr(self.view, "dispose"):
            self.view.dispose()
        if winapi.WINDOWS:
            winapi.detach(int(self.winId()))
        super().closeEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(1280, 720)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="akeno --wallpaper-renderer")
    ap.add_argument("--wallpaper-renderer", action="store_true")
    ap.add_argument("--monitor", required=True)
    ap.add_argument("--server", required=True)
    args, _ = ap.parse_known_args(argv)
    register_scheme()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    window = WallpaperWindow(args.monitor)
    window.place()
    from akeno.desktop.ipc import Channel
    channel = Channel.open(args.server, {"role": "renderer", "monitor": args.monitor})
    if channel is None:
        log.warning("desktop host %s not reachable", args.server)
        return 2

    def command(message: dict) -> None:
        cmd = message.get("cmd")
        if cmd == "pause":
            window.set_paused(True)
        elif cmd == "resume":
            window.set_paused(False)
        elif cmd == "reload":
            window.reload()
            channel.send({"cmd": "ready", "monitor": args.monitor, "error": window.error})
        elif cmd == "vdesk":
            window.set_virtual_desktop(message.get("id"))
        elif cmd == "quit":
            window.close()
            winapi.refresh_wallpaper()
            app.quit()
    channel.message.connect(command)
    channel.closed.connect(lambda: (window.close(), winapi.refresh_wallpaper(), app.quit()))
    channel.send({"cmd": "ready", "monitor": args.monitor, "error": window.error})
    code = app.exec()
    for obj in (channel, window):       # Qt objects go before the application object (no crash on exit)
        obj.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return code
