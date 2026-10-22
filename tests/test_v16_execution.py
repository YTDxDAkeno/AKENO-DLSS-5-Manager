"""Synthetic fixtures for evidence semantics; no RTX or game acceptance is claimed."""
from pathlib import Path
import json
import pytest
from akeno.diagnostics.execution import diagnose,contract
from akeno.diagnostics import neural
from akeno.diagnostics.counters import parse_counter,monotonic_records,BUILD
from test_v15_evidence import line
from test_community_runtime import experiment,nr,prepare,unsigned
from akeno.community.runtime import Category
from akeno.security.authenticode import SignatureInfo


def test_secure_loader_and_capability_failure_do_not_erase_sr_success():
    from akeno.diagnostics.selftest import parse_host_result
    text='[host] --test finished: 300/300 evaluates succeeded\n[feed] NGX feature requirements: feature 18 -> query failed 0xBAD00012 (NotImplemented)\nnvLoadSignedLibraryW() failed: Die digitale Signatur des Objekts konnte nicht bestätigt werden 0xBAD00000'
    parsed=parse_host_result(text,exit_code=0)
    health=diagnose(text)
    assert parsed['dlss_evaluations']['successful']==300 and not parsed['neural_evaluations']
    assert health['secure_loader']=='signature_rejected'
    assert health['feature_18_requirements_query']=='not_implemented'
    report=contract(diagnostics=health)
    assert 'NVIDIA-authenticated' in report['next_action']
    assert not any(report['stages'].values())


def test_module_presence_model_created_message_never_becomes_feature_creation(tmp_path):
    from akeno.hardware.support import verify_from_logs
    for text in ('neural model (feature 18) loaded','DLSS-NR model created (runtime module present)'):
        assert neural.classify(text) not in ('initialized','running')
    (tmp_path/'dlss5-feed.log').write_text('neural model (feature 18) loaded\n')
    assert verify_from_logs(str(tmp_path)).state=='none'


def test_authorized_api_success_is_separate_from_gpu_and_display():
    counter=parse_counter(line())
    r=contract(stages={'runtime_loaded':True,'evaluations_attributed_to_game':True},counters=counter)
    assert r['stages']['feature_created'] and r['stages']['evaluation_api_succeeded']
    assert not r['stages']['gpu_execution_completed'] and not r['stages']['display_contribution_verified']
    assert r['stability']['state']=='unconfirmed'
    assert not contract(counters=counter)['stages']['evaluation_api_succeeded']


@pytest.mark.parametrize('code,kind',[('0x887A0006','device_hung'),('0x887A0005','device_removed'),('0x887A0007','device_reset')])
def test_device_failures_are_reported_without_nr_prefix(code,kind):
    health=diagnose('DRED: device removed, reason '+code)
    assert health['stability_incidents'][0]['kind']==kind
    assert contract(diagnostics=health)['stability']['state']=='failure_observed'


def test_issue63_stall_and_dred_do_not_suggest_tdr_workaround():
    health=diagnose('[feed] the GPU did not retire allocator slot 2 within 2000 ms\n[feed] STALL frame 109: feed 25128.47 ms (of which NGX evaluate 25029.52 ms)\nDRED: reason 0x887A0006')
    assert health['ngx_evaluate_max_ms']==25029.52 and len(health['stability_incidents'])==3
    assert 'do not change Windows TDR' in contract(diagnostics=health)['next_action']


def test_v3_cpu_latency_and_dispatch_counts_are_not_gpu_completion():
    counter=parse_counter(line()+' eval_max_us=25029520 eval_stalls=1 forwarder_evals=4500 proxy_evals=0')
    result=contract(counters=counter,stages={'evaluations_attributed_to_game':True})
    assert result['stability']['state']=='failure_observed' and result['stages']['evaluation_api_succeeded']
    assert not result['stages']['gpu_execution_completed']
    assert parse_counter(line()+' eval_max_us=NaN') is None
    assert monotonic_records([parse_counter(line(seq=1)),parse_counter(line(seq=2,success=5000))])['eval_success']==5000
    with pytest.raises(ValueError):monotonic_records([parse_counter(line(seq=1)),parse_counter(line(seq=2,success=3))])


def test_gpu_failure_is_latched_after_success_and_even_after_log_excerpt_eviction(tmp_path):
    exe=tmp_path/'g.exe';exe.write_bytes(b'inert');model=tmp_path/'nvngx_dlssnr.dll';model.write_bytes(b'inert')
    reader=neural.NeuralMonitor(neural.begin('g',tmp_path,exe,['nvngx_dlssnr.dll'],diagnostic_builds=[BUILD]))
    log=tmp_path/'OptiScaler.log';log.write_text(line()+'\nDRED: device removed 0x887A0006\n'+'ordinary SR line\n'*100)
    result=reader.poll([str(exe)],process_identity={'pid':42,'created_at':'filetime:123'},diagnostic_loaded=True)
    assert result.state=='failed' and result.execution['stability']['state']=='failure_observed'
    # A later log success cannot erase a device-hung incident from this lifetime.
    with log.open('a') as f:f.write(line(seq=2,success=5000)+'\n')
    assert reader.poll([str(exe)],process_identity={'pid':42,'created_at':'filetime:123'},diagnostic_loaded=True).state=='failed'


