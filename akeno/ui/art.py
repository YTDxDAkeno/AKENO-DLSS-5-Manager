"""Artwork: AKENO brand marks, header/recommendation backgrounds, GPU illustration, game cover fallbacks.

The design package in ``akeno/data/assets`` (see :mod:`akeno.ui.assets`) is used first. The procedural
QPainter artwork below is the fallback when an asset file is missing, so the UI never breaks.
"""

from __future__ import annotations

import hashlib
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)

from akeno.ui import theme


def _painter(pm: QPixmap) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return p


def logo_mark(size: int = 64) -> QPixmap:
    """The AKENO 'A' (design package SVG; procedural fallback)."""
    from akeno.ui import assets
    if assets.exists(assets.LOGO):
        pm = assets.svg_pixmap(assets.LOGO, size, size, dpr=2)
        if not pm.isNull():
            return pm
    return _logo_mark_fallback(size)


def wordmark(height: int, dpr: float = 2.0) -> QPixmap:
    """The AKENO wordmark (logo + name) from the design package; null pixmap when unavailable."""
    from akeno.ui import assets
    if not assets.exists(assets.WORDMARK):
        return QPixmap()
    w = round(height * assets.svg_aspect(assets.WORDMARK))
    return assets.svg_pixmap(assets.WORDMARK, w, height, dpr=dpr)


