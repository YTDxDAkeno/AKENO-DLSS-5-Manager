"""Shared building blocks of the SYSTEM pages (System Doctor, Smart Cleaner, Network Lab, Security Center) and the
Universal Mod Scanner.

* :class:`TabbedPage`: a page title over a tab strip; only the visible tab sits in the layout (like Advanced), so
  the window's minimum size follows that tab and the page host is the only scroll area.
* :class:`TaskBar`: runs one long operation on a worker thread with a real Cancel button, its time limit and
  the progress the operation reports (a bar only when the total is known).
* :class:`FindingView`: a finding with its observation, the evidence grouped by kind (measured, possible cause,
  same time) and buttons that open the module that can act on it.
* :class:`ReadingTile`: a value with its source, or "—" and why it is not available.
* :class:`Sparkline`: recorded history, drawn only from stored samples.
* :func:`show_stored`: shows a result saved by an earlier run without ever keeping AKENO from starting.
"""

from __future__ import annotations

import functools
import logging
import weakref
from typing import Callable, Iterable

import shiboken6

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTabBar,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.system.model import EVIDENCE_LABELS, EvidenceKind, Finding, Level, Reading
from akeno.system.tasks import Cancelled, Progress, TaskContext, TimedOut
from akeno.ui import theme
from akeno.ui.widgets import (Card, FlowLayout, PageHeader, Spinner, StatusPill, button, clear_layout, label,
                              status_badge)

LEVEL_TEXT = {"ok": "OK", "info": "Note", "warn": "Warning", "fail": "Problem", "unknown": "Unknown"}
log = logging.getLogger(__name__)

# Results saved by an earlier run (``system.sqlite3``) are shown when a page is built. One that cannot be shown
# (written by another version, a display error) must never keep AKENO from starting: it stays stored unchanged,
# the tab says so, and the failure is listed here for the update's start check (``health_report``: an update that
# cannot show this PC's saved results is rolled back). After a failed start (``akeno.startguard``) saved results
# are not shown at all until AKENO has started once without them.
SAFE_MODE = {"on": False}
STORED_FAILURES: list[str] = []


def show_stored(where: str, load: Callable[[], object], show: Callable[[object], None],
                task: "TaskBar | None" = None) -> bool:
    """``show(load())`` for a saved result; False (logged, listed, the data untouched) when that fails."""
    if SAFE_MODE["on"]:
        return False
    try:
        value = load()
        if value is not None:
            show(value)
        return True
    except Exception as exc:                    # noqa: BLE001 (a saved result never stops the start)
        log.exception("the saved result %s could not be shown", where)
        STORED_FAILURES.append(f"{where}: {type(exc).__name__}: {exc}"[:300])
        if task is not None:
            task.show_message(t("The last saved result could not be shown. It is kept unchanged; run the check "
                                "again for a new one."), error=True)
        return False


class UnavailablePage(QWidget):
    """Stands in for a SYSTEM page whose construction failed: says so, and the window and every other page work.
    Calls the window makes on the real page (``on_show``, ``link``, …) do nothing here."""

    OPTIONAL = frozenset({"doctor", "cleaner", "network", "security"})

    def __init__(self, key: str, error: BaseException):
        super().__init__()
        log.error("the page %s could not be built", key, exc_info=error)
        STORED_FAILURES.append(f"page {key}: {type(error).__name__}: {error}"[:300])
        self.key = key
        lay = QVBoxLayout(self)
        lay.addWidget(label(t("This page could not be opened."), "h2"))
        lay.addWidget(label(t("The error was written to the log. Your saved results and undo records are kept "
                              "unchanged. Updating AKENO (ACCOUNT → Updates) or restarting it usually helps."),
                            "dim", wrap=True))
        lay.addStretch(1)

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class TabbedPage(QWidget):
    """Title, description, tab strip and the visible tab's widget."""

    def __init__(self, title: str, subtitle: str, actions: Iterable[QWidget] = ()):
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(10)
        self.header = PageHeader(title, subtitle, actions)
        self._lay.addWidget(self.header)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setAccessibleName(title)
        self._lay.addWidget(self.tabs)
        self.area = QVBoxLayout()
        self._lay.addLayout(self.area, 1)
        self.keys: list[str] = []
        self.holders: dict[str, QWidget] = {}
        self.pool = QWidget(self)
        self.pool.hide()
        self.current: str | None = None
        self.tabs.currentChanged.connect(self._shown)

    def add_tab(self, key: str, title: str, widget: QWidget) -> None:
        holder = QWidget(self.pool)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 6, 0, 0)
        hl.setSpacing(14)
        hl.addWidget(widget, 1)
        self.holders[key] = holder
        self.keys.append(key)
        self.tabs.addTab(title.replace("&", "&&"))          # "Ping & stability": no keyboard mnemonic
        if self.current is None:
            self._place(key)

    def _place(self, key: str) -> None:
        if self.current == key:
            return
        if self.current is not None:
            old = self.holders[self.current]
            self.area.removeWidget(old)
            old.setParent(self.pool)
        new = self.holders[key]
        new.setParent(self)
        self.area.addWidget(new, 1)
        new.show()
        self.current = key

    def show_tab(self, key: str) -> None:
        if key in self.keys:
            self.tabs.setCurrentIndex(self.keys.index(key))
            self._shown(self.keys.index(key))

    def show_target(self, target: str) -> None:
        """A link from another module ("startup", "scan:C:\\") opens the matching tab."""
        self.show_tab(target.split(":", 1)[0])

    def _shown(self, index: int) -> None:
        if 0 <= index < len(self.keys):
            key = self.keys[index]
            self._place(key)
            hook = getattr(self, "tab_shown", None)
            if hook:
                hook(key)


