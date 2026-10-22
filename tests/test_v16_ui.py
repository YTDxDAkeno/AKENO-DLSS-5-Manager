"""EN/DE UI screenshots use inert fixtures and never claim physical GPU evidence."""
import os
from pathlib import Path
import pytest
from akeno import i18n
from akeno.ui.controller import Controller
from akeno.ui import theme
from PySide6.QtWidgets import QApplication
from test_community_runtime import experiment,nr
from test_fivem_workflow import workflow
from test_fivem_integration import register


def screenshot(widget,name):
    folder=os.environ.get('AKENO_V16_SCREENSHOTS')
    if folder:
        target=Path(folder);target.mkdir(parents=True,exist_ok=True)
        widget.grab().save(str(target/name))


@pytest.mark.parametrize('language',['en','de'])
def test_pipeline_controls_and_local_build_preflight(qtbot,experiment,monkeypatch,language):
    from akeno.ui.pipelines import PipelineDialog
    s,g,c,root=experiment;i18n.set_language(language)
    QApplication.instance().setStyleSheet(theme.stylesheet())
    dialog=PipelineDialog(Controller(s));qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:not dialog.busy)
    assert not dialog.install_button.isEnabled() and not dialog.preview_button.isEnabled()
    monkeypatch.setattr('akeno.diagnostics.localbuild.prerequisites',lambda:{'ready':False,'missing':['v143']})
    dialog.build_diagnostic()
    assert 'v143' in dialog.test_status.text() and not dialog.busy
    dialog.tabs.setCurrentIndex(1);dialog.resize(1200,850);qtbot.wait(20)
    screenshot(dialog,f'v16-selftest-{language}-synthetic-no-hardware.png')
    dialog.close();i18n.set_language('en')


@pytest.mark.parametrize('language',['en','de'])
def test_fivem_neural_workflow_is_read_only_and_has_three_modes(qtbot,workflow,language):
    from akeno.ui.fivem_panel import FiveMPanel
    w=workflow;g=register(w);i18n.set_language(language)
    QApplication.instance().setStyleSheet(theme.stylesheet())
    panel=FiveMPanel(Controller(w.svc),g);qtbot.addWidget(panel)
    panel.resize(1250,900);panel.tabs.setCurrentIndex(5);panel.show()
    assert panel.neural_mode.count()==3 and not panel.install_button.isEnabled()
    panel.analyze_neural();qtbot.waitUntil(lambda:panel.neural_report is not None)
    assert not panel.neural_report['deployment_eligible']
    assert panel.neural_report['inspection']['game_renderer']=='unknown'
    qtbot.wait(20);screenshot(panel,f'v16-fivem-{language}-synthetic-blocked.png')
    panel.close();i18n.set_language('en')
