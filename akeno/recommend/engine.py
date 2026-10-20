"""Recommendation engine for DLSS 5 Neural Rendering profiles.

Order of evidence, strongest first:
  1. Measured benchmarks of this game on this GPU (per profile, plus a baseline without the mod).
  2. A measured baseline only: the expected frame rate per profile is *estimated* from the per-preset
     cost the upstream project published (ms per frame at 1440p), scaled by pixel count. Estimates are
     labelled as such and the recommendation is marked provisional.
  3. Hardware only: a provisional choice from GPU generation, VRAM and display resolution.

The recommendation can be "leave Neural Rendering disabled" — that is a valid outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from akeno.backends.base import BackendAdapter, ProfileDef
from akeno.benchmark.stats import consistency_label
from akeno.core.models import CompatibilityResult, CompatStatus, FrameStats, Game, GpuInfo, GraphicsApi, SystemInfo

# Upstream per-preset model cost (ms/frame), Skynizz/optiscaler-dlss5 README "Results":
# Control Resonant, RTX 4070, 2560x1440, DLSS Balanced.
REFERENCE_COST_MS = {"quality": 5.5, "balanced": 4.7, "performance": 2.9}
REFERENCE_PIXELS = 2560 * 1440
REFERENCE_NOTE = ("Estimated from the per-preset cost published by Skynizz/optiscaler-dlss5 (RTX 4070, 1440p, "
                  "Control Resonant), scaled to your resolution. Your GPU and game will differ.")
QUALITY_ORDER = ["quality", "balanced", "performance"]


@dataclass
class ProfileOutlook:
    profile_id: str
    label: str
    measured: FrameStats | None = None
    estimated_fps: float | None = None
    meets_target: bool | None = None
    consistency: str | None = None


@dataclass
class Recommendation:
    backend_id: str | None
    profile_id: str | None               # None = nothing to recommend; "disabled" = keep NR off
    profile_label: str
    working_scale: float | None          # documented backend parameter (DlssNr.WorkingScale)
    pre_sr: bool | None
    headline: str
    reasons: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    vram_note: str | None = None
    confidence: str = "low"
    provisional: bool = True
    basis: str = "hardware"              # measured | estimated | hardware | none
    outlooks: list[ProfileOutlook] = field(default_factory=list)
    baseline: FrameStats | None = None
    params: dict[str, Any] = field(default_factory=dict)


def _stats(s: dict[str, Any]) -> FrameStats:
    return FrameStats(**s["stats"])


def _relevant(sessions: list[dict[str, Any]], gpu: GpuInfo | None, resolution: str | None) -> list[dict[str, Any]]:
    out = []
    for s in sessions:
        if gpu and s.get("gpu") and s["gpu"] != gpu.name:
            continue
        if resolution and s.get("resolution") and s["resolution"] != resolution:
            continue
        out.append(s)
    return out


def recommend(game: Game | None, system: SystemInfo | None, gpu: GpuInfo | None, adapter: BackendAdapter | None,
              compat: CompatibilityResult | None, sessions: list[dict[str, Any]], target_fps: int) -> Recommendation:
    if game is None:
        return Recommendation(None, None, "—", None, None, "Select a game to get a recommendation.", basis="none")
    if adapter is None or compat is None:
        return Recommendation(None, None, "—", None, None, "No backend is available for this game.", basis="none")
    if compat.status in (CompatStatus.BLOCKED, CompatStatus.INCOMPATIBLE):
        reason = compat.blockers[0].message if compat.blockers else "Not compatible."
        return Recommendation(adapter.id, "disabled", "Disabled", None, None,
                              "DLSS 5 Neural Rendering is not recommended for this game.",
                              [reason], confidence="high", provisional=False, basis="none")
    profiles = {p.id: p for p in adapter.profiles(game)}
    if not profiles:
        return Recommendation(adapter.id, None, "—", None, None,
                              f"{adapter.short} has no settings AKENO can configure; use its own overlay.",
                              ["Profiles are not available for this backend."], basis="none")

    display = system.primary_display if system else None
    resolution = f"{display.width}x{display.height}" if display and display.width else None
    pixels = (display.width * display.height) if display and display.width else None
    rel = _relevant([s for s in sessions if s.get("game_id") == game.id], gpu, resolution)
    by_label: dict[str, dict[str, Any]] = {}
    for s in sorted(rel, key=lambda s: s.get("id") or 0):
        by_label[s["label"]] = s                      # newest session per label wins
    baseline = _stats(by_label["baseline"]) if "baseline" in by_label else None

    outlooks: list[ProfileOutlook] = []
    for pid in QUALITY_ORDER:
        if pid not in profiles:
            continue
        o = ProfileOutlook(pid, profiles[pid].label)
        if pid in by_label:
            o.measured = _stats(by_label[pid])
            o.meets_target = o.measured.avg_fps >= target_fps
            o.consistency = consistency_label(o.measured)
        elif baseline and game.api == GraphicsApi.D3D12 and pid in REFERENCE_COST_MS and pixels:
            cost = REFERENCE_COST_MS[pid] * pixels / REFERENCE_PIXELS
            o.estimated_fps = round(1000.0 / (baseline.avg_frametime_ms + cost), 1)
            o.meets_target = o.estimated_fps >= target_fps
        outlooks.append(o)

    rec = Recommendation(adapter.id, None, "", None, None, "", outlooks=outlooks, baseline=baseline)
    vram = gpu.vram_gb if gpu else None
    if vram is not None:
        if vram < 8:
            rec.vram_note = (f"{vram:.0f} GB VRAM: keep a single model pass and a working resolution of 67% or "
                             "lower; extra passes each need their own video memory.")
        else:
            rec.vram_note = f"{vram:.0f} GB VRAM: enough for one model pass at any working resolution."
    else:
        rec.vram_note = "VRAM size unknown."

    measured = [o for o in outlooks if o.measured]
    if measured:
        rec.basis, rec.provisional = "measured", False
        rec.confidence = "high" if compat.status == CompatStatus.CONFIRMED else "medium"
        good = [o for o in measured if o.meets_target and o.consistency in ("good", "fair")]
        if good:
            best = min(good, key=lambda o: QUALITY_ORDER.index(o.profile_id))
            _pick(rec, profiles[best.profile_id])
            rec.headline = f"{best.label} is recommended for this game."
            rec.reasons.append(f"Measured {best.measured.avg_fps:.0f} FPS average (target {target_fps}) with "
                               f"{best.consistency} frame-time consistency (1% low {best.measured.low_1pct_fps:.0f} FPS).")
            untested = [o.label for o in outlooks if not o.measured and QUALITY_ORDER.index(o.profile_id)
                        < QUALITY_ORDER.index(best.profile_id)]
            if untested:
                rec.reasons.append("Not measured yet: " + ", ".join(untested) + ". Benchmark them to see if they fit.")
        elif [o for o in outlooks if not o.measured and QUALITY_ORDER.index(o.profile_id) >
              min(QUALITY_ORDER.index(m.profile_id) for m in measured)]:
            cheaper = [o for o in outlooks if not o.measured]
            nxt = max(cheaper, key=lambda o: QUALITY_ORDER.index(o.profile_id))
            _pick(rec, profiles[nxt.profile_id])
            rec.provisional, rec.confidence = True, "low"
            rec.headline = f"Try {nxt.label}: the measured profiles missed your target."
            rec.reasons.append("Measured: " + ", ".join(f"{o.label} {o.measured.avg_fps:.0f} FPS" for o in measured) +
                               f" (target {target_fps}). Benchmark {nxt.label} next.")
        else:
            if "disabled" in profiles:
                _pick(rec, profiles["disabled"])
            rec.profile_id, rec.profile_label = "disabled", "Disabled"
            rec.headline = "Neural Rendering is not recommended: the measured performance impact is too large."
            fastest = max(measured, key=lambda o: o.measured.avg_fps)
            rec.reasons.append(f"Best measured profile ({fastest.label}) reached {fastest.measured.avg_fps:.0f} FPS, "
                               f"below your {target_fps} FPS target or with poor frame-time consistency.")
    elif baseline:
        rec.basis, rec.provisional, rec.confidence = "estimated", True, "low"
        est = [o for o in outlooks if o.estimated_fps is not None]
        fitting = [o for o in est if o.meets_target]
        if est and fitting:
            best = min(fitting, key=lambda o: QUALITY_ORDER.index(o.profile_id))
            _pick(rec, profiles[best.profile_id])
            rec.headline = f"{best.label} is provisionally recommended."
            rec.reasons.append(f"Your measured baseline is {baseline.avg_fps:.0f} FPS; {best.label} is estimated "
                               f"at ~{best.estimated_fps:.0f} FPS (target {target_fps}).")
            rec.reasons.append(REFERENCE_NOTE)
        elif est:
            if "disabled" in profiles:
                _pick(rec, profiles["disabled"])
            rec.profile_id, rec.profile_label = "disabled", "Disabled"
            rec.headline = "Neural Rendering would probably drop below your FPS target."
            rec.reasons.append(f"Baseline {baseline.avg_fps:.0f} FPS; even Performance is estimated at "
                               f"~{max(o.estimated_fps for o in est):.0f} FPS (target {target_fps}).")
            rec.reasons.append(REFERENCE_NOTE)
        else:
            _hardware_pick(rec, profiles, gpu, pixels)
            rec.reasons.append("No published cost reference exists for this graphics API, so AKENO cannot estimate "
                               "FPS. Benchmark a profile to replace this guess with measurements.")
    else:
        _hardware_pick(rec, profiles, gpu, pixels)
        rec.reasons.append("Not measured yet — run a baseline benchmark and then a profile benchmark to confirm.")

    if compat.status == CompatStatus.INSUFFICIENT:
        rec.reasons.append("Compatibility cannot be confirmed for this game and GPU.")
        rec.confidence = "low"
    elif compat.status == CompatStatus.EXPERIMENTAL and rec.confidence == "high":
        rec.confidence = "medium"
    if rec.profile_id and rec.profile_id != "disabled":
        p = profiles[rec.profile_id]
        rec.tradeoffs.append(p.tradeoff)
        rec.tradeoffs.append("Neural Rendering adds work to every frame it runs on: it changes image character, "
                             "it does not raise FPS on its own.")
    return rec


def _pick(rec: Recommendation, p: ProfileDef) -> None:
    rec.profile_id = p.id
    rec.profile_label = p.label
    rec.params = dict(p.params)
    rec.working_scale = p.params.get("DlssNr.WorkingScale")
    rec.pre_sr = p.params.get("DlssNr.PreSr")


def _hardware_pick(rec: Recommendation, profiles: dict[str, ProfileDef], gpu: GpuInfo | None, pixels: int | None) -> None:
    rec.basis, rec.provisional, rec.confidence = "hardware", True, "low"
    four_k = bool(pixels and pixels >= 3840 * 2160 * 0.9)
    gen = gpu.generation if gpu else None
    vram = gpu.vram_gb if gpu else None
    if gen == "RTX 50" and not four_k:
        choice, why = "quality", "RTX 50 GPUs have the most Tensor throughput of the supported generations."
    elif gen in ("RTX 50", "RTX 40") or (gen == "RTX 30" and not four_k and (vram or 0) >= 10):
        choice, why = "balanced", f"{gen} at your resolution: a middle setting is a safe starting point."
    else:
        choice, why = "performance", "Older GPUs, 4K displays and smaller VRAM benefit most from the cheapest preset."
    if choice not in profiles:
        choice = next(iter(p for p in QUALITY_ORDER if p in profiles))
    _pick(rec, profiles[choice])
    rec.headline = "Provisional recommendation based on your hardware."
    rec.reasons.append(f"Provisional: based on hardware only. {why}")