def test_official_nr_inventory_is_selectable_but_signature_is_rechecked(experiment,nr):
    s,g,c,root=experiment
    import hashlib
    pin=hashlib.sha256(nr.read_bytes()).hexdigest()
    s.db.set_setting('community-runtime:'+pin,None)
    s.db.set_setting('official_dll_inventory',{pin:{'name':'nvngx_dlssnr.dll','family':'neural_rendering',
        'sha256':pin,'version':'310.8.0.0','stored_path':str(nr),'trust':'nvidia_authenticated'}})
    s.community.runtimes.verifier=lambda _:SignatureInfo(True,True,'NVIDIA Corporation','Synthetic signature')
    assert s.community._candidate(pin).category==Category.OFFICIAL
    s.community.runtimes.verifier=unsigned
    with pytest.raises(PermissionError,match='signature'):s.community._candidate(pin)


def test_official_import_rejects_signature_to_copy_race(experiment,nr):
    s,*_=experiment
    def change(path):
        with open(path,'ab') as f:f.write(b'changed during signature check')
        return SignatureInfo(True,True,'NVIDIA Corporation','Synthetic')
    s.dlls.verifier=change
    with pytest.raises(PermissionError,match='changed during'):s.dlls.import_official(nr,True)


def test_normal_game_launch_starts_observation_before_popen_and_never_elevates(experiment,monkeypatch):
    s,g,c,root=experiment
    assert s.community.install(prepare(experiment),True,True).ok
    monkeypatch.setattr('akeno.paths.is_windows',lambda:True)
    events=[]
    import subprocess
    def launch(args,**kwargs):
        events.append((args,kwargs));assert s.community.sessions[g.id].state=='waiting_for_game'
    monkeypatch.setattr(subprocess,'Popen',launch)
    with pytest.raises(PermissionError):s.community.launch_test(g.id,False,True)
    assert not events
    result=s.community.launch_test(g.id,True,True)
    assert result['launch_requested'] and events==[([str(root/'Game.exe')],{'cwd':str(root)})]
    assert not result['report']['neural']['execution']['stages']['evaluation_api_succeeded']


def test_launch_refuses_changed_installed_binary(experiment,monkeypatch):
    s,g,c,root=experiment
    assert s.community.install(prepare(experiment),True,True).ok
    monkeypatch.setattr('akeno.paths.is_windows',lambda:True)
    (root/'winmm.dll').write_bytes(b'changed')
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Changed binary must not launch'))
    with pytest.raises((PermissionError,ValueError)):s.community.launch_test(g.id,True,True)


def test_selftest_writes_json_only_zip_and_separates_phases(experiment,tmp_path,monkeypatch):
    from test_v15_integration import selftest_fixture
    from zipfile import ZipFile
    from akeno.paths import app_home
    s,g,c,root=selftest_fixture(experiment,tmp_path,monkeypatch,official_inventory=True)
    class Host:
        pid=42;returncode=0
        def __init__(self,args,**kwargs):
            self.polls=0;work=Path(kwargs['cwd'])
            (work/'dlss5-feed-host.log').write_text('[host] device adapter: NVIDIA GeForce RTX 4080  LUID 00000000:00000001  PCI 10DE:2704  driver 617.42 (synthetic)\n[host] --test finished: 300/300 evaluates succeeded\n[feed] NGX feature requirements: feature 18 -> query failed 0xBAD00012\nnvLoadSignedLibraryW() failed: signature rejected 0xBAD00000\n')
        def poll(self):self.polls+=1;return None if self.polls==1 else 0
    monkeypatch.setattr('akeno.diagnostics.selftest.subprocess.Popen',Host)
    monkeypatch.setattr('akeno.diagnostics.selftest.processes.creation_identity',lambda _:'filetime:123')
    monkeypatch.setattr('akeno.diagnostics.selftest.processes.module_inventory',lambda *_:{'state':'observed','modules':[]})
    result=s.neural_selftest.run(c.sha256,True)
    assert result['phases']['super_resolution']['evaluations']['successful']==300
    assert result['execution']['diagnostics']['secure_loader']=='signature_rejected'
    assert not result['execution']['stages']['evaluation_api_succeeded']
    with ZipFile(app_home()/'self-tests'/result['id']/'result.zip') as z:
        assert z.namelist()==['result.json']
        assert json.loads(z.read('result.json'))['execution']==result['execution']
