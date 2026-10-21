"""One launcher: discovery persistence, routing, server profiles, diagnostics and styled UI."""
from dataclasses import replace
from pathlib import Path

import pytest

from akeno.fivem.models import ServerPolicy
from akeno.core.models import CompatStatus
from test_fivem_workflow import workflow


def register(w):
    g = w.svc.library.add_manual(str(w.root))
    w.svc.select_game(g.id)
    return g


def test_library_reanalysis_keeps_fivem_identity_and_art(workflow):
    w = workflow
    g = register(w)
    assert g.launcher == "fivem" and Path(g.artwork_path).is_file()
    assert w.svc.library.reanalyze(g.id).id == g.id
    games = w.svc.library.scan()
    assert [x.id for x in games].count(g.id) == 1
    assert not any(x.launcher == "manual" and x.install_dir == str(w.root) for x in games)


def test_owned_server_profile_roundtrip_and_unknown_status(workflow):
    w = workflow
    g = register(w)
    assert w.svc.compatibility(g).status == CompatStatus.INSUFFICIENT
    w.svc.fivem.save_policy(g.id, w.policy, "dlss5_feeder")
    assert w.svc.fivem.policy(g.id) == w.policy
    assert w.svc.backend_for(g).policy == w.policy
    denied = replace(w.policy, graphics_plugins_allowed=False)
    w.svc.fivem.save_policy(g.id, denied, "dlss5_feeder")
    assert w.svc.compatibility(g).status == CompatStatus.BLOCKED


def test_generic_install_and_configuration_cannot_escape_fivem_guards(workflow):
    w = workflow
    g = register(w)
    a = w.svc.backend_for(g)
    with pytest.raises(PermissionError):
        w.svc.plan_install(g, a, a.package, {}, Path("."))
    with pytest.raises(PermissionError):
        w.svc.install(g, None)
    assert not w.svc.configure(g, a, {}, "quality").ok


def test_working_setup_diagnostics_are_read_only(workflow):
    w = workflow
    g = register(w)
    before = {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    report = w.svc.fivem.diagnose_existing(g)
    assert report["read_only"] and report["game_id"] == g.id
    assert report["gpu"]["name"].endswith("4080") and report["components"]
    assert {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()} == before
    assert w.svc.db.transactions(g.id) == []


@pytest.mark.parametrize("language", ["en", "de"])
def test_all_eight_pages_and_embedded_fivem_wizard(workflow, qtbot, language):
    from akeno.i18n import set_language
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    from akeno.ui import theme
    from PySide6.QtWidgets import QApplication
    w = workflow
    g = register(w)
    set_language(language)
    w.svc.downloads = w.svc.downloads.real
    QApplication.instance().setStyleSheet(theme.stylesheet())
    ctl = Controller(w.svc)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    win.resize(1920, 1009)
    win.show()
    try:
        assert len(win.pages) == 8
        for key in win.pages:
            win.go(key)
            QApplication.processEvents()
        win.go("install")
        page = win.pages["install"]
        assert page.fivem_panel.isVisible() and not page.generic_panel.isVisible()
        assert page.fivem_panel.tabs.count() == 4
        assert not page.fivem_panel.plan_button.isEnabled()
        assert not page.fivem_panel.install_button.isEnabled()
        panel = page.fivem_panel
        panel.server.setText("owned-fixture")
        panel.reference.setText("written fixture permission")
        panel.owned.setChecked(True)
        panel.permission.setCurrentIndex(1)
        panel.pure.setCurrentIndex(1)
        panel.save()
        assert w.svc.fivem.policy(g.id).server_id == "owned-fixture"
        assert w.svc.fivem.policy(g.id).graphics_plugins_allowed is True
    finally:
        set_language("en")
        from akeno.ui import workers
        workers.drain()
        win.close()


def test_unknown_fivem_prerequisites_do_not_recommend_enabling_nr(workflow):
    w = workflow
    g = register(w)
    w.svc.fivem.save_policy(g.id, w.policy, "optiscaler_nr")
    recommendation = w.svc.recommendation(g)
    assert recommendation.profile_id == "disabled"
    assert not any(outlook.estimated_fps for outlook in recommendation.outlooks)
