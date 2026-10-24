"""Readings for the desktop widgets. One background thread collects them every two seconds, so no widget waits
for a program like ``nvidia-smi`` on the screen's thread. Only what Windows or the driver reports is shown:
a value that is not reported stays ``None`` and the widget says so instead of guessing."""

from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

log = logging.getLogger(__name__)


def _number(raw: str) -> float | None:
    try:
        value = float(raw.strip())
    except ValueError:
        return None                                        # "[N/A]", "[Not Supported]"
    return value if value >= 0 else None


def nvidia_smi_path() -> str | None:
    if sys.platform == "win32":
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if exe.is_file():
            return str(exe)
    return shutil.which("nvidia-smi")


FIELDS = ("name", "utilization.gpu", "memory.used", "memory.total", "temperature.gpu", "power.draw")


def parse_nvidia(text: str) -> list[dict]:
    gpus = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) != len(FIELDS):
            continue
        name, *values = row
        util, used, total, temp, power = (_number(v) for v in values)
        gpus.append({"name": name.strip(), "util": util, "vram_used_mib": used, "vram_total_mib": total,
                     "temp_c": temp, "power_w": power})
    return gpus


def nvidia() -> list[dict]:
    exe = nvidia_smi_path()
    if not exe:
        return []
    try:
        result = subprocess.run([exe, "--query-gpu=" + ",".join(FIELDS), "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=4,
                                creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_nvidia(result.stdout) if result.returncode == 0 else []


def cpu_temperature() -> float | None:
    """Linux reports CPU sensors through psutil; Windows has no documented CPU temperature without a vendor
    driver, so the value stays unknown there."""
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return None
    try:
        sensors = reader() or {}
    except (OSError, RuntimeError):
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        for entry in sensors.get(key, []):
            if entry.current:
                return float(entry.current)
    return None


class Sampler:
    """Collects in the background while at least one widget needs it. ``need("gpu")`` adds the NVIDIA query."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.data: dict = {}
        self.needs: set[str] = set()
        self._prev_net = None
        self._prev_disk = None
        self._prev_time = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False

    def need(self, *keys: str) -> None:
        self.needs.update(keys)

    def start(self) -> None:
        if self._thread is None:
            psutil.cpu_percent(None)                       # the first call only sets the reference point
            self.sample()
            self._thread = threading.Thread(target=self._run, name="akeno-widget-sensors", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not self.paused:
                try:
                    self.sample()
                except Exception as exc:                  # a sensor failure must never end the widgets
                    log.warning("widget sensors: %s", exc)

    def sample(self) -> dict:
        now = time.monotonic()
        vm = psutil.virtual_memory()
        data = {"cpu": psutil.cpu_percent(None), "cores": psutil.cpu_count() or 0,
                "ram_percent": vm.percent, "ram_used_gb": vm.used / 2 ** 30, "ram_total_gb": vm.total / 2 ** 30,
                "uptime_s": max(0.0, time.time() - psutil.boot_time()), "time": time.time()}
        try:
            freq = psutil.cpu_freq()
            data["cpu_ghz"] = round(freq.current / 1000, 2) if freq and freq.current else None
        except (OSError, RuntimeError, NotImplementedError):
            data["cpu_ghz"] = None
        elapsed = (now - self._prev_time) if self._prev_time else None
        net = psutil.net_io_counters()
        if net is not None and self._prev_net is not None and elapsed:
            data["net_down"] = max(0.0, (net.bytes_recv - self._prev_net.bytes_recv) / elapsed)
            data["net_up"] = max(0.0, (net.bytes_sent - self._prev_net.bytes_sent) / elapsed)
        self._prev_net = net
        try:
            disk = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            disk = None
        if disk is not None and self._prev_disk is not None and elapsed:
            data["disk_read"] = max(0.0, (disk.read_bytes - self._prev_disk.read_bytes) / elapsed)
            data["disk_write"] = max(0.0, (disk.write_bytes - self._prev_disk.write_bytes) / elapsed)
        self._prev_disk = disk
        self._prev_time = now
        if "disk" in self.needs:
            drives = []
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or not part.fstype:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                drives.append({"name": part.mountpoint, "percent": usage.percent,
                               "free_gb": usage.free / 2 ** 30, "total_gb": usage.total / 2 ** 30})
            data["drives"] = drives[:6]
        if "gpu" in self.needs:
            data["gpus"] = nvidia()
        if "temperature" in self.needs:
            data["cpu_temp_c"] = cpu_temperature()
        self.data = data
        return data

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def rate(value: float | None) -> str:
    if value is None:
        return "—"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return "—"


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} d {hours} h {minutes} min"
    return f"{hours} h {minutes} min"


def downloads_dir() -> Path:
    """The user's Downloads folder (the Windows known folder, wherever the user moved it)."""
    if sys.platform == "win32":
        import ctypes
        import uuid
        from ctypes import wintypes
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
        out = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0 and out.value:
            path = Path(out.value)
            ctypes.WinDLL("ole32").CoTaskMemFree(out)
            return path
    return Path.home() / "Downloads"


def recent_files(folder: Path, limit: int = 6) -> list[Path]:
    try:
        entries = [p for p in folder.iterdir() if not p.name.startswith(".") and not p.name.endswith(
            (".crdownload", ".part", ".tmp"))]
    except OSError:
        return []

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(entries, key=mtime, reverse=True)[:limit]


def last_benchmark(db_file: Path) -> dict | None:
    """The newest Benchmark capture (read only, from the suite's database)."""
    import json
    import sqlite3
    if not db_file.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            row = conn.execute("SELECT b.game_id, b.label, b.data, b.created_at, g.data FROM benchmarks b "
                               "LEFT JOIN games g ON g.id = b.game_id ORDER BY b.id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    stats = (json.loads(row[2]) or {}).get("stats") or {}
    try:
        name = (json.loads(row[4]) or {}).get("name") if row[4] else None
    except ValueError:
        name = None
    return {"game": name or row[0], "label": row[1], "created_at": row[3], "avg_fps": stats.get("avg_fps"),
            "low_1pct_fps": stats.get("low_1pct_fps")}
