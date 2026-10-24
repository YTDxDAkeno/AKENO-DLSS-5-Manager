"""DESKTOP: Wallpapers, Dock, Widgets and the Theme Workshop. The pages edit ``desktop.json``; the desktop host
(its own process, see akeno/desktop/host.py) applies every change live and is started when something is switched
on."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QStackedWidget, QTabBar, QTimeEdit, QToolButton,
                               QVBoxLayout, QWidget)

from shiboken6 import isValid

from akeno.desktop import config as cfgmod
from akeno.desktop import media, themes, winapi
from akeno.desktop import widgets as dwidgets
from akeno.i18n import msg, t
from akeno.ui import icons, theme
from akeno.ui.widgets import (Card, ElidedLabel, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow,
                              StatusPill, button, clear_layout, confirm, fit_control, form_grid, info, label)

KIND_TEXT = {"image": "Picture", "gif": "GIF animation", "video": "Video", "web": "Web wallpaper"}
MODE_TEXT = {"single": "One wallpaper", "rotate": "Change every few minutes", "schedule": "By time of day"}
FIT_TEXT = {"fill": "Fill", "fit": "Fit", "stretch": "Stretch", "center": "Centre"}
EDGE_TEXT = {"bottom": "Bottom", "top": "Top", "left": "Left", "right": "Right"}
STATE_TEXT = {"starting": "Starting", "running": "Running", "waiting": "Starting again shortly",
              "failed": "Not shown", "stopped": "Stopped"}
PAUSE_TEXT = {"game": "A game is running", "fullscreen": "A fullscreen program is in front",
              "battery": "The PC runs on battery", "remote": "A remote desktop session is active",
              "covered": "A window covers the whole monitor"}
WIDGET_ICONS = {"clock": "clock", "calendar": "clock", "cpu": "cpu", "gpu": "chip", "temperature": "gauge",
                "ram": "database", "vram": "chip", "network": "globe", "disk": "database", "fps": "chart",
                "shortcuts": "link", "music": "play", "visualizer": "chart", "notes": "file", "todo": "check",
                "weather": "globe", "uptime": "clock", "downloads": "download", "license": "key"}
MEDIA_FILTER = "Wallpapers (*.jpg *.jpeg *.png *.bmp *.webp *.gif *.mp4 *.webm *.mkv *.mov *.m4v *.html *.htm)"


def _row(*widgets) -> QWidget:
    holder = QWidget()
    flow = FlowLayout(spacing=8)
    for w in widgets:
        flow.addWidget(w)
    holder.setLayout(flow)
    return holder


def _line(text: str, widget: QWidget) -> QHBoxLayout:
    """A setting: its name on the left, the control right-aligned at a width that suits it (design system)."""
    line = QHBoxLayout()
    line.setSpacing(16)
    name = label(text)
    name.setWordWrap(True)
    line.addWidget(name, 1)
    line.addWidget(fit_control(widget), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return line


def _combo(choices: dict, accessible: str) -> QComboBox:
    box = QComboBox()
    box.setAccessibleName(accessible)
    for key, text in choices.items():
        box.addItem(t(text), key)
    return box


def screens() -> list[tuple[str, str]]:
    """(screen key, readable label) of each monitor (the key is Qt's screen name; see winapi.screen_key)."""
    out = []
    primary = QGuiApplication.primaryScreen()
    all_screens = QGuiApplication.screens()
    for i, s in enumerate(all_screens):
        g = s.geometry()
        text = t("Monitor {n}", n=i + 1) + f" · {round(g.width() * s.devicePixelRatio())}×" \
            f"{round(g.height() * s.devicePixelRatio())}"
        if s is primary:
            text += " · " + t("main monitor")
        out.append((winapi.screen_key(s, all_screens), text))
    return out


class DesktopPage(QWidget):
    """Shared by the four pages: the configuration and the connection to the desktop host."""

    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.desktop = window.desktop
        self.cfg = cfgmod.load()
        self._loading = False

    def reload_cfg(self) -> None:
        self.cfg = cfgmod.load()

    def switch_on(self) -> None:
        """Switching something on also ends a Stop."""
        self.cfg["enabled"] = True

    def save(self) -> None:
        if self._loading:
            return
        cfgmod.save(self.cfg)
        self.desktop.ensure(self.cfg)
        for page in self.win.pages.values():
            if isinstance(page, DesktopPage) and page is not self:
                page.cfg = cfgmod.load()

    def header(self, root: QVBoxLayout, title: str, text: str) -> None:
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        root.addWidget(PageHeader(t(title), t(text)))


def _scroll(widget: QWidget) -> QScrollArea:
    """A tab that scrolls on its own (the wallpaper page fills the window)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


FILTERS = {"all": msg("All"), "video": msg("Videos"), "image": msg("Pictures"), "gif": msg("GIFs"), "web": msg("Web"),
           "favorites": msg("Favourites")}
SORT_TEXT = {"recent": msg("Recently added"), "name": msg("Name"), "favorites": msg("Favourites first"),
             "kind": msg("Type"), "size": msg("Size")}
TABS = (msg("Library"), msg("Discover"), msg("Playback & performance"), msg("Playlists & schedule"),
        msg("Desktop Runtime"))


class WallpapersPage(DesktopPage):
    """Wallpapers: a visual library first (pick, preview, apply), the technical settings on their own tabs."""

    scrolls = False

    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        from akeno.desktop import library as libmod
        from akeno.ui import desktop_media as dm
        from akeno.ui.account_ui import Banner
        self.dm = dm
        self.lib = libmod.Library()
        self.lib.sync_from_config(self.cfg)                # AKENO 3.0 playlists become library entries
        self.status_cache: dict | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self.pill = StatusPill(t("Not running"), "pending")
        self.btn_import = button(t("Import"), "plus", "primary")
        menu = QMenu(self.btn_import)
        menu.addAction(icons.icon("image", theme.TEXT, 16), t("Files…"), self._add_files)
        menu.addAction(icons.icon("folder", theme.TEXT, 16), t("Folder…"), self._add_folder)
        menu.addAction(icons.icon("globe", theme.TEXT, 16), t("Web wallpaper folder…"), self._add_web_folder)
        menu.addAction(icons.icon("download", theme.TEXT, 16), t("Lively wallpaper package…"), self._add_package)
        self.btn_import.setMenu(menu)
        self.btn_online = button(t("Browse online"), "globe", on_click=lambda: self.tabs.setCurrentIndex(1))
        root.addWidget(PageHeader(t("Wallpapers"), t("Live wallpapers behind your desktop icons: videos, pictures, "
                                                     "GIFs and web wallpapers."),
                                  [self.pill, self.btn_online, self.btn_import]))
        self.banner = Banner()
        root.addWidget(self.banner)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        for text in TABS:
            self.tabs.addTab(t(text).replace("&", "&&"))
        root.addWidget(self.tabs)
        self.views = QStackedWidget()
        root.addWidget(self.views, 1)
        self.views.addWidget(self._library_tab())
        self.sources: SourcesPanel | None = None
        self.sources_holder = QWidget()
        QVBoxLayout(self.sources_holder).setContentsMargins(0, 0, 0, 0)
        self.views.addWidget(_scroll(self.sources_holder))
        self.views.addWidget(_scroll(self._playback_tab()))
        self.views.addWidget(_scroll(self._playlist_tab()))
        self.views.addWidget(_scroll(self._runtime_tab()))
        self.tabs.currentChanged.connect(self._show_tab)
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh_status)
        thumbs = dm.thumbnails()
        thumbs.meta.connect(self._meta)              # bound methods: Qt drops them with the page
        thumbs.ready.connect(self._thumb_ready)
        self._fill_monitors()
        self.refresh_library()

    # -- library tab -------------------------------------------------------------------------------------------------
    def _library_tab(self) -> QWidget:
        dm = self.dm
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search your wallpapers…"))
        self.search.setAccessibleName(t("Search your wallpapers"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMaximumWidth(340)
        self.search.textChanged.connect(lambda _: self.refresh_library())
        bar.addWidget(self.search, 1)
        self.filter = dm.Segmented({k: t(v) for k, v in FILTERS.items()}, "all")
        self.filter.changed.connect(lambda _: self.refresh_library())
        bar.addWidget(self.filter, 2)
        self.collection = QComboBox()
        self.collection.setAccessibleName(t("Collection"))
        self.collection.currentIndexChanged.connect(lambda _: self.refresh_library())
        bar.addWidget(self.collection)
        self.sort = _combo(SORT_TEXT, t("Sort"))
        self.sort.currentIndexChanged.connect(lambda _: self.refresh_library())
        bar.addWidget(self.sort)
        lay.addLayout(bar)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.gallery_stack = QStackedWidget()
        self.gallery = dm.Gallery()
        self.gallery.selectionModel().currentChanged.connect(lambda *_: self._select())
        self.gallery.dropped.connect(self._dropped)
        self.gallery.activated_item.connect(lambda item: item is not None and self.apply(item))
        self.gallery.menu_requested.connect(self._item_menu)
        self.gallery_stack.addWidget(self.gallery)
        self.empty = EmptyState("image", t("Your wallpaper library is empty"),
                                t("Import videos, pictures, GIFs or web wallpapers, or drop files here. "
                                  "Discover finds free animated wallpapers online."),
                                button(t("Import files…"), "plus", "primary", on_click=self._add_files))
        self.empty.setAcceptDrops(True)
        self.gallery_stack.addWidget(self.empty)
        self.no_match = EmptyState("search", t("Nothing matches"), t("Try another search or filter."))
        self.gallery_stack.addWidget(self.no_match)
        body.addWidget(self.gallery_stack, 1)
        body.addWidget(self._details())
        lay.addLayout(body, 1)
        self.count = label("", "muted")
        lay.addWidget(self.count)
        return tab

    def _details(self) -> QWidget:
        dm = self.dm
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(400)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        self.preview = dm.PreviewPlayer()
        self.preview.setFixedHeight(round(368 * 9 / 16) + 38)
        self.preview.fullscreen_requested.connect(self._fullscreen)
        lay.addWidget(self.preview)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.d_title = ElidedLabel("", "h2")
        head.addWidget(self.d_title, 1)
        self.btn_fav = QToolButton()
        self.btn_fav.setCheckable(True)
        self.btn_fav.setAutoRaise(True)
        self.btn_fav.setToolTip(t("Favourite"))
        self.btn_fav.setAccessibleName(t("Favourite"))
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.toggled.connect(self._favorite)
        head.addWidget(self.btn_fav)
        self.btn_more = QToolButton()
        self.btn_more.setAutoRaise(True)
        self.btn_more.setText("···")
        self.btn_more.setToolTip(t("More"))
        self.btn_more.setAccessibleName(t("More"))
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.btn_more)
        self.more_menu.addAction(t("Rename…"), self._rename)
        self.more_menu.addAction(t("Collections…"), self._collections)
        self.more_menu.addAction(t("Add to the playlist"), self._add_to_playlist)
        self.more_menu.addAction(t("Show in folder"), self._show_in_folder)
        self.more_menu.addSeparator()
        self.more_menu.addAction(t("Remove from library"), self._remove_item)
        self.btn_more.setMenu(self.more_menu)
        head.addWidget(self.btn_more)
        lay.addLayout(head)
        self.d_facts = label("", "dim", shrink=True)
        lay.addWidget(self.d_facts)
        self.d_source = label("", "muted", shrink=True)
        lay.addWidget(self.d_source)
        lay.addWidget(label(t("Show on"), "overline"))
        self.monitor_map = dm.MonitorMap(self._monitor_thumb)
        self.monitor_map.changed.connect(self._monitors_changed)
        lay.addWidget(self.monitor_map)
        mons = QHBoxLayout()
        self.monitor_note = label("", "muted", shrink=True)
        mons.addWidget(self.monitor_note, 1)
        mons.addWidget(button(t("All monitors"), kind="link", on_click=self.monitor_map.select_all))
        lay.addLayout(mons)
        self.fit_choice = dm.Segmented({k: t(v) for k, v in FIT_TEXT.items()}, "fill")
        self.fit_choice.changed.connect(self.preview.set_fit)
        lay.addWidget(self.fit_choice)
        self.btn_apply = button(t("Apply wallpaper"), "check", "primary", on_click=lambda: self.apply())
        self.btn_apply.setMinimumHeight(40)
        lay.addWidget(self.btn_apply)
        self.btn_stop_wp = button(t("Stop wallpaper"), "stop", on_click=self.stop_wallpaper)
        lay.addWidget(self.btn_stop_wp)
        lay.addStretch(1)
        outer.addWidget(_scroll(inner))
        return panel

    # -- other tabs ----------------------------------------------------------------------------------------------
    def _monitor_combo(self) -> QComboBox:
        box = QComboBox()
        box.setAccessibleName(t("Monitor"))
        return box

    def _playback_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        row = ResponsiveRow(spacing=theme.CARD_GAP)
        opts = Card(t("Playback"), "sliders", right=None)
        self.pb_monitor = self._monitor_combo()
        self.pb_monitor.currentIndexChanged.connect(lambda _: self._load_playback())
        opts.body.addLayout(_line(t("Monitor"), self.pb_monitor))
        self.fit = _combo(FIT_TEXT, t("Picture fit"))
        self.fit.currentIndexChanged.connect(lambda _: self._set("fit", self.fit.currentData()))
        opts.body.addLayout(_line(t("Picture fit"), self.fit))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setAccessibleName(t("Video and web sound"))
        self.volume.valueChanged.connect(lambda v: self._set("volume", int(v)))
        opts.body.addLayout(_line(t("Video and web sound"), self.volume))
        self.fps = QSpinBox()
        self.fps.setRange(5, 144)
        self.fps.setSuffix(" FPS")
        self.fps.setAccessibleName(t("Frame limit for web wallpapers"))
        self.fps.valueChanged.connect(lambda v: self._set("fps_limit", int(v)))
        opts.body.addLayout(_line(t("Frame limit for web wallpapers"), self.fps))
        self.chk_interactive = QCheckBox(t("Web wallpapers follow the mouse on the desktop"))
        self.chk_interactive.toggled.connect(lambda v: self._set("interactive", bool(v)))
        self.chk_audio = QCheckBox(t("Web wallpapers react to the sound that is playing"))
        self.chk_audio.toggled.connect(lambda v: self._set("audio_reactive", bool(v)))
        opts.body.addWidget(self.chk_interactive)
        opts.body.addWidget(self.chk_audio)
        opts.body.addWidget(label(t("Web wallpapers run without network access and can only read their own "
                                    "folder."), "muted", shrink=True))
        row.add(opts, 1)
        rules = Card(t("Pause wallpapers when"), "gauge")
        self.rule_checks = {}
        for key, text in PAUSE_TEXT.items():
            box = QCheckBox(t(text))
            box.toggled.connect(lambda v, k=key: self._set_rule(k, bool(v)))
            self.rule_checks[key] = box
            rules.body.addWidget(box)
        self.cpu_limit = QSpinBox()
        self.cpu_limit.setRange(0, 100)
        self.cpu_limit.setSuffix(" %")
        self.cpu_limit.setSpecialValueText(t("Never"))
        self.cpu_limit.setAccessibleName(t("The CPU is busier than"))
        self.cpu_limit.valueChanged.connect(lambda v: self._set_rule("cpu_percent", int(v)))
        rules.body.addLayout(_line(t("The CPU is busier than"), self.cpu_limit))
        rules.body.addWidget(label(t("A paused wallpaper keeps its last frame and stops decoding: no CPU or graphics "
                                     "time until it plays again."), "muted", shrink=True))
        row.add(rules, 1)
        root.addWidget(row)
        root.addStretch(1)
        return tab

    def _playlist_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        row = ResponsiveRow(spacing=theme.CARD_GAP)
        pl = Card(t("Playlist"), "layers")
        self.monitor = self._monitor_combo()
        self.monitor.currentIndexChanged.connect(self._load_monitor)
        pl.body.addLayout(_line(t("Monitor"), self.monitor))
        self.chk_enabled = QCheckBox(t("Show an AKENO wallpaper on this monitor"))
        self.chk_enabled.toggled.connect(lambda v: self._set("enabled", bool(v), playlist_tab=True))
        pl.body.addWidget(self.chk_enabled)
        self.mode = _combo(MODE_TEXT, t("Playlist mode"))
        self.mode.currentIndexChanged.connect(lambda _: self._set("mode", self.mode.currentData(), playlist_tab=True))
        pl.body.addLayout(_line(t("Mode"), self.mode))
        self.list = QListWidget()
        self.list.setAccessibleName(t("Playlist"))
        self.list.setIconSize(QSize(112, 63))
        self.list.setSpacing(3)
        self.list.setMinimumHeight(220)
        self.list.currentRowChanged.connect(self._load_item)
        self.list_empty = EmptyState("layers", t("No playlist on this monitor"),
                                     t("Apply a wallpaper from the library, or add several to rotate them."))
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.list_empty)
        self.list_stack.setMinimumHeight(220)
        pl.body.addWidget(self.list_stack)
        pl.body.addWidget(_row(button(t("Remove"), "trash", on_click=self._remove),
                               button(t("Up"), on_click=lambda: self._move(-1)),
                               button(t("Down"), on_click=lambda: self._move(1)),
                               button(t("Use on every monitor"), "layers", on_click=self._copy_all)))
        row.add(pl, 3)
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(theme.CARD_GAP)
        sched = Card(t("Timing"), "clock")
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 24 * 60)
        self.minutes.setSuffix(" min")
        self.minutes.setAccessibleName(t("Minutes before the next wallpaper"))
        self.minutes.valueChanged.connect(self._item_changed)
        sched.body.addLayout(_line(t("Minutes before the next wallpaper"), self.minutes))
        self.from_time = QTimeEdit()
        self.from_time.setDisplayFormat("HH:mm")
        self.from_time.setAccessibleName(t("Shown from (by time of day)"))
        self.from_time.timeChanged.connect(self._item_changed)
        self.chk_scheduled = QCheckBox(t("Part of the daily schedule"))
        self.chk_scheduled.toggled.connect(self._item_changed)
        sched.body.addLayout(_line(t("Shown from (by time of day)"), self.from_time))
        sched.body.addWidget(self.chk_scheduled)
        self.item_note = label("", "muted", shrink=True)
        sched.body.addWidget(self.item_note)
        side_lay.addWidget(sched)
        vd = Card(t("Virtual desktops"), "layers")
        self.vd_text = label("", "dim", shrink=True)
        vd.body.addWidget(self.vd_text)
        self.vd_list = QListWidget()
        self.vd_list.setAccessibleName(t("Virtual desktops"))
        self.vd_list.setMaximumHeight(90)
        vd.body.addWidget(self.vd_list)
        vd.body.addWidget(_row(button(t("Selected wallpaper on this virtual desktop"), "check",
                                      on_click=self._map_vdesk),
                               button(t("Remove"), "trash", on_click=self._unmap_vdesk)))
        side_lay.addWidget(vd)
        row.add(side, 2)
        root.addWidget(row)
        root.addStretch(1)
        return tab

    def _runtime_tab(self) -> QWidget:
        from akeno.desktop import startup
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        row = ResponsiveRow(spacing=theme.CARD_GAP)
        status = Card(t("Desktop Runtime"), "monitor")
        status.body.addWidget(label(t("Wallpapers, the dock and widgets run in a process of their own. Closing the "
                                      "AKENO window leaves them running; opening AKENO again connects to them."),
                                    "dim", shrink=True))
        self.rt_state = KeyValueGrid(150)
        status.body.addWidget(self.rt_state)
        status.body.addWidget(_row(
            button(t("Stop wallpaper"), "stop", on_click=self.stop_wallpaper),
            button(t("Try again"), "refresh", on_click=self._retry),
            button(t("Quit Desktop Runtime"), "power", "danger", on_click=self._stop)))
        status.body.addWidget(label(t("Stop wallpaper: Windows shows its own wallpaper again; dock and widgets stay. "
                                      "Quit Desktop Runtime: everything stays configured, nothing is shown until you "
                                      "start it again."), "muted", shrink=True))
        row.add(status, 3)
        start = Card(t("Start and background"), "power")
        self.chk_autostart = QCheckBox(t("Start Desktop Runtime with Windows"))
        self.chk_autostart.setEnabled(startup.available())
        self.chk_autostart.toggled.connect(self._autostart)
        start.body.addWidget(self.chk_autostart)
        start.body.addWidget(label(t("Restores your wallpapers, dock and widgets when you sign in, without opening "
                                     "the AKENO window. A normal entry for your user, no service."),
                                   "muted", shrink=True))
        self.chk_tray = QCheckBox(t("Tray icon for wallpapers, dock and widgets"))
        self.chk_tray.toggled.connect(lambda v: self._set_runtime("tray", bool(v)))
        start.body.addWidget(self.chk_tray)
        self.layering = _combo({"auto": msg("Automatic (recommended)"),
                                "workerw": msg("Inside Windows' wallpaper window")}, t("Desktop layer"))
        self.layering.currentIndexChanged.connect(lambda _: self._set_runtime("layering", self.layering.currentData()))
        start.body.addLayout(_line(t("Desktop layer"), self.layering))
        start.body.addWidget(label(t("Change it only if a wallpaper stays invisible on this PC. AKENO never shows a "
                                     "wallpaper in front of your icons: if it cannot place it behind them, it shows "
                                     "the reason here instead."), "muted", shrink=True))
        self.rt_desktop = label("", "muted", shrink=True)
        start.body.addWidget(self.rt_desktop)
        row.add(start, 2)
        root.addWidget(row)
        root.addStretch(1)
        return tab

    def _show_tab(self, index: int) -> None:
        if index == 1 and self.sources is None:          # built on first use: nothing goes online before that
            self.sources = SourcesPanel(self)
            self.sources_holder.layout().addWidget(self.sources)
        self.views.setCurrentIndex(index)
        if index == 0:
            self.refresh_library()
        else:
            self.preview.stop()
        if index == 1:
            self.sources.on_show()
        elif index == 2:
            self._load_playback()
        elif index == 3:
            self._load_monitor()
        elif index == 4:
            self._load_runtime()

    # -- library -------------------------------------------------------------------------------------------------
    def active(self) -> dict[str, list[int]]:
        """Which wallpaper shows on which monitor now (monitor numbers as in Windows' display settings)."""
        out: dict[str, list[int]] = {}
        for n, (name, _) in enumerate(screens(), start=1):
            w = cfgmod.wallpaper(self.cfg, name)
            if not (self.cfg.get("enabled", True) and w["enabled"] and w["playlist"]):
                continue
            from akeno.desktop import playlist as pl
            index = pl.current_index(w, virtual_desktop=winapi.virtual_desktop_id())
            if index is not None:
                out.setdefault(str(Path(w["playlist"][index]["path"]).resolve()), []).append(n)
        return out

    def refresh_library(self, select: str | None = None) -> None:
        current = select or (self.gallery.current_item().id if self.gallery.current_item() else None)
        self.collection.blockSignals(True)
        chosen = self.collection.currentData()
        self.collection.clear()
        self.collection.addItem(t("All collections"), "")
        for name in self.lib.collections():
            self.collection.addItem(name, name)
        self.collection.setCurrentIndex(max(0, self.collection.findData(chosen)))
        self.collection.setVisible(self.collection.count() > 1)
        self.collection.blockSignals(False)
        f = self.filter.value()
        items = self.lib.query(self.search.text(), kind=f if f not in ("all", "favorites") else "all",
                               favorites=f == "favorites", collection=self.collection.currentData() or "",
                               sort=self.sort.currentData() or "recent")
        self.gallery.model_.set_items(items, self.active())
        total = len(self.lib.items())
        self.gallery_stack.setCurrentWidget(self.gallery if items else self.empty if not total else self.no_match)
        self.count.setText(t("{n} of {m} wallpapers", n=len(items), m=total) if total else "")
        if items:
            if current and self.gallery.model_.row_of(current) >= 0:
                self.gallery.select(current)
            elif not self.gallery.currentIndex().isValid():
                self.gallery.select(items[0].id)
        self._select()

    def _select(self) -> None:
        item = self.gallery.current_item()
        has = item is not None
        for w in (self.btn_fav, self.btn_more, self.btn_apply, self.fit_choice):
            w.setEnabled(has)
        if not has:
            self.d_title.set_text(t("No wallpaper selected"))
            self.d_facts.setText("")
            self.d_source.setText("")
            self.preview.show_item(None)
            return
        self.d_title.set_text(item.title)
        self.d_facts.setText(self.dm.facts(item) or t(self.dm.KIND_BADGE.get(item.kind, item.kind)))
        source = {"workshop": t("from Pixabay"), "lively": t("Lively package"), "theme": t("from a theme")}.get(
            item.source, "")
        byline = " · ".join(x for x in (source, item.author and t("by {a}", a=item.author), item.license) if x)
        self.d_source.setText(byline or item.path)
        self.d_source.setToolTip(item.path)
        self.btn_fav.blockSignals(True)
        self.btn_fav.setChecked(item.favorite)
        self.btn_fav.blockSignals(False)
        self.btn_fav.setIcon(icons.icon("star", "#FFD25A" if item.favorite else theme.TEXT_DIM, 20))
        self.btn_apply.setEnabled(not item.missing)
        if self.isVisible() and self.views.currentIndex() == 0:
            self.preview.show_item(item, self.gallery.model_.pixmap(item))
        else:
            self.preview.item = item

    def _thumb_ready(self, _path: str, _image) -> None:
        self.monitor_map.update()

    def _meta(self, path: str, meta: dict) -> None:
        item = self.lib.by_path(path)
        if item is None:
            return
        changed = {k: v for k, v in meta.items() if getattr(item, k, None) != v}
        if changed:
            self.lib.update(item.id, **changed)
            if self.gallery.current_item() and self.gallery.current_item().id == item.id:
                self.d_facts.setText(self.dm.facts(item))
            self.gallery.viewport().update()

    def _monitor_thumb(self, name: str):
        w = cfgmod.wallpaper(self.cfg, name)
        if not (w["enabled"] and w["playlist"]):
            return None
        from akeno.desktop import playlist as pl
        index = pl.current_index(w) or 0
        item = self.lib.by_path(w["playlist"][index]["path"])
        return self.gallery.model_.pixmap(item) if item else None

    def _fill_monitors(self) -> None:
        names = [name for name, _ in screens()]
        if not self.monitor_map.selected & set(names):
            primary = QGuiApplication.primaryScreen()
            self.monitor_map.set_selected({primary.name()} if primary else set(names[:1]))
        for box in (self.monitor, self.pb_monitor):
            box.blockSignals(True)
            keep = box.currentData()
            box.clear()
            for name, text in screens():
                box.addItem(text, name)
            box.setCurrentIndex(max(0, box.findData(keep)))
            box.blockSignals(False)
        self._monitors_changed()

    def _monitors_changed(self) -> None:
        names = [name for name, _ in screens()]
        chosen = [n + 1 for n, name in enumerate(names) if name in self.monitor_map.selected]
        if len(names) > 1 and len(chosen) == len(names):
            text = t("All {n} monitors", n=len(names))
        elif len(chosen) == 1:
            text = t("Monitor {n}", n=chosen[0])
        else:
            text = t("Monitors {list}", list=", ".join(str(n) for n in chosen)) if chosen else ""
        self.monitor_note.setText(text + ("  ·  " + t("Ctrl+click adds monitors") if len(names) > 1 else ""))

    def name(self) -> str:
        """The first chosen monitor (the one the other tabs start with)."""
        chosen = [n for n, _ in screens() if n in self.monitor_map.selected]
        return chosen[0] if chosen else (screens()[0][0] if screens() else "")

    def add_download(self, path: str, title: str = "", source: str = "workshop", author: str = "",
                     license: str = "") -> None:
        """A file from Discover: into the library with where it came from, shown in the gallery."""
        item = self.lib.add(path, title, source, author, license)
        if item is not None:
            self.tabs.setCurrentIndex(0)
            self.refresh_library(select=item.id)
            self.ctl.status.emit(t("In your library: {name}", name=item.title))

    def add_paths(self, paths: list[str]) -> list[str]:
        """Into the library (files stay where they are); returns what could not be added."""
        report = self.lib.add_many(paths)
        problems = [f"{Path(p).name}: {why}" for p, why in report.refused]
        renderer_mod = __import__("akeno.desktop.renderer", fromlist=["web_available"])
        for item in list(report.added):
            if item.kind == "web" and not renderer_mod.web_available():
                self.lib.remove(item.id)
                report.added.remove(item)
                problems.append(t("{n}: web wallpapers need Qt WebEngine, which this build does not include.",
                                  n=Path(item.path).name))
        last = report.added[-1] if report.added else (report.duplicates[-1][1] if report.duplicates else None)
        self.refresh_library(select=last.id if last else None)
        if report.added:
            self.ctl.status.emit(t("{n} wallpaper(s) added to the library.", n=len(report.added)))
        elif report.duplicates and not problems:
            self.ctl.status.emit(t("Already in the library: {name}", name=report.duplicates[-1][1].title))
        return problems

    def _report(self, problems: list[str]) -> None:
        if problems:
            info(self, t("Wallpapers"), t("Some files were not added."), "\n".join(problems), error=True)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Import wallpapers"), "", t(MEDIA_FILTER))
        if paths:
            self._report(self.add_paths(paths))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Import a folder of wallpapers"))
        if folder:
            report = self.lib.add_folder(folder)
            self.refresh_library(select=report.added[-1].id if report.added else None)
            self.ctl.status.emit(t("{n} wallpaper(s) added, {d} already in the library.", n=len(report.added),
                                   d=len(report.duplicates)))

    def _add_web_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Web wallpaper folder (with index.html)"))
        if folder:
            self._report(self.add_paths([folder]))

    def _add_package(self) -> None:
        from akeno.desktop.library import LibraryError
        path, _ = QFileDialog.getOpenFileName(self, t("Lively wallpaper package…"), "",
                                              t("Lively wallpaper packages") + " (*.zip)")
        if not path:
            return
        try:
            item = self.lib.import_lively(path)
        except (LibraryError, OSError) as exc:
            info(self, t("Lively wallpaper package…"), t("This package cannot be imported."), str(exc), error=True)
            return
        self.refresh_library(select=item.id)

    def _dropped(self, paths: list[str]) -> None:
        files = [p for p in paths if Path(p).is_file() or (Path(p) / "index.html").is_file()]
        folders = [p for p in paths if Path(p).is_dir() and p not in files]
        problems = self.add_paths(files) if files else []
        for folder in folders:
            self.lib.add_folder(folder)
        if folders:
            self.refresh_library()
        self._report(problems)

    def _item_menu(self, item, pos) -> None:
        menu = QMenu(self)
        menu.addAction(icons.icon("check", theme.TEXT, 16), t("Apply wallpaper"), lambda: self.apply(item))
        menu.addAction(t("Add to the playlist"), self._add_to_playlist)
        menu.addAction(t("Remove from favourites") if item.favorite else t("Add to favourites"),
                       lambda: self.btn_fav.toggle())
        menu.addAction(t("Rename…"), self._rename)
        menu.addAction(t("Show in folder"), self._show_in_folder)
        menu.addSeparator()
        menu.addAction(t("Remove from library"), self._remove_item)
        menu.exec(pos)

    def _favorite(self, on: bool) -> None:
        item = self.gallery.current_item()
        if item is not None:
            self.lib.update(item.id, favorite=bool(on))
            self.refresh_library(select=item.id)

    def _rename(self) -> None:
        item = self.gallery.current_item()
        if item is None:
            return
        name, ok = QInputDialog.getText(self, t("Rename…"), t("Name"), text=item.title)
        if ok and name.strip():
            self.lib.update(item.id, title=name)
            self.refresh_library(select=item.id)

    def _collections(self) -> None:
        item = self.gallery.current_item()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, t("Collections…"), t("Collections (separated by commas)"),
                                        text=", ".join(item.collections))
        if ok:
            names = [c.strip()[:40] for c in text.split(",") if c.strip()][:20]
            self.lib.update(item.id, collections=list(dict.fromkeys(names)))
            self.refresh_library(select=item.id)

    def _show_in_folder(self) -> None:
        item = self.gallery.current_item()
        if item is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        target = Path(item.path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target if target.is_dir() else target.parent)))

    def _remove_item(self) -> None:
        item = self.gallery.current_item()
        if item is None:
            return
        if not confirm(self, t("Remove from library"), t("Remove “{n}” from the library?", n=item.title),
                       t("The file itself stays where it is, unless AKENO imported it into its own library folder."),
                       ok_text=t("Remove")):
            return
        self.preview.stop()
        self.lib.remove(item.id, delete_imported=True)
        self.refresh_library()

    def _fullscreen(self) -> None:
        item = self.gallery.current_item()
        if item is None or item.missing:
            return
        self.preview.stop()
        self._fs = self.dm.FullscreenPreview(item, self.gallery.model_.pixmap(item), self.fit_choice.value(),
                                             self.window().screen())
        self._fs.destroyed.connect(self._fullscreen_closed)

    def _fullscreen_closed(self, *_):
        self._fs = None
        self._select()

    # -- applying ------------------------------------------------------------------------------------------------
    def apply(self, item=None, monitors: list[str] | None = None) -> bool:
        """Show ``item`` on the chosen monitors (replacing their playlist: one wallpaper, as chosen)."""
        item = item or self.gallery.current_item()
        if item is None or item.missing:
            return False
        names = monitors or [n for n, _ in screens() if n in self.monitor_map.selected] or [self.name()]
        fit = self.fit_choice.value() or "fill"
        for name in names:
            w = cfgmod.wallpaper(self.cfg, name)
            w.update(enabled=True, mode="single", schedule=[], per_virtual_desktop={}, fit=fit,
                     playlist=[{"path": item.path, "kind": item.kind, "minutes": 10}])
            self.cfg.setdefault("wallpapers", {})[name] = w
        self.switch_on()
        self.save()
        self.refresh_library(select=item.id)
        self.monitor_map.update()
        self.ctl.status.emit(t("“{n}” is your wallpaper now.", n=item.title))
        QTimer.singleShot(2500, self.refresh_status)
        return True

    def _add_to_playlist(self) -> None:
        item = self.gallery.current_item()
        if item is None:
            return
        for name in [n for n, _ in screens() if n in self.monitor_map.selected] or [self.name()]:
            w = cfgmod.wallpaper(self.cfg, name)
            if not any(Path(e["path"]) == Path(item.path) for e in w["playlist"]):
                w["playlist"].append({"path": item.path, "kind": item.kind, "minutes": 10})
            if len(w["playlist"]) > 1 and w["mode"] == "single":
                w["mode"] = "rotate"
            w["enabled"] = True
            self.cfg.setdefault("wallpapers", {})[name] = w
        self.switch_on()
        self.save()
        self.refresh_library(select=item.id)
        self.ctl.status.emit(t("Added to the playlist: {name}", name=item.title))

    def stop_wallpaper(self) -> None:
        """Windows' own wallpaper on every monitor; dock and widgets keep running."""
        cfgmod.stop_wallpapers(self.cfg)
        self.save()
        self.refresh_library()
        self.monitor_map.update()
        QTimer.singleShot(1500, self.refresh_status)

    # -- playback and playlist tabs ------------------------------------------------------------------------------
    def wcfg(self, name: str | None = None) -> dict:
        return cfgmod.wallpaper(self.cfg, name or self._tab_monitor())

    def _tab_monitor(self) -> str:
        box = self.pb_monitor if self.views.currentIndex() == 2 else self.monitor
        return box.currentData() or self.name()

    def _load_playback(self) -> None:
        self._loading = True
        try:
            w = cfgmod.wallpaper(self.cfg, self.pb_monitor.currentData() or self.name())
            self.fit.setCurrentIndex(max(0, self.fit.findData(w["fit"])))
            self.volume.setValue(int(w["volume"]))
            self.fps.setValue(int(w["fps_limit"]))
            self.chk_interactive.setChecked(bool(w["interactive"]))
            self.chk_audio.setChecked(bool(w["audio_reactive"]))
            rules = self.cfg["pause"]
            for key, box in self.rule_checks.items():
                box.setChecked(bool(rules.get(key, True)))
            self.cpu_limit.setValue(int(rules.get("cpu_percent") or 0))
        finally:
            self._loading = False

    def _load_monitor(self, *_):
        self._loading = True
        try:
            w = cfgmod.wallpaper(self.cfg, self.monitor.currentData() or self.name())
            self.chk_enabled.setChecked(bool(w["enabled"]))
            self.mode.setCurrentIndex(max(0, self.mode.findData(w["mode"])))
            self._fill_list(w)
            self._fill_vdesk(w)
        finally:
            self._loading = False

    def _fill_list(self, w: dict, select: int = 0) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for entry in w["playlist"]:
            path = Path(entry["path"])
            kind = entry.get("kind") or media.kind_of(path) or "image"
            item = self.lib.by_path(path)
            title = item.title if item else path.name
            text = f"{title}\n{t(KIND_TEXT.get(kind, kind))} · {int(entry.get('minutes', 10))} min"
            pm = self.gallery.model_.pixmap(item) if item else None
            icon = QIcon(pm.scaled(224, 126, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)) if pm else \
                icons.icon({"video": "film", "web": "globe"}.get(kind, "image"), theme.TEXT_DIM, 48)
            row = QListWidgetItem(icon, text)
            if not path.exists():
                row.setForeground(QColor(theme.RED))
                row.setToolTip(t("The file is missing: {p}", p=str(path)))
            self.list.addItem(row)
        self.list.blockSignals(False)
        self.list_stack.setCurrentWidget(self.list if self.list.count() else self.list_empty)
        if self.list.count():
            self.list.setCurrentRow(min(select, self.list.count() - 1))
        else:
            self._load_item(-1)

    def _load_item(self, row: int) -> None:
        w = self.wcfg(self.monitor.currentData() or self.name())
        valid = 0 <= row < len(w["playlist"])
        for widget in (self.minutes, self.from_time, self.chk_scheduled):
            widget.setEnabled(valid)
        if not valid:
            self.item_note.setText("")
            return
        self._loading = True
        try:
            self.minutes.setValue(int(w["playlist"][row].get("minutes", 10)))
            entry = next((e for e in w["schedule"] if e.get("index") == row), None)
            self.chk_scheduled.setChecked(entry is not None)
            self.from_time.setTime(QTime.fromString(entry["from"], "HH:mm") if entry else QTime(8, 0))
            self.item_note.setText(w["playlist"][row]["path"])
        finally:
            self._loading = False

    def _fill_vdesk(self, w: dict) -> None:
        current = winapi.virtual_desktop_id()
        self.vd_text.setText(t("This virtual desktop: {id}", id=current[:8]) if current else
                             t("Windows reports no virtual desktop here."))
        self.vd_list.clear()
        names = [Path(i["path"]).name for i in w["playlist"]]
        for desk, index in w["per_virtual_desktop"].items():
            name = names[index] if isinstance(index, int) and 0 <= index < len(names) else "—"
            entry = QListWidgetItem(f"{desk[:8]} → {name}")
            entry.setData(Qt.ItemDataRole.UserRole, desk)
            self.vd_list.addItem(entry)

    def _store(self, w: dict, name: str | None = None) -> None:
        self.cfg.setdefault("wallpapers", {})[name or self._tab_monitor()] = w
        self.save()

    def _set(self, key: str, value, playlist_tab: bool = False) -> None:
        if self._loading:
            return
        name = (self.monitor if playlist_tab else self.pb_monitor).currentData() or self.name()
        w = cfgmod.wallpaper(self.cfg, name)
        w[key] = value
        if key == "enabled" and value:
            self.switch_on()
        self._store(w, name)

    def _set_rule(self, key: str, value) -> None:
        if self._loading:
            return
        self.cfg["pause"][key] = value
        self.save()

    def _item_changed(self, *_):
        if self._loading:
            return
        name = self.monitor.currentData() or self.name()
        row = self.list.currentRow()
        w = cfgmod.wallpaper(self.cfg, name)
        if not 0 <= row < len(w["playlist"]):
            return
        w["playlist"][row]["minutes"] = int(self.minutes.value())
        w["schedule"] = [e for e in w["schedule"] if e.get("index") != row]
        if self.chk_scheduled.isChecked():
            w["schedule"].append({"from": self.from_time.time().toString("HH:mm"), "index": row})
        self._store(w, name)
        self._fill_list(w, row)

    def _remove(self) -> None:
        name = self.monitor.currentData() or self.name()
        row = self.list.currentRow()
        w = cfgmod.wallpaper(self.cfg, name)
        if not 0 <= row < len(w["playlist"]):
            return
        w["playlist"].pop(row)
        w["schedule"] = [dict(e, index=e["index"] - (e["index"] > row)) for e in w["schedule"] if e["index"] != row]
        w["per_virtual_desktop"] = {k: v - (v > row) for k, v in w["per_virtual_desktop"].items() if v != row}
        if not w["playlist"]:
            w["enabled"] = False
        self._store(w, name)
        self._load_monitor()

    def _move(self, step: int) -> None:
        name = self.monitor.currentData() or self.name()
        row = self.list.currentRow()
        w = cfgmod.wallpaper(self.cfg, name)
        new = row + step
        if not (0 <= row < len(w["playlist"]) and 0 <= new < len(w["playlist"])):
            return
        items = w["playlist"]
        items[row], items[new] = items[new], items[row]
        swap = {row: new, new: row}
        w["schedule"] = [dict(e, index=swap.get(e["index"], e["index"])) for e in w["schedule"]]
        w["per_virtual_desktop"] = {k: swap.get(v, v) for k, v in w["per_virtual_desktop"].items()}
        self._store(w, name)
        self._fill_list(w, new)

    def _copy_all(self) -> None:
        w = cfgmod.wallpaper(self.cfg, self.monitor.currentData() or self.name())
        for name, _ in screens():
            self.cfg.setdefault("wallpapers", {})[name] = json.loads(json.dumps(w))
        self.save()
        self.ctl.status.emit(t("The wallpaper settings now apply to every monitor."))

    def _map_vdesk(self) -> None:
        current = winapi.virtual_desktop_id()
        row = self.list.currentRow()
        if not current or row < 0:
            return
        name = self.monitor.currentData() or self.name()
        w = cfgmod.wallpaper(self.cfg, name)
        w["per_virtual_desktop"][current] = row
        self._store(w, name)
        self._fill_vdesk(w)

    def _unmap_vdesk(self) -> None:
        entry = self.vd_list.currentItem()
        if entry is None:
            return
        name = self.monitor.currentData() or self.name()
        w = cfgmod.wallpaper(self.cfg, name)
        w["per_virtual_desktop"].pop(entry.data(Qt.ItemDataRole.UserRole), None)
        self._store(w, name)
        self._fill_vdesk(w)

    # -- runtime -------------------------------------------------------------------------------------------------
    def _load_runtime(self) -> None:
        from akeno.desktop import startup
        self._loading = True
        try:
            rt = self.cfg.get("runtime", {})
            self.chk_autostart.setChecked(startup.enabled())
            self.chk_tray.setChecked(bool(rt.get("tray", True)))
            self.layering.setCurrentIndex(max(0, self.layering.findData(rt.get("layering", "auto"))))
        finally:
            self._loading = False
        d = winapi.describe()
        if d.get("windows"):
            layer = {"raised": t("Windows 11 24H2 desktop (below the icon view)"),
                     "workerw": t("classic desktop (WorkerW behind the icons)")}.get(d.get("layer") or "",
                                                                                     t("no desktop layer found"))
            self.rt_desktop.setText(t("Windows build {b} · {layer}", b=d.get("build"), layer=layer))
        else:
            self.rt_desktop.setText(t("Desktop wallpapers need Windows; here AKENO shows a preview window."))
        self.refresh_status()

    def _autostart(self, on: bool) -> None:
        if self._loading:
            return
        from akeno.desktop import startup
        try:
            startup.set_enabled(on)
        except OSError as exc:
            info(self, t("Start Desktop Runtime with Windows"), str(exc), error=True)
        self.cfg.setdefault("runtime", {})["autostart"] = bool(on)
        self.save()

    def _set_runtime(self, key: str, value) -> None:
        if self._loading:
            return
        self.cfg.setdefault("runtime", dict(cfgmod.RUNTIME_DEFAULTS))[key] = value
        self.save()
        if key == "layering":
            self.desktop.retry()

    def _retry(self) -> None:
        self.desktop.retry()
        QTimer.singleShot(2000, self.refresh_status)

    def _start(self) -> None:
        self.switch_on()
        cfgmod.save(self.cfg)
        if not self.desktop.ensure(self.cfg):
            info(self, t("Desktop Studio"), t("Switch on a wallpaper, the dock or a widget first."))
        QTimer.singleShot(1500, self.refresh_status)

    def _stop(self) -> None:
        """Quit Desktop Runtime: everything stays as configured; nothing is shown until it is started again."""
        self.cfg["enabled"] = False
        cfgmod.save(self.cfg)
        self.desktop.stop()
        QTimer.singleShot(800, self.refresh_status)

    def refresh_status(self) -> None:
        status = self.desktop.status()
        self.status_cache = status
        rows = []
        if status is None:
            stopped = not self.cfg.get("enabled", True)
            self.pill.set(t("Desktop Runtime quit") if stopped else t("Not running"), "pending")
            rows.append((t("State"), t("Quit. Applying a wallpaper starts it again.") if stopped else
                         t("Starts by itself when a wallpaper, the dock or a widget is on.")))
            self.banner.clear()
        else:
            renderers = status.get("renderers", {})
            failed = {m: r for m, r in renderers.items() if r["state"] == "failed"}
            paused = [m for m, r in renderers.items() if r.get("paused")]
            if failed:
                self.pill.set(t("Wallpaper problem"), "fail")
                first = next(iter(failed.values()))
                self.banner.show_message(t("A wallpaper is not shown: {e}. Your desktop and icons are not affected.",
                                           e=first.get("error") or t("it stopped repeatedly")), "error",
                                         t("Try again"), self._retry)
            else:
                self.banner.clear()
                if paused:
                    self.pill.set(t("Paused"), "warn")
                elif renderers:
                    self.pill.set(t("Running · {n} monitor(s)", n=len(renderers)), "ok")
                else:
                    self.pill.set(t("Running"), "ok")
            labels = dict(screens())
            from datetime import datetime
            if status.get("started"):
                rows.append((t("Running since"), datetime.fromtimestamp(status["started"]).strftime("%d.%m. %H:%M")))
            for monitor, r in renderers.items():
                line = t(STATE_TEXT.get(r["state"], r["state"]))
                if r.get("reasons"):
                    line += " · " + t("paused") + ": " + ", ".join(t(x) for x in r["reasons"])
                if r.get("error"):
                    line += " · " + r["error"]
                rows.append((labels.get(monitor, monitor), line))
            rows.append((t("Dock"), t("on") if status.get("dock") else t("off")))
            rows.append((t("Widgets"), str(status.get("widgets", 0))))
        self.rt_state.set_rows(rows)

    def on_show(self) -> None:
        self.reload_cfg()
        self.lib.load()
        self.lib.sync_from_config(self.cfg)
        self._fill_monitors()
        self.refresh_library()
        self.refresh_status()
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        self.preview.stop()
        super().hideEvent(e)


