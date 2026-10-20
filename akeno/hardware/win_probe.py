"""Windows hardware probes: DXGI (ctypes COM), the display-class registry, NVML and Win32 display APIs.

Each probe is independent and failure-tolerant: an exception in one source is recorded as a detection
error and the remaining sources still contribute. Nothing here requires administrator rights or any
NVIDIA library; NVML is used only when the driver already provides it.
"""

from __future__ import annotations

import ctypes
import os
import platform
from ctypes import wintypes
from typing import Any

# ruff: noqa: N801  (Win32 struct naming)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s: str) -> "GUID":
        import uuid
        u = uuid.UUID(s)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]


IID_IDXGIFactory1 = "770aae78-f26f-4dba-a829-253c83d1b387"
IID_IDXGIDevice = "54ec77fa-1377-44e6-8c32-88fd5f44c84c"
IID_ID3D12Device = "189819f1-1db6-4b57-be54-1821339b85f7"
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_SOFTWARE = 2

FEATURE_LEVELS = [(0xC200, "12_2"), (0xC100, "12_1"), (0xC000, "12_0"), (0xB100, "11_1"), (0xB000, "11_0")]


def _vcall(obj: ctypes.c_void_p, index: int, restype, *args_types):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *args_types)
    return proto(vtbl[index])


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _vcall(obj, 2, ctypes.c_ulong)(obj)


def probe_dxgi() -> list[dict[str, Any]]:
    dxgi = ctypes.WinDLL("dxgi.dll")
    create = dxgi.CreateDXGIFactory1
    create.restype = ctypes.c_long
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    factory = ctypes.c_void_p()
    hr = create(ctypes.byref(GUID.from_str(IID_IDXGIFactory1)), ctypes.byref(factory))
    if hr < 0:
        raise OSError(f"CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        d3d12 = None
        try:
            d3d12 = ctypes.WinDLL("d3d12.dll")
            d3d12.D3D12CreateDevice.restype = ctypes.c_long
            d3d12.D3D12CreateDevice.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(GUID), ctypes.c_void_p]
        except OSError:
            d3d12 = None
        out: list[dict[str, Any]] = []
        enum1 = _vcall(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        i = 0
        while True:
            adapter = ctypes.c_void_p()
            hr = enum1(factory, i, ctypes.byref(adapter))
            if (hr & 0xFFFFFFFF) == DXGI_ERROR_NOT_FOUND:
                break
            if hr < 0:
                raise OSError(f"EnumAdapters1({i}) failed: 0x{hr & 0xFFFFFFFF:08X}")
            try:
                desc = DXGI_ADAPTER_DESC1()
                _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC1))(adapter, ctypes.byref(desc))
                umd = ctypes.c_longlong()
                check = _vcall(adapter, 9, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_longlong))
                ver = None
                if check(adapter, ctypes.byref(GUID.from_str(IID_IDXGIDevice)), ctypes.byref(umd)) >= 0:
                    v = umd.value & 0xFFFFFFFFFFFFFFFF
                    ver = f"{v >> 48}.{(v >> 32) & 0xFFFF}.{(v >> 16) & 0xFFFF}.{v & 0xFFFF}"
                fl = None
                if d3d12 is not None and not desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE:
                    iid = GUID.from_str(IID_ID3D12Device)
                    for level, label in FEATURE_LEVELS:
                        if d3d12.D3D12CreateDevice(adapter, level, ctypes.byref(iid), None) >= 0:
                            fl = label
                            break
                out.append({
                    "name": desc.Description.strip(), "vendor_id": desc.VendorId, "device_id": desc.DeviceId,
                    "subsys_id": desc.SubSysId, "revision": desc.Revision,
                    "vram_bytes": int(desc.DedicatedVideoMemory), "shared_memory_bytes": int(desc.SharedSystemMemory),
                    "luid": f"{desc.AdapterLuid.HighPart:08X}:{desc.AdapterLuid.LowPart:08X}",
                    "is_software": bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE),
                    "driver_version_raw": ver, "feature_level": fl, "index": i,
                })
            finally:
                _release(adapter)
            i += 1
        return out
    finally:
        _release(factory)


DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def probe_display_class_registry() -> list[dict[str, Any]]:
    """Driver version/date and exact VRAM (qwMemorySize) per display adapter from the registry."""
    import re
    import winreg

    out = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as cls:
        idx = 0
        while True:
            try:
                sub = winreg.EnumKey(cls, idx)
            except OSError:
                break
            idx += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(cls, sub) as k:
                    vals: dict[str, Any] = {}
                    for name in ("DriverDesc", "DriverVersion", "DriverDate", "MatchingDeviceId",
                                 "HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            vals[name] = winreg.QueryValueEx(k, name)[0]
                        except OSError:
                            pass
            except OSError:
                continue
            m = re.search(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", str(vals.get("MatchingDeviceId", "")), re.I)
            mem = vals.get("HardwareInformation.qwMemorySize")
            if isinstance(mem, bytes):
                mem = int.from_bytes(mem[:8], "little")
            if mem is None:
                m2 = vals.get("HardwareInformation.MemorySize")
                mem = int.from_bytes(m2[:4], "little") if isinstance(m2, bytes) else m2
            out.append({
                "name": vals.get("DriverDesc"), "driver_version_raw": vals.get("DriverVersion"),
                "driver_date": vals.get("DriverDate"),
                "vendor_id": int(m.group(1), 16) if m else None, "device_id": int(m.group(2), 16) if m else None,
                "vram_bytes": int(mem) if isinstance(mem, int) and mem > 0 else None,
            })
    return out


def probe_nvml() -> dict[str, Any]:
    """NVIDIA driver version via NVML when the driver ships nvml.dll. Optional."""
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    candidates = [os.path.join(sysdir, "nvml.dll"),
                  os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI", "nvml.dll")]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if not path:
        return {}
    nvml = ctypes.CDLL(path)
    if nvml.nvmlInit_v2() != 0:
        return {}
    try:
        buf = ctypes.create_string_buffer(96)
        info: dict[str, Any] = {}
        if nvml.nvmlSystemGetDriverVersion(buf, 96) == 0:
            info["driver_version"] = buf.value.decode()
        return info
    finally:
        nvml.nvmlShutdown()


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", ctypes.c_wchar * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


def probe_displays() -> list[dict[str, Any]]:
    user32 = ctypes.WinDLL("user32")
    out = []
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & 0x1:  # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            continue
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if user32.EnumDisplaySettingsW(dd.DeviceName, -1, ctypes.byref(dm)):  # ENUM_CURRENT_SETTINGS
            out.append({"name": f"{dd.DeviceName} ({dd.DeviceString})", "width": dm.dmPelsWidth,
                        "height": dm.dmPelsHeight, "refresh_hz": dm.dmDisplayFrequency,
                        "is_primary": bool(dd.StateFlags & 0x4)})
    return out


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def probe_os() -> dict[str, Any]:
    import winreg

    info: dict[str, Any] = {"is_64bit": platform.machine().endswith("64")}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
        def q(name):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return None
        build = q("CurrentBuild")
        product = q("ProductName") or "Windows"
        try:
            if build and int(build) >= 22000:
                product = product.replace("Windows 10", "Windows 11")
        except ValueError:
            pass
        ubr = q("UBR")
        info.update(os_name=product, os_display_version=q("DisplayVersion") or q("ReleaseId"),
                    os_build=f"{build}.{ubr}" if build and ubr is not None else build,
                    os_version=platform.version())
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            info["cpu_name"] = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers") as k:
            info["hardware_gpu_scheduling"] = winreg.QueryValueEx(k, "HwSchMode")[0] == 2
    except OSError:
        pass
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        info["ram_bytes"] = int(ms.ullTotalPhys)
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    info["directx_version"] = "DirectX 12" if os.path.isfile(os.path.join(sysdir, "d3d12.dll")) else "DirectX 11"
    return info
