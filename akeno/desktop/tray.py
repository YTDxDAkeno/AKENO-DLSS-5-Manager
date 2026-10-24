"""The Desktop Runtime's tray icon: control wallpapers, the dock and widgets while the suite's window is closed.

Every action is a separate, explicit one, so "close the AKENO window", "stop the wallpaper", "hide the dock",
"hide widgets" and "quit Desktop Runtime" never do each other's job:

* Open AKENO          - starts (or brings up) the suite; the runtime keeps running
* Pause wallpapers    - wallpapers keep their last frame and use no CPU or GPU time until resumed
* Stop wallpaper      - every monitor shows Windows' own wallpaper again; dock and widgets stay
* Dock, Widgets       - shown or hidden; their layout is kept
* Quit Desktop Runtime - everything stays configured; nothing is shown until it is started again
"""

from __future__ import annotations

from PySide6.QtCore import QProcess
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from akeno import APP_NAME
from akeno.desktop import config as cfgmod
from akeno.i18n import t


class RuntimeTray:
    def __init__(self, host):
        from akeno.ui import art
        self.host = host
        self.icon = QSystemTrayIcon(QIcon(art.app_icon_pixmap(64)))
        self.icon.setToolTip(t("AKENO Desktop Runtime"))
        self.menu = QMenu()
        self.a_open = self._add(t("Open {app}", app=APP_NAME), self.open_suite)
        self.menu.addSeparator()
        self.a_pause = self._add(t("Pause wallpapers"), lambda on: host.set_user_paused(on), checkable=True)
        self.a_stop = self._add(t("Stop wallpaper"), lambda: host.edit(cfgmod.stop_wallpapers))
        self.a_dock = self._add(t("Show the dock"), lambda on: host.edit(lambda c: c["dock"].update(enabled=on)),
                                checkable=True)
        self.a_widgets = self._add(t("Show widgets"), lambda on: host.edit(lambda c: c.update(widgets_enabled=on)),
                                   checkable=True)
        self.menu.addSeparator()
        self.a_quit = self._add(t("Quit Desktop Runtime"), self.quit_runtime)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger and
                                    self.open_suite())
        self.refresh()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _add(self, text: str, slot, checkable: bool = False) -> QAction:
        action = QAction(text, self.menu)
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        self.menu.addAction(action)
        return action

    def refresh(self) -> None:
        cfg = self.host.cfg
        for action, on in ((self.a_pause, self.host.user_paused), (self.a_dock, bool(cfg["dock"].get("enabled"))),
                           (self.a_widgets, cfgmod.widgets_on(cfg))):
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)
        self.a_widgets.setEnabled(bool(cfg.get("widgets")))
        self.a_stop.setEnabled(cfgmod.wallpapers_on(cfg))
        self.a_pause.setEnabled(cfgmod.wallpapers_on(cfg))

    def open_suite(self) -> None:
        """The suite's window comes up when the suite runs; otherwise the suite is started."""
        if self.host.apps:
            for ch in self.host.apps:
                ch.send({"cmd": "show"})
            return
        from akeno.desktop.host import self_command
        program, args = self_command()
        QProcess.startDetached(program, args)

    def quit_runtime(self) -> None:
        self.host.edit(lambda c: c.update(enabled=False))     # applying it ends the runtime (nothing is on)
        self.host.quit()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()
