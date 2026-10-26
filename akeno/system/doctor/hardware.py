"""Hardware Health: drive health from the Windows storage stack, temperatures, memory and the recorded history.

Drive statements come from the drive itself (its SMART/NVMe health, reported by Windows as ``HealthStatus``,
``OperationalStatus``, the ``Win32_DiskDrive`` prediction flag and the reliability counters). AKENO repeats what the
drive reports and recommends a backup when the drive itself warns; it never predicts a failure on its own.
Reliability counters (temperature, wear, error counts) are readable only with administrator rights on most PCs;
then they are shown as unavailable with that reason, and can be read once through the UAC prompt.
"""

from __future__ import annotations

import json
import os
from typing import Any

from akeno.i18n import msg
from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.model import Finding, Level, Report, human_bytes
from akeno.system.tasks import TaskContext, context

NVME_HOT_C = 70
WEAR_WARN = 90


def analyse_drives(data: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[Finding]]:
    """(one row per drive for the table, findings)."""
    rows: list[dict[str, Any]] = []
    findings: list[Finding] = []
    if not data:
        return rows, findings
    smart = {str(s.get("Model") or "").strip().lower(): s for s in data.get("smart") or []}
    for d in data.get("disks") or []:
        name = str(d.get("name") or "?").strip()
        health = d.get("health") or ""
        ops = [str(o) for o in d.get("operational") or []]
        s = smart.get(name.lower()) or {}
        row = {"name": name, "media": d.get("media") or "", "bus": d.get("bus") or "", "size": d.get("size"),
               "health": health, "operational": ops, "smart_status": s.get("Status"),
               "temperature": d.get("temperature"), "temperature_max": d.get("temperature_max"), "wear": d.get("wear"),
               "read_errors": d.get("read_errors_uncorrected"), "write_errors": d.get("write_errors_uncorrected"),
               "power_on_hours": d.get("power_on_hours"),
               "counters_note": _counter_note(d.get("reliability_error")) if d.get("reliability_error") else ""}
        rows.append(row)
        if health in ("Unhealthy",) or (s.get("Status") or "").lower() == "pred fail":
            f = Finding("drive.failing." + name, "doctor", Level.PROBLEM, msg("{d} reports a problem", d=name),
                        msg("The drive's own health monitoring reports it as failing."),
                        msg("Back up important data from this drive now and plan to replace it."))
            if health == "Unhealthy":
                f.measured(msg("Health status: Unhealthy"), "Windows Storage (MSFT_PhysicalDisk)")
            if (s.get("Status") or "").lower() == "pred fail":
                f.measured(msg("SMART failure prediction: Pred Fail"), "Win32_DiskDrive")
            findings.append(f)
        elif health == "Warning" or any(o in ("Predictive Failure", "Degraded", "Stressed") for o in ops):
            f = Finding("drive.warning." + name, "doctor", Level.WARN, msg("{d} reports a warning", d=name),
                        msg("Windows reports the drive's health as \"{h}\" ({ops}).", h=health or "?",
                            ops=", ".join(ops) or "—"),
                        msg("Back up important data. The drive's manufacturer tool shows the details."))
            f.measured(msg("Health status: {h}", h=health), "Windows Storage (MSFT_PhysicalDisk)")
            findings.append(f)
        wear = d.get("wear")
        if isinstance(wear, (int, float)) and wear >= WEAR_WARN:
            f = Finding("drive.wear." + name, "doctor", Level.WARN, msg("{d} has used most of its rated endurance",
                                                                        d=name),
                        msg("The SSD reports {w} % of its rated write endurance as used.", w=wear),
                        msg("SSDs often last beyond their rating, but keep backups and plan a replacement."))
            f.measured(msg("Wear: {w} %", w=wear), "Get-StorageReliabilityCounter")
            findings.append(f)
        temp, tmax = d.get("temperature"), d.get("temperature_max")
        if isinstance(temp, (int, float)) and temp > 0 and (temp >= NVME_HOT_C or (tmax and temp >= tmax)):
            f = Finding("drive.hot." + name, "doctor", Level.WARN, msg("{d} is hot", d=name),
                        msg("The drive reports {t} °C.", t=temp),
                        msg("Hot SSDs slow down to protect themselves. A heatsink or better air flow helps."))
            f.measured(msg("{t} °C (limit {m} °C)", t=temp, m=tmax or "?"), "Get-StorageReliabilityCounter")
            findings.append(f)
        for key, label in (("read_errors_uncorrected", "uncorrected read errors"),
                           ("write_errors_uncorrected", "uncorrected write errors")):
            v = d.get(key)
            if isinstance(v, (int, float)) and v > 0:
                f = Finding(f"drive.errors.{name}.{key}", "doctor", Level.WARN,
                            msg("{d} reports errors", d=name), msg("{n} {what}.", n=int(v), what=msg(label)),
                            msg("Back up important data and check the drive with its manufacturer's tool."))
                f.measured(msg("{n} {what}", n=int(v), what=msg(label)), "Get-StorageReliabilityCounter")
                findings.append(f)
    return rows, findings


