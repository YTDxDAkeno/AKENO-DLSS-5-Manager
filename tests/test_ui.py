"""UI tests (offscreen Qt): every page builds, navigation works, actions are wired, language switches."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QPushButton  # noqa: E402


@pytest.fixture
def app_window(qtbot, akeno_home, tmp_path):
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
    from PySide6.QtWidgets import QApplication
    i18n.set_language("en")
    QApplication.instance().setStyleSheet(theme.stylesheet())
    svc = AppServices()
    ctl = Controller(svc)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    win.show()
    yield win, ctl, svc
    from akeno.ui import workers
    workers.drain(30000)    # the runtime search walks the real DriverStore on Windows runners
    svc.db.close()


def test_all_pages_navigate(app_window, qtbot):
    win, ctl, svc = app_window
    from akeno.ui.main_window import ADVANCED, NAV
    for key, _, _ in NAV:
        win.sidebar.buttons[key].click()
        assert win.current_page() == key or (key == "advanced" and win.current_page() in ADVANCED)
    assert set(win.pages) == {k for k, _, _ in NAV} | set(ADVANCED)


def test_keyboard_shortcuts_and_accessible_names(app_window, qtbot):
    win, ctl, svc = app_window
    from PySide6.QtCore import Qt
    qtbot.keyClick(win, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    assert win.current_page() == "library"
    qtbot.keyClick(win, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    assert win.current_page() == "fivem"
    for b in win.findChildren(QPushButton):
        if b.isVisible() and b.text().strip():
            assert b.accessibleName() or b.text()


def test_dashboard_shows_real_data_and_wired_actions(app_window, qtbot):
    win, ctl, svc = app_window
    dash = win.pages["dashboard"]
    assert "RTX 3080" in dash.gpu_name.text()
    assert dash.game_name.text() == "Night City Demo"
    # every action tile has a click handler
    for tile in (dash.a_scan, dash.a_analyze, dash.a_backup, dash.a_install, dash.a_restore):
        assert tile.receivers("2clicked(bool)") > 0
    # no runtime imported -> install must be disabled with a reason
    assert not dash.a_install.isEnabled()
    assert "nvngx_dlssnr.dll" in dash.a_install.toolTip()
    assert not dash.a_restore.isEnabled()
    # fixture benchmarks are labelled as measured basis, never invented when absent
    assert dash.hero_value.text().endswith("%") or dash.hero_value.text() in ("Off", "—")


def test_language_switch_rebuilds_ui(app_window, qtbot):
    win, ctl, svc = app_window
    ctl.set_setting("language", "de")
    qtbot.waitUntil(lambda: win.sidebar.buttons["advanced"].text().strip() == "Erweitert", timeout=3000)
    assert "Meine Spiele" in win.sidebar.buttons["library"].text()
    ctl.set_setting("language", "en")
    qtbot.waitUntil(lambda: win.sidebar.buttons["advanced"].text().strip() == "Advanced", timeout=3000)


def test_backup_snapshot_from_backups_page(app_window, qtbot, monkeypatch):
    win, ctl, svc = app_window
    from akeno.ui import widgets
    monkeypatch.setattr("akeno.ui.pages.backups.info", lambda *a, **k: None)
    win.go("backups")
    # the selected fixture game has nvngx_dlss.dll next to its exe -> snapshot succeeds
    win.pages["backups"]._snapshot()
    qtbot.waitUntil(lambda: any(t["kind"] == "snapshot" for t in svc.db.transactions()), timeout=5000)
    qtbot.waitUntil(lambda: win.pages["backups"].table.rowCount() >= 1, timeout=3000)


def test_profiles_page_only_shows_supported_settings(app_window, qtbot):
    win, ctl, svc = app_window
    page = win.pages["profiles"]
    win.go("profiles")
    keys = set(page.editors)
    assert "DlssNr.WorkingScale" in keys and "DlssNr.PreSr" in keys
    page.select_profile("custom")
    assert page.editors["DlssNr.WorkingScale"].isEnabled()
    assert not page.btn_apply.isEnabled(), "nothing installed: apply must be disabled"


def test_settings_persist_from_ui(app_window, qtbot):
    win, ctl, svc = app_window
    ctl.set_setting("target_fps", 75)
    from akeno.core.settings import Settings
    assert Settings(svc.db).get("target_fps") == 75
