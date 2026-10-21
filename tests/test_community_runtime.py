"""Community policy tests use inert synthetic PE bytes and explicit fake verifiers."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from akeno.backends import runtimes
from akeno.community.runtime import Category, RuntimeManager, load_review, validate_review
from akeno.community.service import CommunityService
from akeno.core.models import AntiCheatRisk, GraphicsApi
from akeno.security.authenticode import SignatureInfo
from akeno.security.catalog import Catalog
from akeno.services import AppServices
from helpers import FakeDownloads, make_opti_package, make_pe, rtx, system_with
from test_runtime_identity import candidate


def unsigned(_):
    return SignatureInfo(True, False, None, "Synthetic unsigned fixture")


def reviewed(path):
    return {"schema": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "version": "310.8.0.0",
        "repository": "https://github.com/example/research", "release": "https://github.com/example/research/releases/tag/test",
        "upstream_commit": "a" * 40, "reviewer": "Synthetic reviewer", "reviewed_at": "2026-10-21",
        "modifications": "Synthetic fixture, no executable modification", "build_instructions": "tests/helpers.py",
        "security_review": "Inert fixture, not a real safety review", "license_review": "Test-owned bytes",
        "local_use_permitted": True, "limitations": ["No real GPU execution"],
        "targets": [{"generation": "RTX 40", "driver": "617.42", "backend": "optiscaler_nr", "api": "d3d12",
                     "status": "experimental", "evidence": "Synthetic eligibility fixture, not working hardware"}]}


@pytest.fixture
def nr(tmp_path):
    return candidate(tmp_path / 'runtime', runtime=runtimes.NR_RUNTIME, original=runtimes.NR_RUNTIME,
                     product="NVIDIA DLSSNR", export_name=runtimes.NR_RUNTIME, version=(310,8,0,0))


def test_four_trust_categories_and_official_gate_stays_separate(db, nr):
    m = RuntimeManager(db, unsigned)
    assert m.inspect(str(nr)).category == Category.UNKNOWN
    review = reviewed(nr)
    assert m.inspect(str(nr), review).category == Category.COMMUNITY
    assert runtimes.evaluate(str(nr), verifier=unsigned).trust.value == "blocked"
    signed = RuntimeManager(db, lambda _: SignatureInfo(True, True, "NVIDIA Corporation", "fixture"))
    assert signed.inspect(str(nr)).category == Category.OFFICIAL
    nr.write_bytes(b'corrupt')
    assert m.inspect(str(nr), review).category == Category.REJECTED


@pytest.mark.parametrize('change', ['hash','version','source','permission','review','signature_unavailable'])
def test_review_requirements(db,nr,change):
    review = reviewed(nr)
    if change == 'hash': review['sha256'] = '0'*64
    if change == 'version': review['version'] = '9.9.9'
    if change == 'source': review['release'] = 'https://github.com/attacker/repack/releases/tag/test'
    if change == 'permission': review['local_use_permitted'] = False
    if change == 'review': review['security_review'] = ''
    m = RuntimeManager(db, unsigned if change != 'signature_unavailable' else lambda _: SignatureInfo(False,False,None,'Linux'))
    assert m.inspect(str(nr),review).category in (Category.REJECTED,Category.UNKNOWN)


@pytest.mark.parametrize('machine,dll,exports', [('x86',True,None),('x64',False,None),('x64',True,[])])
def test_invalid_runtime_shapes(db,tmp_path,machine,dll,exports):
    p = make_pe(tmp_path/runtimes.NR_RUNTIME, machine=machine,dll=dll,exports=exports,strings={'OriginalFilename':runtimes.NR_RUNTIME})
    assert RuntimeManager(db,unsigned).inspect(str(p)).category == Category.REJECTED


def test_renamed_sr_is_not_nr(db,tmp_path):
    p = candidate(tmp_path,runtime=runtimes.NR_RUNTIME,original=runtimes.DLSS_RUNTIME)
    assert RuntimeManager(db,unsigned).inspect(str(p),reviewed(p)).category == Category.REJECTED


def test_denylisted_hash_and_script_archive_never_import(db,nr,tmp_path):
    m = RuntimeManager(db,unsigned, [reviewed(nr)['sha256']])
    assert m.inspect(str(nr),reviewed(nr)).category == Category.REJECTED
    for ext in ('zip','exe','bat','ps1'):
        p=tmp_path/('payload.'+ext); p.write_bytes(nr.read_bytes())
        assert m.inspect(str(p)).category == Category.REJECTED


def test_import_requires_consent_and_keeps_official_store_clean(db,nr):
    m=RuntimeManager(db,unsigned)
    with pytest.raises(PermissionError): m.import_local(str(nr),reviewed(nr),False)
    with pytest.raises(PermissionError): m.import_local(str(nr),None,True)
    c=m.import_local(str(nr),reviewed(nr),True)
    assert Path(c.path).read_bytes()==nr.read_bytes()
    assert runtimes.best_runtime(db,runtimes.NR_RUNTIME) is None
    assert m.imported()[0]['category']=='reviewed_community'


def test_import_rejects_source_changed_during_signature_inspection(db,nr):
    review=reviewed(nr)
    def changing(p):
        with open(p,'ab') as f: f.write(b'x')
        return unsigned(p)
    assert RuntimeManager(db,changing).inspect(str(nr),review).category==Category.REJECTED


def test_review_loader_is_bounded_and_not_executable(tmp_path):
    p=tmp_path/'review.ps1'; p.write_text('{}')
    with pytest.raises(ValueError): load_review(p)


@pytest.fixture
def experiment(db,tmp_path,nr):
    z,entry=make_opti_package(tmp_path/'pkg')
    s=AppServices(db=db,catalog=Catalog({'schema':1,'catalog_version':'test','allowed_hosts':['github.com'],'packages':[entry]}),
                  downloads=FakeDownloads({entry['url']:z},tmp_path/'cache'))
    gpu=rtx('NVIDIA GeForce RTX 4080',device=0x2704,vram_gb=16);gpu.driver_version='617.42';s.system=system_with(gpu)
    root=tmp_path/'game';make_pe(root/'Game.exe',['d3d12.dll'],pad=50000)
    make_pe(root/'nvngx_dlss.dll',[],dll=True)
    g=s.library.add_manual(str(root),'Offline Fixture')
    s.select_game(g.id)
    m=RuntimeManager(db,unsigned);s.community=CommunityService(s,m)
    c=m.import_local(str(nr),reviewed(nr),True)
    return s,g,c,root


def prepare(experiment):
    s,g,c,root=experiment
    return s.community.prepare(g.id,c.sha256,'optiscaler_nr',{},True,True)


def test_rtx4080_offline_dry_run_install_observe_restore(experiment):
    s,g,c,root=experiment
    before={p.name:p.read_bytes() for p in root.iterdir()}
    plan=prepare(experiment)
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
    with pytest.raises(PermissionError): s.install(g,plan.plan)
    result=s.community.install(plan,True,True)
    assert result.ok,result.messages
    report=s.community.observe(g.id)
    assert report['neural']['state']=='installed'
    assert report['runtime']['sha256']==c.sha256
    assert s.restore(result.tx_id).ok
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


@pytest.mark.parametrize('scenario', ['no_consent','online','fivem','renderer','driver','anti_cheat','target_incompatible'])
def test_ineligible_experiments_stay_blocked(experiment,monkeypatch,scenario):
    s,g,c,root=experiment
    if scenario=='driver': s.system.gpus[0].driver_version='610.88'
    if scenario=='renderer':
        make_pe(root/'Game.exe',['opengl32.dll'],pad=50000)
    if scenario in ('anti_cheat','fivem'):
        fresh=s.library.reanalyze(g.id)
        if scenario=='anti_cheat': fresh.anti_cheat=AntiCheatRisk.DETECTED
        else: fresh.launcher='fivem'
        monkeypatch.setattr(s.community,'_game',lambda _:fresh)
    if scenario=='target_incompatible':
        data=s.db.get_setting('community-runtime:'+c.sha256);data['review']['targets'][0]['status']='incompatible';s.db.set_setting('community-runtime:'+c.sha256,data)
    with pytest.raises(PermissionError):
        s.community.prepare(g.id,c.sha256,'optiscaler_nr',{},scenario!='online',scenario!='no_consent')


@pytest.mark.parametrize('change',['runtime','backend','game','plan','gpu'])
def test_changed_inputs_invalidate_prepared_plan(experiment,change):
    s,g,c,root=experiment; prepared=prepare(experiment)
    if change=='runtime': Path(c.path).write_bytes(b'changed')
    if change=='backend': prepared.plan.ops[0].source.write_bytes(b'changed')
    if change=='game': (root/'Game.exe').write_bytes(b'changed')
    if change=='plan': prepared.plan.ops[0].rel_path='../escape.dll'
    if change=='gpu': s.system.gpus[0].driver_version='610.88'
    with pytest.raises((PermissionError,ValueError)): s.community.install(prepared,True,True)


def test_failed_transaction_rolls_back_and_preserves_game(experiment,monkeypatch):
    s,g,c,root=experiment;prepared=prepare(experiment)
    real=s.installer.apply
    monkeypatch.setattr(s.installer,'apply',lambda game,plan:real(game,plan,fail_after=2))
    result=s.community.install(prepared,True,True)
    assert not result.ok and result.state=='rolled_back'
    assert sorted(p.name for p in root.iterdir())==['Game.exe','nvngx_dlss.dll']
