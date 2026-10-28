"""Widgets of the AKENO Network page, built from the suite design system (akeno/ui/theme.py).

* ``StatusOrb``: the state of a connection as a coloured dot; a soft ring pulses while it is changing. No glow.
* ``ConnectionCard``: one connection (LAN Play or VPN) on the dashboard: orb, state, a few facts.
* ``InfoButton``: an (i) that shows a technical explanation on hover or click, so the page itself stays short.
* ``Details``: a "How it works" section that opens on demand.
* ``Steps``: the numbered LAN Play flow with the current step marked.
* ``Avatar``, ``QualityBars`` and ``flag_pixmap``: member initials, link quality from the measured ping, and simple
  painted country flags (Windows has no colour flag emoji, so flags are drawn).
"""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

from akeno.ui import icons, theme
from akeno.ui.widgets import StatTile, label

ORB = {'ok': theme.GREEN, 'pending': theme.CYAN, 'fail': theme.RED, 'warn': theme.AMBER, 'info': theme.INFO,
       'unknown': theme.TEXT_DISABLED}


class StatusOrb(QWidget):
    """A 14 px state dot. ``pending`` animates an expanding ring (1.6 s); every other state is still."""

    def __init__(self, state: str = 'unknown', size: int = 28):
        super().__init__()
        self.setFixedSize(size, size)
        self.state, self._phase = state, 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1600)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self.set(state)

    def _tick(self, value):
        self._phase = float(value)
        self.update()

    def set(self, state: str) -> None:
        self.state = state if state in ORB else 'unknown'
        if self.state == 'pending':
            if self._anim.state() != QVariantAnimation.State.Running:
                self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
        self.setAccessibleDescription(self.state)
        self.update()

    def animating(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(ORB[self.state])
        centre = QPointF(self.width() / 2, self.height() / 2)
        core = 7.0
        if self.state in ('ok', 'pending'):
            halo = QColor(c)
            halo.setAlphaF(0.16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(centre, core + 4, core + 4)
        if self.state == 'pending':
            ring = QColor(c)
            ring.setAlphaF(max(0.0, 0.55 * (1 - self._phase)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1.6))
            r = core + 2 + self._phase * (self.width() / 2 - core - 3)
            p.drawEllipse(centre, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(centre, core - 1.5, core - 1.5)


class Fact(StatTile):
    """A dashboard fact: the value over its name, smaller than a stat tile (same API: ``.value``, ``.set``)."""

    def __init__(self, name: str, value: str = '—', hint: str | None = None):
        super().__init__(name, value, hint)
        self.value.setProperty('role', 'h3')
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setMinimumWidth(96)


class ConnectionCard(QFrame):
    """Dashboard card of one connection: icon and title, a pill-free big state line with its orb, then facts."""

    def __init__(self, title: str, icon_name: str, subtitle: str = ''):
        super().__init__()
        self.setObjectName('Card')
        self.setAccessibleName(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*theme.CARD_PADDING)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.ACCENT_TEXT, 18))
        ic.setFixedSize(20, 20)
        head.addWidget(ic)
        head.addWidget(label(title, 'h2'))
        head.addStretch(1)
        self.right = QHBoxLayout()
        self.right.setSpacing(6)
        head.addLayout(self.right)
        lay.addLayout(head)
        state = QHBoxLayout()
        state.setSpacing(10)
        self.orb = StatusOrb()
        state.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.state_label = label('', 'display')
        self.detail_label = label(subtitle, 'muted', wrap=True)
        texts.addWidget(self.state_label)
        texts.addWidget(self.detail_label)
        state.addLayout(texts, 1)
        lay.addLayout(state)
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(20)
        self.facts.setVerticalSpacing(12)
        lay.addLayout(self.facts)
        lay.addStretch(1)
        self._count = 0

    def add_fact(self, name: str, value: str = '—', hint: str | None = None, columns: int = 3) -> Fact:
        f = Fact(name, value, hint)
        self.facts.addWidget(f, self._count // columns, self._count % columns)
        self._count += 1
        return f

    def set_state(self, text: str, state: str, detail: str | None = None) -> None:
        self.state_label.setText(text)
        self.orb.set(state)
        if detail is not None:
            self.detail_label.setText(detail)
        self.setAccessibleDescription(text)


class InfoButton(QToolButton):
    """(i): the explanation is a tooltip (hover) and also opens on click or keyboard (Space/Enter)."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setIcon(icons.icon('info', theme.TEXT_MUTED, 14))
        self.setIconSize(QSize(14, 14))
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFixedSize(22, 22)
        self.setToolTip(f'<div style="max-width:340px">{text}</div>')
        self.setAccessibleName(text)
        self.setStyleSheet('QToolButton { border: none; background: transparent; border-radius: 11px; }'
                           f'QToolButton:hover {{ background: {theme.SURFACE_3}; }}')
        self.clicked.connect(lambda: QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self.toolTip(), self))


def heading(text: str, info_text: str | None = None, role: str = 'h3') -> QWidget:
    """A section heading with an optional (i) next to it."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(label(text, role))
    if info_text:
        lay.addWidget(InfoButton(info_text))
    lay.addStretch(1)
    return w


class Details(QWidget):
    """A collapsed section: a link-style toggle, the content below it once opened."""

    def __init__(self, title: str, text: str | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setStyleSheet(f'QToolButton {{ border: none; background: transparent; color: {theme.ACCENT_TEXT};'
                                  f' font-weight: 600; padding: 2px 0; }}')
        self.toggle.toggled.connect(self.set_open)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        self.box = QWidget()
        self.box.setLayout(self.body)
        self.box.setVisible(False)
        lay.addWidget(self.box)
        if text:
            self.body.addWidget(label(text, 'dim', wrap=True))

    def set_open(self, opened: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
        self.box.setVisible(opened)
        if self.toggle.isChecked() != opened:
            self.toggle.setChecked(opened)


class Steps(QWidget):
    """1 — 2 — 3 — 4 with the current step in the accent colour and finished steps ticked."""

    def __init__(self, names: list[str]):
        super().__init__()
        self.names, self.current = names, 0
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(' → '.join(names))

    def set_current(self, index: int) -> None:
        self.current = max(0, min(index, len(self.names) - 1))
        self.setAccessibleDescription(self.names[self.current])
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.names)
        w = self.width() / n
        accent = QColor(theme.accent().base)
        for i, name in enumerate(self.names):
            cx = w * i + w / 2
            if i < n - 1:
                p.setPen(QPen(QColor(accent if i < self.current else theme.BORDER_HI), 2))
                p.drawLine(QPointF(cx + 14, 14), QPointF(cx + w - 14, 14))
            done, now = i < self.current, i == self.current
            p.setPen(QPen(accent if (done or now) else QColor(theme.BORDER_BTN), 1.6))
            p.setBrush(accent if done else QColor(theme.accent().soft(0.22)) if now else QColor(theme.SURFACE_2))
            p.drawEllipse(QPointF(cx, 14), 11, 11)
            p.setPen(QColor('#FFFFFF' if done else theme.TEXT if now else theme.TEXT_MUTED))
            f = p.font()
            f.setBold(True)
            f.setPointSizeF(theme.FONT_SMALL)
            p.setFont(f)
            p.drawText(QRectF(cx - 11, 3, 22, 22), Qt.AlignmentFlag.AlignCenter, '✓' if done else str(i + 1))
            f.setBold(now)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if now else theme.TEXT_DIM if done else theme.TEXT_MUTED))
            p.drawText(QRectF(w * i + 2, 30, w - 4, 22), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(w - 6)))


