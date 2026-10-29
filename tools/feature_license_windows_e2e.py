"""Module licenses end to end on a real Windows PC: AKENO VPN Lifetime from code to a real VPN connection.

1. A throwaway local license server (``tools/license_lab.py``, own database and signing key) issues an
   AKENO VPN Lifetime code, an AKENO Full code and a second VPN code.
2. AKENO's own licensing code signs up, redeems the VPN code and must unlock AKENO VPN only.
3. The real main window is built: AKENO Network opens on the VPN tab, every other paid module is a lock page
   (screenshots are saved as evidence).
4. The real elevated network helper (``akeno.network.helper.execute``) refuses LAN Play and the network
   component installation for this license, then runs a real free VPN session (VPN Gate over OpenVPN): the public
   IP must change to a verified exit, and disconnecting must bring the normal IP back.
5. The AKENO Full code unlocks every module; LAN Play passes the helper's license check.
6. A second account: VPN Lifetime plus AKENO Full that ends after 90 seconds falls back to AKENO VPN only;
   revoking the VPN locks it.

Needs Administrator rights (the helper's protected folder and OpenVPN) and the server's Python packages in a
separate environment (``--server-python``). Writes a JSON report; exits 1 when a check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

RESULTS: list[dict] = []
VPN, LAN, SUITE = "akeno-vpn", "akeno-lan-play", "akeno-suite"


def check(ok, name, detail=""):
    RESULTS.append({"ok": bool(ok), "check": name, "detail": str(detail)[:600]})
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return bool(ok)


def info(name, detail):
    RESULTS.append({"ok": None, "check": name, "detail": str(detail)[:600]})
    print(f"[INFO] {name}: {detail}", flush=True)


class Lab:
    def __init__(self, python: str, work: Path, port: int):
        self.python, self.port = python, port
        self.env = {**os.environ, "AKENO_LAB_DIR": str(work / "lab"),
                    "AKENO_PUBLIC_URL": f"http://127.0.0.1:{port}"}
        self.server = None

    def run(self, *argv) -> dict:
        out = subprocess.run([self.python, str(ROOT / "tools" / "license_lab.py"), *argv], env=self.env,
                             capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise RuntimeError(f"license_lab {argv[0]} failed: {out.stderr[-1500:]}")
        return json.loads(out.stdout.strip().splitlines()[-1])

    def start(self) -> str:
        self.server = subprocess.Popen([self.python, str(ROOT / "tools" / "license_lab.py"), "serve", "--port",
                                        str(self.port)], env=self.env)
        url = f"http://127.0.0.1:{self.port}"
        import urllib.request
        for _ in range(120):
            try:
                with urllib.request.urlopen(url + "/api/v1/system/keys", timeout=2):
                    return url
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("the local license server did not start")

    def stop(self):
        if self.server is not None:
            self.server.terminate()
            self.server.wait(20)


def manager(url: str, keys: dict, store: Path | None = None):
    from akeno.licensing import LicenseManager
    from akeno.licensing.config import LicensingConfig
    from akeno.licensing.storage import SecureStore
    return LicenseManager(LicensingConfig(url, keys), SecureStore(store))


def helper_request(body: dict) -> tuple[int, dict]:
    from akeno.network import helper as h
    ident = uuid.uuid4().hex
    h.request_store(ident).save({**body, "created": time.time()})
    code = h.execute(ident)
    return code, h.request_store(ident).load()


def screenshots(m, out: Path) -> dict:
    """The real main window with this license: which pages are real, which are locked; two screenshots."""
    from PySide6.QtWidgets import QApplication
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.locked import LockedModulePage
    from akeno.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow(Controller(AppServices()), m)
    win.resize(1600, 1000)
    win.show()
    shots = {}
    for key in ("akeno_network", "fivem", "license"):
        win.go(key)
        for _ in range(30):
            app.processEvents()
            time.sleep(0.03)
        path = out / f"{'full' if win.locked_pages == [] else 'vpn-only'}-{key}.png"
        win.grab().save(str(path))
        shots[key] = str(path)
    locked = sorted(k for k, p in win.pages.items() if isinstance(p, LockedModulePage))
    start = win.start_page()
    win.close()
    win.deleteLater()
    app.processEvents()
    return {"locked": locked, "start": start, "shots": shots}


def main(args) -> int:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    os.environ["AKENO_HOME"] = str(work / "home")
    out = Path(args.evidence)
    out.mkdir(parents=True, exist_ok=True)
    lab = Lab(args.server_python, work, args.port)
    try:
        return run(args, lab, work, out)
    finally:
        lab.stop()
        Path(args.report).write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
        failed = [r for r in RESULTS if r["ok"] is False]
        print(f"{sum(1 for r in RESULTS if r['ok'])} module-license checks passed, {len(failed)} failed", flush=True)


def run(args, lab: Lab, work: Path, out: Path) -> int:
    prep = lab.run("prepare")
    keys = prep["keys"]
    check(prep["vpn_module_licenses"] and keys, "local license server prepared (migrations 0001-0002, signing key)",
          f"keys={list(keys)}")
    url = lab.start()
    vpn_code = lab.run("codes", "--entitlement", VPN, "--length", "lifetime")["codes"][0]
    full_code = lab.run("codes", "--entitlement", SUITE, "--length", "lifetime")["codes"][0]
    vpn_code_2 = lab.run("codes", "--entitlement", VPN, "--length", "lifetime")["codes"][0]
    check(vpn_code.startswith("AKENO-") and len(vpn_code) == 25, "admin generated an AKENO VPN Lifetime code",
          vpn_code[:6] + "…" + vpn_code[-4:])

    from akeno.licensing import Status, set_manager
    password = secrets.token_urlsafe(16)
    m = manager(url, keys)
    view = m.create_account("vpn-user@example.test", password, "VPN user", "de")
    check(view.status is Status.NOT_ACTIVATED and not view.unlocked, "a new account without a license unlocks nothing",
          view.status.value)
    view = m.redeem(vpn_code)
    check(view.unlocked and view.entitlements == [VPN] and view.allows("vpn"), "the VPN code unlocks AKENO VPN",
          f"entitlements={view.entitlements}")
    check(not any(view.allows(x) for x in ("lan_play", "dlss", "desktop", "gaming", "performance", "*")),
          "the VPN code unlocks no other paid module", "lan_play, dlss, desktop, gaming, performance: locked")
    check(bool(m.state.get("access_certificate")) and view.offline_eligible,
          "a signed offline certificate for AKENO VPN is stored on this PC (DPAPI)", view.status.value)
    set_manager(m)
    shown = screenshots(m, out)
    paid = {"home", "library", "fivem", "downloads", "booster", "benchmark", "doctor", "cleaner", "network",
            "security", "wallpapers", "dock", "widgets", "themes", "community", "sync"}
    check(paid <= set(shown["locked"]) and "akeno_network" not in shown["locked"],
          "the main window builds AKENO Network and shows every other paid module as locked",
          f"start={shown['start']} locked={len(shown['locked'])}")
    check(shown["start"] == "akeno_network", "AKENO opens on AKENO Network for a VPN-only license", shown["start"])
    info("screenshots (real main window, this license)", shown["shots"])

    from akeno.components.service import NetworkModules
    from akeno.network import helper as h
    NetworkModules().set_enabled("vpn", True)
    NetworkModules().set_enabled("lan_play", True)
    code, reply = helper_request({"action": "lan_session", "room_id": uuid.uuid4().hex})
    check(code == 1 and "does not include this module" in reply.get("error", ""),
          "the elevated helper refuses LAN Play for a VPN-only license", reply.get("error"))

    from akeno.network import openvpn_windows
    if openvpn_windows.status()["state"] != "ready":
        result = openvpn_windows.install(consent=True)
        info("VPN component installed with consent (official OpenVPN, pinned SHA-256)", result)
    check(openvpn_windows.status()["state"] == "ready", "the VPN component (official OpenVPN) is ready",
          openvpn_windows.status())

    import free_vpn_windows_e2e as fv
    before = fv.public_ip()
    info("normal public IPv4", before)
    ident = uuid.uuid4().hex
    h.request_store(ident).save({"action": "free_vpn_session", "country": args.country, "consent": 1,
                                 "created": time.time()})
    keys_file = work / "keys.json"
    keys_file.write_text(json.dumps(keys), encoding="utf-8")
    child = subprocess.Popen([sys.executable, __file__, "--helper", ident, "--server", url, "--keys",
                              str(keys_file), "--work", str(work)], env=os.environ.copy())
    status, deadline = {}, time.time() + 420
    while time.time() < deadline:
        status = h.free_vpn_status()
        if status.get("state") in ("connected", "failed", "unavailable") or child.poll() is not None:
            break
        time.sleep(3)
    connected = status.get("state") == "connected" and status.get("exit_ipv4") and \
        len(status.get("confirmed_by") or []) >= 2 and status.get("dns_verified") and status.get("ipv6") == "blocked"
    check(connected, f"a VPN-only user connects through the real helper to a free VPN in {args.country}",
          {k: status.get(k) for k in ("state", "exit_ipv4", "confirmed_by", "server_ip", "error")})
    if connected:
        now_ip = fv.public_ip()
        check(now_ip == status["exit_ipv4"] and now_ip != before, "the public IP changed to the verified VPN exit",
              f"{before} -> {now_ip}")
        h.free_vpn_request(stop=True)
    try:
        child.wait(90)
    except subprocess.TimeoutExpired:
        child.kill()
    final = h.free_vpn_status()
    after = fv.public_ip()
    check(final.get("state") in ("stopped", "failed", "unavailable") and after == before,
          "disconnecting ends the session and the normal IP is back", f"state={final.get('state')} ip={after}")

    view = m.redeem(full_code)
    check(view.full and set(view.entitlements) == {SUITE, VPN} and view.allows("dlss") and view.allows("lan_play"),
          "a later AKENO Full code unlocks every module; AKENO VPN stays", f"entitlements={view.entitlements}")
    shown = screenshots(m, out)
    check(shown["locked"] == [] and shown["start"] == "home", "the main window now builds every page",
          f"start={shown['start']}")
    try:
        h._licensed("lan_play")
        lan_ok = True
    except Exception as exc:                                   # noqa: BLE001 - reported as the check's detail
        lan_ok = exc
    check(lan_ok is True, "the helper's license check now lets LAN Play start", lan_ok)

    m2 = manager(url, keys, work / "second-account")
    m2.create_account("full-month@example.test", password, "Second", "en")
    m2.redeem(vpn_code_2)
    lab.run("full-until", "full-month@example.test", "90")
    view = m2.validate()
    check(view.full and set(view.entitlements) == {SUITE, VPN}, "VPN Lifetime plus AKENO Full (time-limited)",
          f"entitlements={view.entitlements} status={view.status.value}")
    time.sleep(100)
    view = m2.validate()
    check(view.unlocked and view.entitlements == [VPN] and not view.allows("dlss"),
          "when AKENO Full ends, the account falls back to AKENO VPN only", f"entitlements={view.entitlements}")
    lab.run("grant-status", "full-month@example.test", VPN, "revoked")
    view = m2.validate()
    check(not view.unlocked and not m2.state.get("access_certificate"),
          "a revoked AKENO VPN locks it and removes its certificate from the PC", view.status.value)
    return 1 if any(r["ok"] is False for r in RESULTS) else 0


def helper_child(args) -> int:
    """Stands in for the elevated helper process AKENO.exe starts: the same license store, then execute()."""
    from akeno.licensing import set_manager
    from akeno.network import helper as h
    os.environ["AKENO_HOME"] = str(Path(args.work) / "home")
    set_manager(manager(args.server, json.loads(Path(args.keys).read_text(encoding="utf-8"))))
    return h.execute(args.helper)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server-python", default=sys.executable)
    p.add_argument("--work", default=str(Path.cwd() / "feature-license-e2e"))
    p.add_argument("--report", default="feature-license-report.json")
    p.add_argument("--evidence", default="feature-license-evidence")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--country", default="JP")
    p.add_argument("--helper")
    p.add_argument("--server")
    p.add_argument("--keys")
    a = p.parse_args()
    raise SystemExit(helper_child(a) if a.helper else main(a))
