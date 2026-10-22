"""End-to-end independent synthetic packages; no real NVIDIA binary is involved."""
from pathlib import Path
import json
import pytest
from test_community_runtime import experiment,nr
from tests.helpers import make_pe,sha
from akeno.security.catalog import Catalog


def native_pipeline(experiment):
    s,g,c,root=experiment
    make_pe(root/'Game.exe',['d3d12.dll','nvngx_dlss.dll','winmm.dll'],pad=50000)
    data=json.loads(json.dumps({'schema':1,'catalog_version':'synthetic-1.5','allowed_hosts':['github.com'],'packages':[]}))
    from dataclasses import asdict
    entry=asdict(s.catalog.packages[0]);entry['trust']='verified';entry['id']='optiscaler-dlss5-skynizz-1.3.0';entry['version']='1.3.0';data['packages']=[entry]
    s.catalog=Catalog(data);s.library.reanalyze(g.id)
    return s,g,c,root


def test_native_pipeline_shared_resolver_preview_install_exact_restore(experiment):
    s,g,c,root=native_pipeline(experiment)
    before={p.name:p.read_bytes() for p in root.iterdir()}
    decision=s.pipelines.analyze(g.id,True,c.sha256)['routes'][0]
    assert decision.eligible
    prepared=s.pipelines.prepare(g.id,'native_optiscaler',c.sha256,True,True)
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
    assert prepared.plan.params['_pipeline_route']=='native_optiscaler'
    result=s.pipelines.install(prepared);assert result.ok,result.messages
    assert s.installer.restore(result.tx_id).ok
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


def test_changed_route_or_source_invalidates_prepared_install(experiment):
    s,g,c,root=native_pipeline(experiment)
    prepared=s.pipelines.prepare(g.id,'native_optiscaler',c.sha256,True,True)
    prepared.plan.params['_pipeline_route']='feeder'
    with pytest.raises(PermissionError,match='changed'):s.pipelines.install(prepared)
    assert not (root/'winmm.dll').exists()


def test_private_components_require_pin_permission_full_reshade_exports(experiment,tmp_path):
    s,*_=experiment
    component=make_pe(tmp_path/'ReShade64.dll',dll=True,exports=['ReShadeRegisterAddon','ReShadeUnregisterAddon'])
    item={'role':'reshade','path':component.name,'sha256':sha(component.read_bytes()),'version':'6.8.0','source':'https://reshade.me','license':'BSD-3-Clause','architecture':'x64','apis':['d3d12'],'local_use_permitted':True}
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':[item]}))
    with pytest.raises(PermissionError):s.pipelines.components.import_manifest(manifest,False)
    assert s.pipelines.components.import_manifest(manifest,True)['reshade']['trust']=='local_declared_not_a_security_review'
    item['sha256']='0'*64;manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':[item]}))
    with pytest.raises(ValueError,match='hash mismatch'):s.pipelines.components.import_manifest(manifest,True)


def test_official_swap_revalidates_signature_active_path_and_family(experiment,tmp_path):
    from akeno.pipeline.dlls import DllService
    from akeno.security.authenticode import SignatureInfo
    from akeno.security.downloads import sha256_file
    s,g,c,root=experiment
    s.dlls=DllService(s,lambda _:SignatureInfo(True,True,'NVIDIA Corporation','Synthetic signature fixture'))
    original=make_pe(root/'nvngx_dlss.dll',dll=True,version=(3,1,0,0),strings={'OriginalFilename':'nvngx_dlss.dll'},exports=['NVSDK_NGX_D3D12_CreateFeature'])
    replacement=make_pe(tmp_path/'new'/'nvngx_dlss.dll',dll=True,version=(3,2,0,0),strings={'OriginalFilename':'nvngx_dlss.dll'},exports=['NVSDK_NGX_D3D12_CreateFeature'])
    record=s.dlls.import_official(replacement,True)
    with pytest.raises(PermissionError,match='Observe'):s.dlls.prepare(g.id,{str(original):record['sha256']},True)
    from akeno.diagnostics.neural import identity
    s.db.set_setting('dll_load_evidence:'+g.id,{'exe_metadata':list(identity(g.exe_path)),'path_metadata':{str(original.resolve()):list(identity(original))},'exe_path':str(Path(g.exe_path).resolve()),'exe_sha256':sha256_file(g.exe_path),'pid':42,'created_at':'synthetic-only','paths':{str(original.resolve()):sha256_file(original)}})
    before=original.read_bytes();prepared=s.dlls.prepare(g.id,{str(original):record['sha256']},True)
    result=s.dlls.install(prepared);assert result.ok,result.messages
    assert original.read_bytes()==replacement.read_bytes()
    assert s.installer.restore(result.tx_id).ok and original.read_bytes()==before


