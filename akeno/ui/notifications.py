"""Native notifications (Windows toast through the tray icon), in categories the user switches on or off."""

from __future__ import annotations

from akeno import APP_NAME
from akeno.i18n import t

CATEGORIES = {"tasks": "Installs, restores, benchmarks and scans finished",
              "updates": "A new AKENO version is available",
              "license": "License ends soon or needs a check",
              "sync": "Cloud Sync conflicts",
              "games": "FPS Booster session settings when a game starts"}
DEFAULT = {"tasks": True, "updates": True, "license": True, "sync": True, "games": False}


class Notifier:
    def __init__(self, settings, tray=None, window=None):
        self.settings = settings
        self.tray = tray
        self.window = window
        self.sent: set[str] = set()

    def enabled(self, category: str) -> bool:
        if not self.settings.get("notifications"):
            return False
        chosen = self.settings.get("notification_categories") or {}
        return bool(chosen.get(category, DEFAULT.get(category, True)))

    def set_enabled(self, category: str, on: bool) -> None:
        chosen = dict(self.settings.get("notification_categories") or DEFAULT)
        chosen[category] = bool(on)
        self.settings.set("notification_categories", chosen)

    def notify(self, category: str, text: str, once: str | None = None, when_hidden: bool = False) -> bool:
        """Show ``text``; ``once`` keeps a message from repeating in this run."""
        if self.tray is None or not self.enabled(category) or (once and once in self.sent):
            return False
        if when_hidden and self.window is not None and self.window.isActiveWindow():
            return False
        if once:
            self.sent.add(once)
        from PySide6.QtWidgets import QSystemTrayIcon
        self.tray.showMessage(APP_NAME, t(text) if isinstance(text, str) else str(text),
                              QSystemTrayIcon.MessageIcon.Information, 5000)
        return True
