"""Main window: sidebar navigation, artwork header and the page stack.

Layout rules (v1.1):
* The header and sidebar adapt to the window height (three density levels), so a 1920x1080 screen shows
  the whole dashboard and small or scaled screens (125-150 %) keep the most room for content.
* Each page sits in a scroll area, so nothing is ever cut off; a page may expose a ``footer`` widget (the
  dashboard's action buttons) that stays pinned below the scroll area and is always reachable.
* On very wide (ultrawide) windows page content is capped and centred instead of stretched.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from akeno import APP_NAME, __version__
from akeno.i18n import t
from akeno.ui import art, assets, icons, theme
from akeno.ui.widgets import StatusPill, confirm, info, label

NAV = [("dashboard", "Dashboard", "home"), ("hardware", "Hardware Scan", "chip"), ("library", "Game Library", "gamepad"),
       ("profiles", "Smart Profiles", "sliders"), ("benchmark", "Benchmark", "chart"),
       ("install", "Secure Install", "shield"), ("backups", "Backups", "database"), ("settings", "Settings", "gear")]

MAX_CONTENT_WIDTH = 2300          # ultrawide: centre the page instead of stretching cards to 3000+ px
# (window height threshold, header height, title pt, subtitle pt)
HEADER_LEVELS = ((960, theme.HEADER_HEIGHT, 25, 11.5), (800, 90, 21, 10.5), (0, 72, 17, 9.5))


class Header(QWidget):
    def __init__(self):
        super().__init__()
        self._pm = None
        self._pm_key = None
        self.level = -1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(30, 10, 24, 10)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(APP_NAME, "h1")
        col.addStretch(1)
        col.addWidget(self.title)
        self.sub = label(t("Safe analysis & recommendation tool for DLSS 5 community mods"), "dim")
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addStretch(1)
        # Tagline floats over the artwork, left of the character so it never covers her face.
        self.tag = QLabel(t("Neural Rendering.\nSafely managed."), self)
        self.tag.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        self.version = label(f"v{__version__}", "muted")
        row.addWidget(self.version)
        self.status = StatusPill("", "pending")
        row.addWidget(self.status)
        right.addLayout(row)
        lay.addLayout(right)
        self.set_level(0)

    def set_level(self, level: int) -> None:
        if level == self.level:
            return
        self.level = level
        _, height, title_pt, sub_pt = HEADER_LEVELS[level]
        self.setFixedHeight(height)
        self.title.setStyleSheet(f"font-size: {title_pt}pt; font-weight: 800; letter-spacing: 0.5px;")
        self.sub.setStyleSheet(f"font-size: {sub_pt}pt; color: #B7C3DD;")
        self.tag.setStyleSheet(f"font-style: italic; font-size: {12 if level == 0 else 10}pt; font-weight: 600;"
                               " color: rgba(203,190,255,0.90);")
        self._place_tag()

    def resizeEvent(self, e):
        self._pm = None
        super().resizeEvent(e)
        self._place_tag()

    def _place_tag(self) -> None:
        self.tag.adjustSize()
        # The artwork is right-aligned at its native 300 px height scaled to the header; its character
        # starts roughly 760 px (scaled) from the right edge.
        scale = self.height() / 300.0
        x = self.width() - int(800 * scale) - self.tag.width()
        title_right = self.layout().contentsMargins().left() + self.title.sizeHint().width()
        x = max(x, title_right + 40)
        self.tag.move(x, max(6, (self.height() - self.tag.height()) // 2 - 6))
        self.tag.setVisible(self.level < 2 and x + self.tag.width() < self.width() - 200)

    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._pm is None or self._pm_key != key:
            self._pm = art.header_background(max(self.width(), 10), max(self.height(), 10), dpr)
            self._pm_key = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)
        line = QLinearGradient(0, 0, self.width(), 0)
        line.setColorAt(0, QColor(theme.BORDER))
        line.setColorAt(0.6, QColor(78, 49, 213, 140))
        line.setColorAt(1, QColor(22, 142, 255, 120))
        p.setPen(QPen(line, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class PromoCard(QFrame):
    """Sidebar card with the Japanese-landscape artwork (decoration) and the AKENO slogan."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Promo")
        self.setFixedHeight(150)
        self._pm = None
        self._pm_key = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.addStretch(1)
        self.text = QLabel(t("PLAY MORE\nWITH NEW POSSIBILITIES."))
        self.text.setStyleSheet("font-size: 8.5pt; font-weight: 800; letter-spacing: 2px; color: #E6EAFF;")
        lay.addWidget(self.text)
        bar = QFrame()
        bar.setFixedSize(26, 2)
        bar.setStyleSheet("background: #E6EAFF; border-radius: 1px;")
        lay.addWidget(bar)

    def resizeEvent(self, e):
        self._pm = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._pm is None or self._pm_key != key:
            self._pm = self._render(dpr)
            self._pm_key = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)

    def _render(self, dpr: float):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPixmap
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(round(w * dpr), round(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)
        bg = assets.cover(assets.SIDEBAR_LANDSCAPE, w, h, dpr, h_align=0.5, v_align=0.35)
        if bg.isNull():
            bg = art.banner(w, h, seed=3)
        p.drawPixmap(0, 0, bg)
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0.0, QColor(6, 13, 28, 30))
        shade.setColorAt(0.45, QColor(6, 13, 28, 70))
        shade.setColorAt(1.0, QColor(6, 13, 28, 225))
        p.fillRect(r, shade)
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER_HI), 1))
        p.drawRoundedRect(r, 12, 12)
        p.end()
        return pm


