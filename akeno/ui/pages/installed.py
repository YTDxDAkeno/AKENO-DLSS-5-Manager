"""Installed Mods: the mod library of every supported game (installed by AKENO or not) and AKENO's own DLSS 5
installations with their one-click restore.

The library comes from the Universal Mod Scanner (read-only): per game its mods, their source (AKENO, external, game
client, server cache), loader, version, how each was identified, and the conflicts the metadata proves. Externally
installed mods are only shown; AKENO never changes them because it found them. AKENO's installations below keep
their transaction chain: removing one puts back the exact original files."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.stack import configs
from akeno.system import commands
from akeno.system.mods.model import SOURCE_TEXT
from akeno.system.model import human_bytes
from akeno.ui import theme
from akeno.ui.stack_panel import error_text
from akeno.ui.system_ui import FindingList, TaskBar, fill_table, make_table, open_link, selected_data
from akeno.ui.widgets import (Card, EmptyState, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow, StatusPill,
                              button, clear_layout, confirm, game_cover, info, label)

FILTERS = (("all", "All games"), ("warnings", "With warnings"), ("akeno", "Managed by AKENO"),
           ("external", "External mods"), ("minecraft", "Minecraft"), ("fivem", "FiveM"), ("game", "Other games"))
SORTS = (("name", "Name"), ("count", "Most mods"), ("warnings", "Most warnings"), ("scanned", "Last scanned"))
PLATFORM_TEXT = {"minecraft": "Minecraft Java Edition", "fivem": "FiveM", "game": "Game"}
KIND_TEXT = {"minecraft_mod": "Minecraft mod", "asi_plugin": "ASI plugin", "reshade": "ReShade",
             "optiscaler": "OptiScaler", "asi_loader": "ASI loader", "specialk": "Special K", "dxvk": "DXVK",
             "unknown_proxy": "Unknown proxy DLL", "reshade_addon": "ReShade add-on", "bepinex_plugin": "BepInEx plugin",
             "bepinex_patcher": "BepInEx patcher", "melonloader_mod": "MelonLoader mod",
             "melonloader_plugin": "MelonLoader plugin", "ue_pak": "Unreal pak mod", "ue4ss_mod": "UE4SS mod",
             "rpf": "RPF archive", "server_cache": "Server cache", "unknown": "Unknown",
             "optiscaler_config": "OptiScaler settings", "reshade_config": "ReShade settings",
             "dlss5_feeder": "DLSS5-Feeder", "neural_consumer": "DLSS 5 consumer", "disabled_mod": "Disabled file",
             "akeno_marker": "AKENO marker", "optiscaler_nr_forwarder": "OptiScaler NR forwarder",
             "dlssg_to_fsr3": "DLSSG to FSR3"}
COVER = QSize(112, 63)


class InstalledPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.window = ctl, ctl.s, window
        self.scanner = self.s.system_tools.mods
        self.current_key: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.b_scan = button(t("Scan for mods"), "search", "primary", on_click=self.scan)
        self.b_add = button(t("Add Minecraft folder…"), "plus", "ghost", on_click=self._add_folder)
        lay.addWidget(PageHeader(t("Installed Mods"), t("Every mod AKENO finds in your games, installed by AKENO or "
                                                        "by you. Mods AKENO did not install are only shown: AKENO never "
                                                        "changes, updates or deletes them."), [self.b_add, self.b_scan]))
        self.task = TaskBar(ctl, "mods:scan")
        lay.addWidget(self.task)

        # -- the library -------------------------------------------------------------------------------------------
        self.library = QWidget()
        ll = QVBoxLayout(self.library)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        tools = FlowLayout(spacing=8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search games and mods"))
        self.search.setAccessibleName(t("Search"))
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(self._render_list)
        self.filter = QComboBox()
        self.filter.setAccessibleName(t("Filter"))
        for key, text in FILTERS:
            self.filter.addItem(t(text), key)
        self.filter.currentIndexChanged.connect(self._render_list)
        self.sort = QComboBox()
        self.sort.setAccessibleName(t("Sort"))
        for key, text in SORTS:
            self.sort.addItem(t(text), key)
        self.sort.currentIndexChanged.connect(self._render_list)
        for w in (self.search, self.filter, self.sort):
            tools.addWidget(w)
        holder = QWidget()
        holder.setLayout(tools)
        ll.addWidget(holder)
        self.summary = label("", "muted", wrap=True)
        ll.addWidget(self.summary)
        row = ResponsiveRow(spacing=14)
        left = Card(t("Games with mods"), "layers")
        self.list = QListWidget()
        self.list.setIconSize(COVER)
        self.list.setMinimumHeight(360)
        self.list.setMinimumWidth(300)
        self.list.setSpacing(2)
        self.list.setAccessibleName(t("Games with mods"))
        self.list.currentItemChanged.connect(self._selected)
        left.body.addWidget(self.list)
        row.add(left, 2)
        self.detail = Card(t("Details"), "eye")
        self.d_title = label("", "h2", wrap=True)
        self.detail.body.addWidget(self.d_title)
        self.d_grid = KeyValueGrid(150)
        self.detail.body.addWidget(self.d_grid)
        self.d_notes = label("", "muted", wrap=True)
        self.detail.body.addWidget(self.d_notes)
        self.d_table = make_table([t("Mod"), t("Version"), t("Loader"), t("Source"), t("Identified by"),
                                   t("File")], 260)
        self.d_table.itemSelectionChanged.connect(self._mod_selected)
        self.detail.body.addWidget(self.d_table)
        self.d_mod = label("", "dim", wrap=True, selectable=True)
        self.detail.body.addWidget(self.d_mod)
        self.b_open = button(t("Open file location"), "folder", on_click=self._open_mod)
        self.b_folder = button(t("Open game folder"), "folder", "ghost", on_click=self._open_folder)
        self.b_rescan = button(t("Scan again"), "refresh", "ghost", on_click=self._rescan)
        self.b_security = button(t("Check with Security Center"), "shield", "ghost", on_click=self._security)
        self.b_game = button(t("Open game"), "gamepad", "ghost", on_click=self._open_game)
        actions = FlowLayout(spacing=8)
        for b in (self.b_open, self.b_folder, self.b_rescan, self.b_security, self.b_game):
            actions.addWidget(b)
        ah = QWidget()
        ah.setLayout(actions)
        self.detail.body.addWidget(ah)
        self.detail.body.addWidget(label(t("FINDINGS"), "overline"))
        self.d_findings = FindingList(self._link, t("No conflicts found in this game's mods."))
        self.detail.body.addWidget(self.d_findings)
        row.add(self.detail, 3)
        ll.addWidget(row)
        lay.addWidget(self.library)

        self.empty = EmptyState("layers", t("No mods found yet"),
                                t("Scan for mods to see what is installed in your games and Minecraft instances. "
                                  "Or install DLSS 5 for a game in My Games."),
                                button(t("Scan for mods"), "search", "primary", on_click=self.scan))
        self.empty_box = QWidget()
        eb = QVBoxLayout(self.empty_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.addStretch(1)
        eb.addWidget(self.empty)
        eb.addStretch(1)
        lay.addWidget(self.empty_box, 1)

        # -- AKENO's own installations (unchanged behaviour: one reversible transaction chain each) ---------------
        self.akeno_card = Card(t("DLSS 5 installations by AKENO"), "sparkle")
        self.akeno_card.body.addWidget(label(t("Every installation is one reversible transaction chain: removing a mod "
                                               "puts back the exact original files."), "dim", wrap=True))
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.akeno_card.body.addLayout(self.body)
        lay.addWidget(self.akeno_card)
        self.status = label("", "dim", wrap=True)
        lay.addWidget(self.status)
        lay.addStretch(0)
        ctl.installs_changed.connect(self.refresh)
        ctl.games_changed.connect(self.refresh)
        self.refresh()
        self._render_list()

    # -- AKENO installations ----------------------------------------------------------------------------------------
    def refresh(self, *_) -> None:
        clear_layout(self.body)
        rows = self.s.stack.installed()
        self.akeno_card.setVisible(bool(rows))
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
        self._update_empty()

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

    # -- the library ------------------------------------------------------------------------------------------------
    def on_show(self) -> None:
        self._update_empty()
        if self.scanner.last_scan() is None and not self.task.running and self.s.library.games():
            self.scan()

    def scan(self) -> None:
        self.b_scan.setEnabled(False)
        if not self.task.start(self.scanner.scan, self._scanned, self._failed, text=t("Scanning for mods…"),
                               timeout=1800):
            self.b_scan.setEnabled(True)

    def _failed(self, exc) -> None:
        self.b_scan.setEnabled(True)
        self.task.show_message(str(exc), error=True)

    def _scanned(self, groups) -> None:
        self.b_scan.setEnabled(True)
        self._render_list()

    def _groups(self) -> list:
        groups = list(self.scanner.groups)
        q = self.search.text().strip().lower()
        f = self.filter.currentData()
        if q:
            groups = [g for g in groups if q in str(t(g.title)).lower()
                      or any(q in (m.name or "").lower() or q in (m.mod_id or "").lower() for m in g.mods)]
        if f == "warnings":
            groups = [g for g in groups if g.warnings]
        elif f == "akeno":
            groups = [g for g in groups if any(m.source == "akeno" for m in g.mods)]
        elif f == "external":
            groups = [g for g in groups if any(m.source == "external" for m in g.mods)]
        elif f in ("minecraft", "fivem", "game"):
            groups = [g for g in groups if g.platform == f]
        key = self.sort.currentData()
        if key == "count":
            groups.sort(key=lambda g: -len(g.mods))
        elif key == "warnings":
            groups.sort(key=lambda g: -g.warnings)
        elif key == "scanned":
            groups.sort(key=lambda g: -g.scanned_at)
        else:
            groups.sort(key=lambda g: str(t(g.title)).lower())
        return groups

    def _cover(self, g) -> QIcon:
        game = self.s.library.get(g.game_id) if g.game_id else None
        if game is None:
            game = SimpleNamespace(name="Minecraft" if g.platform == "minecraft" else str(t(g.title)),
                                   artwork_path=None)
        return QIcon(game_cover(game, COVER.width(), COVER.height(), 6))

    def _render_list(self, *_) -> None:
        groups = self._groups()
        total = len(self.scanner.groups)
        mods = sum(len(g.mods) for g in self.scanner.groups)
        last = self.scanner.last_scan()
        self.summary.setText(t("{n} games, {m} mods; last scan {when}.", n=total, m=mods,
                               when=time.strftime("%Y-%m-%d %H:%M", time.localtime(last))) if last else
                             t("Not scanned yet."))
        self.list.blockSignals(True)
        self.list.clear()
        for g in groups:
            akeno = sum(1 for m in g.mods if m.source == "akeno")
            parts = [t("1 mod") if len(g.mods) == 1 else t("{n} mods", n=len(g.mods))]
            if akeno:
                parts.append(t("{n} by AKENO", n=akeno))
            if g.loader:
                parts.append(g.loader)
            if g.warnings:
                parts.append(t("1 warning") if g.warnings == 1 else t("{n} warnings", n=g.warnings))
            item = QListWidgetItem(self._cover(g), f"{t(g.title)}\n" + " · ".join(parts))
            item.setData(Qt.ItemDataRole.UserRole, g.key)
            item.setSizeHint(QSize(260, COVER.height() + 12))
            item.setToolTip(g.root)
            self.list.addItem(item)
        self.list.blockSignals(False)
        keys = [g.key for g in groups]
        target = self.current_key if self.current_key in keys else (keys[0] if keys else None)
        if target:
            self.list.setCurrentRow(keys.index(target))
            self._show(target)
        else:
            self._show(None)
        self._update_empty()

    def _update_empty(self) -> None:
        has_mods = bool(self.scanner.groups)
        show_empty = not has_mods and self.akeno_card.isHidden()
        self.library.setVisible(has_mods)
        self.empty_box.setVisible(show_empty)
        if show_empty:
            host = getattr(self.window, "hosts", {}).get("installed")
            view = host.scroll.viewport().height() if host is not None else 600
            self.empty_box.setMinimumHeight(max(360, view - 160))      # centred in the visible content area

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.empty_box.isHidden():
            self._update_empty()

    def _selected(self, cur, _prev=None) -> None:
        if cur is not None:
            self._show(cur.data(Qt.ItemDataRole.UserRole))

    def group(self, key: str | None):
        return next((g for g in self.scanner.groups if g.key == key), None)

    def _show(self, key: str | None) -> None:
        self.current_key = key
        g = self.group(key)
        for b in (self.b_open, self.b_folder, self.b_rescan, self.b_security, self.b_game):
            b.setEnabled(g is not None)
        if g is None:
            self.d_title.setText(t("Select a game"))
            self.d_grid.set_rows([])
            fill_table(self.d_table, [])
            self.d_findings.set_findings([])
            self.d_notes.setText("")
            return
        self.d_title.setText(t(g.title))
        rows = [(t("Platform"), t(PLATFORM_TEXT.get(g.platform, g.platform)))]
        if g.launcher:
            rows.append((t("Launcher"), g.launcher))
        if g.game_version:
            rows.append((t("Game version"), g.game_version))
        if g.loader:
            rows.append((t("Mod loader"), g.loader + (f" {g.loader_version}" if g.loader_version else "")))
        rows += [(t("Folder"), g.root),
                 (t("Last scan"), time.strftime("%Y-%m-%d %H:%M", time.localtime(g.scanned_at))),
                 (t("Scan"), t({"ok": "Complete", "partial": "Some files could not be read",
                                "error": "Failed"}.get(g.status, g.status)))]
        self.d_grid.set_rows(rows, elide=False)
        self.d_notes.setText("\n".join(t(n) for n in g.notes + [str(e) for e in g.errors[:5]]))
        q = self.search.text().strip().lower()
        mods = [m for m in g.mods if not q or q in (m.name or "").lower() or q in (m.mod_id or "").lower()
                or q in str(t(g.title)).lower()]
        fill_table(self.d_table, [[m.name + ("" if m.enabled else "  " + t("(disabled)")),
                                   m.version or "—", m.loader or "—",
                                   (t(SOURCE_TEXT.get(m.source, m.source)), "info" if m.source == "akeno" else None),
                                   t(m.detection) if m.detection else ("—" if not m.error else t("unreadable")),
                                   os.path.basename(m.path)] for m in mods],
                   [m.path for m in mods],
                   [[m.mod_id, None, None, None, m.error or None, m.path] for m in mods])
        self.d_findings.set_findings(g.findings)
        self.b_game.setEnabled(bool(g.game_id and self.s.library.get(g.game_id)))
        self._mod_selected()

    def _current_mod(self):
        g = self.group(self.current_key)
        path = selected_data(self.d_table)
        return next((m for m in g.mods if m.path == path), None) if g and path else None

    def _mod_selected(self) -> None:
        m = self._current_mod()
        self.b_open.setEnabled(m is not None)
        self.b_security.setEnabled(m is not None and m.source != "server")
        if m is None:
            self.d_mod.setText(t("Select a mod to see its details."))
            return
        lines = [t("Type: {kind}", kind=t(KIND_TEXT.get(m.kind, m.kind))), t("File: {path}", path=m.path),
                 t("Size: {size}", size=human_bytes(m.size))]
        if m.mod_id:
            lines.append(t("Mod id: {id}", id=m.mod_id))
        if m.minecraft:
            lines.append(t("Minecraft: {v}", v=m.minecraft))
        req = [d for d in m.depends if d.kind == "required" and d.id not in ("minecraft", "java")]
        if req:
            lines.append(t("Requires: {deps}", deps=", ".join(f"{d.id} {d.versions if isinstance(d.versions, str) else ' | '.join(d.versions)}"
                                                              for d in req[:12])))
        if m.error:
            lines.append(t("Could not be read: {why}", why=m.error))
        if m.source == "external":
            lines.append(t("Installed outside AKENO: AKENO does not change this file."))
        self.d_mod.setText("\n".join(lines))

    def _open_mod(self) -> None:
        m = self._current_mod()
        if m:
            commands.open_location(m.path, select=os.path.isfile(m.path))

    def _open_folder(self) -> None:
        g = self.group(self.current_key)
        if g:
            commands.open_location(g.root)

    def _open_game(self) -> None:
        g = self.group(self.current_key)
        if g and g.game_id:
            self._open(g.game_id)

    def _rescan(self) -> None:
        key = self.current_key
        if key:
            self.task.start(lambda ctx: self.scanner.rescan(key, ctx), lambda _g: self._render_list(),
                            text=t("Scanning again…"), timeout=600)

    def _security(self) -> None:
        m = self._current_mod()
        if m is not None:
            open_link(self.window, "security", "file:" + m.path)

    def _link(self, module: str, target: str = "") -> None:
        open_link(self.window, module, target)

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("Choose a Minecraft game folder (with a mods folder)"))
        if not d:
            return
        try:
            self.scanner.add_minecraft_folder(d)
        except ValueError as exc:
            info(self, t("Installed Mods"), t(str(exc)), error=True)
            return
        self.scan()
