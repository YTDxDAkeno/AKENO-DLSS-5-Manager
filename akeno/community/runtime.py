"""Local-only runtime classification. Reviews are user-selected evidence, not signatures."""
from __future__ import annotations
from akeno.i18n import msg

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from akeno.community import candidates
from akeno.backends import runtimes
from akeno.backends.runtime_identity import identify
from akeno.games import pe
from akeno.paths import app_home
from akeno.security import authenticode
from akeno.security.downloads import sha256_file


class Category(str, Enum):
    OFFICIAL = "official"
    COMMUNITY = "reviewed_community"
    UNKNOWN = "unreviewed"
    ADVANCED = "advanced_local"
    REJECTED = "rejected"


LABELS = {
    Category.OFFICIAL: "Official NVIDIA runtime — Signature verified",
    Category.COMMUNITY: "Experimental community runtime — Not NVIDIA-signed",
    Category.UNKNOWN: "Unverified — Source and consent required",
    Category.ADVANCED: "Advanced local experiment — Unverified",
    Category.REJECTED: "Rejected runtime",
}


# Game binaries can be much larger than bounded NVIDIA runtimes.
MAX_GAME_FILE = 16 * 1024 * 1024 * 1024


def _regular_bounded(path: Path, maximum: int, error: str) -> None:
    for p in (path, *path.parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError(msg("Linked paths are not permitted."))
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError(msg(error))


def regular(path: Path) -> None:
    """Validate runtime/package files with the original 512-MiB limit."""
    _regular_bounded(path, runtimes.MAX_FILE, "A bounded regular runtime file is required.")


def regular_game_file(path: Path) -> None:
    """Validate game executables/assets without applying the DLL size limit."""
    _regular_bounded(path, MAX_GAME_FILE, "A bounded regular game file is required.")


def validate_review(data: dict) -> dict:
    if isinstance(data, dict) and data.get("schema") == 3:
        from akeno.community.profiles import validate_profile
        return validate_profile(data)
    if not isinstance(data, dict) or data.get("schema") not in (1, 2):
        raise ValueError(msg("Unsupported community review schema."))
    required = ("sha256", "version", "repository", "upstream_commit", "reviewer", "reviewed_at",
                "modifications", "build_instructions", "security_review", "license_review")
    if any(not isinstance(data.get(k), str) or not data[k].strip() for k in required):
        raise ValueError(msg("The review needs complete source, build, security and licensing evidence."))
    if not re.fullmatch(r"[a-f0-9]{64}", data["sha256"]) or not re.fullmatch(r"[a-f0-9]{40}", data["upstream_commit"]):
        raise ValueError(msg("The review needs exact SHA-256 and upstream commit pins."))
    candidate = None
    if data["schema"] == 2:
        candidate = candidates.validate_binding(data)
    else:
        if candidates.by_hash(data["sha256"]):
            raise ValueError(msg("Use the named candidate review so runtime source and modified-signature risks remain explicit."))
        repo = urlsplit(data["repository"])
        release = urlsplit(data.get("release", ""))
        if (repo.scheme != "https" or repo.netloc != "github.com" or repo.query or repo.fragment
                or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo.path)
                or release.scheme != "https" or release.netloc != "github.com" or release.query or release.fragment
                or not release.path.startswith(repo.path + "/releases/tag/") or ".." in release.path):
            raise ValueError(msg("Repository and release must identify the same explicit GitHub source."))
    if date.fromisoformat(data["reviewed_at"]) > date.today() or data.get("local_use_permitted") is not True:
        raise ValueError(msg("A dated local-use licensing review is required."))
    if not isinstance(data.get("limitations"), list) or not data["limitations"] or any(not isinstance(x, str) or not x.strip() for x in data["limitations"]):
        raise ValueError(msg("Known limitations must be recorded."))
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError(msg("The review must declare exact experimental hardware/driver/backend/API targets."))
    for t in targets:
        if (not isinstance(t, dict) or t.get("generation") not in ("RTX 20", "RTX 30", "RTX 40", "RTX 50")
                or not re.fullmatch(r"\d+\.\d+", str(t.get("driver", "")))
                or t.get("backend") not in ("optiscaler_nr", "dlss5_feeder")
                or t.get("api") not in ("d3d11", "d3d12", "vulkan")
                or t.get("status") not in ("experimental", "incompatible") or not t.get("evidence")):
            raise ValueError(msg("Invalid exact compatibility target; review claims cannot certify execution."))
        if candidate and (t.get("generation") != "RTX 40" or t.get("gpu_device_id") not in candidate.experimental_device_ids):
            raise ValueError(msg("This named candidate has only an unverified RTX 4080 experimental profile; other GPUs need separate evidence."))
    return json.loads(json.dumps(data))


def load_review(path: str | Path) -> dict:
    p = Path(path)
    regular(p)
    if p.stat().st_size > 128 * 1024 or p.suffix.lower() != ".json":
        raise ValueError(msg("Select a bounded JSON review record, not an installer or archive."))
    return validate_review(json.loads(p.read_text(encoding="utf-8-sig")))


