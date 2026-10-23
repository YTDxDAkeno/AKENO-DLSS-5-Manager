"""FiveM Legacy: the client, the private-server connection and the plugin-folder DLSS 5 stack."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.stack_panel import GamePanel, error_text
from akeno.ui.widgets import Card, EmptyState, KeyValueGrid, StatusPill, button, clear_layout, info, label

KIND_TEXT = {"graphics_hook": "Graphics hook", "reshade_addon": "ReShade add-on", "neural_consumer": "DLSS 5 add-on",
             "asi_plugin": "ASI plug-in", "plugin_file": "Plug-in file", "reshade_config": "ReShade settings"}


class FiveMPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s = ctl, ctl.s
        self.panel: GamePanel | None = None
        self.game_id: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label(t("FiveM Legacy"), "h1"))
        top.addStretch(1)
        top.addWidget(button(t("Locate FiveM…"), "folder", on_click=self._locate))
        lay.addLayout(top)
        lay.addWidget(label(t("DLSS 5 for FiveM installs only into FiveM.app\\plugins (the folder FiveM loads graphics "
                              "mods from). Your GTA V installation is never changed."), "dim", wrap=True))
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        lay.addLayout(self.body)
        ctl.games_changed.connect(self.refresh)
        ctl.installs_changed.connect(self._client)
        self.refresh()

    def _fivem_game(self):
        return next((g for g in self.s.library.games() if g.launcher == "fivem"), None)

    def refresh(self, *_) -> None:
        g = self._fivem_game()
        if g is not None and self.panel is not None and self.game_id == g.id:
            self._client()
            return
        if self.panel is not None:
            self.panel.dispose()
            self.panel = None
        clear_layout(self.body)
        self.game_id = g.id if g else None
        if g is None:
            self.body.addWidget(EmptyState("fivem", t("FiveM was not found"),
                                           t("AKENO looks in %LOCALAPPDATA%\\FiveM. If FiveM is installed elsewhere, "
                                             "choose its folder (the one with FiveM.exe)."),
                                           button(t("Locate FiveM…"), "folder", "primary", on_click=self._locate)))
            return
        self.client = Card(t("FiveM client"), "fivem", theme.CYAN)
        self.client_grid = KeyValueGrid(170)
        self.client.body.addWidget(self.client_grid)
        row = QHBoxLayout()
        row.addWidget(button(t("Open plugins folder"), "folder", on_click=self._open_plugins))
        row.addWidget(button(t("Check again"), "refresh", on_click=self._reinspect))
        row.addStretch(1)
        self.client.body.addLayout(row)
        self.body.addWidget(self.client)
        server = Card(t("Your server"), "globe", theme.VIOLET)
        server.body.addWidget(label(t("Client graphics mods are a client-side choice. FiveM's pure mode (sv_pureLevel) "
                                      "only checks game archives (RPF files); it does not block FiveM.app\\plugins."),
                                    "dim", wrap=True))
        srow = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText(t("Server address, e.g. 192.168.1.20:30120 or cfx.re/join/abc123"))
        self.address.setAccessibleName(t("Server address"))
        self.address.setText(self.s.stack.selection(g.id).get("server") or "")
        srow.addWidget(self.address, 1)
        srow.addWidget(button(t("Launch and connect"), "play", "primary", on_click=self._connect))
        server.body.addLayout(srow)
        crow = QHBoxLayout()
        crow.addWidget(button(t("Check a server.cfg…"), "file", on_click=self._check_cfg))
        crow.addStretch(1)
        server.body.addLayout(crow)
        self.cfg_result = label("", "dim", wrap=True)
        server.body.addWidget(self.cfg_result)
        self.body.addWidget(server)
        self.panel = GamePanel(self.ctl, g.id, self, fivem=True)
        self.body.addWidget(self.panel)
        self._client()

    def _client(self, *_) -> None:
        g = self.s.library.get(self.game_id) if self.game_id else None
        if g is None or not hasattr(self, "client_grid"):
            return
        inst = self.s.library.fivem_installations.get(g.id)
        if inst is None:
            from akeno.fivem.discovery import inspect_installation
            inst = inspect_installation(g.install_dir)
        if inst is None:
            self.client_grid.set_rows([(t("Status"), t("FiveM is no longer at {path}.", path=g.install_dir))])
            return
        from akeno.stack.targets import FiveMTarget
        target = FiveMTarget(g, inst)
        ack = target.reshade_ack() == "present"
        edition = {"legacy": t("GTA V Legacy"), "enhanced": t("GTA V Enhanced (not used by FiveM Legacy)"),
                   "unknown": t("Unknown — set the GTA V folder in FiveM")}[inst.edition.value]
        plugins = [f"{f.path.name} — {t(KIND_TEXT.get(f.kind, f.kind))}" for f in inst.inventory
                   if f.path.parent == inst.plugin_dir]
        self.client_grid.set_rows([
            (t("FiveM folder"), str(inst.root)),
            (t("GTA V folder"), f"{inst.gta_dir or t('Unknown')} · {edition}"),
            (t("Renderer"), t("Direct3D 11 (FiveM Legacy)")),
            (t("Saved game build"), inst.saved_build or t("Unknown")),
            (t("Graphics plug-ins"), "\n".join(plugins) or t("None")),
            (t("ReShade notice"), StatusPill(t("Accepted in CitizenFX.ini") if ack else
                                             t("Accepted automatically during installation"), "ok" if ack else "info")),
        ])

    def _open_plugins(self) -> None:
        g = self.s.library.get(self.game_id)
        inst = self.s.library.fivem_installations.get(g.id) if g else None
        if inst:
            inst.plugin_dir.mkdir(exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(inst.plugin_dir)))

    def _reinspect(self) -> None:
        gid = self.game_id
        self.ctl.run("analyze", lambda: self.s.library.reanalyze(gid),
                     lambda _: (self._client(), self.panel and self.panel.refresh()))

    def _locate(self) -> None:
        path = QFileDialog.getExistingDirectory(self, t("Select the FiveM folder (with FiveM.exe)"))
        if not path:
            return

        def done(game):
            if game.launcher != "fivem":
                info(self, t("FiveM"), t("This folder is not a FiveM installation (FiveM.exe and FiveM.app)."),
                     error=True)
                self.s.library.remove(game.id)
            self.ctl.games_changed.emit()
        self.ctl.run("games", lambda: self.s.library.add_manual(path), done,
                     lambda exc: info(self, t("FiveM"), error_text(exc), error=True))

    def _connect(self) -> None:
        if self.panel is not None:
            self.panel.launch(self.address.text().strip())

    def _check_cfg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Select server.cfg"), "", "server.cfg (*.cfg);;*")
        if not path:
            return
        from akeno.fivem.server_config import inspect_config

        def done(report):
            if report["errors"]:
                self.cfg_result.setText(t("The configuration could not be read completely: {e}",
                                          e="; ".join(report["errors"][:2])))
                return
            pure = report["pure_level"]
            lines = [t("sv_pureLevel: {v}", v=pure if pure is not None else t("not set (0)"))]
            lines.append(t("Pure mode only validates game archives; graphics plug-ins in FiveM.app\\plugins keep working."))
            lines.append(t("{n} configuration file(s) read; nothing was changed.", n=len(report["files"])))
            self.cfg_result.setText("\n".join(lines))
        self.ctl.run("server_cfg", lambda: inspect_config(path), done,
                     lambda exc: self.cfg_result.setText(error_text(exc)))

    def on_show(self) -> None:
        if self.panel is not None:
            self.panel.refresh()
