"""Windows calls for Desktop Studio (ctypes). Every function is safe to call elsewhere and then does nothing.

Wallpapers sit behind the desktop icons with the method Lively Wallpaper and others use: Explorer's Program
Manager window is asked (message 0x052C) to create a ``WorkerW`` window between the icons and the wallpaper, and
the wallpaper window becomes its child. Since Windows 11 24H2 that ``WorkerW`` is a child of ``Progman`` itself;
both layouts are handled. When AKENO stops, the window is destroyed and Windows redraws its own wallpaper. Nothing
of Explorer is replaced, patched or injected.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)
WINDOWS = sys.platform == "win32"

if WINDOWS:
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SetParent.restype = wintypes.HWND
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", ctypes.c_wchar * 32)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]

SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_SENDCHANGE = 0x02
SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0004, 0x0010, 0x0040


@dataclass(frozen=True)
class Monitor:
    name: str                  # \\.\DISPLAY1 (QScreen.name() on Windows)
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def desktop_parent() -> tuple[int, int | None] | None:
    """(parent window, sibling to place the wallpaper below) for a window behind the desktop icons."""
    if not WINDOWS:
        return None
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0x0, 1000, ctypes.byref(result))
    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if defview and user32.FindWindowExW(progman, None, "WorkerW", None):
        return progman, defview          # Windows 11 24H2+: inside Progman, directly below the icon view
    found = []

    def visit(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            worker = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if worker:
                found.append(worker)
        return True
    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return (found[0], None) if found else None


def attach(hwnd: int, monitor: Monitor) -> bool:
    """Put a top-level window behind the desktop icons, covering one monitor (physical pixels)."""
    target = desktop_parent()
    if not target:
        log.warning("no desktop WorkerW window: the wallpaper stays a normal window")
        return False
    parent, below = target
    user32.SetParent(hwnd, parent)
    vx = user32.GetSystemMetrics(76)                     # SM_XVIRTUALSCREEN: the parent's client origin
    vy = user32.GetSystemMetrics(77)
    flags = SWP_NOACTIVATE | SWP_SHOWWINDOW | (0 if below else SWP_NOZORDER)
    user32.SetWindowPos(hwnd, below, monitor.x - vx, monitor.y - vy, monitor.width, monitor.height, flags)
    return True


def detach(hwnd: int) -> None:
    if WINDOWS:
        user32.SetParent(hwnd, None)


def refresh_wallpaper() -> None:
    """Redraw Windows' own wallpaper (after a wallpaper window closed) without changing it."""
    if not WINDOWS:
        return
    buf = ctypes.create_unicode_buffer(1024)
    if user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buf, 0):
        user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, buf, SPIF_SENDCHANGE)


def monitors() -> list[Monitor]:
    if not WINDOWS:
        return []
    out: list[Monitor] = []

    def visit(hmon, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            out.append(Monitor(info.szDevice, r.left, r.top, r.right - r.left, r.bottom - r.top,
                               bool(info.dwFlags & 1)))
        return True
    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(visit), 0)
    return out


def fullscreen_app() -> bool:
    """A program runs fullscreen (games, presentations, videos): Windows' own notification state."""
    if not WINDOWS:
        return False
    state = ctypes.c_int()
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False
    return state.value in (2, 3, 4)          # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE


def covers(rect: tuple[int, int, int, int], monitor: Monitor, maximised: bool) -> bool:
    """A window (left, top, right, bottom) hides this monitor's wallpaper: it is maximised there, or it fills
    the whole monitor (borderless fullscreen games and videos)."""
    left, top, right, bottom = rect
    if left <= monitor.x and top <= monitor.y and right >= monitor.x + monitor.width and \
            bottom >= monitor.y + monitor.height:
        return True
    cx, cy = (left + right) // 2, (top + bottom) // 2
    return maximised and monitor.x <= cx < monitor.x + monitor.width and monitor.y <= cy < monitor.y + monitor.height


def covered(monitor: Monitor) -> bool:
    """The window in front hides the wallpaper on this monitor."""
    if not WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    if not hwnd or _class_name(hwnd) in ("Progman", "WorkerW", "Shell_TrayWnd") or user32.IsIconic(hwnd):
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    return covers((rect.left, rect.top, rect.right, rect.bottom), monitor, bool(user32.IsZoomed(hwnd)))


def remote_session() -> bool:
    return bool(WINDOWS and user32.GetSystemMetrics(0x1000))       # SM_REMOTESESSION


def on_battery() -> bool:
    try:
        import psutil
        battery = psutil.sensors_battery()
        return bool(battery is not None and not battery.power_plugged)
    except (ImportError, OSError, RuntimeError):
        return False


def cursor_pos() -> tuple[int, int] | None:
    if not WINDOWS:
        return None
    point = wintypes.POINT()
    return (point.x, point.y) if user32.GetCursorPos(ctypes.byref(point)) else None


def desktop_in_front() -> bool:
    """The desktop itself has the focus (for interactive wallpapers)."""
    if not WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd) and _class_name(hwnd) in ("Progman", "WorkerW")


def virtual_desktop_id() -> str | None:
    """The current Windows virtual desktop, from Explorer's own registry value (read only)."""
    if not WINDOWS:
        return os.environ.get("AKENO_TEST_VIRTUAL_DESKTOP")
    import uuid
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops") as key:
            raw, _ = winreg.QueryValueEx(key, "CurrentVirtualDesktop")
        return str(uuid.UUID(bytes_le=bytes(raw))) if raw and len(raw) == 16 else None
    except OSError:
        return None


def set_acrylic(hwnd: int, dark: bool = True) -> bool:
    """Windows 11 22H2+: the documented acrylic backdrop (DWMWA_SYSTEMBACKDROP_TYPE = transient window)."""
    if not WINDOWS:
        return False
    value = ctypes.c_int(1 if dark else 0)
    dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 20, ctypes.byref(value), 4)   # immersive dark mode
    kind = ctypes.c_int(3)                                                         # DWMSBT_TRANSIENTWINDOW
    return dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 38, ctypes.byref(kind), 4) == 0


MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def media_key(name: str) -> bool:
    """Send a media key (play/pause, next, previous) to whatever player is active."""
    if not WINDOWS:
        return False
    vk = MEDIA_KEYS[name]
    user32.keybd_event(vk, 0, 0x1, 0)            # KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, 0, 0x1 | 0x2, 0)      # key up
    return True
