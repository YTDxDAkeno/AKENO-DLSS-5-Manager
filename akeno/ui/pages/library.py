"""My Games: the installed games on the left, the selected game's DLSS 5 page on the right.

The list and the game page scroll independently: picking a game far down the list keeps the list where it is and
shows the new game's page from the top. The list is a model/delegate view, so hundreds of games stay fast.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListView, QScrollArea,
                               QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from akeno.games.launchers import ManualGameError
from akeno.i18n import t
from akeno.ui import art, icons, theme
from akeno.ui.stack_panel import LAUNCHERS, GamePanel, error_text
from akeno.ui.widgets import EmptyState, PageHeader, Spinner, button, clear_layout, info, label

__all__ = ["LibraryPage", "LAUNCHERS"]

ROW_HEIGHT = 68
COVER = QSize(96, 54)          # 16:9, the shape of store artwork


class GameListModel(QAbstractListModel):
    GameIdRole = Qt.ItemDataRole.UserRole + 1
    SubtitleRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    GameRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self):
        super().__init__()
        self.rows: list[tuple] = []          # (game, (status text, state))
        self.favorites: dict[str, bool] = {}

    def set_rows(self, rows: list[tuple]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def row_of(self, game_id: str | None) -> int:
        return next((i for i, (g, _) in enumerate(self.rows) if g.id == game_id), -1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        g, status = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return ("★ " if self.favorites.get(g.id) else "") + g.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{g.name}\n{g.install_dir}"
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{g.name}, {t(status[0])}"
        if role == self.GameIdRole:
            return g.id
        if role == self.GameRole:
            return g
        if role == self.SubtitleRole:
            return f"{t(LAUNCHERS.get(g.launcher, g.launcher))} · {t(g.api.label)}"
        if role == self.StatusRole:
            return status
        return None


class GameDelegate(QStyledItemDelegate):
    """One card per game: cover, name, launcher and API, install state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._covers: dict[tuple, QPixmap] = {}

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def _cover(self, game, dpr: float) -> QPixmap:
        path = game.artwork_path if game.artwork_path and os.path.isfile(game.artwork_path) else None
        try:
            stamp = os.path.getmtime(path) if path else 0
        except OSError:
            stamp = 0
        key = (game.id, path, stamp, dpr)
        pm = self._covers.get(key)
        if pm is None:
            w, h = round(COVER.width() * dpr), round(COVER.height() * dpr)
            src = QPixmap(path) if path else QPixmap()
            if src.isNull():
                src = art.cover_fallback(game.name, COVER.width(), COVER.height())
            src = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            pm = src.copy((src.width() - w) // 2, (src.height() - h) // 2, w, h)
            pm.setDevicePixelRatio(dpr)
            if len(self._covers) > 600:          # hundreds of games: keep memory bounded
                self._covers.clear()
            self._covers[key] = pm
        return pm

    def paint(self, painter: QPainter, option, index) -> None:
        game = index.data(GameListModel.GameRole)
        if game is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(0, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(option.state & QStyle.StateFlag.State_HasFocus)
        path = QPainterPath()
        path.addRoundedRect(r.x() + 0.5, r.y() + 0.5, r.width() - 1, r.height() - 1, 8, 8)
        if selected:
            fill = QColor(theme.ACCENT)
            fill.setAlpha(36)
        elif hover:
            fill = QColor(theme.SURFACE_2)
        else:
            fill = QColor(theme.SURFACE)
        painter.fillPath(path, fill)
        border = theme.ACCENT if focus else (theme.BORDER_BTN if selected or hover else theme.BORDER)
        painter.setPen(QPen(QColor(border), 1))
        painter.drawPath(path)
        if selected:                                   # selection bar, as in the navigation
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(r.x() + 1, r.y() + 12, 3, r.height() - 24, 1.5, 1.5)
        cover = self._cover(game, painter.device().devicePixelRatioF() if painter.device() else 1.0)
        cr = QRect(r.x() + 10, r.y() + (r.height() - COVER.height()) // 2, COVER.width(), COVER.height())
        clip = QPainterPath()
        clip.addRoundedRect(cr.x(), cr.y(), cr.width(), cr.height(), 5, 5)
        painter.setClipPath(clip)
        painter.drawPixmap(cr, cover)
        painter.setClipping(False)
        x = cr.right() + 12
        width = r.right() - 10 - x
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        line = fm.height()
        top = r.y() + (r.height() - 3 * line) // 2
        name = game.name
        if getattr(index.model(), "favorites", {}).get(game.id):
            painter.setPen(QColor(theme.AMBER))
            painter.drawText(QRect(x, top, 16, line), Qt.AlignmentFlag.AlignVCenter, "★")
            name_x = x + fm.horizontalAdvance("★ ")
        else:
            name_x = x
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(QRect(name_x, top, width - (name_x - x), line), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(name, Qt.TextElideMode.ElideRight, width - (name_x - x)))
        small = QFont(option.font)
        small.setPointSizeF(theme.FONT_SMALL)
        painter.setFont(small)
        fm = painter.fontMetrics()
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(QRect(x, top + line, width, line), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(index.data(GameListModel.SubtitleRole), Qt.TextElideMode.ElideRight, width))
        text, state = index.data(GameListModel.StatusRole)
        dot = QColor(theme.STATE_COLORS.get(state, theme.TEXT_DIM))
        cy = top + 2 * line + line // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(x + 1, cy - 3, 6, 6)
        painter.setPen(QColor(theme.TEXT_DIM if state == "ok" else theme.TEXT_MUTED))
        painter.drawText(QRect(x + 12, top + 2 * line, width - 12, line), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(t(text), Qt.TextElideMode.ElideRight, width - 12))
        painter.restore()


class LibraryPage(QWidget):
    scrolls = False                         # the list and the game page have their own scroll areas

    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s = ctl, ctl.s
        self.detail_id: str | None = None
        self.panel: GamePanel | None = None
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.spinner = Spinner()
        self.btn_scan = button(t("Rescan"), "refresh", tip=t("Search Steam, Epic, GOG, Ubisoft, EA, Xbox and FiveM"),
                               on_click=lambda: ctl.scan_games())
        lay.addWidget(PageHeader(t("My Games"), None, (self.spinner, self.btn_scan,
                                                       button(t("Add game"), "plus", "primary", on_click=self._add_manual))))
        body = QHBoxLayout()
        body.setSpacing(theme.CARD_GAP)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search games…"))
        self.search.setAccessibleName(t("Search games"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh_list)
        tools.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName(t("Filter"))
        self.filter.addItem(t("All games"), "all")
        self.filter.addItem(t("Favourites"), "favorites")
        self.filter.addItem(t("Mod installed"), "installed")
        self.filter.addItem(t("Not installed"), "not_installed")
        for k, v in LAUNCHERS.items():
            self.filter.addItem(t(v), "launcher:" + k)
        self.filter.currentIndexChanged.connect(self.refresh_list)
        self.filter.setMinimumWidth(130)
        self.filter.setMaximumWidth(170)
        tools.addWidget(self.filter)
        left.addLayout(tools)
        self.count = label("", "muted")
        left.addWidget(self.count)
        self.model = GameListModel()
        self.view = QListView()
        self.view.setObjectName("GameList")
        self.view.setAccessibleName(t("My Games"))
        self.view.setModel(self.model)
        self.view.setItemDelegate(GameDelegate(self.view))
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(24)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.view.setStyleSheet("QListView#GameList { background: transparent; border: none; outline: 0; }"
                                "QListView#GameList::item { background: transparent; border: 0; }")
        self.view.selectionModel().currentRowChanged.connect(self._row_changed)
        self.empty = EmptyState("gamepad", t("No games yet"), t("Scan your launchers or add a game folder manually."),
                                button(t("Scan for games"), "search", "primary", on_click=lambda: self.ctl.scan_games()))
        self.no_match = label(t("No game matches the filter."), "dim", wrap=True)
        self.no_match.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.list_stack = QStackedWidget()
        for w in (self.view, self.empty, self.no_match):
            self.list_stack.addWidget(w)
        left.addWidget(self.list_stack, 1)
        self.errors = label("", "muted", wrap=True)
        left.addWidget(self.errors)
        self.list_holder = QWidget()
        self.list_holder.setLayout(left)
        self.list_holder.setFixedWidth(372)
        body.addWidget(self.list_holder)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("GameDetail")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setStyleSheet("QScrollArea#GameDetail, QScrollArea#GameDetail > QWidget > QWidget"
                                         " { background: transparent; }")
        detail = QWidget()
        self.detail_host = QVBoxLayout(detail)
        self.detail_host.setContentsMargins(0, 0, 6, 0)
        self.detail_scroll.setWidget(detail)
        body.addWidget(self.detail_scroll, 1)
        lay.addLayout(body, 1)
        ctl.games_changed.connect(self.refresh_list)
        ctl.installs_changed.connect(self.refresh_list)
        ctl.selection_changed.connect(self._selection)
        ctl.hardware_changed.connect(self._refresh_panel)
        ctl.busy_changed.connect(self._busy)
        self.refresh_list()
        self._selection()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 150 % scaling on a 1080p screen leaves ~1000 logical pixels: give the game page the room.
        self.list_holder.setFixedWidth(300 if self.width() < 1250 else 372)

    def _busy(self, key, on):
        if key == "games":
            (self.spinner.start if on else self.spinner.stop)()
            self.btn_scan.setEnabled(not on)

    # -- list -------------------------------------------------------------------------------------
    def _installed_map(self) -> dict[str, str]:
        """game id -> backend of its active installation (one query for the whole library)."""
        out: dict[str, str] = {}
        for tx in self.s.db.transactions():
            if tx["state"] == "installed" and tx["kind"] in ("install", "swap"):
                out.setdefault(tx["game_id"], tx["backend_id"])
        return out

    @staticmethod
    def _status(backend: str | None) -> tuple[str, str]:
        if backend == "stack":
            return "Mod installed", "ok"
        if backend:
            return "Older AKENO install", "warn"
        return "Not installed", "pending"

    def refresh_list(self, *_) -> None:
        games = self.s.library.games()
        installed = self._installed_map()
        metas = self.s.db.all_game_meta()
        favorites = {gid: bool(m.get("favorite")) for gid, m in metas.items()}
        self.model.favorites = favorites
        q = self.search.text().strip().lower()
        f = self.filter.currentData() or "all"
        rows = []
        for g in sorted(games, key=lambda g: not favorites.get(g.id)):      # favourites first, order otherwise kept
            category = (metas.get(g.id) or {}).get("category", "").lower()
            if q and q not in g.name.lower() and q not in g.install_dir.lower() and q not in category:
                continue
            if f == "favorites" and not favorites.get(g.id):
                continue
            status = self._status(installed.get(g.id))
            if f == "installed" and status[1] != "ok" or f == "not_installed" and status[1] == "ok":
                continue
            if f.startswith("launcher:") and g.launcher != f.split(":", 1)[1]:
                continue
            rows.append((g, status))
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        self._syncing = True
        try:
            self.model.set_rows(rows)
            self._mark_selected()
        finally:
            self._syncing = False
        bar.setValue(keep)                   # a status change never moves the list
        self.list_stack.setCurrentWidget(self.view if rows else (self.no_match if games else self.empty))
        self.count.setText(t("{n} of {m} games", n=len(rows), m=len(games)) if games and len(rows) != len(games)
                           else (t("{n} game(s)", n=len(games)) if games else ""))
        self.count.setVisible(bool(games))
        errs = self.s.library.last_errors
        self.errors.setText(t("Some sources could not be read: ") + "; ".join(errs) if errs else "")
        self.errors.setVisible(bool(errs))
        if self.panel is None:
            self._selection()

    def _mark_selected(self) -> None:
        """Highlight the shown game without scrolling the list."""
        row = self.model.row_of(self.detail_id)
        sm = self.view.selectionModel()
        was = self._syncing
        self._syncing = True
        try:
            if row < 0:
                sm.clearSelection()
                sm.setCurrentIndex(QModelIndex(), sm.SelectionFlag.NoUpdate)
            else:
                idx = self.model.index(row)
                sm.setCurrentIndex(idx, sm.SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = was

    def _row_changed(self, current, _previous) -> None:
        if self._syncing or not current.isValid():
            return
        gid = current.data(GameListModel.GameIdRole)
        if gid and gid != self.detail_id:
            self.open(gid)

    def open(self, game_id: str) -> None:
        self.detail_id = game_id
        self.ctl.select_game(game_id)

    # -- game page --------------------------------------------------------------------------------
    def _selection(self, *_) -> None:
        g = self.s.selected_game() or self.s.library.get(self.detail_id)
        if g is not None and g.launcher == "fivem" and self.detail_id and self.detail_id != g.id:
            g = self.s.library.get(self.detail_id) or g      # FiveM has its own page; keep the shown game
        if g is None:
            self._set_panel(None)
            return
        if self.panel is not None and self.panel.game_id == g.id:
            return
        self.detail_id = g.id
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        self._mark_selected()
        bar.setValue(keep)
        self._set_panel(g.id)

    def _set_panel(self, game_id: str | None) -> None:
        if self.panel is not None:
            self.panel.dispose()
        clear_layout(self.detail_host)
        self.panel = None
        if game_id is None:
            self.detail_host.addWidget(label(t("Select a game to see its DLSS 5 options."), "dim", wrap=True))
            self.detail_host.addStretch(1)
        else:
            self.panel = GamePanel(self.ctl, game_id, self)
            self.detail_host.addWidget(self.panel)
            game = self.s.library.get(game_id)
            if game is not None and game.launcher != "fivem":
                from akeno.ui.game_hub import GameHubCard
                self.hub = GameHubCard(self.ctl, game, self.window())
                self.detail_host.addWidget(self.hub)
        self._detail_to_top()

    def _detail_to_top(self) -> None:
        self.detail_scroll.verticalScrollBar().setValue(0)
        # the panel renders asynchronously; once its content is laid out, start at its top again
        QTimer.singleShot(0, lambda: self.detail_scroll.verticalScrollBar().setValue(0))

    def _refresh_panel(self, *_) -> None:
        if self.panel is not None:
            self.panel.refresh()

    def on_show(self) -> None:
        self._refresh_panel()

    def _add_manual(self) -> None:
        path = QFileDialog.getExistingDirectory(self, t("Select the game folder"))
        if not path:
            return

        def done(game):
            self.open(game.id)
            self.ctl.games_changed.emit()

        def fail(exc):
            info(self, t("Add game"), t(str(exc)) if isinstance(exc, ManualGameError) else error_text(exc), error=True)
        self.ctl.run("games", lambda: self.s.library.add_manual(path), done, fail)
