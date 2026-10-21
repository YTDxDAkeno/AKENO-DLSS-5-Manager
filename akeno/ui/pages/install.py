"""Secure Install wizard: grouped checks (System · Game · Dependencies · Installation), dependency resolution
and the transactional install.

Rules the page follows:
* A check is green only when it has actually passed *now*. Work that will happen later (download, import,
  backup, writing files) is shown as pending/info until it has happened.
* Every blocked state names the exact reason and, where one exists, the shortest lawful way to fix it.
* "Resolve Dependencies" performs every permitted automatic step (search the PC, import verified NVIDIA
  runtimes, download reviewed packages) after the user confirmed the list, then refreshes the checklist.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QSizePolicy, QVBoxLayout, QWidget)

from akeno.backends import runtimes
from akeno.core.models import AntiCheatRisk, CompatStatus, GraphicsApi, Severity, TrustState, Vendor
from akeno.deps import resolver as R
from akeno.hardware.support import gpu_support, verify_from_logs
from akeno.i18n import t
from akeno.security.downloads import CancelToken
from akeno.ui import icons, theme
from akeno.ui.pages.dashboard import COMPAT_TEXT
from akeno.ui.widgets import (Card, Clickable, ElidedLabel, Spinner, WrapCheck, button, clear_layout, confirm, info,
                              label, status_badge)

TRUST = {TrustState.VERIFIED: ("Verified", "verified"), TrustState.NEEDS_REVIEW: ("Needs review", "needs_review"),
         TrustState.UNVERIFIED: ("Unverified", "unverified"), TrustState.BLOCKED: ("Blocked", "blocked")}

CONFLICT_CODES = ("foreign_optiscaler", "second_consumer", "no_proxy_name", "foreign_feeder", "stock_optiscaler",
                  "renodx_dlss", "consumer_multiple", "unknown_proxy", "fg_mod")
DEP_CODES = ("runtime_missing", "missing_", "reshade_missing", "consumer_missing")
INSTALL_STEPS = (("backup", "Backup created and verified"), ("confirm", "Your confirmation"),
                 ("apply", "Files installed"), ("validate", "Installation validated"), ("record", "Installation recorded"))


@dataclass
class Row:
    state: str          # ok | warn | fail | info | pending
    title: str
    detail: str = ""
    tech: str = ""      # technical details shown when the section is expanded
    blocking: bool = False


def _worst(rows: list[Row]) -> str:
    for st in ("fail", "warn", "info", "pending"):
        if any(r.state == st for r in rows):
            return st
    return "ok" if rows else "pending"


class CheckLine(QWidget):
    """One wizard check: badge, title and a one-line detail; expanded shows the wrapped detail + tech."""

    def __init__(self, row: Row, expanded: bool):
        super().__init__()
        self.row = row
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(9)
        badge = QLabel()
        badge.setPixmap(status_badge(row.state, 18))
        badge.setFixedSize(20, 20)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = label(row.title)
        color = {"fail": "#FF8A9B", "warn": theme.AMBER}.get(row.state, theme.TEXT) if row.blocking or \
            row.state == "fail" else theme.TEXT
        title.setStyleSheet(f"font-weight: 600; color: {color};")
        if expanded:
            col.addWidget(title)
            if row.detail:
                col.addWidget(label(row.detail, "dim", wrap=True, selectable=True))
            if row.tech:
                tl = label(row.tech, "muted", wrap=True, selectable=True)
                tl.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 8.5pt;")
                col.addWidget(tl)
        else:
            head = QHBoxLayout()
            head.setSpacing(8)
            title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            head.addWidget(title)
            d = ElidedLabel(row.detail, "dim")
            d.setStyleSheet("font-size: 9pt;")
            head.addWidget(d, 1)
            col.addLayout(head)
        lay.addLayout(col, 1)
        self.setToolTip(f"{row.title}\n{row.detail}" + (f"\n\n{row.tech}" if row.tech else ""))
        self.setAccessibleName(f"{row.title}: {row.detail}")


class WizardSection(QFrame):
    """Collapsible group of checks with an overall badge and a one-line summary."""

    def __init__(self, number: int, title: str, icon_name: str):
        super().__init__()
        self.setObjectName("Card")
        self.expanded = False
        self.rows: list[Row] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(6)
        self.head = Clickable()
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.head.setStyleSheet("Clickable { background: transparent; border: none; }")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        num = QLabel(str(number))
        num.setFixedSize(26, 26)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {theme.VIOLET_DEEP}, "
                          f"stop:1 {theme.BLUE}); border-radius: 13px; font-weight: 800; color: white;")
        hl.addWidget(num)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.CYAN, 20))
        hl.addWidget(ic)
        self.title = label(title, "h3")
        hl.addWidget(self.title)
        self.badge = QLabel()
        self.badge.setFixedSize(20, 20)
        hl.addWidget(self.badge)
        self.summary = ElidedLabel("", "dim")
        hl.addWidget(self.summary, 1)
        self.chev = QLabel()
        hl.addWidget(self.chev)
        self.head.clicked.connect(self.toggle)
        self.head.setAccessibleName(title)
        self.head.setToolTip(t("Show or hide technical details"))
        outer.addWidget(self.head)
        self.body = QVBoxLayout()
        self.body.setSpacing(2)
        outer.addLayout(self.body)
        self.guides = QHBoxLayout()         # one-click guided fixes, always visible
        self.guides.setSpacing(8)
        outer.addLayout(self.guides)
        self.extra = QVBoxLayout()          # widgets below the rows (e.g. runtime buttons), only when expanded
        outer.addLayout(self.extra)
        outer.addStretch(1)
        self._extra_widgets: list[QWidget] = []

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self._render()

    def set_rows(self, rows: list[Row], summary: str = "") -> None:
        self.rows = rows
        st = _worst(rows)
        self.badge.setPixmap(status_badge(st, 18))
        done = sum(1 for r in rows if r.state == "ok")
        self.summary.set_text(summary or t("{n} of {m} passed", n=done, m=len(rows)))
        self._render()

    def set_guides(self, links: list[tuple[str, object]], on_click) -> None:
        clear_layout(self.guides)
        for text, link in links:
            b = button(text, "link", kind="link", on_click=lambda checked=False, lk=link: on_click(lk))
            self.guides.addWidget(b)
        if links:
            self.guides.addStretch(1)

    def add_extra(self, w: QWidget) -> None:
        self._extra_widgets.append(w)
        self.extra.addWidget(w)
        w.setVisible(self.expanded)

    def _render(self) -> None:
        clear_layout(self.body)
        for r in self.rows:
            self.body.addWidget(CheckLine(r, self.expanded))
        self.chev.setPixmap(icons.pixmap("plus" if not self.expanded else "x", theme.TEXT_DIM, 14))
        for w in self._extra_widgets:
            w.setVisible(self.expanded)


class StepChip(QWidget):
    def __init__(self, number: int, text: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(6)
        self.badge = QLabel()
        self.badge.setFixedSize(18, 18)
        lay.addWidget(self.badge)
        self.text = label(f"{number}. {text}")
        self.text.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.text)
        self.set_state("pending")

    def set_state(self, st: str) -> None:
        self.badge.setPixmap(status_badge(st, 16))
        border = theme.STATE_COLORS.get(st, theme.BORDER_HI) if st != "pending" else theme.BORDER_HI
        self.setStyleSheet(f"StepChip {{ background: rgba(16,29,51,0.8); border: 1px solid {border};"
                           f" border-radius: 12px; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


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
        self._can_resolve = False
        self.disable_checks: list = []
        from akeno.ui.fivem_panel import FiveMPanel
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.fivem_panel = FiveMPanel(ctl)
        self.fivem_panel.hide()
        root_layout.addWidget(self.fivem_panel)
        self.generic_panel = QWidget()
        root_layout.addWidget(self.generic_panel)
        lay = QVBoxLayout(self.generic_panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # -- header: title, context, step chips --------------------------------------------------
        top = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        tcol.addWidget(label(t("Secure Install"), "h1"))
        self.head = ElidedLabel("", "dim")
        tcol.addWidget(self.head)
        top.addLayout(tcol, 1)
        self.chips = [StepChip(i + 1, t(n)) for i, n in enumerate(("System", "Game", "Dependencies", "Installation"))]
        for c in self.chips:
            top.addWidget(c, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(top)

        body = QGridLayout()
        body.setHorizontalSpacing(14)
        body.setVerticalSpacing(14)
        self.sec_system = WizardSection(1, t("System"), "cpu")
        self.sec_game = WizardSection(2, t("Game"), "gamepad")
        self.sec_deps = WizardSection(3, t("Dependencies"), "layers")
        self.sec_install = WizardSection(4, t("Installation"), "shield")
        body.addWidget(self.sec_system, 0, 0)
        body.addWidget(self.sec_game, 0, 1)
        body.addWidget(self.sec_deps, 1, 0)
        body.addWidget(self.sec_install, 1, 1)

        # Runtime tools inside the Dependencies section (visible when expanded).
        rt_tools = QWidget()
        rtl = QVBoxLayout(rt_tools)
        rtl.setContentsMargins(0, 6, 0, 0)
        rtl.setSpacing(6)
        rtl.addWidget(label(t("DLSS 5 Neural Rendering needs NVIDIA's nvngx_dlssnr.dll. AKENO never downloads or "
                              "redistributes it: it uses the copy your NVIDIA driver installed, and accepts only files "
                              "with a valid NVIDIA signature."), "muted", wrap=True))
        self.rt_box = QVBoxLayout()
        rtl.addLayout(self.rt_box)
        rbr = QHBoxLayout()
        rbr.addWidget(button(t("Find in NVIDIA driver"), "search", on_click=self._find_runtime))
        rbr.addWidget(button(t("Import file…"), "file", on_click=self._import_runtime))
        rbr.addStretch(1)
        rtl.addLayout(rbr)
        self.sec_deps.add_extra(rt_tools)
        self.pkg_info = label("", "muted", wrap=True)
        self.pkg_info.setOpenExternalLinks(True)
        self.sec_deps.add_extra(self.pkg_info)

        # -- action panel ------------------------------------------------------------------------
        self.act_card = Card(t("Install"), "download", theme.VIOLET)
        ac = self.act_card.body
        ac.setSpacing(8)
        prow = QHBoxLayout()
        prow.addWidget(label(t("Package"), "key"))
        self.pkg_combo = QComboBox()
        self.pkg_combo.setAccessibleName(t("Package"))
        self.pkg_combo.activated.connect(self._pkg_changed)
        prow.addWidget(self.pkg_combo, 1)
        ac.addLayout(prow)
        orow = QHBoxLayout()
        orow.addWidget(label(t("Profile to apply"), "key"))
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName(t("Profile to apply"))
        orow.addWidget(self.profile_combo, 1)
        ac.addLayout(orow)
        self.disable_box = QVBoxLayout()
        ac.addLayout(self.disable_box)
        self.ack = WrapCheck(t("I understand this is an experimental community mod, not supported by NVIDIA or the "
                               "game developer, and that AKENO keeps a backup to undo it."))
        self.ack.toggled.connect(self._update_buttons)
        ac.addWidget(self.ack)
        rrow = QHBoxLayout()
        self.btn_resolve = button(t("Resolve Dependencies"), "refresh",
                                  tip=t("Search this PC, import verified NVIDIA files and download reviewed packages "
                                        "(you confirm the list first)"), on_click=self._resolve)
        self.btn_resolve.setMinimumHeight(40)
        self.btn_resolve.setStyleSheet(
            f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0B3FAF, stop:1 #0E7FD8);"
            f" border: 1px solid {theme.CYAN}; font-size: 10.5pt; }}"
            f"QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1452D0, stop:1 #12A0F0); }}"
            f"QPushButton:disabled {{ background: #0F1828; border-color: #1A2940; color: {theme.TEXT_MUTED}; }}")
        rrow.addWidget(self.btn_resolve, 1)
        self.spinner = Spinner()
        rrow.addWidget(self.spinner)
        ac.addLayout(rrow)
        self.dl_status = label("", "dim", wrap=True)
        ac.addWidget(self.dl_status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(10)
        ac.addWidget(self.progress)
        self.btn_install = button(t("Install / Update"), "download", "primary", on_click=self._install)
        self.btn_install.setMinimumHeight(48)
        self.btn_install.setIconSize(QSize(22, 22))
        self.btn_install.setStyleSheet("font-size: 12pt;")
        ac.addWidget(self.btn_install)
        brow = QHBoxLayout()
        self.btn_cancel = button(t("Cancel download"), "x", on_click=self._cancel)
        self.btn_cancel.setEnabled(False)
        self.btn_restore = button(t("Uninstall and restore originals"), "restore", on_click=self._restore)
        brow.addWidget(self.btn_cancel)
        brow.addWidget(self.btn_restore, 1)
        ac.addLayout(brow)
        self.why = label("", "dim", wrap=True)
        self.why.setTextFormat(Qt.TextFormat.RichText)
        ac.addWidget(self.why)
        self.btn_log = button(t("Show installation log"), "file", kind="link", on_click=self._toggle_log)
        ac.addWidget(self.btn_log, 0, Qt.AlignmentFlag.AlignLeft)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setPlaceholderText(t("Installation log"))
        self.log.setAccessibleName(t("Installation log"))
        self.log.setVisible(False)
        ac.addWidget(self.log)
        body.addWidget(self.act_card, 0, 2, 2, 1)
        body.setColumnStretch(0, 5)
        body.setColumnStretch(1, 5)
        body.setColumnStretch(2, 4)
        lay.addLayout(body)
        lay.addStretch(1)
        for sig in (ctl.selection_changed, ctl.games_changed, ctl.installs_changed, ctl.hardware_changed,
                    ctl.deps_changed):
            sig.connect(self.refresh)
        ctl.busy_changed.connect(self._busy)
        self.refresh()

    def on_show(self) -> None:
        self.refresh()
        if not self.s.deps.reports and not self.ctl.is_busy("deps"):
            self.ctl.discover_dependencies()

    def _busy(self, key: str, on: bool) -> None:
        if key in ("deps", "resolve"):
            (self.spinner.start if on else self.spinner.stop)()
            if on and key == "deps":
                self.dl_status.setText(t("Searching your NVIDIA driver, NGX store and games…"))
            elif not on and key == "deps":
                self.dl_status.setText("")
            self._update_buttons()

    # ------------------------------------------------------------------------------------------
    def _ctx(self):
        g = self.s.selected_game()
        a = self.s.backend_for(g)
        return g, a, (self.s.package_for(a) if a else None)

    def refresh(self) -> None:
        g, a, pkg = self._ctx()
        five_m = bool(g and g.launcher == "fivem")
        self.fivem_panel.setVisible(five_m)
        self.generic_panel.setVisible(not five_m)
        if five_m:
            self.fivem_panel.refresh(g)
            self.btn_install.setEnabled(False)
            return
        enabled = g is not None and a is not None
        for w in (self.sec_system, self.sec_game, self.sec_deps, self.sec_install, self.act_card):
            w.setEnabled(enabled)
        if not enabled:
            self.head.set_text(t("Select a game in the Game Library first."))
            self.btn_install.setEnabled(False)
            self.btn_restore.setEnabled(False)
            self.btn_resolve.setEnabled(False)
            return
        self.head.set_text(t("Game: {g}  ·  Backend: {b}  ·  Target folder: {d}", g=g.name, b=a.name,
                             d=g.target_dir or t("Unknown")))
        self.pkg_combo.clear()
        for p in self.s.catalog.packages_for(a.id):
            self.pkg_combo.addItem(f"{p.name} {p.version}" + ("" if p.installable else f"  ({t('Needs review')})"), p.id)
        if pkg:
            self.pkg_combo.setCurrentIndex(max(0, self.pkg_combo.findData(pkg.id)))
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
                cb.toggled.connect(self._refresh_checks)
                self.disable_box.addWidget(cb)
                self.disable_checks.append(cb)
        self._refresh_runtimes()
        self._refresh_pkg_info(pkg)
        self._refresh_checks()

    def _refresh_pkg_info(self, pkg) -> None:
        if not pkg:
            self.pkg_info.setText(t("No package listed for this backend."))
            return
        txt, _ = TRUST[pkg.trust]
        sha = (pkg.sha256[:16] + "…" + pkg.sha256[-8:]) if pkg.sha256 else t("Not pinned")
        self.pkg_info.setText(
            f"<b>{pkg.name} {pkg.version}</b> · {t('Trust')}: {t(txt)} · {t('License')}: {pkg.license} · "
            f"{t('Reviewed')}: {pkg.reviewed or '—'}<br>{t('Source')}: <a style='color:{theme.CYAN}' "
            f"href='{pkg.upstream}'>{pkg.upstream}</a><br>SHA-256: {sha}<br>{t(pkg.review_notes)}")

    def _refresh_runtimes(self) -> None:
        clear_layout(self.rt_box)
        rts = [r for r in self.s.db.runtimes() if r["file_name"] in runtimes.SUPPORTED_RUNTIMES]
        for r in rts:
            st = {"verified": "ok", "unverified": "warn", "needs_review": "warn", "blocked": "fail"}[r["trust"]]
            self.rt_box.addWidget(CheckLine(Row(st, f"{r['file_name']} {r.get('version') or ''}",
                                                t("Signer: {s} · {t}", s=r.get("signer") or t("none"),
                                                  t=t(TRUST[TrustState(r["trust"])][0])),
                                                r.get("original_path") or ""), True))

    def _disable_list(self) -> list[str]:
        return [cb.property("file") for cb in getattr(self, "disable_checks", []) if cb.isChecked()]

    # -- checks --------------------------------------------------------------------------------
    def _system_rows(self, g, a) -> list[Row]:
        rows = []
        gpu = self.s.selected_gpu()
        sysinfo = self.s.system
        if gpu is None:
            rows.append(Row("fail", t("GPU and driver"), t("No GPU information. Run a hardware scan first."),
                            blocking=True))
        else:
            ok = gpu.vendor == Vendor.NVIDIA and gpu.has_tensor_cores is not False and not gpu.is_software
            drv = gpu.driver_version or t("Unknown")
            rows.append(Row("ok" if ok and gpu.driver_version else ("warn" if ok else "fail"), t("GPU and driver"),
                            f"{gpu.name} · {t('driver')} {drv}",
                            f"{gpu.architecture or '?'} · {gpu.generation or '?'} · VRAM "
                            f"{(gpu.vram_bytes or 0) / 1024 ** 3:.0f} GB · {', '.join(gpu.sources)}", blocking=not ok))
        osname = (sysinfo.os_name if sysinfo else None) or platform.system()
        if sysinfo and sysinfo.os_display_version:
            osname += f" ({sysinfo.os_display_version})"
        win = (osname or "").lower().startswith("windows")
        bits64 = sysinfo.is_64bit if sysinfo and sysinfo.is_64bit is not None else True
        rows.append(Row("ok" if win and bits64 else ("fail" if not bits64 else "warn"), t("Operating system"),
                        osname + ("" if bits64 else " · 32-bit"),
                        t("Windows 10 or 11 (64-bit) is required for the mods AKENO installs.")))
        rt = self.s.runtime_lookup(runtimes.NR_RUNTIME)
        cand = None
        if rt is None:
            rep = self.s.deps.reports.get(runtimes.NR_RUNTIME)
            cand = rep.best() if rep else None
        version = (rt or {}).get("version") or (cand.version if cand else None)
        sup = gpu_support(gpu, version, bool(rt or cand), self.s.compat_db.nr_runtimes())
        state = {"ok": "ok", "warn": "warn", "fail": "fail"}.get(sup.community_state, "pending")
        tech = "\n".join([t(sup.official)] + [t(x) for x in sup.limitations] + [t(x) for x in sup.recommendations])
        if sup.runtime and sup.runtime.state in ("works", "fails"):
            ev = sup.runtime.evidence or {}
            tech += f"\n{t('Evidence')}: {ev.get('source', '')} {ev.get('url', '')}"
        rows.append(Row(state, t("Hardware capability"), t(sup.community), tech))
        return rows

    def _game_rows(self, g, a, findings, compat) -> list[Row]:
        rows = []
        exe_ok = bool(g.exe_path and os.path.isfile(g.exe_path))
        rows.append(Row("ok" if exe_ok else "fail", t("Executable"),
                        os.path.basename(g.exe_path) if exe_ok else t("The game executable was not found."),
                        g.exe_path or "", blocking=not exe_ok))
        api_bad = [f for f in findings if f.code in ("api_unknown", "api_unsupported", "arch", "vulkan_guided")]
        api_txt = t(g.api.label) + (f" ({t(g.api_source)})" if g.api_source and g.api != GraphicsApi.UNKNOWN else "")
        rows.append(Row("fail" if api_bad else ("warn" if any(f.code == "d3d11_bridge" for f in findings) else "ok"),
                        t("Rendering API"), " ".join(t(f.message) for f in api_bad) or api_txt,
                        ", ".join(g.api_candidates), blocking=bool(api_bad)))
        conflicts = [f for f in findings if f.code in CONFLICT_CODES]
        hard = [f for f in conflicts if f.severity == Severity.BLOCKER]
        mods = [m.name for m in g.existing_mods if m.kind not in ("dlss", "fsr", "xess", "akeno_marker")]
        rows.append(Row("fail" if hard else ("warn" if conflicts else "ok"), t("Existing mods"),
                        " ".join(t(f.message) for f in conflicts) or
                        (t("No conflicting graphics hooks.") + (f" ({', '.join(mods)})" if mods else "")),
                        "\n".join(f"{m.name}: {m.kind} — {m.path}" for m in g.existing_mods), blocking=bool(hard)))
        rows.append(Row({True: "fail", False: "ok", None: "warn"}[g.is_running], t("Game running status"),
                        {True: t("Close the game first."), False: t("No game process found."),
                         None: t("Could not check running processes.")}[g.is_running], blocking=g.is_running is True))
        blocked = compat is not None and compat.status == CompatStatus.BLOCKED
        rows.append(Row("fail" if blocked else ("ok" if g.anti_cheat == AntiCheatRisk.NONE_FOUND else "warn"),
                        t("Anti-cheat risks"),
                        "; ".join(g.anti_cheat_details) or t("No known anti-cheat files found (this is not a "
                                                             "guarantee). Use mods only offline and in single-player."),
                        blocking=blocked))
        writable = bool(g.target_dir and os.access(g.target_dir, os.W_OK))
        rows.append(Row("ok" if writable else "fail", t("Write permission"),
                        t("The game folder is writable without administrator rights.") if writable else
                        t("AKENO cannot write to this folder and will not ask for administrator rights."),
                        g.target_dir or "", blocking=not writable))
        if compat:
            txt, st = COMPAT_TEXT[compat.status]
            rows.append(Row(st if st != "unknown" else "warn", t("Compatibility: {s}", s=t(txt)),
                            t("confidence") + f": {t(compat.confidence)}",
                            "\n".join(f"• {t(f.message)}" for f in compat.findings)))
        other = [f for f in findings if f.severity == Severity.BLOCKER and f.code not in CONFLICT_CODES and
                 not f.code.startswith(DEP_CODES) and f.code not in ("no_exe", "api_unknown", "api_unsupported",
                                                                       "arch", "vulkan_guided")]
        if other:
            rows.append(Row("fail", t("Other blockers"), " ".join(t(f.message) for f in other), blocking=True))
        return rows

    def _dep_rows(self, plan) -> list[Row]:
        rows = []
        for d in plan.items:
            st = d.badge
            detail = t(d.detail)
            if d.state == R.BLOCKED and not d.required:
                st = "warn"
            tech = "\n".join(x for x in (
                (t("Source") + f": {d.source}") if d.source else "", (t("License") + f": {d.license}") if d.license else "",
                (t("Resolve will") + f": {t(d.action)}") if d.action else "", t(d.note) if d.note else "",
                ("\n".join(f"→ {t(gl.label)}" + (f": {gl.url}" if gl.url else "") for gl in d.guide))) if x)
            title = t(d.title) + ("" if d.required else f" ({t('optional')})")
            rows.append(Row(st, title, detail, tech, blocking=d.required and d.state in (R.BLOCKED, R.MANUAL, R.UNKNOWN)))
        pkg = plan.get("package")
        if pkg and pkg.package is not None and pkg.package.installable:
            err = self.s.deps.last_errors.get("package", "")
            if pkg.state == R.OK:
                rows.append(Row("ok", t("Package verification"), t("SHA-256 matches the reviewed release.")))
            elif "discarded" in err:
                rows.append(Row("fail", t("Package verification"), t(err)))
            else:
                rows.append(Row("pending", t("Package verification"),
                                t("The package is verified against its pinned SHA-256 when it is downloaded.")))
        return rows

    def _install_rows(self, g, a) -> list[Row]:
        rows = [Row(self.run_states.get(k, "pending"), t(title)) for k, title in INSTALL_STEPS]
        active = self.s.installer.active_install(g.id, a.id)
        if active:
            rows.append(Row("info", t("Currently installed"),
                            t("Transaction #{n} · {d}", n=active["id"], d=str(active.get("created_at") or "")[:16]),
                            active.get("target_dir") or ""))
            since = None
            try:
                from datetime import datetime
                since = datetime.fromisoformat(str(active.get("created_at"))).timestamp()
            except (TypeError, ValueError):
                since = None
            v = verify_from_logs(active.get("target_dir") or g.target_dir, since)
            rows.append(Row({"verified": "ok", "failed": "fail"}.get(v.state, "pending"), t("Neural Rendering on this PC"),
                            t(v.text), "\n".join(x for x in (v.file or "", v.when or "", v.line or "") if x)))
        return rows

    def _refresh_checks(self) -> None:
        g, a, pkg = self._ctx()
        if g is None or a is None:
            return
        ctx = self.s.context(g, allow_disable=self._disable_list())
        findings = a.check(g, ctx)
        compat = self.s.compatibility(g, a)
        plan = self.s.dependency_plan(g)
        sysrows = self._system_rows(g, a)
        gamerows = self._game_rows(g, a, findings, compat)
        deprows = self._dep_rows(plan)
        instrows = self._install_rows(g, a)
        self.sec_system.set_rows(sysrows)
        self.sec_game.set_rows(gamerows)
        resolvable = len(plan.resolvable)
        self.sec_deps.set_rows(deprows, t("{n} of {m} ready", n=sum(1 for d in plan.items if d.satisfied),
                                          m=len(plan.items)) +
                               (" · " + t("{n} can be resolved automatically", n=resolvable) if resolvable else ""))
        guides, seen = [], set()
        for d in plan.items:
            if d.state in (R.BLOCKED, R.MANUAL):
                for gl in d.guide:
                    if (gl.url, gl.action) not in seen:
                        seen.add((gl.url, gl.action))
                        guides.append((t(gl.label), gl))
        self.sec_deps.set_guides(guides, self.open_guide)
        self.sec_install.set_rows(instrows, t("Nothing changed yet") if not any(
            v == "ok" for v in self.run_states.values()) else "")
        for chip, rows in zip(self.chips, (sysrows, gamerows, deprows, instrows)):
            chip.set_state(_worst(rows) if rows else "pending")
        blockers = [r for r in sysrows + gamerows if r.blocking and r.state == "fail"]
        dep_block = [r for r in deprows if r.blocking]
        self._blocking_rows = blockers + dep_block
        self._can_resolve = bool(plan.resolvable) and not dep_block and not blockers
        # adapter blockers not represented by a row (dependency blockers are handled by the plan)
        extra = [f for f in findings if f.severity == Severity.BLOCKER and f.code.startswith(DEP_CODES)
                 and not plan.resolvable and not dep_block]
        self._blocked = bool(blockers or dep_block or extra)
        self._needs_ack = not compat or compat.status != CompatStatus.CONFIRMED or a.experimental
        self._update_buttons()

    def _update_buttons(self) -> None:
        g, a, pkg = self._ctx()
        # While a runtime search runs, Resolve/Install would start a second one: wait for it.
        busy = self.ctl.is_busy("install") or self.ctl.is_busy("resolve") or self.ctl.is_busy("deps")
        active = self.s.installer.active_install(g.id, a.id) if g and a else None
        ok = bool(g and a and pkg and pkg.installable and not getattr(self, "_blocked", True) and
                  (self.ack.isChecked() or not self._needs_ack))
        self.btn_install.setEnabled(ok and not busy)
        self.btn_install.setText(t("Update") if active else t("Install / Update"))
        self.btn_restore.setEnabled(bool(active) and not busy)
        self.btn_resolve.setEnabled(bool(g and a) and not busy)
        rows = getattr(self, "_blocking_rows", [])
        if rows:
            items = "".join(f"<li><b>{r.title}</b>: {r.detail}</li>" for r in rows[:4])
            self.why.setText(f"<span style='color:#FF8A9B'><b>{t('Installation is blocked')}</b></span>"
                             f"<ul style='margin:2px 0 0 -18px'>{items}</ul>")
        elif pkg is not None and not pkg.installable:
            self.why.setText(t("The package is not a reviewed, verified release."))
        elif self._can_resolve:
            self.why.setText(f"<span style='color:{theme.CYAN}'>" + t("Missing dependencies can be obtained "
                             "automatically: click Resolve Dependencies, or Install (it resolves them first).") + "</span>")
        elif self._needs_ack and not self.ack.isChecked():
            self.why.setText(t("Confirm the experimental-mod notice to continue."))
        else:
            self.why.setText(f"<span style='color:{theme.GREEN}'>" + t("Ready to install.") + "</span>")

    def _toggle_log(self) -> None:
        self.log.setVisible(not self.log.isVisible())
        self.btn_log.setText(t("Hide installation log") if self.log.isVisible() else t("Show installation log"))

    # -- dependency resolution -----------------------------------------------------------------
    def _resolve(self, then=None) -> None:
        """Search (read-only), show what can be obtained, ask, then import/download with progress."""
        g, a, _ = self._ctx()
        if not (g and a):
            return
        self._say(t("Searching your NVIDIA driver, NGX store and games for NVIDIA runtimes…"))

        def searched(_reports):
            plan = self.s.dependency_plan(g)
            todo = plan.resolvable
            if not todo:
                self.refresh()
                msgs = [f"• {t(d.title)}: {t(d.detail)}" for d in plan.blockers]
                if then and plan.ready:
                    then()
                    return
                info(self, t("Resolve Dependencies"),
                     t("All dependencies are ready.") if plan.ready else
                     t("Nothing more can be obtained automatically."), "\n".join(msgs) or None,
                     error=not plan.ready)
                return
            lines = []
            for d in todo:
                extra = []
                if d.size:
                    extra.append(f"{d.size / 1e6:.1f} MB")
                if d.license:
                    extra.append(t("License") + f": {d.license}")
                if d.source:
                    extra.append(d.source)
                lines.append(f"• {t(d.action)}" + (f"\n   {' · '.join(extra)}" if extra else ""))
            if plan.blockers:
                lines.append("\n" + t("Still needs your action afterwards:"))
                lines += [f"• {t(d.title)}: {t(d.detail)}" for d in plan.blockers]
            if not confirm(self, t("Resolve Dependencies"),
                           t("AKENO can do the following now. Nothing in the game folder is changed by this step."),
                           "\n".join(lines), t("Resolve")):
                self._say(t("Cancelled by you. Nothing was changed."))
                return
            self._run_resolve(g, then)

        if not self.ctl.discover_dependencies(on_done=searched):
            self.dl_status.setText(t("A search is already running. Try again when it has finished."))

    def _run_resolve(self, g, then=None) -> None:
        self.token = CancelToken()
        self.btn_cancel.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        def work(emit):
            return self.s.deps.resolve(g, self.s.backend_for(g), lambda k, n, total: emit((k, n, total)), self.token,
                                       rediscover=False)

        def prog(val):
            key, n, total = val
            if key == "search":
                self.dl_status.setText(t("Searching…"))
                return
            if total:
                self.progress.setRange(0, 100)
                self.progress.setValue(int(n * 100 / total))
                self.dl_status.setText(t("Downloading: {a} / {b} MB", a=f"{n / 1e6:.1f}", b=f"{total / 1e6:.1f}"))
            else:
                self.dl_status.setText(t("Downloading: {a} MB", a=f"{n / 1e6:.1f}"))

        def done(res):
            self.progress.setVisible(False)
            self.btn_cancel.setEnabled(False)
            self.dl_status.setText(t("Dependencies ready.") if res.plan.ready else
                                   t("Some dependencies still need your action."))
            for m in res.messages:
                self._say(t(m))
            self.ctl.deps_changed.emit()
            self.ctl.installs_changed.emit()
            if res.cancelled:
                return
            if then and res.plan.ready:
                then()
                return
            info(self, t("Resolve Dependencies"),
                 t("All dependencies are ready.") if res.plan.ready else t("Some dependencies still need your action."),
                 "\n".join(t(m) for m in res.messages), error=not res.plan.ready)

        def fail(exc):
            self.progress.setVisible(False)
            self.btn_cancel.setEnabled(False)
            self._say(t("Failed: ") + str(exc))
            info(self, t("Resolve Dependencies"), str(exc), error=True)

        self.ctl.run("resolve", work, done, fail, on_progress=prog)

    # -- runtime import (manual paths, still verified) -------------------------------------------
    def _find_runtime(self) -> None:
        self._resolve()

    def _import_runtime(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("Import NVIDIA runtime"), "",
                                              "NVIDIA NGX (nvngx_dlssnr.dll nvngx_dlss.dll)")
        if not path:
            return
        try:
            cand = runtimes.import_runtime(self.s.db, path, verifier=self.s.deps.verifier)
        except (ValueError, OSError) as exc:
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
    def _say(self, msg_: str) -> None:
        self.log.appendPlainText(msg_)

    def _set_run(self, key: str, state: str) -> None:
        self.run_states[key] = state
        self._refresh_checks()

    def _install(self) -> None:
        g, a, pkg = self._ctx()
        if g and g.launcher == "fivem":
            self.fivem_panel.tabs.setCurrentIndex(3)
            return
        if not (g and a and pkg):
            return
        if self._can_resolve or not self.s.dependency_plan(g).ready:
            # Obtain missing dependencies first (asks for confirmation), then continue automatically.
            self._resolve(then=self._install_after_resolve)
            return
        self._start_install()

    def _install_after_resolve(self) -> None:
        self.refresh()
        if getattr(self, "_blocked", True):
            info(self, t("Install / Update"), t("Installation is blocked"),
                 "\n".join(f"• {r.title}: {r.detail}" for r in getattr(self, "_blocking_rows", [])), error=True)
            return
        if self._needs_ack and not self.ack.isChecked():
            self.why.setText(t("Confirm the experimental-mod notice to continue."))
            return
        self._start_install()

    def _start_install(self) -> None:
        g, a, pkg = self._ctx()
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
        self.run_states = {}
        self.log.clear()
        self._say(t("Downloading and verifying {p} {v}…", p=pkg.name, v=pkg.version))

        def stage(emit):
            return self.s.stage(g, a, pkg, progress=lambda n, total: emit((n, total)), token=self.token)

        def progress(val):
            n, total = val
            if total:
                self.progress.setRange(0, 100)
                self.progress.setValue(int(n * 100 / total))
                self.dl_status.setText(t("Downloading: {a} / {b} MB", a=f"{n / 1e6:.1f}", b=f"{total / 1e6:.1f}"))

        def staged(staging):
            self.btn_cancel.setEnabled(False)
            self.progress.setVisible(False)
            self.dl_status.setText("")
            self._say(t("Package verified (SHA-256 matches the reviewed release)."))
            try:
                plan = self.s.plan_install(g, a, pkg, params, staging, disable)
            except ValueError as exc:
                self._fail(str(exc))
                return
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
                if res.state == "rolled_back" or any("rolled back" in m.lower() for m in res.messages):
                    self._say(t("All changes were rolled back; the original files are back in place."))
            self._done_ui()
            self.ctl.installs_changed.emit()
            self.ctl.games_changed.emit()
            info(self, t("Install / Update"), t("Installed and validated.") if res.ok else t("Installation failed."),
                 "\n".join(t(m) for m in res.messages), error=not res.ok)

        def prog(msg_):
            self._say(t(str(msg_)))
            if "backup" in str(msg_).lower():
                self.run_states["backup"] = "ok"
                self._refresh_checks()

        self.ctl.run("install", work, done, lambda e: self._fail(str(e)), on_progress=prog)

    def _fail(self, msg_: str) -> None:
        self._say(t("Failed: ") + msg_)
        self._done_ui()
        info(self, t("Install / Update"), t("Installation did not start."), msg_, error=True)

    def _done_ui(self) -> None:
        self.progress.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.spinner.stop()
        self.dl_status.setText("")
        self._refresh_checks()

    def _cancel(self) -> None:
        if self.token:
            self.token.cancel()
            self._say(t("Cancelling…"))

    def _restore(self) -> None:
        g, a, _ = self._ctx()
        active = self.s.installer.active_install(g.id, a.id) if g and a else None
        if active:
            self.win.restore_transaction(active["id"])

    def open_guide(self, link) -> None:
        if link.url:
            QDesktopServices.openUrl(QUrl(link.url))
        elif link.action == "open_library":
            self.ctl.navigate.emit("library")
        elif link.action in ("import_nr_runtime", "import_dlss_runtime"):
            self._import_runtime()

