"""Run AKENO 3.1's system tools against the real machine (the Windows CI runner) and print what they report.

Read-only: nothing is cleaned, disabled, scanned with changes or repaired. The output is the evidence of how the
real Windows providers behave (event logs, performance counters, the storage stack, Defender, the network), as
opposed to the unit tests, which run the analyses on stand-in data.

    python tools/system_check.py [--json out.json] [--modules doctor,cleaner,network,mods,security]

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


MODULES = {"doctor": run_doctor, "cleaner": run_cleaner}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--modules", default=",".join(MODULES))
    args = ap.parse_args(argv)
    import tempfile
    home = tempfile.mkdtemp(prefix="akeno-system-check-")
    import os
    os.environ.setdefault("AKENO_HOME", home)
    out: dict = {"_home": home, "platform": sys.platform}
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
    if "doctor.shortcuts" in out:
        print(f"Broken shortcuts: {len(out['doctor.shortcuts'])}")


if __name__ == "__main__":
    raise SystemExit(main())
