"""Realistic NVIDIA metadata, synthetic export tables and injected signatures; no vendor DLL fixtures."""
from pathlib import Path
import pytest

from akeno.backends import runtimes
from akeno.core.models import TrustState
from akeno.games import pe
from akeno.security.authenticode import SignatureInfo
from helpers import make_pe
from test_runtimes import fake_verifier

EXPORTS = ["NVSDK_NGX_D3D12_CreateFeature", "NVSDK_NGX_D3D12_EvaluateFeature"]


def candidate(tmp_path, runtime="nvngx_dlss.dll", original="CL 38277099", product="NVIDIA DLSSv3",
              export_name="CL 38277099", exports=EXPORTS, dll=True, version=(310, 7, 128, 0)):
    return make_pe(tmp_path / runtime, [], dll=dll, version=version,
        strings={"OriginalFilename": original, "InternalName": original, "CompanyName": "NVIDIA Corporation",
                 "ProductName": product, "FileDescription": product}, exports=exports, export_name=export_name)


@pytest.mark.parametrize("cl,version", [("CL 38277099", (310, 7, 128, 0)), ("CL 37642667", (310, 6, 0, 0)),
                                        ("CL 37460577", (310, 6, 0, 0))])
def test_reported_sr_changelist_builds_are_not_false_rejections(tmp_path, cl, version):
    p = candidate(tmp_path, original=cl, export_name=cl, version=version)
    c = runtimes.evaluate(str(p), verifier=fake_verifier)
    assert c.trust == TrustState.VERIFIED and "CL build metadata" in c.detail
    assert set(pe.parse(p).exports) == set(EXPORTS)


def test_cl_neural_runtime_requires_specific_identity(tmp_path):
    p = candidate(tmp_path, runtime="nvngx_dlssnr.dll", product="NVIDIA DLSS Neural Rendering", version=(310, 9, 16, 0))
    assert runtimes.evaluate(str(p), verifier=fake_verifier).trust == TrustState.VERIFIED
    p = candidate(tmp_path, runtime="nvngx_dlssnr.dll", product="NVIDIA NGX")
    assert runtimes.evaluate(str(p), verifier=fake_verifier).trust == TrustState.BLOCKED


@pytest.mark.parametrize("changes", [
    {"product": "NVIDIA DLSS Frame Generation"}, {"product": "NVIDIA DLSS Ray Reconstruction"},
    {"product": "NVIDIA NGX"}, {"export_name": "nvngx_dlssg.dll"},
    {"original": "nvngx_dlssd.dll"}, {"exports": []}, {"dll": False},
])
def test_unrelated_signed_libraries_cannot_be_renamed_to_sr(tmp_path, changes):
    c = runtimes.evaluate(str(candidate(tmp_path, **changes)), verifier=fake_verifier)
    assert c.trust == TrustState.BLOCKED


def test_ng_export_names_in_an_overlay_are_not_an_export_table(tmp_path):
    p = candidate(tmp_path, exports=[])
    with p.open("ab") as f:
        f.write("\0".join(EXPORTS).encode())
    assert runtimes.evaluate(str(p), verifier=fake_verifier).trust == TrustState.BLOCKED


def test_cl_acceptance_still_requires_actual_signature_success(tmp_path):
    p = candidate(tmp_path)
    c = runtimes.evaluate(str(p), verifier=lambda _: SignatureInfo(True, False, "NVIDIA Corporation", "tampered"))
    assert c.trust == TrustState.BLOCKED and "tampered" in c.detail


@pytest.mark.parametrize("signer", ["Not NVIDIA Corporation", "NVIDIA Evil LLC", "NVIDIA Corporation CA", "Some Modder"])
def test_publisher_substrings_are_not_authentic_nvidia(signer):
    assert not SignatureInfo(True, True, signer, "fixture").is_nvidia


def test_actual_publisher_requires_valid_checked_signature():
    assert SignatureInfo(True, True, "NVIDIA Corporation", "fixture").is_nvidia
    assert not SignatureInfo(False, True, "NVIDIA Corporation", "fixture").is_nvidia


def test_ng_store_cl_identity_and_nested_game_discovery(tmp_path):
    p = candidate(tmp_path / "models", runtime="renamed.bin")
    report = runtimes.discover_report(runtimes.DLSS_RUNTIME, verifier=fake_verifier,
        roots=[runtimes.SearchRoot(str(p.parent), "ngx_store", 2, (runtimes.DLSS_RUNTIME,), bin_files=True)])
    assert len(report.verified) == 1


def test_same_display_version_is_not_a_build_identity(tmp_path):
    a = candidate(tmp_path / "a", original="CL 37642667", version=(310, 6, 0, 0))
    b = candidate(tmp_path / "b", original="CL 37460577", version=(310, 6, 0, 0))
    ca, cb = (runtimes.evaluate(str(p), verifier=fake_verifier) for p in (a, b))
    assert ca.version == cb.version and ca.sha256 != cb.sha256 and ca.original_name != cb.original_name
