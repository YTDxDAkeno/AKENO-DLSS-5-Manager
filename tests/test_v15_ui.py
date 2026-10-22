"""Synthetic graphical workflow and translated high-DPI screenshots."""
import os
from pathlib import Path
import pytest
from akeno.ui.pipelines import PipelineDialog
from akeno.ui.controller import Controller
from akeno import i18n
from test_community_runtime import experiment,nr


@pytest.mark.parametrize('language',['en','de'])
def test_pipeline_dialog_analysis_and_no_implicit_hardware_test(qtbot,experiment,language,monkeypatch):
    s,g,c,root=experiment;i18n.set_language(language)
    monkeypatch.setattr(s.neural_selftest,'run',lambda *a:pytest.fail('Opening a dialog must never run NVIDIA code'))
    from akeno.ui import theme
    from PySide6.QtWidgets import QApplication
    QApplication.instance().setStyleSheet(theme.stylesheet())
    ctl=Controller(s);dialog=PipelineDialog(ctl);qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda:not dialog.busy)
    assert dialog.routes.count()==6 and dialog.tabs.count()==4
    assert dialog.runtime.findData(c.sha256)>0
    dialog.offline.setChecked(True);dialog.runtime.setCurrentIndex(dialog.runtime.findData(c.sha256));dialog.analyze()
    qtbot.waitUntil(lambda:not dialog.busy)
    assert 'native' in dialog.plan_view.toPlainText() or 'Native' in dialog.plan_view.toPlainText()
    dialog.show();qtbot.wait(30)
    directory=os.environ.get('AKENO_V15_SCREENSHOTS')
    if directory:
        target=Path(directory);target.mkdir(parents=True,exist_ok=True);dialog.grab().save(str(target/f'pipeline-v15-{language}-synthetic.png'))
        dialog.tabs.setCurrentIndex(1);dialog.grab().save(str(target/f'selftest-v15-{language}-not-run.png'))
    dialog.close();i18n.set_language('en')


def test_late_pipeline_worker_cannot_update_closed_dialog(qtbot,experiment,monkeypatch):
    s,g,c,root=experiment;ctl=Controller(s);jobs=[]
    monkeypatch.setattr(ctl,'run',lambda key,work,done,error:jobs.append((work,done,error)) or True)
    dialog=PipelineDialog(ctl);qtbot.addWidget(dialog);dialog.close()
    result=jobs[0][0]();jobs[0][1](result)
    assert dialog._closed and not dialog.busy


def test_desktop_hud_reuses_report_and_never_infers_nr_or_fps(qtbot):
    from akeno.ui.monitor_hud import MonitorHud
    data={'session':{'state':'backend_not_detected'},'neural':{'stages':{'runtime_loaded':True}},
          'benchmark_groups':{'current_session':[]}}
    hud=MonitorHud(lambda:data);qtbot.addWidget(hud)
    for _ in range(100):hud.refresh()
    assert 'Not measured' in hud.output.text() and '4500' not in hud.output.text()
    data['session']['ended']=True;hud.refresh();assert not hud.timer.isActive()
