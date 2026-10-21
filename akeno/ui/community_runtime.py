"""Community runtime review, offline dry-run/deployment and fresh verification in the existing theme."""
from __future__ import annotations

import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget, QScrollArea)
from akeno.backends import runtimes
from akeno.community.runtime import Category, LABELS, load_review
from akeno.community import candidates
from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, StatusPill, KeyValueGrid, button, label


class CommunityRuntimeDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl, self.service = ctl, ctl.s.community
        self.game = ctl.s.selected_game()
        self.review = self.evaluation = self.prepared = self.last_report = None
        self.revision = 0
        self.setWindowTitle(t("Community Runtime Manager"))
        self.resize(1100, 760)
        outer = QVBoxLayout(self)
        outer.addWidget(label(t("Community Runtime Manager"), "h1"))
        gpu = ctl.s.selected_gpu()
        outer.addWidget(label(t("GPU: {gpu} · Game: {game}", gpu=gpu.name if gpu else t("Unknown"),
            game=self.game.name if self.game else t("No game selected")), "dim", wrap=True))
        cards = QHBoxLayout()
        for title, text, state in [(t("Official NVIDIA support"), t("Signature and game support are separate"), "pending"),
            (t("Community review"), t("Exact file and source review required"), "warn"),
            (t("Experimental testing"), t("Offline / single-player only"), "warn")]:
            card = Card(title, "shield", theme.VIOLET)
            card.body.addWidget(label(text, "dim", wrap=True))
            cards.addWidget(card)
        outer.addLayout(cards)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        pages = []
        for name in (t("Runtime review"), t("Offline experiment"), t("Execution evidence")):
            page = QWidget(); lay = QVBoxLayout(page)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
            self.tabs.addTab(scroll, name); pages.append(lay)
        inventory, experiment, evidence = pages
        self.candidate_choice = QComboBox()
        self.candidate_choice.addItem(t("Generic local runtime — explicit review required"), None)
        for candidate in candidates.all_candidates():
            self.candidate_choice.addItem(t("Bandukids Ada NR — RTX 4080 experimental, unverified"), candidate.id)
        inventory.addWidget(self.candidate_choice)
        self.candidate_info = label("", "dim", wrap=True)
        self.candidate_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inventory.addWidget(self.candidate_info)
        source_links = QHBoxLayout()
        self.code_link = button(t("View candidate source"), "file", on_click=lambda: self.open_candidate_source(False))
        self.runtime_link = button(t("View runtime source page"), "file", on_click=lambda: self.open_candidate_source(True))
        source_links.addWidget(self.code_link); source_links.addWidget(self.runtime_link)
        inventory.addLayout(source_links)
        self.path = QLineEdit(); self.path.setReadOnly(True)
        inventory.addWidget(self.path)
        files = QHBoxLayout()
        files.addWidget(button(t("Select local runtime"), "folder", on_click=self.choose_runtime))
        files.addWidget(button(t("Find local runtimes"), "search", on_click=self.discover))
        files.addWidget(button(t("Load review record"), "file", on_click=self.choose_review))
        files.addWidget(button(t("Save review template"), "file", on_click=self.review_template))
        inventory.addLayout(files)
        self.copies = QComboBox(); self.copies.addItem(t("Select an imported or discovered copy"), None)
        for record in self.service.runtimes.imported():
            self.copies.addItem(record['version'] + ' · ' + record['sha256'][:12], record)
        self.copies.currentIndexChanged.connect(self.select_copy)
        inventory.addWidget(self.copies)
        self.category = StatusPill(t("Unverified — Import requires further review"), "pending")
        inventory.addWidget(self.category)
        self.inventory = QPlainTextEdit(); self.inventory.setReadOnly(True); inventory.addWidget(self.inventory, 1)
        self.permission = QCheckBox(t("I have permission for local reuse and accept the recorded experimental risks"))
        inventory.addWidget(self.permission)
        row = QHBoxLayout()
        row.addWidget(button(t("Inspect runtime"), "search", on_click=self.inspect))
        self.import_button = button(t("Import selected runtime"), "download", on_click=self.import_runtime)
        row.addWidget(self.import_button); inventory.addLayout(row)
        inventory.addWidget(label(t("A local review is not an AKENO endorsement, NVIDIA authentication or a malware-free certificate. No runtime is downloaded."), "muted", wrap=True))
        form = QFormLayout()
        self.backend = QComboBox()
        for adapter in ctl.s.registry.all(): self.backend.addItem(adapter.name, adapter.id)
        self.profile = QComboBox()
        form.addRow(t("Backend"), self.backend); form.addRow(t("Profile"), self.profile)
        experiment.addLayout(form)
        self.offline = QCheckBox(t("This is an offline single-player session without multiplayer protections"))
        self.consent = QCheckBox(t("I accept an experimental install with verified backup and rollback"))
        experiment.addWidget(self.offline); experiment.addWidget(self.consent)
        experiment.addWidget(label(t("FiveM, GTA Online, ambiguous GTA sessions and protected multiplayer games remain blocked. Offline confirmation is your attestation, not live session detection."), "muted", wrap=True))
        self.plan_output = QPlainTextEdit(); self.plan_output.setReadOnly(True); experiment.addWidget(self.plan_output, 1)
        actions = QHBoxLayout()
        self.dry_button = button(t("Review dry-run and dependencies"), "search", on_click=self.prepare)
        self.install_button = button(t("Backup and install experiment"), "download", "primary", on_click=self.install)
        self.install_button.setEnabled(False)
        actions.addWidget(self.dry_button); actions.addWidget(self.install_button); experiment.addLayout(actions)
        self.evidence_grid = KeyValueGrid()
        self.evidence_grid.set_rows([(t("Neural feature creation"), t("Not verified")),
            (t("Neural feature evaluation"), t("Not verified")), (t("Current installation attribution"), t("Not verified"))])
        evidence.addWidget(self.evidence_grid)
        self.evidence_output = QPlainTextEdit(); self.evidence_output.setReadOnly(True); evidence.addWidget(self.evidence_output, 1)
        evidence.addWidget(label(t("Installed → Backend loaded → Neural initialized → Neural evaluated → Attributed working. Installing files alone is not success."), "dim", wrap=True))
        actions = QHBoxLayout()
        actions.addWidget(button(t("Start fresh observation"), "clock", on_click=self.observe))
        actions.addWidget(button(t("Check execution evidence"), "search", on_click=self.verify))
        actions.addWidget(button(t("Restore experiment"), "database", on_click=self.restore))
        actions.addWidget(button(t("Export experiment report"), "file", on_click=self.export))
        evidence.addLayout(actions)
        self.message = label("", "dim", wrap=True); outer.addWidget(self.message)
        self.backend.currentIndexChanged.connect(self.profiles)
        for control in (self.profile,): control.currentIndexChanged.connect(self.invalidate)
        self.offline.toggled.connect(self.invalidate); self.consent.toggled.connect(self.invalidate)
        self.permission.toggled.connect(self.invalidate)
        self.candidate_choice.currentIndexChanged.connect(self.candidate_changed)
        self.candidate_changed()
        self.profiles()

    def candidate_changed(self, *_):
        candidate = candidates.by_id(self.candidate_choice.currentData())
        self.review = self.evaluation = None
        self.invalidate()
        self.category.set(t("Unverified — Import requires further review"), "pending")
        self.code_link.setEnabled(candidate is not None); self.runtime_link.setEnabled(candidate is not None)
        self.candidate_info.setText(t("Bandukids candidate: {sha}. Modified runtime; Windows HashMismatch was reported. ComfyUI video claims are not game evidence. RTX 4080 is experimental/unverified; RTX 20/30 remain unverified. Source, security and local-use reviews are still required.", sha=candidate.sha256) if candidate else "")
        if self.path.text(): self.inspect()

    def open_candidate_source(self, runtime):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        candidate = candidates.by_id(self.candidate_choice.currentData())
        if candidate:
            # Explicit click on static provenance pages only; no binary download or arbitrary review URL execution.
            QDesktopServices.openUrl(QUrl(candidate.runtime_source if runtime else
                candidate.repository + "/tree/" + candidate.source_commit))

    def invalidate(self, *_):
        self.revision += 1; self.prepared = None; self.install_button.setEnabled(False)

    def profiles(self, *_):
        self.profile.clear()
        adapter = self.ctl.s.registry.get(self.backend.currentData())
        for profile in adapter.profiles(self.game): self.profile.addItem(t(profile.label), profile.params)
        index = self.profile.findText(t("Performance")); self.profile.setCurrentIndex(max(0, index))
        self.invalidate()

    def run(self, key, work, done):
        self.message.setText(t("Working…"))
        self.ctl.run('community_' + key, work, done, lambda e: self.message.setText(t(str(e))))

    def choose_runtime(self):
        path, _ = QFileDialog.getOpenFileName(self, t("Select local runtime"), "", "Runtime (*.dll *.bin)")
        if path:
            self.path.setText(path); self.review = None; self.evaluation = None; self.invalidate(); self.inspect()

    def choose_review(self):
        path, _ = QFileDialog.getOpenFileName(self, t("Load review record"), "", "JSON (*.json)")
        if path:
            self.review = self.evaluation = None
            self.invalidate()
            self.category.set(t("Unverified — Import requires further review"), "pending")
            try: self.review = load_review(path); self.inspect()
            except (OSError, ValueError) as exc: self.message.setText(t(str(exc)))

    def select_copy(self, *_):
        data = self.copies.currentData()
        if data:
            self.path.setText(data['path']); self.review = data.get('review'); self.evaluation = None; self.invalidate(); self.inspect()

    def discover(self):
        def done(reports):
            report = reports.get(runtimes.NR_RUNTIME)
            for candidate in report.candidates if report else []:
                self.copies.addItem((candidate.version or t("Unknown")) + ' · ' + candidate.source_label,
                                   {'path': candidate.path})
            self.message.setText(t("Local search finished: {n} Neural Rendering candidate(s). Regular DLSS is a different runtime.", n=len(report.candidates) if report else 0))
        self.run('discover', self.ctl.s.discover_dependencies, done)

    def show_evaluation(self, result):
        self.evaluation = result
        self.category.set(t(LABELS[result.category]), 'warn' if result.category == Category.COMMUNITY else
                          ('ok' if result.category == Category.OFFICIAL else ('fail' if result.category == Category.REJECTED else 'pending')))
        self.inventory.setPlainText(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        self.message.setText(t(result.detail))

    def inspect(self):
        if not self.path.text(): return
        path, review, revision = self.path.text(), self.review, self.revision
        candidate_id = self.candidate_choice.currentData()
        self.run('inspect', lambda: self.service.runtimes.inspect(path, review, candidate_id),
                 lambda result: self.show_evaluation(result) if revision == self.revision else None)

    def import_runtime(self):
        if not self.permission.isChecked():
            self.message.setText(t("Explicit local reuse permission is required.")); return
        path, review, revision = self.path.text(), self.review, self.revision
        candidate_id = self.candidate_choice.currentData()
        def done(result):
            if revision == self.revision:
                self.path.setText(result.path); self.show_evaluation(result)
                self.message.setText(t("Imported into the separate runtime cache. Game files are unchanged."))
        self.run('import', lambda: self.service.runtimes.import_local(path, review, True, candidate_id), done)

    def review_template(self):
        if not self.evaluation:
            self.message.setText(t("Inspect a runtime before creating a review template.")); return
        path, _ = QFileDialog.getSaveFileName(self, t("Save review template"), 'community-review.json', 'JSON (*.json)')
        if not path: return
        gpu = self.ctl.s.selected_gpu()
        template = {"schema":1, "sha256":self.evaluation.sha256, "version":self.evaluation.version,
            "repository":"", "release":"", "upstream_commit":"", "reviewer":"", "reviewed_at":"",
            "modifications":"", "build_instructions":"", "security_review":"", "license_review":"",
            "local_use_permitted":False, "limitations":[], "targets":[{"generation":gpu.generation if gpu else '',
                "driver":gpu.driver_version if gpu else '', "api":self.game.api.value if self.game else '',
                "backend":self.backend.currentData(), "status":"experimental", "evidence":""}]}
        candidate = candidates.by_id(self.candidate_choice.currentData())
        if candidate:
            try:
                template = candidates.review_draft(candidate, self.evaluation, gpu,
                    self.game.api.value if self.game else '', self.backend.currentData())
            except ValueError as exc:
                self.message.setText(t(str(exc))); return
        Path(path).write_text(json.dumps(template, indent=2), encoding='utf-8')
        self.message.setText(t("Complete the source, security, license and target review before loading this template. Empty evidence cannot authorize import."))

    def prepare(self):
        if not self.game or not self.evaluation: return
        sha, backend, params = self.evaluation.sha256, self.backend.currentData(), self.profile.currentData()
        offline, consent, revision = self.offline.isChecked(), self.consent.isChecked(), self.revision
        def done(prepared):
            if revision != self.revision: return
            self.prepared = prepared
            self.plan_output.setPlainText('\n'.join(t(x) for x in prepared.plan.summary) + '\n\n' + '\n'.join(
                f'{op.rel_path} · {op.expected_sha256 or t("Generated configuration")}' for op in prepared.plan.ops))
            self.install_button.setEnabled(True)
            self.message.setText(t("Dry-run ready. No game files changed; backup occurs only when you install."))
        self.run('prepare', lambda: self.service.prepare(self.game.id, sha, backend, params, offline, consent), done)

    def install(self):
        if not self.prepared: return
        prepared, offline, consent = self.prepared, self.offline.isChecked(), self.consent.isChecked()
        self.invalidate()
        def done(result):
            self.message.setText('\n'.join(t(x) for x in result.messages)); self.ctl.installs_changed.emit(); self.verify()
        self.run('install', lambda: self.service.install(prepared, offline, consent), done)

    def report_done(self, report):
        self.last_report = report
        state = report['neural']['state']
        self.evidence_grid.set_rows([(t("Runtime detected"), t("Yes") if report.get('runtime') else t("Not found")),
            (t("Installed"), t("Yes") if report.get('transaction') else t("Not verified")),
            (t("Backend loaded"), t("Yes") if state in ('loaded','initialized','running','verified') else t("Not verified")),
            (t("Neural feature creation"), t("Yes") if state in ('initialized','verified') else t("Not verified")),
            (t("Neural feature evaluation"), t("Yes") if state in ('running','verified') else t("Not verified")),
            (t("Current installation attribution"), t("Verified") if state == 'verified' else t("Not verified"))])
        self.evidence_output.setPlainText(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        self.message.setText(t("Evidence updated. File installation is separate from neural execution."))

    def observe(self):
        if self.game: self.run('observe', lambda: self.service.observe(self.game.id), self.report_done)

    def verify(self):
        if self.game: self.run('verify', lambda: self.service.report(self.game.id), self.report_done)

    def restore(self):
        if not self.game: return
        tx = self.ctl.s.installer.active_install(self.game.id)
        if not tx or not tx['backend_id'].startswith('community:'): return
        self.run('restore', lambda: self.ctl.s.restore(tx['id']),
                 lambda result: self.message.setText('\n'.join(t(x) for x in result.messages)))

    def export(self):
        if not self.last_report: return
        path, _ = QFileDialog.getSaveFileName(self, t("Export experiment report"), 'akeno-community-report.json', 'JSON (*.json)')
        if path: Path(path).write_text(json.dumps(self.last_report, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
