"""HOME → Dashboard: the game to continue with, the library in numbers, recently played games and the state of
each part of the suite, every item one click from its page. Only what AKENO knows is shown: counted or
measured values, no estimates."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from akeno import __version__
from akeno.desktop import config as desktop_config
from akeno.i18n import t
from akeno.ui import icons, theme
from akeno.ui.widgets import (Card, Clickable, ElidedLabel, EmptyState, PageHeader, ResponsiveRow, StatTile, StatusPill,
                              button, clear_layout, game_cover, label)


def _ts(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


class StatusRow(Clickable):
    """One part of the suite: icon, name, a detail line and its state; the whole row opens the part's page."""

    def __init__(self, title: str, icon_name: str, on_open):
        super().__init__()
        self.setObjectName("StatusRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setStyleSheet(f"#StatusRow {{ border-radius: 8px; border: 1px solid transparent; }}"
                           f"#StatusRow:hover {{ background: {theme.SURFACE_2}; }}"
                           f"#StatusRow:focus {{ border-color: {theme.ACCENT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.ACCENT_TEXT, 18))
        ic.setFixedSize(20, 20)
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(title, "h3"))
        self.detail = ElidedLabel("", "muted")
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        self.pill = StatusPill("", "pending")
        lay.addWidget(self.pill)
        chev = QLabel()
        chev.setPixmap(icons.pixmap("chevron-right", theme.TEXT_MUTED, 16))
        lay.addWidget(chev)
        self.clicked.connect(on_open)

    def set(self, state: str, text: str, detail: str) -> None:
        self.pill.set(text, state)
        self.detail.set_text(detail)


