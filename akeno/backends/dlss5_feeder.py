"""Adapter for DLSS5-Feeder (jlrouzies-fr/DLSS5-Feeder), a ReShade add-on that builds the DLSS request
games without DLSS never make, so a DLSS 5 neural add-on can process the frame.

AKENO installs only the feeder's own files from the pinned release (the ReShade add-on, the 32-bit
helper when needed, and its shader). It does not install ReShade, does not register Vulkan layers
(that needs elevation and a machine-wide registry change) and never runs the upstream PowerShell
installer. Those steps stay guided: AKENO checks for them and explains what is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from akeno.backends.base import BackendAdapter, BackendContext, FileOp, InstallPlan, ParamSpec, ProfileDef
from akeno.backends.runtimes import DLSS_RUNTIME, NR_RUNTIME
from akeno.core.models import Finding, Game, GraphicsApi, Severity, Vendor
from akeno.i18n import msg
from akeno.security.catalog import Package

MARKER = "akeno-install.json"
SHADER = "reshade-shaders/Shaders/DLSS5_Feed.fx"


class Dlss5FeederAdapter(BackendAdapter):
    id = "dlss5_feeder"
    name = "DLSS5-Feeder (ReShade add-on)"
    short = "DLSS5-Feeder"
    experimental = True
    supported_apis = (GraphicsApi.D3D12, GraphicsApi.D3D11, GraphicsApi.D3D9, GraphicsApi.OPENGL)
    supported_arch = ("x64", "x86")
    description = ("Feeds frames from games without DLSS into a DLSS evaluate so a DLSS 5 neural add-on "
                   "can run. Requires ReShade with add-on support and exactly one neural consumer.")
    homepage = "https://github.com/jlrouzies-fr/DLSS5-Feeder"
    license = "MIT"

    def param_specs(self, game: Game | None = None) -> list[ParamSpec]:
        return []   # configured in the ReShade overlay; AKENO does not invent settings for it

    def profiles(self, game: Game | None = None) -> list[ProfileDef]:
        return []

    def _addon_files(self, game: Game) -> list[str]:
        if game.architecture == "x86":
            return ["dlss5-feed.addon32", "host64/dlss5-feed-host64.exe"]
        return ["dlss5-feed.addon64"]

    def check(self, game: Game, ctx: BackendContext) -> list[Finding]:
        f: list[Finding] = []
        B, W, I = Severity.BLOCKER, Severity.WARNING, Severity.INFO
        gpu = ctx.gpu
        if gpu is None:
            f.append(self.finding(B, "gpu_unknown", "No GPU information is available. Run a hardware scan first."))
        elif gpu.vendor != Vendor.NVIDIA or gpu.has_tensor_cores is False:
            f.append(self.finding(B, "gpu_unsupported", msg("{name} cannot run DLSS (NVIDIA RTX GPU required).",
                                                            name=gpu.name)))
        elif gpu.generation in ("RTX 20", "RTX 30", "RTX 40"):
            f.append(self.finding(W, "gpu_experimental",
                                  "Upstream reports that NVIDIA's signed nvngx_dlssnr.dll creates the neural feature "
                                  "on RTX 50 only; on older GPUs DLSS runs but the neural pass may not.",
                                  "jlrouzies-fr/DLSS5-Feeder README, issue #131"))
        if not game.exe_path:
            f.append(self.finding(B, "no_exe", "The game executable is unknown. Select it in the Game Library."))
            return f
        if game.architecture not in self.supported_arch:
            f.append(self.finding(B, "arch", "The game's architecture could not be determined."))
        if game.api == GraphicsApi.VULKAN:
            f.append(self.finding(B, "vulkan_guided",
                                  "Vulkan games need the feeder's Vulkan layer registered machine-wide (administrator "
                                  "rights). AKENO does not do this automatically; follow the upstream README."))
        elif game.api == GraphicsApi.UNKNOWN:
            f.append(self.finding(B, "api_unknown", "Select the game's graphics API in the Game Library first."))
        if "dlss" in game.upscaler_inputs:
            f.append(self.finding(I, "has_dlss", "This game has its own DLSS. Upstream recommends a neural add-on "
                                  "that hooks the game's DLSS directly; the feeder is meant for games without it."))
        kinds = [m.kind for m in game.existing_mods]
        names = [m.name.lower() for m in game.existing_mods]
        if "reshade" not in kinds:
            f.append(self.finding(B, "reshade_missing",
                                  "ReShade with full add-on support was not found next to the game executable. "
                                  "Install it from reshade.me first (\"with full add-on support\")."))
        consumers = [m for m in game.existing_mods if m.kind == "neural_consumer"]
        opti_nr = "optiscaler_nr_forwarder" in kinds
        n_consumers = len(consumers) + (1 if opti_nr else 0)
        if n_consumers == 0:
            f.append(self.finding(B, "consumer_missing",
                                  "No DLSS 5 neural add-on found. Install exactly one: OptiScaler DLSS-NR (via AKENO), "
                                  "Deep Fried Chicken or renodx-dlss5."))
        elif n_consumers > 1:
            f.append(self.finding(B, "consumer_multiple", "More than one neural add-on is installed; upstream says "
                                  "they disable each other. Keep exactly one."))
        if "optiscaler" in kinds and not opti_nr:
            f.append(self.finding(B, "stock_optiscaler", "A stock OptiScaler is installed. It takes over the NGX calls "
                                  "the feeder makes and never runs a neural pass. Only a DLSS-NR fork is compatible."))
        if any(n.startswith("renodx-dlss.") or n == "renodx-dlss.addon64" for n in names):
            f.append(self.finding(B, "renodx_dlss", "renodx-dlss (ShortFuse) replaces the feeder; use one or the other."))
        for need in (NR_RUNTIME, DLSS_RUNTIME):
            present = need in names
            if not present and ctx.runtime_lookup(need) is None:
                f.append(self.finding(B, f"missing_{need}", msg("Cannot install: {need} was not found in the game "
                                                                "folder and no verified copy was imported. Import it "
                                                                "from your NVIDIA driver.", need=need)))
        for m in game.existing_mods:
            if m.kind == "unknown_proxy" and m.name.lower() not in ctx.managed_files:
                f.append(self.finding(B if ctx.strict_conflicts else W, "unknown_proxy",
                                      msg("{name} is an unknown graphics hook that may conflict with ReShade.",
                                          name=m.name), m.path))
        if any(m.kind == "dlss5_feeder" and m.name.lower() not in ctx.managed_files for m in game.existing_mods):
            f.append(self.finding(B, "foreign_feeder", "DLSS5-Feeder is already installed by another tool. Remove it "
                                  "with that tool first, or keep using it there."))
        return f

    def dependency_specs(self, game: Game) -> list[tuple[str, bool]]:
        return [("package", True), ("reshade", True), ("consumer", True), ("nr_runtime", True),
                ("dlss_runtime", True)]

    def required_package_files(self, game: Game, package: Package) -> dict[str, str | None]:
        files = self._addon_files(game) + [SHADER]
        return {n: package.files.get(n) for n in files}

    def plan(self, game: Game, package: Package, params: dict[str, Any], ctx: BackendContext) -> InstallPlan:
        if ctx.staging_dir is None or not game.target_dir:
            raise ValueError("plan() needs a staged package and a game executable")
        staged = Path(ctx.staging_dir)
        plan = InstallPlan(self.id, package, game.target_dir)
        for rel in self._addon_files(game) + [SHADER]:
            plan.ops.append(FileOp(rel, source=staged / rel, expected_sha256=package.files.get(rel),
                                   description="DLSS5-Feeder " + ("shader" if rel.endswith(".fx") else "add-on")))
        names = {m.name.lower() for m in game.existing_mods}
        for need in (NR_RUNTIME, DLSS_RUNTIME):
            if need in names and need not in ctx.managed_files:
                continue
            rt = ctx.runtime_lookup(need)
            if rt is None:
                raise ValueError(f"{need} is required")
            # 32-bit games: NGX runs in the 64-bit helper, which looks beside itself and beside the game exe.
            plan.ops.append(FileOp(need, source=Path(rt["stored_path"]), expected_sha256=rt["sha256"],
                                   description=msg("NVIDIA runtime {need} (your copy)", need=need)))
        plan.ops.append(FileOp(MARKER, content=json.dumps({
            "managed_by": "AKENO DLSS 5 Manager", "backend": self.id, "package": package.id,
            "files": [o.rel_path for o in plan.ops]}, indent=2).encode(), kind="marker",
            description="AKENO installation marker"))
        plan.summary = [msg("Install {name} {version} into {target_dir}",
                            name=package.name, version=package.version, target_dir=game.target_dir),
                        "Afterwards: open the ReShade overlay, enable a motion-vector provider (e.g. LumeniteFX) "
                        "and the DLSS5_Feed technique, as described in the upstream README."]
        return plan

    def validate(self, game: Game, installed: dict[str, str]) -> list[Finding]:
        out = []
        for rel in self._addon_files(game) + [SHADER]:
            if not (Path(game.target_dir or "") / rel).is_file():
                out.append(self.finding(Severity.BLOCKER, "validate_missing",
                                        msg("{rel} is missing after installation.", rel=rel)))
        return out
