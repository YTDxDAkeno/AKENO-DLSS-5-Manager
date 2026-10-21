"""Additional exact evidence for a Bandukids FiveM recipe; never a multiplayer override."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from akeno.community import candidates
from akeno.community.runtime import Category


def review_digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def verify_runtime(path, record, recipe, inspector, gpu):
    candidate = candidates.by_id(recipe.community_candidate_id)
    expected = dict(recipe.runtime_hashes).get('nvngx_dlssnr.dll')
    if not candidate or expected != candidate.sha256 or not inspector or not record or not gpu:
        raise ValueError("A named, pinned Bandukids runtime and independent review inspection are required")
    if record.get('trust') != Category.COMMUNITY.value or not isinstance(record.get('review'), dict):
        raise ValueError("A completed separate community runtime review is required")
    review = record['review']
    if (review.get('schema') != 2 or review.get('candidate_id') != candidate.id
            or review_digest(review) != recipe.community_review_sha256):
        raise ValueError("The local community review differs from the maintainer-reviewed FiveM recipe")
    if gpu.device_id not in candidate.experimental_device_ids:
        raise ValueError("The Bandukids experimental GPU profile does not match")
    matching = [t for t in review.get('targets', []) if
        (t.get('generation'), t.get('gpu_device_id'), t.get('driver'), t.get('backend'), t.get('api')) ==
        (gpu.generation, gpu.device_id, gpu.driver_version, recipe.backend_id, recipe.api.value)]
    if not matching or any(t.get('status') != 'experimental' for t in matching):
        raise ValueError("The runtime review lacks this exact GPU/driver/backend/API target")
    # Reads PE/hash/signature through the separate policy again. No signature bypass or cached approval.
    result = inspector(str(Path(path)), review, candidate.id)
    if result.sha256 != expected or result.category != Category.COMMUNITY:
        raise ValueError("The current file does not pass its separate community identity/review policy")
