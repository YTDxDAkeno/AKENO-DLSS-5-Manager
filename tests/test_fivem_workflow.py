"""Synthetic reviewed recipes exercise the transaction pipeline, NOT real compatibility."""
from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from akeno.backends.dlss5_feeder import SHADER
from akeno.backends.runtimes import NR_RUNTIME, DLSS_RUNTIME
from akeno.core.models import GpuInfo, SystemInfo, Vendor, TrustState, CompatStatus, AntiCheatRisk
from akeno.core.processes import ProcessInfo
from akeno.fivem import inspect_installation, ServerPolicy
from akeno.fivem.compatibility import BackendReview, REVIEWED_BACKENDS, policy_findings
from akeno.fivem.service import FiveMService
from akeno.install.transaction import Installer
from akeno.security.catalog import Catalog
from akeno.services import AppServices
from helpers import make_pe, sha, FakeDownloads


def files(root):
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def workflow(tmp_path, db, monkeypatch):
    root, gta = tmp_path / "FiveM", tmp_path / "GTAV"
    app = root / "FiveM.app"
    plugins = app / "plugins"
    plugins.mkdir(parents=True)
    make_pe(root / "FiveM.exe", ["kernel32.dll"])
    make_pe(gta / "GTA5.exe", ["d3d11.dll"])
    (app / "CitizenFX.ini").write_text(f"[Game]\nIVPath={gta}\nSavedBuildNumber=3258\n")
    make_pe(plugins / "d3d11.dll", [], dll=True, strings={"ProductName": "ReShade"})
    (plugins / "renodx-dlss5.addon64").write_bytes(b"SYNTHETIC consumer, never executed")
    (plugins / "user.txt").write_text("keep me")
    installation = inspect_installation(root)
    members = {"dlss5-feed.addon64": b"SYNTHETIC feeder, never executed", SHADER: b"// fixture shader"}
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    entry = {"id": "fivem-fixture", "backend": "dlss5_feeder", "name": "FIXTURE ONLY", "version": "0",
             "upstream": "https://github.com/example/fixture", "url": "https://github.com/example/fixture.zip",
             "sha256": sha(archive.read_bytes()), "license": "MIT", "trust": "verified",
             "files": {n: sha(data) for n, data in members.items()}}
    catalog = Catalog({"schema": 1, "allowed_hosts": ["github.com"], "packages": [entry]})
    downloads = FakeDownloads({entry["url"]: archive}, tmp_path / "download-cache")
    svc = AppServices(db=db, catalog=catalog, downloads=downloads,
                      installer=Installer(db, tmp_path / "backups", running_check=lambda d: []))
    gpu = GpuInfo("NVIDIA GeForce RTX 4080", vendor=Vendor.NVIDIA, generation="RTX 40",
                  has_tensor_cores=True, vram_bytes=16 * 1024**3, driver_version="617.14")
    svc.system = SystemInfo(os_name="Windows 11 Pro", is_64bit=True, gpus=[gpu])
    runtime_pins = []
    for index, name in enumerate((NR_RUNTIME, DLSS_RUNTIME)):
        path = make_pe(tmp_path / "runtimes" / name, [], dll=True, pad=index)
        pin = sha(path.read_bytes())
        db.add_runtime(sha256=pin, file_name=name, stored_path=str(path), version="fixture",
                       signer="NVIDIA Corporation (FIXTURE)", trust="verified")
        runtime_pins.append((name, pin))
    # Authenticode is NOT available on Linux. This mock ONLY tests gate propagation.
    monkeypatch.setattr("akeno.backends.runtimes.evaluate", lambda p: SimpleNamespace(trust=TrustState.VERIFIED))
    policy = ServerPolicy(server_id="owned-fixture-server", own_server=True,
                          permission_reference="fixture operator approval", graphics_plugins_allowed=True,
                          pure_level=0, allowed_backends=frozenset({"dlss5_feeder"}), experimental_consent=True)
    review = BackendReview("dlss5_feeder", entry["sha256"], installation.fingerprint,
        "https://example.org/FIXTURE-NOT-REAL-HARDWARE", "0" * 40, "RTX 40", ("617.14",), tuple(runtime_pins),
        frozenset({"dlss5-feed.addon64", SHADER, NR_RUNTIME, DLSS_RUNTIME, "akeno-install.json"}),
        neural_feature_evaluated=True, signed_runtime=True,
        prerequisite_hashes=tuple((f.path.relative_to(plugins).as_posix().casefold(), f.sha256) for f in installation.inventory))
    service = FiveMService(svc, process_reader=lambda: [])
    return SimpleNamespace(service=service, svc=svc, installation=installation, policy=policy, review=review,
                           root=root, gta=gta, plugins=plugins, archive=archive, entry=entry)


