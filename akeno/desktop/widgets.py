"""Desktop widgets: small windows on the desktop, below other programs, that the user drags, resizes, locks,
exports and saves in desktop profiles. Each has a panel that shows one thing (clock, CPU, notes, ...).

Readings come from ``desktop/sensors.py`` (one background thread), the level for the music and visualizer widgets
from the playback meter, the FPS value from the last Benchmark capture. The weather widget is off until the user
enters a place; only then does it ask open-meteo.com (no account, no key) every 30 minutes.
"""

from __future__ import annotations

import calendar
import json
import logging
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFileDialog, QFileIconProvider, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from akeno.desktop import sensors, winapi
from akeno.i18n import t

log = logging.getLogger(__name__)

TITLES = {"clock": "Clock", "calendar": "Calendar", "cpu": "CPU", "gpu": "GPU", "temperature": "Temperature",
          "ram": "RAM", "vram": "VRAM", "network": "Network", "disk": "Drives", "fps": "FPS", "shortcuts": "Shortcuts",
          "music": "Music", "visualizer": "Visualizer", "notes": "Notes", "todo": "To-do", "weather": "Weather",
          "uptime": "Uptime", "downloads": "Downloads", "license": "License"}
SIZES = {"clock": (260, 130), "calendar": (260, 230), "notes": (280, 220), "todo": (280, 260), "disk": (280, 190),
         "shortcuts": (280, 150), "downloads": (300, 220), "visualizer": (300, 140), "weather": (260, 150)}
ACCENT = QColor("#8E42FF")
TEXT = QColor("#F4F6FF")
DIM = QColor("#97A9C7")
WEATHER = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog", 51: "Drizzle",
           53: "Drizzle", 55: "Drizzle", 61: "Rain", 63: "Rain", 65: "Heavy rain", 71: "Snow", 73: "Snow",
           75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Heavy showers", 95: "Thunderstorm",
           96: "Thunderstorm", 99: "Thunderstorm"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_SHORT = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


class Context:
    """What panels share: the sensor thread, the playback meter, where the suite keeps its data."""

    def __init__(self, sampler: sensors.Sampler | None = None, meter=None, db_file: Path | None = None,
                 license_view: Callable | None = None):
        self.sampler = sampler or sensors.Sampler()
        if meter is None:
            from akeno.desktop.audio import AudioMeter
            meter = AudioMeter()
        self.meter = meter
        if db_file is None:
            from akeno.paths import db_path
            db_file = db_path()
        self.db_file = db_file
        self.license_view = license_view


def _label(text: str = "", size: float = 10, bold: bool = False, color: QColor = TEXT) -> QLabel:
    lb = QLabel(text)
    # in the label's own style sheet, so an application style sheet cannot shrink the big values
    lb.setStyleSheet(f"color: {color.name()}; background: transparent; font-size: {size}pt; "
                     f"font-weight: {700 if bold else 400};")
    return lb


class Panel(QWidget):
    interval = 2000                 # refresh in ms (0: only on demand)
    needs: tuple[str, ...] = ()     # sensor groups (gpu, disk, temperature)

    def __init__(self, options: dict, ctx: Context, save: Callable[[], None]):
        super().__init__()
        self.options = options
        self.ctx = ctx
        self.save = save
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        ctx.sampler.need(*self.needs)

    def refresh(self) -> None:
        pass

    def menu(self, menu: QMenu) -> None:
        """Options of this widget in its right-click menu."""


class ClockPanel(Panel):
    interval = 1000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.time = _label("", 30, True)
        self.date = _label("", 10, color=DIM)
        lay.addWidget(self.time)
        lay.addWidget(self.date)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        now = datetime.now()
        fmt = ("%H:%M" if self.options.get("h24", True) else "%I:%M %p") if not self.options.get("seconds") else \
            ("%H:%M:%S" if self.options.get("h24", True) else "%I:%M:%S %p")
        self.time.setText(now.strftime(fmt))
        self.date.setText(t(WEEKDAYS[now.weekday()]) + now.strftime(", %d.%m.%Y"))

    def menu(self, menu: QMenu) -> None:
        for key, text, default in (("h24", "24-hour clock", True), ("seconds", "Show seconds", False)):
            a = menu.addAction(t(text))
            a.setCheckable(True)
            a.setChecked(bool(self.options.get(key, default)))
            a.toggled.connect(lambda on, k=key: (self.options.__setitem__(k, on), self.save(), self.refresh()))