def _counter_note(error: str) -> str:
    low = (error or "").lower()
    if "access" in low or "zugriff" in low or "denied" in low or "verweigert" in low:
        return str(msg("Reliability counters need administrator rights."))
    return str(error)


def analyse(probe, ctx: TaskContext | None = None, elevated: dict | None = None) -> Report:
    """``elevated``: the result of :func:`read_elevated` when the user chose to read the counters as administrator."""
    ctx = context(ctx)
    rep = Report("doctor")
    ctx.report(msg("Reading drive health"))
    data = elevated
    if data is None:
        try:
            data = probe.physical_disks(ctx)
        except CommandUnavailable as exc:
            rep.errors.append(str(exc))
            data = None
        except (QueryError, OSError) as exc:
            rep.errors.append(msg("Drive health: {e}", e=str(exc)))
            data = None
    if data and data.get("error"):
        rep.errors.append(msg("Windows Storage: {e}", e=data["error"]))
    rows, findings = analyse_drives(data)
    rep.data["drives"] = rows
    rep.data["elevated"] = elevated is not None
    rep.data["counters_need_admin"] = any(r.get("counters_note") for r in rows)
    for f in findings:
        rep.add(f)
    if rows and not findings:
        rep.add(Finding("drive.ok", "doctor", Level.OK, msg("The drives report no problems"),
                        msg("{n} drives report a healthy state.", n=len(rows))))
    return rep


def read_elevated(out_dir: str, ctx: TaskContext | None = None) -> dict[str, Any] | None:
    """Read the drive data once with administrator rights (UAC) into a file in AKENO's folder; None when the user
    declined. The elevated PowerShell only reads; it writes nothing but that one file."""
    from akeno.system.commands import powershell_exe, run_elevated
    from akeno.system.doctor.providers import DISK_RESULT, DISK_SCRIPT
    import base64
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "drive-health.json")
    try:
        os.unlink(target)
    except OSError:
        pass
    assert DISK_RESULT in DISK_SCRIPT
    script = ("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
              + DISK_SCRIPT.replace(DISK_RESULT, DISK_RESULT + " | ConvertTo-Json -Depth 5 -Compress | "
                                    "Set-Content -LiteralPath '" + target.replace("'", "''") + "' -Encoding UTF8"))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    code = run_elevated(powershell_exe(), f"-NoLogo -NoProfile -NonInteractive -EncodedCommand {encoded}", ctx,
                        timeout=180, show=False)
    if code is None:
        return None
    try:
        with open(target, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise OSError(msg("The administrator read did not produce a result ({e}).", e=str(exc))) from exc
    from akeno.system.commands import as_list
    return {"disks": as_list(data.get("disks")), "smart": as_list(data.get("smart")), "error": data.get("storage_error")}


def memory_breakdown(mem: dict, perf: dict | None, procs: list[dict]) -> dict[str, Any]:
    """For the RAM analysis: totals, commit charge and the processes that hold the most."""
    pm = (perf or {}).get("memory") or {}
    top = sorted(procs, key=lambda p: -(p.get("rss") or 0))[:10]
    return {"total": mem.get("total"), "used": mem.get("used"), "available": mem.get("available"),
            "percent": mem.get("percent"), "swap_used": mem.get("swap_used"), "swap_total": mem.get("swap_total"),
            "commit": pm.get("CommittedBytes"), "commit_limit": pm.get("CommitLimit"),
            "paging": pm.get("PagesPersec"),
            "top": [{"name": p.get("name"), "rss": p.get("rss"), "text": human_bytes(p.get("rss"))} for p in top]}
