"""AKENO Dock: a launcher bar for pinned programs, folders and groups. It is an ordinary always-on-top window
(it never replaces or hides the Windows taskbar or Explorer), docked to a screen edge or floating, optionally
auto-hiding, translucent with the Windows 11 acrylic backdrop, with a dot under running programs."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import (QBoxLayout, QFileDialog, QFileIconProvider, QInputDialog, QMenu, QToolButton,
                               QWidget)

from akeno.desktop import winapi
from akeno.i18n import t

EDGES = ("bottom", "top", "left", "right")


def lnk_target(path: str) -> str | None:
    """The target of a Windows shortcut (.lnk): its LinkInfo local base path, read from the documented
    [MS-SHLLINK] format. Enough to tell whether the program is running."""
    try:
        data = Path(path).read_bytes()
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return None
        flags = int.from_bytes(data[0x14:0x18], "little")
        pos = 0x4C
        if flags & 0x1:                                    # HasLinkTargetIDList
            pos += 2 + int.from_bytes(data[pos:pos + 2], "little")
        if not flags & 0x2:                                # HasLinkInfo
            return None
        base = pos
        local_offset = int.from_bytes(data[base + 0x10:base + 0x14], "little")
        end = data.index(b"\x00", base + local_offset)
        return data[base + local_offset:end].decode("mbcs" if sys.platform == "win32" else "latin-1",
                                                    errors="replace") or None
    except (OSError, ValueError, IndexError):
        return None


def launch(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)                                 # noqa: S606 - the user's own pinned item
    else:
        subprocess.Popen(["xdg-open", path])


def running_exes() -> set[str]:
    import psutil
    out = set()
    for p in psutil.process_iter(["exe"]):
        exe = p.info.get("exe")
        if exe:
            out.add(os.path.normcase(exe))
    return out


class DockButton(QToolButton):
    def __init__(self, item: dict, size: int, on_remove: Callable[[dict], None]):
        super().__init__()
        self.item = item
        self.running = False
        self._remove = on_remove
        self.setAutoRaise(True)
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size + 16, size + 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.get("name") or Path(item.get("path", "")).stem)
        self.setAccessibleName(self.toolTip())
        provider = QFileIconProvider()
        if item.get("type") == "group":
            self.setIcon(provider.icon(QFileIconProvider.IconType.Folder))
            self.clicked.connect(self._group_menu)
        else:
            icon = provider.icon(QFileInfo(item.get("path", "")))
            self.setIcon(icon if not icon.isNull() else QIcon())
            self.clicked.connect(lambda: launch(item["path"]))
        self.setStyleSheet("QToolButton { border: none; border-radius: 10px; background: transparent; }"
                           "QToolButton:hover { background: rgba(255,255,255,0.12); }")

    def target(self) -> str | None:
        path = self.item.get("path", "")
        if path.lower().endswith(".lnk"):
            return lnk_target(path)
        return path if path.lower().endswith(".exe") else None

    def _group_menu(self) -> None:
        menu = QMenu(self)
        for sub in self.item.get("items", []):
            action = menu.addAction(QFileIconProvider().icon(QFileInfo(sub["path"])),
                                    sub.get("name") or Path(sub["path"]).stem)
            action.triggered.connect(lambda _=False, p=sub["path"]: launch(p))
        menu.exec(QCursor.pos())

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction(t("Remove from dock"), lambda: self._remove(self.item))
        menu.exec(e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.running:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor("#8E42FF"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRect(self.width() // 2 - 3, self.height() - 7, 6, 6))


class DockWindow(QWidget):
    def __init__(self, cfg: dict, on_change: Callable[[dict], None]):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AKENO Dock")
        self.setAcceptDrops(True)
        self.cfg = copy.deepcopy(cfg)
        self.on_change = on_change
        self.buttons: list[DockButton] = []
        self.hidden_away = False
        self.game_mode = False
        self._drag: QPoint | None = None
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(10, 8, 10, 8)
        self.box.setSpacing(4)
        self.rebuild()
        self.running_timer = QTimer(self)
        self.running_timer.setInterval(4000)
        self.running_timer.timeout.connect(self.update_running)
        self.running_timer.start()
        self.hide_timer = QTimer(self)
        self.hide_timer.setInterval(250)
        self.hide_timer.timeout.connect(self._autohide)
        self.hide_timer.start()
        self._idle = 0

    # -- layout ------------------------------------------------------------------------------------------------------
    def vertical(self) -> bool:
        return self.cfg.get("edge") in ("left", "right") and not self.cfg.get("floating")

    def rebuild(self) -> None:
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.buttons = []
        self.box.setDirection(QBoxLayout.Direction.TopToBottom if self.vertical() else
                              QBoxLayout.Direction.LeftToRight)
        size = int(self.cfg.get("icon_size", 48))
        for item in self.cfg.get("items", []):
            b = DockButton(item, size, self.remove_item)
            self.buttons.append(b)
            self.box.addWidget(b)
        add = QToolButton()
        add.setText("+")
        add.setToolTip(t("Add a program to the dock"))
        add.setAccessibleName(add.toolTip())
        add.setFixedSize(size // 2 + 16, size + 16)
        add.setStyleSheet("QToolButton { color: #C9D2EE; font-size: 18pt; border: none; background: transparent; }"
                          "QToolButton:hover { color: white; }")
        add.clicked.connect(self.add_program)
        self.box.addWidget(add)
        self.setWindowOpacity(max(0.3, min(1.0, int(self.cfg.get("opacity", 92)) / 100)))
        self.adjustSize()
        self.place()
        self.update_running()

    def screen_geometry(self) -> QRect:
        screens = QGuiApplication.screens()
        index = int(self.cfg.get("screen", 0))
        screen = screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def place(self) -> None:
        area = self.screen_geometry()
        size = self.sizeHint()
        self.resize(size)
        if self.cfg.get("floating") and self.cfg.get("position"):
            x, y = self.cfg["position"]
            self.move(x, y)
            return
        edge = self.cfg.get("edge", "bottom")
        if edge == "bottom":
            self.move(area.center().x() - size.width() // 2, area.bottom() - size.height() - 8)
        elif edge == "top":
            self.move(area.center().x() - size.width() // 2, area.top() + 8)
        elif edge == "left":
            self.move(area.left() + 8, area.center().y() - size.height() // 2)
        else:
            self.move(area.right() - size.width() - 8, area.center().y() - size.height() // 2)

    def showEvent(self, e):
        super().showEvent(e)
        if self.cfg.get("blur", True):
            winapi.set_acrylic(int(self.winId()))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 18, 18)
        p.fillPath(path, QColor(13, 25, 43, 215))
        p.setPen(QColor(38, 58, 90))
        p.drawPath(path)

    def set_game_mode(self, on: bool) -> None:
        """A game or fullscreen program is in front: the always-on-top dock steps aside until it ends."""
        if on == self.game_mode:
            return
        self.game_mode = on
        if on:
            self.hide()
        else:
            self.place()
            self.show()

    # -- running indicators, auto-hide -------------------------------------------------------------------------------
    def update_running(self) -> None:
        try:
            exes = running_exes()
        except Exception:      # psutil hiccups must never break the dock
            return
        for b in self.buttons:
            target = b.target()
            running = bool(target) and os.path.normcase(target) in exes
            if running != b.running:
                b.running = running
                b.update()

    def _autohide(self) -> None:
        if not self.cfg.get("autohide") or self.cfg.get("floating"):
            if self.hidden_away:
                self.hidden_away = False
                self.place()
            return
        pos = QCursor.pos()
        area = self.screen_geometry()
        inside = self.geometry().adjusted(-6, -6, 6, 6).contains(pos)
        edge = self.cfg.get("edge", "bottom")
        at_edge = {"bottom": pos.y() >= area.bottom() - 2, "top": pos.y() <= area.top() + 2,
                   "left": pos.x() <= area.left() + 2, "right": pos.x() >= area.right() - 2}[edge]
        if self.hidden_away and at_edge:
            self.hidden_away = False
            self.place()
        elif not self.hidden_away and not inside:
            self._idle += 1
            if self._idle >= 6:                   # 1.5 s away from the dock
                self._idle = 0
                self.hidden_away = True
                g = self.geometry()
                offset = {"bottom": QPoint(0, g.height() - 3), "top": QPoint(0, -g.height() + 3),
                          "left": QPoint(-g.width() + 3, 0), "right": QPoint(g.width() - 3, 0)}[edge]
                self.move(g.topLeft() + offset)
        else:
            self._idle = 0

    # -- editing ------------------------------------------------------------------------------------------------------
    def _save(self) -> None:
        self.on_change(copy.deepcopy(self.cfg))

    def add_program(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Add a program to the dock"), "",
                                              t("Programs and shortcuts (*.exe *.lnk *.url);;All files (*)"))
        if path:
            self.add_path(path)

    def add_path(self, path: str) -> None:
        self.cfg.setdefault("items", []).append({"type": "app", "path": path, "name": Path(path).stem})
        self._save()
        self.rebuild()

    def add_group(self, name: str, paths: list[str]) -> None:
        self.cfg.setdefault("items", []).append({"type": "group", "name": name,
                                                 "items": [{"path": p, "name": Path(p).stem} for p in paths]})
        self._save()
        self.rebuild()

    def remove_item(self, item: dict) -> None:
        self.cfg["items"] = [i for i in self.cfg.get("items", []) if i is not item and i != item]
        self._save()
        self.rebuild()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.add_path(url.toLocalFile())

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction(t("Add program…"), self.add_program)
        menu.addAction(t("New group from files…"), self._new_group)
        menu.addAction(t("Floating") if not self.cfg.get("floating") else t("Dock to the edge"), self._toggle_float)
        menu.exec(e.globalPos())

    def _new_group(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Programs for the group"), "",
                                                t("Programs and shortcuts (*.exe *.lnk *.url);;All files (*)"))
        if paths:
            name, ok = QInputDialog.getText(self, t("Group name"), t("Name"))
            if ok and name.strip():
                self.add_group(name.strip()[:40], paths)

    def _toggle_float(self) -> None:
        self.cfg["floating"] = not self.cfg.get("floating")
        self.cfg["position"] = [self.x(), self.y()]
        self._save()
        self.rebuild()

    def mousePressEvent(self, e):
        if self.cfg.get("floating") and e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.cfg["position"] = [self.x(), self.y()]
            self._save()
