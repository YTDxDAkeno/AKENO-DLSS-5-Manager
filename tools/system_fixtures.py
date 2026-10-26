"""Screenshots of the 3.1 SYSTEM pages and the mod library with stand-in providers - TEST DATA, never evidence of
real hardware. Every picture carries a red "TEST DATA" banner in the status bar.

    python tools/system_fixtures.py OUT_DIR [--lang en|de] [--size 1600x1000]

The real Windows providers are exercised by ``tools/system_check.py`` instead.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BANNER = "TEST DATA – stand-in providers, not measurements of this PC"


def _junk(base: Path) -> dict:
    env = {"LOCALAPPDATA": str(base / "Local"), "TEMP": str(base / "Local" / "Temp"),
           "SystemRoot": str(base / "Windows"), "USERPROFILE": str(base / "User"), "APPDATA": str(base / "Roaming")}
    old = time.time() - 30 * 86400
    files = {Path(env["TEMP"]) / f"setup-{i}.tmp": 3_500_000 for i in range(6)}
    files[base / "Local" / "NVIDIA" / "DXCache" / "cache.bin"] = 48_000_000
    files[base / "Local" / "CrashDumps" / "Game.exe.1234.dmp"] = 210_000_000
    for p, size in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(size)
        os.utime(p, (old, old))
    return env


def _minecraft(base: Path) -> dict:
    inst = base / "Roaming" / "PrismLauncher" / "instances" / "Survival+"
    mods = inst / ".minecraft" / "mods"
    mods.mkdir(parents=True)
    (inst / "instance.cfg").write_text("name=Survival+ (test data)\n")
    (inst / "mmc-pack.json").write_text(json.dumps({"components": [
        {"uid": "net.minecraft", "version": "1.20.1"}, {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"}]}))
    for name, mid, extra in (("sodium-0.5.8.jar", "sodium", {}), ("sodium-old.jar", "sodium", {}),
                             ("lithium-0.11.2.jar", "lithium", {}),
                             ("iris-1.7.0.jar", "iris", {"depends": {"sodium": ">=0.5.0"}})):
        with zipfile.ZipFile(mods / name, "w") as z:
            z.writestr("fabric.mod.json", json.dumps({"id": mid, "version": name.split("-")[-1][:-4],
                                                      "name": mid.title(), **extra}))
    return {"APPDATA": str(base / "Roaming"), "USERPROFILE": str(base / "User")}


def install_stand_ins(svc, base: Path) -> None:
    from akeno.system.doctor.providers import FakeProbe
    from akeno.system.doctor.startup import RUN, MemoryStartupRegistry, StartupAnalyzer
    from akeno.system.securitycenter.providers import FakeSecurity, defender_active_status
    from akeno.system.signatures import Signature
    tools = svc.system_tools
    tools.doctor.probe = FakeProbe(
        cpu={"percent": 71.0, "per_core": [71.0] * 8, "logical": 16, "physical": 8},
        memory={"total": 32 * 2 ** 30, "available": 9 * 2 ** 30, "used": 23 * 2 ** 30, "percent": 72.0,
                "swap_total": 8 * 2 ** 30, "swap_used": 2 ** 30, "swap_percent": 12.5},
        processes=[{"pid": 99, "name": "SearchIndexer.exe", "exe": "C:\\Windows\\System32\\SearchIndexer.exe",
                    "cpu_percent": 38.0, "rss": 310 * 2 ** 20, "io_bps": 6e6},
                   {"pid": 120, "name": "OneDrive.exe", "exe": "C:\\Users\\Player\\AppData\\Local\\Microsoft\\OneDrive"
                    "\\OneDrive.exe", "cpu_percent": 14.0, "rss": 180 * 2 ** 20, "io_bps": 2e6}],
        volumes=[{"mount": "C:\\", "total": 1000 * 2 ** 30, "free": 21 * 2 ** 30, "removable": False},
                 {"mount": "D:\\", "total": 2000 * 2 ** 30, "free": 740 * 2 ** 30, "removable": False}],
        events={"app_crash": [{"time": f"2026-10-2{i}T21:0{i}:00+00:00",
                               "props": ["Game.exe", "1.0.4", "", "nvwgf2umx.dll", "", "", "0xc0000005"]}
                              for i in range(3)]})
    tools.doctor.startup = StartupAnalyzer(
        MemoryStartupRegistry({("HKCU", RUN): {"Discord": '"C:\\Users\\Player\\AppData\\Local\\Discord\\Update.exe" '
                                                          '--processStart Discord.exe',
                                               "Updater": '"C:\\Users\\Player\\AppData\\Roaming\\tool\\updater.exe"'}}),
        {}, lambda paths, ctx=None: {p: Signature("valid", "Discord Inc.") if "Discord" in p else Signature("unsigned")
                                     for p in paths}, lambda p: {}, tools.store)
    tools.cleaner.env = _junk(base / "junk")
    import ipaddress

    def prober(method, target):
        host = target.rsplit(":", 1)[0] if method == "tcp" else target
        try:
            local = ipaddress.ip_address(host).is_private
        except ValueError:
            local = False
        if local:
            return 1.5 + (time.monotonic() * 1000 % 2)              # the router answers quickly
        if method == "icmp":
            return None                                               # many servers drop ping
        return 18.0 + (time.monotonic() * 1000 % 9)
    tools.network._prober = prober
    tools.mods.env = _minecraft(base / "mc")
    mods = base / "mods"
    mods.mkdir(parents=True)
    with zipfile.ZipFile(mods / "free-fps-mod.zip", "w") as z:
        z.writestr("readme.txt.exe", b"MZ")
        z.writestr("install.vbs", "x")
    (mods / "dinput8.dll").write_bytes(b"MZ" + b"\0" * 400)
    tools.security.provider = FakeSecurity(
        health={"antivirus": "good", "firewall": "good"},
        products={"antivirus": [{"name": "Microsoft Defender Antivirus", "state": "on", "signature": "up_to_date"}],
                  "firewall": [{"name": "Windows Firewall", "state": "on"}]},
        firewall=[{"name": "Domain", "enabled": True}, {"name": "Private", "enabled": True},
                  {"name": "Public", "enabled": True}],
        defender_status=defender_active_status(signature_age_days=1, quick_scan_age_days=2),
        history=[{"name": "PUA:Win32/Presenoker", "severity": "low", "status": "quarantined", "unresolved": False,
                  "resources": ["C:\\Users\\Player\\Downloads\\free-fps-boost.exe"],
                  "time": "2026-10-21T18:22:00+02:00"}],
        procs=[{"pid": 41, "name": "svchost.exe", "exe": "C:\\Users\\Player\\AppData\\Roaming\\svchost.exe",
                "connections": 3},
               {"pid": 52, "name": "steam.exe", "exe": "C:\\Program Files (x86)\\Steam\\steam.exe", "connections": 22}],
        signatures={"C:\\Program Files (x86)\\Steam\\steam.exe": Signature("valid", "Valve Corp.")})
    tools._fixture_mods = str(mods)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--size", default="1600x1000")
    args = ap.parse_args(argv)
    home = Path(tempfile.mkdtemp(prefix="akeno-system-fixtures-"))
    os.environ["AKENO_HOME"] = str(home / "home")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication([])
    fixture_home = importlib.import_module("fixture_home")
    sys.argv = ["x", str(home / "home"), str(home / "games")]
    fixture_home.main()
    from akeno import i18n
    i18n.set_language(args.lang)
    from akeno.services import AppServices
    from akeno.ui import theme, workers
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    theme.apply(app)
    svc = AppServices()
    install_stand_ins(svc, home)
    win = MainWindow(Controller(svc))
    banner = QLabel(BANNER)
    banner.setStyleSheet(f"color: white; background: {theme.RED}; padding: 2px 10px; font-weight: 600;")
    win.statusBar().addPermanentWidget(banner)
    w, h = (int(x) for x in args.size.split("x"))
    win.resize(w, h)
    win.show()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def settle(ms: int = 600) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        workers.drain(30000)
        app.processEvents()

    def shot(name: str) -> None:
        settle(400)
        win.grab().save(str(out / f"{name}-{args.lang}.png"))
        print("saved", name)

    tools = svc.system_tools
    win.go("doctor")
    page = win.pages["doctor"]
    settle(1500)
    shot("doctor-overview")
    page.show_tab("slowpc")
    page.slowpc.run()
    settle(1500)
    shot("doctor-slowpc")
    page.show_tab("startup")
    page.startup.refresh()
    settle(1000)
    shot("doctor-startup")
    page.show_tab("crashes")
    page.crashes.run()
    settle(1000)
    shot("doctor-crashes")
    win.go("cleaner")
    win.pages["cleaner"].junk.scan()
    settle(1500)
    shot("cleaner-junk")
    win.go("network")
    net = win.pages["network"]
    net.show_tab("stability")
    tab = net.tabs_w["stability"]
    tab.duration.setCurrentIndex(0)
    tab.run()
    settle(17000)
    shot("network-stability")
    win.go("installed")
    win.pages["installed"].scan()
    settle(2000)
    shot("installed-mods")
    win.go("security")
    sec = win.pages["security"]
    settle(1500)
    shot("security-overview")
    sec.show_tab("files")
    sec.tabs_w["files"].check([tools._fixture_mods])
    settle(1500)
    shot("security-files")
    sec.show_tab("processes")
    sec.tabs_w["processes"].refresh()
    settle(1500)
    shot("security-processes")
    sec.show_tab("history")
    settle(1200)
    shot("security-history")
    workers.drain(30000)
    tools.store.close()
    svc.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
