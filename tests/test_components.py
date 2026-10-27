"""Component selection, integrity, reset survival and update projection. No Windows installer evidence."""
import json
from pathlib import Path

import pytest

from akeno.components.service import (ComponentError, ComponentService, INVENTORY, SECURITY_FILES, SELECTION,
                                      inventory, selected_content)
from akeno.updater import staging
from tools.build_components import build


def bundle(root):
    root.mkdir(parents=True)
    (root / 'AKENO.exe').write_bytes(b'core fixture')
    for rel in SECURITY_FILES:
        p = root / '_internal' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('# optional code fixture\n')
    build(root)
    return root


def manager(root, tmp_path):
    return ComponentService(root=root, state=tmp_path / 'state.sqlite3', frozen=True)


def test_security_activation_survives_restarts_and_settings_reset(tmp_path, db):
    svc = manager(bundle(tmp_path / 'bundle'), tmp_path)
    assert svc.status()['state'] == 'active'
    svc.set_enabled(False)
    from akeno.core.settings import Settings
    Settings(db).factory_reset()
    assert manager(svc.root, tmp_path).status()['state'] == 'disabled'
    svc.set_enabled(True)
    assert svc.active()


def test_core_only_is_not_an_error_or_an_installed_component(tmp_path):
    root = bundle(tmp_path / 'bundle')
    (root / SELECTION).write_text('[components]\nsecurity_center=0\n')
    for rel in SECURITY_FILES:
        (root / '_internal' / rel).unlink()
    svc = manager(root, tmp_path)
    assert svc.status()['state'] == 'not_installed'
    with pytest.raises(ComponentError):
        svc.set_enabled(True)


@pytest.mark.parametrize('damage', ['missing', 'changed', 'partial', 'inventory', 'version', 'selection'])
def test_blocked_or_damaged_component_never_activates(tmp_path, damage):
    root = bundle(tmp_path / 'bundle')
    p = root / '_internal' / SECURITY_FILES[1]
    if damage == 'missing':
        p.unlink()
    elif damage == 'changed':
        p.write_text('# changed\n')
    elif damage == 'partial':
        (root / SELECTION).write_text('[components]\nsecurity_center=0\n')
        p.unlink()
    elif damage == 'selection':
        (root / SELECTION).write_text('invalid')
    else:
        data = json.loads((root / INVENTORY).read_text())
        if damage == 'inventory':
            data['security_center']['files']['../escape'] = {'sha256': '0'*64, 'size': 1}
        else:
            data['version'] = '0.0.0'
        (root / INVENTORY).write_text(json.dumps(data))
    svc = manager(root, tmp_path)
    assert svc.status()['state'] in ('error', 'incompatible')
    assert not svc.active()


def test_symlink_component_is_rejected(tmp_path):
    root = bundle(tmp_path / 'bundle')
    p = root / '_internal' / SECURITY_FILES[1]
    other = tmp_path / 'outside.py'
    other.write_bytes(p.read_bytes())
    p.unlink()
    p.symlink_to(other)
    assert manager(root, tmp_path).status()['state'] == 'error'


def test_core_update_projection_keeps_full_signed_list_unchanged(tmp_path):
    source = bundle(tmp_path / 'new')
    target = tmp_path / 'installed'
    target.mkdir()
    (target / SELECTION).write_text('[components]\nsecurity_center=0\n')
    content = {'files': {rel: {'size': p.stat().st_size, 'sha256': staging.sha256_of(p)}
                         for rel,p in staging.walk(source).items()}}
    staging.verify(source, content)
    original = json.dumps(content, sort_keys=True)
    projected = selected_content(source, target, content)
    assert set(projected['files']) == {'AKENO.exe', INVENTORY}
    assert json.dumps(content, sort_keys=True) == original
    from akeno.updater.apply import copy_listed
    copy_listed(source, tmp_path / 'copy', projected)
    staging.verify(tmp_path / 'copy', projected)
    assert not (tmp_path / 'copy' / '_internal' / SECURITY_FILES[0]).exists()
    # Other module selection cannot turn optional source code into Core code.
    data = json.loads((source / INVENTORY).read_text())
    data['security_center']['files']['AKENO.exe'] = data['security_center']['files'].pop('_internal/'+SECURITY_FILES[0])
    (source / INVENTORY).write_text(json.dumps(data))
    with pytest.raises(ComponentError):
        inventory(source)


def test_core_only_refuses_monolithic_update_or_manifest_hash_mismatch(tmp_path):
    root = bundle(tmp_path / 'new')
    target = tmp_path / 'installed'
    target.mkdir()
    (target / SELECTION).write_text('[components]\nsecurity_center=0\n')
    with pytest.raises(ComponentError, match='differs'):
        selected_content(root, target, {'files': {}})
    (root / INVENTORY).unlink()
    with pytest.raises(ComponentError):
        selected_content(root, target, {'files': {}})


def test_absent_security_does_not_load_ui_or_fail_health(qtbot, monkeypatch):
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    from akeno.ui.components import ComponentUnavailablePage
    from akeno.ui.system_ui import STORED_FAILURES
    svc = AppServices()
    svc.components.set_enabled(False)
    before = list(STORED_FAILURES)
    import builtins
    original = builtins.__import__
    def no_security(name, *args, **kwargs):
        if name.startswith('akeno.system.securitycenter') or name == 'akeno.ui.pages.security':
            raise AssertionError('Core-only startup imported Security Center')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_security)
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    assert isinstance(win.pages['security'], ComponentUnavailablePage)
    win.go('security')
    assert STORED_FAILURES == before
    assert 'updates' in win.pages and 'license' in win.pages
    # Use the original method: conftest replaces the AV hook for other tests.
    with pytest.raises(ComponentError):
        svc.system_tools.security
    from akeno.ui import workers
    assert workers.drain(30000)
    svc.db.close()


def test_settings_component_toggle_rebuilds_page_and_waits_for_running_task(qtbot, monkeypatch):
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    from akeno.ui.components import ComponentUnavailablePage
    from akeno.ui.system_ui import STORED_FAILURES
    from akeno.ui import workers
    svc = AppServices()
    ctl = Controller(svc)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    assert workers.drain(30000)
    before = list(STORED_FAILURES)
    notices = []
    monkeypatch.setattr('akeno.ui.components.info', lambda *args, **kw: notices.append(args))
    card = win.pages['settings'].components
    ctl.busy.add('security:scan')
    card.change()
    assert svc.components.active() and notices
    ctl.busy.remove('security:scan')
    card.change()
    assert not svc.components.active()
    assert isinstance(win.pages['security'], ComponentUnavailablePage)
    card.change()
    assert svc.components.active()
    assert not isinstance(win.pages['security'], ComponentUnavailablePage)
    assert STORED_FAILURES == before
    assert workers.drain(30000)
    svc.db.close()
