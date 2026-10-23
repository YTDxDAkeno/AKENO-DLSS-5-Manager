"""Reusable AKENO widgets."""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget, QMessageBox)

from akeno.i18n import t
from akeno.ui import icons, theme


class _ShrinkLabel(QLabel):
    """Word-wrapping label that prefers one line: it only wraps when its layout cannot give it that width."""

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        m = self.contentsMargins()
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + m.left() + m.right() + 2, s.height())


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False,
          shrink: bool = False) -> QLabel:
    """``shrink``: one line when there is room, wrapped (instead of widening the page) when there is not."""
    lb = _ShrinkLabel(text) if shrink else QLabel(text)
    wrap = wrap or shrink
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
                 right: QWidget | None = None, parent=None, fill: bool = False):
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
        # fill=True: the body takes all extra height (no empty band at the bottom of tall cards)
        self.outer.addLayout(self.body, 1 if fill else 0)
        if not fill:
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
        # a sentence-long pill wraps instead of forcing the page wider than the window, and then grows in height
        wrap = len(text) > 48
        self.setWordWrap(wrap)
        self.setSizePolicy(QSizePolicy.Policy.Maximum,
                           QSizePolicy.Policy.Minimum if wrap else QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"QLabel {{ background: rgba(255,255,255,0.04); border: 1px solid {theme.BORDER_HI};"
                           f" border-radius: 11px; padding: 3px 10px; }}")
        self.setAccessibleName(text)


def status_badge(state: str, size: int = 22) -> QPixmap:
    """Filled verification badge: green check (done), amber triangle (warning), red cross (failed/blocked),
    blue info, grey clock (pending / not run yet). A green badge must only be used for completed checks."""
    ratio = icons.device_ratio()
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(theme.STATE_COLORS.get(state, theme.TEXT_MUTED))
    s = float(size)
    pen = QPen(QColor("#06101e") if state in ("ok", "warn") else color, s * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if state == "warn":
        tri = QPainterPath()
        tri.moveTo(s * 0.5, s * 0.06)
        tri.lineTo(s * 0.97, s * 0.90)
        tri.lineTo(s * 0.03, s * 0.90)
        tri.closeSubpath()
        p.fillPath(tri, color)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.5, s * 0.36), QPointF(s * 0.5, s * 0.60))
        p.drawPoint(QPointF(s * 0.5, s * 0.75))
    elif state == "ok":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92))
        p.setPen(pen)
        path = QPainterPath(QPointF(s * 0.29, s * 0.52))
        path.lineTo(QPointF(s * 0.44, s * 0.67))
        path.lineTo(QPointF(s * 0.72, s * 0.36))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    else:
        fill = QColor(color)
        fill.setAlpha(46)
        p.setBrush(fill)
        p.setPen(QPen(color, s * 0.08))
        p.drawEllipse(QRectF(s * 0.08, s * 0.08, s * 0.84, s * 0.84))
        p.setPen(pen)
        if state == "fail":
            p.drawLine(QPointF(s * 0.36, s * 0.36), QPointF(s * 0.64, s * 0.64))
            p.drawLine(QPointF(s * 0.64, s * 0.36), QPointF(s * 0.36, s * 0.64))
        elif state in ("info", "unknown"):
            p.drawLine(QPointF(s * 0.5, s * 0.46), QPointF(s * 0.5, s * 0.68))
            p.drawPoint(QPointF(s * 0.5, s * 0.32))
        elif state == "running":
            p.drawArc(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44), 90 * 16, 270 * 16)
        else:   # pending: clock
            p.drawLine(QPointF(s * 0.5, s * 0.30), QPointF(s * 0.5, s * 0.52))
            p.drawLine(QPointF(s * 0.5, s * 0.52), QPointF(s * 0.64, s * 0.62))
    p.end()
    return pm


class ElidedLabel(QLabel):
    """Single-line label that elides with "…" and shows the full text as tooltip."""

    def __init__(self, text: str = "", role: str | None = None):
        super().__init__()
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full if self._full else "")
        self.setAccessibleName(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        w = max(10, self.width())
        super().setText(QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, w))


