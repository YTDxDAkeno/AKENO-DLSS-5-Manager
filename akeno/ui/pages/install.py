"""Secure Install: verified package, NVIDIA runtime import, preflight checklist and the transactional install."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QPlainTextEdit, QProgressBar,
                               QVBoxLayout, QWidget)

from akeno.backends import runtimes
from akeno.core.models import AntiCheatRisk, CompatStatus, Severity, TrustState
from akeno.i18n import t
from akeno.security.downloads import CancelToken
from akeno.ui import theme
from akeno.ui.pages.dashboard import COMPAT_TEXT
from akeno.ui.widgets import (Card, CheckRow, KeyValueGrid, Spinner, StatusPill, button, clear_layout, confirm, info,
                              label)

TRUST = {TrustState.VERIFIED: ("Verified", "verified"), TrustState.NEEDS_REVIEW: ("Needs review", "needs_review"),
         TrustState.UNVERIFIED: ("Unverified", "unverified"), TrustState.BLOCKED: ("Blocked", "blocked")}


class InstallPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        self.win = window
        self.token: CancelToken | None = None
        self.run_states: dict[str, str] = {}
        self._blocked = True
        self._needs_ack = True
        self.disable_checks: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(label(t("Secure Install"), "h1"))
        self.head = label("", "dim", wrap=True)
        lay.addWidget(self.head)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.pkg_card = Card(t("Package"), "lock", theme.GREEN)
        self.pkg_combo = QComboBox()
        self.pkg_combo.setAccessibleName(t("Package"))
        self.pkg_combo.activated.connect(self._pkg_changed)
        self.pkg_card.body.addWidget(self.pkg_combo)
        self.pkg_grid = KeyValueGrid(120)
        self.pkg_card.body.addWidget(self.pkg_grid)
        row.addWidget(self.pkg_card, 1)
        self.rt_card = Card(t("NVIDIA runtime"), "cpu", theme.GREEN)
        self.rt_card.body.addWidget(label(t("DLSS 5 Neural Rendering needs NVIDIA's nvngx_dlssnr.dll. AKENO never "
                                            "downloads or redistributes it: it uses the copy your NVIDIA driver "
                                            "installed, and accepts only files with a valid NVIDIA signature."),
                                          "dim", wrap=True))
        self.rt_box = QVBoxLayout()
        self.rt_card.body.addLayout(self.rt_box)
        rt_btns = QHBoxLayout()
        rt_btns.addWidget(button(t("Find in NVIDIA driver"), "search", on_click=self._find_runtime))
        rt_btns.addWidget(button(t("Import file…"), "file", on_click=self._import_runtime))
        rt_btns.addStretch(1)
        self.rt_card.body.addLayout(rt_btns)
        row.addWidget(self.rt_card, 1)
        lay.addLayout(row)
        self.pre_card = Card(t("Preflight checks"), "shield", theme.VIOLET)
        self.pre_box = QVBoxLayout()
        self.pre_card.body.addLayout(self.pre_box)
        lay.addWidget(self.pre_card)
        self.opt_card = Card(t("Options"), "sliders", theme.BLUE)
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName(t("Profile to apply"))
        orow = QHBoxLayout()
        orow.addWidget(label(t("Profile to apply"), "dim"))
        orow.addWidget(self.profile_combo, 1)
        self.opt_card.body.addLayout(orow)
        self.disable_box = QVBoxLayout()
        self.opt_card.body.addLayout(self.disable_box)
        self.ack = QCheckBox(t("I understand this is an experimental community mod, not supported by NVIDIA or the "
                               "game developer, and that AKENO keeps a backup to undo it."))
        self.ack.toggled.connect(self._update_buttons)
        self.opt_card.body.addWidget(self.ack)
        lay.addWidget(self.opt_card)
        act = QHBoxLayout()
        self.btn_install = button(t("Install / Update"), "download", "primary", on_click=self._install)
        self.btn_cancel = button(t("Cancel download"), "x", on_click=self._cancel)
        self.btn_cancel.setEnabled(False)
        self.btn_restore = button(t("Uninstall and restore originals"), "restore", on_click=self._restore)
        self.spinner = Spinner()
        for w in (self.btn_install, self.btn_cancel, self.btn_restore, self.spinner):
            act.addWidget(w)
        act.addStretch(1)
        lay.addLayout(act)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.why = label("", "dim", wrap=True)
        lay.addWidget(self.why)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setPlaceholderText(t("Installation log"))
        self.log.setAccessibleName(t("Installation log"))
        lay.addWidget(self.log)
        lay.addStretch(1)
        for sig in (ctl.selection_changed, ctl.games_changed, ctl.installs_changed, ctl.hardware_changed):
            sig.connect(self.refresh)
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    # ------------------------------------------------------------------------------------------
    def _ctx(self):
        g = self.s.selected_game()
        a = self.s.backend_for(g)
        return g, a, (self.s.package_for(a) if a else None)

    def refresh(self) -> None:
        g, a, pkg = self._ctx()
        if g is None or a is None:
            self.head.setText(t("Select a game in the Game Library first."))
            for w in (self.pkg_card, self.rt_card, self.pre_card, self.opt_card):
                w.setEnabled(False)
            self.btn_install.setEnabled(False)
            self.btn_restore.setEnabled(False)
            return
        for w in (self.pkg_card, self.rt_card, self.pre_card, self.opt_card):
            w.setEnabled(True)
        self.head.setText(t("Game: {g}  ·  Backend: {b}  ·  Target folder: {d}", g=g.name, b=a.name,
                            d=g.target_dir or t("Unknown")))
        # package
        self.pkg_combo.clear()
        for p in self.s.catalog.packages_for(a.id):
            self.pkg_combo.addItem(f"{p.name} {p.version}", p.id)
        if pkg:
            self.pkg_combo.setCurrentIndex(max(0, self.pkg_combo.findData(pkg.id)))
            txt, st = TRUST[pkg.trust]
            link = label(f"<a style='color:{theme.VIOLET}' href='{pkg.upstream}'>{pkg.upstream}</a>")
            link.setOpenExternalLinks(True)
            self.pkg_grid.set_rows([
                (t("Trust"), StatusPill(t(txt), st)), (t("Source"), link), (t("License"), pkg.license),
                (t("Reviewed"), pkg.reviewed or "—"),
                (t("SHA-256"), (pkg.sha256[:16] + "…" + pkg.sha256[-8:]) if pkg.sha256 else t("Not pinned")),
                (t("Download"), t("Cached and verified") if self.s.package_cached(pkg) else
                 (t("Downloaded on install ({n} MB)", n=f"{(pkg.size or 0) / 1e6:.1f}") if pkg.installable else "—")),
                (t("Notes"), t(pkg.review_notes))])
        else:
            self.pkg_grid.set_rows([(t("Package"), t("No package listed for this backend."))])
        self._refresh_runtimes()
        # profiles
        self.profile_combo.clear()
        rec = self.s.recommendation(g)
        for p in a.profiles(g):
            self.profile_combo.addItem(t(p.label) + (" ★" if p.id == rec.profile_id else ""), p.id)
        if a.profiles(g):
            saved = next((p for p in self.s.db.profiles(g.id, a.id) if p["name"] == "custom"), None)
            if saved:
                self.profile_combo.addItem(t("Custom"), "custom")
            if rec.profile_id:
                self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(rec.profile_id)))
        else:
            self.profile_combo.addItem(t("Configured in the backend's own overlay"), "")
        clear_layout(self.disable_box)
        self.disable_checks = []
        managed = self.s.installer.managed_files(g.id, g.target_dir) if g.target_dir else set()
        for m in g.existing_mods:
            if m.kind == "neural_consumer" and m.name.lower() not in managed and a.id == "optiscaler_nr":
                cb = QCheckBox(t("Disable {f} while {b} is installed (renamed, restored on uninstall)", f=m.name,
                                 b=a.short))
                cb.setProperty("file", m.name)
                cb.toggled.connect(self._refresh_preflight)
                self.disable_box.addWidget(cb)
                self.disable_checks.append(cb)
        self.run_states = {}
        self._refresh_preflight()

    def _refresh_runtimes(self) -> None:
        clear_layout(self.rt_box)
        rts = [r for r in self.s.db.runtimes() if r["file_name"] in runtimes.SUPPORTED_RUNTIMES]
        if not rts:
            self.rt_box.addWidget(CheckRow("fail", t("No runtime imported"),
                                           t("Use 'Find in NVIDIA driver' or import the file manually.")))
        for r in rts:
            st = {"verified": "ok", "unverified": "warn", "needs_review": "warn", "blocked": "fail"}[r["trust"]]
            self.rt_box.addWidget(CheckRow(st, f"{r['file_name']} {r.get('version') or ''}",
                                           t("Signer: {s} · {t}", s=r.get("signer") or t("none"),
                                             t=t(TRUST[TrustState(r["trust"])][0]))))

    def _disable_list(self) -> list[str]:
        return [cb.property("file") for cb in getattr(self, "disable_checks", []) if cb.isChecked()]

    def _refresh_preflight(self) -> None:
        clear_layout(self.pre_box)
        g, a, pkg = self._ctx()
        if g is None or a is None:
            return
        ctx = self.s.context(g, allow_disable=self._disable_list())
        findings = a.check(g, ctx)
        compat = self.s.compatibility(g, a)
        blockers = [f for f in findings if f.severity == Severity.BLOCKER]
        conflicts = [f for f in blockers if f.code in ("foreign_optiscaler", "second_consumer", "no_proxy_name",
                                                         "foreign_feeder", "stock_optiscaler", "renodx_dlss",
                                                         "consumer_multiple")]
        deps = [f for f in blockers if f.code.startswith(("runtime_missing", "missing_", "reshade_missing",
                                                          "consumer_missing"))]
        other = [f for f in blockers if f not in conflicts and f not in deps and f.code not in ("no_exe",)]
        exe_ok = bool(g.exe_path and os.path.isfile(g.exe_path))
        writable = bool(g.target_dir and os.access(g.target_dir, os.W_OK))
        pkg_ok = bool(pkg and pkg.installable)
        steps = [
            ("verify", "ok" if exe_ok else "fail", t("1. Game and executable verified"),
             g.exe_path if exe_ok else t("The game executable was not found.")),
            ("running", {True: "fail", False: "ok", None: "warn"}[g.is_running], t("2. Game is not running"),
             {True: t("Close the game first."), False: t("No game process found."),
              None: t("Could not check running processes.")}[g.is_running]),
            ("anticheat", "fail" if compat and compat.status == CompatStatus.BLOCKED else
             ("ok" if g.anti_cheat == AntiCheatRisk.NONE_FOUND else "warn"), t("3. Anti-cheat risk checked"),
             "; ".join(g.anti_cheat_details) or t("No known anti-cheat files found (this is not a guarantee). "
                                                  "Use mods only offline and in single-player.")),
            ("perm", "ok" if writable else "fail", t("4. Write permission"),
             t("The game folder is writable without administrator rights.") if writable else
             t("AKENO cannot write to this folder and will not ask for administrator rights.")),
            ("conflicts", "fail" if conflicts else "ok", t("5. File conflicts"),
             " ".join(t(f.message) for f in conflicts) or t("No conflicting graphics hooks.")),
            ("deps", "fail" if (deps or not pkg_ok) else "ok", t("6. Dependencies and package authenticity"),
             " ".join(t(f.message) for f in deps) or (t("Package is reviewed and hash-pinned.") if pkg_ok else
                                                       t("The package is not a reviewed, verified release."))),
        ]
        if other:
            steps.append(("other", "fail", t("Other blockers"), " ".join(t(f.message) for f in other)))
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        if warnings:
            steps.append(("warn", "warn", t("Warnings"), " ".join(t(f.message) for f in warnings)))
        if compat:
            txt, st = COMPAT_TEXT[compat.status]
            steps.append(("compat", st, t("Compatibility: {s}", s=t(txt)),
                          t("confidence") + f": {t(compat.confidence)}"))
        later = [("summary", t("7. Installation summary shown")), ("confirm", t("8. Your confirmation")),
                 ("backup", t("9. Backup created and verified")), ("apply", t("10. Changes applied")),
                 ("validate", t("11. Installation validated")), ("record", t("12. Installation recorded"))]
        for key, st, title, detail in steps:
            self.pre_box.addWidget(CheckRow(st, title, detail))
        for key, title in later:
            self.pre_box.addWidget(CheckRow(self.run_states.get(key, "pending"), title, ""))
        self._blocked = any(st == "fail" for _, st, _, _ in steps)
        self._needs_ack = not compat or compat.status != CompatStatus.CONFIRMED or a.experimental
        self._update_buttons()

    def _update_buttons(self) -> None:
        g, a, pkg = self._ctx()
        busy = self.ctl.is_busy("install")
        active = self.s.installer.active_install(g.id, a.id) if g and a else None
        ok = bool(g and a and not getattr(self, "_blocked", True) and (self.ack.isChecked() or not self._needs_ack))
        self.btn_install.setEnabled(ok and not busy)
        self.btn_install.setText(t("Update") if active else t("Install / Update"))
        self.btn_restore.setEnabled(bool(active) and not busy)
        if getattr(self, "_blocked", False):
            self.why.setText(t("Installation is unavailable until every failed check above is resolved."))
        elif self._needs_ack and not self.ack.isChecked():
            self.why.setText(t("Confirm the experimental-mod notice to continue."))
        else:
            self.why.setText("")

    # -- runtime import --------------------------------------------------------------------------
    def _find_runtime(self) -> None:
        def work():
            found = []
            for name in runtimes.SUPPORTED_RUNTIMES:
                found.extend(runtimes.discover(name))
            return found

        def done(found):
            if not found:
                info(self, t("Find in NVIDIA driver"), t("No NVIDIA runtime files were found in the driver store. "
                                                         "Your driver may not include DLSS 5 Neural Rendering yet."))
                return
            ok = [c for c in found if c.trust == TrustState.VERIFIED]
            text = "\n".join(f"• {os.path.basename(c.path)} {c.version or ''} — {t(c.detail)}" for c in found)
            if not ok:
                info(self, t("Find in NVIDIA driver"), t("Files were found, but none has a valid NVIDIA signature."),
                     text, error=True)
                return
            if confirm(self, t("Find in NVIDIA driver"), t("Import {n} NVIDIA-signed file(s) into AKENO?", n=len(ok)),
                       text):
                for c in ok:
                    runtimes.import_runtime(self.s.db, c.path)
                self.ctl.installs_changed.emit()
        self.ctl.run("runtime", work, done, lambda e: info(self, t("Find in NVIDIA driver"), str(e), error=True))

    def _import_runtime(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Import NVIDIA runtime"), "",
                                              "NVIDIA NGX (nvngx_dlssnr.dll nvngx_dlss.dll)")
        if not path:
            return
        try:
            cand = runtimes.import_runtime(self.s.db, path)
        except ValueError as exc:
            info(self, t("Import NVIDIA runtime"), str(exc), error=True)
            return
        info(self, t("Import NVIDIA runtime"),
             t("Imported and verified.") if cand.trust == TrustState.VERIFIED else t("Not accepted for installation."),
             t(cand.detail), error=cand.trust != TrustState.VERIFIED)
        self.ctl.installs_changed.emit()

    def _pkg_changed(self) -> None:
        g, a, _ = self._ctx()
        if a:
            self.s.settings.set(f"package.{a.id}", self.pkg_combo.currentData())
            self.refresh()

    # -- install -------------------------------------------------------------------------------
    def _say(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    def _set_run(self, key: str, state: str) -> None:
        self.run_states[key] = state
        self._refresh_preflight()

    def _install(self) -> None:
        g, a, pkg = self._ctx()
        if not (g and a and pkg):
            return
        pid = self.profile_combo.currentData()
        params = {}
        if pid == "custom":
            saved = next((p for p in self.s.db.profiles(g.id, a.id) if p["name"] == "custom"), None)
            params = dict(a.default_params(g), **(saved["params"] if saved else {}))
        elif pid:
            prof = next((p for p in a.profiles(g) if p.id == pid), None)
            params = dict(a.default_params(g), **(prof.params if prof else {}))
        if pid:
            params["_profile"] = pid
        disable = self._disable_list()
        self.token = CancelToken()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.btn_cancel.setEnabled(True)
        self.btn_install.setEnabled(False)
        self.spinner.start()
        self.log.clear()
        self._say(t("Downloading and verifying {p} {v}…", p=pkg.name, v=pkg.version))

        def stage(emit):
            return self.s.stage(g, a, pkg, progress=lambda n, total: emit((n, total)), token=self.token)

        def progress(val):
            n, total = val
            if total:
                self.progress.setRange(0, 100)
                self.progress.setValue(int(n * 100 / total))

        def staged(staging):
            self.btn_cancel.setEnabled(False)
            self.progress.setVisible(False)
            self._say(t("Package verified (SHA-256 matches the reviewed release)."))
            try:
                plan = self.s.plan_install(g, a, pkg, params, staging, disable)
            except ValueError as exc:
                self._fail(str(exc))
                return
            self._set_run("summary", "ok")
            files = "\n".join(f"• {o.rel_path} — {t(o.description)}" for o in plan.ops)
            if plan.rename_away:
                files += "\n" + "\n".join(f"• {n} → {n}.akeno-disabled" for n in plan.rename_away)
            summary = "\n".join(t(x) for x in plan.summary)
            if not confirm(self, t("Confirm installation"), summary, t("Files AKENO will write:") + "\n" + files +
                           "\n\n" + t("Existing files are backed up and verified first. Nothing else is changed."),
                           ok_text=t("Install")):
                self._say(t("Cancelled by you. Nothing was changed."))
                self._done_ui()
                self._set_run("confirm", "fail")
                return
            self._set_run("confirm", "ok")
            self._apply(g, plan)

        self.ctl.run("install", stage, staged, lambda e: self._fail(str(e)), on_progress=progress)

    def _apply(self, g, plan) -> None:
        self._say(t("Creating backup and applying changes…"))

        def work(emit):
            return self.s.install(g, plan, log_cb=lambda m: emit(m))

        def done(res):
            for m in res.messages:
                self._say(t(m))
            if res.ok:
                for k in ("backup", "apply", "validate", "record"):
                    self.run_states[k] = "ok"
                self._say(t("Done. Start the game and enable DLSS in its settings; open the OptiScaler overlay "
                            "with the Insert key to check Neural Rendering."))
            else:
                self.run_states.update({"apply": "fail", "validate": "fail"})
            self._done_ui()
            self.ctl.installs_changed.emit()
            self.ctl.games_changed.emit()
            info(self, t("Install / Update"), t("Installed and validated.") if res.ok else t("Installation failed."),
                 "\n".join(t(m) for m in res.messages), error=not res.ok)

        def prog(msg):
            self._say(t(str(msg)))
            if "backup" in str(msg).lower():
                self.run_states["backup"] = "ok"

        self.ctl.run("install", work, done, lambda e: self._fail(str(e)), on_progress=prog)

    def _fail(self, msg: str) -> None:
        self._say(t("Failed: ") + msg)
        self._done_ui()
        info(self, t("Install / Update"), t("Installation did not start."), msg, error=True)

    def _done_ui(self) -> None:
        self.progress.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.spinner.stop()
        self._refresh_preflight()

    def _cancel(self) -> None:
        if self.token:
            self.token.cancel()
            self._say(t("Cancelling…"))

    def _restore(self) -> None:
        g, a, _ = self._ctx()
        active = self.s.installer.active_install(g.id, a.id) if g and a else None
        if active:
            self.win.restore_transaction(active["id"])