def adapter(w, **kwargs):
    return w.service.adapter(w.installation, "dlss5_feeder", kwargs.get("policy", w.policy),
                             kwargs.get("review", w.review))


def test_no_backend_approved_and_rtx4080_stays_blocked_by_default(workflow):
    w = workflow
    assert REVIEWED_BACKENDS == ()
    a = adapter(w, review=None)
    result = w.service.compatibility(a)
    assert result.status == CompatStatus.BLOCKED
    assert "fivem_no_review" in {f.code for f in result.blockers}
    with pytest.raises(PermissionError):
        w.service.prepare(a)


def test_full_transaction_and_restore_only_modify_fivem_plugins(workflow):
    w = workflow
    original, gta_original = files(w.root), files(w.gta)
    a = adapter(w)
    assert w.service.compatibility(a).status == CompatStatus.EXPERIMENTAL
    prepared = w.service.prepare(a)
    assert files(w.root) == original  # planning and staging never mutate the client
    assert prepared.plan.target_dir == str(w.plugins)
    applied = w.service.install(prepared, w.policy)
    assert applied.ok, applied.messages
    assert (w.plugins / "dlss5-feed.addon64").is_file()
    assert w.svc.installer.check_restore(applied.tx_id) == []
    restored = w.service.restore(w.installation, applied.tx_id)
    assert restored.ok, restored.messages
    assert files(w.root) == original and files(w.gta) == gta_original


@pytest.mark.parametrize("change,code", [
    ({"own_server": False}, "fivem_server_permission"),
    ({"graphics_plugins_allowed": None}, "fivem_graphics_permission"),
    ({"graphics_plugins_allowed": False}, "fivem_graphics_permission"),
    ({"pure_level": None}, "fivem_pure_unknown"),
    ({"pure_level": True}, "fivem_pure_unknown"),
    ({"pure_level": 2}, "fivem_pure_restricted"),
    ({"pure_level": 1}, "fivem_known_graphics"),
    ({"allowed_backends": frozenset()}, "fivem_backend_permission"),
    ({"experimental_consent": False}, "fivem_experiment_consent"),
])
def test_server_permission_gates(workflow, change, code):
    w = workflow
    a = adapter(w, policy=replace(w.policy, **change))
    assert code in {f.code for f in w.service.compatibility(a).blockers}
    with pytest.raises(PermissionError):
        w.service.prepare(a)


def test_pure_one_needs_exact_known_graphics_hashes(workflow):
    w = workflow
    policy = replace(w.policy, pure_level=1, known_graphics_hashes=frozenset(
        [*w.entry["files"].values(), *(h for _, h in w.review.runtime_hashes),
         *(h for _, h in w.review.prerequisite_hashes)]))
    assert not policy_findings(policy, w.review, adapter(w).package)


def test_scripthook_is_not_automatically_enabled(workflow):
    w = workflow
    r = replace(w.review, requires_script_hook=True)
    assert "fivem_script_hook" in {f.code for f in w.service.compatibility(adapter(w, review=r)).blockers}


@pytest.mark.parametrize("change,code", [
    ({"gpu_generation": "RTX 50"}, "fivem_generation_evidence"),
    ({"driver_versions": ("616.56",)}, "fivem_driver_evidence"),
    ({"signed_runtime": False}, "fivem_neural_evidence"),
    ({"neural_feature_evaluated": False}, "fivem_neural_evidence"),
    ({"upstream_commit": "main"}, "fivem_review_source"),
    ({"client_fingerprint": "old"}, "fivem_client_revision"),
    ({"package_sha256": "0" * 64}, "fivem_package_review"),
    ({"runtime_hashes": ()}, "fivem_runtime_pins"),
])
def test_evidence_matrix_is_not_a_gpu_marketing_claim(workflow, change, code):
    w = workflow
    result = w.service.compatibility(adapter(w, review=replace(w.review, **change)))
    assert result.status == CompatStatus.BLOCKED and code in {f.code for f in result.blockers}


