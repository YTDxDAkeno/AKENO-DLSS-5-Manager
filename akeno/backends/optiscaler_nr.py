"""Adapter for OptiScaler forks with DLSS 5 Neural Rendering (``[DlssNr]`` section).

Installation logic is AKENO's own, modelled on the documented behaviour of the upstream installer
(Skynizz/optiscaler-dlss5 ``deploy/install.py``): OptiScaler is loaded through a free proxy DLL name
(``winmm.dll`` by default, ``OptiScaler.asi`` beside an ASI loader), ``nvngx.dll_dlssnr.dll`` (the
open-source forwarder from the package) and NVIDIA's ``nvngx_dlssnr.dll`` (supplied by the user's own
driver, never by AKENO) sit beside it, and ``OptiScaler.ini`` is written from the shipped template with
only the documented keys changed. Nothing that AKENO did not put there is ever overwritten.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from akeno.backends import ini
from akeno.backends.base import (BackendAdapter, BackendContext, FileOp, InstallPlan, ParamSpec, ProfileDef)
from akeno.backends.runtimes import NR_RUNTIME
from akeno.core.models import Finding, Game, GraphicsApi, Severity, Vendor
from akeno.i18n import msg
from akeno.security.catalog import Package

PROXY_ORDER = ("winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll", "dxgi.dll")
FORWARDER = "nvngx.dll_dlssnr.dll"
MARKER = "akeno-install.json"

UPSCALER_KEY = {
    GraphicsApi.D3D12: ("Upscalers", "Dx12Upscaler", "dlss"),
    # Upstream INI: "dlss_12 (dx11on12, the only option that can run DLSS Neural Rendering as well)".
    GraphicsApi.D3D11: ("Upscalers", "Dx11Upscaler", "dlss_12"),
    GraphicsApi.VULKAN: ("Upscalers", "VulkanUpscaler", "dlss"),
}

REF_CONTROL = ("Upstream measurement (Skynizz OptiScaler DLSS 5 1.3.0 README): Control Resonant, RTX 4070, "
               "1440p, DLSS Balanced — vanilla DLSS 5: 34 fps")


class OptiScalerNrAdapter(BackendAdapter):
    id = "optiscaler_nr"
    name = "OptiScaler DLSS 5 Neural Rendering"
    short = "OptiScaler NR"
    experimental = True
    supported_apis = (GraphicsApi.D3D12, GraphicsApi.D3D11, GraphicsApi.VULKAN)
    supported_arch = ("x64",)
    description = ("Runs NVIDIA's DLSS 5 Neural Rendering model on the frames OptiScaler already handles. "
                   "Needs a game with an upscaler OptiScaler can hook (DLSS, FSR 2+ or XeSS).")
    homepage = "https://github.com/Skynizz/optiscaler-dlss5"
    license = "GPL-3.0"

    # -- settings ------------------------------------------------------------------------------
    def param_specs(self, game: Game | None = None) -> list[ParamSpec]:
        specs = [
            ParamSpec("DlssNr.Enabled", "Neural Rendering", "bool", True,
                      "Turns the DLSS 5 Neural Rendering pass on or off (OptiScaler [DlssNr] Enabled)."),
            ParamSpec("DlssNr.PreSr", "Run before upscaling (pre-SR)", "bool", True,
                      "Runs the model on the render-resolution image before DLSS Super Resolution upscales it. "
                      "Much cheaper; D3D12 games that use DLSS Super Resolution only.", apis=["d3d12"]),
            ParamSpec("DlssNr.WorkingScale", "Neural model working resolution", "float", 0.67,
                      "Fraction of the frame the model works at. The frame itself is never reduced; cost falls "
                      "with the square of this value. Above 1.0 the model supersamples (D3D12 and Vulkan).",
                      minimum=0.25, maximum=2.0, step=0.01),
            ParamSpec("DlssNr.JbuUpsample", "Edge-aware enlargement", "bool", True,
                      "Joint bilateral enlargement for a working resolution below 100%. No effect at 100%+."),
            ParamSpec("DlssNr.CacheEnabled", "Temporal edit cache", "bool", True,
                      "Runs the model one frame in N and carries its correction along motion vectors in "
                      "between (D3D12 only).", apis=["d3d12"]),
            ParamSpec("DlssNr.CacheInterval", "Model runs every N frames", "int", 1,
                      "At most this many frames between model runs (1-16). Higher is faster, with less "
                      "fresh detail in motion.", minimum=1, maximum=16, step=1, apis=["d3d12"]),
            ParamSpec("DlssNr.TransferStrength", "Transfer strength", "float", 1.0,
                      "How far the frame moves toward the model's picture: 0 = upscaler output unchanged, "
                      "1 = the model's picture.", minimum=0.0, maximum=1.5, step=0.05),
            ParamSpec("DlssNr.Style", "Model style", "choice", 0,
                      "0 Default (strongest), 1 Natural (closer to the game's own tones), 2 Cinematic. "
                      "Rebuilds the model when changed.", choices=[(0, "Default"), (1, "Natural"), (2, "Cinematic")],
                      requires_restart=True),
            ParamSpec("DlssNr.Passes", "Model passes", "int", 1,
                      "Each extra pass costs a full model run and its own video memory.",
                      minimum=1, maximum=4, step=1),
        ]
        if game is None:
            return specs
        return [s for s in specs if s.applies_to(game.api)]

    def profiles(self, game: Game | None = None) -> list[ProfileDef]:
        api = game.api if game else GraphicsApi.D3D12
        disabled = ProfileDef("disabled", "Disabled", "Neural Rendering off; OptiScaler stays installed.",
                              {"DlssNr.Enabled": False}, "No visual change, no cost.")
        if api == GraphicsApi.D3D12:
            return [
                disabled,
                ProfileDef("performance", "Performance", "Pre-SR, model every other frame.",
                           {"DlssNr.Enabled": True, "DlssNr.PreSr": True, "DlssNr.CacheEnabled": True,
                            "DlssNr.CacheInterval": 2, "DlssNr.WorkingScale": 0.67, "DlssNr.JbuUpsample": True},
                           "Lowest cost; nearly all of the detail.", REF_CONTROL + "; this preset: 55 fps."),
                ProfileDef("balanced", "Balanced", "After the upscaler at 67% working resolution, every other frame.",
                           {"DlssNr.Enabled": True, "DlssNr.PreSr": False, "DlssNr.WorkingScale": 0.67,
                            "DlssNr.JbuUpsample": True, "DlssNr.CacheEnabled": True, "DlssNr.CacheInterval": 2},
                           "For games where pre-SR does not work.", REF_CONTROL + "; this preset: 48 fps."),
                ProfileDef("quality", "Quality", "Pre-SR, model every frame (upstream default).",
                           {"DlssNr.Enabled": True, "DlssNr.PreSr": True, "DlssNr.CacheEnabled": True,
                            "DlssNr.CacheInterval": 1, "DlssNr.WorkingScale": 0.67, "DlssNr.JbuUpsample": True},
                           "All of the detail with less flicker; higher cost.", REF_CONTROL + "; this preset: 47 fps."),
            ]
        # D3D11 / Vulkan: no pre-SR and no edit cache; only the working resolution trades cost for detail.
        return [
            disabled,
            ProfileDef("performance", "Performance", "Model at 50% working resolution.",
                       {"DlssNr.Enabled": True, "DlssNr.WorkingScale": 0.5, "DlssNr.JbuUpsample": True},
                       "Cost falls with the square of the working resolution (about a quarter of 100%)."),
            ProfileDef("balanced", "Balanced", "Model at 67% working resolution.",
                       {"DlssNr.Enabled": True, "DlssNr.WorkingScale": 0.67, "DlssNr.JbuUpsample": True},
                       "About 45% of the cost of 100% with most of the detail."),
            ProfileDef("quality", "Quality", "Model at 100% working resolution.",
                       {"DlssNr.Enabled": True, "DlssNr.WorkingScale": 1.0},
                       "Full detail at full cost."),
        ]

    def default_params(self, game: Game | None) -> dict[str, Any]:
        q = next(p for p in self.profiles(game) if p.id == "quality")
        return dict(q.params)

    # -- analysis ------------------------------------------------------------------------------
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
                                  msg("{generation}: the neural model may refuse to start with your driver's signed "
                                      "runtime. Treat results as experimental.", generation=gpu.generation),
                                  "jlrouzies-fr/DLSS5-Feeder README; Skynizz/optiscaler-dlss5 README (RTX 4070 tests)"))
        if not game.exe_path:
            f.append(self.finding(B, "no_exe", "The game executable is unknown. Select it in the Game Library."))
            return f
        if game.architecture and game.architecture not in self.supported_arch:
            f.append(self.finding(B, "arch", msg("{architecture} games are not supported by OptiScaler (64-bit only).",
                                                 architecture=game.architecture)))
        if game.api == GraphicsApi.UNKNOWN:
            f.append(self.finding(B, "api_unknown", "The game's graphics API could not be determined. Select it in "
                                  "the Game Library so AKENO can configure OptiScaler correctly."))
        elif game.api not in self.supported_apis:
            f.append(self.finding(B, "api_unsupported", msg("{label} is not supported by OptiScaler (DirectX 11, "
                                                            "DirectX 12 and Vulkan only).", label=game.api.label)))
        elif game.api == GraphicsApi.D3D11:
            f.append(self.finding(W, "d3d11_bridge", "DirectX 11 runs the model through OptiScaler's D3D11-on-12 "
                                  "bridge (dlss_12), which upstream says costs up to ~10% performance."))
        kinds = {m.kind for m in game.existing_mods}
        has_feeder = "dlss5_feeder" in kinds
        if not game.upscaler_inputs and not has_feeder:
            f.append(self.finding(B, "no_upscaler",
                                  "No DLSS, FSR 2+ or XeSS files were found in this game. OptiScaler can only "
                                  "replace an upscaler the game already uses (DLSS5-Feeder covers games without one)."))
        elif "dlss" not in game.upscaler_inputs and not has_feeder:
            f.append(self.finding(W, "no_dlss_input", "The game has no nvngx_dlss.dll. OptiScaler's DLSS output and "
                                  "pre-SR need NVIDIA's DLSS runtime; you may have to supply nvngx_dlss.dll."))
        managed = ctx.managed_files
        for m in game.existing_mods:
            low = m.name.lower()
            if low in managed:
                continue
            if m.kind == "optiscaler" or m.kind == "optiscaler_config":
                f.append(self.finding(B, "foreign_optiscaler",
                                      msg("Another OptiScaler installation was found ({name}). Remove it with the "
                                          "tool that installed it before AKENO manages OptiScaler here.", name=m.name)))
            elif m.kind == "neural_consumer" and m.name not in ctx.allow_disable:
                f.append(self.finding(B, "second_consumer",
                                      msg("{name} is another DLSS 5 neural add-on. Two neural consumers must never "
                                          "run together. AKENO can rename it to disable it (restored on uninstall).",
                                          name=m.name), m.path))
            elif m.kind == "unknown_proxy":
                f.append(self.finding(B if ctx.strict_conflicts else W, "unknown_proxy",
                                      msg("{name} is an unknown graphics hook. Two interceptors may conflict, so "
                                          "AKENO stops here (Settings → Safety defaults controls this).",
                                          name=m.name), m.path))
            elif m.kind == "nvidia_nr_runtime":
                f.append(self.finding(I, "runtime_present", msg("{name} is already in the game folder and will be "
                                                                "left as it is (it was not installed by AKENO).",
                                                                name=m.name)))
            elif m.kind == "dlssg_to_fsr3":
                f.append(self.finding(W, "fg_mod", "A DLSS-FG to FSR3 mod is installed; OptiScaler bundles its own "
                                      "frame generation handling and the two may conflict."))
        if self._pick_proxy(game, managed, ctx.loaded_modules) is None:
            f.append(self.finding(B, "no_proxy_name", "No free supported proxy has an evidenced load path. Inspect game imports or the current process modules; a free filename alone is insufficient."))
        runtime_present = any(m.kind == "nvidia_nr_runtime" for m in game.existing_mods)
        if not runtime_present and ctx.runtime_lookup(NR_RUNTIME) is None:
            f.append(self.finding(B, "runtime_missing",
                                  "Cannot install yet: NVIDIA's nvngx_dlssnr.dll is not imported. Use 'Resolve "
                                  "Dependencies' on the Secure Install page to take it from your NVIDIA driver (AKENO "
                                  "does not redistribute NVIDIA files)."))
        return f

    def _pick_proxy(self, game: Game, managed: set[str], observed=()) -> str | None:
        from akeno.pipeline.proxy import resolve_proxy
        return resolve_proxy(game,managed,supported=PROXY_ORDER,observed=observed)

    def required_package_files(self, game: Game, package: Package) -> dict[str, str | None]:
        return {n: package.files.get(n) for n in ("OptiScaler.dll", FORWARDER, "OptiScaler.ini")}

    def dependency_specs(self, game: Game) -> list[tuple[str, bool]]:
        # nvngx_dlss.dll: the game's own copy is used; without one OptiScaler's DLSS output and pre-SR need it.
        return [("package", True), ("nr_runtime", True), ("dlss_runtime", "dlss" not in game.upscaler_inputs)]

    def config_text(self, template: str, game: Game, params: dict[str, Any]) -> str:
        values = ini.split_params(params)
        up = UPSCALER_KEY.get(game.api)
        if up:
            values.setdefault(up[0], {})[up[1]] = up[2]
        # Keep upstream logging on so failures can be diagnosed.
        values.setdefault("Log", {}).update({"LogToFile": True, "LogLevel": 2})
        if game.api != GraphicsApi.D3D12:
            for k in ("PreSr", "CacheEnabled", "CacheInterval"):
                values.get("DlssNr", {}).pop(k, None)
        return ini.set_values(template, values)

    def plan(self, game: Game, package: Package, params: dict[str, Any], ctx: BackendContext) -> InstallPlan:
        if ctx.staging_dir is None or not game.target_dir:
            raise ValueError("plan() needs a staged package and a game executable")
        proxy = self._pick_proxy(game, ctx.managed_files, ctx.loaded_modules)
        if proxy is None:
            raise ValueError("no free proxy DLL name")
        staged = Path(ctx.staging_dir)
        template = (staged / "OptiScaler.ini").read_text(encoding="utf-8-sig")
        cfg = self.config_text(template, game, params)
        plan = InstallPlan(self.id, package, game.target_dir, params=dict(params))
        plan.ops.append(FileOp(proxy, source=staged / "OptiScaler.dll",
                               description=msg("OptiScaler (loaded as {proxy})", proxy=proxy),
                               expected_sha256=package.files.get("OptiScaler.dll")))
        plan.ops.append(FileOp(FORWARDER, source=staged / FORWARDER, description="NGX caller forwarder (open source)",
                               expected_sha256=package.files.get(FORWARDER)))
        plan.ops.append(FileOp("OptiScaler.ini", content=cfg.encode("utf-8"), kind="config",
                               description="OptiScaler configuration"))
        foreign_runtime = any(m.kind == "nvidia_nr_runtime" and m.name.lower() not in ctx.managed_files
                              for m in game.existing_mods)
        if not foreign_runtime:
            rt = ctx.runtime_lookup(NR_RUNTIME)
            if rt is None:
                raise ValueError("nvngx_dlssnr.dll is required")
            plan.ops.append(FileOp(NR_RUNTIME, source=Path(rt["stored_path"]), expected_sha256=rt["sha256"],
                                   description=msg("NVIDIA Neural Rendering runtime {v} (your copy)",
                                                   v=rt.get('version') or '')))
        for name in ctx.allow_disable:
            plan.rename_away.append(name)
        plan.ops.append(FileOp(MARKER, content=json.dumps({
            "managed_by": "AKENO DLSS 5 Manager", "backend": self.id, "package": package.id,
            "proxy": proxy, "files": [o.rel_path for o in plan.ops]}, indent=2).encode(), kind="marker",
            description="AKENO installation marker"))
        plan.summary = [msg("Install {name} {version} into {target_dir}",
                            name=package.name, version=package.version, target_dir=game.target_dir),
                        msg("OptiScaler loads as {proxy}", proxy=proxy),
                        msg("Upscaler setting: {v}",
                            v='/'.join(UPSCALER_KEY[game.api][1:]) if game.api in UPSCALER_KEY else '-')]
        if plan.rename_away:
            plan.summary.append("Disable (rename): " + ", ".join(plan.rename_away))
        return plan

    def plan_configure(self, game: Game, package: Package | None, params: dict[str, Any], ctx: BackendContext,
                       current_config: str | None) -> InstallPlan | None:
        if current_config is None or not game.target_dir:
            return None
        cfg = self.config_text(current_config, game, params)
        plan = InstallPlan(self.id, package, game.target_dir, params=dict(params), kind="configure")
        plan.ops.append(FileOp("OptiScaler.ini", content=cfg.encode("utf-8"), kind="config",
                               description="OptiScaler configuration"))
        plan.summary = ["Update OptiScaler.ini with the selected profile"]
        return plan

    def validate(self, game: Game, installed: dict[str, str]) -> list[Finding]:
        out = []
        d = game.target_dir or ""
        proxies = [n for n in installed if n.lower() in PROXY_ORDER or n.lower() == "optiscaler.asi"]
        if not proxies:
            out.append(self.finding(Severity.BLOCKER, "validate_proxy", "OptiScaler DLL was not written."))
        for n in (FORWARDER, "OptiScaler.ini"):
            if not os.path.isfile(os.path.join(d, n)):
                out.append(self.finding(Severity.BLOCKER, "validate_missing", msg("{n} is missing after installation.",
                                                                                  n=n)))
        if not os.path.isfile(os.path.join(d, NR_RUNTIME)):
            out.append(self.finding(Severity.BLOCKER, "validate_runtime", "nvngx_dlssnr.dll is missing."))
        cfg = os.path.join(d, "OptiScaler.ini")
        if os.path.isfile(cfg):
            vals = ini.get_values(Path(cfg).read_text(encoding="utf-8-sig", errors="replace"))
            if "DlssNr" not in vals:
                out.append(self.finding(Severity.BLOCKER, "validate_ini", "OptiScaler.ini has no [DlssNr] section."))
        return out
