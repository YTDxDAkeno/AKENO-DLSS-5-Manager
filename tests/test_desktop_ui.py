"""DESKTOP pages with real media: the gallery shows real frames, the preview plays a video and stops when hidden,
favourites, filters, import by drop, the fullscreen preview, and dragging a widget on the layout canvas."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from akeno.desktop import config as cfgmod

MEDIA = Path(__file__).resolve().parent / "fixtures" / "media"


def wait(qapp, condition, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def picture(path: Path, color: str) -> Path:
    from PySide6.QtGui import QColor, QImage
    img = QImage(640, 360, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))
    return path


@pytest.fixture
def window(akeno_home, qtbot, monkeypatch):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: True)
    win = MainWindow(Controller(AppServices(db=Database(akeno_home / "suite.sqlite3"))))
    qtbot.addWidget(win)
    win.resize(1600, 980)
    win.show()
    yield win
    win.sessions.stop()
    from akeno.ui import workers
    workers.drain(10000)


def test_gallery_shows_real_frames_and_the_preview_plays(window, qapp, tmp_path):
    page = window.pages["wallpapers"]
    window.go("wallpapers")
    media = tmp_path / "media"
    media.mkdir()
    for name in ("sample.mp4", "sample.gif"):
        shutil.copy(MEDIA / name, media / name)
    red = picture(media / "red.png", "#ff0000")
    assert page.add_paths([str(media / "sample.mp4"), str(media / "sample.gif"), str(red)]) == []
    model = page.gallery.model_
    assert model.rowCount() == 3
    assert wait(qapp, lambda: all(model.pixmap(i) is not None for i in model.items), 30), \
        "every card gets a real thumbnail, videos included"
    video = page.lib.by_path(media / "sample.mp4")
    assert wait(qapp, lambda: page.lib.get(video.id).duration > 0, 10), "the length comes from the video"
    assert page.lib.get(video.id).resolution == "320×180"
    shot = page.gallery.viewport().grab().toImage()
    assert len({shot.pixelColor(x, y).name() for x in range(0, shot.width(), 23) for y in range(0, shot.height(), 23)}) > 10
    page.gallery.select(video.id)
    qapp.processEvents()
    preview = page.preview
    assert preview.item.id == video.id
    assert wait(qapp, preview.playing, 10), "the focused preview plays the video"
    assert preview.audio.isMuted(), "muted until asked"
    preview.toggle_sound()
    assert not preview.audio.isMuted()
    preview.toggle_play()
    assert not preview.playing()
    page.gallery.select(page.lib.by_path(red).id)
    qapp.processEvents()
    assert preview.stack.currentWidget() is preview.still and preview.still.pm is not None
    page.gallery.select(video.id)
    assert wait(qapp, preview.playing, 10)
    window.go("dock")
    qapp.processEvents()
    assert not preview.playing(), "nothing keeps playing once the page is left"


def test_favourites_filters_search_and_drop(window, qapp, tmp_path):
    page = window.pages["wallpapers"]
    window.go("wallpapers")
    a = picture(tmp_path / "Neon City.png", "#ff00ff")
    b = picture(tmp_path / "Forest.png", "#00aa00")
    page._dropped([str(a), str(b)])
    assert page.gallery.model_.rowCount() == 2
    page.gallery.select(page.lib.by_path(a).id)
    page.btn_fav.setChecked(True)
    assert page.lib.by_path(a).favorite
    page.filter.buttons["favorites"].click()
    assert [i.title for i in page.gallery.model_.items] == ["Neon City"]
    page.filter.buttons["all"].click()
    page.search.setText("forest")
    assert [i.title for i in page.gallery.model_.items] == ["Forest"]
    page.search.setText("nothing like this")
    assert page.gallery_stack.currentWidget() is page.no_match
    page.search.clear()
    page.gallery.select(page.lib.by_path(b).id)
    assert page.apply()
    assert page.gallery.model_.active, "the running wallpaper is marked in the gallery"
    assert page._monitor_thumb(page.name()) is not None, "the monitor map shows what runs there"


def test_fullscreen_preview_opens_and_closes(window, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    page = window.pages["wallpapers"]
    window.go("wallpapers")
    page.add_paths([str(picture(tmp_path / "full.png", "#123456"))])
    page._fullscreen()
    fs = page._fs
    assert fs.isVisible()
    from shiboken6 import isValid
    QTest.keyClick(fs, Qt.Key.Key_Escape)
    assert wait(qapp, lambda: not isValid(fs) or not fs.isVisible(), 5), "Esc closes it (and it deletes itself)"


def test_widget_canvas_drag_moves_and_saves(window, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    page = window.pages["widgets"]
    window.go("widgets")
    page.on_show()
    spec = page.add("clock")
    canvas = page.canvas
    qapp.processEvents()
    r = canvas._rect(spec)
    start = r.center().toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(canvas, start + QPoint(60, 30))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(60, 30))
    saved = cfgmod.load()["widgets"][0]
    assert saved["x"] > spec.get("x", 40) - 1 and (saved["x"], saved["y"]) != (40, 40), saved
    assert saved["x"] % 8 == 0 and saved["y"] % 8 == 0, "positions snap to an 8 px grid"


def test_dock_preview_follows_the_settings(window, qapp, tmp_path):
    page = window.pages["dock"]
    window.go("dock")
    page.add_items([{"type": "app", "path": str(tmp_path), "name": "Files"}])
    page.size.setValue(80)
    page.opacity.setValue(60)
    assert page.preview.cfg["icon_size"] == 80 and page.preview.cfg["opacity"] == 60
    img = page.preview.grab().toImage()
    assert not img.isNull() and img.width() > 100
    assert page.list.count() == 1 and page.list.height() < 220, "the pinned list is as tall as its icons"
