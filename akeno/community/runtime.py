"""Local-only runtime classification. Reviews are user-selected evidence, not signatures."""
from __future__ import annotations

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
    REJECTED = "rejected"


LABELS = {
    Category.OFFICIAL: "Official NVIDIA runtime — Signature verified",
    Category.COMMUNITY: "Experimental community runtime — Not NVIDIA-signed",
    Category.UNKNOWN: "Unverified — Import requires further review",
    Category.REJECTED: "Rejected runtime",
}


def regular(path: Path) -> None:
    for p in (path, *path.parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError("Linked paths are not permitted.")
    if not path.is_file() or not 0 < path.stat().st_size <= runtimes.MAX_FILE:
        raise ValueError("A bounded regular runtime file is required.")


def validate_review(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Unsupported community review schema.")
    required = ("sha256", "version", "repository", "release", "upstream_commit", "reviewer", "reviewed_at",
                "modifications", "build_instructions", "security_review", "license_review")
    if any(not isinstance(data.get(k), str) or not data[k].strip() for k in required):
        raise ValueError("The review needs complete source, build, security and licensing evidence.")
    if not re.fullmatch(r"[a-f0-9]{64}", data["sha256"]) or not re.fullmatch(r"[a-f0-9]{40}", data["upstream_commit"]):
        raise ValueError("The review needs exact SHA-256 and upstream commit pins.")
    repo = urlsplit(data["repository"])
    release = urlsplit(data["release"])
    if (repo.scheme != "https" or repo.netloc != "github.com" or repo.query or repo.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo.path)
            or release.scheme != "https" or release.netloc != "github.com" or release.query or release.fragment
            or not release.path.startswith(repo.path + "/releases/tag/") or ".." in release.path):
        raise ValueError("Repository and release must identify the same explicit GitHub source.")
    if date.fromisoformat(data["reviewed_at"]) > date.today() or data.get("local_use_permitted") is not True:
        raise ValueError("A dated local-use licensing review is required.")
    if not isinstance(data.get("limitations"), list) or not data["limitations"] or any(not isinstance(x, str) or not x.strip() for x in data["limitations"]):
        raise ValueError("Known limitations must be recorded.")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("The review must declare exact experimental hardware/driver/backend/API targets.")
    for t in targets:
        if (not isinstance(t, dict) or t.get("generation") not in ("RTX 20", "RTX 30", "RTX 40", "RTX 50")
                or not re.fullmatch(r"\d+\.\d+", str(t.get("driver", "")))
                or t.get("backend") not in ("optiscaler_nr", "dlss5_feeder")
                or t.get("api") not in ("d3d11", "d3d12", "vulkan")
                or t.get("status") not in ("experimental", "incompatible") or not t.get("evidence")):
            raise ValueError("Invalid exact compatibility target; review claims cannot certify execution.")
    return json.loads(json.dumps(data))


def load_review(path: str | Path) -> dict:
    p = Path(path)
    regular(p)
    if p.stat().st_size > 128 * 1024 or p.suffix.lower() != ".json":
        raise ValueError("Select a bounded JSON review record, not an installer or archive.")
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

    def inspect(self, path: str, review: dict | None = None) -> Evaluation:
        p = Path(path)
        result = Evaluation(str(p), Category.REJECTED, "", None, None, None, None, {}, [],
                            "User-selected local file; origin not independently attested.", "")
        try:
            regular(p)
            if p.suffix.lower() not in (".dll", ".bin"):
                raise ValueError("Select a runtime DLL/model; archives, scripts and installers are not accepted.")
            result.sha256 = sha256_file(p)
            if result.sha256 in self.denied_hashes:
                raise ValueError("This exact file hash is denied by the security policy.")
            info = pe.parse(str(p))
            result.version, result.architecture = info.file_version, info.machine
            result.original_filename = next((v for k, v in info.strings.items() if k.lower() == "originalfilename"), None)
            result.exports = list(info.exports)
            ok, detail = identify(info, runtimes.NR_RUNTIME)
            if not ok:
                raise ValueError(detail)
            if set(info.exports) & {"DllInstall", "DllRegisterServer", "ServiceMain"}:
                raise ValueError("Unexpected installer/service exports require separate review.")
            result.family = runtimes.NR_RUNTIME
            sig = self.verifier(str(p))
            result.signature = asdict(sig)
            if sha256_file(p) != result.sha256:
                raise ValueError("File changed during inspection.")
            if review is not None:
                result.review = validate_review(review)
                if review["sha256"] != result.sha256 or review["version"] != result.version:
                    raise ValueError("The selected DLL does not match the review's exact hash and version.")
            if sig.is_nvidia:
                result.category, result.detail = Category.OFFICIAL, LABELS[Category.OFFICIAL]
            elif result.review and sig.checked:
                result.category = Category.COMMUNITY
                result.detail = "Local review and exact pin accepted. Signature is not NVIDIA-authenticated; safety and execution remain unproven."
            else:
                result.category = Category.UNKNOWN
                result.detail = "A complete local review and a Windows signature inspection are required; unsigned does not mean malicious."
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.category, result.detail = Category.REJECTED, str(exc)
        return result

    def import_local(self, path: str, review: dict | None, consent: bool) -> Evaluation:
        if consent is not True:
            raise PermissionError("Explicit permission for local reuse and experimental import is required.")
        result = self.inspect(path, review)
        if result.category not in (Category.OFFICIAL, Category.COMMUNITY):
            raise PermissionError(result.detail)
        folder = app_home() / "community-runtimes" / result.sha256
        folder.mkdir(parents=True, exist_ok=True)
        regular(Path(path))
        for p in (folder, *folder.parents):
            if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                raise PermissionError("Linked runtime stores are not permitted.")
        dest = folder / runtimes.NR_RUNTIME
        fd, temp = tempfile.mkstemp(dir=folder, suffix=".dll")
        os.close(fd)
        try:
            shutil.copyfile(path, temp)
            copied = self.inspect(temp, review)
            if copied.sha256 != result.sha256 or copied.category != result.category:
                raise PermissionError("Runtime changed while importing.")
            os.replace(temp, dest)
        finally:
            Path(temp).unlink(missing_ok=True)
        result.path = str(dest)
        self.db.set_setting("community-runtime:" + result.sha256, result.to_dict())
        # Intentionally never enters the official runtimes table or best_runtime lookup.
        return result

    def imported(self) -> list[dict]:
        return [v for k, v in self.db.all_settings().items() if k.startswith("community-runtime:")]
