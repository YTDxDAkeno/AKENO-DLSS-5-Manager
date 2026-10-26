"""Running Windows tools: a fixed program path, no shell, no console window, a time limit, cancellation and an
output cap. PowerShell runs as ``-NoProfile -NonInteractive`` with the script passed encoded, so no user input can
change the command line.

Nothing here elevates. A command that needs administrator rights is started through :func:`run_elevated`, which
shows the Windows UAC prompt (the user decides) and is only called after a confirmation that says why.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from akeno.paths import is_windows
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

log = logging.getLogger(__name__)

NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW
MAX_OUTPUT = 8 * 1024 * 1024    # bytes kept from a tool's output


@dataclass
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled


class CommandUnavailable(OSError):
    """The tool does not exist on this PC (or this is not Windows)."""


def system32(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def powershell_exe() -> str:
    return system32(r"WindowsPowerShell\v1.0\powershell.exe")


def run(args: list[str], timeout: float = 30.0, ctx: TaskContext | None = None, encoding: str | None = None,
        input_text: str | None = None) -> CommandResult:
    """Run ``args`` (``args[0]`` an absolute path) and collect its output; polls for cancellation every 100 ms."""
    ctx = context(ctx)
    exe = args[0]
    if not os.path.isabs(exe) or not os.path.isfile(exe):
        raise CommandUnavailable(f"{os.path.basename(exe)} is not available on this PC.")
    flags = NO_WINDOW if is_windows() else 0
    import tempfile
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(args, stdout=out, stderr=err, stdin=subprocess.PIPE if input_text else subprocess.DEVNULL,
                                creationflags=flags)
        if input_text:
            try:
                proc.stdin.write(input_text.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass
        end = time.monotonic() + timeout
        timed_out = cancelled = False
        while proc.poll() is None:
            if ctx.cancelled:
                cancelled = True
                break
            if time.monotonic() >= end or ctx.timed_out:
                timed_out = True
                break
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        out.seek(0)
        err.seek(0)
        raw_out, raw_err = out.read(MAX_OUTPUT), err.read(MAX_OUTPUT)
    enc = encoding or ("utf-8" if not is_windows() else "oem")
    return CommandResult(proc.returncode, _decode(raw_out, enc), _decode(raw_err, enc), timed_out, cancelled)


def _decode(raw: bytes, enc: str) -> str:
    for e in (enc, "utf-8", "cp1252"):
        try:
            return raw.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def powershell(script: str, timeout: float = 60.0, ctx: TaskContext | None = None) -> CommandResult:
    """Run a PowerShell script (Windows PowerShell 5.1, present on every Windows 10/11). Output is UTF-8."""
    if not is_windows():
        raise CommandUnavailable("PowerShell queries need Windows.")
    prelude = ("$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
               "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;")
    encoded = base64.b64encode((prelude + script).encode("utf-16-le")).decode("ascii")
    return run([powershell_exe(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
               timeout, ctx, encoding="utf-8")


class QueryError(OSError):
    def __init__(self, message: str, result: CommandResult | None = None):
        super().__init__(message)
        self.result = result


def powershell_json(script: str, timeout: float = 60.0, ctx: TaskContext | None = None) -> Any:
    """Run ``script`` and parse its ``ConvertTo-Json`` output. Raises :class:`QueryError` with the tool's own
    message when it fails, :class:`Cancelled` / :class:`TimedOut` when stopped."""
    res = powershell(script + " | ConvertTo-Json -Depth 5 -Compress", timeout, ctx)
    if res.cancelled:
        raise Cancelled("Cancelled.")
    if res.timed_out:
        raise TimedOut("Windows did not answer in time.")
    if res.returncode != 0:
        raise QueryError(first_line(res.stderr) or f"PowerShell exited with {res.returncode}.", res)
    text = res.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise QueryError(f"Unreadable answer from Windows: {exc}", res) from exc


def as_list(value: Any) -> list:
    """ConvertTo-Json writes one object without the list around it."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:400]
    return ""


def ps_quote(text: str) -> str:
    """A PowerShell single-quoted literal (the only escape is a doubled quote)."""
    return "'" + str(text).replace("'", "''") + "'"


def run_elevated(exe: str, params: str, ctx: TaskContext | None = None, timeout: float = 3600.0,
                 show: bool = True) -> int | None:
    """Start ``exe`` with administrator rights through the UAC prompt and wait for it; returns its exit code, or
    None when the user declined the prompt. Called only after AKENO explained the command and the user confirmed."""
    if not is_windows():
        raise CommandUnavailable("This repair needs Windows.")
    import ctypes
    from ctypes import wintypes
    ctx = context(ctx)

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040          # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = 1 if show else 0    # SW_SHOWNORMAL: the user sees a repair tool's own window and its output
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:              # ERROR_CANCELLED: the user said no in the UAC prompt
            return None
        raise OSError(f"Windows could not start {os.path.basename(exe)} (error {err}).")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    end = time.monotonic() + timeout
    try:
        while k32.WaitForSingleObject(info.hProcess, 200) == 0x102:      # WAIT_TIMEOUT
            if ctx.cancelled or time.monotonic() >= end:
                # an elevated repair is never killed from here: it would leave Windows half repaired
                raise Cancelled("AKENO stopped waiting; the repair keeps running in its own window.")
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        k32.CloseHandle(info.hProcess)


def open_location(path: str, select: bool = False) -> bool:
    """Show a folder (or a file selected in its folder) in Explorer. Read-only."""
    if not path or not os.path.exists(path):
        return False
    if is_windows():
        explorer = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe")
        args = [explorer, "/select," + os.path.normpath(path)] if select and os.path.isfile(path) else \
            [explorer, os.path.normpath(path if os.path.isdir(path) else os.path.dirname(path))]
        subprocess.Popen(args, creationflags=NO_WINDOW)
        return True
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))


def open_uri(uri: str) -> bool:
    """Open a Windows settings or Windows Security page (``ms-settings:``, ``windowsdefender:``)."""
    if not uri.startswith(("ms-settings:", "windowsdefender:")):
        raise ValueError("only Windows settings pages are opened")
    if is_windows():
        os.startfile(uri)                                   # noqa: S606 (a fixed settings URI)
        return True
    return False
