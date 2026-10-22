"""Synthetic route contracts; never hardware acceptance evidence."""
from akeno.pipeline.capabilities import PipelineFacts
from akeno.pipeline.resolver import resolve
from akeno.pipeline.results import RouteState
from akeno.pipeline.dependencies import ordered_components
from akeno.pipeline.proxy import resolve_proxy
from akeno.pipeline.discovery import native_support
from akeno.pipeline.conflicts import consumer_id,inspect_conflicts
from akeno.core.models import Game,DetectedFile
from tests.helpers import make_pe
import pytest


def facts(**kwargs):
    values=dict(game_id='fixture',api='d3d12',architecture='x64',native_calls=True,gpu_vendor='nvidia',gpu_generation='RTX 50',
                gpu_device_id=0x2b85,driver='617.42',runtime_sha256='a'*64,permitted=True,proxy='winmm.dll',
                components={k:{'version':'1.3.0'} for k in ('optiscaler','forwarder','nr_runtime')})
    values.update(kwargs);return PipelineFacts(**values)


def test_native_priority_and_no_generation_wide_execution_claim():
    for generation,device in [('RTX 40',0x2704),('RTX 50',0x2b85)]:
        a=resolve(facts(gpu_generation=generation,gpu_device_id=device))[0]
        assert a.eligible and a.recommended and a.state==RouteState.EXPERIMENTAL


def test_missing_native_runtime_proxy_and_unsafe():
    for kwargs,code in [({'native_calls':None},'native_calls'),({'runtime_sha256':None},'runtime_pin'),({'proxy':None},'proxy')]:
        a=resolve(facts(**kwargs))[0];assert not a.eligible and any(c.code==code and not c.satisfied for c in a.requirements)
    assert resolve(facts(permitted=False))[0].state==RouteState.UNSAFE


def test_driver_conflicts_and_unsupported_bridge():
    components=facts().components|{'optiscaler':{'version':'1.3.0','minimum_driver':'618.00'}}
    assert not resolve(facts(components=components))[0].eligible
    assert not resolve(facts(consumers=['renodx_nr','optiscaler_nr']))[0].eligible
    assert not resolve(facts(api='d3d9',architecture='x86'))[3].eligible


def test_component_dependencies_require_exact_versions():
    assert [r for r,_ in ordered_components({'a':{'version':'1','requires':{'b':'2'}},'b':{'version':'2'}})]==['b','a']
    with pytest.raises(ValueError):ordered_components({'a':{'requires':{'b':'2'}},'b':{'version':'3'}})
    with pytest.raises(ValueError):ordered_components({'a':{'version':'1','requires':{'a':'1'}}})


def test_proxy_collision_does_not_choose_unproven_free_name(tmp_path):
    exe=make_pe(tmp_path/'Game.exe',['winmm.dll'])
    g=Game('g','Game',str(tmp_path),'manual',exe_path=str(exe))
    assert resolve_proxy(g)=='winmm.dll'
    make_pe(tmp_path/'winmm.dll',dll=True)
    assert resolve_proxy(g) is None
    assert resolve_proxy(g,{'winmm.dll'})=='winmm.dll'


def test_copied_dlss_never_establishes_native_integration(tmp_path):
    exe=make_pe(tmp_path/'Game.exe',['d3d12.dll'])
    g=Game('g','Game',str(tmp_path),'manual',exe_path=str(exe),upscaler_inputs=['dlss'])
    make_pe(tmp_path/'nvngx_dlss.dll',dll=True)
    assert native_support(g)[0] is None
    assert consumer_id('renodx-dlss5-4.7.addon64')=='renodx_nr'
    g.existing_mods=[DetectedFile(n,str(tmp_path/n),'neural_consumer') for n in ['renodx-dlss5-4.7.addon64','renodx-dlss5-6.1.addon64']]
    assert len(inspect_conflicts(g)[0])==2


def test_battlenet_discovery_uses_registered_roots_not_drive_walk(tmp_path):
    from akeno.games.launchers import scan_battlenet
    game=tmp_path/'Registered Game';game.mkdir()
    records=[('game',{'Publisher':'Blizzard Entertainment','DisplayName':'Synthetic game','InstallLocation':str(game)}),
             ('launcher',{'Publisher':'Blizzard','DisplayName':'Battle.net','InstallLocation':str(game)})]
    found=scan_battlenet(records)
    assert len(found)==1 and found[0].launcher=='battlenet'
