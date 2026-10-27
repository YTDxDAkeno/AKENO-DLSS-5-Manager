"""Release Manager schema 2: what a release must carry, and whether a signed release is accepted by the app.

    # the values of the built files (compare them with the signing request of the admin panel)
    python tools/release_v2.py describe packaging/out/AKENO-Gaming-Suite-3.0.0-portable-x64.zip \
        packaging/out/AKENO-Gaming-Suite-3.0.0-Setup-x64.exe

    # a signed release (the JSON GET /api/v1/releases/stable returns, or the admin panel's preview of it):
    # is it accepted by the app's own parser, and does it describe exactly these files?
    python tools/release_v2.py verify release.json --zip ...portable-x64.zip --exe ...Setup-x64.exe \
        [--keys akeno/data/release-keys.json] [--installed 3.0.0] [--previous-sequence 1]

    # before publishing: the admin panel's signed_payload (exactly the text that was signed) and the offline
    # signature, against the recorded values of the tested build instead of the files themselves
    python tools/release_v2.py verify --payload signed_payload.txt --signature <value> \
        --expect docs/release/3.1.0/release-manager-values.json --installed 3.0.0 --previous-sequence 1

Read-only and offline: no network, no signing, no private key. ``describe`` also unpacks the portable ZIP with the
app's own rules (``akeno/updater/staging.py``), so a package the updater would refuse is found before it is signed.
``content_sha256`` is computed exactly as the updater checks it (``akeno/updater/staging.py: content_sha256``).
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
        content = staging.relative_to_root(content, prefix)          # content_sha256 stays the ZIP's own
        if not (root / "AKENO.exe").is_file():
            raise SystemExit(f"{path.name}: no AKENO.exe in the package: the updater would refuse it")
        pe = staging.pe_count(root, content)
    return {"file": path.name, "size_bytes": path.stat().st_size, "sha256": sha256(path),
            "file_count": content["file_count"], "uncompressed_size_bytes": content["bytes"], "pe_files": pe,
            "top_folder": prefix or "(none)", "accepted_by_the_updater": True,
            "content_sha256": content["content_sha256"]}


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


def envelope(payload: Path, signature: str, key_id: str) -> dict:
    """The release as the API serves it, from the signed text and its signature: outer fields are optional and,
    when present, must equal the signed ones, so the signed text alone decides what the app accepts. The file is
    read as bytes and used unchanged (no newline added or removed: the signature covers exactly these bytes)."""
    text = payload.read_bytes().decode("utf-8")
    return {"schema_version": 2, "signed_payload": text,
            "signature": {"algorithm": "Ed25519", "key_id": key_id, "value": signature.strip()}}


def compare_expected(release, expect: dict) -> list[tuple[bool, str]]:
    """The signed release against the recorded ``describe`` values of the tested build (version, both files and
    the portable package's contents). Equal SHA-256 and size mean the same bytes as the tested files."""
    rows = []
    if expect.get("version"):
        rows.append((release.release == expect["version"], f"version {release.release}, expected {expect['version']}"))
    for kind in ("portable", "installer"):
        want, f = expect.get(kind), release.file(kind)
        if not want:
            continue
        if f is None:
            rows.append((False, f"the release has no {kind} file"))
            continue
        rows.append((f.size == want["size_bytes"] and f.sha256 == want["sha256"],
                     f"{kind}: signed {f.size} bytes, sha256 {f.sha256}; tested file {want['size_bytes']} bytes, "
                     f"sha256 {want['sha256']}"))
    d, want = release.v2, expect.get("portable")
    if want and d is not None and d.portable is not None:
        for name, signed, mine in (("file_count", d.portable.file_count, want["file_count"]),
                                   ("uncompressed_size_bytes", d.portable.uncompressed_size,
                                    want["uncompressed_size_bytes"]),
                                   ("content_sha256", d.portable.content_sha256, want["content_sha256"])):
            rows.append((signed == mine, f"{name}: signed {signed}, tested package {mine}"))
    return rows


def verify(args) -> int:
    if args.payload:
        if not args.signature:
            raise SystemExit("--payload needs --signature")
        doc = envelope(args.payload, args.signature, args.key_id)
    elif args.release:
        doc = json.loads(args.release.read_text(encoding="utf-8"))
    else:
        raise SystemExit("give the release JSON, or --payload with --signature")
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
    days = (d.expires_ts - time.time()) / 86400
    print(f"[INFO] valid for another {days:.1f} days (re-issue it with a new sequence before {d.expires_at})")
    if args.expect:
        for same, line in compare_expected(release, json.loads(args.expect.read_text(encoding="utf-8"))):
            print(f"[{'OK  ' if same else 'FAIL'}] {line}")
            ok &= same
    if args.previous_sequence is not None:
        # installed clients refuse a lower sequence, and the same sequence for a different release (anti-replay)
        newer = d.sequence > args.previous_sequence
        print(f"[{'OK  ' if newer else 'FAIL'}] sequence {d.sequence} after the published {args.previous_sequence}")
        ok &= newer
    refusal = d.in_app_refusal(args.installed)
    if refusal:
        print(f"[{'FAIL' if args.expect else 'INFO'}] AKENO {args.installed} shows it but does not install it "
              f"itself: {refusal}")
        ok &= not args.expect                 # a stable update for installed versions must install in the app
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
                                        d.portable.uncompressed_size),
                                       ("content_sha256", info["content_sha256"], d.portable.content_sha256)):
                same = mine == signed                 # the updater compares these exactly
                print(f"[{'OK  ' if same else 'FAIL'}] {name}: package {mine}, signed {signed}")
                ok &= same
            print(f"[INFO] pe_files: package {info['pe_files']}, release {d.portable.pe_files} "
                  f"({d.portable.verified_pe_files} code-signed, status {d.portable.authenticode.status})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    # A base64url signature can start with '-'. Bind that value to its option so argparse does not
    # treat it as another flag. Malformed/missing option values still receive the normal parser error.
    import re
    argv = list(sys.argv[1:] if argv is None else argv)
    for i in range(len(argv) - 2, -1, -1):
        if argv[i] == "--signature" and re.fullmatch(r"[-_A-Za-z0-9+/]{86}(?:==)?", argv[i + 1]):
            argv[i:i + 2] = ["--signature=" + argv[i + 1]]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe")
    d.add_argument("zip", type=Path)
    d.add_argument("exe", type=Path, nargs="?")
    v = sub.add_parser("verify")
    v.add_argument("release", type=Path, nargs="?")
    v.add_argument("--payload", type=Path, help="file with exactly the signed_payload text (instead of a release)")
    v.add_argument("--signature", help="the Ed25519 signature value of --payload (base64url, base64 or hex)")
    v.add_argument("--key-id", default="d6139235b220a4aa", help="key id of --signature")
    v.add_argument("--expect", type=Path, help="recorded describe values of the tested build (version, files)")
    v.add_argument("--zip", type=Path)
    v.add_argument("--exe", type=Path)
    v.add_argument("--keys", type=Path, default=ROOT / "akeno" / "data" / "release-keys.json")
    v.add_argument("--installed", default="3.0.0", help="the version that would update (default 3.0.0)")
    v.add_argument("--previous-sequence", type=int, help="sequence of the release the clients have seen (3.0.0: 1)")
    args = ap.parse_args(argv)
    return describe(args) if args.cmd == "describe" else verify(args)


if __name__ == "__main__":
    sys.exit(main())
