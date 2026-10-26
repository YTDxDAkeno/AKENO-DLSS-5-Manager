"""The published stable release as an installed AKENO sees it: read-only against the production Release Manager.

    # any PC: what an installed 3.0.0 is offered, the signed values against the tested build, the real downloads
    python tools/live_release_check.py --expect docs/release/3.1.0/release-manager-values.json --download

    # a disposable Windows machine only: install the published 3.0.0 and update it from production through the real
    # AKENO.exe files, the way KONTO -> Updates -> "Install now" does it
    python tools/live_release_check.py --expect docs/release/3.1.0/release-manager-values.json \\
        --upgrade-from https://akeno.online/downloads/3.0.0/AKENO-Setup.exe --upgrade-from-sha256 <sha> --work <dir>

Read-only for production: ``GET /api/v1/releases/stable``, ``GET /api/v1/releases/<published version>`` and the
release downloads, with the app's own code (``akeno.updater``: parser, signature check with the built-in release key,
anti-replay, download with SHA-256, unpacking against the signed contents). No account, no license, no login,
nothing is sent. Until the expected version is the live stable release, it reports what is live and stops (exit
code 0; ``--require`` makes that a failure).

The upgrade differs from ``update_e2e.py`` in one way: nothing is faked. No hosts file, no test certificate, no test
key: the installed 3.0.0 keeps the production release key, and the release, its signature and the files come from
akeno.online. Only the confirmation click is not automated: the updater is started the way the Updates page starts it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PUBLISHED = {"version": "3.0.0", "sequence": 1,      # the release installed clients have seen (2026-10-26)
             "installer_sha256": "fe919b720e7db4a7e3e8fce97555bb77cde23851d205de3f039d847b1e0dd20f",
             "installer_size": 157148888,
             "portable_sha256": "d4c5701988f9f21841e1bd8a2c059c32e02b6e1de3f50180849837b7eb95e9f9",
             "portable_size": 222769069}


def live_release(dist):
    """The live stable release, verified like the app verifies it."""
    from akeno.updater import manifest, service
    return manifest.parse_api(service.fetch_json(dist.channel_manifest("stable"), dist.trusted),
                              manifest.release_keys(), dist)


def published_unchanged(report, dist) -> None:
    """The release installed clients have seen is still published as it was (nothing overwritten)."""
    from akeno.updater import api_v2, service
    base = dist.channel_manifest("stable").rsplit("/", 1)[0]
    try:
        doc = service.fetch_json(f"{base}/{PUBLISHED['version']}", dist.trusted)
        m = api_v2.signed_document(doc["signed_payload"])
    except service.NoRelease:
        report.add(None, f"release {PUBLISHED['version']}: the API serves no per-version page (404)")
        return
    except Exception as exc:
        report.add(False, f"release {PUBLISHED['version']} is still published", f"{type(exc).__name__}: {exc}")
        return
    portable = m.get("portable") or {}
    same = (m.get("version") == PUBLISHED["version"] and m.get("sequence") == PUBLISHED["sequence"]
            and m.get("sha256") == PUBLISHED["installer_sha256"] and m.get("size_bytes") == PUBLISHED["installer_size"]
            and portable.get("sha256") == PUBLISHED["portable_sha256"]
            and portable.get("size_bytes") == PUBLISHED["portable_size"])
    report.add(same, f"release {PUBLISHED['version']} unchanged (sequence, installer and portable SHA-256 and size)",
               f"sequence {m.get('sequence')}, installer {m.get('sha256')}, portable {portable.get('sha256')}")


def client_checks(report, args, dist, release, expect: dict) -> None:
    """What installed clients of ``args.installed`` see: a first check, and one after seeing the published release."""
    import release_v2
    from akeno.updater import service
    d = release.v2
    report.add(d is not None, f"live stable {release.release} is schema 2, signed with {release.kid}",
               f"sequence {d.sequence if d else '?'}, issued {d.issued_at if d else '?'}, "
               f"expires {d.expires_at if d else '?'}, install mode {d.install_mode if d else '?'}")
    if d is None:
        return
    report.add(None, "validity left", f"{(d.expires_ts - time.time()) / 86400:.1f} days (re-issue before "
               f"{d.expires_at} with a new sequence)")
    for same, line in release_v2.compare_expected(release, expect):
        report.add(same, "signed value = tested build (CI run %s)" % expect.get("ci_run", "?"), line)
    with tempfile.TemporaryDirectory(prefix="akeno-live-") as tmp:
        seen = {"highest_seen_stable": PUBLISHED["version"],
                "sequence_seen": {"sequence": PUBLISHED["sequence"], "fingerprint": ["published", "", ""]},
                "newest_issued": 0}
        for name, state in (("first check after installing", {}),
                            (f"after having seen {PUBLISHED['version']} (sequence {PUBLISHED['sequence']})", seen)):
            folder = Path(tmp) / str(len(name))
            folder.mkdir()
            (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
            svc = service.UpdateService(None, dist=dist, version=args.installed, folder=folder,
                                        fetch=lambda url: service.fetch_json(url, dist.trusted))
            try:
                offered = svc.check()
            except Exception as exc:
                report.add(False, f"AKENO {args.installed}, {name}: KONTO → Updates refuses the release", str(exc))
                continue
            report.add(offered is not None and offered.release == release.release,
                       f"AKENO {args.installed}, {name}: KONTO → Updates offers {release.release}",
                       offered.release if offered else "no update offered")
        why = d.in_app_refusal(args.installed)
        report.add(not why, f"AKENO {args.installed} installs it itself after the user confirms (Install now)",
                   why or "portable-directory-swap with a portable package")
        if args.download:
            download_and_unpack(report, Path(tmp), dist, release)


def download_and_unpack(report, tmp: Path, dist, release) -> None:
    """Both files from the production download, checked against the signed SHA-256 and size; the portable package
    unpacked against its signed file count, unpacked size and content_sha256 (the updater's own staging)."""
    from akeno.updater import service
    os.environ.setdefault("AKENO_HOME", str(tmp / "home"))
    svc = service.UpdateService(None, dist=dist, version="0.0.0", folder=tmp / "updates")
    for kind in ("portable", "installer"):
        f = release.file(kind)
        start = time.monotonic()
        try:
            path = svc.download(release) if kind == "portable" else Path(svc.downloads().fetch(
                f.url, f.sha256, f.name, f.size).path)
        except Exception as exc:
            report.add(False, f"{kind} download {f.url}", f"{type(exc).__name__}: {exc}")
            continue
        report.add(path.stat().st_size == f.size, f"{kind} downloaded from {f.url}: signed SHA-256 and size",
                   f"{f.size} bytes in {time.monotonic() - start:.0f} s")
        if kind == "portable":
            try:
                root = svc.stage(path, release)
                report.add((root / "AKENO.exe").is_file(), "portable package unpacked by the updater: file count, "
                           "unpacked size and content_sha256 equal the signed values", str(root.name))
            except Exception as exc:
                report.add(False, "portable package unpacked by the updater", f"{type(exc).__name__}: {exc}")
        path.unlink(missing_ok=True)


def upgrade(report, args, expect: dict) -> None:
    """Install the published version on this (disposable) Windows machine and update it from production."""
    import update_e2e
    from akeno.security.downloads import DownloadManager

    class LiveUpgrade(update_e2e.E2E):
        def service(self):
            from akeno.updater import service
            service.install_dir = lambda: self.target          # the orchestrator plays the installed AKENO
            return service.UpdateService(None, version=self.installed_version, folder=self.folder)

        def run(self) -> None:
            r = self.report
            seeded = update_e2e.seed_user_data()
            if not self.install(test_key=False):
                return
            keys = json.loads((self.target / "_internal" / "akeno" / "data" / "release-keys.json").read_text("utf-8"))
            r.add([k["kid"] for k in keys.get("keys", [])] == ["d6139235b220a4aa"],
                  f"the installed {self.rc} trusts only the production release key", str(keys))
            update_e2e.user_data_intact(r, seeded, "after installation")
            outcome, ctx = self.apply(self.next_version)       # live check, download, verify, stage; real updater
            if self.updated(outcome, ctx, self.next_version, seeded):
                self.installed_version = self.next_version
                self.manual_rollback(seeded)
            self.uninstall(seeded)

    args.work.mkdir(parents=True, exist_ok=True)
    name = args.upgrade_from.rsplit("/", 1)[-1]
    setup = DownloadManager(["akeno.online"], cache=args.work / "published").fetch(
        args.upgrade_from, args.upgrade_from_sha256, name)
    report.add(True, f"published {args.installed} installer downloaded and checked against its signed SHA-256",
               f"{setup.size} bytes")
    e2e_args = argparse.Namespace(installer=Path(setup.path), installed_version=args.installed,
                                  next_version=expect["version"], work=args.work, next=None)
    LiveUpgrade(e2e_args, report).run()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--expect", type=Path, required=True, help="recorded release values of the tested build")
    ap.add_argument("--installed", default=PUBLISHED["version"], help="the installed version (default 3.0.0)")
    ap.add_argument("--download", action="store_true", help="download and unpack both files of the live release")
    ap.add_argument("--upgrade-from", help="installer URL of the installed version (disposable Windows machine)")
    ap.add_argument("--upgrade-from-sha256", help="its signed SHA-256")
    ap.add_argument("--work", type=Path, help="working folder of the upgrade")
    ap.add_argument("--require", action="store_true", help="fail while the expected version is not live yet")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import update_e2e
    from akeno import distribution
    report = update_e2e.Report()
    expect = json.loads(args.expect.read_text(encoding="utf-8"))
    dist = distribution.load()
    try:
        release = live_release(dist)
    except Exception as exc:
        report.add(False, "the live stable release verifies in the app", f"{type(exc).__name__}: {exc}")
        release = None
    published_unchanged(report, dist)
    if release is not None and release.release != expect["version"]:
        report.add(False if args.require else None, f"{expect['version']} is not the live stable release yet",
                   f"live: {release.release}")
    elif release is not None:
        try:
            if args.upgrade_from:
                if sys.platform != "win32" or not (args.upgrade_from_sha256 and args.work):
                    raise SystemExit("--upgrade-from runs on a disposable Windows machine, with --upgrade-from-sha256 "
                                     "and --work")
                upgrade(report, args, expect)
            else:
                client_checks(report, args, dist, release, expect)
        except Exception as exc:                # a crash of the check itself is a failed check, with its reason
            import traceback
            traceback.print_exc()
            report.add(False, "the check itself stopped", f"{type(exc).__name__}: {exc}")
    text = report.markdown().replace("### Windows update end-to-end test", "### Live stable release (production)")
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
