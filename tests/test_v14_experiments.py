"""Inert PE fixtures, fake signatures and simulated hardware; never GPU execution evidence."""
from dataclasses import replace
import json
from pathlib import Path
import pytest
from akeno.community.profiles import local_profile, eligibility, validate_profile
from akeno.community.runtime import RuntimeManager, Category
from akeno.community.bundles import OfflineBundle
from akeno.security.downloads import sha256_file
from akeno.core.models import GraphicsApi
from akeno.games.analyzer import analyze
from akeno.games.launchers import RawGame
from helpers import make_pe, rtx
from test_community_runtime import nr, unsigned, experiment


def profile(manager, nr):
    e = manager.inspect(str(nr))
    return local_profile(e, 'https://example.org/test-fixture', True)


@pytest.mark.parametrize('generation,allowed', [('RTX 50', True), ('RTX 40', True), ('RTX 30', False), ('RTX 20', False)])
def test_architecture_first_test_policy(db, nr, generation, allowed):
    m = RuntimeManager(db, unsigned)
    p = profile(m, nr)
    gpu = rtx(); gpu.generation = generation; gpu.driver_version = '617.42'
    if allowed:
        assert 'not yet verified' in eligibility(p, gpu, 'optiscaler_nr', 'd3d12')
    else:
        with pytest.raises(PermissionError, match='independently validated'):
            eligibility(p, gpu, 'optiscaler_nr', 'd3d12')


def test_unknown_advanced_runtime_does_not_require_fabricated_audit(db, nr):
    m = RuntimeManager(db, unsigned)
    p = profile(m, nr)
    imported = m.import_local(str(nr), p, True)
    assert imported.category == Category.ADVANCED
    assert not imported.to_dict()['execution_verified']
    assert not db.query('SELECT * FROM runtimes')
    assert 'security_review' not in imported.review


@pytest.mark.parametrize('change', ['source', 'hash', 'permission', 'version', 'deny'])
def test_advanced_integrity_and_provenance_still_required(db, nr, change):
    m = RuntimeManager(db, unsigned); p = profile(m, nr)
    if change == 'source': p['runtime_source'] = 'file:///tmp/test'
    if change == 'hash': p['sha256'] = '0' * 64
    if change == 'permission': p['local_use_permitted'] = False
    if change == 'version': p['version'] = '0.0.0.0'
    if change == 'deny': m.denied_hashes = {sha256_file(nr)}
    assert m.inspect(str(nr), p).category == Category.REJECTED


def test_named_hashmismatch_can_test_5090_without_claiming_support(db, nr, monkeypatch):
    from akeno.community import candidates
    named = replace(candidates.BANDUKIDS, sha256=sha256_file(nr))
    monkeypatch.setattr(candidates, 'BANDUKIDS', named)
    m = RuntimeManager(db, unsigned)
    p = local_profile(m.inspect(str(nr)), '', True)
    gpu = rtx('NVIDIA GeForce RTX 5090', device=0x2b85, vram_gb=32); gpu.driver_version = '617.42'
    assert 'not yet verified' in eligibility(p, gpu, 'optiscaler_nr', 'd3d12')
    assert m.import_local(str(nr), p, True).category == Category.COMMUNITY


def test_known_incompatibility_and_minimum_driver_are_blockers(db, nr):
    p = profile(RuntimeManager(db, unsigned), nr)
    gpu = rtx('NVIDIA GeForce RTX 4080', device=0x2704, vram_gb=16); gpu.driver_version = '617.42'
    p['incompatible_generations'] = [gpu.generation]
    with pytest.raises(PermissionError, match='incompatible'): eligibility(p, gpu, 'optiscaler_nr', 'd3d12')
    p['incompatible_generations'] = []; p['minimum_driver'] = '620.00'
    with pytest.raises(PermissionError, match='620.00'): eligibility(p, gpu, 'optiscaler_nr', 'd3d12')


def bundle(tmp_path, nr, manager):
    root = tmp_path / 'bundle'; root.mkdir()
    target = root / 'candidate.dll'; target.write_bytes(nr.read_bytes())
    item = {'id': 'fixture-nr', 'kind': 'neural_runtime', 'path': target.name,
            'sha256': sha256_file(target), 'profile': profile(manager, target)}
    (root / 'manifest.json').write_text(json.dumps({'schema': 1, 'components': [item]}))
    return root, item


def test_bundled_candidate_inventory_and_tamper(db, tmp_path, nr):
    m = RuntimeManager(db, unsigned); root, item = bundle(tmp_path, nr, m)
    inventory = OfflineBundle(root)
    assert inventory.candidates(m)[0]['sha256'] == item['sha256']
    (root / item['path']).write_bytes(b'corrupt')
    assert not inventory.candidates(m) and inventory.errors


