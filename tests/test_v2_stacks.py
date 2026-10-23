"""Inert, independently constructed stack packages: no client/GPU success claims."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import zipfile
import pytest
from test_community_runtime import experiment,nr,unsigned
from test_v2_manager import manager,native_recipe
from helpers import make_pe,sha
from akeno.security.authenticode import SignatureInfo
from akeno.pipeline.manifests import ComponentStore
from akeno.core.models import GraphicsApi
from akeno.fivem.models import ServerPolicy
from akeno.fivem.community_stack import _client_exception as native_client_exception
from akeno.fivem.server_config import inspect_config


@pytest.fixture
def complete(manager,tmp_path):
    s,g,c,root=manager
    # One local runtime profile explicitly covers both consumers; this is synthetic eligibility.
    row=s.db.get_setting('community-runtime:'+c.sha256)
    from akeno.community.profiles import local_profile
    evaluation=s.community.runtimes.inspect(c.path,c.review)
    row['review']=local_profile(evaluation,'https://example.org/test/runtime',True)
    s.db.set_setting('community-runtime:'+c.sha256,row)
    reshade=make_pe(tmp_path/'components'/'ReShade64.dll',dll=True,version=(6,8,0,1),strings={'ProductName':'ReShade'},exports=['ReShadeRegisterAddon','ReShadeUnregisterAddon'])
    motion=tmp_path/'components'/'motion.fx';motion.write_text('texture texMotionVectors { Width=BUFFER_WIDTH; Height=BUFFER_HEIGHT; Format=RG16F; };\ntechnique TestMotion { }')
    consumer=make_pe(tmp_path/'components'/'renodx-dlss5.addon64',dll=True,strings={'ProductName':'Synthetic test NR consumer'})
    consumer.write_bytes(consumer.read_bytes()+b'NeuralUplift\0NRPasses\0NREnableUpscaling\0')
    items=[]
    for role,path,version,license in [('reshade',reshade,'6.8.0','BSD-3-Clause'),('motion_provider',motion,'fixture','MIT'),('neural_consumer',consumer,'fixture-config7','Test-owned synthetic bytes')]:
        item={'role':role,'path':path.name,'sha256':sha(path.read_bytes()),'version':version,'license':license,'source':'https://example.org/synthetic-only','local_use_permitted':True,'architecture':'x64','apis':['d3d11','d3d12']}
        if role=='motion_provider':item.update(destination='reshade-shaders/Shaders/motion.fx',provider_id=0,technique='TestMotion')
        if role=='neural_consumer':item.update(contract='renodx-dlss5-config7')
        items.append(item)
    manifest=motion.parent/'manifest.json';manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':items}));s.pipelines.components.import_manifest(manifest,True)
    sr=make_pe(tmp_path/'official'/'nvngx_dlss.dll',dll=True,version=(310,8,0,0),strings={'OriginalFilename':'nvngx_dlss.dll'},exports=['NVSDK_NGX_D3D12_CreateFeature','NVSDK_NGX_D3D12_EvaluateFeature'])
    s.community.runtimes.verifier=lambda p:SignatureInfo(True,True,'NVIDIA Corporation','Synthetic verifier') if Path(p).name=='nvngx_dlss.dll' else unsigned(p)
    from akeno.pipeline.dlls import DllService
    s.dlls=DllService(s,s.community.runtimes.verifier);s.dlls.import_official(sr,True)
    # Complete fake release with a real PE add-on, used only as test-owned bytes.
    addon=make_pe(tmp_path/'feeder'/'dlss5-feed.addon64',dll=True)
    members={'dlss5-feed.addon64':addon.read_bytes(),'reshade-shaders/Shaders/DLSS5_Feed.fx':b'// Synthetic shader'}
    archive=tmp_path/'feeder.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for name,data in members.items():z.writestr(name,data)
    p=s.catalog.package('dlss5-feeder-1.19.0');p.sha256=sha(archive.read_bytes());p.size=archive.stat().st_size;p.files={n:sha(data) for n,data in members.items()};s.downloads.files[p.url]=archive
    return s,g,c,root


@pytest.mark.parametrize('api',['d3d11','d3d12'])
@pytest.mark.parametrize('family',['feeder','renodx_feeder'])
def test_complete_feeder_install_configuration_restore(complete,api,family):
    s,g,c,root=complete
    # Non-DLSS game. One DXGI hook plus a separately evidenced OptiScaler proxy.
    (root/'nvngx_dlss.dll').unlink();make_pe(root/'Game.exe',[api+'.dll','dxgi.dll','winmm.dll'],pad=50000);s.library.reanalyze(g.id)
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    recipe='feeder-opti-1.19.0-1.3.0' if family=='feeder' else 'renodx_feeder'
    prepared=s.mods.prepare(g.id,recipe,c.sha256,'balanced',True,True)
    names={o.rel_path for o in prepared.plan.ops}
    assert {'dxgi.dll','dlss5-feed.addon64','reshade-shaders/Shaders/motion.fx','ReShade.ini','AKENO-Feeder.ini','dlss5-feed.cfg','nvngx_dlss.dll','nvngx_dlssnr.dll'}<=names
    config=next(o.content.decode() for o in prepared.plan.ops if o.rel_path=='AKENO-Feeder.ini')
    assert config.index('TestMotion')<config.index('DLSS5_Feed')
    assert ('winmm.dll' in names)==(family=='feeder')
    result=s.mods.install(prepared);assert result.ok,result.messages
    assert s.mods.configure(g.id,'performance').ok
    if family=='feeder':assert 'PreSr=false' in (root/'OptiScaler.ini').read_text()
    assert s.mods.restore(g.id).ok
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before


@pytest.fixture
def fivem(complete,tmp_path,monkeypatch):
    s,_,c,_=complete;root=tmp_path/'FiveM';app=root/'FiveM.app';app.mkdir(parents=True);gta=tmp_path/'GTA'
    make_pe(root/'FiveM.exe',['kernel32.dll']);make_pe(gta/'GTA5.exe',['d3d11.dll'],pad=50000)
    (app/'CitizenFX.ini').write_text('[Game]\nIVPath='+str(gta)+'\nSavedBuildNumber=3258\n')
    g=s.library.add_manual(str(root));s.library.set_override(g.id,'api','d3d11');g=s.library.reanalyze(g.id)
    policy=ServerPolicy(server_id='test fixture',permission_reference='Synthetic operator permission',graphics_plugins_allowed=True,pure_level=0,experimental_consent=True)
    s.fivem.save_policy(g.id,policy,'dlss5_feeder');s.db.set_setting('fivem_community:'+g.id,{'client_plugins_permitted':True})
    s.fivem.process_reader=lambda:[]
    # Only mock a pre-existing native client acknowledgement, not protection permission.
    monkeypatch.setattr('akeno.fivem.community_stack._client_exception',lambda _:True)
    return s,g,c,root,gta


def test_fivem_full_stack_never_writes_gta_and_restores_without_permission(fivem):
    s,g,c,root,gta=fivem;before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()};gta_before={p.name:p.read_bytes() for p in gta.iterdir()}
    prepared=s.mods.prepare(g.id,'fivem-legacy-community',c.sha256,'performance',False,True)
    assert Path(prepared.plan.target_dir)==root/'FiveM.app/plugins'
    assert 'd3d11.dll' in {o.rel_path for o in prepared.plan.ops} and 'dxgi.dll' not in {o.rel_path for o in prepared.plan.ops}
    result=s.mods.install(prepared);assert result.ok,result.messages
    assert s.mods.configure(g.id,'balanced').ok
    s.fivem.save_policy(g.id,replace(s.fivem.policy(g.id),graphics_plugins_allowed=False),'dlss5_feeder')
    assert s.mods.restore(g.id).ok
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
    assert {p.name:p.read_bytes() for p in gta.iterdir()}==gta_before


@pytest.mark.parametrize('change',['pure','client','policy_after_preview','component_after_preview'])
def test_fivem_specific_blocks_and_preview_revalidation(fivem,change):
    s,g,c,root,_=fivem
    if change=='pure':s.fivem.save_policy(g.id,replace(s.fivem.policy(g.id),pure_level=2),'dlss5_feeder')
    if change=='client':s.db.set_setting('fivem_community:'+g.id,{'client_plugins_permitted':False})
    if change in ('pure','client'):
        with pytest.raises(PermissionError):s.mods.prepare(g.id,'fivem-legacy-community',c.sha256,'quality',False,True)
    else:
        prepared=s.mods.prepare(g.id,'fivem-legacy-community',c.sha256,'quality',False,True)
        if change=='policy_after_preview':s.fivem.save_policy(g.id,replace(s.fivem.policy(g.id),permission_reference='changed'),'dlss5_feeder')
        else:Path(s.pipelines.components.records()['neural_consumer']['stored_path']).write_bytes(b'changed')
        with pytest.raises((ValueError,PermissionError)):s.mods.install(prepared)
    assert not (root/'FiveM.app/plugins/renodx-dlss5.addon64').exists()


def test_server_config_order_includes_unknown_and_cycles(tmp_path):
    main=tmp_path/'server.cfg';extra=tmp_path/'graphics.cfg'
    main.write_text('sv_pureLevel 2\nexec graphics.cfg\nsets sv_scripthookAllowed false\n')
    extra.write_text('set sv_pureLevel 0\n')
    result=inspect_config(main);assert result['pure_level']==0 and result['script_hook_allowed'] is False and len(result['files'])==2
    extra.write_text('exec server.cfg\n');assert inspect_config(main)['pure_level'] is None
    extra.write_text('exec missing.cfg\n');assert not inspect_config(main)['complete']
    extra.write_text('exec ../escape.cfg\n');assert inspect_config(main)['pure_level'] is None


def test_local_consumer_import_rejects_generic_hdr_addon(complete,tmp_path):
    s,*_=complete;hdr=make_pe(tmp_path/'generic-hdr.addon64',dll=True,strings={'ProductName':'Generic HDR'})
    with pytest.raises(ValueError,match='declared NR'):s.mods.import_consumer(hdr,'1.0','https://example.org/fixture','MIT',True)


def test_config7_custom_keys_are_real_bounded_and_exclusive():
    from akeno.fivem.community_stack import preset_settings
    values=preset_settings('custom',{'NRResolutionScale':.5,'NRPasses':1})
    assert values['ConfigVersion']=='7' and values['NREnableUpscaling']=='0'
    with pytest.raises(ValueError):preset_settings('custom',{'NRPasses':1.5})
    with pytest.raises(ValueError):preset_settings('custom',{'InventedOption':1})


def test_client_reshade_permission_is_read_only_and_machine_bound(fivem,monkeypatch):
    import importlib.util
    s,g,c,root,_=fivem;app=root/'FiveM.app';path=app/'CitizenFX.ini';original=path.read_text()
    monkeypatch.setenv('COMPUTERNAME','test-fixture')
    from akeno.fivem.discovery import inspect_installation
    assert native_client_exception(inspect_installation(root)) is False
    assert path.read_text()==original


def test_consumer_version_library_preserves_versions_and_game_selection(complete,tmp_path):
    s,g,c,root=complete;old=s.pipelines.components.records()['neural_consumer'];pin=old['sha256']
    replacement=make_pe(tmp_path/'new-consumer.addon64',dll=True,strings={'ProductName':'Synthetic newer NR'})
    replacement.write_bytes(replacement.read_bytes()+b'NeuralUplift\0NRPasses\0NREnableUpscaling\0')
    newer=s.mods.import_consumer(replacement,'test-config7-next','https://example.org/synthetic-next','Test-owned',True)['neural_consumer']
    library=s.db.get_setting('pipeline_component_library')
    assert pin in library and newer['sha256'] in library
    s.pipelines.components.select(g.id,'neural_consumer',pin)
    assert s.pipelines.components.verified('neural_consumer',g.id)['version']==old['version']
    assert s.pipelines.components.records()['neural_consumer']['version']=='test-config7-next'


def test_renodx_upgrade_previous_restores_consumer_selection_and_originals(complete,tmp_path):
    s,g,c,root=complete;(root/'nvngx_dlss.dll').unlink();make_pe(root/'Game.exe',['d3d12.dll','dxgi.dll'],pad=50000);s.library.reanalyze(g.id)
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    first=s.mods.install(s.mods.prepare(g.id,'renodx_feeder',c.sha256,'quality',True,True));assert first.ok
    original=s.pipelines.components.records()['neural_consumer']
    newer=make_pe(tmp_path/'next.addon64',dll=True);newer.write_bytes(newer.read_bytes()+b'NeuralUplift\0NRPasses\0NREnableUpscaling\0')
    s.mods.import_consumer(newer,'config7-next','https://example.org/test/next','Test-owned',True)
    assert s.mods.install(s.mods.prepare(g.id,'renodx_feeder',c.sha256,'balanced',True,True)).ok
    assert s.mods.previous(g.id).ok
    assert s.pipelines.components.records(g.id)['neural_consumer']['sha256']==original['sha256']
    assert sha((root/'renodx-dlss5.addon64').read_bytes())==original['sha256']
    assert s.mods.restore(g.id).ok
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
