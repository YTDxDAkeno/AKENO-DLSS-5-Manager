"""Advanced: diagnostics and expert tools that are not needed for an ordinary installation."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.widgets import label

# page key -> tab title. These pages were top-level in v1.x; they stay fully functional here.
TABS = (("dashboard", "Diagnostics overview"), ("benchmark", "Benchmark"), ("profiles", "Smart Profiles"),
        ("install", "Expert tools and legacy installer"))


class AdvancedPage(QWidget):
    def __init__(self, ctl, window, pages: dict):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label(t("Advanced"), "h1"))
        lay.addWidget(label(t("Optional diagnostics: hardware evidence, frame-time measurement, runtime verification and "
                              "the older per-backend installer. Normal installations happen in My Games."),
                            "dim", wrap=True))
        self.tabs = QTabWidget()
        self.keys: list[str] = []
        for key, title in TABS:
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 8, 0, 0)
            hl.addWidget(pages[key], 1)     # the window's page host scrolls; no nested scroll areas
            footer = getattr(pages[key], "footer", None)
            if isinstance(footer, QWidget):
                hl.addWidget(footer)          # e.g. the overview's pinned action buttons
            self.tabs.addTab(holder, t(title))
            self.keys.append(key)
        self.pages = pages
        self.tabs.currentChanged.connect(self._shown)
        lay.addWidget(self.tabs, 1)

    def show_tab(self, key: str) -> None:
        if key in self.keys:
            self.tabs.setCurrentIndex(self.keys.index(key))
            self._shown(self.keys.index(key))

    def _shown(self, index: int) -> None:
        page = self.pages.get(self.keys[index]) if 0 <= index < len(self.keys) else None
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def on_show(self) -> None:
        self._shown(self.tabs.currentIndex())
