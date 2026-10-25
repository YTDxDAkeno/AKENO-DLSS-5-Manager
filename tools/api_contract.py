"""Print the API contract a running AKENO server publishes, and how it matches what the Windows app calls.

    python tools/api_contract.py [--api https://api.akeno.online]

Read-only: ``GET /api/v1/openapi.json`` and ``GET /api/v1/system/version`` over verified HTTPS, nothing else.
- every published path with its methods, endpoints the app uses marked ``app``;
- endpoints the app uses that the server does not publish;
- paths about releases, updates and downloads (the Release Manager), so the app's update path is built against
  the server's real contract and not against guessed addresses;
- ``--release-details``: the published schema of every ``/releases`` operation, and the public answer of each
  ``GET /releases…`` path without parameters (read-only, no credentials);
- ``--release-keys FILE``: the key ``GET /releases/signing-key`` names must be one of the build's release keys
  (exit code 1 otherwise), and a published ``/releases/stable`` must verify with the app's own parser;
- ``--schemas``: every published component schema about releases (portable packages, signatures, manifests) in
  full, with the API's own description, so serialization rules are read from the server and not guessed;
- ``--api-version v2`` (repeatable): the same listing for another API version's OpenAPI document, if published.

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


def resolve(doc: dict, node, depth: int = 0):
    """A schema with its ``$ref``s replaced (bounded), for printing."""
    if depth > 6:
        return node
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            target = doc
            for part in node["$ref"][2:].split("/"):
                target = target.get(part, {}) if isinstance(target, dict) else {}
            return resolve(doc, target, depth + 1)
        return {k: resolve(doc, v, depth + 1) for k, v in node.items() if k not in ("title",)}
    if isinstance(node, list):
        return [resolve(doc, v, depth + 1) for v in node]
    return node


SCHEMA_WORDS = re.compile(r"release|portable|package|manifest|signature|signed|update|artifact|channel|authenticode|"
                          r"rollout|sequence|file|hash|digest", re.I)


def dump_schemas(doc: dict) -> None:
    """The API's description and every release-related component schema, resolved, in full."""
    info = doc.get("info") or {}
    if info.get("description"):
        print("info.description:")
        for line in str(info["description"]).strip().splitlines():
            print(f"  {line}")
    schemas = (doc.get("components") or {}).get("schemas") or {}
    names = sorted(n for n in schemas if SCHEMA_WORDS.search(n))
    print(f"Release-related component schemas: {', '.join(names) or 'none'}")
    for name in names:
        print(f"=== schema {name}")
        print(json.dumps(resolve(doc, schemas[name]), indent=1, sort_keys=True))


def release_details(doc: dict, base: str, prefix: str = "/api/v1") -> None:
    for path in sorted(doc["paths"]):
        short = path[len(prefix):] if path.startswith(prefix) else path
        if not RELEASE_WORDS.search(short):
            continue
        for method, op in sorted(doc["paths"][path].items()):
            if not isinstance(op, dict):
                continue
            print(f"--- {method.upper()} {short}: {op.get('summary') or ''}")
            if op.get("description"):
                print(f"  description: {op['description'].strip()}")
            for prm in op.get("parameters") or []:
                prm = resolve(doc, prm)
                print(f"  parameter {prm.get('in')} {prm.get('name')}: {json.dumps(prm.get('schema'))}")
            if op.get("security") is not None or doc.get("security"):
                print(f"  security: {json.dumps(op.get('security', doc.get('security')))}")
            for code, resp in (op.get("responses") or {}).items():
                content = (resolve(doc, resp).get("content") or {})
                for ctype, body in content.items():
                    print(f"  {code} {ctype}: {json.dumps(body.get('schema'), sort_keys=True)}")
            if method.lower() == "get" and "{" not in short and not short.startswith("/admin"):
                # a path published with another version's prefix is called under that prefix
                url = f"{base[:-len(prefix)]}{path}" if path.startswith("/api/") else f"{base}{short}"
                status, body = get_json(url)
                text = json.dumps(body, indent=1, sort_keys=True) if body is not None else "(no JSON)"
                print(f"  live answer: HTTP {status}")
                for line in text.splitlines()[:400]:
                    print(f"    {line}")


