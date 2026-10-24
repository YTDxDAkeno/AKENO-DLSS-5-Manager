"""The PC's pseudonymous identity for the license server.

The fingerprint is ``SHA-256("akeno-device-v1|" + machine id)``: Windows' ``MachineGuid``, which survives
reinstalling AKENO and changes with a new Windows installation. The server never sees the machine id itself,
and the AKENO prefix means the value cannot be matched with other programs that use ``MachineGuid``.
"""

from __future__ import annotations

import hashlib
import os
import platform
import socket
import sys
import uuid
from pathlib import Path

from akeno.paths import app_home


def machine_id() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value).strip().lower()
        except OSError:
            pass
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(path).read_text(encoding="ascii").strip()
            if text:
                return text
        except OSError:
            pass
    fallback = app_home() / "device-id"         # last resort: stable for this AKENO data folder
    try:
        return fallback.read_text(encoding="ascii").strip()
    except OSError:
        value = uuid.uuid4().hex
        fallback.write_text(value, encoding="ascii")
        return value


def fingerprint() -> str:
    override = os.environ.get("AKENO_DEVICE_ID")          # tests and multi-PC simulations
    return hashlib.sha256(f"akeno-device-v1|{override or machine_id()}".encode()).hexdigest()


def label() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC")[:80]


def os_name() -> str:
    if sys.platform == "win32":
        build = sys.getwindowsversion().build
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    return f"{platform.system()} {platform.release()}"[:80]
