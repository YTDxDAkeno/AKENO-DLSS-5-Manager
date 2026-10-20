"""Running-process inspection (Windows Toolhelp + QueryFullProcessImageName). Read-only."""

from __future__ import annotations

import os
from dataclasses import dataclass

from akeno.paths import is_windows


@dataclass
class ProcessInfo:
    pid: int
    name: str
    path: str | None


def list_processes() -> list[ProcessInfo]:
    if not is_windows():
        return _list_procfs()
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    out = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            path = None
            h = k32.OpenProcess(0x1000, False, e.th32ProcessID)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    path = buf.value
                k32.CloseHandle(h)
            out.append(ProcessInfo(int(e.th32ProcessID), e.szExeFile, path))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _list_procfs() -> list[ProcessInfo]:
    out = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            path = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            path = None
        try:
            with open(f"/proc/{pid}/comm") as f:
                name = f.read().strip()
        except OSError:
            continue
        out.append(ProcessInfo(int(pid), name, path))
    return out


def processes_in_dir(directory: str, procs: list[ProcessInfo] | None = None) -> list[ProcessInfo]:
    """Processes whose image lives inside ``directory`` (the game is running if any match)."""
    root = os.path.normcase(os.path.abspath(directory)).rstrip("\\/") + os.sep
    procs = list_processes() if procs is None else procs
    return [p for p in procs if p.path and os.path.normcase(os.path.abspath(p.path)).startswith(root)]


def find_by_exe(exe_path: str, procs: list[ProcessInfo] | None = None) -> list[ProcessInfo]:
    target = os.path.normcase(os.path.abspath(exe_path))
    name = os.path.basename(exe_path).lower()
    procs = list_processes() if procs is None else procs
    return [p for p in procs if (p.path and os.path.normcase(os.path.abspath(p.path)) == target)
            or (not p.path and p.name.lower() == name)]
