"""End-to-end installation workflow in controlled temporary game folders (never real games)."""

import hashlib
import os
from pathlib import Path

import pytest

from akeno.backends.runtimes import NR_RUNTIME
from akeno.compat.engine import CompatDatabase
from akeno.core.models import CompatStatus, GraphicsApi, TrustState
from akeno.install.transaction import Installer
from akeno.security.catalog import Catalog
from akeno.services import AppServices
from helpers import FakeDownloads, make_opti_package, make_pe, rtx, system_with


def snapshot(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture
def env(tmp_path, db, monkeypatch):
    zpath, entry = make_opti_package(tmp_path)
    catalog = Catalog({"schema": 1, "allowed_hosts": ["github.com"], "packages": [entry], "tools": []})
    downloads = FakeDownloads({entry["url"]: zpath}, tmp_path / "dlcache")
    compat = CompatDatabase(CompatDatabase.load().data, "test")
    installer = Installer(db, tmp_path / "backups", running_check=lambda d: [])
    svc = AppServices(db=db, catalog=catalog, downloads=downloads, installer=installer, compat_db=compat)
    svc.system = system_with(rtx())
    # Game folder: DX12 game with its own DLSS and an unrelated existing ReShade dxgi.dll.
    game_dir = tmp_path / "games" / "Night City"
    make_pe(game_dir / "bin" / "x64" / "NightCity.exe", ["d3d12.dll", "kernel32.dll", "winmm.dll"], pad=50000)
    make_pe(game_dir / "bin" / "x64" / "nvngx_dlss.dll", [], dll=True, version=(310, 2, 1, 0))
    make_pe(game_dir / "bin" / "x64" / "dxgi.dll", [], dll=True, strings={"ProductName": "ReShade"})
    (game_dir / "bin" / "x64" / "settings.cfg").write_text("user=1")
    game = svc.library.add_manual(str(game_dir), "Night City")
    # NVIDIA runtime the user imported (signature checks need Windows; recorded as verified for the test).
    rt = tmp_path / "rt" / NR_RUNTIME
    make_pe(rt, [], dll=True, version=(310, 8, 0, 0))
    db.add_runtime(sha256=hashlib.sha256(rt.read_bytes()).hexdigest(), file_name=NR_RUNTIME, stored_path=str(rt),
                   version="310.8.0.0", signer="NVIDIA Corporation", trust=TrustState.VERIFIED.value)
    return svc, game, game_dir, entry


def test_full_install_configure_restore_cycle(env):
    svc, game, game_dir, entry = env
    exe_dir = game_dir / "bin" / "x64"
    before = snapshot(game_dir)
    adapter = svc.backend_for(game)
    assert adapter.id == "optiscaler_nr"
    compat = svc.compatibility(game)
    assert compat.status == CompatStatus.INSUFFICIENT and not compat.blockers
    rd = svc.readiness(game)
    assert rd.can_install, rd.reasons
    pkg = svc.package_for(adapter)
    staging = svc.stage(game, adapter, pkg)
    params = dict(adapter.default_params(game), _profile="quality")
    plan = svc.plan_install(game, adapter, pkg, params, staging)
    names = [o.rel_path for o in plan.ops]
    assert names[0] == "winmm.dll", "dxgi.dll is taken by ReShade, so OptiScaler must load as winmm.dll"
    res = svc.install(game, plan)
    assert res.ok, res.messages
    assert (exe_dir / "winmm.dll").is_file() and (exe_dir / NR_RUNTIME).is_file()
    ini_text = (exe_dir / "OptiScaler.ini").read_text()
    assert "Dx12Upscaler=dlss" in ini_text and "PreSr=true" in ini_text and "; comment kept" in ini_text
    assert (exe_dir / "dxgi.dll").read_bytes() == (game_dir / "bin" / "x64" / "dxgi.dll").read_bytes()
    assert svc.db.active_profile(game.id, adapter.id)["name"] == "quality"
    # reconfigure to Performance
    game = svc.library.get(game.id)
    perf = next(p for p in adapter.profiles(game) if p.id == "performance")
    r2 = svc.configure(game, adapter, perf.params, "performance")
    assert r2.ok, r2.messages
    assert "CacheInterval=2" in (exe_dir / "OptiScaler.ini").read_text()
    # restore -> folder identical to before (no leftovers, ReShade untouched)
    tx = svc.installer.active_install(game.id, adapter.id)
    assert svc.installer.check_restore(tx["id"]) == []
    r3 = svc.restore(tx["id"])
    assert r3.ok, r3.messages
    assert snapshot(game_dir) == before


def test_conflicting_foreign_file_blocks_install(env):
    svc, game, game_dir, entry = env
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    staging = svc.stage(game, adapter, pkg)
    plan = svc.plan_install(game, adapter, pkg, adapter.default_params(game), staging)
    (game_dir / "bin" / "x64" / "OptiScaler.ini").write_text("someone else's")
    res = svc.installer.apply(game, plan)
    assert not res.ok and "never overwrites" in res.messages[0]
    assert (game_dir / "bin" / "x64" / "OptiScaler.ini").read_text() == "someone else's"


def test_interrupted_install_rolls_back(env):
    svc, game, game_dir, entry = env
    before = snapshot(game_dir)
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    plan = svc.plan_install(game, adapter, pkg, adapter.default_params(game), svc.stage(game, adapter, pkg))
    res = svc.installer.apply(game, plan, fail_after=2)
    assert not res.ok and res.state == "rolled_back"
    assert snapshot(game_dir) == before
    assert svc.db.get_transaction(res.tx_id)["state"] == "rolled_back"


def test_user_modified_file_needs_confirmation_and_running_game_blocks(env, db, tmp_path):
    svc, game, game_dir, entry = env
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    plan = svc.plan_install(game, adapter, pkg, adapter.default_params(game), svc.stage(game, adapter, pkg))
    tx = svc.install(game, plan).tx_id
    (game_dir / "bin" / "x64" / "OptiScaler.ini").write_text("edited by user")
    conflicts = svc.installer.check_restore(tx)
    assert [c.rel_path for c in conflicts] == ["OptiScaler.ini"]
    r = svc.installer.restore(tx)
    assert not r.ok and r.conflicts
    busy = Installer(db, tmp_path / "backups", running_check=lambda d: [type("P", (), {"name": "NightCity.exe"})()])
    assert "running" in busy.restore(tx, force=True).messages[0]
    assert "running" in busy.apply(game, plan).messages[0]
    assert svc.installer.restore(tx, force=True).ok


def test_reinstall_keeps_original_backup_chain(env):
    svc, game, game_dir, entry = env
    before = snapshot(game_dir)
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    for _ in range(2):
        g = svc.library.get(game.id)
        plan = svc.plan_install(g, adapter, pkg, adapter.default_params(g), svc.stage(g, adapter, pkg))
        assert svc.install(g, plan).ok
    txs = svc.db.transactions(game.id)
    assert [t["state"] for t in txs] == ["installed", "superseded"]
    assert svc.restore(txs[0]["id"]).ok
    assert snapshot(game_dir) == before


def test_second_neural_consumer_blocks_until_user_allows_disable(env):
    svc, game, game_dir, entry = env
    exe_dir = game_dir / "bin" / "x64"
    (exe_dir / "renodx-dlss5.addon64").write_bytes(b"addon")
    game = svc.library.reanalyze(game.id)
    adapter = svc.backend_for(game)
    assert any(f.code == "second_consumer" for f in svc.compatibility(game).blockers)
    ctx = svc.context(game, allow_disable=["renodx-dlss5.addon64"])
    assert not [f for f in adapter.check(game, ctx) if f.severity.value == "blocker"]
    pkg = svc.package_for(adapter)
    plan = svc.plan_install(game, adapter, pkg, adapter.default_params(game), svc.stage(game, adapter, pkg),
                            ["renodx-dlss5.addon64"])
    assert svc.install(game, plan).ok
    assert not (exe_dir / "renodx-dlss5.addon64").exists() and (exe_dir / "renodx-dlss5.addon64.akeno-disabled").exists()
    tx = svc.installer.active_install(game.id)
    assert svc.restore(tx["id"]).ok
    assert (exe_dir / "renodx-dlss5.addon64").read_bytes() == b"addon"


def test_missing_runtime_and_unverified_package_block(env, db):
    svc, game, game_dir, entry = env
    for r in db.runtimes():
        db.delete_runtime(r["sha256"])
    assert any(f.code == "runtime_missing" for f in svc.compatibility(game).blockers)
    assert not svc.readiness(game).can_install
    unverified = Catalog({"schema": 1, "packages": [dict(entry, trust="needs_review", url=None, sha256=None)]})
    with pytest.raises(PermissionError):
        svc.stage(game, svc.backend_for(game), unverified.packages[0])


def test_tampered_package_is_rejected(env, tmp_path):
    svc, game, game_dir, entry = env
    import zipfile
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("DLSS5-Optimised/OptiScaler.dll", b"malware")
    svc.downloads.files[entry["url"]] = bad
    from akeno.security.downloads import VerificationError
    with pytest.raises(VerificationError):
        svc.stage(game, svc.backend_for(game), svc.package_for(svc.backend_for(game)))


def test_adapter_failure_and_validation_failure_roll_back(env, monkeypatch):
    svc, game, game_dir, entry = env
    before = snapshot(game_dir)
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    plan = svc.plan_install(game, adapter, pkg, adapter.default_params(game), svc.stage(game, adapter, pkg))
    from akeno.core.models import Finding, Severity
    monkeypatch.setattr(type(adapter), "validate", lambda self, g, inst: [Finding(Severity.BLOCKER, "x", "broken")])
    res = svc.install(game, plan)
    assert not res.ok and "Validation failed" in res.messages[0]
    assert snapshot(game_dir) == before
    with pytest.raises(ValueError):
        adapter.plan(game, pkg, {}, svc.context(game))   # no staged package


def test_anti_cheat_blocks(env):
    svc, game, game_dir, entry = env
    (game_dir / "EasyAntiCheat").mkdir()
    game = svc.library.reanalyze(game.id)
    c = svc.compatibility(game)
    assert c.status == CompatStatus.BLOCKED and not svc.readiness(game).can_install


def test_feeder_requirements(env):
    svc, game, game_dir, entry = env
    feeder = svc.registry.get("dlss5_feeder")
    codes = {f.code for f in feeder.check(game, svc.context(game))}
    assert "consumer_missing" in codes and "missing_nvngx_dlssnr.dll" not in codes
    game.api = GraphicsApi.VULKAN
    assert "vulkan_guided" in {f.code for f in feeder.check(game, svc.context(game))}
