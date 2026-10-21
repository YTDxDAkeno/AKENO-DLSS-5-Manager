"""Benchmark page: PresentMon capture, CSV import, sessions, frame-time graphs and export."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QSpinBox, QTableWidget, QLineEdit,
                               QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from akeno.benchmark.stats import consistency_label
from akeno.core import processes
from akeno.i18n import t
from akeno.paths import is_windows
from akeno.security.downloads import CancelToken
from akeno.ui import theme
from akeno.ui.widgets import (Card, EmptyState, FrameTimeChart, KeyValueGrid, Spinner, button, clear_layout, confirm,
                              info, label)


class BenchmarkPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        self.token: CancelToken | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(label(t("Benchmark"), "h1"))
        lay.addWidget(label(t("Measurements come from Intel PresentMon (MIT licensed, downloaded from its official "
                              "GitHub release and hash-verified on first use). Start the game, go to a repeatable "
                              "scene, then start the capture. Nothing here is simulated."), "dim", wrap=True))
        run = Card(t("New measurement"), "play", theme.GREEN)
        row = QHBoxLayout()
        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(260)
        self.game_combo.setAccessibleName(t("Game"))
        row.addWidget(label(t("Game"), "dim"))
        row.addWidget(self.game_combo, 2)
        self.label_combo = QComboBox()
        self.label_combo.setAccessibleName(t("Measurement label"))
        self.label_combo.setToolTip(t("Label the run with what was active in the game: the baseline (no Neural "
                                      "Rendering) or the profile you applied."))
        row.addWidget(label(t("Configuration"), "dim"))
        row.addWidget(self.label_combo, 1)
        self.duration = QSpinBox()
        self.duration.setRange(10, 600)
        self.duration.setSuffix(" s")
        self.duration.setValue(int(self.s.settings.get("benchmark_duration_s")))
        self.duration.setAccessibleName(t("Duration"))
        row.addWidget(label(t("Duration"), "dim"))
        row.addWidget(self.duration)
        run.body.addLayout(row)
        conditions = QHBoxLayout()
        self.conditions = {}
        for key, title in [('game_version', 'Game version'), ('workload', 'Scene / workload'),
                           ('resolution', 'Game resolution'), ('settings', 'Graphics settings')]:
            edit = QLineEdit(); edit.setPlaceholderText(t(title))
            edit.setAccessibleName(t(title)); self.conditions[key] = edit; conditions.addWidget(edit)
        run.body.addLayout(conditions)
        row2 = QHBoxLayout()
        self.btn_start = button(t("Start Benchmark"), "play", "primary", on_click=self._start)
        self.btn_stop = button(t("Stop"), "stop", on_click=self._stop)
        self.btn_stop.setEnabled(False)
        self.btn_import = button(t("Import PresentMon CSV"), "file", on_click=self._import)
        self.spinner = Spinner()
        self.status = label("", "dim", wrap=True)
        for w in (self.btn_start, self.btn_stop, self.btn_import, self.spinner):
            row2.addWidget(w)
        row2.addWidget(self.status, 1)
        run.body.addLayout(row2)
        run.body.addWidget(label(t("Baseline: restore original files and capture a repeatable scene. Experimental: install the selected runtime and repeat the same scene and settings. Select both measured sessions below to compare performance; assess visual quality separately."), "muted", wrap=True))
        lay.addWidget(run)
        self.sessions_card = Card(t("Sessions"), "chart", theme.BLUE)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([t("Game"), t("Configuration"), t("Avg FPS"), t("1% low"), t("0.1% low"),
                                              t("Median ms"), t("P99 ms"), t("Source"), t("Date")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.setMinimumHeight(200)
        self.sessions_card.body.addWidget(self.table)
        acts = QHBoxLayout()
        acts.addWidget(button(t("Export CSV"), "export", on_click=lambda: self._export("csv")))
        acts.addWidget(button(t("Export JSON"), "export", on_click=lambda: self._export("json")))
        acts.addWidget(button(t("Export HTML"), "export", on_click=lambda: self._export("html")))
        acts.addWidget(button(t("Export frame samples"), "export", on_click=self._export_frames))
        acts.addStretch(1)
        self.sessions_card.body.addLayout(acts)
        session_actions = QHBoxLayout()
        session_actions.addWidget(button(t("Delete selected"), "trash", "danger", on_click=self._delete))
        session_actions.addWidget(label(t("Select two sessions to compare them."), "muted", wrap=True), 1)
        self.sessions_card.body.addLayout(session_actions)
        self.empty = EmptyState("chart", t("No benchmarks yet"),
                                t("Not measured yet — run a benchmark to see performance results."),
                                button(t("Start Benchmark"), "play", "primary", on_click=self._start))
        self.sessions_card.body.addWidget(self.empty)
        lay.addWidget(self.sessions_card)
        self.detail_card = Card(t("Analysis"), "gauge", theme.VIOLET)
        self.chart = FrameTimeChart()
        self.detail_card.body.addWidget(self.chart)
        self.detail_grid = KeyValueGrid(200)
        self.detail_card.body.addWidget(self.detail_grid)
        lay.addWidget(self.detail_card)
        lay.addStretch(1)
        for sig in (ctl.games_changed, ctl.selection_changed, ctl.benchmarks_changed):
            sig.connect(self.refresh)
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def preset_label(self, label_id: str) -> None:
        i = self.label_combo.findData(label_id)
        if i >= 0:
            self.label_combo.setCurrentIndex(i)

    def refresh(self) -> None:
        sel = self.s.selected_game()
        cur_label = self.label_combo.currentData()
        self.game_combo.clear()
        for g in self.s.library.games():
            self.game_combo.addItem(g.name, g.id)
        if sel:
            self.game_combo.setCurrentIndex(max(0, self.game_combo.findData(sel.id)))
        self.label_combo.clear()
        self.label_combo.addItem(t("Baseline (Neural Rendering off)"), "baseline")
        a = self.s.backend_for(sel)
        for p in (a.profiles(sel) if a and sel else []):
            if p.id != "disabled":
                self.label_combo.addItem(t(p.label), p.id)
        self.label_combo.addItem(t("Custom"), "custom")
        if cur_label:
            self.preset_label(cur_label)
        sessions = self.s.benchmarks.sessions()
        names = {g.id: g.name for g in self.s.library.games()}
        self.table.setRowCount(len(sessions))
        self._sessions = sessions
        for i, s in enumerate(sessions):
            st = s["stats"]
            vals = [names.get(s["game_id"], s["game_id"]), s["label"], f"{st['avg_fps']:.1f}",
                    f"{st['low_1pct_fps']:.1f}", f"{st['low_01pct_fps']:.1f}" if st.get("low_01pct_fps") else "—",
                    f"{st['median_frametime_ms']:.2f}", f"{st['p99_frametime_ms']:.2f}", s.get("source", ""),
                    s["created_at"][:16].replace("T", " ")]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.setVisible(bool(sessions))
        self.empty.setVisible(not sessions)
        self.detail_card.setVisible(bool(sessions))
        self._selection()

    def _selected(self) -> list[dict]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._sessions[r] for r in rows if r < len(self._sessions)]

    def _selection(self) -> None:
        sel = self._selected()[:2]
        if not sel and getattr(self, "_sessions", None):
            sel = self._sessions[:1]
        colors = [theme.VIOLET, theme.CYAN]
        self.chart.set_series([(f"{s['label']} #{s['id']}", self.s.benchmarks.frametimes(s), colors[i])
                               for i, s in enumerate(sel)])
        rows = []
        for s in sel:
            st = s["stats"]
            rows.append((f"{s['label']} #{s['id']}",
                         t("{f} FPS avg · 1% low {l} · median {m} ms · consistency {c} · {n} frames in {d} s",
                           f=f"{st['avg_fps']:.1f}", l=f"{st['low_1pct_fps']:.1f}", m=f"{st['median_frametime_ms']:.2f}",
                           c=t(consistency_label(self.s.benchmarks.stats_of(s))), n=st["frames"], d=f"{st['duration_s']:.0f}")))
            rows.append((t("Recorded with"), " · ".join(x for x in (s.get("gpu"), s.get("driver") and
                                                                      f"{t('driver')} {s.get('driver')}",
                                                                      s.get("resolution"), s.get("backend_id"))
                                                         if x) or t("Unknown")))
        if len(sel) == 2:
            a, b = sel
            if b.get('label') == 'baseline' and a.get('label') != 'baseline': a, b = b, a
            diff = (b["stats"]["avg_fps"] / a["stats"]["avg_fps"] - 1) * 100 if a["stats"]["avg_fps"] else 0
            rows.append((t("Difference"), f"{diff:+.1f}% ({b['label']} vs {a['label']})"))
            for metric in self.s.benchmarks.comparison_rows(a, b):
                if metric['difference'] is not None:
                    rows.append((metric['metric'], f"{metric['native']:.2f} → {metric['experimental']:.2f} ({metric['difference']:+.2f})"))
                else:
                    rows.append((metric['metric'], t('Not measured')))
            warn = self.s.benchmarks.comparable(a, b)
            if warn:
                rows.append((t("Comparison warning"), t("Measured under different conditions: ") + ", ".join(warn)))
        self.detail_grid.set_rows(rows)

    def _meta(self, game) -> dict:
        gpu = self.s.selected_gpu()
        active = self.s.installer.active_install(game.id)
        return {"gpu": gpu.name if gpu else None, "driver": gpu.driver_version if gpu else None,
                "resolution": self.conditions["resolution"].text().strip() or None,
                "backend_id": active["backend_id"] if active else None,
                "neural_config": dict(active["params"] if active else {},
                    capture_conditions={k: v.text().strip() for k, v in self.conditions.items()})}

    def _start(self) -> None:
        gid = self.game_combo.currentData()
        game = self.s.library.get(gid)
        if not game or not game.exe_path:
            info(self, t("Benchmark"), t("Select a game with a known executable first."), error=True)
            return
        if not is_windows():
            info(self, t("Benchmark"), t("PresentMon capture needs Windows."), error=True)
            return
        running = processes.find_by_exe(game.exe_path)
        if not running:
            info(self, t("Benchmark"), t("{g} is not running. Start the game, load a repeatable scene, then start "
                                         "the benchmark.", g=game.name))
            return
        lbl = self.label_combo.currentData() or "custom"
        a = self.s.backend_for(game)
        installed = self.s.installer.active_install(game.id)
        if lbl == "baseline" and installed:
            if not confirm(self, t("Benchmark"), t("{b} is installed in this game. For a true baseline, set the "
                                                   "Disabled profile (or turn Neural Rendering off in its overlay) "
                                                   "before measuring.", b=a.short), ok_text=t("It is off — measure")):
                return
        delay = int(self.s.settings.get("benchmark_delay_s"))
        dur = int(self.duration.value())
        if not confirm(self, t("Benchmark"), t("PresentMon will record {g} for {d} seconds after a {w}-second delay. "
                                               "If Windows asks for permission, PresentMon needs it to read "
                                               "performance events.", g=game.name, d=dur, w=delay),
                       ok_text=t("Start")):
            return
        self.token = CancelToken()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.spinner.start()
        self.status.setText(t("Preparing PresentMon…"))
        meta = self._meta(game)
        meta['neural_config']['baseline_user_attested_off'] = bool(lbl == 'baseline' and installed)
        proc = os.path.basename(game.exe_path)

        gpu = self.s.selected_gpu()
        def work(emit):
            from akeno.hardware.sensors import sample
            self.s.benchmarks.runner.ensure(token=self.token)
            return self.s.benchmarks.run(game.id, proc, lbl, meta, dur, delay, on_status=emit, token=self.token, process_id=running[0].pid if len(running) == 1 else None, sensor_sampler=lambda: sample(gpu))

        def done(sess):
            self._finish()
            self.status.setText(t("Done: {f} FPS average, 1% low {l} FPS.", f=f"{sess.stats.avg_fps:.1f}",
                                  l=f"{sess.stats.low_1pct_fps:.1f}"))
            self.ctl.benchmarks_changed.emit()

        def fail(exc):
            self._finish()
            self.status.setText(t("Benchmark failed: ") + str(exc))

        self.ctl.run("benchmark", work, done, fail, on_progress=lambda m: self.status.setText(t(str(m))))

    def _finish(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.spinner.stop()

    def _stop(self) -> None:
        if self.token:
            self.token.cancel()
            self.status.setText(t("Stopping…"))

    def _import(self) -> None:
        gid = self.game_combo.currentData()
        game = self.s.library.get(gid)
        if not game:
            info(self, t("Import PresentMon CSV"), t("Select a game first."), error=True)
            return
        path, _ = QFileDialog.getOpenFileName(self, t("Import PresentMon CSV"), "", "CSV (*.csv)")
        if not path:
            return
        lbl = self.label_combo.currentData() or "custom"
        try:
            sess = self.s.benchmarks.import_csv(game.id, path, lbl, self._meta(game),
                                                os.path.basename(game.exe_path) if game.exe_path else None)
        except Exception as exc:
            info(self, t("Import PresentMon CSV"), str(exc), error=True)
            return
        self.status.setText(t("Imported {n} frames.", n=sess.stats.frames))
        self.ctl.benchmarks_changed.emit()

    def _export_frames(self):
        sessions = self._selected()
        if not sessions: return
        path, _ = QFileDialog.getSaveFileName(self, t('Export frame samples'), 'akeno-frames.csv', 'CSV (*.csv)')
        if path: self.s.benchmarks.export_frame_csv(sessions, path)

    def _export(self, kind: str) -> None:
        sessions = self._selected() or self._sessions
        if not sessions:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("Export"), f"akeno-benchmarks.{kind}", f"{kind.upper()} (*.{kind})")
        if not path:
            return
        getattr(self.s.benchmarks, "export_" + kind)(sessions, path)
        self.status.setText(t("Exported {n} session(s).", n=len(sessions)))

    def _delete(self) -> None:
        sel = self._selected()
        if sel and confirm(self, t("Delete selected"), t("Delete {n} benchmark session(s)?", n=len(sel))):
            for s in sel:
                self.s.db.delete_benchmark(s["id"])
            self.ctl.benchmarks_changed.emit()
