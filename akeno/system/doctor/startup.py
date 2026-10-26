"""Startup Analyzer: the programs that start when you sign in, where each comes from, who published it, whether
its signature is valid, and a reversible way to switch it off.

Sources (the ones Task Manager's Startup page shows):

* ``HKCU\\...\\CurrentVersion\\Run`` (this user), ``HKLM\\...\\Run`` (all users, 64-bit) and
  ``HKLM\\...\\WOW6432Node\\...\\Run`` (all users, 32-bit);
* the Startup folders of this user and of all users.

**Disabling** works like Task Manager: the entry stays where it is and Windows' ``StartupApproved`` list marks it
disabled. AKENO records the previous ``StartupApproved`` value before it writes, so *Enable* and *Restore* bring
back exactly what was there. Entries of all users need administrator rights; AKENO then asks Windows (UAC) and
says so before. Windows components and security software are never disabled.
"""

from __future__ import annotations

import os
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system import lnk

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# (source id, hive, run key, approved subkey, scope)
REG_SOURCES = (("hkcu_run", "HKCU", RUN, "Run", "user"),
               ("hklm_run", "HKLM", RUN, "Run", "machine"),
               ("hklm_run32", "HKLM", RUN32, "Run32", "machine"))
SOURCE_LABELS = {"hkcu_run": "Registry: this user", "hklm_run": "Registry: all users",
                 "hklm_run32": "Registry: all users (32-bit)", "folder_user": "Startup folder: this user",
                 "folder_common": "Startup folder: all users"}

SCRIPT_HOSTS = {"wscript.exe", "cscript.exe", "mshta.exe", "powershell.exe", "pwsh.exe", "cmd.exe"}
LAUNCHERS = {"rundll32.exe", "regsvr32.exe"} | SCRIPT_HOSTS
SYSTEM_NAMES = {"svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "explorer.exe",
                "dwm.exe", "taskhostw.exe", "conhost.exe", "rundll32.exe", "spoolsv.exe", "wininit.exe"}
SECURITY_PROGRAMS = {"securityhealthsystray.exe", "msmpeng.exe", "msascuil.exe", "avp.exe", "avpui.exe", "avgui.exe",
                     "avastui.exe", "mbamtray.exe", "bdagent.exe", "egui.exe", "ekrn.exe", "nortonui.exe",
                     "mcuicnt.exe", "mcshield.exe", "sophosui.exe", "wrsa.exe", "psuaservice.exe", "f-secure.exe",
                     "gdatatray.exe", "avira.systray.exe", "ccsvchst.exe"}


@dataclass
class StartupEntry:
    id: str                       # "<source>:<value or file name>"
    name: str                     # value name, or the shortcut's file name without extension
    source: str                   # one of SOURCE_LABELS
    scope: str                    # user | machine
    command: str                  # as stored (the value data, or the shortcut target + arguments)
    path: str | None              # the program that starts (resolved), or None when it cannot be determined
    approved_key: tuple[str, str]     # (hive, StartupApproved subkey)
    approved_name: str
    enabled: bool = True
    exists: bool = True
    publisher: str | None = None
    signature: dict = field(default_factory=dict)
    windows_component: bool = False
    protected_reason: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def protected(self) -> bool:
        return bool(self.protected_reason)

    @property
    def needs_admin(self) -> bool:
        return self.scope == "machine"

    def to_dict(self) -> dict[str, Any]:
        return {k: (str(v) if k in ("protected_reason",) else v) for k, v in self.__dict__.items()
                if k not in ("problems", "notes")} | {"problems": [str(p) for p in self.problems],
                                                      "notes": [str(n) for n in self.notes]}


