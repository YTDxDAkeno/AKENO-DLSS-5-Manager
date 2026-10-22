"""Synthetic client inspection only; no plugin compatibility is established."""
from pathlib import Path
from dataclasses import replace
import pytest
from helpers import make_pe
from test_fivem_discovery import installation
from akeno.fivem.discovery import inspect_installation
from akeno.fivem.models import ServerPolicy,Edition
from akeno.core.processes import ProcessInfo
from akeno.fivem.neural import inspect_client,assess,evidence_current


def inspect_fixture(installation):
    root,app,gta=installation
    exe=make_pe(app/'data/cache/subprocess/FiveM_b3258_GTAProcess.exe',['d3d11.dll'])
    plugin=make_pe(app/'plugins/d3d11.dll',dll=True)
    i=inspect_installation(root);p=ProcessInfo(42,exe.name,str(exe))
    modules={'state':'observed','modules':[{'name':'d3d11.dll','path':str(plugin)},
        {'name':'d3d12.dll','path':str(root/'system/d3d12.dll')}],'detail':'Synthetic modules only'}
    return i,p,modules


def test_actual_process_and_plugin_folder_are_separate_from_base_game(installation):
    i,p,modules=inspect_fixture(installation)
    before={str(p):p.read_bytes() for p in i.gta_dir.rglob('*') if p.is_file()}
    r=inspect_client(i,[p],lambda *_:modules,lambda _:'filetime:123')
    assert r['process']['name']==p.name and r['process']['created_at']=='filetime:123'
    assert r['observed_plugin_paths']==[str(i.plugin_dir/'d3d11.dll')]
    assert set(r['loaded_api_modules'])=={'d3d11.dll','d3d12.dll'}
    assert r['game_renderer']=='unknown' and r['helper_api']=='unknown' and not r['loader_verified']
    assert before=={str(p):p.read_bytes() for p in i.gta_dir.rglob('*') if p.is_file()}
    assert evidence_current(r,i,lambda _:'filetime:123')
    assert not evidence_current(r,i,lambda _:'filetime:124')
    Path(p.path).write_bytes(b'game update')
    assert not evidence_current(r,i,lambda _:'filetime:123')


@pytest.mark.parametrize('mode,pure',[('offline_development',None),('authorized_server',0),('authorized_server',1),('authorized_server',2),('blocked_session',2)])
def test_all_three_modes_require_real_loader_and_complete_transport(installation,mode,pure):
    i,p,modules=inspect_fixture(installation)
    r=inspect_client(i,[p],lambda *_:modules,lambda _:'filetime:123')
    policy=ServerPolicy(server_id='synthetic-private',own_server=True,permission_reference='operator statement',
        graphics_plugins_allowed=True,pure_level=pure,experimental_consent=True)
    report=assess(i,policy,r,mode=mode)
    assert not report['deployment_eligible'] and not report['base_game_writes']
    assert any('renderer' in b for b in report['blockers'])
    assert any('transport' in b for b in report['blockers'])
    if pure==2:assert any('sv_pureLevel 2' in b or 'unsupported' in b for b in report['blockers'])


def test_enhanced_never_uses_legacy_recipe_and_duplicate_processes_stay_unknown(installation):
    i,p,modules=inspect_fixture(installation)
    i.edition=Edition.ENHANCED
    report=assess(i,ServerPolicy(),inspect_client(i,[p,p],lambda *_:modules,lambda _:'filetime:123'))
    assert report['inspection']['process_state']=='ambiguous'
    assert any('Enhanced' in b for b in report['blockers'])
    assert not report['deployment_eligible']
