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


def creation_identity(pid: int) -> str | None:
    """Stable process birth identity, read-only. None means attribution is unresolved."""
    if not is_windows():
        try:
            # /proc stat starttime is field 22; the command in parentheses may contain spaces.
            fields = open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1].split()
            return 'procfs:' + fields[19]
        except (OSError, IndexError):
            return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return 'filetime:' + str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)
    return None


def module_inventory(pid: int, expected_birth: str | None = None) -> dict:
    """Toolhelp module snapshot. Does not open/inject/execute remote code."""
    if not is_windows():
        return {'state': 'unavailable', 'modules': [], 'detail': 'Windows module inspection is unavailable.'}
    if not expected_birth or creation_identity(pid) != expected_birth:
        return {'state': 'unavailable', 'modules': [], 'detail': 'Process identity could not be confirmed.'}
    import ctypes
    from ctypes import wintypes
    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('th32ModuleID', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD), ('GlblcntUsage', wintypes.DWORD),
            ('ProccntUsage', wintypes.DWORD), ('modBaseAddr', ctypes.c_void_p),
            ('modBaseSize', wintypes.DWORD), ('hModule', wintypes.HMODULE),
            ('szModule', wintypes.WCHAR * 256), ('szExePath', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return {'state': 'unavailable', 'modules': [], 'detail': f'Module snapshot unavailable (Windows error {ctypes.get_last_error()}).'}
    modules = []
    try:
        entry = MODULEENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Module32FirstW(handle, ctypes.byref(entry))
        while ok and len(modules) < 512:
            modules.append({'name': entry.szModule, 'path': entry.szExePath})
            ok = kernel.Module32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    if creation_identity(pid) != expected_birth:
        return {'state': 'unavailable', 'modules': [], 'detail': 'The process changed during the module snapshot.'}
    return {'state': 'observed', 'modules': modules, 'detail': 'Read-only Toolhelp snapshot; module presence is not Feature 18 evaluation.'}