# -- registry access ---------------------------------------------------------------------------------------------
class WinStartupRegistry:
    def _hive(self, hive: str):
        import winreg
        return {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]

    def values(self, hive: str, key: str) -> dict[str, Any]:
        import winreg
        out = {}
        flags = winreg.KEY_READ | (winreg.KEY_WOW64_64KEY if hive == "HKLM" else 0)
        try:
            with winreg.OpenKey(self._hive(hive), key, 0, flags) as k:
                i = 0
                while True:
                    try:
                        name, data, kind = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if kind == winreg.REG_EXPAND_SZ and isinstance(data, str):
                        data = os.path.expandvars(data)
                    out[name] = data
        except OSError:
            pass
        return out

    def set_binary(self, hive: str, key: str, name: str, data: bytes) -> None:
        import winreg
        flags = winreg.KEY_SET_VALUE | (winreg.KEY_WOW64_64KEY if hive == "HKLM" else 0)
        with winreg.CreateKeyEx(self._hive(hive), key, 0, flags) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)

    def delete(self, hive: str, key: str, name: str) -> None:
        import winreg
        flags = winreg.KEY_SET_VALUE | (winreg.KEY_WOW64_64KEY if hive == "HKLM" else 0)
        try:
            with winreg.OpenKey(self._hive(hive), key, 0, flags) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


class MemoryStartupRegistry:
    """In-memory stand-in: {(hive, key lower): {name: data}}."""

    def __init__(self, data: dict[tuple[str, str], dict[str, Any]] | None = None):
        self.data = {(h, k.lower()): dict(v) for (h, k), v in (data or {}).items()}
        self.denied_hives: set[str] = set()          # writes there fail like a standard user's would

    def values(self, hive: str, key: str) -> dict[str, Any]:
        return dict(self.data.get((hive, key.lower()), {}))

    def set_binary(self, hive: str, key: str, name: str, data: bytes) -> None:
        if hive in self.denied_hives:
            raise PermissionError("Access is denied.")
        self.data.setdefault((hive, key.lower()), {})[name] = bytes(data)

    def delete(self, hive: str, key: str, name: str) -> None:
        if hive in self.denied_hives:
            raise PermissionError("Access is denied.")
        self.data.get((hive, key.lower()), {}).pop(name, None)


# -- command lines -------------------------------------------------------------------------------------------------
def split_command(command: str, exists=os.path.isfile) -> tuple[str | None, str]:
    """The program a Run value starts and its arguments. Handles quoted paths, unquoted paths with spaces (the way
    Windows tries them: shortest existing prefix first) and environment variables."""
    cmd = os.path.expandvars((command or "").strip())
    if not cmd:
        return None, ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
        return cmd[1:], ""
    parts = cmd.split(" ")
    for i in range(1, len(parts) + 1):
        candidate = " ".join(parts[:i])
        for c in (candidate, candidate + ".exe"):
            if exists(c):
                return c, " ".join(parts[i:]).strip()
    first = parts[0]
    if not os.path.dirname(first):                     # "rundll32.exe ..." resolves through the search path
        return first, " ".join(parts[1:]).strip()
    return first, " ".join(parts[1:]).strip()


def resolve_program(path: str | None) -> str | None:
    """A bare program name ("rundll32.exe") resolved to System32 like Windows' search path does."""
    if not path:
        return None
    if os.path.dirname(path):
        return path
    if is_windows():
        sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        for name in (path, path + ".exe"):
            if (sysdir / name).is_file():
                return str(sysdir / name)
    return path


def base_name(path: str) -> str:
    """The file name of a Windows path on every platform (Run values always use backslashes)."""
    import ntpath
    return ntpath.basename(path)


def windows_dir() -> str:
    return os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))


