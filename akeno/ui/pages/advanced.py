"""Advanced: diagnostics and expert tools that are not needed for an ordinary installation."""

from __future__ import annotations

from PySide6.QtWidgets import QTabBar, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.widgets import PageHeader, label

# page key -> tab title. These pages were top-level in v1.x; they stay fully functional here.
TABS = (("dashboard", "Diagnostics overview"), ("profiles", "Smart Profiles"),
        ("install", "Expert tools and legacy installer"))           # Benchmark is a page of its own since 3.0


class AdvancedPage(QWidget):
    """A tab strip over the expert pages. Only the visible page sits in the layout, so the window's minimum
    size follows that page alone (a QTabWidget would take the widest of all four)."""

    def __init__(self, ctl, window, pages: dict):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(PageHeader(t("Advanced"), t("Optional diagnostics: hardware evidence, frame-time measurement, runtime verification and "
                              "the older per-backend installer. Normal installations happen in My Games.")))
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.keys: list[str] = []
        self.holders: dict[str, QWidget] = {}
        self.pool = QWidget(self)
        self.pool.hide()
        for key, title in TABS:
            holder = QWidget(self.pool)
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 8, 0, 0)
            hl.addWidget(pages[key], 1)     # the window's page host scrolls; no nested scroll areas
            footer = getattr(pages[key], "footer", None)
            if isinstance(footer, QWidget):
                hl.addWidget(footer)          # e.g. the overview's pinned action buttons
            self.holders[key] = holder
            self.tabs.addTab(t(title))
            self.keys.append(key)
        self.pages = pages
        lay.addWidget(self.tabs)
        self.area = QVBoxLayout()
        lay.addLayout(self.area, 1)
        self.current: str | None = None
        self._place(self.keys[0])
        self.tabs.currentChanged.connect(self._shown)

    def _place(self, key: str) -> None:
        if self.current == key:
            return
        if self.current is not None:
            old = self.holders[self.current]
            self.area.removeWidget(old)
            old.setParent(self.pool)
        new = self.holders[key]
        new.setParent(self)
        self.area.addWidget(new, 1)
        new.show()
        self.current = key

    def show_tab(self, key: str) -> None:
        if key in self.keys:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self.keys.index(key))
            self.tabs.blockSignals(False)
            self._shown(self.keys.index(key))

    def _shown(self, index: int) -> None:
        if not 0 <= index < len(self.keys):
            return
        key = self.keys[index]
        self._place(key)
        page = self.pages.get(key)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def on_show(self) -> None:
        self._shown(self.tabs.currentIndex())
