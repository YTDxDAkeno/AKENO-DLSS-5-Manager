"""Benchmark capture with Intel PresentMon, storage and export.

PresentMon reads ETW events, which Windows only allows to administrators and members of the
"Performance Log Users" group. AKENO first starts PresentMon normally; only if that is refused does it
ask Windows for elevation (a UAC prompt the user can decline). Results are never simulated: a session
exists only if PresentMon (or an imported CSV) produced frame times.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from akeno.benchmark import stats as st
from akeno.core.db import Database, now_iso
from akeno.core.models import BenchmarkSession, FrameStats
from akeno.paths import benchmarks_dir, is_windows, tools_dir
from akeno.security.catalog import Tool
from akeno.security.downloads import CancelToken, DownloadManager, sha256_file

log = logging.getLogger(__name__)
SESSION_NAME = "AKENO_Benchmark"


class BenchmarkError(Exception):
    pass


class PresentMonRunner:
    def __init__(self, tool: Tool, downloads: DownloadManager | None):
        self.tool = tool
        self.downloads = downloads

    def exe_path(self) -> Path:
        return tools_dir() / self.tool.file_name

    def available(self) -> bool:
        p = self.exe_path()
        return p.is_file() and sha256_file(p) == self.tool.sha256

    def ensure(self, progress=None, token: CancelToken | None = None) -> Path:
        p = self.exe_path()
        if p.is_file() and sha256_file(p) == self.tool.sha256:
            return p
        if self.downloads is None:
            raise BenchmarkError("PresentMon is not available and downloads are disabled.")
        res = self.downloads.fetch(self.tool.url, self.tool.sha256, self.tool.file_name, self.tool.size,
                                   progress, token)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(res.path.read_bytes())
        os.replace(tmp, p)
        return p

    def _args(self, process_name: str, csv_path: Path, duration: int, delay: int) -> list[str]:
        return ["--process_name", process_name, "--output_file", str(csv_path), "--timed", str(duration),
                "--delay", str(delay), "--terminate_after_timed", "--no_console_stats",
                "--session_name", SESSION_NAME, "--stop_existing_session"]

    def capture(self, process_name: str, duration: int, delay: int, csv_path: Path,
                on_status: Callable[[str], None] | None = None, token: CancelToken | None = None) -> Path:
        if not is_windows():
            raise BenchmarkError("PresentMon capture needs Windows.")
        exe = self.ensure()
        args = self._args(process_name, csv_path, duration, delay)
        say = on_status or (lambda m: None)
        say("Starting PresentMon…")
        proc = subprocess.Popen([str(exe), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)  # CREATE_NO_WINDOW
        t0 = time.time()
        while proc.poll() is None:
            if token and token.cancelled:
                proc.terminate()
                break
            time.sleep(0.2)
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        if proc.returncode not in (0, None) and not csv_path.exists() and time.time() - t0 < duration:
            low = out.lower()
            if "access" in low or "administrator" in low or "elevat" in low or "permission" in low or proc.returncode == 5:
                say("PresentMon needs permission to read performance events. Waiting for your approval (UAC)…")
                self._run_elevated(exe, args, duration + delay + 30, token)
            else:
                raise BenchmarkError(f"PresentMon failed (exit code {proc.returncode}): {out.strip()[:400]}")
        if not csv_path.exists():
            raise BenchmarkError("PresentMon did not record any frames. Is the game running and rendering?")
        return csv_path

    def _run_elevated(self, exe: Path, args: list[str], timeout: int, token: CancelToken | None) -> None:
        import ctypes
        from ctypes import wintypes

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                        ("hProcess", wintypes.HANDLE)]

        params = subprocess.list2cmdline(args)
        sei = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40, lpVerb="runas",
                                lpFile=str(exe), lpParameters=params, nShow=0)
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()
            if err == 1223:
                raise BenchmarkError("Benchmark cancelled: administrator permission was declined.")
            raise BenchmarkError(f"Could not start PresentMon with elevation (error {err}).")
        k32 = ctypes.windll.kernel32
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if k32.WaitForSingleObject(sei.hProcess, 250) == 0:
                    return
                if token and token.cancelled:
                    self.stop_elevated(exe)
                    return
        finally:
            k32.CloseHandle(sei.hProcess)

    def stop_elevated(self, exe: Path) -> None:
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe),
                                            f"--terminate_existing_session --session_name {SESSION_NAME}", None, 0)


class BenchmarkService:
    def __init__(self, db: Database, runner: PresentMonRunner | None):
        self.db = db
        self.runner = runner

    def record(self, game_id: str, label: str, frametimes: list[float], meta: dict[str, Any],
               source: str, csv_path: str | None) -> BenchmarkSession:
        stats = st.compute(frametimes)
        session = BenchmarkSession(id=None, game_id=game_id, label=label, started_at=meta.get("started_at", now_iso()),
                                   stats=stats, gpu=meta.get("gpu"), driver=meta.get("driver"),
                                   resolution=meta.get("resolution"), backend_id=meta.get("backend_id"),
                                   neural_config=meta.get("neural_config") or {}, source=source, csv_path=csv_path,
                                   process_name=meta.get("process_name"))
        data = asdict(session)
        data.pop("id")
        data["stats"] = stats.to_dict()
        session.id = self.db.add_benchmark(game_id, label, data)
        return session

    def run(self, game_id: str, process_name: str, label: str, meta: dict[str, Any], duration: int, delay: int,
            on_status=None, token=None) -> BenchmarkSession:
        if self.runner is None:
            raise BenchmarkError("PresentMon is not configured.")
        out = benchmarks_dir() / f"{game_id.replace(':', '_')}-{label}-{int(time.time())}.csv"
        meta = dict(meta, started_at=now_iso(), process_name=process_name)
        self.runner.capture(process_name, duration, delay, out, on_status, token)
        parsed = st.parse_presentmon_csv(out.read_text(encoding="utf-8", errors="replace"), process_name)
        if len(parsed.frametimes_ms) < 30:
            raise BenchmarkError("Too few frames were captured. Keep the game in the foreground while measuring.")
        return self.record(game_id, label, parsed.frametimes_ms, meta, "presentmon", str(out))

    def import_csv(self, game_id: str, path: str, label: str, meta: dict[str, Any],
                   process_name: str | None = None) -> BenchmarkSession:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        parsed = st.parse_presentmon_csv(text, process_name)
        if not parsed.frametimes_ms and process_name:
            parsed = st.parse_presentmon_csv(text, None)
        if len(parsed.frametimes_ms) < 30:
            raise st.CsvError("The CSV contains fewer than 30 usable frames.")
        return self.record(game_id, label, parsed.frametimes_ms, dict(meta, process_name=", ".join(parsed.process_names)),
                           "csv-import", path)

    def sessions(self, game_id: str | None = None) -> list[dict[str, Any]]:
        return self.db.benchmarks(game_id)

    @staticmethod
    def stats_of(session: dict[str, Any]) -> FrameStats:
        return FrameStats(**session["stats"])

    @staticmethod
    def comparable(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
        """Reasons two sessions should not be compared directly (empty list = comparable)."""
        out = []
        for key, label in (("gpu", "GPU"), ("driver", "driver"), ("resolution", "resolution")):
            if a.get(key) and b.get(key) and a[key] != b[key]:
                out.append(f"different {label} ({a[key]} vs {b[key]})")
        if abs((a["stats"]["duration_s"]) - (b["stats"]["duration_s"])) > max(a["stats"]["duration_s"], 1) * 0.5:
            out.append("very different capture durations")
        return out

    def export_json(self, sessions: list[dict[str, Any]], path: str) -> None:
        Path(path).write_text(json.dumps({"exported_at": now_iso(), "sessions": sessions}, indent=2), encoding="utf-8")

    def export_csv(self, sessions: list[dict[str, Any]], path: str) -> None:
        cols = ["id", "game_id", "label", "created_at", "source", "gpu", "driver", "resolution", "backend_id",
                "frames", "duration_s", "avg_fps", "low_1pct_fps", "low_01pct_fps", "avg_frametime_ms",
                "median_frametime_ms", "p95_frametime_ms", "p99_frametime_ms", "stdev_frametime_ms", "stutter_ratio",
                "neural_config"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for s in sessions:
                row = []
                for c in cols:
                    if c in s.get("stats", {}):
                        row.append(s["stats"][c])
                    elif c == "neural_config":
                        row.append(json.dumps(s.get(c) or {}))
                    else:
                        row.append(s.get(c))
                w.writerow(row)

    @staticmethod
    def frametimes(session: dict[str, Any]) -> list[float]:
        p = session.get("csv_path")
        if not p or not os.path.isfile(p):
            return []
        try:
            return st.parse_presentmon_csv(Path(p).read_text(encoding="utf-8", errors="replace"),
                                           None).frametimes_ms
        except st.CsvError:
            return []
