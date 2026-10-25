"""Check what a packaged AKENO build trusts and talks to, before it is shipped.

    python tools/check_build_config.py dist/AKENO
    python tools/check_build_config.py packaging/out/AKENO-Gaming-Suite-3.0.0-portable-x64.zip

Reads the build's ``akeno/data`` (``licensing.json``, ``release-keys.json``, ``distribution.json``) from a
PyInstaller folder or a portable ZIP and fails when:
- the license API or website is not the expected HTTPS address (default ``https://api.akeno.online`` and
  ``https://akeno.online``);
- there is no license key or no release key, or a key id does not match its public key;
- a key is used both as license key and as release key (they must be separate key pairs);
- a key entry carries anything besides ``kid`` and ``public_key``, or a data file holds private key material;
- the release signing seed (environment variable ``AKENO_RELEASE_SIGNING_KEY``, when the CI job has it) appears
  anywhere in the build, as text or as raw bytes;
- a test-only key or test-signed release is in the build (the public contract fixture of the Release Manager,
  ``tests/fixtures/api_contract``): its key id among the trusted keys, its public key or the fixture's text anywhere.

Only public values are printed: addresses and key ids.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno import distribution  # noqa: E402

DATA_FILES = ("licensing.json", "release-keys.json", "distribution.json")
PRIVATE_MARKERS = (b"PRIVATE KEY-----", b"\"private_key\"", b"\"seed\"", b"\"signing_key\"", b"\"secret\"")
# keys and signatures that exist only for tests; a shipped build never trusts or carries them
TEST_ONLY_KEYS = {"4f0b6765af0b6fa0": "E-vFWNXW473DNvBjrDmgPXOLihAVEpjErKDUx9kZuuo"}   # contract fixture key
TEST_ONLY_MARKERS = tuple(v.encode() for v in TEST_ONLY_KEYS.values()) + (b"Never publish this fixture",)


def kid_of(public: str) -> str:
    raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    if len(raw) != 32:
        raise ValueError("an Ed25519 public key has 32 bytes")
    return hashlib.sha256(raw).hexdigest()[:16]


def build_files(path: Path):
    """Every file of the build as (name, bytes), one at a time (a build folder holds hundreds of MB)."""
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if not info.is_dir():
                    yield info.filename.replace("\\", "/"), z.read(info)
    elif path.is_dir():
        for f in sorted(path.rglob("*")):
            if f.is_file():
                yield f.relative_to(path).as_posix(), f.read_bytes()
    else:
        raise SystemExit(f"{path}: neither a build folder nor a ZIP")


def data_file(name: str) -> str:
    """``licensing.json`` for ``…/akeno/data/licensing.json``, else ""."""
    parts = name.split("/")
    if len(parts) >= 3 and parts[-3:-1] == ["akeno", "data"] and parts[-1] in DATA_FILES:
        return parts[-1]
    return ""


def keys_of(doc: dict, what: str, problems: list[str]) -> dict[str, str]:
    out = {}
    for k in doc.get("keys") or []:
        if not isinstance(k, dict) or set(k) != {"kid", "public_key"}:
            problems.append(f"{what}: a key entry must hold exactly kid and public_key")
            continue
        try:
            if kid_of(k["public_key"]) != k["kid"]:
                problems.append(f"{what}: key {k['kid']} does not match its public key")
                continue
        except ValueError as exc:
            problems.append(f"{what}: key {k.get('kid')}: {exc}")
            continue
        out[k["kid"]] = k["public_key"]
    if not out:
        problems.append(f"{what}: no key (the build could not verify anything)")
    return out


def check(path: Path, server: str, website: str, signing_seed: str = "") -> tuple[list[str], list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    seed = signing_seed.strip()
    needles = [seed.encode()] if seed else []
    if seed:
        raw = base64.urlsafe_b64decode(seed + "=" * (-len(seed) % 4))
        if len(raw) == 32:
            needles.append(raw)
    data: dict[str, bytes] = {}
    count = 0
    for name, content in build_files(path):
        count += 1
        if data_file(name):
            data[data_file(name)] = content
        if any(n in content for n in needles):
            problems.append(f"{name} contains the release signing key")
        if any(n in content for n in TEST_ONLY_MARKERS):
            problems.append(f"{name} contains a test-only key or test-signed release")
    for name in ("licensing.json", "release-keys.json"):
        if name not in data:
            problems.append(f"akeno/data/{name} is missing from the build")
    lic = json.loads(data.get("licensing.json", b"{}"))
    rel = json.loads(data.get("release-keys.json", b"{}"))
    dist = distribution.from_dict(json.loads(data.get("distribution.json", b"{}")))
    if (lic.get("server_url") or "").rstrip("/") != server:
        problems.append(f"license API is {lic.get('server_url') or 'empty'}, expected {server}")
    lic_website = (lic.get("website_url") or "").rstrip("/")
    if lic_website and lic_website != website:
        problems.append(f"website is {lic_website}, expected {website}")
    lic_keys = keys_of(lic, "license keys", problems)
    rel_keys = keys_of(rel, "release keys", problems)
    for kid in sorted((set(lic_keys) | set(rel_keys)) & set(TEST_ONLY_KEYS)):
        problems.append(f"the build trusts the test-only key {kid}")
    shared = set(lic_keys.values()) & set(rel_keys.values())
    if shared:
        problems.append("the same public key is used as license key and as release key")
    for name, content in data.items():
        for marker in PRIVATE_MARKERS:
            if marker in content:
                problems.append(f"akeno/data/{name} contains {marker.decode()!r}: private key material?")
    lines.append(f"License API: {lic.get('server_url') or 'none'}; website {lic_website or '(derived)'}")
    lines.append(f"License verification keys: {', '.join(sorted(lic_keys)) or 'NONE'}")
    lines.append(f"Release verification keys: {', '.join(sorted(rel_keys)) or 'NONE'}")
    lines.append(f"Updates: {dist.label}; manifest {dist.channel_manifest('stable')}")
    lines.append(f"Release signing key scan: {'done' if signing_seed else 'skipped (secret not available here)'}; "
                 f"{count} files")
    return lines, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("build", type=Path, help="dist/AKENO or the portable ZIP")
    ap.add_argument("--server", default="https://api.akeno.online")
    ap.add_argument("--website", default="https://akeno.online")
    args = ap.parse_args(argv)
    lines, problems = check(args.build, args.server.rstrip("/"), args.website.rstrip("/"),
                            os.environ.get("AKENO_RELEASE_SIGNING_KEY", ""))
    print(f"{args.build}:")
    for line in lines:
        print(f"  {line}")
    for p in problems:
        print(f"  FAIL {p}")
    print("  build configuration OK" if not problems else f"  {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
