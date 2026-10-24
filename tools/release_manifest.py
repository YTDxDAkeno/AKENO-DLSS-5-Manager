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

    # for AKENO's own release server (files below one HTTPS base on akeno.online), valid for 30 days:
    python tools/release_manifest.py sign --channel stable --release 3.1.0 --tag v3.1.0 \
        --base-url https://downloads.akeno.online/releases/3.1.0 --notes-url https://akeno.online/download \
        --expires-days 30 --out channels/stable.json packaging/out/*.zip packaging/out/*.exe ...

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
    """The manifest kind of a release file. Only the program's portable package counts as "portable" (the updater
    installs it); other archives of a release (e.g. the community dependencies) are not part of the manifest."""
    lower = name.lower()
    if lower.endswith("-source.zip"):
        return "source"
    if lower.endswith(".zip"):
        return "portable" if "-portable-" in lower else ""
    return KINDS.get(Path(name).suffix.lower(), "")


def build(files: list[Path], channel: str, release: str, tag: str, repo: str = "", notes: str = "",
          base_url: str = "", notes_url: str = "", expires_days: int = 0) -> dict:
    """The manifest payload. Files are addressed on GitHub (``repo``) or below ``base_url`` (HTTPS on akeno.online,
    AKENO's own release server)."""
    from akeno import distribution
    from akeno.updater import manifest
    manifest.version_key(release)
    if channel not in manifest.CHANNELS:
        raise SystemExit(f"unknown channel {channel}")
    if bool(repo) == bool(base_url):
        raise SystemExit("give either --repo (GitHub) or --base-url (AKENO's release server)")
    base_url = base_url.rstrip("/")
    if base_url and not distribution._https_official(base_url):
        raise SystemExit("--base-url must be an https:// address on akeno.online")
    if notes_url and not (distribution._https_official(notes_url) or notes_url.startswith("https://github.com/")):
        raise SystemExit("--notes-url must be an https:// address on akeno.online or github.com")
    entries = []
    for f in files:
        kind = kind_of(f.name)
        if not kind:
            continue
        url = f"{base_url}/{f.name}" if base_url else f"https://github.com/{repo}/releases/download/{tag}/{f.name}"
        entries.append({"kind": kind, "name": f.name, "size": f.stat().st_size,
                        "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "url": url})
    if not any(e["kind"] == "portable" for e in entries):
        raise SystemExit("a release needs the portable package (the updater installs it)")
    payload = {"format": manifest.FORMAT, "version": 1, "app": manifest.APP, "channel": channel, "release": release,
               "tag": tag, "published": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "notes": notes[:4000],
               "notes_url": notes_url or (f"https://github.com/{repo}/releases/tag/{tag}" if repo else ""),
               "files": entries}
    if expires_days:
        payload["expires"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + expires_days * 86400))
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--private-out", type=Path, required=True)
    sg = sub.add_parser("sign")
    sg.add_argument("--channel", required=True)
    sg.add_argument("--release", required=True)
    sg.add_argument("--tag", required=True)
    sg.add_argument("--repo", default="", help="GitHub release repository OWNER/NAME")
    sg.add_argument("--base-url", default="", help="or: HTTPS base on akeno.online where the files are served")
    sg.add_argument("--notes-url", default="")
    sg.add_argument("--expires-days", type=int, default=0, help="the manifest stops being offered after N days")
    sg.add_argument("--notes", default="")
    sg.add_argument("--out", type=Path, required=True)
    sg.add_argument("files", nargs="+", type=Path)
    vf = sub.add_parser("verify")
    vf.add_argument("channel_file", type=Path)
    vf.add_argument("--keys", type=Path, default=ROOT / "akeno" / "data" / "release-keys.json")
    vf.add_argument("--channel", default=None)
    vf.add_argument("--release-base", default="", help="check file addresses against this akeno.online base")
    ds = sub.add_parser("distribution", help="write akeno/data/distribution.json (where updates come from)")
    ds.add_argument("--repo", default="", help="the public GitHub release repository OWNER/NAME")
    ds.add_argument("--source", default="github", choices=("github", "akeno"))
    ds.add_argument("--release-base", default="")
    ds.add_argument("--release-page", default="")
    ds.add_argument("--download-hosts", default="", help="comma-separated hosts on akeno.online")
    ds.add_argument("--output", type=Path, default=ROOT / "akeno" / "data" / "distribution.json")
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
        payload = build(args.files, args.channel, args.release, args.tag, args.repo, args.notes, args.base_url,
                        args.notes_url, args.expires_days)
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
        from akeno import distribution
        dist = distribution.from_dict({"update_source": "akeno", "release_base": args.release_base}) \
            if args.release_base else distribution.load()
        release = manifest.parse(doc, keys, channel, dist)
        print(f"verified: {release.channel} {release.release}, signed by {release.kid}")
        for f in release.files:
            print(f"  {f.kind:10} {f.sha256}  {f.name}")
        return 0
    if args.cmd == "distribution":
        from akeno import distribution
        doc = {}
        if args.repo:
            doc["release_repo"] = args.repo
        if args.source == "akeno":
            doc.update(update_source="akeno", release_base=args.release_base.rstrip("/"),
                       release_page=args.release_page.rstrip("/"),
                       download_hosts=[h.strip() for h in args.download_hosts.split(",") if h.strip()])
        dist = distribution.from_dict(doc)
        if dist.source != args.source or (args.repo and dist.release_repo != args.repo):
            raise SystemExit("refused: the release source must be HTTPS on akeno.online (or a valid GitHub repo)")
        dropped = set(doc.get("download_hosts", [])) - set(dist.hosts)
        if dropped:
            raise SystemExit(f"refused download hosts (not on akeno.online): {', '.join(sorted(dropped))}")
        args.output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"{args.output}: updates from {dist.label}, manifest {dist.channel_manifest('stable')}")
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
