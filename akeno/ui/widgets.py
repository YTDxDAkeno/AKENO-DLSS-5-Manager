"""Reusable AKENO widgets."""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton,
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
        color = {"primary": "#FFFFFF", "danger": "#FF9FAC", "link": theme.ACCENT_TEXT,
                 "ghost": theme.TEXT_DIM}.get(kind or "", theme.TEXT)
        b.setIcon(icons.icon(icon_name, color, 16))
        b.setIconSize(QSize(16, 16))
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
    """Panel with an icon + title header and an optional widget on the right.

    Section icons are drawn in the accent colour whatever ``icon_color`` a caller passes: colour on a card header
    is decoration, and state colours belong to the pills and badges that carry the state."""

    def __init__(self, title: str = "", icon_name: str | None = None, icon_color: str | None = None,
                 right: QWidget | None = None, parent=None, fill: bool = False):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAccessibleName(title)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(*theme.CARD_PADDING)
        self.outer.setSpacing(10)
        if title:
            head = QHBoxLayout()
            head.setSpacing(10)
            if icon_name:
                ic = QLabel()
                ic.setPixmap(icons.pixmap(icon_name, theme.ACCENT_TEXT, 18))
                ic.setFixedSize(20, 20)
                ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                head.addWidget(ic)
            self.title_label = label(title, "h2")
            head.addWidget(self.title_label)
            head.addStretch(1)
            if right is not None:
                head.addWidget(right)
            self.outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        # fill=True: the body takes all extra height (no empty band at the bottom of tall cards)
        self.outer.addLayout(self.body, 1 if fill else 0)
        if not fill:
            self.outer.addStretch(1)


class SubCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SubCard")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
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
        self.setStyleSheet(f"QLabel {{ background: rgba(255,255,255,0.045); border: 1px solid {theme.BORDER};"
                           f" border-radius: {theme.RADIUS_PILL}px; padding: 2px 10px; color: {theme.TEXT_DIM};"
                           f" font-size: {theme.FONT_SMALL}pt; font-weight: 600; }}")
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

    def __init__(self, text: str = "", role: str | None = None, mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__()
        if role:
            self.setProperty("role", role)
        self._mode = mode
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
        super().setText(QFontMetrics(self.font()).elidedText(self._full, self._mode, w))


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
            color = "#FF9FAC"
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
    """Large action button (icon, title, subtitle). ``style`` picks a filled accent tile (primary actions) or a
    quiet surface tile; hover lifts the border, no glow."""

    STYLES = ("blue", "violet", "install", "dark")

    def __init__(self, title: str, subtitle: str, icon_name: str, style: str = "dark"):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        self.style_name = style
        self.filled = style == "install"           # one filled tile per row: the main action
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 14, 8)
        lay.setSpacing(12)
        self.ic = QLabel()
        self._icon_name = icon_name
        self.ic.setFixedSize(26, 26)
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
        a = theme.accent()
        if self.filled:
            css = (f"QPushButton {{ background: {a.strong}; border: 1px solid {a.strong}; border-radius: 9px;"
                   f" text-align: left; padding: 0; }}"
                   f"QPushButton:hover {{ background: {a.hover}; border-color: {a.text}; }}"
                   f"QPushButton:pressed {{ background: {a.pressed}; }}"
                   f"QPushButton:focus {{ border-color: #FFFFFF; }}")
        else:
            css = (f"QPushButton {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER_HI};"
                   f" border-radius: 9px; text-align: left; padding: 0; }}"
                   f"QPushButton:hover {{ background: {theme.SURFACE_3}; border-color: {theme.BORDER_BTN}; }}"
                   f"QPushButton:pressed {{ background: #111827; }}"
                   f"QPushButton:focus {{ border-color: {a.base}; }}")
        css += (f"QPushButton:disabled {{ background: #0D131E; border: 1px solid {theme.BORDER}; }}")
        self.setStyleSheet(css)
        self._apply_text(True)

    def _apply_text(self, on: bool) -> None:
        main = "#FFFFFF" if self.filled else theme.TEXT
        sub = "rgba(255,255,255,0.80)" if self.filled else theme.TEXT_DIM
        self.t.setStyleSheet(f"font-size: 11pt; font-weight: 600; color: {main if on else theme.TEXT_DISABLED};")
        self.s.setStyleSheet(f"font-size: 9pt; color: {sub if on else theme.TEXT_DISABLED};")
        color = ("#FFFFFF" if self.filled else theme.ACCENT_TEXT) if on else theme.TEXT_DISABLED
        self.ic.setPixmap(icons.pixmap(self._icon_name, color, 24, 1.8))

    def setEnabled(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        super().setEnabled(on)
        self._apply_text(on)


class KeyValueGrid(QWidget):
    def __init__(self, key_width: int = 140):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnMinimumWidth(0, key_width)
        self.grid.setColumnStretch(1, 1)

    def set_rows(self, rows: Iterable[tuple[str, str | QWidget]], elide: bool = False) -> None:
        """``elide``: values stay on one line and shorten in the middle (paths); the full text is the tooltip."""
        clear_layout(self.grid)
        for i, (k, v) in enumerate(rows):
            self.grid.addWidget(label(k, "key"), i, 0, Qt.AlignmentFlag.AlignTop)
            if isinstance(v, QWidget):
                align = Qt.AlignmentFlag.AlignLeft if isinstance(v, StatusPill) else Qt.AlignmentFlag(0)
                self.grid.addWidget(v, i, 1, align)
            elif elide:
                lb = ElidedLabel(v, "value", Qt.TextElideMode.ElideMiddle)
                lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.grid.addWidget(lb, i, 1, Qt.AlignmentFlag.AlignTop)
            else:
                lb = label(v, "value", wrap=True, selectable=True)
                lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                self.grid.addWidget(lb, i, 1, Qt.AlignmentFlag.AlignTop)


class EmptyState(QWidget):
    """What a list shows when it has nothing yet: an icon, what is missing and the one action that fills it."""

    def __init__(self, icon_name: str, title: str, text: str, action: QPushButton | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(6)
        lay.setContentsMargins(16, 20, 16, 20)
        ic = QLabel()
        ic.setPixmap(_icon_disc(icon_name, 44))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        lay.addSpacing(4)
        tl = label(title, "h3")
        tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tl)
        tx = label(text, "dim", wrap=True)
        tx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tx.setMaximumWidth(460)
        line = QHBoxLayout()                    # centred by stretches: an aligned word-wrapped label loses lines
        line.addStretch(1)
        line.addWidget(tx, 100)
        line.addStretch(1)
        lay.addLayout(line)
        if action:
            lay.addSpacing(6)
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignCenter)


