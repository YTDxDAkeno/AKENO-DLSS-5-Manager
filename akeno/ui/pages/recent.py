"""Recently Played (HOME): the last games you played, with playtime, favourites and a start button."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.account_ui import when
from akeno.ui.game_hub import playtime_text
from akeno.ui.widgets import Card, EmptyState, button, clear_layout, label


def _ts(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


class RecentPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label(t("Recently Played"), "h1"))
        root.addWidget(label(t("AKENO counts playtime while it runs and notices your games (intelligent game mode)."),
                             "dim", shrink=True))
        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        root.addLayout(self.list)
        self.favourites = Card(t("Favourites"), "sparkle")
        self.fav_list = QVBoxLayout()
        self.favourites.body.addLayout(self.fav_list)
        root.addWidget(self.favourites)
        root.addStretch(1)
        ctl.games_changed.connect(self.refresh)
        self.refresh()

    def _row(self, game, meta) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        star = "★ " if meta.get("favorite") else ""
        text = QVBoxLayout()
        text.addWidget(label(star + game.name, "h3", shrink=True))
        text.addWidget(label(t("Played {p} · last {d} · started {n} time(s)", p=playtime_text(meta["playtime_s"]),
                               d=when(_ts(meta["last_played"])), n=meta["launches"]), "muted", shrink=True))
        row.addLayout(text, 1)
        row.addWidget(button(t("Open"), on_click=lambda _=False, g=game.id: self._open(g)))
        row.addWidget(button(t("Start game"), "play", "primary", on_click=lambda _=False, g=game.id: self._start(g)))
        return w

    def refresh(self, *_) -> None:
        clear_layout(self.list)
        clear_layout(self.fav_list)
        metas = self.s.db.all_game_meta()
        games = {g.id: g for g in self.s.library.games()}
        played = sorted((m for m in metas.values() if m.get("last_played") and m["game_id"] in games),
                        key=lambda m: m["last_played"], reverse=True)[:12]
        if not played:
            self.list.addWidget(EmptyState("clock", t("Nothing played yet"),
                                           t("Start a game from My Games or your launcher while AKENO runs.")))
        for m in played:
            card = Card()
            card.body.addWidget(self._row(games[m["game_id"]], m))
            self.list.addWidget(card)
        favs = [m for m in metas.values() if m.get("favorite") and m["game_id"] in games]
        self.favourites.setVisible(bool(favs))
        for m in sorted(favs, key=lambda m: games[m["game_id"]].name.lower()):
            self.fav_list.addWidget(self._row(games[m["game_id"]], m))

    def _open(self, game_id: str) -> None:
        self.ctl.select_game(game_id)
        self.win.go("library")

    def _start(self, game_id: str) -> None:
        def done(text):
            self.ctl.status.emit(t(text))
        self.ctl.run("launch", lambda: self.s.stack.launch(game_id), done,
                     lambda e: self.ctl.status.emit(t(str(e))))

    def on_show(self) -> None:
        self.refresh()
