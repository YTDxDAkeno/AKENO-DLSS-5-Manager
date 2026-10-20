"""Fail-closed server and backend evidence gates on top of AKENO's compatibility engine."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from akeno.backends.base import BackendContext
from akeno.core.models import (AntiCheatRisk, Finding,
                               GraphicsApi, Severity, TrustState, Vendor)
from akeno.security.catalog import Package
from akeno.security.downloads import sha256_file
from akeno.backends import runtimes
from .models import Edition, FiveMInstallation, ServerPolicy


@dataclass(frozen=True)
class BackendReview:
    """Maintainer-reviewed recipe, not a user preference or downloaded manifest.

    No real FiveM recipe is shipped. Fixtures may construct one to test the pipeline.
    Each recipe needs an exact client/configuration fingerprint, artifact pins, explicit
    server permission and successful signed-runtime neural-feature evidence for its GPU.
    Evidence must include feature creation AND evaluation, not just a working overlay/SR.
    """
    backend_id: str
    package_sha256: str
    client_fingerprint: str
    source: str
    upstream_commit: str
    gpu_generation: str
    driver_versions: tuple[str, ...]
    runtime_hashes: tuple[tuple[str, str], ...]
    installed_paths: frozenset[str]
    target: str = "plugins"  # relative to FiveM.app, never GTA's exe directory
    requires_script_hook: bool = False
    neural_feature_evaluated: bool = False
    signed_runtime: bool = False
    api: GraphicsApi = GraphicsApi.D3D11
    prerequisite_hashes: tuple[tuple[str, str], ...] = ()
    gpu_device_id: int | None = None
    tested_vram_bytes: int | None = None


# Explicitly empty: a catalog package being verified does NOT establish FiveM compatibility.
REVIEWED_BACKENDS: tuple[BackendReview, ...] = ()


def policy_findings(policy: ServerPolicy, review: BackendReview | None, package: Package | None) -> list[Finding]:
    out: list[Finding] = []
    def block(code, message):
        out.append(Finding(Severity.BLOCKER, code, message))
    if policy.own_server is not True or not policy.server_id.strip() or not policy.permission_reference.strip():
        block("fivem_server_permission", "An identified, operator-controlled test server and written plugin permission are required.")
    if policy.graphics_plugins_allowed is not True:
        block("fivem_graphics_permission", "Effective server permission for this graphics plugin is unknown or denied.")
    if type(policy.pure_level) is not int or policy.pure_level not in (0, 1, 2):
        block("fivem_pure_unknown", "The server's effective sv_pureLevel is unknown; inspect all server configuration includes.")
    elif policy.pure_level == 2:
        block("fivem_pure_restricted", "sv_pureLevel 2 blocks modified client files. AKENO will not change or bypass it.")
    elif policy.pure_level == 1:
        pins = {h for n, h in (package.files.items() if package else [])
                if n.lower().endswith((".dll", ".asi", ".addon64"))}
        if review:
            pins.update(h for _, h in review.runtime_hashes)
            pins.update(h for n, h in review.prerequisite_hashes
                        if n.lower().endswith((".dll", ".asi", ".addon64", ".addon32")))
        if not pins or not pins.issubset(policy.known_graphics_hashes):
            block("fivem_known_graphics", "Pure level 1 only allows known graphics mods; this exact artifact set is not confirmed allowed.")
    if review:
        if review.backend_id not in policy.allowed_backends:
            block("fivem_backend_permission", "The selected backend is not covered by the operator's permission.")
        if review.requires_script_hook and policy.script_hook_allowed is not True:
            block("fivem_script_hook", "This recipe requires Script Hook permission, which is unknown or denied.")
    if policy.experimental_consent is not True:
        block("fivem_experiment_consent", "Explicit consent to an experimental FiveM test is required.")
    return out


def evidence_findings(installation: FiveMInstallation, ctx: BackendContext,
                      review: BackendReview | None, package: Package | None) -> list[Finding]:
    out: list[Finding] = []
    def block(code, message):
        out.append(Finding(Severity.BLOCKER, code, message))
    if installation.edition != Edition.LEGACY:
        block("fivem_edition", "Only positively identified FiveM Legacy installations are eligible; Enhanced and unknown editions are blocked.")
    if installation.errors:
        block("fivem_inspection", "; ".join(installation.errors))
    if not ctx.system or not (ctx.system.os_name or "").lower().startswith("windows") or ctx.system.is_64bit is not True:
        block("fivem_platform", "A detected 64-bit Windows installation is required for deployment.")
    if installation.game.anti_cheat != AntiCheatRisk.NONE_FOUND:
        block("fivem_protection", "Client protection was detected or could not be inspected. No bypass is available.")
    gpu = ctx.gpu
    if not gpu or gpu.vendor != Vendor.NVIDIA or gpu.has_tensor_cores is not True or gpu.is_software:
        block("fivem_gpu", "A positively identified NVIDIA RTX GPU with Tensor cores is required.")
    if gpu and (gpu.vram_bytes is None or gpu.vram_bytes <= 0):
        block("fivem_vram_unknown", "Dedicated VRAM could not be detected; no memory-fit claim is possible.")
    if not gpu or not gpu.driver_version or not re.fullmatch(r"\d{3}\.\d{2}", gpu.driver_version):
        block("fivem_driver_unknown", "A detected NVIDIA driver version is required; no minimum version is guessed.")
    if not review:
        block("fivem_no_review", "No reviewed FiveM Legacy neural-rendering route is available. Generic backend or RTX support is not sufficient.")
        return out
    if not review.source.startswith("https://") or not re.fullmatch(r"[0-9a-f]{40}", review.upstream_commit):
        block("fivem_review_source", "The recipe needs a pinned upstream commit and an HTTPS evidence reference.")
    if not review.neural_feature_evaluated or not review.signed_runtime:
        block("fivem_neural_evidence", "Successful neural-feature creation/evaluation with an unmodified signed runtime is not documented.")
    if review.client_fingerprint != installation.fingerprint:
        block("fivem_client_revision", "The inspected client/configuration differs from the reviewed test configuration.")
    if gpu and (gpu.vendor_id != 0x10DE or gpu.device_id is None or review.gpu_device_id is None
                or gpu.device_id != review.gpu_device_id):
        block("fivem_gpu_identity", "The exact detected NVIDIA PCI device is not covered by the reviewed test.")
    if not review.tested_vram_bytes or (gpu and gpu.vram_bytes is not None and gpu.vram_bytes < review.tested_vram_bytes):
        block("fivem_memory_evidence", "Dedicated VRAM is below the reviewed test configuration, or memory evidence is missing.")
    if gpu and gpu.generation != review.gpu_generation:
        block("fivem_generation_evidence", "The reviewed GPU generation differs from the selected GPU; RTX 4080 support cannot be inferred.")
    if gpu and gpu.driver_version not in review.driver_versions:
        block("fivem_driver_evidence", "This driver version is outside the reviewed matrix; no automatic driver change is performed.")
    if installation.game.api != review.api or review.api != GraphicsApi.D3D11:
        block("fivem_api_evidence", "This Legacy recipe requires measured Direct3D 11 compatibility.")
    if not package or not package.installable or package.backend != review.backend_id or package.sha256 != review.package_sha256:
        block("fivem_package_review", "The package does not match a verified, pinned FiveM recipe.")
    needed = {runtimes.NR_RUNTIME}
    if review.backend_id == "dlss5_feeder":
        needed.add(runtimes.DLSS_RUNTIME)
    pins = dict(review.runtime_hashes)
    if len(pins) != len(review.runtime_hashes) or set(pins) != needed:
        block("fivem_runtime_pins", "The reviewed runtime set is missing or ambiguous.")
    for item in installation.inventory:
        if item.path.name.lower() in needed:
            try:
                if item.sha256 != pins.get(item.path.name.lower()) or runtimes.evaluate(str(item.path)).trust != TrustState.VERIFIED:
                    raise ValueError("existing runtime is not the reviewed signed copy")
            except (OSError, ValueError) as exc:
                block("fivem_existing_runtime", f"{item.path.name}: {exc}")
    for name in sorted(needed):
        record = ctx.runtime_lookup(name)
        # Existing files are not trusted merely because their names match.
        if not record or record.get("trust") != TrustState.VERIFIED.value:
            block("fivem_runtime_trust", f"Import and verify your legally obtained {name}; proprietary downloads are never performed.")
            continue
        path = Path(record.get("stored_path") or "")
        try:
            if path.is_symlink() or not path.is_file() or sha256_file(path) != pins.get(name) or record.get("sha256") != pins.get(name):
                raise ValueError("runtime hash differs from the reviewed copy")
            # Recheck actual Authenticode, not just a cached database trust flag.
            candidate = runtimes.evaluate(str(path))
            if candidate.trust != TrustState.VERIFIED:
                raise ValueError("a valid NVIDIA Authenticode signature cannot be established")
        except (OSError, ValueError) as exc:
            block("fivem_runtime_integrity", f"{name}: {exc}")
    return out

