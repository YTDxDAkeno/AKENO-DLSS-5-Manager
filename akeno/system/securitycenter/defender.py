"""Microsoft Defender through its own documented tools.

* status, threat history: the Defender PowerShell module (``Get-MpComputerStatus``, ``Get-MpThreatDetection``,
  ``Get-MpThreat``) - read-only;
* scans: ``MpCmdRun.exe -Scan`` (quick, full, custom). A quick, full or custom scan the user starts is a normal
  Defender scan: Defender handles what it finds by its own settings and lists it in Protection history, exactly
  like the button in Windows Security. A *check* (``report_only``) adds ``-DisableRemediation``: Defender only
  reports, nothing is changed, moved or quarantined - used for downloads and for external mods, which AKENO keeps
  read-only;
* signature update: ``Update-MpSignature``.

AKENO never calls ``Set-MpPreference``/``Add-MpPreference``: no exclusions, no switched-off protection, no change
to Tamper Protection.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system.commands import CommandUnavailable, QueryError, as_list, first_line, powershell_json, run
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

NEVER = 4294967295                                       # QuickScanAge / FullScanAge when there was none
SCAN_TYPES = {"quick": 1, "full": 2, "custom": 3}
SCAN_TIMEOUT = {"quick": 3 * 3600.0, "full": 24 * 3600.0, "custom": 6 * 3600.0}
RUNNING_MODES = {"normal": "normal", "passive mode": "passive", "sxs passive mode": "passive",
                 "edr block mode": "edr_block", "not running": "off"}
THREAT_STATUS = {0: "unknown", 1: "detected", 2: "cleaned", 3: "quarantined", 4: "removed", 5: "allowed",
                 6: "blocked", 102: "quarantine_failed", 103: "remove_failed", 104: "allow_failed",
                 105: "abandoned", 107: "block_failed"}
UNRESOLVED = {"detected", "quarantine_failed", "remove_failed", "allow_failed", "abandoned", "block_failed"}
SEVERITY = {0: "unknown", 1: "low", 2: "moderate", 4: "high", 5: "severe"}
COMPUTER_STATE = {1: "full_scan", 2: "reboot", 4: "manual_steps", 8: "offline_scan", 16: "critical_failure"}

STATUS_SCRIPT = (
    "Get-MpComputerStatus -ErrorAction Stop | Select-Object AMServiceEnabled,AntivirusEnabled,AntispywareEnabled,"
    "RealTimeProtectionEnabled,BehaviorMonitorEnabled,IoavProtectionEnabled,OnAccessProtectionEnabled,NISEnabled,"
    "IsTamperProtected,AMRunningMode,AMProductVersion,AMEngineVersion,AntivirusSignatureVersion,"
    "AntivirusSignatureAge,DefenderSignaturesOutOfDate,QuickScanAge,FullScanAge,ComputerState,"
    "@{n='SignatureUpdated';e={if($_.AntivirusSignatureLastUpdated){$_.AntivirusSignatureLastUpdated.ToString('o')}}},"
    "@{n='QuickScanEnd';e={if($_.QuickScanEndTime){$_.QuickScanEndTime.ToString('o')}}},"
    "@{n='FullScanEnd';e={if($_.FullScanEndTime){$_.FullScanEndTime.ToString('o')}}}")

HISTORY_SCRIPT = (
    "$d=@(Get-MpThreatDetection -ErrorAction Stop | Select-Object ThreatID,ThreatStatusID,ActionSuccess,Resources,"
    "ProcessName,DetectionSourceTypeID,"
    "@{n='Time';e={if($_.InitialDetectionTime){$_.InitialDetectionTime.ToString('o')}}},"
    "@{n='Changed';e={if($_.LastThreatStatusChangeTime){$_.LastThreatStatusChangeTime.ToString('o')}}});"
    "$t=@(Get-MpThreat -ErrorAction Stop | Select-Object ThreatID,ThreatName,SeverityID,CategoryID,IsActive,"
    "DidThreatExecute);"
    "@{detections=$d;threats=$t}")


@dataclass
class ScanResult:
    kind: str                                    # quick | full | custom
    target: str | None
    status: str                                  # clean | threats | error | cancelled | timed_out | unavailable
    report_only: bool = False
    threats: list[dict[str, Any]] = field(default_factory=list)      # {"name", "resources": [paths]}
    found: int = 0
    returncode: int | None = None
    detail: str = ""
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "status": self.status, "report_only": self.report_only,
                "threats": self.threats, "found": self.found, "returncode": self.returncode,
                "detail": str(self.detail), "started_at": self.started_at, "duration_s": self.duration_s}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScanResult":
        return cls(**{k: d.get(k) for k in ("kind", "target", "status")}, report_only=bool(d.get("report_only")),
                   threats=list(d.get("threats") or []), found=int(d.get("found") or 0),
                   returncode=d.get("returncode"), detail=d.get("detail") or "",
                   started_at=d.get("started_at") or 0.0, duration_s=d.get("duration_s") or 0.0)


def _version_key(name: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.-]", name))


def mpcmdrun(env: dict | None = None) -> str | None:
    """The newest Defender platform's ``MpCmdRun.exe`` (the platform folder updates monthly), else the in-box one."""
    env = env if env is not None else os.environ
    program_data = env.get("ProgramData") or r"C:\ProgramData"
    platform = os.path.join(program_data, "Microsoft", "Windows Defender", "Platform")
    try:
        versions = sorted(os.listdir(platform), key=_version_key, reverse=True)
    except OSError:
        versions = []
    for v in versions:
        exe = os.path.join(platform, v, "MpCmdRun.exe")
        if os.path.isfile(exe):
            return exe
    exe = os.path.join(env.get("ProgramFiles") or r"C:\Program Files", "Windows Defender", "MpCmdRun.exe")
    return exe if os.path.isfile(exe) else None


