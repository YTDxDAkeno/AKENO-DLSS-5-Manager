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
    i18n.set_language("en")                                  # the language is global: later tests expect English


def _visible(w) -> bool:
    return shiboken6.isValid(w) and w.isVisible() and not w.visibleRegion().isEmpty()


def _clipped_label(lb: QLabel) -> bool:
    text = lb.text()
    if text and lb.wordWrap():                              # wrapped text must get the height of all its lines
        need = lb.heightForWidth(lb.width())
        return need > lb.height() + 2
    if not text or lb.wordWrap() or "<" in text or lb.textFormat() == Qt.TextFormat.RichText or "\n" in text or lb.pixmap() is not None \
            and not lb.pixmap().isNull():
        return False
    if type(lb).__name__ == "ElidedLabel":                  # shortens on purpose, full text in the tooltip
        return False
    m = lb.contentsMargins()
    return QFontMetrics(lb.font()).horizontalAdvance(text) > lb.width() - m.left() - m.right() + 1


def _scan(key, host, problems) -> None:
    """Every scroll area of the page, one screen at a time: spin boxes and labels must show all their text."""
    from PySide6.QtWidgets import QScrollArea
    areas = [host.scroll] + [a for a in host.findChildren(QScrollArea) if _visible(a)]
    for area in areas:
        bar = area.verticalScrollBar()
        step = max(200, area.viewport().height() - 40)
        for pos in range(0, bar.maximum() + step, step):
            bar.setValue(min(pos, bar.maximum()))
            QApplication.processEvents()
            for spin in host.findChildren(QAbstractSpinBox):
                if _visible(spin) and spin.lineEdit() is not None:
                    need = QFontMetrics(spin.font()).horizontalAdvance(spin.text())
                    if need > spin.lineEdit().width():
                        problems.append(f"{key}: spin box {spin.accessibleName() or spin.text()!r} shows "
                                        f"{spin.lineEdit().width()} of {need} px")
            for lb in host.findChildren(QLabel):
                if _visible(lb) and _clipped_label(lb):
                    problems.append(f"{key}: label {lb.text()[:60]!r} is {lb.width()}x{lb.height()} px")
        bar.setValue(0)


CASES = [("en", size) for size in SIZES] + [("de", (1280, 680))]     # German texts run longest


@pytest.mark.parametrize("lang,size", CASES, ids=[f"{lang}-{w}x{h}" for lang, (w, h) in CASES])
def test_no_page_is_wider_than_the_window_and_nothing_is_clipped(window, qtbot, lang, size):
    from akeno.ui.main_window import ADVANCED, NAV
    win = window
    if lang != "en":
        win.s.settings.set("language", lang)
        win.ctl.settings_changed.emit("language")              # the window rebuilds every page in that language
        qtbot.wait(200)
    win.setMinimumSize(1100 if size[0] > 1100 else size[0], 680)
    win.resize(*size)
    qtbot.wait(100)
    problems = []
    views = [(key, None) for key, _, _ in NAV] + [(tab, None) for tab in ADVANCED] + [("wallpapers", 1)]
    for key, tab in views:
        win.go(key)
        if tab is not None:
            win.pages[key].tabs.setCurrentIndex(tab)
        qtbot.wait(60)
        name = f"{key}#{tab}" if tab is not None else key
        host = win.hosts["advanced" if key in ADVANCED else key]
        content = host.scroll.widget()
        if content.minimumSizeHint().width() > host.scroll.viewport().width() + 1:
            problems.append(f"{name}: page needs {content.minimumSizeHint().width()} px, has "
                            f"{host.scroll.viewport().width()}")
        _scan(name, host, problems)
        if tab is not None:
            win.pages[key].tabs.setCurrentIndex(0)
    problems = list(dict.fromkeys(problems))
    assert not problems, "\n".join(problems[:40])


def test_dialogs_never_open_larger_than_the_screen(window, qtbot):
    """The offscreen test screen is 800x800, smaller than any of these dialogs asks for."""
    from akeno.ui.community_runtime import CommunityRuntimeDialog
    from akeno.ui.license_gate import LicenseGate
    from akeno.ui.pipelines import PipelineDialog
    avail = QApplication.primaryScreen().availableGeometry()
    for dialog in (CommunityRuntimeDialog(window.ctl, window), LicenseGate(window.license),
                   PipelineDialog(window.ctl, window)):
        qtbot.addWidget(dialog)
        assert dialog.width() <= avail.width() and dialog.height() <= avail.height(), type(dialog).__name__
        assert dialog.minimumWidth() <= avail.width() and dialog.minimumHeight() <= avail.height()
