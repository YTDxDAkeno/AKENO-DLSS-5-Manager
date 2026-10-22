"""Community runtime review, offline dry-run/deployment and fresh verification in the existing theme."""
from __future__ import annotations

import json
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget, QScrollArea)
from akeno.backends import runtimes
from akeno.community.runtime import Category, LABELS, load_review
from akeno.community import candidates
from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, StatusPill, KeyValueGrid, button, label, confirm


class CommunityRuntimeDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl, self.service = ctl, ctl.s.community
        self._closing = False
        self._monitor_epoch = 0
        self._diagnostic_busy = False
        self._pending_diagnostic = None
        self._last_view = None
        self._last_status = None
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
            (t("Community review"), t("Exact file identity and source required"), "warn"),
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
        self.candidate_choice.addItem(t("Advanced local experiment — source required"), None)
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
        self.source = QLineEdit()
        self.source.setPlaceholderText(t("HTTPS source page for an advanced local experiment"))
        inventory.addWidget(self.source)
        files = QHBoxLayout()
        files.addWidget(button(t("Select local runtime"), "folder", on_click=self.choose_runtime))
        files.addWidget(button(t("Find local runtimes"), "search", on_click=self.discover))
        inventory.addLayout(files)
        advanced = QWidget(); advanced_row = QHBoxLayout(advanced)
        advanced_row.addWidget(button(t("Load review record"), "file", on_click=self.choose_review))
        advanced_row.addWidget(button(t("Save review template"), "file", on_click=self.review_template))
        advanced.hide()
        inventory.addWidget(button(t("Advanced review tools"), "file", on_click=lambda: advanced.setVisible(not advanced.isVisible())))
        inventory.addWidget(advanced)
        self.copies = QComboBox(); self.copies.addItem(t("Select an imported or discovered copy"), None)
        records = self.service.runtimes.imported() + ctl.s.offline_bundle.candidates(self.service.runtimes)
        for record in sorted(records, key=lambda r: r['category'] != Category.OFFICIAL.value):
            origin = t('bundled') if record.get('origin') == 'bundle' else t('Imported')
            self.copies.addItem(origin + ' · ' + (record['version'] or t('Unknown')) + ' · ' + record['sha256'][:12], record)
        self.copies.currentIndexChanged.connect(self.select_copy)
        inventory.addWidget(self.copies)
        self.category = StatusPill(t("Unverified — Source and consent required"), "pending")
        inventory.addWidget(self.category)
        self.identity = label('', 'dim', wrap=True); inventory.addWidget(self.identity)
        self.inventory = QPlainTextEdit(); self.inventory.setReadOnly(True); self.inventory.hide()
        inventory.addWidget(button(t("Technical details"), 'file', on_click=lambda: self.inventory.setVisible(not self.inventory.isVisible())))
        inventory.addWidget(self.inventory, 1)
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
        self.runtime_preview = label("", "dim", wrap=True); experiment.addWidget(self.runtime_preview)
        self.offline = QCheckBox(t("This is an offline single-player session without multiplayer protections"))
        self.consent = QCheckBox(t("I accept an experimental install with verified backup and rollback"))
        experiment.addWidget(self.offline); experiment.addWidget(self.consent)
        experiment.addWidget(label(t("FiveM, GTA Online, ambiguous GTA sessions and protected multiplayer games remain blocked. Offline confirmation is your attestation, not live session detection."), "muted", wrap=True))
        self.plan_output = QPlainTextEdit(); self.plan_output.setReadOnly(True); experiment.addWidget(self.plan_output, 1)
        actions = QHBoxLayout()
        self.dry_button = button(t("Test Compatibility"), "search", on_click=self.prepare)
        self.install_button = button(t("Install Experimental DLSS 5"), "download", "primary", on_click=self.install)
        self.install_button.setEnabled(False)
        actions.addWidget(self.dry_button); actions.addWidget(self.install_button); experiment.addLayout(actions)
        self.evidence_grid = KeyValueGrid()
        self.evidence_grid.set_rows([(t("Neural feature creation"), t("Not verified")),
            (t("Neural feature evaluation"), t("Not verified")), (t("Current installation attribution"), t("Not verified"))])
        evidence.addWidget(self.evidence_grid)
        self.evidence_output = QPlainTextEdit(); self.evidence_output.setReadOnly(True); evidence.addWidget(self.evidence_output, 1)
        evidence.addWidget(label(t("Installed → Backend loaded → Neural initialized → Neural evaluated → Attributed working. Installing files alone is not success."), "dim", wrap=True))
        actions = QHBoxLayout()
        actions.addWidget(button(t("Start DLSS 5 Verification Session"), "clock", on_click=self.observe))
        actions.addWidget(button(t("Check execution evidence"), "search", on_click=self.verify))
        actions.addWidget(button(t("Restore experiment"), "database", on_click=self.restore))
        actions.addWidget(button(t("Export experiment report"), "file", on_click=self.export))
        evidence.addLayout(actions)
        self.message = label("", "dim", wrap=True); outer.addWidget(self.message)
        self.backend.currentIndexChanged.connect(self.profiles)
        for control in (self.profile,): control.currentIndexChanged.connect(self.invalidate)
        self.offline.toggled.connect(self.invalidate); self.consent.toggled.connect(self.invalidate)
        self.permission.toggled.connect(self.invalidate)
        self.source.textChanged.connect(self.invalidate)
        self.candidate_choice.currentIndexChanged.connect(self.candidate_changed)
        self.candidate_changed()
        self.profiles()
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(3000)
        self.monitor_timer.timeout.connect(self.monitor_tick)
        self.monitor = QCheckBox(t("Collect local diagnostics automatically while the game runs"))
        evidence.addWidget(self.monitor)
        self.monitor.toggled.connect(self.toggle_monitor)
        controls = QHBoxLayout()
        self.monitor_indicator = StatusPill(t('Monitoring stopped'), 'pending')
        self.last_checked = label('', 'muted')
        controls.addWidget(self.monitor_indicator)
        controls.addWidget(button(t("Export support ZIP"), "file", on_click=self.export_support))
        controls.addWidget(button(t('Stop monitoring'), 'stop', on_click=lambda: self.stop_monitor()))
        controls.addWidget(self.last_checked, 1); evidence.addLayout(controls)
        evidence.addWidget(button(t('Open desktop monitoring HUD'),on_click=self.open_hud))
        self.include_excerpts = QCheckBox(t('Include redacted recent log excerpts in support export'))
        evidence.addWidget(self.include_excerpts)
        self.performance = QCheckBox(t("Start automatic performance monitoring (local PresentMon required)"))
        experiment.addWidget(self.performance)
        self.hud = None
        self.capture_busy = False
        self.capture_token = None
        self.finished.connect(self._closed)
        if self.copies.count() == 2:
            self.copies.setCurrentIndex(1)
        if ctl.s.offline_bundle.errors:
            self.message.setText('\n'.join(ctl.s.offline_bundle.errors))

    def open_hud(self):
        from akeno.ui.monitor_hud import MonitorHud
        if self.hud:self.hud.close()
        self.hud=MonitorHud(lambda:self.last_report,self);self.hud.show()

    def _closed(self, *_):
        self._closing = True
        if self.hud:self.hud.close()
        self._pending_diagnostic = None
        self.stop_monitor()

    def stop_monitor(self, save=True):
        self._monitor_epoch += 1
        self._pending_diagnostic = None
        self.monitor_timer.stop()
        self.monitor.blockSignals(True); self.monitor.setChecked(False); self.monitor.blockSignals(False)
        if self.capture_token:
            self.capture_token.cancel()
        if not self._closing:
            self.monitor_indicator.set(t('Monitoring stopped'), 'pending')
            if save: self.message.setText(t('Monitoring stopped. The last report is preserved.'))
        if save and self.game:
            game_id = self.game.id
            self.ctl.run('community_stop', lambda: self.service.stop_observation(game_id),
                lambda report: self.report_done(report, force=True) if not self._closing else None)

    def monitor_tick(self):
        self._request_diagnostics(manual=False)
        if self._closing or not self.performance.isChecked() or self.capture_busy or not self.game or self.ctl.is_busy('benchmark'):
            return
        session = (self.last_report or {}).get('session', {})
        process = session.get('process')
        if not process or session.get('ended') or not process.get('created_at'):
            return
        runner = self.ctl.s.benchmarks.runner
        if not runner or not runner.available():
            # Stable warning; do not repeat a busy notification on every tick.
            text = t('Automatic FPS monitoring needs a locally verified PresentMon executable.')
            if self.message.text() != text: self.message.setText(text)
            return
        permission=runner.permission_status()
        if permission['state'] in ('denied','permission_required','unavailable'):
            self.performance.setChecked(False)
            self.message.setText(t('FPS capture unavailable. Neural log diagnostics continue.')+' '+permission['detail'])
            return
        from akeno.security.downloads import CancelToken
        self.capture_busy = True; self.capture_token = CancelToken()
        game, gpu, token = self.game, self.ctl.s.selected_gpu(), self.capture_token
        pid = process['pid']
        def work():
            from akeno.hardware.sensors import sample
            meta = {'gpu': gpu.name if gpu else None, 'driver': gpu.driver_version if gpu else None,
                'neural_config': {'capture_conditions': {}, 'automatic_monitoring': True}}
            result = self.ctl.s.benchmarks.run(game.id, Path(game.exe_path).name, 'automatic', meta, 30, 0,
                token=token, process_id=pid, sensor_sampler=lambda: sample(gpu),
                context_provider=lambda: self.service.capture_context(game.id, pid))
            self.ctl.s.benchmarks.rotate_automatic()
            return result
        def done(_):
            self.capture_busy = False
            if not self._closing: self.ctl.benchmarks_changed.emit()
        def failed(error):
            self.capture_busy = False
            if not self._closing and not token.cancelled:
                self.performance.setChecked(False)
                self.message.setText(t('FPS capture stopped. Enable it explicitly to retry.')+' '+t(str(error)))
        if not self.ctl.run('community_performance', work, done, failed, silent=True): self.capture_busy = False

    def toggle_monitor(self, enabled):
        if enabled:
            tx = self.ctl.s.installer.active_install(self.game.id) if self.game else None
            if not tx or not tx['backend_id'].startswith('community:'):
                self.monitor.setChecked(False)
                self.message.setText(t('Install an experimental runtime before enabling automatic monitoring.'))
                return
            self._request_diagnostics(start=True, manual=True)
        else:
            self.stop_monitor()

    def _request_diagnostics(self, start=False, manual=False):
        if self._closing or not self.game: return
        if self._diagnostic_busy:
            # One pending request; a new observation takes precedence over periodic polls.
            previous = self._pending_diagnostic or (False, False)
            self._pending_diagnostic = (start or previous[0], manual or previous[1])
            return
        self._diagnostic_busy = True
        if manual: self.message.setText(t('Working…'))
        game_id = self.game.id
        epoch = self._monitor_epoch
        def work():
            report = self.service.observe(game_id) if start else self.service.report(game_id)
            if self._closing or epoch != self._monitor_epoch:
                self.service.stop_observation(game_id)
            return report
        def done(report):
            self._diagnostic_busy = False
            if self._closing or epoch != self._monitor_epoch: return
            self.report_done(report, force=manual)
            if start and not report.get('session', {}).get('ended'):
                self.monitor.blockSignals(True); self.monitor.setChecked(True); self.monitor.blockSignals(False)
                self.monitor_indicator.set(t('Monitoring active'), 'ok')
                self.monitor_timer.start()
            self._drain_diagnostic()
        def failed(error):
            self._diagnostic_busy = False
            if self._closing or epoch != self._monitor_epoch: return
            text = t(str(error))
            if self.message.text() != text: self.message.setText(text)
            if start: self.monitor.setChecked(False)
            self._drain_diagnostic()
        if not self.ctl.run('community_diagnostics', work, done, failed, silent=not manual):
            self._diagnostic_busy = False

    def _drain_diagnostic(self):
        pending, self._pending_diagnostic = self._pending_diagnostic, None
        if pending and not self._closing:
            QTimer.singleShot(0, lambda: self._request_diagnostics(start=pending[0], manual=pending[1]))

    def candidate_changed(self, *_):
        candidate = candidates.by_id(self.candidate_choice.currentData())
        self.review = self.evaluation = None
        self.invalidate()
        self.category.set(t("Unverified — Source and consent required"), "pending")
        self.code_link.setEnabled(candidate is not None); self.runtime_link.setEnabled(candidate is not None)
        self.candidate_info.setText(t("Bandukids candidate: {sha}. Modified runtime; Windows HashMismatch was reported. ComfyUI video claims are not game evidence. RTX 4080 is experimental/unverified; RTX 20/30 remain unverified. Local reuse permission is required. RTX 40/50 offline tests remain unverified until execution succeeds.", sha=candidate.sha256) if candidate else "")
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
        self.revision += 1; self.prepared = None
        self.install_button.setEnabled(bool(self.evaluation and self.evaluation.category != Category.REJECTED
            and self.permission.isChecked() and self.offline.isChecked() and self.consent.isChecked()))

    def profiles(self, *_):
        self.profile.clear()
        adapter = self.ctl.s.registry.get(self.backend.currentData())
        for profile in adapter.profiles(self.game): self.profile.addItem(t(profile.label), profile.params)
        index = self.profile.findText(t("Performance")); self.profile.setCurrentIndex(max(0, index))
        self.invalidate()

    def run(self, key, work, done):
        if self._closing: return
        def completed(result):
            if not self._closing: done(result)
        def failed(error):
            if not self._closing: self.message.setText(t(str(error)))
        if self.ctl.run('community_' + key, work, completed, failed):
            self.message.setText(t('Working…'))

    def choose_runtime(self):
        path, _ = QFileDialog.getOpenFileName(self, t("Select local runtime"), "", "Runtime (*.dll *.bin)")
        if path:
            self.path.setText(path); self.review = None; self.evaluation = None; self.invalidate(); self.inspect()

    def choose_review(self):
        path, _ = QFileDialog.getOpenFileName(self, t("Load review record"), "", "JSON (*.json)")
        if path:
            self.review = self.evaluation = None
            self.invalidate()
            self.category.set(t("Unverified — Source and consent required"), "pending")
            try: self.review = load_review(path); self.inspect()
            except (OSError, ValueError) as exc: self.message.setText(t(str(exc)))

    def select_copy(self, *_):
        data = self.copies.currentData()
        if data:
            self.path.setText(data['path'])
            review = data.get('review') or {}
            self.review = review if review.get('schema') == 3 else None
            self.source.setText(review.get('runtime_source') or review.get('release') or review.get('repository') or '')
            self.evaluation = None; self.invalidate(); self.inspect()

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
        self.category.set(t(LABELS[result.category]), 'warn' if result.category in (Category.COMMUNITY, Category.ADVANCED) else
                          ('ok' if result.category == Category.OFFICIAL else ('fail' if result.category == Category.REJECTED else 'pending')))
        self.inventory.setPlainText(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        identity = t("Runtime: {version} · SHA-256: {sha} · Source: {source}", version=result.version or t('Unknown'), sha=result.sha256, source=result.source)
        self.identity.setText(identity)
        self.runtime_preview.setText(identity + '\n' + t(LABELS[result.category]) + '\n' +
            t("Signature result: {detail}. This does not certify runtime safety.", detail=result.signature.get('detail', t('Unknown'))))
        self.message.setText(t(result.detail))
        self.invalidate()

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
        if review is None and self.evaluation and self.evaluation.category != Category.OFFICIAL:
            from akeno.community.profiles import local_profile
            try:
                review = local_profile(self.evaluation, self.source.text(), True)
            except ValueError as exc:
                self.message.setText(str(exc)); return
        def done(result):
            if revision == self.revision:
                self.path.setText(result.path); self.review = result.review; self.show_evaluation(result)
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

    def prepare(self, install_after=False):
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
            if install_after and confirm(self, t("Install Experimental DLSS 5"),
                    t("This runtime is experimental. Keep Windows protections enabled. AKENO will back up files before installing. Execution is not yet verified."),
                    ok_text=t("Install Experimental DLSS 5")):
                self.install()
        path, review = self.evaluation.path, self.review
        permission = self.permission.isChecked()
        source = self.source.text()
        evaluation = self.evaluation
        def work():
            from akeno.community.profiles import local_profile
            existing = self.ctl.s.db.get_setting('community-runtime:' + sha)
            if not existing or ((existing.get('review') or {}).get('schema') != 3 and evaluation.category != Category.OFFICIAL):
                profile = review or (None if evaluation.category == Category.OFFICIAL else local_profile(evaluation, source, permission))
                self.service.runtimes.import_local(path, profile, permission)
            return self.service.prepare(self.game.id, sha, backend, params, offline, consent)
        self.run('prepare', work, done)

    def install(self):
        if not self.prepared:
            self.prepare(install_after=True); return
        prepared, offline, consent = self.prepared, self.offline.isChecked(), self.consent.isChecked()
        self.invalidate()
        def done(result):
            self.message.setText('\n'.join(t(x) for x in result.messages)); self.ctl.installs_changed.emit(); self.verify()
            if result.ok and self.performance.isChecked(): self.monitor.setChecked(True)
        self.run('install', lambda: self.service.install(prepared, offline, consent), done)

    def report_done(self, report, force=False):
        if self._closing: return
        from datetime import datetime
        self.last_report = report
        session = report.get('session', {})
        state = session.get('state', 'ready')
        state_labels = {'ready':t('Ready'), 'waiting_for_game':t('Waiting for game'),
            'backend_not_detected':t('Backend not detected'), 'neural_initialization_pending':t('Neural initialization pending'),
            'neural_evaluation_detected':t('Neural evaluation detected'), 'neural_evaluation_failed':t('Neural evaluation failed'),
            'game_exited':t('Game exited'), 'game_restarted':t('Game restarted — start a new session'),
            'installation_changed':t('Installation changed'), 'integrity_changed':t('Watched file changed'), 'stopped':t('Stopped')}
        stages = report['neural'].get('stages', {})
        status = (state, report['neural']['state'], report['neural'].get('detail'), tuple(report['neural'].get('error_codes', [])))
        view = json.dumps([session.get('process'), state, stages, report['neural'].get('lines'),
            report['neural'].get('logs'), report['neural'].get('counters'), report.get('current_session_benchmark_ids'), report.get('benchmark_status'), self.capture_busy, self.performance.isChecked()], sort_keys=True, default=str)
        # No repaint/scroll reset when only counters or polling durations change.
        if view != self._last_view:
            self._last_view = view
            self.evidence_output.setPlainText(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            self.evidence_grid.set_rows([
                (t('Installation'), t('Installed') if report.get('transaction') else t('Not verified')),
                (t('Game session'), state_labels.get(state, state)),
                (t('OptiScaler / backend'), t('Yes') if stages.get('backend_loaded') else t('Not verified')),
                (t('Runtime loaded'), t('Yes') if stages.get('runtime_loaded') else t('Not verified')),
                (t('NGX initialized'), t('Yes') if stages.get('ngx_initialized') else t('Not verified')),
                (t('Feature 18 created'), t('Yes') if stages.get('feature_18_created') else t('Not verified')),
                (t('Successful NR API evaluations'), str(report['neural'].get('counters', {}).get('eval_success', t('Not measured')))),
                (t('Neural feature evaluation'), t('Yes') if stages.get('feature_evaluated') else t('Not verified')),
                (t('FPS monitoring'), t('Active') if self.capture_busy else (t('Ready') if self.performance.isChecked() and not session.get('ended') else t('Off'))),
                (t('Current benchmark'), t(report.get('benchmark_status', 'Not verified')))])
        if force or status != self._last_status:
            self._last_status = status
            if state == 'waiting_for_game': text = t('DLSS 5 files installed. Waiting for {game} to start.', game=self.game.name)
            elif state in ('game_exited','game_restarted'):
                text = t('Game exited. Verification session complete; the last report is preserved.')
                if not stages.get('evaluations_attributed_to_game'):
                    text += ' ' + t('No conclusive Neural Rendering evaluation evidence was captured.')
            elif state == 'stopped': text = t('Monitoring stopped. The last report is preserved.')
            elif state == 'neural_evaluation_detected':
                count=report['neural'].get('counters',{}).get('eval_success')
                text = t('Neural Rendering working — {count} successful API evaluations recorded',count=count) if count is not None else t('Neural Rendering active — evaluation evidence detected')
            elif state == 'neural_evaluation_failed': text = t('Neural Rendering reported a failure: {codes}', codes=', '.join(report['neural'].get('error_codes', [])) or t('View diagnostic details'))
            elif state == 'backend_not_detected': text = t('{game} was detected, but OptiScaler Neural Rendering activity was not confirmed. View diagnostic details.', game=self.game.name)
            elif state == 'neural_initialization_pending': text = t('Backend loaded. Waiting for fresh Neural Rendering initialization and evaluation evidence.')
            else: text = t(report['neural'].get('detail') or 'Runtime installed — execution not yet verified')
            if session.get('started_after_game_launch'):
                text += ' ' + t('Monitoring started after game launch. Restart the game to capture initialization if needed.')
            if self.message.text() != text: self.message.setText(text)
        self.last_checked.setText(t('Last checked: {time}', time=datetime.now().strftime('%H:%M:%S')))
        if session.get('ended'):
            self.monitor.blockSignals(True); self.monitor.setChecked(False); self.monitor.blockSignals(False)
            self.stop_monitor(save=False)

    def observe(self):
        self._request_diagnostics(start=True, manual=True)

    def verify(self):
        self._request_diagnostics(manual=True)

    def export_support(self):
        if not self.last_report: return
        path, _ = QFileDialog.getSaveFileName(self, t('Export support ZIP'), 'akeno-support.zip', 'ZIP (*.zip)')
        if not path: return
        include = self.include_excerpts.isChecked()
        if include and not confirm(self, t('Export support ZIP'), t('Export redacted recent log excerpts? Review the resulting archive before sharing.')): return
        from akeno.diagnostics.support import export_support_zip
        report = self.last_report
        self.run('support_export', lambda: export_support_zip(report, path, include, include),
            lambda _: self.message.setText(t('Support ZIP saved locally. No files were uploaded.')))

    def restore(self):
        if not self.game: return
        tx = self.ctl.s.installer.active_install(self.game.id)
        if not tx or not tx['backend_id'].startswith('community:'): return
        self.run('restore', lambda: self.ctl.s.restore(tx['id']),
                 lambda result: self.message.setText('\n'.join(t(x) for x in result.messages)))

    def export(self):
        if not self.last_report: return
        path, _ = QFileDialog.getSaveFileName(self, t("Export experiment report"), 'akeno-community-report.json', 'JSON (*.json)')
        if path:
            from akeno.diagnostics.support import safe_report
            Path(path).write_text(json.dumps(safe_report(self.last_report), indent=2, ensure_ascii=False, default=str), encoding='utf-8')
