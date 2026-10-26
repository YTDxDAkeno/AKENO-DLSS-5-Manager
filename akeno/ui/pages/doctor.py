"""System Doctor: health dashboard, Slow PC Detective, Startup Analyzer, Crash Detective, Hardware Health and
Guided Repair. Every analysis runs on a worker thread with a Cancel button; nothing changes without a confirmation
that says what will change."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.system import commands
from akeno.system.doctor.repair import STEPS
from akeno.system.doctor.startup import SOURCE_LABELS
from akeno.system.model import Level, Reading, dec, human_bytes, human_duration
from akeno.ui import theme
from akeno.ui.system_ui import (FindingList, ReadingTile, Sparkline, TabbedPage, TaskBar, card, export_button,
                                fill_table, make_table, open_link, selected_data, show_stored)
from akeno.ui.widgets import (Card, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, Meter, ResponsiveRow, StatusPill,
                              SubCard, button, clear_layout, confirm, info, label)

TABS = (("overview", "Overview"), ("slowpc", "Slow PC Detective"), ("startup", "Startup"), ("crashes", "Crashes"),
        ("hardware", "Hardware Health"), ("repair", "Repair"))
SIG_TEXT = {"valid": "Signed", "unsigned": "Not signed", "invalid": "Invalid signature", "unknown": "Unknown"}
PERIODS = ((7, "Last 7 days"), (14, "Last 14 days"), (30, "Last 30 days"))


def _flow(*widgets) -> QWidget:
    row = FlowLayout(spacing=8)
    for w in widgets:
        row.addWidget(w)
    holder = QWidget()
    holder.setLayout(row)
    return holder


class DoctorPage(TabbedPage):
    def __init__(self, ctl, window):
        super().__init__(t("System Doctor"), t("What this PC reports about its hardware and Windows, why it may be "
                                               "slow or crash, and Windows' own repair tools. AKENO measures and "
                                               "explains; it changes nothing without your confirmation."))
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.tools = self.s.system_tools
        self.svc = self.tools.doctor
        self.overview = OverviewTab(self)
        self.slowpc = SlowPcTab(self)
        self.startup = StartupTab(self)
        self.crashes = CrashTab(self)
        self.hardware = HardwareTab(self)
        self.repair = RepairTab(self)
        for (key, title), w in zip(TABS, (self.overview, self.slowpc, self.startup, self.crashes, self.hardware,
                                          self.repair)):
            self.add_tab(key, t(title), w)

    def link(self, module: str, target: str = "") -> None:
        if module == "doctor":
            self.show_tab(target or "overview")
            return
        open_link(self.win, module, target)

    def on_show(self) -> None:
        self.tab_shown(self.current or "overview")

    def tab_shown(self, key: str) -> None:
        w = {"overview": self.overview, "slowpc": self.slowpc, "startup": self.startup, "crashes": self.crashes,
             "hardware": self.hardware, "repair": self.repair}[key]
        if hasattr(w, "on_show"):
            w.on_show()


# -- overview ------------------------------------------------------------------------------------------------------
class OverviewTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        self.loaded_at = 0.0
        self.last: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.task = TaskBar(page.ctl, "doctor:snapshot")
        top = QHBoxLayout()
        self.btn = button(t("Refresh"), "refresh", on_click=self.refresh)
        top.addWidget(self.btn)
        top.addWidget(export_button(self, page.tools.store, "doctor", lambda: {"overview": self.last}))
        top.addWidget(self.task, 1)
        top.addStretch(0)
        lay.addLayout(top)

        row = ResponsiveRow(spacing=14)
        self.score_value = label("—", "stat")
        self.score_text = label(t("Run a refresh to see the indicator."), "dim", wrap=True)
        self.score_list = QVBoxLayout()
        sc = Card(t("Health indicator"), "gauge")
        sc.body.addWidget(self.score_value)
        sc.body.addWidget(self.score_text)
        sc.body.addLayout(self.score_list)
        sc.body.addWidget(label(t("100 minus 15 points per problem and 6 per warning found by System Doctor (at most "
                                  "three of a kind). A summary of the findings, not a verdict on the PC; sensors "
                                  "Windows does not report never cost points."), "muted", wrap=True))
        row.add(sc, 1)
        sysc = Card(t("Windows"), "monitor")
        self.sys_grid = KeyValueGrid(130)
        sysc.body.addWidget(self.sys_grid)
        row.add(sysc, 1)
        lay.addWidget(row)

        tiles = ResponsiveRow(spacing=14)
        cpu = Card(t("Processor"), "cpu")
        self.cpu_name = label("—", wrap=True)
        cpu.body.addWidget(self.cpu_name)
        line = QHBoxLayout()
        self.t_cpu = {k: ReadingTile(t(n)) for k, n in (("load", "Load"), ("clock", "Clock"),
                                                         ("temperature", "Temperature"))}
        for w in self.t_cpu.values():
            line.addWidget(w, 1)
        cpu.body.addLayout(line)
        self.spark_cpu = Sparkline(theme.ACCENT)
        cpu.body.addWidget(self.spark_cpu)
        tiles.add(cpu, 1)
        mem = Card(t("Memory"), "layers")
        line = QHBoxLayout()
        self.t_mem = {k: ReadingTile(t(n)) for k, n in (("percent", "In use"), ("commit", "Committed"),
                                                         ("paging", "Paging"))}
        for w in self.t_mem.values():
            line.addWidget(w, 1)
        mem.body.addLayout(line)
        self.mem_total = label("", "muted")
        mem.body.addWidget(self.mem_total)
        self.spark_ram = Sparkline(theme.INFO)
        mem.body.addWidget(self.spark_ram)
        tiles.add(mem, 1)
        lay.addWidget(tiles)

        lower = ResponsiveRow(spacing=14)
        self.gpu_card = Card(t("Graphics"), "chip")
        self.gpu_box = QVBoxLayout()
        self.gpu_card.body.addLayout(self.gpu_box)
        lower.add(self.gpu_card, 1)
        self.disk_card = Card(t("Drives"), "database")
        self.disk_box = QVBoxLayout()
        self.disk_card.body.addLayout(self.disk_box)
        lower.add(self.disk_card, 1)
        lay.addWidget(lower)

        self.temp_card = Card(t("Temperatures"), "gauge")
        self.temp_grid = KeyValueGrid(260)
        self.temp_card.body.addWidget(self.temp_grid)
        lay.addWidget(self.temp_card)

        fc = Card(t("What System Doctor noticed"), "eye")
        self.findings = FindingList(page.link, t("Nothing to report from the readings. Slow PC Detective and Crash "
                                                 "Detective look deeper."))
        fc.body.addWidget(self.findings)
        lay.addWidget(fc)
        lay.addStretch(1)

    def on_show(self) -> None:
        if time.time() - self.loaded_at > 30 and not self.task.running:
            self.refresh()

    def refresh(self) -> None:
        self.task.start(self.page.svc.snapshot, self._show, text=t("Reading this PC's sensors…"), timeout=120)

    def _show(self, out: dict) -> None:
        self.loaded_at = time.time()
        self.last = {"snapshot": out["snapshot"].to_dict(), "score": out["score"].to_dict()}
        snap = out["snapshot"]
        score = out["score"]
        self.score_value.setText(f"{score.value} / 100")
        self.score_text.setText(t("No problem found in the last analyses.") if not score.deductions else
                                t("Findings that lower the indicator: {n}", n=len(score.deductions)))
        clear_layout(self.score_list)
        for points, reason in score.deductions[:8]:
            self.score_list.addWidget(label(f"−{points}  {t(reason)}", "dim", wrap=True))
        osd = snap.os or {}
        uptime = human_duration(snap.uptime_s) if snap.uptime_s is not None else "—"
        boot = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap.boot_time)) if snap.boot_time else "—"
        self.sys_grid.set_rows([(t("Version"), " ".join(x for x in (osd.get("name"), osd.get("display_version"))
                                                         if x) or "—"),
                                (t("Build"), str(osd.get("build") or "—")),
                                (t("Running for"), uptime), (t("Started"), boot)])
        self.cpu_name.setText(osd.get("cpu_name") or t("Processor name not detected yet (scan the hardware on the "
                                                        "Hardware page)."))
        cpu = snap.cpu
        self.t_cpu["load"].set(cpu.get("load"), _pct(cpu.get("load")))
        self.t_cpu["clock"].set(cpu.get("clock"))
        self.t_cpu["temperature"].set(cpu.get("temperature"))
        m = snap.memory
        self.t_mem["percent"].set(m.get("percent"), _pct(m.get("percent")))
        self.t_mem["commit"].set(m.get("commit"), _pct(m.get("commit")))
        pg = m.get("paging")
        self.t_mem["paging"].set(pg, f"{float(pg.value):.0f}/s" if pg is not None and pg.available else None)
        total = m.get("total")
        self.mem_total.setText(t("{used} of {total} in use", used=human_bytes(m["used"].value),
                                 total=human_bytes(total.value)) if total and total.available else "")
        hist = out.get("history") or []
        self.spark_cpu.set_values([h.get("cpu") for h in hist])
        self.spark_ram.set_values([h.get("ram") for h in hist])
        clear_layout(self.gpu_box)
        if not snap.gpus:
            self.gpu_box.addWidget(label(t("No graphics processor known yet. The hardware scan on the Hardware page "
                                           "detects it."), "dim", wrap=True))
        for g in snap.gpus:
            self.gpu_box.addWidget(label(g["name"], "h3", wrap=True))
            grid = KeyValueGrid(130)
            rows = []
            for key, name in (("load", "Load"), ("vram_used", "VRAM in use"), ("vram_total", "VRAM"),
                              ("temperature", "Temperature"), ("clock", "Clock"), ("power", "Power draw"),
                              ("driver", "Driver"), ("throttle", "Throttling")):
                r = g["readings"].get(key)
                if r is None:
                    continue
                rows.append((t(name), _reading_text(r)))
            grid.set_rows(rows)
            self.gpu_box.addWidget(grid)
        clear_layout(self.disk_box)
        for v in snap.volumes:
            if not v.get("total"):
                continue
            used = 1 - (v.get("free") or 0) / v["total"]
            self.disk_box.addWidget(label(f"{v['mount']}  ·  {t('{free} free of {total}', free=human_bytes(v.get('free')), total=human_bytes(v['total']))}",
                                          shrink=True))
            meter = Meter(used, (theme.RED, theme.AMBER) if used > 0.9 else (theme.VIOLET_DEEP, theme.VIOLET))
            self.disk_box.addWidget(meter)
        if not snap.volumes:
            self.disk_box.addWidget(label(t("No drive reported."), "dim"))
        rows = [(f"{z['name']}", f"{z['celsius']:.0f} °C  ·  {t(z['source'])}") for z in snap.temperatures]
        if not rows:
            rows = [(t("Processor"), t("Not available: Windows has no documented CPU temperature sensor without a "
                                       "vendor driver."))]
        self.temp_grid.set_rows(rows)
        self.findings.set_findings(out["findings"], snap.errors)


def _pct(r: Reading | None) -> str | None:
    return f"{r.value:.0f} %" if r is not None and r.available else None


def _reading_text(r: Reading) -> str:
    if not r.available:
        return "— (" + t(r.note) + ")" if r.note else "—"
    if r.unit == "B":
        text = human_bytes(r.value)
    elif isinstance(r.value, float):
        text = f"{r.value:.0f} {r.unit}".strip()
    else:
        text = f"{t(r.value) if isinstance(r.value, str) else r.value} {r.unit}".strip()
    return text + (f"  ({t(r.note)})" if r.note else "")


# -- Slow PC Detective -------------------------------------------------------------------------------------------
class SlowPcTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        intro = Card(t("My PC is slower than usual"), "search")
        intro.body.addWidget(label(t("AKENO measures processor, memory and disk load for a few seconds, checks free "
                                     "space and startup programs, and reads Windows' records of overheating, "
                                     "hardware errors and recent driver and update installations. Each result says "
                                     "whether it was measured, is a possible cause, or only happened at the same "
                                     "time."), "dim", wrap=True))
        self.btn = button(t("Analyse now"), "search", "primary", on_click=self.run)
        intro.body.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.task = TaskBar(page.ctl, "doctor:slowpc")
        self.task.finished.connect(lambda _ok: self.btn.setEnabled(True))
        intro.body.addWidget(self.task)
        self.when = label("", "muted")
        intro.body.addWidget(self.when)
        lay.addWidget(intro)
        res = Card(t("Results"), "eye")
        self.findings = FindingList(page.link)
        res.body.addWidget(self.findings)
        self.empty = EmptyState("search", t("No analysis yet"), t("Start the analysis while the PC feels slow."))
        res.body.addWidget(self.empty)
        lay.addWidget(res)
        procs = Card(t("Processes during the measurement"), "layers")
        self.table = make_table([t("Process"), t("Processor"), t("Memory"), t("Disk I/O")], 220)
        procs.body.addWidget(self.table)
        lay.addWidget(procs)
        lay.addStretch(1)
        show_stored("doctor.slowpc", lambda: page.svc.last("slowpc"), self._show, self.task)

    def run(self) -> None:
        self.btn.setEnabled(False)
        if not self.task.start(self.page.svc.slow_pc, self._show, self._failed, text=t("Analysing…"), timeout=240):
            self.btn.setEnabled(True)

    def _failed(self, exc) -> None:
        self.btn.setEnabled(True)
        self.task.show_message(str(exc), error=True)

    def _show(self, rep) -> None:
        self.btn.setEnabled(True)
        self.empty.hide()
        self.when.setText(t("Last analysis: {when} ({s} s)", when=rep.created_at[:16].replace("T", " "),
                            s=rep.duration_s if rep.duration_s is not None else "?"))
        self.findings.set_findings(rep.findings, rep.errors)
        rows = []
        for p in (rep.data.get("top_cpu") or [])[:12]:
            rows.append([p.get("name") or "?", f"{(p.get('cpu_percent') or 0):.1f} %", human_bytes(p.get("rss")),
                         (human_bytes(p.get("io_bps")) + "/s") if p.get("io_bps") else "—"])
        fill_table(self.table, rows, tips=[[p.get("exe") or ""] for p in (rep.data.get("top_cpu") or [])[:12]])


# -- Startup Analyzer --------------------------------------------------------------------------------------------
class StartupTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        self.entries = []
        self.loaded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Programs that start with Windows"), "power")
        c.body.addWidget(label(t("Disabling works like Task Manager: the program stays installed and its entry stays "
                                 "where it is; Windows just does not start it. Enable brings it back. Windows "
                                 "components and security software cannot be disabled here. Entries for all users "
                                 "need administrator rights (Windows asks)."), "dim", wrap=True))
        self.task = TaskBar(page.ctl, "doctor:startup")
        c.body.addWidget(self.task)
        self.table = make_table([t("Name"), t("Publisher"), t("Status"), t("Signature"), t("Source"),
                                 t("Notes")], 260)
        self.table.itemSelectionChanged.connect(self._selected)
        c.body.addWidget(EmptyAware(self.table, EmptyState("power", t("No startup entries found"),
                                                           t("Neither the registry nor the Startup folders list a "
                                                             "program that starts when you sign in.")), 260))
        self.detail = label("", "dim", wrap=True, selectable=True)
        c.body.addWidget(self.detail)
        self.b_disable = button(t("Disable…"), "pause", on_click=lambda: self._toggle(False))
        self.b_enable = button(t("Enable…"), "play", on_click=lambda: self._toggle(True))
        self.b_open = button(t("Open file location"), "folder", "ghost", on_click=self._open)
        self.b_refresh = button(t("Refresh"), "refresh", "ghost", on_click=self.refresh)
        c.body.addWidget(_flow(self.b_disable, self.b_enable, self.b_open, self.b_refresh))
        lay.addWidget(c)
        h = Card(t("Changes made by AKENO"), "restore")
        self.hist = make_table([t("When"), t("Change"), t("Entry"), t("State")], 140)
        h.body.addWidget(self.hist)
        self.b_restore = button(t("Restore selected change…"), "restore", on_click=self._restore)
        h.body.addWidget(self.b_restore, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(h)
        lay.addStretch(1)
        self._selected()

    def on_show(self) -> None:
        if not self.loaded and not self.task.running:
            self.refresh()

    def refresh(self) -> None:
        self.task.start(self.page.svc.startup_entries, self._show, text=t("Reading startup entries and signatures…"),
                        timeout=120)

    def _show(self, entries) -> None:
        self.loaded = True
        self.entries = entries
        rows, tips = [], []
        for e in entries:
            sig = e.signature or {}
            sig_text = t(SIG_TEXT.get(sig.get("status") or "unknown", "Unknown"))
            if sig.get("signer"):
                sig_text += f" ({sig['signer']})"
            notes = [t(p) for p in e.problems] + [t(n) for n in e.notes]
            rows.append([e.name, e.publisher or "—",
                         (t("Enabled"), "ok") if e.enabled else (t("Disabled"), "pending"),
                         (sig_text, "ok" if sig.get("status") == "valid" else
                          ("fail" if sig.get("status") == "invalid" else None)),
                         t(SOURCE_LABELS.get(e.source, e.source)),
                         ("; ".join(notes), "warn") if e.problems else "; ".join(notes)])
            tips.append([e.command, None, None, sig.get("detail"), None, "\n".join(notes)])
        fill_table(self.table, rows, list(range(len(entries))), tips)
        self._history()
        self._selected()

    def _history(self) -> None:
        hist = self.page.svc.startup.history()
        rows = [[h["created_at"][:16].replace("T", " "),
                 t("Disabled") if h["kind"] == "startup.disable" else t("Enabled"), h["detail"] or h["target"],
                 t({"done": "Done", "failed": "Failed", "undone": "Restored", "planned": "Started"}.get(h["state"],
                                                                                                 h["state"]))]
                for h in hist]
        fill_table(self.hist, rows, [h["id"] for h in hist])

    def _current(self):
        i = selected_data(self.table)
        return self.entries[i] if isinstance(i, int) and 0 <= i < len(self.entries) else None

    def _selected(self) -> None:
        e = self._current()
        self.b_disable.setEnabled(bool(e and e.enabled and not e.protected))
        self.b_enable.setEnabled(bool(e and not e.enabled))
        self.b_open.setEnabled(bool(e and e.path and os.path.exists(e.path)))
        if e is None:
            self.detail.setText(t("Select an entry to see its command line."))
            return
        text = t("Command: {c}", c=e.command)
        if e.protected:
            text += "\n" + t("Not disabled by AKENO: {why}", why=t(e.protected_reason))
        if e.needs_admin:
            text += "\n" + t("For all users: changing it needs administrator rights.")
        self.detail.setText(text)

    def _toggle(self, enable: bool) -> None:
        e = self._current()
        if e is None:
            return
        what = t("Enable {name}?", name=e.name) if enable else t("Disable {name}?", name=e.name)
        detail = (t("Windows will start it again when you sign in.") if enable else
                  t("Windows will not start it when you sign in. The program stays installed and you can enable it "
                    "again here or in Task Manager."))
        if e.needs_admin:
            detail += "\n\n" + t("This entry belongs to all users: Windows will ask for administrator rights.")
        if not confirm(self, t("Startup"), what, detail, t("Enable") if enable else t("Disable")):
            return
        self.task.start(lambda ctx: self.page.svc.startup.set_enabled(e, enable), lambda _: self.refresh(),
                        lambda exc: info(self, t("Startup"), t("Nothing was changed."), t(str(exc)), error=True),
                        text=t("Changing the startup entry…"), cancellable=False)

    def _restore(self) -> None:
        aid = selected_data(self.hist)
        if aid is None:
            return
        if not confirm(self, t("Startup"), t("Restore the state before this change?"),
                       t("AKENO writes back the exact value Windows had before the change."), t("Restore")):
            return
        self.task.start(lambda ctx: self.page.svc.startup.restore(aid), lambda _: self.refresh(),
                        lambda exc: info(self, t("Startup"), t("Nothing was changed."), t(str(exc)), error=True),
                        text=t("Restoring…"), cancellable=False)

    def _open(self) -> None:
        e = self._current()
        if e and e.path:
            commands.open_location(e.path, select=True)


# -- Crash Detective ------------------------------------------------------------------------------------------------
class CrashTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Crashes, blue screens and driver resets"), "warn")
        c.body.addWidget(label(t("Crash Detective reads Windows' Application and System logs: program crashes and "
                                 "hangs, blue screens, unexpected restarts, graphics driver resets and hardware "
                                 "errors. Drivers or updates installed shortly before are shown as a time "
                                 "relation, not as the cause."), "dim", wrap=True))
        self.period = QComboBox()
        self.period.setAccessibleName(t("Period"))
        for days, text in PERIODS:
            self.period.addItem(t(text), days)
        self.period.setCurrentIndex(1)
        self.btn = button(t("Analyse"), "search", "primary", on_click=self.run)
        c.body.addWidget(_flow(self.period, self.btn))
        self.task = TaskBar(page.ctl, "doctor:crashes")
        c.body.addWidget(self.task)
        lay.addWidget(c)
        r = Card(t("Results"), "eye")
        self.findings = FindingList(page.link)
        r.body.addWidget(self.findings)
        self.empty = EmptyState("warn", t("No analysis yet"), t("Analyse reads the event logs of this PC."))
        r.body.addWidget(self.empty)
        lay.addWidget(r)
        g = Card(t("All program crashes in the period"), "layers")
        self.table = make_table([t("Program"), t("Faulting module"), t("Crashes"), t("Last")], 180)
        g.body.addWidget(self.table)
        self.dumps = label("", "muted", wrap=True)
        g.body.addWidget(self.dumps)
        lay.addWidget(g)
        lay.addStretch(1)
        show_stored("doctor.crashes", lambda: page.svc.last("crashes"), self._show, self.task)

    def run(self) -> None:
        days = int(self.period.currentData())
        self.task.start(lambda ctx: self.page.svc.crashes(days, ctx), self._show, text=t("Reading event logs…"),
                        timeout=180)

    def _show(self, rep) -> None:
        self.empty.hide()
        self.findings.set_findings(rep.findings, rep.errors)
        groups = rep.data.get("crash_groups") or []
        fill_table(self.table, [[g["app"], g["module"], str(g["count"]), (g.get("last") or "")[:16].replace("T", " ")]
                                for g in groups], tips=[[g.get("app_path"), g.get("module_path")] for g in groups])
        d = rep.data.get("minidumps") or {}
        if d.get("files"):
            self.dumps.setText(t("{n} minidump files in {folder}.", n=len(d["files"]), folder=d.get("folder")))
        elif d.get("note"):
            self.dumps.setText(t("Minidumps: {note}", note=t(d["note"])))


# -- Hardware Health ----------------------------------------------------------------------------------------------
class HardwareTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Drive health"), "database")
        c.body.addWidget(label(t("What each drive reports about itself (SMART / NVMe health through Windows). AKENO "
                                 "repeats the drive's own statements and never predicts a failure by itself."),
                               "dim", wrap=True))
        self.btn = button(t("Check drives"), "search", "primary", on_click=self.run)
        self.btn_admin = button(t("Read details as administrator…"), "shield", on_click=self.run_admin)
        self.btn_admin.setVisible(False)
        c.body.addWidget(_flow(self.btn, self.btn_admin))
        self.task = TaskBar(page.ctl, "doctor:hardware")
        c.body.addWidget(self.task)
        self.table = make_table([t("Drive"), t("Type"), t("Health"), t("Temperature"), t("Wear"), t("Errors"),
                                 t("Hours on")], 150)
        c.body.addWidget(self.table)
        self.note = label("", "muted", wrap=True)
        c.body.addWidget(self.note)
        lay.addWidget(c)
        row = ResponsiveRow(spacing=14)
        m = Card(t("Memory use"), "layers")
        self.mem_grid = KeyValueGrid(160)
        m.body.addWidget(self.mem_grid)
        self.mem_top = make_table([t("Process"), t("Memory")], 160)
        m.body.addWidget(self.mem_top)
        row.add(m, 1)
        tc = Card(t("Temperatures and cooling"), "gauge")
        self.temp_grid = KeyValueGrid(200)
        tc.body.addWidget(self.temp_grid)
        row.add(tc, 1)
        lay.addWidget(row)
        r = Card(t("Findings"), "eye")
        self.findings = FindingList(page.link, t("Check the drives to see their findings."))
        r.body.addWidget(self.findings)
        lay.addWidget(r)
        lay.addStretch(1)
        show_stored("doctor.hardware", lambda: page.svc.last("hardware"), self._show, self.task)

    def run(self) -> None:
        self.task.start(self.page.svc.hardware, self._show, text=t("Reading drive health…"), timeout=180)

    def run_admin(self) -> None:
        if not confirm(self, t("Drive health"), t("Read the reliability counters with administrator rights?"),
                       t("Windows asks for administrator rights. A hidden PowerShell only reads the drives' "
                         "counters into a file in AKENO's folder; nothing is changed."), t("Continue")):
            return
        self.task.start(self.page.svc.read_drives_as_admin,
                        lambda rep: self._show(rep) if rep is not None else self.task.show_message(
                            t("Administrator rights were not granted; nothing was read.")),
                        text=t("Waiting for Windows…"), timeout=240, cancellable=False)

    def _show(self, rep) -> None:
        rows = []
        for d in rep.data.get("drives") or []:
            temp = f"{d['temperature']} °C" if isinstance(d.get("temperature"), (int, float)) and d["temperature"] else "—"
            wear = f"{d['wear']} %" if isinstance(d.get("wear"), (int, float)) else "—"
            errs = [str(d[k]) for k in ("read_errors", "write_errors") if isinstance(d.get(k), (int, float))]
            hours = str(d["power_on_hours"]) if isinstance(d.get("power_on_hours"), (int, float)) else "—"
            health = d.get("health") or "—"
            state = {"Healthy": "ok", "Warning": "warn", "Unhealthy": "fail"}.get(health)
            rows.append([d.get("name") or "?", " ".join(x for x in (d.get("media"), d.get("bus")) if x) or "—",
                         (t(health), state), temp, wear, " / ".join(errs) or "—", hours])
        fill_table(self.table, rows)
        need_admin = bool(rep.data.get("counters_need_admin")) and not rep.data.get("elevated")
        self.btn_admin.setVisible(need_admin and os.name == "nt")
        self.note.setText(t("Temperature, wear and error counters need administrator rights on this PC.")
                          if need_admin else "")
        mem = rep.data.get("memory") or {}
        rows = [(t("In use"), f"{human_bytes(mem.get('used'))} / {human_bytes(mem.get('total'))}"
                 + (f"  ({mem['percent']:.0f} %)" if mem.get("percent") is not None else ""))]
        if mem.get("commit_limit"):
            rows.append((t("Committed"), f"{human_bytes(mem.get('commit'))} / {human_bytes(mem.get('commit_limit'))}"))
        if mem.get("paging") is not None:
            rows.append((t("Paging"), f"{float(mem['paging']):.0f} " + t("pages/s")))
        if mem.get("swap_total"):
            rows.append((t("Page file in use"), f"{human_bytes(mem.get('swap_used'))} / {human_bytes(mem.get('swap_total'))}"))
        self.mem_grid.set_rows(rows)
        fill_table(self.mem_top, [[p.get("name") or "?", p.get("text") or ""] for p in mem.get("top") or []])
        temps = [(z["name"], f"{z['celsius']:.0f} °C · {t(z['source'])}") for z in rep.data.get("temperatures") or []]
        cpu_t = rep.data.get("cpu_temperature") or {}
        if not cpu_t.get("value"):
            # the note is a translatable message stored as JSON ({"t": template, "p": values}, model.enc)
            temps.append((t("Processor"), t(dec(cpu_t.get("note")) or "Not available on this PC.")))
        for d in rep.data.get("drives") or []:
            if isinstance(d.get("temperature"), (int, float)) and d["temperature"]:
                temps.append((d.get("name") or "?", f"{d['temperature']} °C · " + t("drive")))
        self.temp_grid.set_rows(temps or [(t("Temperatures"), t("No temperature sensor reported."))])
        self.findings.set_findings(rep.findings, rep.errors)


# -- Guided Repair -------------------------------------------------------------------------------------------------
class RepairTab(QWidget):
    def __init__(self, page: DoctorPage):
        super().__init__()
        self.page = page
        self.broken = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(label(t("Windows' own repair tools, explained before they run. Steps marked as recommended "
                              "have a reason from the last analyses; the others are available but not needed by "
                              "what AKENO found."), "dim", wrap=True))
        self.task = TaskBar(page.ctl, "doctor:repair")
        lay.addWidget(self.task)
        self.steps_box = QVBoxLayout()
        self.steps_box.setSpacing(10)
        lay.addLayout(self.steps_box)
        sc = Card(t("Broken shortcuts"), "link")
        sc.body.addWidget(label(t("Shortcuts on the desktop and in the Start menu whose program no longer exists. "
                                  "Only your own shortcuts can be moved to the Recycle Bin; shortcuts for all users "
                                  "are listed only."), "dim", wrap=True))
        self.b_find = button(t("Find broken shortcuts"), "search", on_click=self.find)
        self.b_recycle = button(t("Move selected to the Recycle Bin…"), "trash", on_click=self.recycle)
        self.b_recycle.setEnabled(False)
        sc.body.addWidget(_flow(self.b_find, self.b_recycle))
        self.table = make_table([t("Shortcut"), t("Missing target"), t("Folder")], 140)
        self.table.itemSelectionChanged.connect(lambda: self.b_recycle.setEnabled(
            bool(self._sel() and self._sel().get("own"))))
        sc.body.addWidget(self.table)
        lay.addWidget(sc)
        lay.addStretch(1)
        self._steps()

    def on_show(self) -> None:
        self._steps()

    def _steps(self) -> None:
        clear_layout(self.steps_box)
        rec: dict = {}                     # reasons from the saved analyses; the steps are shown without them too
        show_stored("doctor.repair", self.page.svc.repair_recommendations, rec.update, self.task)
        for s in STEPS:
            box = SubCard()
            head = QHBoxLayout()
            head.addWidget(label(t(s.title), "h3", wrap=True), 1)
            if s.id in rec:
                head.addWidget(StatusPill(t("Recommended"), "warn"))
            if s.admin:
                head.addWidget(StatusPill(t("Administrator"), "info"))
            box.lay.addLayout(head)
            box.lay.addWidget(label(t(s.what), wrap=True))
            box.lay.addWidget(label(t("Effect: {e}", e=t(s.effect)) + "  ·  " + t("Duration: {d}", d=t(s.duration)),
                                    "muted", wrap=True))
            if s.id in rec:
                box.lay.addWidget(label(t("Why now: {why}", why=t(rec[s.id])), "dim", wrap=True))
            if s.module:
                module, _, target = s.module.partition(":")
                b = button(t("Open"), "chevron-right", on_click=lambda _=False, m=module, x=target: self.page.link(m, x))
            elif s.id == "shortcuts":
                b = button(t("Find broken shortcuts"), "search", on_click=self.find)
            else:
                b = button(t("Run…"), "play", on_click=lambda _=False, step=s: self.run(step))
                b.setEnabled(os.name == "nt")
                if os.name != "nt":
                    b.setToolTip(t("Needs Windows."))
            box.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
            self.steps_box.addWidget(box)

    def run(self, step) -> None:
        detail = t(step.what) + "\n\n" + t("Effect: {e}", e=t(step.effect)) + "\n" + \
            t("Windows asks for administrator rights. The tool runs in its own window; close it when it has finished.")
        if not confirm(self, t(step.title), t("Run {title}?", title=t(step.title)), detail, t("Run")):
            return
        self.task.start(lambda ctx: self.page.svc.run_repair(step.id, ctx),
                        lambda res: self.task.show_message(t(res["text"]), error=not res["ok"]),
                        text=t("Running in its own window…"), cancellable=True)

    def find(self) -> None:
        self.task.start(self.page.svc.broken_shortcuts, self._found, text=t("Looking for broken shortcuts…"),
                        timeout=120)

    def _found(self, items) -> None:
        self.broken = items
        fill_table(self.table, [[os.path.basename(i["path"]), i["target"], t(i["folder"])] for i in items],
                   list(range(len(items))), [[i["path"], None, None] for i in items])
        self.task.show_message(t("{n} broken shortcuts found.", n=len(items)) if items else
                               t("No broken shortcuts found."))

    def _sel(self):
        i = selected_data(self.table)
        return self.broken[i] if isinstance(i, int) and 0 <= i < len(self.broken) else None

    def recycle(self) -> None:
        item = self._sel()
        if not item or not item.get("own"):
            return
        if not confirm(self, t("Broken shortcuts"), t("Move this shortcut to the Recycle Bin?"),
                       item["path"] + "\n\n" + t("You can restore it from the Recycle Bin."), t("Move")):
            return
        self.task.start(lambda ctx: self.page.svc.recycle_shortcuts([item]),
                        lambda res: (self.find(), None), text=t("Moving…"), cancellable=False)