class CalendarPanel(Panel):
    interval = 60_000

    def __init__(self, *a):
        super().__init__(*a)
        self.offset = 0
        self.setMinimumSize(200, 170)

    def wheelEvent(self, e):
        self.offset += -1 if e.angleDelta().y() > 0 else 1
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.offset = 0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        today = datetime.now().date()
        month = today.month - 1 + self.offset
        year, month = today.year + month // 12, month % 12 + 1
        head = QFont(self.font())
        head.setBold(True)
        p.setFont(head)
        p.setPen(TEXT)
        p.drawText(QRect(0, 0, self.width(), 22), Qt.AlignmentFlag.AlignLeft,
                   f"{t(MONTHS[month - 1])} {year}")
        cell_w, cell_h = self.width() / 7, (self.height() - 44) / 6
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, min(10.0, cell_h / 2.2)))
        p.setFont(small)
        for i, name in enumerate(DAY_SHORT):
            p.setPen(DIM)
            p.drawText(QRect(int(i * cell_w), 24, int(cell_w), 18), Qt.AlignmentFlag.AlignCenter, t(name))
        for row, week in enumerate(calendar.Calendar().monthdayscalendar(year, month)):
            for col, day in enumerate(week):
                if not day:
                    continue
                r = QRect(int(col * cell_w), int(44 + row * cell_h), int(cell_w), int(cell_h))
                if (year, month, day) == (today.year, today.month, today.day):
                    p.setBrush(ACCENT)
                    p.setPen(Qt.PenStyle.NoPen)
                    side = int(min(cell_w, cell_h)) - 2
                    p.drawEllipse(QRect(r.center().x() - side // 2, r.center().y() - side // 2, side, side))
                p.setPen(TEXT if col < 5 else DIM)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(day))


class Spark(QWidget):
    """A small history line of the last values (0..100)."""

    def __init__(self, length: int = 60, color: QColor = ACCENT):
        super().__init__()
        self.values: list[float] = []
        self.length = length
        self.color = color
        self.setMinimumHeight(28)

    def push(self, value: float | None) -> None:
        if value is None:
            return
        self.values = (self.values + [max(0.0, min(100.0, float(value)))])[-self.length:]
        self.update()

    def paintEvent(self, e):
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        step = w / (self.length - 1)
        path = QPainterPath()
        start = self.length - len(self.values)
        for i, v in enumerate(self.values):
            x, y = (start + i) * step, h - 2 - (h - 4) * v / 100
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(self.color, 2))
        p.drawPath(path)


class MetricPanel(Panel):
    """One big value with a history line: CPU, GPU, RAM, VRAM, temperature."""
    kind = "cpu"

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = _label("—", 24, True)
        self.sub = _label("", 9, color=DIM)
        self.sub.setWordWrap(True)
        self.spark = Spark()
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addWidget(self.spark, 1)
        self.refresh()

    def _gpu(self) -> dict | None:
        gpus = self.ctx.sampler.get("gpus") or []
        index = int(self.options.get("gpu", 0))
        return gpus[index] if 0 <= index < len(gpus) else None

    def reading(self) -> tuple[float | None, str, str]:
        s = self.ctx.sampler
        if self.kind == "cpu":
            ghz = s.get("cpu_ghz")
            return s.get("cpu"), f"{s.get('cpu', 0):.0f} %", t("{n} threads", n=s.get("cores", 0)) + \
                (f" · {ghz:.2f} GHz" if ghz else "")
        if self.kind == "ram":
            return s.get("ram_percent"), f"{s.get('ram_percent', 0):.0f} %", \
                f"{s.get('ram_used_gb', 0):.1f} / {s.get('ram_total_gb', 0):.1f} GB"
        gpu = self._gpu()
        if self.kind in ("gpu", "vram") and gpu is None:
            return None, "—", t("No NVIDIA graphics card reports this value.")
        if self.kind == "gpu":
            util = gpu["util"]
            return util, "—" if util is None else f"{util:.0f} %", gpu["name"]
        if self.kind == "vram":
            used, total = gpu["vram_used_mib"], gpu["vram_total_mib"]
            if used is None or not total:
                return None, "—", gpu["name"]
            return used / total * 100, f"{used / 1024:.1f} GB", t("of {total:.0f} GB", total=total / 1024)
        cpu_temp = s.get("cpu_temp_c")
        gpu_temp = gpu["temp_c"] if gpu else None
        parts = []
        if gpu_temp is not None:
            parts.append(t("GPU {c:.0f} °C", c=gpu_temp))
        if cpu_temp is not None:
            parts.append(t("CPU {c:.0f} °C", c=cpu_temp))
        if not parts:
            return None, "—", t("Windows reports no temperature here without a vendor tool.")
        main = gpu_temp if gpu_temp is not None else cpu_temp
        return main, f"{main:.0f} °C", " · ".join(parts)

    def refresh(self) -> None:
        value, big, sub = self.reading()
        self.value.setText(big)
        self.sub.setText(sub)
        self.spark.push(value)