def check_release_keys(base: str, keys_file: Path) -> bool:
    """The server's release signing key is built in, and a published stable release verifies like in the app."""
    from akeno import distribution
    from akeno.licensing import crypto
    from akeno.updater import manifest
    built = {k["kid"]: k["public_key"] for k in json.loads(keys_file.read_text(encoding="utf-8")).get("keys", [])}
    print(f"Release keys in the build: {', '.join(sorted(built)) or 'none'}")
    status, key = get_json(f"{base}/releases/signing-key")
    if not isinstance(key, dict):
        print(f"[FAIL] GET /releases/signing-key: HTTP {status}")
        return False
    kid, public = str(key.get("key_id") or ""), str(key.get("public_key") or "")
    if built.get(kid) != public or crypto.kid_for(public) != kid:
        print(f"[FAIL] the server signs releases with {kid}:{public}, which this build does not trust")
        return False
    print(f"[OK  ] the server's release signing key {kid} is built in")
    dist_file = keys_file.parent / "distribution.json"
    dist = distribution.from_dict(json.loads(dist_file.read_text(encoding="utf-8"))) if dist_file.exists() else None
    if dist is None or dist.source != "akeno-api":
        dist = distribution.from_dict({"update_source": "akeno-api", "release_api": base,
                                       "download_hosts": ["akeno.online"]})
    status, doc = get_json(f"{base}/releases/stable")
    if status == 404:
        print("[INFO] no stable release published yet (GET /releases/stable: 404)")
        return True
    try:
        release = manifest.parse_api(doc, {k: crypto.public_key(v) for k, v in built.items()}, dist)
    except manifest.ManifestError as exc:
        print(f"[FAIL] the published stable release does not verify in the app: {exc}")
        return False
    for f in release.files:
        print(f"[OK  ] stable {release.release} verified (key {release.kid}): {f.kind} {f.name}, {f.size} bytes, "
              f"sha256 {f.sha256}")
    d = getattr(release, "v2", None)
    if d is not None:
        print(f"[INFO] schema 2: sequence {d.sequence}, issued {d.issued_at}, expires {d.expires_at}, "
              f"install mode {d.install_mode}, signed fields: {', '.join(d.signed_fields)}")
        why = d.in_app_refusal("3.0.0")
        print(f"[INFO] AKENO 3.0.0 {'installs it after confirmation' if not why else 'shows it only: ' + why}")
    return True


def list_version(api: str, version: str, details: bool, schemas: bool) -> None:
    """Paths, release operations and schemas of ``{api}/api/{version}/openapi.json`` (read-only)."""
    prefix = f"/api/{version}"
    base = api + prefix
    print(f"===== API {version}")
    status, ver = get_json(f"{base}/system/version")
    print(f"GET {prefix}/system/version: HTTP {status}")
    if isinstance(ver, dict):
        for key in sorted(ver):
            print(f"  {key}: {ver[key]}")
    status, doc = get_json(f"{base}/openapi.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        print(f"GET {prefix}/openapi.json: HTTP {status}, not published")
        return
    info = doc.get("info") or {}
    print(f"GET {prefix}/openapi.json: {info.get('title', '?')} {info.get('version', '?')}, {len(doc['paths'])} paths")
    for path in sorted(doc["paths"]):
        methods = sorted(m.upper() for m in doc["paths"][path] if m.lower() in ("get", "post", "put", "patch", "delete"))
        print(f"  {','.join(methods):18} {path}")
    if details:
        release_details(doc, base, prefix)
    if schemas:
        dump_schemas(doc)
    print(f"===== end of API {version}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default="https://api.akeno.online")
    ap.add_argument("--release-details", action="store_true")
    ap.add_argument("--release-keys", type=Path, help="the build's release-keys.json")
    ap.add_argument("--schemas", action="store_true")
    ap.add_argument("--api-version", action="append", default=[], help="another API version to list, e.g. v2")
    args = ap.parse_args(argv)
    for other in args.api_version:
        list_version(args.api.rstrip("/"), other, args.release_details, args.schemas)
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
    if args.release_details:
        release_details(doc, base)
    if args.schemas:
        dump_schemas(doc)
    if args.release_keys and not check_release_keys(base, args.release_keys):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
