"""Updates: AKENO's own new versions from the build's release source (AKENO's release server or the public release
repository), installed only when the release signature and every checksum match, by a separate updater process that
keeps the previous version for a rollback.

Nothing is installed without the user's "Update now" and the confirmation that follows. Automatic checks (on start
and every few hours) only announce a version once; "Later" keeps it from being announced again.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QPlainTextEdit,
                               QProgressBar, QVBoxLayout, QWidget)

from akeno import __version__
from akeno.i18n import t
from akeno.ui import workers
from akeno.ui.account_ui import Banner
from akeno.ui.widgets import (Card, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow, StatusPill, button,
                              confirm, info, label)
from akeno.updater import manifest
from akeno.updater.service import CHECK_HOURS, ServiceUnavailable, UpdateService

CHANNEL_TEXT = {"stable": "Stable", "beta": "Beta (earlier, less tested)"}
TIMER_MS = 30 * 60_000          # look whether an automatic check is due (it runs at most every CHECK_HOURS)


def size_text(size: int | None) -> str:
    if not size:
        return "—"
    mb = size / 2 ** 20
    return t("{n} MB", n=f"{mb:.0f}" if mb >= 10 else f"{mb:.1f}")


def date_text(published: str) -> str:
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return published or "—"
    from akeno.i18n import language
    return dt.strftime("%d.%m.%Y" if language() == "de" else "%Y-%m-%d")


class NotesDialog(QDialog):
    """What's new in a version: the release notes as published (plain text, never interpreted as markup)."""

    def __init__(self, release: manifest.Release, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("What's new in AKENO {v}", v=release.release))
        from akeno.ui.widgets import fit_to_screen
        fit_to_screen(self, 640, 520, 420, 320)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.addWidget(label(t("AKENO Gaming Suite {v}", v=release.release), "h2"))
        lay.addWidget(label(t("Released {d} · {c}", d=date_text(release.published), c=t(CHANNEL_TEXT.get(
            release.channel, release.channel))), "muted"))
        text = QPlainTextEdit(release.notes or t("No release notes were published for this version."))
        text.setReadOnly(True)
        text.setAccessibleName(t("Release notes"))
        lay.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        if release.notes_url:
            more = buttons.addButton(t("Open the release page"), QDialogButtonBox.ButtonRole.ActionRole)
            more.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.notes_url)))
        lay.addWidget(buttons)


class UpdatesPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.svc = UpdateService(self.s.settings)
        self.release: manifest.Release | None = None
        self.busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader(t("Updates"), t(
            "New versions are installed only when their release signature and every checksum match. Your license, "
            "settings, games, mods and backups stay as they are; the previous version is kept for a rollback.")))
        self.banner = Banner()
        root.addWidget(self.banner)
        row = ResponsiveRow(spacing=14)
        # -- the available version -----------------------------------------------------------------------------------
        new = Card(t("Available update"), "sparkle")
        self.pill = StatusPill(t("Not checked yet"), "pending")
        new.body.addWidget(self.pill)
        self.headline = label("", "h2", shrink=True)
        self.headline.setVisible(False)
        new.body.addWidget(self.headline)
        self.facts = KeyValueGrid(170)
        self.facts.setVisible(False)
        new.body.addWidget(self.facts)
        self.phase = label("", "dim", shrink=True)
        self.phase.setVisible(False)
        new.body.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setAccessibleName(t("Update progress"))
        self.progress.setVisible(False)
        new.body.addWidget(self.progress)
        acts = FlowLayout(spacing=8)
        self.btn_install = button(t("Update now"), "download", "primary", on_click=self._install)
        self.btn_notes = button(t("What's new"), "file", on_click=self._notes)
        self.btn_later = button(t("Later"), kind="ghost", on_click=self._later)
        self.btn_page = button(t("Download page"), "external", on_click=self._open_page)
        for b in (self.btn_install, self.btn_notes, self.btn_later, self.btn_page):
            acts.addWidget(b)
        new.body.addLayout(acts)
        new.body.addWidget(label(t("Each release also publishes its corresponding source code (GPL-3.0-or-later)."),
                                 "muted", shrink=True))
        row.add(new, 3)
        # -- this PC -------------------------------------------------------------------------------------------------
        now = Card(t("This PC"), "download")
        self.kv = KeyValueGrid(170)
        now.body.addWidget(self.kv)
        self.channel = QComboBox()
        self.channel.setAccessibleName(t("Update channel"))
        for key, text in CHANNEL_TEXT.items():
            self.channel.addItem(t(text), key)
        self.channel.setCurrentIndex(max(0, self.channel.findData(self.svc.channel)))
        self.channel.currentIndexChanged.connect(self._channel)
        line = QHBoxLayout()
        line.addWidget(label(t("Update channel"), "key"), 1)
        line.addWidget(self.channel, 1)
        now.body.addLayout(line)
        acts = FlowLayout(spacing=8)
        self.btn_check = button(t("Check now"), "refresh", on_click=lambda: self.check(quiet=False))
        self.btn_rollback = button(t("Roll back"), "restore", on_click=self._rollback)
        acts.addWidget(self.btn_check)
        acts.addWidget(self.btn_rollback)
        now.body.addLayout(acts)
        row.add(now, 2)
        root.addWidget(row)
        root.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(TIMER_MS)
        self.timer.timeout.connect(self.auto_check)
        self.timer.start()
        self._show_release(None, checked=False)
        self.refresh()

    # -- state -------------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        state = self.svc.state()
        ok, why = self.svc.can_install()
        last = state.get("last_check")
        rows = [(t("Installed version"), __version__),
                (t("Update source"), self.svc.dist.label),
                (t("Last check"), time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "—"),
                (t("Release signature keys"), str(len(self.svc.keys)) if self.svc.keys else t("none in this build"))]
        if state.get("signature"):
            rows.append((t("Code signature"), t(state["signature"])))
        if not ok:
            rows.append((t("Install from here"), t(why)))
        outcome = self.svc.last_outcome()
        if outcome:
            rows.append((t("Last update"), t("{v}: installed", v=outcome.get("version")) if outcome.get("ok") else
                         t("{v}: {e}", v=outcome.get("version"), e=outcome.get("error", ""))))
        self.kv.set_rows(rows)
        previous = self.svc.previous()
        self.btn_rollback.setVisible(bool(previous) and ok and not self.busy)
        if previous:
            self.btn_rollback.setText(t("Roll back to {v}", v=previous))

    def _show_release(self, release: manifest.Release | None, checked: bool = True) -> None:
        self.release = release
        has = release is not None
        ok = self.svc.can_install(release)[0]
        self.headline.setVisible(has)
        self.facts.setVisible(has)
        self.btn_install.setVisible(has and ok)
        self.btn_notes.setVisible(has)
        self.btn_later.setVisible(has and not self.svc.snoozed(release.release) if has else False)
        self.btn_page.setVisible(has and not ok)
        if not has:
            if checked:
                self.pill.set(t("AKENO is up to date ({v}).", v=__version__), "ok")
            return
        self.pill.set(t("Update available"), "warn")
        self.headline.setText(t("AKENO Gaming Suite {v} is available.", v=release.release))
        package = release.file("portable") or release.file("installer")
        self.facts.set_rows([(t("Latest version"), release.release),
                             (t("Channel"), t(CHANNEL_TEXT.get(release.channel, release.channel))),
                             (t("Release date"), date_text(release.published)),
                             (t("Download size"), size_text(package.size if package else None))])

    # -- checking ----------------------------------------------------------------------------------------------------
    def auto_check(self) -> None:
        """Called on start and by the timer: checks only when enabled and due, and never interrupts."""
        if self.busy or not self.s.settings.get("check_app_updates") or not self.svc.due():
            return
        self.check(quiet=True)

    def check(self, quiet: bool = False) -> None:
        if self.busy:
            return
        self.btn_check.setEnabled(False)
        self.pill.set(t("Checking…"), "pending")
        if not quiet:
            self.banner.clear()

        def done(release):
            self.btn_check.setEnabled(True)
            self.refresh()
            self._show_release(release)
            if release is None:
                if not quiet:
                    self.banner.show_message(t("AKENO is up to date ({v}).", v=__version__), "success")
                return
            notifier = getattr(self.win, "notifier", None)
            if notifier is not None and not self.svc.snoozed(release.release):
                notifier.notify("updates", t("AKENO Gaming Suite {v} is available.", v=release.release),
                                once=release.release)

        def failed(exc):
            self.btn_check.setEnabled(True)
            self.refresh()
            if isinstance(exc, ServiceUnavailable):
                text = t("The update service cannot be reached right now. AKENO keeps working and checks again "
                         "later.")
                self.pill.set(t("Update service unreachable"), "warn")
            elif isinstance(exc, manifest.ManifestError):
                text = t("The update information could not be verified, so it was ignored: {e}", e=str(exc))
                self.pill.set(t("Update information not verified"), "fail")
            else:
                text = t("Update check failed: {e}", e=str(exc))
                self.pill.set(t("Update check failed"), "fail")
            if not quiet:
                self.banner.show_message(text, "error" if not isinstance(exc, ServiceUnavailable) else "warn")
        workers.run(self.svc.check, done, failed)

    def _channel(self, _index: int) -> None:
        self.s.settings.set("update_channel", self.channel.currentData())
        self._show_release(None, checked=False)
        self.pill.set(t("Not checked yet"), "pending")

    # -- installing --------------------------------------------------------------------------------------------------
    def _install(self) -> None:
        release = self.release
        if release is None or self.busy or not self.svc.can_install(release)[0]:
            return
        package = release.file("portable")
        if not confirm(self, t("Update now"), t("Install AKENO Gaming Suite {v} now?", v=release.release),
                       t("AKENO downloads {s}, checks the release signature and the checksum, then closes, updates "
                         "itself and starts again. Your license, settings and games are kept; the current version "
                         "stays available for a rollback.", s=size_text(package.size if package else None)),
                       ok_text=t("Update now")):
            return
        self.busy = True
        for b in (self.btn_install, self.btn_later, self.btn_check, self.btn_rollback):
            b.setEnabled(False)
        self.channel.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.phase.setVisible(True)
        self.phase.setText(t("Starting the download…"))
        self.banner.clear()

        def step(event) -> None:
            kind = event[0]
            if kind == "download":
                done, total = event[1], event[2] or (package.size if package else 0)
                if total:
                    self.progress.setRange(0, 1000)
                    self.progress.setValue(int(1000 * min(1.0, done / total)))
                    self.phase.setText(t("Downloading: {d} of {s}", d=size_text(done), s=size_text(total)))
            elif kind == "verified":
                self.progress.setRange(0, 0)
                self.phase.setText(t("Release signature and SHA-256 checksum verified."))
            elif kind == "unpack":
                self.phase.setText(t("Unpacking the new version…"))
            elif kind == "signature":
                self.phase.setText(t("Checking the program's code signature…"))

        def done(plan):
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.phase.setText(t("Ready. AKENO closes now, installs {v} and starts again.", v=release.release))
            QTimer.singleShot(1200, lambda: self._launch(plan))

        def failed(exc):
            self.busy = False
            for b in (self.btn_install, self.btn_later, self.btn_check):
                b.setEnabled(True)
            self.channel.setEnabled(True)
            self.progress.setVisible(False)
            self.phase.setVisible(False)
            self.refresh()
            reason = t("The update service cannot be reached right now.") if isinstance(exc, ServiceUnavailable) \
                else str(exc)
            self.banner.show_message(t("The update was not installed. Nothing was changed: {e}", e=reason), "error")
        workers.run(lambda emit: self.svc.prepare(release, emit), done, failed, on_progress=step)

    def _launch(self, plan: dict) -> None:
        try:
            self.svc.launch(plan)
        except OSError as exc:
            self.busy = False
            self.banner.show_message(t("The update was not installed. Nothing was changed: {e}", e=str(exc)), "error")
            return
        QApplication.instance().quit()

    def _later(self) -> None:
        if self.release is not None:
            self.svc.snooze(self.release.release)
            self.btn_later.setVisible(False)
            self.banner.show_message(t("AKENO will not remind you of {v} again. You can install it here at any "
                                       "time.", v=self.release.release), "info")

    def _notes(self) -> None:
        if self.release is not None:
            NotesDialog(self.release, self).exec()

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


__all__ = ["UpdatesPage", "CHANNEL_TEXT", "CHECK_HOURS"]
