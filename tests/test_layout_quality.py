"""Every page at the window sizes AKENO must support: nothing clipped, nothing wider than the window.

1920x1009 is a maximised window on a 1920x1080 screen at 100 % (and a 4K screen at 200 %); 1280x680 is the
same screen at 150 % (and a 2560x1440 screen at 200 %). Logical pixels, so the checks hold for every scale."""

from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLabel
from PySide6.QtTest import QTest

SIZES = [(1920, 1009), (1280, 680)]


@pytest.fixture
def window(qtbot, akeno_home, tmp_path):
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
    from akeno.ui import interaction
    i18n.set_language("en")
    theme.apply(QApplication.instance())
    interaction.install(QApplication.instance())            # as the app does
    svc = AppServices()
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.show()
    QTest.qWaitForWindowExposed(win)
    yield win
    from akeno.ui import workers
    workers.drain(30000)
    svc.db.close()


def _visible(w) -> bool:
    return shiboken6.isValid(w) and w.isVisible() and not w.visibleRegion().isEmpty()


def _clipped_label(lb: QLabel) -> bool:
    text = lb.text()
    if not text or lb.wordWrap() or "<" in text or lb.textFormat() == Qt.TextFormat.RichText or "\n" in text or lb.pixmap() is not None \
            and not lb.pixmap().isNull():
        return False
    if type(lb).__name__ == "ElidedLabel":                  # shortens on purpose, full text in the tooltip
        return False
    m = lb.contentsMargins()
    return QFontMetrics(lb.font()).horizontalAdvance(text) > lb.width() - m.left() - m.right() + 1


@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_no_page_is_wider_than_the_window_and_nothing_is_clipped(window, qtbot, size):
    from akeno.ui.main_window import NAV
    win = window
    win.setMinimumSize(1100 if size[0] > 1100 else size[0], 680)
    win.resize(*size)
    qtbot.wait(100)
    problems = []
    for key, _, _ in NAV:
        win.go(key)
        qtbot.wait(60)
        host = win.hosts[key]
        content = host.scroll.widget()
        if content.minimumSizeHint().width() > host.scroll.viewport().width() + 1:
            problems.append(f"{key}: page needs {content.minimumSizeHint().width()} px, has "
                            f"{host.scroll.viewport().width()}")
        for spin in host.findChildren(QAbstractSpinBox):
            if _visible(spin) and spin.lineEdit() is not None:
                need = QFontMetrics(spin.font()).horizontalAdvance(spin.text())
                if need > spin.lineEdit().width():
                    problems.append(f"{key}: spin box {spin.accessibleName() or spin.text()!r} shows {spin.lineEdit().width()}"
                                    f" of {need} px")
        for lb in host.findChildren(QLabel):
            if _visible(lb) and _clipped_label(lb):
                problems.append(f"{key}: label {lb.text()[:60]!r} is {lb.width()} px wide")
    assert not problems, "\n".join(problems[:40])
