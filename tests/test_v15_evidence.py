"""Synthetic NR protocol and source instrumentation tests, never GPU evidence."""
import subprocess
from pathlib import Path
import pytest
from akeno.diagnostics.selftest import parse_host_result
from akeno.diagnostics.counters import parse_counter,attributed,BUILD
from akeno.diagnostics import neural


def line(pid=42,birth=123,seq=1,success=4500):
    return f'AKENO_NR_V1 build={BUILD} pid={pid} birth={birth} seq={seq} create_attempts=1 create_success=1 create_fail=0 eval_attempts={success} eval_success={success} eval_fail=0 last_result=0x00000001'


def test_host_300_dlaa_success_does_not_prove_nr():
    result=parse_host_result('[host] NVSDK_NGX_D3D12_Init -> 0x00000001 (Success)\n[host] --test finished: 300/300 evaluates succeeded',exit_code=0)
    assert result['status']=='inconclusive' and result['dlss_evaluations']['successful']==300
    assert not result['neural_evaluations'] and result['feature_18_creation']=='not_observed'
    failed=parse_host_result('[host] NVSDK_NGX_D3D12_Init -> 0xBAD00001 (Fail)',exit_code=1)
    assert failed['status']=='fail' and '0xBAD00001' in failed['error_codes']


def test_counters_require_exact_process_birth_build_and_sane_counts():
    counter=parse_counter(line());assert counter['eval_success']==4500
    assert attributed(counter,{'pid':42,'created_at':'filetime:123'},[BUILD])
    assert not attributed(counter,{'pid':42,'created_at':'filetime:124'},[BUILD])
    assert parse_counter(line().replace('eval_attempts=4500','eval_attempts=1')) is None
    assert parse_counter(line().replace(BUILD,'unknown-build')) is None


def test_counter_stream_is_fresh_attributable_and_does_not_sum_periodic_totals(tmp_path):
    exe=tmp_path/'g.exe';exe.write_bytes(b'fixture');model=tmp_path/'nvngx_dlssnr.dll';model.write_bytes(b'synthetic-model')
    observation=neural.begin('g',tmp_path,exe,['nvngx_dlssnr.dll'],diagnostic_builds=[BUILD])
    reader=neural.NeuralMonitor(observation);log=tmp_path/'OptiScaler.log';log.write_text(line()+'\n')
    result=reader.poll([str(exe)],process_identity={'pid':42,'created_at':'filetime:123'},diagnostic_loaded=True)
    assert result.state=='verified' and result.counters['eval_success']==4500
    with log.open('a') as out:out.write(line(seq=2,success=5000)+'\n')
    assert reader.poll([str(exe)],process_identity={'pid':42,'created_at':'filetime:123'},diagnostic_loaded=True).counters['eval_success']==5000
    other=neural.NeuralMonitor(neural.begin('g',tmp_path,exe,['nvngx_dlssnr.dll'],diagnostic_builds=[BUILD]))
    with log.open('a') as out:out.write(line(seq=3)+'\n')
    assert other.poll([str(exe)],process_identity={'pid':99,'created_at':'filetime:123'},diagnostic_loaded=True).state!='verified'


def test_portable_counter_templates_preserve_results(tmp_path):
    import shutil
    compiler=shutil.which('g++') or shutil.which('clang++')
    if not compiler:pytest.skip('C++ compiler not available')
    source=tmp_path/'test.cpp';header=Path('diagnostics/optiscaler/AkenoNrDiagnostic.h').resolve().as_posix()
    source.write_text('#define LOG_INFO(...) ((void)0)\n#include "'+header+'"\n#include <cassert>\nint main(){assert(AkenoNrDiagnostic::Evaluate([]{return 1;})==1);assert(AkenoNrDiagnostic::Evaluate([]{return -1;})==-1);assert(AkenoNrDiagnostic::counters.success==1);assert(AkenoNrDiagnostic::counters.failed==1);}')
    exe=tmp_path/'counter-test';subprocess.run([compiler,'-std=c++17',str(source),'-o',str(exe)],check=True,capture_output=True)
    subprocess.run([str(exe)],check=True)


def test_other_managed_modules_cannot_authorize_diagnostic_counter(qapp,tmp_path,monkeypatch):
    from akeno.diagnostics.session import VerificationSession
    from akeno.core.processes import ProcessInfo
    import time
    exe=tmp_path/'game.exe';exe.write_bytes(b'synthetic exe')
    for name in ('winmm.dll','dxgi.dll','nvngx_dlssnr.dll'):(tmp_path/name).write_bytes(b'synthetic '+name.encode())
    observation=neural.begin('g',tmp_path,exe,['winmm.dll','dxgi.dll','nvngx_dlssnr.dll'],diagnostic_builds=[BUILD],diagnostic_modules=['winmm.dll'])
    session=VerificationSession(observation,{'id':5})
    monkeypatch.setattr('akeno.diagnostics.session.processes.creation_identity',lambda _: 'filetime:123')
    monkeypatch.setattr('akeno.diagnostics.session.processes.module_inventory',lambda *args:{'state':'observed','modules':[{'name':'dxgi.dll','path':str(tmp_path/'dxgi.dll')}],'detail':'Synthetic other backend'})
    log=tmp_path/'OptiScaler.log';log.write_text(line()+'\n')
    live=[ProcessInfo(42,'game.exe',str(exe))]
    assert session.poll(live,{'id':5}).state!='verified'
    session.modules={'state':'observed','modules':[{'name':'winmm.dll','path':str(tmp_path/'winmm.dll')}],'detail':'Synthetic exact diagnostic consumer'}
    session.module_checked=time.monotonic()
    with log.open('a') as output:output.write(line(seq=2)+'\n')
    assert session.poll(live,{'id':5}).state=='verified'