class HomePage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.CARD_GAP)
        root.addWidget(PageHeader(t("Dashboard"), t("Everything of the AKENO Gaming Suite on this PC at a glance.")))

        top = ResponsiveRow(theme.CARD_GAP)
        self.hero = Card()
        self.hero_stack = QStackedWidget()
        self.hero.body.addWidget(self.hero_stack)
        self.hero_game = QWidget()
        hl = QHBoxLayout(self.hero_game)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(20)
        self.cover = QLabel()
        self.cover.setFixedSize(288, 162)
        hl.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.hero_kind = label("", "overline")
        col.addWidget(self.hero_kind)
        self.hero_name = label("", "display", shrink=True)
        col.addWidget(self.hero_name)
        self.hero_meta = label("", "dim", shrink=True)
        col.addWidget(self.hero_meta)
        self.hero_play = label("", "muted", shrink=True)
        col.addWidget(self.hero_play)
        self.hero_pill = StatusPill("", "pending")
        col.addSpacing(4)
        col.addWidget(self.hero_pill, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACE_S)
        self.btn_start = button(t("Start game"), "play", "primary", on_click=self._start)
        self.btn_open = button(t("Open game page"), on_click=self._open)
        actions.addWidget(self.btn_start)
        actions.addWidget(self.btn_open)
        actions.addStretch(1)
        col.addLayout(actions)
        hl.addLayout(col, 1)
        self.hero_stack.addWidget(self.hero_game)
        self.hero_empty = EmptyState("gamepad", t("No games yet"), t("Scan your launchers or add a game folder manually."),
                                     button(t("Scan for games"), "search", "primary", on_click=lambda: ctl.scan_games()))
        self.hero_stack.addWidget(self.hero_empty)
        top.add(self.hero, 3)

        self.lib = Card(t("Library"), "gamepad")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)
        self.stats = {"games": StatTile(t("Games")), "installs": StatTile(t("DLSS 5 installs")),
                      "favourites": StatTile(t("Favourites")), "boosted": StatTile(t("Games with a profile"))}
        for i, w in enumerate(self.stats.values()):
            grid.addWidget(w, i // 2, i % 2)
        self.lib.body.addLayout(grid)
        self.lib.body.addStretch(1)
        self.lib.body.addWidget(button(t("Open My Games"), on_click=lambda: self.win.go("library")), 0,
                                Qt.AlignmentFlag.AlignLeft)
        top.add(self.lib, 2)
        root.addWidget(top)

        bottom = ResponsiveRow(theme.CARD_GAP)
        self.recent = Card(t("Recently Played"), "clock",
                           right=button(t("Show all"), kind="link", on_click=lambda: self.win.go("recent")))
        self.recent_list = QVBoxLayout()
        self.recent_list.setSpacing(2)
        self.recent.body.addLayout(self.recent_list)
        bottom.add(self.recent, 3)
        self.status = Card(t("Suite"), "layers")
        self.rows = {}
        for key, title, icon_name, page in (("license", "License", "key", "license"),
                                            ("booster", "FPS Booster", "gauge", "booster"),
                                            ("desktop", "Desktop Studio", "monitor", "wallpapers"),
                                            ("sync", "Cloud Sync", "refresh", "sync"),
                                            ("updates", "Updates", "download", "updates")):
            row = StatusRow(t(title), icon_name, lambda _=False, p=page: self.win.go(p))
            self.rows[key] = row
            self.status.body.addWidget(row)
        bottom.add(self.status, 2)
        root.addWidget(bottom)
        root.addStretch(1)
        self.hero_game_id: str | None = None
        ctl.games_changed.connect(self.refresh)

    # -- data --------------------------------------------------------------------------------------
    def refresh(self) -> None:
        games = self.s.library.games()
        metas = self.s.db.all_game_meta()
        self._hero(games, metas)
        self._library(games, metas)
        self._recent(games, metas)
        self._license()
        self._booster()
        self._desktop()
        self._sync()
        self._updates()

    def _installed(self) -> dict[str, dict]:
        out = {}
        for tx in self.s.db.transactions():
            if tx["state"] == "installed" and tx["kind"] in ("install", "swap"):
                out.setdefault(tx["game_id"], tx)
        return out

    def _hero(self, games, metas) -> None:
        if not games:
            self.hero_game_id = None
            self.hero_stack.setCurrentWidget(self.hero_empty)
            return
        by_id = {g.id: g for g in games}
        played = sorted((m for m in metas.values() if m.get("last_played") and m["game_id"] in by_id),
                        key=lambda m: m["last_played"], reverse=True)
        if played:
            game, meta, kind = by_id[played[0]["game_id"]], played[0], t("Continue playing")
        else:
            game = self.s.selected_game() or games[0]
            meta, kind = metas.get(game.id) or {}, t("Selected game")
        self.hero_game_id = game.id
        self.hero_stack.setCurrentWidget(self.hero_game)
        self.cover.setPixmap(game_cover(game, 288, 162, 8))
        self.hero_kind.setText(kind.upper())
        self.hero_name.setText(game.name)
        from akeno.ui.stack_panel import LAUNCHERS
        self.hero_meta.setText(" · ".join(x for x in (t(LAUNCHERS.get(game.launcher, game.launcher)),
                                                       t(game.api.label), game.architecture or "") if x))
        if meta.get("last_played"):
            from akeno.ui.account_ui import when
            from akeno.ui.game_hub import playtime_text
            self.hero_play.setText(t("Played {p} · last {d}", p=playtime_text(meta.get("playtime_s") or 0),
                                     d=when(_ts(meta["last_played"]))))
        else:
            self.hero_play.setText(t("Not played with AKENO running yet"))
        installed = self._installed().get(game.id)
        if installed and installed["backend_id"] == "stack":
            self.hero_pill.set(t("DLSS 5 installed"), "ok")
        elif installed:
            self.hero_pill.set(t("Installed by an older AKENO version"), "warn")
        else:
            self.hero_pill.set(t("DLSS 5 not installed"), "pending")
        self.btn_start.setEnabled(game.launcher == "fivem" or bool(game.exe_path))

    def _library(self, games, metas) -> None:
        ids = {g.id for g in games}
        installs = {gid for gid, tx in self._installed().items() if gid in ids}
        boosted = [gid for gid, v in self.s.boost.state.get("games", {}).items() if v.get("profile") and gid in ids]
        self.stats["games"].set(str(len(games)))
        self.stats["installs"].set(str(len(installs)))
        self.stats["favourites"].set(str(sum(1 for m in metas.values() if m.get("favorite") and m["game_id"] in ids)))
        self.stats["boosted"].set(str(len(boosted)))

    def _recent(self, games, metas) -> None:
        clear_layout(self.recent_list)
        by_id = {g.id: g for g in games}
        played = sorted((m for m in metas.values() if m.get("last_played") and m["game_id"] in by_id),
                        key=lambda m: m["last_played"], reverse=True)[:4]
        if not played:
            self.recent_list.addWidget(EmptyState("clock", t("Nothing played yet"),
                                                  t("Start a game from My Games or your launcher while AKENO runs.")))
            return
        from akeno.ui.account_ui import when
        from akeno.ui.game_hub import playtime_text
        for m in played:
            game = by_id[m["game_id"]]
            row = Clickable()
            row.setObjectName("RecentRow")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            row.setAccessibleName(game.name)
            row.setStyleSheet(f"#RecentRow {{ border-radius: 8px; border: 1px solid transparent; }}"
                              f"#RecentRow:hover {{ background: {theme.SURFACE_2}; }}"
                              f"#RecentRow:focus {{ border-color: {theme.ACCENT}; }}")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(8, 6, 10, 6)
            lay.setSpacing(12)
            thumb = QLabel()
            thumb.setPixmap(game_cover(game, 80, 45, 5))
            thumb.setFixedSize(80, 45)
            lay.addWidget(thumb)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(label(("★ " if m.get("favorite") else "") + game.name, "h3", shrink=True))
            col.addWidget(label(t("Played {p} · last {d}", p=playtime_text(m.get("playtime_s") or 0),
                                  d=when(_ts(m["last_played"]))), "muted", shrink=True))
            lay.addLayout(col, 1)
            row.clicked.connect(lambda gid=game.id: self._open(gid))
            self.recent_list.addWidget(row)

    def _license(self) -> None:
        view = self.win.license.view()
        from akeno.ui.account_ui import STATUS_NAME
        if view.lifetime:
            detail = t("Lifetime, also offline")
        elif view.remaining is not None:
            days = view.remaining / 86400
            detail = t("{d:.1f} days", d=days) if days >= 1 else t("{h:.0f} hours", h=view.remaining / 3600)
            detail = f"{t(view.type_name)} · {detail}"
        else:
            detail = t(view.message)
        self.rows["license"].set("ok" if view.unlocked else "warn",
                                 t(STATUS_NAME.get(view.status, view.status.value)), detail)

    def _booster(self) -> None:
        configured = [gid for gid, v in self.s.boost.state.get("games", {}).items() if v.get("profile")]
        running = self.win.sessions.running() if getattr(self.win, "sessions", None) else {}
        mode = t("Intelligent game mode") + ": " + (t("on") if self.s.settings.get("intelligent_game_mode") else t("off"))
        benches = self.s.db.benchmarks()
        if benches and benches[0].get("stats", {}).get("avg_fps") is not None:
            mode += " · " + t("Last measured") + f": {benches[0]['stats']['avg_fps']:.0f} FPS"
        self.rows["booster"].set("running" if running else ("ok" if configured else "pending"),
                                 t("A game is running") if running else t("{n} game(s) boosted", n=len(configured)),
                                 mode)

    def _desktop(self) -> None:
        cfg = desktop_config.load()
        walls = sum(1 for w in cfg["wallpapers"].values() if w.get("enabled") and w.get("playlist"))
        on = cfg.get("enabled", True) and (walls or cfg["dock"].get("enabled") or cfg["widgets"])
        detail = " · ".join((t("Wallpapers") + f": {walls}",
                             t("Dock") + ": " + (t("on") if cfg["dock"].get("enabled") else t("off")),
                             t("Widgets") + f": {len(cfg['widgets'])}"))
        self.rows["desktop"].set("ok" if on else "pending", t("Switched on") if on else t("Off"), detail)

    def _sync(self) -> None:
        st = self.win.sync.state
        detail = t("Last sync") + ": " + (st["last_sync"] or "—")
        if st["conflicts"]:
            self.rows["sync"].set("warn", t("{n} conflict(s) to decide", n=len(st["conflicts"])), detail)
        elif st["enabled"] and st["folder"]:
            self.rows["sync"].set("ok", t("In sync") if st["last_sync"] else t("Not synced yet"), detail)
        else:
            self.rows["sync"].set("pending", t("Off"), detail)

    def _updates(self) -> None:
        page = self.win.pages.get("updates")
        state = page.svc.state() if page is not None else {}
        last = state.get("last_check")
        detail = t("Last check") + ": " + (time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "—")
        self.rows["updates"].set("pending", t("Version {v}", v=__version__), detail)

    # -- actions -----------------------------------------------------------------------------------
    def _open(self, game_id: str | None = None) -> None:
        gid = game_id or self.hero_game_id
        if gid:
            self.ctl.select_game(gid)
            self.win.go("library")

    def _start(self) -> None:
        if not self.hero_game_id:
            return
        gid = self.hero_game_id
        self.ctl.run("launch", lambda: self.s.stack.launch(gid), lambda text: self.ctl.status.emit(t(text)),
                     lambda e: self.ctl.status.emit(t(str(e))))

    def on_show(self) -> None:
        self.refresh()