DOCK_STYLES = {"glass": msg("Glass"), "solid": msg("Solid"), "clear": msg("Icons only"), "accent": msg("Accent")}
DOCK_THEMES = {
    "akeno": (msg("AKENO"), {"style": "accent", "opacity": 94, "icon_size": 48, "labels": False}),
    "glass": (msg("Glass"), {"style": "glass", "opacity": 92, "icon_size": 48, "labels": False, "blur": True}),
    "midnight": (msg("Midnight"), {"style": "solid", "opacity": 100, "icon_size": 44, "labels": True}),
    "minimal": (msg("Minimal"), {"style": "clear", "opacity": 100, "icon_size": 40, "labels": False}),
}
WIDGET_STYLES = {"dark": msg("Dark"), "glass": msg("Glass"), "solid": msg("Solid"), "accent": msg("Accent")}
SWATCHES = ("#8E42FF", "#4C9DFF", "#3CC8F0", "#34D399", "#F6C152", "#F7687C", "#E879F9", "#E8EDF6")
WIDGET_ABOUT = {
    "clock": msg("Time and date"), "calendar": msg("This month"), "cpu": msg("Processor load and clock"),
    "gpu": msg("NVIDIA graphics load"), "temperature": msg("GPU and CPU temperature where reported"),
    "ram": msg("Memory in use"), "vram": msg("NVIDIA video memory"), "network": msg("Download and upload rate"),
    "disk": msg("Free space per drive"), "fps": msg("Last Benchmark result"), "shortcuts": msg("Programs and folders"),
    "music": msg("Media keys for the active player"), "visualizer": msg("Level of the sound that plays"),
    "notes": msg("A note on the desktop"), "todo": msg("A short to-do list"), "weather": msg("open-meteo.com, once "
                                                                                             "you enter a place"),
    "uptime": msg("Time since Windows started"), "downloads": msg("Newest files in Downloads"),
    "license": msg("Your AKENO license"),
}


