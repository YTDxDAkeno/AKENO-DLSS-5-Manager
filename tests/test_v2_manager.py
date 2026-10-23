"""Complete synthetic package installs/updates/rollback, never actual GPU evidence."""
import copy
from pathlib import Path
import json
import pytest
from test_community_runtime import experiment,nr
from helpers import make_opti_package,make_pe,sha,FakeDownloads
from akeno.mods.catalog import CommunityCatalog
from akeno.mods.service import ModManager
from akeno.security.catalog import Catalog
from akeno.security.downloads import CancelToken,Cancelled


@pytest.fixture
def manager(experiment,tmp_path):
    s,g,c,root=experiment;make_pe(root/'Game.exe',['d3d12.dll','nvngx_dlss.dll','winmm.dll'],pad=50000);s.library.reanalyze(g.id);data=copy.deepcopy(CommunityCatalog.bundled().data);files={}
    for i,p in enumerate(data['packages']):
        if p.get('contract')!='skynizz_nr':continue
        archive,entry=make_opti_package(tmp_path/p['version'])
        # Deliberately distinct test-owned backend bytes at each version.
        import zipfile
        with zipfile.ZipFile(archive) as z:members={n:z.read(n) for n in z.namelist()}
        name='DLSS5-Optimised/OptiScaler.dll';members[name]+=str(i).encode()
        with zipfile.ZipFile(archive,'w') as z:
            for name,blob in members.items():z.writestr(name,blob)
        p.update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size,files={n:sha(members['DLSS5-Optimised/'+n]) for n in entry['files']})
        files[p['url']]=archive
    s.catalog=Catalog({'schema':1,'packages':[],'allowed_hosts':['github.com']});s.mods=ModManager(s,CommunityCatalog(data));s.downloads=FakeDownloads(files,tmp_path/'cached')
    return s,g,c,root


def native_recipe(s,version):return next(r['id'] for r in s.mods.catalog.recipes.values() if r['family']=='native_optiscaler' and version in r['id'])


def test_two_versions_preset_previous_and_full_original_restore(manager):
    s,g,c,root=manager;before={p.name:p.read_bytes() for p in root.iterdir()}
    prepared=s.mods.prepare(g.id,native_recipe(s,'1.2.1'),c.sha256,'quality',True,True)
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
    first=s.mods.install(prepared);assert first.ok,first.messages
    first_bytes=(root/'winmm.dll').read_bytes()
    assert s.mods.configure(g.id,'performance').ok
    second=s.mods.install(s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'balanced',True,True));assert second.ok,second.messages
    assert (root/'winmm.dll').read_bytes()!=first_bytes
    reverted=s.mods.previous(g.id);assert reverted.ok,reverted.messages
    assert (root/'winmm.dll').read_bytes()==first_bytes
    assert s.installer.active_install(g.id)['package_version']=='1.2.1'
    assert s.installer.active_install(g.id)['params']['_mod_preset']=='performance'
    assert s.db.game_overrides(g.id)['mod_preset']=='performance'
    assert s.mods.restore(g.id).ok
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


def test_per_game_version_pins_and_stale_preview(manager,tmp_path):
    s,g,c,root=manager
    make_pe(tmp_path/'other'/'Other.exe',['d3d12.dll','winmm.dll'],pad=50000)
    other=s.library.add_manual(str(tmp_path/'other'))
    prepared=s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'quality',True,True)
    s.mods.select(other.id,native_recipe(s,'1.2.0'),c.sha256)
    assert s.package_for(s.registry.get('optiscaler_nr'),g).version=='1.3.0'
    assert s.package_for(s.registry.get('optiscaler_nr'),other).version=='1.2.0'
    s.mods.select(g.id,native_recipe(s,'1.2.1'),c.sha256)
    with pytest.raises(PermissionError,match='selection'):s.mods.install(prepared)
    assert not (root/'winmm.dll').exists()


