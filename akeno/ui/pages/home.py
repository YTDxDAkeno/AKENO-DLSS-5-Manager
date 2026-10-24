"""HOME → Dashboard: the suite at a glance. License, games, the last game played, FPS Booster, Desktop Studio,
Cloud Sync and updates, each with the way to its page. Only what AKENO knows is shown: no estimated FPS."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QVBoxLayout, QWidget

from akeno import __version__
from akeno.desktop import config as desktop_config
from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, KeyValueGrid, ResponsiveRow, StatusPill, button, label


class HomePage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label(t("Dashboard"), "h1"))
        root.addWidget(label(t("Everything of the AKENO Gaming Suite on this PC at a glance."), "dim", shrink=True))
        self.cards: dict[str, tuple[StatusPill, KeyValueGrid]] = {}
        rows = [ResponsiveRow(spacing=14) for _ in range(2)]
        spec = [
            (0, "license", "License", "key", theme.VIOLET, "license", "Open"),
            (0, "games", "My Games", "gamepad", theme.BLUE, "library", "Open"),
            (0, "booster", "FPS Booster", "gauge", theme.CYAN, "booster", "Open"),
            (1, "desktop", "Desktop Studio", "monitor", theme.MAGENTA, "wallpapers", "Open"),
            (1, "sync", "Cloud Sync", "refresh", theme.GREEN, "sync", "Open"),
            (1, "updates", "Updates", "download", theme.AMBER, "updates", "Open"),
        ]
        for row, key, title, icon, color, page, action in spec:
            card = Card(t(title), icon, color)
            pill = StatusPill("", "pending")
            card.body.addWidget(pill)
            grid = KeyValueGrid(140)
            card.body.addWidget(grid)
            card.body.addWidget(button(t(action), on_click=lambda _=False, p=page: self.win.go(p)), 0)
            self.cards[key] = (pill, grid)
            rows[row].add(card, 1)
        for r in rows:
            root.addWidget(r)
        root.addStretch(1)
        ctl.games_changed.connect(self.refresh)

    def refresh(self) -> None:
        self._license()
        self._games()
        self._booster()
        self._desktop()
        self._sync()
        self._updates()

    def _set(self, key: str, state: str, text: str, rows: list) -> None:
        pill, grid = self.cards[key]
        pill.set(text, state)
        grid.set_rows(rows)

    def _license(self) -> None:
        view = self.win.license.view()
        rows = [(t("Type"), t(view.type_name))]
        if view.lifetime:
            rows.append((t("Valid"), t("Lifetime, also offline")))
        elif view.remaining is not None:
            days = view.remaining / 86400
            rows.append((t("Remaining"), t("{d:.1f} days", d=days) if days >= 1 else
                         t("{h:.0f} hours", h=view.remaining / 3600)))
        from akeno.ui.account_ui import STATUS_NAME
        rows.append((t("Status"), t(view.message)))
        self._set("license", "ok" if view.unlocked else "warn", t(STATUS_NAME.get(view.status, view.status.value)),
                  rows)

    def _games(self) -> None:
        games = self.s.library.games()
        meta = self.s.db.all_game_meta()
        favourites = sum(1 for m in meta.values() if m.get("favorite"))
        last = max((m for m in meta.values() if m.get("last_played")), key=lambda m: m["last_played"], default=None)
        names = {g.id: g.name for g in games}
        rows = [(t("Games"), str(len(games))), (t("Favourites"), str(favourites))]
        if last:
            rows.append((t("Last played"), f"{names.get(last['game_id'], last['game_id'])} · "
                                           f"{str(last['last_played'])[:16].replace('T', ' ')}"))
        installs = len({x["game_id"] for x in self.s.db.transactions() if x["state"] == "installed"})
        rows.append((t("DLSS 5 installs"), str(installs)))
        self._set("games", "ok" if games else "pending",
                  t("{n} game(s) in the library", n=len(games)) if games else t("No game in the library yet"), rows)

    def _booster(self) -> None:
        configured = [gid for gid in self.s.boost.state.get("games", {}) if self.s.boost.state["games"][gid].get(
            "profile")]
        running = self.win.sessions.running() if getattr(self.win, "sessions", None) else {}
        rows = [(t("Games with a profile"), str(len(configured))),
                (t("Intelligent game mode"), t("on") if self.s.settings.get("intelligent_game_mode") else t("off"))]
        benches = self.s.db.benchmarks()
        if benches and benches[0].get("stats", {}).get("avg_fps") is not None:
            rows.append((t("Last measured"), f"{benches[0]['stats']['avg_fps']:.0f} FPS · {benches[0]['label']}"))
        self._set("booster", "running" if running else ("ok" if configured else "pending"),
                  t("A game is running") if running else t("{n} game(s) boosted", n=len(configured)), rows)

    def _desktop(self) -> None:
        cfg = desktop_config.load()
        walls = sum(1 for w in cfg["wallpapers"].values() if w.get("enabled") and w.get("playlist"))
        rows = [(t("Wallpapers"), str(walls)), (t("Dock"), t("on") if cfg["dock"].get("enabled") else t("off")),
                (t("Widgets"), str(len(cfg["widgets"])))]
        on = cfg.get("enabled", True) and (walls or cfg["dock"].get("enabled") or cfg["widgets"])
        self._set("desktop", "ok" if on else "pending", t("Switched on") if on else t("Off"), rows)

    def _sync(self) -> None:
        st = self.win.sync.state
        rows = [(t("Last sync"), st["last_sync"] or "—")]
        if st["conflicts"]:
            self._set("sync", "warn", t("{n} conflict(s) to decide", n=len(st["conflicts"])), rows)
        elif st["enabled"] and st["folder"]:
            self._set("sync", "ok", t("In sync") if st["last_sync"] else t("Not synced yet"), rows)
        else:
            self._set("sync", "pending", t("Off"), rows)

    def _updates(self) -> None:
        page = self.win.pages.get("updates")
        state = page.svc.state() if page is not None else {}
        last = state.get("last_check")
        rows = [(t("Installed version"), __version__),
                (t("Last check"), time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "—")]
        self._set("updates", "pending", t("Version {v}", v=__version__), rows)

    def on_show(self) -> None:
        self.refresh()
