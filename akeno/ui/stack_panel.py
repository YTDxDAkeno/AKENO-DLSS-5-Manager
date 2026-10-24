"""The game page: pick a DLSS 5 method and versions, install, choose a preset, launch, switch or restore.

Everything slow (state evaluation, downloads, file operations) runs on worker threads through the
controller; this widget only renders results. A newer refresh request supersedes an older result.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
                               QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from akeno.core.models import GraphicsApi
from akeno.i18n import msg, t
from akeno.security.downloads import CancelToken
from akeno.stack import configs
from akeno.stack.errors import explain
from akeno.ui import art, theme
from akeno.ui.widgets import Card, Clickable, Divider, FlowLayout, KeyValueGrid, Spinner, StatusPill, button, \
    clear_layout, confirm, game_cover, info, label, scaled

LAUNCHERS = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "ubisoft": "Ubisoft Connect", "ea": "EA app",
             "xbox": "Xbox / Microsoft Store", "manual": "Added manually", "fivem": "FiveM", "battlenet": "Battle.net"}
CHANNEL = {"stable": "Stable", "prerelease": "Pre-release", "community": "Community build", "local": "On this PC",
           "unreviewed": "New upstream release"}
ACTION_TEXT = {"add": "New", "update": "Update", "remove": "Remove", "restore": "Put back", "replace": "Replace",
               "disable": "Disable", "keep": "Keep", "config": "Settings"}
LEVEL_STATE = {"block": "fail", "warn": "warn", "info": "info"}


def error_text(exc: BaseException) -> str:
    e = explain(exc)
    return (t(e.message) + ("\n" + t(e.remedy) if e.remedy else "")).strip()


def mib(n: int | None) -> str:
    return f"{(n or 0) / 1048576:.0f} MB" if (n or 0) >= 1048576 else f"{(n or 0) / 1024:.0f} KB"


def version_text(v, gpu_gen: str | None) -> str:
    parts = [v.display, t(CHANNEL.get(v.channel, v.channel))]
    if v.signed == "nvidia":
        parts.append(t("signed by NVIDIA"))
    elif v.signed == "modified":
        parts.append(t("not signed by NVIDIA"))
    if v.gpus:
        if "RTX" not in v.display:         # catalog labels such as "(SF-v2 community build, RTX 20-50)" say it already
            parts.append(t("for {gpus}", gpus=", ".join(v.gpus)))
        if v.supports_gpu(gpu_gen) is False:
            parts.append(t("not for your GPU"))
    return " · ".join(parts)


class _Bridge(QObject):
    progress = Signal(object)


class PreviewDialog(QDialog):
    """Every file the installation adds, updates, removes, replaces or disables, before anything changes."""

    def __init__(self, prepared, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Review the installation"))
        self.resize(940, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(label(t(prepared.plan.summary[0]) if prepared.plan.summary else "", "h3", wrap=True))
        lay.addWidget(label(t("Nothing has been changed yet. AKENO backs up every file it replaces and can restore "
                              "the folder exactly."), "dim", wrap=True))
        table = QTableWidget(len(prepared.rows), 3)
        table.setHorizontalHeaderLabels([t("Action"), t("File"), t("Details")])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        for i, row in enumerate(prepared.rows):
            action = QTableWidgetItem(t(ACTION_TEXT.get(row.action, row.action)))
            color = {"replace": theme.AMBER, "disable": theme.AMBER, "remove": theme.RED}.get(row.action)
            if color:
                action.setForeground(theme.qc(color))
            table.setItem(i, 0, action)
            table.setItem(i, 1, QTableWidgetItem(row.path))
            table.setItem(i, 2, QTableWidgetItem(t(row.detail)))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(table, 1)
        self.approve = None
        if prepared.approvals:
            box = QFrame()
            box.setObjectName("SubCard")
            bl = QVBoxLayout(box)
            bl.addWidget(label(t("{n} file(s) belong to other tools.", n=len(prepared.approvals)), "h3"))
            bl.addWidget(label(t("They are replaced or disabled so the stack can work. Their originals are kept in the "
                                 "backup and come back when you remove the mod."), "dim", wrap=True))
            self.approve = QCheckBox(t("Replace / disable these files (backed up and restorable)"))
            bl.addWidget(self.approve)
            lay.addWidget(box)
        if prepared.fivem_ack:
            lay.addWidget(label(t("FiveM blocks ReShade 5+ until you accept its crash-risk notice. AKENO adds FiveM's "
                                  "own acknowledgement line to CitizenFX.ini and removes it when you remove the mod."),
                                "dim", wrap=True))
        buttons = QDialogButtonBox()
        self.ok = buttons.addButton(t("Install"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setProperty("kind", "primary")
        buttons.addButton(t("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        if self.approve is not None:
            self.ok.setEnabled(False)
            self.approve.toggled.connect(self.ok.setEnabled)


class CustomDialog(QDialog):
    def __init__(self, settings, values: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Custom settings"))
        lay = QVBoxLayout(self)
        lay.addWidget(label(t("Only settings this exact version reads are offered."), "dim", wrap=True))
        form = QFormLayout()
        self.getters = {}
        for s in settings:
            current = values.get(s.key, s.default)
            if s.kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(current))
                get = w.isChecked
            elif s.kind == "choice":
                w = QComboBox()
                for value, title in s.choices:
                    w.addItem(t(title), value)
                w.setCurrentIndex(max(0, w.findData(current)))
                get = w.currentData
            elif s.kind == "int":
                w = QSpinBox()
                w.setRange(int(s.minimum), int(s.maximum))
                w.setValue(int(current))
                get = w.value
            else:
                w = QDoubleSpinBox()
                w.setDecimals(2)
                w.setSingleStep(0.05)
                w.setRange(float(s.minimum), float(s.maximum))
                w.setValue(float(current))
                get = w.value
            if s.help:
                w.setToolTip(t(s.help))
            w.setAccessibleName(t(s.label))
            form.addRow(t(s.label), w)
            self.getters[s.key] = get
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict:
        return {k: get() for k, get in self.getters.items()}


class MethodCard(Clickable):
    def __init__(self, m, selected: bool, recommended: bool):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(t(m.recipe.name))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        a = theme.accent()
        bg = a.soft(0.12) if selected else "rgba(255,255,255,0.018)"
        border = a.base if selected else theme.BORDER
        self.setStyleSheet(f"MethodCard {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
                           f"MethodCard:hover {{ border-color: {a.base if selected else theme.BORDER_BTN}; }}"
                           f"MethodCard:focus {{ border-color: {a.text}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 11)
        lay.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(10)
        mark = QLabel()                         # a radio mark: which method is chosen
        mark.setFixedSize(16, 16)
        mark.setStyleSheet(f"border-radius: 8px; border: {'5px' if selected else '1px'} solid "
                           f"{a.strong if selected else theme.BORDER_BTN}; background: "
                           f"{'#FFFFFF' if selected else theme.INPUT};")
        head.addWidget(mark)
        title = label(t(m.recipe.name), "h3", shrink=True)
        head.addWidget(title)
        if recommended and m.available:
            head.addWidget(StatusPill(t("Recommended"), "ok"))
        elif not m.available:
            head.addWidget(StatusPill(t("Not available"), "fail"))
        head.addStretch(1)
        if m.download_bytes:
            head.addWidget(label(t("Download {size}", size=mib(m.download_bytes)), "muted"))
        lay.addLayout(head)
        summary = label(t(m.recipe.summary), "dim", wrap=True)
        summary.setContentsMargins(26, 0, 0, 0)
        lay.addWidget(summary)
        if not m.available:
            b = m.blockers[0]
            lay.addWidget(label(f"<span style='color:{theme.RED}'>●</span> " + t(b.message) +
                                (" " + t(b.remedy) if b.remedy else ""), wrap=True))


class GamePanel(QWidget):
    """Right-hand side of My Games (and the body of the FiveM page) for one game."""

    def __init__(self, ctl, game_id: str, parent=None, fivem: bool = False):
        super().__init__(parent)
        self.ctl, self.s, self.game_id, self.fivem = ctl, ctl.s, game_id, fivem
        self.stack = self.s.stack
        self.state = None
        self.token: CancelToken | None = None
        self.busy = False
        self.closed = False
        self.epoch = 0
        self.bridge = _Bridge(self)
        self.bridge.progress.connect(self._progress)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(theme.CARD_GAP)
        self.header = QFrame()
        self.header.setObjectName("Card")
        self.header_lay = QHBoxLayout(self.header)
        self.header_lay.setContentsMargins(16, 16, 20, 16)
        self.header_lay.setSpacing(20)
        self.lay.addWidget(self.header)
        self.main = Card(t("DLSS 5 method"), "sliders")
        self.lay.addWidget(self.main)
        self.methods_box = QVBoxLayout()
        self.methods_box.setSpacing(8)
        self.main.body.addLayout(self.methods_box)
        self.versions_title = label(t("Versions"), "h3")
        self.main.body.addWidget(self.versions_title)
        self.versions = QGridLayout()
        self.versions.setHorizontalSpacing(12)
        self.versions.setVerticalSpacing(6)
        self.versions.setColumnStretch(1, 1)
        self.main.body.addLayout(self.versions)
        self.main.body.addWidget(label(t("Quality preset"), "h3"))
        chips = FlowLayout()
        self.preset_buttons: dict[str, QPushButton] = {}
        for key in configs.PRESETS:
            b = button(t(configs.PRESET_LABELS[key]), kind="chip", on_click=lambda _=False, k=key: self._preset(k))
            b.setCheckable(True)
            self.preset_buttons[key] = b
            chips.addWidget(b)
        self.main.body.addLayout(chips)
        self.preset_text = label("", "dim", wrap=True)
        self.main.body.addWidget(self.preset_text)
        self.issues_box = QVBoxLayout()
        self.issues_box.setSpacing(4)
        self.main.body.addLayout(self.issues_box)
        self.main.body.addWidget(Divider())
        actions = FlowLayout(spacing=theme.SPACE_S)
        self.btn_install = button(t("Install DLSS 5"), "download", "primary", on_click=self.install)
        self.btn_install.setMinimumHeight(36)
        self.btn_launch = button(t("Launch game"), "play", on_click=self.launch)
        self.btn_launch.setMinimumHeight(36)
        self.btn_previous = button(t("Previous version"), "restore", "ghost", on_click=self.previous)
        self.btn_previous.setMinimumHeight(36)
        self.btn_remove = button(t("Remove mod"), "trash", "danger", on_click=self.remove)
        self.btn_remove.setMinimumHeight(36)
        self.btn_test = button(t("Test the 64-bit helper"), "check",
                               tip=t("Runs the helper's own DLSS self-test (300 evaluations, about a minute) "
                                     "without starting the game."), on_click=self.test_helper)
        self.btn_test.hide()
        for b in (self.btn_install, self.btn_launch, self.btn_previous, self.btn_remove, self.btn_test):
            actions.addWidget(b)
        self.main.body.addLayout(actions)
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.hide()
        prog.addWidget(self.progress, 1)
        self.btn_cancel = button(t("Cancel"), "stop", on_click=self.cancel)
        self.btn_cancel.hide()
        prog.addWidget(self.btn_cancel)
        self.main.body.addLayout(prog)
        self.status = label("", "dim", wrap=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.main.body.addWidget(self.status)
        self.details = Card(t("Game details"), "gamepad")
        self.details_grid = KeyValueGrid(150)
        self.details.body.addWidget(self.details_grid)
        self.details_extra = QVBoxLayout()
        self.details.body.addLayout(self.details_extra)
        self.lay.addWidget(self.details)
        self.lay.addStretch(1)
        self.spinner = Spinner()
        self.refresh()

    def closeEvent(self, e):
        self.closed = True
        self.cancel()
        super().closeEvent(e)

    def dispose(self) -> None:
        self.closed = True
        self.epoch += 1
        self.cancel()

    # -- state ----------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.epoch += 1
        epoch = self.epoch
        gid = self.game_id

        def done(state):
            if self.closed or not isValid(self) or epoch != self.epoch:
                return
            self.state = state
            self._render()

        def fail(exc):
            if self.closed or not isValid(self) or epoch != self.epoch:
                return
            self.status.setText(error_text(exc))
        if not self.ctl.run("stack_state:" + gid + ":" + str(epoch), lambda: self.stack.state(gid), done, fail,
                            silent=True):
            QTimer.singleShot(200, self.refresh)

    def _render(self) -> None:
        st = self.state
        g = st.game
        clear_layout(self.header_lay)
        img = QLabel()
        img.setPixmap(game_cover(g, 240, 135, 8))
        img.setFixedSize(240, 135)
        self.header_lay.addWidget(img, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label(g.name, "display", shrink=True))
        api = t(GraphicsApi(st.api).label) if st.api in {a.value for a in GraphicsApi} else st.api
        if self.s.db.game_overrides(g.id).get("api"):
            api += " (" + t("your choice") + ")"          # a manual renderer stays visible as such
        col.addWidget(label(" · ".join([t(LAUNCHERS.get(g.launcher, g.launcher)), api, g.architecture or ""]).strip(" ·"),
                            "dim", shrink=True))
        gpu = st.gpu
        col.addWidget(label((gpu.name + (f" · {gpu.generation}" if gpu.generation else "") +
                             (f" · {t('driver')} {gpu.driver_version}" if gpu.driver_version else ""))
                            if gpu else t("Graphics card not detected yet"), "muted", shrink=True))
        if st.installed:
            p = st.installed["params"]
            recipe = self.stack.catalog.recipes.get(p.get("recipe"))
            consumer = self.stack.catalog.version(p.get("versions", {}).get(recipe.consumer_role, "")) if recipe else None
            text = t("Installed: {method} · {preset}", method=t(recipe.name) if recipe else "?",
                     preset=t(configs.PRESET_LABELS.get(p.get("preset"), "?")))
            if consumer:
                text += f" · {consumer.display}"
            col.addWidget(StatusPill(text, "ok"), 0, Qt.AlignmentFlag.AlignLeft)
        elif st.legacy:
            col.addWidget(StatusPill(t("Installed by an older AKENO version"), "warn"), 0, Qt.AlignmentFlag.AlignLeft)
        else:
            col.addWidget(StatusPill(t("Not installed"), "pending"), 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        self.header_lay.addLayout(col, 1)
        # methods
        clear_layout(self.methods_box)
        for m in st.methods:
            card = MethodCard(m, st.selected is m, st.recommended is m)
            card.clicked.connect(lambda rid=m.recipe.id: self._choose_recipe(rid))
            self.methods_box.addWidget(card)
        if st.selected:
            for d in st.selected.recipe.details:
                self.methods_box.addWidget(label("• " + t(d), "muted", wrap=True))
        # versions
        clear_layout(self.versions)
        gen = gpu.generation if gpu else None
        if st.selected:
            for i, rc in enumerate(st.selected.roles):
                self.versions.addWidget(label(t(rc.title), "key", shrink=True), i, 0)
                combo = QComboBox()
                combo.setAccessibleName(t(rc.title))
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(10)
                for v in rc.options:
                    combo.addItem(version_text(v, gen), v.key)
                    if v.notes:
                        combo.setItemData(combo.count() - 1, t(v.notes[0]), Qt.ItemDataRole.ToolTipRole)
                if rc.version:
                    combo.setCurrentIndex(max(0, combo.findData(rc.version.key)))
                combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                combo.activated.connect(lambda _=0, c=combo, role=rc.role: self._choose_version(role, c.currentData()))
                self.versions.addWidget(combo, i, 1)
                state = {"ready": (t("Ready"), "ok"), "download": (t("Download {size}", size=mib(rc.download_bytes))
                                                                     if rc.download_bytes else t("Download"), "info"),
                         "unavailable": (t("Not available"), "fail")}[rc.state]
                pill = StatusPill(*state)
                if rc.note:
                    pill.setToolTip(t(rc.note))
                self.versions.addWidget(pill, i, 2)
                if rc.role in ("nr_runtime", "sr_runtime", "consumer_renodx", "reshade", "reshade32"):
                    imp = button("", "folder", tip=t("Use a file from this PC"),
                                 on_click=lambda _=False, role=rc.role: self._import(role))
                    self.versions.addWidget(imp, i, 3)
        # preset
        family = st.selected.recipe.presets if st.selected else "renodx"
        for k, b in self.preset_buttons.items():
            b.setChecked(k == st.preset)
        self.preset_text.setText(self._preset_description(st, family))
        # issues
        clear_layout(self.issues_box)
        issues = list(st.issues) + (list(st.selected.issues) if st.selected else [])
        for issue in issues:
            row = QHBoxLayout()
            row.addWidget(StatusPill("", LEVEL_STATE[issue.level]))
            text = label(t(issue.message) + (" " + t(issue.remedy) if issue.remedy else ""), wrap=True)
            row.addWidget(text, 1)
            act = self._action_button(issue)
            if act:
                row.addWidget(act)
            self.issues_box.addLayout(row)
        # buttons
        blocked = any(i.level == "block" for i in issues) or st.selected is None
        self.btn_install.setEnabled(not blocked and not self.busy)
        self.btn_install.setText(t("Update / Reinstall") if st.installed else t("Install DLSS 5"))
        self.btn_remove.setEnabled(bool(st.installed or st.legacy) and not self.busy)
        self.btn_previous.setEnabled(bool(st.installed) and bool(self.stack.previous_versions(g.id)) and not self.busy)
        self.btn_launch.setEnabled(not self.busy and (g.launcher == "fivem" or bool(g.exe_path)))
        installed_recipe = self.stack.catalog.recipes.get((st.installed or {}).get("params", {}).get("recipe"))
        self.btn_test.setVisible(bool(installed_recipe and installed_recipe.helper))
        self.btn_test.setEnabled(not self.busy)
        self._render_details(st)

    def _preset_description(self, st, family) -> str:
        if not st.selected:
            return ""
        recipe = st.selected.recipe
        consumer = next((r.version for r in st.selected.roles if r.role == recipe.consumer_role), None)
        features = self.stack.catalog.component(consumer.component).features if consumer else frozenset()
        fam = "renodx" if recipe.consumer_role == "consumer_renodx" else "optiscaler"
        try:
            text = configs.describe_preset(fam, st.preset, features, st.api, recipe.uses_feeder)
        except (ValueError, KeyError):
            text = ""
        return t(text)

    def _action_button(self, issue) -> QPushButton | None:
        a = issue.action or ""
        if a == "scan_hardware":
            return button(t("Scan hardware"), "chip", on_click=lambda: self.ctl.scan_hardware(lambda _: self.refresh()))
        if a == "select_exe":
            return button(t("Select .exe"), "folder", on_click=self._browse_exe)
        if a == "ack_anti_cheat":
            return button(t("I play offline only"), "check", on_click=self._ack_anti_cheat)
        if a.startswith("import:"):
            return button(t("Import file"), "folder", on_click=lambda: self._import(a.split(":", 1)[1]))
        if a in ("refresh", "rescan"):
            return button(t("Check again"), "refresh", on_click=self._reanalyze)
        if a == "stop_helper":
            return button(t("Stop the helper"), "stop", on_click=self._stop_helper)
        if a == "choose_api":
            combo = QComboBox()
            combo.addItem(t("Choose the renderer…"), "")
            for api in (GraphicsApi.D3D12, GraphicsApi.D3D11, GraphicsApi.D3D10, GraphicsApi.D3D9, GraphicsApi.D3D8,
                        GraphicsApi.VULKAN, GraphicsApi.OPENGL):
                combo.addItem(t(api.label), api.value)
            combo.activated.connect(lambda _=0, c=combo: c.currentData() and self._override("api", c.currentData()))
            return combo
        return None

    def _render_details(self, st) -> None:
        g = st.game
        rows = [(t("Install folder"), g.install_dir), (t("Executable"), g.exe_path or t("Unknown")),
                (t("Upscalers in game"), ", ".join(x.upper() for x in sorted(set(g.upscaler_inputs) |
                                                                              set(st.profile.upscalers if st.profile else ())))
                 or t("None found")),
                (t("DLSS (game)"), g.dlss_version or t("Not found")),
                (t("Anti-cheat"), "\n".join(g.anti_cheat_details) if g.anti_cheat_details else t("None found"))]
        if st.profile:
            rows.append((t("Game profile"), t(st.profile.name)))
        self.details_grid.set_rows(rows)
        clear_layout(self.details_extra)
        if st.profile:
            for n in st.profile.notes:
                self.details_extra.addWidget(label("• " + t(n), "dim", wrap=True))
        row = QHBoxLayout()
        if g.launcher != "fivem":
            exe = QComboBox()
            exe.setAccessibleName(t("Executable"))
            exe.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            exe.setMinimumContentsLength(10)
            for c in g.exe_candidates or ([g.exe_path] if g.exe_path else []):
                exe.addItem(os.path.relpath(c, g.install_dir) if c.startswith(g.install_dir) else c, c)
            exe.addItem(t("Browse…"), "__browse__")
            if g.exe_path:
                exe.setCurrentIndex(max(0, exe.findData(g.exe_path)))
            exe.activated.connect(lambda _=0, c=exe: self._browse_exe() if c.currentData() == "__browse__"
                                  else self._override("exe_path", c.currentData()))
            row.addWidget(label(t("Executable"), "key"))
            row.addWidget(exe, 2)
            self.details_extra.addLayout(row)
            row = QHBoxLayout()
            api = QComboBox()
            api.setAccessibleName(t("Graphics API"))
            ov = self.s.db.game_overrides(g.id).get("api")
            if ov:                             # what Automatic would pick without the user's choice
                auto = st.profile.api if st.profile and st.profile.api else (
                    g.api_candidates[0] if len(g.api_candidates) == 1 else None)
            else:
                auto = self.stack.game_api(g, st.profile)
            auto_label = t(GraphicsApi(auto).label) if auto in {a.value for a in GraphicsApi} else t("Unknown")
            api.addItem(t("Automatic ({api})", api=auto_label), "")
            for a in (GraphicsApi.D3D12, GraphicsApi.D3D11, GraphicsApi.D3D10, GraphicsApi.D3D9, GraphicsApi.D3D8,
                      GraphicsApi.VULKAN, GraphicsApi.OPENGL):
                api.addItem(t(a.label), a.value)
            if ov:
                api.setCurrentIndex(max(0, api.findData(ov)))
                api.setToolTip(t("You chose this renderer. Pick Automatic to use the detected one again."))
            else:
                api.setToolTip(t("Detected from: {source}", source=t(g.api_source or "") or t("Unknown")))
            api.activated.connect(lambda _=0, c=api: self._override("api", c.currentData()))
            row.addWidget(label(t("Renderer"), "key"))
            row.addWidget(api, 1)
        row.addWidget(button("", "folder", tip=t("Open the game folder"),
                             on_click=lambda d=(st.game.target_dir or st.game.install_dir):
                             QDesktopServices.openUrl(QUrl.fromLocalFile(d))))
        row.addWidget(button(t("Check again"), "refresh", on_click=self._reanalyze))
        self.details_extra.addLayout(row)

    # -- user choices --------------------------------------------------------------------------------------
    def _choose_recipe(self, rid: str) -> None:
        if self.busy:
            return
        self.stack.choose_recipe(self.game_id, rid)
        self.refresh()

    def _choose_version(self, role: str, key: str) -> None:
        if self.busy:
            return
        self.stack.choose_version(self.game_id, role, key)
        self.refresh()

    def _preset(self, key: str) -> None:
        if self.busy or self.state is None:
            return
        custom = None
        if key == "custom":
            custom = self._custom_dialog()
            if custom is None:
                self._render()
                return
        gid = self.game_id

        def done(res):
            if res.ok:
                self.status.setText("\n".join(t(m) for m in res.messages[-2:]))
            else:
                self.status.setText(t("The preset could not be applied.") + "\n" +
                                    "\n".join(t(m) for m in res.messages[:4]))
            self.ctl.installs_changed.emit()
            self.refresh()
        self._run("preset", lambda token: self.stack.apply_preset(gid, key, custom), done)

    def _custom_dialog(self) -> dict | None:
        st = self.state
        recipe = st.selected.recipe
        consumer = next((r.version for r in st.selected.roles if r.role == recipe.consumer_role), None)
        if recipe.consumer_role == "consumer_renodx":
            specs = configs.RENODX_SETTINGS
        else:
            comp = self.stack.catalog.component(consumer.component) if consumer else None
            specs = configs.optiscaler_settings(comp.features if comp else frozenset(), st.api,
                                                consumer.settings if consumer else (), recipe.uses_feeder)
        dlg = CustomDialog(specs, st.custom, self)
        return dlg.values() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def _override(self, key: str, value) -> None:
        gid = self.game_id
        self.ctl.run("analyze", lambda: self.s.library.set_override(gid, key, value or None),
                     lambda _: (self.ctl.games_changed.emit(), self.refresh()))

    def _browse_exe(self) -> None:
        g = self.state.game if self.state else None
        path, _ = QFileDialog.getOpenFileName(self, t("Select the game executable"), g.install_dir if g else "",
                                              "Executable (*.exe)")
        if path:
            self._override("exe_path", path)
        else:
            self.refresh()

    def _reanalyze(self) -> None:
        gid = self.game_id
        self.ctl.run("analyze", lambda: self.s.library.reanalyze(gid), lambda _: self.refresh())

    def _ack_anti_cheat(self) -> None:
        if confirm(self, t("Anti-cheat"), t("Graphics mods can get you banned in online modes. Use this game only "
                                            "offline / single-player while the mod is installed."),
                   ok_text=t("I play offline only")):
            self.stack.save_selection(self.game_id, anti_cheat_ack=True)
            self.refresh()

    def _import(self, role: str) -> None:
        filters = {"nr_runtime": "nvngx_dlssnr.dll (*.dll)", "sr_runtime": "nvngx_dlss.dll (*.dll)",
                   "consumer_renodx": "RenoDX DLSS 5 (*.addon64)", "reshade": "ReShade64.dll (*.dll)",
                   "reshade32": "ReShade32.dll (*.dll)"}
        path, _ = QFileDialog.getOpenFileName(self, t("Use a file from this PC"), "", filters.get(role, "*"))
        if not path:
            return
        gid = self.game_id

        def done(v):
            self.stack.choose_version(gid, role, v.key)
            self.status.setText(t("Imported {name}.", name=v.display))
            self.refresh()
        self._run("import", lambda token: self.stack.store.import_file(role, path), done)

    # -- long operations -------------------------------------------------------------------------------------
    def _run(self, key: str, work, done) -> None:
        if self.busy:
            return
        self.busy = True
        self.token = CancelToken()
        token = self.token
        self.progress.setRange(0, 0)
        self.progress.show()
        self.btn_cancel.show()
        for b in (self.btn_install, self.btn_launch, self.btn_previous, self.btn_remove, self.btn_test):
            b.setEnabled(False)

        def finish(value):
            self.busy = False
            if self.closed or not isValid(self):
                return
            self.progress.hide()
            self.btn_cancel.hide()
            done(value)

        def fail(exc):
            self.busy = False
            if self.closed or not isValid(self):
                return
            self.progress.hide()
            self.btn_cancel.hide()
            self.status.setText(error_text(exc))
            self.refresh()
        if not self.ctl.run("stack:" + self.game_id + ":" + key, lambda: work(token), finish, fail):
            self.busy = False
            self.progress.hide()
            self.btn_cancel.hide()

    def _progress(self, value) -> None:
        if self.closed or not isValid(self):
            return
        text, cur, tot = value
        if tot:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(cur * 1000 / tot)))
            self.status.setText(t("Downloading {what}: {cur} of {tot}", what=t(text), cur=mib(cur), tot=mib(tot)))
        else:
            self.status.setText(t("Preparing {what}…", what=t(text)))

    def cancel(self) -> None:
        if self.token:
            self.token.cancel()

    def install(self) -> None:
        gid = self.game_id
        emit = self.bridge.progress.emit
        self.status.setText(t("Preparing the complete mod stack…"))
        self._run("prepare", lambda token: self.stack.prepare(gid, progress=lambda a, b, c: emit((a, b, c)),
                                                              token=token), self._review)

    def _review(self, prepared) -> None:
        dlg = PreviewDialog(prepared, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.status.setText(t("Installation cancelled. Game files were not changed."))
            self.refresh()
            return
        approve = bool(dlg.approve and dlg.approve.isChecked())
        self.status.setText(t("Installing…"))
        self._run("install", lambda token: self.stack.install(prepared, approve=approve), self._installed)

    def _installed(self, res) -> None:
        lines = [t(m) for m in res.messages]
        if res.ok:
            self.status.setText("\n".join(lines[-3:]))
        else:
            detail = "\n".join(f"• {c.rel_path}: {t(c.reason)}" for c in res.conflicts[:10])
            info(self, t("Installation"), t("The installation did not complete. Your files were restored."),
                 "\n".join(lines[:6]) + ("\n" + detail if detail else ""), error=True)
        self.ctl.installs_changed.emit()
        self.ctl.games_changed.emit()
        self.refresh()

    def previous(self) -> None:
        gid = self.game_id
        prev = self.stack.previous_versions(gid)
        if not prev:
            return
        emit = self.bridge.progress.emit
        self._run("previous", lambda token: self.stack.restore_previous(gid, lambda a, b, c: emit((a, b, c)), token),
                  self._review)

    def remove(self) -> None:
        if not confirm(self, t("Remove mod"), t("Remove the mod completely and put the original files back?")):
            return
        gid = self.game_id

        def done(res):
            if not res.ok and res.conflicts and not any("backup" in c.reason for c in res.conflicts):
                detail = "\n".join(f"• {c.rel_path}: {t(c.reason)}" for c in res.conflicts[:10])
                if confirm(self, t("Remove mod"), t("Some installed files were changed after installation."), detail,
                           t("Remove anyway")):
                    self._run("remove", lambda token: self.stack.uninstall(gid, force=True), done)
                    return
            self.status.setText("\n".join(t(m) for m in res.messages[-3:]))
            self.ctl.installs_changed.emit()
            self.refresh()
        st = self.state
        if st and st.legacy and not st.installed:
            self._run("remove", lambda token: self.stack.remove_legacy(gid), done)
        else:
            self._run("remove", lambda token: self.stack.uninstall(gid), done)

    def test_helper(self) -> None:
        gid = self.game_id
        self.status.setText(t("Testing the 64-bit helper (about a minute)…"))
        self._run("test_helper", lambda token: self.stack.test_helper(gid),
                  lambda res: (self.status.setText("\n".join(t(m) for m in res.messages)), self.refresh()))

    def _stop_helper(self) -> None:
        gid = self.game_id
        self._run("stop_helper", lambda token: self.stack.stop_helper(gid),
                  lambda res: (self.status.setText("\n".join(t(m) for m in res.messages)), self.refresh()))

    def launch(self, connect: str | None = None) -> None:
        gid = self.game_id
        self._run("launch", lambda token: self.stack.launch(gid, connect),
                  lambda text: (self.status.setText(t(text)), self.refresh()))
