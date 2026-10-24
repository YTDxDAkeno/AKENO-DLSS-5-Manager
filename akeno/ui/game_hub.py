"""Game Hub details on a game's page: favourite, category, playtime, launch arguments, notes, FPS Booster profile."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QWidget

from akeno.i18n import t
from akeno.ui.account_ui import when
from akeno.ui.widgets import Card, KeyValueGrid, button, label

CATEGORIES = ["", "Action", "Adventure", "Racing", "Role-playing", "Shooter", "Simulation", "Sports", "Strategy",
              "Multiplayer", "Favourites to finish"]


def playtime_text(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return t("{n} min", n=0) if seconds == 0 else t("under a minute")
    h, rem = divmod(seconds, 3600)
    return t("{h} h {m} min", h=h, m=rem // 60) if h else t("{n} min", n=rem // 60)


class GameHubCard(Card):
    def __init__(self, ctl, game, window=None):
        super().__init__(t("Game Hub"), "home")
        self.ctl, self.s, self.game, self.win = ctl, ctl.s, game, window
        meta = self.s.db.game_meta(game.id)
        self.fav = QCheckBox(t("Favourite"))
        self.fav.setChecked(bool(meta["favorite"]))
        self.fav.toggled.connect(lambda v: self._save(favorite=bool(v)))
        row = QHBoxLayout()
        row.addWidget(self.fav)
        row.addWidget(label(t("Category"), "key"))
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setAccessibleName(t("Category"))
        for c in CATEGORIES:
            self.category.addItem(t(c) if c else "", c)
        self.category.setCurrentText(meta["category"])
        self.category.lineEdit().editingFinished.connect(lambda: self._save(category=self.category.currentText()
                                                                            .strip()[:60]))
        row.addWidget(self.category, 1)
        self.body.addLayout(row)
        self.stats = KeyValueGrid(150)
        self.body.addWidget(self.stats)
        self.body.addWidget(label(t("Launch arguments"), "key"))
        self.args = QLineEdit(meta["launch_args"])
        self.args.setPlaceholderText(t("for example -dx12 -fullscreen"))
        self.args.setAccessibleName(t("Launch arguments"))
        self.args.setMaxLength(500)
        self.args.editingFinished.connect(lambda: self._save(launch_args=self.args.text().strip()))
        self.body.addWidget(self.args)
        self.body.addWidget(label(t("Used by Start game in AKENO (Steam and direct starts; an Epic game with "
                                    "arguments starts directly)."), "muted", shrink=True))
        self.body.addWidget(label(t("Notes"), "key"))
        self.notes = QPlainTextEdit(meta["notes"])
        self.notes.setAccessibleName(t("Notes"))
        self.notes.setMaximumHeight(90)
        self.body.addWidget(self.notes)
        acts = QHBoxLayout()
        acts.addWidget(button(t("Save notes"), on_click=lambda: self._save(notes=self.notes.toPlainText()[:4000])))
        acts.addWidget(button(t("FPS Booster"), "gauge", on_click=self._booster))
        acts.addStretch(1)
        self.body.addLayout(acts)
        self.refresh()

    def _save(self, **fields) -> None:
        self.s.db.set_game_meta(self.game.id, **fields)
        if "favorite" in fields:
            self.ctl.games_changed.emit()            # the list shows favourites first
        self.refresh()

    def refresh(self) -> None:
        meta = self.s.db.game_meta(self.game.id)
        boost = self.s.boost.summary(self.game.id)
        from akeno.performance.profiles import BY_ID
        profile = BY_ID.get(boost.get("profile") or "")
        self.stats.set_rows([
            (t("Playtime"), playtime_text(meta["playtime_s"])),
            (t("Started"), t("{n} time(s)", n=meta["launches"])),
            (t("Last played"), when(_ts(meta["last_played"])) if meta["last_played"] else t("Never")),
            (t("FPS Booster"), t(profile.name) if profile else t("Not boosted")),
        ])

    def _booster(self) -> None:
        if self.win is None:
            return
        self.ctl.select_game(self.game.id)
        self.win.go("booster")


def _ts(iso: str | None) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None
