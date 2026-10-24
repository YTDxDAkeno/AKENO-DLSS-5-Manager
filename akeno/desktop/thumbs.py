"""Wallpaper thumbnails and media facts, made in the background and cached.

* Pictures and GIFs: decoded at thumbnail size with ``QImageReader`` on a small thread pool (two threads); a GIF
  shows a frame from its middle.
* Videos: one at a time through Qt Multimedia (the FFmpeg backend AKENO already ships): the file is opened, the
  frame at a quarter of its length is taken, the player is stopped again. Resolution and duration come along.
  Never more than one video is decoded for thumbnails, so a big library cannot load the PC.
* Web wallpapers: the picture their package brought along, or the frame the wallpaper renderer captured the first
  time it showed the page (``live_path``); otherwise the gallery draws a placeholder.

The cache key is the file's identity (resolved path, size, modification time) plus the thumbnail version and
size, so a changed file gets a new thumbnail and an unchanged one is never decoded again. A broken file costs a
placeholder, never a crash: every decode has a time limit and errors become ``failed``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader

from akeno.paths import sub

log = logging.getLogger(__name__)

VERSION = 2
SIZE = QSize(480, 270)
VIDEO_TIMEOUT_MS = 12_000


def cache_dir() -> Path:
    return sub("desktop") / "thumbs"


def cache_key(path: str | Path, size: QSize = SIZE) -> str:
    p = Path(path)
    try:
        st = p.stat()
        ident = f"{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        ident = "missing"
    raw = f"{os.path.normcase(str(p.resolve()))}|{ident}|v{VERSION}|{size.width()}x{size.height()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def thumb_path(path: str | Path, folder: Path | None = None) -> Path:
    return (folder or cache_dir()) / f"{cache_key(path)}.jpg"


def live_path(path: str | Path, folder: Path | None = None) -> Path:
    """Where the renderer leaves a frame of a running web wallpaper (no thumbnail can be decoded from HTML)."""
    p = Path(path)
    return (folder or cache_dir()) / f"{hashlib.sha1(os.path.normcase(str(p.resolve())).encode()).hexdigest()}.live.jpg"


def cover(image: QImage, size: QSize = SIZE) -> QImage:
    """Scaled to fill ``size`` and cropped to it from the centre (a proper 16:9 crop, never letterboxed)."""
    if image.isNull():
        return image
    scaled = image.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    return scaled.copy(x, y, size.width(), size.height()).convertToFormat(QImage.Format.Format_RGB32)


def read_still(path: str | Path, size: QSize = SIZE) -> tuple[QImage, dict]:
    """A picture or GIF at thumbnail size, with its width, height and (GIF) duration. Safe for any thread."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    full = reader.size()
    meta: dict = {}
    if full.isValid():
        meta.update(width=full.width(), height=full.height())
        target = full.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        if target.width() < full.width():
            reader.setScaledSize(target)               # decode small: a 4K picture never lands in memory whole
    frames = reader.imageCount()
    if frames > 1:
        delay = max(10, reader.nextImageDelay())
        meta["duration"] = round(frames * delay / 1000, 2)
        reader.jumpToImage(frames // 2)
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString() or "unreadable picture")
    return cover(image, size), meta


class _StillJob(QRunnable):
    def __init__(self, service: "ThumbnailService", path: str, source: str, out: Path):
        super().__init__()
        self.service, self.path, self.source, self.out = service, path, source, out

    def run(self) -> None:
        try:
            image, meta = read_still(self.source)
            self.out.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(self.out), "JPG", 88)
            self.service._still_done.emit(self.path, image, meta, "")
        except Exception as exc:              # a broken file: a placeholder in the gallery, never a crash
            self.service._still_done.emit(self.path, QImage(), {}, str(exc) or type(exc).__name__)


