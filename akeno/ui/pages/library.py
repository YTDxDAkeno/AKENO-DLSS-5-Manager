"""My Games: the installed games on the left, the selected game's DLSS 5 page on the right."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from akeno.games.launchers import ManualGameError
from akeno.i18n import t
from akeno.ui import art, theme
from akeno.ui.stack_panel import LAUNCHERS, GamePanel, error_text
from akeno.ui.widgets import Clickable, EmptyState, Spinner, StatusPill, button, clear_layout, info, label, scaled

__all__ = ["LibraryPage", "LAUNCHERS"]


class GameRow(Clickable):
    def __init__(self, game, selected: bool, status: tuple[str, str]):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(game.name)
        border = theme.VIOLET if selected else theme.BORDER
        bg = "rgba(142,66,255,0.16)" if selected else theme.CARD
        self.setStyleSheet(f"GameRow {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
                           f"GameRow:hover {{ border-color: {theme.BORDER_HI}; }}"
                           f"GameRow:focus {{ border-color: {theme.VIOLET}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(10)
        img = QLabel()
        pm = QPixmap(game.artwork_path) if game.artwork_path and os.path.isfile(game.artwork_path) else None
        img.setPixmap(scaled(pm, 84, 46) if pm and not pm.isNull() else art.cover_fallback(game.name, 84, 46))
        img.setFixedSize(84, 46)
        lay.addWidget(img)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = label(game.name)
        name.setStyleSheet("font-weight: 600;")
        col.addWidget(name)
        col.addWidget(label(f"{t(LAUNCHERS.get(game.launcher, game.launcher))} · {t(game.api.label)}", "muted"))
        lay.addLayout(col, 1)
        lay.addWidget(StatusPill(t(status[0]), status[1]))


class LibraryPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s = ctl, ctl.s
        self.detail_id: str | None = None
        self.panel: GamePanel | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label(t("My Games"), "h1"))
        top.addStretch(1)
        self.spinner = Spinner()
        top.addWidget(self.spinner)
        self.btn_scan = button(t("Rescan"), "refresh", tip=t("Search Steam, Epic, GOG, Ubisoft, EA, Xbox and FiveM"),
                               on_click=lambda: ctl.scan_games())
        top.addWidget(self.btn_scan)
        top.addWidget(button(t("Add game"), "plus", "primary", on_click=self._add_manual))
        lay.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search games…"))
        self.search.setAccessibleName(t("Search games"))
        self.search.textChanged.connect(self.refresh_list)
        left.addWidget(self.search)
        self.filter = QComboBox()
        self.filter.setAccessibleName(t("Filter"))
        self.filter.addItem(t("All games"), "all")
        self.filter.addItem(t("Mod installed"), "installed")
        self.filter.addItem(t("Not installed"), "not_installed")
        for k, v in LAUNCHERS.items():
            self.filter.addItem(t(v), "launcher:" + k)
        self.filter.currentIndexChanged.connect(self.refresh_list)
        left.addWidget(self.filter)
        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(6)
        self.list.setAlignment(Qt.AlignmentFlag.AlignTop)
        left.addWidget(self.list_host, 1)
        self.errors = label("", "muted", wrap=True)
        left.addWidget(self.errors)
        self.list_holder = QWidget()
        self.list_holder.setLayout(left)
        self.list_holder.setFixedWidth(390)
        body.addWidget(self.list_holder, 0, Qt.AlignmentFlag.AlignTop)
        self.detail_host = QVBoxLayout()
        body.addLayout(self.detail_host, 1)
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
        self.list_holder.setFixedWidth(300 if self.width() < 1250 else 390)

    def _busy(self, key, on):
        if key == "games":
            (self.spinner.start if on else self.spinner.stop)()
            self.btn_scan.setEnabled(not on)

    def _status(self, g) -> tuple[str, str]:
        active = self.s.installer.active_install(g.id)
        if active and active["backend_id"] == "stack":
            return "Mod installed", "ok"
        if active:
            return "Older AKENO install", "warn"
        return "Not installed", "pending"

    def refresh_list(self, *_) -> None:
        clear_layout(self.list)
        games = self.s.library.games()
        if not games:
            self.list.addWidget(EmptyState("gamepad", t("No games yet"),
                                           t("Scan your launchers or add a game folder manually."),
                                           button(t("Scan for games"), "search", "primary",
                                                  on_click=lambda: self.ctl.scan_games())))
        q = self.search.text().strip().lower()
        f = self.filter.currentData() or "all"
        sel = self.detail_id or (self.s.selected_game().id if self.s.selected_game() else None)
        shown = 0
        for g in games:
            if q and q not in g.name.lower() and q not in g.install_dir.lower():
                continue
            status = self._status(g)
            if f == "installed" and status[1] != "ok" or f == "not_installed" and status[1] == "ok":
                continue
            if f.startswith("launcher:") and g.launcher != f.split(":", 1)[1]:
                continue
            row = GameRow(g, g.id == sel, status)
            row.clicked.connect(lambda gid=g.id: self.open(gid))
            self.list.addWidget(row)
            shown += 1
        if games and not shown:
            self.list.addWidget(label(t("No game matches the filter."), "dim"))
        errs = self.s.library.last_errors
        self.errors.setText(t("Some sources could not be read: ") + "; ".join(errs) if errs else "")
        if self.panel is None:
            self._selection()

    def open(self, game_id: str) -> None:
        self.detail_id = game_id
        self.ctl.select_game(game_id)

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
        self._set_panel(g.id)
        self.refresh_list()

    def _set_panel(self, game_id: str | None) -> None:
        if self.panel is not None:
            self.panel.dispose()
        clear_layout(self.detail_host)
        self.panel = None
        if game_id is None:
            self.detail_host.addWidget(label(t("Select a game to see its DLSS 5 options."), "dim", wrap=True))
            return
        self.panel = GamePanel(self.ctl, game_id, self)
        self.detail_host.addWidget(self.panel)

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
