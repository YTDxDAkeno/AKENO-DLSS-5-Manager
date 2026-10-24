"""Windows calls for Desktop Studio (ctypes). Every function is safe to call elsewhere and then does nothing.

Wallpapers sit behind the desktop icons, the way Lively Wallpaper (``WinDesktopCore.cs``) and ScreenPlay
(``windowsintegration.cpp``) do it; the code here is our own (see docs/DESKTOP_OPEN_SOURCE_RESEARCH.md):

* Explorer's ``Progman`` window is asked (message 0x052C) to split the icons from the wallpaper.
* **Classic layout** (Windows 10, Windows 11 before 24H2): the icons (``SHELLDLL_DefView``) move into a top-level
  ``WorkerW``; the next top-level ``WorkerW`` behind it draws the wallpaper. Our window becomes its child.
* **Raised desktop** (Windows 11 24H2 and later, recognised by ``WS_EX_NOREDIRECTIONBITMAP`` on ``Progman``, as
  Microsoft describes it): ``SHELLDLL_DefView`` and the wallpaper ``WorkerW`` are both children of ``Progman``.
  Our window becomes a ``WS_EX_LAYERED`` child of ``Progman`` placed directly below ``SHELLDLL_DefView``, and the
  ``WorkerW`` stays the bottom child.

The window is always a ``WS_CHILD`` that cannot be activated, so it never takes the focus, never shows in Alt+Tab
or the taskbar, and the icon view above it keeps every click, drag and right-click. :func:`attach` checks the result
(:func:`verify`); when anything is off the caller must not show the window (``renderer.py`` then closes it and
reports the error), so a wallpaper can never end up as an ordinary window over the desktop. Nothing of Explorer is
replaced, patched or injected; closing the window gives the desktop back as it was.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass, field

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
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
    _get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _get_long.restype = ctypes.c_ssize_t
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _set_long.restype = ctypes.c_ssize_t
    _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
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
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0004, 0x0010, 0x0040
SWP_FRAMECHANGED = 0x0020
HWND_TOP, HWND_BOTTOM = 0, 1
GWL_STYLE, GWL_EXSTYLE = -16, -20
GW_HWNDNEXT, GW_CHILD = 2, 5
GA_PARENT, GA_ROOT = 1, 2
WS_CHILD, WS_POPUP, WS_CAPTION, WS_THICKFRAME = 0x40000000, 0x80000000, 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000020, 0x00000080, 0x00040000
WS_EX_LAYERED, WS_EX_NOREDIRECTIONBITMAP, WS_EX_NOACTIVATE = 0x00080000, 0x00200000, 0x08000000
LWA_ALPHA = 0x2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SPAWN_WORKER = 0x052C


@dataclass(frozen=True)
class Monitor:
    name: str                  # \\.\DISPLAY1 (QScreen.name() on Windows)
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


class Win32:
    """The window functions the desktop embedding needs (tests replace this with a model of Explorer's windows)."""

    def find_window(self, cls: str) -> int:
        return user32.FindWindowW(cls, None) or 0

    def find_child(self, parent: int, after: int, cls: str) -> int:
        return user32.FindWindowExW(parent or None, after or None, cls, None) or 0

    def top_level(self) -> list[int]:
        out: list[int] = []
        cb = EnumWindowsProc(lambda hwnd, _: out.append(hwnd) or True)
        user32.EnumWindows(cb, 0)
        return out

    def children(self, parent: int) -> list[int]:
        """Direct children, top of the z-order first."""
        out, child = [], user32.GetWindow(parent, GW_CHILD)
        while child and len(out) < 4096:
            out.append(child)
            child = user32.GetWindow(child, GW_HWNDNEXT)
        return out

    def class_name(self, hwnd: int) -> str:
        return _class_name(hwnd)

    def style(self, hwnd: int) -> int:
        return _get_long(hwnd, GWL_STYLE) & 0xFFFFFFFF

    def ex_style(self, hwnd: int) -> int:
        return _get_long(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF

    def set_style(self, hwnd: int, value: int) -> None:
        _set_long(hwnd, GWL_STYLE, ctypes.c_ssize_t(ctypes.c_int32(value & 0xFFFFFFFF).value).value)

    def set_ex_style(self, hwnd: int, value: int) -> None:
        _set_long(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(ctypes.c_int32(value & 0xFFFFFFFF).value).value)

    def set_alpha(self, hwnd: int, alpha: int) -> None:
        user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)

    def spawn_worker(self, progman: int) -> None:
        result = ctypes.c_size_t()
        user32.SendMessageTimeoutW(progman, SPAWN_WORKER, 0xD, 0x1, 0x0, 1000, ctypes.byref(result))

    def set_parent(self, hwnd: int, parent: int) -> bool:
        ctypes.set_last_error(0)
        old = user32.SetParent(hwnd, parent)
        return bool(old) or ctypes.get_last_error() == 0     # a window without parent returns NULL on success

    def parent(self, hwnd: int) -> int:
        return user32.GetAncestor(hwnd, GA_PARENT) or 0

    def root(self, hwnd: int) -> int:
        return user32.GetAncestor(hwnd, GA_ROOT) or 0

    def set_pos(self, hwnd: int, after: int, x: int, y: int, w: int, h: int, flags: int) -> bool:
        return bool(user32.SetWindowPos(hwnd, after, x, y, w, h, flags))

    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        pt = wintypes.POINT(x, y)
        user32.ScreenToClient(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(user32.IsWindow(hwnd))

    def visible(self, hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))

    def show(self, hwnd: int, cmd: int) -> None:
        user32.ShowWindow(hwnd, cmd)

    def window_at(self, x: int, y: int) -> int:
        return user32.WindowFromPoint(wintypes.POINT(x, y)) or 0


@dataclass
class Layer:
    """Where wallpapers go: ``raised`` (Windows 11 24H2+, inside Progman below the icons) or ``workerw``."""
    mode: str
    parent: int
    progman: int
    defview: int
    workerw: int
    icon_host: int = 0                 # the top-level window holding SHELLDLL_DefView (classic layout)


@dataclass
class Embedding:
    ok: bool
    mode: str = ""
    problems: list = field(default_factory=list)
    layer: Layer | None = None

    @property
    def error(self) -> str:
        return "; ".join(self.problems)


def _api(api):
    return api if api is not None else (Win32() if WINDOWS else None)


def raised_desktop(api=None) -> bool:
    """Windows 11 24H2+: Progman has no GDI surface and holds the icon view and the wallpaper WorkerW itself."""
    api = _api(api)
    if api is None:
        return False
    progman = api.find_window("Progman")
    return bool(progman) and bool(api.ex_style(progman) & WS_EX_NOREDIRECTIONBITMAP)


def find_layer(api=None, prefer: str = "auto") -> Layer | None:
    """The desktop layer behind the icons, or None when Explorer offers none (no shell, Explorer restarting).

    ``prefer``: ``auto`` (Microsoft's way on 24H2: a child of Progman below the icons), or ``workerw`` (child of the
    wallpaper WorkerW, the alternative Lively and ScreenPlay offer for systems where the first one shows nothing)."""
    api = _api(api)
    if api is None:
        return None
    progman = api.find_window("Progman")
    if not progman:
        return None
    raised = bool(api.ex_style(progman) & WS_EX_NOREDIRECTIONBITMAP)
    api.spawn_worker(progman)
    defview = workerw = host = 0
    for top in api.top_level():
        found = api.find_child(top, 0, "SHELLDLL_DefView")
        if found:
            defview, host = found, top
            workerw = api.find_child(0, top, "WorkerW")       # the next top-level WorkerW: behind the icons
    if raised:
        defview = api.find_child(progman, 0, "SHELLDLL_DefView") or defview
        workerw = api.find_child(progman, 0, "WorkerW")
        if not defview:
            return None
        if prefer == "workerw" and workerw:
            return Layer("raised-workerw", workerw, progman, defview, workerw, progman)
        return Layer("raised", progman, progman, defview, workerw, progman)
    if not defview or not workerw or host == progman:
        return None                                           # the icons were not split off: no safe place
    return Layer("workerw", workerw, progman, defview, workerw, host)


def _ensure_workerw_bottom(api, layer: Layer) -> None:
    if layer.mode != "raised" or not layer.workerw:
        return
    kids = api.children(layer.progman)
    if kids and kids[-1] != layer.workerw:
        api.set_pos(layer.workerw, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def attach(hwnd: int, monitor: Monitor, api=None, prefer: str = "auto") -> Embedding:
    """Make ``hwnd`` (not yet shown) a child of the desktop layer covering ``monitor`` (physical pixels).

    Returns what happened; when ``ok`` is False the window must not be shown."""
    api = _api(api)
    if api is None:
        return Embedding(False, problems=["not Windows"])
    layer = find_layer(api, prefer)
    if layer is None:
        return Embedding(False, problems=["Windows offers no desktop layer behind the icons (is Explorer running?)"])
    style = api.style(hwnd)
    style &= ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    api.set_style(hwnd, style | WS_CHILD)
    ex = (api.ex_style(hwnd) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT) & ~WS_EX_APPWINDOW
    if layer.mode == "raised":
        ex |= WS_EX_LAYERED                                   # before SetParent (Microsoft, Lively)
    api.set_ex_style(hwnd, ex)
    if layer.mode == "raised":
        api.set_alpha(hwnd, 255)
    if not api.set_parent(hwnd, layer.parent):
        return Embedding(False, layer.mode, ["SetParent was refused"], layer)
    place(hwnd, monitor, layer, api, show=False)
    problems = verify(hwnd, monitor, layer, api, check_visible=False)
    return Embedding(not problems, layer.mode, problems, layer)


def place(hwnd: int, monitor: Monitor, layer: Layer, api=None, show: bool = True) -> None:
    """Position (again) over the monitor, in the parent's client coordinates, at the right place in the z-order."""
    api = _api(api)
    x, y = api.screen_to_client(layer.parent, monitor.x, monitor.y)
    flags = SWP_NOACTIVATE | SWP_FRAMECHANGED | (SWP_SHOWWINDOW if show else 0)
    if layer.mode == "raised":
        api.set_pos(hwnd, layer.defview, x, y, monitor.width, monitor.height, flags)
    else:
        api.set_pos(hwnd, HWND_TOP, x, y, monitor.width, monitor.height, flags | SWP_NOZORDER)
    _ensure_workerw_bottom(api, layer)


def verify(hwnd: int, monitor: Monitor, layer: Layer, api=None, check_visible: bool = True) -> list[str]:
    """Problems with the embedding (empty: the wallpaper is behind the icons, where it belongs)."""
    api = _api(api)
    problems = []
    if not api.is_window(hwnd) or not api.is_window(layer.parent):
        return ["the desktop window is gone (Explorer restarted?)"]
    if api.parent(hwnd) != layer.parent:
        problems.append("the wallpaper is not a child of the desktop layer")
    if not api.style(hwnd) & WS_CHILD:
        problems.append("the wallpaper is not a child window")
    if layer.mode in ("raised", "raised-workerw"):
        kids = api.children(layer.progman)
        target = hwnd if layer.mode == "raised" else layer.workerw
        if target not in kids or layer.defview not in kids or kids.index(layer.defview) > kids.index(target):
            problems.append("the wallpaper is not below the desktop icons")
        elif layer.mode == "raised" and layer.workerw in kids and kids.index(hwnd) > kids.index(layer.workerw):
            problems.append("the wallpaper is below Windows' own wallpaper")
    else:
        tops = api.top_level()
        if layer.icon_host not in tops or layer.workerw not in tops or \
                tops.index(layer.icon_host) > tops.index(layer.workerw):
            problems.append("the wallpaper layer is not behind the desktop icons")
    left, top, right, bottom = api.rect(hwnd)
    if abs(left - monitor.x) > 2 or abs(top - monitor.y) > 2 or abs(right - left - monitor.width) > 2 or \
            abs(bottom - top - monitor.height) > 2:
        problems.append(f"the wallpaper does not cover the monitor ({left},{top},{right},{bottom})")
    if check_visible and not api.visible(hwnd):
        problems.append("the wallpaper is not visible")
    return problems


def still_attached(hwnd: int, layer: Layer, api=None) -> bool:
    """False after Explorer restarted (its windows, and with them ours, are gone) or the layer was rebuilt."""
    api = _api(api)
    return api is not None and api.is_window(hwnd) and api.is_window(layer.parent) and api.parent(hwnd) == layer.parent


def hide(hwnd: int, api=None) -> None:
    """Hide before closing. The window is never detached into a top-level window (that would cover the desktop)."""
    api = _api(api)
    if api is not None and api.is_window(hwnd):
        api.show(hwnd, SW_HIDE)


def _within(api, hwnd: int, ancestor: int) -> bool:
    for _ in range(64):
        if not hwnd:
            return False
        if hwnd == ancestor:
            return True
        hwnd = api.parent(hwnd)
    return False


def inspect(hwnd: int, monitor: Monitor, api=None, samples: int = 5) -> dict:
    """How a running wallpaper window sits on the real desktop, measured from outside (tools/desktop_check.py):
    its parent, styles, whether it is a top-level window (it must not be: those show in Alt+Tab), whether
    :func:`verify` agrees, and which window receives clicks at points spread over the monitor (never the wallpaper:
    the icon view or a program in front of it)."""
    api = _api(api)
    if api is None or not api.is_window(hwnd):
        return {"exists": False}
    layer = find_layer(api)
    parent = api.parent(hwnd)
    out = {"exists": True, "parent_class": api.class_name(parent) if parent else "",
           "child_style": bool(api.style(hwnd) & WS_CHILD), "no_activate": bool(api.ex_style(hwnd) & WS_EX_NOACTIVATE),
           "layered": bool(api.ex_style(hwnd) & WS_EX_LAYERED), "top_level": hwnd in api.top_level(),
           "visible": api.visible(hwnd), "layer": layer.mode if layer else None,
           "problems": verify(hwnd, monitor, layer, api) if layer else ["no desktop layer"]}
    hits: dict[str, int] = {}
    wallpaper_hits = 0
    for i in range(samples):
        for j in range(samples):
            x = monitor.x + monitor.width * (2 * i + 1) // (2 * samples)
            y = monitor.y + monitor.height * (2 * j + 1) // (2 * samples)
            at = api.window_at(x, y)
            if not at:
                continue
            if _within(api, at, hwnd):
                wallpaper_hits += 1
            name = api.class_name(at)
            hits[name] = hits.get(name, 0) + 1
    out["clicks_reach_wallpaper"] = wallpaper_hits
    out["clicks_reach"] = hits
    out["icon_view_receives_clicks"] = any(c in hits for c in ("SysListView32", "SHELLDLL_DefView"))
    return out


def describe(api=None) -> dict:
    """What the desktop looks like to the embedding (diagnostics for the suite and the CI desktop check)."""
    api = _api(api)
    if api is None:
        return {"windows": False}
    progman = api.find_window("Progman")
    out = {"windows": True, "build": getattr(sys.getwindowsversion(), "build", 0) if WINDOWS else 0,
           "progman": bool(progman), "raised_desktop": raised_desktop(api)}
    layer = find_layer(api)
    out["layer"] = layer.mode if layer else None
    return out


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def refresh_wallpaper(api=None) -> None:
    """Redraw Windows' own wallpaper (after a wallpaper window closed) without changing it.

    Not on the raised desktop: there Windows' WorkerW draws the wallpaper anyway, and this call would rebuild it
    (Lively's ``RefreshDesktop`` skips it for the same reason)."""
    if not WINDOWS or raised_desktop(api):
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


def stack(hwnd: int, top: bool) -> None:
    """Widgets: just above the desktop while it is in front (Win+D), at the bottom otherwise (Rainmeter's
    "on desktop" position). Never topmost, never activated."""
    if WINDOWS and hwnd:
        user32.SetWindowPos(hwnd, HWND_TOP if top else HWND_BOTTOM, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


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
