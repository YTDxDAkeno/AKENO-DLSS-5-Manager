"""Smart Cleaner: scan, results, choice, preview of the exact files, confirmation, clean-up with progress, report.
Disk Space Analyzer and Smart Maintenance. Nothing is removed without the preview and its confirmation."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLineEdit,
                               QListWidget, QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.system import commands
from akeno.system.cleaner.engine import SKIP_REASONS
from akeno.system.cleaner.rules import GROUP_TITLES, RISK_LOW, RISK_MEDIUM
from akeno.system.cleaner.service import INTERVALS
from akeno.system.model import human_bytes
from akeno.ui import theme
from akeno.ui.system_ui import TabbedPage, TaskBar, fill_table, make_table, open_link, selected_data
from akeno.ui.widgets import (Card, Divider, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, Meter,
                              ResponsiveRow, StatusPill, SubCard, button, clear_layout, confirm, fit_to_screen, info,
                              label)

TABS = (("junk", "Junk and caches"), ("space", "Disk space"), ("maintenance", "Maintenance"))
RISK_TEXT = {"low": "Low risk", "medium": "Check first", "review": "Your decision"}
RISK_STATE = {"low": "ok", "medium": "warn", "review": "info"}
STATUS_TEXT = {"not_found": "Not on this PC", "empty": "Nothing to clean", "app_running": "Close {apps} first",
               "limited": "Scan stopped at its file limit"}


def _flow(*widgets) -> QWidget:
    row = FlowLayout(spacing=8)
    for w in widgets:
        row.addWidget(w)
    holder = QWidget()
    holder.setLayout(row)
    return holder


class CleanerPage(TabbedPage):
    def __init__(self, ctl, window):
        super().__init__(t("Smart Cleaner"), t("Finds files that can go without harm and shows exactly which ones "
                                               "before anything happens. Safety comes before gigabytes: removed "
                                               "files go to the Recycle Bin, and what is in use, changed or "
                                               "outside the confirmed folders stays."))
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.svc = self.s.system_tools.cleaner
        self.junk = JunkTab(self)
        self.space = SpaceTab(self)
        self.maint = MaintenanceTab(self)
        for (key, title), w in zip(TABS, (self.junk, self.space, self.maint)):
            self.add_tab(key, t(title), w)
        # Smart Maintenance: a read-only check when it is due, while AKENO runs anyway (never a removal)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.setInterval(3600_000)
        self.timer.timeout.connect(self.maintenance_tick)
        self.timer.start()
        QTimer.singleShot(90_000, self.maintenance_tick)

    def maintenance_tick(self) -> None:
        if not self.svc.maintenance_due() or self.junk.task.running or self.ctl.is_busy("cleaner:maintenance"):
            return

        def done(out) -> None:
            if out["bytes"] >= 100 * 2 ** 20:
                text = t("Smart Cleaner found {size} that could be cleaned. Open Smart Cleaner to review it.",
                         size=human_bytes(out["bytes"]))
                notifier = getattr(self.win, "notifier", None)
                if notifier is not None:
                    notifier.notify("system", text, once="maintenance-" + time.strftime("%Y-%m-%d"))
                if hasattr(self.win, "statusBar"):
                    self.win.statusBar().showMessage(text, 15000)
        self.ctl.run("cleaner:maintenance", self.svc.maintenance_scan, done, lambda e: None, silent=True)

    def show_target(self, target: str) -> None:
        key, _, arg = target.partition(":")
        if key == "scan":
            self.show_tab("junk")
            if not self.junk.task.running and self.svc.last is None:
                self.junk.scan()
        elif key in ("space", "junk", "maintenance"):
            self.show_tab(key)
            if key == "space" and arg:
                self.space.path.setText(arg)
        else:
            self.show_tab("junk")

    def tab_shown(self, key: str) -> None:
        if key == "maintenance":
            self.maint.refresh()
        elif key == "space":
            self.space.on_show()


# -- junk scan, preview, clean -----------------------------------------------------------------------------------
class JunkTab(QWidget):
    def __init__(self, page: CleanerPage):
        super().__init__()
        self.page = page
        self.checks: dict[str, QCheckBox] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = Card(t("Scan"), "search")
        top.body.addWidget(label(t("AKENO looks only in folders it knows: temporary files, its own leftovers, old "
                                   "logs and reports, browser and launcher caches, graphics shader caches and large "
                                   "files for your review. Browser cookies, sign-ins, passwords and profiles are never "
                                   "part of a category."), "dim", wrap=True))
        self.b_scan = button(t("Scan now"), "search", "primary", on_click=self.scan)
        self.b_clean = button(t("Preview and clean…"), "broom", on_click=self.preview)
        self.b_clean.setEnabled(False)
        top.body.addWidget(_flow(self.b_scan, self.b_clean))
        self.task = TaskBar(page.ctl, "cleaner:junk")
        top.body.addWidget(self.task)
        self.summary = label("", "dim", wrap=True)
        top.body.addWidget(self.summary)
        lay.addWidget(top)
        self.results = QVBoxLayout()
        self.results.setSpacing(14)
        lay.addLayout(self.results)
        self.empty = EmptyState("broom", t("No scan yet"), t("Scan to see what can be cleaned. Nothing is removed "
                                                             "until you confirm a preview."))
        lay.addWidget(self.empty)
        self.report = Card(t("Last clean-up"), "check")
        self.report_grid = KeyValueGrid(200)
        self.report.body.addWidget(self.report_grid)
        self.b_measure = button(t("Measure again in System Doctor"), "stethoscope",
                                on_click=lambda: open_link(page.win, "doctor", "overview"))
        self.report.body.addWidget(self.b_measure, 0, Qt.AlignmentFlag.AlignLeft)
        self.report.hide()
        lay.addWidget(self.report)
        win = Card(t("Windows' own clean-up"), "monitor")
        win.body.addWidget(label(t("Windows Update leftovers, delivery optimisation files and previous Windows "
                                   "installations need administrator rights and are cleaned best by Windows itself. "
                                   "These buttons open Windows' tools; AKENO does not change anything there."),
                                 "dim", wrap=True))
        b1 = button(t("Open Storage settings"), "external", on_click=lambda: commands.open_uri("ms-settings:storagesense"))
        b2 = button(t("Open Disk Cleanup"), "external", on_click=self._cleanmgr)
        for b in (b1, b2):
            b.setEnabled(os.name == "nt")
        win.body.addWidget(_flow(b1, b2))
        lay.addWidget(win)
        lay.addStretch(1)

    def _cleanmgr(self) -> None:
        import subprocess
        exe = commands.system32("cleanmgr.exe")
        if os.path.isfile(exe):
            subprocess.Popen([exe])

    def scan(self) -> None:
        self.b_scan.setEnabled(False)
        self.b_clean.setEnabled(False)
        self.task.start(self.page.svc.scan, self._show, self._failed, text=t("Scanning…"), timeout=900)

    def _failed(self, exc) -> None:
        self.b_scan.setEnabled(True)
        self.task.show_message(str(exc), error=True)

    def _show(self, res) -> None:
        self.b_scan.setEnabled(True)
        self.empty.hide()
        clear_layout(self.results)
        self.checks = {}
        groups: dict[str, list] = {}
        absent = 0
        for c in res.categories:
            if c.status == "not_found":
                absent += 1            # a browser or launcher that is not installed: nothing to show
                continue
            groups.setdefault(c.category.group, []).append(c)
        for group in GROUP_TITLES:
            items = groups.get(group)
            if not items:
                continue
            card = Card(t(GROUP_TITLES[group]), "layers" if group != "shader" else "chip")
            if group == "shader":
                card.body.addWidget(label(t("Not selected by default: after deleting a shader cache, games compile "
                                            "their shaders again and can stutter at first."), "dim", wrap=True))
            for c in items:
                card.body.addWidget(self._row(c))
            self.results.addWidget(card)
        total = res.cleanable_bytes
        self.summary.setText(t("{size} in {n} categories could be cleaned (scan took {s} s).", size=human_bytes(total),
                               n=sum(1 for c in res.categories if c.files and not c.category.review_only),
                               s=res.duration_s)
                             + (" " + t("{n} categories are not on this PC (programs not installed).", n=absent)
                                if absent else ""))
        self._update_clean()

    def _row(self, c) -> QWidget:
        cat = c.category
        box = SubCard()
        head = QHBoxLayout()
        head.setSpacing(10)
        if cat.review_only:
            head.addWidget(label(t(cat.title), "h3", wrap=True), 1)
        else:
            chk = QCheckBox(t(cat.title))
            chk.setChecked(bool(cat.preselect and c.files and not c.running))
            chk.setEnabled(bool(c.files) and not c.running)
            chk.toggled.connect(self._update_clean)
            chk.setStyleSheet("font-weight: 600;")
            self.checks[cat.id] = chk
            head.addWidget(chk, 1)
        head.addWidget(label(f"{c.count} · {human_bytes(c.bytes)}" if c.files else "—", "dim"))
        head.addWidget(StatusPill(t(RISK_TEXT.get(cat.risk, cat.risk)), RISK_STATE.get(cat.risk, "info")))
        box.lay.addLayout(head)
        box.lay.addWidget(label(t(cat.reason), "dim", wrap=True))
        if cat.side_effect:
            box.lay.addWidget(label(t("Afterwards: {what}", what=t(cat.side_effect)), "muted", wrap=True))
        roots = [r for r in cat.roots if os.path.isdir(r)] or list(cat.roots)
        where = label(t("Location: {path}", path="; ".join(roots[:3]) + (" …" if len(roots) > 3 else "")), "muted",
                      wrap=True, selectable=True)
        box.lay.addWidget(where)
        if c.status in STATUS_TEXT and c.status != "ok":
            text = t(STATUS_TEXT[c.status], apps=", ".join(c.running)) if c.status == "app_running" else \
                t(STATUS_TEXT[c.status])
            box.lay.addWidget(StatusPill(text, "warn" if c.status in ("app_running", "limited") else "pending"))
        if cat.review_only and c.files:
            tbl = make_table([t("File"), t("Size"), t("Folder")], 150)
            fill_table(tbl, [[os.path.basename(f.path), human_bytes(f.size), os.path.dirname(f.path)]
                             for f in c.files[:100]], [f.path for f in c.files[:100]])
            box.lay.addWidget(tbl)
            box.lay.addWidget(button(t("Open file location"), "folder", "ghost",
                                     on_click=lambda _=False, tb=tbl: self._open(tb)), 0, Qt.AlignmentFlag.AlignLeft)
        elif c.files:
            b = button(t("Open folder"), "folder", "ghost",
                       on_click=lambda _=False, p=roots[0]: commands.open_location(p))
            box.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def _open(self, tbl) -> None:
        p = selected_data(tbl)
        if p:
            commands.open_location(p, select=True)

    def selected(self) -> list[str]:
        return [cid for cid, chk in self.checks.items() if chk.isChecked() and chk.isEnabled()]

    def _update_clean(self, *_) -> None:
        self.b_clean.setEnabled(bool(self.selected()) and not self.task.running)

    def preview(self) -> None:
        sel = self.selected()
        if not sel:
            return
        pv = self.page.svc.preview(sel)
        dlg = PreviewDialog(self, pv)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        permanent, shaders = dlg.permanent.isChecked(), dlg.shader_ok.isChecked()
        self.b_scan.setEnabled(False)
        self.b_clean.setEnabled(False)
        self.task.start(lambda ctx: self.page.svc.clean(sel, ctx, permanent, shaders), self._cleaned, self._failed,
                        text=t("Cleaning…"), timeout=3600)

    def _cleaned(self, rep) -> None:
        self.b_scan.setEnabled(True)
        rows = [(t("Removed"), t("{n} files, {size}", n=rep.removed, size=human_bytes(rep.removed_bytes))),
                (t("Where to"), t("Deleted permanently") if rep.permanent else
                 t("Recycle Bin (the space is free once you empty it)"))]
        for reason, n in sorted(rep.skipped.items()):
            text = SKIP_REASONS.get(reason) or {"app_running": "the program was running",
                                                "shader_not_confirmed": "shader caches were not confirmed"}.get(reason,
                                                                                                          reason)
            rows.append((t("Skipped"), t("{n} files: {why}", n=n, why=t(text))))
        for drive, before in rep.free_before.items():
            after = rep.free_after.get(drive)
            if after is not None:
                rows.append((t("Free on {d}", d=drive), f"{human_bytes(before)} → {human_bytes(after)}"))
        if rep.cancelled:
            rows.append((t("State"), t("Cancelled: everything removed until then is listed above.")))
        self.report_grid.set_rows(rows)
        self.report.show()
        self.task.show_message(t("Clean-up finished."))
        self.scan()


class PreviewDialog(QDialog):
    """The exact files per category, how they are removed and what cannot be undone. Confirm is the only way on."""

    def __init__(self, parent, pv):
        super().__init__(parent)
        self.setWindowTitle(t("Preview"))
        self.pv = pv
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(label(t("These files will be removed"), "h2"))
        lay.addWidget(label(t("{n} files, {size}. Each file is checked again right before it is removed; a file that "
                              "changed, is in use or moved since the scan stays.", n=pv.count,
                              size=human_bytes(pv.bytes)), "dim", wrap=True))
        self.list = QListWidget()
        for r in pv.rows:
            self.list.addItem(f"■ {t(r['title'])} — {r['count']} · {human_bytes(r['bytes'])}")
            for path in r["sample"]:
                self.list.addItem("    " + path)
            if r["more"]:
                self.list.addItem("    " + t("… and {n} more", n=r["more"]))
        self.list.setMinimumHeight(220)
        lay.addWidget(self.list, 1)
        for b in pv.blocked:
            lay.addWidget(label(t("Not included: {title} – {why}", title=t(b["title"]), why=t(b["why"])), "muted",
                                wrap=True))
        self.permanent = QCheckBox(t("Delete permanently instead of using the Recycle Bin (cannot be undone)"))
        self.permanent.toggled.connect(self._update)
        lay.addWidget(self.permanent)
        self.warn = label(t("Permanently deleted files cannot be restored from the Recycle Bin."), wrap=True)
        self.warn.setStyleSheet(f"color: {theme.RED};")
        self.warn.hide()
        lay.addWidget(self.warn)
        self.shader_ok = QCheckBox(t("I understand that games compile their shaders again and may stutter at first"))
        self.shader_ok.setVisible(pv.needs_shader_confirmation)
        self.shader_ok.toggled.connect(self._update)
        lay.addWidget(self.shader_ok)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button(t("Cancel"), on_click=self.reject))
        self.ok = button(t("Clean {n} files", n=pv.count), "broom", "primary", on_click=self.accept)
        row.addWidget(self.ok)
        lay.addLayout(row)
        fit_to_screen(self, 760, 560, 520, 380)
        self._update()

    def _update(self, *_) -> None:
        self.warn.setVisible(self.permanent.isChecked())
        self.ok.setEnabled(self.pv.count > 0 and (not self.pv.needs_shader_confirmation or self.shader_ok.isChecked()))
        self.ok.setProperty("kind", "danger" if self.permanent.isChecked() else "primary")
        self.ok.style().unpolish(self.ok)
        self.ok.style().polish(self.ok)


# -- disk space ------------------------------------------------------------------------------------------------------
class SpaceTab(QWidget):
    def __init__(self, page: CleanerPage):
        super().__init__()
        self.page = page
        self.loaded = False
        self.report = None
        self.dups = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        drives = Card(t("Drives"), "database")
        self.drive_box = QVBoxLayout()
        drives.body.addLayout(self.drive_box)
        lay.addWidget(drives)

        an = Card(t("What fills a folder"), "search")
        self.path = QLineEdit()
        self.path.setAccessibleName(t("Folder"))
        self.path.setPlaceholderText(t("Folder or drive, e.g. C:\\"))
        b_browse = button(t("Choose…"), "folder", "ghost", on_click=self._browse)
        b_go = button(t("Analyse"), "search", "primary", on_click=self.analyse)
        b_up = button(t("Up"), "chevron-right", "ghost", on_click=self._up)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(b_browse)
        row.addWidget(b_up)
        row.addWidget(b_go)
        an.body.addLayout(row)
        self.task = TaskBar(page.ctl, "cleaner:space")
        an.body.addWidget(self.task)
        self.total = label("", "dim", wrap=True)
        an.body.addWidget(self.total)
        self.bars = QVBoxLayout()
        self.bars.setSpacing(6)
        an.body.addLayout(self.bars)
        an.body.addWidget(label(t("LARGEST FILES"), "overline"))
        self.largest = make_table([t("File"), t("Size"), t("Folder")], 180)
        an.body.addWidget(self.largest)
        an.body.addWidget(_flow(button(t("Open file location"), "folder", "ghost",
                                       on_click=lambda: self._open(self.largest))))
        lay.addWidget(an)

        row2 = ResponsiveRow(spacing=14)
        games = Card(t("Game installations"), "gamepad")
        self.b_games = button(t("Measure game folders"), "search", on_click=self.measure_games)
        games.body.addWidget(self.b_games, 0, Qt.AlignmentFlag.AlignLeft)
        self.games = make_table([t("Game"), t("Size"), t("Folder")], 160)
        games.body.addWidget(self.games)
        games.body.addWidget(button(t("Open folder"), "folder", "ghost", on_click=lambda: self._open(self.games)), 0,
                             Qt.AlignmentFlag.AlignLeft)
        row2.add(games, 1)
        dl = Card(t("Downloads"), "download")
        self.b_dl = button(t("Analyse Downloads"), "search", on_click=self.downloads)
        dl.body.addWidget(self.b_dl, 0, Qt.AlignmentFlag.AlignLeft)
        self.dl_text = label("", "dim", wrap=True)
        dl.body.addWidget(self.dl_text)
        self.dl = make_table([t("File"), t("Size")], 160)
        dl.body.addWidget(self.dl)
        dl.body.addWidget(button(t("Open file location"), "folder", "ghost", on_click=lambda: self._open(self.dl)), 0,
                          Qt.AlignmentFlag.AlignLeft)
        row2.add(dl, 1)
        lay.addWidget(row2)

        dup = Card(t("Duplicate files"), "copy")
        dup.body.addWidget(label(t("Finds byte-identical files of 1 MB or more in the folder above (compared by "
                                   "SHA-256). AKENO only shows them; open the folder and decide which copy you keep."),
                                 "dim", wrap=True))
        self.b_dup = button(t("Find duplicates in this folder"), "search", on_click=self.find_dups)
        dup.body.addWidget(self.b_dup, 0, Qt.AlignmentFlag.AlignLeft)
        self.dup = make_table([t("File"), t("Copies"), t("Size each"), t("Could be freed")], 160)
        dup.body.addWidget(self.dup)
        dup.body.addWidget(button(t("Open file location"), "folder", "ghost", on_click=lambda: self._open(self.dup)),
                           0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(dup)
        lay.addStretch(1)

    def on_show(self) -> None:
        if not self.loaded:
            self.loaded = True
            self._drives()
            if not self.path.text():
                self.path.setText(os.path.expanduser("~"))

    def _drives(self) -> None:
        import psutil
        clear_layout(self.drive_box)
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            if u.total < 1 << 30:
                continue
            line = QHBoxLayout()
            line.addWidget(label(f"{part.mountpoint}  ·  {t('{free} free of {total}', free=human_bytes(u.free), total=human_bytes(u.total))}",
                                 shrink=True), 1)
            line.addWidget(button(t("Analyse"), "search", "ghost",
                                  on_click=lambda _=False, m=part.mountpoint: (self.path.setText(m), self.analyse())))
            self.drive_box.addLayout(line)
            used = u.used / u.total if u.total else 0
            self.drive_box.addWidget(Meter(used, (theme.RED, theme.AMBER) if used > 0.9 else
                                           (theme.VIOLET_DEEP, theme.VIOLET)))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("Choose a folder"), self.path.text() or os.path.expanduser("~"))
        if d:
            self.path.setText(d)

    def _up(self) -> None:
        p = self.path.text().strip()
        if p:
            parent = os.path.dirname(p.rstrip("\\/")) or p
            self.path.setText(parent if parent.endswith(("\\", "/")) or len(parent) > 3 else parent + os.sep)

    def analyse(self, root: str | None = None) -> None:
        root = root or self.path.text().strip()
        if not root or not os.path.isdir(root):
            self.task.show_message(t("This folder does not exist."), error=True)
            return
        self.path.setText(root)
        self.task.start(lambda ctx: self.page.svc.folder(root, ctx), self._show, text=t("Measuring…"), timeout=1800)

    def _show(self, rep) -> None:
        self.report = rep
        self.total.setText(t("{size} in {n} files", size=human_bytes(rep.total), n=rep.files)
                           + (" · " + t("stopped at the file limit") if rep.limited else "")
                           + (" · " + t("{n} entries could not be read or are links", n=len(rep.errors))
                              if rep.errors else ""))
        clear_layout(self.bars)
        top = rep.children[0].bytes if rep.children else 1
        for c in rep.children[:14]:
            row = QHBoxLayout()
            row.setSpacing(10)
            name = button(c.name + (os.sep if c.is_dir else ""), "folder" if c.is_dir else "file", "link",
                          on_click=lambda _=False, p=c.path, d=c.is_dir: self.analyse(p) if d else
                          commands.open_location(p, select=True))
            name.setToolTip(c.path)
            name.setMaximumWidth(320)
            row.addWidget(name)
            m = Meter(c.bytes / top if top else 0, (theme.VIOLET_DEEP, theme.VIOLET))
            row.addWidget(m, 1)
            pct = c.bytes / rep.total * 100 if rep.total else 0
            row.addWidget(label(f"{human_bytes(c.bytes)}  ({pct:.0f} %)", "dim"))
            holder = QWidget()
            holder.setLayout(row)
            self.bars.addWidget(holder)
        fill_table(self.largest, [[os.path.basename(p), human_bytes(s), os.path.dirname(p)] for s, p in rep.largest],
                   [p for _, p in rep.largest])

    def _open(self, tbl) -> None:
        p = selected_data(tbl)
        if p:
            commands.open_location(p, select=os.path.isfile(p))

    def measure_games(self) -> None:
        self.task.start(self.page.svc.games, self._games, text=t("Measuring game folders…"), timeout=1800)

    def _games(self, rows) -> None:
        fill_table(self.games, [[r["name"], human_bytes(r["bytes"]), r["path"]] for r in rows], [r["path"] for r in rows])
        if not rows:
            self.task.show_message(t("No game folders in the library yet."))

    def downloads(self) -> None:
        self.task.start(self.page.svc.downloads, self._downloads, text=t("Measuring Downloads…"), timeout=900)

    def _downloads(self, rep) -> None:
        if rep is None:
            self.dl_text.setText(t("No Downloads folder found."))
            return
        self.dl_text.setText(t("{size} in {n} files in {path}", size=human_bytes(rep.total), n=rep.files,
                               path=rep.root))
        fill_table(self.dl, [[os.path.basename(p), human_bytes(s)] for s, p in rep.largest[:30]],
                   [p for _, p in rep.largest[:30]])

    def find_dups(self) -> None:
        root = self.path.text().strip()
        if not root or not os.path.isdir(root):
            self.task.show_message(t("This folder does not exist."), error=True)
            return
        self.task.start(lambda ctx: self.page.svc.duplicates(root, ctx), self._dups,
                        text=t("Comparing files…"), timeout=3600)

    def _dups(self, out) -> None:
        groups, errors = out
        rows, data = [], []
        for g in groups[:200]:
            for p in g.paths:
                rows.append([p, str(len(g.paths)), human_bytes(g.size), human_bytes(g.wasted)])
                data.append(p)
        fill_table(self.dup, rows, data)
        self.task.show_message(t("{n} groups of identical files.", n=len(groups)) if groups else
                               t("No duplicate files found."))


# -- maintenance -----------------------------------------------------------------------------------------------------
class MaintenanceTab(QWidget):
    def __init__(self, page: CleanerPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Smart Maintenance"), "clock")
        c.body.addWidget(label(t("Off by default. When on, AKENO checks for junk at the chosen interval while it is "
                                 "running anyway and tells you how much could go. It never removes anything by "
                                 "itself, needs no start with Windows and no administrator rights; you decide in "
                                 "the preview."), "dim", wrap=True))
        self.interval = QComboBox()
        self.interval.setAccessibleName(t("Check for junk"))
        for days, text in INTERVALS.items():
            self.interval.addItem(t(text), days)
        self.interval.currentIndexChanged.connect(self._changed)
        from akeno.ui.widgets import form_row
        c.body.addWidget(form_row(t("Check for junk"), self.interval))
        self.last = label("", "muted")
        c.body.addWidget(self.last)
        lay.addWidget(c)
        h = Card(t("Clean-ups"), "restore")
        self.hist = make_table([t("When"), t("Category"), t("Result")], 180)
        h.body.addWidget(EmptyAware(self.hist, EmptyState("broom", t("Nothing cleaned yet"),
                                                          t("Clean-ups you confirm are listed here.")), 180))
        h.body.addWidget(label(t("Files moved to the Recycle Bin can be restored from there until you empty it."),
                               "muted", wrap=True))
        lay.addWidget(h)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        m = self.page.svc.maintenance()
        self.interval.blockSignals(True)
        idx = self.interval.findData(m["interval_days"])
        self.interval.setCurrentIndex(max(0, idx))
        self.interval.blockSignals(False)
        self.last.setText(t("Last check: {when}", when=time.strftime("%Y-%m-%d %H:%M", time.localtime(m["last_scan"])))
                          if m["last_scan"] else t("No check yet."))
        rows = [[a["created_at"][:16].replace("T", " "), a["target"], a["detail"]]
                for a in self.page.svc.store.actions("cleaner")]
        fill_table(self.hist, rows)

    def _changed(self, *_) -> None:
        self.page.svc.set_maintenance(int(self.interval.currentData()))
