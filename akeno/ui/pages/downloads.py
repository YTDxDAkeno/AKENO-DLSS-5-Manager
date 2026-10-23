"""Downloads: every component version AKENO can obtain, what is cached, upstream updates and local copies."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget
from shiboken6 import isValid

from akeno.i18n import t
from akeno.security.downloads import CancelToken
from akeno.stack.catalog import ROLES
from akeno.ui import theme
from akeno.ui.stack_panel import CHANNEL, error_text, mib, version_text
from akeno.ui.widgets import Card, StatusPill, button, clear_layout, label


class _Bridge(QObject):
    progress = Signal(object)


class DownloadsPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s = ctl, ctl.s
        self.token: CancelToken | None = None
        self.busy = False
        self.bridge = _Bridge(self)
        self.bridge.progress.connect(self._progress)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label(t("Downloads"), "h1"))
        top.addStretch(1)
        self.btn_updates = button(t("Check for updates"), "refresh", on_click=self.check_updates)
        top.addWidget(self.btn_updates)
        self.btn_find = button(t("Find on this PC"), "search", on_click=self.find_local)
        top.addWidget(self.btn_find)
        lay.addLayout(top)
        lay.addWidget(label(t("Reviewed versions are pinned to the exact bytes of their original GitHub release. "
                              "Downloaded versions stay cached, so reinstalling or switching back works offline."),
                            "dim", wrap=True))
        opts = QHBoxLayout()
        self.show_unreviewed = QCheckBox(t("Show new upstream releases AKENO has not reviewed yet"))
        self.show_unreviewed.setChecked(True)
        self.show_unreviewed.toggled.connect(self.refresh)
        opts.addWidget(self.show_unreviewed)
        opts.addStretch(1)
        lay.addLayout(opts)
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.hide()
        prog.addWidget(self.progress, 1)
        self.btn_cancel = button(t("Cancel"), "stop", on_click=lambda: self.token and self.token.cancel())
        self.btn_cancel.hide()
        prog.addWidget(self.btn_cancel)
        lay.addLayout(prog)
        self.status = label("", "dim", wrap=True)
        lay.addWidget(self.status)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        lay.addLayout(self.body)
        lay.addStretch(1)
        ctl.installs_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_) -> None:
        clear_layout(self.body)
        stack = self.s.stack
        gpu = self.s.selected_gpu()
        gen = gpu.generation if gpu else None
        last = self.s.db.get_setting("stack-releases-last") or []
        checked = [stack.releases.cached(r).get("checked_at") for r in stack.releases.allowed]
        checked = [c for c in checked if c]
        if checked:
            when = max(checked)[:16].replace("T", " ")
            failed = [r for r in last if not r.get("ok")]
            self.status.setText(t("Last update check: {when} UTC.", when=when) +
                                (" " + t(failed[0]["error"]) if failed else ""))
        for comp in stack.catalog.components.values():
            card = Card(comp.name, "download", theme.BLUE,
                        right=button(t("Source"), "link", kind="link",
                                     on_click=lambda _=False, url=comp.homepage: QDesktopServices.openUrl(QUrl(url)))
                        if comp.homepage else None)
            card.body.addWidget(label(t(ROLES[comp.role]) + " · " + (comp.license or ""), "muted", wrap=True))
            if comp.description:
                card.body.addWidget(label(t(comp.description), "dim", wrap=True))
            grid = QGridLayout()
            grid.setColumnStretch(0, 1)
            versions = stack.all_versions(comp.id, include_unreviewed=self.show_unreviewed.isChecked())
            for i, v in enumerate(versions):
                text = version_text(v, gen)
                if v.published:
                    text += " · " + v.published
                if v.size:
                    text += " · " + mib(v.size)
                grid.addWidget(label(text, wrap=True), i, 0)
                ready = stack.store.is_ready(v)
                if v.source == "bundled":
                    pill = StatusPill(t("Included with AKENO") if stack.store.bundled_available(v)
                                      else t("Not in this build"), "ok" if stack.store.bundled_available(v) else "pending")
                elif v.source == "local":
                    pill = StatusPill(t("On this PC") if ready else t("File missing"), "ok" if ready else "fail")
                elif not v.reviewed:
                    pill = StatusPill(t(CHANNEL["unreviewed"]), "warn")
                else:
                    pill = StatusPill(t("Downloaded") if ready else t("Not downloaded"), "ok" if ready else "pending")
                grid.addWidget(pill, i, 1)
                if v.source in ("download", "reshade_setup"):
                    if ready:
                        grid.addWidget(button(t("Delete"), "trash", on_click=lambda _=False, k=v.key: self.delete(k)),
                                       i, 2)
                    else:
                        grid.addWidget(button(t("Download"), "download",
                                              on_click=lambda _=False, k=v.key: self.download(k)), i, 2)
                elif v.source == "local":
                    grid.addWidget(button(t("Forget"), "trash",
                                          on_click=lambda _=False, sha=v.sha256: self.forget(sha)), i, 2)
            card.body.addLayout(grid)
            self.body.addWidget(card)

    # -- actions -------------------------------------------------------------------------------------------
    def _run(self, key, work, done) -> None:
        if self.busy:
            return
        self.busy = True
        self.token = CancelToken()
        token = self.token
        self.progress.setRange(0, 0)
        self.progress.show()
        self.btn_cancel.show()

        def finish(value):
            self.busy = False
            if not isValid(self):
                return
            self.progress.hide()
            self.btn_cancel.hide()
            done(value)
            self.refresh()

        def fail(exc):
            self.busy = False
            if not isValid(self):
                return
            self.progress.hide()
            self.btn_cancel.hide()
            self.status.setText(error_text(exc))
            self.refresh()
        if not self.ctl.run("downloads:" + key, lambda: work(token), finish, fail):
            self.busy = False

    def _progress(self, value) -> None:
        cur, tot = value
        if tot:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(cur * 1000 / tot)))
            self.status.setText(t("Downloading: {cur} of {tot}", cur=mib(cur), tot=mib(tot)))

    def download(self, key: str) -> None:
        emit = self.bridge.progress.emit
        self._run("download", lambda token: self.s.stack.download(key, lambda c, tot: emit((c, tot)), token),
                  lambda staged: self.status.setText(t("Downloaded and verified {name}.", name=staged.version.display)))

    def delete(self, key: str) -> None:
        self.s.stack.delete_cached(key)
        self.refresh()

    def forget(self, sha: str) -> None:
        self.s.stack.store.forget_local(sha)
        self.refresh()

    def check_updates(self) -> None:
        def done(results):
            ok = sum(1 for r in results if r.ok)
            failed = [r for r in results if not r.ok]
            text = t("Checked {ok} of {n} upstream projects.", ok=ok, n=len(results))
            if failed:
                text += " " + t(failed[0].error)
            self.status.setText(text)
        self._run("updates", lambda token: self.s.stack.releases.refresh_all(token), done)

    def find_local(self) -> None:
        games = [g.target_dir or g.install_dir for g in self.s.library.games()]
        self._run("find", lambda token: self.s.stack.store.discover(games, token),
                  lambda found: self.status.setText(t("Found {n} runtime file(s) on this PC.", n=len(found))))

    def auto_check(self) -> None:
        """Daily update check when enabled in Settings (never installs anything by itself)."""
        if not self.s.settings.get("check_mod_updates"):
            return
        stamps = [self.s.stack.releases.cached(r).get("checked_at") for r in self.s.stack.releases.allowed]
        stamps = [s for s in stamps if s]
        if stamps:
            try:
                age = datetime.now().astimezone() - datetime.fromisoformat(max(stamps))
                if age.total_seconds() < 86400:
                    return
            except ValueError:
                pass
        self.check_updates()