def test_visual_ab_requires_distinct_matching_real_image_files(experiment,tmp_path,qapp):
    from PySide6.QtGui import QImage,QColor
    s,g,*_=experiment
    paths={}
    for side,color in [('off','black'),('on','gray')]:
        image=QImage(320,180,QImage.Format.Format_RGB32);image.fill(QColor(color));path=tmp_path/(side+'.png');image.save(str(path));paths[side]=str(path)
    conditions={'viewpoint':'Synthetic still fixture','resolution':'320x180','settings':'Identical synthetic test','renderer':'No GPU renderer'}
    result=s.visual_comparisons.create(g.id,paths,conditions,'Synthetic workflow test; not game evidence.')
    assert Path(result['local_html']).is_file()
    assert result['images']['off']['sha256']!=result['images']['on']['sha256']
    assert 'not independently instrumented' in result['capture_attribution']
    with pytest.raises(ValueError,match='identical'):s.visual_comparisons.create(g.id,{'off':paths['off'],'on':paths['off']},conditions)


def selftest_fixture(experiment,tmp_path,monkeypatch,official_inventory=False):
    import zipfile
    from dataclasses import asdict
    from akeno.backends import runtimes
    from akeno.security.authenticode import SignatureInfo
    from akeno.security.catalog import Catalog
    s,g,c,root=native_pipeline(experiment)
    s.community.runtimes.verifier=lambda path: SignatureInfo(True,True,'NVIDIA Corporation','Synthetic SR signature') if Path(path).name=='nvngx_dlss.dll' else __import__('test_community_runtime').unsigned(path)
    sr=make_pe(tmp_path/'official'/'nvngx_dlss.dll',dll=True,version=(310,8,0,0),strings={'OriginalFilename':'nvngx_dlss.dll'},exports=['NVSDK_NGX_D3D12_CreateFeature','NVSDK_NGX_D3D12_EvaluateFeature'])
    if official_inventory:
        from akeno.pipeline.dlls import DllService
        s.dlls=DllService(s,s.community.runtimes.verifier)
        imported=s.dlls.import_official(sr,True)
        assert imported['family']=='super_resolution'
        assert s.runtime_lookup(runtimes.DLSS_RUNTIME) is None  # v1.5 stores are separate
    else:
        runtimes.import_runtime(s.db,str(sr),s.community.runtimes.verifier)
    host=make_pe(tmp_path/'host'/'dlss5-feed-host64.exe',imports=['winmm.dll'])
    archive=tmp_path/'synthetic-feeder.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('host64/dlss5-feed-host64.exe',host.read_bytes())
    entry={'id':'dlss5-feeder-1.19.0','backend':'dlss5_feeder','name':'Synthetic test host','version':'1.19.0','upstream':'https://github.com/jlrouzies-fr/DLSS5-Feeder','url':'https://github.com/example/synthetic.zip','sha256':sha(archive.read_bytes()),'license':'MIT','trust':'verified','files':{'host64/dlss5-feed-host64.exe':sha(host.read_bytes())},'root':''}
    opti=asdict(s.catalog.packages[0]);opti['trust']='verified'
    s.catalog=Catalog({'schema':1,'catalog_version':'synthetic-only','allowed_hosts':['github.com'],'packages':[opti,entry]});s.downloads.files[entry['url']]=archive
    monkeypatch.setattr('akeno.diagnostics.selftest.is_windows',lambda:True)
    return s,g,c,root


@pytest.mark.parametrize('official_inventory',[False,True])
def test_isolated_pinned_host_selftest_never_changes_game_or_turns_sr_into_nr(experiment,tmp_path,monkeypatch,official_inventory):
    s,g,c,root=selftest_fixture(experiment,tmp_path,monkeypatch,official_inventory=official_inventory)
    before={p.name:p.read_bytes() for p in root.iterdir()};launched=[]
    class Host:
        pid=42;returncode=0
        def __init__(self,args,**kwargs):
            launched.append((args,kwargs));self.polls=0;work=Path(kwargs['cwd'])
            assert args[1:]==['--test'] and work.resolve()!=root.resolve()
            (work/'dlss5-feed-host.log').write_text('[host] device adapter: NVIDIA GeForce RTX 4080  LUID 00000000:00000001  PCI 10DE:2704  driver 617.42 (DXGI\'s default adapter)\n[host] NVSDK_NGX_D3D12_Init -> 0x00000001 (Success)\n[host] --test finished: 300/300 evaluates succeeded\n')
        def poll(self):self.polls+=1;return None if self.polls==1 else 0
    monkeypatch.setattr('akeno.diagnostics.selftest.subprocess.Popen',Host)
    monkeypatch.setattr('akeno.diagnostics.selftest.processes.creation_identity',lambda _: 'filetime:123')
    monkeypatch.setattr('akeno.diagnostics.selftest.processes.module_inventory',lambda *a:{'state':'unavailable','modules':[]})
    result=s.neural_selftest.run(c.sha256,True)
    assert len(launched)==1 and result['completion']=='completed' and result['status']=='inconclusive'
    assert result['dlss_evaluations']['successful']==300 and not result['neural_evaluations']
    assert result['runtime']['sha256']==c.sha256 and result['host_adapter']['device_id']==0x2704
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
    assert s.db.get_setting('last_neural_selftest')['status']=='inconclusive'


