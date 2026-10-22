"""What each RTX generation can actually do, and what AKENO has actually verified on this PC.

Three separate questions are kept apart on purpose, so a merely *selectable* configuration is never shown
as *working*:

1. **Feature matrix** — which DLSS technologies NVIDIA ships officially per generation (Super Resolution,
   Ray Reconstruction, Frame Generation, Multi Frame Generation) versus DLSS 5 Neural Rendering, which only
   community mods drive through an undocumented NVIDIA driver feature.
2. **Runtime verdict** — whether the NVIDIA runtime version on this PC is *documented* to create the neural
   feature on this GPU generation (evidence: ``nr_runtimes`` in ``data/compatibility.json``).
3. **Verification** — whether a game on this PC has *actually* reported the neural feature running, read
   from the logs the backends write next to the game. Nothing else counts as verified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from akeno.core.models import GpuInfo, HardwareSupport, Vendor
from akeno.hardware.gpu_db import nr_hardware_support
from akeno.i18n import msg

GENERATIONS = ("RTX 50", "RTX 40", "RTX 30", "RTX 20")

# What each technology is (shown in the UI so the four are never confused).
FEATURES = [
    ("sr", "DLSS Super Resolution",
     "Renders fewer pixels and upscales them with AI. Raises FPS. All RTX GPUs (RTX 20 and newer)."),
    ("rr", "DLSS Ray Reconstruction",
     "AI denoiser for ray-traced lighting. Improves image quality in ray-traced games. All RTX GPUs."),
    ("fg", "DLSS Frame Generation",
     "Generates one extra frame between two rendered frames. Raises displayed FPS, adds some latency. "
     "Officially RTX 40 and RTX 50."),
    ("mfg", "DLSS Multi Frame Generation",
     "Generates several frames per rendered frame. Officially RTX 50 only."),
    ("nr", "DLSS 5 Neural Rendering",
     "A neural model that changes the picture itself (lighting, materials, detail) on every frame it runs. "
     "It costs GPU time and lowers FPS; it does not raise it. AKENO manages community mods that drive it."),
]

# Official NVIDIA availability per generation (documented NVIDIA product support, DLSS 4 era).
OFFICIAL = {
    "RTX 50": {"sr": True, "rr": True, "fg": True, "mfg": True},
    "RTX 40": {"sr": True, "rr": True, "fg": True, "mfg": False},
    "RTX 30": {"sr": True, "rr": True, "fg": False, "mfg": False},
    "RTX 20": {"sr": True, "rr": True, "fg": False, "mfg": False},
}

# Used when the compatibility database carries no "nr_runtimes" section (older or downloaded copies).
DEFAULT_NR_RUNTIMES: list[dict[str, Any]] = [{
    "version": "310.8.0.0",
    "signed": True,
    "works_on": ["RTX 50"],
    "fails_on": ["RTX 20", "RTX 30", "RTX 40"],
    "failure": "feature 18 create failed with 0xbad00001",
    "evidence": {"source": "DLSS5-Feeder README (RTX 50 only), issue #131",
                 "url": "https://github.com/jlrouzies-fr/DLSS5-Feeder/issues/131", "date": "2026-10"},
}]


def _version_matches(version: str | None, pattern: str) -> bool:
    """'310.8.*' matches 310.8 and 310.8.x.y; a pattern without '*' matches that version (trailing .0 ignored)."""
    if not version or not pattern:
        return False

    def norm(v: str) -> str:
        parts = v.split(".")
        while len(parts) > 1 and parts[-1] == "0":
            parts.pop()
        return ".".join(parts)
    if pattern.endswith("*"):
        prefix = pattern[:-1].rstrip(".")
        return version == prefix or version.startswith(prefix + ".")
    return norm(version) == norm(pattern)


@dataclass
class RuntimeVerdict:
    state: str              # works | fails | unknown | none
    text: str
    evidence: dict[str, Any] | None = None


def runtime_verdict(version: str | None, generation: str | None, data: list[dict[str, Any]] | None = None,
                    present: bool = True, build_sha256: str | None = None) -> RuntimeVerdict:
    """Documented behaviour of NVIDIA's signed runtime ``version`` on ``generation``."""
    if not present:
        return RuntimeVerdict("none", msg("No NVIDIA Neural Rendering runtime is available yet."))
    if not generation:
        return RuntimeVerdict("unknown", msg("GPU generation unknown."))
    for entry in data if data is not None else DEFAULT_NR_RUNTIMES:
        if not _version_matches(version, entry.get("version", "")):
            continue
        ev = entry.get("evidence")
        hashes = entry.get("sha256") or []
        if isinstance(hashes, str):
            hashes = [hashes]
        if not build_sha256 or build_sha256 not in hashes:
            return RuntimeVerdict("unknown", msg("A version-level community report exists for {v} on {g}; this exact runtime hash has not been verified.", v=version, g=generation), ev)
        if generation in entry.get("works_on", []):
            return RuntimeVerdict("works", msg("Runtime {v} is reported to create the neural feature on {g}.",
                                               v=version, g=generation), ev)
        if generation in entry.get("fails_on", []):
            return RuntimeVerdict("fails", msg("Runtime {v} is reported to refuse the neural feature on {g} ({f}).",
                                               v=version, g=generation, f=entry.get("failure", "error")), ev)
    return RuntimeVerdict("unknown", msg("No report yet for runtime {v} on {g}.", v=version or "?", g=generation))


