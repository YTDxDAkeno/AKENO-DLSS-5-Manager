"""The page shown for a module the current license does not include.

It stands in for the real page, which is not built at all: nothing of the module is loaded, scanned or started.
Calls the window makes on the real page (``on_show``, ``auto_check``, …) do nothing here. Redeeming a code that
includes the module rebuilds the window with the real page.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.widgets import Card, EmptyState, PageHeader, button

LOCKED_TEXT = "This module is not included in your current AKENO license."


class LockedModulePage(QWidget):
    def __init__(self, key: str, title: str, window=None):
        super().__init__()
        self.key, self.window_ = key, window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(PageHeader(t(title)))
        card = Card()
        self.redeem_button = button(t("Redeem activation code"), "key", "primary", on_click=self.redeem)
        card.body.addWidget(EmptyState("lock", t(LOCKED_TEXT),
                                       t("Your license and settings stay as they are. A code for this module or "
                                         "for AKENO Full unlocks it right away."), self.redeem_button))
        lay.addWidget(card)
        lay.addStretch(1)

    def redeem(self) -> None:
        if self.window_ is not None and hasattr(self.window_, "go"):
            self.window_.go("license")

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None
