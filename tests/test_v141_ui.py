"""Offscreen Qt fixtures exercise silent polling and cancellation, never GPU execution."""
import json
import os
from pathlib import Path
import pytest
from akeno.ui.community_runtime import CommunityRuntimeDialog
from akeno.ui.controller import Controller
from akeno import i18n
from test_community_runtime import experiment, nr


def report():
    return {'transaction':{'id':5},'neural':{'state':'installed','detail':'No new log content.',
        'stages':{'runtime_present':True}, 'lines':[], 'logs':[{'name':'OptiScaler.log','size':2320000,'activity':'no_new_content'}]},
        'session':{'state':'waiting_for_game','monitoring_active':True},
        'benchmark_status':'No benchmark has been captured for the current installation.',
        'current_session_benchmark_ids':[]}


@pytest.mark.parametrize('language',['en','de'])
def test_hundreds_of_polls_are_silent_coalesced_and_do_not_repaint(qtbot,experiment,monkeypatch,language):
    s,g,r,root=experiment;i18n.set_language(language)
    ctl=Controller(s);dialog=CommunityRuntimeDialog(ctl);qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda:not ctl.busy)
    jobs=[]
    def enqueue(key, work, done=None, error=None, **kwargs):
        jobs.append((key, work, done, error));return True
    monkeypatch.setattr(ctl,'run',enqueue)
    dialog.report_done(report())
    before=dialog.message.text();changes=[];paints=[]
    dialog.message.linkActivated.connect(lambda _:None)
    original=dialog.message.setText
    monkeypatch.setattr(dialog.message,'setText',lambda text:(changes.append(text),original(text))[-1])
    monkeypatch.setattr(dialog.evidence_output,'setPlainText',lambda text:paints.append(text))
    for _ in range(500):dialog.monitor_tick()
    assert len(jobs)==1 and dialog._diagnostic_busy
    assert not changes and dialog.message.text()==before
    jobs[0][2](report())
    qtbot.waitUntil(lambda:len(jobs)==2)
    jobs[1][2](report())
    assert len(jobs)==2 and not dialog._diagnostic_busy
    assert not changes and not paints
    # Late worker results after close must not access deleted/closed controls or enqueue more work.
    dialog.monitor_tick();done=jobs[-1][2]
    dialog.close();done(report())
    assert not dialog.monitor_timer.isActive()
    i18n.set_language('en')


@pytest.mark.parametrize('language',['en','de'])
def test_diagnostics_status_panel_screenshot_and_exit_preservation(qtbot,experiment,language):
    from akeno.ui import theme
    from PySide6.QtWidgets import QApplication
    from helpers import rtx
    s,g,r,root=experiment;i18n.set_language(language)
    s.system.gpus[0] = rtx('NVIDIA GeForce RTX 5090', device=0x2b85, vram_gb=32)
    QApplication.instance().setStyleSheet(theme.stylesheet())
    ctl=Controller(s);dialog=CommunityRuntimeDialog(ctl);qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda:not ctl.busy)
    dialog.game.name = 'Borderlands 4 (synthetic fixture)'
    dialog.resize(1100,800);dialog.show();dialog.tabs.setCurrentIndex(2)
    data=report();dialog.report_done(data);qtbot.wait(20)
    capture=os.environ.get('AKENO_V14_SCREENSHOTS')
    if capture:
        target=Path(capture);target.mkdir(parents=True,exist_ok=True)
        dialog.grab().save(str(target/f'diagnostics-v141-{language}-fixture.png'))
    data['session'].update(state='game_exited',ended=True,monitoring_active=False)
    dialog.report_done(data)
    assert dialog.last_report is data
    assert not dialog.monitor_timer.isActive()
    dialog.close();i18n.set_language('en')


def test_silent_controller_errors_do_not_emit_global_notifications(qtbot,experiment):
    s,*_=experiment;ctl=Controller(s);notifications=[];busy=[];errors=[]
    ctl.status.connect(notifications.append);ctl.busy_changed.connect(lambda *args:busy.append(args))
    def fail():raise ValueError('unchanged background finding')
    assert ctl.run('diagnostic_fixture',fail,on_error=errors.append,silent=True)
    qtbot.waitUntil(lambda:bool(errors))
    assert not notifications and not busy
    assert not ctl.busy


def test_stop_during_start_ignores_late_result_and_clears_busy_message(qtbot,experiment,monkeypatch):
    from akeno.i18n import t
    s,*_=experiment;ctl=Controller(s);dialog=CommunityRuntimeDialog(ctl);qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda:not ctl.busy)
    jobs=[]
    monkeypatch.setattr(ctl,'run',lambda key,work,done=None,*args,**kwargs:(jobs.append((key,done)) or True))
    dialog.observe()
    callback=jobs[0][1]
    dialog.stop_monitor()
    callback(report())
    assert not dialog.monitor_timer.isActive()
    assert dialog.message.text()==t('Monitoring stopped. The last report is preserved.')
    dialog.close()
