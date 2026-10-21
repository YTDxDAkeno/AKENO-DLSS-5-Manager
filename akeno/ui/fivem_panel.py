"""FiveM wizard embedded in the existing Library and Secure Install pages."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem, QHeaderView)

from akeno.fivem.models import ServerPolicy
from akeno.fivem.messages import describe
from akeno.diagnostics import neural
from akeno.hardware.support import OFFICIAL
from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, KeyValueGrid, button, confirm, info, label


class FiveMPanel(Card):
    def __init__(self, ctl, game=None, compact=False):
        super().__init__(t("FiveM Legacy setup"), "gamepad", theme.CYAN)
        self.ctl, self.s, self.game = ctl, ctl.s, game
        self.prepared = self.observation = None
        self._loaded_game_id = None
        self.context = label("", "dim", wrap=True)
        self.body.addWidget(self.context)
        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(220)
        analysis = QWidget()
        analysis_layout = QVBoxLayout(analysis)
        analysis_layout.addWidget(label(t("Existing files are inspected without replacing a working configuration."), "muted", wrap=True))
        self.overview = KeyValueGrid(145)
        analysis_layout.addWidget(self.overview)
        self.plugins = QTableWidget(0, 3)
        self.plugins.setHorizontalHeaderLabels([t("Component"), t("Role"), t("Verification")])
        self.plugins.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.plugins.verticalHeader().hide()
        self.plugins.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plugins.setMinimumHeight(110)
        self.plugins.setMaximumHeight(170)
        analysis_layout.addWidget(self.plugins)
        self.report.setMaximumHeight(140)
        self.report.hide()
        analysis_layout.addWidget(button(t("Technical details"), "file", kind="link",
            on_click=lambda: self.report.setVisible(not self.report.isVisible())))
        analysis_layout.addWidget(self.report)
        actions = QHBoxLayout()
        actions.addWidget(button(t("Analyze"), "search", "primary", on_click=self.analyze))
        actions.addWidget(button(t("Export diagnostics"), "file", on_click=self.export))
        analysis_layout.addLayout(actions)
        self.tabs.addTab(analysis, t("1. Analysis"))
        policy_page = QWidget()
        form = QFormLayout(policy_page)
        self.server = QLineEdit()
        self.reference = QLineEdit()
        self.owned = QCheckBox(t("I operate this test server"))
        self.consent = QCheckBox(t("I consent to an experimental test"))
        self.permission = QComboBox()
        for text, value in (("Unknown", None), ("Allowed", True), ("Prohibited", False)):
            self.permission.addItem(t(text), value)
        self.pure = QComboBox()
        for text, value in (("Unknown", None), ("0", 0), ("1", 1), ("2", 2)):
            self.pure.addItem(t(text), value)
        self.hook = QComboBox()
        for text, value in (("Unknown", None), ("Allowed", True), ("Prohibited", False)):
            self.hook.addItem(t(text), value)
        self.backend = QComboBox()
        self.backend.addItem("DLSS5-Feeder", "dlss5_feeder")
        self.backend.addItem("OptiScaler NR", "optiscaler_nr")
        for title, widget in ((t("Server identifier"), self.server), (t("Permission reference"), self.reference),
                              (t("Operator"), self.owned), (t("Graphics plugins"), self.permission),
                              (t("Pure Mode"), self.pure), (t("Script Hook permission"), self.hook),
                              ("Backend", self.backend), (t("Experimental consent"), self.consent)):
            form.addRow(t(title), widget)
        form.addRow(label(t("Ownership does not bypass client protection. AKENO never changes server security settings."), "muted", wrap=True))
        form.addRow(button(t("Save server profile"), "check", "primary", on_click=self.save))
        self.tabs.addTab(policy_page, t("2. Server policy"))
        evidence = QWidget()
        el = QVBoxLayout(evidence)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(220)
        el.addWidget(self.evidence)
        er = QHBoxLayout()
        er.addWidget(button(t("Start observation"), "clock", on_click=self.observe))
        er.addWidget(button(t("Check execution logs"), "refresh", on_click=self.check_logs))
        el.addLayout(er)
        self.tabs.addTab(evidence, t("3. Execution evidence"))
        installation = QWidget()
        il = QVBoxLayout(installation)
        self.findings = QPlainTextEdit()
        self.findings.setReadOnly(True)
        self.findings.setMinimumHeight(180)
        il.addWidget(self.findings)
        ir = QHBoxLayout()
        ir.addWidget(button(t("Create Backup"), "database", on_click=self.snapshot))
        self.plan_button = button(t("Review installation plan"), "file", on_click=self.plan)
        ir.addWidget(self.plan_button)
        self.install_button = button(t("Install reviewed plan"), "download", "primary", on_click=self.install)
        self.install_button.setEnabled(False)
        ir.addWidget(self.install_button)
        il.addLayout(ir)
        self.tabs.addTab(installation, t("4. Installation"))
        for control in (self.server, self.reference):
            control.textChanged.connect(self._invalidate)
        for control in (self.permission, self.pure, self.hook, self.backend):
            control.currentIndexChanged.connect(self._invalidate)
        self.owned.toggled.connect(self._invalidate)
        self.consent.toggled.connect(self._invalidate)
        self.refresh(game)
        if compact:
            self.tabs.hide()
            self.body.addWidget(label(t("Open Secure Install for the complete server policy, runtime evidence and backup wizard."), "muted", wrap=True))

    def _invalidate(self, *args):
        self.prepared = None
        self.install_button.setEnabled(False)

    def refresh(self, game=None):
        self.game = game or self.s.selected_game()
        g = self.game
        if not g or g.launcher != "fivem":
            return
        adapter = self.s.backend_for(g)
        if not adapter:
            self.context.setText(t("FiveM installation is unavailable. Analyze the library again."))
            return
        i, gpu = adapter.installation, self.s.selected_gpu()
        self.context.setText(f"{g.name} · {gpu.name if gpu else t('Unknown')} · {gpu.driver_version if gpu else '—'}\n{i.app_dir}")
        if self._loaded_game_id != g.id:
            self._loaded_game_id = g.id
            self._invalidate()
            self.observation = None
            p = self.s.fivem.policy(g.id)
            self.server.setText(p.server_id)
            self.reference.setText(p.permission_reference)
            self.owned.setChecked(p.own_server)
            self.consent.setChecked(p.experimental_consent)
            for combo, value in ((self.permission, p.graphics_plugins_allowed), (self.pure, p.pure_level),
                                  (self.hook, p.script_hook_allowed), (self.backend, adapter.delegate.id)):
                combo.setCurrentIndex(max(0, combo.findData(value)))
        official = OFFICIAL.get(gpu.generation if gpu else None, {})
        technologies = [f"{name}: {t('Supported') if official.get(key) else t('Unsupported or unknown')}"
                        for key, name in (("sr", "DLSS SR"), ("rr", "DLSS RR"), ("fg", "DLSS FG"), ("mfg", "DLSS MFG"))]
        summary = {"edition": i.edition.value, "FiveM": str(i.app_dir), "GTA V": str(i.gta_dir),
                   "GPU": gpu.name if gpu else None, "driver": gpu.driver_version if gpu else None,
                   "plugins": [{"name": f.path.name, "kind": f.kind, "sha256": f.sha256} for f in i.inventory],
                   "inspection_errors": i.errors, "backup_transactions": len(self.s.db.transactions(g.id))}
        self.overview.set_rows([(t("Edition"), i.edition.value), (t("FiveM folder"), str(i.app_dir)),
            (t("GTA V folder"), str(i.gta_dir)), (t("GPU"), gpu.name if gpu else t("Unknown")),
            (t("NVIDIA driver"), gpu.driver_version if gpu else t("Unknown")),
            (t("Backup transactions"), str(len(self.s.db.transactions(g.id))))])
        items = [f for f in i.inventory if f.path.is_relative_to(i.plugin_dir)]
        self.plugins.setRowCount(len(items))
        for row, item in enumerate(items):
            role = t("Graphics hook") if item.kind == "graphics_hook" else t("Neural consumer") if item.kind == "neural_consumer" else t("Plugin component")
            for col, value in enumerate((item.path.name, role, t("Not inspected"))):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item.sha256)
                self.plugins.setItem(row, col, cell)
        self.report.setPlainText(json.dumps(summary, indent=2, ensure_ascii=False))
        self.evidence.setPlainText("\n".join(technologies) + "\n\n" + t("Neural Rendering requires separate runtime, creation and evaluation evidence.") +
            "\n" + t("Driver versions below 616.56 are outside the documented community test matrix; newer versions still require verification.") +
            "\nhttps://www.nvidia.com/en-us/drivers/")
        result = self.s.fivem.compatibility(adapter)
        self.findings.setPlainText(t("Technical status") + ": " + t(result.status.value) + "\n\n" +
                                  "\n".join(describe(f) for f in result.findings))
        self.plan_button.setEnabled(adapter.review is not None and not result.blockers)

    def _done(self, result):
        self.prepared = None
        self.install_button.setEnabled(False)
        self.ctl.installs_changed.emit()
        info(self, t("FiveM Legacy setup"), "\n".join(t(m) for m in result.messages), error=not result.ok)

    def _error(self, error):
        info(self, t("FiveM Legacy setup"), str(error), error=True)

    def save(self):
        if not self.game:
            return
        old = self.s.fivem.policy(self.game.id)
        policy = ServerPolicy(self.server.text().strip(), self.owned.isChecked(), self.reference.text().strip(),
            self.permission.currentData(), self.pure.currentData(), self.hook.currentData(),
            frozenset({self.backend.currentData()}) if self.permission.currentData() is True else frozenset(),
            old.known_graphics_hashes, self.consent.isChecked())
        self.s.fivem.save_policy(self.game.id, policy, self.backend.currentData())
        self.prepared = None
        self.install_button.setEnabled(False)
        self.ctl.selection_changed.emit()
        self.refresh(self.game)

    def analyze(self):
        if self.game:
            game_id = self.game.id
            def work():
                g = self.s.library.reanalyze(game_id)
                if g is None:
                    raise ValueError("FiveM installation is unavailable")
                return g, self.s.fivem.diagnose_existing(g)
            def done(value):
                g, report = value
                if self.game and self.game.id == g.id:
                    self.refresh(g)
                    self.report.setPlainText(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            self.ctl.run("fivem_analyze", work, done, self._error)

    def export(self):
        if not self.game:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("Export diagnostics"), "akeno-fivem-diagnostics.json", "JSON (*.json)")
        if path:
            def done(report):
                Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                info(self, t("Export diagnostics"), t("Read-only diagnostics exported. Review local paths before sharing."))
            self.ctl.run("fivem_diagnostics", lambda: self.s.fivem.diagnose_existing(self.game), done, self._error)

    def observe(self):
        if not self.game:
            return
        a = self.s.backend_for(self.game)
        paths = [f.path.relative_to(a.installation.plugin_dir).as_posix() for f in a.installation.inventory
                 if f.path.is_relative_to(a.installation.plugin_dir)]
        from akeno.core import processes
        active = [p for p in processes.processes_in_dir(str(a.installation.root))
                  if "gtaprocess" in p.name.casefold() and p.path]
        exe = active[0].path if len(active) == 1 else self.game.exe_path
        try:
            self.observation = neural.begin(self.game.id, a.target, exe, paths)
            self.evidence.setPlainText(t("Observation started. Existing log content does not count as new execution evidence."))
        except (OSError, ValueError) as exc:
            self._error(exc)

    def check_logs(self):
        if not self.game:
            return
        from akeno.core import processes
        a = self.s.backend_for(self.game)
        try:
            result = neural.inspect(a.target, self.observation, self.game.id,
                                    [p.path for p in processes.processes_in_dir(str(a.installation.root))])
            self.evidence.setPlainText(t(result.state) + "\n" + t(result.detail) + "\n\n" +
                                      "\n".join(f"{line['file']}: {line['line']}" for line in result.lines))
        except (OSError, ValueError) as exc:
            self._error(exc)

    def snapshot(self):
        a = self.s.backend_for(self.game) if self.game else None
        if a:
            self.ctl.run("backup", lambda: self.s.fivem.snapshot(a.installation), self._done, self._error)

    def plan(self):
        a = self.s.backend_for(self.game) if self.game else None
        if not a:
            return
        def done(prepared):
            if not self.game or prepared.installation.game.id != self.game.id or self.s.fivem.policy(self.game.id) != prepared.adapter.policy:
                return
            self.prepared = prepared
            self.findings.setPlainText("\n".join(prepared.plan.summary) + "\n\n" +
                                      "\n".join(op.rel_path for op in prepared.plan.ops))
            self.install_button.setEnabled(True)
        self.ctl.run("fivem_plan", lambda: self.s.fivem.prepare(a), done, self._error)

    def install(self):
        if self.prepared and confirm(self, t("Confirm installation"), "\n".join(self.prepared.plan.summary)):
            prepared = self.prepared
            policy = self.s.fivem.policy(self.game.id)
            self.ctl.run("install", lambda: self.s.fivem.install(prepared, policy), self._done, self._error)
