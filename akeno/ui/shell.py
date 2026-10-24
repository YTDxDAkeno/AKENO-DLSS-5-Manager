"""Starts the suite only with an active license, keeps it verified in the background, locks it when it ends.

* Start: the saved license is checked offline (milliseconds). Active → the suite opens at once and the online
  check runs in the background. Not active → the welcome screen opens; no module is created until it unlocks.
* While running: the license state is re-read every 30 seconds (a local check of the signed lease, no network)
  and verified online when the server's interval has passed. Network problems change nothing.
* License ended: the suite window is hidden (after a running installation or restore has finished) and the
  welcome screen explains why. Nothing is closed or deleted, so redeeming a code opens everything again.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from akeno.licensing import LicenseManager, LicenseView
from akeno.ui import workers

log = logging.getLogger(__name__)

TICK_MS = 30_000
CHECK_MS = 5 * 60_000


class SuiteShell(QObject):
    view_changed = Signal(object)          # LicenseView, always delivered on the UI thread

    def __init__(self, manager: LicenseManager, build_suite: Callable[[], object],
                 on_language: Callable[[str], None] | None = None, busy: Callable[[], bool] | None = None):
        super().__init__()
        self.m = manager
        self._build = build_suite
        self._on_language = on_language
        self._busy = busy or (lambda: False)
        self.gate = None
        self.win = None
        self.minimized = False
        self._last = None
        manager.listeners.append(lambda view: self.view_changed.emit(view))
        self.view_changed.connect(self._changed)
        self.tick = QTimer(self)
        self.tick.setInterval(TICK_MS)
        self.tick.timeout.connect(lambda: self._changed(self.m.view()))
        self.check = QTimer(self)
        self.check.setInterval(CHECK_MS)
        self.check.timeout.connect(self.check_in_background)

    def start(self) -> None:
        view = self.m.view()
        self.tick.start()
        self.check.start()
        if view.unlocked:
            self.open_suite(view)
        else:
            self.show_gate()
        self.check_in_background()

    def stop(self) -> None:
        """No more checks (application exit, tests)."""
        self.tick.stop()
        self.check.stop()

    def check_in_background(self) -> None:
        if self.m.due():
            workers.run(self.m.check_if_due, lambda view: self._changed(view), lambda e: None)

    # -- windows -----------------------------------------------------------------------------------------------------
    def show_gate(self, view: LicenseView | None = None) -> None:
        from akeno.ui.license_gate import LicenseGate
        if self.gate is None:
            self.gate = LicenseGate(self.m)
            self.gate.unlocked.connect(self.open_suite)
            self.gate.language_changed.connect(self._language)
        if view is not None:
            self.gate.refresh(view)
        self.gate.show()
        self.gate.raise_()
        self.gate.activateWindow()

    def _language(self, lang: str) -> None:
        if self._on_language:
            self._on_language(lang)
        old = self.gate
        self.gate = None
        self.show_gate()
        if old is not None:
            old.hide()
            old.deleteLater()

    def open_suite(self, view: LicenseView) -> None:
        if self.win is None:
            self.win = self._build()
        if self.gate is not None:
            self.gate.hide()
            self.gate.deleteLater()
            self.gate = None
        if self.minimized:
            self.win.showMinimized()
            self.minimized = False
        else:
            self.win.show()
            self.win.raise_()

    # -- state -------------------------------------------------------------------------------------------------------
    def _changed(self, view: LicenseView) -> None:
        if self.win is not None:
            for page in getattr(self.win, "pages", {}).values():
                if hasattr(page, "show_view"):
                    page.show_view(view)
        if view.unlocked:
            if (self.win is None or not self.win.isVisible()) and self.gate is not None:
                self.open_suite(view)
            self._last = view.status
            return
        if self.win is not None and self.win.isVisible():
            if self._busy():
                log.info("license ended during a running task; locking when it has finished")
                return
            log.info("license not active (%s): locking the suite", view.status.value)
            self.win.hide()
            self.show_gate(view)
        elif self.gate is not None and view.status != self._last:
            self.gate.refresh(view)              # only on a change: never resets what someone is typing
        self._last = view.status
