"""DESKTOP: Wallpapers, Dock, Widgets and the Theme Workshop. The pages edit ``desktop.json``; the desktop host
(its own process, see akeno/desktop/host.py) applies every change live and is started when something is switched
on."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSizePolicy, QSlider, QSpinBox,
                               QStackedWidget, QTabBar, QTimeEdit, QVBoxLayout, QWidget)

from shiboken6 import isValid

from akeno.desktop import config as cfgmod
from akeno.desktop import media, themes, winapi
from akeno.desktop import widgets as dwidgets
from akeno.i18n import t
from akeno.ui import icons, theme
from akeno.ui.widgets import (Card, ElidedLabel, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow,
                              StatusPill, button, clear_layout, confirm, fit_control, form_grid, info, label)

KIND_TEXT = {"image": "Picture", "gif": "GIF animation", "video": "Video", "web": "Web wallpaper"}
MODE_TEXT = {"single": "One wallpaper", "rotate": "Change every few minutes", "schedule": "By time of day"}
FIT_TEXT = {"fill": "Fill", "fit": "Fit", "stretch": "Stretch", "center": "Centre"}
EDGE_TEXT = {"bottom": "Bottom", "top": "Top", "left": "Left", "right": "Right"}
STATE_TEXT = {"starting": "Starting", "running": "Running", "waiting": "Restarting after a crash",
              "failed": "Stopped after repeated crashes", "stopped": "Stopped"}
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
    """(QScreen name, readable label) of each monitor."""
    out = []
    primary = QGuiApplication.primaryScreen()
    for i, s in enumerate(QGuiApplication.screens()):
        g = s.geometry()
        text = t("Monitor {n}", n=i + 1) + f" · {round(g.width() * s.devicePixelRatio())}×" \
            f"{round(g.height() * s.devicePixelRatio())}"
        if s is primary:
            text += " · " + t("main monitor")
        out.append((s.name(), text))
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


class WallpapersPage(DesktopPage):
    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        root = QVBoxLayout(self)
        self.header(root, "Wallpapers", "Animated wallpapers behind your desktop icons: pictures, GIFs, videos and web "
                    "wallpapers (HTML, WebGL). Each monitor's wallpaper runs in its own process; when you stop, "
                    "Windows shows its own wallpaper again. Nothing of Explorer is replaced.")
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab(t("My wallpapers"))
        self.tabs.addTab(t("Find wallpapers"))
        root.addWidget(self.tabs)
        self.views = QStackedWidget()
        root.addWidget(self.views)
        mine = QWidget()
        root = QVBoxLayout(mine)                      # the existing wallpaper settings live on the first tab
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        self.views.addWidget(mine)
        self.sources: SourcesPanel | None = None
        self.tabs.currentChanged.connect(self._show_tab)

        top = ResponsiveRow(spacing=theme.CARD_GAP)
        status = Card(t("Desktop Studio"), "monitor")
        self.pill = StatusPill(t("Not running"), "pending")
        status.body.addWidget(self.pill)
        self.details = label("", "muted", shrink=True)
        status.body.addWidget(self.details)
        self.btn_start = button(t("Start"), "play", "primary", on_click=self._start)
        self.btn_stop = button(t("Stop"), "stop", on_click=self._stop)
        status.body.addWidget(_row(self.btn_start, self.btn_stop, button(t("Refresh"), "refresh",
                                                                        on_click=self.refresh_status)))
        top.add(status, 2)

        mon = Card(t("Monitor"), "monitor")
        self.monitor = QComboBox()
        self.monitor.setAccessibleName(t("Monitor"))
        self.monitor.currentIndexChanged.connect(self._load_monitor)
        mon.body.addWidget(self.monitor)
        self.chk_enabled = QCheckBox(t("Show an AKENO wallpaper on this monitor"))
        self.chk_enabled.toggled.connect(lambda v: self._set("enabled", bool(v)))
        mon.body.addWidget(self.chk_enabled)
        mon.body.addWidget(_row(button(t("Use on every monitor"), "layers", on_click=self._copy_all)))
        top.add(mon, 2)
        root.addWidget(top)

        lists = ResponsiveRow(spacing=theme.CARD_GAP)
        pl = Card(t("Playlist"), "image")
        self.mode = _combo(MODE_TEXT, t("Playlist mode"))
        self.mode.currentIndexChanged.connect(lambda _: self._set("mode", self.mode.currentData()))
        pl.body.addLayout(_line(t("Mode"), self.mode))
        self.list = QListWidget()
        self.list.setAccessibleName(t("Playlist"))
        self.list.setIconSize(QSize(96, 54))
        self.list.setSpacing(2)
        self.list.setMinimumHeight(200)
        self.list.currentRowChanged.connect(self._load_item)
        self.list_empty = EmptyState("image", t("No wallpaper on this monitor yet"),
                                     t("Add pictures, GIFs, videos or a web wallpaper folder, or find one on the "
                                       "Find wallpapers tab."),
                                     button(t("Add files…"), "plus", "primary", on_click=self._add_files))
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.list_empty)
        self.list_stack.setMinimumHeight(200)
        pl.body.addWidget(self.list_stack)
        pl.body.addWidget(_row(button(t("Add files…"), "plus", on_click=self._add_files),
                               button(t("Add web wallpaper folder…"), "folder", on_click=self._add_folder),
                               button(t("Remove"), "trash", on_click=self._remove),
                               button(t("Up"), on_click=lambda: self._move(-1)),
                               button(t("Down"), on_click=lambda: self._move(1))))
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 24 * 60)
        self.minutes.setSuffix(" min")
        self.minutes.setAccessibleName(t("Minutes before the next wallpaper"))
        self.minutes.valueChanged.connect(self._item_changed)
        pl.body.addLayout(_line(t("Minutes before the next wallpaper"), self.minutes))
        self.from_time = QTimeEdit()
        self.from_time.setDisplayFormat("HH:mm")
        self.from_time.setAccessibleName(t("Shown from (by time of day)"))
        self.from_time.timeChanged.connect(self._item_changed)
        self.chk_scheduled = QCheckBox(t("Part of the daily schedule"))
        self.chk_scheduled.toggled.connect(self._item_changed)
        pl.body.addLayout(_line(t("Shown from (by time of day)"), self.from_time))
        pl.body.addWidget(self.chk_scheduled)
        self.item_note = label("", "muted", shrink=True)
        pl.body.addWidget(self.item_note)
        lists.add(pl, 3)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(theme.CARD_GAP)
        opts = Card(t("Playback"), "sliders")
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
        side_lay.addWidget(opts)
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
        lists.add(side, 2)
        root.addWidget(lists)

        rules = Card(t("Pause wallpapers when"), "gauge")
        self.rule_checks = {}
        flow = []
        for key, text in PAUSE_TEXT.items():
            box = QCheckBox(t(text))
            box.toggled.connect(lambda v, k=key: self._set_rule(k, bool(v)))
            self.rule_checks[key] = box
            flow.append(box)
        rules.body.addWidget(_row(*flow))
        self.cpu_limit = QSpinBox()
        self.cpu_limit.setRange(0, 100)
        self.cpu_limit.setSuffix(" %")
        self.cpu_limit.setSpecialValueText(t("Never"))
        self.cpu_limit.setAccessibleName(t("The CPU is busier than"))
        self.cpu_limit.valueChanged.connect(lambda v: self._set_rule("cpu_percent", int(v)))
        rules.body.addLayout(_line(t("The CPU is busier than"), self.cpu_limit))
        rules.body.addWidget(label(t("Paused wallpapers keep their last picture and use no CPU or graphics time."),
                                   "muted", shrink=True))
        root.addWidget(rules)
        root.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh_status)
        self._fill_monitors()

    def _show_tab(self, index: int) -> None:
        if index == 1 and self.sources is None:          # built on first use: nothing goes online before that
            self.sources = SourcesPanel(self)
            self.views.addWidget(self.sources)
        for i in range(self.views.count()):               # only the shown tab counts for the page height
            w = self.views.widget(i)
            on = i == index
            w.setSizePolicy(QSizePolicy.Policy.Preferred if on else QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred if on else QSizePolicy.Policy.Ignored)
        self.views.setCurrentIndex(index)
        if index == 1:
            self.sources.on_show()

    # -- loading -------------------------------------------------------------------------------------------------
    def _fill_monitors(self) -> None:
        self.monitor.blockSignals(True)
        self.monitor.clear()
        for name, text in screens():
            self.monitor.addItem(text, name)
        self.monitor.blockSignals(False)
        self._load_monitor()

    def name(self) -> str:
        return self.monitor.currentData() or ""

    def wcfg(self) -> dict:
        return cfgmod.wallpaper(self.cfg, self.name())

    def _load_monitor(self, *_):
        self._loading = True
        try:
            w = self.wcfg()
            self.chk_enabled.setChecked(bool(w["enabled"]))
            self.mode.setCurrentIndex(max(0, self.mode.findData(w["mode"])))
            self.fit.setCurrentIndex(max(0, self.fit.findData(w["fit"])))
            self.volume.setValue(int(w["volume"]))
            self.fps.setValue(int(w["fps_limit"]))
            self.chk_interactive.setChecked(bool(w["interactive"]))
            self.chk_audio.setChecked(bool(w["audio_reactive"]))
            rules = self.cfg["pause"]
            for key, box in self.rule_checks.items():
                box.setChecked(bool(rules.get(key, True)))
            self.cpu_limit.setValue(int(rules.get("cpu_percent") or 0))
            self._fill_list(w)
            self._fill_vdesk(w)
        finally:
            self._loading = False

    def _fill_list(self, w: dict, select: int = 0) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for item in w["playlist"]:
            path = Path(item["path"])
            kind = item.get("kind") or media.kind_of(path) or "image"
            text = f"{path.name}\n{t(KIND_TEXT.get(kind, kind))} · {int(item.get('minutes', 10))} min"
            entry = QListWidgetItem(self._thumb(path, kind), text)
            if not path.exists():
                entry.setForeground(QColor(theme.RED))
                entry.setToolTip(t("The file is missing: {p}", p=str(path)))
            self.list.addItem(entry)
        self.list.blockSignals(False)
        self.list_stack.setCurrentWidget(self.list if self.list.count() else self.list_empty)
        if self.list.count():
            self.list.setCurrentRow(min(select, self.list.count() - 1))
        else:
            self._load_item(-1)

    @staticmethod
    def _thumb(path: Path, kind: str) -> QIcon:
        if kind in ("image", "gif") and path.is_file():
            img = QImage(str(path))
            if not img.isNull():
                return QIcon(QPixmap.fromImage(img.scaled(192, 108, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                          Qt.TransformationMode.SmoothTransformation)))
        return icons.icon({"video": "play", "web": "globe"}.get(kind, "image"), theme.TEXT_DIM, 48)

    def _load_item(self, row: int) -> None:
        w = self.wcfg()
        valid = 0 <= row < len(w["playlist"])
        for widget in (self.minutes, self.from_time, self.chk_scheduled):
            widget.setEnabled(valid)
        if not valid:
            self.item_note.setText(t("Add pictures, GIFs, videos or a web wallpaper folder (with index.html)."))
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
            item = QListWidgetItem(f"{desk[:8]} → {name}")
            item.setData(Qt.ItemDataRole.UserRole, desk)
            self.vd_list.addItem(item)

    # -- editing -------------------------------------------------------------------------------------------------
    def _store(self, w: dict) -> None:
        self.cfg.setdefault("wallpapers", {})[self.name()] = w
        self.save()

    def _set(self, key: str, value) -> None:
        if self._loading:
            return
        w = self.wcfg()
        w[key] = value
        if key == "enabled" and value:
            self.switch_on()
            if not w["playlist"]:
                self.item_note.setText(t("Add a wallpaper to the playlist to show it."))
        self._store(w)

    def _set_rule(self, key: str, value) -> None:
        if self._loading:
            return
        self.cfg["pause"][key] = value
        self.save()

    def _item_changed(self, *_):
        if self._loading:
            return
        row = self.list.currentRow()
        w = self.wcfg()
        if not 0 <= row < len(w["playlist"]):
            return
        w["playlist"][row]["minutes"] = int(self.minutes.value())
        w["schedule"] = [e for e in w["schedule"] if e.get("index") != row]
        if self.chk_scheduled.isChecked():
            w["schedule"].append({"from": self.from_time.time().toString("HH:mm"), "index": row})
        self._store(w)
        self._fill_list(w, row)

    def add_paths(self, paths: list[str]) -> list[str]:
        w = self.wcfg()
        first = not w["playlist"]
        problems = []
        for p in paths:
            try:
                kind = media.check(p)
            except media.MediaError as exc:
                problems.append(str(exc))
                continue
            if kind == "web" and not __import__("akeno.desktop.renderer", fromlist=["web_available"]).web_available():
                problems.append(t("{n}: web wallpapers need Qt WebEngine, which this build does not include.",
                                  n=Path(p).name))
                continue
            w["playlist"].append({"path": str(Path(p)), "kind": kind, "minutes": 10})
        if first and w["playlist"]:
            w["enabled"] = True                      # the first wallpaper of a monitor switches it on
            self.switch_on()
        self._store(w)
        self._load_monitor()
        self.list.setCurrentRow(self.list.count() - 1)
        return problems

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Add wallpapers"), "", t(MEDIA_FILTER))
        if paths:
            problems = self.add_paths(paths)
            if problems:
                info(self, t("Wallpapers"), t("Some files were not added."), "\n".join(problems), error=True)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Web wallpaper folder (with index.html)"))
        if folder:
            problems = self.add_paths([folder])
            if problems:
                info(self, t("Wallpapers"), t("The folder was not added."), "\n".join(problems), error=True)

    def _remove(self) -> None:
        row = self.list.currentRow()
        w = self.wcfg()
        if not 0 <= row < len(w["playlist"]):
            return
        w["playlist"].pop(row)
        w["schedule"] = [dict(e, index=e["index"] - (e["index"] > row)) for e in w["schedule"] if e["index"] != row]
        w["per_virtual_desktop"] = {k: v - (v > row) for k, v in w["per_virtual_desktop"].items() if v != row}
        if not w["playlist"]:
            w["enabled"] = False
        self._store(w)
        self._load_monitor()

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        w = self.wcfg()
        new = row + step
        if not (0 <= row < len(w["playlist"]) and 0 <= new < len(w["playlist"])):
            return
        items = w["playlist"]
        items[row], items[new] = items[new], items[row]
        swap = {row: new, new: row}
        w["schedule"] = [dict(e, index=swap.get(e["index"], e["index"])) for e in w["schedule"]]
        w["per_virtual_desktop"] = {k: swap.get(v, v) for k, v in w["per_virtual_desktop"].items()}
        self._store(w)
        self._fill_list(w, new)

    def _copy_all(self) -> None:
        w = self.wcfg()
        for name, _ in screens():
            self.cfg.setdefault("wallpapers", {})[name] = json.loads(json.dumps(w))
        self.save()
        self.ctl.status.emit(t("The wallpaper settings now apply to every monitor."))

    def _map_vdesk(self) -> None:
        current = winapi.virtual_desktop_id()
        row = self.list.currentRow()
        if not current or row < 0:
            return
        w = self.wcfg()
        w["per_virtual_desktop"][current] = row
        self._store(w)
        self._fill_vdesk(w)

    def _unmap_vdesk(self) -> None:
        item = self.vd_list.currentItem()
        if item is None:
            return
        w = self.wcfg()
        w["per_virtual_desktop"].pop(item.data(Qt.ItemDataRole.UserRole), None)
        self._store(w)
        self._fill_vdesk(w)

    # -- host ------------------------------------------------------------------------------------------------------
    def _start(self) -> None:
        self.switch_on()
        cfgmod.save(self.cfg)
        if not self.desktop.ensure(self.cfg):
            info(self, t("Desktop Studio"), t("Switch on a wallpaper, the dock or a widget first."))
        QTimer.singleShot(1500, self.refresh_status)

    def _stop(self) -> None:
        """Everything stays as configured; nothing is shown until Start (also after a restart of the PC)."""
        self.cfg["enabled"] = False
        cfgmod.save(self.cfg)
        self.desktop.stop()
        QTimer.singleShot(800, self.refresh_status)

    def refresh_status(self) -> None:
        status = self.desktop.status()
        if status is None:
            stopped = not self.cfg.get("enabled", True)
            self.pill.set(t("Stopped") if stopped else t("Not running"), "pending")
            self.details.setText(t("Stopped with the Stop button. Start shows everything again.") if stopped else
                                 t("Desktop Studio starts by itself when a wallpaper, the dock or a widget is on."))
            return
        renderers = status.get("renderers", {})
        failed = [m for m, r in renderers.items() if r["state"] == "failed"]
        paused = [m for m, r in renderers.items() if r.get("paused")]
        if failed:
            self.pill.set(t("A wallpaper stopped after repeated crashes"), "fail")
        elif paused:
            self.pill.set(t("Paused"), "warn")
        else:
            self.pill.set(t("Running"), "ok")
        labels = dict(screens())
        lines = []
        for monitor, r in renderers.items():
            line = f"{labels.get(monitor, monitor)}: {t(STATE_TEXT.get(r['state'], r['state']))}"
            if r.get("reasons"):
                line += " · " + t("paused") + ": " + ", ".join(t(x) for x in r["reasons"])
            if r.get("error"):
                line += " · " + r["error"]
            lines.append(line)
        lines.append(t("Dock: {d} · widgets: {n}", d=t("on") if status.get("dock") else t("off"),
                       n=status.get("widgets", 0)))
        self.details.setText("\n".join(lines))

    def on_show(self) -> None:
        self.reload_cfg()
        self._fill_monitors()
        self.refresh_status()
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)


class DockPage(DesktopPage):
    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        root = QVBoxLayout(self)
        self.header(root, "AKENO Dock", "A launcher bar for your programs, folders and groups, with a dot under "
                    "programs that run. It is an extra bar: the Windows taskbar and Explorer stay as they are.")
        row = ResponsiveRow(spacing=14)
        look = Card(t("Dock"), "dock")
        self.chk_enabled = QCheckBox(t("Show the AKENO Dock"))
        self.chk_enabled.toggled.connect(lambda v: (v and not self._loading and self.switch_on(),
                                                    self._set("enabled", bool(v))))
        look.body.addWidget(self.chk_enabled)
        self.edge = _combo(EDGE_TEXT, t("Screen edge"))
        self.edge.currentIndexChanged.connect(lambda _: self._set("edge", self.edge.currentData()))
        look.body.addLayout(_line(t("Screen edge"), self.edge))
        self.screen = QComboBox()
        self.screen.setAccessibleName(t("Monitor"))
        self.screen.currentIndexChanged.connect(lambda i: self._set("screen", max(0, i)))
        look.body.addLayout(_line(t("Monitor"), self.screen))
        self.chk_float = QCheckBox(t("Floating (drag it anywhere)"))
        self.chk_float.toggled.connect(lambda v: self._set("floating", bool(v)))
        self.chk_hide = QCheckBox(t("Hide automatically (show at the screen edge)"))
        self.chk_hide.toggled.connect(lambda v: self._set("autohide", bool(v)))
        self.chk_blur = QCheckBox(t("Blurred background (Windows 11)"))
        self.chk_blur.toggled.connect(lambda v: self._set("blur", bool(v)))
        for box in (self.chk_float, self.chk_hide, self.chk_blur):
            look.body.addWidget(box)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setAccessibleName(t("Opacity"))
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", int(v)))
        look.body.addLayout(_line(t("Opacity"), self.opacity))
        self.size = QSpinBox()
        self.size.setRange(24, 96)
        self.size.setSuffix(" px")
        self.size.setAccessibleName(t("Icon size"))
        self.size.valueChanged.connect(lambda v: self._set("icon_size", int(v)))
        look.body.addLayout(_line(t("Icon size"), self.size))
        row.add(look, 2)

        items = Card(t("Pinned"), "layers", theme.VIOLET)
        self.list = QListWidget()
        self.list.setAccessibleName(t("Pinned"))
        self.list.setMinimumHeight(240)
        self.list.setSpacing(3)
        self.list.setIconSize(QSize(28, 28))
        items.body.addWidget(EmptyAware(self.list, EmptyState("dock", t("Nothing pinned yet"),
                                                              t("Add programs, folders or groups with the buttons "
                                                                "below.")), 240))
        items.body.addWidget(_row(button(t("Add program…"), "plus", on_click=self._add_program),
                                  button(t("Add folder…"), "folder", on_click=self._add_folder),
                                  button(t("New group…"), "layers", on_click=self._add_group),
                                  button(t("Remove"), "trash", on_click=self._remove),
                                  button(t("Up"), on_click=lambda: self._move(-1)),
                                  button(t("Down"), on_click=lambda: self._move(1))))
        items.body.addWidget(label(t("You can also drop programs and shortcuts on the dock, and right-click it."),
                                   "muted", shrink=True))
        row.add(items, 3)
        root.addWidget(row)
        root.addStretch(1)
        self.load()

    def load(self) -> None:
        self._loading = True
        try:
            d = self.cfg["dock"]
            self.chk_enabled.setChecked(bool(d.get("enabled")))
            self.edge.setCurrentIndex(max(0, self.edge.findData(d.get("edge", "bottom"))))
            self.screen.clear()
            for _, text in screens():
                self.screen.addItem(text)
            self.screen.setCurrentIndex(min(int(d.get("screen", 0)), max(0, self.screen.count() - 1)))
            self.chk_float.setChecked(bool(d.get("floating")))
            self.chk_hide.setChecked(bool(d.get("autohide")))
            self.chk_blur.setChecked(bool(d.get("blur", True)))
            self.opacity.setValue(int(d.get("opacity", 92)))
            self.size.setValue(int(d.get("icon_size", 48)))
            self._fill()
        finally:
            self._loading = False

    def _fill(self, select: int = -1) -> None:
        from PySide6.QtCore import QFileInfo
        from PySide6.QtWidgets import QFileIconProvider
        provider = QFileIconProvider()
        self.list.clear()
        for item in self.cfg["dock"].get("items", []):
            if item.get("type") == "group":
                entry = QListWidgetItem(provider.icon(QFileIconProvider.IconType.Folder),
                                        t("{name} (group of {n})", name=item.get("name", ""),
                                          n=len(item.get("items", []))))
            else:
                entry = QListWidgetItem(provider.icon(QFileInfo(item.get("path", ""))),
                                        item.get("name") or Path(item.get("path", "")).name)
                entry.setToolTip(item.get("path", ""))
            self.list.addItem(entry)
        if select >= 0:
            self.list.setCurrentRow(select)

    def _set(self, key: str, value) -> None:
        if self._loading:
            return
        self.cfg["dock"][key] = value
        self.save()

    def add_items(self, new: list[dict]) -> None:
        self.cfg["dock"].setdefault("items", []).extend(new)
        self.save()
        self._fill(self.list.count() + len(new) - 1)

    def _add_program(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, t("Add a program to the dock"), "",
                                                t("Programs and shortcuts (*.exe *.lnk *.url);;All files (*)"))
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

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        items = self.cfg["dock"].get("items", [])
        new = row + step
        if 0 <= row < len(items) and 0 <= new < len(items):
            items[row], items[new] = items[new], items[row]
            self.save()
            self._fill(new)

    def on_show(self) -> None:
        self.reload_cfg()
        self.load()


class WidgetsPage(DesktopPage):
    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        root = QVBoxLayout(self)
        self.header(root, "Widgets", "Clock, calendar, system load, notes and more on your desktop, below your "
                    "windows. Drag them with the mouse, resize them at the lower right corner, lock them when "
                    "they sit right. Right-click a widget for its options.")
        row = ResponsiveRow(spacing=14)
        lst = Card(t("On the desktop"), "grid")
        self.list = QListWidget()
        self.list.setAccessibleName(t("On the desktop"))
        self.list.setMinimumHeight(240)
        self.list.setSpacing(3)
        self.list.setIconSize(QSize(22, 22))
        self.list.currentRowChanged.connect(self._load_item)
        lst.body.addWidget(EmptyAware(self.list, EmptyState("grid", t("No widgets on the desktop yet"),
                                                            t("Pick a widget below and add it to a monitor.")), 240))
        self.kind = QComboBox()
        self.kind.setAccessibleName(t("Widget"))
        for key in cfgmod.WIDGET_TYPES:
            self.kind.addItem(t(dwidgets.TITLES[key]), key)
        self.add_screen = QComboBox()
        self.add_screen.setAccessibleName(t("Monitor"))
        lst.body.addWidget(_row(self.kind, self.add_screen, button(t("Add widget"), "plus", "primary",
                                                                   on_click=self._add),
                                button(t("Remove"), "trash", on_click=self._remove)))
        self.chk_lock = QCheckBox(t("Lock all widgets (no moving or resizing)"))
        self.chk_lock.toggled.connect(self._lock)
        lst.body.addWidget(self.chk_lock)
        row.add(lst, 3)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(theme.CARD_GAP)
        opt = Card(t("Selected widget"), "sliders")
        self.sel = KeyValueGrid(130)
        opt.body.addWidget(self.sel)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setAccessibleName(t("Opacity"))
        self.opacity.valueChanged.connect(lambda v: self._set_item("opacity", int(v)))
        opt.body.addLayout(_line(t("Opacity"), self.opacity))
        self.place = QLineEdit()
        self.place.setPlaceholderText(t("City, for example Berlin"))
        self.place.setAccessibleName(t("Weather place"))
        self.place.editingFinished.connect(self._set_place)
        self.place_line = QWidget()
        pl = QHBoxLayout(self.place_line)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(label(t("Weather place"), "key"), 1)
        pl.addWidget(self.place, 1)
        opt.body.addWidget(self.place_line)
        self.weather_note = label(t("The weather widget asks open-meteo.com only after you enter a place."),
                                  "muted", shrink=True)
        opt.body.addWidget(self.weather_note)
        side_lay.addWidget(opt)
        prof = Card(t("Desktop profiles"), "layers", theme.VIOLET)
        prof.body.addWidget(label(t("A profile keeps the wallpapers, the dock and the widgets as they are now, for "
                                    "example one for gaming and one for work."), "dim", shrink=True))
        self.profiles = QComboBox()
        self.profiles.setAccessibleName(t("Desktop profiles"))
        prof.body.addWidget(self.profiles)
        prof.body.addWidget(_row(button(t("Save as profile…"), "check", on_click=self._save_profile),
                                 button(t("Use"), "play", on_click=self._apply_profile),
                                 button(t("Delete"), "trash", on_click=self._delete_profile)))
        side_lay.addWidget(prof)
        share = Card(t("Layout file"), "export")
        share.body.addWidget(label(t("Positions, sizes and options as a file. Notes and to-dos stay on this PC."),
                                   "dim", shrink=True))
        share.body.addWidget(_row(button(t("Export…"), "export", on_click=self._export),
                                  button(t("Import…"), "download", on_click=self._import)))
        side_lay.addWidget(share)
        row.add(side, 2)
        root.addWidget(row)
        root.addStretch(1)
        self.load()

    def load(self, select: int = 0) -> None:
        self._loading = True
        try:
            self.chk_lock.setChecked(bool(self.cfg.get("widgets_locked")))
            self.add_screen.clear()
            for _, text in screens():
                self.add_screen.addItem(text)
            self.list.blockSignals(True)
            self.list.clear()
            for w in self.cfg["widgets"]:
                text = f"{t(dwidgets.TITLES[w['type']])}  ·  " + t("monitor {n}", n=int(w.get("screen", 0)) + 1) + \
                    f"  ·  {w.get('x', 0)}, {w.get('y', 0)}  ·  {w.get('w', 0)}×{w.get('h', 0)}"
                self.list.addItem(QListWidgetItem(icons.icon(WIDGET_ICONS.get(w["type"], "grid"), theme.TEXT_DIM, 22),
                                                  text))
            self.list.blockSignals(False)
            self.profiles.clear()
            self.profiles.addItems(sorted(self.cfg.get("profiles", {})))
        finally:
            self._loading = False
        if self.list.count():
            self.list.setCurrentRow(min(select, self.list.count() - 1))
        else:
            self._load_item(-1)

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        return self.cfg["widgets"][row] if 0 <= row < len(self.cfg["widgets"]) else None

    def _load_item(self, _row: int) -> None:
        w = self._current()
        self.opacity.setEnabled(w is not None)
        self.place_line.setVisible(bool(w and w["type"] == "weather"))
        self.weather_note.setVisible(bool(w and w["type"] == "weather"))
        if w is None:
            self.sel.set_rows([(t("Widget"), "—")])
            return
        self._loading = True
        try:
            self.sel.set_rows([(t("Widget"), t(dwidgets.TITLES[w["type"]])),
                               (t("Position"), f"{w.get('x', 0)}, {w.get('y', 0)}"),
                               (t("Size"), f"{w.get('w', 0)} × {w.get('h', 0)}")])
            self.opacity.setValue(int(w.get("opacity", 90)))
            self.place.setText(w.get("options", {}).get("place", ""))
        finally:
            self._loading = False

    def _set_item(self, key: str, value) -> None:
        w = self._current()
        if self._loading or w is None:
            return
        w[key] = value
        self.save()

    def _set_place(self) -> None:
        w = self._current()
        if self._loading or w is None or w["type"] != "weather":
            return
        place = self.place.text().strip()[:80]
        if place != w.setdefault("options", {}).get("place", ""):
            w["options"].update(place=place, lat=None, lon=None)
            self.save()

    def add(self, kind: str, screen: int = 0) -> dict:
        spec = dwidgets.new_widget(kind, self.cfg["widgets"], screen)
        self.cfg["widgets"].append(spec)
        self.switch_on()
        self.save()
        self.load(len(self.cfg["widgets"]) - 1)
        return spec

    def _add(self) -> None:
        self.add(self.kind.currentData(), max(0, self.add_screen.currentIndex()))

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.cfg["widgets"]):
            self.cfg["widgets"].pop(row)
            self.save()
            self.load(row)

    def _lock(self, on: bool) -> None:
        if self._loading:
            return
        self.cfg["widgets_locked"] = bool(on)
        self.save()

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
        self.load(self.list.currentRow())


def preview_png(cfg: dict, monitor: str, name: str) -> bytes:
    """A 640×360 library picture: the first picture of the playlist, or a gradient with the theme name."""
    img = QImage()
    for item in cfgmod.wallpaper(cfg, monitor)["playlist"]:
        if (item.get("kind") or media.kind_of(item["path"])) in ("image", "gif") and Path(item["path"]).is_file():
            img = QImage(item["path"])
            if not img.isNull():
                img = img.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation).copy(0, 0, 640, 360)
                break
    if img.isNull():
        img = QImage(640, 360, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        grad = QLinearGradient(0, 0, 640, 360)
        grad.setColorAt(0, QColor(theme.VIOLET_DEEP))
        grad.setColorAt(1, QColor(theme.BLUE))
        p.fillRect(img.rect(), grad)
        font = p.font()
        font.setPointSize(28)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
        p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


class ThemesPage(DesktopPage):
    def __init__(self, ctl, window):
        super().__init__(ctl, window)
        root = QVBoxLayout(self)
        self.header(root, "Theme Workshop", "Save your desktop (wallpapers with their files, the dock style and the "
                    "widget layout) as one theme file, share it, and import themes from others. Themes contain "
                    "media only: programs, scripts for Windows and links are refused when importing.")
        self.lib = Card(t("Theme library"), "palette", theme.VIOLET,
                        right=button(t("Import theme…"), "download", "primary", on_click=self._import))
        self.grid = QVBoxLayout()
        self.grid.setSpacing(10)
        self.lib.body.addLayout(self.grid)
        root.addWidget(self.lib)

        exp = Card(t("Create a theme from this desktop"), "export")
        self.name = QLineEdit()
        self.name.setAccessibleName(t("Name"))
        self.name.setMaxLength(60)
        self.author = QLineEdit()
        self.author.setAccessibleName(t("Author"))
        self.author.setMaxLength(60)
        self.license = QComboBox()
        self.license.setEditable(True)
        self.license.setAccessibleName(t("License"))
        self.license.addItems(["CC BY 4.0", "CC BY-SA 4.0", "CC BY-NC 4.0", "CC0 1.0", t("All rights reserved")])
        self.description = QPlainTextEdit()
        self.description.setAccessibleName(t("Description"))
        self.description.setMaximumHeight(80)
        self.source = QComboBox()
        self.source.setAccessibleName(t("Wallpapers of"))
        exp.body.addLayout(form_grid([(t("Name"), self.name), (t("Author"), self.author), (t("License"), self.license),
                                      (t("Wallpapers of"), self.source), (t("Description"), self.description)]))
        self.chk_widgets = QCheckBox(t("Include the widget layout"))
        self.chk_widgets.setChecked(True)
        self.chk_dock = QCheckBox(t("Include the dock style (never your pinned programs)"))
        self.chk_dock.setChecked(True)
        exp.body.addWidget(_row(self.chk_widgets, self.chk_dock))
        exp.body.addWidget(label(t("Only share wallpapers you made yourself or whose license allows it."), "muted",
                                 shrink=True))
        exp.body.addWidget(_row(button(t("Export theme…"), "export", "primary", on_click=self._export)))
        root.addWidget(exp)
        root.addStretch(1)
        self.load()

    def load(self) -> None:
        self.source.clear()
        for name, text in screens():
            self.source.addItem(text, name)
        clear_layout(self.grid)
        items = themes.library()
        if not items:
            self.grid.addWidget(label(t("No themes yet. Import a .akenotheme file or create one below."), "dim",
                                      shrink=True))
        for th in items:
            self.grid.addWidget(self._card(th))

    def _card(self, th: themes.Theme) -> QWidget:
        from akeno.ui.widgets import SubCard
        card = SubCard()
        row = QHBoxLayout()
        pic = label()
        pm = QPixmap(str(th.preview)) if th.preview else QPixmap()
        if not pm.isNull():
            pic.setPixmap(pm.scaled(192, 108, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
        else:
            pic.setPixmap(icons.pixmap("palette", theme.TEXT_DIM, 48))
        row.addWidget(pic)
        text = QVBoxLayout()
        text.addWidget(label(th.name, "h2"))
        byline = " · ".join(x for x in (th.author and t("by {a}", a=th.author), th.license,
                                        t("{n} wallpaper(s)", n=th.files)) if x)
        text.addWidget(label(byline, "dim", shrink=True))
        if th.description:
            text.addWidget(label(th.description, "muted", shrink=True))
        text.addWidget(_row(button(t("Use on every monitor"), "check", "primary",
                                   on_click=lambda _=False, x=th: self.use(x)),
                            button(t("Delete"), "trash", on_click=lambda _=False, x=th: self._delete(x))))
        row.addLayout(text, 1)
        card.lay.addLayout(row)
        return card

    def use(self, th: themes.Theme) -> None:
        themes.apply(th, self.cfg, [name for name, _ in screens()])
        self.switch_on()
        self.save()
        self.ctl.status.emit(t("Theme “{n}” is in use.", n=th.name))

    def _delete(self, th: themes.Theme) -> None:
        if confirm(self, t("Theme library"), t("Delete the theme “{n}” and its files from the library?", n=th.name),
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

    def export_to(self, path: Path) -> Path:
        name = self.name.text().strip() or t("My desktop")
        return themes.export(self.cfg, self.source.currentData() or "", path, name, self.author.text(),
                             self.description.toPlainText(), self.license.currentText(),
                             preview_png(self.cfg, self.source.currentData() or "", name),
                             include_widgets=self.chk_widgets.isChecked(), include_dock=self.chk_dock.isChecked())

    def _export(self) -> None:
        name = self.name.text().strip() or t("My desktop")
        path, _ = QFileDialog.getSaveFileName(self, t("Export theme…"), f"{name}{themes.SUFFIX}",
                                              t("AKENO themes (*.akenotheme)"))
        if not path:
            return
        try:
            out = self.export_to(Path(path))
        except (themes.ThemeError, media.MediaError, OSError) as exc:
            info(self, t("Export theme…"), t("The theme was not created."), str(exc), error=True)
            return
        info(self, t("Export theme…"), t("Theme saved: {p}", p=str(out)))

    def on_show(self) -> None:
        self.reload_cfg()
        self.load()


# -- Find wallpapers (akeno/desktop/sources.py) ------------------------------------------------------------------
class VideoTile(QFrame):
    """One Pixabay result: preview, what it shows, length and size, its creator, and the two actions."""

    WIDTH = 236

    def __init__(self, video, quality: str, on_add, on_page):
        super().__init__()
        self.video = video
        self.setObjectName("VideoTile")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"#VideoTile {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
                           f" border-radius: 8px; }} #VideoTile:hover {{ border-color: {theme.BORDER_BTN}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 10)
        lay.setSpacing(4)
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.WIDTH - 16, round((self.WIDTH - 16) * 9 / 16))
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background: {theme.INPUT}; border-radius: 6px;")
        self.thumb.setPixmap(icons.pixmap("film", theme.TEXT_MUTED, 28))
        lay.addWidget(self.thumb)
        title = ElidedLabel(video.title)
        title.setStyleSheet("font-weight: 600;")
        lay.addWidget(title)
        chosen = video.best(quality)
        minutes, seconds = divmod(max(0, video.duration), 60)
        meta = f"{minutes}:{seconds:02d}"
        if chosen:
            meta += f" · {chosen.label}"
            if chosen.bytes:
                meta += f" · {chosen.bytes / 1e6:.0f} MB"
        lay.addWidget(ElidedLabel(meta, "muted"))
        if video.user:
            lay.addWidget(ElidedLabel(t("by {name} on Pixabay", name=video.user), "muted"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_add = button(t("Add"), "plus", "primary", on_click=lambda: on_add(self))
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
        size = self.thumb.size()
        ratio = self.devicePixelRatioF() or 1.0
        pm = QPixmap.fromImage(img.scaled(round(size.width() * ratio), round(size.height() * ratio),
                                          Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation))
        pm = pm.copy((pm.width() - round(size.width() * ratio)) // 2, (pm.height() - round(size.height() * ratio)) // 2,
                     round(size.width() * ratio), round(size.height() * ratio))
        pm.setDevicePixelRatio(ratio)
        self.thumb.setPixmap(pm)


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
                tile.btn_add.setText(t("Added"))
            if isValid(self.page):
                self._added(self.page.add_paths([str(path)]), video.title)

        def fail(exc):
            if isValid(tile):
                tile.btn_add.setEnabled(True)
                tile.btn_add.setText(t("Add"))
            info(self, "Pixabay", exc.text() if isinstance(exc, self.src.SourceError) else str(exc), error=True)
        if not self.ctl.run(f"pixabay-download-{video.id}", work, done, fail, on_progress=progress, silent=True):
            tile.btn_add.setEnabled(True)
            tile.btn_add.setText(t("Add"))

    def _added(self, problems: list[str], name: str) -> None:
        if problems:
            info(self, t("Wallpapers"), t("Some files were not added."), "\n".join(problems), error=True)
        else:
            self.ctl.status.emit(t("Added to the playlist of this monitor: {name}", name=name))

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
            row.addWidget(button(t("Add"), "plus", on_click=lambda _=False, p=path: self._import(p)))
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

        def fail(exc):
            info(self, t("Recent downloads"), exc.text() if isinstance(exc, self.src.SourceError) else str(exc),
                 error=True)
        self.ctl.run("wallpaper-import", lambda: self.src.import_file(path, self.src.library_dir()), done, fail,
                     silent=True)
