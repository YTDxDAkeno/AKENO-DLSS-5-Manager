"""Minimal, read-only Portable Executable inspection.

Only what AKENO needs: machine type, imported DLL names and version-resource strings. The parser
seeks inside the file instead of loading it, so multi-gigabyte game executables are cheap to inspect,
and every offset is bounds-checked so a malformed file yields ``PeInfo(valid=False)`` rather than an
exception.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

MACHINES = {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}


@dataclass
class PeInfo:
    valid: bool
    machine: str | None = None
    is_dll: bool = False
    imports: list[str] = field(default_factory=list)
    file_version: str | None = None
    strings: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    exports: list[str] = field(default_factory=list)
    export_dll_name: str | None = None


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for va, vsize, raw_ptr, raw_size in sections:
        if va <= rva < va + max(vsize, raw_size):
            off = rva - va
            if off >= raw_size:
                return None
            return raw_ptr + off
    return None


def _read_cstr(f, offset: int, limit: int = 256) -> str:
    f.seek(offset)
    data = f.read(limit)
    end = data.find(b"\0")
    return data[: end if end >= 0 else limit].decode("ascii", "replace")


def parse(path: str | Path, with_version: bool = True, version_scan_limit: int = 96 * 1024 * 1024) -> PeInfo:
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            mz = f.read(64)
            if len(mz) < 64 or mz[:2] != b"MZ":
                return PeInfo(False, error="not an MZ executable")
            e_lfanew = struct.unpack_from("<I", mz, 0x3C)[0]
            if e_lfanew <= 0 or e_lfanew > size - 24:
                return PeInfo(False, error="bad PE header offset")
            f.seek(e_lfanew)
            hdr = f.read(24)
            if hdr[:4] != b"PE\0\0":
                return PeInfo(False, error="missing PE signature")
            machine, nsect, _, _, _, opt_size, characteristics = struct.unpack_from("<HHIIIHH", hdr, 4)
            info = PeInfo(True, machine=MACHINES.get(machine, f"0x{machine:04x}"),
                          is_dll=bool(characteristics & 0x2000))
            opt = f.read(opt_size)
            if len(opt) < 2:
                return info
            magic = struct.unpack_from("<H", opt, 0)[0]
            dd_off = 112 if magic == 0x20B else 96   # PE32+ vs PE32
            sections = []
            f.seek(e_lfanew + 24 + opt_size)
            for _ in range(min(nsect, 96)):
                s = f.read(40)
                if len(s) < 40:
                    break
                vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", s, 8)
                sections.append((va, vsize, raw_ptr, raw_size))
            if len(opt) >= dd_off + 8:
                exp_rva, exp_size = struct.unpack_from("<II", opt, dd_off)
                exp_off = _rva_to_offset(exp_rva, sections) if exp_rva else None
                if exp_off is not None and exp_off + 40 <= size:
                    f.seek(exp_off)
                    export = f.read(40)
                    name_rva = struct.unpack_from("<I", export, 12)[0]
                    name_off = _rva_to_offset(name_rva, sections)
                    if name_off is not None and name_off < size:
                        info.export_dll_name = _read_cstr(f, name_off)
                    count = struct.unpack_from("<I", export, 24)[0]
                    names_rva = struct.unpack_from("<I", export, 32)[0]
                    names_off = _rva_to_offset(names_rva, sections)
                    if names_off is not None:
                        for i in range(min(count, 4096)):
                            if names_off + 4 * i + 4 > size:
                                break
                            f.seek(names_off + 4 * i)
                            raw = f.read(4)
                            if len(raw) != 4:
                                break
                            entry = _rva_to_offset(struct.unpack("<I", raw)[0], sections)
                            if entry is not None and entry < size:
                                name = _read_cstr(f, entry)
                                if name:
                                    info.exports.append(name)
            if len(opt) >= dd_off + 16:
                imp_rva, _imp_size = struct.unpack_from("<II", opt, dd_off + 8)
                off = _rva_to_offset(imp_rva, sections) if imp_rva else None
                for i in range(512 if off is not None else 0):
                    f.seek(off + i * 20)
                    desc = f.read(20)
                    if len(desc) < 20 or desc == b"\0" * 20:
                        break
                    name_rva = struct.unpack_from("<I", desc, 12)[0]
                    name_off = _rva_to_offset(name_rva, sections)
                    if name_off is not None and name_off < size:
                        info.imports.append(_read_cstr(f, name_off).lower())
            if len(opt) >= dd_off + 24:
                # Delay-load imports matter too: some engines delay-load d3d12.dll.
                dl_rva = struct.unpack_from("<I", opt, dd_off + 13 * 8)[0] if len(opt) >= dd_off + 13 * 8 + 8 else 0
                off = _rva_to_offset(dl_rva, sections) if dl_rva else None
                for i in range(256 if off is not None else 0):
                    f.seek(off + i * 32)
                    desc = f.read(32)
                    if len(desc) < 32 or desc == b"\0" * 32:
                        break
                    name_off = _rva_to_offset(struct.unpack_from("<I", desc, 4)[0], sections)
                    if name_off is not None and name_off < size:
                        name = _read_cstr(f, name_off).lower()
                        if name and name not in info.imports:
                            info.imports.append(name)
            if with_version and size <= version_scan_limit:
                rsrc = _resource_section(sections, opt, dd_off)
                if rsrc is not None:
                    f.seek(rsrc[0])
                    blob = f.read(min(rsrc[1], 8 * 1024 * 1024))
                else:
                    f.seek(max(0, size - 4 * 1024 * 1024))
                    blob = f.read()
                info.file_version, info.strings = parse_version_blob(blob)
            return info
    except OSError as exc:
        return PeInfo(False, error=str(exc))
    except struct.error as exc:
        return PeInfo(False, error=f"malformed PE: {exc}")


def _resource_section(sections, opt: bytes, dd_off: int) -> tuple[int, int] | None:
    if len(opt) < dd_off + 24:
        return None
    rva, rsize = struct.unpack_from("<II", opt, dd_off + 16)
    if not rva:
        return None
    for va, vsize, raw_ptr, raw_size in sections:
        if va <= rva < va + max(vsize, raw_size):
            return raw_ptr + (rva - va), min(raw_size - (rva - va), max(rsize, 0) or raw_size)
    return None


_KEYS = ("CompanyName", "FileDescription", "FileVersion", "ProductName", "ProductVersion",
         "OriginalFilename", "InternalName", "LegalCopyright")


def parse_version_blob(blob: bytes) -> tuple[str | None, dict[str, str]]:
    version = None
    i = blob.find(struct.pack("<I", 0xFEEF04BD))
    if i >= 0 and i + 16 <= len(blob):
        ms, ls = struct.unpack_from("<II", blob, i + 8)
        version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    strings: dict[str, str] = {}
    for key in _KEYS:
        k = key.encode("utf-16-le") + b"\0\0"
        j = blob.find(k)
        if j < 0:
            continue
        j += len(k)
        if j % 4:
            j += 4 - j % 4  # values are DWORD aligned
        end = j
        while end + 1 < len(blob) and blob[end:end + 2] != b"\0\0":
            end += 2
        value = blob[j:end].decode("utf-16-le", "replace").strip()
        if value and re.fullmatch(r"[\x20-\x7e -￿]{1,200}", value):
            strings[key] = value
    return version, strings


def contains_bytes(path: str | Path, needles: list[bytes], limit: int = 64 * 1024 * 1024) -> set[bytes]:
    """Return which byte strings occur in the first ``limit`` bytes of a file (streamed)."""
    found: set[bytes] = set()
    overlap = max((len(n) for n in needles), default=1) - 1
    try:
        with open(path, "rb") as f:
            tail = b""
            read = 0
            while read < limit and len(found) < len(needles):
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                read += len(chunk)
                buf = tail + chunk
                for n in needles:
                    if n not in found and n in buf:
                        found.add(n)
                tail = buf[-overlap:] if overlap else b""
    except OSError:
        pass
    return found