def test_runtime_signature_and_hash_are_rechecked(workflow, monkeypatch):
    w = workflow
    monkeypatch.setattr("akeno.backends.runtimes.evaluate", lambda p: SimpleNamespace(trust=TrustState.BLOCKED))
    assert "fivem_runtime_integrity" in {f.code for f in w.service.compatibility(adapter(w)).blockers}


def test_runtime_tampering_after_planning_blocks_without_writes(workflow):
    w = workflow
    prepared = w.service.prepare(adapter(w))
    before = files(w.root)
    runtime = w.svc.runtime_lookup(NR_RUNTIME)
    Path(runtime["stored_path"]).write_bytes(b"tampered")
    with pytest.raises(PermissionError):
        w.service.install(prepared, w.policy)
    assert files(w.root) == before


@pytest.mark.parametrize("mutation", ["client", "plan", "source", "permission", "process", "unreadable_processes"])
def test_fresh_preapply_checks(workflow, mutation):
    w = workflow
    prepared = w.service.prepare(adapter(w))
    policy = w.policy
    if mutation == "client":
        (w.plugins / "new.asi").write_bytes(b"new")
    elif mutation == "plan":
        prepared.plan.ops[0].rel_path = "../escape.dll"
    elif mutation == "source":
        prepared.plan.ops[0].source.write_bytes(b"tampered")
    elif mutation == "permission":
        policy = replace(policy, graphics_plugins_allowed=False)
    elif mutation == "process":
        w.service.process_reader = lambda: [ProcessInfo(1, "FiveM_b3258_GTAProcess.exe", None)]
    else:
        def fail():
            raise OSError("denied")
        w.service.process_reader = fail
    before = files(w.root)
    with pytest.raises(PermissionError):
        w.service.install(prepared, policy)
    assert files(w.root) == before


def test_foreign_file_appearing_after_plan_is_preserved(workflow):
    w = workflow
    prepared = w.service.prepare(adapter(w))
    (w.plugins / "akeno-install.json").write_text("foreign")
    with pytest.raises(PermissionError):
        w.service.install(prepared, w.policy)
    assert (w.plugins / "akeno-install.json").read_text() == "foreign"


def test_transaction_failure_rolls_back(workflow, monkeypatch):
    w = workflow
    before = files(w.root)
    original_apply = w.svc.installer.apply
    monkeypatch.setattr(w.svc.installer, "apply", lambda g, p, cb: original_apply(g, p, cb, fail_after=1))
    result = w.service.install(w.service.prepare(adapter(w)), w.policy)
    assert not result.ok and result.state == "rolled_back"
    assert files(w.root) == before


def test_restore_conflict_and_running_process(workflow):
    w = workflow
    applied = w.service.install(w.service.prepare(adapter(w)), w.policy)
    assert applied.ok
    (w.plugins / "dlss5-feed.addon64").write_bytes(b"user changed")
    result = w.service.restore(w.installation, applied.tx_id)
    assert not result.ok and result.conflicts
    w.service.process_reader = lambda: [ProcessInfo(2, "FiveM.exe", None)]
    with pytest.raises(PermissionError):
        w.service.restore(w.installation, applied.tx_id, force=True)
    w.service.process_reader = lambda: []
    assert w.service.restore(w.installation, applied.tx_id, force=True).ok


def test_snapshot_restore_preserves_unrelated_files(workflow):
    w = workflow
    original = files(w.root)
    snapshot = w.service.snapshot(w.installation)
    assert snapshot.ok
    (w.plugins / "renodx-dlss5.addon64").write_bytes(b"edited")
    assert not w.service.restore_snapshot(w.installation, snapshot.tx_id).ok
    assert w.service.restore_snapshot(w.installation, snapshot.tx_id, force=True).ok
    assert files(w.root) == original


def test_wrong_game_cannot_use_adapter(workflow):
    w = workflow
    a = adapter(w)
    wrong = replace(w.installation.game, launcher="steam", install_dir=str(w.gta), exe_path=str(w.gta / "GTA5.exe"))
    assert "fivem_identity" in {f.code for f in a.check(wrong, w.service.context(a))}
    with pytest.raises(PermissionError):
        a.plan(wrong, a.package, {}, w.service.context(a))


