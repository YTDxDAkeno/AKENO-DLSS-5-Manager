"""Synthetic independent recipe evidence; never a production FiveM/Bandukids approval."""
from dataclasses import replace
from pathlib import Path
import pytest
from akeno.community import candidates
from akeno.community.runtime import RuntimeManager
from akeno.core.models import AntiCheatRisk, CompatStatus
from akeno.fivem.community import review_digest
from test_community_runtime import unsigned
from test_runtime_identity import candidate
from test_bandukids_runtime import complete_review
from test_fivem_workflow import workflow, files


@pytest.fixture
def bandukids_fivem(workflow,tmp_path,monkeypatch):
    w=workflow
    nr=candidate(tmp_path/'bandukids',runtime='nvngx_dlssnr.dll',original='nvngx_dlssnr.dll',
        product='NVIDIA DLSSNR',export_name='nvngx_dlssnr.dll',version=(310,8,0,0))
    from akeno.security.downloads import sha256_file
    named=replace(candidates.BANDUKIDS,sha256=sha256_file(nr))
    monkeypatch.setattr(candidates,'BANDUKIDS',named)
    data=complete_review(named,nr);data['targets'][0].update(driver='617.14',api='d3d11',backend='dlss5_feeder')
    manager=RuntimeManager(w.svc.db,unsigned);w.svc.community.runtimes=manager
    imported=manager.import_local(str(nr),data,True,named.id)
    pins=dict(w.review.runtime_hashes);pins['nvngx_dlssnr.dll']=imported.sha256
    w.review=replace(w.review,runtime_hashes=tuple(pins.items()),signed_runtime=False,
        runtime_policy='reviewed_bandukids',community_candidate_id=named.id,
        community_review_sha256=review_digest(data),offline_feature_evaluated=True,
        client_plugin_loading_verified=True,client_protection_compatible=True)
    return w,imported


def adapter(w):return w.service.adapter(w.installation,'dlss5_feeder',w.policy,w.review)


def test_qualified_fivem_community_recipe_reuses_transaction_and_restore(bandukids_fivem):
    w,c=bandukids_fivem;before=files(w.root);gta=files(w.gta)
    a=adapter(w);result=w.service.compatibility(a)
    assert result.status==CompatStatus.EXPERIMENTAL and not result.blockers,[f.message for f in result.blockers]
    plan=w.service.prepare(a)
    assert plan.plan.target_dir==str(w.plugins)
    result=w.service.install(plan,w.policy)
    assert result.ok,result.messages
    assert files(w.gta)==gta
    assert w.service.restore(w.installation,result.tx_id).ok
    assert files(w.root)==before


@pytest.mark.parametrize('field',['offline_feature_evaluated','client_plugin_loading_verified','client_protection_compatible','neural_feature_evaluated'])
def test_no_fivem_enablement_without_independent_evidence(bandukids_fivem,field):
    w,c=bandukids_fivem;w.review=replace(w.review,**{field:False})
    result=w.service.compatibility(adapter(w))
    assert 'fivem_community_evidence' in {f.code for f in result.blockers}
    with pytest.raises(PermissionError):w.service.prepare(adapter(w))


@pytest.mark.parametrize('change',['pure','permission','protection','review_pin','runtime_change','candidate','signature_spoof'])
def test_community_route_preserves_fivem_security_gates(bandukids_fivem,change):
    w,c=bandukids_fivem
    if change=='pure':w.policy=replace(w.policy,pure_level=2)
    if change=='permission':w.policy=replace(w.policy,graphics_plugins_allowed=False)
    if change=='protection':w.installation.game.anti_cheat=AntiCheatRisk.DETECTED
    if change=='review_pin':w.review=replace(w.review,community_review_sha256='0'*64)
    if change=='runtime_change':Path(c.path).write_bytes(b'changed')
    if change=='candidate':w.review=replace(w.review,community_candidate_id='unknown')
    if change=='signature_spoof':w.review=replace(w.review,signed_runtime=True)
    assert w.service.compatibility(adapter(w)).blockers
    with pytest.raises(PermissionError):w.service.prepare(adapter(w))
