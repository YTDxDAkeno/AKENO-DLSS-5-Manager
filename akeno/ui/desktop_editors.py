"""Visual editors of the DESKTOP pages: the dock preview and pinned list, the widget layout canvas and widget
previews.

* :class:`DockPreview` draws the dock as it will look (style, size, transparency, edge) on the chosen monitor's
  current wallpaper, with the real program icons.
* :class:`PinnedList` holds the pinned items as large icons; drag them into a new order, drop programs, shortcuts
  or folders from Explorer on it.
* :class:`WidgetCanvas` shows a monitor with its wallpaper and taskbar area and every widget on it, drawn by the
  real widget code (:func:`render_widget`); drag to move, drag the corner to resize, click to select.

Widget previews come from the same panels the desktop runtime shows, filled by one real sensor sample: a value the
PC does not report shows as "—" here too. Nothing is invented for a preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileIconProvider, QListView, QListWidget, QListWidgetItem,
                               QSizePolicy, QStyle, QStyledItemDelegate, QWidget)

from akeno.desktop import dock as dockmod
from akeno.desktop import widgets as dwidgets
from akeno.i18n import t
from akeno.ui import icons, theme
from akeno.ui.desktop_media import rounded


def item_icon(item: dict, size: int) -> QPixmap:
    provider = QFileIconProvider()
    if item.get("type") == "group":
        icon = provider.icon(QFileIconProvider.IconType.Folder)
    else:
        icon = provider.icon(QFileInfo(item.get("path", "")))
    pm = icon.pixmap(size, size) if not icon.isNull() else QPixmap()
    return pm if not pm.isNull() else icons.pixmap("folder" if item.get("type") == "group" else "file",
                                                   theme.TEXT_DIM, size)


def _background(p: QPainter, r: QRectF, pm: QPixmap | None) -> None:
    if pm is not None and not pm.isNull():
        rounded(p, r, 10, pm)
    else:
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor("#1A1236"))
        g.setColorAt(1, QColor("#08182C"))
        p.fillPath(path, g)


class DockPreview(QWidget):
    def __init__(self, wallpaper_for: Callable[[int], QPixmap | None] = lambda i: None, parent=None):
        super().__init__(parent)
        self.cfg: dict = {}
        self.running: set[str] = set()
        self.wallpaper_for = wallpaper_for
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(t("Dock preview"))

    def heightForWidth(self, w: int) -> int:  # noqa: N802 (Qt naming)
        return max(240, round(w * 9 / 16))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setFixedHeight(self.heightForWidth(self.width()))

    def set_config(self, cfg: dict, running: set[str] | None = None) -> None:
        self.cfg = dict(cfg)
        if running is not None:
            self.running = running
        self.update()

    def _screen(self):
        screens = QGuiApplication.screens()
        index = int(self.cfg.get("screen", 0))
        return screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        screen = self._screen()
        geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        avail = screen.availableGeometry() if screen else geo
        area = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        ratio = geo.width() / max(1, geo.height())
        w = min(area.width(), area.height() * ratio)
        h = w / ratio
        mon = QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)
        _background(p, mon, self.wallpaper_for(int(self.cfg.get("screen", 0))))
        scale = w / max(1, geo.width())
        # the Windows taskbar stays where it is: the dock is an extra bar
        task = QRectF(mon.x() + (avail.x() - geo.x()) * scale, mon.y() + (avail.y() - geo.y()) * scale,
                      avail.width() * scale, avail.height() * scale)
        p.fillRect(QRectF(mon.x(), task.bottom(), mon.width(), mon.bottom() - task.bottom()), QColor(0, 0, 0, 110))
        items = self.cfg.get("items", [])
        if not self.cfg.get("enabled"):
            p.fillRect(mon, QColor(0, 0, 0, 90))
        icon = max(10, round(int(self.cfg.get("icon_size", 48)) * scale * 1.6))     # a little larger to read
        vertical = self.cfg.get("edge") in ("left", "right") and not self.cfg.get("floating")
        labels = bool(self.cfg.get("labels")) and not vertical
        cell = icon + max(6, icon // 3)
        n = max(1, len(items)) + 1                                                   # the "+" button
        pad = max(4, icon // 4)
        length = n * cell + 2 * pad
        thick = cell + 2 * pad + (12 if labels else 0)
        edge = self.cfg.get("edge", "bottom")
        gap = 6
        if vertical:
            bw, bh = thick, length
            x = task.left() + gap if edge == "left" else task.right() - bw - gap
            y = task.center().y() - bh / 2
        else:
            bw, bh = length, thick
            x = task.center().x() - bw / 2
            y = task.top() + gap if edge == "top" and not self.cfg.get("floating") else task.bottom() - bh - gap
            if self.cfg.get("floating"):
                y -= task.height() * 0.12
        bar = QRectF(x, y, bw, bh)
        p.setOpacity(max(0.3, min(1.0, int(self.cfg.get("opacity", 92)) / 100)))
        dockmod.paint_background(p, bar.toRect(), self.cfg.get("style", "glass"), radius=max(6, thick * 0.32))
        provider_items = items or []
        for i, item in enumerate(provider_items + [None]):
            cx = bar.x() + pad + i * cell + cell / 2 if not vertical else bar.center().x()
            cy = bar.y() + pad + cell / 2 if not vertical else bar.y() + pad + i * cell + cell / 2
            r = QRectF(cx - icon / 2, cy - icon / 2, icon, icon)
            if item is None:
                p.setPen(QColor("#C9D2EE"))
                p.setFont(theme.ui_font(max(7.0, icon / 2.2)))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "+")
                continue
            p.drawPixmap(r.toRect(), item_icon(item, icon * 2))
            target = self._target(item)
            if target and target in self.running:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#8E42FF"))
                p.drawEllipse(QPointF(cx, r.bottom() + max(2, pad / 2)), 2.2, 2.2)
            if labels:
                p.setPen(QColor(theme.TEXT))
                p.setFont(theme.ui_font(theme.FONT_OVERLINE * 0.9))
                name = item.get("name") or Path(item.get("path", "")).stem
                p.drawText(QRectF(cx - cell / 2, r.bottom() + 2, cell, 12), Qt.AlignmentFlag.AlignCenter,
                           QFontMetrics(p.font()).elidedText(name, Qt.TextElideMode.ElideRight, int(cell)))
        p.setOpacity(1.0)
        if self.cfg.get("autohide") and not self.cfg.get("floating"):
            p.setPen(QPen(QColor(theme.ACCENT_TEXT), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(bar.adjusted(-3, -3, 3, 3), 8, 8)
        if not items:
            p.setPen(QColor(theme.TEXT_DIM))
            p.setFont(theme.ui_font(theme.FONT_SMALL))
            p.drawText(mon.adjusted(0, 0, 0, -mon.height() * 0.3), Qt.AlignmentFlag.AlignCenter,
                       t("Drop programs, shortcuts or folders below to pin them."))

    @staticmethod
    def _target(item: dict) -> str | None:
        import os
        path = item.get("path", "")
        if path.lower().endswith(".lnk"):
            path = dockmod.lnk_target(path) or ""
        return os.path.normcase(path) if path.lower().endswith(".exe") else None


class _PinDelegate(QStyledItemDelegate):
    """Icon above its name, centred, with hover and selection like the gallery cards."""

    def paint(self, p: QPainter, option, index) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor(theme.SURFACE_3 if selected else theme.SURFACE_2))
            if selected:
                p.setPen(QPen(QColor(theme.ACCENT), 1.5))
                p.drawPath(path)
        pm = index.data(Qt.ItemDataRole.DecorationRole)
        size = 40
        if isinstance(pm, QPixmap) and not pm.isNull():
            p.drawPixmap(QRect(int(r.center().x() - size / 2), int(r.y() + 10), size, size), pm)
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.ui_font(theme.FONT_SMALL))
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        p.drawText(QRectF(r.x() + 4, r.y() + 14 + size, r.width() - 8, 20), Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(p.font()).elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 8)))
        p.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(96, 92)


class PinnedList(QListWidget):
    """Pinned dock items: drag to reorder, drop files and folders from Explorer to pin them."""

    reordered = Signal(list)          # the items' previous indexes in their new order
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setGridSize(QSize(100, 96))
        self.setUniformItemSizes(True)
        self.setItemDelegate(_PinDelegate(self))
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(t("Pinned"))
        self._fit()

    def _fit(self) -> None:
        """As tall as its rows (no empty box below a few icons)."""
        per_row = max(1, (self.viewport().width() or 600) // self.gridSize().width())
        rows = max(1, -(-max(1, self.count()) // per_row))
        self.setFixedHeight(rows * self.gridSize().height() + 14)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def set_items(self, items: list[dict]) -> None:
        self.clear()
        for i, item in enumerate(items):
            name = item.get("name") or Path(item.get("path", "")).stem
            if item.get("type") == "group":
                name = t("{name} ({n})", name=name, n=len(item.get("items", [])))
            entry = QListWidgetItem(name)
            entry.setData(Qt.ItemDataRole.DecorationRole, item_icon(item, 80))
            entry.setData(Qt.ItemDataRole.UserRole, i)
            entry.setToolTip(item.get("path", "") or name)
            self.addItem(entry)
        self._fit()

    def dragEnterEvent(self, e):
        if e.source() is self or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.source() is self:
            super().dragMoveEvent(e)
        elif e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.source() is not self:
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                e.acceptProposedAction()
                self.files_dropped.emit(paths)
            return
        target = self.indexAt(e.position().toPoint()).row()
        source = self.currentRow()
        if source < 0:
            return
        order = [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        moved = order.pop(source)
        order.insert(target if target >= 0 else len(order), moved)
        e.setDropAction(Qt.DropAction.IgnoreAction)          # the page rebuilds the list from the new order
        e.accept()
        self.reordered.emit(order)


def sample_context():
    """A widget context with one real sensor sample (the previews show what this PC reports, or "—")."""
    from akeno.desktop import sensors
    sampler = sensors.Sampler()

    class _Silent:                        # previews never open the playback meter
        def peak(self):
            return None

        def values(self):
            return []
    return dwidgets.Context(sampler=sampler, meter=_Silent())


def render_widget(spec: dict, ctx, size: QSize | None = None) -> QPixmap:
    """The widget as the desktop runtime draws it (real panel code), as a picture."""
    spec = dict(spec, options=dict(spec.get("options") or {}))
    widget = dwidgets.DesktopWidget(spec, ctx, lambda: True, lambda s: None, lambda i: None, lambda: None)
    widget.timer.stop()
    widget.resize(size or QSize(int(spec.get("w", 240)), int(spec.get("h", 130))))
    widget.panel.refresh()
    pm = widget.grab()
    widget.deleteLater()
    return pm


class WidgetCanvas(QWidget):
    """One monitor, its wallpaper, taskbar area and the widgets on it. Drag, resize, select."""

    selected = Signal(str)                # widget id
    changed = Signal(dict)                # a moved or resized widget spec

    GRIP = 10
    SNAP = 8

    def __init__(self, preview: Callable[[dict], QPixmap | None], wallpaper_for: Callable[[int], QPixmap | None],
                 parent=None):
        super().__init__(parent)
        self.preview = preview
        self.wallpaper_for = wallpaper_for
        self.specs: list[dict] = []
        self.screen_index = 0
        self.current = ""
        self._drag: tuple[str, QPointF, dict, str] | None = None    # id, press point, spec at press, move|resize
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(t("Widget layout"))

    def set_widgets(self, specs: list[dict], screen_index: int, current: str = "") -> None:
        self.specs = specs
        self.screen_index = screen_index
        self.current = current
        self.update()

    def _geometry(self):
        screens = QGuiApplication.screens()
        screen = screens[self.screen_index] if 0 <= self.screen_index < len(screens) else \
            QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        avail = screen.availableGeometry() if screen else geo
        area = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        ratio = geo.width() / max(1, geo.height())
        w = min(area.width(), area.height() * ratio)
        h = w / ratio
        mon = QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)
        return geo, avail, mon, w / max(1, geo.width())

    def _rect(self, spec: dict) -> QRectF:
        geo, avail, mon, scale = self._geometry()
        x = avail.x() - geo.x() + int(spec.get("x", 40))
        y = avail.y() - geo.y() + int(spec.get("y", 40))
        return QRectF(mon.x() + x * scale, mon.y() + y * scale, int(spec.get("w", 240)) * scale,
                      int(spec.get("h", 130)) * scale)

    def on_screen(self) -> list[dict]:
        return [s for s in self.specs if int(s.get("screen", 0)) == self.screen_index]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        geo, avail, mon, scale = self._geometry()
        _background(p, mon, self.wallpaper_for(self.screen_index))
        bottom = mon.y() + (avail.bottom() + 1 - geo.y()) * scale
        p.fillRect(QRectF(mon.x(), bottom, mon.width(), mon.bottom() - bottom), QColor(0, 0, 0, 120))
        for spec in self.on_screen():
            r = self._rect(spec)
            pm = self.preview(spec)
            p.setOpacity(max(0.3, min(1.0, int(spec.get("opacity", 90)) / 100)))
            if pm is not None and not pm.isNull():
                p.drawPixmap(r.toRect(), pm)
            else:
                dwidgets.paint_widget_background(p, r.toRect(), spec.get("style", "dark"),
                                                 QColor(spec.get("color") or dwidgets.ACCENT.name()), 8)
            p.setOpacity(1.0)
            if spec.get("id") == self.current:
                p.setPen(QPen(QColor(theme.ACCENT), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 8, 8)
                grip = QRectF(r.right() - self.GRIP / 2, r.bottom() - self.GRIP / 2, self.GRIP, self.GRIP)
                p.setBrush(QColor(theme.ACCENT))
                p.setPen(QColor("#FFFFFF"))
                p.drawRoundedRect(grip, 3, 3)
        if not self.on_screen():
            p.setPen(QColor(theme.TEXT_DIM))
            p.setFont(theme.ui_font(theme.FONT_BODY))
            p.drawText(mon, Qt.AlignmentFlag.AlignCenter, t("No widgets on this monitor yet. Add one below."))

    def _hit(self, pos: QPointF) -> tuple[dict | None, str]:
        for spec in reversed(self.on_screen()):
            r = self._rect(spec)
            if spec.get("id") == self.current and QRectF(r.right() - self.GRIP, r.bottom() - self.GRIP,
                                                         2 * self.GRIP, 2 * self.GRIP).contains(pos):
                return spec, "resize"
            if r.contains(pos):
                return spec, "move"
        return None, ""

    def mousePressEvent(self, e):
        spec, mode = self._hit(e.position())
        if spec is None:
            return
        self.current = spec["id"]
        self._drag = (spec["id"], e.position(), dict(spec), mode)
        self.selected.emit(spec["id"])
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            spec, mode = self._hit(e.position())
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if mode == "resize" else
                           Qt.CursorShape.OpenHandCursor if mode == "move" else Qt.CursorShape.ArrowCursor)
            return
        wid, start, before, mode = self._drag
        spec = next((s for s in self.specs if s.get("id") == wid), None)
        if spec is None:
            return
        geo, avail, mon, scale = self._geometry()
        dx = (e.position().x() - start.x()) / scale
        dy = (e.position().y() - start.y()) / scale

        def snap(v: float) -> int:
            return int(round(v / self.SNAP) * self.SNAP)
        if mode == "move":
            spec["x"] = max(0, min(snap(int(before.get("x", 40)) + dx), avail.width() - 60))
            spec["y"] = max(0, min(snap(int(before.get("y", 40)) + dy), avail.height() - 40))
        else:
            spec["w"] = max(140, min(snap(int(before.get("w", 240)) + dx), avail.width()))
            spec["h"] = max(80, min(snap(int(before.get("h", 130)) + dy), avail.height()))
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            wid = self._drag[0]
            self._drag = None
            spec = next((s for s in self.specs if s.get("id") == wid), None)
            if spec is not None:
                self.changed.emit(spec)


def compose_preview(cfg: dict, monitor: str, wallpaper: QPixmap | None, size: QSize = QSize(640, 360)) -> QPixmap:
    """A theme's picture: the monitor's wallpaper with the widgets and the dock drawn by their real code."""
    screens = QGuiApplication.screens()
    names = [s.name() for s in screens]
    index = names.index(monitor) if monitor in names else 0
    screen = screens[index] if screens else None
    geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
    avail = screen.availableGeometry() if screen else geo
    base = QPixmap(size)
    base.fill(QColor("#0B1020"))
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if wallpaper is not None and not wallpaper.isNull():
        rounded(p, QRectF(0, 0, size.width(), size.height()), 0, wallpaper)
    else:
        _background(p, QRectF(0, 0, size.width(), size.height()), None)
    scale = size.width() / max(1, geo.width())
    widgets = [w for w in cfg.get("widgets", []) if int(w.get("screen", 0)) == index] \
        if cfg.get("widgets_enabled", True) else []
    if widgets:
        ctx = sample_context()
        ctx.sampler.sample()
        for spec in widgets:
            x = (avail.x() - geo.x() + int(spec.get("x", 40))) * scale
            y = (avail.y() - geo.y() + int(spec.get("y", 40))) * scale
            r = QRectF(x, y, int(spec.get("w", 240)) * scale, int(spec.get("h", 130)) * scale)
            p.setOpacity(max(0.3, min(1.0, int(spec.get("opacity", 90)) / 100)))
            try:
                p.drawPixmap(r.toRect(), render_widget(spec, ctx))
            except Exception:                     # a picture never fails because of one widget
                pass
        p.setOpacity(1.0)
    p.end()
    dock = cfg.get("dock", {})
    if not dock.get("enabled") or not dock.get("items"):
        return base
    preview = DockPreview(lambda _i: base)
    preview.set_config(dict(dock, screen=index))
    preview.resize(size)
    shot = preview.grab()
    preview.deleteLater()
    return shot
