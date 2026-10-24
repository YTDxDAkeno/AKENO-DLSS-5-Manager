"""Protected storage of the sign-in and license state.

On Windows the file is encrypted with DPAPI (``CryptProtectData``) for the current Windows user: another user
account or another PC cannot read it, and any change to it makes it unreadable (DPAPI authenticates the data).
Elsewhere (development and tests) it is a JSON file readable only by its owner.

Writes are atomic and keep the previous good copy, so a crash or a full disk never loses a lifetime license.
The state holds only licensing data: settings, games, mods, backups and customisations live elsewhere and are
never touched by licensing.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
from pathlib import Path

from akeno.paths import sub

log = logging.getLogger(__name__)

ENTROPY = b"AKENO license state v1"
FILE = "state.bin"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def _dpapi(data: bytes, protect: bool) -> bytes:
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p if protect else ctypes.c_void_p, ctypes.POINTER(_Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src_buf = ctypes.create_string_buffer(data, len(data))          # kept alive until the call returns
    ent_buf = ctypes.create_string_buffer(ENTROPY, len(ENTROPY))
    src = _Blob(len(data), ctypes.cast(src_buf, ctypes.c_void_p))
    ent = _Blob(len(ENTROPY), ctypes.cast(ent_buf, ctypes.c_void_p))
    out = _Blob()
    flags = 0x1                                                      # CRYPTPROTECT_UI_FORBIDDEN
    descr = "AKENO" if protect else None
    if not fn(ctypes.byref(src), descr, ctypes.byref(ent), None, None, flags, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class SecureStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else sub("license")
        self.protected = sys.platform == "win32"

    @property
    def path(self) -> Path:
        return self.dir / FILE

    def _encode(self, state: dict) -> bytes:
        raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return _dpapi(raw, True) if self.protected else raw

    def _decode(self, data: bytes) -> dict:
        raw = _dpapi(data, False) if self.protected else data
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not a state object")
        return value

    def load(self) -> dict:
        for candidate in (self.path, self.path.with_suffix(".bak")):
            try:
                return self._decode(candidate.read_bytes())
            except FileNotFoundError:
                continue
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                log.warning("license state %s is unreadable (%s); keeping it aside", candidate.name, exc)
                try:
                    os.replace(candidate, candidate.with_suffix(".unreadable"))
                except OSError:
                    pass
        return {}

    def save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = self._encode(state)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if self.path.exists():
            try:
                os.replace(self.path, self.path.with_suffix(".bak"))
            except OSError:
                pass
        os.replace(tmp, self.path)

    def clear(self) -> None:
        for suffix in ("", ".bak", ".tmp"):
            p = self.path.with_suffix(suffix) if suffix else self.path
            try:
                p.unlink()
            except FileNotFoundError:
                pass