# -- verification from game logs -----------------------------------------------------------------------
LOG_FILES = ("OptiScaler.log", "dlss5-feed.log", "ReShade.log", os.path.join("host64", "dlss5-feed-host.log"),
             os.path.join("host64", "ReShade.log"))
SUCCESS_MARKERS = ( "inline feature 18 evaluation succeeded", "feature 18 created")
FAILURE_MARKERS = ("feature 18 create failed", "0xbad00001", "neural model not created",
                   "the driver answered the ngx probe", "not a dlss-nr fork")
MAX_LOG_BYTES = 8 * 1024 * 1024


@dataclass
class Verification:
    state: str              # verified | failed | none
    text: str
    file: str | None = None
    line: str | None = None
    when: str | None = None


def verify_from_logs(target_dir: str | None, since: float | None = None) -> Verification:
    """Read the backend logs beside the game. Only lines written after ``since`` (install time) count.

    The last relevant line wins, so a later success after an earlier failure (or the reverse) is reported
    as it is now."""
    if not target_dir or not os.path.isdir(target_dir):
        return Verification("none", msg("No game folder to check."))
    latest: tuple[float, str, str, str] | None = None     # (file mtime, kind, file, line)
    for rel in LOG_FILES:
        path = os.path.join(target_dir, rel)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if since and st.st_mtime < since:
            continue
        try:
            with open(path, "rb") as f:
                if st.st_size > MAX_LOG_BYTES:
                    f.seek(st.st_size - MAX_LOG_BYTES)
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        last = None
        for line in text.splitlines():
            low = line.lower()
            if any(m in low for m in FAILURE_MARKERS):          # failures win on a line that mentions both
                last = ("failed", line)
            elif any(m in low for m in SUCCESS_MARKERS):
                last = ("running" if "evaluation succeeded" in low else "initialized", line)
        if last and (latest is None or st.st_mtime >= latest[0]):
            latest = (st.st_mtime, last[0], rel, last[1].strip()[:240])
    if latest is None:
        return Verification("none", msg("Not verified yet: start the game once with the mod installed, then check again."))
    mtime, kind, rel, line = latest
    when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    if kind in ("running", "initialized"):
        return Verification(kind, msg("Backend evidence ({stage}) from {f}; current installation attribution is still required.", stage=kind, f=rel),
                            rel, line, when)
    return Verification("failed", msg("Not running on this PC: {f} reports a neural feature failure.", f=rel),
                        rel, line, when)


# -- per-GPU report -----------------------------------------------------------------------------------------
@dataclass
class GpuSupport:
    gpu_name: str
    generation: str | None
    hardware: HardwareSupport
    official: str
    community: str
    community_state: str     # ok | warn | fail | unknown
    backends: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    runtime: RuntimeVerdict | None = None


def gpu_support(gpu: GpuInfo | None, runtime_version: str | None, runtime_present: bool,
                nr_runtimes: list[dict[str, Any]] | None = None) -> GpuSupport:
    hw = nr_hardware_support(gpu)
    gen = gpu.generation if gpu else None
    name = gpu.name if gpu else msg("No GPU")
    official = msg("Not an official NVIDIA integration: community mods drive an undocumented driver feature.")
    rv = runtime_verdict(runtime_version, gen, nr_runtimes, runtime_present)
    rep = GpuSupport(name, gen, hw.level, official, "", "unknown", runtime=rv)
    if gpu is None or hw.level == HardwareSupport.UNSUPPORTED or gpu.vendor != Vendor.NVIDIA:
        rep.community = msg("Not possible: {r}", r=hw.reason)
        rep.community_state = "fail"
        return rep
    rep.backends = [msg("OptiScaler DLSS 5 (games with DLSS, FSR 2+ or XeSS)"),
                    msg("DLSS5-Feeder + ReShade (games without an upscaler; partly manual)")]
    if gen == "RTX 50":
        rep.community = msg("Community-verified with NVIDIA's signed runtime (RTX 5090 reports, drivers 616.64 and 617.14).")
        rep.community_state = "ok"
    elif gen in ("RTX 20", "RTX 30", "RTX 40"):
        rep.community = msg("Experimental on {g}: older-runtime failures and other-game successes are reported. New signed builds require their own evidence.", g=gen)
        rep.community_state = "warn"
        rep.limitations.append(msg("Results depend on the exact signed runtime hash, driver and backend; verify neural creation and evaluation locally."))
    else:
        rep.community = msg("Unknown NVIDIA GPU generation.")
    if gpu and gpu.driver_version:
        try:
            driver = tuple(int(x) for x in gpu.driver_version.split("."))
        except ValueError:
            driver = ()
        if driver and driver < (616, 56):
            rep.limitations.append(msg("Driver {v} predates the community test matrix starting at 616.56. Check NVIDIA's official update page; no driver is updated automatically.", v=gpu.driver_version))
    if rv.state == "fails":
        rep.community_state = "fail"
        rep.limitations.append(rv.text)
    vram = (gpu.vram_bytes or 0) / 1024 ** 3
    if vram and vram < 10:
        rep.recommendations.append(msg("{n} GB VRAM: keep one model pass and a working resolution of 67% or lower.",
                                       n=f"{vram:.0f}"))
    elif vram:
        rep.recommendations.append(msg("{n} GB VRAM detected. Memory fit depends on resolution, game and model; measure before increasing quality.",
                                       n=f"{vram:.0f}"))
    rep.recommendations.append(msg("Neural Rendering costs FPS: use the Performance profile first and measure with a "
                                   "benchmark before raising quality."))
    return rep