@pytest.mark.parametrize('path', ['../outside.dll', 'C:/Windows/test.dll', '/tmp/test.dll', 'dir\\test.dll'])
def test_bundle_rejects_escaping_paths(db, tmp_path, nr, path):
    m = RuntimeManager(db, unsigned); root, item = bundle(tmp_path, nr, m)
    item['path'] = path
    with pytest.raises(ValueError): OfflineBundle(root).path(item)


def test_advanced_5090_install_restore_fully_offline(experiment, monkeypatch, tmp_path):
    s, g, imported, root = experiment
    m = s.community.runtimes
    p = profile(m, Path(imported.path))
    imported = m.import_local(imported.path, p, True)
    s.system.gpus[0] = rtx('NVIDIA GeForce RTX 5090', device=0x2b85, vram_gb=32)
    s.system.gpus[0].driver_version = '617.42'
    package = s.package_for(s.registry.get('optiscaler_nr'))
    archive = s.downloads.files[package.url]
    bundled = tmp_path / 'offline'; bundled.mkdir()
    (bundled / 'backend.zip').write_bytes(archive.read_bytes())
    (bundled / 'manifest.json').write_text(json.dumps({'schema': 1, 'components': [
        {'id': package.id, 'kind': 'backend_archive', 'path': 'backend.zip', 'sha256': package.sha256}]}))
    s.offline_bundle = OfflineBundle(bundled)
    monkeypatch.setattr(s.downloads, 'fetch', lambda *a, **k: pytest.fail('network access during offline experiment'))
    prepared = s.community.prepare(g.id, imported.sha256, 'optiscaler_nr', {}, True, True)
    before = {f.name: f.read_bytes() for f in root.iterdir()}
    result = s.community.install(prepared, True, True)
    assert result.ok, result.messages
    assert s.restore(result.tx_id).ok
    assert before == {f.name: f.read_bytes() for f in root.iterdir()}


def test_large_game_executable_not_limited_like_a_runtime(experiment, monkeypatch):
    """A ~771-MiB game EXE must not trip the 512-MiB DLL runtime cap.

    Sparse synthetic file + test-only fingerprint avoid hashing 512+ MiB.
    """
    from akeno.backends import runtimes
    from akeno.community.runtime import regular, regular_game_file
    from akeno.community import service as community_service

    s, game, imported, root = experiment
    exe = root / 'Game.exe'
    with exe.open('r+b') as stream:
        stream.truncate(runtimes.MAX_FILE + 1)
    with pytest.raises(ValueError, match='bounded regular runtime'):
        regular(exe)
    regular_game_file(exe)

    original_hash = community_service.sha256_file
    monkeypatch.setattr(community_service, 'sha256_file',
        lambda path: 'synthetic-large-exe-fingerprint' if Path(path) == exe else original_hash(path))
    plan = s.community.prepare(game.id, imported.sha256, 'optiscaler_nr', {}, True, True)
    assert plan.plan.ops and plan.environment_pin


def test_borderlands_exact_dx12_profile_and_plugin_inventory(tmp_path):
    root = tmp_path / 'Borderlands4'
    exe = make_pe(root/'OakGame/Binaries/Win64/Borderlands4.exe', ['d3d12.dll', 'd3d11.dll', 'opengl32.dll'])
    make_pe(root/'Engine/Plugins/Runtime/Nvidia/Streamline/Binaries/Win64/sl.interposer.dll', [], dll=True)
    raw = RawGame(id='steam:1285190', name='Borderlands 4', install_dir=str(root), launcher='steam', launcher_id='1285190')
    game = analyze(raw, running=[])
    assert game.api == GraphicsApi.D3D12 and 'game profile' in game.api_source
    assert any(m.kind == 'streamline' for m in game.existing_mods)
    assert analyze(raw, api_override='d3d11', running=[]).api == GraphicsApi.D3D11
    raw.launcher_id = '999'
    assert analyze(raw, running=[]).api == GraphicsApi.UNKNOWN


def test_private_bundle_assembler_uses_actual_fixture_bytes_only(db, nr, tmp_path, monkeypatch):
    from tools.build_private_bundle import build
    from akeno.security.catalog import Catalog
    from helpers import make_opti_package
    archive, entry = make_opti_package(tmp_path / 'package')
    catalog = Catalog({'schema': 1, 'packages': [entry]})
    monkeypatch.setattr(Catalog, 'load_bundled', lambda: catalog)
    output = tmp_path / 'personal' / 'runtimes'
    manifest = build(nr, archive, output, 'https://example.org/inert-fixture', True,
                     RuntimeManager(db, unsigned), include_presentmon=False)
    assert manifest['distribution'] == 'private-local-use'
    bundle = OfflineBundle(output)
    assert bundle.candidates(RuntimeManager(db, unsigned))[0]['sha256'] == sha256_file(nr)
    assert bundle.backend_archive(catalog.best_package('optiscaler_nr')).read_bytes() == archive.read_bytes()
    with pytest.raises(ValueError, match='new empty'): build(nr, archive, output, permitted=True)