class NetworkPanel(Panel):
    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.down = _label("↓ —", 14, True)
        self.up = _label("↑ —", 11, color=DIM)
        self.spark = Spark(color=QColor("#12C7FF"))
        self.peak = 1.0
        lay.addWidget(self.down)
        lay.addWidget(self.up)
        lay.addWidget(self.spark, 1)
        self.refresh()

    def refresh(self) -> None:
        down, up = self.ctx.sampler.get("net_down"), self.ctx.sampler.get("net_up")
        self.down.setText("↓ " + sensors.rate(down))
        self.up.setText("↑ " + sensors.rate(up))
        if down is not None:
            self.peak = max(self.peak * 0.98, down, 1.0)
            self.spark.push(down / self.peak * 100)


class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(6)

    def set(self, value: float) -> None:
        self.value = max(0.0, min(100.0, value))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(self.rect(), 3, 3)
        p.setBrush(QColor("#FF5B73") if self.value >= 90 else ACCENT)
        p.drawRoundedRect(QRect(0, 0, int(self.width() * self.value / 100), self.height()), 3, 3)


class DiskPanel(Panel):
    interval = 5000
    needs = ("disk",)

    def __init__(self, *a):
        super().__init__(*a)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)
        self.rows: dict[str, tuple[QLabel, Bar]] = {}
        self.io = _label("", 9, color=DIM)
        self.lay.addWidget(self.io)
        self.refresh()

    def refresh(self) -> None:
        s = self.ctx.sampler
        for drive in s.get("drives") or []:
            if drive["name"] not in self.rows:
                text, bar = _label("", 9), Bar()
                self.lay.insertWidget(self.lay.count() - 1, text)
                self.lay.insertWidget(self.lay.count() - 1, bar)
                self.rows[drive["name"]] = (text, bar)
            text, bar = self.rows[drive["name"]]
            text.setText(t("{d}  {free:.0f} GB free of {total:.0f} GB", d=drive["name"], free=drive["free_gb"],
                           total=drive["total_gb"]))
            bar.set(drive["percent"])
        self.io.setText(t("Read {r} · write {w}", r=sensors.rate(s.get("disk_read")),
                          w=sensors.rate(s.get("disk_write"))))


class FpsPanel(Panel):
    interval = 30_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.value = _label("—", 24, True)
        self.sub = _label("", 9, color=DIM)
        self.sub.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        last = sensors.last_benchmark(Path(self.ctx.db_file))
        if not last or last.get("avg_fps") is None:
            self.value.setText("—")
            self.sub.setText(t("No Benchmark capture yet. FPS values come only from measurements."))
            return
        self.value.setText(f"{last['avg_fps']:.0f} FPS")
        low = last.get("low_1pct_fps")
        self.sub.setText(t("Last measured: {game}", game=last["game"]) +
                         (" · " + t("1 % low {v:.0f}", v=low) if low is not None else "") +
                         f"\n{str(last.get('created_at', ''))[:16].replace('T', ' ')}")