class VideoGrabber(QObject):
    """Takes one frame of a video (at a quarter of its length) with Qt Multimedia, then stops."""

    done = Signal(str, QImage, dict, str)          # path, frame, meta, error

    def __init__(self):
        super().__init__()
        self.player = None
        self.sink = None
        self.path = ""
        self.target = 0
        self.meta: dict = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._finish(QImage(), "timed out"))

    def busy(self) -> bool:
        return bool(self.path)

    def grab(self, path: str) -> None:
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        self.path = path
        self.meta = {}
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)        # no audio output: thumbnails are silent
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(lambda _e, text: self._finish(QImage(), text or "cannot be played"))
        self.timer.start(VIDEO_TIMEOUT_MS)
        self.player.setSource(QUrl.fromLocalFile(path))

    def _status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer
        if self.player is None:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia and not self.meta:
            duration = self.player.duration()
            res = self.player.metaData().value(QMediaMetaData.Key.Resolution)
            self.meta = {"duration": round(duration / 1000, 2)} if duration > 0 else {"duration": 0.0}
            if res is not None and hasattr(res, "width") and res.width() > 0:
                self.meta.update(width=res.width(), height=res.height())
            self.target = int(min(max(duration * 0.25, 500), max(duration - 300, 0))) if duration > 0 else 0
            if self.target:
                self.player.setPosition(self.target)
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._finish(QImage(), "not a playable video")

    def _frame(self, frame) -> None:
        if self.player is None or not frame.isValid() or self.player.position() < self.target - 150:
            return
        image = frame.toImage()
        if image.isNull():
            return
        if "width" not in self.meta:
            self.meta.update(width=image.width(), height=image.height())
        self._finish(cover(image), "")

    def _finish(self, image: QImage, error: str) -> None:
        if not self.path:
            return
        path, meta = self.path, dict(self.meta)
        self.path = ""
        self.timer.stop()
        player, sink = self.player, self.sink
        self.player = self.sink = None
        if player is not None:
            player.stop()
            player.setSource(QUrl())
            player.deleteLater()
        if sink is not None:
            sink.deleteLater()
        self.done.emit(path, image, meta, error)


class ThumbnailService(QObject):
    """``request()`` returns a cached thumbnail at once, or queues it; ``ready``/``failed`` follow."""

    ready = Signal(str, QImage)        # wallpaper path, thumbnail
    meta = Signal(str, dict)           # wallpaper path, {"width", "height", "duration"}
    failed = Signal(str, str)
    _still_done = Signal(str, QImage, dict, str)

    def __init__(self, folder: Path | None = None, threads: int = 2):
        super().__init__()
        self.folder = Path(folder or cache_dir())
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(1, threads))
        self.videos: deque[str] = deque()
        self.pending: set[str] = set()
        self.grabber = VideoGrabber()
        self.grabber.setParent(self)
        self.grabber.done.connect(self._video_done)
        self._still_done.connect(self._done)

    def cached(self, path: str) -> QImage | None:
        for candidate in (thumb_path(path, self.folder), live_path(path, self.folder)):
            if candidate.is_file():
                image = QImage(str(candidate))
                if not image.isNull():
                    return image
        return None

    def request(self, path: str, kind: str, preview: str = "") -> QImage | None:
        image = self.cached(path)
        if image is not None or path in self.pending:
            return image
        out = thumb_path(path, self.folder)
        if kind in ("image", "gif") and Path(path).is_file():
            self.pending.add(path)
            self.pool.start(_StillJob(self, path, path, out))
        elif kind == "web" and preview and Path(preview).is_file():
            self.pending.add(path)
            self.pool.start(_StillJob(self, path, preview, out))
        elif kind == "video" and Path(path).is_file():
            self.pending.add(path)
            self.videos.append(path)
            self._next_video()
        return None

    def cancel_all(self) -> None:
        """Queued videos are dropped (the gallery was left); running work finishes."""
        for path in self.videos:
            self.pending.discard(path)
        self.videos.clear()
        self.pool.clear()

    def _next_video(self) -> None:
        if not self.grabber.busy() and self.videos:
            self.grabber.grab(self.videos.popleft())

    def _video_done(self, path: str, image: QImage, meta: dict, error: str) -> None:
        if not image.isNull():
            out = thumb_path(path, self.folder)
            out.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(out), "JPG", 88)
        self._done(path, image, meta, error)
        QTimer.singleShot(0, self._next_video)

    def _done(self, path: str, image: QImage, meta: dict, error: str) -> None:
        self.pending.discard(path)
        if meta:
            self.meta.emit(path, meta)
        if image.isNull():
            log.info("no thumbnail for %s: %s", path, error)
            self.failed.emit(path, error or "no picture")
        else:
            self.ready.emit(path, image)

    def wait(self, ms: int = 15_000) -> bool:
        """Tests: until everything queued is done."""
        import time
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + ms / 1000
        while (self.pending or self.grabber.busy()) and time.monotonic() < end:
            QApplication.processEvents()
            self.pool.waitForDone(20)
        return not self.pending
