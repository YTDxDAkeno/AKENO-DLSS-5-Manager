"""The real main window driving the stack engine (offscreen Qt, fixture games, local HTTP stand-in)."""

import os

import pytest
from PySide6.QtWidgets import QDialog

from stack_fixtures import dlss_game, fivem_install, plain_game, services


@pytest.fixture
def ui(qtbot, tmp_path, db, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "AKENO-PC")
    svc, fx = services(tmp_path, db, monkeypatch)
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    from akeno.ui import stack_panel, theme, widgets
    from PySide6.QtWidgets import QApplication
    QApplication.instance().setFont(theme.ui_font(10))
    QApplication.instance().setStyleSheet(theme.stylesheet())    # the real theme, so layouts are tested as shipped
    monkeypatch.setattr(stack_panel.PreviewDialog, "exec", lambda self: (
        self.approve.setChecked(True) if self.approve else None, QDialog.DialogCode.Accepted)[1])
    monkeypatch.setattr(stack_panel, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: True)
    ctl = Controller(svc)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    win.resize(1500, 950)
    win.show()
    yield svc, fx, ctl, win
    from akeno.ui import workers
    workers.drain(20000)


def wait_state(qtbot, panel):
    qtbot.waitUntil(lambda: panel.state is not None and not panel.busy, timeout=15000)


def shot(qtbot, win, name):
    """Optional evidence screenshot (AKENO_V2_SCREENSHOTS); fixture data, never hardware evidence."""
    folder = os.environ.get("AKENO_V2_SCREENSHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        qtbot.wait(300)                    # widgets added by the render are shown on the next event loop pass
        win.grab().save(os.path.join(folder, name + ".png"))


def test_navigation_has_the_product_pages_and_advanced_tabs(ui):
    svc, fx, ctl, win = ui
    from akeno.ui.main_window import NAV
    assert [k for k, _, _ in NAV][:5] == ["library", "fivem", "installed", "downloads", "backups"]
    for key in ("dashboard", "benchmark", "profiles", "install"):
        win.go(key)
        assert win.current_page() == key
    win.go("library")
    assert win.current_page() == "library"


def test_install_preset_and_remove_from_my_games(ui, qtbot, tmp_path):
    svc, fx, ctl, win = ui
    game, exe_dir = dlss_game(svc, tmp_path)
    ctl.games_changed.emit()
    ctl.select_game(game.id)
    page = win.pages["library"]
    panel = page.panel
    assert panel is not None and panel.game_id == game.id
    wait_state(qtbot, panel)
    assert panel.btn_install.isEnabled()
    assert panel.state.recommended.recipe.id == "optiscaler"
    panel.btn_install.click()
    qtbot.waitUntil(lambda: (exe_dir / "winmm.dll").is_file() and not panel.busy, timeout=20000)
    qtbot.waitUntil(lambda: panel.state.installed is not None, timeout=10000)
    assert "Installation complete" in panel.status.text()
    shot(qtbot, win, "my-games-installed")
    panel.preset_buttons["performance"].click()
    qtbot.waitUntil(lambda: "CacheInterval=2" in (exe_dir / "OptiScaler.ini").read_text(), timeout=15000)
    win.go("installed")
    qtbot.waitUntil(lambda: win.pages["installed"].body.count() == 1, timeout=5000)
    shot(qtbot, win, "installed-mods")
    win.go("library")
    wait_state(qtbot, panel)
    qtbot.waitUntil(lambda: panel.btn_remove.isEnabled(), timeout=10000)
    panel.btn_remove.click()
    qtbot.waitUntil(lambda: not (exe_dir / "winmm.dll").exists() and not panel.busy, timeout=15000)
    assert sorted(p.name for p in exe_dir.iterdir()) == ["NightCity.exe", "nvngx_dlss.dll"]


def test_choosing_another_method_and_version_updates_the_panel(ui, qtbot, tmp_path):
    svc, fx, ctl, win = ui
    game, root = plain_game(svc, tmp_path)
    ctl.games_changed.emit()
    ctl.select_game(game.id)
    panel = win.pages["library"].panel
    wait_state(qtbot, panel)
    assert panel.state.recommended.recipe.id == "feeder"
    panel._choose_recipe("feeder_optiscaler")
    qtbot.waitUntil(lambda: panel.state.selected.recipe.id == "feeder_optiscaler", timeout=10000)
    roles = [r.role for r in panel.state.selected.roles]
    assert "consumer_optiscaler" in roles and "feeder" in roles
    shot(qtbot, win, "my-games-feeder-optiscaler")


def test_fivem_page_installs_into_plugins(ui, qtbot, tmp_path):
    svc, fx, ctl, win = ui
    game, root, app, gta = fivem_install(svc, tmp_path)
    ctl.games_changed.emit()
    win.go("fivem")
    page = win.pages["fivem"]
    qtbot.waitUntil(lambda: page.panel is not None, timeout=5000)
    panel = page.panel
    wait_state(qtbot, panel)
    assert any(i.code == "fivem_ack" for i in panel.state.recommended.issues)
    shot(qtbot, win, "fivem-legacy")
    panel.btn_install.click()
    qtbot.waitUntil(lambda: (app / "plugins" / "d3d11.dll").is_file() and not panel.busy, timeout=20000)
    assert "ReShade5=" in (app / "CitizenFX.ini").read_text()


def test_downloads_page_downloads_and_deletes(ui, qtbot):
    svc, fx, ctl, win = ui
    win.go("downloads")
    page = win.pages["downloads"]
    key = "nvngx-dlss:310.9.16"
    page.download(key)
    v = svc.stack.catalog.version(key)
    qtbot.waitUntil(lambda: svc.stack.store.is_ready(v) and not page.busy, timeout=15000)
    assert "Downloaded and verified" in page.status.text()
    shot(qtbot, win, "downloads")
    page.delete(key)
    assert not svc.stack.store.is_ready(v)


def test_german_ui_renders_game_page(qtbot, tmp_path, db, monkeypatch):
    from akeno import i18n
    i18n.set_language("de")
    try:
        svc, fx = services(tmp_path, db, monkeypatch)
        game, _ = dlss_game(svc, tmp_path)
        from akeno.ui.controller import Controller
        from akeno.ui.main_window import MainWindow
        ctl = Controller(svc)
        from akeno.ui import theme
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(theme.stylesheet())
        win = MainWindow(ctl)
        qtbot.addWidget(win)
        win.resize(1280, 720)
        win.show()
        ctl.select_game(game.id)
        panel = win.pages["library"].panel
        wait_state(qtbot, panel)
        assert panel.btn_install.text() == "DLSS 5 installieren"
        shot(qtbot, win, "my-games-de")
    finally:
        i18n.set_language("en")
        from akeno.ui import workers
        workers.drain(20000)


def test_advanced_overview_agrees_with_my_games(ui, qtbot, tmp_path):
    svc, fx, ctl, win = ui
    game, root, app, gta = fivem_install(svc, tmp_path)
    ctl.games_changed.emit()
    ctl.select_game(game.id)
    win.go("dashboard")
    page = win.pages["dashboard"]
    page.refresh()
    assert page.stack_state is not None and page.stack_state.recommended.recipe.id == "fivem"
    assert "DLSS5-Feeder" in page.hero_profile.text() and page.hero_value.text() == "50%"   # RTX 4080: Performance
    assert "Install unavailable" not in page.action_note.text()
    shot(qtbot, win, "advanced-overview-fivem")
    page.a_install.click()
    assert win.current_page() == "fivem"
