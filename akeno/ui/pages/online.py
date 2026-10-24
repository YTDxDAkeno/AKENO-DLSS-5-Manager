"""ONLINE: Community (shared presets and themes, data only) and Cloud Sync (your data between your PCs through a
folder you choose). Neither uses the license server."""

from __future__ import annotations

import time
from pathlib import Path

import shiboken6
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit,
                               QListWidget, QListWidgetItem, QSpinBox, QVBoxLayout, QWidget)

from akeno import distribution
from akeno.i18n import t
from akeno.packages import index as cindex
from akeno.packages import packs
from akeno.sync import engine as sync
from akeno.ui import theme, workers
from akeno.ui.widgets import (Card, FlowLayout, KeyValueGrid, ResponsiveRow, StatusPill, SubCard, button,
                              clear_layout, confirm, info, label)

KIND_TEXT = {**packs.KINDS, "theme": "Theme"}
EXPORTS = (("dlss_preset", "DLSS 5 preset of a game…"), ("booster_profile", "FPS Booster profile of a game…"),
           ("widget_layout", "My widget layout…"))


def _row(*widgets) -> QWidget:
    holder = QWidget()
    flow = FlowLayout(spacing=8)
    for w in widgets:
        flow.addWidget(w)
    holder.setLayout(flow)
    return holder


class CommunityPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.dist = distribution.load()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label(t("Community"), "h1"))
        root.addWidget(label(t("Presets and themes other players share: DLSS 5 presets, FPS Booster profiles, widget "
                               "layouts and desktop themes. Packages are data only; nothing in them is ever run, "
                               "and using one changes nothing until you apply it yourself."), "dim", shrink=True))
        row = ResponsiveRow(spacing=14)
        mine = Card(t("My packages"), "layers", theme.VIOLET)
        self.mine = QVBoxLayout()
        self.mine.setSpacing(8)
        mine.body.addLayout(self.mine)
        self.export_kind = QComboBox()
        self.export_kind.setAccessibleName(t("Share"))
        for key, text in EXPORTS:
            self.export_kind.addItem(t(text), key)
        mine.body.addWidget(_row(button(t("Import package…"), "download", "primary", on_click=self._import_file),
                                 self.export_kind, button(t("Share…"), "export", on_click=self._export)))
        row.add(mine, 3)

        idx = Card(t("Community index"), "globe")
        idx.body.addWidget(label(t("A reviewed list in AKENO's public release repository. Packages get in through a "
                                   "reviewed pull request; reported ones are removed and blocked."), "dim",
                                 shrink=True))
        self.index_state = label("", "muted", shrink=True)
        idx.body.addWidget(self.index_state)
        self.index_list = QListWidget()
        self.index_list.setAccessibleName(t("Community index"))
        self.index_list.setMinimumHeight(220)
        idx.body.addWidget(self.index_list)
        idx.body.addWidget(_row(button(t("Refresh"), "refresh", on_click=self.refresh_index),
                                button(t("Download and import"), "download", on_click=self._download),
                                button(t("Report…"), "warn", on_click=self._report_entry)))
        row.add(idx, 2)
        root.addWidget(row)
        root.addStretch(1)
        self.load()

    # -- my packages ---------------------------------------------------------------------------------------------
    def load(self) -> None:
        clear_layout(self.mine)
        items = packs.library()
        if not items:
            self.mine.addWidget(label(t("No packages yet. Import a .akenopack file or download one from the "
                                        "community index."), "dim", shrink=True))
        for pack in items:
            self.mine.addWidget(self._card(pack))
        index = cindex.cached()
        self._fill_index(index)

    def _card(self, pack: packs.Pack) -> QWidget:
        card = SubCard()
        head = QHBoxLayout()
        head.addWidget(label(pack.name, "h2"), 1)
        head.addWidget(StatusPill(t("Reviewed") if pack.reviewed else t("Not reviewed"),
                                  "ok" if pack.reviewed else "warn"))
        card.lay.addLayout(head)
        parts = [t(KIND_TEXT[pack.kind])]
        if pack.data["game"]["name"]:
            parts.append(pack.data["game"]["name"])
        if pack.data["author"]:
            parts.append(t("by {a}", a=pack.data["author"]))
        if pack.data["license"]:
            parts.append(pack.data["license"])
        card.lay.addWidget(label(" · ".join(parts), "dim", shrink=True))
        if pack.data["description"]:
            card.lay.addWidget(label(pack.data["description"], "muted", shrink=True))
        card.lay.addWidget(_row(button(t("Use…"), "check", "primary", on_click=lambda _=False, p=pack: self.use(p)),
                                button(t("Report…"), "warn", on_click=lambda _=False, p=pack: self._report(
                                    p.name, p.sha256)),
                                button(t("Delete"), "trash", on_click=lambda _=False, p=pack: self._delete(p))))
        return card

    def choose_game(self, pack: packs.Pack):
        games = self.s.library.games()
        if not games:
            info(self, t("Community"), t("Add the game to My Games first."))
            return None
        matches = packs.matching_games(pack, games)
        if len(matches) == 1:
            return matches[0]
        names = [g.name for g in (matches or games)]
        name, ok = QInputDialog.getItem(self, t("Use…"), t("Game"), names, 0, False)
        return next((g for g in (matches or games) if g.name == name), None) if ok else None

    def use(self, pack: packs.Pack, game=None) -> str | None:
        if pack.kind != "widget_layout":
            game = game or self.choose_game(pack)
            if game is None:
                return None
        elif not confirm(self, t("Use…"), t("Replace your widgets with the {n} of “{p}”?",
                                            n=len(pack.data["content"]["widgets"]), p=pack.name),
                         ok_text=t("Replace")):
            return None
        try:
            what = packs.apply(pack, self.s, game.id if game else None)
        except (packs.PackError, ValueError) as exc:
            info(self, t("Use…"), t("The package could not be used."), str(exc), error=True)
            return None
        if what == "dlss":
            self.ctl.status.emit(t("“{p}” is selected for {g}. Install it on the game's page.", p=pack.name,
                                   g=game.name))
            self.ctl.games_changed.emit()
        elif what == "booster":
            self.ctl.status.emit(t("“{p}” is pre-selected for {g} on the FPS Booster page; nothing is applied yet.",
                                   p=pack.name, g=game.name))
        else:
            self.win.desktop.ensure()
            self.ctl.status.emit(t("Widget layout “{p}” is in use.", p=pack.name))
        return what

    def import_file(self, path: Path, source: str = "file", reviewed: bool = False) -> packs.Pack | None:
        if path.suffix.lower() == ".akenotheme":
            page = self.win.pages.get("themes")
            if page is not None:
                page.import_file(path)
                self.win.go("themes")
            return None
        try:
            pack = packs.install(path, source, reviewed, blocked=cindex.blocked())
        except (packs.PackError, OSError) as exc:
            info(self, t("Import package…"), t("This package cannot be imported."), str(exc), error=True)
            return None
        self.load()
        return pack

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Import package…"), "",
                                              t("AKENO packages (*.akenopack *.akenotheme)"))
        if path:
            self.import_file(Path(path))

    def export_pack(self, kind: str, out: Path, name: str, game=None) -> Path:
        if kind == "dlss_preset":
            sel = self.s.stack.selection(game.id)
            content = {"recipe": sel.get("recipe"), "preset": sel.get("preset") or "balanced",
                       "custom": sel.get("custom")}
        elif kind == "booster_profile":
            entry = self.s.boost.configured(game.id) or self.s.db.game_overrides(game.id).get("boost_choice") or {}
            content = {"profile": entry.get("profile"), "options": entry.get("options") or {}}
        else:
            from akeno.desktop import config as desktop_config
            content = {"widgets": desktop_config.load()["widgets"]}
        ids = [game.id] if game is not None and not game.id.startswith("manual:") else []
        return packs.export(kind, content, out, name, game_name=game.name if game else "", game_ids=ids)

    def _export(self) -> None:
        kind = self.export_kind.currentData()
        game = None
        if kind != "widget_layout":
            games = self.s.library.games()
            if not games:
                info(self, t("Share…"), t("Add the game to My Games first."))
                return
            name, ok = QInputDialog.getItem(self, t("Share…"), t("Game"), [g.name for g in games], 0, False)
            if not ok:
                return
            game = next(g for g in games if g.name == name)
        title, ok = QInputDialog.getText(self, t("Share…"), t("Name of the package"),
                                         text=f"{game.name} – {t(packs.KINDS[kind])}" if game else t("My widgets"))
        if not ok or not title.strip():
            return
        path, _ = QFileDialog.getSaveFileName(self, t("Share…"), f"{title.strip()}{packs.SUFFIX}",
                                              t("AKENO packages (*.akenopack *.akenotheme)"))
        if not path:
            return
        try:
            out = self.export_pack(kind, Path(path), title.strip(), game)
        except (packs.PackError, OSError, KeyError) as exc:
            info(self, t("Share…"), t("The package was not created."), str(exc), error=True)
            return
        info(self, t("Share…"), t("Package saved: {p}", p=str(out)))

    def _delete(self, pack: packs.Pack) -> None:
        if confirm(self, t("Delete"), t("Delete the package “{n}”?", n=pack.name), ok_text=t("Delete")):
            packs.remove(pack)
            self.load()

    def _report(self, name: str, sha: str) -> None:
        QDesktopServices.openUrl(QUrl(cindex.report_url(self.dist.release_repo, name, sha)))

    # -- index ---------------------------------------------------------------------------------------------------
    def _fill_index(self, index: cindex.Index | None) -> None:
        self.index_list.clear()
        if index is None:
            self.index_state.setText(t("Not loaded yet. Refresh asks the public release repository for the list."))
            return
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(index.fetched)) if index.fetched else "—"
        self.index_state.setText(t("{n} package(s), loaded {w}", n=len(index.entries), w=when))
        for entry in index.entries:
            item = QListWidgetItem(f"{entry.name}  ·  {t(KIND_TEXT[entry.kind])}" +
                                   (f"  ·  {t('by {a}', a=entry.author)}" if entry.author else ""))
            item.setToolTip(entry.description)
            item.setData(256, entry)
            self.index_list.addItem(item)

    def refresh_index(self) -> None:
        self.index_state.setText(t("Loading…"))

        def failed(exc):
            self.index_state.setText(t("The community index could not be loaded: {e}", e=str(exc)))
        workers.run(lambda: cindex.refresh(self.dist.community_index), self._fill_index, failed)

    def _selected(self) -> cindex.Entry | None:
        item = self.index_list.currentItem()
        return item.data(256) if item is not None else None

    def _download(self) -> None:
        entry = self._selected()
        if entry is None:
            return

        def done(path: Path):
            self.import_file(path, "index", reviewed=True)
            if entry.kind == "theme":
                return
            self.ctl.status.emit(t("“{p}” imported from the community index.", p=entry.name))

        def failed(exc):
            info(self, t("Download and import"), t("The download failed."), str(exc), error=True)
        workers.run(lambda: cindex.download(entry), done, failed)

    def _report_entry(self) -> None:
        entry = self._selected()
        if entry is not None:
            self._report(entry.name, entry.sha256)

    def on_show(self) -> None:
        self.load()


class CloudSyncPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.engine: sync.SyncEngine = window.sync
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label(t("Cloud Sync"), "h1"))
        root.addWidget(label(t("Your AKENO data on all your PCs: choose a folder that your cloud drive (OneDrive, "
                               "Dropbox, Google Drive) or NAS already syncs. AKENO writes small files there and merges "
                               "what your other PCs wrote. Licenses, tokens, passwords, program files, DLLs and game "
                               "files are never synced."), "dim", shrink=True))
        row = ResponsiveRow(spacing=14)
        setup = Card(t("Sync folder"), "folder")
        self.chk = QCheckBox(t("Sync this PC"))
        self.chk.toggled.connect(self._enabled)
        setup.body.addWidget(self.chk)
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setAccessibleName(t("Sync folder"))
        line = QHBoxLayout()
        line.addWidget(self.folder, 1)
        line.addWidget(button(t("Choose…"), "folder", on_click=self._choose))
        setup.body.addLayout(line)
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText(t("Optional: encrypt the folder with a passphrase"))
        self.passphrase.setAccessibleName(t("Passphrase"))
        setup.body.addWidget(self.passphrase)
        setup.body.addWidget(_row(button(t("Save passphrase"), "lock", on_click=self._save_passphrase)))
        setup.body.addWidget(label(t("Use the same passphrase on every PC. It is kept on this PC with Windows' data "
                                     "protection and never written to the folder."), "muted", shrink=True))
        self.quota = QSpinBox()
        self.quota.setRange(1, 1024)
        self.quota.setSuffix(" MB")
        self.quota.setAccessibleName(t("Space AKENO may use in the folder"))
        self.quota.valueChanged.connect(lambda v: None if self._loading else self.engine.configure(quota_mb=v))
        q = QHBoxLayout()
        q.addWidget(label(t("Space AKENO may use in the folder"), "key"), 1)
        q.addWidget(self.quota, 1)
        setup.body.addLayout(q)
        row.add(setup, 3)

        what = Card(t("What is synced"), "check", theme.VIOLET)
        self.cats = {}
        for key, text in sync.CATEGORIES.items():
            box = QCheckBox(t(text))
            box.toggled.connect(self._categories)
            self.cats[key] = box
            what.body.addWidget(box)
        what.body.addWidget(label(t("FPS Booster choices arrive as a suggestion: Windows settings change only when "
                                    "you apply them on that PC."), "muted", shrink=True))
        row.add(what, 2)
        root.addWidget(row)

        row2 = ResponsiveRow(spacing=14)
        state = Card(t("Status"), "refresh")
        self.pill = StatusPill("", "pending")
        state.body.addWidget(self.pill)
        self.kv = KeyValueGrid(150)
        state.body.addWidget(self.kv)
        self.btn_sync = button(t("Sync now"), "refresh", "primary", on_click=self.sync_now)
        state.body.addWidget(_row(self.btn_sync))
        row2.add(state, 2)
        conf = Card(t("Conflicts"), "warn", theme.AMBER)
        conf.body.addWidget(label(t("Changed differently on two PCs. Choose which one counts; everything else was "
                                    "merged."), "dim", shrink=True))
        self.conflicts = QListWidget()
        self.conflicts.setAccessibleName(t("Conflicts"))
        self.conflicts.setMinimumHeight(140)
        conf.body.addWidget(self.conflicts)
        conf.body.addWidget(_row(button(t("Keep this PC's"), "check", on_click=lambda: self._resolve("local")),
                                 button(t("Use the other PC's"), "download", on_click=lambda: self._resolve("remote"))))
        row2.add(conf, 3)
        root.addWidget(row2)
        root.addStretch(1)
        self.load()

    def load(self) -> None:
        self._loading = True
        try:
            st = self.engine.state
            self.chk.setChecked(bool(st["enabled"]))
            self.folder.setText(st["folder"])
            self.quota.setValue(int(st["quota_mb"]))
            for key, box in self.cats.items():
                box.setChecked(key in st["categories"])
        finally:
            self._loading = False
        self._status()

    def _status(self) -> None:
        st = self.engine.state
        if not st["enabled"] or not st["folder"]:
            self.pill.set(t("Off"), "pending")
        elif st["conflicts"]:
            self.pill.set(t("{n} conflict(s) to decide", n=len(st["conflicts"])), "warn")
        elif st["last_sync"]:
            self.pill.set(t("In sync"), "ok")
        else:
            self.pill.set(t("Not synced yet"), "pending")
        report = st.get("last_report") or {}
        devices = self.engine.devices() if st["folder"] else {}
        used = self.engine.usage() if st["folder"] else 0
        rows = [(t("Last sync"), st["last_sync"] or "—"),
                (t("Last result"), t("{p} sent, {r} received, {m} merged, {c} conflict(s)", p=report.get("pushed", 0),
                                     r=report.get("pulled", 0), m=report.get("merged", 0),
                                     c=report.get("conflicts", 0)) if report else "—"),
                (t("Space used"), f"{used / 2 ** 20:.2f} / {st['quota_mb']} MB"),
                (t("PCs in this folder"), ", ".join(d.get("name", "?") for d in devices.values()) or "—")]
        if report.get("skipped"):
            rows.append((t("Not synced"), "\n".join(report["skipped"][:5])))
        self.kv.set_rows(rows)
        self.conflicts.clear()
        names = {g.id: g.name for g in self.s.library.games()}
        for key, c in st["conflicts"].items():
            kind, _, rest = key.partition("/")
            title = names.get(rest, rest) if rest else (t("Settings") if kind == "settings" else t("Desktop"))
            item = QListWidgetItem(f"{title}: {', '.join(c['fields'])}  ({t('other PC')}: {c.get('updated') or '?'})")
            item.setData(256, key)
            self.conflicts.addItem(item)

    def _enabled(self, on: bool) -> None:
        if self._loading:
            return
        self.engine.configure(enabled=bool(on))
        self._status()

    def _categories(self, *_):
        if self._loading:
            return
        self.engine.configure(categories=[k for k, b in self.cats.items() if b.isChecked()])

    def set_folder(self, folder: str) -> None:
        self.engine.configure(folder=folder)
        self.load()

    def _choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Sync folder"), self.engine.state["folder"])
        if folder:
            self.set_folder(folder)

    def _save_passphrase(self) -> None:
        self.engine.configure(passphrase=self.passphrase.text())
        self.passphrase.clear()
        self.ctl.status.emit(t("Passphrase saved on this PC."))

    def sync_now(self, quiet: bool = False) -> None:
        if not self.engine.state["enabled"]:
            return
        self.btn_sync.setEnabled(False)

        def done(report: sync.Report):
            if not shiboken6.isValid(self.btn_sync):
                return                                  # the window closed while the sync ran
            self.btn_sync.setEnabled(True)
            self._status()
            self.ctl.status.emit(t("Cloud Sync: {r}", r=t(report.text())))
            if report.conflicts and getattr(self.win, "notifier", None) is not None:
                self.win.notifier.notify("sync", t("{n} conflict(s) to decide", n=report.conflicts),
                                         once=f"sync-{self.engine.state.get('last_sync')}")
            if report.pulled or report.merged:
                self.ctl.games_changed.emit()
                self.ctl.settings_changed.emit("*")

        def failed(exc):
            if not shiboken6.isValid(self.btn_sync):
                return
            self.btn_sync.setEnabled(True)
            self.pill.set(t("Sync failed"), "fail")
            if not quiet:
                info(self, t("Cloud Sync"), t("The sync did not complete."), str(exc), error=True)
        workers.run(self.engine.sync, done, failed)

    def _resolve(self, keep: str) -> None:
        item = self.conflicts.currentItem()
        if item is None:
            return
        try:
            self.engine.resolve(item.data(256), keep)
        except (sync.SyncError, OSError) as exc:
            info(self, t("Conflicts"), str(exc), error=True)
        self._status()
        self.ctl.games_changed.emit()

    def on_show(self) -> None:
        self.load()
