"""The Windows interfaces the FPS Booster changes: per-user registry values, power plans and process priorities.

Each has an in-memory stand-in with the same calls, used outside Windows (tests, development), so every tweak's
preview, apply and restore path runs in the test suite. Nothing here needs administrator rights: the booster
only writes under ``HKEY_CURRENT_USER`` and switches between power plans that already exist.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# documented power scheme GUIDs (powercfg /list)
POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"

REG_SZ = "sz"
REG_DWORD = "dword"


@dataclass(frozen=True)
class RegValue:
    kind: str          # sz | dword
    data: object


class MemoryRegistry:
    """HKCU stand-in: {key path: {value name: RegValue}}."""

    def __init__(self):
        self.keys: dict[str, dict[str, RegValue]] = {}

    def get(self, key: str, name: str) -> RegValue | None:
        return self.keys.get(key.lower(), {}).get(name.lower())

    def set(self, key: str, name: str, value: RegValue) -> None:
        self.keys.setdefault(key.lower(), {})[name.lower()] = value

    def delete(self, key: str, name: str) -> None:
        self.keys.get(key.lower(), {}).pop(name.lower(), None)

    def read_machine_dword(self, key: str, name: str) -> int | None:
        value = self.keys.get("hklm\\" + key.lower(), {}).get(name.lower())
        return int(value.data) if value else None


class WinRegistry:
    def get(self, key: str, name: str) -> RegValue | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
                data, kind = winreg.QueryValueEx(k, name)
        except OSError:
            return None
        return RegValue(REG_DWORD if kind == winreg.REG_DWORD else REG_SZ, data)

    def set(self, key: str, name: str, value: RegValue) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            if value.kind == REG_DWORD:
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value.data))
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value.data))

    def delete(self, key: str, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    def read_machine_dword(self, key: str, name: str) -> int | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                data, _ = winreg.QueryValueEx(k, name)
                return int(data)
        except (OSError, ValueError):
            return None


@dataclass(frozen=True)
class PowerScheme:
    guid: str
    name: str


class MemoryPower:
    def __init__(self, schemes: list[PowerScheme] | None = None, active: str = BALANCED):
        self.schemes = schemes or [PowerScheme(BALANCED, "Balanced"), PowerScheme(HIGH_PERFORMANCE, "High performance"),
                                   PowerScheme(POWER_SAVER, "Power saver")]
        self.active_guid = active

    def list(self) -> list[PowerScheme]:
        return list(self.schemes)

    def active(self) -> str:
        return self.active_guid

    def set_active(self, guid: str) -> None:
        if guid not in {s.guid for s in self.schemes}:
            raise ValueError(f"no power plan {guid}")
        self.active_guid = guid


GUID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+\((.*?)\)")


class WinPower:
    """``powercfg``: the documented command-line interface to power plans (no administrator rights needed)."""

    def _run(self, *args: str) -> str:
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "powercfg.exe")
        res = subprocess.run([exe, *args], capture_output=True, text=True, timeout=15,
                             creationflags=0x08000000)       # CREATE_NO_WINDOW
        if res.returncode:
            raise OSError(res.stderr.strip() or res.stdout.strip() or f"powercfg {args[0]} failed")
        return res.stdout

    def list(self) -> list[PowerScheme]:
        return [PowerScheme(m.group(1).lower(), m.group(2)) for m in GUID.finditer(self._run("/list"))]

    def active(self) -> str:
        m = GUID.search(self._run("/getactivescheme"))
        if not m:
            raise OSError("powercfg did not report the active plan")
        return m.group(1).lower()

    def set_active(self, guid: str) -> None:
        self._run("/setactive", guid)


REGISTRY = WinRegistry() if os.name == "nt" else MemoryRegistry()
POWER = WinPower() if os.name == "nt" else MemoryPower()


# -- process priorities (psutil) -------------------------------------------------------------------------------------
PRIORITY_NAMES = ("idle", "below_normal", "normal", "above_normal", "high")


def _psutil_class(name: str):
    import psutil
    if os.name != "nt":                      # POSIX: nice values (tests run as an unprivileged user)
        return {"idle": 19, "below_normal": 10, "normal": 0, "above_normal": -5, "high": -10}[name]
    return {"idle": psutil.IDLE_PRIORITY_CLASS, "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "normal": psutil.NORMAL_PRIORITY_CLASS, "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
            "high": psutil.HIGH_PRIORITY_CLASS}[name]


def priority_of(pid: int) -> str | None:
    import psutil
    try:
        value = psutil.Process(pid).nice()
    except (psutil.Error, OSError):
        return None
    for name in PRIORITY_NAMES:
        if _psutil_class(name) == value:
            return name
    if os.name != "nt":
        return "below_normal" if value > 0 else ("normal" if value == 0 else "above_normal")
    return None


def set_priority(pid: int, name: str) -> bool:
    """Never realtime. Returns False when the process is gone or refuses (an elevated or protected process)."""
    import psutil
    if name not in PRIORITY_NAMES:
        raise ValueError(name)
    try:
        psutil.Process(pid).nice(_psutil_class(name))
        return True
    except (psutil.Error, OSError) as exc:
        log.info("priority of %s not changed: %s", pid, exc)
        return False


def suspend(pid: int) -> bool:
    import psutil
    try:
        process=psutil.Process(pid)
        process.suspend()
        if sys.platform!='win32':
            # SIGSTOP delivery is asynchronous. Report success only after the
            # kernel confirms the state, including under a loaded test runner.
            deadline=time.monotonic()+1
            while process.status()!=psutil.STATUS_STOPPED:
                if time.monotonic()>=deadline:
                    process.resume()
                    return False
                time.sleep(.002)
        return True
    except (psutil.Error, OSError):
        return False


def resume(pid: int) -> bool:
    import psutil
    try:
        psutil.Process(pid).resume()
        return True
    except (psutil.Error, OSError):
        return False


def rtss_dir() -> str | None:
    """RivaTuner Statistics Server, when installed (its frame limiter is configured through profile files)."""
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            path = os.path.join(base, "RivaTuner Statistics Server")
            if os.path.isfile(os.path.join(path, "RTSS.exe")):
                return path
    return None
