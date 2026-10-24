"""Installed Mods: every game where AKENO manages a DLSS 5 stack, with its versions and quick actions."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.stack import configs
from akeno.ui import theme
from akeno.ui.stack_panel import error_text
from akeno.ui.widgets import PageHeader, Card, EmptyState, KeyValueGrid, StatusPill, button, clear_layout, confirm, label


class InstalledPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.window = ctl, ctl.s, window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(PageHeader(t("Installed Mods"), t("Every installation is one reversible transaction chain: removing a mod puts back the "
                              "exact original files.")))
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        lay.addLayout(self.body)
        self.status = label("", "dim", wrap=True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        ctl.installs_changed.connect(self.refresh)
        ctl.games_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_) -> None:
        clear_layout(self.body)
        rows = self.s.stack.installed()
        if not rows:
            self.body.addWidget(EmptyState("layers", t("No mods installed yet"),
                                           t("Open My Games, choose a game and click Install DLSS 5."),
                                           button(t("Open My Games"), "gamepad", "primary",
                                                  on_click=lambda: self.ctl.navigate.emit("library"))))
            return
        cat = self.s.stack.catalog
        for tx in rows:
            p = tx["params"]
            if tx["backend_id"] == "stack":
                recipe = cat.recipes.get(p.get("recipe"))
                title = t(recipe.name) if recipe else p.get("recipe", "?")
                versions = []
                for role, key in (p.get("versions") or {}).items():
                    v = cat.version(key, self.s.stack.store.local_versions())
                    versions.append(f"{t(v.display) if v else key}")
                state = ("ok", t("Installed"))
            else:
                title = t("Installed by an older AKENO version") + f" ({tx['backend_id']})"
                versions = [tx.get("package_version") or ""]
                state = ("warn", t("Older installation"))
            card = Card(tx["game_name"], "gamepad", theme.VIOLET, right=StatusPill(state[1], state[0]))
            grid = KeyValueGrid(150)
            grid.set_rows([(t("Method"), title),
                           (t("Components"), "\n".join(x for x in versions if x) or "—"),
                           (t("Preset"), t(configs.PRESET_LABELS.get(p.get("preset"), "—"))),
                           (t("Folder"), tx["target_dir"]),
                           (t("Installed"), (tx.get("finished_at") or tx.get("created_at") or "")[:16].replace("T", " "))])
            card.body.addWidget(grid)
            row = QHBoxLayout()
            row.addWidget(button(t("Open game"), "gamepad", on_click=lambda _=False, gid=tx["game_id"]: self._open(gid)))
            row.addWidget(button(t("Remove mod"), "trash", "danger",
                                 on_click=lambda _=False, gid=tx["game_id"]: self._remove(gid)))
            row.addStretch(1)
            card.body.addLayout(row)
            self.body.addWidget(card)

    def _open(self, game_id: str) -> None:
        g = self.s.library.get(game_id)
        self.ctl.select_game(game_id)
        self.ctl.navigate.emit("fivem" if g is not None and g.launcher == "fivem" else "library")

    def _remove(self, game_id: str, force: bool = False) -> None:
        if not force and not confirm(self, t("Remove mod"), t("Remove the mod completely and put the original files back?")):
            return
        stack = self.s.stack
        legacy_only = stack.active(game_id) is None

        def work():
            return stack.remove_legacy(game_id) if legacy_only else stack.uninstall(game_id, force=force)

        def done(res):
            if not res.ok and res.conflicts and not force and not any("backup" in c.reason for c in res.conflicts):
                detail = "\n".join(f"• {c.rel_path}: {t(c.reason)}" for c in res.conflicts[:10])
                if confirm(self, t("Remove mod"), t("Some installed files were changed after installation."), detail,
                           t("Remove anyway")):
                    self._remove(game_id, force=True)
                return
            self.status.setText("\n".join(t(m) for m in res.messages[-3:]))
            self.ctl.installs_changed.emit()
        self.ctl.run("remove:" + game_id, work, done, lambda exc: self.status.setText(error_text(exc)))
