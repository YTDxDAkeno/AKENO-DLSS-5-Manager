"""Main window: sidebar navigation and the page stack.

Layout rules (3.0 design system, docs/DESIGN_SYSTEM.md):
* No banner header: each page starts with its own title and actions, over a faint strip of the AKENO artwork,
  so every window size gives its height to content. The sidebar footer says which AKENO and compatibility
  database this is.
* Pages adapt to the window height (three density levels), so a 1920x1080 screen shows the whole dashboard and
  small or scaled screens (125-200 %) keep the most room for content.
* Each page sits in a scroll area, so nothing is ever cut off; a page may expose a ``footer`` widget (the
  dashboard's action buttons) that stays pinned below the scroll area and is always reachable.
* On very wide (ultrawide) windows page content is capped and centred instead of stretched.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from akeno import APP_NAME, __version__
from akeno.i18n import t
from akeno.ui import art, assets, icons, theme
from akeno.ui.widgets import confirm, info, label

# The navigation: sections, each with pages; a group (like "DLSS 5 Manager") holds pages shown indented when it is
# open. NAV is the flat list of every page in display order (its index is the page's place in the stack).
SECTIONS = [
    ("HOME", [("home", "Dashboard", "home"), ("library", "My Games", "gamepad"),
              ("recent", "Recently Played", "clock")]),
    ("GAMING", [("group", "dlss", "DLSS 5 Manager", "sparkle", [
        ("fivem", "FiveM Legacy", "fivem"), ("downloads", "Downloads", "download"),
        ("backups", "Restore / Backups", "restore"), ("hardware", "Hardware", "chip"),
        ("advanced", "Advanced", "gauge")]),
        ("booster", "FPS Booster", "gauge"), ("benchmark", "Benchmark", "chart"),
        ("installed", "Installed Mods", "layers")]),
    ("SYSTEM", [("doctor", "System Doctor", "stethoscope"), ("cleaner", "Smart Cleaner", "broom"),
                ("network", "Network Lab", "wifi")]),
    ("DESKTOP", [("wallpapers", "Wallpapers", "image"), ("dock", "Dock", "dock"), ("widgets", "Widgets", "grid"),
                 ("themes", "Theme Workshop", "palette")]),
    ("ONLINE", [("community", "Community", "globe"), ("sync", "Cloud Sync", "refresh")]),
    ("ACCOUNT", [("account", "My Account", "user"), ("license", "License", "key"), ("updates", "Updates", "download"),
                 ("settings", "Settings", "gear")]),
]


def _flatten() -> list[tuple[str, str, str]]:
    out = []
    for _, items in SECTIONS:
        for item in items:
            if item[0] == "group":
                out.extend(item[4])
            else:
                out.append(item)
    return out


NAV = _flatten()
GROUPS = {item[1]: [k for k, _, _ in item[4]] for _, items in SECTIONS for item in items if item[0] == "group"}
# Pages shown as tabs of "Advanced" (diagnostics and the v1 per-backend installer).
ADVANCED = ("dashboard", "profiles", "install")

MAX_CONTENT_WIDTH = 2300          # ultrawide: centre the page instead of stretching cards to 3000+ px
ART_HEIGHT = 230                  # the faint artwork behind the top of each page
# density levels by window height: 0 roomy, 1 compact, 2 tight (pages with set_density adapt to them)
DENSITY_LEVELS = (960, 800, 0)


class NavButton(QPushButton):
    """Navigation entry painted to the design grid: icon, label, a selection bar and, for a group, a chevron.

    It stays a QPushButton (checkable, clicked, text, keyboard focus), so it behaves like one for keyboard,
    screen readers and tests."""

    def __init__(self, text: str, icon_name: str, child: bool = False, group: bool = False):
        super().__init__(text)
        self.icon_name = icon_name
        self.child = child
        self.group = group
        self.opened = False
        self._hover = False
        self.setCheckable(not group)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(29 if child else 33)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self.active = False                 # a group whose page is open

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + (66 if self.child else 52), self.minimumHeight())

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 1, 0, -1)
        on = self.isChecked()
        a = theme.accent()
        if on:
            bg = QColor(a.base)
            bg.setAlpha(36)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, theme.RADIUS_BUTTON, theme.RADIUS_BUTTON)
            p.setBrush(QColor(a.base))
            p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 8, 3, r.height() - 16), 1.5, 1.5)
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(r, theme.RADIUS_BUTTON, theme.RADIUS_BUTTON)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            p.setPen(QPen(QColor(a.base if on else theme.BORDER_BTN), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), theme.RADIUS_BUTTON, theme.RADIUS_BUTTON)
        strong = on or self.active
        color = QColor(theme.TEXT if (strong or self._hover) else theme.TEXT_DIM)
        size = 16 if self.child else 18
        x = 34 if self.child else 12
        icon = icons.pixmap(self.icon_name, theme.ACCENT_TEXT if on else color.name(), size)
        p.drawPixmap(QRectF(x, (self.height() - size) / 2, size, size), icon, QRectF())
        font = self.font()
        font.setWeight(QFont.Weight.DemiBold if strong else QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(color)
        tx = x + size + 12
        right = 28 if self.group else 8
        text_rect = QRectF(tx, 0, self.width() - tx - right, self.height())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_rect.width())))
        if self.group:                      # chevron: down when open, right when closed
            p.setPen(QPen(QColor(theme.TEXT_MUTED), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            cx, cy = self.width() - 16, self.height() / 2
            path = QPainterPath()
            if self.opened:
                path.moveTo(cx - 3.5, cy - 1.8)
                path.lineTo(cx, cy + 1.8)
                path.lineTo(cx + 3.5, cy - 1.8)
            else:
                path.moveTo(cx - 1.8, cy - 3.5)
                path.lineTo(cx + 1.8, cy)
                path.lineTo(cx - 1.8, cy + 3.5)
            p.drawPath(path)


class Sidebar(QFrame):
    def __init__(self, on_nav):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(theme.SIDEBAR_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 16, 12, 0)
        outer.setSpacing(0)
        self.logo = QLabel()
        self.logo.setAccessibleName("AKENO")
        word = art.wordmark(36)
        if not word.isNull():
            self.logo.setPixmap(word)
            self.logo.setContentsMargins(10, 0, 0, 0)
            outer.addWidget(self.logo)
        else:   # asset missing: icon + text
            row = QHBoxLayout()
            self.logo.setPixmap(art.logo_mark(36))
            row.addWidget(self.logo)
            name = QLabel("AKENO")
            name.setStyleSheet("font-size: 18pt; font-weight: 700; letter-spacing: 2px;")
            row.addWidget(name, 1)
            outer.addLayout(row)
        outer.addSpacing(12)
        # the navigation scrolls on its own when a short window cannot show every section
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 2, 8)
        lay.setSpacing(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)
        self.scroll, self.nav_holder = scroll, holder
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self.groups: dict[str, NavButton] = {}
        self.headers: dict[str, QLabel] = {}
        self._open: set[str] = set()
        self._on_nav = on_nav
        index = {k: i for i, (k, _, _) in enumerate(NAV)}
        for n, (section, items) in enumerate(SECTIONS):
            head = label(t(section), "overline")
            head.setContentsMargins(12, 11 if n else 0, 0, 3)
            self.headers[section] = head
            lay.addWidget(head)
            for item in items:
                if item[0] == "group":
                    _, gkey, gtext, gicon, children = item
                    g = NavButton(t(gtext), gicon, group=True)
                    g.clicked.connect(lambda _=False, k=gkey: self._toggle(k))
                    self.groups[gkey] = g
                    lay.addWidget(g)
                    for key, text, ic in children:
                        b = self._nav_button(text, ic, index[key], child=True)
                        b.clicked.connect(lambda checked=False, k=key: on_nav(k))
                        self.buttons[key] = b
                        lay.addWidget(b)
                else:
                    key, text, ic = item
                    b = self._nav_button(text, ic, index[key])
                    b.clicked.connect(lambda checked=False, k=key: on_nav(k))
                    self.buttons[key] = b
                    lay.addWidget(b)
        lay.addStretch(1)
        self._apply_groups()
        # Footer: what this copy of AKENO is (version, compatibility database, managed installs).
        foot = QFrame()
        foot.setObjectName("SidebarFooter")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(12, 8, 8, 10)
        fl.setSpacing(2)
        self.version = label(f"{APP_NAME} {__version__}", "muted")
        self.status = StatusLine()
        fl.addWidget(self.version)
        fl.addWidget(self.status)
        outer.addWidget(foot)

    def _nav_button(self, text: str, ic: str, index: int | None, child: bool = False) -> NavButton:
        b = NavButton(t(text), ic, child=child)
        b.setToolTip(t(text) + (f"  (Ctrl+{index + 1})" if index is not None and index < 9 else ""))
        if index is not None:
            self.group.addButton(b)
        return b

    def _toggle(self, group: str) -> None:
        if group in self._open:
            self._open.discard(group)
            self._apply_groups()
        else:
            self._open.add(group)
            self._apply_groups()
            current = next((k for k, b in self.buttons.items() if b.isChecked()), None)
            if current not in GROUPS[group]:
                self._on_nav(GROUPS[group][0])

    def _apply_groups(self) -> None:
        for gkey, children in GROUPS.items():
            opened = gkey in self._open
            for key in children:
                self.buttons[key].setVisible(opened)
            self.groups[gkey].opened = opened
            self.groups[gkey].update()

    def select(self, key: str) -> None:
        # a group is open while one of its pages is (and when the user opens it); leaving it closes it again
        for gkey, children in GROUPS.items():
            opened = key in children
            if opened != (gkey in self._open):
                (self._open.add if opened else self._open.discard)(gkey)
                self._apply_groups()
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        for gkey, g in self.groups.items():
            g.active = key in GROUPS[gkey]
            g.update()
        if key in self.buttons:                       # the chosen page stays visible in a scrolled navigation
            QTimer.singleShot(0, lambda b=self.buttons[key]: self.scroll.ensureWidgetVisible(b, 0, 8))

    def set_compact(self, height: int) -> None:
        """Short windows: the footer gives its room to the navigation."""
        self.version.setVisible(height >= 760)


class StatusLine(QWidget):
    """A state dot and one elided line (the sidebar footer's compatibility status)."""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        lay.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        from akeno.ui.widgets import ElidedLabel
        self.text = ElidedLabel("", "muted")
        lay.addWidget(self.text, 1)
        self.set("", "pending")

    def set(self, text: str, state: str) -> None:
        color = theme.STATE_COLORS.get(state, theme.TEXT_MUTED)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.text.set_text(text)
        self.setAccessibleName(text)


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
        if not getattr(page, "scrolls", True):
            # the page fills the window and scrolls its own parts (My Games: list and game page)
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrap = QWidget()
        self.wrap_layout = QHBoxLayout(wrap)
        self.wrap_layout.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_TOP, theme.PAGE_MARGIN, 20)
        self.wrap_layout.addWidget(page, 1)
        self.scroll.setWidget(wrap)
        v.addWidget(self.scroll, 1)
        self.footer_layout = None
        footer = getattr(page, "footer", None)
        if isinstance(footer, QWidget):
            fw = QWidget()
            fw.setObjectName("PageFooter")
            self.footer_layout = QHBoxLayout(fw)
            self.footer_layout.setContentsMargins(theme.PAGE_MARGIN, 4, theme.PAGE_MARGIN, 14)
            self.footer_layout.addWidget(footer, 1)
            v.addWidget(fw)
        self._art = None
        self._art_key = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        side = max(theme.PAGE_MARGIN, (self.width() - MAX_CONTENT_WIDTH) // 2)
        self.wrap_layout.setContentsMargins(side, theme.PAGE_TOP, side, 20)
        if self.footer_layout is not None:
            self.footer_layout.setContentsMargins(side, 4, side, 14)

    def paintEvent(self, e):
        """The AKENO artwork, faint, behind the top right of every page; it fades out before the content."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), dpr)
        if self._art_key != key:
            self._art, self._art_key = art.page_backdrop(self.width(), ART_HEIGHT, dpr), key
        if self._art is not None and not self._art.isNull():
            QPainter(self).drawPixmap(0, 0, self._art)


class MainWindow(QMainWindow):
    def __init__(self, controller, license_manager=None):
        super().__init__()
        self.ctl = controller
        self.s = controller.s
        from akeno.licensing import manager
        self.license = license_manager or manager()
        from akeno.desktop.client import DesktopClient
        from akeno.ui.game_sessions import GameSessions
        self.sessions = GameSessions(self.s, self)
        self.desktop = DesktopClient()
        self.desktop.on_show = lambda: (self.showNormal(), self.raise_(), self.activateWindow())
        from akeno.ui.notifications import Notifier
        self.notifier = Notifier(self.s.settings, None, self)       # the tray icon is attached by the app
        from akeno.sync.data import LocalData
        from akeno.sync.engine import SyncEngine
        self.sync = SyncEngine(LocalData(self.s))
        self.sync_timer = QTimer(self)                  # Cloud Sync: soon after the start, then every 15 minutes
        self.sync_timer.setInterval(15 * 60_000)
        self.sync_timer.timeout.connect(self._auto_sync)
        self.sync_timer.start()
        self.sync_first = QTimer(self)                  # a child timer: it ends with the window
        self.sync_first.setSingleShot(True)
        self.sync_first.setInterval(20_000)
        self.sync_first.timeout.connect(self._auto_sync)
        self.sync_first.start()
        # Desktop Studio pauses wallpapers while a library game runs
        self.sessions.started.connect(lambda *_: self.desktop.game(True))
        self.sessions.stopped.connect(lambda *_: self.desktop.game(bool(self.sessions.running())))
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.QIcon(art.app_icon_pixmap(256)))
        self._initial_size()
        self.setMinimumSize(self._minimum())
        self._build()
        controller.navigate.connect(self.go)
        controller.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        controller.settings_changed.connect(self._setting_changed)
        controller.games_changed.connect(self._status_footer)
        controller.installs_changed.connect(self._status_footer)
        self._status_footer()
        for i, (key, _, _) in enumerate(NAV[:9]):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=key: self.go(k))
        self.go("home")

    @staticmethod
    def _minimum() -> QSize:
        """1100 x 680, but never more than the screen offers (200 % scaling on a small screen)."""
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return QSize(1100, 680)
        avail = scr.availableGeometry()
        return QSize(min(1100, avail.width()), min(680, avail.height()))

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
        from akeno.ui.pages.account import AccountPage, LicensePage
        from akeno.ui.pages.advanced import AdvancedPage
        from akeno.ui.pages.backups import BackupsPage
        from akeno.ui.pages.booster import BoosterPage
        from akeno.ui.pages.desktop import DockPage, ThemesPage, WallpapersPage, WidgetsPage
        from akeno.ui.pages.online import CloudSyncPage, CommunityPage
        from akeno.ui.pages.updates import UpdatesPage
        from akeno.ui.pages.home import HomePage
        from akeno.ui.pages.recent import RecentPage
        from akeno.ui.pages.benchmark import BenchmarkPage
        from akeno.ui.pages.dashboard import DashboardPage
        from akeno.ui.pages.downloads import DownloadsPage
        from akeno.ui.pages.fivem import FiveMPage
        from akeno.ui.pages.hardware import HardwarePage
        from akeno.ui.pages.install import InstallPage
        from akeno.ui.pages.installed import InstalledPage
        from akeno.ui.pages.library import LibraryPage
        from akeno.ui.pages.profiles import ProfilesPage
        from akeno.ui.pages.settings import SettingsPage
        from akeno.ui.pages.doctor import DoctorPage
        from akeno.ui.pages.cleaner import CleanerPage
        from akeno.ui.pages.network import NetworkPage

        root = QWidget()
        root.setObjectName("Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar(self.go)
        h.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        if self.statusBar() is None or not isinstance(self.statusBar(), QStatusBar):
            self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(True)
        self.pages = {}
        self.hosts = {}
        advanced = {}
        for key, cls in (("dashboard", DashboardPage), ("profiles", ProfilesPage), ("install", InstallPage)):
            page = cls(self.ctl, self)
            page.setObjectName(f"page_{key}")
            advanced[key] = page
        main = {"library": LibraryPage, "fivem": FiveMPage, "installed": InstalledPage, "downloads": DownloadsPage,
                "backups": BackupsPage, "hardware": HardwarePage, "settings": SettingsPage, "account": AccountPage,
                "license": LicensePage, "recent": RecentPage, "booster": BoosterPage, "benchmark": BenchmarkPage,
                "wallpapers": WallpapersPage, "dock": DockPage, "widgets": WidgetsPage, "themes": ThemesPage,
                "community": CommunityPage, "sync": CloudSyncPage, "updates": UpdatesPage, "home": HomePage,
                "doctor": DoctorPage, "cleaner": CleanerPage, "network": NetworkPage}
        for key, _, _ in NAV:
            page = AdvancedPage(self.ctl, self, advanced) if key == "advanced" else main[key](self.ctl, self)
            page.setObjectName(f"page_{key}")
            host = PageHost(page)
            self.pages[key] = page
            self.hosts[key] = host
            self.stack.addWidget(host)
        self.pages.update(advanced)
        self._apply_density()

    def _auto_sync(self) -> None:
        page = getattr(self, "pages", {}).get("sync")
        if page is not None and self.sync.state["enabled"] and self.sync.state["folder"] and \
                not self.sync.lock.locked():
            page.sync_now(quiet=True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_density()

    def _apply_density(self) -> None:
        if not hasattr(self, "sidebar"):
            return
        h = self.height()
        level = next(i for i, threshold in enumerate(DENSITY_LEVELS) if h >= threshold)
        self.density = level
        self.sidebar.set_compact(h)
        for page in getattr(self, "pages", {}).values():
            if hasattr(page, "set_density"):
                page.set_density(level)

    def go(self, key: str) -> None:
        keys = [k for k, _, _ in NAV]
        tab = None
        if key in ADVANCED:
            key, tab = "advanced", key
        if key not in keys:
            return
        self.stack.setCurrentIndex(keys.index(key))
        self.sidebar.select(key)
        page = self.pages[key]
        if tab:
            page.show_tab(tab)
        elif hasattr(page, "on_show"):
            page.on_show()

    def current_page(self) -> str:
        key = [k for k, _, _ in NAV][self.stack.currentIndex()]
        if key == "advanced":
            return self.pages["advanced"].keys[self.pages["advanced"].tabs.currentIndex()]
        return key

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
                return self.s.restore_snapshot(tx_id, force=True)
            if tx["backend_id"] == "stack" and tx["state"] == "installed" and tx["kind"] in ("install", "swap"):
                # the stack also takes back what lives outside the transaction (FiveM's ReShade notice, its logs)
                return self.s.stack.uninstall(tx["game_id"], force=force)
            return self.s.restore(tx_id, force=force)

        def done(res):
            self.ctl.installs_changed.emit()
            self.ctl.games_changed.emit()
            info(self, t("Restore"), t("Original files restored.") if res.ok else t("Restore did not complete."),
                 "\n".join(t(m) for m in res.messages[:12]), error=not res.ok)
        self.ctl.run("restore", work, done, lambda e: info(self, t("Restore"), str(e), error=True))

    def _status_footer(self) -> None:
        # one game with an install plus later preset changes is one managed install, not a transaction count
        n = len({t_["game_id"] for t_ in self.s.db.transactions() if t_["state"] == "installed"})
        text = t("Compatibility DB {v}", v=self.s.compat_db.version)
        if n:
            text += " · " + t("{n} managed install(s)", n=n)
        self.sidebar.status.set(text, "ok")
        self.sidebar.status.setToolTip(t("Package catalog {c}; compatibility database {v} ({o}).",
                                         c=self.s.catalog.version, v=self.s.compat_db.version,
                                         o=t(self.s.compat_db.origin)))

    def _setting_changed(self, key: str) -> None:
        if key not in ("language", "*", "theme_accent"):
            self.statusBar().showMessage(t("Saved."), 2000)     # every preference applies at once; say so
        if key in ("language", "*", "theme_accent"):
            # Rebuild every page so all texts and styles pick up the change.
            from akeno import i18n
            i18n.set_language(self.s.settings.get("language"))
            from PySide6.QtWidgets import QApplication
            theme.apply(QApplication.instance(), self.s.settings.get("theme_accent"))
            current = self.current_page()
            old = self.takeCentralWidget()
            old.deleteLater()
            self._build()
            QTimer.singleShot(0, lambda: self.go(current))
