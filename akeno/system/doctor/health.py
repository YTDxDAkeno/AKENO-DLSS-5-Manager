"""System Health Dashboard: one snapshot of what this PC reports, the history AKENO recorded, and an optional score.

The snapshot merges what AKENO already knows (``SystemInfo`` from the hardware scan: CPU and GPU names, Windows
build) with live readings. Every reading names its source; a sensor Windows does not expose stays unavailable with
the reason.

The **health indicator** is a transparent sum: it starts at 100 and every deduction is listed with its reason and
its points. It summarises the findings below it; it is not a statement about the PC's condition beyond them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from akeno.i18n import msg
from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.model import Finding, Level, Reading, human_bytes
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

GPU_HOT_C = 87          # most desktop GPUs start limiting their clock around 83-90 °C
DRIVE_LOW_PERCENT = 10
DRIVE_LOW_BYTES = 15 * 2 ** 30
MIN_VOLUME = 2 ** 30


@dataclass
class HealthSnapshot:
    created_at: float = field(default_factory=time.time)
    os: dict[str, Any] = field(default_factory=dict)
    cpu: dict[str, Reading] = field(default_factory=dict)
    memory: dict[str, Reading] = field(default_factory=dict)
    gpus: list[dict[str, Any]] = field(default_factory=list)       # name + readings
    volumes: list[dict[str, Any]] = field(default_factory=list)
    temperatures: list[dict[str, Any]] = field(default_factory=list)
    uptime_s: float | None = None
    boot_time: float | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"created_at": self.created_at, "os": self.os,
                "cpu": {k: v.to_dict() for k, v in self.cpu.items()},
                "memory": {k: v.to_dict() for k, v in self.memory.items()},
                "gpus": [{"name": g["name"], "readings": {k: v.to_dict() for k, v in g["readings"].items()}}
                         for g in self.gpus],
                "volumes": self.volumes, "temperatures": self.temperatures, "uptime_s": self.uptime_s,
                "boot_time": self.boot_time, "errors": [str(e) for e in self.errors]}


def collect(probe, system=None, ctx: TaskContext | None = None, sample_s: float = 1.0) -> HealthSnapshot:
    """Read everything for the dashboard. ``system`` is AKENO's last hardware scan (``SystemInfo``) or None."""
    ctx = context(ctx)
    snap = HealthSnapshot()
    if system is not None:
        snap.os = {"name": system.os_name, "version": system.os_version, "build": system.os_build,
                   "display_version": system.os_display_version, "cpu_name": system.cpu_name,
                   "ram_bytes": system.ram_bytes}
    ctx.report(msg("Reading processor load"))
    try:
        cpu = probe.cpu(sample_s, ctx)
        snap.cpu["load"] = Reading(_round(cpu.get("percent")), "%", "psutil")
        snap.cpu["cores"] = Reading(cpu.get("logical"), "", "psutil",
                                    msg("{n} physical cores", n=cpu.get("physical")) if cpu.get("physical") else "")
    except (Cancelled, TimedOut):
        raise
    except Exception as exc:                               # one failing reading never ends the snapshot
        snap.errors.append(msg("Processor load: {e}", e=str(exc)))
    perf = None
    ctx.report(msg("Reading Windows performance counters"))
    try:
        perf = probe.perf(ctx)
    except CommandUnavailable as exc:
        perf = None
        snap.errors.append(str(exc))
    except (QueryError, OSError) as exc:
        snap.errors.append(msg("Windows performance counters: {e}", e=str(exc)))
    pcpu = (perf or {}).get("cpu") or {}
    if pcpu.get("ProcessorFrequency"):
        snap.cpu["clock"] = Reading(pcpu.get("ProcessorFrequency"), "MHz", "Windows performance counters",
                                    msg("{p} % of the rated maximum", p=pcpu.get("PercentofMaximumFrequency"))
                                    if pcpu.get("PercentofMaximumFrequency") is not None else "")
    else:
        snap.cpu["clock"] = Reading.missing(msg("Windows did not report the current processor clock."))
    temp = None
    try:
        temp = probe.cpu_temperature()
    except Exception:                                      # noqa: BLE001 (optional sensor)
        temp = None
    if temp and temp.get("value") is not None:
        snap.cpu["temperature"] = Reading(_round(temp["value"]), "°C", temp.get("source", ""))
    else:
        snap.cpu["temperature"] = Reading.missing(msg("Windows has no documented CPU temperature sensor without a "
                                                      "vendor driver."))
    ctx.check()
    mem = probe.memory()
    snap.memory["total"] = Reading(mem.get("total"), "B", "psutil")
    snap.memory["used"] = Reading(mem.get("used"), "B", "psutil")
    snap.memory["percent"] = Reading(_round(mem.get("percent")), "%", "psutil")
    pm = (perf or {}).get("memory") or {}
    if pm.get("CommitLimit"):
        snap.memory["commit"] = Reading(_round(pm.get("PercentCommittedBytesInUse")), "%",
                                        "Windows performance counters",
                                        msg("{used} of {limit} committed", used=human_bytes(pm.get("CommittedBytes")),
                                            limit=human_bytes(pm.get("CommitLimit"))))
        snap.memory["paging"] = Reading(pm.get("PagesPersec"), "pages/s", "Windows performance counters",
                                        msg("Pages read from or written to disk to resolve hard page faults."))
    else:
        snap.memory["commit"] = Reading.missing(msg("Needs Windows performance counters."))
        snap.memory["paging"] = Reading.missing(msg("Needs Windows performance counters."))
    ctx.report(msg("Reading graphics processors"))
    snap.gpus = _gpus(probe, system, perf)
    for z in (perf or {}).get("thermal") or []:
        kelvin = z.get("HighPrecisionTemperature")
        c = (kelvin / 10 - 273.15) if kelvin else ((z.get("Temperature") or 0) - 273.15 if z.get("Temperature") else None)
        if c is None or c < -50 or c > 150:
            continue
        snap.temperatures.append({"name": z.get("Name") or "", "celsius": round(c, 1),
                                  "passive_limit": z.get("PercentPassiveLimit"), "kind": "acpi",
                                  "source": "ACPI thermal zone (not necessarily the processor)"})
    for g in snap.gpus:
        t = g["readings"].get("temperature")
        if t is not None and t.available:
            snap.temperatures.append({"name": g["name"], "celsius": t.value, "kind": "gpu", "source": t.source})
    ctx.check()
    # tiny special volumes (recovery partitions, Linux bind mounts) are not drives a user stores data on
    snap.volumes = [v for v in probe.volumes() if (v.get("total") or 0) >= MIN_VOLUME]
    bt = probe.boot_time()
    if bt:
        snap.boot_time = bt
        snap.uptime_s = max(0.0, time.time() - bt)
    return snap