AVATAR_COLORS = ('#6D4AFF', '#2E8BC0', '#1E9E7A', '#B4529C', '#C27B2C', '#4F67D8', '#2A9DAE', '#8E5BD6')


def avatar_pixmap(seed: str, text: str, size: int = 28) -> QPixmap:
    """Initials on a colour chosen from the account id (stable for the same member on every PC)."""
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    colour = AVATAR_COLORS[int(hashlib.sha256(seed.encode()).hexdigest()[:4], 16) % len(AVATAR_COLORS)]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    p.drawEllipse(QRectF(0, 0, size, size))
    p.setPen(QColor('#FFFFFF'))
    f = p.font()
    f.setBold(True)
    f.setPixelSize(int(size * 0.42))
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials(text))
    p.end()
    return pm


def initials(text: str) -> str:
    parts = [w for w in (text or '').replace('_', ' ').split() if w[:1].isalnum()]
    if not parts:
        return '?'
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else '')).upper()


def quality(latency_ms: float | None) -> int:
    """0-4 bars from a measured round trip: <40 ms 4, <80 3, <150 2, otherwise 1; 0 when nothing was measured."""
    if latency_ms is None:
        return 0
    return 4 if latency_ms < 40 else 3 if latency_ms < 80 else 2 if latency_ms < 150 else 1


