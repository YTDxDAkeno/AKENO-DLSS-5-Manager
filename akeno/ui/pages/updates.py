"""AKENO – Updates: AKENO's own new versions from the build's release source (the AKENO Release Manager, or the
older static and GitHub channel files), installed only when the release signature and every checksum match, by a
separate updater process with a journal, a health check and an automatic rollback.

Nothing is installed without the user's "Install now" and the confirmation that follows. Automatic checks (on start
and every few hours) only announce a version once; "Remind me later" keeps it from being announced again. Downloads
run in the background and can be cancelled; the window stays usable meanwhile.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget)

from akeno import __version__
from akeno.i18n import t
from akeno.ui import workers
from akeno.ui.account_ui import Banner
from akeno.ui.widgets import (Card, Divider, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow, StatusPill, button,
                              confirm, indented, info, label)
from akeno.updater import manifest
from akeno.updater.service import CHECK_HOURS, ServiceUnavailable, UpdateService

CHANNEL_TEXT = {"stable": "Stable", "beta": "Beta (earlier, less tested)"}
TIMER_MS = 30 * 60_000          # look whether an automatic check is due (it runs at most every CHECK_HOURS)
START_HOURS = 1                 # "check on start": not again when AKENO is restarted within the hour
UPDATER_START_S = 90            # the updater must report within this time (a virus scanner may delay its first start)
AUTHENTICODE_TEXT = {"verified": "Valid code signature", "not_signed": "Not code-signed (allowed)",
                     "present_unverified": "Code signature present, not verified by the server",
                     "invalid": "Invalid code signature", "mixed": "Partly code-signed"}


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


def time_text(ts: float | None) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "—"


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
        self.token = None
        self.verification: dict[str, tuple[str, str]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader(t("AKENO – Updates"), t(
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
        self.notes_preview = label("", "muted", wrap=True)
        self.notes_preview.setVisible(False)
        new.body.addWidget(self.notes_preview)
        self.phase = label("", "dim", shrink=True)
        self.phase.setVisible(False)
        new.body.addWidget(self.phase)
        self.progress = QProgressBar()
        self.progress.setAccessibleName(t("Update progress"))
        self.progress.setVisible(False)
        new.body.addWidget(self.progress)
        acts = FlowLayout(spacing=8)
        self.btn_install = button(t("Install now"), "download", "primary", on_click=self._install)
        self.btn_download = button(t("Download update"), "download", on_click=self._download)
        self.btn_cancel = button(t("Cancel"), kind="ghost", on_click=self._cancel)
        self.btn_notes = button(t("Show release notes"), "file", on_click=self._notes)
        self.btn_later = button(t("Remind me later"), kind="ghost", on_click=self._later)
        self.btn_page = button(t("Download page"), "external", on_click=self._open_page)
        for b in (self.btn_install, self.btn_download, self.btn_cancel, self.btn_notes, self.btn_later, self.btn_page):
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
        self.channel_row = QWidget()
        line = QHBoxLayout(self.channel_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(label(t("Update channel"), "key"), 1)
        line.addWidget(self.channel, 1)
        # the Release Manager publishes only the stable channel
        self.channel_row.setVisible(self.svc.dist.source != "akeno-api")
        now.body.addWidget(self.channel_row)
        acts = FlowLayout(spacing=8)
        self.btn_check = button(t("Check for updates"), "refresh", on_click=lambda: self.check(quiet=False))
        self.btn_rollback = button(t("Roll back"), "restore", on_click=self._rollback)
        acts.addWidget(self.btn_check)
        acts.addWidget(self.btn_rollback)
        now.body.addLayout(acts)
        row.add(now, 2)
        root.addWidget(row)
        row2 = ResponsiveRow(spacing=14)
        # -- verification --------------------------------------------------------------------------------------------
        ver = Card(t("Verification"), "shield")
        self.verify_kv = KeyValueGrid(170)
        ver.body.addWidget(self.verify_kv)
        ver.body.addWidget(label(t(
            "AKENO trusts only release information signed with the release key built into this program, and only "
            "files whose SHA-256 checksum and size match the signed values. This cannot be switched off."),
            "muted", wrap=True))
        row2.add(ver, 3)
        # -- options -------------------------------------------------------------------------------------------------
        opts = Card(t("Options"), "gear")
        self.opt_auto = self._option("check_app_updates", t("Check for updates automatically (every few hours)"))
        self.opt_start = self._option("check_updates_on_start", t("Check for updates when AKENO starts"))
        self.opt_background = self._option("update_background_download",
                                           t("Download updates in the background (installing always asks)"))
        self.opt_ask = QCheckBox(t("Always ask before installing"))
        self.opt_ask.setChecked(True)
        self.opt_ask.setEnabled(False)
        self.opt_ask.setToolTip(t("AKENO never installs an update without your confirmation."))
        opts.body.addWidget(self.opt_auto)
        for box in (self.opt_start, self.opt_background):        # they belong to (and wait for) automatic checks
            opts.body.addWidget(indented(box))
        opts.body.addWidget(self.opt_ask)
        self.opt_auto.toggled.connect(self._options_enabled)
        self._options_enabled(self.opt_auto.isChecked())
        opts.body.addWidget(Divider())
        opts.body.addWidget(label(t("AKENO never installs an update without your confirmation."), "muted",
                                  wrap=True))
        row2.add(opts, 2)
        root.addWidget(row2)
        root.addStretch(1)
        ctl.settings_changed.connect(self._setting_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(TIMER_MS)
        self.timer.timeout.connect(self.auto_check)
        self.timer.start()
        self._show_release(None, checked=False)
        self.refresh()

    def _option(self, key: str, text: str) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(self.svc.setting(key)))
        box.toggled.connect(lambda on, k=key: self.ctl.set_setting(k, on))
        return box

    def _options_enabled(self, on: bool) -> None:
        self.opt_start.setEnabled(on)
        self.opt_background.setEnabled(on)

    def _setting_changed(self, key: str) -> None:
        for box, name in ((self.opt_auto, "check_app_updates"), (self.opt_start, "check_updates_on_start"),
                          (self.opt_background, "update_background_download")):
            if key in (name, "*"):
                box.blockSignals(True)
                box.setChecked(bool(self.svc.setting(name)))
                box.blockSignals(False)
        self._options_enabled(self.opt_auto.isChecked())

    # -- state -------------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        state = self.svc.state()
        ok, why = self.svc.can_install()
        channel = t(CHANNEL_TEXT.get(self.svc.channel, self.svc.channel))
        rows = [(t("Installed version"), __version__),
                (t("Latest version"), state.get("latest") or ("—" if not state.get("last_success") else __version__)),
                (t("Update channel"), channel),
                (t("Update source"), self.svc.dist.label),
                (t("Last successful check"), time_text(state.get("last_success") or state.get("last_check"))),
                (t("Release signature keys"), str(len(self.svc.keys)) if self.svc.keys else t("none in this build"))]
        if state.get("signature"):
            rows.append((t("Code signature"), t(state["signature"])))
        if not ok:
            rows.append((t("Install from here"), t(why)))
        rows.append((t("Installation status"), self._install_status()))
        self.kv.set_rows(rows)
        previous = self.svc.previous()
        self.btn_rollback.setVisible(bool(previous) and ok and not self.busy)
        if previous:
            self.btn_rollback.setText(t("Roll back to {v}", v=previous))
        self._show_verification()

    def _install_status(self) -> str:
        journal = self.svc.journal()
        from akeno.updater import apply
        if journal.get("phase") in apply.UNFINISHED:
            return t("An update to {v} was interrupted; it is finished when AKENO starts again.",
                     v=journal.get("version"))
        outcome = self.svc.last_outcome()
        if not outcome:
            return t("No update installed by AKENO yet")
        if outcome.get("ok"):
            return t("{v} installed and started successfully", v=outcome.get("version"))
        if outcome.get("rolled_back"):
            return t("{v} was not installed: the previous version was restored", v=outcome.get("version"))
        return t("{v}: {e}", v=outcome.get("version"), e=outcome.get("error", ""))

    def _verify(self, key: str, text: str, state: str = "ok") -> None:
        self.verification[key] = (text, state)
        self._show_verification()

    def _show_verification(self) -> None:
        v = self.verification
        dots = {"ok": "✓", "warn": "!", "fail": "✕", "pending": "…"}
        order = (("signature", t("Release signature")), ("information", t("Release information")),
                 ("checksum", t("Package checksum")), ("contents", t("Package contents")),
                 ("authenticode", t("Code signature")))
        rows = []
        for key, title in order:
            text, state = v.get(key, (t("not checked yet"), "pending"))
            rows.append((title, f"{dots.get(state, '')} {text}".strip()))
        self.verify_kv.set_rows(rows)

    def _show_release(self, release: manifest.Release | None, checked: bool = True) -> None:
        self.release = release
        has = release is not None
        ok = self.svc.can_install(release)[0]
        ready = has and ok and self.svc.downloaded(release)
        self.headline.setVisible(has)
        self.facts.setVisible(has)
        self.notes_preview.setVisible(has and bool(release.notes))
        self.btn_install.setVisible(has and ok and not self.busy)
        self.btn_download.setVisible(has and ok and not ready and not self.busy)
        self.btn_cancel.setVisible(False)
        self.btn_notes.setVisible(has)
        self.btn_later.setVisible(has and not self.svc.snoozed(release.release) if has else False)
        self.btn_page.setVisible(has and not ok)
        if not has:
            if checked:
                self.pill.set(t("AKENO is up to date ({v}).", v=__version__), "ok")
            return
        self.pill.set(t("Ready to install") if ready else t("Update available"), "ok" if ready else "warn")
        self.headline.setText(t("AKENO Gaming Suite {v} is available.", v=release.release))
        package = release.file("portable") or release.file("installer")
        self.facts.set_rows([(t("Latest version"), release.release),
                             (t("Channel"), t(CHANNEL_TEXT.get(release.channel, release.channel))),
                             (t("Release date"), date_text(release.published)),
                             (t("Download size"), size_text(package.size if package else None))])
        lines = [x for x in (release.notes or "").splitlines() if x.strip()]
        self.notes_preview.setText("\n".join(lines[:3]) + (" …" if len(lines) > 3 else ""))
        self._verify("signature", t("Ed25519, key {k}: valid", k=release.kid) if release.kid else t("valid"))
        details = getattr(release, "v2", None)
        if details is not None:
            self._verify("information", t("sequence {n}, valid until {d}", n=details.sequence,
                                          d=date_text(details.expires_at)))
            auth = details.portable.authenticode if details.portable else details.installer_authenticode
            self._verify("authenticode", t(AUTHENTICODE_TEXT.get(auth.status, auth.status)),
                         "fail" if auth.status == "invalid" else "ok" if auth.status == "verified" else "warn")
        if ready:
            self._verify("checksum", t("SHA-256 and size match the signed values"))

    # -- checking ----------------------------------------------------------------------------------------------------
    def auto_check(self, on_start: bool = False) -> None:
        """Called on start and by the timer: checks only when enabled and due, and never interrupts."""
        if self.busy or not self.s.settings.get("check_app_updates"):
            return
        if on_start:
            if self.svc.setting("check_updates_on_start") and self.svc.due(hours=START_HOURS):
                self.check(quiet=True)
            return
        if self.svc.due():
            self.check(quiet=True)

    def check(self, quiet: bool = False) -> None:
        if self.busy:
            return
        self.btn_check.setEnabled(False)
        self.pill.set(t("Checking…"), "pending")
        self.verification.clear()                 # every check shows its own verification, nothing older
        self._show_verification()
        if not quiet:
            self.banner.clear()

        def done(release):
            self.btn_check.setEnabled(True)
            self._show_release(release)
            self.refresh()
            if release is None:
                if not quiet:
                    self.banner.show_message(t("AKENO is up to date ({v}).", v=__version__), "success")
                return
            notifier = getattr(self.win, "notifier", None)
            if notifier is not None and not self.svc.snoozed(release.release):
                notifier.notify("updates", t("AKENO Gaming Suite {v} is available.", v=release.release),
                                once=release.release)
            if quiet and self.svc.setting("update_background_download", False) and \
                    self.svc.can_install(release)[0] and not self.svc.downloaded(release):
                self._download(background=True)

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
                self._verify("signature", t("refused"), "fail")
                self._verify("information", str(exc), "fail")
            else:
                text = t("Update check failed: {e}", e=str(exc))
                self.pill.set(t("Update check failed"), "fail")
            if not quiet:
                self.banner.show_message(text, "error" if not isinstance(exc, ServiceUnavailable) else "warn")
        workers.run(self.svc.check, done, failed)

    def _channel(self, _index: int) -> None:
        self.ctl.set_setting("update_channel", self.channel.currentData())
        self._show_release(None, checked=False)
        self.pill.set(t("Not checked yet"), "pending")

    # -- downloading and installing ----------------------------------------------------------------------------------
    def _busy(self, on: bool) -> None:
        self.busy = on
        for b in (self.btn_install, self.btn_download, self.btn_later, self.btn_check, self.btn_rollback):
            b.setEnabled(not on)
        self.channel.setEnabled(not on)
        self.btn_cancel.setVisible(on)
        self.progress.setVisible(on)
        self.phase.setVisible(on)

    def _step(self, event) -> None:
        kind = event[0]
        package = self.release.file("portable") if self.release else None
        if kind == "space":
            self.phase.setText(t("Checking free disk space…"))
        elif kind == "download":
            done, total = event[1], event[2] or (package.size if package else 0)
            if total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(1000 * min(1.0, done / total)))
                self.phase.setText(t("Downloading: {d} of {s}", d=size_text(done), s=size_text(total)))
        elif kind == "verified":
            self.progress.setRange(0, 0)
            self.phase.setText(t("Release signature and SHA-256 checksum verified."))
            self._verify("checksum", t("SHA-256 and size match the signed values"))
        elif kind == "unpack":
            done, total = (event[1], event[2]) if len(event) > 2 else (0, 0)
            if total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(1000 * min(1.0, done / total)))
            self.phase.setText(t("Unpacking and checking the new version…"))
        elif kind == "signature":
            self.phase.setText(t("Checking the program's code signature…"))

    def _download(self, background: bool = False) -> None:
        """Download and verify the package now; installing is a separate step that asks first."""
        release = self.release
        if release is None or self.busy or not self.svc.can_install(release)[0]:
            return
        from akeno.security.downloads import CancelToken
        self.token = CancelToken()
        self._busy(True)
        self.progress.setRange(0, 0)
        self.phase.setText(t("Starting the download…"))
        if not background:
            self.banner.clear()

        def done(_path):
            self._busy(False)
            self._show_release(release)
            self.refresh()
            if not background:
                self.banner.show_message(t("AKENO {v} is downloaded and verified. Install it when it suits you.",
                                           v=release.release), "success")

        def failed(exc):
            self._busy(False)
            self._show_release(release)
            self.refresh()
            self._verify("checksum", str(exc)[:160], "fail")
            if not background:
                self.banner.show_message(t("The update was not downloaded: {e}", e=self._reason(exc)), "error")
        workers.run(lambda emit: self.svc.download(release, lambda d, tot: emit(("download", d, tot)), self.token),
                    done, failed, on_progress=self._step)

    def _reason(self, exc) -> str:
        from akeno.security.downloads import Cancelled
        if isinstance(exc, Cancelled):
            return t("cancelled")
        if isinstance(exc, ServiceUnavailable):
            return t("The update service cannot be reached right now.")
        return str(exc)

    def _cancel(self) -> None:
        if self.token is not None:
            self.token.cancel()
            self.phase.setText(t("Cancelling…"))

    def _install(self) -> None:
        release = self.release
        if release is None or self.busy or not self.svc.can_install(release)[0]:
            return
        package = release.file("portable")
        if not confirm(self, t("Install now"), t("Install AKENO Gaming Suite {v} now?", v=release.release),
                       t("AKENO downloads {s} if needed, checks the release signature and every checksum, then "
                         "closes, updates itself and starts again. Your license, settings and games are kept; the "
                         "current version stays available for a rollback.",
                         s=size_text(package.size if package else None)),
                       ok_text=t("Install now")):
            return
        from akeno.security.downloads import CancelToken
        self.token = CancelToken()
        self._busy(True)
        self.progress.setRange(0, 0)
        self.phase.setText(t("Preparing the update…"))
        self.banner.clear()

        def done(plan):
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.btn_cancel.setVisible(False)
            n = self._file_count(release)
            if getattr(release, "v2", None) is not None:     # compared in UpdateService.stage
                self._verify("contents", t("{n} files unpacked; count, size and content_sha256 match the signed "
                                           "values", n=n))
            else:                                             # older channel files carry no content aggregate
                self._verify("contents", t("{n} files unpacked and checked", n=n))
            self.phase.setText(t("Ready. AKENO closes now, installs {v} and starts again.", v=release.release))
            QTimer.singleShot(1200, lambda: self._launch(plan))

        def failed(exc):
            self._busy(False)
            self._show_release(release)
            self.refresh()
            self.banner.show_message(t("The update was not installed. Nothing was changed: {e}",
                                       e=self._reason(exc)), "error")
        # the service reports phases as separate values; the worker signal carries one object
        workers.run(lambda emit: self.svc.prepare(release, lambda *event: emit(event), self.token), done, failed,
                    on_progress=self._step)

    def _file_count(self, release) -> int:
        try:
            from akeno.updater import staging
            return len(staging.load(self.svc.content_path(release))["files"])
        except (OSError, ValueError, staging.StagingError):
            return 0

    def _launch(self, plan: dict) -> None:
        desktop = getattr(self.win, "desktop", None)
        if desktop is not None:
            try:
                desktop.stop()          # the desktop runtime runs from the program folder: it ends first
            except Exception:           # the updater ends it anyway
                pass
        try:
            proc = self.svc.launch(plan)
        except OSError as exc:
            self._busy(False)
            self.banner.show_message(t("The update was not installed. Nothing was changed: {e}", e=str(exc)), "error")
            return
        self._wait_for_updater(proc, time.monotonic() + UPDATER_START_S)

    def _wait_for_updater(self, proc, deadline: float) -> None:
        """AKENO quits only once the updater runs; an updater that does not start leaves AKENO open."""
        started = self.svc.updater_started(proc)
        if started:
            QApplication.instance().quit()
            return
        if started is None and time.monotonic() < deadline:
            QTimer.singleShot(250, lambda: self._wait_for_updater(proc, deadline))
            return
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        self._busy(False)
        self.svc._store(pending=None)
        self._show_release(self.release)
        self.refresh()
        self.banner.show_message(t("The update was not installed. Nothing was changed: {e}",
                                   e=t("the new version's updater did not start")), "error")

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
        self._launch(plan)

    def _open_page(self) -> None:
        url = self.release.notes_url if self.release and self.release.notes_url else self.svc.dist.releases
        QDesktopServices.openUrl(QUrl(url))

    def on_show(self) -> None:
        self.refresh()


__all__ = ["UpdatesPage", "CHANNEL_TEXT", "CHECK_HOURS"]