class CheckRow(QWidget):
    """Status badge + title + detail used in the verification panels and the install wizard.

    ``compact`` keeps the detail on one elided line (full text in the tooltip) so dense panels keep a
    fixed height."""

    def __init__(self, state: str, title: str, detail: str = "", compact: bool = False):
        super().__init__()
        self.state = state
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(status_badge(state, 20))
        ic.setFixedSize(22, 22)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 1, 0, 0)
        tl = label(title)
        low = title.lower()
        if state == "fail":
            color = "#FF8A9B"
        elif state == "warn" and ("community" in low or "experimental" in low or "experiment" in low):
            color = theme.AMBER
        else:
            color = theme.TEXT
        tl.setStyleSheet(f"font-weight:600; color:{color}")
        col.addWidget(tl)
        if detail:
            if compact:
                dl = ElidedLabel(detail, "dim")
                dl.setStyleSheet("font-size: 9pt;")
            else:
                dl = label(detail, "dim", wrap=True)
            col.addWidget(dl)
        lay.addLayout(col, 1)
        self.setAccessibleName(f"{title}: {detail}")
        self.setToolTip(f"{title}\n{detail}" if detail else title)


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
        hi = QLinearGradient(fill.topLeft(), fill.bottomLeft())
        hi.setColorAt(0, QColor(255, 255, 255, 60))
        hi.setColorAt(0.5, QColor(255, 255, 255, 0))
        p.setBrush(hi)
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
    """Large premium action button (icon, title, subtitle) with a gradient and an animated hover glow."""

    GRADS = {"blue": ("#0B3FAF", "#1C6DFF", "#3E8BFF"), "violet": ("#3E1FB0", "#6A35F0", "#8E5CFF"),
             "install": ("#6421E6", "#9A3DFF", "#C77DFF"), "dark": ("#13213A", "#0E192C", "#3A5585")}

    def __init__(self, title: str, subtitle: str, icon_name: str, style: str = "dark"):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        self.style_name = style
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 14, 8)
        lay.setSpacing(14)
        self.ic = QLabel()
        self.ic.setPixmap(icons.pixmap(icon_name, "#ffffff", 32, 1.8))
        self.ic.setFixedSize(34, 34)
        lay.addWidget(self.ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.t = label(title)
        self.s = label(subtitle)
        self.s.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addStretch(1)
        col.addWidget(self.t)
        col.addWidget(self.s)
        col.addStretch(1)
        lay.addLayout(col, 1)
        a, b, edge = self.GRADS.get(style, self.GRADS["dark"])
        self.setStyleSheet(
            f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a}, stop:1 {b});"
            f" border: 1px solid {edge}; border-radius: 12px; text-align: left; padding: 0; }}"
            f"QPushButton:hover {{ border: 1px solid #D9CCFF; }}"
            f"QPushButton:pressed {{ background: {b}; }}"
            f"QPushButton:focus {{ border: 1px solid #FFFFFF; }}"
            f"QPushButton:disabled {{ background: #0D1729; border: 1px solid {theme.BORDER}; }}")
        self._glow_color = QColor(edge if style != "dark" else "#6B5DFA")
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(0)
        self._glow.setColor(self._glow_color)
        self.setGraphicsEffect(self._glow)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self._glow.setBlurRadius(float(v)))
        self._apply_text(True)

    def _apply_text(self, on: bool) -> None:
        self.t.setStyleSheet(f"font-size: 12pt; font-weight: 700; color: {'white' if on else theme.TEXT_MUTED};")
        self.s.setStyleSheet(f"font-size: 9pt; color: {'rgba(255,255,255,0.78)' if on else theme.TEXT_MUTED};")
        self.ic.setEnabled(on)

    def _animate(self, target: float) -> None:
        if not ANIMATIONS["on"]:
            self._glow.setBlurRadius(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._glow.blurRadius())
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._animate(26.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate(0.0)
        super().leaveEvent(e)

    def setEnabled(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        super().setEnabled(on)
        self._apply_text(on)
        if not on:
            self._glow.setBlurRadius(0)


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


class GradientText(QWidget):
    """Large headline text (e.g. the recommended working resolution) filled with a neon gradient."""

    def __init__(self, text: str = "", point_size: float = 40, colors: tuple[str, ...] = ("#E9E2FF", "#B79CFF", "#7F6BFF")):
        super().__init__()
        self._text = text
        self.colors = colors
        self._font = theme.ui_font(point_size, QFont.Weight.Black)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def set_point_size(self, pt: float) -> None:
        self._font.setPointSizeF(pt)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        return QSize(fm.horizontalAdvance(self._text or "—") + 8, fm.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        fm = QFontMetrics(self._font)
        path = QPainterPath()
        path.addText(QPointF(2, (self.height() + fm.ascent() - fm.descent()) / 2), self._font, self._text)
        r = path.boundingRect()
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        for i, c in enumerate(self.colors):
            g.setColorAt(i / max(1, len(self.colors) - 1), QColor(c))
        glow = QColor(self.colors[-1])
        glow.setAlpha(55)
        p.setPen(QPen(glow, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        p.fillPath(path, g)


class ArtPanel(QFrame):
    """Rounded panel that paints a design-package artwork behind real widgets (cover-scaled, right-aligned)
    with a dark-to-transparent gradient on the left so text stays readable."""

    def __init__(self, asset: str, base: tuple[str, str] = ("#120F3A", "#1C1650"), border: str = "#3B2D8A",
                 radius: int = 12, art_opacity: float = 0.9, fade_to: float = 0.62, parent=None):
        super().__init__(parent)
        self.asset = asset
        self.base = base
        self.border = border
        self.radius = radius
        self.art_opacity = art_opacity
        self.fade_to = fade_to
        self._cache: QPixmap | None = None
        self._cache_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def _background(self) -> QPixmap:
        from akeno.ui import assets
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(round(w * dpr), round(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        clip = QPainterPath()
        clip.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(clip)
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0, QColor(self.base[0]))
        base.setColorAt(1, QColor(self.base[1]))
        p.fillRect(r, base)
        art = assets.cover(self.asset, w, h, dpr, h_align=1.0, v_align=0.5)
        if not art.isNull():
            p.setOpacity(self.art_opacity)
            p.drawPixmap(0, 0, art)
            p.setOpacity(1.0)
        fade = QLinearGradient(0, 0, w, 0)
        c0 = QColor(self.base[0])
        fade.setColorAt(0, QColor(c0.red(), c0.green(), c0.blue(), 235))
        fade.setColorAt(0.38, QColor(c0.red(), c0.green(), c0.blue(), 170))
        fade.setColorAt(self.fade_to, QColor(c0.red(), c0.green(), c0.blue(), 0))
        p.fillRect(r, fade)
        p.setClipping(False)
        p.setPen(QPen(QColor(self.border), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, self.radius, self.radius)
        p.end()
        self._cache, self._cache_key = pm, key
        return pm

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._background())


class ProfileRow(Clickable):
    """Profile comparison row: icon badge, title, summary, gradient meter and value; selected glow and an
    animated hover highlight. Painted by hand so the glow does not need a graphics effect."""

    def __init__(self, title: str, summary: str, icon_name: str, colors: tuple[str, str], selected: bool = False):
        super().__init__()
        self.colors = colors
        self.selected = selected
        self._hover = 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setMinimumHeight(56)
        self.setMaximumHeight(78)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(12)
        self.badge = QLabel()
        self.badge.setFixedSize(34, 34)
        self.badge.setPixmap(self._badge(icon_name))
        lay.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title)
        self.title.setStyleSheet("font-weight: 700; font-size: 10.5pt;")
        col.addWidget(self.title)
        self.summary = ElidedLabel(summary, "muted")
        col.addWidget(self.summary)
        self.meter = Meter(None, colors, 6)
        col.addWidget(self.meter)
        lay.addLayout(col, 1)
        self.value = label("", "dim")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value.setMinimumWidth(92)
        lay.addWidget(self.value)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_hover)

    def _badge(self, icon_name: str) -> QPixmap:
        ratio = icons.device_ratio()
        pm = QPixmap(34 * ratio, 34 * ratio)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(ratio)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.colors[0])
        c.setAlpha(40)
        ring = QColor(self.colors[0])
        ring.setAlpha(150)
        p.setBrush(c)
        p.setPen(QPen(ring, 1.2))
        p.drawEllipse(QRectF(1, 1, 32, 32))
        p.drawPixmap(QRectF(7, 7, 20, 20), icons.pixmap(icon_name, self.colors[1], 20, 2.0), QRectF())
        p.end()
        return pm

    def _set_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        if not ANIMATIONS["on"]:
            self._set_hover(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate(0.0)
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self._animate(1.0)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._animate(0.0)
        super().focusOutEvent(e)

    def set_value(self, text: str, frac: float | None) -> None:
        self.value.setText(text)
        self.meter.set(frac)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5)
        if self.selected:
            for i, a in enumerate((26, 16, 8)):
                g = QColor(theme.VIOLET)
                g.setAlpha(a)
                p.setPen(QPen(g, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-i - 0.5, -i - 0.5, i + 0.5, i + 0.5), 11 + i, 11 + i)
        bg = QLinearGradient(r.topLeft(), r.topRight())
        if self.selected:
            bg.setColorAt(0, QColor(78, 49, 213, 70 + int(25 * self._hover)))
            bg.setColorAt(1, QColor(22, 142, 255, 18 + int(15 * self._hover)))
        else:
            bg.setColorAt(0, QColor(255, 255, 255, 6 + int(14 * self._hover)))
            bg.setColorAt(1, QColor(255, 255, 255, 3 + int(8 * self._hover)))
        border = QColor(theme.VIOLET) if self.selected else QColor(theme.BORDER_HI)
        if not self.selected and self._hover:
            hc = QColor(self.colors[0])
            border = QColor(int(border.red() + (hc.red() - border.red()) * self._hover * 0.7),
                            int(border.green() + (hc.green() - border.green()) * self._hover * 0.7),
                            int(border.blue() + (hc.blue() - border.blue()) * self._hover * 0.7))
        p.setBrush(bg)
        p.setPen(QPen(border, 1.4 if self.selected else 1.0))
        p.drawRoundedRect(r, 10, 10)


class WrapCheck(QWidget):
    """Checkbox whose (long) text wraps; clicking the text toggles it. Exposes isChecked/setChecked/toggled."""

    def __init__(self, text: str):
        super().__init__()
        from PySide6.QtWidgets import QCheckBox
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.box = QCheckBox()
        self.box.setAccessibleName(text)
        lay.addWidget(self.box, 0, Qt.AlignmentFlag.AlignTop)
        self.text = label(text, wrap=True)
        self.text.setCursor(Qt.CursorShape.PointingHandCursor)
        self.text.mouseReleaseEvent = lambda e: self.box.toggle()
        lay.addWidget(self.text, 1)
        self.toggled = self.box.toggled

    def isChecked(self) -> bool:  # noqa: N802 (Qt naming)
        return self.box.isChecked()

    def setChecked(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        self.box.setChecked(on)


class FlowLayout(QLayout):
    """Left-to-right row that wraps onto the next line when it is too narrow (action buttons, preset chips), so
    long translations or wide system fonts never force horizontal scrolling."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _visible(self):
        return [it for it in self._items if not (it.widget() is not None and it.widget().isHidden())]

    def sizeHint(self) -> QSize:
        items = self._visible()
        m = self.contentsMargins()
        w = sum(it.sizeHint().width() for it in items) + self._gap * max(0, len(items) - 1)
        h = max((it.sizeHint().height() for it in items), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._visible():
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[list] = [[]]
        x = area.x()
        for it in self._visible():
            w = it.sizeHint().width()
            if lines[-1] and x + w > area.right() + 1:
                lines.append([])
                x = area.x()
            lines[-1].append(it)
            x += w + self._gap
        y = area.y()
        for row in lines:
            height = max((it.sizeHint().height() for it in row), default=0)
            x = area.x()
            for it in row:                 # items of different heights share the line's vertical centre
                hint = it.sizeHint()
                if apply:
                    it.setGeometry(QRect(QPoint(x, y + (height - hint.height()) // 2), hint))
                x += hint.width() + self._gap
            y += height + self._gap
        return max(0, y - self._gap) - rect.y() + m.bottom()


class ResponsiveRow(QWidget):
    """Cards side by side when they fit, stacked when the window is too narrow (150 % scaling, long
    translations), so a page never needs horizontal scrolling."""

    def __init__(self, spacing: int = 16):
        super().__init__()
        self._cards: list[tuple[QWidget, int]] = []
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._stacked: bool | None = None

    def add(self, widget: QWidget, stretch: int = 1) -> None:
        self._cards.append((widget, stretch))
        self._place(bool(self._stacked))

    def _side_by_side_width(self) -> int:
        return (sum(w.minimumSizeHint().width() for w, _ in self._cards)
                + self._grid.spacing() * max(0, len(self._cards) - 1))

    def minimumSizeHint(self) -> QSize:
        width = max((w.minimumSizeHint().width() for w, _ in self._cards), default=0)
        return QSize(width, super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        stacked = e.size().width() < self._side_by_side_width()
        if stacked != self._stacked:
            QTimer.singleShot(0, lambda: self._place(stacked))   # not while the parent layout is resizing us

    def _place(self, stacked: bool) -> None:
        self._stacked = stacked
        for w, _ in self._cards:
            self._grid.removeWidget(w)
        for c in range(len(self._cards)):
            self._grid.setColumnStretch(c, 0)
        for i, (w, stretch) in enumerate(self._cards):
            if stacked:
                self._grid.addWidget(w, i, 0)
            else:
                self._grid.addWidget(w, 0, i)
                self._grid.setColumnStretch(i, stretch)
        if stacked:
            self._grid.setColumnStretch(0, 1)