class ShortcutsPanel(Panel):
    interval = 0

    def __init__(self, *a):
        super().__init__(*a)
        self.setAcceptDrops(True)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.rebuild()

    def rebuild(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = self.options.setdefault("items", [])
        if not items:
            hint = _label(t("Drop programs, files or folders here."), 9, color=DIM)
            hint.setWordWrap(True)
            self.grid.addWidget(hint, 0, 0)
            return
        provider = QFileIconProvider()
        from PySide6.QtCore import QFileInfo
        from akeno.desktop.dock import launch
        for i, item in enumerate(items):
            b = QToolButton()
            b.setAutoRaise(True)
            b.setIcon(provider.icon(QFileInfo(item["path"])))
            b.setIconSize(QSize(32, 32))
            b.setToolTip(item.get("name") or Path(item["path"]).name)
            b.setAccessibleName(b.toolTip())
            b.setStyleSheet("QToolButton { border: none; border-radius: 8px; } "
                            "QToolButton:hover { background: rgba(255,255,255,0.12); }")
            b.clicked.connect(lambda _=False, p=item["path"]: launch(p))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, i=i: self._remove(i))
            self.grid.addWidget(b, i // 5, i % 5)

    def add(self, path: str) -> None:
        self.options.setdefault("items", []).append({"path": path, "name": Path(path).stem})
        self.save()
        self.rebuild()

    def _remove(self, index: int) -> None:
        menu = QMenu(self)
        menu.addAction(t("Remove shortcut"), lambda: (self.options["items"].pop(index), self.save(), self.rebuild()))
        menu.exec(QCursor.pos())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.add(url.toLocalFile())

    def menu(self, menu: QMenu) -> None:
        def choose():
            path, _ = QFileDialog.getOpenFileName(self, t("Add shortcut"))
            if path:
                self.add(path)
        menu.addAction(t("Add shortcut…"), choose)


class LevelBars(QWidget):
    def __init__(self, meter, bars: int = 48):
        super().__init__()
        self.meter = meter
        self.bars = bars
        self.values: list[float] = []

    def tick(self) -> bool:
        level = self.meter.peak()
        self.values = self.meter.values()[-self.bars:] if level is not None else []
        self.update()
        return level is not None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.values:
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       t("No playback device reports a level."))
            return
        w = self.width() / len(self.values)
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self.values):
            h = max(2, int(v * (self.height() - 2)))
            color = QColor(ACCENT)
            color.setAlpha(120 + int(135 * v))
            p.setBrush(color)
            p.drawRoundedRect(QRect(int(i * w) + 1, self.height() - h, max(1, int(w) - 2), h), 2, 2)


class VisualizerPanel(Panel):
    interval = 40

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bars = LevelBars(self.ctx.meter)
        lay.addWidget(self.bars, 1)

    def refresh(self) -> None:
        self.bars.tick()


