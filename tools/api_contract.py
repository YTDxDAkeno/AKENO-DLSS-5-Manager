"""Print the API contract a running AKENO server publishes, and how it matches what the Windows app calls.

    python tools/api_contract.py [--api https://api.akeno.online]

Read-only: ``GET /api/v1/openapi.json`` and ``GET /api/v1/system/version`` over verified HTTPS, nothing else.
- every published path with its methods, endpoints the app uses marked ``app``;
- endpoints the app uses that the server does not publish;
- paths about releases, updates and downloads (the Release Manager), so the app's update path is built against
  the server's real contract and not against guessed addresses.

A server that does not publish its OpenAPI document (docs switched off) is reported as such; it is not an error.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from account_acceptance import ENDPOINTS  # noqa: E402

RELEASE_WORDS = re.compile(r"release|update|download|channel|manifest|client|version|artifact|build", re.I)


def get_json(url: str, timeout: float = 20) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "AKENO-contract-check", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except ValueError:
        return 200, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default="https://api.akeno.online")
    args = ap.parse_args(argv)
    base = args.api.rstrip("/") + "/api/v1"
    status, version = get_json(f"{base}/system/version")
    print(f"GET /system/version: HTTP {status}")
    if isinstance(version, dict):
        for key in sorted(version):
            print(f"  {key}: {version[key]}")
    status, doc = get_json(f"{base}/openapi.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        print(f"GET /openapi.json: HTTP {status}, not published: the server's contract cannot be read from outside")
        return 0
    info = doc.get("info") or {}
    print(f"GET /openapi.json: {info.get('title', '?')} {info.get('version', '?')}, {len(doc['paths'])} paths")
    used = {(m, p) for m, p, _, _ in ENDPOINTS}
    published = set()
    for path in sorted(doc["paths"]):
        # paths are published with or without the /api/v1 prefix, depending on how the routers are mounted
        short = path[len("/api/v1"):] if path.startswith("/api/v1") else path
        methods = sorted(m.upper() for m in doc["paths"][path] if m.lower() in
                         ("get", "post", "put", "patch", "delete"))
        for m in methods:
            published.add((m, short))
        marks = []
        if any((m, short) in used for m in methods):
            marks.append("app")
        if RELEASE_WORDS.search(short):
            marks.append("release/update")
        print(f"  {','.join(methods):18} {short}" + (f"   [{', '.join(marks)}]" if marks else ""))
    missing = sorted(used - published)
    print(f"Endpoints the app uses that are not published: {len(missing)}")
    for m, p in missing:
        print(f"  {m} {p}")
    release = sorted({p for _, p in published if RELEASE_WORDS.search(p)})
    print(f"Release/update related paths: {', '.join(release) or 'none published'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
