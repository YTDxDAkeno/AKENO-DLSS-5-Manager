"""NVIDIA runtime files (``nvngx_dlssnr.dll``, ``nvngx_dlss.dll``) taken from the user's own PC.

Licensing (reviewed 2026-10-20, see docs/DEPENDENCIES.md):
  * ``nvngx_dlssnr.dll`` (DLSS 5 Neural Rendering model) "ships in driver packages" (Skynizz/optiscaler-dlss5
    ``OptiScaler/dlssnr/README.md``). NVIDIA publishes **no** separate download for it: it is not in the
    official DLSS SDK (github.com/NVIDIA/DLSS ``lib/Windows_x86_64/rel``). AKENO therefore never downloads it.
  * ``nvngx_dlss.dll`` (DLSS Super Resolution) is in NVIDIA's DLSS SDK, whose license lets *developers*
    ship it inside their own application and forbids distributing it "as a stand-alone product". AKENO is
    not that application, so it neither bundles nor downloads it; it uses a copy already on the PC (most
    DLSS games ship one, and NVIDIA's NGX updater keeps current versions in its model store).

So AKENO can only *find and import* a copy that is already on this PC. Every candidate, wherever it was
found, must carry a valid Authenticode signature from NVIDIA and identify itself (PE version resource) as
the runtime it is imported as. Modified ("patched") NVIDIA DLLs — the community route for RTX 20/30/40 —
fail the signature check and are refused, because installing them would mean bypassing NVIDIA's code
signature. A renamed NVIDIA DLL (e.g. ``nvngx_dlssg.dll`` saved as ``nvngx_dlssnr.dll``) is refused too.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterable

from akeno.core.db import Database
from akeno.core.models import TrustState
from akeno.games import pe
from akeno.paths import is_windows, runtimes_dir
from akeno.security import authenticode
from akeno.security.downloads import sha256_file

log = logging.getLogger(__name__)

NR_RUNTIME = "nvngx_dlssnr.dll"
DLSS_RUNTIME = "nvngx_dlss.dll"
SUPPORTED_RUNTIMES = (NR_RUNTIME, DLSS_RUNTIME)

# Where a candidate was found. Order = preference when several verified copies have the same version.
SOURCES = {
    "driver_store": "NVIDIA display driver (Windows DriverStore)",
    "system": "Windows system folder (placed by the NVIDIA driver)",
    "ngx_store": "NVIDIA NGX model store (driver / NVIDIA App updates)",
    "driver_package": "Extracted NVIDIA driver package (C:\\NVIDIA\\DisplayDriver)",
    "game": "One of your installed games",
    "rhi": "RHI tool folder (copy made by a third-party tool)",
    "downloads": "Your Downloads folder",
    "swapper": "Local DLSS Swapper library (signature and identity still required)",
    "manual": "File you selected",
}
SOURCE_RANK = {k: i for i, k in enumerate(SOURCES)}
OFFICIAL_SOURCES = ("driver_store", "system", "ngx_store", "driver_package")

# NVIDIA's NGX updater stores feature DLLs renamed, e.g. models\dlss\versions\<n>\files\<id>.bin; those are
# recognised by their version resource (OriginalFilename), never by their name.
MAX_FILE = 512 * 1024 * 1024

Verifier = Callable[[str], authenticode.SignatureInfo]


@dataclass
class RuntimeCandidate:
    path: str
    sha256: str
    version: str | None
    machine: str | None
    signer: str | None
    trust: TrustState
    detail: str
    source: str = "manual"
    file_name: str | None = None          # runtime this candidate is (nvngx_dlssnr.dll / nvngx_dlss.dll)
    original_name: str | None = None      # PE OriginalFilename
    extracted_from: str | None = None     # compressed driver-package file this was expanded from

    @property
    def source_label(self) -> str:
        return SOURCES.get(self.source, self.source)

    @property
    def official_source(self) -> bool:
        return self.source in OFFICIAL_SOURCES


@dataclass
class SearchRoot:
    path: str
    source: str
    depth: int
    names: tuple[str, ...] = ()           # exact file names (lower case) to accept
    bin_files: bool = False               # also inspect *.bin (NGX store) by their version resource
    compressed: bool = False              # also accept '<name>.dl_' (driver package, cabinet-compressed)


@dataclass
class DiscoveryReport:
    file_name: str
    candidates: list[RuntimeCandidate] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)    # human-readable list of searched locations
    errors: list[str] = field(default_factory=list)

    @property
    def verified(self) -> list[RuntimeCandidate]:
        return [c for c in self.candidates if c.trust == TrustState.VERIFIED]

    @property
    def refused(self) -> list[RuntimeCandidate]:
        return [c for c in self.candidates if c.trust == TrustState.BLOCKED]

    def best(self) -> RuntimeCandidate | None:
        return self.verified[0] if self.verified else None


# -- search locations ---------------------------------------------------------------------------------
def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def search_roots(file_name: str = NR_RUNTIME, game_dirs: Iterable[str] = (), user_dirs: bool = True) -> list[SearchRoot]:
    """Every location AKENO checks for ``file_name``, with the reason it is trusted to look there."""
    low = file_name.lower()
    roots: list[SearchRoot] = []
    if is_windows():
        win = _env("SystemRoot", r"C:\Windows")
        pd = _env("ProgramData", r"C:\ProgramData")
        sysdrive = _env("SystemDrive", "C:") + os.sep
        # NVIDIA display driver packages are staged in the DriverStore (nv_dispi.inf_amd64_*, nvlt*.inf, ...);
        # feature DLLs can sit in the package root or a sub-folder.
        for d in glob.glob(os.path.join(win, "System32", "DriverStore", "FileRepository", "nv*.inf_amd64_*")):
            roots.append(SearchRoot(d, "driver_store", 3, (low,)))
        roots.append(SearchRoot(os.path.join(win, "System32"), "system", 0, (low,)))
        # NGX model store: driver and NVIDIA App over-the-air updates of DLSS feature DLLs.
        roots.append(SearchRoot(os.path.join(pd, "NVIDIA", "NGX"), "ngx_store", 6, (low,), bin_files=True))
        # Default extraction folder of NVIDIA's driver installer; files there are cabinet-compressed (*.dl_).
        roots.append(SearchRoot(os.path.join(sysdrive, "NVIDIA", "DisplayDriver"), "driver_package", 6, (low,),
                                compressed=True))
        if user_dirs:
            la = _env("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
            # Documented by Skynizz/optiscaler-dlss5 (its installer looks here). Accepted only with an intact
            # NVIDIA signature, which proves the copy is unmodified whatever tool put it there.
            roots.append(SearchRoot(os.path.join(la, "RHI", "DLSS-NR"), "rhi", 2, (low,)))
            for cache in (os.path.join(la, "DLSS Swapper"), os.path.join(la, "DLSSSwapper")):
                roots.append(SearchRoot(cache, "swapper", 5, (low,), bin_files=True))
            roots.append(SearchRoot(os.path.join(pd, "NVIDIA Corporation", "NGX"), "ngx_store", 6, (low,), bin_files=True))
            roots.append(SearchRoot(os.path.join(os.path.expanduser("~"), "Downloads"), "downloads", 2, (low,)))
    for gd in game_dirs:
        if gd:
            roots.append(SearchRoot(gd, "game", 3, (low,)))
    return roots


def _identity_names(path: str) -> tuple[str | None, str | None]:
    info = pe.parse(path)
    if not info.valid:
        return None, None
    s = {k.lower(): v for k, v in info.strings.items()}
    return (s.get("originalfilename") or s.get("internalname") or "").strip() or None, info.file_version


def _walk(root: SearchRoot) -> Iterable[tuple[str, bool]]:
    """Yield (path, compressed) for files under a root that may be the runtime."""
    if not os.path.isdir(root.path):
        return
    base = root.path.rstrip("\\/").count(os.sep)
    names = set(root.names)
    compressed = {n[:-1] + "_" for n in names} if root.compressed else set()
    for dirpath, dirs, files in os.walk(root.path):
        if dirpath.count(os.sep) - base >= root.depth:
            dirs[:] = []
        for f in files:
            fl = f.lower()
            if fl in names:
                yield os.path.join(dirpath, f), False
            elif fl in compressed:
                yield os.path.join(dirpath, f), True
            elif root.bin_files and fl.endswith(".bin"):
                yield os.path.join(dirpath, f), False


def _bin_matches(path: str, file_name: str) -> bool:
    try:
        if os.path.getsize(path) > MAX_FILE:
            return False
    except OSError:
        return False
    from akeno.backends.runtime_identity import identify
    return identify(pe.parse(path), file_name.lower())[0]


def expand_cabinet(src: str, dest_dir: str) -> str | None:
    """Expand a cabinet-compressed driver file (``*.dl_``) with Windows' own ``expand.exe``."""
    if not is_windows():
        return None
    exe = os.path.join(_env("SystemRoot", r"C:\Windows"), "System32", "expand.exe")
    if not os.path.isfile(exe):
        return None
    out = os.path.join(dest_dir, os.path.basename(src)[:-1] + "l")
    try:
        subprocess.run([exe, src, out], check=True, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("expand failed for %s: %s", src, exc)
        return None
    return out if os.path.isfile(out) else None


# -- evaluation ---------------------------------------------------------------------------------------
def evaluate(path: str, file_name: str | None = None, source: str = "manual",
             verifier: Verifier | None = None) -> RuntimeCandidate:
    """Hash, version, architecture, identity and NVIDIA signature of one file."""
    verifier = verifier or authenticode.verify
    want = (file_name or os.path.basename(path)).lower()
    if os.path.islink(path) or os.path.getsize(path) > MAX_FILE:
        raise ValueError("Linked or oversized runtime file")
    sha = sha256_file(path)
    info = pe.parse(path)
    if not info.valid:
        return RuntimeCandidate(path, sha, None, None, None, TrustState.BLOCKED, "Not a valid Windows DLL.", source, want)
    strings = {k.lower(): v for k, v in info.strings.items()}
    orig = (strings.get("originalfilename") or strings.get("internalname") or "").strip() or None
    if info.machine != "x64":
        return RuntimeCandidate(path, sha, info.file_version, info.machine, None, TrustState.BLOCKED,
                                f"Wrong architecture ({info.machine}); a 64-bit DLL is required.", source, want, orig)
    from akeno.backends.runtime_identity import identify
    identity_ok, identity_detail = identify(info, want)
    if not identity_ok:
        return RuntimeCandidate(path, sha, info.file_version, info.machine, None, TrustState.BLOCKED,
                                identity_detail, source, want, orig)
    sig = verifier(path)
    if not sig.checked:
        trust, detail = TrustState.UNVERIFIED, sig.detail
    elif sig.is_nvidia:
        trust, detail = TrustState.VERIFIED, f"Valid signature from {sig.signer}. {identity_detail}"
    elif sig.valid:
        trust, detail = TrustState.BLOCKED, f"Signed by '{sig.signer}', not NVIDIA."
    else:
        trust, detail = TrustState.BLOCKED, (sig.detail + " Modified NVIDIA files are not installed by AKENO.")
    return RuntimeCandidate(path, sha, info.file_version, info.machine, sig.signer, trust, detail, source, want, orig)


def _version_key(v: str | None) -> tuple[int, ...]:
    out = []
    for part in (v or "").replace(",", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def sort_candidates(cands: list[RuntimeCandidate]) -> list[RuntimeCandidate]:
    trust_rank = {TrustState.VERIFIED: 0, TrustState.UNVERIFIED: 1, TrustState.NEEDS_REVIEW: 2, TrustState.BLOCKED: 3}
    return sorted(cands, key=lambda c: (trust_rank.get(c.trust, 9), tuple(-x for x in _version_key(c.version)),
                                        SOURCE_RANK.get(c.source, 99)))


def discover_report(file_name: str = NR_RUNTIME, game_dirs: Iterable[str] = (), user_dirs: bool = True,
                    verifier: Verifier | None = None, roots: list[SearchRoot] | None = None) -> DiscoveryReport:
    """Search every trusted location for ``file_name``; evaluate each distinct file once."""
    rep = DiscoveryReport(file_name.lower())
    seen: dict[str, RuntimeCandidate] = {}
    tmp = None
    for root in roots if roots is not None else search_roots(file_name, game_dirs, user_dirs):
        rep.searched.append(f"{SOURCES.get(root.source, root.source)}: {root.path}")
        try:
            for path, compressed in _walk(root):
                real, src_note = path, None
                if path.lower().endswith(".bin") and not _bin_matches(path, file_name):
                    continue
                if compressed:
                    tmp = tmp or tempfile.mkdtemp(prefix="akeno-expand-")
                    real = expand_cabinet(path, tmp)
                    if not real:
                        rep.errors.append(f"Could not expand {path}")
                        continue
                    src_note = path
                try:
                    cand = evaluate(real, file_name, root.source, verifier)
                except (OSError, ValueError) as exc:
                    rep.errors.append(f"{path}: {exc}")
                    continue
                cand.extracted_from = src_note
                prev = seen.get(cand.sha256)
                if prev is None or SOURCE_RANK.get(cand.source, 99) < SOURCE_RANK.get(prev.source, 99):
                    seen[cand.sha256] = cand
        except OSError as exc:
            rep.errors.append(f"{root.path}: {exc}")
    rep.candidates = sort_candidates(list(seen.values()))
    return rep


def search_locations(file_name: str = NR_RUNTIME) -> list[str]:
    """Paths of candidate files in the driver/NGX locations (kept for callers of the v1.0 API)."""
    out: list[str] = []
    for root in search_roots(file_name, (), user_dirs=False):
        for p, compressed in _walk(root):
            if not compressed and (not p.lower().endswith(".bin") or _bin_matches(p, file_name)) and p not in out:
                out.append(p)
    return out


def discover(file_name: str = NR_RUNTIME, game_dirs: Iterable[str] = (), verifier: Verifier | None = None
             ) -> list[RuntimeCandidate]:
    return discover_report(file_name, game_dirs, verifier=verifier).candidates


# -- import / lookup ----------------------------------------------------------------------------------
def import_candidate(db: Database, cand: RuntimeCandidate) -> RuntimeCandidate:
    """Copy an evaluated candidate into AKENO's runtime store (blocked files are only recorded)."""
    file_name = (cand.file_name or os.path.basename(cand.path)).lower()
    origin = cand.extracted_from or cand.path
    if cand.trust == TrustState.BLOCKED:
        db.add_runtime(sha256=cand.sha256, file_name=file_name, stored_path="", original_path=origin,
                       version=cand.version, signer=cand.signer, trust=cand.trust.value)
        return cand
    dest_dir = runtimes_dir() / cand.sha256[:16]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_name
    if not dest.exists() or sha256_file(dest) != cand.sha256:
        tmp = dest.with_suffix(".part")
        shutil.copy2(cand.path, tmp)
        if sha256_file(tmp) != cand.sha256:
            tmp.unlink(missing_ok=True)
            raise OSError(f"{file_name} changed while it was being copied; nothing was imported.")
        os.replace(tmp, dest)
    db.add_runtime(sha256=cand.sha256, file_name=file_name, stored_path=str(dest), original_path=origin,
                   version=cand.version, signer=cand.signer, trust=cand.trust.value)
    return cand


def import_runtime(db: Database, path: str, verifier: Verifier | None = None) -> RuntimeCandidate:
    file_name = os.path.basename(path).lower()
    if file_name not in SUPPORTED_RUNTIMES:
        raise ValueError(f"AKENO imports only {', '.join(SUPPORTED_RUNTIMES)}; got {os.path.basename(path)}.")
    return import_candidate(db, evaluate(path, file_name, "manual", verifier))


def best_runtime(db: Database, file_name: str = NR_RUNTIME, allow_unverified: bool = False) -> dict | None:
    """Imported runtime to install: newest verified one (unverified only when explicitly allowed, e.g. tests)."""
    rows = [r for r in db.runtimes(file_name) if r["stored_path"] and os.path.isfile(r["stored_path"])]
    rows.sort(key=lambda r: _version_key(r.get("version")), reverse=True)
    for r in rows:
        # No re-hash here (this runs on every UI refresh): the installer re-hashes every source file
        # against this recorded SHA-256 before it writes anything.
        if r["trust"] == TrustState.VERIFIED.value or (allow_unverified and r["trust"] == TrustState.UNVERIFIED.value):
            return r
    return None
