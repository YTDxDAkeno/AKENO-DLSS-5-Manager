"""What System Doctor reads: psutil for load, Windows' own performance counters (through CIM, whose class names are
the same in every Windows language), the event logs and the storage stack.

``LocalProbe`` is the real implementation. Calls that need Windows raise :class:`CommandUnavailable` elsewhere, and
the analysis turns that into "not available on this PC" instead of a value. ``FakeProbe`` (tests) answers from
dictionaries with the same shapes.

Nothing here writes anything.
"""

from __future__ import annotations

import os
import time
from typing import Any

import psutil

from akeno.paths import is_windows
from akeno.system import commands
from akeno.system.commands import CommandUnavailable, as_list, powershell_json
from akeno.system.tasks import TaskContext, context

# Event logs System Doctor reads. Each query: log, provider (None = any), event ids.
EVENT_QUERIES = {
    "unexpected_shutdown": ("System", None, (41, 6008)),
    "boot": ("System", "EventLog", (6005, 6006, 6009)),
    "planned_restart": ("System", "User32", (1074,)),
    "bugcheck": ("System", "Microsoft-Windows-WER-SystemErrorReporting", (1001,)),
    "gpu_reset": ("System", "Display", (4101,)),
    "whea": ("System", "Microsoft-Windows-WHEA-Logger", (17, 18, 19, 47)),
    "disk": ("System", None, (7, 51, 153, 55, 129)),
    "cpu_limited": ("System", "Microsoft-Windows-Kernel-Processor-Power", (37,)),
    "app_crash": ("Application", "Application Error", (1000,)),
    "app_hang": ("Application", "Application Hang", (1002,)),
    "wer": ("Application", "Windows Error Reporting", (1001,)),
    "dotnet": ("Application", ".NET Runtime", (1026,)),
    "driver_install": ("System", "Microsoft-Windows-UserPnp", (20001, 20003)),
    "update_install": ("System", "Microsoft-Windows-WindowsUpdateClient", (19, 20)),
    "service_install": ("System", "Service Control Manager", (7045,)),
}


DISK_SCRIPT = r"""
$disks = @()
try {
  foreach ($d in @(Get-PhysicalDisk -ErrorAction Stop)) {
    $r = $null; $err = $null
    try { $r = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch { $err = $_.Exception.Message }
    $disks += [pscustomobject]@{id=[string]$d.DeviceId; name=$d.FriendlyName; media=[string]$d.MediaType; bus=[string]$d.BusType;
      health=[string]$d.HealthStatus; operational=@($d.OperationalStatus | ForEach-Object { [string]$_ }); size=$d.Size;
      temperature=$r.Temperature; temperature_max=$r.TemperatureMax; wear=$r.Wear;
      read_errors_uncorrected=$r.ReadErrorsUncorrected; write_errors_uncorrected=$r.WriteErrorsUncorrected;
      power_on_hours=$r.PowerOnHours; reliability_error=$err}
  }
} catch { $storage_error = $_.Exception.Message }
$smart = @()
try { $smart = @(Get-CimInstance Win32_DiskDrive -ErrorAction Stop | Select-Object Index,Model,Status,InterfaceType,Size) } catch {}
[pscustomobject]@{disks=$disks; smart=$smart; storage_error=$storage_error}
"""
DISK_RESULT = "[pscustomobject]@{disks=$disks; smart=$smart; storage_error=$storage_error}"


def norm_luid(text: str) -> str:
    """"luid_0x00000000_0x0000D1E3" or "00000000:0000D1E3" -> "00000000:0000D1E3"."""
    t = text.lower().replace("luid_", "")
    parts = [p.replace("0x", "") for p in t.replace(":", "_").split("_") if p]
    if len(parts) >= 2:
        return f"{int(parts[0], 16):08X}:{int(parts[1], 16):08X}"
    return text.upper()


