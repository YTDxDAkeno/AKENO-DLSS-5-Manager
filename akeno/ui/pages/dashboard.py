"""Dashboard: hardware, game analysis, recommendation, profile comparison, verification and actions."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget

from akeno.core.models import CompatStatus, HardwareSupport, Vendor
from akeno.hardware.gpu_db import nr_hardware_support
from akeno.i18n import t
from akeno.recommend.engine import Recommendation
from akeno.benchmark.stats import consistency_label
from akeno.ui import art, icons, theme
from akeno.ui.widgets import (ActionTile, ArtPanel, Card, CheckRow, ElidedLabel, GradientText, KeyValueGrid, Meter,
                              ProfileRow, Spinner, StatusPill, SubCard, button, clear_layout, info, label, scaled)

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


def _shield_badge(size: int) -> QPixmap:
    """Large green shield badge for the safety panel header (decorative; the checks carry the status)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
    ratio = icons.device_ratio()
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QRadialGradient(size / 2, size / 2, size / 2)
    g.setColorAt(0, QColor(32, 217, 145, 90))
    g.setColorAt(1, QColor(32, 217, 145, 20))
    p.setBrush(g)
    p.setPen(QPen(QColor(32, 217, 145, 160), 1.2))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 12, 12)
    p.drawPixmap(QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6), icons.pixmap("shield", theme.GREEN, 28, 2.0),
                 QRectF())
    p.end()
    return pm