def status(ctx: TaskContext | None = None) -> dict[str, Any]:
    if not is_windows():
        raise CommandUnavailable("Microsoft Defender needs Windows.")
    rows = as_list(powershell_json(STATUS_SCRIPT, 45, ctx))
    if not rows:
        raise QueryError("Get-MpComputerStatus returned nothing.")
    return normalise_status(rows[0])


def normalise_status(r: dict[str, Any]) -> dict[str, Any]:
    def age(v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None
        return None if v >= NEVER or v < 0 else v

    mode = RUNNING_MODES.get(str(r.get("AMRunningMode") or "").strip().lower(), "unknown")
    if r.get("AMRunningMode") is None and r.get("AMServiceEnabled"):
        mode = "normal"                                  # Windows 10 before 1903 has no AMRunningMode
    state = int(r.get("ComputerState") or 0)
    return {
        "service": bool(r.get("AMServiceEnabled")), "antivirus": bool(r.get("AntivirusEnabled")),
        "antispyware": bool(r.get("AntispywareEnabled")), "realtime": bool(r.get("RealTimeProtectionEnabled")),
        "behavior": bool(r.get("BehaviorMonitorEnabled")), "ioav": bool(r.get("IoavProtectionEnabled")),
        "on_access": bool(r.get("OnAccessProtectionEnabled")), "network": bool(r.get("NISEnabled")),
        "tamper": r.get("IsTamperProtected"), "mode": mode, "product_version": r.get("AMProductVersion"),
        "engine_version": r.get("AMEngineVersion"), "signature_version": r.get("AntivirusSignatureVersion"),
        "signature_age_days": age(r.get("AntivirusSignatureAge")),
        "signatures_out_of_date": r.get("DefenderSignaturesOutOfDate"),
        "signature_updated": r.get("SignatureUpdated"),
        "quick_scan_age_days": age(r.get("QuickScanAge")), "full_scan_age_days": age(r.get("FullScanAge")),
        "quick_scan_end": r.get("QuickScanEnd"), "full_scan_end": r.get("FullScanEnd"),
        "computer_state": [name for bit, name in COMPUTER_STATE.items() if state & bit],
    }


def usable(st: dict[str, Any] | None) -> tuple[bool, str]:
    """Whether Defender can run on-demand scans (Microsoft allows them in passive mode, too)."""
    if not st:
        return False, msg("Microsoft Defender does not answer on this PC (it is usually switched off when "
                          "another antivirus is active).")
    if not st.get("service"):
        return False, msg("The Microsoft Defender service is not running.")
    if st.get("mode") == "off":
        return False, msg("Microsoft Defender is not running.")
    if st.get("mode") == "normal" and not st.get("antivirus"):
        return False, msg("Microsoft Defender Antivirus is switched off.")
    return True, ""


_THREAT_RE = re.compile(r"^\s*Threat\s*:\s*(.+?)\s*$", re.I)
_RESOURCE_RE = re.compile(r"^\s*(file|containerfile|process|regkey|regkeyvalue|service|folder)\s*:\s*(.+?)\s*$", re.I)
_FOUND_RE = re.compile(r"found\s+(\d+)\s+threats?", re.I)


def parse_scan_output(text: str) -> tuple[int, list[dict[str, Any]]]:
    """``MpCmdRun -Scan`` output: the number found and, when listed, each threat with its resources."""
    found, threats, current = 0, [], None
    for line in (text or "").splitlines():
        m = _FOUND_RE.search(line)
        if m:
            found = max(found, int(m.group(1)))
            continue
        m = _THREAT_RE.match(line)
        if m:
            current = {"name": m.group(1), "resources": []}
            threats.append(current)
            continue
        m = _RESOURCE_RE.match(line)
        if m and current is not None:
            current["resources"].append(m.group(2))
    return max(found, len(threats)), threats


def scan(kind: str, target: str | None = None, report_only: bool = False, ctx: TaskContext | None = None,
         exe: str | None = None) -> ScanResult:
    """One Defender scan. ``report_only`` (custom scans only) changes nothing on the PC."""
    ctx = context(ctx)
    if kind not in SCAN_TYPES:
        raise ValueError(kind)
    if kind == "custom" and not target:
        raise ValueError("custom scans need a target")
    res = ScanResult(kind, target, "unavailable", report_only and kind == "custom")
    exe = exe or (mpcmdrun() if is_windows() else None)
    if not exe:
        res.detail = msg("MpCmdRun.exe (Microsoft Defender) was not found.")
        return res
    args = [exe, "-Scan", "-ScanType", str(SCAN_TYPES[kind])]
    if kind == "custom":
        args += ["-File", os.path.normpath(target)]
        if res.report_only:
            args.append("-DisableRemediation")
    start = time.monotonic()
    try:
        out = run(args, timeout=min(SCAN_TIMEOUT[kind], ctx.remaining() or SCAN_TIMEOUT[kind]), ctx=ctx)
    except CommandUnavailable as exc:
        res.detail = str(exc)
        return res
    res.duration_s = time.monotonic() - start
    res.returncode = out.returncode
    if out.cancelled or out.timed_out:
        _cancel(exe)
        res.status = "cancelled" if out.cancelled else "timed_out"
        res.detail = msg("The scan was stopped.") if out.cancelled else msg("The scan did not finish in time.")
        return res
    res.found, res.threats = parse_scan_output(out.stdout)
    code = out.returncode & 0xFFFFFFFF if out.returncode is not None else None
    if code == 0 and not res.found:
        res.status = "clean"
    elif code == 2 or res.found:
        res.status = "threats"
    else:
        res.status = "error"
        res.detail = _error_text(code, out.stdout + "\n" + out.stderr)
    return res


ERRORS = {0x80508023: "The file or folder to scan was not found.",
          0x800106BA: "The Microsoft Defender service is not running.",
          0x80070005: "Access denied.",
          0x8050800C: "Microsoft Defender is not the active antivirus or is switched off.",
          0x80508026: "Microsoft Defender is switched off by policy."}


def _error_text(code: int | None, text: str) -> str:
    known = ERRORS.get(code or 0)
    if known:
        return msg(known)
    for line in text.splitlines():
        if re.search(r"fail|error|0x8", line, re.I):
            return line.strip()[:300]
    return msg("Microsoft Defender ended with code {code}.", code=f"0x{(code or 0):08X}")


def _cancel(exe: str) -> None:
    """Stop the scan the killed client started (``MpCmdRun -Scan -Cancel``); best effort."""
    try:
        run([exe, "-Scan", "-Cancel"], timeout=30)
    except (OSError, Cancelled, TimedOut):
        pass


def update_signatures(ctx: TaskContext | None = None) -> str:
    if not is_windows():
        raise CommandUnavailable("Microsoft Defender needs Windows.")
    powershell_json("Update-MpSignature -ErrorAction Stop; 'ok'", 900, ctx)
    return "ok"


def _resource_paths(resources) -> list[str]:
    out = []
    for r in as_list(resources):
        text = str(r)
        kind, sep, rest = text.partition(":_")
        if sep and kind.lower() in ("file", "containerfile", "process", "folder"):
            rest = rest[4:] if rest.startswith("\\\\?\\") else rest
            out.append(rest.split("->")[0])
        elif sep:
            out.append(kind + ": " + rest)
        else:
            out.append(text)
    return out


def history(ctx: TaskContext | None = None) -> list[dict[str, Any]]:
    """Protection history (newest first): one entry per detection with the threat's name and severity."""
    if not is_windows():
        raise CommandUnavailable("Microsoft Defender needs Windows.")
    data = powershell_json(HISTORY_SCRIPT, 60, ctx) or {}
    return combine_history(as_list(data.get("detections")), as_list(data.get("threats")))


def combine_history(detections: list[dict], threats: list[dict]) -> list[dict[str, Any]]:
    by_id = {str(t.get("ThreatID")): t for t in threats}
    out = []
    for d in detections:
        t = by_id.get(str(d.get("ThreatID")), {})
        state = THREAT_STATUS.get(int(d.get("ThreatStatusID") or 0), "unknown")
        out.append({"threat_id": d.get("ThreatID"), "name": t.get("ThreatName") or msg("Unknown threat"),
                    "severity": SEVERITY.get(int(t.get("SeverityID") or 0), "unknown"), "status": state,
                    "unresolved": state in UNRESOLVED or (bool(t.get("IsActive")) and state not in
                                                          ("quarantined", "removed", "cleaned", "allowed",
                                                           "blocked")),
                    "executed": t.get("DidThreatExecute"), "resources": _resource_paths(d.get("Resources")),
                    "process": d.get("ProcessName"), "time": d.get("Time"), "changed": d.get("Changed"),
                    "action_success": d.get("ActionSuccess")})
    out.sort(key=lambda e: e.get("time") or "", reverse=True)
    return out


def first_error(exc: BaseException) -> str:
    res = getattr(exc, "result", None)
    return first_line(getattr(res, "stderr", "") or "") or str(exc)
