"""Slow PC Detective: "My PC is slower than usual" as a structured analysis.

Each step measures something and turns it into findings that keep three things apart:

* **measured**: what AKENO read on this PC just now (load, memory, disk activity, free space, events);
* **possible cause**: an explanation the measurement fits (Windows Update installing, a full system drive);
* **same time**: something that happened when the slowdown may have started (a driver installed four days ago).
  Only a correlation; the finding says so.

Nothing is changed. Recommendations name the next step and the module that can take it; the user decides.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from akeno.i18n import msg
from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.doctor import health
from akeno.system.model import Finding, Level, Report, human_bytes
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

CPU_WARN, CPU_PROBLEM = 30.0, 60.0          # % of the whole machine, sampled while no game runs
PROC_NOTE = 10.0                            # one process at or above this share is named
RAM_WARN, RAM_PROBLEM = 85.0, 95.0
PAGING_HIGH = 500                           # hard-fault pages per second
DISK_BUSY = 80.0
STARTUP_MANY = 12

# What a busy process usually means. A possible cause, never a verdict.
KNOWN_ACTIVITY = {
    "msmpeng.exe": "Microsoft Defender Antivirus is scanning files.",
    "mpdefendercoreservice.exe": "Microsoft Defender Antivirus is scanning files.",
    "tiworker.exe": "Windows Update is installing or cleaning up updates.",
    "trustedinstaller.exe": "Windows Update is installing or cleaning up updates.",
    "usocoreworker.exe": "Windows Update is checking for or preparing updates.",
    "mousocoreworker.exe": "Windows Update is checking for or preparing updates.",
    "wuauclt.exe": "Windows Update is checking for or preparing updates.",
    "searchindexer.exe": "Windows Search is indexing files.",
    "searchprotocolhost.exe": "Windows Search is indexing files.",
    "searchfilterhost.exe": "Windows Search is indexing files.",
    "compattelrunner.exe": "Windows compatibility telemetry is running (a scheduled task).",
    "msiexec.exe": "A program is being installed or removed.",
    "onedrive.exe": "OneDrive is synchronising files.",
    "fossilize_replay.exe": "Steam is pre-compiling shaders for an installed game.",
    "steamwebhelper.exe": "The Steam client (store, overlay or downloads).",
    "epicgameslauncher.exe": "The Epic Games Launcher (downloads or updates).",
    "memory compression": "Windows compresses memory because RAM is under pressure.",
    "system": "Drivers and the Windows kernel. Constant high load here usually comes from a driver.",
    "dwm.exe": "The Desktop Window Manager (drawing the desktop); animated wallpapers or many monitors add to it.",
}
WINDOWS_CORE = {"system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
                "svchost.exe", "memory compression", "dwm.exe", "explorer.exe", "winlogon.exe", "fontdrvhost.exe"}
DISK_PROVIDERS = ("disk", "ntfs", "stor", "iastor", "nvme")


def run(probe, system=None, startup: list | None = None, previous: dict | None = None,
        exclude_pids: set[int] | None = None, ctx: TaskContext | None = None, sample_s: float = 3.0,
        services: dict[int, list[str]] | None = None, now: float | None = None) -> Report:
    """The whole analysis. ``startup`` are Startup Analyzer entries, ``previous`` the last run's report data
    (for "new since last check"), ``exclude_pids`` the processes of a running game."""
    ctx = context(ctx)
    t0 = time.monotonic()
    rep = Report("doctor")
    exclude = exclude_pids or set()
    rep.data["steps"] = []

    def step(key: str, text: str):
        ctx.check()
        ctx.report(text, len(rep.data["steps"]), 7)
        rep.data["steps"].append(key)

    step("processes", msg("Measuring processor load and processes"))
    procs = []
    cpu_total = None
    try:
        cpu = probe.cpu(min(1.0, sample_s), ctx)
        cpu_total = cpu.get("percent")
        procs = [p for p in probe.processes(sample_s, ctx) if p.get("pid") not in exclude]
    except (Cancelled, TimedOut):
        raise
    except Exception as exc:                          # noqa: BLE001
        rep.errors.append(msg("Process measurement: {e}", e=str(exc)))
    _cpu(rep, cpu_total, procs, services or {})
    _changes(rep, procs, previous)

    step("memory", msg("Checking memory and paging"))
    perf = None
    try:
        perf = probe.perf(ctx)
    except CommandUnavailable as exc:
        rep.errors.append(str(exc))
    except (QueryError, OSError) as exc:
        rep.errors.append(msg("Windows performance counters: {e}", e=str(exc)))
    _memory(rep, probe.memory(), perf, procs)

    step("disk", msg("Measuring disk activity"))
    try:
        activity = probe.disk_activity(min(2.0, sample_s), ctx)
    except (Cancelled, TimedOut):
        raise
    except Exception as exc:                          # noqa: BLE001
        activity = None
        rep.errors.append(msg("Disk activity: {e}", e=str(exc)))
    _disk(rep, activity, procs)

    step("space", msg("Checking free space"))
    from akeno.system.doctor.providers import system_drive
    for f in health.findings(_volumes_only(probe), system_drive()):
        rep.add(f)

    step("startup", msg("Reviewing startup programs"))
    _startup(rep, startup)

    step("events", msg("Reading Windows events"))
    events = {}
    try:
        events = probe.events(("unexpected_shutdown", "whea", "disk", "cpu_limited", "driver_install",
                               "update_install", "service_install"), 14, 120, ctx)
    except CommandUnavailable as exc:
        rep.errors.append(str(exc))
    except (QueryError, OSError) as exc:
        rep.errors.append(msg("Windows event logs: {e}", e=str(exc)))
    for failed in events.get("_failed", []) if isinstance(events, dict) else []:
        rep.errors.append(msg("Event log query {q}: {e}", q=failed.get("q"), e=failed.get("error")))

    step("thermal", msg("Looking for signs of thermal throttling"))
    _thermal(rep, perf, events, cpu_total, probe)
    _events(rep, events, now)
    _recent_changes(rep, events, now)

    rep.data["cpu_total"] = cpu_total
    rep.data["top_cpu"] = [_slim(p) for p in sorted(procs, key=lambda p: -(p.get("cpu_percent") or 0))[:12]]
    rep.data["top_memory"] = [_slim(p) for p in sorted(procs, key=lambda p: -(p.get("rss") or 0))[:12]]
    rep.data["top_io"] = [_slim(p) for p in sorted(procs, key=lambda p: -(p.get("io_bps") or 0))[:8]
                          if (p.get("io_bps") or 0) > 0]
    rep.duration_s = round(time.monotonic() - t0, 1)
    ctx.report(msg("Analysis finished"), 7, 7)
    if not rep.problems():
        rep.add(Finding("slowpc.none", "doctor", Level.OK, msg("No measurable cause found right now"),
                        msg("Processor, memory, disks and free space look normal at the moment."),
                        msg("If the PC is slow only sometimes, run the analysis again while it is slow.")))
    return rep


def _volumes_only(probe):
    snap = health.HealthSnapshot()
    snap.volumes = [v for v in probe.volumes() if (v.get("total") or 0) >= health.MIN_VOLUME]
    return snap


def _slim(p: dict) -> dict:
    return {k: p.get(k) for k in ("pid", "name", "exe", "cpu_percent", "rss", "io_bps")}


def explain(name: str) -> str | None:
    return KNOWN_ACTIVITY.get((name or "").lower())


def _cpu(rep: Report, total: float | None, procs: list[dict], services: dict[int, list[str]]) -> None:
    if total is None:
        return
    heavy = [p for p in sorted(procs, key=lambda p: -(p.get("cpu_percent") or 0)) if (p.get("cpu_percent") or 0) >= PROC_NOTE]
    if total < CPU_WARN and not heavy:
        return
    level = Level.PROBLEM if total >= CPU_PROBLEM else (Level.WARN if total >= CPU_WARN else Level.INFO)
    f = rep.add(Finding("cpu.background", "doctor", level, msg("High processor load in the background"),
                        msg("The processor was {p:.0f} % busy while AKENO measured.", p=total),
                        msg("Check the processes below. A program you do not need can be closed, or its start with "
                            "Windows disabled in Startup Analyzer.")))
    f.measured(msg("{p:.0f} % total processor load over the measurement", p=total), "psutil")
    for p in heavy[:5]:
        name = p.get("name") or "?"
        extra = ""
        if name.lower() == "svchost.exe" and services.get(p.get("pid")):
            extra = " (" + ", ".join(services[p["pid"]][:4]) + ")"
        f.measured(msg("{name}{extra}: {p:.0f} % of the processor", name=name, extra=extra, p=p["cpu_percent"]),
                   "psutil")
        why = explain(name)
        if why:
            f.cause(msg(why))
    f.link("doctor", msg("Review startup programs"), "startup")
    f.data["processes"] = [_slim(p) for p in heavy[:8]]


def _changes(rep: Report, procs: list[dict], previous: dict | None) -> None:
    """Processes that are busy now but were not among the busy ones at the last check."""
    if not previous or not previous.get("top_cpu"):
        return
    before = {(p.get("name") or "").lower() for p in previous["top_cpu"]}
    new = [p for p in procs if (p.get("cpu_percent") or 0) >= 5 and (p.get("name") or "").lower() not in before
           and (p.get("name") or "").lower() not in WINDOWS_CORE]
    if not new:
        return
    when = previous.get("created_at", "")[:16].replace("T", " ")
    f = rep.add(Finding("cpu.new_load", "doctor", Level.INFO, msg("Processes busier than at the last check"),
                        msg("These processes use the processor now but did not at the last check ({when}).", when=when),
                        msg("If the slowdown started recently, these are good candidates to look at.")))
    for p in sorted(new, key=lambda p: -p["cpu_percent"])[:5]:
        f.correlation(msg("{name}: {p:.0f} % now, not among the busy processes on {when}", name=p.get("name"),
                          p=p["cpu_percent"], when=when), "AKENO history")


def _memory(rep: Report, mem: dict, perf: dict | None, procs: list[dict]) -> None:
    pct = mem.get("percent")
    pm = (perf or {}).get("memory") or {}
    paging = pm.get("PagesPersec")
    if pct is None:
        return
    if pct < RAM_WARN and not (paging and paging >= PAGING_HIGH and pct >= 75):
        return
    level = Level.PROBLEM if pct >= RAM_PROBLEM else Level.WARN
    f = rep.add(Finding("ram.pressure", "doctor", level, msg("Memory is nearly full"),
                        msg("{p:.0f} % of {total} is in use.", p=pct, total=human_bytes(mem.get("total"))),
                        msg("Close programs that use a lot of memory, or open fewer browser tabs. With this little free "
                            "memory Windows moves data to the disk, which is much slower.")))
    f.measured(msg("{used} in use, {free} available", used=human_bytes(mem.get("used")),
                   free=human_bytes(mem.get("available"))), "psutil")
    if paging is not None:
        f.measured(msg("{n:.0f} pages per second read from or written to the page file", n=float(paging)),
                   "Windows performance counters")
        if paging >= PAGING_HIGH:
            f.cause(msg("Windows is paging heavily: programs wait for the disk instead of memory."))
    for p in sorted(procs, key=lambda p: -(p.get("rss") or 0))[:3]:
        f.measured(msg("{name}: {mem}", name=p.get("name"), mem=human_bytes(p.get("rss"))), "psutil")


def _disk(rep: Report, activity: dict | None, procs: list[dict]) -> None:
    if not activity:
        return
    busy = [(name, d) for name, d in (activity.get("disks") or {}).items()
            if d.get("busy_percent") is not None and d["busy_percent"] >= DISK_BUSY]
    if not busy:
        return
    f = rep.add(Finding("disk.busy", "doctor", Level.WARN, msg("A disk is constantly busy"),
                        msg("At least one disk was busy for most of the measurement."),
                        msg("Programs that read or write a lot are listed below. A disk that is always busy makes "
                            "everything that loads from it wait.")))
    for name, d in busy:
        f.measured(msg("{disk}: busy {p:.0f} % of the time, {r}/s read, {w}/s written", disk=name,
                       p=d["busy_percent"], r=human_bytes(d.get("read_bps")), w=human_bytes(d.get("write_bps"))),
                   "psutil")
    for p in sorted((p for p in procs if p.get("io_bps")), key=lambda p: -p["io_bps"])[:4]:
        f.measured(msg("{name}: {rate}/s disk I/O", name=p.get("name"), rate=human_bytes(p["io_bps"])), "psutil")
        why = explain(p.get("name"))
        if why:
            f.cause(msg(why))


def _startup(rep: Report, entries: list | None) -> None:
    if entries is None:
        return
    enabled = [e for e in entries if e.enabled]
    third = [e for e in enabled if not e.windows_component]
    flagged = [e for e in enabled if e.problems]
    if flagged:
        f = rep.add(Finding("startup.flagged", "doctor", Level.WARN, msg("Startup entries need a look"),
                            msg("{n} programs that start with Windows have a problem (missing file, unusual "
                                "location or no publisher).", n=len(flagged)),
                            msg("Open Startup Analyzer to see the details and disable what you do not need.")))
        for e in flagged[:5]:
            f.measured(msg("{name}: {why}", name=e.name, why="; ".join(str(p) for p in e.problems)), e.source)
        f.link("doctor", msg("Open Startup Analyzer"), "startup")
    if len(third) >= STARTUP_MANY:
        f = rep.add(Finding("startup.many", "doctor", Level.INFO, msg("Many programs start with Windows"),
                            msg("{n} programs from other publishers start when you sign in.", n=len(third)),
                            msg("Each one adds to the time until the desktop is ready. Disable the ones you do not "
                                "need right after signing in.")))
        f.measured(msg("{n} enabled startup entries", n=len(enabled)), "Startup Analyzer")
        f.link("doctor", msg("Open Startup Analyzer"), "startup")


def _thermal(rep: Report, perf: dict | None, events: dict, cpu_total: float | None, probe) -> None:
    limited = (events or {}).get("cpu_limited") or []
    if limited:
        f = rep.add(Finding("thermal.firmware", "doctor", Level.WARN,
                            msg("The processor speed was limited by the firmware"),
                            msg("Windows recorded {n} times in the last 14 days that the system firmware limited the "
                                "processor speed.", n=len(limited)),
                            msg("This happens when the PC gets too hot or the power supply or power settings limit "
                                "it. Check cooling and the power plan.")))
        f.measured(msg("{n} events \"Kernel-Processor-Power 37\", last on {when}", n=len(limited),
                       when=_date(limited[0].get("time"))), "Event Log: System")
        f.cause(msg("Overheating, or a firmware power limit (laptops on battery, BIOS power settings)."))
    pcpu = (perf or {}).get("cpu") or {}
    share = pcpu.get("PercentofMaximumFrequency")
    if share is not None and cpu_total is not None and cpu_total >= 50 and share < 70:
        f = rep.add(Finding("thermal.clock", "doctor", Level.WARN, msg("The processor runs below its rated clock"),
                            msg("Under {load:.0f} % load the processor ran at {share} % of its rated maximum frequency.",
                                load=cpu_total, share=share),
                            msg("Possible reasons are heat, a power-saving plan or a power limit. Check the power plan "
                                "in FPS Booster and the cooling.")))
        f.measured(msg("{share} % of the maximum frequency", share=share), "Windows performance counters")
        f.cause(msg("Thermal throttling or a power limit; the counter does not tell which."))
        f.link("booster", msg("Open FPS Booster"))
    try:
        gpus = probe.nvidia() or []
    except Exception:                                  # noqa: BLE001
        gpus = []
    for g in gpus:
        if g.get("thermal_hw") or g.get("thermal_sw"):
            f = rep.add(Finding("thermal.gpu." + g["name"], "doctor", Level.WARN,
                                msg("{gpu} slows down because of heat", gpu=g["name"]),
                                msg("The NVIDIA driver reports an active thermal slowdown at {t} °C.",
                                    t=g.get("temp_c") if g.get("temp_c") is not None else "?"),
                                msg("Check the GPU fans and the case air flow.")))
            f.measured(msg("thermal slowdown active"), "nvidia-smi")


def _events(rep: Report, events: dict, now: float | None) -> None:
    shut = (events or {}).get("unexpected_shutdown") or []
    if shut:
        f = rep.add(Finding("events.unexpected_shutdown", "doctor", Level.WARN,
                            msg("Windows was not shut down properly"),
                            msg("{n} unexpected restarts or power losses in the last 14 days.", n=len(shut)),
                            msg("Crash Detective shows whether a blue screen came before them.")))
        f.measured(msg("Last on {when}", when=_date(shut[0].get("time"))), "Event Log: System")
        f.link("doctor", msg("Open Crash Detective"), "crashes")
    whea = (events or {}).get("whea") or []
    if whea:
        f = rep.add(Finding("events.whea", "doctor", Level.WARN, msg("The hardware reported errors"),
                            msg("Windows Hardware Error Architecture recorded {n} hardware errors in the last 14 days.",
                                n=len(whea)),
                            msg("Corrected errors can be harmless; many of them, or uncorrected ones, point to "
                                "unstable overclocking, memory or the processor. Remove overclocks first.")))
        f.measured(msg("Last on {when}: {text}", when=_date(whea[0].get("time")), text=whea[0].get("message") or ""),
                   "Event Log: System (WHEA-Logger)")
    disk = [e for e in (events or {}).get("disk") or [] if any(k in (e.get("provider") or "").lower()
                                                               for k in DISK_PROVIDERS)]
    if disk:
        f = rep.add(Finding("events.disk", "doctor", Level.WARN, msg("Disk errors were recorded"),
                            msg("Windows recorded {n} disk or file system errors in the last 14 days.", n=len(disk)),
                            msg("Check the drive in Hardware Health and back up important data.")))
        for e in disk[:3]:
            f.measured(msg("{when} {provider} {id}: {text}", when=_date(e.get("time")), provider=e.get("provider"),
                           id=e.get("id"), text=e.get("message") or ""), "Event Log: System")
        f.link("doctor", msg("Open Hardware Health"), "hardware")


def _recent_changes(rep: Report, events: dict, now: float | None) -> None:
    items = []
    for key, label in (("driver_install", "Driver installed"), ("update_install", "Windows update"),
                       ("service_install", "Service installed")):
        for e in (events or {}).get(key) or []:
            if key == "update_install" and e.get("id") != 19:
                continue
            text = e.get("message") or ""
            if key == "service_install" and e.get("props"):
                text = str(e["props"][0])
            items.append((e.get("time") or "", label, text))
    if not items:
        return
    items.sort(reverse=True)
    f = rep.add(Finding("changes.recent", "doctor", Level.INFO, msg("Changes to Windows in the last 14 days"),
                        msg("{n} drivers, updates or services were installed.", n=len(items)),
                        msg("If the PC became slow at one of these times, the change may be related. Only the time "
                            "matches; this is not proof.")))
    for when, label, text in items[:8]:
        f.correlation(msg("{when} – {label}: {text}", when=_date(when), label=msg(label), text=text[:160]),
                      "Event Log: System")
    f.data["changes"] = [{"time": w, "kind": str(lb), "text": tx} for w, lb, tx in items[:30]]


def _date(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def within_days(iso: str | None, days: int, now: datetime | None = None) -> bool:
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return now - dt <= timedelta(days=days)


def svchost_services() -> dict[int, list[str]]:
    """Windows: the services each svchost.exe hosts (psutil's service list), so its load can be named."""
    import psutil
    out: dict[int, list[str]] = {}
    if not hasattr(psutil, "win_service_iter"):
        return out
    try:
        for s in psutil.win_service_iter():
            try:
                d = s.as_dict()
            except (psutil.Error, OSError):
                continue
            if d.get("pid"):
                out.setdefault(d["pid"], []).append(d.get("display_name") or d.get("name"))
    except (psutil.Error, OSError):
        pass
    return out


def previous_data(store) -> dict[str, Any] | None:
    last = store.latest("doctor.slowpc") if store is not None else None
    if not last:
        return None
    return dict(last.get("data") or {}, created_at=last.get("created_at") or "")
