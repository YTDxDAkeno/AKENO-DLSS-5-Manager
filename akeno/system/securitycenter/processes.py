"""Suspicious Process Analyzer: running programs with a concrete reason to take a closer look.

A finding needs a reason that holds up on its own, never just "unknown" or "high CPU":

* a program carries the name of a Windows component (svchost.exe, lsass.exe, ...) but runs from somewhere else;
* a name one letter away from such a component ("scvhost.exe", "lsas.exe") runs from outside Windows and is
  unsigned;
* the program's own file name hides its type ("invoice.pdf.exe");
* an unsigned program runs from a temporary or download folder *and* holds internet connections - each alone is
  common (installers, portable tools), together they are worth a look;
* Microsoft Defender lists the program's file in an unresolved detection.

Everything else is shown as plain information. AKENO never ends, suspends or deletes anything from here; the next
step is a check of the file with the antivirus.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import psutil

from akeno.i18n import msg
from akeno.system.model import Finding, Level, Report
from akeno.system.securitycenter.filecheck import double_extension
from akeno.system.tasks import TaskContext, context

WINDOWS_NAMES = {"svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "wininit.exe",
                 "spoolsv.exe", "taskhostw.exe", "dwm.exe", "conhost.exe", "rundll32.exe", "dllhost.exe",
                 "sihost.exe", "fontdrvhost.exe", "ctfmon.exe", "lsaiso.exe", "searchindexer.exe",
                 "runtimebroker.exe", "audiodg.exe", "wmiprvse.exe", "msmpeng.exe", "explorer.exe", "taskmgr.exe",
                 "regsvr32.exe", "mshta.exe", "wscript.exe", "cscript.exe", "powershell.exe", "cmd.exe"}
# where each may legitimately live (below %SystemRoot%)
WINDOWS_DIRS = ("system32", "syswow64", "sysnative", "winsxs", "servicing", "systemapps", "immersivecontrolpanel")
DEFENDER_DIRS = ("windows defender", "microsoft\\windows defender\\platform")
MAX_SIGNED = 400
NOT_LOOKALIKES = {"service.exe", "taskhost.exe", "cmdhost.exe", "svchost32.exe"}   # common names of real tools


@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: str | None
    location: str = "other"                     # windows | program_files | temp | downloads | appdata | other
    signature: dict[str, Any] = field(default_factory=dict)
    connections: int = 0
    cpu_percent: float | None = None
    rss: int | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _lev1(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` differ by exactly one edit (insert, delete, replace or swap of neighbours)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def lookalike(name: str) -> str | None:
    n = name.lower()
    if n in WINDOWS_NAMES or n in NOT_LOOKALIKES or len(n) < 8:
        return None
    for w in WINDOWS_NAMES:
        if len(w) >= 9 and _lev1(n, w):
            return w
    return None


def classify(exe: str, env: dict) -> str:
    p = ntpath.normcase(ntpath.normpath(exe))
    root = ntpath.normcase(env.get("SystemRoot") or env.get("windir") or r"C:\Windows")

    def under(base: str | None) -> bool:
        if not base:
            return False
        b = ntpath.normcase(ntpath.normpath(base)).rstrip("\\") + "\\"
        return p.startswith(b)

    if under(root):
        return "windows"
    if under(env.get("TEMP")) or under(env.get("TMP")) or "\\$recycle.bin\\" in p or \
            under(ntpath.join(env.get("LOCALAPPDATA") or "", "Temp")):
        return "temp"
    if under(ntpath.join(env.get("USERPROFILE") or "", "Downloads")):
        return "downloads"
    if under(env.get("ProgramFiles")) or under(env.get("ProgramFiles(x86)")) or under(env.get("ProgramW6432")):
        return "program_files"
    if under(env.get("APPDATA")) or under(env.get("LOCALAPPDATA")):
        return "appdata"
    if under(env.get("PUBLIC")) or under(env.get("ProgramData")):
        return "shared"
    return "other"


def windows_home(exe: str, env: dict) -> bool:
    p = ntpath.normcase(ntpath.normpath(exe))
    root = ntpath.normcase(env.get("SystemRoot") or env.get("windir") or r"C:\Windows").rstrip("\\")
    if ntpath.basename(p) in ("explorer.exe",) and ntpath.dirname(p) == root:
        return True
    rel = p[len(root) + 1:] if p.startswith(root + "\\") else None
    if rel is not None and rel.split("\\", 1)[0] in WINDOWS_DIRS:
        return True
    pf = [ntpath.normcase(env.get(k) or "") for k in ("ProgramFiles", "ProgramData")]
    return any(b and p.startswith(b.rstrip("\\") + "\\" + d + "\\") for b in pf for d in DEFENDER_DIRS)


def collect(ctx: TaskContext | None = None) -> list[ProcessInfo]:
    ctx = context(ctx)
    out = []
    from akeno.system.network import bandwidth
    conns = {pid: p.tcp + p.udp for pid, p in bandwidth.connections().items()}
    for p in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
        ctx.check()
        info = p.info
        if not info.get("pid"):
            continue
        mem = info.get("memory_info")
        out.append(ProcessInfo(info["pid"], info.get("name") or "?", info.get("exe") or None,
                               connections=conns.get(info["pid"], 0), rss=getattr(mem, "rss", None)))
    return out


def analyse(procs: list[ProcessInfo], signer: Callable[[list[str], TaskContext], dict], env: dict | None = None,
            detections: list[dict] | None = None, ctx: TaskContext | None = None) -> Report:
    ctx = context(ctx)
    env = env if env is not None else dict(os.environ)
    report = Report("security")
    for p in procs:
        if p.exe:
            p.location = classify(p.exe, env)
    # signatures: every program outside Windows' own folders (those are checked for their name only)
    paths = sorted({p.exe for p in procs if p.exe and p.location != "windows"})[:MAX_SIGNED]
    ctx.report(msg("Checking digital signatures"), 0, 2)
    sigs = signer(paths, ctx) if paths else {}
    for p in procs:
        s = sigs.get(p.exe) if p.exe else None
        if s is not None:
            p.signature = s.to_dict() if hasattr(s, "to_dict") else dict(s)
    flagged_paths = {ntpath.normcase(r) for d in detections or [] if d.get("unresolved")
                     for r in d.get("resources", [])}
    seen: set[str] = set()
    for p in procs:
        if not p.exe:
            continue
        key = ntpath.normcase(p.exe)
        if key in seen:
            continue
        seen.add(key)
        name = ntpath.basename(p.exe)
        lower = name.lower()
        unsigned = p.signature.get("status") in ("unsigned", "invalid")
        f = None
        if key in flagged_paths:
            f = Finding("proc.detected." + lower, "security", Level.PROBLEM,
                        msg("{name} is listed in an unresolved Microsoft Defender detection", name=name),
                        p.exe, msg("Open Protection history in Windows Security and follow Defender's advice."))
            f.measured(msg("Path matches a detection in Protection history"), "Get-MpThreatDetection")
            f.link("security", msg("Open protection history"), "history")
            p.flags.append("detected")
        elif lower in WINDOWS_NAMES and not windows_home(p.exe, env):
            f = Finding("proc.masquerade." + lower, "security", Level.WARN,
                        msg("{name} runs from an unusual folder", name=name),
                        msg("This name belongs to a Windows component, which lives in the Windows folder. This "
                            "one runs from {path}.", path=ntpath.dirname(p.exe)),
                        msg("Check this file with the antivirus. AKENO does not end the program."))
            f.measured(msg("Program path: {path}", path=p.exe), "psutil")
            p.flags.append("masquerade")
        elif lookalike(lower) and p.location != "windows" and unsigned:
            f = Finding("proc.lookalike." + lower, "security", Level.WARN,
                        msg("{name} looks like the Windows component {real}", name=name, real=lookalike(lower)),
                        msg("One letter differs, it runs from {path} and has no valid signature.",
                            path=ntpath.dirname(p.exe)),
                        msg("Check this file with the antivirus. AKENO does not end the program."))
            f.measured(msg("Name, folder and signature"), "psutil, Get-AuthenticodeSignature")
            p.flags.append("lookalike")
        elif double_extension(name):
            f = Finding("proc.double." + lower, "security", Level.WARN,
                        msg("{name} hides its real type behind a second extension", name=name), p.exe,
                        msg("Check this file with the antivirus."))
            f.measured(msg("Program path: {path}", path=p.exe), "psutil")
            p.flags.append("double_extension")
        elif unsigned and p.location in ("temp", "downloads") and p.connections > 0:
            f = Finding("proc.temp_net." + lower, "security", Level.WARN,
                        msg("Unsigned {name} from a {where} folder is online", name=name,
                            where=msg("temporary") if p.location == "temp" else msg("download")),
                        msg("Internet connections: {n}; path {path}", n=p.connections, path=p.exe),
                        msg("Each of these alone is common; together they are worth a check of the file with "
                            "the antivirus."))
            f.measured(msg("Folder, signature and connections"), "psutil, Get-AuthenticodeSignature")
            p.flags.append("temp_network")
        if f is not None:
            f.link("security", msg("Check the file"), "file:" + p.exe)
            f.data = {"pid": p.pid, "exe": p.exe}
            report.add(f)
    report.data = {"processes": [p.to_dict() for p in procs], "signed_checked": len(paths)}
    if not report.findings:
        report.add(Finding("proc.none", "security", Level.OK, msg("No running program shows a concrete warning sign"),
                           msg("Processes looked at: {n}; names, folders and signatures checked.", n=len(procs)),
                           msg("This is no malware scan. A full scan with the antivirus checks the files "
                               "themselves.")))
    return report
