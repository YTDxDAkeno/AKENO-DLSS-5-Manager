"""Configuration files written for a stack, and what each preset changes.

Presets map to keys the selected consumer version really reads:

* OptiScaler DLSS 5 (Skynizz) ``[DlssNr]``: the upstream README's presets -- Quality = pre-SR every frame
  (its installer default), Performance = pre-SR every other frame, Balanced = after the upscaler at 67% every
  other frame, Ultra = "Max quality" (after the upscaler, 100%, every frame).
* OptiScaler DLSSNR (Dagherbou) has no pre-SR or cache: presets change ``WorkingScale`` (model resolution;
  above 1.0 it supersamples on DX12/Vulkan).
* RenoDX DLSS 5 ``[RenoDX.DLSS5]``: ``NRResolutionScale`` (model resolution) and ``NRPasses``.
* DLSS5-Feeder ``dlss5-feed.cfg`` stays at 100% transport resolution (its README: lower the consumer's own
  resolution instead), with upscaling off so the frame keeps its size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from akeno.backends import ini

PRESETS = ("performance", "balanced", "quality", "ultra", "custom")
PRESET_LABELS = {"performance": "Performance", "balanced": "Balanced", "quality": "Quality", "ultra": "Ultra",
                 "custom": "Custom"}


@dataclass(frozen=True)
class Setting:
    key: str                 # section.key
    label: str
    kind: str                # bool | int | float | choice
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    help: str = ""

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.key.split(".", 1)[1]

    def coerce(self, value: Any) -> Any:
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if self.kind == "choice":
            allowed = [c for c, _ in self.choices]
            v = type(allowed[0])(value) if allowed else value
            if v not in allowed:
                raise ValueError(f"{self.label}: {value} is not one of the supported values.")
            return v
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.label}: {value!r} is not a number.") from exc
        if (self.minimum is not None and v < self.minimum) or (self.maximum is not None and v > self.maximum):
            raise ValueError(f"{self.label} must be between {self.minimum} and {self.maximum}.")
        return int(round(v)) if self.kind == "int" else round(v, 3)


# -- OptiScaler ------------------------------------------------------------------------------------
UPSCALER = {"d3d12": ("Dx12Upscaler", "dlss"), "d3d11": ("Dx11Upscaler", "dlss_12"),
            "vulkan": ("VulkanUpscaler", "dlss")}


def optiscaler_settings(features: frozenset[str], api: str, supported: tuple[str, ...], feeder: bool) -> list[Setting]:
    """Custom-preset controls offered for this OptiScaler build, renderer and route."""
    dx12 = api == "d3d12" and not feeder
    out = [Setting("DlssNr.WorkingScale", "Model resolution", "float", 1.0, 0.25, 2.0 if api != "d3d11" else 1.0,
                   help="Fraction of the frame the model works at. Cost falls with the square; above 1.0 it "
                        "supersamples (DirectX 12 / Vulkan)."),
           Setting("DlssNr.Style", "Model style", "choice", 0, choices=((0, "Default"), (1, "Natural"), (2, "Cinematic")),
                   help="Rebuilds the model when changed."),
           Setting("DlssNr.TransferStrength", "Detail strength", "float", 1.0, 0.0, 1.5,
                   help="0 keeps the upscaler's picture, 1 is the model's picture.")]
    if "presr" in features and dx12:
        out.insert(0, Setting("DlssNr.PreSr", "Run before upscaling (pre-SR)", "bool", True,
                              help="Much cheaper; DirectX 12 games that use DLSS Super Resolution."))
    if "cache" in features and dx12:
        out.append(Setting("DlssNr.CacheEnabled", "Temporal edit cache", "bool", True,
                           help="Runs the model every N frames and carries its edit along motion vectors."))
        out.append(Setting("DlssNr.CacheInterval", "Model every N frames", "int", 1, 1, 16))
    if "passes" in features:
        out.append(Setting("DlssNr.Passes", "Model passes", "int", 1, 1, 4,
                           help="Each pass costs a full model run and its own video memory."))
    return [s for s in out if s.name in supported or not supported]


def optiscaler_preset(preset: str, features: frozenset[str], api: str, feeder: bool) -> dict[str, Any]:
    if preset not in PRESETS or preset == "custom":
        raise ValueError(f"Unknown preset {preset}.")
    dx12 = api == "d3d12" and not feeder
    if "presr" in features and "cache" in features and dx12:
        table = {
            "performance": {"PreSr": True, "CacheEnabled": True, "CacheInterval": 2, "WorkingScale": 0.67,
                            "JbuUpsample": True},
            "balanced": {"PreSr": False, "CacheEnabled": True, "CacheInterval": 2, "WorkingScale": 0.67,
                         "JbuUpsample": True},
            "quality": {"PreSr": True, "CacheEnabled": True, "CacheInterval": 1, "WorkingScale": 0.67,
                        "JbuUpsample": True, "CacheRefreshBlend": 1.0},
            "ultra": {"PreSr": False, "CacheEnabled": False, "WorkingScale": 1.0},
        }
    else:
        supersample = 1.5 if api in ("d3d12", "vulkan") else 1.0
        table = {
            "performance": {"WorkingScale": 0.5, "JbuUpsample": True},
            "balanced": {"WorkingScale": 0.67, "JbuUpsample": True},
            "quality": {"WorkingScale": 1.0},
            "ultra": {"WorkingScale": 1.0, "Passes": 2} if "passes" in features else {"WorkingScale": supersample},
        }
        if feeder and "presr" in features:
            for row in table.values():
                row["PreSr"] = False       # the Feeder's DLAA contract has no image before an upscaler
    return {"DlssNr": dict(table[preset])}


def optiscaler_values(preset: str, custom: dict | None, features: frozenset[str], api: str, supported: tuple[str, ...],
                      feeder: bool = False, overrides: dict | None = None) -> dict[str, dict[str, Any]]:
    if preset == "custom":
        specs = {s.key: s for s in optiscaler_settings(features, api, supported, feeder)}
        unknown = set(custom or {}) - set(specs)
        if unknown:
            raise ValueError("Settings not supported by this version: " + ", ".join(sorted(unknown)))
        values = {"DlssNr": {}}
        base = optiscaler_preset("quality", features, api, feeder)["DlssNr"]
        values["DlssNr"].update(base)
        for key, spec in specs.items():
            if key in (custom or {}):
                values["DlssNr"][spec.name] = spec.coerce(custom[key])
    else:
        values = optiscaler_preset(preset, features, api, feeder)
    values["DlssNr"]["Enabled"] = True
    if "presr" in features:
        # Skynizz's installer sets the automatic white point (measured per scene, adapted per region).
        values["DlssNr"]["WhitePointSource"] = 3
    if supported:
        values["DlssNr"] = {k: v for k, v in values["DlssNr"].items() if k in supported or k == "WhitePointSource"}
    upscaler = UPSCALER.get("d3d12" if feeder else api)
    if upscaler:
        values["Upscalers"] = {upscaler[0]: upscaler[1]}
    values["Log"] = {"LogToFile": True, "LogLevel": 2}
    for key, value in (overrides or {}).items():
        sec, _, name = key.partition(".")
        if name:
            values.setdefault(sec, {})[name] = value
    return values


def optiscaler_ini(template: str, values: dict[str, dict[str, Any]], previous: str | None = None) -> str:
    """The shipped OptiScaler.ini with the preset applied. Settings the user changed in an older install
    (non-``auto`` values of keys the new template still has) are carried over first."""
    text = template
    if previous:
        old = ini.get_values(previous)
        new = ini.get_values(template)
        carry: dict[str, dict[str, str]] = {}
        for sec, keys in old.items():
            for key, value in keys.items():
                if sec in new and key in new[sec] and value not in ("auto", new[sec][key]):
                    carry.setdefault(sec, {})[key] = value
        text = ini.set_values(text, carry)
    return ini.set_values(text, values)


# -- RenoDX DLSS 5 ------------------------------------------------------------------------------------
RENODX_SETTINGS = [
    Setting("RenoDX.DLSS5.NRResolutionScale", "Model resolution", "float", 1.0, 0.25, 1.0,
            help="Resolution the neural model works at; cost falls with the square."),
    Setting("RenoDX.DLSS5.NRPasses", "Model passes", "int", 1, 1, 4,
            help="Each pass is a full model run."),
    Setting("RenoDX.DLSS5.NRStyle", "Model style", "choice", 0, choices=((0, "Default"), (1, "Natural"), (2, "Cinematic")),
            help="Style 2 has crashed some games at start-up (DLSS5-Feeder README)."),
    Setting("RenoDX.DLSS5.NeuralUplift", "Neural Rendering", "choice", 1, choices=((1, "On"), (0, "Off"))),
]
_RENODX_TABLE = {"performance": (0.5, 1), "balanced": (0.67, 1), "quality": (1.0, 1), "ultra": (1.0, 2)}
SECTION_RENODX = "RenoDX.DLSS5"


def renodx_values(preset: str, custom: dict | None, supported: tuple[str, ...], config_version: int | None,
                  feeder: bool, overrides: dict | None = None) -> dict[str, dict[str, Any]]:
    if preset == "custom":
        specs = {s.key.rsplit(".", 1)[1]: s for s in RENODX_SETTINGS}
        values: dict[str, Any] = {"NRResolutionScale": 1.0, "NRPasses": 1, "NRStyle": 0, "NeuralUplift": 1}
        for key, value in (custom or {}).items():
            name = key.rsplit(".", 1)[-1]
            if name not in specs:
                raise ValueError(f"Setting not supported by RenoDX DLSS 5: {key}")
            values[name] = specs[name].coerce(value)
    elif preset in _RENODX_TABLE:
        scale, passes = _RENODX_TABLE[preset]
        values = {"NRResolutionScale": scale, "NRPasses": passes, "NRStyle": 0, "NeuralUplift": 1}
    else:
        raise ValueError(f"Unknown preset {preset}.")
    # Upscaling off keeps the frame size (and 16:9 on ultrawide screens); the Feeder needs it 1:1.
    values["NREnableUpscaling"] = 0
    if feeder:
        values["EnableHooks"] = 2
    for key, value in (overrides or {}).items():
        values[key] = value
    if config_version:
        values = {"ConfigVersion": config_version, **values}
    if supported:
        values = {k: v for k, v in values.items() if k in supported or k == "ConfigVersion"}
    return {SECTION_RENODX: values}


# -- ReShade / Feeder ------------------------------------------------------------------------------------
PRESET_FILE = "AKENO-DLSS5.ini"


def reshade_ini(current: str, *, effects: bool, definitions: dict[str, str] | None = None,
                consumer: dict[str, dict[str, Any]] | None = None) -> str:
    """Merge what the stack needs into ReShade.ini, keeping every unrelated setting the user has."""
    values: dict[str, dict[str, Any]] = {"ADDON": {"AddonPath": ".\\"}}
    existing = ini.get_values(current)
    general = existing.get("GENERAL", {})
    if effects:
        values["GENERAL"] = {"PresetPath": ".\\" + PRESET_FILE}
        for key, path in (("EffectSearchPaths", ".\\reshade-shaders\\Shaders\\**"),
                          ("TextureSearchPaths", ".\\reshade-shaders\\Textures\\**")):
            old = general.get(key, "")
            values["GENERAL"][key] = old if path.casefold() in old.casefold() else ",".join(filter(None, [old, path]))
        defs = [d for d in general.get("PreprocessorDefinitions", "").split(",") if d.strip()]
        wanted = dict(definitions or {})
        kept = [d for d in defs if d.split("=", 1)[0].strip() not in wanted]
        values["GENERAL"]["PreprocessorDefinitions"] = ",".join(kept + [f"{k}={v}" for k, v in wanted.items()])
    if "KeyOverlay" not in existing.get("INPUT", {}):
        values["INPUT"] = {"KeyOverlay": "36,0,0,0"}      # Home
    for sec, keys in (consumer or {}).items():
        values.setdefault(sec, {}).update(keys)
    return ini.set_values(current, values)


def reshade_preset(technique: str, shader: str, provider_id: int) -> str:
    order = f"{technique}@{shader},DLSS5_Feed@DLSS5_Feed.fx"
    return (f"Techniques={order}\nTechniqueSorting={order}\n\n"
            f"[DLSS5_Feed.fx]\nPreprocessorDefinitions=DLSS5_MV_PROVIDER={provider_id}\n")


def feeder_cfg(overrides: dict | None = None) -> str:
    values = {"enabled": "1", "mode": "2", "hdr": "-1", "depth_inverted": "-1", "work_resolution": "100",
              "work_upscale": "0", "preset": "0", "gpu_timeout_ms": "2000", "create_delay": "60"}
    values.update({k: str(v) for k, v in (overrides or {}).items()})
    return "".join(f"{k}={v}\n" for k, v in values.items())


def describe_preset(family: str, preset: str, features: frozenset[str] = frozenset(), api: str = "d3d12",
                    feeder: bool = False) -> str:
    """One line telling the user what a preset really changes."""
    if family == "renodx":
        if preset == "custom":
            return "Your own model resolution, passes and style."
        scale, passes = _RENODX_TABLE[preset]
        return f"Model at {round(scale * 100)}% resolution, {passes} pass{'es' if passes > 1 else ''}."
    if preset == "custom":
        return "Your own OptiScaler DLSS 5 settings."
    v = optiscaler_preset(preset, features, api, feeder)["DlssNr"]
    parts = []
    if "PreSr" in v:
        parts.append("before the upscaler" if v["PreSr"] else "after the upscaler")
    parts.append(f"model at {round(v['WorkingScale'] * 100)}%")
    if v.get("CacheEnabled"):
        n = v.get("CacheInterval", 1)
        parts.append("every frame" if n == 1 else f"every {n} frames")
    elif "CacheEnabled" in v:
        parts.append("every frame")
    if v.get("Passes", 1) > 1:
        parts.append(f"{v['Passes']} passes")
    return ", ".join(parts).capitalize() + "."
