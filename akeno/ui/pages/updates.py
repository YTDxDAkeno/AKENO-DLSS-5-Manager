"""Updates: AKENO's own new versions from the public release repository, verified by signature and SHA-256,
installed by a separate updater process that keeps the previous version for a rollback."""

from __future__ import annotations

import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from akeno import __version__
from akeno.i18n import t
from akeno.ui import theme, workers
from akeno.ui.widgets import PageHeader, Card, KeyValueGrid, ResponsiveRow, StatusPill, button, confirm, info, label
from akeno.updater import manifest
from akeno.updater.service import UpdateService

CHANNEL_TEXT = {"stable": "Stable", "beta": "Beta (earlier, less tested)"}


class UpdatesPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.svc = UpdateService(self.s.settings)
        self.release: manifest.Release | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader(t("Updates"), t("New versions come from AKENO's public release page, need no GitHub account and are "
                               "installed only when their release signature and every checksum match. Your "
                               "license, settings, games, mods and backups stay as they are; the previous version "
                               "is kept for a rollback.")))
        row = ResponsiveRow(spacing=14)
        now = Card(t("This PC"), "download")
        self.kv = KeyValueGrid(170)
        now.body.addWidget(self.kv)
        self.channel = QComboBox()
        self.channel.setAccessibleName(t("Update channel"))
        for key, text in CHANNEL_TEXT.items():
            self.channel.addItem(t(text), key)
        self.channel.setCurrentIndex(max(0, self.channel.findData(self.svc.channel)))
        self.channel.currentIndexChanged.connect(
            lambda _: self.s.settings.set("update_channel", self.channel.currentData()))
        line = QHBoxLayout()
        line.addWidget(label(t("Update channel"), "key"), 1)
        line.addWidget(self.channel, 1)
        now.body.addLayout(line)
        self.btn_check = button(t("Check for updates"), "refresh", "primary", on_click=self.check)
        self.btn_rollback = button(t("Roll back"), "restore", on_click=self._rollback)
        acts = QHBoxLayout()
        acts.addWidget(self.btn_check)
        acts.addWidget(self.btn_rollback)
        acts.addStretch(1)
        now.body.addLayout(acts)
        row.add(now, 2)

        new = Card(t("New version"), "sparkle", theme.VIOLET)
        self.pill = StatusPill(t("Not checked yet"), "pending")
        new.body.addWidget(self.pill)
        self.notes = label("", "dim", wrap=True)
        new.body.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        new.body.addWidget(self.progress)
        self.btn_install = button(t("Download and install"), "download", "primary", on_click=self._install)
        self.btn_install.setVisible(False)
        self.btn_page = button(t("Release page"), "globe", on_click=self._open_page)
        acts = QHBoxLayout()
        acts.addWidget(self.btn_install)
        acts.addWidget(self.btn_page)
        acts.addStretch(1)
        new.body.addLayout(acts)
        new.body.addWidget(label(t("Each release also publishes its corresponding source code (GPL-3.0-or-later) on "
                                   "the release page."), "muted", shrink=True))
        row.add(new, 3)
        root.addWidget(row)
        root.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        state = self.svc.state()
        ok, why = self.svc.can_install()
        last = state.get("last_check")
        rows = [(t("Installed version"), __version__),
                (t("Release page"), self.svc.dist.releases),
                (t("Last check"), time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "—"),
                (t("Signature keys"), str(len(self.svc.keys)) if self.svc.keys else t("none in this build"))]
        if not ok:
            rows.append((t("Install from here"), t(why)))
        outcome = self.svc.last_outcome()
        if outcome:
            rows.append((t("Last update"), t("{v}: installed", v=outcome.get("version")) if outcome.get("ok") else
                         t("{v}: {e}", v=outcome.get("version"), e=outcome.get("error", ""))))
        self.kv.set_rows(rows)
        previous = self.svc.previous()
        self.btn_rollback.setVisible(bool(previous) and ok)
        if previous:
            self.btn_rollback.setText(t("Roll back to {v}", v=previous))

    def check(self, quiet: bool = False) -> None:
        self.btn_check.setEnabled(False)
        self.pill.set(t("Checking…"), "pending")

        def done(release):
            self.btn_check.setEnabled(True)
            self.release = release
            self.refresh()
            if release is None:
                self.pill.set(t("AKENO is up to date ({v}).", v=__version__), "ok")
                self.notes.setText("")
                self.btn_install.setVisible(False)
                return
            self.pill.set(t("AKENO {v} is available.", v=release.release), "warn")
            self.notes.setText(release.notes or "")
            self.btn_install.setVisible(self.svc.can_install()[0])
            notifier = getattr(self.win, "notifier", None)
            if notifier is not None:
                notifier.notify("updates", t("AKENO {v} is available.", v=release.release), once=release.release)

        def failed(exc):
            self.btn_check.setEnabled(True)
            self.pill.set(t("Update check failed: ") + str(exc), "fail")
            self.refresh()
            if not quiet and self.isVisible():
                info(self, t("Updates"), t("Update check failed: ") + str(exc), error=True)
        workers.run(self.svc.check, done, failed)

    def _install(self) -> None:
        release = self.release
        if release is None:
            return
        if not confirm(self, t("Updates"), t("Install AKENO {v} now? AKENO closes, updates itself and starts again.",
                                             v=release.release), ok_text=t("Install")):
            return
        self.btn_install.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        def work():
            archive = self.svc.download(release)
            return self.svc.plan(release, self.svc.stage(archive, release))

        def done(plan):
            self.svc.launch(plan)
            QApplication.instance().quit()

        def failed(exc):
            self.btn_install.setEnabled(True)
            self.progress.setVisible(False)
            info(self, t("Updates"), t("The update was not installed."), str(exc), error=True)
        workers.run(work, done, failed)

    def _rollback(self) -> None:
        previous = self.svc.previous()
        if not previous or not confirm(self, t("Roll back"), t("Go back to AKENO {v}? AKENO closes and starts again.",
                                                               v=previous), ok_text=t("Roll back")):
            return
        try:
            plan = self.svc.rollback_plan()
        except Exception as exc:              # a missing or damaged backup: say so, change nothing
            info(self, t("Roll back"), str(exc), error=True)
            return
        self.svc.launch(plan)
        QApplication.instance().quit()

    def _open_page(self) -> None:
        url = self.release.notes_url if self.release and self.release.notes_url else self.svc.dist.releases
        QDesktopServices.openUrl(QUrl(url))

    def on_show(self) -> None:
        self.refresh()
