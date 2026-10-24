"""Release signing for AKENO's updater: keys, signed channel manifests, verification.

    # once, on the maintainer's own PC (never in CI logs, never in Git):
    python tools/release_manifest.py keygen --private-out release-signing.key
        -> store the file's content as the GitHub Actions secret AKENO_RELEASE_SIGNING_KEY of the private
           source repository, then delete the file; put the printed public key line into the repository variable
           AKENO_RELEASE_PUBLIC_KEYS

    # in CI, after the Windows build:
    python tools/release_manifest.py sign --channel stable --release 3.0.0 --tag v3.0.0 \
        --repo OWNER/AKENO-Releases --out channels/stable.json packaging/out/*.zip packaging/out/*.exe ...
        (the private key comes from the environment variable AKENO_RELEASE_SIGNING_KEY)

    # anyone:
    python tools/release_manifest.py verify channels/stable.json --keys akeno/data/release-keys.json

    # at build time, the public keys the app trusts:
    python tools/release_manifest.py embed --keys "$AKENO_RELEASE_PUBLIC_KEYS"
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KINDS = {".zip": "portable", ".exe": "installer", ".txt": "checksums"}


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_of(key: Ed25519PrivateKey) -> str:
    return b64e(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def kid_of(public: str) -> str:
    return hashlib.sha256(b64d(public)).hexdigest()[:16]


def load_private(text: str) -> Ed25519PrivateKey:
    raw = b64d(text.strip())
    if len(raw) != 32:
        raise SystemExit("AKENO_RELEASE_SIGNING_KEY must be the 32-byte Ed25519 seed (base64url) from keygen")
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign_payload(key: Ed25519PrivateKey, payload: dict) -> str:
    public = public_of(key)
    header = b64e(json.dumps({"alg": "EdDSA", "crv": "Ed25519", "kid": kid_of(public), "typ": "akeno-release"},
                             sort_keys=True, separators=(",", ":")).encode())
    body = b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{header}.{body}.{b64e(key.sign(f'{header}.{body}'.encode('ascii')))}"


def kind_of(name: str) -> str:
    if name.endswith("-source.zip"):
        return "source"
    return KINDS.get(Path(name).suffix.lower(), "")


def build(files: list[Path], channel: str, release: str, tag: str, repo: str, notes: str = "") -> dict:
    from akeno.updater import manifest
    manifest.version_key(release)
    if channel not in manifest.CHANNELS:
        raise SystemExit(f"unknown channel {channel}")
    entries = []
    for f in files:
        kind = kind_of(f.name)
        if not kind:
            continue
        entries.append({"kind": kind, "name": f.name, "size": f.stat().st_size,
                        "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                        "url": f"https://github.com/{repo}/releases/download/{tag}/{f.name}"})
    if not any(e["kind"] == "portable" for e in entries):
        raise SystemExit("a release needs the portable package (the updater installs it)")
    return {"format": manifest.FORMAT, "version": 1, "app": manifest.APP, "channel": channel, "release": release,
            "tag": tag, "published": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "notes": notes[:4000],
            "notes_url": f"https://github.com/{repo}/releases/tag/{tag}", "files": entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--private-out", type=Path, required=True)
    sg = sub.add_parser("sign")
    sg.add_argument("--channel", required=True)
    sg.add_argument("--release", required=True)
    sg.add_argument("--tag", required=True)
    sg.add_argument("--repo", required=True)
    sg.add_argument("--notes", default="")
    sg.add_argument("--out", type=Path, required=True)
    sg.add_argument("files", nargs="+", type=Path)
    vf = sub.add_parser("verify")
    vf.add_argument("channel_file", type=Path)
    vf.add_argument("--keys", type=Path, default=ROOT / "akeno" / "data" / "release-keys.json")
    vf.add_argument("--channel", default=None)
    em = sub.add_parser("embed")
    em.add_argument("--keys", required=True, help='"kid:public_key" pairs separated by commas, or JSON')
    em.add_argument("--output", type=Path, default=ROOT / "akeno" / "data" / "release-keys.json")
    em.add_argument("--require", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "keygen":
        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        fd = os.open(args.private_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(b64e(seed) + "\n")
        public = public_of(key)
        print(f"private key written to {args.private_out} (store it as the secret AKENO_RELEASE_SIGNING_KEY)")
        print(f"public key for AKENO_RELEASE_PUBLIC_KEYS: {kid_of(public)}:{public}")
        return 0
    if args.cmd == "sign":
        secret = os.environ.get("AKENO_RELEASE_SIGNING_KEY", "")
        if not secret:
            raise SystemExit("AKENO_RELEASE_SIGNING_KEY is not set")
        payload = build(args.files, args.channel, args.release, args.tag, args.repo, args.notes)
        token = sign_payload(load_private(secret), payload)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"format": "akeno-release-channel", "token": token,
                                        "release": payload["release"], "files": payload["files"]}, indent=1) + "\n",
                            encoding="utf-8")
        print(f"{args.out}: {payload['release']} ({len(payload['files'])} files), key {kid_of(public_of(load_private(secret)))}")
        return 0
    if args.cmd == "verify":
        from akeno.licensing import crypto
        from akeno.updater import manifest
        keys = {}
        for k in json.loads(args.keys.read_text(encoding="utf-8")).get("keys", []):
            keys[k["kid"]] = crypto.public_key(k["public_key"])
        doc = json.loads(args.channel_file.read_text(encoding="utf-8"))
        channel = args.channel or args.channel_file.stem
        release = manifest.parse(doc, keys, channel)
        print(f"verified: {release.channel} {release.release}, signed by {release.kid}")
        for f in release.files:
            print(f"  {f.kind:10} {f.sha256}  {f.name}")
        return 0
    if args.cmd == "embed":
        text = args.keys.strip()
        keys = []
        if text.startswith("{") or text.startswith("["):
            doc = json.loads(text)
            keys = doc.get("keys", []) if isinstance(doc, dict) else doc
        elif text:
            for pair in text.split(","):
                kid, _, public = pair.strip().partition(":")
                keys.append({"kid": kid, "public_key": public})
        for k in keys:
            if kid_of(k["public_key"]) != k["kid"]:
                raise SystemExit(f"release key {k['kid']} does not match its public key")
        if args.require and not keys:
            raise SystemExit("a release build needs AKENO_RELEASE_PUBLIC_KEYS, or its updater cannot verify updates")
        args.output.write_text(json.dumps({"keys": keys}, indent=2) + "\n", encoding="utf-8")
        print(f"{args.output}: {len(keys)} release key(s): {', '.join(k['kid'] for k in keys)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
