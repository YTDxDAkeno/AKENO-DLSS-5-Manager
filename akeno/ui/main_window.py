"""Main window: sidebar navigation, header banner and the page stack."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea, QStackedWidget,
                               QStatusBar, QVBoxLayout, QWidget)

from akeno import APP_NAME, __version__
from akeno.i18n import t
from akeno.ui import art, icons, theme
from akeno.ui.widgets import StatusPill, confirm, info, label

NAV = [("dashboard", "Dashboard", "home"), ("hardware", "Hardware Scan", "chip"), ("library", "Game Library", "gamepad"),
       ("profiles", "Smart Profiles", "sliders"), ("benchmark", "Benchmark", "chart"),
       ("install", "Secure Install", "shield"), ("backups", "Backups", "database"), ("settings", "Settings", "gear")]


class Header(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(118)
        self._pm = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 14, 24, 12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(APP_NAME, "h1")
        self.title.setStyleSheet("font-size: 24pt; font-weight: 800;")
        col.addStretch(1)
        col.addWidget(self.title)
        self.sub = label(t("Safe analysis & recommendation tool for DLSS 5 community mods"), "dim")
        self.sub.setStyleSheet("font-size: 11pt;")
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        right = QVBoxLayout()
        right.addStretch(1)
        tag = QLabel(t("Neural Rendering.\nSafely managed."))
        tag.setAlignment(Qt.AlignmentFlag.AlignRight)
        tag.setStyleSheet("font-style: italic; font-size: 12pt; color: rgba(196,181,253,0.85);")
        right.addWidget(tag)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(label(f"v{__version__}", "muted"))
        self.status = StatusPill("", "pending")
        row.addWidget(self.status)
        right.addLayout(row)
        lay.addLayout(right)

    def resizeEvent(self, e):
        self._pm = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        if self._pm is None or self._pm.size() != self.size():
            self._pm = art.banner(max(self.width(), 10), max(self.height(), 10))
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)
        p.setPen(theme.qc(theme.BORDER))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class Sidebar(QFrame):
    def __init__(self, on_nav):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(250)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 16)
        lay.setSpacing(4)
        logo_row = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(art.logo_mark(52))
        logo_row.addWidget(mark)
        word = QLabel("AKENO")
        word.setStyleSheet("font-size: 24pt; font-weight: 800; letter-spacing: 2px;")
        logo_row.addWidget(word, 1)
        lay.addLayout(logo_row)
        lay.addSpacing(22)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        from PySide6.QtWidgets import QPushButton
        for i, (key, text, ic) in enumerate(NAV):
            b = QPushButton("  " + t(text))
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, theme.TEXT_DIM, 22))
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(t(text))
            b.setToolTip(f"{t(text)}  (Ctrl+{i + 1})")
            b.clicked.connect(lambda checked=False, k=key: on_nav(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        promo = QFrame()
        promo.setFixedHeight(140)
        promo.setObjectName("Promo")
        pl = QVBoxLayout(promo)
        pl.addStretch(1)
        pt = QLabel(t("PLAY MORE\nWITH NEW POSSIBILITIES."))
        pt.setStyleSheet("font-size: 9pt; font-weight: 700; letter-spacing: 2px; color: #dbe1ff;")
        pl.addWidget(pt)
        self.promo = promo
        lay.addWidget(promo)
        lay.addSpacing(10)
        foot = label(f"{APP_NAME}\n" + t("Community driven.\nYour files, your choice."), "muted")
        lay.addWidget(foot)

    def select(self, key: str) -> None:
        for k, b in self.buttons.items():
            b.setChecked(k == key)
            b.setIcon(icons.icon(dict((n[0], n[2]) for n in NAV)[k], theme.TEXT if k == key else theme.TEXT_DIM, 22))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        g = self.promo.geometry()
        pm = art.banner(g.width(), g.height(), seed=3)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(g.x(), g.y(), g.width(), g.height(), 12, 12)
        p.setClipPath(path)
        p.drawPixmap(g.topLeft(), pm)


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.ctl = controller
        self.s = controller.s
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.QIcon(art.app_icon_pixmap(256)))
        self.resize(1600, 980)
        self.setMinimumSize(1100, 700)
        self._build()
        controller.navigate.connect(self.go)
        controller.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        controller.settings_changed.connect(self._setting_changed)
        controller.games_changed.connect(self._header_status)
        controller.installs_changed.connect(self._header_status)
        self._header_status()
        for i, (key, _, _) in enumerate(NAV):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=key: self.go(k))
        self.go("dashboard")

    def _build(self) -> None:
        from akeno.ui.pages.backups import BackupsPage
        from akeno.ui.pages.benchmark import BenchmarkPage
        from akeno.ui.pages.dashboard import DashboardPage
        from akeno.ui.pages.hardware import HardwarePage
        from akeno.ui.pages.install import InstallPage
        from akeno.ui.pages.library import LibraryPage
        from akeno.ui.pages.profiles import ProfilesPage
        from akeno.ui.pages.settings import SettingsPage

        root = QWidget()
        root.setObjectName("Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar(self.go)
        h.addWidget(self.sidebar)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.header = Header()
        right.addWidget(self.header)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        h.addLayout(right, 1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.pages = {}
        for key, cls in (("dashboard", DashboardPage), ("hardware", HardwarePage), ("library", LibraryPage),
                         ("profiles", ProfilesPage), ("benchmark", BenchmarkPage), ("install", InstallPage),
                         ("backups", BackupsPage), ("settings", SettingsPage)):
            page = cls(self.ctl, self)
            page.setObjectName(f"page_{key}")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(22, 18, 22, 18)
            wl.addWidget(page)
            scroll.setWidget(wrap)
            self.pages[key] = page
            self.stack.addWidget(scroll)

    def go(self, key: str) -> None:
        keys = [k for k, _, _ in NAV]
        if key not in keys:
            return
        self.stack.setCurrentIndex(keys.index(key))
        self.sidebar.select(key)
        page = self.pages[key]
        if hasattr(page, "on_show"):
            page.on_show()

    def current_page(self) -> str:
        return [k for k, _, _ in NAV][self.stack.currentIndex()]

    def open_profile(self, profile_id: str) -> None:
        self.go("profiles")
        self.pages["profiles"].select_profile(profile_id)

    def restore_transaction(self, tx_id: int) -> None:
        tx = self.s.db.get_transaction(tx_id)
        if not tx:
            return
        conflicts = self.s.installer.check_restore(tx_id) if tx["kind"] != "snapshot" else []
        detail = "\n".join(f"• {c.rel_path}: {t(c.reason)}" for c in conflicts)
        text = t("Restore the original files of {g}? Files installed by AKENO are removed and backed-up originals "
                 "are put back.", g=tx["game_name"])
        if conflicts:
            text += "\n\n" + t("Some files changed after installation and will be overwritten:")
        if not confirm(self, t("Restore"), text, detail or None, t("Restore")):
            return
        force = bool(conflicts)

        def work():
            if tx["kind"] == "snapshot":
                return self.s.installer.restore_snapshot(tx_id, force=True)
            return self.s.restore(tx_id, force=force)

        def done(res):
            self.ctl.installs_changed.emit()
            self.ctl.games_changed.emit()
            info(self, t("Restore"), t("Original files restored.") if res.ok else t("Restore did not complete."),
                 "\n".join(res.messages[:12]), error=not res.ok)
        self.ctl.run("restore", work, done, lambda e: info(self, t("Restore"), str(e), error=True))

    def _header_status(self) -> None:
        n = len([t_ for t_ in self.s.db.transactions() if t_["state"] == "installed"])
        text = t("Compatibility DB {v}", v=self.s.compat_db.version)
        if n:
            text += " · " + t("{n} managed install(s)", n=n)
        self.header.status.set(text, "ok")
        self.header.status.setToolTip(t("Package catalog {c}; compatibility database {v} ({o}).",
                                        c=self.s.catalog.version, v=self.s.compat_db.version, o=t(self.s.compat_db.origin)))

    def _setting_changed(self, key: str) -> None:
        if key in ("language", "*", "theme_accent"):
            # Rebuild every page so all texts and styles pick up the change.
            from akeno import i18n
            i18n.set_language(self.s.settings.get("language"))
            from PySide6.QtWidgets import QApplication
            QApplication.instance().setStyleSheet(theme.stylesheet(self.s.settings.get("theme_accent")))
            current = self.current_page()
            old = self.takeCentralWidget()
            old.deleteLater()
            self._build()
            QTimer.singleShot(0, lambda: self.go(current))