def test_unreviewed_layout_cannot_fallback_to_proxy(workflow):
    w = workflow
    a = adapter(w, review=replace(w.review, installed_paths=frozenset({"winmm.dll"})))
    with pytest.raises(PermissionError, match="layout"):
        w.service.prepare(a)


def test_unverified_catalog_or_tampered_archive_is_rejected(workflow):
    w = workflow
    a = adapter(w)
    a.package.trust = TrustState.NEEDS_REVIEW
    with pytest.raises(PermissionError):
        w.service.prepare(a)
    a.package.trust = TrustState.VERIFIED
    w.archive.write_bytes(b"tampered archive")
    with pytest.raises(Exception, match="SHA-256 mismatch"):
        w.service.prepare(a)


def test_anticheat_detection_is_never_overridden_by_ownership(workflow):
    w = workflow
    w.installation.game.anti_cheat = AntiCheatRisk.DETECTED
    assert w.service.compatibility(adapter(w)).status == CompatStatus.BLOCKED


def test_unreviewed_existing_plugin_is_blocked(workflow):
    w = workflow
    (w.plugins / "unknown.asi").write_bytes(b"unknown")
    w.installation = inspect_installation(w.root)
    r = replace(w.review, client_fingerprint=w.installation.fingerprint)
    assert "fivem_unreviewed_plugin" in {f.code for f in w.service.compatibility(adapter(w, review=r)).blockers}


def test_existing_runtime_is_not_trusted_by_filename(workflow):
    w = workflow
    (w.plugins / NR_RUNTIME).write_bytes(b"untrusted runtime")
    w.installation = inspect_installation(w.root)
    r = replace(w.review, client_fingerprint=w.installation.fingerprint)
    assert "fivem_existing_runtime" in {f.code for f in w.service.compatibility(adapter(w, review=r)).blockers}


def test_changed_review_binding_after_prepare_is_rejected(workflow):
    w = workflow
    prepared = w.service.prepare(adapter(w))
    prepared.adapter.review = replace(w.review, source="https://example.org/changed")
    with pytest.raises(PermissionError, match="evidence changed"):
        w.service.install(prepared, w.policy)


def test_temporary_symlink_cannot_redirect_installer(workflow, tmp_path):
    w = workflow
    prepared = w.service.prepare(adapter(w))
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"preserve")
    (w.plugins / "dlss5-feed.addon64.akeno-tmp").symlink_to(outside)
    with pytest.raises(PermissionError, match="temporary"):
        w.service.install(prepared, w.policy)
    assert outside.read_bytes() == b"preserve"


def test_temporary_symlink_cannot_redirect_restore(workflow, tmp_path):
    w = workflow
    applied = w.service.install(w.service.prepare(adapter(w)), w.policy)
    assert applied.ok
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"preserve")
    (w.plugins / "dlss5-feed.addon64.akeno-tmp").symlink_to(outside)
    with pytest.raises(PermissionError, match="temporary"):
        w.service.restore(w.installation, applied.tx_id)
    assert outside.read_bytes() == b"preserve"


def test_validation_failure_restores_plugin_files(workflow, monkeypatch):
    from akeno.core.models import Finding, Severity
    from akeno.fivem.adapter import FiveMLegacyAdapter
    w = workflow
    before = files(w.root)
    prepared = w.service.prepare(adapter(w))
    monkeypatch.setattr(FiveMLegacyAdapter, "validate", lambda *args: [Finding(Severity.BLOCKER, "fixture", "failed")])
    applied = w.service.install(prepared, w.policy)
    assert not applied.ok and applied.state == "rolled_back"
    assert files(w.root) == before


@pytest.mark.parametrize("change,code", [
    ({"vendor": Vendor.AMD}, "fivem_gpu"),
    ({"has_tensor_cores": None}, "fivem_gpu"),
    ({"vram_bytes": None}, "fivem_vram_unknown"),
    ({"driver_version": None}, "fivem_driver_unknown"),
])
def test_hardware_unknowns_are_not_filled_with_optimistic_defaults(workflow, change, code):
    w = workflow
    for key, value in change.items():
        setattr(w.svc.system.gpus[0], key, value)
    assert code in {f.code for f in w.service.compatibility(adapter(w)).blockers}


def test_linux_deployment_is_blocked(workflow):
    w = workflow
    w.svc.system.os_name = "Linux"
    assert "fivem_platform" in {f.code for f in w.service.compatibility(adapter(w)).blockers}