class MusicPanel(Panel):
    interval = 60

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for name, text, tip in (("previous", "⏮", "Previous track"), ("play_pause", "⏯", "Play / pause"),
                                ("next", "⏭", "Next track")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(t(tip))
            b.setAccessibleName(t(tip))
            b.setStyleSheet("QToolButton { color: white; font-size: 18pt; border: none; background: transparent; }"
                            "QToolButton:hover { color: #CBBEFF; }")
            b.clicked.connect(lambda _=False, n=name: winapi.media_key(n))
            row.addWidget(b)
        lay.addLayout(row)
        self.bars = LevelBars(self.ctx.meter, 32)
        lay.addWidget(self.bars, 1)
        hint = _label(t("Controls whichever player is active (media keys)."), 8, color=DIM)
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def refresh(self) -> None:
        self.bars.tick()


class NotesPanel(Panel):
    interval = 0
    LIMIT = 20_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QPlainTextEdit(self.options.get("text", ""))
        self.edit.setPlaceholderText(t("Notes…"))
        self.edit.setAccessibleName(t("Notes"))
        self.edit.setStyleSheet("QPlainTextEdit { background: transparent; color: #F4F6FF; border: none; }")
        lay.addWidget(self.edit)
        self.later = QTimer(self)
        self.later.setSingleShot(True)
        self.later.setInterval(800)
        self.later.timeout.connect(self._store)
        self.edit.textChanged.connect(self.later.start)

    def _store(self) -> None:
        self.options["text"] = self.edit.toPlainText()[:self.LIMIT]
        self.save()


class TodoPanel(Panel):
    interval = 0

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAccessibleName(t("To-do"))
        self.list.setStyleSheet("QListWidget { background: transparent; color: #F4F6FF; border: none; }")
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(t("New task, Enter to add"))
        self.entry.setAccessibleName(t("New task"))
        self.entry.setStyleSheet("QLineEdit { background: rgba(255,255,255,0.06); color: #F4F6FF; "
                                 "border: 1px solid #263A5A; border-radius: 6px; padding: 3px 6px; }")
        self.entry.returnPressed.connect(self._add)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.entry)
        self.list.itemChanged.connect(self._store)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._item_menu)
        self.load()

    def load(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for entry in self.options.get("items", []):
            item = QListWidgetItem(entry.get("text", ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entry.get("done") else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _add(self) -> None:
        text = self.entry.text().strip()[:200]
        if text:
            self.options.setdefault("items", []).append({"text": text, "done": False})
            self.entry.clear()
            self.save()
            self.load()

    def _store(self, *_):
        self.options["items"] = [{"text": self.list.item(i).text(),
                                  "done": self.list.item(i).checkState() == Qt.CheckState.Checked}
                                 for i in range(self.list.count())]
        self.save()

    def _item_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(t("Delete task"), lambda: (self.list.takeItem(self.list.row(item)), self._store()))
        menu.addAction(t("Delete finished tasks"), self._clear_done)
        menu.exec(QCursor.pos())

    def _clear_done(self) -> None:
        self.options["items"] = [e for e in self.options.get("items", []) if not e.get("done")]
        self.save()
        self.load()


class _Fetched(QObject):
    done = Signal(dict)


def fetch_weather(place: str, lat: float | None = None, lon: float | None = None, timeout: float = 10) -> dict:
    """Current weather from open-meteo.com: the place is looked up once, then only its coordinates are sent."""
    if lat is None or lon is None:
        query = urllib.parse.urlencode({"name": place, "count": 1, "format": "json"})
        with urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{query}",
                                    timeout=timeout) as r:
            found = (json.loads(r.read(200_000)).get("results") or [])
        if not found:
            return {"error": "not_found"}
        lat, lon, place = found[0]["latitude"], found[0]["longitude"], found[0].get("name", place)
    query = urllib.parse.urlencode({"latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
                                    "current": "temperature_2m,weather_code,wind_speed_10m"})
    with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=timeout) as r:
        current = json.loads(r.read(200_000)).get("current") or {}
    return {"place": place, "lat": lat, "lon": lon, "temp_c": current.get("temperature_2m"),
            "code": current.get("weather_code"), "wind_kmh": current.get("wind_speed_10m")}


class WeatherPanel(Panel):
    interval = 30 * 60_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.value = _label("—", 24, True)
        self.sub = _label("", 9, color=DIM)
        self.sub.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addStretch(1)
        self.result = _Fetched()
        self.result.done.connect(self._show)
        self._busy = False
        self.refresh()

    def refresh(self) -> None:
        place = (self.options.get("place") or "").strip()
        if not place:
            self.sub.setText(t("Right-click and choose a place to show its weather (from open-meteo.com)."))
            return
        if self._busy:
            return
        self._busy = True
        lat, lon = self.options.get("lat"), self.options.get("lon")

        def work():
            try:
                data = fetch_weather(place, lat, lon)
            except (OSError, ValueError) as exc:
                data = {"error": str(exc)}
            self.result.done.emit(data)
        threading.Thread(target=work, name="akeno-weather", daemon=True).start()

    def _show(self, data: dict) -> None:
        self._busy = False
        if data.get("error"):
            self.sub.setText(t("No weather for “{p}”: place not found.", p=self.options.get("place"))
                             if data["error"] == "not_found" else t("Weather not reachable right now."))
            return
        if self.options.get("lat") is None:
            self.options.update(lat=data["lat"], lon=data["lon"], place=data["place"])
            self.save()
        temp = data.get("temp_c")
        self.value.setText("—" if temp is None else f"{temp:.0f} °C")
        self.sub.setText(f"{data.get('place')} · {t(WEATHER.get(data.get('code'), 'Weather'))}" +
                         (f" · {data['wind_kmh']:.0f} km/h" if data.get("wind_kmh") is not None else "") +
                         "\n" + t("Weather data: Open-Meteo.com (CC BY 4.0)"))

    def menu(self, menu: QMenu) -> None:
        def choose():
            place, ok = QInputDialog.getText(self, t("Weather"), t("Place (city name)"),
                                             text=self.options.get("place", ""))
            if ok:
                self.options.update(place=place.strip()[:80], lat=None, lon=None)
                self.save()
                self.refresh()
        menu.addAction(t("Choose place…"), choose)


class UptimePanel(Panel):
    interval = 30_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.value = _label("", 18, True)
        self.sub = _label("", 9, color=DIM)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        up = self.ctx.sampler.get("uptime_s", 0.0)
        self.value.setText(sensors.duration(up))
        since = datetime.fromtimestamp(self.ctx.sampler.get("time", 0) - up)
        self.sub.setText(t("Running since {d}", d=since.strftime("%d.%m.%Y %H:%M")))


class DownloadsPanel(Panel):
    interval = 10_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAccessibleName(t("Downloads"))
        self.list.setStyleSheet("QListWidget { background: transparent; color: #F4F6FF; border: none; }")
        self.list.itemActivated.connect(lambda item: QDesktopServices.openUrl(
            QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))))
        lay.addWidget(self.list)
        self.folder = Path(self.options.get("folder") or sensors.downloads_dir())
        self.refresh()

    def refresh(self) -> None:
        files = sensors.recent_files(self.folder)
        names = [str(p) for p in files]
        if names == [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]:
            return
        self.list.clear()
        provider = QFileIconProvider()
        from PySide6.QtCore import QFileInfo
        for p in files:
            item = QListWidgetItem(provider.icon(QFileInfo(str(p))), p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.list.addItem(item)
        if not files:
            self.list.addItem(t("No downloads in {f}", f=str(self.folder)))

    def menu(self, menu: QMenu) -> None:
        menu.addAction(t("Open the folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))


class LicensePanel(Panel):
    interval = 60_000

    def __init__(self, *a):
        super().__init__(*a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.value = _label("", 14, True)
        self.sub = _label("", 9, color=DIM)
        self.sub.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        if self.ctx.license_view is None:
            self.value.setText("—")
            return
        view = self.ctx.license_view()
        self.value.setText(t(view.type_name) if view.unlocked else t("Locked"))
        if view.lifetime:
            self.sub.setText(t("Lifetime license, verified on this PC."))
        elif view.remaining is not None:
            self.sub.setText(t("{d} left", d=sensors.duration(view.remaining)))
        else:
            self.sub.setText(t(view.message))


PANELS: dict[str, Callable] = {
    "clock": ClockPanel, "calendar": CalendarPanel, "network": NetworkPanel, "disk": DiskPanel, "fps": FpsPanel,
    "shortcuts": ShortcutsPanel, "music": MusicPanel, "visualizer": VisualizerPanel, "notes": NotesPanel,
    "todo": TodoPanel, "weather": WeatherPanel, "uptime": UptimePanel, "downloads": DownloadsPanel,
    "license": LicensePanel,
    **{k: type(f"{k.title()}Panel", (MetricPanel,), {"kind": k, "needs": needs}) for k, needs in (
        ("cpu", ()), ("gpu", ("gpu",)), ("ram", ()), ("vram", ("gpu",)), ("temperature", ("gpu", "temperature")))},
}


def new_widget(kind: str, existing: list[dict], screen: int = 0) -> dict:
    """A new widget of this kind, placed below the ones already on that screen."""
    w, h = SIZES.get(kind, (240, 130))
    taken = [x for x in existing if x.get("screen", 0) == screen]
    y = 40 + sum(x.get("h", 130) + 16 for x in taken if x.get("x", 40) < 340) if taken else 40
    ids = {x.get("id") for x in existing}
    n = 1
    while f"w{n}" in ids:
        n += 1
    return {"id": f"w{n}", "type": kind, "x": 40, "y": min(y, 900), "w": w, "h": h, "screen": screen,
            "opacity": 90, "options": {}}


class DesktopWidget(QWidget):
    GRIP = 14

    def __init__(self, spec: dict, ctx: Context, locked: Callable[[], bool], changed: Callable[[dict], None],
                 removed: Callable[[str], None], toggle_lock: Callable[[], None]):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnBottomHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.spec = spec
        self.locked = locked
        self.changed = changed
        self.removed = removed
        self.toggle_lock = toggle_lock
        self.setWindowTitle(f"AKENO {t(TITLES[spec['type']])}")
        self.setMinimumSize(140, 80)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        self.title = _label(t(TITLES[spec["type"]]).upper(), 8, True, DIM)
        lay.addWidget(self.title)
        self.panel: Panel = PANELS[spec["type"]](spec.setdefault("options", {}), ctx, self._save)
        lay.addWidget(self.panel, 1)
        self.timer = QTimer(self)
        if self.panel.interval:
            self.timer.setInterval(self.panel.interval)
            self.timer.timeout.connect(self.panel.refresh)
            self.timer.start()
        self._drag: QPoint | None = None
        self._resize: QPoint | None = None
        self.setWindowOpacity(max(0.3, min(1.0, int(spec.get("opacity", 90)) / 100)))
        self.place()

    def place(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        index = int(self.spec.get("screen", 0))
        area = (screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()).availableGeometry()
        w, h = int(self.spec.get("w", 240)), int(self.spec.get("h", 130))
        x = area.x() + max(0, min(int(self.spec.get("x", 40)), area.width() - 60))
        y = area.y() + max(0, min(int(self.spec.get("y", 40)), area.height() - 40))
        self.setGeometry(x, y, w, h)

    def set_paused(self, paused: bool) -> None:
        if not self.panel.interval:
            return
        if paused:
            self.timer.stop()
        else:
            self.timer.start()
            self.panel.refresh()

    def _save(self) -> None:
        self.changed(self.spec)

    def _store_geometry(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.spec.update(screen=QGuiApplication.screens().index(screen), x=self.x() - area.x(),
                         y=self.y() - area.y(), w=self.width(), h=self.height())
        self._save()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        p.fillPath(path, QColor(10, 18, 34, 200))
        p.setPen(QColor(38, 58, 90, 220))
        p.drawPath(path)
        if not self.locked():
            p.setPen(QPen(DIM, 1.5))
            for i in (4, 8):
                p.drawLine(self.width() - i - 3, self.height() - 4, self.width() - 4, self.height() - i - 3)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.locked():
            return
        pos = e.position().toPoint()
        if pos.x() >= self.width() - self.GRIP and pos.y() >= self.height() - self.GRIP:
            self._resize = e.globalPosition().toPoint() - QPoint(self.width(), self.height())
        else:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        g = e.globalPosition().toPoint()
        if self._drag is not None:
            self.move(g - self._drag)
        elif self._resize is not None:
            size = g - self._resize
            self.resize(max(self.minimumWidth(), size.x()), max(self.minimumHeight(), size.y()))
        elif not self.locked():
            pos = e.position().toPoint()
            corner = pos.x() >= self.width() - self.GRIP and pos.y() >= self.height() - self.GRIP
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag is not None or self._resize is not None:
            self._drag = self._resize = None
            self._store_geometry()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        self.panel.menu(menu)
        if not menu.isEmpty():
            menu.addSeparator()
        opacity = menu.addMenu(t("Transparency"))
        for value in (100, 90, 75, 60, 45):
            a = opacity.addAction(f"{value} %")
            a.setCheckable(True)
            a.setChecked(int(self.spec.get("opacity", 90)) == value)
            a.triggered.connect(lambda _=False, v=value: self._opacity(v))
        menu.addAction(t("Unlock widgets") if self.locked() else t("Lock widgets"), self.toggle_lock)
        menu.addAction(t("Remove widget"), lambda: self.removed(self.spec["id"]))
        menu.exec(e.globalPos())

    def _opacity(self, value: int) -> None:
        self.spec["opacity"] = value
        self.setWindowOpacity(value / 100)
        self._save()


class WidgetManager(QObject):
    """The widget windows of the current configuration. ``on_change(widgets, locked)`` saves the configuration."""

    def __init__(self, ctx: Context, on_change: Callable[[list[dict], bool], None]):
        super().__init__()
        self.ctx = ctx
        self.on_change = on_change
        self.windows: dict[str, DesktopWidget] = {}
        self.specs: list[dict] = []
        self.locked = False
        self.paused = False

    def sync(self, widgets: list[dict], locked: bool) -> None:
        self.locked = bool(locked)
        self.specs = [dict(w) for w in widgets if w.get("type") in PANELS and w.get("id")]
        wanted = {w["id"]: w for w in self.specs}
        for wid in list(self.windows):
            old = self.windows[wid]
            if wid not in wanted or wanted[wid]["type"] != old.spec["type"] or \
                    wanted[wid].get("options") != old.spec.get("options"):
                old.close()
                old.deleteLater()
                del self.windows[wid]
        for spec in self.specs:
            win = self.windows.get(spec["id"])
            if win is None:
                try:
                    win = DesktopWidget(spec, self.ctx, lambda: self.locked, self._changed, self._removed,
                                        self._toggle_lock)
                except Exception as exc:          # one broken widget must not stop the others
                    log.warning("widget %s: %s", spec.get("type"), exc)
                    continue
                self.windows[spec["id"]] = win
                win.show()
            else:
                win.spec.update({k: v for k, v in spec.items() if k != "options"})
                win.place()
                win.setWindowOpacity(max(0.3, min(1.0, int(spec.get("opacity", 90)) / 100)))
                win.update()
            win.set_paused(self.paused)
        if self.windows:
            self.ctx.sampler.start()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.ctx.sampler.paused = paused
        for win in self.windows.values():
            win.set_paused(paused)

    def _emit(self) -> None:
        self.on_change([dict(w.spec) for w in self.windows.values()], self.locked)

    def _changed(self, spec: dict) -> None:
        self._emit()

    def _removed(self, wid: str) -> None:
        win = self.windows.pop(wid, None)
        if win is not None:
            win.close()
            win.deleteLater()
        self._emit()

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        for win in self.windows.values():
            win.update()
        self._emit()

    def close(self) -> None:
        for win in self.windows.values():
            win.close()
            win.deleteLater()
        self.windows.clear()
        self.ctx.sampler.stop()


def export_layout(widgets: list[dict], file: Path) -> None:
    """Widgets (positions, sizes, options) as a JSON file. Notes and to-dos are personal and not included."""
    clean = []
    for w in widgets:
        w = json.loads(json.dumps(w))
        if w.get("type") in ("notes", "todo"):
            w["options"] = {}
        clean.append(w)
    Path(file).write_text(json.dumps({"format": "akeno-widgets", "version": 1, "widgets": clean}, indent=1),
                          encoding="utf-8")


def import_layout(file: Path, limit: int = 60) -> list[dict]:
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != "akeno-widgets":
        raise ValueError("not an AKENO widget layout")
    return clean_layout(data.get("widgets", []), limit)


def clean_layout(widgets, limit: int = 60) -> list[dict]:
    """Widgets from a file, a theme or a community package: known types, sane numbers, plain options."""
    out = []
    for w in (widgets if isinstance(widgets, list) else [])[:limit]:
        if not isinstance(w, dict) or w.get("type") not in PANELS:
            continue
        spec = new_widget(w["type"], out, int(w.get("screen", 0)) if str(w.get("screen", 0)).isdigit() else 0)
        for key in ("x", "y", "w", "h", "opacity"):
            if isinstance(w.get(key), int):
                spec[key] = max(0, min(w[key], 10_000))
        options = w.get("options") if isinstance(w.get("options"), dict) else {}
        # only plain values survive: a layout never carries anything that runs
        spec["options"] = {k: v for k, v in options.items() if isinstance(k, str) and
                           isinstance(v, (str, int, float, bool, list, type(None)))}
        out.append(spec)
    return out
