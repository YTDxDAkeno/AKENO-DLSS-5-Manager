"""Smart Profiles: per-game profiles built only from settings the selected backend supports."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QSlider, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from akeno.i18n import t
from akeno.ui import theme
from akeno.ui.widgets import Card, FlowLayout, ResponsiveRow, StatusPill, button, clear_layout, info, label


class ProfilesPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        self.current: str | None = None
        self.values: dict = {}
        self.editors: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label(t("Smart Profiles"), "h1"))
        top.addStretch(1)
        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(300)
        self.game_combo.setAccessibleName(t("Game"))
        self.game_combo.activated.connect(lambda i: self.ctl.select_game(self.game_combo.currentData()))
        top.addWidget(label(t("Game"), "dim"))
        top.addWidget(self.game_combo)
        lay.addLayout(top)
        self.backend_line = label("", "dim", wrap=True)
        lay.addWidget(self.backend_line)
        self.rec_card = Card(t("Recommendation"), "sparkle", theme.VIOLET)
        self.rec_text = label("", wrap=True)
        self.rec_card.body.addWidget(self.rec_text)
        lay.addWidget(self.rec_card)
        self.chip_row = FlowLayout()
        self.chip_group = QButtonGroup(self)
        lay.addLayout(self.chip_row)
        body = ResponsiveRow(16)          # side by side when they fit, stacked at 150 % / long translations
        self.edit_card = Card(t("Settings"), "sliders", theme.BLUE)
        self.edit_box = QVBoxLayout()
        self.edit_box.setSpacing(12)
        self.edit_card.body.addLayout(self.edit_box)
        acts = FlowLayout()
        self.btn_reset = button(t("Restore recommended values"), "restore", on_click=self._reset)
        self.btn_save = button(t("Save as custom profile"), "file", on_click=self._save)
        self.btn_apply = button(t("Apply to installed backend"), "check", "primary", on_click=self._apply)
        for b in (self.btn_reset, self.btn_save, self.btn_apply):
            acts.addWidget(b)
        self.edit_card.body.addLayout(acts)
        self.apply_note = label("", "muted", wrap=True)
        self.edit_card.body.addWidget(self.apply_note)
        body.add(self.edit_card, 3)
        self.cmp_card = Card(t("Recorded benchmark results"), "chart", theme.CYAN)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([t("Profile"), t("Avg FPS"), t("1% low"), t("Frame time"), t("Date")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(220)
        self.cmp_card.body.addWidget(self.table)
        self.cmp_card.body.addWidget(button(t("Benchmark this profile"), "play",
                                            on_click=lambda: self._benchmark()))
        body.add(self.cmp_card, 2)
        lay.addWidget(body)
        lay.addStretch(1)
        for sig in (ctl.selection_changed, ctl.games_changed, ctl.installs_changed, ctl.benchmarks_changed,
                    ctl.hardware_changed):
            sig.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------------------------------
    def _ctx(self):
        g = self.s.selected_game()
        a = self.s.backend_for(g)
        return g, a

    def refresh(self) -> None:
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        games = self.s.library.games()
        for g in games:
            self.game_combo.addItem(g.name, g.id)
        g, a = self._ctx()
        if g:
            self.game_combo.setCurrentIndex(max(0, self.game_combo.findData(g.id)))
        self.game_combo.blockSignals(False)
        clear_layout(self.chip_row)
        clear_layout(self.edit_box)
        self.editors = {}
        if g is None or a is None:
            self.backend_line.setText(t("Select a game in the Game Library first."))
            self.rec_text.setText("")
            self._set_actions(False, t("No game selected."))
            return
        self.backend_line.setText(t("Backend: {b}. Only settings this backend actually supports are shown.", b=a.name))
        rec = self.s.recommendation(g)
        lines = [f"<b>{t(rec.headline)}</b>"] + [t(r) for r in rec.reasons]
        if rec.tradeoffs:
            lines.append(t("Trade-offs: ") + " ".join(t(x) for x in rec.tradeoffs))
        if rec.vram_note:
            lines.append(t(rec.vram_note))
        self.rec_text.setText("<br>".join(lines))
        profiles = a.profiles(g)
        if not profiles:
            self.edit_box.addWidget(label(t("{b} has no settings AKENO can configure. Use its own in-game overlay.",
                                            b=a.short), "dim", wrap=True))
            self._set_actions(False, t("Not configurable."))
            self._fill_table(g, a)
            return
        active = self.s.db.active_profile(g.id, a.id)
        saved_custom = next((p for p in self.s.db.profiles(g.id, a.id) if p["name"] == "custom"), None)
        ids = [p.id for p in profiles] + ["custom"]
        if self.current not in ids:
            self.current = (active["name"] if active else None) or rec.profile_id or "quality"
        for pid in ids:
            p = next((x for x in profiles if x.id == pid), None)
            text = t(p.label) if p else t("Custom")
            if pid == rec.profile_id:
                text += " ★"
            if active and active["name"] == pid:
                text += f" · {t('active')}"
            b = button(text)
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.setChecked(pid == self.current)
            b.setToolTip(t(p.tradeoff) + (("\n" + t("Reference (not your system): ") + p.reference) if p.reference else "")
                         if p else t("Your own combination of supported settings."))
            b.clicked.connect(lambda checked=False, x=pid: self.select_profile(x))
            self.chip_group.addButton(b)
            self.chip_row.addWidget(b)
        base = a.default_params(g)
        prof = next((x for x in profiles if x.id == self.current), None)
        if prof:
            self.values = dict(base, **prof.params)
        elif saved_custom:
            self.values = dict(base, **saved_custom["params"])
        elif active:
            self.values = dict(base, **active["params"])
        else:
            self.values = dict(base)
        if prof:
            self.edit_box.addWidget(label(f"<b>{t(prof.label)}</b> — {t(prof.summary)}<br>{t(prof.tradeoff)}",
                                          wrap=True))
        for spec in a.param_specs(g):
            self.edit_box.addWidget(self._editor(spec, self.values.get(spec.key, spec.default), editable=prof is None))
        installed = self.s.installer.active_install(g.id, a.id)
        self._set_actions(bool(installed), "" if installed else
                          t("{b} is not installed in this game yet. Install it on the Secure Install page; the "
                            "selected profile is used for the installation.", b=a.short))
        self._fill_table(g, a)

    def _editor(self, spec, value, editable: bool) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        row = QHBoxLayout()
        name = label(t(spec.label))
        name.setStyleSheet("font-weight: 600;")
        row.addWidget(name)
        if spec.requires_restart:
            row.addWidget(StatusPill(t("needs game restart"), "warn"))
        row.addStretch(1)
        if spec.kind == "bool":
            ed = QCheckBox(t("On"))
            ed.setChecked(bool(value))
            ed.toggled.connect(lambda v, k=spec.key: self._changed(k, v))
        elif spec.kind == "choice":
            ed = QComboBox()
            for val, text in spec.choices:
                ed.addItem(t(text), val)
            ed.setCurrentIndex(max(0, ed.findData(value)))
            ed.currentIndexChanged.connect(lambda i, k=spec.key, e=ed: self._changed(k, e.currentData()))
        elif spec.kind == "int":
            ed = QSpinBox()
            ed.setRange(int(spec.minimum), int(spec.maximum))
            ed.setValue(int(value))
            ed.valueChanged.connect(lambda v, k=spec.key: self._changed(k, v))
        else:
            ed = QWidget()
            el = QHBoxLayout(ed)
            el.setContentsMargins(0, 0, 0, 0)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setMinimumWidth(240)
            factor = 100
            sl.setRange(int(spec.minimum * factor), int(spec.maximum * factor))
            sl.setValue(int(round(float(value) * factor)))
            sb = QDoubleSpinBox()
            sb.setRange(spec.minimum, spec.maximum)
            sb.setSingleStep(spec.step or 0.01)
            sb.setDecimals(2)
            sb.setValue(float(value))
            sl.valueChanged.connect(lambda v, s=sb: s.setValue(v / factor))
            sb.valueChanged.connect(lambda v, s=sl, k=spec.key: (s.blockSignals(True), s.setValue(int(round(v * factor))),
                                                                 s.blockSignals(False), self._changed(k, v)))
            el.addWidget(sl, 1)
            el.addWidget(sb)
            if spec.key.endswith("WorkingScale"):
                pct = label(f"{float(value) * 100:.0f}%", "dim")
                sb.valueChanged.connect(lambda v, lb=pct: lb.setText(f"{v * 100:.0f}%"))
                el.addWidget(pct)
            ed.setAccessibleName(t(spec.label))
        ed.setEnabled(editable)
        ed.setToolTip(t(spec.help) + ("" if editable else "\n" + t("Switch to Custom to change this value.")))
        row.addWidget(ed)
        lay.addLayout(row)
        lay.addWidget(label(t(spec.help), "muted", wrap=True))
        self.editors[spec.key] = ed
        return w

    def _changed(self, key, value) -> None:
        self.values[key] = value

    def _set_actions(self, installed: bool, note: str) -> None:
        g, a = self._ctx()
        configurable = bool(g and a and a.profiles(g))
        self.btn_reset.setEnabled(configurable)
        self.btn_save.setEnabled(configurable and self.current == "custom")
        self.btn_apply.setEnabled(configurable and installed)
        self.btn_apply.setToolTip(note or t("Write these settings to the backend's configuration file"))
        self.apply_note.setText(note)

    def _fill_table(self, g, a) -> None:
        sessions = self.s.benchmarks.sessions(g.id)
        self.table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            st = s["stats"]
            for j, v in enumerate([s["label"], f"{st['avg_fps']:.1f}", f"{st['low_1pct_fps']:.1f}",
                                   f"{st['avg_frametime_ms']:.2f} ms", s["created_at"][:16].replace("T", " ")]):
                self.table.setItem(i, j, QTableWidgetItem(v))

    def select_profile(self, pid: str) -> None:
        self.current = pid
        self.refresh()

    def selected_params(self) -> tuple[str, dict]:
        g, a = self._ctx()
        specs = {s.key: s for s in a.param_specs(g)} if a else {}
        clean = {}
        for k, v in self.values.items():
            if k in specs:
                clean[k] = specs[k].coerce(v)
        return self.current or "custom", clean

    def _reset(self) -> None:
        g, a = self._ctx()
        rec = self.s.recommendation(g)
        self.current = rec.profile_id if rec.profile_id else "quality"
        self.refresh()

    def _save(self) -> None:
        g, a = self._ctx()
        try:
            name, params = self.selected_params()
        except ValueError as exc:
            info(self, t("Save as custom profile"), str(exc), error=True)
            return
        self.s.db.save_profile(g.id, "custom", a.id, params, active=False)
        self.ctl.status.emit(t("Custom profile saved."))
        self.refresh()

    def _apply(self) -> None:
        g, a = self._ctx()
        try:
            name, params = self.selected_params()
        except ValueError as exc:
            info(self, t("Apply to installed backend"), str(exc), error=True)
            return

        def done(res):
            self.ctl.installs_changed.emit()
            info(self, t("Apply to installed backend"),
                 t("Settings applied. Restart the game for them to take effect.") if res.ok else t("Not applied."),
                 "\n".join(t(m) for m in res.messages), error=not res.ok)
        self.ctl.run("configure", lambda: self.s.configure(g, a, params, name), done,
                     lambda e: info(self, t("Apply to installed backend"), str(e), error=True))

    def _benchmark(self) -> None:
        self.ctl.navigate.emit("benchmark")
        win = self.window()
        if hasattr(win, "pages"):
            win.pages["benchmark"].preset_label(self.current or "custom")
