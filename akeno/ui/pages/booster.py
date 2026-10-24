"""FPS Booster: per-game profiles with a preview of every change, apply and one-click restore, background
programs, intelligent game mode and the live system load."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.performance import tweaks
from akeno.performance.profiles import PROFILES
from akeno.ui import theme, workers
from akeno.ui.widgets import Card, FlowLayout, KeyValueGrid, ResponsiveRow, StatusPill, button, confirm, info, label

MODES = (("boost", "Standard Boost"), ("dlss5_boost", "DLSS 5 + Boost"))
SCOPE = {"persistent": "Until you restore it", "session": "While the game runs"}
CUSTOM_CHOICES = {
    "gpu_preference": ("Graphics processor", [("", "Unchanged"), ("high", "High performance"),
                                              ("power_saving", "Power saving")]),
    "game_mode": ("Windows Game Mode", [("", "Unchanged"), ("on", "On"), ("off", "Off")]),
    "capture": ("Background recording", [("", "Unchanged"), ("off", "Off"), ("on", "On")]),
    "fullscreen_optimizations": ("Fullscreen optimisations", [("", "Unchanged"), ("off", "Off"), ("on", "On")]),
    "power_plan": ("Power plan while playing", [("", "Unchanged"), ("high", "High performance"),
                                                ("balanced", "Balanced"), ("saver", "Power saver")]),
    "priority": ("Game priority", [("", "Unchanged"), ("above_normal", "Above normal"), ("high", "High")]),
    "background": ("Background programs", [("", "Unchanged"), ("lower", "Lower priority"), ("pause", "Pause")]),
}


class BoosterPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.profile = "balanced"
        self.mode = "boost"
        self.preview = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(label(t("FPS Booster"), "h1"))
        root.addWidget(label(t("Real Windows settings for one game, each shown before it is applied, saved with its "
                               "previous value and restorable with one click. AKENO measures the effect with "
                               "Benchmark and never claims a gain it did not measure."), "dim", shrink=True))
        self.note = label("", shrink=True)
        self.note.setVisible(False)
        root.addWidget(self.note)

        top = ResponsiveRow(spacing=14)
        game_card = Card(t("Game"), "gamepad")
        self.game_combo = QComboBox()
        self.game_combo.setAccessibleName(t("Game"))
        self.game_combo.currentIndexChanged.connect(self._game_changed)
        game_card.body.addWidget(self.game_combo)
        self.game_state = StatusPill("", "pending")
        game_card.body.addWidget(self.game_state)
        self.applied = label("", "muted", shrink=True)
        game_card.body.addWidget(self.applied)
        modes = FlowLayout(spacing=8)
        self.mode_group = QButtonGroup(self)
        for key, text in MODES:
            b = QPushButton(t(text))
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.setChecked(key == self.mode)
            b.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self.mode_group.addButton(b)
            modes.addWidget(b)
        holder = QWidget()
        holder.setLayout(modes)
        game_card.body.addWidget(holder)
        self.dlss_state = label("", "dim", shrink=True)
        game_card.body.addWidget(self.dlss_state)
        self.btn_dlss = button(t("Install DLSS 5 in My Games"), "sparkle", on_click=self._open_dlss)
        game_card.body.addWidget(self.btn_dlss, 0, Qt.AlignmentFlag.AlignLeft)
        top.add(game_card, 1)

        prof_card = Card(t("Profile"), "sliders")
        chips = FlowLayout(spacing=8)
        self.profile_group = QButtonGroup(self)
        self.profile_buttons = {}
        for p in PROFILES:
            b = QPushButton(t(p.name))
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.setChecked(p.id == self.profile)
            b.setToolTip(t(p.summary))
            b.clicked.connect(lambda _=False, k=p.id: self._set_profile(k))
            self.profile_group.addButton(b)
            self.profile_buttons[p.id] = b
            chips.addWidget(b)
        holder = QWidget()
        holder.setLayout(chips)
        prof_card.body.addWidget(holder)
        self.profile_text = label("", "dim", shrink=True)
        prof_card.body.addWidget(self.profile_text)
        self.custom_box = QWidget()
        grid = QVBoxLayout(self.custom_box)
        grid.setContentsMargins(0, 0, 0, 0)
        self.custom = {}
        for key, (title, choices) in CUSTOM_CHOICES.items():
            line = QHBoxLayout()
            line.addWidget(label(t(title), "key"), 1)
            combo = QComboBox()
            combo.setAccessibleName(t(title))
            for value, text in choices:
                combo.addItem(t(text), value)
            combo.currentIndexChanged.connect(self._refresh_preview)
            self.custom[key] = combo
            line.addWidget(combo, 1)
            grid.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(label(t("Frame limit (FPS, 0 = unchanged)"), "key"), 1)
        self.custom_fps = QSpinBox()
        self.custom_fps.setRange(0, 500)
        self.custom_fps.setAccessibleName(t("Frame limit (FPS, 0 = unchanged)"))
        self.custom_fps.valueChanged.connect(self._refresh_preview)
        line.addWidget(self.custom_fps, 1)
        grid.addLayout(line)
        prof_card.body.addWidget(self.custom_box)
        top.add(prof_card, 2)
        root.addWidget(top)

        self.preview_card = Card(t("Preview"), "eye", theme.VIOLET)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([t("Setting"), t("Now"), t("After"), t("Applies")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(170)
        self.preview_card.body.addWidget(self.table)
        self.unavailable = label("", "muted", shrink=True)
        self.preview_card.body.addWidget(self.unavailable)
        acts = FlowLayout(spacing=8)
        self.btn_apply = button(t("Apply"), "check", "primary", on_click=self._apply)
        self.btn_restore = button(t("Restore everything"), "restore", on_click=self._restore)
        self.btn_bench = button(t("Measure with Benchmark"), "chart", on_click=self._benchmark)
        for b in (self.btn_apply, self.btn_restore, self.btn_bench):
            acts.addWidget(b)
        holder = QWidget()
        holder.setLayout(acts)
        self.preview_card.body.addWidget(holder)
        root.addWidget(self.preview_card)

        lower = ResponsiveRow(spacing=14)
        bg = Card(t("Background programs"), "layers")
        bg.body.addWidget(label(t("Programs that run next to your games. Tick the ones the Maximum FPS, Low Latency "
                                  "and Streaming profiles may slow down while you play. Windows, security software, "
                                  "anti-cheat and drivers are never touched."), "dim", shrink=True))
        self.bg_table = QTableWidget(0, 3)
        self.bg_table.setHorizontalHeaderLabels([t("Program"), t("CPU"), t("Memory")])
        self.bg_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bg_table.verticalHeader().setVisible(False)
        self.bg_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.bg_table.setMinimumHeight(150)
        bg.body.addWidget(self.bg_table)
        line = FlowLayout(spacing=8)
        self.btn_analyse = button(t("Analyse"), "search", on_click=self._analyse)
        self.bg_action = QComboBox()
        self.bg_action.addItem(t("Lower their priority"), "lower")
        self.bg_action.addItem(t("Pause them (resumed after the game)"), "pause")
        self.bg_action.setAccessibleName(t("What happens to them while playing"))
        self.btn_bg_save = button(t("Save selection"), "check", on_click=self._save_background)
        for w in (self.btn_analyse, self.bg_action, self.btn_bg_save):
            line.addWidget(w)
        holder = QWidget()
        holder.setLayout(line)
        bg.body.addWidget(holder)
        lower.add(bg, 3)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(14)
        auto = Card(t("Intelligent game mode"), "gauge")
        self.chk_auto = QCheckBox(t("Notice when a game starts: apply its session settings, restore them when it ends"))
        self.chk_auto.setChecked(bool(self.s.settings.get("intelligent_game_mode")))
        self.chk_auto.toggled.connect(lambda v: self.s.settings.set("intelligent_game_mode", bool(v)))
        auto.body.addWidget(self.chk_auto)
        self.chk_wall = QCheckBox(t("Pause animated wallpapers while a game runs"))
        self.chk_wall.setChecked(bool(self.s.settings.get("pause_wallpaper_in_games")))
        self.chk_wall.toggled.connect(lambda v: self.s.settings.set("pause_wallpaper_in_games", bool(v)))
        auto.body.addWidget(self.chk_wall)
        self.running = label("", "muted", shrink=True)
        auto.body.addWidget(self.running)
        side_lay.addWidget(auto)
        load = Card(t("System load"), "cpu")
        self.load = KeyValueGrid(150)
        load.body.addWidget(self.load)
        load.body.addWidget(label(t("Frame rates come from Benchmark captures (PresentMon), never from estimates."),
                                  "muted", shrink=True))
        side_lay.addWidget(load)
        lower.add(side, 2)
        root.addWidget(lower)
        root.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._load_tick)
        ctl.games_changed.connect(self.refresh)
        ctl.selection_changed.connect(self._follow_selection)
        self._set_profile(self.profile, refresh=False)
        self.refresh()

    def _follow_selection(self) -> None:
        """A game chosen elsewhere (My Games, Game Hub) is the one shown here."""
        gid = self.s.settings.get("selected_game_id")
        i = self.game_combo.findData(gid)
        if i >= 0 and i != self.game_combo.currentIndex():
            self.game_combo.setCurrentIndex(i)

    # -- data ------------------------------------------------------------------------------------------------------
    def game(self):
        gid = self.game_combo.currentData()
        return self.s.library.get(gid) if gid else None

    def refresh(self, *_) -> None:
        current = self.game_combo.currentData() or self.s.settings.get("selected_game_id")
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        for g in sorted(self.s.library.games(), key=lambda g: g.name.lower()):
            if g.launcher != "fivem":
                self.game_combo.addItem(g.name, g.id)
        i = self.game_combo.findData(current)
        self.game_combo.setCurrentIndex(max(0, i))
        self.game_combo.blockSignals(False)
        self._game_changed()

    def _game_changed(self, *_) -> None:
        g = self.game()
        summary = self.s.boost.summary(g.id) if g else {}
        running = (getattr(self.win, "sessions", None) and self.win.sessions.running()) or {}
        if g is None:
            self.game_state.set(t("No game in the library yet"), "pending")
        elif g.id in running:
            self.game_state.set(t("Running: session settings active") if summary.get("session_active")
                                else t("Running"), "running")
        else:
            self.game_state.set(t("Not running"), "pending")
        if summary.get("profile") and summary["profile"] in self.profile_buttons:
            self._set_profile(summary["profile"], refresh=False)
            entry = self.s.boost.configured(g.id) or {}
            if summary["profile"] == "custom":
                self._custom_from(entry.get("options", {}))
        self.applied.setText(t("Applied: {p} ({n} setting(s) saved for restore)",
                               p=t(next((p.name for p in PROFILES if p.id == summary.get("profile")), "")),
                               n=len(summary.get("persistent", []))) if summary.get("profile")
                             else t("Nothing applied for this game."))
        self.btn_restore.setEnabled(bool(summary.get("profile")))
        self._dlss_state()
        self._refresh_preview()

    def _dlss_state(self) -> None:
        g = self.game()
        installed = False
        if g is not None:
            installed = any(tx["state"] == "installed" and tx["kind"] in ("install", "swap") and tx["game_id"] == g.id
                            for tx in self.s.db.transactions(g.id))
        wants = self.mode == "dlss5_boost"
        self.dlss_state.setVisible(wants)
        self.btn_dlss.setVisible(wants and not installed)
        self.dlss_state.setText(t("DLSS 5 is installed for this game: Boost adds to it.") if installed else
                                t("DLSS 5 is not installed for this game yet. Install it in My Games, then apply the "
                                  "Boost here."))

    def _custom_from(self, options: dict) -> None:
        for key, combo in self.custom.items():
            value = options.get(key)
            if key == "game_mode" and isinstance(value, bool):
                value = "on" if value else "off"
            if key == "background" and isinstance(value, dict):
                value = value.get("action")
            combo.setCurrentIndex(max(0, combo.findData(value or "")))
        limit = options.get("frame_limit")
        self.custom_fps.setValue(int(limit) if isinstance(limit, int) else 0)

    def _custom_options(self) -> dict:
        out = {}
        for key, combo in self.custom.items():
            value = combo.currentData()
            if not value:
                continue
            if key == "game_mode":
                out[key] = value == "on"
            elif key == "background":
                out[key] = {"action": value}
            else:
                out[key] = value
        if self.custom_fps.value():
            out["frame_limit"] = self.custom_fps.value()
        return out

    def _refresh_hz(self) -> int | None:
        system = self.s.system
        displays = getattr(system, "displays", None) or []
        rates = [d.refresh_hz for d in displays if getattr(d, "refresh_hz", None)]
        return int(max(rates)) if rates else None

    # -- interaction -------------------------------------------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._dlss_state()

    def _set_profile(self, profile: str, refresh: bool = True) -> None:
        self.profile = profile
        self.profile_buttons[profile].setChecked(True)
        p = next(p for p in PROFILES if p.id == profile)
        self.profile_text.setText(t(p.summary))
        self.custom_box.setVisible(profile == "custom")
        if refresh:
            self._refresh_preview()

    def _refresh_preview(self, *_) -> None:
        g = self.game()
        self.table.setRowCount(0)
        self.btn_apply.setEnabled(g is not None)
        if g is None:
            self.unavailable.setText(t("Scan or add a game in My Games first."))
            return
        self.preview = self.s.boost.preview(g, self.profile, self._custom_options(), refresh_hz=self._refresh_hz())
        for change in self.preview.changes:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate((t(change.title), t(change.before), t(change.after), t(SCOPE[change.scope]))):
                item = QTableWidgetItem(text)
                item.setToolTip(t(change.detail))
                self.table.setItem(row, col, item)
        notes = [f"{t(u.title)}: {t(u.reason)}" for u in self.preview.unavailable]
        if not self.preview.changes:
            notes.insert(0, t("Nothing to change: this game already has these settings."))
        self.unavailable.setText("\n".join(notes))
        self.unavailable.setVisible(bool(notes))

    def _say(self, text: str, error: bool = False) -> None:
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {theme.RED if error else theme.GREEN}; font-weight: 600;")
        self.note.setVisible(bool(text))

    def _apply(self) -> None:
        g = self.game()
        if g is None or self.preview is None:
            return
        persistent = len(self.preview.persistent)
        session = len(self.preview.session)
        text = t("Apply {a} setting(s) now and {b} while {g} runs? Each previous value is saved; Restore everything "
                 "puts them back.", a=persistent, b=session, g=g.name)
        if not confirm(self, t("FPS Booster"), text, ok_text=t("Apply")):
            return
        profile, custom, hz = self.profile, self._custom_options(), self._refresh_hz()

        def done(_):
            self._say(t("Applied. Measure the result with Benchmark (Boost or DLSS 5 + Boost)."))
            self._game_changed()
        workers.run(lambda: self.s.boost.apply(g, profile, custom, refresh_hz=hz), done,
                    lambda e: self._say(t("Not applied: {e}", e=str(e)), error=True))

    def _restore(self) -> None:
        g = self.game()
        if g is None:
            return
        if not confirm(self, t("FPS Booster"), t("Put every setting AKENO changed for {g} back to its previous value?",
                                                 g=g.name), ok_text=t("Restore")):
            return

        def done(items):
            self._say(t("Restored {n} setting(s).", n=len(items)))
            self._game_changed()
        workers.run(lambda: self.s.boost.restore(g), done,
                    lambda e: self._say(t("Not restored: {e}", e=str(e)), error=True))

    def _benchmark(self) -> None:
        self.win.go("benchmark")
        page = self.win.pages.get("benchmark")
        if page is not None and self.game() is not None:
            page.preset(self.game().id, self.mode if self.s.boost.configured(self.game().id) else "baseline")

    def _open_dlss(self) -> None:
        g = self.game()
        if g is not None:
            self.ctl.select_game(g.id)
        self.win.go("library")

    def _analyse(self) -> None:
        from akeno.performance.background import analyse
        self.btn_analyse.setEnabled(False)
        saved = set(self.s.boost.background_names())

        def done(rows):
            self.btn_analyse.setEnabled(True)
            self.bg_table.setRowCount(0)
            for r in rows:
                row = self.bg_table.rowCount()
                self.bg_table.insertRow(row)
                item = QTableWidgetItem(f"{r.title} ({r.name})")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if r.name in saved else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, r.name)
                self.bg_table.setItem(row, 0, item)
                self.bg_table.setItem(row, 1, QTableWidgetItem(f"{r.cpu_percent:.1f} %"))
                self.bg_table.setItem(row, 2, QTableWidgetItem(f"{r.memory_mb:.0f} MB"))
        workers.run(lambda: analyse(0.7), done, lambda e: (self.btn_analyse.setEnabled(True),
                                                           self._say(str(e), error=True)))

    def _save_background(self) -> None:
        shown, checked = set(), set()
        for row in range(self.bg_table.rowCount()):
            item = self.bg_table.item(row, 0)
            name = item.data(Qt.ItemDataRole.UserRole)
            shown.add(name)
            if item.checkState() == Qt.CheckState.Checked:
                checked.add(name)
        names = sorted(checked | (set(self.s.boost.background_names()) - shown))   # not running now: kept
        self.s.boost.set_background(names, self.bg_action.currentData())
        self._say(t("Saved: {n} program(s).", n=len(names)))
        self._refresh_preview()

    def _load_tick(self) -> None:
        from akeno.performance.monitor import snapshot
        gpu = self.s.system.gpus[0] if self.s.system and self.s.system.gpus else None
        sessions = getattr(self.win, "sessions", None)
        running = sessions.running() if sessions else {}
        pid = next(iter(running.values()), None)

        def done(snap):
            rows = [(t("CPU"), f"{snap.cpu_percent:.0f} %"),
                    (t("RAM"), f"{snap.ram_used_gb:.1f} / {snap.ram_total_gb:.1f} GB")]
            if snap.gpu_percent is not None:
                rows.append((t("GPU"), f"{snap.gpu_percent:.0f} %"))
            if snap.vram_used_gb is not None:
                rows.append((t("VRAM"), f"{snap.vram_used_gb:.1f}" + (f" / {snap.vram_total_gb:.0f} GB"
                                                                      if snap.vram_total_gb else " GB")))
            if snap.gpu_temp_c is not None:
                rows.append((t("GPU temperature"), f"{snap.gpu_temp_c:.0f} °C"))
            if snap.game_ram_gb is not None:
                rows.append((t("Game memory"), f"{snap.game_ram_gb:.1f} GB"))
            rows.append((t("GPU scheduling"), t({"on": "On", "off": "Off", "unknown": "Unknown"}[
                tweaks.hags_state()])))
            self.load.set_rows(rows)
            names = [self.s.library.get(g).name for g in running if self.s.library.get(g)]
            self.running.setText(t("Running now: {g}", g=", ".join(names)) if names else t("No game is running."))
        workers.run(lambda: snapshot(gpu, pid), done, lambda e: None)

    def on_show(self) -> None:
        self.refresh()
        self._load_tick()
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def info(self, text: str) -> None:
        info(self, t("FPS Booster"), text)
