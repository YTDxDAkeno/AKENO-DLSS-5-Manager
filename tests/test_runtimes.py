"""NVIDIA runtime discovery and import: provenance, identity, signature and ordering rules."""

from pathlib import Path

import pytest

from akeno.backends import runtimes
from akeno.backends.runtimes import DLSS_RUNTIME, NR_RUNTIME, SearchRoot
from akeno.core.models import TrustState
from akeno.security.authenticode import SignatureInfo
from helpers import make_pe


def nv_dll(path: Path, original: str = NR_RUNTIME, version=(310, 8, 0, 0), machine="x64", marker=b"") -> Path:
    make_pe(path, ["kernel32.dll"], machine=machine, dll=True, version=version,
            strings={"CompanyName": "NVIDIA Corporation", "OriginalFilename": original, "ProductName": "NVIDIA NGX"},
            pad=64)
    if marker:
        with open(path, "ab") as f:
            f.write(marker)
    return path


def fake_verifier(path: str) -> SignatureInfo:
    """Files whose bytes end in a marker get a matching signature result."""
    tail = Path(path).read_bytes()[-16:]
    if b"MODIFIED" in tail:
        return SignatureInfo(True, False, "NVIDIA Corporation", "The signature is invalid (file was modified).")
    if b"OTHERSIGNER" in tail:
        return SignatureInfo(True, True, "Some Modder", "Valid Authenticode signature.")
    if b"UNSIGNED" in tail:
        return SignatureInfo(True, False, None, "The file is not signed.")
    return SignatureInfo(True, True, "NVIDIA Corporation", "Valid Authenticode signature.")


def test_valid_nvidia_runtime_is_verified(tmp_path):
    c = runtimes.evaluate(str(nv_dll(tmp_path / NR_RUNTIME)), verifier=fake_verifier)
    assert c.trust == TrustState.VERIFIED and c.version == "310.8.0.0" and c.original_name == NR_RUNTIME


@pytest.mark.parametrize("marker,detail", [(b"MODIFIED", "Modified NVIDIA files"), (b"OTHERSIGNER", "not NVIDIA"),
                                           (b"UNSIGNED", "not signed")])
def test_modified_or_foreign_signed_runtime_is_refused(tmp_path, marker, detail):
    c = runtimes.evaluate(str(nv_dll(tmp_path / NR_RUNTIME, marker=marker)), verifier=fake_verifier)
    assert c.trust == TrustState.BLOCKED and detail in c.detail


def test_renamed_nvidia_dll_is_refused(tmp_path):
    # A genuine, NVIDIA-signed frame-generation DLL saved under the Neural Rendering name.
    c = runtimes.evaluate(str(nv_dll(tmp_path / NR_RUNTIME, original="nvngx_dlssg.dll")), verifier=fake_verifier)
    assert c.trust == TrustState.BLOCKED and "nvngx_dlssg.dll" in c.detail


def test_wrong_architecture_and_garbage_are_refused(tmp_path):
    x86 = runtimes.evaluate(str(nv_dll(tmp_path / "a" / NR_RUNTIME, machine="x86")), verifier=fake_verifier)
    assert x86.trust == TrustState.BLOCKED and "architecture" in x86.detail
    junk = tmp_path / "b" / NR_RUNTIME
    junk.parent.mkdir()
    junk.write_bytes(b"not a dll at all")
    assert runtimes.evaluate(str(junk), verifier=fake_verifier).trust == TrustState.BLOCKED


def test_without_signature_support_runtime_stays_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(runtimes.authenticode, "is_windows", lambda: False)
    c = runtimes.evaluate(str(nv_dll(tmp_path / NR_RUNTIME)))
    assert c.trust == TrustState.UNVERIFIED


