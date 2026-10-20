"""Hardware Scan page: every detected GPU, system and display details, diagnostic export."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget, QButtonGroup

from akeno.core.models import Vendor
from akeno.diagnostics import report
from akeno.hardware.gpu_db import nr_hardware_support
from akeno.i18n import t
from akeno.ui import art, theme
from akeno.ui.pages.dashboard import SUPPORT_TEXT, fmt_gb
from akeno.ui.widgets import Card, KeyValueGrid, Spinner, StatusPill, button, clear_layout, confirm, info, label


def collect_diagnostics(s) -> dict:
    game = s.selected_game()
    compat = s.compatibility(game) if game else None
    rec = s.recommendation(game) if game else None
    return {
        "system": s.system.to_dict() if s.system else None,
        "selected_gpu": s.selected_gpu().to_dict() if s.selected_gpu() else None,
        "games": [g.to_dict() for g in s.library.games()],
        "library_errors": s.library.last_errors,
        "selected_game": game.id if game else None,
        "compatibility": None if not compat else {
            "status": compat.status.value, "backend": compat.backend_id, "confidence": compat.confidence,
            "findings": [f.__dict__ | {"severity": f.severity.value} for f in compat.findings],
            "evidence": compat.evidence},
        "recommendation": None if not rec else {"profile": rec.profile_id, "basis": rec.basis,
                                                "headline": rec.headline, "reasons": rec.reasons},
        "transactions": [dict(tx, files=s.db.tx_files(tx["id"])) for tx in s.db.transactions()],
        "benchmarks": [{k: v for k, v in b.items() if k != "csv_path"} for b in s.db.benchmarks()],
        "runtimes": [{k: v for k, v in r.items() if k not in ("stored_path", "original_path")} for r in s.db.runtimes()],
        "catalog_version": s.catalog.version, "compat_db_version": s.compat_db.version,
        "compat_db_origin": s.compat_db.origin,
    }


def export_diagnostics(parent, s) -> None:
    text = t("The diagnostic package contains:") + "\n• " + "\n• ".join(t(x) for x in report.EXPORT_DESCRIPTION)
    text += "\n\n" + t("It never contains passwords, tokens, game files or backups.")
    if not confirm(parent, t("Export Diagnostic Package"), text, ok_text=t("Choose location…")):
        return
    default = os.path.join(os.path.expanduser("~"), f"AKENO-diagnostics-{datetime.now():%Y%m%d-%H%M}.zip")
    path, _ = QFileDialog.getSaveFileName(parent, t("Export Diagnostic Package"), default, "ZIP (*.zip)")
    if not path:
        return
    try:
        report.build(path, collect_diagnostics(s))
        info(parent, t("Export Diagnostic Package"), t("Saved to {p}", p=path))
    except OSError as exc:
        info(parent, t("Export Diagnostic Package"), str(exc), error=True)


class HardwarePage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(label(t("Hardware Scan"), "h1"))
        top.addStretch(1)
        self.spinner = Spinner()
        top.addWidget(self.spinner)
        self.btn_scan = button(t("Rescan"), "refresh", "primary", on_click=lambda: ctl.scan_hardware())
        top.addWidget(self.btn_scan)
        top.addWidget(button(t("Export Diagnostic Package"), "export",
                             tip=t("Save a report for troubleshooting (you choose where)"),
                             on_click=lambda: export_diagnostics(self, self.s)))
        lay.addLayout(top)
        lay.addWidget(label(t("AKENO reads your GPUs through DXGI, the Windows display-driver registry and, when the "
                              "NVIDIA driver provides it, NVML. No NVIDIA software is required to start AKENO."),
                            "dim", wrap=True))
        self.gpu_box = QVBoxLayout()
        self.gpu_box.setSpacing(12)
        lay.addLayout(self.gpu_box)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.sys_card = Card(t("System"), "cpu", theme.BLUE)
        self.sys_grid = KeyValueGrid(170)
        self.sys_card.body.addWidget(self.sys_grid)
        row.addWidget(self.sys_card, 1)
        self.disp_card = Card(t("Displays"), "monitor", theme.CYAN)
        self.disp_grid = KeyValueGrid(170)
        self.disp_card.body.addWidget(self.disp_grid)
        row.addWidget(self.disp_card, 1)
        lay.addLayout(row)
        self.err_card = Card(t("Detection notes"), "info", theme.AMBER)
        self.err_text = label("", "dim", wrap=True, selectable=True)
        self.err_card.body.addWidget(self.err_text)
        lay.addWidget(self.err_card)
        lay.addStretch(1)
        ctl.hardware_changed.connect(self.refresh)
        ctl.busy_changed.connect(self._busy)
        self.refresh()

    def _busy(self, key, on):
        if key == "hardware":
            (self.spinner.start if on else self.spinner.stop)()
            self.btn_scan.setEnabled(not on)

    def refresh(self) -> None:
        clear_layout(self.gpu_box)
        info_ = self.s.system
        if info_ is None:
            self.gpu_box.addWidget(label(t("Not scanned yet. Click Rescan to detect your hardware."), "dim"))
            self.sys_grid.set_rows([])
            self.disp_grid.set_rows([])
            self.err_card.hide()
            return
        selected = self.s.selected_gpu()
        group = QButtonGroup(self)
        for i, g in enumerate(info_.gpus):
            card = Card()
            h = QHBoxLayout()
            img = QLabel()
            img.setPixmap(art.gpu_card(150, 100, theme.GREEN if g.vendor == Vendor.NVIDIA else theme.TEXT_MUTED))
            h.addWidget(img, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            head = QHBoxLayout()
            head.addWidget(label(g.name, "h2"))
            v = nr_hardware_support(g)
            txt, st = SUPPORT_TEXT[v.level]
            pill = StatusPill(t(txt), st)
            pill.setToolTip(t(v.reason))
            head.addWidget(pill)
            head.addStretch(1)
            rb = QRadioButton(t("Use for analysis"))
            rb.setChecked(g is selected)
            rb.setEnabled(not g.is_software)
            rb.toggled.connect(lambda on, idx=i: on and self._select(idx))
            group.addButton(rb)
            head.addWidget(rb)
            col.addLayout(head)
            grid = KeyValueGrid(170)
            grid.set_rows([
                (t("Vendor"), g.vendor.value.upper() if g.vendor != Vendor.UNKNOWN else t("Unknown")),
                (t("PCI IDs"), f"VEN_{g.vendor_id or 0:04X} DEV_{g.device_id or 0:04X}" +
                 (f" SUBSYS_{g.subsys_id:08X}" if g.subsys_id else "")),
                (t("Architecture"), " · ".join(x for x in (g.architecture, g.chip, g.generation) if x) or t("Unknown")),
                (t("Tensor Cores"), {True: t("Yes"), False: t("No"), None: t("Unknown")}[g.has_tensor_cores]),
                (t("VRAM"), fmt_gb(g.vram_bytes)),
                (t("Driver Version"), " ".join(x for x in (g.driver_version, f"({g.driver_version_raw})"
                                                            if g.driver_version_raw and g.driver_version != g.driver_version_raw
                                                            else None) if x) or t("Unknown")),
                (t("Driver Date"), g.driver_date or t("Unknown")),
                (t("Direct3D 12 Feature Level"), g.feature_level or t("Unknown")),
                (t("Detected via"), ", ".join(g.sources) or "—"),
                (t("Support"), t(v.reason)),
            ])
            col.addWidget(grid)
            h.addLayout(col, 1)
            card.body.addLayout(h)
            self.gpu_box.addWidget(card)
        if not info_.gpus:
            self.gpu_box.addWidget(label(t("No graphics adapter was detected."), "dim"))
        ram = f"{info_.ram_bytes / 1024 ** 3:.0f} GB" if info_.ram_bytes else t("Unknown")
        self.sys_grid.set_rows([
            (t("Operating System"), " ".join(x for x in (info_.os_name, info_.os_display_version) if x) or t("Unknown")),
            (t("Build"), info_.os_build or t("Unknown")),
            (t("64-bit"), {True: t("Yes"), False: t("No"), None: t("Unknown")}[info_.is_64bit]),
            (t("CPU"), info_.cpu_name or t("Unknown")),
            (t("Memory"), ram),
            (t("DirectX"), info_.directx_version or t("Unknown")),
            (t("Hardware-accelerated GPU scheduling"),
             {True: t("On"), False: t("Off"), None: t("Unknown")}[info_.hardware_gpu_scheduling]),
            (t("Detected at"), info_.detected_at or "—"),
        ])
        self.disp_grid.set_rows([(d.name + (f" · {t('primary')}" if d.is_primary else ""),
                                  f"{d.width} × {d.height} @ {d.refresh_hz} Hz") for d in info_.displays]
                                or [(t("Displays"), t("Unknown"))])
        self.err_card.setVisible(bool(info_.detection_errors))
        self.err_text.setText("\n".join(info_.detection_errors))

    def _select(self, idx: int) -> None:
        self.ctl.set_setting("selected_gpu_index", idx)
        self.ctl.hardware_changed.emit()
