"""User-facing mod manager workflows, with no network/GPU work on construction."""
import os
from pathlib import Path
import pytest
from test_community_runtime import experiment,nr
from test_v2_manager import manager
from test_v2_stacks import complete,fivem
from akeno.ui.controller import Controller
from akeno.ui.mod_manager import ModPanel
from akeno.ui import theme
from akeno import i18n


@pytest.mark.parametrize('language',['en','de'])
def test_game_first_window_version_selection_and_screenshots(qtbot,manager,monkeypatch,language):
    from akeno.ui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication
    s,g,c,root=manager;i18n.set_language(language);QApplication.instance().setStyleSheet(theme.stylesheet())
    monkeypatch.setattr(s.mods,'refresh_releases',lambda *a:pytest.fail('No implicit network before opt-in'))
    monkeypatch.setattr(s.neural_selftest,'run',lambda *a:pytest.fail('No mandatory 300-frame test'))
    s.downloads=s.downloads.real;ctl=Controller(s);win=MainWindow(ctl);qtbot.addWidget(win);win.resize(1600,1000);win.show();qtbot.wait(30)
    assert win.current_page()=='library' and len(win.pages)==8
    panel=win.pages['library'].mod_panel
    assert panel.tabs.count()==3
    panel.offline.setChecked(True)
    assert panel.install_button.isEnabled()
    panel.version_table.selectRow(1);panel.use_version()
    assert '1.2.1' in panel.methods.currentData()
    qtbot.wait(30)
    assert panel.facts.grid.count()>=10
    folder=os.environ.get('AKENO_V2_SCREENSHOTS')
    if folder:
        out=Path(folder);out.mkdir(parents=True,exist_ok=True)
        win.grab().save(str(out/f'v2-my-games-{language}-synthetic.png'))
        panel.tabs.setCurrentIndex(1);win.grab().save(str(out/f'v2-versions-{language}-synthetic.png'))
    win.close();i18n.set_language('en')


def test_closed_or_replaced_panel_ignores_late_worker(qtbot,manager,monkeypatch):
    s,g,c,_=manager;ctl=Controller(s);jobs=[]
    monkeypatch.setattr(ctl,'run',lambda key,work,done,error:jobs.append((work,done,error)) or True)
    panel=ModPanel(ctl,g);qtbot.addWidget(panel)
    panel.run('test',lambda token:'late',lambda _:pytest.fail('Closed widget updated'))
    panel.close();jobs[0][1]('late')
    assert panel.closed and panel.token.cancelled


def test_fivem_policy_controls_save_to_the_mod_workflow(qtbot,fivem):
    s,g,c,_,_=fivem;panel=ModPanel(Controller(s),g);qtbot.addWidget(panel)
    panel.consent.setChecked(True);settings=panel.fivem_settings
    settings.server.setText('father-private-fixture');settings.reference.setText('Synthetic graphics approval')
    settings.allowed.setChecked(True);settings.client.setChecked(True);settings.pure.setCurrentIndex(settings.pure.findData(0));settings.save()
    assert s.fivem.policy(g.id).server_id=='father-private-fixture'
    assert panel.install_button.isEnabled()
    folder=os.environ.get('AKENO_V2_SCREENSHOTS')
    if folder:
        out=Path(folder);out.mkdir(parents=True,exist_ok=True);panel.resize(1050,1400);panel.show();qtbot.wait(20);panel.grab().save(str(out/'v2-fivem-rtx4080-synthetic.png'))
    panel.close()
