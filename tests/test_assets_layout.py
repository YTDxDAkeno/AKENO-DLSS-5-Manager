"""Design assets (bundling, High-DPI scaling, fallback) and window layout regressions."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]


def test_every_required_asset_is_bundled_and_decodes(qapp):
    from akeno.ui import assets
    assert assets.assets_dir() == ROOT / "akeno" / "data" / "assets"
    assert assets.verify_all() == {}
    assert "19/19 OK" in assets.summary()


def test_pyinstaller_spec_and_package_data_bundle_assets():
    spec = (ROOT / "packaging" / "akeno.spec").read_text()
    assert '(str(ROOT / "akeno" / "data"), "akeno/data")' in spec, "assets live under akeno/data"
    toml = (ROOT / "pyproject.toml").read_text()
    assert "data/assets/" in toml


def test_frozen_layout_resolves_assets_from_meipass(monkeypatch, tmp_path, qapp):
    from akeno import paths
    from akeno.ui import assets
    fake = tmp_path / "bundle"
    (fake / "akeno").mkdir(parents=True)
    shutil.copytree(ROOT / "akeno" / "data", fake / "akeno" / "data")   # symlinks need admin rights on Windows
    monkeypatch.setattr(sys, "_MEIPASS", str(fake), raising=False)
    assets.image.cache_clear()
    assets.svg_bytes.cache_clear()
    try:
        assert paths.resource_dir() == fake / "akeno" / "data"
        assert assets.verify_all() == {}
    finally:
        assets.image.cache_clear()
        assets.svg_bytes.cache_clear()


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_artwork_scales_crisply_for_each_device_pixel_ratio(qapp, dpr):
    from akeno.ui import art, assets
    pm = assets.cover(assets.HEADER, 1600, 110, dpr)
    assert pm.devicePixelRatio() == dpr and pm.width() == round(1600 * dpr) and pm.height() == round(110 * dpr)
    hdr = art.header_background(1600, 110, dpr)
    assert assets.logical_size(hdr).width() == 1600
    gpu = art.gpu_illustration(230, 160, dpr)
    s = assets.logical_size(gpu)
    assert s.width() <= 230 and s.height() <= 160 and gpu.hasAlphaChannel()
    logo = assets.svg_pixmap(assets.LOGO, 50, 50, dpr)
    assert logo.width() == round(50 * dpr)


def test_cover_keeps_aspect_ratio_on_ultrawide(qapp):
    from akeno.ui import assets
    src = assets.image(assets.HEADER)
    pm = assets.cover(assets.HEADER, 3200, 110, 1.0, h_align=1.0)
    # 1920x300 scaled to cover 3200x110 -> 3200 wide, 500 tall, cropped vertically: no stretching
    assert pm.width() == 3200 and pm.height() == 110
    assert src.width() / src.height() == pytest.approx(6.4)


def test_missing_asset_falls_back_to_procedural_art(qapp, monkeypatch, tmp_path):
    from akeno.ui import art, assets
    monkeypatch.setattr(assets, "assets_dir", lambda: tmp_path / "nothing")
    assets.image.cache_clear()
    assets.svg_bytes.cache_clear()
    try:
        assert assets.verify_all()
        assert not art.header_background(800, 110).isNull()
        assert not art.gpu_illustration(200, 140).isNull()
        assert not art.logo_mark(48).isNull() and art.wordmark(40).isNull()
        assert not art.app_icon_pixmap(64).isNull()
    finally:
        assets.image.cache_clear()
        assets.svg_bytes.cache_clear()


# -- layout ------------------------------------------------------------------------------------------
@pytest.fixture
def window(qtbot, akeno_home, tmp_path):
    import importlib
    sys.path.insert(0, str(ROOT / "tools"))
    fixture_home = importlib.import_module("fixture_home")
    sys.argv = ["x", str(akeno_home), str(tmp_path / "games"), "--with-benchmarks", "--gpu", "rtx5090"]
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
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.show()
    yield win
    from akeno.ui import workers
    workers.drain(30000)    # the runtime search walks the real DriverStore on Windows runners
    svc.db.close()


def _settle(qtbot, win, w, h):
    win.setMinimumSize(min(w, 1100), min(h, 680))
    win.resize(w, h)
    qtbot.wait(150)


def test_dashboard_fits_maximised_1920x1080_without_scrolling(window, qtbot):
    win = window
    _settle(qtbot, win, 1920, 1009)
    win.go("dashboard")
    qtbot.wait(100)
    host = win.hosts["dashboard"]
    assert host.scroll.verticalScrollBar().maximum() == 0, "dashboard must fit a maximised 1920x1080 window"
    assert win.header.height() == 110
    dash = win.pages["dashboard"]
    for card in (dash.hw_card, dash.game_card, dash.rec_card, dash.cmp_card, dash.safe_card):
        assert card.height() < 420, f"{card.accessibleName()} is too tall ({card.height()} px)"


def test_actions_stay_reachable_at_150_percent_scaling(window, qtbot):
    win = window
    _settle(qtbot, win, 1280, 680)          # 1920x1080 at 150 % (logical pixels, taskbar subtracted)
    win.go("dashboard")
    qtbot.wait(100)
    dash = win.pages["dashboard"]
    tile = dash.a_scan
    bottom = tile.mapTo(win, tile.rect().bottomLeft()).y()
    assert tile.isVisible() and bottom <= win.height(), "pinned action buttons must stay on screen"
    assert win.header.height() < 110, "short windows get the compact header"
    assert win.hosts["dashboard"].scroll.verticalScrollBar().maximum() > 0, "content scrolls when it must"


def test_ultrawide_content_is_capped_and_centred(window, qtbot):
    from akeno.ui.main_window import MAX_CONTENT_WIDTH
    win = window
    _settle(qtbot, win, 3440, 1400)
    win.go("dashboard")
    qtbot.wait(100)
    page = win.pages["dashboard"]
    assert page.width() <= MAX_CONTENT_WIDTH
    left = page.mapTo(win.hosts["dashboard"], page.rect().topLeft()).x()
    right = win.hosts["dashboard"].width() - (left + page.width())
    assert abs(left - right) < 30


def test_install_wizard_groups_checks_and_explains_blocks(window, qtbot):
    win = window
    _settle(qtbot, win, 1920, 1009)
    win.go("install")
    # opening the wizard starts the read-only runtime search; buttons wait for it (slow on Windows runners)
    qtbot.waitUntil(lambda: not win.ctl.is_busy("deps"), timeout=30000)
    qtbot.wait(50)
    page = win.pages["install"]
    titles = [s.title.text() for s in (page.sec_system, page.sec_game, page.sec_deps, page.sec_install)]
    assert titles == ["System", "Game", "Dependencies", "Installation"]
    deps = [r.title for r in page.sec_deps.rows]
    assert any("nvngx_dlssnr.dll" in t for t in deps) and any("Package verification" in t for t in deps)
    # nothing has happened yet -> no install step may be green
    assert all(r.state == "pending" for r in page.sec_install.rows[:5])
    assert not page.btn_install.isEnabled()
    assert "nvngx_dlssnr.dll" in page.why.text()
    assert page.btn_resolve.isEnabled()
    assert win.hosts["install"].scroll.verticalScrollBar().maximum() == 0, "wizard fits at 1920x1080"
    page.sec_deps.toggle()
    assert page.sec_deps.expanded
    # while a runtime search runs, neither Resolve nor Install may start a second one silently
    win.ctl.busy.add("deps")
    page._update_buttons()
    assert not page.btn_resolve.isEnabled() and not page.btn_install.isEnabled()
    win.ctl.busy.discard("deps")
    page._update_buttons()
    assert page.btn_resolve.isEnabled()


def test_hardware_page_shows_matrix_and_honest_verification(window, qtbot):
    win = window
    win.go("hardware")
    qtbot.wait(50)
    hw = win.pages["hardware"]
    from PySide6.QtWidgets import QLabel
    texts = " ".join(lb.text() for lb in hw.nr_card.findChildren(QLabel))
    assert "Not verified on this PC" in texts and "No official NVIDIA support" in texts
    mx = " ".join(lb.text() for lb in hw.mx_card.findChildren(QLabel))
    assert "DLSS Multi Frame Generation" in mx and "RTX 50" in mx


def test_high_dpi_screenshot_render_from_source(tmp_path, akeno_home):
    """End-to-end: the real app renders every page at 150 % scaling (offscreen) without errors."""
    env = dict(os.environ, AKENO_HOME=str(akeno_home), QT_QPA_PLATFORM="offscreen")
    out = tmp_path / "shots"
    r = subprocess.run([sys.executable, "-m", "akeno", "--screenshot", str(out), "--screenshot-size", "1280x720",
                        "--screenshot-scale", "1.5"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    from PySide6.QtGui import QImage
    img = QImage(str(out / "dashboard.png"))
    assert img.width() == 1920 and img.height() == 1080, "1280x720 logical at 150 % = 1920x1080 physical"