class LocalProbe:
    """This PC."""

    # -- psutil (every platform) -----------------------------------------------------------------------------------
    def cpu(self, sample_s: float = 1.0, ctx: TaskContext | None = None) -> dict[str, Any]:
        ctx = context(ctx)
        psutil.cpu_percent(None, percpu=True)
        ctx.sleep(sample_s)
        per = psutil.cpu_percent(None, percpu=True)
        freq = None
        try:
            f = psutil.cpu_freq()
            freq = {"current": f.current, "max": f.max} if f else None
        except (OSError, NotImplementedError, AttributeError):
            freq = None
        return {"percent": sum(per) / len(per) if per else None, "per_core": per,
                "logical": psutil.cpu_count(True), "physical": psutil.cpu_count(False), "freq": freq}

    def memory(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {"total": vm.total, "available": vm.available, "used": vm.total - vm.available, "percent": vm.percent,
                "swap_total": sw.total, "swap_used": sw.used, "swap_percent": sw.percent}

    def volumes(self) -> list[dict[str, Any]]:
        out = []
        for part in psutil.disk_partitions(all=False):
            opts = (part.opts or "").lower()
            if "cdrom" in opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            out.append({"mount": part.mountpoint, "device": part.device, "fs": part.fstype,
                        "removable": "removable" in opts, "total": u.total, "used": u.used, "free": u.free,
                        "percent": u.percent})
        return out

    def disk_activity(self, sample_s: float = 2.0, ctx: TaskContext | None = None) -> dict[str, Any]:
        """Busy share, read and write rates per physical disk over the sample (psutil's disk counters)."""
        ctx = context(ctx)
        try:
            a = psutil.disk_io_counters(perdisk=True) or {}
        except (OSError, RuntimeError):
            a = {}
        t0 = time.monotonic()
        ctx.sleep(sample_s)
        try:
            b = psutil.disk_io_counters(perdisk=True) or {}
        except (OSError, RuntimeError):
            b = {}
        dt = max(0.001, time.monotonic() - t0)
        disks = {}
        for name, end in b.items():
            start = a.get(name)
            if start is None:
                continue
            busy_ms = None
            if hasattr(end, "busy_time"):
                busy_ms = end.busy_time - start.busy_time
            elif hasattr(end, "read_time"):
                busy_ms = (end.read_time - start.read_time) + (end.write_time - start.write_time)
            disks[name] = {"read_bps": (end.read_bytes - start.read_bytes) / dt,
                           "write_bps": (end.write_bytes - start.write_bytes) / dt,
                           # read+write time can add up to more than the interval with queued requests
                           "busy_percent": None if busy_ms is None else min(100.0, busy_ms / (dt * 10))}
        return {"seconds": dt, "disks": disks}

    def processes(self, sample_s: float = 2.0, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        """CPU share (of the whole machine), working set and disk I/O rate of every process over the sample."""
        ctx = context(ctx)
        procs = []
        for p in psutil.process_iter(["pid", "name", "exe", "username", "create_time"]):
            if p.pid in (0,):
                continue
            try:
                p.cpu_percent(None)
                io = p.io_counters() if hasattr(p, "io_counters") else None
            except (psutil.Error, OSError):
                io = None
            procs.append((p, io))
        t0 = time.monotonic()
        ctx.sleep(sample_s)
        dt = max(0.001, time.monotonic() - t0)
        cores = psutil.cpu_count() or 1
        out = []
        for p, io0 in procs:
            try:
                cpu = p.cpu_percent(None) / cores
                mem = p.memory_info().rss
                io1 = p.io_counters() if io0 is not None else None
            except (psutil.Error, OSError):
                continue
            io_bps = None
            if io0 is not None and io1 is not None:
                io_bps = ((io1.read_bytes - io0.read_bytes) + (io1.write_bytes - io0.write_bytes)) / dt
            info = p.info
            out.append({"pid": p.pid, "name": info.get("name") or "", "exe": info.get("exe") or "",
                        "user": info.get("username") or "", "started": info.get("create_time"),
                        "cpu_percent": cpu, "rss": mem, "io_bps": io_bps})
        return out

    def boot_time(self) -> float | None:
        try:
            return psutil.boot_time()
        except (OSError, RuntimeError):
            return None

    def cpu_temperature(self) -> dict[str, Any] | None:
        """Linux sensors (psutil). Windows has no documented CPU temperature without a vendor driver."""
        from akeno.desktop.sensors import cpu_temperature
        value = cpu_temperature()
        return {"value": value, "source": "psutil sensors"} if value is not None else None

    # -- NVIDIA (nvidia-smi, read-only) ----------------------------------------------------------------------------
    NV_FIELDS = ("name", "utilization.gpu", "memory.used", "memory.total", "temperature.gpu", "power.draw",
                 "clocks.gr", "clocks.max.gr", "clocks_throttle_reasons.hw_thermal_slowdown",
                 "clocks_throttle_reasons.sw_thermal_slowdown", "clocks_throttle_reasons.sw_power_cap")

    def nvidia(self) -> list[dict[str, Any]]:
        from akeno.desktop.sensors import nvidia_smi_path
        exe = nvidia_smi_path()
        if not exe:
            return []
        try:
            res = commands.run([exe, "--query-gpu=" + ",".join(self.NV_FIELDS), "--format=csv,noheader,nounits"],
                               timeout=6, encoding="utf-8")
        except (OSError, CommandUnavailable):
            return []
        if not res.ok:
            return []
        return parse_nvidia(res.stdout)

    # -- Windows (CIM performance counters, event logs, storage) -----------------------------------------------------
    def perf(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        """Memory paging and commit, processor clock against its maximum, ACPI thermal zones, GPU engines."""
        if not is_windows():
            raise CommandUnavailable("Windows performance counters need Windows.")
        script = r"""
function Q($c, $f) { try { if ($f) { Get-CimInstance $c -Filter $f -ErrorAction Stop } else { Get-CimInstance $c -ErrorAction Stop } } catch { $null } }
$null = Q 'Win32_PerfFormattedData_PerfOS_Memory'
Start-Sleep -Milliseconds 1100
$m = Q 'Win32_PerfFormattedData_PerfOS_Memory' | Select-Object PagesPersec,PageReadsPersec,CommittedBytes,CommitLimit,AvailableMBytes,PercentCommittedBytesInUse
$p = Q 'Win32_PerfFormattedData_Counters_ProcessorInformation' "Name='_Total'" | Select-Object PercentProcessorPerformance,PercentofMaximumFrequency,ProcessorFrequency,PercentProcessorUtility
$t = @(Q 'Win32_PerfFormattedData_Counters_ThermalZoneInformation' | Select-Object Name,Temperature,HighPrecisionTemperature,PercentPassiveLimit,ThrottleReasons)
$g = @(Q 'Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine' | Where-Object { $_.Name -like '*engtype_3D*' -and $_.UtilizationPercentage -gt 0 } | Select-Object Name,UtilizationPercentage)
$a = @(Q 'Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory' | Select-Object Name,DedicatedUsage,SharedUsage)
[pscustomobject]@{memory=$m; cpu=$p; thermal=$t; gpu_engines=$g; gpu_memory=$a}
"""
        data = powershell_json(script, 45, ctx) or {}
        engines: dict[str, float] = {}
        per_pid: dict[int, float] = {}
        for e in as_list(data.get("gpu_engines")):
            name = str(e.get("Name") or "")
            util = float(e.get("UtilizationPercentage") or 0)
            if "luid_" in name:
                luid = norm_luid("luid_" + name.split("luid_", 1)[1].split("_phys")[0])
                engines[luid] = min(100.0, engines.get(luid, 0.0) + util)
            if name.startswith("pid_"):
                try:
                    pid = int(name.split("_")[1])
                    per_pid[pid] = per_pid.get(pid, 0.0) + util
                except (IndexError, ValueError):
                    pass
        memory = {}
        for a in as_list(data.get("gpu_memory")):
            name = str(a.get("Name") or "")
            if "luid_" in name:
                luid = norm_luid("luid_" + name.split("luid_", 1)[1].split("_phys")[0])
                memory[luid] = {"dedicated": a.get("DedicatedUsage"), "shared": a.get("SharedUsage")}
        return {"memory": data.get("memory") or {}, "cpu": data.get("cpu") or {},
                "thermal": as_list(data.get("thermal")), "gpu_3d": engines, "gpu_memory": memory,
                "gpu_3d_by_pid": per_pid}

    def events(self, names: list[str] | tuple[str, ...], days: int = 14, max_each: int = 120,
               ctx: TaskContext | None = None) -> dict[str, list[dict[str, Any]]]:
        """Events of the named :data:`EVENT_QUERIES` from the last ``days``; one PowerShell run for all of them."""
        if not is_windows():
            raise CommandUnavailable("Windows event logs need Windows.")
        parts = []
        for name in names:
            log, provider, ids = EVENT_QUERIES[name]
            filt = f"@{{LogName='{log}';Id={','.join(map(str, ids))};StartTime=$since}}"
            if provider:
                filt = f"@{{LogName='{log}';ProviderName='{provider}';Id={','.join(map(str, ids))};StartTime=$since}}"
            parts.append(f"E '{name}' {filt}")
        script = (
            "$since=(Get-Date).AddDays(-" + str(int(days)) + ")\n"
            "$all=New-Object System.Collections.ArrayList\n"
            "$fail=New-Object System.Collections.ArrayList\n"
            "function E($q, $f) {\n"
            "  try { $ev = @(Get-WinEvent -FilterHashtable $f -MaxEvents " + str(int(max_each)) + " -ErrorAction Stop) }\n"
            "  catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { return } "
            "[void]$fail.Add([pscustomobject]@{q=$q; error=$_.Exception.Message}); return }\n"
            "  foreach ($e in $ev) {\n"
            "    $msg = ''; try { if ($e.Message) { $msg = (($e.Message -split \"`r?`n\")[0]); if ($msg.Length -gt 400) { $msg = $msg.Substring(0,400) } } } catch {}\n"
            "    [void]$all.Add([pscustomobject]@{q=$q; id=$e.Id; provider=$e.ProviderName; "
            "time=$e.TimeCreated.ToUniversalTime().ToString('o'); level=$e.Level; "
            "props=@($e.Properties | ForEach-Object { [string]$_.Value }); message=$msg})\n"
            "  }\n"
            "}\n" + "\n".join(parts) + "\n"
            "[pscustomobject]@{events=$all.ToArray(); failed=$fail.ToArray()}")
        data = powershell_json(script, 90, ctx) or {}
        out: dict[str, list[dict[str, Any]]] = {n: [] for n in names}
        for e in as_list(data.get("events")):
            q = e.get("q")
            if q in out:
                e["props"] = as_list(e.get("props"))
                out[q].append(e)
        failed = {f.get("q"): f.get("error") for f in as_list(data.get("failed"))}
        if failed:
            out["_failed"] = [{"q": k, "error": v} for k, v in failed.items()]       # type: ignore[assignment]
        return out

    def physical_disks(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        """Storage Spaces' view of each disk (health, media, bus) and its reliability counters when Windows lets
        this user read them; Win32_DiskDrive's SMART prediction flag. No serial numbers are read."""
        if not is_windows():
            raise CommandUnavailable("Drive health needs Windows.")
        script = DISK_SCRIPT
        data = powershell_json(script, 60, ctx) or {}
        return {"disks": as_list(data.get("disks")), "smart": as_list(data.get("smart")),
                "error": data.get("storage_error")}


def parse_nvidia(text: str) -> list[dict[str, Any]]:
    import csv
    import io

    def num(raw: str) -> float | None:
        try:
            v = float(raw.strip())
        except ValueError:
            return None
        return v if v >= 0 else None

    def flag(raw: str) -> bool | None:
        r = raw.strip().lower()
        return True if r == "active" else (False if r == "not active" else None)

    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) != len(LocalProbe.NV_FIELDS):
            continue
        out.append({"name": row[0].strip(), "util": num(row[1]), "vram_used_mib": num(row[2]),
                    "vram_total_mib": num(row[3]), "temp_c": num(row[4]), "power_w": num(row[5]),
                    "clock_mhz": num(row[6]), "clock_max_mhz": num(row[7]), "thermal_hw": flag(row[8]),
                    "thermal_sw": flag(row[9]), "power_cap": flag(row[10])})
    return out


class FakeProbe:
    """Answers from fixed data (tests). Missing keys behave like Windows not reporting them."""

    def __init__(self, **data: Any):
        self.data = data
        self.calls: list[str] = []

    def _get(self, key: str, default: Any = None) -> Any:
        self.calls.append(key)
        value = self.data.get(key, default)
        if isinstance(value, Exception):
            raise value
        return value

    def cpu(self, sample_s: float = 1.0, ctx=None):
        return self._get("cpu", {"percent": 5.0, "per_core": [5.0], "logical": 8, "physical": 4, "freq": None})

    def memory(self):
        return self._get("memory", {"total": 16 * 2 ** 30, "available": 10 * 2 ** 30, "used": 6 * 2 ** 30,
                                    "percent": 37.5, "swap_total": 0, "swap_used": 0, "swap_percent": 0})

    def volumes(self):
        return self._get("volumes", [])

    def disk_activity(self, sample_s: float = 2.0, ctx=None):
        return self._get("disk_activity", {"seconds": sample_s, "disks": {}})

    def processes(self, sample_s: float = 2.0, ctx=None):
        return self._get("processes", [])

    def boot_time(self):
        return self._get("boot_time", time.time() - 3600)

    def cpu_temperature(self):
        return self._get("cpu_temperature", None)

    def nvidia(self):
        return self._get("nvidia", [])

    def perf(self, ctx=None):
        return self._get("perf", CommandUnavailable("Windows performance counters need Windows."))

    def events(self, names, days: int = 14, max_each: int = 120, ctx=None):
        data = self._get("events", CommandUnavailable("Windows event logs need Windows."))
        return {n: list(data.get(n, [])) for n in names} | ({"_failed": data["_failed"]} if "_failed" in data else {})

    def physical_disks(self, ctx=None):
        return self._get("physical_disks", CommandUnavailable("Drive health needs Windows."))


def system_drive() -> str:
    if is_windows():
        return (os.environ.get("SystemDrive") or "C:") + "\\"
    return "/"
