"""Security Center: the protection status Windows reports, Microsoft Defender's scans and history, a security check
for mods and downloads, and a review of running programs. AKENO is no antivirus of its own; it shows what the
antivirus says and leaves every action on threats to it."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.system import commands
from akeno.system.model import Level, human_bytes, human_duration
from akeno.system.securitycenter import filecheck
from akeno.system.securitycenter.service import URIS
from akeno.ui.system_ui import (LEVEL_TEXT, FindingList, TabbedPage, TaskBar, export_button, fill_table, make_table,
                                open_link, selected_data)
from akeno.ui.widgets import (Card, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, ResponsiveRow, StatTile,
                              StatusPill, button, confirm, info, label)

TABS = (("overview", "Overview"), ("scan", "Defender scans"), ("files", "Check files"),
        ("processes", "Running programs"), ("history", "Threat history"))
STATE_TEXT = {"good": "OK", "poor": "Problem", "snoozed": "Paused", "not_monitored": "Not monitored",
              "unknown": "Unknown", "on": "On", "off": "Off", "expired": "Expired"}
STATE_LEVEL = {"good": "ok", "on": "ok", "poor": "fail", "off": "fail", "expired": "fail", "snoozed": "warn",
               "not_monitored": "warn"}
SCAN_TEXT = {"quick": "Quick scan", "full": "Full scan", "custom": "Custom scan"}
RESULT_TEXT = {"clean": "No threats found", "threats": "Threats found", "error": "Failed",
               "cancelled": "Cancelled", "timed_out": "Time limit reached", "unavailable": "Not available"}
RESULT_LEVEL = {"clean": "ok", "threats": "fail", "error": "warn", "cancelled": "info", "timed_out": "warn",
                "unavailable": "unknown"}
THREAT_STATUS_TEXT = {"detected": "Detected (action needed)", "cleaned": "Cleaned", "quarantined": "Quarantined",
                      "removed": "Removed", "allowed": "Allowed by you", "blocked": "Blocked",
                      "quarantine_failed": "Quarantine failed", "remove_failed": "Removal failed",
                      "allow_failed": "Allow failed", "abandoned": "Abandoned", "block_failed": "Block failed",
                      "unknown": "Unknown"}
SEVERITY_TEXT = {"low": "Low", "moderate": "Moderate", "high": "High", "severe": "Severe", "unknown": "Unknown"}
LOCATION_TEXT = {"windows": "Windows", "program_files": "Programs", "temp": "Temporary folder",
                 "downloads": "Downloads", "appdata": "App data", "shared": "Shared folder", "other": "Other"}


def _flow(*widgets) -> QWidget:
    row = FlowLayout(spacing=8)
    for w in widgets:
        row.addWidget(w)
    holder = QWidget()
    holder.setLayout(row)
    return holder


def _when(iso: str | None) -> str:
    if not iso:
        return "—"
    return str(iso).replace("T", " ")[:16]


class SecurityPage(TabbedPage):
    def __init__(self, ctl, window):
        super().__init__(t("Security Center"), t("Shows what Windows Security and your antivirus report, starts "
                                                 "Microsoft Defender's own scans and checks mods and downloads "
                                                 "before you use them. AKENO is not an antivirus: it never "
                                                 "switches protection off, adds no exclusions and deletes nothing."))
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.svc = self.s.system_tools.security
        self.tabs_w = {"overview": OverviewTab(self), "scan": ScanTab(self), "files": FilesTab(self),
                       "processes": ProcessesTab(self), "history": HistoryTab(self)}
        for key, title in TABS:
            self.add_tab(key, t(title), self.tabs_w[key])

    def link(self, module: str, target: str = "") -> None:
        if module == "security":
            self.show_target(target)
        else:
            open_link(self.win, module, target)

    def show_target(self, target: str) -> None:
        key, _, arg = target.partition(":")
        if key == "uri":
            commands.open_uri(URIS.get(arg, URIS["home"]))
        elif key == "file" and arg:
            self.show_tab("files")
            self.tabs_w["files"].check([arg])
        elif key == "defender-file" and arg:
            self.show_tab("scan")
            self.tabs_w["scan"].custom(arg)
        elif key == "scan":
            self.show_tab("scan")
            if arg in ("quick", "full"):
                self.tabs_w["scan"].run(arg)
        elif key == "update":
            self.show_tab("scan")
            self.tabs_w["scan"].update()
        else:
            self.show_tab(key or "overview")

    def on_show(self) -> None:
        self.tab_shown(self.current or "overview")

    def tab_shown(self, key: str) -> None:
        w = self.tabs_w.get(key)
        if w is not None and hasattr(w, "on_show"):
            w.on_show()


# -- overview ------------------------------------------------------------------------------------------------------
class OverviewTab(QWidget):
    def __init__(self, page: SecurityPage):
        super().__init__()
        self.page = page
        self.loaded_at = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(button(t("Refresh"), "refresh", on_click=self.refresh))
        top.addWidget(button(t("Open Windows Security"), "shield", "ghost",
                             on_click=lambda: commands.open_uri(URIS["home"])))
        self.last: dict = {}
        top.addWidget(export_button(self, page.svc.store, "security", lambda: {"overview": self.last}))
        self.task = TaskBar(page.ctl, "security:overview")
        top.addWidget(self.task, 1)
        top.addStretch(0)
        lay.addLayout(top)
        tiles = Card(t("Protection"), "shield")
        row = QHBoxLayout()
        self.t_av, self.t_fw = StatTile(t("Virus protection")), StatTile(t("Firewall"))
        self.t_sig, self.t_scan = StatTile(t("Virus definitions")), StatTile(t("Last quick scan"))
        for w in (self.t_av, self.t_fw, self.t_sig, self.t_scan):
            row.addWidget(w)
        tiles.body.addLayout(row)
        self.note = label("", "dim", wrap=True)
        tiles.body.addWidget(self.note)
        lay.addWidget(tiles)
        r = ResponsiveRow(spacing=14)
        prod = Card(t("Registered security products"), "layers")
        self.products = make_table([t("Product"), t("Area"), t("State"), t("Definitions")], 120)
        prod.body.addWidget(self.products)
        r.add(prod, 3)
        fw = Card(t("Windows Firewall profiles"), "lock")
        self.fw = KeyValueGrid(120)
        fw.body.addWidget(self.fw)
        r.add(fw, 2)
        lay.addWidget(r)
        d = Card(t("Assessment"), "eye")
        self.findings = FindingList(page.link, t("Refresh to read the protection status."))
        d.body.addWidget(self.findings)
        lay.addWidget(d)
        dl = Card(t("Download protection"), "download")
        dl.body.addWidget(label(t("Before AKENO installs a downloaded component, the file has already passed its "
                                  "SHA-256 and signature checks. If Microsoft Defender is available it then checks "
                                  "the file as well (report only, nothing is changed). A detection stops the "
                                  "installation; without a usable antivirus the installation continues with the "
                                  "checks above. AKENO never adds antivirus exclusions."), "dim", wrap=True))
        self.downloads = make_table([t("Time"), t("File"), t("Result")], 100)
        dl.body.addWidget(EmptyAware(self.downloads, EmptyState("download", t("No downloads checked yet"),
                                                                t("Checks appear here after AKENO downloads a "
                                                                  "component.")), 120))
        lay.addWidget(dl)
        lay.addStretch(1)

    def on_show(self) -> None:
        if time.time() - self.loaded_at > 30 and not self.task.running:
            self.refresh()

    def refresh(self) -> None:
        self.task.start(self.page.svc.overview, self._show, text=t("Asking Windows Security…"), timeout=120)

    def _show(self, rep) -> None:
        self.loaded_at = time.time()
        self.last = rep.to_dict()
        st = rep.data.get("state", {})
        health = st.get("health") or {}
        d = st.get("defender") or {}
        avs = st.get("antivirus") or []
        on = [p.get("name") for p in avs if p.get("state") == "on"]
        self.t_av.set(t(STATE_TEXT.get(health.get("antivirus") or "", "")) or (", ".join(on) or "—"))
        self.t_fw.set(t(STATE_TEXT.get(health.get("firewall") or "", "")) or "—")
        age = d.get("signature_age_days")
        self.t_sig.set("—" if not d else t("{n} days old", n=age) if age is not None else "—")
        quick = d.get("quick_scan_age_days")
        self.t_scan.set("—" if not d else t("{n} days ago", n=quick) if quick is not None else t("never"))
        ok, reason = self.page.svc.defender_ready(d or None)
        self.note.setText(t("Active antivirus: {names}", names=", ".join(on)) + ("" if ok else "  ·  " + t(reason))
                          if on else (t(reason) if not ok else ""))
        rows = []
        for area, items in (("Antivirus", avs), ("Firewall", st.get("firewall_products") or [])):
            for p in items:
                state = p.get("state") or "unknown"
                rows.append([p.get("name") or "?", t(area), (t(STATE_TEXT.get(state, state)),
                                                             STATE_LEVEL.get(state, "unknown")),
                             {"up_to_date": t("Up to date"), "out_of_date": t("Out of date")}.get(
                                 p.get("signature"), "—")])
        fill_table(self.products, rows)
        profiles = st.get("firewall") or []
        self.fw.set_rows([(p.get("name") or "?", StatusPill(t("On") if p.get("enabled") else t("Off"),
                                                            "ok" if p.get("enabled") else "fail"))
                          for p in profiles] or [(t("Profiles"), t("not available"))])
        self.findings.set_findings(rep.sorted(), rep.errors)
        dl = rep.data.get("downloads") or []
        fill_table(self.downloads, [[time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("created_at") or 0)),
                                     e.get("label") or "—",
                                     ({"clean": t("Nothing found"), "threats": t("Blocked: {names}", names=", ".join(
                                         e.get("threats") or [])), "not_checked": t("Not checked")}.get(
                                         e.get("status"), t("Check failed")),
                                      {"clean": "ok", "threats": "fail"}.get(e.get("status"), "unknown"))]
                                    for e in dl], tips=[[None, None, e.get("detail")] for e in dl])


# -- Defender scans ------------------------------------------------------------------------------------------------
class ScanTab(QWidget):
    def __init__(self, page: SecurityPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Microsoft Defender"), "shield")
        c.body.addWidget(label(t("These are Microsoft Defender's own scans, the same as in Windows Security. "
                                 "Defender handles what it finds according to its settings and lists it in "
                                 "Protection history. A full scan can take an hour or more; you can keep using "
                                 "the PC."), "dim", wrap=True))
        self.ready = label("", "muted", wrap=True)
        c.body.addWidget(self.ready)
        self.b_quick = button(t("Quick scan"), "play", "primary", on_click=lambda: self.run("quick"))
        self.b_full = button(t("Full scan"), "hdd", on_click=lambda: self.run("full"))
        self.b_custom = button(t("Scan a folder…"), "folder", on_click=self._pick)
        self.b_update = button(t("Update virus definitions"), "download", "ghost", on_click=self.update)
        c.body.addWidget(_flow(self.b_quick, self.b_full, self.b_custom, self.b_update))
        self.task = TaskBar(page.ctl, "security:scan", t("Stop scan"))
        c.body.addWidget(self.task)
        self.result = label("", "", wrap=True)
        c.body.addWidget(self.result)
        lay.addWidget(c)
        h = Card(t("Scans started from AKENO"), "clock")
        self.table = make_table([t("Started"), t("Scan"), t("Target"), t("Result"), t("Duration")], 160)
        h.body.addWidget(EmptyAware(self.table, EmptyState("shield", t("No scans yet"),
                                                           t("Scans you start here are listed with their "
                                                             "result.")), 180))
        lay.addWidget(h)
        lay.addStretch(1)

    def on_show(self) -> None:
        self._history()
        self.page.ctl.run("security:ready", lambda: self.page.svc.defender_ready(), self._ready, lambda e: None,
                          silent=True)

    def _ready(self, out) -> None:
        ok, reason = out
        for b in (self.b_quick, self.b_full, self.b_custom, self.b_update):
            b.setEnabled(ok)
        self.ready.setText(t("Microsoft Defender is ready for scans.") if ok else t(reason) + " " +
                           t("Scans and updates are then done in your antivirus."))

    def _pick(self) -> None:
        folder = self.choose_folder()
        if folder:
            self.custom(folder)

    def choose_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, t("Folder to scan with Microsoft Defender"))

    def custom(self, path: str) -> None:
        if not confirm(self, t("Scan with Microsoft Defender"),
                       t("Microsoft Defender scans {path}. If it finds a threat, Defender handles it according to "
                         "its settings (usually quarantine) and lists it in Protection history.", path=path)):
            return
        self.run("custom", path)

    def run(self, kind: str, target: str | None = None) -> None:
        if kind == "full" and not confirm(self, t("Full scan"),
                                          t("A full scan checks every file and can take an hour or more. It runs "
                                            "in the background and can be stopped.")):
            return
        self.result.setText("")
        self.task.start(lambda ctx: self.page.svc.scan(kind, target, ctx), self._done,
                        text=t("{scan} running…", scan=t(SCAN_TEXT[kind])), timeout=None)

    def _done(self, res) -> None:
        text = t(RESULT_TEXT.get(res.status, res.status))
        if res.status == "threats":
            names = ", ".join(x.get("name", "?") for x in res.threats) or t("{n} threats", n=res.found)
            text += ": " + names + ". " + t("Defender handles them according to its settings; details are in "
                                            "Threat history.")
        elif res.detail:
            text += ": " + t(str(res.detail))
        self.result.setText(text)
        self._history()
        if res.status == "threats":
            self.page.tabs_w["history"].loaded_at = 0.0

    def update(self) -> None:
        self.task.start(self.page.svc.update_signatures, lambda _: self.result.setText(
            t("Virus definitions are up to date.")), lambda e: self.result.setText(
            t("The update did not run: {error}", error=str(e))), text=t("Updating virus definitions…"), timeout=900)

    def _history(self) -> None:
        rows, tips = [], []
        for e in self.page.svc.scans(30):
            rows.append([time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("started_at") or 0)),
                         t(SCAN_TEXT.get(e.get("kind"), e.get("kind") or "")), e.get("target") or "—",
                         (t(RESULT_TEXT.get(e.get("status"), e.get("status") or "")),
                          RESULT_LEVEL.get(e.get("status"), "unknown")),
                         human_duration(e.get("duration_s") or 0)])
            tips.append([None, None, e.get("target"), ", ".join(x.get("name", "") for x in e.get("threats", []))
                         or e.get("detail"), None])
        fill_table(self.table, rows, tips=tips)


# -- file checks ---------------------------------------------------------------------------------------------------
class FilesTab(QWidget):
    def __init__(self, page: SecurityPage):
        super().__init__()
        self.page = page
        self.reports: list[filecheck.FileReport] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Gaming & mod security check"), "bug")
        c.body.addWidget(label(t("Checks what a mod, download or tool really is: file type, SHA-256, vendor, "
                                 "digital signature, download origin, programs inside archives and the verdict of "
                                 "Microsoft Defender (report only). Nothing is opened, extracted, run, changed or "
                                 "uploaded. A missing signature alone is no warning: most mods are unsigned."),
                               "dim", wrap=True))
        c.body.addWidget(_flow(button(t("Check files…"), "file", "primary", on_click=self._pick_files),
                               button(t("Check a folder…"), "folder", on_click=self._pick_folder)))
        self.task = TaskBar(page.ctl, "security:files")
        c.body.addWidget(self.task)
        self.summary = label("", "muted", wrap=True)
        c.body.addWidget(self.summary)
        lay.addWidget(c)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        self.table = make_table([t("File"), t("Type"), t("Signature"), t("Antivirus"), t("Assessment")], 200)
        self.table.itemSelectionChanged.connect(self._select)
        split.addWidget(EmptyAware(self.table, EmptyState("bug", t("Nothing checked yet"),
                                                          t("Choose files or a folder, or use \"Check with "
                                                            "Security Center\" in Installed Mods.")), 220))
        detail = Card(t("Details"), "eye")
        self.kv = KeyValueGrid(170)
        detail.body.addWidget(self.kv)
        self.f_list = FindingList(page.link, t("No warning signs for this file."))
        detail.body.addWidget(self.f_list)
        self.b_open = button(t("Open location"), "folder", "ghost", on_click=self._open)
        self.b_scan = button(t("Scan with Microsoft Defender"), "shield", "ghost", on_click=self._scan)
        detail.body.addWidget(_flow(self.b_open, self.b_scan))
        split.addWidget(detail)
        lay.addWidget(split, 1)
        self._select()

    def _pick_files(self) -> None:
        files = self.choose_files()
        if files:
            self.check(files)

    def _pick_folder(self) -> None:
        folder = self.choose_folder()
        if folder:
            self.check([folder])

    def choose_files(self) -> list[str]:
        return QFileDialog.getOpenFileNames(self, t("Files to check"))[0]

    def choose_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, t("Folder to check"))

    def check(self, paths: list[str]) -> None:
        minecraft = any(p.lower().endswith(".jar") or ".minecraft" in p.lower() or
                        os.path.basename(os.path.normpath(p)).lower() == "mods" for p in paths)
        self.summary.setText("")
        self.task.start(lambda ctx: self.page.svc.check_files(paths, ctx, minecraft), self._show,
                        text=t("Checking… ({n} selected)", n=len(paths)), timeout=1800)

    def _show(self, out) -> None:
        self.reports, errors = out
        rows = []
        for r in self.reports:
            sig = (r.signature or {}).get("status")
            av = (r.av or {}).get("status")
            rows.append([r.name, t(filecheck.KIND_TEXT.get(r.kind, r.kind)),
                         {"valid": t("Signed"), "unsigned": t("Not signed"), "invalid": (t("Broken"), "warn"),
                          "unknown": t("Not checked")}.get(sig, "—"),
                         {"clean": (t("Nothing found"), "ok"), "threats": (t("Threat"), "fail"),
                          "not_checked": t("Not checked"), "error": (t("Check failed"), "warn")}.get(av, "—"),
                         (t(LEVEL_TEXT.get(r.level.value, "")), r.level.value)])
        fill_table(self.table, rows, list(range(len(self.reports))), [[r.path] for r in self.reports])
        bad = sum(1 for r in self.reports if r.level in (Level.WARN, Level.PROBLEM))
        text = t("Files checked: {n}, with warning signs: {bad}.", n=len(self.reports), bad=bad)
        if errors:
            text += " " + " ".join(t(str(e)) for e in errors[:3])
        self.summary.setText(text)
        if self.reports:
            self.table.selectRow(0)

    def _current(self):
        i = selected_data(self.table)
        return self.reports[i] if isinstance(i, int) and 0 <= i < len(self.reports) else None

    def _select(self) -> None:
        r = self._current()
        self.b_open.setEnabled(r is not None)
        self.b_scan.setEnabled(r is not None)
        if r is None:
            self.kv.set_rows([])
            self.f_list.set_findings([])
            return
        self.kv.set_rows([(t(k), t(v) if isinstance(v, str) else v) for k, v in
                          [(t("Path"), r.path)] + filecheck.summary_rows(r)], elide=True)
        self.f_list.set_findings(r.findings, r.errors)

    def _open(self) -> None:
        r = self._current()
        if r is not None:
            commands.open_location(r.path, select=True)

    def _scan(self) -> None:
        r = self._current()
        if r is not None:
            self.page.show_target("defender-file:" + r.path)


# -- running programs ----------------------------------------------------------------------------------------------
class ProcessesTab(QWidget):
    def __init__(self, page: SecurityPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Running programs"), "activity")
        c.body.addWidget(label(t("Looks at the programs running now: name, folder, digital signature and internet "
                                 "connections. A warning needs a concrete reason, for example a program with the "
                                 "name of a Windows component outside the Windows folder. AKENO does not end, pause "
                                 "or delete anything here."), "dim", wrap=True))
        top = QHBoxLayout()
        top.addWidget(button(t("Review running programs"), "eye", "primary", on_click=self.refresh))
        self.task = TaskBar(page.ctl, "security:processes")
        top.addWidget(self.task, 1)
        top.addStretch(0)
        c.body.addLayout(top)
        self.findings = FindingList(page.link, t("Start the review to see the result."))
        c.body.addWidget(self.findings)
        lay.addWidget(c)
        p = Card(t("All programs looked at"), "layers")
        self.table = make_table([t("Program"), t("Folder"), t("Signature"), t("Connections"), t("Memory")], 240)
        p.body.addWidget(EmptyAware(self.table, EmptyState("activity", t("Not reviewed yet"),
                                                           t("The list appears after the review.")), 240))
        lay.addWidget(p)
        lay.addStretch(1)

    def refresh(self) -> None:
        self.task.start(self.page.svc.processes, self._show, text=t("Checking running programs…"), timeout=300)

    def _show(self, rep) -> None:
        self.findings.set_findings(rep.sorted(), rep.errors)
        procs = sorted((p for p in rep.data.get("processes", []) if p.get("exe")),
                       key=lambda p: (not p.get("flags"), (p.get("name") or "").lower()))
        sig_text = {"valid": t("Signed"), "unsigned": t("Not signed"), "invalid": t("Broken"),
                    "unknown": t("Not checked")}
        fill_table(self.table, [[(p["name"], "warn") if p.get("flags") else p["name"],
                                 t(LOCATION_TEXT.get(p.get("location"), "Other")),
                                 sig_text.get((p.get("signature") or {}).get("status"), "—")
                                 if p.get("location") != "windows" else t("Windows"),
                                 str(p.get("connections") or 0), human_bytes(p.get("rss"))] for p in procs],
                   tips=[[p.get("exe"), p.get("exe"), (p.get("signature") or {}).get("signer"), None, None]
                         for p in procs])


# -- threat history ------------------------------------------------------------------------------------------------
class HistoryTab(QWidget):
    def __init__(self, page: SecurityPage):
        super().__init__()
        self.page = page
        self.loaded_at = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Microsoft Defender protection history"), "clock")
        c.body.addWidget(label(t("What Defender detected on this PC and what it did. Restoring or removing "
                                 "quarantined items is done in Windows Security, where Defender asks for the "
                                 "needed confirmation; AKENO only shows the list."), "dim", wrap=True))
        top = QHBoxLayout()
        top.addWidget(button(t("Refresh"), "refresh", on_click=self.refresh))
        top.addWidget(button(t("Open protection history"), "shield", "ghost",
                             on_click=lambda: commands.open_uri(URIS["history"])))
        self.task = TaskBar(page.ctl, "security:history")
        top.addWidget(self.task, 1)
        top.addStretch(0)
        c.body.addLayout(top)
        self.table = make_table([t("Detected"), t("Threat"), t("Severity"), t("Status"), t("Files")], 240)
        c.body.addWidget(EmptyAware(self.table, EmptyState("shield", t("No detections"),
                                                           t("Microsoft Defender reports no threats on this PC, or "
                                                             "another antivirus keeps the history.")), 240))
        self.note = label("", "muted", wrap=True)
        c.body.addWidget(self.note)
        lay.addWidget(c)
        lay.addStretch(1)

    def on_show(self) -> None:
        if time.time() - self.loaded_at > 60 and not self.task.running:
            self.refresh()

    def refresh(self) -> None:
        self.task.start(self.page.svc.history, self._show, self._failed, text=t("Reading protection history…"),
                        timeout=90)

    def _failed(self, exc) -> None:
        self.loaded_at = time.time()
        fill_table(self.table, [])
        self.note.setText(t("Microsoft Defender's history is not available: {error}", error=str(exc)))

    def _show(self, items) -> None:
        self.loaded_at = time.time()
        self.note.setText("")
        fill_table(self.table, [[_when(e.get("time")), e.get("name") or "?",
                                 t(SEVERITY_TEXT.get(e.get("severity"), "Unknown")),
                                 (t(THREAT_STATUS_TEXT.get(e.get("status"), "Unknown")),
                                  "fail" if e.get("unresolved") else "ok"),
                                 ", ".join(e.get("resources") or []) or "—"] for e in items],
                   tips=[[None, None, None, None, "\n".join(e.get("resources") or [])] for e in items])
