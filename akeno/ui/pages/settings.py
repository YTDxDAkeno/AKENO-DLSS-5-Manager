"""Settings page: language, appearance, updates, storage, backups, benchmarks, safety, diagnostics, reset."""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from akeno import __version__
from akeno.compat.engine import CompatDatabase, CompatDbError
from akeno.i18n import t
from akeno.paths import app_home, backups_dir, cache_dir, compat_override_path, is_windows, logs_dir
from akeno.ui import theme
from akeno.ui.pages.hardware import export_diagnostics
from akeno.ui.widgets import Card, KeyValueGrid, ResponsiveRow, button, confirm, info, label

log = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RELEASES_API = "https://api.github.com/repos/YTDxDAkeno/AKENO-DLSS-5-Manager/releases/latest"


def set_autostart(enabled: bool) -> None:
    if not is_windows():
        raise OSError("Launch with Windows is only available on Windows.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "AKENO DLSS 5 Manager", 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, "AKENO DLSS 5 Manager")
            except FileNotFoundError:
                pass


def fetch_json(url: str, allowed: set[str], timeout: float = 15) -> object:
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in allowed:
        raise ValueError(f"Refusing to contact {u.hostname}: not an official AKENO source.")
    req = urllib.request.Request(url, headers={"User-Agent": f"AKENO-DLSS5-Manager/{__version__}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Response too large.")
    return json.loads(data.decode("utf-8"))


class SettingsPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl = ctl
        self.s = ctl.s
        st = self.s.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(label(t("Settings"), "h1"))
        rows = [ResponsiveRow() for _ in range(3)]
        for r in rows:
            lay.addWidget(r)

        # General / appearance
        gen = Card(t("General"), "globe", theme.BLUE)
        self.lang = QComboBox()
        self.lang.setAccessibleName(t("Language"))
        for k, v in (("auto", "Automatic (Windows language)"), ("en", "English"), ("de", "Deutsch")):
            self.lang.addItem(t(v) if k == "auto" else v, k)
        self.lang.setCurrentIndex(max(0, self.lang.findData(st.get("language"))))
        self.lang.activated.connect(lambda i: ctl.set_setting("language", self.lang.currentData()))
        self.accent = QComboBox()
        self.accent.setAccessibleName(t("Accent colour"))
        for k, v in (("violet", "Violet"), ("blue", "Blue"), ("cyan", "Cyan")):
            self.accent.addItem(t(v), k)
        self.accent.setCurrentIndex(max(0, self.accent.findData(st.get("theme_accent"))))
        self.accent.activated.connect(lambda i: ctl.set_setting("theme_accent", self.accent.currentData()))
        gen.body.addLayout(self._row(t("Language"), self.lang))
        gen.body.addLayout(self._row(t("Accent colour"), self.accent))
        gen.body.addWidget(self._check("animations", t("Animations (spinners and transitions)")))
        gen.body.addWidget(self._check("notifications", t("Show a notification when long tasks finish")))
        self.autostart = QCheckBox(t("Launch with Windows"))
        self.autostart.setChecked(bool(st.get("launch_with_windows")))
        self.autostart.toggled.connect(self._autostart)
        gen.body.addWidget(self.autostart)
        gen.body.addWidget(self._check("rescan_on_start", t("Rescan hardware when AKENO starts")))
        rows[0].add(gen)

        # Updates
        upd = Card(t("Updates"), "refresh", theme.CYAN)
        upd.body.addWidget(self._check("check_app_updates", t("Check for AKENO updates on start")))
        upd.body.addWidget(self._check("auto_update_compat_db", t("Update the compatibility database automatically")))
        mods = self._check("check_mod_updates", t("Check for new mod versions once a day"))
        mods.setToolTip(t("Asks GitHub for new releases of the mods. Nothing is installed by itself."))
        upd.body.addWidget(mods)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText(t("Optional: GitHub token for more update checks per hour"))
        self.token.setAccessibleName(t("GitHub token"))
        self.token.setText(st.get("github_token") or "")
        self.token.editingFinished.connect(lambda: ctl.set_setting("github_token", self.token.text().strip()))
        upd.body.addLayout(self._row(t("GitHub token"), self.token))
        self.upd_info = label(t("Compatibility database {v} ({o}); package catalog {c}.", v=self.s.compat_db.version,
                                o=t(self.s.compat_db.origin), c=self.s.catalog.version), "dim", wrap=True)
        upd.body.addWidget(self.upd_info)
        r = QHBoxLayout()
        r.addWidget(button(t("Check for updates now"), "refresh", on_click=self.check_updates))
        r.addWidget(button(t("Update compatibility database"), "download", on_click=self.update_compat))
        r.addStretch(1)
        upd.body.addLayout(r)
        upd.body.addWidget(label(t("Database updates are fetched only from AKENO's official repository over HTTPS and "
                                   "are rejected unless every entry passes validation. They never change your "
                                   "settings, profiles or the list of installable packages."), "muted", wrap=True))
        rows[0].add(upd)

        # Storage
        sto = Card(t("Storage"), "folder", theme.VIOLET)
        self.sto_grid = KeyValueGrid(150)
        sto.body.addWidget(self.sto_grid)
        self.cache_limit = QSpinBox()
        self.cache_limit.setRange(100, 20000)
        self.cache_limit.setSuffix(" MB")
        self.cache_limit.setValue(int(st.get("cache_limit_mb")))
        self.cache_limit.valueChanged.connect(lambda v: ctl.set_setting("cache_limit_mb", v))
        sto.body.addLayout(self._row(t("Download cache limit"), self.cache_limit))
        r2 = QHBoxLayout()
        r2.addWidget(button(t("Open data folder"), "folder",
                            on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_home())))))
        r2.addWidget(button(t("Clear download cache"), "trash", on_click=self._clear_cache))
        r2.addStretch(1)
        sto.body.addLayout(r2)
        rows[1].add(sto)

        # Backups & benchmarks
        bb = Card(t("Backups and benchmarks"), "database", theme.GREEN)
        keep = QSpinBox()
        keep.setRange(1, 100)
        keep.setValue(int(st.get("backup_keep_per_game")))
        keep.valueChanged.connect(lambda v: ctl.set_setting("backup_keep_per_game", v))
        keep.setToolTip(t("After each installation or manual backup, finished backups beyond this number are deleted, "
                          "oldest first. Restore points of active installations are never deleted automatically."))
        bb.body.addLayout(self._row(t("Backups kept per game"), keep))
        dur = QSpinBox()
        dur.setRange(10, 600)
        dur.setSuffix(" s")
        dur.setValue(int(st.get("benchmark_duration_s")))
        dur.valueChanged.connect(lambda v: ctl.set_setting("benchmark_duration_s", v))
        bb.body.addLayout(self._row(t("Benchmark duration"), dur))
        delay = QSpinBox()
        delay.setRange(0, 60)
        delay.setSuffix(" s")
        delay.setValue(int(st.get("benchmark_delay_s")))
        delay.valueChanged.connect(lambda v: ctl.set_setting("benchmark_delay_s", v))
        bb.body.addLayout(self._row(t("Delay before recording"), delay))
        fps = QSpinBox()
        fps.setRange(20, 500)
        fps.setSuffix(" FPS")
        fps.setValue(int(st.get("target_fps")))
        fps.valueChanged.connect(lambda v: ctl.set_setting("target_fps", v))
        fps.setToolTip(t("Recommendations prefer the highest-quality profile that stays above this frame rate."))
        bb.body.addLayout(self._row(t("FPS target"), fps))
        rows[1].add(bb)

        # Safety
        saf = Card(t("Safety defaults"), "shield", theme.AMBER)
        saf.body.addWidget(self._check("block_unknown_conflicts", t("Stop installation when unknown graphics hooks are found")))
        saf.body.addWidget(label(t("Always on: every download is checked against the SHA-256 of its original "
                                   "release, every installation is previewed before anything changes, originals "
                                   "are backed up and verified first, and removal puts them back byte for byte. "
                                   "Games with anti-cheat need your confirmation that you play offline. Files of "
                                   "other mods are only replaced or disabled after you approve them in the "
                                   "preview."), "muted", wrap=True))
        rows[2].add(saf)

        # Diagnostics
        dia = Card(t("Diagnostics"), "info", theme.BLUE)
        dia.body.addWidget(self._check("debug_logging", t("Detailed diagnostic logging")))
        r3 = QHBoxLayout()
        r3.addWidget(button(t("Export Diagnostic Package"), "export", on_click=lambda: export_diagnostics(self, self.s)))
        r3.addWidget(button(t("Open log folder"), "folder",
                            on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir())))))
        r3.addWidget(button(t("Rescan hardware"), "chip", on_click=lambda: ctl.scan_hardware()))
        r3.addStretch(1)
        dia.body.addLayout(r3)
        rows[2].add(dia)

        reset = Card(t("Reset"), "restore", theme.RED)
        reset.body.addWidget(label(t("Factory reset restores AKENO's settings to their defaults. Your games, profiles, "
                                     "benchmarks, backups and installed mods are kept, so everything can still be "
                                     "restored."), "dim", wrap=True))
        reset.body.addWidget(button(t("Factory reset settings"), "restore", "danger", on_click=self._reset))
        lay.addWidget(reset)
        lay.addStretch(1)
        self._refresh_storage()

    def _row(self, text: str, w: QWidget) -> QHBoxLayout:
        r = QHBoxLayout()
        r.addWidget(label(text, "dim"), 1)
        r.addWidget(w, 1)
        return r

    def _check(self, key: str, text: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(bool(self.s.settings.get(key)))
        cb.toggled.connect(lambda v, k=key: self.ctl.set_setting(k, v))
        return cb

    def _refresh_storage(self) -> None:
        size = self.s.downloads.cache_size()
        self.sto_grid.set_rows([(t("Data folder"), str(app_home())), (t("Backups"), str(backups_dir())),
                                (t("Download cache"), f"{cache_dir()}  ({size / 1e6:.1f} MB)"),
                                (t("Logs"), str(logs_dir()))])

    def _autostart(self, on: bool) -> None:
        try:
            set_autostart(on)
            self.ctl.set_setting("launch_with_windows", on)
        except OSError as exc:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not on)
            self.autostart.blockSignals(False)
            info(self, t("Launch with Windows"), str(exc), error=True)

    def _clear_cache(self) -> None:
        if confirm(self, t("Clear download cache"), t("Delete cached downloads? Verified packages are downloaded "
                                                       "again when needed. Backups are not affected.")):
            n = self.s.downloads.clear_cache()
            self._refresh_storage()
            self.ctl.status.emit(t("Removed {n} cached file(s).", n=n))

    def check_updates(self) -> None:
        def work():
            return fetch_json(RELEASES_API, {"api.github.com"})

        def done(data):
            tag = str((data or {}).get("tag_name") or "").lstrip("v")
            if not tag:
                info(self, t("Updates"), t("No published release was found."))
            elif _ver(tag) > _ver(__version__):
                info(self, t("Updates"), t("AKENO {v} is available.", v=tag),
                     t("Download it from the official GitHub releases page: {u}",
                       u=data.get("html_url", "https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/releases")))
            else:
                info(self, t("Updates"), t("AKENO is up to date ({v}).", v=__version__))
        self.ctl.run("updates", work, done, lambda e: info(self, t("Updates"), t("Update check failed: ") + str(e),
                                                           error=True))

    def update_compat(self) -> None:
        url = self.s.settings.get("compat_db_url")

        def work():
            data = fetch_json(url, {"raw.githubusercontent.com"})
            CompatDatabase.validate(data)
            if str(data.get("version")) < self.s.compat_db.version:
                return f"older:{data.get('version')}"
            tmp = compat_override_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, compat_override_path())
            self.s.compat_db = CompatDatabase.load()
            return self.s.compat_db.version

        def done(v):
            if str(v).startswith("older:"):
                info(self, t("Updates"), t("The installed compatibility database is already current."))
            else:
                info(self, t("Updates"), t("Compatibility database updated to {v}.", v=v))
                self.ctl.games_changed.emit()

        def fail(e):
            msg = t("The downloaded database was rejected: ") if isinstance(e, CompatDbError) else t("Update failed: ")
            info(self, t("Updates"), msg + str(e), error=True)
        self.ctl.run("compat", work, done, fail)

    def _reset(self) -> None:
        if confirm(self, t("Factory reset settings"), t("Reset all AKENO settings to their defaults?")):
            try:
                if is_windows():
                    set_autostart(False)
            except OSError:
                pass
            self.s.settings.factory_reset()
            self.ctl.settings_changed.emit("*")


def _ver(v: str) -> tuple:
    out = []
    for part in v.split("."):
        num = "".join(c for c in part if c.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)
