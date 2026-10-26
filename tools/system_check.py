"""Run AKENO 3.1's system tools against the real machine (the Windows CI runner) and print what they report.

Read-only: nothing is cleaned, disabled, scanned with changes or repaired. The output is the evidence of how the
real Windows providers behave (event logs, performance counters, the storage stack, Defender, the network), as
opposed to the unit tests, which run the analyses on stand-in data.

    python tools/system_check.py [--json out.json] [--modules doctor,cleaner,network,mods,security] [--eicar]

Exit code 1 means a Python error in AKENO's code (a bug); a Windows answer like "access denied" is reported and
is not an error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _jsonable(o):
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def run_doctor(out: dict) -> None:
    from akeno.hardware.detect import HardwareService
    from akeno.system.doctor.providers import LocalProbe
    from akeno.system.doctor.service import DoctorService
    from akeno.system.store import SystemStore
    store = SystemStore(Path(out["_home"]) / "system.sqlite3")
    system = HardwareService().detect()
    svc = DoctorService(store, system_info=lambda: system, probe=LocalProbe())
    t0 = time.monotonic()
    snap = svc.snapshot()
    out["doctor.snapshot"] = {"seconds": round(time.monotonic() - t0, 1), "score": snap["score"].to_dict(),
                              "snapshot": snap["snapshot"].to_dict(),
                              "findings": [f.to_dict() for f in snap["findings"]]}
    for name, fn in (("slowpc", svc.slow_pc), ("crashes", svc.crashes), ("hardware", svc.hardware)):
        t0 = time.monotonic()
        rep = fn()
        out[f"doctor.{name}"] = {"seconds": round(time.monotonic() - t0, 1), "report": rep.to_dict()}
    t0 = time.monotonic()
    entries = svc.startup_entries()
    out["doctor.startup"] = {"seconds": round(time.monotonic() - t0, 1), "entries": [e.to_dict() for e in entries]}
    out["doctor.shortcuts"] = svc.broken_shortcuts()
    store.close()


def run_cleaner(out: dict) -> None:
    """Scan only: the check never removes a file."""
    from akeno.system.cleaner.service import CleanerService
    from akeno.system.store import SystemStore
    store = SystemStore(Path(out["_home"]) / "system.sqlite3")
    svc = CleanerService(store)
    t0 = time.monotonic()
    res = svc.scan()
    out["cleaner.scan"] = {"seconds": round(time.monotonic() - t0, 1),
                           "categories": [c.summary() for c in res.categories]}
    folder = svc.folder(str(ROOT))
    out["cleaner.folder"] = {"root": folder.root, "total": folder.total, "files": folder.files,
                             "children": len(folder.children)}
    store.close()


def run_network(out: dict) -> None:
    """Overview, a short stability test, the DNS benchmark and one bandwidth sample. No bufferbloat load test and
    no change to any setting."""
    from akeno.system.network.service import NetworkService
    from akeno.system.store import SystemStore
    store = SystemStore(Path(out["_home"]) / "system.sqlite3")
    svc = NetworkService(store)
    t0 = time.monotonic()
    ov = svc.overview()
    out["network.overview"] = {"seconds": round(time.monotonic() - t0, 1),
                               "active": ov["active"].to_dict() if ov["active"] else None,
                               "adapters": [a.to_dict() for a in ov["adapters"]], "errors": ov["errors"],
                               "diagnosis": ov["diagnosis"].to_dict(), "rates": ov["rates"]}
    t0 = time.monotonic()
    rep = svc.stability(count=8)
    out["network.stability"] = {"seconds": round(time.monotonic() - t0, 1), "report": rep.to_dict()}
    t0 = time.monotonic()
    out["network.dns"] = {"seconds": round(time.monotonic() - t0, 1), "results": svc.dns_benchmark()}
    out["network.dns"]["seconds"] = round(time.monotonic() - t0, 1)
    out["network.bandwidth"] = svc.bandwidth()
    store.close()


def run_mods(out: dict) -> None:
    """Scan the runner's library (games found by AKENO's scanners) and Minecraft instances; read-only."""
    from akeno.services import AppServices
    from akeno.system.mods.service import ModScanner
    from akeno.system.store import SystemStore
    svc = AppServices()
    svc.library.scan()
    store = SystemStore(Path(out["_home"]) / "system.sqlite3")
    t0 = time.monotonic()
    groups = ModScanner(svc, store).scan()
    out["mods.scan"] = {"seconds": round(time.monotonic() - t0, 1), "games": len(svc.library.games()),
                        "groups": [g.summary() for g in groups]}
    store.close()
    svc.db.close()


def _eicar() -> bytes:
    """The industry-standard antivirus test file (harmless text every antivirus reports as a test detection),
    assembled at run time so this script itself is not a detection."""
    head = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
    return (head + "-".join(["EICAR", "STANDARD", "ANTIVIRUS", "TEST", "FILE!$H+H*"])).encode("ascii")


def run_security(out: dict) -> None:
    """Status, history, the process review, a report-only file check and, with ``--eicar``, Defender's verdict on
    the EICAR test file plus the download veto. Nothing on the runner is changed: no scan with remediation, no
    setting, no exclusion."""
    import os
    import zipfile
    from akeno.system.securitycenter.service import SecurityService
    from akeno.system.store import SystemStore
    store = SystemStore(Path(out["_home"]) / "system.sqlite3")
    svc = SecurityService(store)
    t0 = time.monotonic()
    rep = svc.overview()
    out["security.overview"] = {"seconds": round(time.monotonic() - t0, 1), "report": rep.to_dict(),
                                "state": rep.data["state"]}
    t0 = time.monotonic()
    out["security.ready"] = list(svc.defender_ready())
    rep = svc.processes()
    out["security.processes"] = {"seconds": round(time.monotonic() - t0, 1), "report": rep.to_dict()}
    work = Path(out["_home"]) / "security-check"
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(work / "sample-mod.jar", "w") as z:
        z.writestr("fabric.mod.json", "{}")
        z.writestr("natives/helper.dll", b"MZ")
    t0 = time.monotonic()
    reports, errors = svc.check_files([sys.executable, str(work)])
    out["security.files"] = {"seconds": round(time.monotonic() - t0, 1), "errors": errors,
                             "reports": [r.to_dict() for r in reports]}
    if out.get("_eicar"):
        folder = Path(out["_home"]) / "eicar-test"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "eicar-test-file.txt"
        try:
            target.write_bytes(_eicar())
            written = target.is_file()
        except OSError as exc:                          # real-time protection may block the write itself
            written = f"blocked: {exc}"
        time.sleep(2)
        still = target.is_file()
        t0 = time.monotonic()
        verdict = svc.verdict(str(folder))
        download = svc.check_download(str(folder), None, "eicar-test-file.txt")
        out["security.eicar"] = {"seconds": round(time.monotonic() - t0, 1), "written": written,
                                 "present_before_scan": still, "verdict": verdict, "download_check": download}
        try:
            target.unlink(missing_ok=True)              # the test file this script created, nothing else
        except OSError:
            pass
    store.close()


MODULES = {"doctor": run_doctor, "cleaner": run_cleaner, "network": run_network, "mods": run_mods,
           "security": run_security}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--modules", default=",".join(MODULES))
    ap.add_argument("--eicar", action="store_true", help="CI only: ask Defender about the EICAR test file")
    ap.add_argument("--print-summary", metavar="JSON", help="print the digest of an earlier --json run and exit")
    args = ap.parse_args(argv)
    if args.print_summary:
        try:
            summary(json.loads(Path(args.print_summary).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"no system check result: {exc}")
        return 0
    import tempfile
    home = tempfile.mkdtemp(prefix="akeno-system-check-")
    import os
    os.environ.setdefault("AKENO_HOME", home)
    out: dict = {"_home": home, "platform": sys.platform, "_eicar": args.eicar}
    failed = []
    for name in args.modules.split(","):
        fn = MODULES.get(name.strip())
        if fn is None:
            continue
        t0 = time.monotonic()
        try:
            fn(out)
        except Exception:                               # a bug in AKENO's code: fail the check
            failed.append(name)
            out[name + ".error"] = traceback.format_exc()
        print(f"{name}: {time.monotonic() - t0:.1f} s{' FAILED' if name in failed else ''}")
    text = json.dumps(out, indent=1, default=_jsonable, ensure_ascii=False)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    summary(out)
    for name in failed:
        print(out[name + ".error"])
    return 1 if failed else 0


def summary(out: dict) -> None:
    """A readable digest for the CI log."""
    from akeno.system.model import dec
    snap = out.get("doctor.snapshot")
    if snap:
        s = snap["snapshot"]
        print(f"Health indicator {snap['score']['value']} ({snap['seconds']} s); errors: {s['errors']}")
        print("CPU", {k: v.get("value") for k, v in s["cpu"].items()}, "memory",
              {k: v.get("value") for k, v in s["memory"].items()})
        for g in s["gpus"]:
            print("GPU", g["name"], {k: (v.get("value"), v.get("source"), str(v.get("note"))[:60])
                                    for k, v in g["readings"].items()})
        print("Temperatures", s["temperatures"])
    for key in ("doctor.slowpc", "doctor.crashes", "doctor.hardware"):
        r = out.get(key)
        if r:
            rep = r["report"]
            print(f"{key} ({r['seconds']} s): " + "; ".join(f"[{f['level']}] {dec(f['title'])}" for f in rep["findings"]))
            if rep["errors"]:
                print(f"  not checked: {rep['errors']}")
    st = out.get("doctor.startup")
    if st:
        print(f"Startup entries ({st['seconds']} s): {len(st['entries'])}")
        for e in st["entries"][:20]:
            print(f"  {e['name']}: {e['source']} enabled={e['enabled']} sig={e['signature'].get('status')} "
                  f"signer={e['signature'].get('signer')} protected={bool(e['protected_reason'])} "
                  f"problems={e['problems']}")
    sc = out.get("cleaner.scan")
    if sc:
        print(f"Cleaner scan ({sc['seconds']} s):")
        for c in sc["categories"]:
            if c["status"] != "not_found":
                print(f"  {c['id']}: {c['status']} {c['count']} files {c['bytes']} bytes running={c['running']} "
                      f"errors={len(c['errors'])}")
    ov = out.get("network.overview")
    if ov:
        a = ov["active"] or {}
        print(f"Network ({ov['seconds']} s): active={a.get('name')} kind={a.get('kind')} gw={a.get('gateway')} "
              f"dns={a.get('dns')} speed={a.get('speed_mbps')} wifi={a.get('wifi')} errors={ov['errors']}")
    st = out.get("network.stability")
    if st:
        rep = st["report"]
        print(f"Stability ({st['seconds']} s): " + "; ".join(f"[{f['level']}] {dec(f['title'])}" for f in rep["findings"]))
        for s in rep["data"].get("series", []):
            x = s["stats"]
            print(f"  {s['method']} {s['target']}: loss={x['loss_percent']} median={x['median']} jitter={x['jitter']} "
                  f"error={s['error'][:80]}")
    dn = out.get("network.dns")
    if dn:
        print(f"DNS ({dn['seconds']} s): " + "; ".join(f"{r['name']} {r['cached_ms']}/{r['uncached_ms']} ms "
                                                     f"{r['answered_percent']}%" for r in dn["results"]))
    bw = out.get("network.bandwidth")
    if bw:
        print(f"Bandwidth: down {bw['down_bps']:.0f} B/s up {bw['up_bps']:.0f} B/s, "
              f"{len(bw['processes'])} programs with connections")
    ms = out.get("mods.scan")
    if ms:
        print(f"Mod scan ({ms['seconds']} s): {ms['games']} library games, {len(ms['groups'])} with mods")
    so = out.get("security.overview")
    if so:
        st = so["state"]
        d = st.get("defender") or {}
        print(f"Security ({so['seconds']} s): health={st.get('health')} antivirus={st.get('antivirus')} "
              f"firewall={st.get('firewall')} defender mode={d.get('mode')} realtime={d.get('realtime')} "
              f"signature age={d.get('signature_age_days')} tamper={d.get('tamper')} "
              f"history={len(st.get('history') or [])} errors={st.get('errors')}")
        print("  " + "; ".join(f"[{f['level']}] {dec(f['title'])}" for f in so["report"]["findings"]))
        print(f"  Defender ready for scans: {out.get('security.ready')}")
    sp = out.get("security.processes")
    if sp:
        rep = sp["report"]
        procs = rep["data"]["processes"]
        print(f"Process review ({sp['seconds']} s): {len(procs)} processes, {rep['data']['signed_checked']} "
              f"signatures; " + "; ".join(f"[{f['level']}] {dec(f['title'])}" for f in rep["findings"]))
    sf = out.get("security.files")
    if sf:
        print(f"File check ({sf['seconds']} s): " + "; ".join(
            f"{Path(r['path']).name} {r['kind']} sig={r['signature'].get('status')} "
            f"signer={r['signature'].get('signer')} av={r['av'].get('status')} {str(r['av'].get('detail'))[:60]}"
            for r in sf["reports"]))
    se = out.get("security.eicar")
    if se:
        v = se["verdict"]
        print(f"EICAR ({se['seconds']} s): written={se['written']} present={se['present_before_scan']} "
              f"verdict={v.get('status')} threats={[t.get('name') for t in v.get('threats', [])]} "
              f"detail={str(v.get('detail'))[:100]} download check={se['download_check'].get('status')}")
    if "doctor.shortcuts" in out:
        print(f"Broken shortcuts: {len(out['doctor.shortcuts'])}")


if __name__ == "__main__":
    raise SystemExit(main())