def _icon_disc(icon_name: str, size: int) -> QPixmap:
    """An icon on a faint accent disc (empty states, list placeholders)."""
    ratio = icons.device_ratio()
    pm = QPixmap(round(size * ratio), round(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    disc = QColor(theme.ACCENT)
    disc.setAlpha(30)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(disc)
    p.drawEllipse(QRectF(0, 0, size, size))
    inner = size * 0.5
    p.drawPixmap(QRectF((size - inner) / 2, (size - inner) / 2, inner, inner),
                 icons.pixmap(icon_name, theme.ACCENT_TEXT, int(inner)), QRectF())
    p.end()
    return pm


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
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.selected:
            acc = QColor(theme.ACCENT)
            acc.setAlpha(34 + int(14 * self._hover))
            bg = acc
        else:
            bg = QColor(255, 255, 255, 5 + int(9 * self._hover))
        border = QColor(theme.ACCENT) if self.selected else QColor(theme.BORDER_HI)
        if not self.selected and self._hover:
            hc = QColor(self.colors[0])
            border = QColor(int(border.red() + (hc.red() - border.red()) * self._hover * 0.7),
                            int(border.green() + (hc.green() - border.green()) * self._hover * 0.7),
                            int(border.blue() + (hc.blue() - border.blue()) * self._hover * 0.7))
        p.setBrush(bg)
        p.setPen(QPen(border, 1.2 if self.selected else 1.0))
        p.drawRoundedRect(r, 9, 9)


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



# -- page structure and forms (design system, docs/DESIGN_SYSTEM.md) ------------------------------------------
class Divider(QFrame):
    """1 px line between groups inside a card."""

    def __init__(self):
        super().__init__()
        self.setProperty("role", "divider")
        self.setFixedHeight(1)


class PageHeader(QWidget):
    """Page title, an optional one- or two-line description and the page's own actions on the right."""

    def __init__(self, title: str, subtitle: str | None = None, actions: Iterable[QWidget] = ()):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title = label(title, "h1")
        col.addWidget(self.title, 0, Qt.AlignmentFlag.AlignLeft)
        self.subtitle = label(subtitle or "", "dim", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        for w in actions:
            self.actions.addWidget(w)
        lay.addLayout(self.actions)
        lay.setAlignment(self.actions, Qt.AlignmentFlag.AlignTop)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))


NUMBER_WIDTH = (104, 150)          # spin boxes: room for the value and its unit, not half a card
CHOICE_WIDTH = (200, 340)          # combo boxes
SLIDER_WIDTH = (180, 300)
TEXT_WIDTH = (240, 460)


def fit_control(w: QWidget) -> QWidget:
    """Size a value control for what it holds (see NUMBER_WIDTH...) instead of stretching it across a card."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QSlider
    for kind, (lo, hi) in ((QAbstractSpinBox, NUMBER_WIDTH), (QComboBox, CHOICE_WIDTH), (QSlider, SLIDER_WIDTH),
                           (QLineEdit, TEXT_WIDTH)):
        if isinstance(w, kind):
            if kind is QAbstractSpinBox:            # the widest value with its unit, plus the arrow buttons
                from akeno.ui.interaction import spin_min_width
                lo = max(lo, spin_min_width(w))
                hi = max(hi, lo)
            w.setMinimumWidth(lo)
            w.setMaximumWidth(hi)
            w.setSizePolicy(QSizePolicy.Policy.Preferred if kind is not QLineEdit else QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
            break
    return w


def game_cover(game, width: int, height: int, radius: int = 6) -> QPixmap:
    """A game's artwork cropped to ``width`` x ``height`` (cover) with rounded corners; the AKENO placeholder
    cover when the game has no artwork."""
    import os
    from akeno.ui import art
    ratio = icons.device_ratio()
    w, h = round(width * ratio), round(height * ratio)
    path = getattr(game, "artwork_path", None)
    src = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
    if src.isNull():
        src = art.cover_fallback(game.name, w, h)
    src = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    src = src.copy((src.width() - w) // 2, (src.height() - h) // 2, w, h)
    src.setDevicePixelRatio(1)                 # painted 1:1 into ``out`` below (the fallback comes at 2x)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), radius * ratio, radius * ratio)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, src)
    p.setClipping(False)
    p.setPen(QPen(QColor(255, 255, 255, 18), ratio))
    p.drawRoundedRect(QRectF(0.5 * ratio, 0.5 * ratio, w - ratio, h - ratio), radius * ratio, radius * ratio)
    p.end()
    out.setDevicePixelRatio(ratio)
    return out


def form_row(text: str, control: QWidget | QLayout, help_text: str | None = None) -> QWidget:
    """One setting: its name (and a short explanation) on the left, the control right-aligned on the same line.

    Every form row of a card lines its controls up on the card's right edge, at widths that suit them."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 2, 0, 2)
    lay.setSpacing(16)
    col = QVBoxLayout()
    col.setSpacing(2)
    name = label(text)
    name.setWordWrap(True)
    col.addWidget(name)
    if help_text:
        col.addWidget(label(help_text, "muted", wrap=True))
    lay.addLayout(col, 1)
    if isinstance(control, QLayout):
        lay.addLayout(control)
    else:
        fit_control(control)
        lay.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.setAccessibleName(text)
    return row


def indented(widget: QWidget, depth: int = 1) -> QWidget:
    """A dependent option, set in under the option it belongs to."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(26 * depth, 0, 0, 0)
    lay.addWidget(widget)
    return box


class StatTile(QWidget):
    """A measured or counted number with its name (dashboard). Shows "—" when there is no value."""

    def __init__(self, name: str, value: str = "—", hint: str | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = label(value, "stat")
        self.name = label(name, "muted")
        lay.addWidget(self.value)
        lay.addWidget(self.name)
        if hint:
            self.setToolTip(hint)
        self.setAccessibleName(name)

    def set(self, value: str) -> None:
        self.value.setText(value)
        self.setAccessibleDescription(value)


class EmptyAware(QWidget):
    """A list or table that shows an EmptyState instead of an empty box, switching by itself as rows come and go."""

    def __init__(self, view, empty: QWidget, min_height: int = 0):
        super().__init__()
        from PySide6.QtWidgets import QStackedLayout
        self.view, self.empty = view, empty
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(view)
        self._stack.addWidget(empty)
        if min_height:
            self.setMinimumHeight(min_height)
        model = view.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
            sig.connect(self._update)
        self._update()

    def _update(self, *_) -> None:
        self._stack.setCurrentWidget(self.view if self.view.model().rowCount() else self.empty)


def form_grid(rows, label_width: int = 150, field_width: int = 560) -> QGridLayout:
    """A form to fill in (a theme's name, author, licence...): labels in one column, fields next to them, left-aligned
    and no wider than ``field_width``, so name and field stay together however wide the card is."""
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(10)
    grid.setColumnMinimumWidth(0, label_width)
    grid.setColumnStretch(2, 1)
    for i, (text, field) in enumerate(rows):
        name = label(text, "key")
        grid.addWidget(name, i, 0, Qt.AlignmentFlag.AlignVCenter if not hasattr(field, "toPlainText")
                       else Qt.AlignmentFlag.AlignTop)
        field.setMaximumWidth(field_width)
        grid.addWidget(field, i, 1)
    return grid
