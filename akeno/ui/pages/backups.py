"""Backups page: every installation transaction and manual snapshot, with restore, delete and logs."""

from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QPlainTextEdit, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, EmptyState, button, confirm, info, label

STATE = {"installed": "Installed (restorable)", "superseded": "Superseded by an update", "restored": "Restored",
         "rolled_back": "Failed — rolled back", "failed": "Failed", "snapshot": "Manual backup",
         "planned": "Planned", "backing_up": "Backing up", "applying": "Applying"}


class BackupsPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        self.win = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label(t("Backups"), "h1"))
        top.addStretch(1)
        top.addWidget(button(t("Create Backup"), "database", "primary",
                             tip=t("Back up the graphics-mod related files of the selected game"),
                             on_click=self._snapshot))
        lay.addLayout(top)
        lay.addWidget(label(t("AKENO backs up every file before it changes anything and verifies the copy. Restoring "
                              "an installation removes the files AKENO added and puts the originals back."),
                            "dim", wrap=True))
        card = Card(t("History"), "clock", theme.BLUE)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([t("Game"), t("Date"), t("Backend"), t("Files"), t("Status"),
                                              t("Backup size"), t("ID")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._show)
        self.table.setMinimumHeight(260)
        card.body.addWidget(self.table)
        self.empty = EmptyState("database", t("No backups yet"),
                                t("Backups appear here after an installation or a manual backup."))
        card.body.addWidget(self.empty)
        acts = QHBoxLayout()
        self.btn_restore = button(t("Restore"), "restore", "primary", on_click=self._restore)
        self.btn_delete = button(t("Delete backup"), "trash", "danger", on_click=self._delete)
        acts.addWidget(self.btn_restore)
        acts.addWidget(self.btn_delete)
        acts.addStretch(1)
        card.body.addLayout(acts)
        lay.addWidget(card)
        dcard = Card(t("Details"), "file", theme.VIOLET)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(200)
        self.details.setAccessibleName(t("Backup details"))
        dcard.body.addWidget(self.details)
        lay.addWidget(dcard)
        lay.addStretch(1)
        ctl.installs_changed.connect(self.refresh)
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self) -> None:
        self.txs = self.s.db.transactions()
        self.table.setRowCount(len(self.txs))
        for i, tx in enumerate(self.txs):
            files = self.s.db.tx_files(tx["id"])
            size = self.s.installer.backup_size(tx)
            a = self.s.registry.get(tx["backend_id"])
            vals = [tx["game_name"], tx["created_at"][:16].replace("T", " "),
                    (a.short if a else t("Manual backup") if tx["kind"] == "snapshot" else tx["backend_id"]) +
                    (f" {tx['package_version']}" if tx.get("package_version") else ""),
                    str(len(files)), t(STATE.get(tx["state"], tx["state"])),
                    f"{size / 1024:.0f} KB" if size else "—", str(tx["id"])]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.setVisible(bool(self.txs))
        self.empty.setVisible(not self.txs)
        self._show()

    def _current(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.txs[min(rows)] if rows and min(rows) < len(self.txs) else None

    def _show(self) -> None:
        tx = self._current()
        restorable = bool(tx and (tx["state"] in ("installed",) or tx["kind"] == "snapshot"))
        self.btn_restore.setEnabled(restorable)
        self.btn_restore.setToolTip(t("Restore the original files") if restorable else
                                    t("Select an active installation or a manual backup."))
        self.btn_delete.setEnabled(bool(tx) and (tx["state"] not in ("installed", "superseded") or tx["kind"] == "snapshot"))
        if not tx:
            self.details.setPlainText("")
            return
        lines = [f"{t('Game')}: {tx['game_name']}", f"{t('Folder')}: {tx['target_dir']}",
                 f"{t('Backup folder')}: {tx.get('backup_dir') or '—'}", f"{t('Kind')}: {tx['kind']}",
                 f"{t('Status')}: {t(STATE.get(tx['state'], tx['state']))}", "", t("Files:")]
        for f in self.s.db.tx_files(tx["id"]):
            what = {"create": t("added by AKENO"), "replace": t("replaced (original backed up)"),
                    "rename_away": t("disabled by renaming"), "snapshot": t("backed up")}.get(f["action"], f["action"])
            lines.append(f"  {f['rel_path']}  —  {what}" + (f"  [sha256 {f['original_sha256'][:12]}…]"
                                                              if f.get("original_sha256") else ""))
        if tx["kind"] != "snapshot" and tx["state"] == "installed":
            conflicts = self.s.installer.check_restore(tx["id"])
            if conflicts:
                lines += ["", t("Changed after installation:")] + [f"  {c.rel_path}: {t(c.reason)}" for c in conflicts]
        lines += ["", t("Log:")] + [f"  {e['t']}  {e['msg']}" for e in tx.get("log", [])]
        self.details.setPlainText("\n".join(lines))

    def _restore(self) -> None:
        tx = self._current()
        if tx:
            self.win.restore_transaction(tx["id"])

    def _delete(self) -> None:
        tx = self._current()
        if not tx or not confirm(self, t("Delete backup"), t("Delete this backup permanently? This cannot be undone.")):
            return
        res = self.s.installer.delete_backup(tx["id"])
        if not res.ok:
            info(self, t("Delete backup"), t(res.messages[0]), error=True)
        self.ctl.installs_changed.emit()

    def _snapshot(self) -> None:
        g = self.s.selected_game()
        if not g:
            info(self, t("Create Backup"), t("Select a game first."), error=True)
            return

        def done(res):
            self.ctl.installs_changed.emit()
            info(self, t("Create Backup"), t(res.messages[0]) if res.messages else "", error=not res.ok)
        self.ctl.run("backup", lambda: self.s.create_snapshot(g), done)
