"""Synthetic local regression tests. No NVIDIA execution or compatibility is implied."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
import zipfile
import pytest
from akeno.core.processes import ProcessInfo
from akeno.diagnostics import neural
from akeno.diagnostics.privacy import redact
from akeno.diagnostics.support import safe_report, export_support_zip
from akeno.community.runtime import Category
from test_community_runtime import experiment, nr


@pytest.fixture
def installed(experiment):
    s,g,r,root = experiment
    plan = s.community.prepare(g.id, r.sha256, 'optiscaler_nr', {}, True, True)
    result = s.community.install(plan, True, True)
    assert result.ok, result.messages
    return s,g,r,root


@pytest.fixture
def live(monkeypatch):
    value = []
    monkeypatch.setattr('akeno.community.service.processes.processes_in_dir', lambda *a,**k: list(value))
    monkeypatch.setattr('akeno.core.processes.creation_identity', lambda pid: 'test-birth-' + str(pid))
    monkeypatch.setattr('akeno.core.processes.module_inventory', lambda *a: {'state':'unavailable','modules':[], 'detail':'fixture'})
    return value


def test_large_unchanged_inputs_are_hashed_once_and_idle_logs_never_reread(tmp_path, monkeypatch, record_property):
    exe = tmp_path/'Borderlands4.exe'; model = tmp_path/'nvngx_dlssnr.dll'
    for p,size in ((exe,771*1024**2),(model,158*1024**2)):
        with p.open('wb') as out: out.truncate(size)  # sparse inert fixture; never executed
    log = tmp_path/'OptiScaler.log'; log.write_bytes(b'x'*(2320*1024))
    real = neural.sha256_file; calls=[]
    def counted(path): calls.append(str(path)); return real(path)
    monkeypatch.setattr(neural,'sha256_file',counted)
    observation = neural.begin('game',tmp_path,exe,[model.name])
    monitor = neural.NeuralMonitor(observation)
    before = time.perf_counter()
    for _ in range(300):
        result = monitor.poll()
        assert result.overhead['log_bytes_read'] == 0
        assert result.overhead['full_file_hashes_this_poll'] == 0
        assert result.logs[0]['activity'] == 'no_new_content'
    record_property('idle_poll_ms', (time.perf_counter()-before)*1000/300)
    assert calls == [str(model),str(exe)]
    model.write_bytes(b'changed')
    assert monitor.poll().state == 'stale'
    assert len(calls) == 2


def test_incremental_log_reads_and_no_duplicate_events(tmp_path):
    exe=tmp_path/'game.exe'; exe.write_bytes(b'inert')
    observation=neural.begin('g',tmp_path,exe,[]); monitor=neural.NeuralMonitor(observation)
    log=tmp_path/'OptiScaler_123.log'
    log.write_text('DLSS-NR forwarder loaded from fixture\nfeature 18 created\n')
    first=monitor.poll([str(exe)])
    with log.open('a') as out: out.write('inline feature 18 evaluation succeeded\n')
    second=monitor.poll([str(exe)])
    assert second.state == 'verified' if observation.artifacts else second.stages['feature_evaluated']
    assert second.overhead['log_bytes_read'] == len('inline feature 18 evaluation succeeded\n')
    for _ in range(100):
        result=monitor.poll([str(exe)])
        assert result.lines == second.lines
        assert result.overhead['log_bytes_read'] == 0


@pytest.mark.parametrize('replacement', ['truncate','replace'])
def test_log_rotation_invalidates_session(tmp_path,replacement):
    exe=tmp_path/'game.exe'; exe.write_bytes(b'inert')
    log=tmp_path/'OptiScaler.log'; log.write_text('old content\n')
    monitor=neural.NeuralMonitor(neural.begin('g',tmp_path,exe,[]))
    if replacement=='replace': log.unlink()
    log.write_text('feature 18 created\n')
    assert monitor.poll().state=='stale'


def test_configured_log_name_is_discovered_but_outside_scope_is_not(tmp_path):
    (tmp_path/'OptiScaler.ini').write_text('[Log]\nLogFileName=logs/custom.log\n')
    (tmp_path/'logs').mkdir(); target=tmp_path/'logs/custom_123.log';target.write_text('fixture')
    assert target in neural.discover_logs(tmp_path)
    (tmp_path/'OptiScaler.ini').write_text('[Log]\nLogFileName=../../outside.log\n')
    assert target not in neural.discover_logs(tmp_path)


@pytest.mark.parametrize('text,stage', [
    ('DLSS-NR (proxy): CreateFeature(18) failed 0xBAD00001','failed'),
    ('DLSS-NR (proxy): feature created at 1280x720 through the driver nvngx','initialized'),
    ('DLSS-NR Vulkan: the model initialised on this device','ngx_initialized'),
    ('DLSS-NR Vulkan: feature up at 1280x720','initialized'),
    ('DLSS-NR: pass 1 built at 1280x720, first evaluated next frame','initialized'),
    ('DLSS-NR SUPERSAMPLE: model at 1280x720, evaluate result 1 (Success)','running'),
    ('DLSS-NR proxy probe: feature 18 -> 0xBAD00001, control feature 200 -> 0xBAD00001','capability_probe'),
    ('DLSS-NR (proxy): re-init at SDK 0x15 returned 0x1 (idempotent)','ngx_initialized'),
    ('DLSS initialized successfully',None), ('Frame Generation evaluation succeeded',None),
    ('nvngx_dlssnr.dll Feature 18 Evaluate succeeded',None),
    ('Native DLSS EvaluateFeature result: 0x1',None),
])
def test_real_shaped_upstream_patterns(text,stage): assert neural.classify(text)==stage


def test_wait_start_evaluate_exit_and_restart_do_not_mix_sessions(installed, live):
    s,g,r,root=installed
    first=s.community.observe(g.id)
    assert first['session']['state']=='waiting_for_game'
    live.append(ProcessInfo(42,'Game.exe',g.exe_path))
    report=s.community.report(g.id)
    assert report['session']['state']=='backend_not_detected'
    (root/'OptiScaler.log').write_text('feature 18 created\ninline feature 18 evaluation succeeded\n')
    report=s.community.report(g.id)
    assert report['session']['state']=='neural_evaluation_detected'
    sid=report['session']['id']
    live.clear()
    final=s.community.report(g.id)
    assert final['session']['state']=='game_exited'
    assert final['neural']['stages']['evaluations_attributed_to_game']
    live.append(ProcessInfo(43,'Game.exe',g.exe_path))
    assert s.community.report(g.id)['session']['id']==sid
    fresh=s.community.observe(g.id)
    assert fresh['session']['id']!=sid
    assert not fresh['neural']['stages']['feature_evaluated']
    assert fresh['session']['started_after_game_launch']


def test_process_birth_change_does_not_reuse_pid(installed, live, monkeypatch):
    s,g,r,root=installed
    live.append(ProcessInfo(42,'Game.exe',g.exe_path))
    s.community.observe(g.id)
    monkeypatch.setattr('akeno.core.processes.creation_identity',lambda _: 'reused-pid-new-birth')
    assert s.community.report(g.id)['session']['state']=='game_restarted'


def test_poll_does_not_reanalyze_or_rehash_and_keeps_no_current_benchmark(installed,live,monkeypatch):
    s,g,r,root=installed;s.community.observe(g.id)
    monkeypatch.setattr(s.library,'reanalyze',lambda _:pytest.fail('library rescan'))
    monkeypatch.setattr(neural,'sha256_file',lambda _:pytest.fail('repeated full hash'))
    for _ in range(100):
        report=s.community.report(g.id)
        assert not report['measured_sessions']
        assert report['benchmark_status']=='No benchmark has been captured for the current installation.'
    assert 'no fresh' in report['neural']['detail'] if report['neural']['logs'] else 'No backend log' in report['neural']['detail']


def test_local_paths_redacted_urls_and_hash_metadata_preserved():
    urls=['https://github.com/Bandukids/ComfyUI-DLSS5-VideoSuite',
        'https://huggingface.co/Bandukids/DLSS-Runtimes/blob/main/nvngx_dlssnr.dll']
    data={'source':urls,'executable_sha256':'a'*64,'nested':[{'message':r'C:\Users\Alice\Downloads\runtime.dll '+urls[0]},
        r'\\server\Alice\private\log.txt','/home/Alice/private/log.txt']}
    cleaned=redact(data)
    encoded=json.dumps(cleaned)
    assert 'Alice' not in encoded and 'http[local' not in encoded
    assert cleaned['source']==urls and cleaned['executable_sha256']=='a'*64
    assert urls[0] in cleaned['nested'][0]['message']


def test_support_zip_contains_only_bounded_redacted_text_with_explicit_excerpt_consent(tmp_path):
    report={'runtime':{'category':'reviewed_community','source':'https://github.com/a/b'},
        'neural':{'raw_excerpts':[{'file':'OptiScaler.log','line':r'bad C:\Users\Alice\private.dll'}],
        'lines':[{'stage':'failed','line':r'C:\Users\Alice\file.dll','error_code':'0xBAD00001'}]}}
    with pytest.raises(PermissionError): export_support_zip(report,tmp_path/'no.zip',True,False)
    export_support_zip(report,tmp_path/'safe.zip')
    with zipfile.ZipFile(tmp_path/'safe.zip') as archive:
        assert set(archive.namelist())=={'akeno-diagnostics.json','README.txt'}
        payload=archive.read('akeno-diagnostics.json').decode()
        assert 'Alice' not in payload and 'raw_excerpts' not in payload
        assert 'community_experimental' in payload and 'reviewed_community' not in payload
    export_support_zip(report,tmp_path/'opt-in.zip',True,True)
    with zipfile.ZipFile(tmp_path/'opt-in.zip') as archive:
        assert all(n.endswith(('.json','.txt')) for n in archive.namelist())
        assert b'Alice' not in archive.read('redacted-backend-excerpts.txt')


def test_legacy_category_is_migrated_without_security_claims():
    assert Category('reviewed_community') is Category.COMMUNITY
    assert Category.COMMUNITY.value=='community_experimental'


@pytest.mark.skipif(__import__('sys').platform != 'win32',reason='Actual read-only Windows process APIs')
def test_windows_process_birth_and_module_snapshot_are_read_only():
    import subprocess,sys
    from akeno.core import processes
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    try:
        birth=processes.creation_identity(child.pid)
        assert birth and processes.creation_identity(child.pid)==birth
        result=processes.module_inventory(child.pid,birth)
        assert result['state'] in ('observed','unavailable')
        if result['state']=='observed': assert result['modules']
        assert processes.module_inventory(child.pid,'wrong-birth')['state']=='unavailable'
    finally:
        child.terminate();child.wait(timeout=5)
