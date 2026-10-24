"""Write akeno/data/licensing.json: the license server address and the public signing keys the app trusts.

    python tools/configure-licensing.py --server-url https://licenses.example --fetch
    python tools/configure-licensing.py --server-url https://licenses.example --keys '{"keys": [...]}'

``--fetch`` reads ``/api/v1/system/keys`` over HTTPS and keeps only keys whose id matches their public key. The
Windows build in CI runs this with the repository variables AKENO_LICENSE_SERVER_URL and AKENO_LICENSE_PUBLIC_KEYS.
Public keys are not secret; the file may be committed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "akeno" / "data" / "licensing.json"


def kid_for(public_key: str) -> str:
    raw = base64.urlsafe_b64decode(public_key + "=" * (-len(public_key) % 4))
    if len(raw) != 32:
        raise ValueError("an Ed25519 public key has 32 bytes")
    return hashlib.sha256(raw).hexdigest()[:16]


def clean(keys: list) -> list[dict]:
    out = []
    for k in keys:
        if not isinstance(k, dict) or not k.get("kid") or not k.get("public_key"):
            continue
        if kid_for(k["public_key"]) != k["kid"]:
            raise SystemExit(f"key {k['kid']} does not match its public key")
        out.append({"kid": k["kid"], "public_key": k["public_key"]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server-url", default="", help="https:// address of the license server (empty: none)")
    ap.add_argument("--keys", default="", help='JSON: {"keys": [{"kid", "public_key"}]} or a list of keys')
    ap.add_argument("--fetch", action="store_true", help="read the keys from the server's /api/v1/system/keys")
    ap.add_argument("--output", type=Path, default=TARGET)
    ap.add_argument("--require", action="store_true",
                    help="fail without a server address and at least one key (release builds)")
    args = ap.parse_args(argv)
    url = args.server_url.strip().rstrip("/")
    if url and not url.startswith("https://"):
        raise SystemExit("the server address must start with https://")
    keys: list = []
    if args.keys.strip():
        doc = json.loads(args.keys)
        keys = doc.get("keys", []) if isinstance(doc, dict) else doc
    elif args.fetch:
        if not url:
            raise SystemExit("--fetch needs --server-url")
        with urllib.request.urlopen(f"{url}/api/v1/system/keys", timeout=20) as resp:
            keys = json.load(resp).get("keys", [])
    keys = clean(keys)
    if args.require and not (url and keys):
        raise SystemExit("a release build needs AKENO_LICENSE_SERVER_URL and AKENO_LICENSE_PUBLIC_KEYS: without them "
                         "it could not verify any license")
    args.output.write_text(json.dumps({"server_url": url, "keys": keys}, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: server {url or '(none)'}, {len(keys)} key(s): {', '.join(k['kid'] for k in keys)}")
    print("compare the key ids with 'akeno-server keys list' on the license server")
    return 0


if __name__ == "__main__":
    sys.exit(main())