def _logo_mark_fallback(size: int = 64) -> QPixmap:
    """The AKENO 'A': two blade strokes in a blue-violet gradient."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2)
    p = _painter(pm)
    s = size
    g = QLinearGradient(0, s, s, 0)
    g.setColorAt(0, QColor(theme.BLUE))
    g.setColorAt(0.55, QColor(theme.VIOLET))
    g.setColorAt(1, QColor("#c084fc"))
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.04)
    path.lineTo(s * 0.96, s * 0.94)
    path.lineTo(s * 0.74, s * 0.94)
    path.lineTo(s * 0.50, s * 0.44)
    path.lineTo(s * 0.26, s * 0.94)
    path.lineTo(s * 0.04, s * 0.94)
    path.closeSubpath()
    p.fillPath(path, QBrush(g))
    bar = QPainterPath()
    bar.moveTo(s * 0.30, s * 0.70)
    bar.lineTo(s * 0.72, s * 0.70)
    bar.lineTo(s * 0.64, s * 0.80)
    bar.lineTo(s * 0.25, s * 0.80)
    bar.closeSubpath()
    p.fillPath(bar, QColor(255, 255, 255, 210))
    p.end()
    return pm


def header_background(width: int, height: int, dpr: float = 1.0) -> QPixmap:
    """Header: anime/cyberpunk artwork right-aligned (cover), dark gradient on the left for the title,
    soft fade at the bottom. Falls back to the procedural skyline when the asset is missing."""
    from akeno.ui import assets
    art_pm = assets.cover(assets.HEADER, width, height, dpr, h_align=1.0, v_align=0.35)
    if art_pm.isNull():
        return banner(width, height)
    pm = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(theme.BG))
    p = _painter(pm)
    p.drawPixmap(0, 0, art_pm)
    # The artwork is dark on its left already; strengthen that so the title is always readable, even when
    # a very wide window shows more of the picture.
    fade = QLinearGradient(0, 0, width, 0)
    fade.setColorAt(0.0, QColor(6, 13, 28, 245))
    fade.setColorAt(0.30, QColor(6, 13, 28, 200))
    fade.setColorAt(0.55, QColor(6, 13, 28, 60))
    fade.setColorAt(1.0, QColor(6, 13, 28, 0))
    p.fillRect(QRectF(0, 0, width, height), fade)
    glow = QRadialGradient(QPointF(width * 0.86, height * 0.2), width * 0.35)
    glow.setColorAt(0, QColor(142, 66, 255, 46))
    glow.setColorAt(1, QColor(142, 66, 255, 0))
    p.fillRect(QRectF(0, 0, width, height), glow)
    bottom = QLinearGradient(0, height * 0.65, 0, height)
    bottom.setColorAt(0, QColor(6, 13, 28, 0))
    bottom.setColorAt(1, QColor(6, 13, 28, 150))
    p.fillRect(QRectF(0, 0, width, height), bottom)
    p.end()
    return pm


def page_backdrop(width: int, height: int, dpr: float = 1.0, opacity: float = 0.26) -> QPixmap:
    """The header artwork as a faint strip behind the top right of a page: it fades in from the left and out
    towards the bottom, so it never competes with the page title or the cards. Transparent when the asset is
    missing (no procedural fallback: the strip is identity, not content)."""
    from akeno.ui import assets
    w, h = max(1, width), max(1, height)
    pm = QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    art_w = min(w, round(h * 3.4))
    art_pm = assets.cover(assets.HEADER, art_w, h, dpr, h_align=1.0, v_align=0.3)
    if art_pm.isNull():
        return pm
    layer = QPixmap(art_pm)
    mask = _painter(layer)
    mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    across = QLinearGradient(0, 0, art_w, 0)
    across.setColorAt(0.0, QColor(0, 0, 0, 0))
    across.setColorAt(0.55, QColor(0, 0, 0, 255))
    mask.fillRect(QRectF(0, 0, art_w, h), across)
    down = QLinearGradient(0, 0, 0, h)
    down.setColorAt(0.0, QColor(0, 0, 0, 255))
    down.setColorAt(0.35, QColor(0, 0, 0, 200))
    down.setColorAt(1.0, QColor(0, 0, 0, 0))
    mask.fillRect(QRectF(0, 0, art_w, h), down)
    mask.end()
    p = _painter(pm)
    p.setOpacity(opacity)
    p.drawPixmap(QPointF(w - art_w, 0), layer)
    p.end()
    return pm


def banner(width: int, height: int, seed: int = 7) -> QPixmap:
    """Night city skyline with neon accents and a stylised silhouette watching it (original artwork)."""
    pm = QPixmap(width, height)
    p = _painter(pm)
    sky = QLinearGradient(0, 0, 0, height)
    sky.setColorAt(0, QColor("#0b0f2e"))
    sky.setColorAt(0.6, QColor("#1a1250"))
    sky.setColorAt(1, QColor("#2a1a6e"))
    p.fillRect(pm.rect(), sky)
    # horizontal fade into the app background on the left
    rnd = random.Random(seed)
    glow = QRadialGradient(QPointF(width * 0.72, height * 0.95), width * 0.45)
    glow.setColorAt(0, QColor(139, 92, 246, 150))
    glow.setColorAt(1, QColor(139, 92, 246, 0))
    p.fillRect(pm.rect(), glow)
    # stars
    for _ in range(70):
        x, y = rnd.random() * width, rnd.random() * height * 0.6
        a = rnd.randint(60, 200)
        p.setPen(QPen(QColor(255, 255, 255, a), 1))
        p.drawPoint(QPointF(x, y))
    # moon
    moon = QRadialGradient(QPointF(width * 0.86, height * 0.25), height * 0.5)
    moon.setColorAt(0, QColor(220, 210, 255, 90))
    moon.setColorAt(1, QColor(220, 210, 255, 0))
    p.fillRect(pm.rect(), moon)
    p.setBrush(QColor(235, 230, 255, 230))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(width * 0.86, height * 0.25), height * 0.09, height * 0.09)
    # three layers of buildings
    for layer, (col, hmin, hmax, wmin, wmax, win) in enumerate([
            ("#1d1858", 0.25, 0.55, 18, 40, 25), ("#141144", 0.35, 0.75, 22, 55, 45), ("#0a0b26", 0.45, 0.95, 26, 70, 70)]):
        x = -10.0
        while x < width:
            w = rnd.uniform(wmin, wmax)
            h = height * rnd.uniform(hmin, hmax)
            r = QRectF(x, height - h, w, h)
            p.fillRect(r, QColor(col))
            for _ in range(int(w * h / 900 * win / 50)):
                wx = x + rnd.uniform(3, max(4, w - 5))
                wy = height - h + rnd.uniform(4, max(5, h - 6))
                c = rnd.choice([theme.CYAN, theme.VIOLET, "#f0abfc", "#93c5fd", "#fde68a"])
                qc = QColor(c)
                qc.setAlpha(rnd.randint(70, 200) if layer else 60)
                p.fillRect(QRectF(wx, wy, 2, 2.5), qc)
            if layer == 2 and rnd.random() < 0.18:
                neon = QColor(rnd.choice([theme.MAGENTA, theme.CYAN, theme.VIOLET]))
                p.fillRect(QRectF(x + 3, height - h + 10, w - 6, 3), neon)
            x += w + rnd.uniform(0, 8)
    # silhouette: a figure with long hair looking at the city (right third)
    cx, base = width * 0.80, height
    s = height / 160.0
    fig = QPainterPath()
    fig.moveTo(cx - 70 * s, base)
    fig.cubicTo(cx - 62 * s, base - 40 * s, cx - 40 * s, base - 58 * s, cx - 22 * s, base - 64 * s)   # shoulder
    fig.cubicTo(cx - 18 * s, base - 78 * s, cx - 22 * s, base - 92 * s, cx - 20 * s, base - 100 * s)  # neck back
    fig.cubicTo(cx - 32 * s, base - 108 * s, cx - 30 * s, base - 140 * s, cx - 4 * s, base - 146 * s)  # head back
    fig.cubicTo(cx + 18 * s, base - 150 * s, cx + 30 * s, base - 132 * s, cx + 26 * s, base - 118 * s)  # crown
    fig.cubicTo(cx + 30 * s, base - 112 * s, cx + 33 * s, base - 108 * s, cx + 28 * s, base - 104 * s)  # face
    fig.cubicTo(cx + 26 * s, base - 98 * s, cx + 22 * s, base - 94 * s, cx + 14 * s, base - 92 * s)
    fig.cubicTo(cx + 12 * s, base - 84 * s, cx + 16 * s, base - 72 * s, cx + 36 * s, base - 62 * s)  # chin/neck
    fig.cubicTo(cx + 60 * s, base - 50 * s, cx + 72 * s, base - 30 * s, cx + 76 * s, base)
    fig.closeSubpath()
    hair = QPainterPath()
    hair.moveTo(cx - 4 * s, base - 146 * s)
    hair.cubicTo(cx - 50 * s, base - 140 * s, cx - 60 * s, base - 100 * s, cx - 86 * s, base - 50 * s)
    hair.cubicTo(cx - 96 * s, base - 30 * s, cx - 112 * s, base - 18 * s, cx - 128 * s, base - 14 * s)
    hair.cubicTo(cx - 92 * s, base - 30 * s, cx - 60 * s, base - 60 * s, cx - 30 * s, base - 96 * s)
    hair.closeSubpath()
    p.fillPath(hair, QColor("#05061a"))
    p.fillPath(fig, QColor("#05061a"))
    rim = QPen(QColor(167, 139, 250, 200), 1.6 * s)
    p.strokePath(fig, rim)
    p.strokePath(hair, QPen(QColor(167, 139, 250, 120), 1.2 * s))
    # fade left edge into background so the title stays readable
    fade = QLinearGradient(0, 0, width * 0.55, 0)
    fade.setColorAt(0, QColor(theme.BG))
    fade.setColorAt(0.65, QColor(6, 10, 24, 200))
    fade.setColorAt(1, QColor(6, 10, 24, 0))
    p.fillRect(pm.rect(), fade)
    bottom = QLinearGradient(0, height * 0.7, 0, height)
    bottom.setColorAt(0, QColor(6, 10, 24, 0))
    bottom.setColorAt(1, QColor(6, 10, 24, 160))
    p.fillRect(pm.rect(), bottom)
    p.end()
    return pm


def gpu_illustration(width: int, height: int, dpr: float = 2.0, accent: str = theme.GREEN) -> QPixmap:
    """Graphics-card artwork from the design package (edges feathered into the card), else :func:`gpu_card`.

    It is a generic illustration: the actual GPU name and specifications are always shown as text."""
    from akeno.ui import assets
    pm = assets.feathered(assets.GPU, width, height, dpr, feather=0.16)
    return pm if not pm.isNull() else gpu_card(width, height, accent)


def gpu_card(width: int, height: int, accent: str = theme.GREEN) -> QPixmap:
    """A stylised dual-fan graphics card."""
    pm = QPixmap(width * 2, height * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = _painter(pm)
    w, h = width, height
    body = QRectF(w * 0.04, h * 0.18, w * 0.92, h * 0.62)
    g = QLinearGradient(body.topLeft(), body.bottomRight())
    g.setColorAt(0, QColor("#3a3f52"))
    g.setColorAt(0.5, QColor("#1c1f2b"))
    g.setColorAt(1, QColor("#0e1018"))
    p.setPen(QPen(QColor("#555b72"), 1))
    p.setBrush(g)
    p.drawRoundedRect(body, 10, 10)
    # bracket and PCIe
    p.setBrush(QColor("#8a8fa3"))
    p.drawRect(QRectF(w * 0.0, h * 0.14, w * 0.05, h * 0.74))
    p.setBrush(QColor("#c9a227"))
    p.drawRect(QRectF(w * 0.32, h * 0.80, w * 0.42, h * 0.05))
    for fx in (0.32, 0.70):
        c = QPointF(w * fx, h * 0.49)
        r = min(w * 0.17, h * 0.27)
        rg = QRadialGradient(c, r)
        rg.setColorAt(0, QColor("#2b2f3f"))
        rg.setColorAt(1, QColor("#0b0c12"))
        p.setBrush(rg)
        p.setPen(QPen(QColor("#4b5066"), 1.5))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor("#6b7190"), 1.2))
        for i in range(9):
            ang = i * 2 * math.pi / 9
            path = QPainterPath(c)
            path.quadTo(c + QPointF(math.cos(ang) * r * 0.6, math.sin(ang) * r * 0.6),
                        c + QPointF(math.cos(ang + 0.6) * r * 0.92, math.sin(ang + 0.6) * r * 0.92))
            p.drawPath(path)
        p.setBrush(QColor("#1a1d29"))
        p.drawEllipse(c, r * 0.22, r * 0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(QRectF(w * 0.08, h * 0.22, w * 0.18, h * 0.025), 2, 2)
    p.end()
    return pm


def cover_fallback(title: str, width: int, height: int) -> QPixmap:
    """Deterministic gradient cover with the game's initials, for games without local artwork."""
    hsh = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16)
    hue = hsh % 360
    pm = QPixmap(width * 2, height * 2)
    pm.setDevicePixelRatio(2)
    p = _painter(pm)
    g = QLinearGradient(0, 0, width, height)
    g.setColorAt(0, QColor.fromHsl(hue, 160, 70))
    g.setColorAt(1, QColor.fromHsl((hue + 60) % 360, 170, 28))
    p.fillRect(QRectF(0, 0, width, height), g)
    rnd = random.Random(hsh)
    for _ in range(14):
        c = QColor(255, 255, 255, rnd.randint(8, 26))
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        x, y, r = rnd.random() * width, rnd.random() * height, rnd.uniform(8, width * 0.35)
        p.drawEllipse(QPointF(x, y), r, r)
    words = [w for w in title.replace(":", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:3]).upper() or "?"
    f = QFont(theme.ui_font(10))
    f.setPixelSize(int(height * 0.32))
    f.setWeight(QFont.Weight.Black)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 230))
    p.drawText(QRectF(0, 0, width, height), Qt.AlignmentFlag.AlignCenter, initials)
    p.end()
    return pm


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """Application icon: the AKENO 'A' (design package SVG) on a dark rounded tile."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = _painter(pm)
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#101d38"))
    bg.setColorAt(1, QColor(theme.BG))
    p.setBrush(bg)
    p.setPen(QPen(QColor(theme.VIOLET), size * 0.02))
    p.drawRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), size * 0.2, size * 0.2)
    mark = logo_mark(int(size * 0.66))
    p.drawPixmap(QRectF(size * 0.17, size * 0.15, size * 0.66, size * 0.66), mark, QRectF(mark.rect()))
    p.end()
    return pm
