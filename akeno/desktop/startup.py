"""Start the Desktop Runtime with Windows.

A per-user ``Run`` entry (``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``), the way Lively registers its
unpackaged build (``WindowsStartup.SetStartupRegistry``): no service, no scheduled task, no administrator rights.
At sign-in Windows starts ``AKENO.exe --desktop``; the runtime restores the saved wallpapers, dock and widgets and
ends by itself when nothing is switched on. It is separate from the suite's own "Launch with Windows".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "AKENO Desktop Runtime"


def command() -> str:
    """The command line Windows runs at sign-in."""
    from akeno.desktop.host import self_command
    program, args = self_command("--desktop")
    if not getattr(sys, "frozen", False):
        windowless = Path(program).with_name("pythonw.exe")     # no console window when run from the source
        if windowless.is_file():
            program = str(windowless)
        args = ["-m", "akeno", "--desktop"]
    return subprocess.list2cmdline([program, *args])


class WindowsRun:
    """The Run key of the current user."""

    def get(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
                value, _ = winreg.QueryValueEx(key, VALUE)
                return str(value)
        except OSError:
            return None

    def set(self, value: str | None) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if value is None:
                try:
                    winreg.DeleteValue(key, VALUE)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, value)


def _store(store):
    if store is not None:
        return store
    return WindowsRun() if sys.platform == "win32" else None


def available(store=None) -> bool:
    return _store(store) is not None


def enabled(store=None) -> bool:
    """The entry exists and starts this AKENO (an entry of a moved or older copy counts as off)."""
    store = _store(store)
    if store is None:
        return False
    value = store.get()
    return bool(value) and os.path.normcase(value) == os.path.normcase(command())


def set_enabled(on: bool, store=None) -> bool:
    """Add or remove the entry; False where Windows' Run key is not available."""
    store = _store(store)
    if store is None:
        return False
    store.set(command() if on else None)
    return True
