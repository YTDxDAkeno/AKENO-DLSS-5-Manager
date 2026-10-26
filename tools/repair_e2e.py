"""The way back for a 3.1.0 that no longer starts: the new installer over it, on a real Windows PC (CI runner).

    python tools/repair_e2e.py --broken-installer published\\AKENO-Setup-3.1.0.exe \\
        --installer packaging\\out\\AKENO-Gaming-Suite-3.1.1-Setup-x64.exe --work %RUNNER_TEMP%\\repair \\
        [--report repair-report.md]

**Only for a disposable test machine** (it installs AKENO for the current user and writes its data folder).

1. User data like a 3.0/3.1 user has it: license state (DPAPI), settings, themes, wallpapers, mod backups, and in
   ``system.sqlite3`` the Hardware Health report exactly as 3.1.0 stored it (CPU temperature not available), more
   System Doctor and Network Lab results and undo records of both.
2. The published 3.1.0 installer; its AKENO.exe builds its pages from that data and does not get through
   (the start crash users see: ``TypeError: unhashable type: 'dict'``).
3. The new installer over it (silent, per user, nothing removed before).
4. The new AKENO.exe: ``--check-start`` builds every page from the same data and shows the saved results; a normal
   start stays up and its start guard clears; the user data and every stored result and undo record are unchanged.

Nothing on the user's side has to work for this: no AKENO window, no update page, no deleted files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

LEGACY = ROOT / "tests" / "data" / "akeno-3.1.0-doctor-hardware.json"


def seed_system_results() -> None:
    """``system.sqlite3`` as a 3.1.0 user has it after running Hardware Health and a few other tools."""
    from akeno.system.doctor.providers import FakeProbe
    from akeno.system.doctor.service import DoctorService
    from akeno.system.network.service import NetworkService
    from akeno.system.store import SystemStore
    store = SystemStore()
    probe = FakeProbe(cpu={"percent": 12.0, "per_core": [12.0], "logical": 8, "physical": 4},
                      volumes=[{"mount": "C:\\", "total": 500 * 2 ** 30, "free": 120 * 2 ** 30, "removable": False}],
                      events={"app_crash": [{"time": f"2026-10-2{i}T10:00:00+00:00",
                                             "props": ["Game.exe", "1.0", "", "dxgi.dll", "", "", "0xc0000005"]}
                                            for i in range(3)]})
    doctor = DoctorService(store, probe=probe)
    doctor.slow_pc()
    doctor.crashes()
    NetworkService(store, prober=lambda method, target: None if method == "icmp" else 21.0).stability(count=3)
    a = store.begin("doctor", "startup.disable", "OneDrive", "disabled at start", undo={"value": "OneDrive.exe"})
    store.finish(a, True)
    b = store.begin("network", "dns.set", "Ethernet", "1.1.1.1", undo={"previous": {"dhcp": True}})
    store.finish(b, True)
    store.add_history("doctor.hardware", json.loads(LEGACY.read_text(encoding="utf-8")), keep=50)
    store.close()


def system_rows() -> dict:
    from akeno.paths import app_home
    con = sqlite3.connect(app_home() / "system.sqlite3")
    try:
        return {"results": con.execute("SELECT id, kind, data FROM history WHERE kind != 'doctor.sample' "
                                       "ORDER BY id").fetchall(),
                "undo": con.execute("SELECT * FROM actions ORDER BY id").fetchall()}
    finally:
        con.close()


def kill_tree(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)


def run_build(exe: Path, *args: str, timeout: float) -> tuple[int | None, float]:
    """Exit code (None: still running after ``timeout``, e.g. on the "Unhandled exception" dialog) and seconds."""
    start = time.monotonic()
    proc = subprocess.Popen([str(exe), *args], cwd=str(exe.parent))
    try:
        return proc.wait(timeout), time.monotonic() - start
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        return None, time.monotonic() - start


def install(report, installer: Path, target: Path, work: Path, what: str) -> bool:
    p = subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                        f"/DIR={target}", f"/LOG={work / (what + '-setup.log')}"], timeout=600)
    return report.add(p.returncode == 0 and (target / "AKENO.exe").is_file(), f"{what} installer (per user, silent)",
                      f"exit code {p.returncode}, {target}")


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("this test runs on a disposable Windows machine only")
        return 2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--broken-installer", type=Path, required=True, help="the published 3.1.0 installer")
    ap.add_argument("--broken-version", default="3.1.0")
    ap.add_argument("--installer", type=Path, required=True, help="this build's installer")
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import update_e2e
    from akeno import __version__
    from akeno.paths import app_home, logs_dir
    report = update_e2e.Report()
    args.work.mkdir(parents=True, exist_ok=True)
    target = args.work / "Programs" / "AKENO Gaming Suite"
    try:
        seeded = update_e2e.seed_user_data()
        seed_system_results()
        before = system_rows()
        report.add(True, "user data with saved System Doctor and Network Lab results and undo records",
                   f"{len(before['results'])} results, {len(before['undo'])} undo records, Hardware Health as "
                   f"{args.broken_version} stored it")
        if not install(report, args.broken_installer, target, args.work, args.broken_version):
            return finish(report, args)
        report.add(update_e2e.file_version(target / "AKENO.exe") == args.broken_version,
                   f"installed AKENO.exe is {args.broken_version}", update_e2e.file_version(target / "AKENO.exe"))
        shots = args.work / "shots-broken"
        code, secs = run_build(target / "AKENO.exe", "--screenshot", str(shots), timeout=180)
        report.add(code != 0, f"{args.broken_version} cannot build its pages from this data (the reported start "
                   "crash, reproduced)", f"exit code {code} after {secs:.0f} s" if code is not None else
                   f"stopped after {secs:.0f} s on its error dialog")
        update_e2e.end_all(target)

        if not install(report, args.installer, target, args.work, __version__):
            return finish(report, args)
        report.add(update_e2e.file_version(target / "AKENO.exe") == __version__,
                   f"installed AKENO.exe is {__version__} (installed over {args.broken_version})",
                   update_e2e.file_version(target / "AKENO.exe"))
        result = logs_dir() / "check-start.txt"
        result.unlink(missing_ok=True)
        code, secs = run_build(target / "AKENO.exe", "--check-start", timeout=240)
        text = result.read_text(encoding="utf-8") if result.exists() else ""
        report.add(code == 0 and "start check OK" in text, f"{__version__} builds every page from the same data "
                   "and shows the saved results", (text.splitlines() or [f"exit code {code}"])[0] +
                   f" ({secs:.0f} s)")
        shots = args.work / "shots-fixed"
        code, secs = run_build(target / "AKENO.exe", "--screenshot", str(shots), timeout=300)
        report.add(code == 0 and (shots / "doctor.png").is_file(), f"{__version__} renders every page "
                   "(screenshots, System Doctor included)", f"exit code {code} after {secs:.0f} s")
        guard = app_home() / "startup.json"
        proc = subprocess.Popen([str(target / "AKENO.exe")], cwd=str(target))
        time.sleep(40)                           # the start guard clears after 20 s of a window that stays up
        alive = proc.poll() is None
        kill_tree(proc)
        report.add(alive and not guard.exists(), "a normal start stays up and its start guard clears",
                   "running" if alive else f"ended with exit code {proc.returncode}")
        update_e2e.end_all(target)
        after = system_rows()
        report.add(after["results"][:len(before["results"])] == before["results"],
                   "saved System results unchanged (nothing deleted or rewritten)",
                   f"{len(before['results'])} results")
        report.add(after["undo"] == before["undo"], "undo records unchanged", f"{len(before['undo'])} records")
        update_e2e.user_data_intact(report, seeded, "after the repair")
        h = hashlib.sha256((target / "AKENO.exe").read_bytes()).hexdigest()
        report.add(None, "repaired AKENO.exe", h)
    except Exception as exc:                     # a crash of the test itself is a failed check, with its reason
        import traceback
        traceback.print_exc()
        report.add(False, "the test itself stopped", f"{type(exc).__name__}: {exc}")
    return finish(report, args)


def finish(report, args) -> int:
    text = report.markdown().replace("### Windows update end-to-end test",
                                     "### Repair of a 3.1.0 that no longer starts (installer over it)")
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
