"""Crash Detective: program crashes and hangs, blue screens, graphics driver resets and hardware errors from the
Windows event logs, turned into readable reports.

* Program crashes (Application Error 1000) are grouped by program and faulting module. A program that crashed
  three times or more in the period is a finding; its faulting module and exception code are explained in plain
  words, as *possible* causes.
* A blue screen (BugCheck 1001) names its stop code; the table below explains the usual causes.
* Driver resets (Display 4101) and hardware errors (WHEA-Logger) are counted.
* A driver or Windows update installed shortly before a series of crashes is shown as a *correlation in time*
  and never as the cause.

The faulting module of a game crash may be a mod's proxy DLL in the game folder; such a crash links to the
Universal Mod Scanner. Minidump files are listed when this user may read them (usually only administrators).
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from akeno.i18n import msg
from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.model import Finding, Level, Report
from akeno.system.tasks import TaskContext, context

REPEATED = 3                  # crashes of the same program and module in the period
CHANGE_WINDOW_DAYS = 3        # a change this long before the first crash of a series is shown as a correlation

EXCEPTIONS = {
    "c0000005": "Access violation: the program used memory it may not use (a bug in the program, a mod or "
                "a driver module it loaded).",
    "c0000409": "Stack buffer overrun or fail-fast: the program stopped itself after detecting a serious error.",
    "c0000374": "Heap corruption: the program's memory was damaged (program, mod or unstable memory).",
    "c000001d": "Illegal instruction: the processor met an instruction it could not run (an unstable overclock "
                "or a program built for newer processors).",
    "c0000142": "A DLL failed to initialise while the program started.",
    "c000041d": "An unhandled error inside a callback: often a driver or overlay hooking the program.",
    "e0434352": ".NET exception: the program's own error handling did not catch an error.",
    "80000003": "Breakpoint: the program stopped deliberately (an assertion or a debug build).",
    "c00000fd": "Stack overflow: the program called itself too deeply.",
    "c0000094": "Division by zero inside the program.",
    "e06d7363": "C++ exception the program did not catch.",
}
BUGCHECKS = {
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", "Usually a faulty driver; less often faulty memory."),
    0x1A: ("MEMORY_MANAGEMENT", "Memory problem: faulty RAM, an unstable memory overclock (XMP/EXPO) or a driver."),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", "A driver caused an error it did not handle."),
    0x3B: ("SYSTEM_SERVICE_EXCEPTION", "A driver or system component failed; often graphics or antivirus drivers."),
    0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", "Invalid memory access: a driver, faulty RAM or damaged system files."),
    0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "A system thread failed, usually in a driver."),
    0x7F: ("UNEXPECTED_KERNEL_MODE_TRAP", "Hardware problem or unstable overclocking; sometimes a driver."),
    0x9F: ("DRIVER_POWER_STATE_FAILURE", "A driver did not handle sleep or wake correctly."),
    0xC2: ("BAD_POOL_CALLER", "A driver used system memory incorrectly."),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "A driver accessed memory it may not access."),
    0xEF: ("CRITICAL_PROCESS_DIED", "An essential Windows process ended; damaged system files are a common cause."),
    0x101: ("CLOCK_WATCHDOG_TIMEOUT", "A processor core stopped responding: unstable overclock or a processor "
                                      "problem."),
    0x116: ("VIDEO_TDR_FAILURE", "The graphics driver could not recover from a reset: driver, GPU overclock or the "
                                 "graphics card."),
    0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", "The graphics driver stopped responding."),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "The hardware reported an error it could not correct: overclocking, "
                                        "voltage, temperature or a faulty component."),
    0x133: ("DPC_WATCHDOG_VIOLATION", "A driver took too long; often storage or network drivers or firmware."),
    0x139: ("KERNEL_SECURITY_CHECK_FAILURE", "A driver damaged a kernel data structure; also faulty RAM."),
    0x19: ("BAD_POOL_HEADER", "System memory was damaged, usually by a driver."),
    0x4E: ("PFN_LIST_CORRUPT", "Memory management data damaged: faulty RAM or a driver."),
    0xFC: ("ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "A driver tried to run code from protected memory."),
    0x1000007E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED_M", "A system thread failed, usually in a driver."),
    0x1000008E: ("KERNEL_MODE_EXCEPTION_NOT_HANDLED_M", "A driver caused an error it did not handle."),
}
GPU_MODULES = ("nvwgf2um", "nvoglv", "nvd3dum", "nvlddmkm", "nvapi", "nvgpucomp", "amdxx", "atiumd", "atidxx",
               "amdvlk", "igxel", "igd10", "igd12", "igdumd", "ig9icd", "igc64", "nvcuda", "nvwgf2umx")
WINDOWS_CORE_MODULES = ("ntdll.dll", "kernelbase.dll", "kernel32.dll", "ucrtbase.dll", "msvcrt.dll",
                        "combase.dll", "user32.dll")
REPAIR_BUGCHECKS = {0xEF, 0x50}


@dataclass
class CrashGroup:
    app: str
    module: str
    count: int = 0
    first: str = ""
    last: str = ""
    codes: set[str] = field(default_factory=set)
    app_path: str = ""
    module_path: str = ""
    versions: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {"app": self.app, "module": self.module, "count": self.count, "first": self.first, "last": self.last,
                "codes": sorted(self.codes), "app_path": self.app_path, "module_path": self.module_path,
                "versions": sorted(self.versions)}


QUERIES = ("app_crash", "app_hang", "wer", "bugcheck", "unexpected_shutdown", "gpu_reset", "whea", "driver_install",
           "update_install")


def analyse(probe, days: int = 14, ctx: TaskContext | None = None, minidump_dir: str | None = None,
            game_dirs: dict[str, str] | None = None, now: datetime | None = None) -> Report:
    """``game_dirs`` maps a library game's install folder to its name, so a crash inside a game folder can name
    the game and point to its mods."""
    ctx = context(ctx)
    rep = Report("doctor")
    ctx.report(msg("Reading Windows event logs"))
    try:
        ev = probe.events(QUERIES, days, 200, ctx)
    except CommandUnavailable as exc:
        rep.errors.append(str(exc))
        return rep
    except (QueryError, OSError) as exc:
        rep.errors.append(msg("Windows event logs: {e}", e=str(exc)))
        return rep
    for failed in ev.get("_failed", []):
        rep.errors.append(msg("Event log query {q}: {e}", q=failed.get("q"), e=failed.get("error")))
    ctx.check()
    changes = _changes(ev)
    groups = _app_groups(ev.get("app_crash", []))
    rep.data["crash_groups"] = [g.to_dict() for g in sorted(groups.values(), key=lambda g: -g.count)]
    rep.data["changes"] = changes
    for g in sorted(groups.values(), key=lambda g: -g.count):
        if g.count < REPEATED:
            continue
        _app_finding(rep, g, changes, game_dirs or {})
    hangs = _count_by(ev.get("app_hang", []), 0)
    for app, rows in hangs.items():
        if len(rows) >= REPEATED:
            f = rep.add(Finding("crash.hang." + app.lower(), "doctor", Level.WARN,
                                msg("{app} stopped responding repeatedly", app=app),
                                msg("Windows recorded {n} hangs in the last {d} days.", n=len(rows), d=days),
                                msg("Update the program. If it hangs while loading, check free disk space and the "
                                    "disk's health.")))
            f.measured(msg("Last on {when}", when=_date(rows[0].get("time"))), "Event Log: Application")
    _bugchecks(rep, ev.get("bugcheck", []), ev.get("unexpected_shutdown", []), changes, days)
    resets = ev.get("gpu_reset", [])
    if resets:
        f = rep.add(Finding("crash.gpu_reset", "doctor", Level.WARN if len(resets) >= 2 else Level.INFO,
                            msg("The graphics driver was reset"),
                            msg("Windows reset the graphics driver {n} times in the last {d} days because it stopped "
                                "responding.", n=len(resets), d=days),
                            msg("Common reasons: a GPU overclock or undervolt, an unstable driver version, overheating "
                                "or a power supply at its limit. Remove overclocks first, then try a clean driver "
                                "installation.")))
        f.measured(msg("Last on {when}: {text}", when=_date(resets[0].get("time")), text=resets[0].get("message", "")),
                   "Event Log: System (Display 4101)")
        _correlate(f, resets, changes)
    whea = ev.get("whea", [])
    if whea:
        f = rep.add(Finding("crash.whea", "doctor", Level.WARN, msg("Hardware errors were reported"),
                            msg("{n} hardware error reports (WHEA) in the last {d} days.", n=len(whea), d=days),
                            msg("Return overclocked processor, memory or graphics settings to their defaults. If the "
                                "errors continue, a component may be failing.")))
        f.measured(msg("Last on {when}: {text}", when=_date(whea[0].get("time")), text=whea[0].get("message", "")),
                   "Event Log: System (WHEA-Logger)")
    dumps = minidumps(minidump_dir)
    rep.data["minidumps"] = dumps
    if not rep.findings:
        rep.add(Finding("crash.none", "doctor", Level.OK, msg("No repeated crashes found"),
                        msg("No program crashed repeatedly and Windows recorded no blue screen in the last {d} days.",
                            d=days)))
    return rep


def _app_groups(rows: list[dict]) -> dict[tuple[str, str], CrashGroup]:
    groups: dict[tuple[str, str], CrashGroup] = {}
    for e in rows:
        p = e.get("props") or []
        app = (p[0] if len(p) > 0 else "") or "?"
        module = (p[3] if len(p) > 3 else "") or "?"
        key = (app.lower(), module.lower())
        g = groups.get(key)
        if g is None:
            g = groups[key] = CrashGroup(app, module)
        g.count += 1
        t = e.get("time") or ""
        g.first = min(g.first, t) if g.first else t
        g.last = max(g.last, t)
        if len(p) > 6 and p[6]:
            g.codes.add(_code(p[6]))
        if len(p) > 1 and p[1]:
            g.versions.add(p[1])
        if len(p) > 10 and p[10]:
            g.app_path = p[10]
        if len(p) > 11 and p[11]:
            g.module_path = p[11]
    return groups


def _code(raw: str) -> str:
    raw = str(raw).strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    try:
        return f"{int(raw, 16) & 0xFFFFFFFF:08x}"
    except ValueError:
        return raw


def _app_finding(rep: Report, g: CrashGroup, changes: list[dict], game_dirs: dict[str, str]) -> None:
    module = g.module.lower()
    game = None
    for folder, name in game_dirs.items():
        if g.app_path and os.path.normcase(g.app_path).startswith(os.path.normcase(folder)):
            game = name
    f = rep.add(Finding("crash.app." + g.app.lower() + "." + module, "doctor",
                        Level.PROBLEM if g.count >= 2 * REPEATED else Level.WARN,
                        msg("{app} crashed {n} times", app=g.app, n=g.count),
                        msg("Faulting module: {module}. First on {first}, last on {last}.", module=g.module,
                            first=_date(g.first), last=_date(g.last)),
                        msg("Update the program. The faulting module below narrows down where the error happened.")))
    f.measured(msg("{n} crash reports \"Application Error 1000\"", n=g.count), "Event Log: Application")
    for code in sorted(g.codes):
        text = EXCEPTIONS.get(code)
        f.measured(msg("Exception code 0x{code}", code=code.upper()), "Event Log: Application")
        if text:
            f.cause(msg(text))
    if any(module.startswith(m) for m in GPU_MODULES):
        f.cause(msg("The faulting module belongs to the graphics driver: try the current driver, or a clean "
                    "installation of the previous one."))
    elif module in WINDOWS_CORE_MODULES:
        f.cause(msg("The faulting module is part of Windows; the error is usually in the program itself or in a "
                    "module it loaded, not in Windows."))
    if g.module_path and g.app_path and os.path.dirname(os.path.normcase(g.module_path)) == \
            os.path.dirname(os.path.normcase(g.app_path)) and module.endswith(".dll"):
        from akeno.games.analyzer import PROXY_NAMES
        if module in PROXY_NAMES:
            f.cause(msg("{module} was loaded from the game's own folder. That is where graphics mods put their "
                        "proxy DLLs; check the installed mods.", module=g.module))
            f.link("installed", msg("Open Installed Mods"))
    if game:
        f.data["game"] = game
        f.link("installed", msg("Check this game's mods"))
    _correlate(f, [{"time": g.first}], changes)


def _bugchecks(rep: Report, bugchecks: list[dict], shutdowns: list[dict], changes: list[dict], days: int) -> None:
    codes: dict[int, list[dict]] = defaultdict(list)
    for e in bugchecks:
        p = e.get("props") or []
        code = parse_bugcheck(p[0] if p else e.get("message", ""))
        codes[code if code is not None else -1].append(e)
    for e in shutdowns:
        if e.get("id") == 41:
            p = e.get("props") or []
            try:
                code = int(p[0]) if p and str(p[0]).strip() else 0
            except ValueError:
                code = 0
            if code and not any(_close(e.get("time"), b.get("time")) for b in bugchecks):
                codes[code].append(e)
    for code, rows in codes.items():
        name, why = BUGCHECKS.get(code, (None, None)) if code > 0 else (None, None)
        title = msg("Blue screen {name}", name=name) if name else (
            msg("Blue screen with stop code 0x{code:X}", code=code) if code > 0 else msg("Blue screen"))
        f = rep.add(Finding(f"crash.bugcheck.{code:x}" if code > 0 else "crash.bugcheck", "doctor", Level.PROBLEM,
                            title,
                            msg("Windows stopped with a blue screen {n} times in the last {d} days.", n=len(rows), d=days),
                            msg("Note the stop code. Update drivers (graphics, chipset, storage) and the BIOS, and "
                                "remove overclocks. If it keeps happening, the guided repair checks Windows' system "
                                "files.")))
        if code > 0:
            f.measured(msg("Stop code 0x{code:08X}, last on {when}", code=code, when=_date(rows[0].get("time"))),
                       "Event Log: System")
        if why:
            f.cause(msg(why))
        if code in REPAIR_BUGCHECKS:
            f.link("doctor", msg("Open Guided Repair"), "repair")
        _correlate(f, rows, changes)
    unexplained = [e for e in shutdowns if e.get("id") in (41, 6008)
                   and not any(_close(e.get("time"), b.get("time")) for b in bugchecks)]
    unexplained = [e for e in unexplained if not (e.get("id") == 41 and _bugcode(e))]
    if unexplained:
        f = rep.add(Finding("crash.power_loss", "doctor", Level.WARN, msg("The PC restarted without warning"),
                            msg("{n} restarts without a blue screen report in the last {d} days.", n=len(unexplained),
                                d=days),
                            msg("Without a stop code, power is the usual suspect: the power supply, a power cut, "
                                "overheating that switched the PC off, or holding the power button.")))
        f.measured(msg("Last on {when}", when=_date(unexplained[0].get("time"))), "Event Log: System (Kernel-Power 41)")


def _bugcode(e: dict) -> int:
    p = e.get("props") or []
    try:
        return int(p[0]) if p and str(p[0]).strip() else 0
    except ValueError:
        return 0


def parse_bugcheck(text: str) -> int | None:
    """"0x0000009f (0x...)" or "0x9f" -> 0x9F."""
    import re
    m = re.search(r"0x([0-9a-fA-F]{1,8})", str(text or ""))
    return int(m.group(1), 16) if m else None


def _changes(ev: dict) -> list[dict]:
    out = []
    for e in ev.get("driver_install", []):
        out.append({"time": e.get("time"), "kind": "driver", "text": e.get("message") or ""})
    for e in ev.get("update_install", []):
        if e.get("id") == 19:
            out.append({"time": e.get("time"), "kind": "update", "text": e.get("message") or ""})
    return sorted(out, key=lambda c: c.get("time") or "", reverse=True)


def _correlate(f: Finding, rows: list[dict], changes: list[dict]) -> None:
    times = [r.get("time") for r in rows if r.get("time")]
    if not times or not changes:
        return
    first = _dt(min(times))
    if first is None:
        return
    for c in changes:
        t = _dt(c.get("time"))
        if t is not None and first - timedelta(days=CHANGE_WINDOW_DAYS) <= t <= first:
            label = msg("a driver") if c["kind"] == "driver" else msg("a Windows update")
            f.correlation(msg("{what} was installed on {when}, shortly before: {text}", what=label,
                              when=_date(c["time"]), text=(c.get("text") or "")[:140]), "Event Log: System")


def _count_by(rows: list[dict], index: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for e in rows:
        p = e.get("props") or []
        out[(p[index] if len(p) > index else "") or "?"].append(e)
    return out


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _close(a: str | None, b: str | None, minutes: int = 15) -> bool:
    x, y = _dt(a), _dt(b)
    return bool(x and y and abs((x - y).total_seconds()) <= minutes * 60)


def _date(iso: str | None) -> str:
    d = _dt(iso)
    return d.astimezone().strftime("%Y-%m-%d %H:%M") if d else "?"


def minidumps(folder: str | None = None) -> dict[str, Any]:
    folder = folder or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Minidump")
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".dmp")]
    except PermissionError:
        return {"folder": folder, "files": [], "note": str(msg("Only administrators can read this folder."))}
    except OSError:
        return {"folder": folder, "files": [], "note": str(msg("No minidump folder."))}
    files = []
    for n in names:
        try:
            st = os.stat(os.path.join(folder, n))
            files.append({"name": n, "size": st.st_size, "mtime": st.st_mtime})
        except OSError:
            continue
    return {"folder": folder, "files": sorted(files, key=lambda f: -f["mtime"])[:50], "note": ""}
