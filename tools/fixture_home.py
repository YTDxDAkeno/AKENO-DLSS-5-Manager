"""Populate an AKENO data folder with clearly labelled TEST FIXTURE data (for screenshots and UI tests).

Creates a fake game folder, a synthetic system description (an RTX 3080 at 1440p) and optionally
fixture benchmark sessions. Nothing here is used by the shipped application; fixture benchmarks are
stored with source="fixture" so the UI shows they are not real measurements.

    python tools/fixture_home.py <akeno_home> <games_dir> [--with-benchmarks] [--gpu rtx5090]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main() -> None:
    home, games = Path(sys.argv[1]), Path(sys.argv[2])
    with_bench = "--with-benchmarks" in sys.argv
    os.environ["AKENO_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    from helpers import make_pe, rtx, system_with
    from akeno.services import AppServices
    from akeno.core.models import GpuInfo
    from akeno.hardware import gpu_db

    svc = AppServices()
    if "rtx5090" in sys.argv:
        gpu = rtx("NVIDIA GeForce RTX 5090", device=0x2B85, vram_gb=32, driver_raw="32.0.15.8180")
        gpu.driver_date = "9-30-2026"
    else:
        gpu = rtx()
        gpu.driver_date = "1-16-2025"
    gpu.sources = ["fixture"]
    igpu = gpu_db.classify(GpuInfo("Intel(R) UHD Graphics 770", vendor_id=0x8086, device_id=0x4680,
                                   vram_bytes=128 << 20, driver_version="31.0.101.4502", sources=["fixture"]))
    info = system_with(gpu)
    info.gpus.append(igpu)
    info.os_build = "22631.4890"
    info.cpu_name = "FIXTURE CPU"
    info.ram_bytes = 32 << 30
    info.directx_version = "DirectX 12"
    info.is_64bit = True
    info.detection_errors = ["TEST FIXTURE: synthetic hardware description, not a real scan."]
    info.detected_at = "fixture"
    svc.system = info
    svc.db.set_setting("_last_system", info.to_dict())
    svc.settings.set("rescan_on_start", False)
    g1 = games / "Night City Demo"
    make_pe(g1 / "bin" / "x64" / "NightCityDemo.exe", ["d3d12.dll", "kernel32.dll"], pad=60000)
    make_pe(g1 / "bin" / "x64" / "nvngx_dlss.dll", [], dll=True, version=(310, 2, 1, 0))
    g2 = games / "Retro Shooter"
    make_pe(g2 / "RetroShooter.exe", ["d3d11.dll"], pad=20000)
    make_pe(g2 / "dxgi.dll", [], dll=True, strings={"ProductName": "ReShade"})
    g3 = games / "Arena Online"
    make_pe(g3 / "Arena.exe", ["d3d12.dll"], pad=30000)
    (g3 / "EasyAntiCheat").mkdir(parents=True, exist_ok=True)
    first = None
    for d in (g1, g2, g3):
        g = svc.library.add_manual(str(d))
        first = first or g
    svc.select_game(first.id)
    if with_bench:
        import random
        rnd = random.Random(1)
        for label, fps in (("baseline", 92), ("quality", 61), ("balanced", 68), ("performance", 79)):
            ft = [1000 / fps * rnd.uniform(0.9, 1.12) for _ in range(3000)]
            svc.benchmarks.record(first.id, label, ft, {"gpu": gpu.name, "driver": gpu.driver_version,
                                                        "resolution": "2560x1440"}, "fixture", None)
    print(f"fixture data written to {home}")


if __name__ == "__main__":
    main()