def test_cancel_before_game_mutation_and_changed_owned_mod_blocks_update(manager):
    s,g,c,root=manager;token=CancelToken();token.cancel()
    with pytest.raises(Cancelled):s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'quality',True,True,token)
    assert not (root/'winmm.dll').exists()
    result=s.mods.install(s.mods.prepare(g.id,native_recipe(s,'1.2.1'),c.sha256,'quality',True,True));assert result.ok
    (root/'winmm.dll').write_bytes(b'edited by another tool')
    with pytest.raises(PermissionError):s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'quality',True,True)
    assert (root/'winmm.dll').read_bytes()==b'edited by another tool'


def test_previous_removes_version_only_files_and_keeps_initial_backup(manager):
    from akeno.backends.base import FileOp
    s,g,c,root=manager;before={p.name:p.read_bytes() for p in root.iterdir()}
    first=s.mods.install(s.mods.prepare(g.id,native_recipe(s,'1.2.1'),c.sha256,'quality',True,True));assert first.ok
    prepared=s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'quality',True,True)
    # Synthetic reviewed version adds a file; reconstruct a newly pinned prepared plan.
    from dataclasses import replace
    from akeno.community.service import plan_digest
    prepared.plan.ops.append(FileOp('version-only.txt',content=b'new version metadata'))
    pipeline=replace(prepared.pipeline,plan_pin=plan_digest(prepared.plan),environment_pin=s.community._environment(s.library.get(g.id),prepared.plan))
    prepared=replace(prepared,pipeline=pipeline,plan_pin=pipeline.plan_pin)
    assert s.mods.install(prepared).ok and (root/'version-only.txt').exists()
    assert s.mods.previous(g.id).ok and not (root/'version-only.txt').exists()
    assert s.mods.restore(g.id).ok
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


def test_custom_settings_are_versioned_and_restore_exactly(manager):
    s,g,c,root=manager
    custom={'DlssNr.WorkingScale':.5,'DlssNr.CacheInterval':3,'DlssNr.Passes':1}
    result=s.mods.install(s.mods.prepare(g.id,native_recipe(s,'1.3.0'),c.sha256,'custom',True,True,custom=custom));assert result.ok
    text=(root/'OptiScaler.ini').read_text();assert 'WorkingScale=0.5' in text and 'CacheInterval=3' in text
    with pytest.raises(ValueError):s.mods.configure(g.id,'custom',{'DlssNr.ImaginaryFeature':True})
    assert s.mods.restore(g.id).ok


def test_staged_binary_architecture_is_checked_without_loading_code(tmp_path):
    from akeno.mods.validation import validate_sources
    from akeno.backends.base import FileOp,InstallPlan
    binary=make_pe(tmp_path/'wrong.dll',machine='x86',dll=True)
    plan=InstallPlan('fixture',None,str(tmp_path),[FileOp('dxgi.dll',source=binary,expected_sha256=sha(binary.read_bytes()))])
    with pytest.raises(PermissionError,match='architecture'):validate_sources(plan)


def test_runtime_recommendation_is_consumer_specific_and_prefers_eligible_4080_variant(manager):
    s,g,c,root=manager
    # Original fixture covers only OptiScaler, so it cannot become RenoDX evidence.
    recipe=s.mods.catalog.recipes['renodx_feeder']
    assert s.mods.selected_runtime(g,recipe) is None
    assert s.mods.selected_runtime(g,s.mods.catalog.recipes[native_recipe(s,'1.3.0')])==c.sha256
    record=s.db.get_setting('community-runtime:'+c.sha256)
    record['review']['targets'][0]['status']='incompatible';s.db.set_setting('community-runtime:'+c.sha256,record)
    assert s.mods.selected_runtime(g,s.mods.catalog.recipes[native_recipe(s,'1.3.0')]) is None


def test_reinstall_uses_verified_cache_with_network_unavailable(manager):
    s,g,c,root=manager;recipe=native_recipe(s,'1.3.0')
    first=s.mods.install(s.mods.prepare(g.id,recipe,c.sha256,'quality',True,True));assert first.ok
    assert s.mods.restore(g.id).ok
    s.downloads=s.downloads.real
    class Offline:
        def open(self,*args,**kwargs):pytest.fail('Verified cached reinstall made a network request')
    s.downloads._opener=Offline()
    reinstall=s.mods.install(s.mods.prepare(g.id,recipe,c.sha256,'balanced',True,True))
    assert reinstall.ok and s.mods.restore(g.id).ok
