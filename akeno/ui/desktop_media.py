"""Media pieces of the DESKTOP pages: the wallpaper gallery, the monitor map, the preview player.

* :class:`Gallery` is a list view in icon mode over :class:`GalleryModel`; :class:`CardDelegate` paints each
  wallpaper as a card: its real thumbnail (cropped 16:9), title, resolution · format · length, a favourite star
  and the monitors it runs on. Cards only ever show cached still frames; nothing plays in the grid.
* :class:`PreviewPlayer` shows the selected wallpaper: a video plays (muted until asked) with play/pause, sound,
  loop, fit and fullscreen; a GIF animates; a picture is shown as it will look.
* :class:`MonitorMap` draws the monitors as Windows arranges them, each with its current wallpaper; clicking one
  chooses where "Apply" goes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient, QMovie, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QLabel, QListView, QMenu, QPushButton,
                               QSizePolicy, QStackedLayout, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

from akeno.desktop import thumbs as thumbsmod
from akeno.desktop.library import Item
from akeno.i18n import msg, t
from akeno.ui import icons, theme

KIND_BADGE = {"image": msg("Picture"), "gif": msg("GIF"), "video": msg("Video"), "web": msg("Web")}
KIND_ICON = {"image": "image", "gif": "film", "video": "film", "web": "globe"}
CARD_MIN = 236                         # narrowest card; wider windows get more columns, never stretched cards
TEXT_H = 58                            # title and facts below the picture
MARGIN = 8

_service: thumbsmod.ThumbnailService | None = None


def thumbnails() -> thumbsmod.ThumbnailService:
    """One thumbnail service for the suite (its cache and its single video decoder are shared)."""
    global _service
    if _service is None:
        _service = thumbsmod.ThumbnailService()
    return _service


def duration_text(seconds: float) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def facts(item: Item) -> str:
    """"3840×2160 · MP4 · 0:24"."""
    return " · ".join(x for x in (item.resolution, item.format, duration_text(item.duration)) if x)


def rounded(painter: QPainter, rect: QRectF, radius: float, pixmap: QPixmap) -> None:
    """A pixmap filling ``rect`` (cropped from its centre) with rounded corners."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(path)
    src = pixmap.rect()
    target_ratio = rect.width() / max(1.0, rect.height())
    if src.width() / max(1, src.height()) > target_ratio:
        w = round(src.height() * target_ratio)
        src = QRect((src.width() - w) // 2, 0, w, src.height())
    else:
        h = round(src.width() / target_ratio)
        src = QRect(0, (src.height() - h) // 2, src.width(), h)
    painter.drawPixmap(rect.toRect(), pixmap, src)
    painter.restore()


def placeholder(painter: QPainter, rect: QRectF, radius: float, kind: str, text: str = "") -> None:
    """No thumbnail (yet): the AKENO gradient with the kind's icon."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor("#17112E"))
    grad.setColorAt(1, QColor("#0B1A2E"))
    painter.fillPath(path, grad)
    size = int(min(rect.width(), rect.height()) * 0.28)
    pm = icons.pixmap(KIND_ICON.get(kind, "image"), theme.TEXT_MUTED, max(16, size))
    painter.drawPixmap(int(rect.center().x() - size / 2), int(rect.center().y() - size / 2 - (8 if text else 0)), pm)
    if text:
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.setFont(theme.ui_font(theme.FONT_SMALL))
        painter.drawText(rect.adjusted(8, size, -8, -6), Qt.AlignmentFlag.AlignCenter, text)


def badge(painter: QPainter, x: float, y: float, text: str, fill: QColor, color: QColor = QColor("#FFFFFF"),
          right: bool = False) -> QRectF:
    font = theme.ui_font(theme.FONT_OVERLINE, QFont.Weight.Bold)
    painter.setFont(font)
    w = QFontMetrics(font).horizontalAdvance(text) + 14
    r = QRectF(x - w if right else x, y, w, 20)
    path = QPainterPath()
    path.addRoundedRect(r, 6, 6)
    painter.fillPath(path, fill)
    painter.setPen(color)
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    return r


class GalleryModel(QAbstractListModel):
    ItemRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[Item] = []
        self.active: dict[str, list[int]] = {}      # wallpaper path -> monitor numbers it runs on
        self.pixmaps: dict[str, QPixmap] = {}
        self.failed: set[str] = set()
        svc = thumbnails()
        svc.ready.connect(self._ready)
        svc.failed.connect(self._failed)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt naming)
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{item.title}\n{facts(item)}\n{item.path}"
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{item.title}, {t(KIND_BADGE.get(item.kind, item.kind))}"
        if role == self.ItemRole:
            return item
        return None

    def set_items(self, items: list[Item], active: dict[str, list[int]] | None = None) -> None:
        self.beginResetModel()
        self.items = list(items)
        if active is not None:
            self.active = active
        self.endResetModel()

    def row_of(self, item_id: str) -> int:
        return next((i for i, it in enumerate(self.items) if it.id == item_id), -1)

    def pixmap(self, item: Item) -> QPixmap | None:
        pm = self.pixmaps.get(item.path)
        if pm is not None or item.path in self.failed or item.missing:
            return pm
        image = thumbnails().request(item.path, item.kind, item.preview)
        if image is not None:
            pm = QPixmap.fromImage(image)
            self.pixmaps[item.path] = pm
        return pm

    def _ready(self, path: str, image: QImage) -> None:
        self.pixmaps[path] = QPixmap.fromImage(image)
        self._changed(path)

    def _failed(self, path: str, _error: str) -> None:
        self.failed.add(path)
        self._changed(path)

    def _changed(self, path: str) -> None:
        for row, item in enumerate(self.items):
            if item.path == path:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view: "Gallery"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return self.view.gridSize()

    def paint(self, painter: QPainter, option, index) -> None:
        item: Item = index.data(GalleryModel.ItemRole)
        if item is None:
            return
        model: GalleryModel = index.model()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = QRectF(option.rect).adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        path = QPainterPath()
        path.addRoundedRect(card, 12, 12)
        painter.fillPath(path, QColor(theme.SURFACE_2 if hover or selected else theme.SURFACE))
        thumb = QRectF(card.x() + 6, card.y() + 6, card.width() - 12, (card.width() - 12) * 9 / 16)
        pm = model.pixmap(item)
        if item.missing:
            placeholder(painter, thumb, 9, item.kind, t("File missing"))
        elif pm is not None:
            rounded(painter, thumb, 9, pm)
        else:
            placeholder(painter, thumb, 9, item.kind,
                        t("No preview") if item.path in model.failed else "")
        # overlays: kind, favourite, where it runs
        badge(painter, thumb.x() + 8, thumb.y() + 8, t(KIND_BADGE.get(item.kind, item.kind)).upper(),
              QColor(8, 12, 21, 190))
        if item.favorite:
            painter.drawPixmap(int(thumb.right() - 30), int(thumb.y() + 8), icons.pixmap("star", "#FFD25A", 20))
        monitors = model.active.get(item.path)
        if monitors:
            text = t("Active") + " · " + ", ".join(str(m) for m in monitors)
            badge(painter, thumb.x() + 8, thumb.bottom() - 28, text, QColor(theme.ACCENT_STRONG))
        if item.duration and item.kind in ("video", "gif"):
            badge(painter, thumb.right() - 8, thumb.bottom() - 28, duration_text(item.duration),
                  QColor(8, 12, 21, 190), right=True)
        # text
        text = QRectF(thumb.x() + 2, thumb.bottom() + 8, thumb.width() - 4, 22)
        painter.setFont(theme.ui_font(theme.FONT_BODY, QFont.Weight.DemiBold))
        painter.setPen(QColor(theme.RED if item.missing else theme.TEXT))
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(painter.font()).elidedText(item.title, Qt.TextElideMode.ElideRight,
                                                                 int(text.width())))
        painter.setFont(theme.ui_font(theme.FONT_SMALL))
        painter.setPen(QColor(theme.TEXT_MUTED))
        sub = facts(item) or t(KIND_BADGE.get(item.kind, item.kind))
        painter.drawText(text.translated(0, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(painter.font()).elidedText(sub, Qt.TextElideMode.ElideRight, int(text.width())))
        # selection and hover rings
        if selected or hover:
            pen = QPen(QColor(theme.ACCENT if selected else theme.BORDER_BTN), 2 if selected else 1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), 12, 12)
        painter.restore()


class Gallery(QListView):
    """The wallpaper grid: responsive columns, keyboard and mouse selection, drop files to import."""

    dropped = Signal(list)              # local paths
    activated_item = Signal(object)     # Item (double click / Enter): apply
    menu_requested = Signal(object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = GalleryModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(CardDelegate(self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setStyleSheet("QListView { background: transparent; border: none; }")
        self.setAccessibleName(t("Wallpaper library"))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda idx: self.activated_item.emit(idx.data(GalleryModel.ItemRole)))
        self._columns()

    def _columns(self) -> None:
        width = max(CARD_MIN, self.viewport().width() - 2)
        cols = max(1, width // CARD_MIN)
        card_w = width // cols
        thumb_h = round((card_w - 2 * MARGIN - 12) * 9 / 16)
        size = QSize(card_w, thumb_h + 2 * MARGIN + 12 + TEXT_H)
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._columns()

    def current_item(self) -> Item | None:
        idx = self.currentIndex()
        return idx.data(GalleryModel.ItemRole) if idx.isValid() else None

    def select(self, item_id: str) -> None:
        row = self.model_.row_of(item_id)
        if row >= 0:
            idx = self.model_.index(row)
            self.setCurrentIndex(idx)
            self.scrollTo(idx)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current_item() is not None:
            self.activated_item.emit(self.current_item())
            return
        super().keyPressEvent(e)

    def _menu(self, pos: QPoint) -> None:
        idx = self.indexAt(pos)
        if idx.isValid():
            self.setCurrentIndex(idx)
            self.menu_requested.emit(idx.data(GalleryModel.ItemRole), self.viewport().mapToGlobal(pos))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.dropped.emit(paths)


class Segmented(QWidget):
    """A row of chips, one checked (filters and simple choices)."""

    changed = Signal(str)

    def __init__(self, choices: dict[str, str], current: str | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in choices.items():
            b = QPushButton(text)
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(text)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.set(current or next(iter(choices), ""))

    def set(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def value(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "")


class MonitorMap(QWidget):
    """The monitors as Windows arranges them; click to choose. Each shows its current wallpaper."""

    changed = Signal()

    def __init__(self, thumb_for: Callable[[str], QPixmap | None] = lambda name: None, parent=None):
        super().__init__(parent)
        self.thumb_for = thumb_for
        self.selected: set[str] = set()
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(t("Monitors"))
        self._rects: list[tuple[str, QRectF]] = []
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _s: self.update())
            app.screenRemoved.connect(lambda _s: self._screens_changed())

    def sizeHint(self) -> QSize:
        return QSize(360, 130)

    def screens(self):
        return QGuiApplication.screens()

    def _screens_changed(self) -> None:
        names = {s.name() for s in self.screens()}
        self.selected &= names
        self.update()
        self.changed.emit()

    def select_all(self) -> None:
        self.selected = {s.name() for s in self.screens()}
        self.update()
        self.changed.emit()

    def set_selected(self, names: set[str]) -> None:
        self.selected = set(names)
        self.update()

    def _layout(self) -> list[tuple[str, QRectF, object]]:
        screens = self.screens()
        if not screens:
            return []
        geos = [s.geometry() for s in screens]
        left = min(g.left() for g in geos)
        top = min(g.top() for g in geos)
        right = max(g.right() + 1 for g in geos)
        bottom = max(g.bottom() + 1 for g in geos)
        area = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        scale = min(area.width() / max(1, right - left), area.height() / max(1, bottom - top))
        ox = area.x() + (area.width() - (right - left) * scale) / 2
        oy = area.y() + (area.height() - (bottom - top) * scale) / 2
        out = []
        for s, g in zip(screens, geos):
            r = QRectF(ox + (g.left() - left) * scale + 3, oy + (g.top() - top) * scale + 3,
                       g.width() * scale - 6, g.height() * scale - 6)
            out.append((s.name(), r, s))
        return out

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._rects = []
        primary = QGuiApplication.primaryScreen()
        for n, (name, r, screen) in enumerate(self._layout(), start=1):
            self._rects.append((name, r))
            on = name in self.selected
            pm = self.thumb_for(name)
            if pm is not None and not pm.isNull():
                rounded(p, r, 6, pm)
                shade = QPainterPath()
                shade.addRoundedRect(r, 6, 6)
                p.fillPath(shade, QColor(0, 0, 0, 70 if on else 130))
            else:
                path = QPainterPath()
                path.addRoundedRect(r, 6, 6)
                p.fillPath(path, QColor(theme.SURFACE_3 if on else theme.INPUT))
            p.setPen(QPen(QColor(theme.ACCENT if on else theme.BORDER_BTN), 2.2 if on else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor("#FFFFFF" if on else theme.TEXT_DIM))
            p.setFont(theme.ui_font(theme.FONT_H2, QFont.Weight.Bold))
            p.drawText(r.adjusted(0, -10, 0, 0), Qt.AlignmentFlag.AlignCenter, str(n))
            dpr = screen.devicePixelRatio()
            res = f"{round(screen.geometry().width() * dpr)}×{round(screen.geometry().height() * dpr)}"
            if screen is primary:
                res += " · " + t("main")
            p.setFont(theme.ui_font(theme.FONT_OVERLINE))
            p.drawText(r.adjusted(2, 18, -2, 0), Qt.AlignmentFlag.AlignCenter, res)

    def mousePressEvent(self, e):
        """A click chooses that monitor; Ctrl/Shift+click adds or removes it (at least one stays chosen)."""
        for name, r in self._rects:
            if r.contains(e.position()):
                if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                    if name not in self.selected:
                        self.selected.add(name)
                    elif len(self.selected) > 1:
                        self.selected.discard(name)
                else:
                    self.selected = {name}
                self.update()
                self.changed.emit()
                return


class _Still(QWidget):
    """A picture filling the preview (fill or fit), drawn with rounded corners."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm: QPixmap | None = None
        self.kind = "image"
        self.fit = "fill"
        self.note = ""

    def set(self, pm: QPixmap | None, kind: str, note: str = "") -> None:
        self.pm, self.kind, self.note = pm, kind, note
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self.pm is None or self.pm.isNull():
            placeholder(p, r, 10, self.kind, self.note)
            return
        if self.fit == "fill":
            rounded(p, r, 10, self.pm)
        else:
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor("#000000"))
            size = self.pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - size.width()) // 2, (self.height() - size.height()) // 2
            p.drawPixmap(QRect(x, y, size.width(), size.height()), self.pm)


