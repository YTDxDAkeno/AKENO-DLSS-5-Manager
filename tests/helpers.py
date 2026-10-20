"""Test fixtures: synthetic PE files, fake packages and a fake download manager."""

from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path

from akeno.core.models import GpuInfo, SystemInfo, DisplayInfo
from akeno.hardware import gpu_db


def _version_blob(version: tuple[int, int, int, int] | None, strings: dict[str, str]) -> bytes:
    out = bytearray()
    if version:
        a, b, c, d = version
        out += struct.pack("<IIII", 0xFEEF04BD, 0x10000, (a << 16) | b, (c << 16) | d)
        out += b"\0" * 36
    for k, v in strings.items():
        while len(out) % 4:
            out += b"\0"
        out += k.encode("utf-16-le") + b"\0\0"
        while len(out) % 4:
            out += b"\0"
        out += v.encode("utf-16-le") + b"\0\0"
    return bytes(out)


def make_pe(path: Path, imports: list[str] = (), machine: str = "x64", dll: bool = False,
            version: tuple[int, int, int, int] | None = None, strings: dict[str, str] | None = None,
            pad: int = 0) -> Path:
    """Write a minimal but structurally valid PE file with an import table and a version blob."""
    mach = {"x64": 0x8664, "x86": 0x14C}[machine]
    pe32plus = machine == "x64"
    opt_size = 240 if pe32plus else 224
    dd_off = 112 if pe32plus else 96
    # .idata section content
    idata = bytearray()
    n = len(imports)
    desc_size = 20 * (n + 1)
    names_off = desc_size
    names = bytearray()
    descs = bytearray()
    for imp in imports:
        rva = 0x1000 + names_off + len(names)
        descs += struct.pack("<IIIII", 0, 0, 0, rva, 0)
        names += imp.encode() + b"\0"
    descs += b"\0" * 20
    idata = descs + names
    idata += b"\0" * (0x200 - len(idata) % 0x200)
    rsrc = _version_blob(version, strings or {})
    rsrc += b"\0" * (0x200 - len(rsrc) % 0x200 if len(rsrc) % 0x200 else 0)
    if not rsrc:
        rsrc = b"\0" * 0x200
    headers_size = 0x400
    idata_ptr = headers_size
    rsrc_ptr = idata_ptr + len(idata)
    buf = bytearray(b"\0" * headers_size)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)
    buf[0x80:0x84] = b"PE\0\0"
    chars = 0x0022 | (0x2000 if dll else 0)
    struct.pack_into("<HHIIIHH", buf, 0x84, mach, 2, 0, 0, 0, opt_size, chars)
    opt = 0x98
    struct.pack_into("<H", buf, opt, 0x20B if pe32plus else 0x10B)
    struct.pack_into("<I", buf, opt + dd_off - 4, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<II", buf, opt + dd_off + 8, 0x1000, desc_size)       # import dir
    struct.pack_into("<II", buf, opt + dd_off + 16, 0x2000, len(rsrc))      # resource dir
    sect = opt + opt_size
    struct.pack_into("<8sIIIIIIHHI", buf, sect, b".idata", len(idata), 0x1000, len(idata), idata_ptr, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<8sIIIIIIHHI", buf, sect + 40, b".rsrc", len(rsrc), 0x2000, len(rsrc), rsrc_ptr, 0, 0, 0, 0, 0x40000040)
    data = bytes(buf) + bytes(idata) + bytes(rsrc) + b"\0" * pad
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rtx(name="NVIDIA GeForce RTX 3080", device=0x2206, vram_gb=10, driver_raw="32.0.15.6109") -> GpuInfo:
    g = GpuInfo(name=name, vendor_id=0x10DE, device_id=device, vram_bytes=vram_gb * 1024 ** 3,
                driver_version_raw=driver_raw, feature_level="12_2")
    gpu_db.classify(g)
    g.driver_version = gpu_db.nvidia_driver_from_windows_version(driver_raw)
    return g


def system_with(gpu: GpuInfo, w=2560, h=1440) -> SystemInfo:
    return SystemInfo(os_name="Windows 11 Pro", os_display_version="23H2", gpus=[gpu],
                      displays=[DisplayInfo("\\\\.\\DISPLAY1", w, h, 144, True)])


OPTI_INI = """; OptiScaler test template
[Upscalers]
Dx11Upscaler=auto
Dx12Upscaler=auto
VulkanUpscaler=auto

[Log]
LogToFile=auto
LogLevel=auto

[DlssNr]
; comment kept
Enabled=auto
WorkingScale=auto
PreSr=auto
"""


def make_opti_package(tmp: Path) -> tuple[Path, dict]:
    """A zip shaped like the Skynizz release plus the matching catalog entry."""
    dll = make_pe(tmp / "src" / "OptiScaler.dll", ["kernel32.dll"], dll=True,
                  strings={"ProductName": "OptiScaler", "OriginalFilename": "OptiScaler.dll"})
    fwd = make_pe(tmp / "src" / "nvngx.dll_dlssnr.dll", ["kernel32.dll"], dll=True)
    files = {"OptiScaler.dll": dll.read_bytes(), "nvngx.dll_dlssnr.dll": fwd.read_bytes(),
             "OptiScaler.ini": OPTI_INI.encode(), "INSTALL.bat": b"@echo off", "install.py": b"print('x')"}
    zpath = tmp / "OptiScaler-DLSS5-test.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for n, d in files.items():
            z.writestr(f"DLSS5-Optimised/{n}", d)
    entry = {
        "id": "opti-test", "backend": "optiscaler_nr", "name": "OptiScaler DLSS 5 (test)", "version": "9.9.9",
        "upstream": "https://github.com/example/optiscaler", "url": "https://github.com/example/opti.zip",
        "sha256": sha(zpath.read_bytes()), "size": zpath.stat().st_size, "license": "GPL-3.0", "trust": "verified",
        "root": "DLSS5-Optimised",
        "files": {n: sha(files[n]) for n in ("OptiScaler.dll", "nvngx.dll_dlssnr.dll", "OptiScaler.ini")},
        "apis": ["d3d12", "d3d11", "vulkan"], "architectures": ["x64"],
    }
    return zpath, entry


class FakeDownloads:
    """DownloadManager stand-in that serves local files for allow-listed URLs and still verifies hashes."""

    def __init__(self, files: dict[str, Path], cache: Path):
        from akeno.security.downloads import DownloadManager
        self.files = files
        self.real = DownloadManager(["github.com"], cache)
        self.cache = cache

    def check_url(self, url):
        self.real.check_url(url)

    def cached_path(self, sha256, name):
        return self.real.cached_path(sha256, name)

    def fetch(self, url, sha256, name, expected_size=None, progress=None, token=None):
        from akeno.security.downloads import DownloadResult, VerificationError
        self.check_url(url)
        data = self.files[url].read_bytes()
        if sha(data) != sha256:
            raise VerificationError("SHA-256 mismatch")
        dest = self.cached_path(sha256, name)
        dest.write_bytes(data)
        return DownloadResult(dest, sha256, len(data), False)
