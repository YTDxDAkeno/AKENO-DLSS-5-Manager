"""Synthetic FiveM/RTX 4080 data for real launcher screenshots; never hardware evidence.

Usage: python tools/fivem_fixture.py HOME GAMES
All generated DLL/EXE files are inert PE fixtures. No game or plugin is executed.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import fixture_home


def main():
    fixture_home.main()
    from helpers import make_pe, rtx, system_with
    from akeno.services import AppServices
    from akeno.fivem.models import ServerPolicy
    home, games = (Path(p).resolve() for p in sys.argv[1:3])
    svc = AppServices()
    gpu = rtx("NVIDIA GeForce RTX 4080 — TEST FIXTURE", device=0x2704, vram_gb=16)
    gpu.driver_version = "617.14"
    gpu.sources = ["fixture"]
    system = system_with(gpu)
    system.is_64bit = True
    system.detection_errors = ["TEST FIXTURE — not a real hardware scan"]
    system.detected_at = "fixture"
    svc.db.set_setting("_last_system", system.to_dict())
    root, gta = games / "FiveM Fixture", games / "GTA Legacy Fixture"
    plugins = root / "FiveM.app" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    make_pe(root / "FiveM.exe", ["kernel32.dll"])
    make_pe(gta / "GTA5.exe", ["d3d11.dll"])
    (root / "FiveM.app" / "CitizenFX.ini").write_text(f"[Game]\nIVPath={gta}\nSavedBuildNumber=3258\n")
    make_pe(plugins / "d3d11.dll", [], dll=True, strings={"ProductName": "ReShade — TEST FIXTURE"})
    (plugins / "renodx-dlss5.addon64").write_bytes(b"TEST FIXTURE - NOT EXECUTABLE")
    g = svc.library.add_manual(str(root))
    g.name = "FiveM Legacy — TEST FIXTURE"
    svc.db.upsert_game(g.to_dict(), manual=False)
    svc.fivem.save_policy(g.id, ServerPolicy(server_id="rp-server.example (TEST FIXTURE)", own_server=True,
        permission_reference="TEST FIXTURE: permission must be established on the real server",
        graphics_plugins_allowed=None, pure_level=None), "dlss5_feeder")
    svc.select_game(g.id)
    svc.settings.set("rescan_on_start", False)
    svc.settings.set("check_app_updates", False)
    svc.settings.set("auto_update_compat_db", False)
    svc.db.close()
    print(f"FiveM + RTX 4080 TEST FIXTURE selected in {home}")


if __name__ == "__main__":
    main()