class _Relay(QObject):
    """Carries progress from worker threads to the GUI thread.

    One instance for the whole process, created on the GUI thread and never deleted: a worker may still report
    progress while the page that started it is being closed, and emitting on a QObject that another thread is
    destroying at that moment crashes the process (seen as a segfault in the Linux test run). The bar itself is only
    reached through a weak reference and only while its C++ object is alive."""
    progress = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.progress.connect(self._deliver)

    @staticmethod
    def _deliver(ref, value) -> None:
        bar = ref()
        if bar is not None and shiboken6.isValid(bar):
            bar._progress(value)


_RELAY: list[_Relay] = []


def _relay() -> _Relay:
    if not _RELAY:
        _RELAY.append(_Relay())
    return _RELAY[0]


def _report(relay: _Relay, ref, value) -> None:
    relay.progress.emit(ref, value)


class TaskBar(QWidget):
    """One long operation at a time: spinner, what it is doing, a bar when the total is known, Cancel."""

    finished = Signal(bool)                 # True when it completed (not cancelled, no error)

    def __init__(self, ctl, key: str, cancel_text: str | None = None):
        super().__init__()
        self.ctl, self.key = ctl, key
        self.ctx: TaskContext | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.spinner = Spinner(18)
        lay.addWidget(self.spinner)
        self.text = label("", "dim", shrink=True)
        lay.addWidget(self.text, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(180)
        self.bar.setFixedHeight(8)
        self.bar.setAccessibleName(t("Progress"))
        self.bar.hide()
        lay.addWidget(self.bar)
        self.cancel = button(cancel_text or t("Cancel"), "stop", "ghost", on_click=self.stop)
        self.cancel.hide()
        lay.addWidget(self.cancel)
        self._relay = _relay()
        # A closed page cancels its operation; the holder (not ``self``) is captured so this works during deletion.
        self._running: dict[str, TaskContext] = {}
        running = self._running
        self.destroyed.connect(lambda *_: [ctx.cancel() for ctx in running.values()])
        self.setVisible(False)

    @property
    def running(self) -> bool:
        return self.ctx is not None

    def start(self, fn: Callable[[TaskContext], object], on_done: Callable[[object], None] | None = None,
              on_error: Callable[[Exception], None] | None = None, text: str = "", timeout: float | None = None,
              cancellable: bool = True) -> bool:
        if self.ctx is not None:
            return False
        ctx = TaskContext(progress=functools.partial(_report, self._relay, weakref.ref(self)), timeout=timeout)

        def done(res):
            self._running.pop("ctx", None)
            self._stop_ui()
            self.finished.emit(True)
            if on_done:
                on_done(res)

        def failed(exc):
            self._running.pop("ctx", None)
            self._stop_ui()
            self.finished.emit(False)
            if isinstance(exc, Cancelled):
                self.show_message(t("Cancelled."))
            elif isinstance(exc, TimedOut):
                self.show_message(t("Stopped: the time limit was reached."))
            elif on_error:
                on_error(exc)
            else:
                self.show_message(str(exc), error=True)

        if not self.ctl.run(self.key, lambda: fn(ctx), done, failed, silent=True):
            return False
        self.ctx = ctx
        self._running["ctx"] = ctx
        self.text.setStyleSheet("")
        self.text.setText(text or t("Working…"))
        self.spinner.start()
        self.cancel.setVisible(cancellable)
        self.cancel.setEnabled(True)
        self.bar.hide()
        self.setVisible(True)
        return True

    def stop(self) -> None:
        if self.ctx is not None:
            self.ctx.cancel()
            self.cancel.setEnabled(False)
            self.text.setText(t("Cancelling…"))

    def _progress(self, p: Progress) -> None:
        if self.ctx is None or not isinstance(p, Progress):
            return
        self.text.setText(t(p.text))
        if p.fraction is not None:
            self.bar.setValue(int(p.fraction * 1000))
            self.bar.show()
        else:
            self.bar.hide()

    def _stop_ui(self) -> None:
        self.ctx = None
        self.spinner.stop()
        self.cancel.hide()
        self.bar.hide()
        self.setVisible(False)

    def show_message(self, text: str, error: bool = False) -> None:
        self.setVisible(True)
        self.spinner.stop()
        self.cancel.hide()
        self.bar.hide()
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {theme.RED};" if error else "")


class FindingView(QFrame):
    """One finding. Evidence is grouped by kind so a correlation is never read as a measurement."""

    def __init__(self, f: Finding, on_link: Callable[[str, str], None] | None = None, compact: bool = False):
        super().__init__()
        self.setObjectName("SubCard")
        self.finding = f
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel()
        badge.setPixmap(status_badge(f.level.value if f.level != Level.UNKNOWN else "unknown", 20))
        badge.setFixedSize(22, 22)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title = label(t(f.title), "h3", wrap=True)
        head.addWidget(title, 1)
        head.addWidget(StatusPill(t(LEVEL_TEXT.get(f.level.value, "")), f.level.value), 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        if f.observation:
            lay.addWidget(label(t(f.observation), wrap=True))
        if not compact:
            for kind in (EvidenceKind.MEASURED, EvidenceKind.POSSIBLE_CAUSE, EvidenceKind.CORRELATION):
                items = [e for e in f.evidence if e.kind == kind]
                if not items:
                    continue
                lay.addWidget(label(t(EVIDENCE_LABELS[kind]).upper(), "overline"))
                for e in items[:8]:
                    text = "• " + t(e.text) + (f"  ({e.source})" if e.source else "")
                    lb = label(text, "dim" if kind != EvidenceKind.MEASURED else None, wrap=True, selectable=True)
                    if kind == EvidenceKind.CORRELATION:
                        lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-style: italic;")
                    lay.addWidget(lb)
        if f.recommendation:
            rec = label(t("Recommendation: {rec}", rec=t(f.recommendation)), wrap=True)
            rec.setStyleSheet(f"color: {theme.ACCENT_TEXT};")
            lay.addWidget(rec)
        if f.links and on_link:
            row = FlowLayout(spacing=8)
            for link in f.links:
                row.addWidget(button(t(link.label), "chevron-right", "ghost",
                                     on_click=lambda _=False, m=link.module, x=link.target: on_link(m, x)))
            holder = QWidget()
            holder.setLayout(row)
            lay.addWidget(holder)
        self.setAccessibleName(t(f.title))


class FindingList(QWidget):
    """Findings sorted by severity, with an empty text when there are none."""

    def __init__(self, on_link=None, empty: str = ""):
        super().__init__()
        self.on_link = on_link
        self.empty = empty
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)

    def set_findings(self, findings: list[Finding], errors: list | None = None) -> None:
        clear_layout(self.lay)
        for f in sorted(findings, key=lambda x: -x.level.rank):
            self.lay.addWidget(FindingView(f, self.on_link))
        if not findings and self.empty:
            self.lay.addWidget(label(self.empty, "dim", wrap=True))
        if errors:
            box = QFrame()
            box.setObjectName("SubCard")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.addWidget(label(t("NOT CHECKED"), "overline"))
            for e in list(dict.fromkeys(str(x) for x in errors))[:8]:
                bl.addWidget(label("• " + t(e), "muted", wrap=True))
            self.lay.addWidget(box)


class ReadingTile(QWidget):
    """A reading: the value with its unit, its name, and the source (or the reason it is missing) as tooltip."""

    def __init__(self, name: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = label("—", "stat")
        self.name = label(name, "muted")
        self.note = label("", "muted", wrap=True)
        self.note.setStyleSheet("font-size: 8.5pt;")
        self.note.hide()
        lay.addWidget(self.value)
        lay.addWidget(self.name)
        lay.addWidget(self.note)
        self.setAccessibleName(name)

    def set(self, r: Reading | None, text: str | None = None) -> None:
        if r is None or not r.available:
            self.value.setText("—")
            reason = t(r.note) if r is not None and r.note else t("Not available on this PC.")
            self.note.setText(reason)
            self.note.show()
            self.setToolTip(reason)
            return
        self.value.setText(text if text is not None else f"{r.value} {r.unit}".strip())
        self.note.setText(t(r.note) if r.note else "")
        self.note.setVisible(bool(r.note))
        self.setToolTip(t("Source: {s}", s=r.source) if r.source else "")


class Sparkline(QWidget):
    """Recorded values over time (0-100 scale unless ``top`` is given). Draws nothing it was not given."""

    def __init__(self, color: str | None = None, top: float | None = 100.0, height: int = 46):
        super().__init__()
        self.values: list[float | None] = []
        self.color = color or theme.ACCENT
        self.top = top
        self.setFixedHeight(height)
        self.setMinimumWidth(120)

    def set_values(self, values: list[float | None]) -> None:
        self.values = list(values)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        vals = [v for v in self.values if v is not None]
        if len(vals) < 2:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, t("No history yet"))
            return
        top = self.top or max(vals) * 1.1 or 1
        n = len(self.values)
        path = QPainterPath()
        started = False
        for i, v in enumerate(self.values):
            if v is None:
                started = False
                continue
            x = r.left() + r.width() * i / max(1, n - 1)
            y = r.bottom() - r.height() * min(v, top) / top
            if not started:
                path.moveTo(x, y)
                started = True
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor(self.color), 1.6))
        p.drawPath(path)


