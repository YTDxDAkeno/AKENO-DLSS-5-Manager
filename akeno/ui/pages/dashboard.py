"""Dashboard: hardware, game analysis, recommendation, profile comparison, verification and actions."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from akeno.core.models import CompatStatus, HardwareSupport, Vendor
from akeno.hardware.gpu_db import nr_hardware_support
from akeno.i18n import t
from akeno.recommend.engine import Recommendation
from akeno.benchmark.stats import consistency_label
from akeno.ui import art, icons, theme
from akeno.ui.widgets import (ActionTile, Card, CheckRow, Clickable, KeyValueGrid, Meter, Spinner, StatusPill, SubCard,
                              button, clear_layout, confirm, info, label, scaled)

SUPPORT_TEXT = {HardwareSupport.OFFICIAL: ("Officially supported", "ok"),
                HardwareSupport.COMMUNITY: ("Community-supported", "ok"),
                HardwareSupport.EXPERIMENTAL: ("Experimental", "warn"),
                HardwareSupport.UNSUPPORTED: ("Unsupported", "fail"),
                HardwareSupport.UNKNOWN: ("Unknown", "unknown")}

COMPAT_TEXT = {CompatStatus.CONFIRMED: ("Confirmed compatible", "ok"),
               CompatStatus.EXPERIMENTAL: ("Experimental", "warn"),
               CompatStatus.INSUFFICIENT: ("Insufficient information", "unknown"),
               CompatStatus.INCOMPATIBLE: ("Known incompatible", "fail"),
               CompatStatus.BLOCKED: ("Installation blocked for safety", "fail")}


def fmt_gb(b: int | None) -> str:
    return t("Unknown") if not b else f"{b / 1024 ** 3:.0f} GB"


class DashboardPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.win = window
        self.s = ctl.s
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        # --- Detected hardware ---------------------------------------------------------------
        self.hw_spinner = Spinner()
        rescan_row = QWidget()
        rl = QHBoxLayout(rescan_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.hw_spinner)
        self.btn_rescan = button(t("Rescan"), "refresh", tip=t("Detect GPUs, driver and display again"),
                                 on_click=lambda: self.ctl.scan_hardware())
        rl.addWidget(self.btn_rescan)
        self.hw_card = Card(t("Detected Hardware"), "chip", theme.BLUE, rescan_row)
        hw_body = QHBoxLayout()
        hw_body.setSpacing(18)
        self.gpu_img = QLabel()
        self.gpu_img.setFixedSize(190, 130)
        hw_body.addWidget(self.gpu_img, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        name_row = QHBoxLayout()
        self.gpu_logo = QLabel()
        name_row.addWidget(self.gpu_logo)
        self.gpu_name = label("", "h2")
        name_row.addWidget(self.gpu_name, 1)
        col.addLayout(name_row)
        self.hw_grid = KeyValueGrid(130)
        col.addWidget(self.hw_grid)
        hw_body.addLayout(col, 1)
        self.hw_card.body.addLayout(hw_body)
        grid.addWidget(self.hw_card, 0, 0, 1, 5)

        # --- Game analysis --------------------------------------------------------------------
        self.btn_change = button(t("Change Game"), tip=t("Pick another installed game"))
        self.btn_change.clicked.connect(self._change_game_menu)
        self.game_card = Card(t("Game Analysis"), "gamepad", theme.BLUE, self.btn_change)
        gb = QHBoxLayout()
        gb.setSpacing(16)
        self.cover = QLabel()
        self.cover.setFixedSize(220, 140)
        self.cover.setStyleSheet(f"border-radius: 10px; border: 1px solid {theme.BORDER};")
        gb.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        gcol = QVBoxLayout()
        self.game_name = label("", "h2")
        gcol.addWidget(self.game_name)
        gcol.addWidget(label(t("Game Path"), "key"))
        prow = QHBoxLayout()
        self.game_path = QLineEdit()
        self.game_path.setReadOnly(True)
        self.game_path.setAccessibleName(t("Game Path"))
        prow.addWidget(self.game_path, 1)
        self.btn_open = button("", "folder", tip=t("Open the game folder"), on_click=self._open_folder)
        prow.addWidget(self.btn_open)
        gcol.addLayout(prow)
        self.game_grid = KeyValueGrid(110)
        gcol.addWidget(self.game_grid)
        gb.addLayout(gcol, 1)
        self.game_card.body.addLayout(gb)
        grid.addWidget(self.game_card, 0, 5, 1, 4)

        # --- Smart recommendation ---------------------------------------------------------------
        self.rec_badge = StatusPill("", "pending")
        self.rec_card = Card(t("Smart Recommendation"), "sparkle", theme.VIOLET, self.rec_badge)
        self.hero = QFrame()
        self.hero.setObjectName("Hero")
        self.hero.setStyleSheet(
            "#Hero { border-radius: 12px; border: 1px solid #3b2d8a; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #1a1145, stop:0.6 #241a63, stop:1 #2b1f75); }")
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(18, 12, 18, 12)
        self.hero_title = label("", "h3")
        hl.addWidget(self.hero_title)
        hrow = QHBoxLayout()
        self.hero_value = QLabel("—")
        self.hero_value.setStyleSheet("font-size: 40pt; font-weight: 800; color: #c4b5fd;")
        self.hero_value.setToolTip(t("Neural model working resolution (OptiScaler DlssNr.WorkingScale): the share "
                                     "of the frame the model works at. It is a real backend setting, not a quality score."))
        hrow.addWidget(self.hero_value)
        hcol = QVBoxLayout()
        self.hero_profile = StatusPill("", "pending")
        hcol.addWidget(self.hero_profile, 0, Qt.AlignmentFlag.AlignLeft)
        self.hero_text = label("", "dim", wrap=True)
        hcol.addWidget(self.hero_text)
        hrow.addLayout(hcol, 1)
        hl.addLayout(hrow)
        self.rec_card.body.addWidget(self.hero)
        subs = QHBoxLayout()
        subs.setSpacing(10)
        self.perf_sub = SubCard()
        self.cons_sub = SubCard()
        self.vram_sub = SubCard()
        for sc in (self.perf_sub, self.cons_sub, self.vram_sub):
            subs.addWidget(sc, 1)
        self.rec_card.body.addLayout(subs)
        grid.addWidget(self.rec_card, 1, 0, 1, 6)

        # --- Profile comparison ---------------------------------------------------------------
        self.cmp_card = Card(t("Profile Comparison"), "chart", theme.BLUE)
        self.cmp_list = QVBoxLayout()
        self.cmp_list.setSpacing(8)
        self.cmp_card.body.addLayout(self.cmp_list)
        self.cmp_card.body.addStretch(1)
        grid.addWidget(self.cmp_card, 1, 6, 1, 3)

        # --- Safety / verification --------------------------------------------------------------
        self.safe_card = Card(t("Safety / Verification"), "shield", theme.GREEN)
        self.safe_grid = QGridLayout()
        self.safe_grid.setHorizontalSpacing(30)
        self.safe_card.body.addLayout(self.safe_grid)
        grid.addWidget(self.safe_card, 2, 0, 1, 9)

        # --- Actions ---------------------------------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(12)
        self.a_scan = ActionTile(t("Scan Hardware"), t("Detect GPU and system info"), "chip", "blue")
        self.a_analyze = ActionTile(t("Analyze Game"), t("Check compatibility"), "gamepad", "violet")
        self.a_backup = ActionTile(t("Create Backup"), t("Save game files (recommended)"), "database", "dark")
        self.a_install = ActionTile(t("Install / Update"), t("Apply DLSS 5 mod"), "download", "install")
        self.a_restore = ActionTile(t("Restore"), t("Revert to original files"), "restore", "dark")
        for a in (self.a_scan, self.a_analyze, self.a_backup, self.a_install, self.a_restore):
            actions.addWidget(a, 1)
        self.a_scan.clicked.connect(lambda: self.ctl.scan_hardware())
        self.a_analyze.clicked.connect(self._analyze)
        self.a_backup.clicked.connect(self._backup)
        self.a_install.clicked.connect(lambda: self.ctl.navigate.emit("install"))
        self.a_restore.clicked.connect(self._restore)
        grid.addLayout(actions, 3, 0, 1, 9)
        self.action_note = label("", "muted", wrap=True)
        grid.addWidget(self.action_note, 4, 0, 1, 9)
        for c in range(9):
            grid.setColumnStretch(c, 1)

        ctl.hardware_changed.connect(self.refresh)
        ctl.games_changed.connect(self.refresh)
        ctl.selection_changed.connect(self.refresh)
        ctl.installs_changed.connect(self.refresh)
        ctl.benchmarks_changed.connect(self.refresh)
        ctl.settings_changed.connect(self._on_setting)
        ctl.busy_changed.connect(self._busy)
        self.refresh()

    def _on_setting(self, key: str) -> None:
        if key not in ("language", "*", "theme_accent"):
            self.refresh()

    # ------------------------------------------------------------------------------------------
    def _busy(self, key: str, on: bool) -> None:
        if key == "hardware":
            (self.hw_spinner.start if on else self.hw_spinner.stop)()
            self.btn_rescan.setEnabled(not on)
            self.a_scan.setEnabled(not on)
        if key in ("analyze", "games"):
            self.a_analyze.setEnabled(not on)

    def refresh(self) -> None:
        self._refresh_hw()
        game = self.s.selected_game()
        self._refresh_game(game)
        rec = self.s.recommendation(game)
        self._refresh_rec(game, rec)
        self._refresh_cmp(game, rec)
        self._refresh_safety(game)

    def _refresh_hw(self) -> None:
        sysinfo = self.s.system
        gpu = self.s.selected_gpu()
        verdict = nr_hardware_support(gpu)
        accent = theme.GREEN if gpu and gpu.vendor == Vendor.NVIDIA else theme.TEXT_MUTED
        self.gpu_img.setPixmap(art.gpu_card(190, 130, accent))
        self.gpu_logo.setPixmap(icons.pixmap("chip", accent, 26))
        if sysinfo is None:
            self.gpu_name.setText(t("Not scanned yet"))
            self.hw_grid.set_rows([(t("Status"), t("Run a hardware scan to detect your GPU."))])
            return
        if gpu is None:
            self.gpu_name.setText(t("No GPU detected"))
            rows = [(t("Status"), "; ".join(sysinfo.detection_errors) or t("Unknown"))]
            self.hw_grid.set_rows(rows)
            return
        self.gpu_name.setText(gpu.name)
        drv = gpu.driver_version or t("Unknown")
        if gpu.driver_date:
            drv += f"  ({gpu.driver_date})"
        osname = sysinfo.os_name or t("Unknown")
        if sysinfo.os_display_version:
            osname += f" ({sysinfo.os_display_version})"
        dx = sysinfo.directx_version or t("Unknown")
        if gpu.feature_level:
            dx += f"  ({t('Feature Level')} {gpu.feature_level})"
        disp = sysinfo.primary_display
        res = f"{disp.width} × {disp.height}  ({disp.refresh_hz} Hz)" if disp and disp.width else t("Unknown")
        arch = gpu.architecture or t("Unknown")
        if gpu.generation:
            arch += f" · {gpu.generation}"
        label_text, state = SUPPORT_TEXT[verdict.level]
        pill = StatusPill(t(label_text), state)
        pill.setToolTip(t(verdict.reason))
        rows = [(t("VRAM"), fmt_gb(gpu.vram_bytes)), (t("Driver Version"), drv), (t("Operating System"), osname),
                (t("DirectX"), dx), (t("Current Resolution"), res), (t("Architecture"), arch),
                (t("DLSS 5 NR support"), pill)]
        others = [g for g in sysinfo.gpus if g is not gpu and not g.is_software]
        if others:
            rows.append((t("Other GPUs"), ", ".join(g.name for g in others)))
        self.hw_grid.set_rows(rows)

    def _refresh_game(self, game) -> None:
        self.btn_open.setEnabled(bool(game))
        if game is None:
            self.game_name.setText(t("No game selected"))
            self.game_path.setText("")
            self.cover.setPixmap(art.cover_fallback("?", 220, 140))
            self.game_grid.set_rows([(t("Status"), t("Scan your game library or add a game folder."))])
            return
        self.game_name.setText(game.name)
        self.game_path.setText(game.install_dir)
        pm = QPixmap(game.artwork_path) if game.artwork_path and os.path.isfile(game.artwork_path) else None
        self.cover.setPixmap(scaled(pm, 220, 140) if pm and not pm.isNull() else art.cover_fallback(game.name, 220, 140))
        adapter = self.s.backend_for(game)
        compat = self.s.compatibility(game, adapter) if adapter else None
        api = t(game.api.label) + (f"  ({game.api_source})" if game.api_source and game.api.value != "unknown" else "")
        if game.api.value == "unknown" and game.api_candidates:
            api = t("Unknown") + " — " + t("candidates: {c}", c=", ".join(game.api_candidates))
        ac_state = {"detected": ("fail", t("Detected — installation blocked")),
                    "none_found": ("ok", t("None found (not a guarantee)")),
                    "unknown": ("unknown", t("Unknown"))}[game.anti_cheat.value]
        ac = StatusPill(ac_state[1], ac_state[0])
        if game.anti_cheat_details:
            ac.setToolTip("\n".join(game.anti_cheat_details))
        rows = [(t("API"), api)]
        rows.append((t("Anti-Cheat"), ac))
        if compat:
            txt, st = COMPAT_TEXT[compat.status]
            cp = StatusPill(t(txt) + f" · {t('confidence')}: {t(compat.confidence)}", st)
            cp.setToolTip("\n".join(f"• {f.message}" for f in compat.findings) or t(txt))
            rows.append((t("Compatibility"), cp))
        rows.append((t("Backend"), adapter.name if adapter else t("None available")))
        mods = [m.detail or m.name for m in game.existing_mods if m.kind not in ("dlss", "fsr", "xess", "akeno_marker")]
        rows.append((t("Existing mods"), ", ".join(mods) if mods else t("None found")))
        if game.dlss_version:
            rows.append((t("DLSS (game)"), game.dlss_version))
        self.game_grid.set_rows(rows)

    def _refresh_rec(self, game, rec: Recommendation) -> None:
        self.hero_title.setText(t("Recommended DLSS 5 Neural Rendering Configuration"))
        if rec.profile_id in (None,):
            self.hero_value.setText("—")
            self.hero_profile.set(t("No recommendation"), "pending")
        elif rec.profile_id == "disabled":
            self.hero_value.setText(t("Off"))
            self.hero_profile.set(t("Leave disabled"), "warn")
        else:
            ws = rec.working_scale
            self.hero_value.setText(f"{ws * 100:.0f}%" if ws else "—")
            tag = t("Provisional") if rec.provisional else t("Recommended")
            self.hero_profile.set(f"{t(rec.profile_label)} ({tag})", "warn" if rec.provisional else "ok")
        text = t(rec.headline)
        if rec.pre_sr:
            text += " " + t("Runs before upscaling (pre-SR), at the game's render resolution.")
        if rec.reasons:
            text += "\n" + "\n".join("• " + t(r) for r in rec.reasons[:3])
        self.hero_text.setText(text)
        self.rec_badge.set(t("Basis: {b}", b=t({"measured": "measured", "estimated": "estimated",
                                                "hardware": "hardware only", "none": "—"}[rec.basis]))
                           + f" · {t('confidence')}: {t(rec.confidence)}",
                           "ok" if rec.basis == "measured" else ("warn" if rec.basis != "none" else "pending"))
        # sub-cards
        for sc in (self.perf_sub, self.cons_sub, self.vram_sub):
            clear_layout(sc.lay)
        self._sub_title(self.perf_sub, "gauge", t("Performance"))
        base = rec.baseline
        chosen = next((o for o in rec.outlooks if o.profile_id == rec.profile_id), None)
        if base is None and not (chosen and chosen.measured):
            self.perf_sub.lay.addWidget(label(t("Not measured yet — run a benchmark to see performance results."),
                                              "dim", wrap=True))
            b = button(t("Start Benchmark"), "play", on_click=lambda: self.ctl.navigate.emit("benchmark"))
            self.perf_sub.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            top = max([x for x in [base.avg_fps if base else None,
                                   chosen.measured.avg_fps if chosen and chosen.measured else None,
                                   chosen.estimated_fps if chosen else None] if x] or [1])
            if base:
                self._fps_row(self.perf_sub, t("Before (native)"), base.avg_fps, top, (theme.TEXT_MUTED, "#64748b"),
                              t("measured"))
            if chosen and chosen.measured:
                self._fps_row(self.perf_sub, t("With {p}", p=t(chosen.label)), chosen.measured.avg_fps, top,
                              (theme.BLUE, theme.VIOLET), t("measured"))
                if base:
                    d = (chosen.measured.avg_fps / base.avg_fps - 1) * 100
                    self.perf_sub.lay.addWidget(label(f"{d:+.0f}% " + t("vs. native (measured)"), "dim"))
            elif chosen and chosen.estimated_fps:
                self._fps_row(self.perf_sub, t("With {p}", p=t(chosen.label)), chosen.estimated_fps, top,
                              (theme.BLUE, theme.VIOLET), t("estimated"))
            elif rec.profile_id and rec.profile_id != "disabled":
                self.perf_sub.lay.addWidget(label(t("With the mod: not measured yet."), "dim", wrap=True))
        self._sub_title(self.cons_sub, "shield", t("Frame-time consistency"))
        stats = chosen.measured if chosen and chosen.measured else base
        if stats:
            lab = consistency_label(stats)
            v = label(t(lab.capitalize()), "h2")
            v.setStyleSheet(f"color: {theme.STATE_COLORS[{'good': 'ok', 'fair': 'warn', 'poor': 'fail'}[lab]]};")
            self.cons_sub.lay.addWidget(v)
            self.cons_sub.lay.addWidget(label(t("1% low {l} FPS · {s}% stutter frames",
                                                l=f"{stats.low_1pct_fps:.0f}", s=f"{stats.stutter_ratio * 100:.1f}"),
                                              "dim", wrap=True))
            self.cons_sub.lay.addWidget(Meter(stats.low_1pct_fps / stats.avg_fps if stats.avg_fps else 0,
                                              (theme.GREEN, "#4ade80")))
        else:
            self.cons_sub.lay.addWidget(label(t("No measurement yet."), "dim", wrap=True))
            self.cons_sub.lay.addWidget(Meter(None))
        self._sub_title(self.vram_sub, "layers", t("VRAM"))
        gpu = self.s.selected_gpu()
        self.vram_sub.lay.addWidget(label(fmt_gb(gpu.vram_bytes) if gpu else t("Unknown"), "h2"))
        self.vram_sub.lay.addWidget(label(t(rec.vram_note or ""), "dim", wrap=True))
        for sc in (self.perf_sub, self.cons_sub, self.vram_sub):
            sc.lay.addStretch(1)

    def _sub_title(self, sc: SubCard, icon_name: str, text: str) -> None:
        row = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.BLUE, 18))
        row.addWidget(ic)
        row.addWidget(label(text, "h3"), 1)
        sc.lay.addLayout(row)

    def _fps_row(self, sc: SubCard, name: str, fps: float, top: float, colors, kind: str) -> None:
        row = QHBoxLayout()
        nl = label(name, "dim")
        nl.setFixedWidth(120)
        row.addWidget(nl)
        m = Meter(fps / top if top else 0, colors, 14)
        row.addWidget(m, 1)
        v = label(f"{fps:.0f} FPS")
        v.setToolTip(kind)
        row.addWidget(v)
        tag = label(kind, "muted")
        row.addWidget(tag)
        sc.lay.addLayout(row)

    def _refresh_cmp(self, game, rec: Recommendation) -> None:
        clear_layout(self.cmp_list)
        adapter = self.s.backend_for(game)
        if game is None or adapter is None or not adapter.profiles(game):
            self.cmp_list.addWidget(label(t("Profiles appear here once a game with a configurable backend is selected."),
                                          "dim", wrap=True))
            return
        base = rec.baseline
        out = {o.profile_id: o for o in rec.outlooks}
        colors = {"performance": (theme.GREEN, "#4ade80"), "balanced": (theme.BLUE, theme.VIOLET),
                  "quality": (theme.CYAN, theme.BLUE), "custom": (theme.MAGENTA, "#f0abfc")}
        active = self.s.db.active_profile(game.id, adapter.id) if game else None
        profiles = [p for p in adapter.profiles(game) if p.id != "disabled"]
        custom = next((p for p in self.s.db.profiles(game.id, adapter.id) if p["name"] == "custom"), None)
        for p in profiles + [None]:
            pid = p.id if p else "custom"
            title = t(p.label) if p else t("Custom")
            params = p.params if p else (custom["params"] if custom else {})
            ws = params.get("DlssNr.WorkingScale")
            if ws:
                title += f"  ({ws * 100:.0f}%)"
            sub = t(p.summary) if p else (t("Your saved settings") if custom else t("Not configured yet"))
            row = Clickable()
            row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            row.setAccessibleName(title)
            sel = rec.profile_id == pid
            border = theme.VIOLET if sel else theme.BORDER
            row.setStyleSheet(f"Clickable {{ background: rgba(255,255,255,{0.06 if sel else 0.02});"
                              f" border: 1px solid {border}; border-radius: 10px; }}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            ic = QLabel()
            ic.setPixmap(icons.pixmap({"performance": "gauge", "balanced": "sliders", "quality": "eye"}.get(pid, "gear"),
                                      colors[pid][0], 22))
            rl.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(2)
            tl = label(title + (f"  · {t('active')}" if active and active["name"] == pid else ""))
            tl.setStyleSheet("font-weight: 600;")
            col.addWidget(tl)
            col.addWidget(label(sub, "muted", wrap=True))
            o = out.get(pid)
            val, frac, kind = None, None, ""
            if o and o.measured:
                val, kind = o.measured.avg_fps, t("measured")
            elif o and o.estimated_fps:
                val, kind = o.estimated_fps, t("estimated")
            if val and base:
                frac = val / max(base.avg_fps, val)
            col.addWidget(Meter(frac, colors[pid], 6))
            rl.addLayout(col, 1)
            right = label(f"{val:.0f} FPS\n{kind}" if val else t("Not measured"), "dim")
            right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if val and base:
                d = (val / base.avg_fps - 1) * 100
                right.setText(f"<b style='color:{theme.GREEN if d >= 0 else theme.AMBER}'>{d:+.0f}%</b><br>"
                              f"<span style='color:{theme.TEXT_MUTED}'>{val:.0f} FPS · {kind}</span>")
            rl.addWidget(right)
            tip = t(p.tradeoff) if p else t("Your own combination of supported settings.")
            if p and p.reference:
                tip += "\n" + t("Reference (not your system): ") + p.reference
            row.setToolTip(tip)
            row.clicked.connect(lambda pid=pid: self.win.open_profile(pid))
            self.cmp_list.addWidget(row)

    def _refresh_safety(self, game) -> None:
        clear_layout(self.safe_grid)
        r = self.s.readiness(game)
        for i, it in enumerate(r.items):
            self.safe_grid.addWidget(CheckRow(it.state, t(it.title), t(it.detail)), i % 3, i // 3)
        for c in range(3):
            self.safe_grid.setColumnStretch(c, 1)
        active = self.s.installer.active_install(game.id) if game else None
        self.a_install.setEnabled(r.can_install)
        self.a_install.setToolTip("\n".join(t(x) for x in r.reasons) if r.reasons else t("Open the guided installer"))
        self.a_restore.setEnabled(bool(active))
        self.a_restore.setToolTip(t("Restore the original files of {g}", g=game.name) if active else
                                  t("Nothing installed by AKENO for this game."))
        self.a_backup.setEnabled(bool(game and game.exe_path))
        self.a_analyze.setEnabled(not self.ctl.is_busy("analyze"))
        notes = []
        if not r.can_install and r.reasons:
            notes.append(t("Install unavailable: ") + t(r.reasons[0]))
        if not active and game:
            notes.append(t("Restore unavailable: nothing installed by AKENO for this game."))
        self.action_note.setText("   ·   ".join(notes))

    # -- actions -------------------------------------------------------------------------------
    def _change_game_menu(self) -> None:
        menu = QMenu(self)
        games = self.s.library.games()
        for g in games[:40]:
            a = menu.addAction(g.name)
            a.triggered.connect(lambda checked=False, gid=g.id: self.ctl.select_game(gid))
        if games:
            menu.addSeparator()
        menu.addAction(t("Open Game Library…")).triggered.connect(lambda: self.ctl.navigate.emit("library"))
        menu.exec(self.btn_change.mapToGlobal(self.btn_change.rect().bottomLeft()))

    def _open_folder(self) -> None:
        g = self.s.selected_game()
        if g and os.path.isdir(g.install_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(g.install_dir))

    def _analyze(self) -> None:
        g = self.s.selected_game()
        if g is None:
            self.ctl.scan_games()
        else:
            self.ctl.analyze_game(g)

    def _backup(self) -> None:
        g = self.s.selected_game()
        if not g:
            return

        def done(res):
            self.ctl.installs_changed.emit()
            info(self, t("Create Backup"), t(res.messages[0]) if res.messages else "", error=not res.ok)
        self.ctl.run("backup", lambda: self.s.create_snapshot(g), done)

    def _restore(self) -> None:
        g = self.s.selected_game()
        active = self.s.installer.active_install(g.id) if g else None
        if not active:
            return
        self.win.restore_transaction(active["id"])
