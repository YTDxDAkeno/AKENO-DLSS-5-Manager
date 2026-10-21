"""Synthetic named-candidate plumbing; no Bandukids/NVIDIA DLL is downloaded or executed."""
from dataclasses import replace
import pytest
from akeno.community import candidates
from akeno.community.runtime import Category, RuntimeManager, validate_review
from akeno.security.authenticode import SignatureInfo
from test_community_runtime import nr, unsigned, reviewed, experiment


@pytest.fixture
def named(nr,monkeypatch):
    # Pin inert test bytes in a test-only registry; never spoof the published DLL hash.
    from akeno.security.downloads import sha256_file
    c=replace(candidates.BANDUKIDS,sha256=sha256_file(nr))
    monkeypatch.setattr(candidates,'BANDUKIDS',c)
    return c


def complete_review(c,nr):
    data=reviewed(nr)
    data.pop('release');data.update(schema=2,candidate_id=c.id,sha256=c.sha256,
        repository=c.repository,upstream_commit=c.source_commit,runtime_source=c.runtime_source)
    data['targets'][0]['gpu_device_id']=0x2704
    return data


def test_production_pin_and_profiles_are_unverified():
    c=candidates.BANDUKIDS
    assert c.sha256=='8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206'
    data=c.to_dict()
    assert data['profile']['status']=='unverified'
    assert data['other_generations']['RTX 20']==data['other_generations']['RTX 30']=='unverified'
    assert not data['review_approved'] and not data['download_automatically']


def test_hash_match_alone_is_not_review_or_nvidia_authentication(db,nr,named):
    manager=RuntimeManager(db,unsigned)
    result=manager.inspect(str(nr),candidate_id=named.id)
    assert result.category==Category.UNKNOWN and result.named_candidate['id']==named.id
    with pytest.raises(PermissionError):manager.import_local(str(nr),None,True,named.id)
    misleading=RuntimeManager(db,lambda _:SignatureInfo(True,True,'NVIDIA Corporation','fixture'))
    assert misleading.inspect(str(nr),candidate_id=named.id).category==Category.REJECTED


def test_exact_local_import_keeps_hashmismatch_separate(db,nr,named):
    m=RuntimeManager(db,lambda _:SignatureInfo(True,False,'NVIDIA Corporation','HashMismatch fixture'))
    data=complete_review(named,nr)
    imported=m.import_local(str(nr),data,True,named.id)
    assert imported.category==Category.COMMUNITY
    assert imported.signature['valid'] is False
    assert imported.to_dict()['execution_verified'] is False
    assert not db.query('SELECT * FROM runtimes')


@pytest.mark.parametrize('change',['hash','source','commit','permission','security','gpu'])
def test_named_review_cannot_bypass_missing_evidence(db,nr,named,change):
    data=complete_review(named,nr)
    if change=='hash':data['sha256']='0'*64
    if change=='source':data['runtime_source']='https://huggingface.co/other/model/blob/main/nvngx_dlssnr.dll'
    if change=='commit':data['upstream_commit']='0'*40
    if change=='permission':data['local_use_permitted']=False
    if change=='security':data['security_review']=''
    if change=='gpu':data['targets'][0].update(generation='RTX 30',gpu_device_id=0x2206)
    assert RuntimeManager(db,unsigned).inspect(str(nr),data,named.id).category==Category.REJECTED


def test_wrong_file_never_satisfies_named_selection(db,nr,named):
    with nr.open('ab') as f:f.write(b'changed')
    assert RuntimeManager(db,unsigned).inspect(str(nr),candidate_id=named.id).category==Category.REJECTED


def test_generic_review_cannot_mislabel_known_modified_candidate(nr,named):
    with pytest.raises(ValueError):validate_review(reviewed(nr))


def test_review_template_stays_incomplete(db,nr,named):
    e=RuntimeManager(db,unsigned).inspect(str(nr),candidate_id=named.id)
    template=candidates.review_draft(named,e,None,'d3d12','optiscaler_nr')
    assert template['schema']==2 and not template['local_use_permitted']
    assert template['reviewer']==template['security_review']==''
    with pytest.raises(ValueError):validate_review(template)


def test_named_offline_experiment_reuses_existing_adapter(experiment,monkeypatch):
    from pathlib import Path
    from akeno.security.downloads import sha256_file
    s,g,c,root=experiment
    named=replace(candidates.BANDUKIDS,sha256=sha256_file(c.path));monkeypatch.setattr(candidates,'BANDUKIDS',named)
    data=complete_review(named,Path(c.path))
    imported=s.community.runtimes.import_local(c.path,data,True,named.id)
    plan=s.community.prepare(g.id,imported.sha256,'optiscaler_nr',{},True,True)
    result=s.community.install(plan,True,True)
    assert result.ok,result.messages
    assert s.community.report(g.id)['runtime']['named_candidate']['profile']['status']=='unverified'
    assert s.restore(result.tx_id).ok


def test_comfyui_video_evaluation_is_not_a_game_success_marker():
    from akeno.diagnostics.neural import classify
    assert classify('nvngx_dlssnr.dll Feature 18 Evaluate succeeded') is None
    assert classify('[dlss5] runtime signatures and versions valid') is None


def test_named_source_links_are_guided_pages_only(qtbot,experiment,monkeypatch):
    from akeno.ui.community_runtime import CommunityRuntimeDialog
    from akeno.ui.controller import Controller
    from PySide6.QtGui import QDesktopServices
    s,g,c,root=experiment;dialog=CommunityRuntimeDialog(Controller(s));qtbot.addWidget(dialog)
    opened=[];monkeypatch.setattr(QDesktopServices,'openUrl',lambda u:opened.append(u.toString()))
    dialog.candidate_choice.setCurrentIndex(dialog.candidate_choice.findData(candidates.BANDUKIDS.id))
    assert opened==[] and not dialog.install_button.isEnabled()
    dialog.open_candidate_source(False);dialog.open_candidate_source(True)
    assert opened==[candidates.BANDUKIDS.repository+'/tree/'+candidates.BANDUKIDS.source_commit,candidates.BANDUKIDS.runtime_source]
    assert '/blob/' in opened[1] and '/resolve/' not in opened[1]
