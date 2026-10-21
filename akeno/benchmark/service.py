"""Benchmark capture with Intel PresentMon, storage and export.

PresentMon reads ETW events, which Windows only allows to administrators and members of the
"Performance Log Users" group. AKENO first starts PresentMon normally; only if that is refused does it
ask Windows for elevation (a UAC prompt the user can decline). Results are never simulated: a session
exists only if PresentMon (or an imported CSV) produced frame times.
"""

from __future__ import annotations

import csv
import html
import re
import uuid
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
        local = tools_dir() / self.tool.file_name
        if local.is_file():
            return local
        from akeno.community.bundles import OfflineBundle
        try:
            return OfflineBundle().tool(self.tool) or local
        except (OSError, ValueError):
            return local

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
        safe_game = re.sub(r'[^A-Za-z0-9_-]', '_', game_id)[:80]
        safe_label = re.sub(r'[^A-Za-z0-9_-]', '_', label)[:40]
        out = benchmarks_dir() / f"{safe_game}-{safe_label}-{uuid.uuid4().hex}.csv"
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

    def rotate_automatic(self, max_sessions=100, max_bytes=100 * 1024 * 1024):
        """Bound only AKENO automatic captures; never delete imported user CSVs."""
        sessions = [s for s in self.sessions() if s.get('neural_config', {}).get('automatic_monitoring')]
        total = 0
        for index, session in enumerate(sorted(sessions, key=lambda x: x.get('id', 0), reverse=True)):
            path = Path(session['csv_path']) if session.get('csv_path') else None
            owned = bool(path and path.parent.resolve() == benchmarks_dir().resolve() and '-automatic-' in path.name and not path.is_symlink())
            size = path.stat().st_size if owned and path.is_file() else 0
            total += size
            if index >= max_sessions or total > max_bytes:
                if owned:
                    path.unlink(missing_ok=True)
                self.db.delete_benchmark(session['id'])

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
        for key in ('game_id',):
            if not a.get(key) or not b.get(key) or a[key] != b[key]:
                out.append('different or unknown game identity')
        for key in ('gpu', 'driver', 'resolution'):
            if not a.get(key) or not b.get(key):
                out.append('uncontrolled: unknown ' + key)
        ac = a.get('neural_config', {}).get('capture_conditions', {})
        bc = b.get('neural_config', {}).get('capture_conditions', {})
        for key in ('game_version', 'workload', 'settings'):
            if not ac.get(key) or not bc.get(key) or ac[key] != bc[key]:
                out.append('uncontrolled: different or unknown ' + key)
        if abs((a["stats"]["duration_s"]) - (b["stats"]["duration_s"])) > max(a["stats"]["duration_s"], 1) * 0.5:
            out.append("very different capture durations")
        return out

    def export_json(self, sessions: list[dict[str, Any]], path: str, include_paths=False) -> None:
        from akeno.diagnostics.privacy import redact
        Path(path).write_text(json.dumps({"exported_at": now_iso(), "sessions": redact(sessions, include_paths)}, indent=2), encoding="utf-8")

    def export_html(self, sessions, path):
        from akeno.diagnostics.privacy import redact
        rows = []
        for session in sessions:
            stats = session['stats']
            rows.append('<tr>' + ''.join('<td>' + html.escape(str(x)) + '</td>' for x in
                (session.get('label'), stats['avg_fps'], stats['low_1pct_fps'],
                 stats['avg_frametime_ms'], stats['p99_frametime_ms'])) + '</tr>')
        warnings = self.comparable(*sessions) if len(sessions) == 2 else []
        content = '<!doctype html><meta charset="utf-8"><title>AKENO measured performance</title>'
        content += '<h1>AKENO measured performance</h1><p>Unavailable GPU sensors are not inferred. Performance does not verify Neural Rendering.</p>'
        content += '<table border="1"><tr><th>Run</th><th>Average FPS</th><th>1% low FPS</th><th>Mean ms</th><th>P99 ms</th></tr>' + ''.join(rows) + '</table>'
        content += '<p>' + html.escape('; '.join(warnings)) + '</p><pre>' + html.escape(json.dumps(redact(sessions), indent=2)) + '</pre>'
        Path(path).write_text(content, encoding='utf-8')

    def export_csv(self, sessions: list[dict[str, Any]], path: str) -> None:
        cols = ["id", "game_id", "label", "created_at", "source", "gpu", "driver", "resolution", "backend_id",
                "frames", "duration_s", "avg_fps", "low_1pct_fps", "low_01pct_fps", "avg_frametime_ms",
                "median_frametime_ms", "p95_frametime_ms", "p99_frametime_ms", "stdev_frametime_ms", "stutter_ratio",
                "neural_config"]
        from akeno.diagnostics.privacy import redact
        sessions = redact(sessions)
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
                                           session.get("process_name")).frametimes_ms
        except st.CsvError:
            return []
