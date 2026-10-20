"""Game Library: discovered and manually added games, search/sort/filter, per-game detail and overrides."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
                               QWidget)

from akeno.core.models import CompatStatus, GraphicsApi
from akeno.games.launchers import ManualGameError
from akeno.i18n import t
from akeno.ui import art, theme
from akeno.ui.pages.dashboard import COMPAT_TEXT
from akeno.ui.widgets import (Card, Clickable, EmptyState, KeyValueGrid, Spinner, StatusPill, button, clear_layout,
                              confirm, info, label, scaled)

LAUNCHERS = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "ubisoft": "Ubisoft Connect", "ea": "EA app",
             "xbox": "Xbox / Microsoft Store", "manual": "Added manually"}


class GameCard(Clickable):
    def __init__(self, game, compat, selected: bool):
        super().__init__()
        self.setFixedSize(236, 214)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(game.name)
        border = theme.VIOLET if selected else theme.BORDER
        self.setStyleSheet(f"GameCard {{ background: {theme.CARD}; border: 1px solid {border}; border-radius: 12px; }}"
                           f"GameCard:hover {{ border-color: {theme.BORDER_HI}; }}"
                           f"GameCard:focus {{ border-color: {theme.VIOLET}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 10)
        lay.setSpacing(6)
        img = QLabel()
        pm = QPixmap(game.artwork_path) if game.artwork_path and os.path.isfile(game.artwork_path) else None
        img.setPixmap(scaled(pm, 218, 110) if pm and not pm.isNull() else art.cover_fallback(game.name, 218, 110))
        img.setFixedSize(218, 110)
        lay.addWidget(img)
        nm = label(game.name)
        nm.setStyleSheet("font-weight: 600;")
        nm.setWordWrap(True)
        lay.addWidget(nm)
        lay.addWidget(label(f"{t(LAUNCHERS.get(game.launcher, game.launcher))} · {t(game.api.label)}", "muted"))
        if compat:
            txt, st = COMPAT_TEXT[compat.status]
            lay.addWidget(StatusPill(t(txt), st))
        lay.addStretch(1)


class LibraryPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        self.detail_id: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label(t("Game Library"), "h1"))
        top.addStretch(1)
        self.spinner = Spinner()
        top.addWidget(self.spinner)
        self.btn_scan = button(t("Rescan"), "refresh", tip=t("Search Steam, Epic, GOG, Ubisoft, EA and Xbox libraries"),
                               on_click=lambda: ctl.scan_games())
        top.addWidget(self.btn_scan)
        top.addWidget(button(t("Add Game Folder"), "plus", "primary", on_click=self._add_manual))
        lay.addLayout(top)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search games…"))
        self.search.setAccessibleName(t("Search games"))
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 2)
        self.sort = QComboBox()
        self.sort.setAccessibleName(t("Sort"))
        for key, text in (("name", "Sort: Name"), ("launcher", "Sort: Launcher"), ("compat", "Sort: Compatibility")):
            self.sort.addItem(t(text), key)
        self.sort.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.sort, 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName(t("Filter"))
        self.filter.addItem(t("All games"), "all")
        for k, v in LAUNCHERS.items():
            self.filter.addItem(t(v), f"launcher:{k}")
        for st in CompatStatus:
            self.filter.addItem(t(COMPAT_TEXT[st][0]), f"compat:{st.value}")
        self.filter.addItem(t("Managed by AKENO"), "managed")
        self.filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.filter, 1)
        lay.addLayout(bar)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.grid_host, 3)
        self.detail = Card(t("Game details"), "gamepad", theme.VIOLET)
        self.detail.setMinimumWidth(430)
        self.detail_body = QVBoxLayout()
        self.detail.body.addLayout(self.detail_body)
        body.addWidget(self.detail, 2, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(body, 1)
        self.errors = label("", "muted", wrap=True)
        lay.addWidget(self.errors)
        ctl.games_changed.connect(self.refresh)
        ctl.selection_changed.connect(self.refresh)
        ctl.installs_changed.connect(self.refresh)
        ctl.hardware_changed.connect(self.refresh)
        ctl.busy_changed.connect(self._busy)
        self._compat_cache: dict = {}
        self.refresh()

    def _busy(self, key, on):
        if key == "games":
            (self.spinner.start if on else self.spinner.stop)()
            self.btn_scan.setEnabled(not on)

    def _games(self):
        games = self.s.library.games()
        q = self.search.text().strip().lower()
        if q:
            games = [g for g in games if q in g.name.lower() or q in g.install_dir.lower()]
        f = self.filter.currentData() or "all"
        compat = {}
        for g in games:
            compat[g.id] = self.s.compatibility(g)
        if f.startswith("launcher:"):
            games = [g for g in games if g.launcher == f.split(":", 1)[1]]
        elif f.startswith("compat:"):
            games = [g for g in games if compat[g.id] and compat[g.id].status.value == f.split(":", 1)[1]]
        elif f == "managed":
            games = [g for g in games if self.s.installer.active_install(g.id)]
        key = self.sort.currentData() or "name"
        order = [s.value for s in (CompatStatus.CONFIRMED, CompatStatus.EXPERIMENTAL, CompatStatus.INSUFFICIENT,
                                   CompatStatus.INCOMPATIBLE, CompatStatus.BLOCKED)]
        if key == "launcher":
            games.sort(key=lambda g: (g.launcher, g.name.lower()))
        elif key == "compat":
            games.sort(key=lambda g: (order.index(compat[g.id].status.value) if compat[g.id] else 9, g.name.lower()))
        return games, compat

    def refresh(self) -> None:
        clear_layout(self.grid)
        games, compat = self._games()
        sel = self.s.selected_game()
        if not self.s.library.games():
            self.grid.addWidget(EmptyState("gamepad", t("No games yet"),
                                           t("Scan your launchers or add a game folder manually."),
                                           button(t("Scan for games"), "search", "primary",
                                                  on_click=lambda: self.ctl.scan_games())), 0, 0)
        cols = 3
        for i, g in enumerate(games):
            card = GameCard(g, compat.get(g.id), sel is not None and sel.id == g.id)
            card.clicked.connect(lambda gid=g.id: self._open(gid))
            self.grid.addWidget(card, i // cols, i % cols)
        if self.s.library.last_errors:
            self.errors.setText(t("Some sources could not be read: ") + "; ".join(self.s.library.last_errors))
        else:
            self.errors.setText("")
        self._render_detail()

    def _open(self, game_id: str) -> None:
        self.detail_id = game_id
        self.ctl.select_game(game_id)

    def _render_detail(self) -> None:
        clear_layout(self.detail_body)
        g = self.s.library.get(self.detail_id) or self.s.selected_game()
        if g is None:
            self.detail_body.addWidget(label(t("Select a game to see its analysis."), "dim", wrap=True))
            return
        self.detail_id = g.id
        self.detail_body.addWidget(label(g.name, "h2"))
        grid = KeyValueGrid(130)
        exe_combo = QComboBox()
        exe_combo.setAccessibleName(t("Executable"))
        for c in g.exe_candidates or ([g.exe_path] if g.exe_path else []):
            exe_combo.addItem(os.path.relpath(c, g.install_dir) if c.startswith(g.install_dir) else c, c)
        exe_combo.addItem(t("Browse…"), "__browse__")
        if g.exe_path:
            exe_combo.setCurrentIndex(max(0, exe_combo.findData(g.exe_path)))
        exe_combo.activated.connect(lambda idx, c=exe_combo, gid=g.id: self._exe_changed(gid, c))
        api_combo = QComboBox()
        api_combo.setAccessibleName(t("Graphics API"))
        api_combo.addItem(t("Detect automatically") + (f" ({t(g.api.label)})" if g.api_source != "selected by you" else ""), "")
        for a in GraphicsApi:
            if a != GraphicsApi.UNKNOWN:
                api_combo.addItem(t(a.label), a.value)
        ov = self.s.db.game_overrides(g.id)
        if ov.get("api"):
            api_combo.setCurrentIndex(api_combo.findData(ov["api"]))
        api_combo.activated.connect(lambda idx, c=api_combo, gid=g.id: self._override(gid, "api", c.currentData()))
        backend_combo = QComboBox()
        backend_combo.setAccessibleName(t("Backend"))
        backend_combo.addItem(t("Automatic"), "")
        for a in self.s.registry.all():
            backend_combo.addItem(a.name, a.id)
        if ov.get("backend"):
            backend_combo.setCurrentIndex(backend_combo.findData(ov["backend"]))
        backend_combo.activated.connect(lambda idx, c=backend_combo, gid=g.id: self._backend(gid, c.currentData()))
        ac = {"detected": t("Detected — installation blocked"), "none_found": t("None found (not a guarantee)"),
              "unknown": t("Unknown")}[g.anti_cheat.value]
        mods = "\n".join(f"{m.name} — {m.detail or m.kind}" + (f" {m.version}" if m.version else "")
                         for m in g.existing_mods) or t("None found")
        running = {True: t("Running"), False: t("Not running"), None: t("Unknown")}[g.is_running]
        grid.set_rows([
            (t("Launcher"), t(LAUNCHERS.get(g.launcher, g.launcher))),
            (t("Install folder"), g.install_dir),
            (t("Executable"), exe_combo),
            (t("Graphics API"), api_combo),
            (t("API evidence"), (g.api_source or "—") + (f" ({', '.join(g.api_candidates)})" if g.api_candidates else "")),
            (t("Architecture"), g.architecture or t("Unknown")),
            (t("Upscalers in game"), ", ".join(x.upper() for x in g.upscaler_inputs) or t("None found")),
            (t("DLSS (game)"), g.dlss_version or t("Not found")),
            (t("Anti-Cheat"), ac + ("\n" + "\n".join(g.anti_cheat_details) if g.anti_cheat_details else "")),
            (t("Process"), running),
            (t("Backend"), backend_combo),
            (t("Files found"), mods),
        ])
        self.detail_body.addWidget(grid)
        for n in g.notes:
            self.detail_body.addWidget(label("⚠ " + t(n), "dim", wrap=True))
        compat = self.s.compatibility(g)
        if compat:
            txt, st = COMPAT_TEXT[compat.status]
            self.detail_body.addWidget(StatusPill(t(txt) + f" · {t('confidence')}: {t(compat.confidence)}", st))
            for f in compat.findings:
                color = {"blocker": theme.RED, "warning": theme.AMBER, "info": theme.TEXT_DIM}[f.severity.value]
                lb = label(f"<span style='color:{color}'>●</span> {t(f.message)}", wrap=True)
                if f.evidence:
                    lb.setToolTip(t("Source: ") + f.evidence)
                self.detail_body.addWidget(lb)
            for e in compat.evidence:
                if e.get("kind") == "game":
                    self.detail_body.addWidget(label(
                        t("Evidence: {src} · version {v} · verified {d} · GPU {gpu}", src=e.get("source"),
                          v=e.get("mod_version", "?"), d=e.get("date_verified", "?"), gpu=e.get("gpu_tested", "?")),
                        "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button(t("Set as active game"), "check", "primary",
                             on_click=lambda gid=g.id: (self.ctl.select_game(gid), self.ctl.navigate.emit("dashboard"))))
        row.addWidget(button(t("Re-analyze"), "refresh", on_click=lambda gg=g: self.ctl.analyze_game(gg)))
        row.addWidget(button("", "folder", tip=t("Open the game folder"),
                             on_click=lambda d=g.install_dir: QDesktopServices.openUrl(QUrl.fromLocalFile(d))))
        if g.launcher == "manual":
            row.addWidget(button("", "trash", "danger", tip=t("Remove from library (no files are deleted)"),
                                 on_click=lambda gid=g.id: self._remove(gid)))
        row.addStretch(1)
        self.detail_body.addLayout(row)

    def _exe_changed(self, gid: str, combo: QComboBox) -> None:
        data = combo.currentData()
        if data == "__browse__":
            g = self.s.library.get(gid)
            path, _ = QFileDialog.getOpenFileName(self, t("Select the game executable"), g.install_dir if g else "",
                                                  "Executable (*.exe)")
            if not path:
                self._render_detail()
                return
            data = path
        self._override(gid, "exe_path", data)

    def _override(self, gid: str, key: str, value) -> None:
        self.ctl.run("analyze", lambda: self.s.library.set_override(gid, key, value),
                     lambda _: (self.ctl.games_changed.emit(), self.ctl.selection_changed.emit()))

    def _backend(self, gid: str, backend_id: str) -> None:
        g = self.s.library.get(gid)
        if g:
            self.s.set_backend(g, backend_id or None)
            self.ctl.selection_changed.emit()

    def _remove(self, gid: str) -> None:
        if self.s.installer.active_install(gid):
            info(self, t("Remove game"), t("AKENO manages files in this game. Restore it on the Backups page first."))
            return
        if confirm(self, t("Remove game"), t("Remove this game from AKENO's library? No files are deleted.")):
            self.s.library.remove(gid)
            self.detail_id = None
            self.ctl.games_changed.emit()

    def _add_manual(self) -> None:
        path = QFileDialog.getExistingDirectory(self, t("Select the game folder"))
        if not path:
            return

        def done(game):
            self.detail_id = game.id
            self.ctl.select_game(game.id)
            self.ctl.games_changed.emit()

        def fail(exc):
            info(self, t("Add Game Folder"), t(str(exc)) if isinstance(exc, ManualGameError) else str(exc), error=True)
        self.ctl.run("games", lambda: self.s.library.add_manual(path), done, fail)
