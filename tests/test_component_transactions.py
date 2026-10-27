"""Signed module operations using fixture executables; real Windows packaging is tested separately."""
import json
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import test_updater_v2 as u
from akeno import __version__
from akeno.components.service import INVENTORY, SECURITY_FILES, SELECTION, selection, write_selection
from akeno.licensing import crypto
from akeno.updater import apply, service, staging
from tools.build_components import build


@pytest.fixture
def setup(tmp_path, monkeypatch):
    key=Ed25519PrivateKey.generate();public=u.tool.public_of(key)
    keys={u.tool.kid_of(public):crypto.public_key(public)}
    pkg=u.portable_zip(tmp_path,__version__)
    extra=tmp_path/'component'
    extra.mkdir()
    for rel in SECURITY_FILES:
        p=extra/'_internal'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# optional fixture\n')
    build(extra)
    with zipfile.ZipFile(pkg,'a') as z:
        for p in extra.rglob('*'):
            if p.is_file():z.write(p,'AKENO/'+p.relative_to(extra).as_posix())
    svc=u.service_for(tmp_path,key,keys,pkg,version=__version__)
    # fields() defaults to 3.1.0, so this fixture signs the actual package version explicitly.
    doc=u.envelope(key,u.fields(pkg=pkg,version=__version__))
    svc.fetch=lambda url:doc
    target=u.installed(tmp_path,__version__)
    monkeypatch.setattr(service,'install_dir',lambda:target)
    monkeypatch.setattr(service,'frozen',lambda:True)
    # the "running program" here is python.exe, which is code-signed on Windows; the rule has its own tests
    monkeypatch.setattr(service,'signature_check',lambda new_exe,running_exe=None,pin=None:'not checked in this test')
    return svc,target,pkg,doc


def plan(svc,action):
    p=svc.prepare_component(action)
    p.update(pid=None,timeout=10,settle=0.1,grace=0,recovery_command='',start=[sys.executable,'{target}/AKENO.py'])
    return p


def test_same_version_signed_release_available_only_on_explicit_component_check(setup):
    svc,*_=setup
    assert svc.check() is None
    assert svc.check(include_current=True).release==__version__


@pytest.mark.parametrize('action',['install','repair','remove'])
def test_install_repair_remove_atomic_swap_and_previous_selection(setup,action):
    svc,target,_,_=setup
    before=action!='install';write_selection(target,before)
    p=plan(svc,action)
    out=apply.run(p)
    assert out['ok'],out
    assert selection(target)==(action!='remove')
    assert all((target/'_internal'/f).exists()==(action!='remove') for f in SECURITY_FILES)
    assert selection(svc.folder/'previous')==before
    staging.verify(Path(p['source']),staging.load(p['content']))


def test_failed_health_check_restores_component_selection_and_files(setup,monkeypatch):
    svc,target,_,_=setup
    write_selection(target,False)
    original=(target/'AKENO.exe').read_bytes()
    p=plan(svc,'install')
    monkeypatch.setattr(apply,'health',lambda *a,**k:(False,'fixture rejected',None))
    out=apply.run(p)
    assert out['rolled_back'] and not out['ok']
    assert not selection(target)
    assert not (target/'_internal'/SECURITY_FILES[0]).exists()
    assert (target/'AKENO.exe').read_bytes()==original


def test_tampered_optional_file_rejected_even_when_removing(setup):
    svc,target,_,_=setup
    write_selection(target,True)
    p=plan(svc,'remove')
    (Path(p['source'])/'_internal'/SECURITY_FILES[0]).write_text('tampered')
    out=apply.run(p)
    assert not out['ok'] and 'nothing was changed' in out['error']
    assert selection(target)


def test_invalid_release_signature_refuses_component_operation(setup):
    svc,target,_,doc=setup
    doc['signature']['value']='A'*86
    with pytest.raises(Exception,match='signature|Signature'):
        svc.prepare_component('install')
    assert (target/'AKENO.exe').is_file()


def test_signed_package_cannot_override_local_component_selection(setup):
    svc,target,pkg,doc=setup
    with zipfile.ZipFile(pkg,'a') as z:z.writestr('AKENO/'+SELECTION,'[components]\nsecurity_center=1\n')
    # Test the extractor directly: even a deliberately supplied signed description may not carry local state.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=Ed25519PrivateKey.generate();public=u.tool.public_of(key)
    keys={u.tool.kid_of(public):crypto.public_key(public)}
    release=u.parse(u.envelope(key,u.fields(pkg=pkg,version=__version__)),keys)
    with pytest.raises(service.UpdateError,match='component selection'):
        svc.stage(pkg,release)
