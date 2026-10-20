"""Reusable AKENO widgets."""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget, QMessageBox)

from akeno.i18n import t
from akeno.ui import icons, theme


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def button(text: str, icon_name: str | None = None, kind: str | None = None, tip: str | None = None,
           on_click: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#ffffff" if kind == "primary" else theme.TEXT, 18))
        b.setIconSize(QSize(18, 18))
    if kind:
        b.setProperty("kind", kind)
    if tip:
        b.setToolTip(tip)
    b.setAccessibleName(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


class Card(QFrame):
    """Rounded panel with an icon + title header and an optional widget on the right."""

    def __init__(self, title: str = "", icon_name: str | None = None, icon_color: str = theme.BLUE,
                 right: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAccessibleName(title)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(18, 12, 18, 14)
        self.outer.setSpacing(8)
        if title:
            head = QHBoxLayout()
            head.setSpacing(10)
            if icon_name:
                ic = QLabel()
                ic.setPixmap(icons.pixmap(icon_name, icon_color, 24))
                head.addWidget(ic)
            self.title_label = label(title, "h2")
            head.addWidget(self.title_label)
            head.addStretch(1)
            if right is not None:
                head.addWidget(right)
            self.outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.outer.addLayout(self.body)
        self.outer.addStretch(1)


class SubCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SubCard")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 12)
        self.lay.setSpacing(6)


class StatusPill(QLabel):
    def __init__(self, text: str = "", state: str = "unknown"):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set(text, state)

    def set(self, text: str, state: str) -> None:
        color = theme.STATE_COLORS.get(state, theme.TEXT_DIM)
        self.setText(f"<span style='color:{color}'>●</span>&nbsp;{text}")
        self.setStyleSheet(f"QLabel {{ background: rgba(255,255,255,0.04); border: 1px solid {theme.BORDER_HI};"
                           f" border-radius: 11px; padding: 3px 10px; }}")
        self.setAccessibleName(text)


class CheckRow(QWidget):
    """Icon + title + detail line used in the verification panels."""

    ICON = {"ok": ("check", theme.GREEN), "warn": ("warn", theme.AMBER), "fail": ("x", theme.RED),
            "pending": ("clock", theme.TEXT_MUTED), "unknown": ("info", theme.TEXT_MUTED)}

    def __init__(self, state: str, title: str, detail: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        name, color = self.ICON.get(state, self.ICON["unknown"])
        ic = QLabel()
        ic.setPixmap(icons.pixmap(name, color, 22))
        ic.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        tl = label(title)
        tl.setStyleSheet(f"font-weight:600; color:{theme.AMBER if state == 'warn' and 'community' in title.lower() else theme.TEXT}")
        col.addWidget(tl)
        if detail:
            col.addWidget(label(detail, "dim", wrap=True))
        lay.addLayout(col, 1)
        self.setAccessibleName(f"{title}: {detail}")


class Meter(QWidget):
    """Thin gradient bar. value=None draws an empty dashed track (no data)."""

    def __init__(self, value: float | None = None, colors: tuple[str, str] = (theme.BLUE, theme.VIOLET),
                 height: int = 8):
        super().__init__()
        self.value = value
        self.colors = colors
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: float | None, colors: tuple[str, str] | None = None) -> None:
        self.value = value
        if colors:
            self.colors = colors
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(r, rad, rad)
        if self.value is None:
            pen = QPen(QColor(theme.TEXT_MUTED), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, rad, rad)
            return
        v = max(0.0, min(1.0, self.value))
        if v <= 0:
            return
        fill = QRectF(r.x(), r.y(), max(r.height(), r.width() * v), r.height())
        g = QLinearGradient(fill.topLeft(), fill.topRight())
        g.setColorAt(0, QColor(self.colors[0]))
        g.setColorAt(1, QColor(self.colors[1]))
        p.setBrush(g)
        p.drawRoundedRect(fill, rad, rad)


ANIMATIONS = {"on": True}


class Spinner(QWidget):
    def __init__(self, size: int = 18):
        super().__init__()
        self.setFixedSize(size, size)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self):
        self.angle = (self.angle + 30) % 360
        self.update()

    def start(self):
        self.show()
        if ANIMATIONS["on"]:
            self.timer.start(80)

    def stop(self):
        self.timer.stop()
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self.angle)
        r = self.width() / 2 - 2
        pen = QPen(QColor(theme.VIOLET), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), 0, 270 * 16)