def test_discovery_finds_driver_ngx_store_and_game_copies(tmp_path):
    drv = tmp_path / "DriverStore" / "nv_dispi.inf_amd64_abc" / "NGX"
    nv_dll(drv / NR_RUNTIME, version=(310, 8, 0, 0))
    store = tmp_path / "NGX" / "models" / "dlssnr" / "versions" / "1" / "files"
    nv_dll(store / "160_ABC.bin", version=(310, 9, 1, 0))                         # renamed by NVIDIA's updater
    nv_dll(store / "161_DEF.bin", original="nvngx_dlssg.dll", version=(400, 0, 0, 0))  # other feature: ignored
    game = tmp_path / "Games" / "Some Game"
    nv_dll(game / NR_RUNTIME, version=(310, 8, 0, 0))                              # same bytes as driver copy
    rhi = tmp_path / "RHI" / "DLSS-NR"
    nv_dll(rhi / NR_RUNTIME, version=(310, 8, 5, 0), marker=b"MODIFIED")          # patched build
    roots = [SearchRoot(str(tmp_path / "DriverStore" / "nv_dispi.inf_amd64_abc"), "driver_store", 3, (NR_RUNTIME,)),
             SearchRoot(str(tmp_path / "NGX"), "ngx_store", 6, (NR_RUNTIME,), bin_files=True),
             SearchRoot(str(game), "game", 0, (NR_RUNTIME,)),
             SearchRoot(str(rhi), "rhi", 2, (NR_RUNTIME,)),
             SearchRoot(str(tmp_path / "missing"), "downloads", 2, (NR_RUNTIME,))]
    rep = runtimes.discover_report(NR_RUNTIME, verifier=fake_verifier, roots=roots)
    assert len(rep.searched) == 5
    # driver and game copies are identical -> one candidate, credited to the driver
    paths = [c.path for c in rep.candidates]
    assert not any("Some Game" in p for p in paths)
    best = rep.best()
    assert best.source == "ngx_store" and best.version == "310.9.1.0", "newest verified copy first"
    assert [c.source for c in rep.verified] == ["ngx_store", "driver_store"]
    assert [c.source for c in rep.refused] == ["rhi"]
    assert all(c.official_source for c in rep.verified)


def test_import_copies_verified_runtime_and_best_runtime_prefers_newest(tmp_path, db):
    old = runtimes.evaluate(str(nv_dll(tmp_path / "old" / NR_RUNTIME, version=(310, 8, 0, 0))), NR_RUNTIME,
                            "driver_store", fake_verifier)
    new = runtimes.evaluate(str(nv_dll(tmp_path / "new" / NR_RUNTIME, version=(310, 10, 0, 0))), NR_RUNTIME,
                            "ngx_store", fake_verifier)
    runtimes.import_candidate(db, new)
    runtimes.import_candidate(db, old)          # imported later, but older
    best = runtimes.best_runtime(db, NR_RUNTIME)
    assert best["version"] == "310.10.0.0" and Path(best["stored_path"]).is_file()
    assert Path(best["stored_path"]).read_bytes() == (tmp_path / "new" / NR_RUNTIME).read_bytes()


def test_blocked_candidate_is_recorded_but_never_installable(tmp_path, db):
    bad = runtimes.evaluate(str(nv_dll(tmp_path / NR_RUNTIME, marker=b"MODIFIED")), verifier=fake_verifier)
    runtimes.import_candidate(db, bad)
    assert db.runtimes(NR_RUNTIME)[0]["trust"] == "blocked"
    assert runtimes.best_runtime(db, NR_RUNTIME) is None


def test_import_runtime_rejects_other_file_names(tmp_path, db):
    with pytest.raises(ValueError):
        runtimes.import_runtime(db, str(nv_dll(tmp_path / "nvngx_dlssg.dll", original="nvngx_dlssg.dll")))


def test_dlss_runtime_discovery_uses_its_own_name(tmp_path):
    g = tmp_path / "g"
    nv_dll(g / DLSS_RUNTIME, original=DLSS_RUNTIME, version=(310, 4, 0, 0))
    nv_dll(g / NR_RUNTIME)
    rep = runtimes.discover_report(DLSS_RUNTIME, verifier=fake_verifier,
                                   roots=[SearchRoot(str(g), "game", 0, (DLSS_RUNTIME,))])
    assert [c.file_name for c in rep.candidates] == [DLSS_RUNTIME]


def test_search_roots_cover_documented_locations(monkeypatch, tmp_path):
    monkeypatch.setattr(runtimes, "is_windows", lambda: True)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    (tmp_path / "Windows" / "System32" / "DriverStore" / "FileRepository" / "nv_dispi.inf_amd64_x").mkdir(parents=True)
    (tmp_path / "Windows" / "System32" / "DriverStore" / "FileRepository" / "realtek.inf_amd64_x").mkdir(parents=True)
    roots = runtimes.search_roots(NR_RUNTIME, ["D:/Games/X"])
    kinds = [r.source for r in roots]
    assert kinds.count("driver_store") == 1, "only NVIDIA driver packages are searched"
    assert {"system", "ngx_store", "driver_package", "rhi", "downloads", "game"} <= set(kinds)
    assert next(r for r in roots if r.source == "ngx_store").bin_files
    assert next(r for r in roots if r.source == "driver_package").compressed
    assert "game" not in [r.source for r in runtimes.search_roots(NR_RUNTIME, [], user_dirs=False)]
