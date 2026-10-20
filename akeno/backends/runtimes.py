"""NVIDIA runtime files (``nvngx_dlssnr.dll``) supplied by the user's own driver installation.

AKENO never downloads or redistributes NVIDIA binaries. It can:
  * locate the copy that the user's installed NVIDIA driver placed on this PC, or
  * import a file the user picks,
and in both cases it records the hash, version and Authenticode signer. Only files with a valid
NVIDIA signature are considered installable: modified ("patched") NVIDIA DLLs are refused, because
installing them would mean bypassing NVIDIA's code signature.
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass

from akeno.core.db import Database
from akeno.core.models import TrustState
from akeno.games import pe
from akeno.paths import is_windows, runtimes_dir
from akeno.security import authenticode
from akeno.security.downloads import sha256_file

NR_RUNTIME = "nvngx_dlssnr.dll"
DLSS_RUNTIME = "nvngx_dlss.dll"
SUPPORTED_RUNTIMES = (NR_RUNTIME, DLSS_RUNTIME)


@dataclass
class RuntimeCandidate:
    path: str
    sha256: str
    version: str | None
    machine: str | None
    signer: str | None
    trust: TrustState
    detail: str


def search_locations(file_name: str = NR_RUNTIME) -> list[str]:
    if not is_windows():
        return []
    win = os.environ.get("SystemRoot", r"C:\Windows")
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    pats = [
        os.path.join(win, "System32", "DriverStore", "FileRepository", "nv*.inf_amd64_*", file_name),
        os.path.join(win, "System32", "DriverStore", "FileRepository", "nv*.inf_amd64_*", "*", file_name),
        os.path.join(pd, "NVIDIA", "NGX", "**", file_name),
    ]
    out: list[str] = []
    for p in pats:
        for f in glob.glob(p, recursive=True):
            if f not in out:
                out.append(f)
    return out


def evaluate(path: str) -> RuntimeCandidate:
    info = pe.parse(path)
    sha = sha256_file(path)
    if not info.valid:
        return RuntimeCandidate(path, sha, None, None, None, TrustState.BLOCKED, "Not a valid Windows DLL.")
    if info.machine != "x64":
        return RuntimeCandidate(path, sha, info.file_version, info.machine, None, TrustState.BLOCKED,
                                f"Wrong architecture ({info.machine}); a 64-bit DLL is required.")
    sig = authenticode.verify(path)
    if not sig.checked:
        trust, detail = TrustState.UNVERIFIED, sig.detail
    elif sig.is_nvidia:
        trust, detail = TrustState.VERIFIED, f"Valid signature from {sig.signer}."
    elif sig.valid:
        trust, detail = TrustState.BLOCKED, f"Signed by '{sig.signer}', not NVIDIA."
    else:
        trust, detail = TrustState.BLOCKED, (sig.detail + " Modified NVIDIA files are not installed by AKENO.")
    return RuntimeCandidate(path, sha, info.file_version, info.machine, sig.signer, trust, detail)


def discover(file_name: str = NR_RUNTIME) -> list[RuntimeCandidate]:
    return [evaluate(p) for p in search_locations(file_name)]


def import_runtime(db: Database, path: str) -> RuntimeCandidate:
    file_name = os.path.basename(path).lower()
    if file_name not in SUPPORTED_RUNTIMES:
        raise ValueError(f"AKENO imports only {', '.join(SUPPORTED_RUNTIMES)}; got {os.path.basename(path)}.")
    cand = evaluate(path)
    if cand.trust == TrustState.BLOCKED:
        db.add_runtime(sha256=cand.sha256, file_name=file_name, stored_path="", original_path=path,
                       version=cand.version, signer=cand.signer, trust=cand.trust.value)
        return cand
    dest_dir = runtimes_dir() / cand.sha256[:16]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_name
    if not dest.exists() or sha256_file(dest) != cand.sha256:
        shutil.copy2(path, dest)
    db.add_runtime(sha256=cand.sha256, file_name=file_name, stored_path=str(dest), original_path=path,
                   version=cand.version, signer=cand.signer, trust=cand.trust.value)
    return cand


def best_runtime(db: Database, file_name: str = NR_RUNTIME, allow_unverified: bool = False) -> dict | None:
    """Imported runtime to install: newest verified one (unverified only when explicitly allowed, e.g. tests)."""
    for r in db.runtimes(file_name):
        if not r["stored_path"] or not os.path.isfile(r["stored_path"]):
            continue
        if r["trust"] == TrustState.VERIFIED.value or (allow_unverified and r["trust"] == TrustState.UNVERIFIED.value):
            return r
    return None
