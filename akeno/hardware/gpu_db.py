"""GPU identification from PCI vendor/device IDs, with the adapter name as a cross-check.

The device ID ranges below follow the PCI ID allocation NVIDIA uses per chip family (as published in
the PCI ID repository, pci-ids.ucw.cz). Ranges are coarse on purpose: when an ID falls outside every
known range, the name is used and the result is marked accordingly instead of guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from akeno.core.models import GpuInfo, HardwareSupport, Vendor

VENDOR_IDS = {0x10DE: Vendor.NVIDIA, 0x1002: Vendor.AMD, 0x1022: Vendor.AMD, 0x8086: Vendor.INTEL,
              0x1414: Vendor.MICROSOFT}


@dataclass(frozen=True)
class ChipRange:
    start: int
    end: int
    chip: str
    architecture: str
    tensor_cores: bool


# NVIDIA consumer/workstation chip ranges (inclusive).
NVIDIA_RANGES: tuple[ChipRange, ...] = (
    ChipRange(0x1E00, 0x1E3F, "TU102", "Turing", True),
    ChipRange(0x1E80, 0x1EBF, "TU104", "Turing", True),
    ChipRange(0x1F00, 0x1F7F, "TU106", "Turing", True),
    ChipRange(0x1F80, 0x1FFF, "TU117", "Turing", False),   # GTX 1650 family: no tensor cores
    ChipRange(0x2180, 0x21FF, "TU116", "Turing", False),   # GTX 1660 family: no tensor cores
    ChipRange(0x2200, 0x223F, "GA102", "Ampere", True),
    ChipRange(0x2400, 0x243F, "GA103", "Ampere", True),
    ChipRange(0x2480, 0x24FF, "GA104", "Ampere", True),
    ChipRange(0x2500, 0x255F, "GA106", "Ampere", True),
    ChipRange(0x2560, 0x25FF, "GA107", "Ampere", True),
    ChipRange(0x2680, 0x26FF, "AD102", "Ada Lovelace", True),
    ChipRange(0x2700, 0x277F, "AD103", "Ada Lovelace", True),
    ChipRange(0x2780, 0x27FF, "AD104", "Ada Lovelace", True),
    ChipRange(0x2800, 0x287F, "AD106", "Ada Lovelace", True),
    ChipRange(0x2880, 0x28FF, "AD107", "Ada Lovelace", True),
    ChipRange(0x2B80, 0x2BFF, "GB202", "Blackwell", True),
    ChipRange(0x2C00, 0x2C7F, "GB203", "Blackwell", True),
    ChipRange(0x2C80, 0x2CFF, "GB205", "Blackwell", True),
    ChipRange(0x2D00, 0x2D7F, "GB206", "Blackwell", True),
    ChipRange(0x2D80, 0x2DFF, "GB207", "Blackwell", True),
)

ARCH_GENERATION = {"Turing": "RTX 20", "Ampere": "RTX 30", "Ada Lovelace": "RTX 40", "Blackwell": "RTX 50"}
GENERATION_ARCH = {v: k for k, v in ARCH_GENERATION.items()}

_RTX_CONSUMER = re.compile(r"\bRTX\s*(20|30|40|50)(\d0)\b", re.I)
_RTX_WORKSTATION = {
    re.compile(r"\bQuadro\s+RTX\b", re.I): "Turing",
    re.compile(r"\bRTX\s+A\d{3,4}\b", re.I): "Ampere",
    re.compile(r"\bRTX\s+\d{4}\s+Ada\b", re.I): "Ada Lovelace",
    re.compile(r"\bRTX\s+PRO\s+\d{4}\s+Blackwell\b", re.I): "Blackwell",
}


def vendor_from_id(vendor_id: int | None, name: str = "") -> Vendor:
    if vendor_id is not None and vendor_id in VENDOR_IDS:
        return VENDOR_IDS[vendor_id]
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n:
        return Vendor.NVIDIA
    if "amd" in n or "radeon" in n:
        return Vendor.AMD
    if "intel" in n:
        return Vendor.INTEL
    if "microsoft basic" in n:
        return Vendor.MICROSOFT
    return Vendor.UNKNOWN


def chip_for_device(device_id: int | None) -> ChipRange | None:
    if device_id is None:
        return None
    for r in NVIDIA_RANGES:
        if r.start <= device_id <= r.end:
            return r
    return None


def arch_from_name(name: str) -> tuple[str | None, bool | None]:
    m = _RTX_CONSUMER.search(name)
    if m:
        return GENERATION_ARCH.get(f"RTX {m.group(1)}"), True
    for rx, arch in _RTX_WORKSTATION.items():
        if rx.search(name):
            return arch, True
    if re.search(r"\bGTX\b", name, re.I):
        return None, False
    return None, None


def classify(gpu: GpuInfo) -> GpuInfo:
    """Fill vendor/architecture/generation/tensor-core fields in place and return the GPU."""
    gpu.vendor = vendor_from_id(gpu.vendor_id, gpu.name)
    if gpu.vendor != Vendor.NVIDIA:
        gpu.has_tensor_cores = False if gpu.vendor in (Vendor.AMD, Vendor.INTEL, Vendor.MICROSOFT) else None
        gpu.is_software = gpu.is_software or gpu.vendor == Vendor.MICROSOFT
        return gpu

    chip = chip_for_device(gpu.device_id)
    name_arch, name_tensor = arch_from_name(gpu.name)
    if chip:
        gpu.chip = chip.chip
        gpu.architecture = chip.architecture
        gpu.has_tensor_cores = chip.tensor_cores
        if name_tensor is False:      # e.g. GTX in name: trust the stricter answer
            gpu.has_tensor_cores = False
    else:
        gpu.architecture = name_arch
        gpu.has_tensor_cores = name_tensor
    if gpu.architecture and gpu.has_tensor_cores:
        gpu.generation = ARCH_GENERATION.get(gpu.architecture)
    return gpu


@dataclass
class SupportVerdict:
    level: HardwareSupport
    reason: str
    reason_code: str


def nr_hardware_support(gpu: GpuInfo | None) -> SupportVerdict:
    """Hardware-level support for DLSS 5 Neural Rendering community mods.

    The upstream projects drive an undocumented NVIDIA feature, so nothing here is reported as
    officially supported. The evidence behind the per-generation wording is recorded in the
    compatibility database (``hardware`` section) and shown in the UI.
    """
    if gpu is None:
        return SupportVerdict(HardwareSupport.UNKNOWN, "No graphics adapter was detected.", "no_gpu")
    if gpu.is_software:
        return SupportVerdict(HardwareSupport.UNSUPPORTED, "Software renderer, not a physical GPU.", "software")
    if gpu.vendor != Vendor.NVIDIA:
        return SupportVerdict(HardwareSupport.UNSUPPORTED,
                              "DLSS Neural Rendering needs an NVIDIA RTX GPU (Tensor Cores and the NVIDIA NGX runtime).",
                              "not_nvidia")
    if gpu.has_tensor_cores is False:
        return SupportVerdict(HardwareSupport.UNSUPPORTED,
                              "This NVIDIA GPU has no Tensor Cores (GTX class), so DLSS cannot run.", "no_tensor")
    if gpu.generation == "RTX 50":
        return SupportVerdict(HardwareSupport.COMMUNITY,
                              "Upstream reports that NVIDIA's signed nvngx_dlssnr.dll creates the neural feature on RTX 50.",
                              "rtx50")
    if gpu.generation in ("RTX 20", "RTX 30", "RTX 40"):
        return SupportVerdict(HardwareSupport.EXPERIMENTAL,
                              "Experimental: upstream reports NVIDIA's signed runtime refusing the neural feature on "
                              "RTX 20/30/40 with some driver versions. Whether it runs depends on your driver's "
                              "nvngx_dlssnr.dll; AKENO will not install modified NVIDIA files.",
                              "rtx_older")
    return SupportVerdict(HardwareSupport.UNKNOWN,
                          "This NVIDIA GPU could not be matched to a known RTX generation.", "unknown_nvidia")


def nvidia_driver_from_windows_version(raw: str | None) -> str | None:
    """Windows driver version 32.0.15.6109 -> NVIDIA version 561.09 (last five digits)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", ".".join(raw.split(".")[-2:]))
    if len(digits) < 5:
        return None
    d = digits[-5:]
    return f"{int(d[:3])}.{d[3:]}"


def pick_primary(gpus: list[GpuInfo]) -> GpuInfo | None:
    """The GPU AKENO analyses by default: the RTX GPU with the most VRAM, else the first physical GPU."""
    physical = [g for g in gpus if not g.is_software]
    if not physical:
        return gpus[0] if gpus else None
    rtx = [g for g in physical if g.vendor == Vendor.NVIDIA and g.has_tensor_cores]
    pool = rtx or physical
    return max(pool, key=lambda g: (g.vram_bytes or 0))
