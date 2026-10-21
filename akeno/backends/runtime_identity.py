"""Runtime-specific identity from signed PE structure, never from the filesystem name alone."""
from __future__ import annotations

import re
from akeno.games.pe import PeInfo

KNOWN = {"nvngx_dlss.dll", "nvngx_dlssnr.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll", "nvngx.dll"}


def identify(info: PeInfo, requested: str) -> tuple[bool, str]:
    if requested not in {"nvngx_dlss.dll", "nvngx_dlssnr.dll"}:
        return False, "Unsupported runtime identity."
    if not info.valid or not info.is_dll or info.machine != "x64":
        return False, "Runtime must be an x64 PE DLL."
    strings = {k.lower(): v.strip() for k, v in info.strings.items()}
    identities = [strings.get("originalfilename", ""), strings.get("internalname", ""), info.export_dll_name or ""]
    explicit = {n.casefold() for n in identities if n.casefold() in KNOWN}
    if explicit - {requested}:
        return False, "Conflicting runtime identity: " + ", ".join(sorted(explicit))
    exports = set(info.exports)
    ngx = any({f"NVSDK_NGX_{api}_CreateFeature", f"NVSDK_NGX_{api}_EvaluateFeature"} <= exports
              for api in ("D3D11", "D3D12", "VULKAN"))
    if not ngx:
        return False, "Required NGX CreateFeature/EvaluateFeature exports are missing."
    if requested in explicit:
        return True, "Exact runtime identity and NGX exports agree."
    # CL is a build/changelist label, not a runtime family. Accept only with discriminating metadata.
    cl = any(re.fullmatch(r"CL\s+\d+", n, re.I) for n in identities)
    description = " ".join(strings.get(k, "") for k in ("productname", "filedescription")).casefold()
    neural = bool(re.search(r"\bdlss[ _-]*nr\b|\bneural rendering\b", description))
    sr = bool(re.search(r"\bdlssv?\d*\b|deep learning super sampling", description)) and not neural
    excluded = any(x in description for x in ("frame generation", "ray reconstruction", "dlssg", "dlssd"))
    matching = neural if requested == "nvngx_dlssnr.dll" else sr
    if cl and matching and not excluded and strings.get("companyname", "").casefold() == "nvidia corporation":
        return True, "NVIDIA CL build metadata, runtime-specific product identity and NGX exports agree."
    return False, "Runtime family cannot be established: CL/generic NVIDIA metadata alone is insufficient."