def _gpus(probe, system, perf) -> list[dict[str, Any]]:
    out = []
    nv = []
    try:
        nv = probe.nvidia() or []
    except Exception:                                      # noqa: BLE001 (optional)
        nv = []
    known = list(getattr(system, "gpus", None) or [])
    names = [g.name for g in known if not getattr(g, "is_software", False)] or [n["name"] for n in nv]
    for name in names:
        info = next((g for g in known if g.name == name), None)
        r: dict[str, Reading] = {}
        n = next((x for x in nv if x["name"] == name), None)
        if info is not None and info.vram_bytes:
            r["vram_total"] = Reading(info.vram_bytes, "B", "DXGI / registry")
        if info is not None and info.driver_version:
            r["driver"] = Reading(info.driver_version, "", "driver")
        if n:
            r["load"] = Reading(n.get("util"), "%", "nvidia-smi")
            if n.get("vram_used_mib") is not None:
                r["vram_used"] = Reading(n["vram_used_mib"] * 2 ** 20, "B", "nvidia-smi")
            r["temperature"] = Reading(n.get("temp_c"), "°C", "nvidia-smi") if n.get("temp_c") is not None else \
                Reading.missing(msg("The driver did not report a temperature."), "nvidia-smi")
            if n.get("clock_mhz") is not None:
                r["clock"] = Reading(n.get("clock_mhz"), "MHz", "nvidia-smi",
                                     msg("max {m} MHz", m=int(n["clock_max_mhz"])) if n.get("clock_max_mhz") else "")
            r["power"] = Reading(n.get("power_w"), "W", "nvidia-smi") if n.get("power_w") is not None else \
                Reading.missing(msg("The driver did not report the power draw."), "nvidia-smi")
            if n.get("thermal_hw") or n.get("thermal_sw"):
                r["throttle"] = Reading(msg("thermal slowdown active"), "", "nvidia-smi")
        else:
            luid = getattr(info, "luid", None) if info else None
            load = (perf or {}).get("gpu_3d", {}).get(luid) if luid else None
            if load is not None:
                r["load"] = Reading(round(load, 1), "%", "Windows GPU counters (3D engine)")
            elif perf:
                r["load"] = Reading(0.0, "%", "Windows GPU counters (3D engine)")
            else:
                r["load"] = Reading.missing(msg("Needs Windows GPU performance counters."))
            used = (perf or {}).get("gpu_memory", {}).get(luid, {}).get("dedicated") if luid else None
            if used is not None:
                r["vram_used"] = Reading(used, "B", "Windows GPU counters")
            r["temperature"] = Reading.missing(msg("Windows does not report this GPU's temperature without the "
                                                   "vendor's tools."))
        out.append({"name": name, "readings": r})
    return out


def _round(v):
    return None if v is None else round(float(v), 1)


