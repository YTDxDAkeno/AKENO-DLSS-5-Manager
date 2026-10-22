"""Permission denials never create an automatic UAC loop."""
from pathlib import Path
import pytest
from akeno.benchmark.service import PresentMonRunner,BenchmarkError
from akeno.security.catalog import Catalog
from akeno.security.downloads import CancelToken


def runner(monkeypatch):
    r=PresentMonRunner(Catalog.load_bundled().tool('presentmon'),None)
    monkeypatch.setattr('akeno.benchmark.service.is_windows',lambda:True)
    monkeypatch.setattr(r,'ensure',lambda:Path('verified-presentmon.exe'))
    return r


def test_automatic_permission_failure_is_latched_without_launch(monkeypatch,tmp_path):
    r=runner(monkeypatch)
    monkeypatch.setattr('akeno.benchmark.permissions.capture_permission',lambda:{'state':'permission_required','detail':'ETW token denied'})
    monkeypatch.setattr(r,'_run_elevated',lambda *a:pytest.fail('Automatic UAC request'))
    monkeypatch.setattr('akeno.benchmark.service.subprocess.Popen',lambda *a,**k:pytest.fail('Known denial must not launch'))
    for _ in range(20):
        with pytest.raises(BenchmarkError,match='ETW token denied'):r.capture('game.exe',30,0,tmp_path/'out.csv')
    assert r.permission_status()['state']=='denied'


def test_explicit_elevated_capture_once_and_denial_latches(monkeypatch,tmp_path):
    r=runner(monkeypatch);r.permission_error='Previous ETW denial';calls=[]
    def elevated(*args):calls.append(args);raise BenchmarkError('administrator permission was declined')
    monkeypatch.setattr(r,'_run_elevated',elevated)
    with pytest.raises(BenchmarkError,match='declined'):r.capture('g.exe',10,0,tmp_path/'out.csv',allow_elevation=True)
    with pytest.raises(BenchmarkError,match='declined'):r.capture('g.exe',10,0,tmp_path/'out.csv')
    assert len(calls)==1


def test_overlapping_capture_and_pre_cancelled_launch_are_rejected(monkeypatch,tmp_path):
    r=runner(monkeypatch);r._capture_lock.acquire()
    with pytest.raises(BenchmarkError,match='already active'):r.capture('g.exe',10,0,tmp_path/'out.csv')
    r._capture_lock.release();token=CancelToken();token.cancel()
    with pytest.raises(BenchmarkError,match='cancelled'):r.capture('g.exe',10,0,tmp_path/'out.csv',token=token)
    args=r._args('g.exe',tmp_path/'out.csv',10,0,12)
    assert '--terminate_on_proc_exit' in args and '--stop_existing_session' not in args
