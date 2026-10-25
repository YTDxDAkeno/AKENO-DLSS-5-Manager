"""Release Manager schema 2: what a release must carry, and whether a signed release is accepted by the app.

    # the values of the built files (compare them with the signing request of the admin panel)
    python tools/release_v2.py describe packaging/out/AKENO-Gaming-Suite-3.0.0-portable-x64.zip \
        packaging/out/AKENO-Gaming-Suite-3.0.0-Setup-x64.exe

    # a signed release (the JSON GET /api/v1/releases/stable returns, or the admin panel's preview of it):
    # is it accepted by the app's own parser, and does it describe exactly these files?
    python tools/release_v2.py verify release.json --zip ...portable-x64.zip --exe ...Setup-x64.exe \
        [--keys akeno/data/release-keys.json] [--installed 3.0.0]

Read-only and offline: no network, no signing, no private key. ``describe`` also unpacks the portable ZIP with the
app's own rules (``akeno/updater/staging.py``), so a package the updater would refuse is found before it is signed.
``content_sha256`` is not computed here: how the server derives it is not documented in this repository (see
``docs/AKENO_3_0_UPDATER_V2.md``); compare it with the value the admin panel shows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno import distribution  # noqa: E402
from akeno.licensing import crypto  # noqa: E402
from akeno.updater import api_v2, manifest, staging  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def describe_zip(path: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        content = staging.extract(path, Path(tmp) / "x", api_v2.MAX_FILES, api_v2.MAX_UNPACKED)
        root = staging.program_root(Path(tmp) / "x")
        prefix = root.relative_to(Path(tmp) / "x").as_posix() if root != Path(tmp) / "x" else ""
        content = staging.relative_to_root(content, prefix)
        if not (root / "AKENO.exe").is_file():
            raise SystemExit(f"{path.name}: no AKENO.exe in the package: the updater would refuse it")
        pe = staging.pe_count(root, content)
    return {"file": path.name, "size_bytes": path.stat().st_size, "sha256": sha256(path),
            "file_count": content["file_count"], "uncompressed_size_bytes": content["bytes"], "pe_files": pe,
            "top_folder": prefix or "(none)", "accepted_by_the_updater": True,
            "content_sha256": "not computed here (server algorithm not documented in this repository)"}


def describe(args) -> int:
    out = {"portable": describe_zip(args.zip)}
    if args.exe:
        out["installer"] = {"file": args.exe.name, "size_bytes": args.exe.stat().st_size, "sha256": sha256(args.exe)}
    limits = []
    p = out["portable"]
    if p["size_bytes"] > api_v2.MAX_PORTABLE:
        limits.append(f"portable size {p['size_bytes']} > {api_v2.MAX_PORTABLE}")
    if p["file_count"] > api_v2.MAX_FILES:
        limits.append(f"file_count {p['file_count']} > {api_v2.MAX_FILES}")
    if p["pe_files"] > api_v2.MAX_PE:
        limits.append(f"pe_files {p['pe_files']} > {api_v2.MAX_PE}")
    if args.exe and out["installer"]["size_bytes"] > api_v2.MAX_INSTALLER:
        limits.append(f"installer size > {api_v2.MAX_INSTALLER}")
    out["within_the_release_manager_limits"] = not limits
    print(json.dumps(out, indent=1))
    for problem in limits:
        print(f"[FAIL] {problem}", file=sys.stderr)
    return 1 if limits else 0


def load_keys(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k["kid"]: crypto.public_key(k["public_key"]) for k in data.get("keys", [])
            if crypto.kid_for(k["public_key"]) == k["kid"]}


def verify(args) -> int:
    doc = json.loads(args.release.read_text(encoding="utf-8"))
    dist = distribution.load()
    if dist.source != "akeno-api":
        dist = distribution.from_dict({"update_source": "akeno-api", "release_api": "https://api.akeno.online/api/v1",
                                       "download_hosts": ["akeno.online"]})
    keys = load_keys(args.keys)
    ok = True
    try:
        release = manifest.parse_api(doc, keys, dist, time.time())
    except manifest.ManifestError as exc:
        print(f"[FAIL] the app refuses this release: {exc}")
        return 1
    d = release.v2
    if d is None:
        print("[FAIL] schema 1: shown by the app, never installed (production serves schema 2)")
        return 1
    print(f"[OK  ] signature valid (key {release.kid}); version {release.release}, sequence {d.sequence}, "
          f"issued {d.issued_at}, expires {d.expires_at}, install mode {d.install_mode}")
    print(f"       signed fields: {', '.join(d.signed_fields)}")
    refusal = d.in_app_refusal(args.installed)
    if refusal:
        print(f"[INFO] AKENO {args.installed} shows it but does not install it itself: {refusal}")
    else:
        print(f"[OK  ] AKENO {args.installed} can install it itself (after the user confirms)")
    for kind, path in (("portable", args.zip), ("installer", args.exe)):
        f = release.file(kind)
        if path is None:
            continue
        if f is None:
            print(f"[FAIL] the release has no {kind} file, but {path.name} was given")
            ok = False
            continue
        size, digest = path.stat().st_size, sha256(path)
        same = size == f.size and digest == f.sha256
        print(f"[{'OK  ' if same else 'FAIL'}] {kind} {path.name}: {size} bytes, sha256 {digest}"
              + ("" if same else f" — the release says {f.size} bytes, sha256 {f.sha256}"))
        ok &= same
        if kind == "portable" and same and d.portable is not None:
            info = describe_zip(path)
            for name, mine, signed in (("file_count", info["file_count"], d.portable.file_count),
                                       ("uncompressed_size_bytes", info["uncompressed_size_bytes"],
                                        d.portable.uncompressed_size)):
                fits = mine <= signed
                print(f"[{'OK  ' if fits else 'FAIL'}] {name}: package {mine}, signed limit {signed}")
                ok &= fits
            print(f"[INFO] pe_files: package {info['pe_files']}, release {d.portable.pe_files} "
                  f"({d.portable.verified_pe_files} code-signed, status {d.portable.authenticode.status})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe")
    d.add_argument("zip", type=Path)
    d.add_argument("exe", type=Path, nargs="?")
    v = sub.add_parser("verify")
    v.add_argument("release", type=Path)
    v.add_argument("--zip", type=Path)
    v.add_argument("--exe", type=Path)
    v.add_argument("--keys", type=Path, default=ROOT / "akeno" / "data" / "release-keys.json")
    v.add_argument("--installed", default="3.0.0", help="the version that would update (default 3.0.0)")
    args = ap.parse_args(argv)
    return describe(args) if args.cmd == "describe" else verify(args)


if __name__ == "__main__":
    sys.exit(main())