def test_selftest_refuses_modified_official_inventory_sr(experiment,tmp_path,monkeypatch):
    """A stored inventory row alone cannot authorize a tampered NVIDIA DLL."""
    from akeno.diagnostics.selftest import _official_sr_from_inventory
    s,g,c,root=selftest_fixture(experiment,tmp_path,monkeypatch,official_inventory=True)
    record=next(item for item in s.db.get_setting('official_dll_inventory',{}).values()
                if item['name']=='nvngx_dlss.dll')
    path=Path(record['stored_path'])
    path.write_bytes(path.read_bytes()+b'tampered')
    assert _official_sr_from_inventory(s) is None


def test_selftest_never_runs_implicitly(experiment,monkeypatch):
    s,g,c,root=experiment
    monkeypatch.setattr('akeno.diagnostics.selftest.subprocess.Popen',lambda *a,**k:pytest.fail('Implicit test launch'))
    with pytest.raises(PermissionError,match='explicitly'):s.neural_selftest.run(c.sha256)


def test_shader_dependency_graph_is_complete_and_hash_pinned(experiment,tmp_path):
    s,*_=experiment
    shader=tmp_path/'Provider.fx';shader.write_text('#include "ReShade.fxh"\n')
    entry={'role':'motion_provider','path':shader.name,'sha256':sha(shader.read_bytes()),'version':'synthetic','source':'https://github.com/example/source','license':'MIT','local_use_permitted':True}
    manifest=tmp_path/'shader-manifest.json';manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':[entry]}))
    with pytest.raises(ValueError,match='Missing pinned shader include'):s.pipelines.components.import_manifest(manifest,True)
    include=tmp_path/'ReShade.fxh';include.write_text('// synthetic include')
    entry['assets']=[{'path':include.name,'destination':'reshade-shaders/Shaders/ReShade.fxh','sha256':sha(include.read_bytes())}]
    manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':[entry]}))
    assert s.pipelines.components.import_manifest(manifest,True)['motion_provider']['assets'][0]['stored_path']


def test_feeder_and_optiscaler_composite_is_one_complete_reversible_plan(experiment,tmp_path,monkeypatch):
    import zipfile
    from dataclasses import asdict
    from akeno.security.catalog import Catalog
    s,g,c,root=selftest_fixture(experiment,tmp_path,monkeypatch)
    (root/'nvngx_dlss.dll').unlink() # remove this test's original synthetic SR file
    make_pe(root/'Game.exe',['d3d12.dll','winmm.dll','dxgi.dll'],pad=50000)
    addon=make_pe(tmp_path/'addon'/'dlss5-feed.addon64',dll=True)
    data={'dlss5-feed.addon64':addon.read_bytes(),'reshade-shaders/Shaders/DLSS5_Feed.fx':b'// Synthetic shader fixture only'}
    archive=tmp_path/'complete-feeder-fixture.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for name,body in data.items():z.writestr(name,body)
    entries=[]
    for package in s.catalog.packages:
        entry=asdict(package);entry['trust']='verified'
        if entry['backend']=='dlss5_feeder':entry.update(sha256=sha(archive.read_bytes()),files={name:sha(body) for name,body in data.items()})
        entries.append(entry)
    s.catalog=Catalog({'schema':1,'catalog_version':'synthetic','allowed_hosts':['github.com'],'packages':entries})
    s.downloads.files[entries[1]['url']]=archive
    reshade=make_pe(tmp_path/'imports'/'ReShade64.dll',dll=True,exports=['ReShadeRegisterAddon','ReShadeUnregisterAddon'])
    shader=tmp_path/'imports'/'Motion.fx';shader.write_text('// synthetic provider contract; no physical rendering')
    items=[]
    for role,path in [('reshade',reshade),('motion_provider',shader)]:
        items.append({'role':role,'path':path.name,'sha256':sha(path.read_bytes()),'version':'synthetic','source':'https://github.com/example/source','license':'MIT','architecture':'x64' if role=='reshade' else None,'apis':['d3d12'],'local_use_permitted':True})
    manifest=tmp_path/'imports'/'manifest.json';manifest.write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':items}))
    s.pipelines.components.import_manifest(manifest,True)
    s.library.reanalyze(g.id)
    decision=next(r for r in s.pipelines.analyze(g.id,True,c.sha256)['routes'] if r.route_id=='feeder')
    assert decision.eligible and decision.recommended
    before={p.name:p.read_bytes() for p in root.iterdir()}
    prepared=s.pipelines.prepare(g.id,'feeder',c.sha256,True,True)
    paths={op.rel_path for op in prepared.plan.ops}
    assert {'winmm.dll','dxgi.dll','dlss5-feed.addon64','nvngx.dll_dlssnr.dll','nvngx_dlssnr.dll','nvngx_dlss.dll','reshade-shaders/Shaders/Motion.fx'}<=paths
    result=s.pipelines.install(prepared);assert result.ok,result.messages
    assert s.installer.restore(result.tx_id).ok
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