class ActionTile(QPushButton):
    """Large bottom action button: icon, title and subtitle."""

    def __init__(self, title: str, subtitle: str, icon_name: str, style: str = "dark"):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(68)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(14)
        self.ic = QLabel()
        self.ic.setPixmap(icons.pixmap(icon_name, "#ffffff", 30))
        lay.addWidget(self.ic)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.t = label(title)
        self.t.setStyleSheet("font-size: 12pt; font-weight: 600; color: white;")
        self.s = label(subtitle)
        self.s.setStyleSheet("font-size: 9pt; color: rgba(255,255,255,0.75);")
        col.addWidget(self.t)
        col.addWidget(self.s)
        lay.addLayout(col, 1)
        grads = {"blue": ("#1d4ed8", "#2563eb"), "violet": ("#5b21b6", "#7c3aed"),
                 "install": ("#7c3aed", "#a855f7"), "dark": (theme.CARD_2, theme.CARD)}
        a, b = grads.get(style, grads["dark"])
        border = theme.BORDER_HI if style == "dark" else b
        self.setStyleSheet(
            f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a}, stop:1 {b});"
            f" border: 1px solid {border}; border-radius: 12px; text-align: left; }}"
            f"QPushButton:hover {{ border: 1px solid #c4b5fd; }}"
            f"QPushButton:disabled {{ background: {theme.CARD}; border: 1px solid {theme.BORDER}; }}")

    def setEnabled(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        super().setEnabled(on)
        self.t.setStyleSheet(f"font-size: 12pt; font-weight: 600; color: {'white' if on else theme.TEXT_MUTED};")


class KeyValueGrid(QWidget):
    def __init__(self, key_width: int = 140):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnMinimumWidth(0, key_width)
        self.grid.setColumnStretch(1, 1)

    def set_rows(self, rows: Iterable[tuple[str, str | QWidget]]) -> None:
        clear_layout(self.grid)
        for i, (k, v) in enumerate(rows):
            self.grid.addWidget(label(k, "key"), i, 0, Qt.AlignmentFlag.AlignTop)
            if isinstance(v, QWidget):
                align = Qt.AlignmentFlag.AlignLeft if isinstance(v, StatusPill) else Qt.AlignmentFlag(0)
                self.grid.addWidget(v, i, 1, align)
            else:
                lb = label(v, "value", wrap=True, selectable=True)
                lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                self.grid.addWidget(lb, i, 1, Qt.AlignmentFlag.AlignTop)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, action: QPushButton | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.TEXT_MUTED, 42))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        tl = label(title, "h3")
        tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tl)
        tx = label(text, "dim", wrap=True)
        tx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tx.setMaximumWidth(520)
        lay.addWidget(tx, 0, Qt.AlignmentFlag.AlignCenter)
        if action:
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignCenter)


class FrameTimeChart(QWidget):
    """Frame-time line chart for one or two measured sessions."""

    def __init__(self):
        super().__init__()
        self.series: list[tuple[str, list[float], str]] = []
        self.setMinimumHeight(200)

    def set_series(self, series: list[tuple[str, list[float], str]]) -> None:
        self.series = [(n, v, c) for n, v, c in series if v]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(46, 12, -12, -28)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRect(r)
        if not self.series:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, t("No frame-time data to show."))
            return
        all_v = [x for _, v, _ in self.series for x in v]
        top = max(sorted(all_v)[int(len(all_v) * 0.995) - 1] * 1.15, 1.0)
        p.setPen(QColor(theme.TEXT_MUTED))
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{top * i / 4:.0f}")
        p.drawText(QRectF(r.left(), r.bottom() + 6, r.width(), 18), Qt.AlignmentFlag.AlignCenter,
                   t("frame time (ms) over the capture"))
        legend_x = r.left() + 8
        for name, vals, color in self.series:
            path = QPainterPath()
            n = len(vals)
            step = max(1, n // int(max(r.width(), 1)))
            for i in range(0, n, step):
                v = max(vals[i:i + step])
                x = r.left() + r.width() * i / max(n - 1, 1)
                y = r.bottom() - r.height() * min(v, top) / top
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(QColor(color), 1.4))
            p.drawPath(path)
            p.fillRect(QRectF(legend_x, r.top() + 8, 10, 10), QColor(color))
            p.setPen(QColor(theme.TEXT))
            p.drawText(QPointF(legend_x + 14, r.top() + 17), name)
            legend_x += 24 + p.fontMetrics().horizontalAdvance(name)


def confirm(parent, title: str, text: str, details: str | None = None, ok_text: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text)
    if details:
        box.setInformativeText(details)
    ok = box.addButton(ok_text or t("Continue"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(t("Cancel"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is ok


def info(parent, title: str, text: str, details: str | None = None, error: bool = False) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if error else QMessageBox.Icon.Information)
    box.setText(text)
    if details:
        box.setInformativeText(details)
    box.exec()


def scaled(pm: QPixmap, w: int, h: int) -> QPixmap:
    return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)


class Clickable(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        super().keyPressEvent(e)