def make_table(headers: list[str], min_height: int = 160, stretch_last: bool = False) -> QTableWidget:
    tbl = QTableWidget(0, len(headers))
    tbl.setHorizontalHeaderLabels(headers)
    hh = tbl.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    if stretch_last:
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tbl.setWordWrap(False)
    tbl.setMinimumHeight(min_height)
    tbl.setAlternatingRowColors(False)
    return tbl


def fill_table(tbl: QTableWidget, rows: list[list], data: list | None = None, tips: list[list] | None = None) -> None:
    """Rows of text (or (text, state) pairs, which get a state dot); ``data`` is kept on column 0 (UserRole)."""
    tbl.setSortingEnabled(False)
    tbl.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            state = None
            if isinstance(cell, tuple):
                cell, state = cell
            item = QTableWidgetItem(str(cell))
            if state:
                item.setForeground(QColor(theme.STATE_COLORS.get(state, theme.TEXT)))
            if tips and i < len(tips) and j < len(tips[i]) and tips[i][j]:
                item.setToolTip(str(tips[i][j]))
            elif len(str(cell)) > 40:
                item.setToolTip(str(cell))
            if j == 0 and data is not None and i < len(data):
                item.setData(Qt.ItemDataRole.UserRole, data[i])
            tbl.setItem(i, j, item)


