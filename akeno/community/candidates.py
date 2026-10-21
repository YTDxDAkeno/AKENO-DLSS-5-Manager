"""Named research candidates, not approved binaries or automatic download sources."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from akeno.i18n import msg


@dataclass(frozen=True)
class RuntimeCandidate:
    id: str
    name: str
    sha256: str
    repository: str
    source_commit: str
    runtime_source: str
    reported_signature: str
    experimental_device_ids: tuple[int, ...]

    def to_dict(self):
        return {**asdict(self), "review_approved": False, "binary_safety_verified": False,
            "game_execution_verified": False, "download_automatically": False,
            "profile": {"name": "RTX 4080 experimental", "vendor_id": 0x10DE, "device_id": 0x2704,
                "generation": "RTX 40", "vram_gb": 16, "status": "unverified",
                "note": "Target hardware inventory, not a memory-fit or performance guarantee."},
            "other_generations": {"RTX 20": "unverified", "RTX 30": "unverified", "RTX 50": "unverified"},
            "limitations": ["ComfyUI video-worker claims do not establish game/plugin compatibility.",
                "User reports local hash match and Windows HashMismatch; this does not establish safety or permission.",
                "NVIDIA model modifications and redistribution/local-use permission need independent review.",
                "FiveM requires a separate exact client/plugin/server and neural-evaluation recipe."]}


BANDUKIDS = RuntimeCandidate(
    "bandukids-ada-nr", "Bandukids Ada Neural Rendering — RTX 4080 experimental",
    "8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206",
    "https://github.com/Bandukids/ComfyUI-DLSS5-VideoSuite",
    "d7ed7bb0bfa3e5ff53bee977f431b92a54a0557e",
    "https://huggingface.co/Bandukids/DLSS-Runtimes/blob/main/nvngx_dlssnr.dll",
    "HashMismatch (user-reported Windows result)", (0x2704,))


def all_candidates():
    return (BANDUKIDS,)


def by_id(candidate_id):
    return next((c for c in all_candidates() if c.id == candidate_id), None)


def by_hash(sha256):
    return next((c for c in all_candidates() if c.sha256 == sha256), None)


def validate_binding(review):
    candidate = by_id(review.get("candidate_id"))
    if candidate is None or (review.get("sha256"), review.get("repository"), review.get("upstream_commit"),
            review.get("runtime_source")) != (candidate.sha256, candidate.repository, candidate.source_commit, candidate.runtime_source):
        raise ValueError(msg("The named runtime review does not match its pinned hash and source provenance."))
    return candidate


def review_draft(candidate, evaluation, gpu, api, backend):
    if evaluation.sha256 != candidate.sha256:
        raise ValueError(msg("The selected file does not match the named runtime's published SHA-256."))
    # Intentionally incomplete: selecting a candidate/hash cannot fabricate expert reviews or permission.
    return {"schema": 2, "candidate_id": candidate.id, "sha256": candidate.sha256, "version": evaluation.version,
        "repository": candidate.repository, "upstream_commit": candidate.source_commit,
        "runtime_source": candidate.runtime_source, "reviewer": "", "reviewed_at": "", "modifications": "",
        "build_instructions": "", "security_review": "", "license_review": "", "local_use_permitted": False,
        "limitations": candidate.to_dict()["limitations"], "targets": [{"generation": "RTX 40",
            "gpu_device_id": 0x2704, "driver": gpu.driver_version if gpu else "", "api": api,
            "backend": backend, "status": "experimental", "evidence": ""}]}