class PreviewPlayer(QWidget):
    """The focused preview of one wallpaper (only one plays at a time, and only while it is shown)."""

    fullscreen_requested = Signal()

    def __init__(self, parent=None, controls: bool = True):
        super().__init__(parent)
        self.item: Item | None = None
        self.fit = "fill"
        self.muted = True
        self.loop = True
        self.player = None
        self.audio = None
        self.video_view = None
        self.movie: QMovie | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.stage = QWidget()
        self.stage.setMinimumHeight(160)
        self.stack = QStackedLayout(self.stage)
        self.still = _Still()
        self.stack.addWidget(self.still)
        self.gif = QLabel()
        self.gif.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gif.setStyleSheet("background: #000; border-radius: 10px;")
        self.stack.addWidget(self.gif)
        lay.addWidget(self.stage, 1)
        self.bar = QWidget()
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        self.btn_play = self._tool("pause", t("Pause"), self.toggle_play)
        self.btn_sound = self._tool("volume-off", t("Sound on"), self.toggle_sound)
        self.btn_loop = self._tool("repeat", t("Loop"), self.toggle_loop, checkable=True)
        self.btn_loop.setChecked(True)
        self.btn_fit = self._tool("crop", t("Fit the whole picture"), self.toggle_fit, checkable=True)
        bar.addWidget(self.btn_play)
        bar.addWidget(self.btn_sound)
        bar.addWidget(self.btn_loop)
        bar.addStretch(1)
        self.time = QLabel("")
        self.time.setProperty("role", "muted")
        bar.addWidget(self.time)
        bar.addWidget(self.btn_fit)
        self.btn_full = self._tool("maximize", t("Fullscreen preview"), self.fullscreen_requested.emit)
        bar.addWidget(self.btn_full)
        lay.addWidget(self.bar)
        self.bar.setVisible(controls)
        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._tick)

    def _tool(self, icon_name: str, tip: str, slot, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(icon_name, theme.TEXT, 18))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(34, 30)
        b.setStyleSheet(f"QToolButton {{ border: 1px solid transparent; border-radius: 7px; }}"
                        f"QToolButton:hover {{ background: {theme.SURFACE_2}; border-color: {theme.BORDER_HI}; }}"
                        f"QToolButton:checked {{ background: {theme.SURFACE_3}; border-color: {theme.ACCENT}; }}")
        b.clicked.connect(slot)
        return b

    def heightForWidth(self, w: int) -> int:  # noqa: N802 (Qt naming)
        return round(w * 9 / 16) + (38 if self.bar.isVisible() else 0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    # -- showing ---------------------------------------------------------------------------------------------------
    def show_item(self, item: Item | None, pm: QPixmap | None = None, autoplay: bool = True) -> None:
        self.stop()
        self.item = item
        kind = item.kind if item else "image"
        playable = item is not None and not item.missing
        for b in (self.btn_play, self.btn_sound, self.btn_loop):
            b.setVisible(kind == "video")
        self.btn_play.setVisible(kind in ("video", "gif"))
        self.time.setText("")
        if item is None:
            self.still.set(None, "image")
            self.stack.setCurrentWidget(self.still)
            return
        if not playable:
            self.still.set(None, kind, t("File missing"))
            self.stack.setCurrentWidget(self.still)
            return
        if kind == "image":
            image = QImage(item.path)
            self.still.set(QPixmap.fromImage(image) if not image.isNull() else pm, kind)
            self.stack.setCurrentWidget(self.still)
        elif kind == "gif":
            self.movie = QMovie(item.path, parent=self)
            self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
            self.gif.setMovie(self.movie)
            self._scale_movie()
            self.stack.setCurrentWidget(self.gif)
            if autoplay:
                self.movie.start()
                self._set_play_icon(True)
        elif kind == "video":
            self._video()
            self.player.setSource(QUrl.fromLocalFile(item.path))
            self.stack.setCurrentWidget(self.video_view)
            if autoplay:
                self.player.play()
                self._set_play_icon(True)
                self.clock.start()
        else:                                                          # web: the captured frame or package picture
            self.still.set(pm, kind, t("Live preview after applying") if pm is None else "")
            self.stack.setCurrentWidget(self.still)

    def _video(self) -> None:
        if self.player is not None:
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(self.muted)
        self.audio.setVolume(0.6)
        self.player.setAudioOutput(self.audio)
        self.player.setLoops(QMediaPlayer.Loops.Infinite if self.loop else 1)
        self.video_view = QVideoWidget()
        self.video_view.setStyleSheet("background: #000;")
        self.video_view.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fit == "fill"
                                           else Qt.AspectRatioMode.KeepAspectRatio)
        self.stack.addWidget(self.video_view)
        self.player.setVideoOutput(self.video_view)

    def stop(self) -> None:
        """Nothing keeps decoding once the preview is hidden or another wallpaper is chosen."""
        self.clock.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        if self.movie is not None:
            self.movie.stop()
            self.gif.setMovie(None)
            self.movie.deleteLater()
            self.movie = None

    def playing(self) -> bool:
        if self.player is not None and self.item is not None and self.item.kind == "video":
            from PySide6.QtMultimedia import QMediaPlayer
            return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        return self.movie is not None and self.movie.state() == QMovie.MovieState.Running

    def _set_play_icon(self, playing: bool) -> None:
        self.btn_play.setIcon(icons.icon("pause" if playing else "play", theme.TEXT, 18))
        self.btn_play.setToolTip(t("Pause") if playing else t("Play"))

    def toggle_play(self) -> None:
        if self.item is None:
            return
        if self.item.kind == "video" and self.player is not None:
            if self.playing():
                self.player.pause()
                self.clock.stop()
            else:
                self.player.play()
                self.clock.start()
        elif self.movie is not None:
            self.movie.setPaused(self.movie.state() == QMovie.MovieState.Running)
        self._set_play_icon(self.playing())

    def toggle_sound(self) -> None:
        self.muted = not self.muted
        if self.audio is not None:
            self.audio.setMuted(self.muted)
        self.btn_sound.setIcon(icons.icon("volume-off" if self.muted else "volume", theme.TEXT, 18))
        self.btn_sound.setToolTip(t("Sound on") if self.muted else t("Mute"))

    def toggle_loop(self) -> None:
        self.loop = self.btn_loop.isChecked()
        if self.player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            self.player.setLoops(QMediaPlayer.Loops.Infinite if self.loop else 1)

    def toggle_fit(self) -> None:
        self.set_fit("fit" if self.btn_fit.isChecked() else "fill")

    def set_fit(self, fit: str) -> None:
        self.fit = fit
        self.btn_fit.setChecked(fit == "fit")
        self.still.fit = fit
        self.still.update()
        if self.video_view is not None:
            self.video_view.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding if fit == "fill"
                                               else Qt.AspectRatioMode.KeepAspectRatio)
        self._scale_movie()

    def _scale_movie(self) -> None:
        if self.movie is None:
            return
        self.movie.jumpToFrame(0)
        frame = self.movie.currentPixmap().size()
        if frame.isEmpty():
            return
        mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fit == "fill" else \
            Qt.AspectRatioMode.KeepAspectRatio
        self.movie.setScaledSize(frame.scaled(self.stage.size(), mode))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._scale_movie()

    def _tick(self) -> None:
        if self.player is None:
            return
        pos, dur = self.player.position() / 1000, self.player.duration() / 1000
        self.time.setText(f"{duration_text(pos) or '0:00'} / {duration_text(dur)}" if dur else "")

    def hideEvent(self, e):
        if self.player is not None and self.playing():
            self.player.pause()
            self.clock.stop()
            self._set_play_icon(False)
        if self.movie is not None:
            self.movie.setPaused(True)
        super().hideEvent(e)


class FullscreenPreview(QWidget):
    """The selected wallpaper over a whole monitor, as it will look (Esc or a click closes it)."""

    def __init__(self, item: Item, pm: QPixmap | None, fit: str, screen=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("background: #000;")
        self.setWindowTitle(t("Fullscreen preview") + f" · {item.title}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.player = PreviewPlayer(self, controls=False)
        self.player.set_fit(fit)
        lay.addWidget(self.player)
        hint = QLabel(t("Esc closes the preview"), self)
        hint.setStyleSheet("color: rgba(255,255,255,0.75); background: rgba(0,0,0,0.45); padding: 6px 12px;"
                           " border-radius: 8px;")
        hint.adjustSize()
        self.hint = hint
        QTimer.singleShot(2500, hint.hide)
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.player.show_item(item, pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.hint.move((self.width() - self.hint.width()) // 2, 24)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.close()

    def mousePressEvent(self, e):
        self.close()

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)
