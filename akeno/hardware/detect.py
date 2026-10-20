"""Hardware detection service: merges DXGI, registry, NVML and display probes into ``SystemInfo``."""

from __future__ import annotations

import logging
import platform
from typing import Any, Callable

from akeno.core.db import now_iso
from akeno.core.models import DisplayInfo, GpuInfo, SystemInfo, Vendor
from akeno.hardware import gpu_db
from akeno.paths import is_windows

log = logging.getLogger(__name__)

Probe = Callable[[], Any]


def default_probes() -> dict[str, Probe]:
    if not is_windows():
        return {}
    from akeno.hardware import win_probe
    return {
        "dxgi": win_probe.probe_dxgi,
        "registry": win_probe.probe_display_class_registry,
        "nvml": win_probe.probe_nvml,
        "displays": win_probe.probe_displays,
        "os": win_probe.probe_os,
    }


def _run(name: str, probe: Probe | None, errors: list[str]) -> Any:
    if probe is None:
        return None
    try:
        return probe()
    except Exception as exc:  # each probe is optional; record and continue
        log.warning("hardware probe %s failed: %s", name, exc)
        errors.append(f"{name}: {exc}")
        return None


def merge(dxgi: list[dict] | None, registry: list[dict] | None, nvml: dict | None) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    registry = list(registry or [])
    seen_luids: set[str] = set()
    for a in dxgi or []:
        if a.get("luid") in seen_luids:
            continue
        seen_luids.add(a.get("luid"))
        g = GpuInfo(name=a.get("name") or "Unknown adapter", vendor_id=a.get("vendor_id"),
                    device_id=a.get("device_id"), subsys_id=a.get("subsys_id"), revision=a.get("revision"),
                    vram_bytes=a.get("vram_bytes") or None, shared_memory_bytes=a.get("shared_memory_bytes"),
                    driver_version_raw=a.get("driver_version_raw"), luid=a.get("luid"),
                    is_software=bool(a.get("is_software")), feature_level=a.get("feature_level"),
                    is_primary=a.get("index") == 0, sources=["dxgi"])
        match = next((r for r in registry if r.get("vendor_id") == g.vendor_id and r.get("device_id") == g.device_id),
                     None)
        if match:
            registry.remove(match)
            g.driver_date = match.get("driver_date")
            g.driver_version_raw = g.driver_version_raw or match.get("driver_version_raw")
            # DXGI caps DedicatedVideoMemory on some drivers; the registry qwMemorySize is exact.
            if match.get("vram_bytes"):
                g.vram_bytes = max(g.vram_bytes or 0, match["vram_bytes"])
            g.sources.append("registry")
        gpus.append(g)
    # Adapters only the registry knows about (e.g. DXGI failed): still report them.
    for r in registry if not dxgi else []:
        if not r.get("name"):
            continue
        gpus.append(GpuInfo(name=r["name"], vendor_id=r.get("vendor_id"), device_id=r.get("device_id"),
                            vram_bytes=r.get("vram_bytes"), driver_version_raw=r.get("driver_version_raw"),
                            driver_date=r.get("driver_date"), sources=["registry"]))
    for g in gpus:
        gpu_db.classify(g)
        if g.vendor == Vendor.NVIDIA:
            g.driver_version = (nvml or {}).get("driver_version") or gpu_db.nvidia_driver_from_windows_version(
                g.driver_version_raw)
            if nvml and nvml.get("driver_version"):
                g.sources.append("nvml")
        else:
            g.driver_version = g.driver_version_raw
    return gpus


class HardwareService:
    def __init__(self, probes: dict[str, Probe] | None = None):
        self.probes = default_probes() if probes is None else probes

    def detect(self) -> SystemInfo:
        errors: list[str] = []
        if not self.probes:
            info = SystemInfo(os_name=platform.system(), os_version=platform.release(),
                              is_64bit=platform.machine().endswith("64"))
            info.detection_errors.append("Hardware detection uses Windows APIs and is unavailable on this platform.")
            info.detected_at = now_iso()
            return info
        dxgi = _run("dxgi", self.probes.get("dxgi"), errors)
        reg = _run("registry", self.probes.get("registry"), errors)
        nvml = _run("nvml", self.probes.get("nvml"), errors)
        displays = _run("displays", self.probes.get("displays"), errors) or []
        os_info = _run("os", self.probes.get("os"), errors) or {}
        info = SystemInfo(**{k: v for k, v in os_info.items() if k in SystemInfo.__dataclass_fields__})
        info.gpus = merge(dxgi, reg, nvml)
        info.displays = [DisplayInfo(**d) for d in displays]
        info.detection_errors = errors
        info.detected_at = now_iso()
        return info
