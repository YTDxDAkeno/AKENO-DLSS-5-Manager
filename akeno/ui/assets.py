"""Bundled AKENO design artwork (``akeno/data/assets``): loading, High-DPI scaling and integrity checks.

The artwork is decoration only. Every title, value, badge and button is a real Qt widget drawn on top of
it. Files are resolved through :func:`akeno.paths.resource_dir`, so the same code works from source and
from the PyInstaller bundle (``_MEIPASS/akeno/data/assets``). A missing or unreadable file never breaks
the UI: callers get a null pixmap and fall back to the procedural artwork in :mod:`akeno.ui.art`.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer

from akeno.paths import resource_dir

log = logging.getLogger(__name__)

HEADER = "backgrounds/header_anime_1920x300.png"
RECOMMENDATION = "backgrounds/recommendation_neon_1600x420.png"
SIDEBAR_LANDSCAPE = "backgrounds/sidebar_landscape_840x510.png"
GPU = "illustrations/gpu_illustration_824x648.png"
WORDMARK = "branding/akeno_wordmark.svg"
LOGO = "branding/akeno_icon.svg"
LOGO_PNG = "branding/akeno_icon_512.png"
ICON_STROKE = "#A0ACD3"            # stroke colour used by every SVG in assets/icons

# Every file the UI expects. The packaged smoke test and the unit tests check this list.
REQUIRED = (HEADER, RECOMMENDATION, SIDEBAR_LANDSCAPE, GPU, WORDMARK, LOGO, LOGO_PNG) + tuple(
    f"icons/{n}.svg" for n in ("home", "cpu", "gamepad", "sliders", "chart", "shield", "database", "settings",
                               "rotate", "folder", "download", "check"))


def assets_dir() -> Path:
    return resource_dir() / "assets"


def path(rel: str) -> Path:
    return assets_dir() / rel


def exists(rel: str) -> bool:
    return path(rel).is_file()


@lru_cache(maxsize=64)
def image(rel: str) -> QImage:
    """Decoded source image (cached). Null QImage when missing or unreadable."""
    p = path(rel)
    img = QImage(str(p)) if p.is_file() else QImage()
    if img.isNull():
        log.warning("design asset missing or unreadable: %s", p)
    return img


@lru_cache(maxsize=64)
def svg_bytes(rel: str) -> bytes:
    p = path(rel)
    try:
        return p.read_bytes()
    except OSError:
        log.warning("design asset missing: %s", p)
        return b""


def svg_renderer(rel: str, recolor: str | None = None, stroke_width: float | None = None) -> QSvgRenderer | None:
    data = svg_bytes(rel)
    if not data:
        return None
    if recolor or stroke_width:
        text = data.decode("utf-8", errors="replace")
        if recolor:
            text = text.replace(ICON_STROKE, recolor).replace(ICON_STROKE.lower(), recolor)
        if stroke_width:
            text = text.replace('stroke-width="1.8"', f'stroke-width="{stroke_width}"')
        data = text.encode("utf-8")
    r = QSvgRenderer(QByteArray(data))
    return r if r.isValid() else None


def svg_pixmap(rel: str, width: int, height: int, dpr: float = 2.0, recolor: str | None = None,
               stroke_width: float | None = None) -> QPixmap:
    """Render an SVG asset crisply at ``width`` x ``height`` logical pixels for a device pixel ratio."""
    r = svg_renderer(rel, recolor, stroke_width)
    pm = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    pm.fill(Qt.GlobalColor.transparent)
    if r is None:
        return QPixmap()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def svg_aspect(rel: str) -> float:
    r = svg_renderer(rel)
    if r is None:
        return 1.0
    s = r.defaultSize()
    return s.width() / s.height() if s.height() else 1.0


def cover(rel: str, width: int, height: int, dpr: float = 1.0, h_align: float = 1.0, v_align: float = 0.5,
          max_upscale: float | None = None) -> QPixmap:
    """CSS ``background-size: cover`` for a bitmap asset.

    The image keeps its aspect ratio, fills ``width`` x ``height`` logical pixels and is cropped; ``h_align``
    and ``v_align`` (0 = left/top, 1 = right/bottom) choose which part stays visible. ``max_upscale`` limits
    enlargement (the extracted artwork has only its original detail); the remaining area stays transparent
    so the caller's own background/gradient shows through.
    """
    src = image(rel)
    if src.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    tw, th = max(1, round(width * dpr)), max(1, round(height * dpr))
    scale = max(tw / src.width(), th / src.height())
    if max_upscale is not None:
        scale = min(scale, max_upscale * dpr)
    sw, sh = max(1, round(src.width() * scale)), max(1, round(src.height() * scale))
    scaled = src.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    x = round((tw - sw) * h_align)
    y = round((th - sh) * v_align)
    p.drawImage(x, y, scaled)
    p.end()
    pm = QPixmap.fromImage(out)
    pm.setDevicePixelRatio(dpr)
    return pm


def fit(rel: str, width: int, height: int, dpr: float = 1.0) -> QPixmap:
    """Scale a bitmap asset to fit inside the box (aspect ratio kept, nothing cropped)."""
    src = image(rel)
    if src.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    scaled = src.scaled(max(1, round(width * dpr)), max(1, round(height * dpr)), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    pm = QPixmap.fromImage(scaled)
    pm.setDevicePixelRatio(dpr)
    return pm


def feathered(rel: str, width: int, height: int, dpr: float = 1.0, feather: float = 0.18) -> QPixmap:
    """Fit a bitmap into the box and fade its edges to transparent, so an opaque illustration (the GPU
    artwork has a dark backdrop) blends into whatever card it sits on."""
    src = fit(rel, width, height, dpr)
    if src.isNull():
        return src
    img = src.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    w, h = img.width(), img.height()
    mask = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    mp = QPainter(mask)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing)
    rg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.62)
    rg.setColorAt(0.0, QColor(0, 0, 0, 255))
    rg.setColorAt(1.0 - feather, QColor(0, 0, 0, 255))
    rg.setColorAt(1.0, QColor(0, 0, 0, 0))
    mp.fillRect(QRectF(0, 0, w, h), rg)
    for horizontal in (True, False):
        size = (w if horizontal else h) * feather
        for start in (True, False):
            if horizontal:
                g = QLinearGradient(0 if start else w, 0, size if start else w - size, 0)
            else:
                g = QLinearGradient(0, 0 if start else h, 0, size if start else h - size)
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, 255))
            mp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            mp.fillRect(QRectF(0, 0, w, h), g)
    mp.end()
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def rounded(pm: QPixmap, radius: float) -> QPixmap:
    """Clip a pixmap to a rounded rectangle (radius in logical pixels)."""
    if pm.isNull():
        return pm
    dpr = pm.devicePixelRatio()
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path_ = QPainterPath()
    path_.addRoundedRect(QRectF(0, 0, pm.width(), pm.height()), radius * dpr, radius * dpr)
    p.setClipPath(path_)
    src = QPixmap(pm)
    src.setDevicePixelRatio(1.0)
    p.drawPixmap(0, 0, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def verify_all() -> dict[str, str]:
    """Check every required asset decodes. Returns {relative path: problem}; empty when all are fine."""
    problems: dict[str, str] = {}
    for rel in REQUIRED:
        p = path(rel)
        if not p.is_file():
            problems[rel] = "missing"
            continue
        if rel.endswith(".svg"):
            if svg_renderer(rel) is None:
                problems[rel] = "invalid SVG"
        elif image(rel).isNull():
            problems[rel] = "cannot decode"
    return problems


def summary() -> str:
    """One line for logs and the smoke test."""
    bad = verify_all()
    where = assets_dir()
    if bad:
        return f"design assets: {len(REQUIRED) - len(bad)}/{len(REQUIRED)} OK in {where}; problems: {bad}"
    return f"design assets: {len(REQUIRED)}/{len(REQUIRED)} OK in {where}"


def logical_size(pm: QPixmap) -> QSize:
    dpr = pm.devicePixelRatio() or 1.0
    return QSize(round(pm.width() / dpr), round(pm.height() / dpr))
