"""The welcome screen's "Restore game files" window: everything AKENO changed can be put back without an active
license (see ``akeno/recovery.py``). Nothing here installs, applies or starts anything."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui import workers
from akeno.ui.widgets import Card, KeyValueGrid, Spinner, button, clear_layout, confirm, label


class RecoveryDialog(QDialog):
    def __init__(self, services_factory: Callable[[], object], parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Restore game files"))
        self.setMinimumSize(640, 520)
        self._factory = services_factory
        self.rec = None
        self.busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label(t("Restore game files"), "h2"))
        lay.addWidget(label(t("Puts back what AKENO changed on this PC: the original files of games where AKENO "
                              "installed a mod, FPS Booster settings and programs Network Gaming Mode paused. This "
                              "works without an active license; it does not unlock any other feature."), "dim",
                            wrap=True))
        top = QHBoxLayout()
        self.spinner = Spinner(18)
        top.addWidget(self.spinner)
        self.status = label(t("Reading what AKENO changed…"), "muted", wrap=True)
        top.addWidget(self.status, 1)
        lay.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button(t("Close"), on_click=self.close))
        lay.addLayout(row)
        self._load()

    def _run(self, fn, done, text: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.spinner.start()
        self.status.setText(text)

        def finish(res):
            self.busy = False
            self.spinner.stop()
            done(res)

        def failed(exc):
            self.busy = False
            self.spinner.stop()
            self.status.setText(t("Failed: {error}", error=str(exc)))
        workers.run(fn, finish, failed)

    def _load(self) -> None:
        def work():
            if self.rec is None:
                from akeno.recovery import Recovery
                self.rec = Recovery(self._factory())
            return self.rec.items()
        self._run(work, self._show, t("Reading what AKENO changed…"))

    def _show(self, items: dict) -> None:
        clear_layout(self.body)
        n = len(items["installs"]) + len(items["boost"]) + (1 if items["paused"] else 0)
        self.status.setText(t("Nothing to restore: AKENO has no changes on this PC.") if not n else
                            t("Choose what to put back."))
        for it in items["installs"]:
            c = Card(it["game"], "gamepad")
            grid = KeyValueGrid(120)
            grid.set_rows([(t("Folder"), it["folder"]), (t("Installed"), it["installed"] or "—")], elide=True)
            c.body.addWidget(grid)
            c.body.addWidget(button(t("Restore original files"), "restore", "primary",
                                    on_click=lambda _=False, gid=it["game_id"], name=it["game"]: self._install(gid, name)))
            self.body.addWidget(c)
        for it in items["boost"]:
            c = Card(it["game"], "gauge")
            c.body.addWidget(label(t("FPS Booster changed Windows settings for this game."), "dim", wrap=True))
            c.body.addWidget(button(t("Undo FPS Booster changes"), "restore",
                                    on_click=lambda _=False, gid=it["game_id"]: self._boost(gid)))
            self.body.addWidget(c)
        if items["paused"]:
            c = Card(t("Paused programs"), "pause")
            c.body.addWidget(label(t("Programs paused by Network Gaming Mode: {n}", n=len(items["paused"])), "dim",
                                   wrap=True))
            c.body.addWidget(button(t("Resume them"), "play", on_click=self._paused))
            self.body.addWidget(c)
        self.body.addStretch(1)

    def _install(self, game_id: str, name: str, force: bool = False) -> None:
        if not force and not confirm(self, t("Restore original files"),
                                     t("Remove the mod from {game} and put the original files back?", game=name)):
            return

        def done(res):
            if not res.ok and res.conflicts and not force and not any("backup" in c.reason for c in res.conflicts):
                detail = "\n".join(f"• {c.rel_path}: {t(c.reason)}" for c in res.conflicts[:10])
                if confirm(self, t("Restore original files"), t("Some installed files were changed after "
                                                                  "installation."), detail, t("Remove anyway")):
                    self._install(game_id, name, force=True)
                    return
            self._load()
            self.status.setText("\n".join(t(m) for m in res.messages[-3:]) or t("Done."))
        self._run(lambda: self.rec.restore_install(game_id, force), done, t("Restoring the original files…"))

    def _boost(self, game_id: str) -> None:
        self._run(lambda: self.rec.restore_boost(game_id), lambda _: (self._load()), t("Undoing FPS Booster changes…"))

    def _paused(self) -> None:
        self._run(self.rec.resume_paused, lambda _: self._load(), t("Resuming programs…"))

    def closeEvent(self, e):
        if self.busy:                                   # a restore runs to its end; it is never cut off halfway
            e.ignore()
            return
        super().closeEvent(e)