def test_public_bundle_has_no_proprietary_files():
    root = Path(__file__).resolve().parents[1] / 'runtimes'
    assert OfflineBundle(root).entries == []
    assert not list(root.rglob('*.dll'))


@pytest.mark.parametrize('language', ['en', 'de'])
def test_v14_dialog_and_benchmark_conditions_remain_accessible(qtbot, experiment, language, monkeypatch):
    import os
    from akeno import i18n
    from akeno.ui.community_runtime import CommunityRuntimeDialog
    from akeno.ui.controller import Controller
    from akeno.ui.pages.benchmark import BenchmarkPage
    from akeno.ui import theme
    from PySide6.QtWidgets import QApplication
    s, game, imported, root = experiment
    i18n.set_language(language)
    QApplication.instance().setStyleSheet(theme.stylesheet())
    controller = Controller(s)
    dialog = CommunityRuntimeDialog(controller)
    qtbot.addWidget(dialog)
    dialog.resize(1100, 760); dialog.show()
    try:
        qtbot.waitUntil(lambda: dialog.evaluation is not None, timeout=5000)
        assert not dialog.install_button.isEnabled()
        dialog.permission.setChecked(True); dialog.offline.setChecked(True); dialog.consent.setChecked(True)
        assert dialog.install_button.isEnabled()
        dialog.tabs.setCurrentIndex(1)
        qtbot.wait(50)
        assert dialog.install_button.isVisible()
        assert dialog.performance.isVisible()
        capture = os.environ.get('AKENO_V14_SCREENSHOTS')
        if capture:
            target = Path(capture); target.mkdir(parents=True, exist_ok=True)
            dialog.grab().save(str(target / f'experiment-{language}-fixture.png'))
        page = BenchmarkPage(controller, None); qtbot.addWidget(page)
        page.resize(1000, 760); page.show(); qtbot.wait(50)
        assert page.width() == 1000  # German export actions must fit the content area.
        assert set(page.conditions) == {'game_version', 'workload', 'resolution', 'settings'}
        assert page._meta(game)['resolution'] is None
        if capture: page.grab().save(str(target / f'benchmark-{language}-fixture.png'))
    finally:
        dialog.close(); i18n.set_language('en')


def test_private_bundle_missing_backend_never_falls_back_to_network(experiment, monkeypatch, tmp_path):
    s, game, imported, root = experiment
    folder = tmp_path / 'incomplete'; folder.mkdir()
    (folder / 'manifest.json').write_text(json.dumps({'schema': 1, 'distribution': 'private-local-use', 'components': []}))
    s.offline_bundle = OfflineBundle(folder)
    monkeypatch.setattr(s.downloads, 'fetch', lambda *a, **k: pytest.fail('unexpected network fallback'))
    with pytest.raises(PermissionError, match='missing the selected pinned backend'):
        s.community.prepare(game.id, imported.sha256, 'optiscaler_nr', {}, True, True)


def test_normal_install_button_converts_legacy_review_without_expert_json(qtbot, experiment, monkeypatch):
    from akeno.ui import community_runtime
    from akeno.ui.controller import Controller
    s, game, imported, root = experiment
    controller = Controller(s)
    dialog = community_runtime.CommunityRuntimeDialog(controller); qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.evaluation is not None)
    dialog.permission.setChecked(True); dialog.offline.setChecked(True); dialog.consent.setChecked(True)
    monkeypatch.setattr(community_runtime, 'confirm', lambda *a, **k: True)
    before = {f.name: f.read_bytes() for f in root.iterdir()}
    dialog.install()  # One normal action prepares, previews, confirms, imports and applies the transaction.
    qtbot.waitUntil(lambda: s.installer.active_install(game.id) is not None, timeout=10000)
    qtbot.waitUntil(lambda: not controller.busy, timeout=10000)
    tx = s.installer.active_install(game.id)
    assert s.db.get_setting('community-runtime:' + imported.sha256)['review']['schema'] == 3
    assert s.restore(tx['id']).ok
    assert before == {f.name: f.read_bytes() for f in root.iterdir()}
    dialog.close()


def test_fresh_observation_does_not_inherit_previous_process_exit(experiment):
    s, game, imported, root = experiment
    prepared = s.community.prepare(game.id, imported.sha256, 'optiscaler_nr', {}, True, True)
    assert s.community.install(prepared, True, True).ok
    s.community.seen_live.add(game.id)
    report = s.community.observe(game.id)
    assert report['stability']['state'] == 'not_observed'
    assert not report['stability']['crash_verified']
