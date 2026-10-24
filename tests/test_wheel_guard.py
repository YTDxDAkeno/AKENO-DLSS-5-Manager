"""The mouse wheel scrolls pages and never changes a control by passing over it (akeno/ui/interaction.py).

Wheel turns are sent through the window system (QTest.wheelEvent), so they travel the same way as a real mouse:
to the widget under the pointer first, then on to its parents."""

from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QTabBar, QVBoxLayout, QWidget)

from akeno.ui import interaction


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def guard(qapp):
    clock = Clock()
    g = interaction.WheelGuard(qapp, clock=clock)
    qapp.installEventFilter(g)
    yield g, clock
    qapp.removeEventFilter(g)
    g.deleteLater()


@pytest.fixture
def page(qtbot, guard):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.resize(420, 320)
    content = QWidget()
    lay = QVBoxLayout(content)
    start = QPushButton("start")                    # takes the window's first focus, like a page's first button
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    combo = QComboBox()
    combo.addItems([str(i) for i in range(10)])
    combo.setCurrentIndex(5)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(40)
    tabs = QTabBar()
    for name in ("one", "two", "three"):
        tabs.addTab(name)
    tabs.setCurrentIndex(1)
    items = QListWidget()
    items.addItems([f"item {i}" for i in range(3)])  # shorter than the list: nothing to scroll
    items.setFixedHeight(120)
    for w in (start, spin, combo, slider, tabs, items):
        lay.addWidget(w)
    lay.addSpacing(2000)
    area.setWidget(content)
    qtbot.addWidget(area)
    area.show()
    QTest.qWaitForWindowExposed(area)
    start.setFocus()
    ns = type("Page", (), {})()
    ns.area, ns.start, ns.spin, ns.combo, ns.slider, ns.tabs, ns.items = area, start, spin, combo, slider, tabs, items
    return ns


def wheel(window: QWidget, target: QWidget, dy: int = -120) -> None:
    pos = target.mapTo(window, target.rect().center())
    QTest.wheelEvent(window.windowHandle(), QPointF(pos), QPoint(0, dy))
    QApplication.processEvents()


def values(p):
    return p.spin.value(), p.combo.currentIndex(), p.slider.value(), p.tabs.currentIndex()


def test_wheel_over_unfocused_controls_scrolls_the_page_and_changes_nothing(page, guard):
    _, clock = guard
    before = values(page)
    bar = page.area.verticalScrollBar()
    for ctrl in (page.spin, page.combo, page.slider, page.tabs):
        bar.setValue(0)
        clock.now += 5
        wheel(page.area, ctrl)
        assert bar.value() > 0, f"the page scrolls over {type(ctrl).__name__}"
        assert not ctrl.hasFocus(), f"the wheel gives {type(ctrl).__name__} no focus"
    assert values(page) == before


def test_focused_control_takes_a_wheel_that_starts_on_it(page, guard):
    _, clock = guard
    page.spin.setFocus()
    clock.now += 5
    wheel(page.area, page.spin)
    assert page.spin.value() == 49
    clock.now += 0.1                                   # the same gesture goes on adjusting the value
    wheel(page.area, page.spin)
    assert page.spin.value() == 48


def test_page_scroll_passing_over_a_focused_control_keeps_scrolling_the_page(page, guard):
    _, clock = guard
    page.combo.setFocus()
    bar = page.area.verticalScrollBar()
    clock.now += 5
    wheel(page.area, page.start)                       # the gesture starts on the page...
    first = bar.value()
    assert first > 0
    clock.now += 0.1
    bar.setValue(0)                                    # ...and the focused combo box is under the pointer now
    wheel(page.area, page.combo)
    assert page.combo.currentIndex() == 5 and bar.value() > 0
    clock.now += interaction.LATCH + 0.1               # after a pause the user means the combo box
    wheel(page.area, page.combo)
    assert page.combo.currentIndex() == 6


def test_wheel_never_gives_focus(qtbot, guard):
    w = QWidget()
    lay = QVBoxLayout(w)
    spin, combo, text = QSpinBox(), QComboBox(), QPlainTextEdit()
    for c in (spin, combo, text):
        lay.addWidget(c)
    qtbot.addWidget(w)
    w.show()
    QTest.qWaitForWindowExposed(w)
    for c in (spin, combo, text):
        assert c.focusPolicy() == Qt.FocusPolicy.StrongFocus, type(c).__name__


def test_nested_list_without_room_to_scroll_hands_the_wheel_to_the_page(page, guard):
    _, clock = guard
    bar = page.area.verticalScrollBar()
    bar.setValue(0)
    clock.now += 5
    wheel(page.area, page.items)
    assert bar.value() > 0


def test_nested_list_scrolls_itself_while_it_can(page, guard):
    _, clock = guard
    page.items.addItems([f"more {i}" for i in range(60)])
    inner = page.items.verticalScrollBar()
    outer = page.area.verticalScrollBar()
    outer.setValue(0)
    inner.setValue(0)
    clock.now += 5
    wheel(page.area, page.items)
    assert inner.value() > 0 and outer.value() == 0


def test_install_is_idempotent(qapp):
    first = interaction.install(qapp)
    assert interaction.install(qapp) is first


@pytest.fixture
def window_with_fixture_games(qtbot, akeno_home, tmp_path):
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    fixture_home = importlib.import_module("fixture_home")
    sys.argv = ["x", str(akeno_home), str(tmp_path / "games"), "--with-benchmarks"]
    fixture_home.main()
    from akeno import i18n
    from akeno.services import AppServices
    from akeno.ui import theme
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    i18n.set_language("en")
    QApplication.instance().setStyleSheet(theme.stylesheet())
    svc = AppServices()
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.resize(1600, 1000)
    win.show()
    QTest.qWaitForWindowExposed(win)
    yield win
    from akeno.ui import workers
    workers.drain(30000)
    svc.db.close()


def test_no_control_on_any_page_changes_when_the_wheel_passes_over_it(window_with_fixture_games, qtbot):
    """Every page of the suite: a wheel turn over each visible spin box, combo box, slider and tab bar."""
    win = window_with_fixture_games
    interaction.install(QApplication.instance())
    from akeno.ui.main_window import NAV
    checked = 0
    for key, _, _ in NAV:
        win.go(key)
        qtbot.wait(30)
        host = win.hosts[key]
        for ctrl in host.findChildren(QWidget):
            if not shiboken6.isValid(ctrl):              # a page rebuilt part of itself meanwhile
                continue
            if not interaction._guarded(ctrl) or not ctrl.isVisible() or not ctrl.isEnabled():
                continue
            centre = ctrl.rect().center()
            if not ctrl.visibleRegion().contains(centre):
                continue
            state = _state(ctrl)
            win.setFocus()
            wheel(win, ctrl)
            assert _state(ctrl) == state, f"{key}: {type(ctrl).__name__} {ctrl.objectName()} changed"
            assert not ctrl.hasFocus()
            checked += 1
    assert checked >= 20, "the pages have value controls to check"


def _state(ctrl):
    for attr in ("value", "currentIndex"):
        if hasattr(ctrl, attr):
            return getattr(ctrl, attr)()
    return None