def _switch(text: str, on_toggle) -> QPushButton:
    """A chip that stays pressed while something is on (dock shown, widgets shown, positions locked)."""
    b = QPushButton(text)
    b.setProperty("kind", "chip")
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setAccessibleName(text)
    b.toggled.connect(on_toggle)
    return b


class DockPage(DesktopPage):
    """The dock as you will see it, the pinned items to drag into order, and its look and place."""

    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        from akeno.ui import desktop_editors as de
        from akeno.ui import desktop_media as dm
        self.de, self.dm = de, dm
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        self.btn_on = _switch(t("Show the dock"), lambda v: self._set("enabled", bool(v), switch=True))
        root.addWidget(PageHeader(t("AKENO Dock"), t("A launcher bar for programs, folders and shortcuts. It is an "
                                                     "extra bar: the Windows taskbar stays as it is."),
                                  [self.btn_on]))
        top = ResponsiveRow(spacing=theme.CARD_GAP)
        view = Card(t("Preview"), "eye")
        self.preview = de.DockPreview(self._wallpaper)
        view.body.addWidget(self.preview)
        themes_row = QHBoxLayout()
        themes_row.setSpacing(6)
        themes_row.addWidget(label(t("Themes"), "key"))
        for key, (name, _values) in DOCK_THEMES.items():
            themes_row.addWidget(button(t(name), kind="chip", on_click=lambda _=False, k=key: self._theme(k)))
        themes_row.addStretch(1)
        view.body.addLayout(themes_row)
        top.add(view, 3)
        look = Card(t("Look and place"), "sliders")
        self.style = self.dm.Segmented({k: t(v) for k, v in DOCK_STYLES.items()}, "glass")
        self.style.changed.connect(lambda v: self._set("style", v))
        look.body.addWidget(label(t("Style"), "overline"))
        look.body.addWidget(self.style)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(24, 96)
        self.size.setSingleStep(4)
        self.size.setAccessibleName(t("Icon size"))
        self.size.valueChanged.connect(lambda v: self._set("icon_size", int(v)))
        look.body.addLayout(_line(t("Icon size"), self.size))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setAccessibleName(t("Opacity"))
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", int(v)))
        look.body.addLayout(_line(t("Opacity"), self.opacity))
        self.chk_labels = QCheckBox(t("Names under the icons"))
        self.chk_labels.toggled.connect(lambda v: self._set("labels", bool(v)))
        look.body.addWidget(self.chk_labels)
        look.body.addWidget(label(t("Screen edge"), "overline"))
        self.edge = self.dm.Segmented({k: t(v) for k, v in EDGE_TEXT.items()}, "bottom")
        self.edge.changed.connect(lambda v: self._set("edge", v))
        look.body.addWidget(self.edge)
        self.screen = QComboBox()
        self.screen.setAccessibleName(t("Monitor"))
        self.screen.currentIndexChanged.connect(lambda i: self._set("screen", max(0, i)))
        look.body.addLayout(_line(t("Monitor"), self.screen))
        self.chk_float = QCheckBox(t("Floating (drag it anywhere)"))
        self.chk_float.toggled.connect(lambda v: self._set("floating", bool(v)))
        self.chk_hide = QCheckBox(t("Hide automatically (show at the screen edge)"))
        self.chk_hide.toggled.connect(lambda v: self._set("autohide", bool(v)))
        self.chk_games = QCheckBox(t("Step aside while a game or fullscreen program runs"))
        self.chk_games.toggled.connect(lambda v: self._set("hide_in_games", bool(v)))
        self.chk_anim = QCheckBox(t("Animations"))
        self.chk_anim.toggled.connect(lambda v: self._set("animate", bool(v)))
        for box in (self.chk_float, self.chk_hide, self.chk_games, self.chk_anim):
            look.body.addWidget(box)
        top.add(look, 2)
        root.addWidget(top)
        pins = Card(t("Pinned"), "dock", right=_row(
            button(t("Add program…"), "plus", on_click=self._add_program),
            button(t("Add folder…"), "folder", on_click=self._add_folder),
            button(t("New group…"), "layers", on_click=self._add_group),
            button(t("Remove"), "trash", on_click=self._remove)))
        self.list = de.PinnedList()
        self.list.reordered.connect(self._reorder)
        self.list.files_dropped.connect(lambda paths: self.add_items(
            [{"type": "app", "path": p, "name": Path(p).stem} for p in paths]))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._pin_menu)
        pins.body.addWidget(self.list)
        pins.body.addWidget(label(t("Drag icons into the order you want. Drop programs, shortcuts or folders from "
                                    "Explorer here or on the dock itself."), "muted", shrink=True))
        root.addWidget(pins)
        root.addStretch(1)
        self.load()

    def _wallpaper(self, index: int):
        names = [n for n, _ in screens()]
        if not 0 <= index < len(names):
            return None
        page = self.win.pages.get("wallpapers")
        return page._monitor_thumb(names[index]) if page is not None else None

    def load(self) -> None:
        self._loading = True
        try:
            d = self.cfg["dock"]
            self.btn_on.setChecked(bool(d.get("enabled")) and self.cfg.get("enabled", True))
            self.style.set(d.get("style", "glass"))
            self.size.setValue(int(d.get("icon_size", 48)))
            self.opacity.setValue(int(d.get("opacity", 92)))
            self.chk_labels.setChecked(bool(d.get("labels")))
            self.edge.set(d.get("edge", "bottom"))
            self.screen.clear()
            for _, text in screens():
                self.screen.addItem(text)
            self.screen.setCurrentIndex(min(int(d.get("screen", 0)), max(0, self.screen.count() - 1)))
            self.chk_float.setChecked(bool(d.get("floating")))
            self.chk_hide.setChecked(bool(d.get("autohide")))
            self.chk_games.setChecked(bool(d.get("hide_in_games", True)))
            self.chk_anim.setChecked(bool(d.get("animate", True)))
            self._fill()
        finally:
            self._loading = False

    def _running(self) -> set[str]:
        try:
            from akeno.desktop.dock import running_exes
            return running_exes()
        except Exception:                    # psutil hiccups never break the page
            return set()

    def _fill(self, select: int = -1) -> None:
        self.list.set_items(self.cfg["dock"].get("items", []))
        if select >= 0:
            self.list.setCurrentRow(select)
        self.preview.set_config(self.cfg["dock"], self._running())

    def _set(self, key: str, value, switch: bool = False) -> None:
        if self._loading:
            return
        self.cfg["dock"][key] = value
        if switch and value:
            self.switch_on()
        self.save()
        self.preview.set_config(self.cfg["dock"])

    def _theme(self, key: str) -> None:
        self.cfg["dock"].update(DOCK_THEMES[key][1])
        self.save()
        self.load()

    def add_items(self, new: list[dict]) -> None:
        self.cfg["dock"].setdefault("items", []).extend(new)
        self.save()
        self._fill(len(self.cfg["dock"]["items"]) - 1)

    def _reorder(self, order: list[int]) -> None:
        items = self.cfg["dock"].get("items", [])
        if sorted(order) != list(range(len(items))):
            return
        self.cfg["dock"]["items"] = [items[i] for i in order]
        self.save()
        self._fill()

    def _pin_menu(self, pos) -> None:
        row = self.list.indexAt(pos).row()
        if row < 0:
            return
        self.list.setCurrentRow(row)
        menu = QMenu(self)
        menu.addAction(t("Rename…"), self._rename)
        menu.addAction(t("Remove from dock"), self._remove)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename(self) -> None:
        row = self.list.currentRow()
        items = self.cfg["dock"].get("items", [])
        if not 0 <= row < len(items):
            return
        name, ok = QInputDialog.getText(self, t("Rename…"), t("Name"), text=items[row].get("name", ""))
        if ok and name.strip():
            items[row]["name"] = name.strip()[:40]
            self.save()
            self._fill(row)

    def _add_program(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Add a program to the dock"), "",
                                                t("Programs and shortcuts (*.exe *.lnk *.url);;All files (*)"))
        if paths:
            self.add_items([{"type": "app", "path": p, "name": Path(p).stem} for p in paths])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Add folder…"))
        if folder:
            self.add_items([{"type": "app", "path": folder, "name": Path(folder).name}])

    def _add_group(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Programs for the group"), "",
                                                t("Programs and shortcuts (*.exe *.lnk *.url);;All files (*)"))
        if not paths:
            return
        name, ok = QInputDialog.getText(self, t("Group name"), t("Name"))
        if ok and name.strip():
            self.add_items([{"type": "group", "name": name.strip()[:40],
                             "items": [{"path": p, "name": Path(p).stem} for p in paths]}])

    def _remove(self) -> None:
        row = self.list.currentRow()
        items = self.cfg["dock"].get("items", [])
        if 0 <= row < len(items):
            items.pop(row)
            self.save()
            self._fill(min(row, len(items) - 1))

    def on_show(self) -> None:
        self.reload_cfg()
        self.load()


