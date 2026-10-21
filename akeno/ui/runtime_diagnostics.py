"""Read-only working-setup diagnosis and explicit local runtime import in the same launcher."""
import json
from akeno.diagnostics.privacy import redact
from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QCheckBox, QFileDialog
from akeno.backends import runtimes
from akeno.core.models import TrustState
from akeno.i18n import t
from akeno.ui.widgets import label, button, info


class RuntimeDiagnosticsDialog(QDialog):
    def __init__(self, ctl, game, parent=None):
        super().__init__(parent)
        self.ctl, self.game, self.report = ctl, game, None
        self.setWindowTitle(t("Inspect existing setup"))
        self.resize(900, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(label(t("Local inventory does not change game files. Signed files still require permission for local reuse."), "dim", wrap=True))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        lay.addWidget(self.output, 1)
        self.permission = QCheckBox(t("I am permitted to reuse the selected local NVIDIA runtime"))
        lay.addWidget(self.permission)
        row = QHBoxLayout()
        row.addWidget(button(t("Analyze"), "search", "primary", on_click=self.analyze))
        row.addWidget(button(t("Export diagnostics"), "file", on_click=self.export))
        self.import_button = button(t("Import verified local runtime"), "folder", on_click=self.import_local)
        self.import_button.setEnabled(False)
        self.permission.toggled.connect(self.import_button.setEnabled)
        row.addWidget(self.import_button)
        lay.addLayout(row)
        self.analyze()

    def analyze(self):
        def done(report):
            self.report = report
            self.output.setPlainText(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        self.ctl.run("runtime_diagnostics", lambda: self.ctl.s.fivem.diagnose_existing(self.game), done,
                     lambda e: self.output.setPlainText(str(e)))

    def export(self):
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("Export diagnostics"), "akeno-existing-setup.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(redact(self.report), indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def import_local(self):
        if not self.permission.isChecked():
            return
        path, _ = QFileDialog.getOpenFileName(self, t("Import verified local runtime"), "", "NVIDIA runtimes (nvngx_dlss.dll nvngx_dlssnr.dll)")
        if not path:
            return
        def done(candidate):
            self.ctl.deps_changed.emit()
            info(self, t("Import verified local runtime"), candidate.detail,
                 error=candidate.trust != TrustState.VERIFIED)
        self.ctl.run("runtime_import", lambda: runtimes.import_runtime(self.ctl.s.db, path), done,
                     lambda e: info(self, t("Import verified local runtime"), str(e), error=True))