def selected_data(tbl: QTableWidget):
    rows = tbl.selectionModel().selectedRows() if tbl.selectionModel() else []
    if not rows:
        return None
    item = tbl.item(rows[0].row(), 0)
    return item.data(Qt.ItemDataRole.UserRole) if item else None


def open_link(window, module: str, target: str = "") -> None:
    """Open another module's page (and its tab) from a finding's link."""
    window.go(module)
    page = getattr(window, "pages", {}).get(module)
    if target and page is not None and hasattr(page, "show_target"):
        page.show_target(target)


def card(title: str, icon: str, *widgets: QWidget, right: QWidget | None = None) -> Card:
    c = Card(title, icon, right=right)
    for w in widgets:
        c.body.addWidget(w)
    return c


def level_pill(level: Level) -> StatusPill:
    return StatusPill(t(LEVEL_TEXT.get(level.value, "")), level.value)


def export_report(parent, store, module: str, extra: dict | None = None) -> str | None:
    """Save the module's latest results as JSON for support. Private details are left out (``system.report``)."""
    import json
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    from akeno.system import report
    path = choose_save_path(parent, t("Export report"), f"akeno-{module}-report.json")
    if not path:
        return None
    data = report.build(store, module, extra)
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def choose_save_path(parent, title: str, name: str) -> str:
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getSaveFileName(parent, title, name, "JSON (*.json)")[0]


def export_button(parent, store, module: str, extra: Callable[[], dict] | None = None):
    """"Export report…": what the report contains is said in the tooltip; the file stays on this PC."""
    def run() -> None:
        path = export_report(parent, store, module, extra() if extra else None)
        if path:
            from akeno.ui.widgets import info
            info(parent, t("Export report"), t("The report was saved. File paths, addresses, user and PC names "
                                               "and serial numbers are left out."), path)
    b = button(t("Export report…"), "export", "ghost", on_click=run)
    b.setToolTip(t("Saves the latest results as a file for support. Nothing is sent; private details are left "
                   "out."))
    return b