@dataclass
class Evaluation:
    path: str
    category: Category
    sha256: str
    version: str | None
    original_filename: str | None
    family: str | None
    architecture: str | None
    signature: dict
    exports: list[str]
    source: str
    detail: str
    review: dict | None = None
    named_candidate: dict | None = None

    def to_dict(self):
        d = asdict(self)
        d["category"] = self.category.value
        d["trust_label"] = LABELS[self.category]
        d["execution_verified"] = False
        d["review_scope"] = "User-selected local review; not an AKENO endorsement or malware-free certificate."
        return d


class RuntimeManager:
    def __init__(self, db, verifier=None, denied_hashes=()):
        self.db, self.verifier = db, verifier or authenticode.verify
        self.denied_hashes = frozenset(denied_hashes)

    def inspect(self, path: str, review: dict | None = None, candidate_id: str | None = None) -> Evaluation:
        p = Path(path)
        result = Evaluation(str(p), Category.REJECTED, "", None, None, None, None, {}, [],
                            "User-selected local file; origin not independently attested.", "")
        try:
            regular(p)
            if p.suffix.lower() not in (".dll", ".bin"):
                raise ValueError(msg("Select a runtime DLL/model; archives, scripts and installers are not accepted."))
            result.sha256 = sha256_file(p)
            named = candidates.by_hash(result.sha256)
            selected = candidates.by_id(candidate_id) if candidate_id else None
            if candidate_id and (selected is None or selected.sha256 != result.sha256):
                raise ValueError(msg("The selected file does not match the named runtime's published SHA-256."))
            result.named_candidate = named.to_dict() if named else None
            if named:
                result.source = named.runtime_source
            if result.sha256 in self.denied_hashes:
                raise ValueError(msg("This exact file hash is denied by the security policy."))
            info = pe.parse(str(p))
            result.version, result.architecture = info.file_version, info.machine
            result.original_filename = next((v for k, v in info.strings.items() if k.lower() == "originalfilename"), None)
            result.exports = list(info.exports)
            ok, detail = identify(info, runtimes.NR_RUNTIME)
            if not ok:
                raise ValueError(detail)
            if set(info.exports) & {"DllInstall", "DllRegisterServer", "ServiceMain"}:
                raise ValueError(msg("Unexpected installer/service exports require separate review."))
            result.family = runtimes.NR_RUNTIME
            sig = self.verifier(str(p))
            result.signature = asdict(sig)
            if sha256_file(p) != result.sha256:
                raise ValueError(msg("File changed during inspection."))
            if review is not None:
                result.review = validate_review(review)
                if review["sha256"] != result.sha256 or review["version"] != result.version:
                    raise ValueError(msg("The selected DLL does not match the review's exact hash and version."))
            if not result.version:
                result.category, result.detail = Category.UNKNOWN, msg("Runtime version metadata is missing; further review is required.")
            elif named and sig.is_nvidia:
                result.category, result.detail = Category.REJECTED, msg("The known modified runtime cannot be classified as NVIDIA-authenticated; the signature result conflicts with its recorded provenance.")
            elif sig.is_nvidia:
                result.category, result.detail = Category.OFFICIAL, LABELS[Category.OFFICIAL]
            elif result.review and result.review.get("schema") == 3 and sig.checked:
                result.category = Category.COMMUNITY if named else Category.ADVANCED
                result.source = result.review["runtime_source"]
                result.detail = msg("Experimental local use only. Signature is not NVIDIA-authenticated; safety and execution remain unverified.")
            elif result.review and sig.checked:
                result.category = Category.COMMUNITY
                result.detail = "Local review and exact pin accepted. Signature is not NVIDIA-authenticated; safety and execution remain unproven."
            else:
                result.category = Category.UNKNOWN
                result.detail = msg("Record the runtime source and confirm local-use permission to prepare an unverified experiment. A Windows signature inspection is required; unsigned does not mean malicious.")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.category, result.detail = Category.REJECTED, str(exc)
        return result

    def import_local(self, path: str, review: dict | None, consent: bool, candidate_id: str | None = None) -> Evaluation:
        if consent is not True:
            raise PermissionError(msg("Explicit permission for local reuse and experimental import is required."))
        result = self.inspect(path, review, candidate_id)
        if result.category not in (Category.OFFICIAL, Category.COMMUNITY, Category.ADVANCED):
            raise PermissionError(result.detail)
        folder = app_home() / "community-runtimes" / result.sha256
        folder.mkdir(parents=True, exist_ok=True)
        regular(Path(path))
        for p in (folder, *folder.parents):
            if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                raise PermissionError(msg("Linked runtime stores are not permitted."))
        dest = folder / runtimes.NR_RUNTIME
        fd, temp = tempfile.mkstemp(dir=folder, suffix=".dll")
        os.close(fd)
        try:
            shutil.copyfile(path, temp)
            copied = self.inspect(temp, review, candidate_id)
            if copied.sha256 != result.sha256 or copied.category != result.category:
                raise PermissionError(msg("Runtime changed while importing."))
            os.replace(temp, dest)
        finally:
            Path(temp).unlink(missing_ok=True)
        result.path = str(dest)
        self.db.set_setting("community-runtime:" + result.sha256, result.to_dict())
        # Intentionally never enters the official runtimes table or best_runtime lookup.
        return result

    def imported(self) -> list[dict]:
        return [v for k, v in self.db.all_settings().items() if k.startswith("community-runtime:")]