class Sidebar(QFrame):
    def __init__(self, on_nav):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(theme.SIDEBAR_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)
        self.logo = QLabel()
        self.logo.setAccessibleName("AKENO")
        word = art.wordmark(50)
        if not word.isNull():
            self.logo.setPixmap(word)
            self.logo.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(self.logo)
        else:   # asset missing: icon + text
            row = QHBoxLayout()
            self.logo.setPixmap(art.logo_mark(50))
            row.addWidget(self.logo)
            name = QLabel("AKENO")
            name.setStyleSheet("font-size: 24pt; font-weight: 800; letter-spacing: 2px;")
            row.addWidget(name, 1)
            lay.addLayout(row)
        lay.addSpacing(18)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(sep)
        lay.addSpacing(10)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self._icons = {k: ic for k, _, ic in NAV}
        for i, (key, text, ic) in enumerate(NAV):
            b = QPushButton("  " + t(text))
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, theme.TEXT_DIM, 24))
            b.setIconSize(QSize(24, 24))
            b.setMinimumHeight(46)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(t(text))
            b.setToolTip(f"{t(text)}  (Ctrl+{i + 1})")
            b.clicked.connect(lambda checked=False, k=key: on_nav(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.promo = PromoCard()
        lay.addWidget(self.promo)
        lay.addSpacing(10)
        self.foot = label(f"{APP_NAME}\n" + t("Community driven.\nYour files, your choice."), "muted")
        lay.addWidget(self.foot)

    def select(self, key: str) -> None:
        for k, b in self.buttons.items():
            b.setChecked(k == key)
            b.setIcon(icons.icon(self._icons[k], "#FFFFFF" if k == key else theme.TEXT_DIM, 24))

    def set_compact(self, height: int) -> None:
        """Hide decoration first when the window is short, so navigation never needs scrolling."""
        self.promo.setVisible(height >= 820)
        self.foot.setVisible(height >= 700)


class PageHost(QWidget):
    """Scrollable page with optional pinned footer and capped content width."""

    def __init__(self, page: QWidget):
        super().__init__()
        self.page = page
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrap = QWidget()
        self.wrap_layout = QHBoxLayout(wrap)
        self.wrap_layout.setContentsMargins(20, 16, 20, 12)
        self.wrap_layout.addWidget(page, 1)
        self.scroll.setWidget(wrap)
        v.addWidget(self.scroll, 1)
        self.footer_layout = None
        footer = getattr(page, "footer", None)
        if isinstance(footer, QWidget):
            fw = QWidget()
            fw.setObjectName("PageFooter")
            self.footer_layout = QHBoxLayout(fw)
            self.footer_layout.setContentsMargins(20, 2, 20, 12)
            self.footer_layout.addWidget(footer, 1)
            v.addWidget(fw)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        side = max(20, (self.width() - MAX_CONTENT_WIDTH) // 2)
        self.wrap_layout.setContentsMargins(side, 16, side, 12)
        if self.footer_layout is not None:
            self.footer_layout.setContentsMargins(side, 2, side, 12)


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.ctl = controller
        self.s = controller.s
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.QIcon(art.app_icon_pixmap(256)))
        self._initial_size()
        self.setMinimumSize(1100, 680)
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

    def _initial_size(self) -> None:
        """Open at a size that fits the screen's available area (taskbar and scaling respected)."""
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            self.resize(1600, 980)
            return
        avail = scr.availableGeometry()
        w = min(1760, max(1100, avail.width() - 40))
        h = min(1040, max(680, avail.height() - 60))
        self.resize(w, h)

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
        if self.statusBar() is None or not isinstance(self.statusBar(), QStatusBar):
            self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(True)
        self.pages = {}
        self.hosts = {}
        for key, cls in (("dashboard", DashboardPage), ("hardware", HardwarePage), ("library", LibraryPage),
                         ("profiles", ProfilesPage), ("benchmark", BenchmarkPage), ("install", InstallPage),
                         ("backups", BackupsPage), ("settings", SettingsPage)):
            page = cls(self.ctl, self)
            page.setObjectName(f"page_{key}")
            host = PageHost(page)
            self.pages[key] = page
            self.hosts[key] = host
            self.stack.addWidget(host)
        self._apply_density()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_density()

    def _apply_density(self) -> None:
        if not hasattr(self, "header"):
            return
        h = self.height()
        level = next(i for i, (threshold, *_rest) in enumerate(HEADER_LEVELS) if h >= threshold)
        self.header.set_level(level)
        self.sidebar.set_compact(h)
        for page in getattr(self, "pages", {}).values():
            if hasattr(page, "set_density"):
                page.set_density(level)

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
                 "\n".join(t(m) for m in res.messages[:12]), error=not res.ok)
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