def user_writable(path: str) -> bool:
    low = os.path.normcase(path)
    roots = [os.environ.get(v) for v in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE")]
    return any(r and low.startswith(os.path.normcase(r)) for r in roots)


def temp_location(path: str) -> bool:
    low = os.path.normcase(path)
    temps = [os.environ.get(v) for v in ("TEMP", "TMP")]
    return any(t and low.startswith(os.path.normcase(t)) for t in temps) or \
        (os.sep + "temp" + os.sep) in low or (os.sep + "downloads" + os.sep) in low


def approved_enabled(data: bytes | None) -> bool:
    """Task Manager's StartupApproved bytes: absent or an even first byte = enabled (02, 06), odd = disabled."""
    if not data:
        return True
    return (data[0] & 1) == 0


def disabled_bytes(now: float | None = None) -> bytes:
    """03 00 00 00 + the time it was disabled (FILETIME), as Task Manager writes it."""
    ft = int(((now if now is not None else time.time()) + 11644473600) * 10_000_000)
    return b"\x03\x00\x00\x00" + struct.pack("<Q", ft)


ENABLED_BYTES = b"\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"


class StartupAnalyzer:
    def __init__(self, registry=None, folders: dict[str, str] | None = None, signature_check=None,
                 version_info=None, store=None):
        self.reg = registry or (WinStartupRegistry() if is_windows() else MemoryStartupRegistry())
        self.folders = folders if folders is not None else default_folders()
        from akeno.system import signatures
        self._sig = signature_check or signatures.check
        self._ver = version_info or _version_strings
        self.store = store

    # -- listing -----------------------------------------------------------------------------------------------
    def entries(self, ctx=None, check_signatures: bool = True) -> list[StartupEntry]:
        out: list[StartupEntry] = []
        for sid, hive, key, approved, scope in REG_SOURCES:
            states = self.reg.values(hive, APPROVED + "\\" + approved)
            for name, data in self.reg.values(hive, key).items():
                if not isinstance(data, str) or not name:
                    continue
                prog, args = split_command(data)
                prog = resolve_program(prog)
                e = StartupEntry(f"{sid}:{name}", name, sid, scope, data, prog, (hive, APPROVED + "\\" + approved),
                                 name, approved_enabled(states.get(name)))
                out.append(e)
        for sid, folder in self.folders.items():
            hive = "HKCU" if sid == "folder_user" else "HKLM"
            states = self.reg.values(hive, APPROVED + "\\StartupFolder")
            try:
                files = sorted(os.listdir(folder))
            except OSError:
                continue
            for fn in files:
                full = os.path.join(folder, fn)
                if fn.lower() == "desktop.ini" or not os.path.isfile(full):
                    continue
                target, args = full, ""
                if fn.lower().endswith(".lnk"):
                    sc = lnk.read(full)
                    target = sc.target if sc and sc.target else None
                    args = sc.arguments if sc else ""
                command = (f'"{target}" {args}'.strip() if target else full)
                e = StartupEntry(f"{sid}:{fn}", os.path.splitext(fn)[0], sid,
                                 "user" if sid == "folder_user" else "machine", command, target,
                                 (hive, APPROVED + "\\StartupFolder"), fn, approved_enabled(states.get(fn)))
                if sc_network(full):
                    e.notes.append(msg("The shortcut points to a network location."))
                out.append(e)
        sigs = self._sig([e.path for e in out if e.path and os.path.isfile(e.path)], ctx) if check_signatures else {}
        for e in out:
            self._assess(e, sigs.get(e.path) if e.path else None)
        return out

    def _assess(self, e: StartupEntry, sig) -> None:
        path = e.path
        e.exists = bool(path and os.path.isfile(path))
        if sig is not None:
            e.signature = sig.to_dict() if hasattr(sig, "to_dict") else dict(sig)
        if not path:
            e.problems.append(msg("The program it starts could not be determined."))
            return
        name = base_name(path).lower()
        in_windows = os.path.normcase(path).startswith(windows_dir() + os.sep)
        signer = (e.signature or {}).get("signer") or ""
        if e.exists:
            strings = self._ver(path)
            e.publisher = strings.get("CompanyName") or signer or None
        e.windows_component = in_windows and name not in LAUNCHERS and (
            not e.signature or (e.signature.get("status") == "valid" and "microsoft" in signer.lower()))
        if name in SECURITY_PROGRAMS:
            e.protected_reason = msg("Security software: switching it off could leave the PC less protected.")
        elif e.windows_component:
            e.protected_reason = msg("Part of Windows.")
        if not e.exists:
            e.problems.append(msg("The program file does not exist (left over from an uninstalled program?)."))
            return
        if name in SCRIPT_HOSTS or name in ("rundll32.exe", "regsvr32.exe"):
            _, args = split_command(e.command)
            e.notes.append(msg("Starts through {host}: {args}", host=base_name(path), args=args[:200]))
            if name in SCRIPT_HOSTS and re.search(r"-e(nc|ncodedcommand)?\s", args, re.I):
                e.problems.append(msg("Starts an encoded PowerShell command."))
        if name in SYSTEM_NAMES and not in_windows:
            e.problems.append(msg("Uses the name of a Windows program but is not in the Windows folder."))
        if temp_location(path):
            e.problems.append(msg("Starts from a temporary or download folder."))
        status = (e.signature or {}).get("status")
        if status == "invalid":
            e.problems.append(msg("The digital signature is not valid: {d}", d=e.signature.get("detail") or ""))
        elif status == "unsigned" and user_writable(path) and not e.publisher:
            e.problems.append(msg("No digital signature and no publisher, and it runs from a user folder."))
        elif status == "unsigned":
            e.notes.append(msg("Not digitally signed. Many legitimate programs are not; this alone means nothing."))

    # -- changes -------------------------------------------------------------------------------------------------
    def set_enabled(self, entry: StartupEntry, enabled: bool, elevate=None) -> dict:
        """Enable or disable one entry. Records the previous StartupApproved value in the journal first.
        ``elevate(hive, key, name, data_or_None)`` writes machine-wide values through UAC (default: reg.exe)."""
        if not enabled and entry.protected:
            raise PermissionError(msg("{name} is not disabled: {why}", name=entry.name, why=entry.protected_reason))
        hive, key = entry.approved_key
        before = self.reg.values(hive, key).get(entry.approved_name)
        undo = {"hive": hive, "key": key, "name": entry.approved_name,
                "before": before.hex() if isinstance(before, (bytes, bytearray)) else None}
        aid = self.store.begin("doctor", "startup.enable" if enabled else "startup.disable", entry.id,
                               entry.name, undo) if self.store else None
        new = ENABLED_BYTES if enabled else disabled_bytes()
        try:
            try:
                self.reg.set_binary(hive, key, entry.approved_name, new)
            except PermissionError:
                if hive != "HKLM":
                    raise
                (elevate or elevated_write)(hive, key, entry.approved_name, new)
        except Exception as exc:
            if aid:
                self.store.finish(aid, False, str(exc))
            raise
        if aid:
            self.store.finish(aid, True)
        entry.enabled = enabled
        return undo

    def restore(self, action_id: int, elevate=None) -> None:
        """Put back the StartupApproved value an earlier change replaced (or remove it if there was none)."""
        a = self.store.action(action_id) if self.store else None
        if not a or a["state"] != "done" or not a["kind"].startswith("startup."):
            raise ValueError("This change cannot be restored.")
        u = a["undo"]
        try:
            if u.get("before") is None:
                self.reg.delete(u["hive"], u["key"], u["name"])
            else:
                self.reg.set_binary(u["hive"], u["key"], u["name"], bytes.fromhex(u["before"]))
        except PermissionError:
            if u["hive"] != "HKLM":
                raise
            (elevate or elevated_write)(u["hive"], u["key"], u["name"],
                                        None if u.get("before") is None else bytes.fromhex(u["before"]))
        self.store.mark_undone(action_id)

    def history(self) -> list[dict]:
        return [a for a in (self.store.actions("doctor") if self.store else []) if a["kind"].startswith("startup.")]


def sc_network(path: str) -> bool:
    if not path.lower().endswith(".lnk"):
        return False
    sc = lnk.read(path)
    return bool(sc and sc.network)


def default_folders() -> dict[str, str]:
    out = {}
    appdata = os.environ.get("APPDATA")
    if appdata:
        out["folder_user"] = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
    programdata = os.environ.get("ProgramData")
    if programdata and is_windows():
        out["folder_common"] = os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs\StartUp")
    return out


def _version_strings(path: str) -> dict[str, str]:
    try:
        from akeno.games import pe
        info = pe.parse(path)
        return dict(info.strings) if info.valid else {}
    except Exception:                                  # noqa: BLE001 (any unreadable file)
        return {}


def elevated_write(hive: str, key: str, name: str, data: bytes | None) -> None:
    """Write (or delete) one machine-wide StartupApproved value with reg.exe through the UAC prompt."""
    from akeno.system.commands import run_elevated, system32
    if '"' in name or '"' in key:
        raise PermissionError("This entry's name cannot be passed to reg.exe safely.")
    full = f'{hive}\\{key}'
    if data is None:
        params = f'delete "{full}" /v "{name}" /f /reg:64'
    else:
        params = f'add "{full}" /v "{name}" /t REG_BINARY /d {data.hex().upper()} /f /reg:64'
    code = run_elevated(system32("reg.exe"), params, timeout=120, show=False)
    if code is None:
        raise PermissionError(msg("Administrator rights were not granted; nothing was changed."))
    if code != 0:
        raise OSError(msg("reg.exe reported error {code}; nothing was changed.", code=code))
