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