class DashboardPage(QWidget):
    """Composition follows the AKENO reference design at 1920x1080: hardware + game analysis, recommendation +
    profile comparison, safety panel, and the action buttons pinned below the scroll area (``footer``)."""

    GPU_ART = ((230, 160), (190, 132), (160, 112))      # per density level
    COVER = ((210, 136), (184, 120), (160, 104))
    HERO_PT = (44, 38, 32)

    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.win = window
        self.s = ctl.s
        self.density = 0
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

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
        hw_body.setSpacing(16)
        self.gpu_img = QLabel()
        self.gpu_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gpu_img.setToolTip(t("Illustration. Your GPU's real name and specifications are shown on the right."))
        hw_body.addWidget(self.gpu_img, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(6)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.gpu_logo = QLabel()
        name_row.addWidget(self.gpu_logo)
        self.gpu_name = label("", "h2")
        self.gpu_name.setStyleSheet("font-size: 14pt; font-weight: 700;")
        name_row.addWidget(self.gpu_name, 1)
        col.addLayout(name_row)
        self.hw_grid = KeyValueGrid(138)
        self.hw_grid.grid.setVerticalSpacing(3)
        col.addWidget(self.hw_grid)
        hw_body.addLayout(col, 1)
        self.hw_card.body.addLayout(hw_body)
        grid.addWidget(self.hw_card, 0, 0, 1, 10)

        # --- Game analysis --------------------------------------------------------------------
        self.btn_change = button(t("Change Game"), tip=t("Pick another installed game"))
        self.btn_change.clicked.connect(self._change_game_menu)
        self.game_card = Card(t("Game Analysis"), "gamepad", theme.BLUE, self.btn_change)
        gb = QHBoxLayout()
        gb.setSpacing(16)
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gb.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        gcol = QVBoxLayout()
        gcol.setSpacing(6)
        self.game_name = label("", "h2")
        self.game_name.setStyleSheet("font-size: 14pt; font-weight: 700;")
        gcol.addWidget(self.game_name)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        pk = label(t("Game Path"), "key")
        pk.setFixedWidth(104)
        prow.addWidget(pk)
        self.game_path = QLineEdit()
        self.game_path.setReadOnly(True)
        self.game_path.setAccessibleName(t("Game Path"))
        prow.addWidget(self.game_path, 1)
        self.btn_open = button("", "folder", tip=t("Open the game folder"), on_click=self._open_folder)
        prow.addWidget(self.btn_open)
        gcol.addLayout(prow)
        self.game_grid = KeyValueGrid(104)
        self.game_grid.grid.setVerticalSpacing(3)
        gcol.addWidget(self.game_grid)
        gb.addLayout(gcol, 1)
        self.game_card.body.addLayout(gb)
        grid.addWidget(self.game_card, 0, 10, 1, 10)

        # --- Smart recommendation ---------------------------------------------------------------
        self.rec_badge = StatusPill("", "pending")
        self.rec_card = Card(t("Smart Recommendation"), "sparkle", theme.VIOLET, self.rec_badge, fill=True)
        from akeno.ui import assets
        self.hero = ArtPanel(assets.RECOMMENDATION, fade_to=0.78)
        self.hero.setMinimumHeight(118)
        self.hero.setObjectName("Hero")
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(20, 12, 20, 12)
        hl.setSpacing(2)
        self.hero_title = label("", "h3")
        self.hero_title.setStyleSheet("font-size: 12.5pt; font-weight: 700;")
        hl.addWidget(self.hero_title)
        hrow = QHBoxLayout()
        hrow.setSpacing(16)
        self.hero_value = GradientText("—", self.HERO_PT[0])
        self.hero_value.setToolTip(t("Neural model working resolution (OptiScaler DlssNr.WorkingScale): the share "
                                     "of the frame the model works at. It is a real backend setting, not a quality score."))
        hrow.addWidget(self.hero_value, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        hcol = QVBoxLayout()
        hcol.setSpacing(4)
        hcol.addStretch(1)
        self.hero_profile = StatusPill("", "pending")
        hcol.addWidget(self.hero_profile, 0, Qt.AlignmentFlag.AlignLeft)
        self.hero_text = label("", "dim", wrap=True)
        self.hero_text.setStyleSheet("color: #C9D3EA;")
        self.hero_text.setMaximumWidth(560)
        hcol.addWidget(self.hero_text)
        hcol.addStretch(1)
        hrow.addLayout(hcol, 1)
        hrow.addStretch(0)
        hl.addLayout(hrow, 1)
        self.rec_card.body.addWidget(self.hero, 1)
        subs = QHBoxLayout()
        subs.setSpacing(10)
        self.perf_sub = SubCard()
        self.cons_sub = SubCard()
        self.vram_sub = SubCard()
        for sc in (self.perf_sub, self.cons_sub, self.vram_sub):
            subs.addWidget(sc, 1)
        self.rec_card.body.addLayout(subs)
        grid.addWidget(self.rec_card, 1, 0, 1, 13)

        # --- Profile comparison ---------------------------------------------------------------
        self.cmp_card = Card(t("Profile Comparison"), "chart", theme.BLUE, fill=True)
        self.cmp_list = QVBoxLayout()
        self.cmp_list.setSpacing(6)
        self.cmp_card.body.addLayout(self.cmp_list, 1)
        self.cmp_note = label(t("Values appear only after you measure them with a benchmark; estimates are labelled."),
                              "muted", wrap=True)
        self.cmp_card.body.addWidget(self.cmp_note)
        grid.addWidget(self.cmp_card, 1, 13, 1, 7)

        # --- Safety / verification --------------------------------------------------------------
        self.safe_card = Card()
        self.safe_card.setAccessibleName(t("Safety / Verification"))
        self.safe_card.outer.setContentsMargins(18, 12, 18, 12)
        srow = QHBoxLayout()
        srow.setSpacing(24)
        shead = QHBoxLayout()
        shead.setSpacing(12)
        self.safe_icon = QLabel()
        self.safe_icon.setPixmap(_shield_badge(44))
        shead.addWidget(self.safe_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        stcol = QVBoxLayout()
        stcol.setSpacing(2)
        stcol.addStretch(1)
        stitle = label(t("Safety / Verification"), "h2")
        stcol.addWidget(stitle)
        self.safe_sub = label("", "muted", wrap=True)
        stcol.addWidget(self.safe_sub)
        stcol.addStretch(1)
        shead.addLayout(stcol)
        swrap = QWidget()
        swrap.setFixedWidth(260)
        swrap.setLayout(shead)
        srow.addWidget(swrap)
        self.safe_grid = QGridLayout()
        self.safe_grid.setHorizontalSpacing(26)
        self.safe_grid.setVerticalSpacing(6)
        srow.addLayout(self.safe_grid, 1)
        self.safe_card.body.addLayout(srow)
        grid.addWidget(self.safe_card, 2, 0, 1, 20)

        # --- Actions (pinned footer, always reachable) -------------------------------------------
        self.footer = QWidget()
        fl = QVBoxLayout(self.footer)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(4)
        self.action_note = ElidedLabel("", "muted")
        fl.addWidget(self.action_note)
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
        fl.addLayout(actions)
        for c in range(20):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 0)

        ctl.hardware_changed.connect(self.refresh)
        ctl.games_changed.connect(self.refresh)
        ctl.selection_changed.connect(self.refresh)
        ctl.installs_changed.connect(self.refresh)
        ctl.benchmarks_changed.connect(self.refresh)
        ctl.deps_changed.connect(self.refresh)
        ctl.settings_changed.connect(self._on_setting)
        ctl.busy_changed.connect(self._busy)
        self.refresh()

    def set_density(self, level: int) -> None:
        """Called by the main window: smaller artwork and headline on short windows."""
        if level == self.density:
            return
        self.density = level
        self.hero_value.set_point_size(self.HERO_PT[level])
        self._refresh_art()

    def _refresh_art(self) -> None:
        gpu = self.s.selected_gpu()
        accent = theme.GREEN if gpu and gpu.vendor == Vendor.NVIDIA else theme.TEXT_MUTED
        w, h = self.GPU_ART[self.density]
        self.gpu_img.setFixedSize(w, h)
        self.gpu_img.setPixmap(art.gpu_illustration(w, h, max(2.0, self.devicePixelRatioF()), accent))
        self.gpu_logo.setPixmap(icons.pixmap("chip", accent, 24))
        cw, ch = self.COVER[self.density]
        self.cover.setFixedSize(cw, ch)
        game = self.s.selected_game()
        if game is None:
            self.cover.setPixmap(art.cover_fallback("?", cw, ch))
            return
        pm = QPixmap(game.artwork_path) if game.artwork_path and os.path.isfile(game.artwork_path) else None
        src = scaled(pm, cw, ch) if pm and not pm.isNull() else art.cover_fallback(game.name, cw, ch)
        from akeno.ui import assets
        self.cover.setPixmap(assets.rounded(src, 10))

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
        self._refresh_art()
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
            self.game_grid.set_rows([(t("Status"), t("Scan your game library or add a game folder."))])
            return
        self.game_name.setText(game.name)
        self.game_path.setText(game.install_dir)
        self.game_path.setToolTip(game.install_dir)
        self.game_path.setCursorPosition(0)
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
        # Keep the hero compact: first reason visible, everything in the tooltip.
        visible = text + ("\n• " + t(rec.reasons[0]) if rec.reasons else "")
        self.hero_text.setText(visible)
        self.hero_text.setToolTip("\n".join([text] + ["• " + t(r) for r in rec.reasons]))
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
            self.cmp_note.setVisible(False)
            return
        base = rec.baseline
        out = {o.profile_id: o for o in rec.outlooks}
        colors = {"performance": theme.GRAD_GREEN, "balanced": theme.GRAD_NEON,
                  "quality": theme.GRAD_BLUE, "custom": theme.GRAD_PINK}
        icon_for = {"performance": "gauge", "balanced": "sliders", "quality": "eye", "custom": "sparkle"}
        active = self.s.db.active_profile(game.id, adapter.id) if game else None
        profiles = [p for p in adapter.profiles(game) if p.id != "disabled"]
        custom = next((p for p in self.s.db.profiles(game.id, adapter.id) if p["name"] == "custom"), None)
        any_value = False
        for p in profiles + [None]:
            pid = p.id if p else "custom"
            title = t(p.label) if p else t("Custom")
            params = p.params if p else (custom["params"] if custom else {})
            ws = params.get("DlssNr.WorkingScale")
            if ws:
                title += f"  ({ws * 100:.0f}%)"
            if active and active["name"] == pid:
                title += f"  · {t('active')}"
            sub = t(p.summary) if p else (t("Your saved settings") if custom else t("Not configured yet"))
            if rec.profile_id == pid:
                sub = t("Recommended") + " · " + sub
            row = ProfileRow(title, sub, icon_for.get(pid, "gear"), colors[pid], selected=rec.profile_id == pid)
            o = out.get(pid)
            val, frac, kind = None, None, ""
            if o and o.measured:
                val, kind = o.measured.avg_fps, t("measured")
            elif o and o.estimated_fps:
                val, kind = o.estimated_fps, t("estimated")
            if val and base:
                frac = val / max(base.avg_fps, val)
            if val and base:
                any_value = True
                d = (val / base.avg_fps - 1) * 100
                row.set_value(f"<b style='font-size:11pt; color:{theme.GREEN if d >= 0 else theme.AMBER}'>{d:+.0f}%</b>"
                              f"<br><span style='color:{theme.TEXT_MUTED}'>{val:.0f} FPS · {kind}</span>", frac)
            elif val:
                any_value = True
                row.set_value(f"{val:.0f} FPS<br><span style='color:{theme.TEXT_MUTED}'>{kind}</span>", None)
            else:
                row.set_value(f"<span style='color:{theme.TEXT_MUTED}'>{t('Not measured')}</span>", None)
            tip = t(p.tradeoff) if p else t("Your own combination of supported settings.")
            if p and p.reference:
                tip += "\n" + t("Reference (not your system): ") + p.reference
            row.setToolTip(tip)
            row.clicked.connect(lambda pid=pid: self.win.open_profile(pid))
            self.cmp_list.addWidget(row)
        self.cmp_note.setVisible(not any_value)

    def _refresh_safety(self, game) -> None:
        clear_layout(self.safe_grid)
        r = self.s.readiness(game)
        rows = 2
        for i, it in enumerate(r.items):
            self.safe_grid.addWidget(CheckRow(it.state, t(it.title), t(it.detail), compact=True), i % rows, i // rows)
        cols = max(1, (len(r.items) + rows - 1) // rows)
        for c in range(cols):
            self.safe_grid.setColumnStretch(c, 1)
        done = sum(1 for it in r.items if it.state == "ok")
        self.safe_sub.setText(t("{n} of {m} checks passed", n=done, m=len(r.items)) if r.items else "")
        active = self.s.installer.active_install(game.id) if game else None
        self.a_install.setEnabled(r.can_install or r.can_resolve)
        if r.can_resolve:
            self.a_install.s.setText(t("Resolve dependencies, then install"))
            self.a_install.setToolTip(t("Everything still missing can be obtained automatically. The guided installer "
                                        "resolves it first, with your confirmation."))
        else:
            self.a_install.s.setText(t("Apply DLSS 5 mod"))
            self.a_install.setToolTip("\n".join(t(x) for x in r.reasons) if r.reasons else
                                      t("Open the guided installer"))
        self.a_restore.setEnabled(bool(active))
        self.a_restore.setToolTip(t("Restore the original files of {g}", g=game.name) if active else
                                  t("Nothing installed by AKENO for this game."))
        self.a_backup.setEnabled(bool(game and game.exe_path))
        self.a_analyze.setEnabled(not self.ctl.is_busy("analyze"))
        notes = []
        if not r.can_install and not r.can_resolve and r.reasons:
            notes.append(t("Install unavailable: ") + t(r.reasons[0]))
        if not active and game:
            notes.append(t("Restore unavailable: nothing installed by AKENO for this game."))
        self.action_note.set_text("   ·   ".join(notes))
        self.action_note.setVisible(bool(notes))

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
