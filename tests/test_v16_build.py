"""Optional build-tool guards and real portable header compile, never GPU tests."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import json
import pytest
from akeno.diagnostics import localbuild
from akeno.diagnostics.source_build import wrap_calls


def test_local_build_is_explicit_and_missing_tools_cannot_launch(monkeypatch):
    monkeypatch.setattr(localbuild.subprocess,'Popen',lambda *a,**k:pytest.fail('No command may run'))
    with pytest.raises(PermissionError,match='explicitly'):localbuild.build()
    monkeypatch.setattr(localbuild,'prerequisites',lambda:{'ready':False,'missing':['v143','Windows SDK']})
    with pytest.raises(PermissionError,match='v143'):localbuild.build(True)


def test_packaged_header_is_identical_to_public_source_and_registry():
    public=Path('diagnostics/optiscaler/AkenoNrDiagnostic.h').read_bytes()
    assert Path('akeno/data/diagnostic_source/AkenoNrDiagnostic.h').read_bytes()==public
    assert json.loads(Path('akeno/data/diagnostic_backend.json').read_text())['counter_header_sha256']==hashlib.sha256(public).hexdigest()


def test_counter_records_latency_dispatch_and_failure_without_changing_return_values(tmp_path):
    compiler=shutil.which('g++') or shutil.which('clang++')
    if not compiler:pytest.skip('Portable C++ compiler unavailable')
    header=Path('diagnostics/optiscaler/AkenoNrDiagnostic.h').resolve().as_posix()
    src=tmp_path/'check.cpp';src.write_text('#define LOG_INFO(...) ((void)0)\n#include "'+header+'"\n#include <cassert>\nint main(){AkenoNrDiagnostic::RecordEvaluate(0xBAD00012u,25029520,true);assert(AkenoNrDiagnostic::counters.proxy==1);assert(AkenoNrDiagnostic::counters.stalls==1);assert(AkenoNrDiagnostic::counters.maxUs==25029520);assert(AkenoNrDiagnostic::counters.lastError==0xBAD00012u);assert(AkenoNrDiagnostic::Evaluate([]{return 1;})==1);assert(AkenoNrDiagnostic::counters.lastError==0xBAD00012u);}')
    exe=tmp_path/'header-test';subprocess.run([compiler,'-std=c++17',str(src),'-o',str(exe)],capture_output=True,check=True)
    subprocess.run([str(exe)],check=True)


def test_rect_call_wrapper_keeps_balanced_arguments_and_original_return():
    text,count=wrap_calls('int r = g_nr.evaluateRect(list, f(a, b), out);','g_nr.evaluateRect','Evaluate')
    assert count==1 and 'return g_nr.evaluateRect(list, f(a, b), out);' in text


def test_public_payload_rejects_nvidia_runtime_in_folder_and_zip(tmp_path):
    import sys,zipfile
    sys.path.insert(0,str(Path('tools').resolve()))
    from check_public_payload import check
    payload=tmp_path/'public';payload.mkdir();(payload/'AKENO.exe').write_bytes(b'inert')
    assert check(payload)==1
    (payload/'nvngx_dlssnr.dll').write_bytes(b'inert prohibited filename')
    with pytest.raises(ValueError):check(payload)
    target=tmp_path/'public.zip'
    with zipfile.ZipFile(target,'w') as z:z.writestr('AKENO/sl.dlss_nr.dll',b'inert')
    with pytest.raises(ValueError):check(target)