class WidgetCard(QFrame):
    """One widget type in the gallery: what it looks like (real panel), what it shows, Add."""

    def __init__(self, kind: str, on_add):
        super().__init__()
        self.kind = kind
        self.setObjectName("WidgetCard")
        self.setFixedWidth(228)
        self.setStyleSheet(f"#WidgetCard {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
                           f" border-radius: 10px; }} #WidgetCard:hover {{ border-color: {theme.BORDER_BTN}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.pic = QLabel()
        self.pic.setFixedSize(208, 118)
        self.pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pic.setStyleSheet("background: #0A0F1A; border-radius: 8px;")
        self.pic.setPixmap(icons.pixmap(WIDGET_ICONS.get(kind, "grid"), theme.TEXT_MUTED, 30))
        lay.addWidget(self.pic)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(label(t(dwidgets.TITLES[kind]), "h3"), 1)
        self.btn = QToolButton()
        self.btn.setIcon(icons.icon("plus", theme.TEXT, 16))
        self.btn.setToolTip(t("Add {w}", w=t(dwidgets.TITLES[kind])))
        self.btn.setAccessibleName(self.btn.toolTip())
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setFixedSize(30, 28)
        self.btn.setStyleSheet(f"QToolButton {{ background: {theme.ACCENT_STRONG}; border-radius: 7px; }}"
                               f"QToolButton:hover {{ background: {theme.ACCENT}; }}")
        self.btn.clicked.connect(lambda: on_add(kind))
        row.addWidget(self.btn)
        lay.addLayout(row)
        about = label(t(WIDGET_ABOUT.get(kind, "")), "muted", wrap=True)
        about.setMinimumHeight(about.fontMetrics().height() * 2 + 2)
        about.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(about)

    def set_picture(self, pm: QPixmap) -> None:
        if pm.isNull():
            return
        target = self.pic.size()
        scaled = pm.scaled(target.width() - 8, target.height() - 8, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        canvas = QPixmap(target)
        canvas.fill(QColor(0, 0, 0, 0))
        p = QPainter(canvas)
        p.drawPixmap((target.width() - scaled.width()) // 2, (target.height() - scaled.height()) // 2, scaled)
        p.end()
        self.pic.setPixmap(canvas)


class WidgetsPage(DesktopPage):
    """A widget studio: the layout on your monitor, the selected widget's look, and the widgets you can add."""

    scrolls = True

    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        from akeno.ui import desktop_editors as de
        from akeno.ui import desktop_media as dm
        self.de, self.dm = de, dm
        self.ctx = None
        self.previews: dict[str, QPixmap] = {}
        self.current = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        self.btn_on = _switch(t("Show widgets"), self._enabled)
        self.btn_lock = _switch(t("Lock positions"), self._lock)
        root.addWidget(PageHeader(t("Widgets"), t("Clock, system load, notes and more on your desktop, below your "
                                                  "windows. Arrange them here or drag them on the desktop."),
                                  [self.btn_lock, self.btn_on,
                                   button(t("Default layout"), "restore", on_click=self._defaults)]))
        top = ResponsiveRow(spacing=theme.CARD_GAP)
        lay_card = Card(t("Layout"), "monitor")
        self.screen_pick = QComboBox()
        self.screen_pick.setAccessibleName(t("Monitor"))
        self.screen_pick.currentIndexChanged.connect(lambda _: self._refresh_canvas())
        lay_card.body.addLayout(_line(t("Monitor"), self.screen_pick))
        self.canvas = de.WidgetCanvas(self._preview, self._wallpaper)
        self.canvas.setMinimumHeight(360)
        self.canvas.selected.connect(self._select)
        self.canvas.changed.connect(lambda spec: self._store())
        lay_card.body.addWidget(self.canvas, 1)
        lay_card.body.addWidget(label(t("Drag a widget to move it, drag its corner to resize it. Changes appear on "
                                        "the desktop right away."), "muted", shrink=True))
        top.add(lay_card, 3)
        sel = Card(t("Selected widget"), "sliders")
        self.sel_title = label(t("Click a widget in the layout."), "h3")
        sel.body.addWidget(self.sel_title)
        self.sel_box = QWidget()
        sl = QVBoxLayout(self.sel_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        sl.addWidget(label(t("Style"), "overline"))
        self.w_style = dm.Segmented({k: t(v) for k, v in WIDGET_STYLES.items()}, "dark")
        self.w_style.changed.connect(lambda v: self._set_item("style", v))
        sl.addWidget(self.w_style)
        sl.addWidget(label(t("Colour"), "overline"))
        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        self.swatches = []
        for colour in SWATCHES:
            b = QToolButton()
            b.setFixedSize(26, 26)
            b.setCheckable(True)
            b.setToolTip(colour)
            b.setAccessibleName(t("Colour {c}", c=colour))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QToolButton {{ background: {colour}; border: 2px solid {theme.BORDER}; "
                            f"border-radius: 13px; }} QToolButton:checked {{ border-color: #FFFFFF; }}")
            b.clicked.connect(lambda _=False, c=colour: self._set_item("color", c))
            swatches.addWidget(b)
            self.swatches.append(b)
        custom = button(t("Other…"), kind="link", on_click=self._pick_colour)
        swatches.addWidget(custom)
        swatches.addStretch(1)
        sl.addLayout(swatches)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setAccessibleName(t("Opacity"))
        self.opacity.valueChanged.connect(lambda v: self._set_item("opacity", int(v)))
        sl.addLayout(_line(t("Opacity"), self.opacity))
        self.w_screen = QComboBox()
        self.w_screen.setAccessibleName(t("Monitor"))
        self.w_screen.currentIndexChanged.connect(lambda i: self._set_item("screen", max(0, i)))
        sl.addLayout(_line(t("Monitor"), self.w_screen))
        self.place = QLineEdit()
        self.place.setPlaceholderText(t("City, for example Berlin"))
        self.place.setAccessibleName(t("Weather place"))
        self.place.editingFinished.connect(self._set_place)
        self.place_line = QWidget()
        pl = QHBoxLayout(self.place_line)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(label(t("Weather place"), "key"), 1)
        pl.addWidget(self.place, 1)
        sl.addWidget(self.place_line)
        self.weather_note = label(t("The weather widget asks open-meteo.com only after you enter a place."),
                                  "muted", shrink=True)
        sl.addWidget(self.weather_note)
        self.sel_size = label("", "muted")
        sl.addWidget(self.sel_size)
        sl.addWidget(_row(button(t("Remove widget"), "trash", "danger", on_click=self._remove)))
        sel.body.addWidget(self.sel_box)
        top.add(sel, 2)
        root.addWidget(top)
        gallery = Card(t("Add widgets"), "grid")
        self.gallery_holder = QWidget()
        self.gallery = FlowLayout(self.gallery_holder, spacing=12)
        self.cards: dict[str, WidgetCard] = {}
        for kind in cfgmod.WIDGET_TYPES:
            card = WidgetCard(kind, self._add_kind)
            self.cards[kind] = card
            self.gallery.addWidget(card)
        gallery.body.addWidget(self.gallery_holder)
        gallery.body.addWidget(label(t("Previews show this PC's real values. A value Windows or your graphics card "
                                       "does not report shows as —, on the desktop too."), "muted", shrink=True))
        root.addWidget(gallery)
        layouts = ResponsiveRow(spacing=theme.CARD_GAP)
        prof = Card(t("Desktop profiles"), "layers")
        prof.body.addWidget(label(t("A profile keeps the wallpapers, the dock and the widgets as they are now, for "
                                    "example one for gaming and one for work."), "dim", shrink=True))
        self.profiles = QComboBox()
        self.profiles.setAccessibleName(t("Desktop profiles"))
        prof.body.addWidget(self.profiles)
        prof.body.addWidget(_row(button(t("Save as profile…"), "check", on_click=self._save_profile),
                                 button(t("Use"), "play", on_click=self._apply_profile),
                                 button(t("Delete"), "trash", on_click=self._delete_profile)))
        layouts.add(prof, 1)
        share = Card(t("Layout file"), "export")
        share.body.addWidget(label(t("Positions, sizes and options as a file. Notes and to-dos stay on this PC."),
                                   "dim", shrink=True))
        share.body.addWidget(_row(button(t("Export…"), "export", on_click=self._export),
                                  button(t("Import…"), "download", on_click=self._import)))
        layouts.add(share, 1)
        root.addWidget(layouts)
        root.addStretch(1)
        self.load()

    # -- previews --------------------------------------------------------------------------------------------------
    def _context(self):
        if self.ctx is None:
            self.ctx = self.de.sample_context()
            self.ctx.sampler.need("disk", "temperature", "gpu")

            def done(_data):
                if isValid(self):
                    self.previews.clear()
                    self._refresh_previews()

            self.ctl.run("widget-preview-sensors", self.ctx.sampler.sample, done, None, silent=True)
        return self.ctx

    def _preview(self, spec: dict) -> QPixmap | None:
        key = json.dumps({k: spec.get(k) for k in ("type", "w", "h", "style", "color", "options")}, sort_keys=True)
        pm = self.previews.get(key)
        if pm is None:
            try:
                pm = self.de.render_widget(spec, self._context())
            except Exception as exc:              # a preview never breaks the page
                import logging
                logging.getLogger(__name__).warning("widget preview %s: %s", spec.get("type"), exc)
                pm = QPixmap()
            self.previews[key] = pm
        return pm

    def _refresh_previews(self) -> None:
        for kind, card in self.cards.items():
            spec = dwidgets.new_widget(kind, [], 0)
            card.set_picture(self._preview(spec) or QPixmap())
        self.canvas.update()

    def _wallpaper(self, index: int):
        names = [n for n, _ in screens()]
        page = self.win.pages.get("wallpapers")
        return page._monitor_thumb(names[index]) if page is not None and 0 <= index < len(names) else None

    # -- loading ---------------------------------------------------------------------------------------------------
    def load(self, select: str | None = None) -> None:
        self._loading = True
        try:
            self.btn_on.setChecked(cfgmod.widgets_on(self.cfg) and self.cfg.get("enabled", True))
            self.btn_lock.setChecked(bool(self.cfg.get("widgets_locked")))
            for box in (self.screen_pick, self.w_screen):
                keep = box.currentIndex()
                box.blockSignals(True)
                box.clear()
                for _, text in screens():
                    box.addItem(text)
                box.setCurrentIndex(max(0, min(keep, box.count() - 1)))
                box.blockSignals(False)
            self.profiles.clear()
            self.profiles.addItems(sorted(self.cfg.get("profiles", {})))
        finally:
            self._loading = False
        ids = [w["id"] for w in self.cfg["widgets"]]
        self.current = select if select in ids else (self.current if self.current in ids else
                                                     (ids[0] if ids else ""))
        self._refresh_canvas()
        self._select(self.current)

    def _refresh_canvas(self) -> None:
        self.canvas.set_widgets(self.cfg["widgets"], max(0, self.screen_pick.currentIndex()), self.current)

    def _current(self) -> dict | None:
        return next((w for w in self.cfg["widgets"] if w.get("id") == self.current), None)

    def _select(self, wid: str) -> None:
        self.current = wid
        w = self._current()
        self.sel_box.setVisible(w is not None)
        if w is None:
            self.sel_title.setText(t("Click a widget in the layout."))
            return
        self._loading = True
        try:
            self.sel_title.setText(t(dwidgets.TITLES[w["type"]]))
            self.w_style.set(w.get("style", "dark"))
            colour = (w.get("color") or SWATCHES[0]).upper()
            for b, c in zip(self.swatches, SWATCHES):
                b.setChecked(c.upper() == colour)
            self.opacity.setValue(int(w.get("opacity", 90)))
            self.w_screen.setCurrentIndex(min(int(w.get("screen", 0)), max(0, self.w_screen.count() - 1)))
            self.place_line.setVisible(w["type"] == "weather")
            self.weather_note.setVisible(w["type"] == "weather")
            self.place.setText(w.get("options", {}).get("place", ""))
            self.sel_size.setText(t("Position {x}, {y} · size {w} × {h}", x=w.get("x", 0), y=w.get("y", 0),
                                    w=w.get("w", 0), h=w.get("h", 0)))
        finally:
            self._loading = False
        self.canvas.current = wid
        self.canvas.update()

    # -- editing ---------------------------------------------------------------------------------------------------
    def _store(self) -> None:
        self.save()
        self._select(self.current)

    def _set_item(self, key: str, value) -> None:
        w = self._current()
        if self._loading or w is None:
            return
        w[key] = value
        if key == "screen":
            self.screen_pick.setCurrentIndex(int(value))
        self.save()
        self._refresh_canvas()
        self._select(self.current)

    def _pick_colour(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        w = self._current()
        if w is None:
            return
        colour = QColorDialog.getColor(QColor(w.get("color") or SWATCHES[0]), self, t("Colour"))
        if colour.isValid():
            self._set_item("color", colour.name())

    def _set_place(self) -> None:
        w = self._current()
        if self._loading or w is None or w["type"] != "weather":
            return
        place = self.place.text().strip()[:80]
        if place != w.setdefault("options", {}).get("place", ""):
            w["options"].update(place=place, lat=None, lon=None)
            self.save()

    def add(self, kind: str, screen: int | None = None) -> dict:
        screen = max(0, self.screen_pick.currentIndex()) if screen is None else screen
        spec = dwidgets.new_widget(kind, self.cfg["widgets"], screen)
        self.cfg["widgets"].append(spec)
        self.cfg["widgets_enabled"] = True
        self.switch_on()
        self.save()
        self.load(spec["id"])
        return spec

    def _add_kind(self, kind: str) -> None:
        self.add(kind)
        self.ctl.status.emit(t("{w} is on your desktop.", w=t(dwidgets.TITLES[kind])))

    def _remove(self) -> None:
        w = self._current()
        if w is None:
            return
        self.cfg["widgets"] = [x for x in self.cfg["widgets"] if x is not w]
        self.current = ""
        self.save()
        self.load()

    def _enabled(self, on: bool) -> None:
        if self._loading:
            return
        self.cfg["widgets_enabled"] = bool(on)
        if on:
            self.switch_on()
        self.save()

    def _lock(self, on: bool) -> None:
        if self._loading:
            return
        self.cfg["widgets_locked"] = bool(on)
        self.save()

    def _defaults(self) -> None:
        if self.cfg["widgets"] and not confirm(self, t("Default layout"), t("Replace your {n} widget(s) with the "
                                                                          "default layout?",
                                                                          n=len(self.cfg["widgets"])),
                                               ok_text=t("Replace")):
            return
        layout: list[dict] = []
        for kind in ("clock", "calendar", "cpu", "ram"):
            spec = dwidgets.new_widget(kind, layout, 0)
            layout.append(spec)
        self.cfg["widgets"] = layout
        self.cfg["widgets_enabled"] = True
        self.switch_on()
        self.save()
        self.load(layout[0]["id"])

    def _save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, t("Desktop profiles"), t("Name of the profile"),
                                        text=self.profiles.currentText())
        if ok and name.strip():
            cfgmod.save_profile(self.cfg, name.strip()[:40])
            self.save()
            self.load()
            self.profiles.setCurrentText(name.strip()[:40])

    def _apply_profile(self) -> None:
        name = self.profiles.currentText()
        if name:
            cfgmod.apply_profile(self.cfg, name)
            self.switch_on()
            self.save()
            self.load()
            self.ctl.status.emit(t("Desktop profile “{n}” is in use.", n=name))

    def _delete_profile(self) -> None:
        name = self.profiles.currentText()
        if name and confirm(self, t("Desktop profiles"), t("Delete the profile “{n}”?", n=name), ok_text=t("Delete")):
            self.cfg.get("profiles", {}).pop(name, None)
            self.save()
            self.load()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, t("Export…"), "akeno-widgets.json", "JSON (*.json)")
        if path:
            dwidgets.export_layout(self.cfg["widgets"], Path(path))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Import…"), "", "JSON (*.json)")
        if not path:
            return
        try:
            layout = dwidgets.import_layout(Path(path))
        except (OSError, ValueError) as exc:
            info(self, t("Widgets"), t("This file is not an AKENO widget layout."), str(exc), error=True)
            return
        if confirm(self, t("Widgets"), t("Replace your {n} widget(s) with the {m} of this layout?",
                                         n=len(self.cfg["widgets"]), m=len(layout)), ok_text=t("Replace")):
            self.cfg["widgets"] = layout
            self.save()
            self.load()

    def on_show(self) -> None:
        self.reload_cfg()
        self.load()
        self._context()
        self._refresh_previews()


def preview_png(cfg: dict, monitor: str, name: str, wallpaper: QPixmap | None = None) -> bytes:
    """A 640×360 library picture: the desktop as the theme makes it (wallpaper, widgets, dock)."""
    from akeno.ui.desktop_editors import compose_preview
    if wallpaper is None:
        for item in cfgmod.wallpaper(cfg, monitor)["playlist"]:
            if (item.get("kind") or media.kind_of(item["path"])) in ("image", "gif") and Path(item["path"]).is_file():
                img = QImage(item["path"])
                if not img.isNull():
                    wallpaper = QPixmap.fromImage(img.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                             Qt.TransformationMode.SmoothTransformation))
                    break
            from akeno.ui.desktop_media import thumbnails
            cached = thumbnails().cached(item["path"])
            if cached is not None:
                wallpaper = QPixmap.fromImage(cached)
                break
    pm = compose_preview(cfg, monitor, wallpaper)
    if wallpaper is None and not cfg.get("widgets") and not cfg.get("dock", {}).get("enabled"):
        p = QPainter(pm)
        font = p.font()
        font.setPointSize(28)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
        p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.toImage().save(buf, "PNG")
    return bytes(data)


class ThemeDialog(QDialog):
    """Save the current desktop as a theme, or edit a library theme's details."""

    def __init__(self, parent, title: str, values: dict | None = None, saving: bool = True):
        super().__init__(parent)
        from akeno.ui import desktop_media as dm
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        values = values or {}
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        self.name = QLineEdit(values.get("name", ""))
        self.name.setAccessibleName(t("Name"))
        self.name.setMaxLength(60)
        self.author = QLineEdit(values.get("author", ""))
        self.author.setAccessibleName(t("Author"))
        self.author.setMaxLength(60)
        self.license = QComboBox()
        self.license.setEditable(True)
        self.license.setAccessibleName(t("License"))
        self.license.addItems(["CC BY 4.0", "CC BY-SA 4.0", "CC BY-NC 4.0", "CC0 1.0", t("All rights reserved")])
        if values.get("license"):
            self.license.setCurrentText(values["license"])
        self.description = QPlainTextEdit(values.get("description", ""))
        self.description.setAccessibleName(t("Description"))
        self.description.setMaximumHeight(80)
        rows = [(t("Name"), self.name), (t("Author"), self.author), (t("License"), self.license),
                (t("Description"), self.description)]
        self.source = QComboBox()
        self.source.setAccessibleName(t("Wallpapers of"))
        for name, text in screens():
            self.source.addItem(text, name)
        if saving:
            rows.insert(3, (t("Wallpapers of"), self.source))
        lay.addLayout(form_grid(rows, 120, 360))
        self.accent = dm.Segmented({c: "" for c in SWATCHES}, values.get("accent") or SWATCHES[0])
        for c, b in self.accent.buttons.items():
            b.setFixedSize(28, 28)
            b.setToolTip(c)
            b.setAccessibleName(t("Colour {c}", c=c))
            b.setStyleSheet(f"QPushButton {{ background: {c}; border: 2px solid {theme.BORDER}; border-radius: 14px; }}"
                            f"QPushButton:checked {{ border-color: #FFFFFF; }}")
        lay.addWidget(label(t("Accent colour for widgets and the dock"), "overline"))
        lay.addWidget(self.accent)
        self.chk_widgets = QCheckBox(t("Include the widget layout"))
        self.chk_widgets.setChecked(True)
        self.chk_dock = QCheckBox(t("Include the dock style (never your pinned programs)"))
        self.chk_dock.setChecked(True)
        if saving:
            lay.addWidget(self.chk_widgets)
            lay.addWidget(self.chk_dock)
            lay.addWidget(label(t("Only share wallpapers you made yourself or whose license allows it."), "muted",
                                shrink=True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict:
        return {"name": self.name.text().strip() or t("My desktop"), "author": self.author.text().strip(),
                "license": self.license.currentText().strip(), "description": self.description.toPlainText(),
                "monitor": self.source.currentData() or "", "widgets": self.chk_widgets.isChecked(),
                "dock": self.chk_dock.isChecked(), "accent": self.accent.value()}


class ThemeCard(QFrame):
    """A theme in the gallery: its picture, name, what it holds, Apply and the other actions."""

    WIDTH = 316

    def __init__(self, th: themes.Theme, active: bool, page: "ThemesPage"):
        super().__init__()
        self.theme = th
        self.setObjectName("ThemeCard")
        self.setFixedWidth(self.WIDTH)
        border = theme.ACCENT if active else theme.BORDER
        self.setStyleSheet(f"#ThemeCard {{ background: {theme.SURFACE_2}; border: 1px solid {border};"
                           f" border-radius: 12px; }} #ThemeCard:hover {{ border-color: "
                           f"{theme.ACCENT if active else theme.BORDER_BTN}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 12)
        lay.setSpacing(6)
        pic = QLabel()
        w = self.WIDTH - 16
        pic.setFixedSize(w, round(w * 9 / 16))
        pm = QPixmap(str(th.preview)) if th.preview else QPixmap()
        canvas = QPixmap(pic.size())
        canvas.fill(QColor(0, 0, 0, 0))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        from akeno.ui.desktop_media import placeholder, rounded
        from PySide6.QtCore import QRectF
        r = QRectF(0, 0, pic.width(), pic.height())
        if not pm.isNull():
            rounded(painter, r, 9, pm)
        else:
            placeholder(painter, r, 9, "image")
        painter.end()
        pic.setPixmap(canvas)
        pic.setCursor(Qt.CursorShape.PointingHandCursor)
        pic.mousePressEvent = lambda _e: page.preview(th)
        lay.addWidget(pic)
        head = QHBoxLayout()
        head.setContentsMargins(4, 4, 4, 0)
        head.addWidget(ElidedLabel(th.name, "h3"), 1)
        if active:
            head.addWidget(StatusPill(t("Active"), "ok"))
        lay.addLayout(head)
        byline = " · ".join(x for x in (th.author and t("by {a}", a=th.author), th.license) if x)
        sub = label(byline or t("Your theme"), "muted", shrink=True)
        sub.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(sub)
        contents = label(page.contents(th), "dim", shrink=True)
        contents.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(contents)
        row = QHBoxLayout()
        row.setContentsMargins(4, 4, 4, 0)
        row.setSpacing(6)
        row.addWidget(button(t("Apply"), "check", "primary", on_click=lambda: page.use(th)))
        row.addWidget(button(t("Preview"), "eye", "ghost", on_click=lambda: page.preview(th)))
        row.addStretch(1)
        more = QToolButton()
        more.setText("···")
        more.setAutoRaise(True)
        more.setToolTip(t("More"))
        more.setAccessibleName(t("More"))
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icons.icon("edit", theme.TEXT, 16), t("Edit…"), lambda: page.edit(th))
        menu.addAction(icons.icon("copy", theme.TEXT, 16), t("Duplicate"), lambda: page.duplicate(th))
        menu.addAction(icons.icon("export", theme.TEXT, 16), t("Export…"), lambda: page.export_dialog(th))
        menu.addSeparator()
        menu.addAction(icons.icon("trash", theme.TEXT, 16), t("Delete"), lambda: page._delete(th))
        more.setMenu(menu)
        row.addWidget(more)
        lay.addLayout(row)


class ThemesPage(DesktopPage):
    """Theme Workshop: whole desktops (wallpaper, its playback, dock look, widget layout and look, accent) as
    themes to save, preview, apply, duplicate, edit, share and undo."""

    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        self.btn_restore = button(t("Restore previous"), "restore", on_click=self.restore)
        root.addWidget(PageHeader(t("Theme Workshop"), t("Your whole desktop as a theme: wallpaper, dock look, widgets "
                                                         "and colours. Save it, share it, switch back any time."),
                                  [self.btn_restore, button(t("Import…"), "download", on_click=self._import),
                                   button(t("Save current desktop"), "plus", "primary", on_click=self._save)]))
        self.lib = Card(t("Your themes"), "palette")
        self.grid_holder = QWidget()
        self.grid = FlowLayout(self.grid_holder, spacing=14)
        self.lib.body.addWidget(self.grid_holder)
        self.empty = EmptyState("palette", t("No themes yet"),
                                t("Save your current desktop as a theme, or import a .akenotheme file."),
                                button(t("Save current desktop"), "plus", "primary", on_click=self._save))
        self.lib.body.addWidget(self.empty)
        self.lib.body.addWidget(label(t("Themes contain media only: programs, scripts for Windows and links are "
                                        "refused when importing. Your pinned dock programs are never part of a theme."),
                                      "muted", shrink=True))
        root.addWidget(self.lib)
        root.addStretch(1)
        self.load()

    def contents(self, th: themes.Theme) -> str:
        m = th.manifest
        n = th.files
        parts = [t("1 wallpaper") if n == 1 else t("{n} wallpapers", n=n)]
        if m.get("dock"):
            parts.append(t("Dock"))
        if m.get("widgets"):
            n = len(m["widgets"])
            parts.append(t("1 widget") if n == 1 else t("{n} widgets", n=n))
        return " · ".join(parts)

    def load(self) -> None:
        clear_layout(self.grid)
        items = themes.library()
        current = self.cfg.get("theme")
        for th in items:
            self.grid.addWidget(ThemeCard(th, th.id == current, self))
        self.grid_holder.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.btn_restore.setEnabled(themes.has_snapshot())

    def _wallpaper_pm(self, monitor: str) -> QPixmap | None:
        page = self.win.pages.get("wallpapers")
        w = cfgmod.wallpaper(self.cfg, monitor)
        for entry in w["playlist"][:1]:
            if (entry.get("kind") or media.kind_of(entry["path"])) == "image" and Path(entry["path"]).is_file():
                img = QImage(entry["path"])
                if not img.isNull():
                    return QPixmap.fromImage(img.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                        Qt.TransformationMode.SmoothTransformation))
        return page._monitor_thumb(monitor) if page is not None else None

    def save_current(self, name: str, author: str = "", description: str = "", license: str = "",
                     monitor: str = "", widgets: bool = True, dock: bool = True, accent: str = "") -> themes.Theme:
        """The desktop as it is now, into the theme library."""
        monitor = monitor or (screens()[0][0] if screens() else "")
        png = preview_png(self.cfg, monitor, name, self._wallpaper_pm(monitor))
        th = themes.save_to_library(self.cfg, monitor, name, author, description, license, png, widgets, dock,
                                    accent)
        self.cfg["theme"] = th.id
        self.save()
        self.load()
        return th

    def _save(self) -> None:
        dlg = ThemeDialog(self, t("Save current desktop"), {"accent": self.cfg.get("accent", "")})
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        try:
            th = self.save_current(v["name"], v["author"], v["description"], v["license"], v["monitor"], v["widgets"],
                                   v["dock"], v["accent"])
        except (themes.ThemeError, media.MediaError, OSError) as exc:
            info(self, t("Save current desktop"), t("The theme was not created."), str(exc), error=True)
            return
        self.ctl.status.emit(t("Theme “{n}” saved.", n=th.name))

    def use(self, th: themes.Theme) -> None:
        themes.snapshot(self.cfg)
        themes.apply(th, self.cfg, [name for name, _ in screens()])
        self.switch_on()
        self.save()
        self.load()
        self.ctl.status.emit(t("Theme “{n}” is in use. “Restore previous” brings your desktop back.", n=th.name))

    def restore(self) -> None:
        if themes.restore_snapshot(self.cfg):
            self.save()
            self.load()
            self.ctl.status.emit(t("Your previous desktop is back."))

    def preview(self, th: themes.Theme) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(th.name)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        pic = QLabel()
        pm = QPixmap(str(th.preview)) if th.preview else QPixmap()
        if not pm.isNull():
            pic.setPixmap(pm.scaled(800, 450, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(pic)
        lay.addWidget(label(th.name, "h2"))
        byline = " · ".join(x for x in (th.author and t("by {a}", a=th.author), th.license, self.contents(th)) if x)
        lay.addWidget(label(byline, "dim", shrink=True))
        if th.description:
            lay.addWidget(label(th.description, "muted", wrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button(t("Close"), on_click=dlg.reject))
        buttons.addWidget(button(t("Apply"), "check", "primary", on_click=lambda: (dlg.accept(), self.use(th))))
        lay.addLayout(buttons)
        dlg.exec()

    def edit(self, th: themes.Theme) -> None:
        dlg = ThemeDialog(self, t("Edit…"), {"name": th.name, "author": th.author, "license": th.license,
                                             "description": th.description,
                                             "accent": th.manifest.get("accent", "")}, saving=False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        try:
            themes.edit(th, name=v["name"], author=v["author"], license=v["license"], description=v["description"],
                        accent=v["accent"])
        except (themes.ThemeError, OSError) as exc:
            info(self, t("Edit…"), str(exc), error=True)
        self.load()

    def duplicate(self, th: themes.Theme, name: str | None = None) -> themes.Theme | None:
        if name is None:
            name, ok = QInputDialog.getText(self, t("Duplicate"), t("Name"), text=t("{n} (copy)", n=th.name))
            if not ok:
                return None
        copy = themes.duplicate(th, name)
        self.load()
        return copy

    def export_theme(self, th: themes.Theme, path: Path) -> Path:
        return themes.export_installed(th, path)

    def export_dialog(self, th: themes.Theme) -> None:
        path, _ = QFileDialog.getSaveFileName(self, t("Export…"), f"{th.name}{themes.SUFFIX}",
                                              t("AKENO themes (*.akenotheme)"))
        if not path:
            return
        try:
            out = self.export_theme(th, Path(path))
        except (themes.ThemeError, OSError) as exc:
            info(self, t("Export…"), t("The theme was not exported."), str(exc), error=True)
            return
        self.ctl.status.emit(t("Theme saved: {p}", p=str(out)))

    def _delete(self, th: themes.Theme) -> None:
        if confirm(self, t("Your themes"), t("Delete the theme “{n}” and its files from the library?", n=th.name),
                   ok_text=t("Delete")):
            themes.remove(th)
            self.load()

    def import_file(self, path: Path, ask=True) -> themes.Theme | None:
        try:
            manifest = themes.check_archive(path)
        except (themes.ThemeError, OSError) as exc:
            info(self, t("Import theme…"), t("This theme cannot be imported."), str(exc), error=True)
            return None
        web = sum(1 for i in manifest["wallpaper"]["playlist"] if i["kind"] == "web")
        details = "\n".join(x for x in (
            t("Author: {a}", a=manifest["author"] or "—"), t("License: {l}", l=manifest["license"] or "—"),
            t("{n} wallpaper(s)", n=len(manifest["wallpaper"]["playlist"])),
            t("{n} web wallpaper(s): they run without network access and only read their own folder.", n=web)
            if web else "", manifest["description"]) if x)
        if ask and not confirm(self, t("Import theme…"), t("Import “{n}”?", n=manifest["name"]), details,
                               t("Import")):
            return None
        try:
            th = themes.install(path)
        except FileExistsError:
            if ask and not confirm(self, t("Import theme…"), t("“{n}” is already in the library. Replace it?",
                                                              n=manifest["name"]), ok_text=t("Replace")):
                return None
            th = themes.install(path, replace=True)
        except (themes.ThemeError, OSError) as exc:
            info(self, t("Import theme…"), t("This theme cannot be imported."), str(exc), error=True)
            return None
        self.load()
        return th

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Import theme…"), "", t("AKENO themes (*.akenotheme)"))
        if path:
            self.import_file(Path(path))

    def on_show(self) -> None:
        self.reload_cfg()
        self.load()


# -- Find wallpapers (akeno/desktop/sources.py) ------------------------------------------------------------------
class _TilePicture(QLabel):
    """A result's picture with its length, resolution and kind on it (painted, like the library cards)."""

    def __init__(self, video, chosen, size: QSize):
        super().__init__()
        self.video, self.chosen = video, chosen
        self.pm: QPixmap | None = None
        self.setFixedSize(size)

    def paintEvent(self, e):
        from PySide6.QtCore import QRectF
        from akeno.ui import desktop_media as dm
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self.pm is not None:
            dm.rounded(p, r, 8, self.pm)
        else:
            dm.placeholder(p, r, 8, "video")
        kind = {"animation": t("Animation"), "film": t("Filmed")}.get(self.video.kind, "")
        if kind:
            dm.badge(p, 8, 8, kind.upper(), QColor(8, 12, 21, 190))
        if self.video.duration:
            dm.badge(p, r.right() - 8, r.bottom() - 28, dm.duration_text(self.video.duration), QColor(8, 12, 21, 190),
                     right=True)
        if self.chosen and self.chosen.height:
            res = "4K" if self.chosen.height >= 2000 else f"{self.chosen.height}p"
            dm.badge(p, 8, r.bottom() - 28, res, QColor(theme.ACCENT_STRONG))


class VideoTile(QFrame):
    """One Pixabay result as a media card: picture, what it shows, its creator, size, Import and the page."""

    WIDTH = 252

    def __init__(self, video, quality: str, on_add, on_page):
        super().__init__()
        self.video = video
        self.setObjectName("VideoTile")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"#VideoTile {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
                           f" border-radius: 12px; }} #VideoTile:hover {{ border-color: {theme.BORDER_BTN}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 10)
        lay.setSpacing(4)
        chosen = video.best(quality)
        self.thumb = _TilePicture(video, chosen, QSize(self.WIDTH - 16, round((self.WIDTH - 16) * 9 / 16)))
        lay.addWidget(self.thumb)
        title = ElidedLabel(video.title.capitalize() if video.title.islower() else video.title, "h3")
        title.setContentsMargins(2, 4, 2, 0)
        lay.addWidget(title)
        meta = []
        if video.user:
            meta.append(t("by {name}", name=video.user))
        if chosen:
            meta.append(chosen.label)
            if chosen.bytes:
                meta.append(f"{chosen.bytes / 1e6:.0f} MB")
        line = ElidedLabel(" · ".join(meta), "muted")
        line.setContentsMargins(2, 0, 2, 0)
        lay.addWidget(line)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_add = button(t("Import"), "download", "primary", on_click=lambda: on_add(self))
        self.btn_add.setEnabled(chosen is not None)
        row.addWidget(self.btn_add)
        row.addStretch(1)
        row.addWidget(button(t("Page"), "external", "link", tip=video.page_url, on_click=lambda: on_page(video)))
        lay.addLayout(row)
        self.setAccessibleName(video.title)

    def set_preview(self, data: bytes) -> None:
        img = QImage.fromData(data)
        if img.isNull():
            return
        self.thumb.pm = QPixmap.fromImage(img)
        self.thumb.update()


class SourcesPanel(QWidget):
    """Wallpapers → Find wallpapers: Pixabay through its API, the other sites in the browser, and recent downloads."""

    def __init__(self, page):
        super().__init__()
        from akeno.desktop import sources
        self.src = sources
        self.page, self.ctl = page, page.ctl
        self.keys = sources.KeyStore()
        self.results = None
        self.page_no = 1
        self.query = ""
        self.epoch = 0
        self.tiles: list[VideoTile] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.CARD_GAP)

        # Pixabay
        px = Card("Pixabay", "film", right=button("pixabay.com", "external", "link",
                                                 on_click=lambda: self._open("https://pixabay.com/videos/")))
        self.key_box = QWidget()
        kl = QVBoxLayout(self.key_box)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(8)
        kl.addWidget(label(t("Pixabay offers free videos for wallpapers through an official interface. Searching it "
                             "needs your own free Pixabay API key: sign in on Pixabay, open the API documentation and "
                             "copy the key shown there."), "dim", wrap=True))
        krow = QHBoxLayout()
        krow.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(t("Pixabay API key"))
        self.key_edit.setAccessibleName(t("Pixabay API key"))
        self.key_edit.returnPressed.connect(self._save_key)
        krow.addWidget(self.key_edit, 1)
        krow.addWidget(button(t("Save key"), "check", "primary", on_click=self._save_key))
        krow.addWidget(button(t("Get a free API key"), "external", on_click=lambda: self._open(
            "https://pixabay.com/api/docs/")))
        kl.addLayout(krow)
        kl.addWidget(label(t("The key is stored encrypted for your Windows account and is only sent to Pixabay."),
                           "muted", wrap=True))
        px.body.addWidget(self.key_box)

        self.search_box = QWidget()
        sl = QVBoxLayout(self.search_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search animated wallpapers…"))
        self.search.setAccessibleName(t("Search animated wallpapers"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(lambda: self.run_search(1))
        srow.addWidget(self.search, 1)
        self.kind = _combo(sources.KIND_TEXT, t("Kind of video"))
        self.kind.setMaximumWidth(170)
        srow.addWidget(self.kind)
        self.quality = _combo(sources.QUALITY_TEXT, t("Quality"))
        self.quality.setCurrentIndex(self.quality.findData("1080p"))
        self.quality.setMaximumWidth(140)
        self.quality.setToolTip(t("4K when Pixabay has it, otherwise the best size there is."))
        srow.addWidget(self.quality)
        self.btn_search = button(t("Search"), "search", "primary", on_click=lambda: self.run_search(1))
        srow.addWidget(self.btn_search)
        sl.addLayout(srow)
        topics = FlowLayout(spacing=6)
        for word, text in sources.TOPICS.items():
            topics.addWidget(button(t(text), kind="chip", on_click=lambda _=False, w=word: self._topic(w)))
        sl.addLayout(topics)
        self.status = label("", "muted", wrap=True)
        sl.addWidget(self.status)
        self.grid_holder = QWidget()
        self.grid = FlowLayout(self.grid_holder, spacing=12)
        sl.addWidget(self.grid_holder)
        self.empty = EmptyState("film", t("Search Pixabay"), t("Type what you want to see, or pick a topic above."))
        sl.addWidget(self.empty)
        nav = QHBoxLayout()
        self.pager = label("", "muted")
        nav.addWidget(self.pager)
        nav.addStretch(1)
        self.btn_prev = button(t("Previous"), on_click=lambda: self.run_search(self.page_no - 1))
        self.btn_next = button(t("Next"), on_click=lambda: self.run_search(self.page_no + 1))
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        sl.addLayout(nav)
        foot = QHBoxLayout()
        foot.addWidget(label(t("Videos from Pixabay, free to use under the Pixabay Content License. Each result names "
                               "its creator; added videos keep a note of where they came from."), "muted", wrap=True), 1)
        foot.addWidget(button(t("Forget API key"), kind="link", on_click=self._forget_key))
        sl.addLayout(foot)
        px.body.addWidget(self.search_box)
        lay.addWidget(px)

        # Sites without an interface for apps
        ext = Card(t("Other wallpaper sites"), "globe")
        ext.body.addWidget(label(t("These sites offer no interface for apps. Open one in your browser, download a "
                                   "wallpaper there under the site's own terms, then add the file from Recent downloads. "
                                   "AKENO does not download from them or show their content."),
                                 "dim", wrap=True))
        for site in sources.SITES:
            row = QHBoxLayout()
            row.setSpacing(12)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(label(site.name, "h3"))
            col.addWidget(label(t(site.about), "muted", wrap=True))
            row.addLayout(col, 1)
            row.addWidget(button(t("Open website"), "external", on_click=lambda _=False, u=site.url: self._open(u)))
            ext.body.addLayout(row)

        # Recent downloads
        self.imp = Card(t("Recent downloads"), "download",
                        right=button(t("Choose a file…"), "folder", on_click=self._choose))
        self.imp_list = QVBoxLayout()
        self.imp_list.setSpacing(4)
        self.imp.body.addLayout(self.imp_list)
        self.imp.body.addWidget(label(t("Video files (MP4, WebM, MKV, MOV) and GIFs from your Downloads folder of the "
                                        "last two weeks. Adding one copies it into AKENO's wallpaper library, so it "
                                        "stays when you tidy the folder."), "muted", wrap=True))
        side = ResponsiveRow(theme.CARD_GAP)
        side.add(ext, 1)
        side.add(self.imp, 1)
        lay.addWidget(side)
        lay.addStretch(1)
        self._update_mode()

    # -- state ---------------------------------------------------------------------------------------------------
    def on_show(self) -> None:
        self._update_mode()
        self.refresh_downloads()

    def _update_mode(self) -> None:
        has_key = bool(self.keys.get())
        self.key_box.setVisible(not has_key)
        self.search_box.setVisible(has_key)
        self._paging()

    def _paging(self) -> None:
        res = self.results
        show = res is not None and bool(res.videos)
        self.empty.setVisible(not show)
        self.grid_holder.setVisible(show)
        self.btn_prev.setVisible(show and res.pages > 1)
        self.btn_next.setVisible(show and res.pages > 1)
        self.btn_prev.setEnabled(show and self.page_no > 1)
        self.btn_next.setEnabled(show and self.page_no < res.pages)
        self.pager.setText(t("Page {p} of {n}", p=self.page_no, n=res.pages) if show else "")

    def _open(self, url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _save_key(self) -> None:
        try:
            self.keys.set(self.key_edit.text())
        except self.src.SourceError as exc:
            info(self, "Pixabay", exc.text(), error=True)
            return
        self.key_edit.clear()
        self._update_mode()
        self.search.setFocus()

    def _forget_key(self) -> None:
        if confirm(self, t("Forget API key"), t("Remove the Pixabay API key from this PC? Wallpapers you added stay.")):
            self.keys.clear()
            self.results = None
            clear_layout(self.grid)
            self._update_mode()

    def _topic(self, word: str) -> None:
        self.search.setText(word)
        self.run_search(1)

    # -- search --------------------------------------------------------------------------------------------------
    def run_search(self, page: int) -> None:
        query = self.search.text().strip()
        if not query:
            self.search.setFocus()
            return
        key, kind, quality = self.keys.get(), self.kind.currentData(), self.quality.currentData()
        self.epoch += 1
        epoch = self.epoch
        self.status.setText(t("Searching Pixabay…"))
        self.btn_search.setEnabled(False)

        def work():
            return self.src.Pixabay(key).search(query, kind, page, 24, quality)

        def done(res):
            if epoch != self.epoch or not isValid(self):
                return
            self.btn_search.setEnabled(True)
            self.results, self.page_no, self.query = res, page, query
            self._show_results(quality)

        def fail(exc):
            if epoch != self.epoch or not isValid(self):
                return
            self.btn_search.setEnabled(True)
            self.status.setText(exc.text() if isinstance(exc, self.src.SourceError) else str(exc))
        self.ctl.run("pixabay-search", work, done, fail, silent=True)

    def _show_results(self, quality: str) -> None:
        clear_layout(self.grid)
        self.tiles = []
        res = self.results
        if not res.videos:
            self.status.setText(t("Pixabay has no video for “{q}”.", q=self.query))
        else:
            self.status.setText(t("{n} videos on Pixabay", n=res.total) + (" · " + t("from the last 24 hours' results")
                                                                           if res.from_cache else ""))
        for video in res.videos:
            tile = VideoTile(video, quality, self._add, lambda v: self._open(v.page_url))
            self.tiles.append(tile)
            self.grid.addWidget(tile)
        self._paging()
        self._load_previews()

    def _load_previews(self) -> None:
        key = self.keys.get()
        epoch = self.epoch
        wanted = [(tile, tile.video.thumbnail) for tile in self.tiles if tile.video.thumbnail]

        def work(emit):
            client = self.src.Pixabay(key)
            for i, (_, url) in enumerate(wanted):
                try:
                    emit((i, client.thumbnail(url)))
                except self.src.SourceError:
                    continue

        def preview(item):
            i, data = item
            if epoch == self.epoch and isValid(self) and i < len(wanted) and isValid(wanted[i][0]):
                wanted[i][0].set_preview(data)
        if wanted:
            self.ctl.run("pixabay-previews", work, None, None, on_progress=preview, silent=True)

    def _add(self, tile: VideoTile) -> None:
        video, quality = tile.video, self.quality.currentData()
        key = self.keys.get()
        tile.btn_add.setEnabled(False)
        tile.btn_add.setText(t("Downloading…"))

        def work(emit):
            return self.src.Pixabay(key).download(video, quality, self.src.library_dir(),
                                                  lambda got, total: emit((got, total)))

        def progress(value):
            got, total = value
            if isValid(tile):
                tile.btn_add.setText(f"{got / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"{got / 1e6:.0f} MB")

        def done(path):
            if isValid(tile):
                tile.btn_add.setText(t("In the library"))
            if isValid(self.page):
                self.page.add_download(str(path), video.title, "workshop", video.user, "Pixabay Content License")

        def fail(exc):
            if isValid(tile):
                tile.btn_add.setEnabled(True)
                tile.btn_add.setText(t("Import"))
            info(self, "Pixabay", exc.text() if isinstance(exc, self.src.SourceError) else str(exc), error=True)
        if not self.ctl.run(f"pixabay-download-{video.id}", work, done, fail, on_progress=progress, silent=True):
            tile.btn_add.setEnabled(True)
            tile.btn_add.setText(t("Import"))

    def _added(self, problems: list[str], name: str) -> None:
        if problems:
            info(self, t("Wallpapers"), t("Some files were not added."), "\n".join(problems), error=True)
        else:
            self.ctl.status.emit(t("In your library: {name}", name=name))

    # -- recent downloads ----------------------------------------------------------------------------------------
    def refresh_downloads(self) -> None:
        clear_layout(self.imp_list)
        files = self.src.recent_downloads()
        if not files:
            self.imp_list.addWidget(label(t("No video files from the last two weeks in your Downloads folder."), "dim",
                                          wrap=True))
            return
        from datetime import datetime
        for path in files:
            row = QHBoxLayout()
            row.setSpacing(10)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("film", theme.ACCENT_TEXT, 18))
            row.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(path.name))
            try:
                st = path.stat()
                col.addWidget(label(f"{st.st_size / 1e6:.0f} MB · " + datetime.fromtimestamp(st.st_mtime)
                                    .strftime("%d.%m.%Y %H:%M"), "muted"))
            except OSError:
                pass
            row.addLayout(col, 1)
            row.addWidget(button(t("Import"), "download", on_click=lambda _=False, p=path: self._import(p)))
            self.imp_list.addLayout(row)

    def _choose(self) -> None:
        from akeno.desktop.sensors import downloads_dir
        path, _ = QFileDialog.getOpenFileName(self, t("Choose a downloaded wallpaper"), str(downloads_dir()),
                                              t("Videos and GIFs") + " (*.mp4 *.webm *.mkv *.mov *.m4v *.gif)")
        if path:
            self._import(Path(path))

    def _import(self, path: Path) -> None:
        def done(dest):
            if isValid(self.page):
                self._added(self.page.add_paths([str(dest)]), Path(dest).name)
                self.page.tabs.setCurrentIndex(0)

        def fail(exc):
            info(self, t("Recent downloads"), exc.text() if isinstance(exc, self.src.SourceError) else str(exc),
                 error=True)
        self.ctl.run("wallpaper-import", lambda: self.src.import_file(path, self.src.library_dir()), done, fail,
                     silent=True)