# -- findings and the health indicator ---------------------------------------------------------------------------
def findings(snap: HealthSnapshot, system_drive: str) -> list[Finding]:
    """What the snapshot alone shows (the detective and crash analysis add their own)."""
    out: list[Finding] = []
    for v in snap.volumes:
        if v.get("removable") or not v.get("total"):
            continue
        free, total = v.get("free") or 0, v["total"]
        pct = free / total * 100
        system = str(v.get("mount", "")).lower().rstrip("\\/") == system_drive.lower().rstrip("\\/")
        if pct < DRIVE_LOW_PERCENT or (system and free < DRIVE_LOW_BYTES):
            f = Finding(f"disk.low.{v['mount']}", "doctor", Level.PROBLEM if pct < 5 else Level.WARN,
                        msg("Little free space on {d}", d=v["mount"]),
                        msg("{free} free of {total} ({pct:.0f} %).", free=human_bytes(free), total=human_bytes(total),
                            pct=pct),
                        msg("Windows needs free space for updates, the page file and temporary files. Smart Cleaner "
                            "shows what can be removed safely."))
            f.measured(msg("{pct:.1f} % free", pct=pct), "psutil")
            if system:
                f.cause(msg("A nearly full system drive slows updates, the page file and programs that write "
                            "temporary files."))
            f.link("cleaner", msg("Analyse with Smart Cleaner"), v["mount"])
            out.append(f)
    ram = snap.memory.get("percent")
    if ram is not None and ram.available and ram.value >= 90:
        f = Finding("ram.high", "doctor", Level.WARN, msg("Memory almost fully used"),
                    msg("{p:.0f} % of the memory is in use.", p=ram.value),
                    msg("Close programs you do not need, or check which programs use the most memory in Slow PC "
                        "Detective."))
        f.measured(msg("{p:.0f} % used", p=ram.value), "psutil")
        f.link("doctor", msg("Run Slow PC Detective"), "slowpc")
        out.append(f)
    commit = snap.memory.get("commit")
    if commit is not None and commit.available and commit.value >= 90:
        f = Finding("ram.commit", "doctor", Level.PROBLEM, msg("Committed memory near its limit"),
                    msg("{p:.0f} % of the commit limit is in use. When it is reached, programs fail to allocate "
                        "memory.", p=commit.value),
                    msg("Close programs, and check that the page file is managed by Windows."))
        f.measured(str(commit.note), "Windows performance counters")
        out.append(f)
    for g in snap.gpus:
        t = g["readings"].get("temperature")
        if t is not None and t.available and t.value >= GPU_HOT_C:
            f = Finding("gpu.hot." + g["name"], "doctor", Level.WARN, msg("{gpu} runs hot", gpu=g["name"]),
                        msg("{t:.0f} °C reported by the driver.", t=t.value),
                        msg("Check that the fans run and the case has air flow; dust in the cooler is a common "
                            "reason."))
            f.measured(msg("{t:.0f} °C", t=t.value), t.source)
            if "throttle" in g["readings"]:
                f.measured(msg("The driver reports a thermal slowdown."), "nvidia-smi")
            out.append(f)
    for z in snap.temperatures:
        if z.get("kind") == "acpi" and z.get("passive_limit") is not None and z["passive_limit"] < 100:
            f = Finding("thermal.passive." + z["name"], "doctor", Level.WARN,
                        msg("Windows limits the processor to cool down"),
                        msg("The thermal zone {z} reports a passive cooling limit of {p} %.", z=z["name"],
                            p=z["passive_limit"]),
                        msg("The PC is too warm under its current load. Check fans, air flow and dust."))
            f.measured(msg("{c:.0f} °C, passive limit {p} %", c=z["celsius"], p=z["passive_limit"]), z["source"])
            out.append(f)
    return out


@dataclass
class Score:
    value: int
    deductions: list[tuple[int, str]]

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "deductions": [(p, str(r)) for p, r in self.deductions]}


WEIGHTS = {Level.PROBLEM: 15, Level.WARN: 6}


def score(all_findings: list[Finding]) -> Score:
    """100 minus a fixed number of points per problem (15) and warning (6), at most 3 of the same kind of finding
    counted, never below 0. Information and unknown values deduct nothing: missing sensors are not a defect."""
    deductions = []
    seen: dict[str, int] = {}
    for f in sorted(all_findings, key=lambda x: -x.level.rank):
        points = WEIGHTS.get(f.level)
        if not points:
            continue
        family = f.id.split(".")[0]
        seen[family] = seen.get(family, 0) + 1
        if seen[family] > 3:
            continue
        deductions.append((points, f.title))
    return Score(max(0, 100 - sum(p for p, _ in deductions)), deductions)


def history_sample(snap: HealthSnapshot) -> dict[str, Any]:
    """What the resource history keeps of a snapshot (small: one row per sample)."""
    def val(d, k):
        r = d.get(k)
        return r.value if r is not None and r.available else None
    sysvol = None
    for v in snap.volumes:
        if v.get("total") and not v.get("removable"):
            sysvol = sysvol or v
    gpu = snap.gpus[0]["readings"] if snap.gpus else {}
    return {"cpu": val(snap.cpu, "load"), "ram": val(snap.memory, "percent"),
            "gpu": val(gpu, "load") if gpu else None, "gpu_temp": val(gpu, "temperature") if gpu else None,
            "disk_free": (sysvol or {}).get("free"), "t": snap.created_at}
