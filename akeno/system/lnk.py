"""Reads the target of a Windows shortcut (.lnk) from its bytes ([MS-SHLLINK]); nothing is resolved or started.

Only the fields Startup Analyzer and the broken-shortcut check need: the local target path (LinkInfo, or the
relative path joined with the working directory) and the arguments.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

HEADER_SIZE = 0x4C
LINK_CLSID = bytes.fromhex("0114020000000000c000000000000046")
MAX_LNK = 1 << 20

HAS_ID_LIST, HAS_LINK_INFO, HAS_NAME, HAS_RELATIVE_PATH, HAS_WORKING_DIR, HAS_ARGUMENTS, HAS_ICON, IS_UNICODE = (
    0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80)


@dataclass
class Shortcut:
    target: str | None
    arguments: str = ""
    working_dir: str = ""
    network: bool = False           # the target is on a network share (not checked for existence)


def read(path: str) -> Shortcut | None:
    """The shortcut's target, or None when the file is not a readable shell link."""
    try:
        if os.path.getsize(path) > MAX_LNK:
            return None
        with open(path, "rb") as f:
            data = f.read(MAX_LNK)
    except OSError:
        return None
    return parse(data)


def parse(data: bytes) -> Shortcut | None:
    if len(data) < HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != HEADER_SIZE or data[4:20] != LINK_CLSID:
        return None
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = HEADER_SIZE
    try:
        if flags & HAS_ID_LIST:
            size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + size
        local = None
        network = False
        if flags & HAS_LINK_INFO:
            info_size, header_size, info_flags, _vol, base_off, net_off, suffix_off = struct.unpack_from(
                "<IIIIIII", data, pos)
            if info_flags & 0x1 and base_off:
                suffix = None
                if header_size >= 0x24:
                    uoff, usuffix = struct.unpack_from("<II", data, pos + 0x1C)
                    local = _cstr_w(data, pos + uoff) if uoff else None
                    suffix = _cstr_w(data, pos + usuffix) if usuffix else None
                if not local:
                    local = _cstr_a(data, pos + base_off)
                    suffix = _cstr_a(data, pos + suffix_off) if suffix_off else None
                if local and suffix:
                    local = local.rstrip("\\") + "\\" + suffix.lstrip("\\")
            if info_flags & 0x2 and net_off:
                network = True
            pos += info_size
        strings = {}
        for key, bit in (("name", HAS_NAME), ("relative", HAS_RELATIVE_PATH), ("workdir", HAS_WORKING_DIR),
                         ("args", HAS_ARGUMENTS), ("icon", HAS_ICON)):
            if flags & bit:
                count = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                if flags & IS_UNICODE:
                    strings[key] = data[pos:pos + count * 2].decode("utf-16-le", errors="replace")
                    pos += count * 2
                else:
                    strings[key] = data[pos:pos + count].decode("mbcs" if os.name == "nt" else "latin-1",
                                                                errors="replace")
                    pos += count
    except (struct.error, IndexError):
        return None
    target = local
    if not target and strings.get("relative"):
        base = strings.get("workdir") or ""
        target = os.path.normpath(os.path.join(base, strings["relative"])) if base else None
    return Shortcut(os.path.expandvars(target) if target else None, strings.get("args", ""),
                    strings.get("workdir", ""), network)


def _cstr_a(data: bytes, off: int) -> str | None:
    end = data.find(b"\0", off)
    if end < 0:
        return None
    return data[off:end].decode("mbcs" if os.name == "nt" else "latin-1", errors="replace") or None


def _cstr_w(data: bytes, off: int) -> str | None:
    end = off
    while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
        end += 2
    return data[off:end].decode("utf-16-le", errors="replace") or None


def build(target: str, arguments: str = "", unicode: bool = True) -> bytes:
    """A minimal valid shell link pointing at ``target`` (tests and fixtures)."""
    flags = HAS_LINK_INFO | (IS_UNICODE if unicode else 0) | (HAS_ARGUMENTS if arguments else 0)
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<I", header, 0, HEADER_SIZE)
    header[4:20] = LINK_CLSID
    struct.pack_into("<I", header, 0x14, flags)
    base = target.encode("latin-1", errors="replace") + b"\0"
    header_size = 0x1C
    vol = struct.pack("<IIII", 0x10, 3, 0, 0x10) + b"\0"
    vol_off = header_size
    base_off = vol_off + len(vol)
    suffix_off = base_off + len(base)
    body = vol + base + b"\0"
    info = struct.pack("<IIIIIII", header_size + len(body), header_size, 0x1, vol_off, base_off, 0, suffix_off) + body
    out = bytes(header) + info
    if arguments:
        out += struct.pack("<H", len(arguments)) + (arguments.encode("utf-16-le") if unicode else arguments.encode())
    return out