def quality_pixmap(bars: int, width: int = 22, height: int = 14) -> QPixmap:
    ratio = 2
    pm = QPixmap(width * ratio, height * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    on = QColor(theme.GREEN if bars >= 3 else theme.AMBER if bars == 2 else theme.RED)
    for i in range(4):
        h = height * (i + 1) / 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(on if i < bars else QColor(theme.BORDER_BTN))
        p.drawRoundedRect(QRectF(i * (width / 4) + 1, height - h, width / 4 - 2, h), 1.2, 1.2)
    p.end()
    return pm


def _stripes(p, rect, colours, vertical=False):
    n = len(colours)
    for i, c in enumerate(colours):
        p.setBrush(QColor(c))
        if vertical:
            p.drawRect(QRectF(rect.x() + rect.width() * i / n, rect.y(), rect.width() / n + 0.5, rect.height()))
        else:
            p.drawRect(QRectF(rect.x(), rect.y() + rect.height() * i / n, rect.width(), rect.height() / n + 0.5))


def flag_pixmap(code: str | None, width: int = 24, height: int = 16) -> QPixmap:
    """A simplified flag for the shipped target countries; other codes get a neutral tile with the code."""
    ratio = 2
    pm = QPixmap(width * ratio, height * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    r = QRectF(0, 0, width, height)
    clip.addRoundedRect(r, 2.5, 2.5)
    p.setClipPath(clip)
    p.setPen(Qt.PenStyle.NoPen)
    code = (code or '').upper()
    if code == 'DE':
        _stripes(p, r, ('#000000', '#DD0000', '#FFCE00'))
    elif code == 'NL':
        _stripes(p, r, ('#AE1C28', '#FFFFFF', '#21468B'))
    elif code == 'FR':
        _stripes(p, r, ('#0055A4', '#FFFFFF', '#EF4135'), vertical=True)
    elif code == 'JP':
        p.setBrush(QColor('#FFFFFF'))
        p.drawRect(r)
        p.setBrush(QColor('#BC002D'))
        p.drawEllipse(QPointF(width / 2, height / 2), height * 0.3, height * 0.3)
    elif code == 'TR':
        p.setBrush(QColor('#E30A17'))
        p.drawRect(r)
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(QPointF(width * 0.4, height / 2), height * 0.25, height * 0.25)
        p.setBrush(QColor('#E30A17'))
        p.drawEllipse(QPointF(width * 0.44, height / 2), height * 0.2, height * 0.2)
        p.setBrush(QColor('#FFFFFF'))
        p.drawPolygon(_star(width * 0.6, height / 2, height * 0.12))
    elif code == 'CA':
        _stripes(p, r, ('#D80621', '#FFFFFF', '#FFFFFF', '#D80621'), vertical=True)
        p.setBrush(QColor('#D80621'))
        p.drawPolygon(_star(width / 2, height / 2, height * 0.27, points=5))
    elif code == 'US':
        _stripes(p, r, ['#B22234' if i % 2 == 0 else '#FFFFFF' for i in range(7)])
        p.setBrush(QColor('#3C3B6E'))
        p.drawRect(QRectF(0, 0, width * 0.45, height * 4 / 7))
    elif code == 'GB':
        p.setBrush(QColor('#012169'))
        p.drawRect(r)
        p.setPen(QPen(QColor('#FFFFFF'), height * 0.22))
        p.drawLine(QPointF(0, 0), QPointF(width, height))
        p.drawLine(QPointF(width, 0), QPointF(0, height))
        p.setPen(QPen(QColor('#C8102E'), height * 0.08))
        p.drawLine(QPointF(0, 0), QPointF(width, height))
        p.drawLine(QPointF(width, 0), QPointF(0, height))
        p.setPen(QPen(QColor('#FFFFFF'), height * 0.32))
        p.drawLine(QPointF(width / 2, 0), QPointF(width / 2, height))
        p.drawLine(QPointF(0, height / 2), QPointF(width, height / 2))
        p.setPen(QPen(QColor('#C8102E'), height * 0.18))
        p.drawLine(QPointF(width / 2, 0), QPointF(width / 2, height))
        p.drawLine(QPointF(0, height / 2), QPointF(width, height / 2))
    else:
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRect(r)
        p.setPen(QColor(theme.TEXT_DIM))
        f = p.font()
        f.setBold(True)
        f.setPixelSize(int(height * 0.6))
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, code or '·')
    p.setClipping(False)
    p.setPen(QPen(QColor(255, 255, 255, 40), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 2.5, 2.5)
    p.end()
    return pm


def _star(cx, cy, radius, points=5):
    import math
    poly = QPolygonF()
    for i in range(points * 2):
        rr = radius if i % 2 == 0 else radius * 0.45
        a = math.pi / points * i - math.pi / 2
        poly.append(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return poly
