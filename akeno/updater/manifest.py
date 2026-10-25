"""The release manifest: which version a channel (stable, beta) offers and the SHA-256 of every file.

``channels/<channel>.json`` in the public release repository::

    {"format": "akeno-release-channel", "token": "<JWS, EdDSA, typ akeno-release>"}

The token's payload::

    {"format": "akeno-release", "version": 1, "app": "akeno-gaming-suite", "channel": "stable",
     "release": "3.0.1", "tag": "v3.0.1", "published": "2026-11-02T10:00:00Z", "notes": "...",
     "notes_url": "https://github.com/<release repo>/releases/tag/v3.0.1",
     "files": [{"kind": "portable", "name": "...zip", "url": "...", "sha256": "...", "size": 123},
               {"kind": "installer", ...}, {"kind": "source", ...}, {"kind": "checksums", ...}]}

It is signed with the release key, outside any web server (CI secret or the maintainer's PC); the app holds only
the public half (``akeno/data/release-keys.json``, written at build time). A manifest that does not verify,
belongs to another app or channel, would go back to an older version, or is past its signed ``expires`` time
(optional: a frozen or replayed manifest stops being offered) is ignored. File and notes addresses must be HTTPS
on a host of the build's distribution (GitHub's release hosts, or ``akeno.online``). Release keys are separate from
license keys: neither can stand in for the other.

The AKENO Release Manager (``GET /api/v1/releases/stable`` on the production API) serves schema 2 since
2026-10-25 (``api_v2.py``: sequence, expiry, install mode and a portable package for the in-app updater). Schema 1
publishes one
release as an object with ``signed_payload`` and ``signature`` (``{"algorithm": "Ed25519", "key_id", "value"}``).
:func:`parse_api` trusts it only when the signature verifies over the exact ``signed_payload`` text with a built-in
release key, and then uses only the values inside ``signed_payload``: every one of them must also equal the field
of the same name outside it, and the security fields (product, platform, channel, version, download address,
SHA-256, size, ``unattended_install_allowed: false``) must be inside it. It describes one file (the installer the
release page offers), never a portable package, so such a release is shown and opened on the release page but not
installed by the app.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from akeno.distribution import HOSTS
from akeno.licensing import crypto
from akeno.paths import resource_dir

FORMAT = "akeno-release"
APP = "akeno-gaming-suite"
TYP = "akeno-release"
CHANNELS = ("stable", "beta")
KINDS = ("portable", "installer", "source", "checksums")
SHA = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.?(\d+))?$")
PRE = {"alpha": 0, "beta": 1, "rc": 2}


class ManifestError(ValueError):
    pass


def version_key(text: str) -> tuple:
    """3.0.0 < 3.0.1-beta.1 < 3.0.1-rc.2 < 3.0.1 (a final release sorts after its pre-releases)."""
    m = VERSION_RE.match(str(text).strip().lstrip("v"))
    if not m:
        raise ManifestError(f"not a version: {text}")
    major, minor, patch, pre, num = m.groups()
    return int(major), int(minor), int(patch), PRE[pre] if pre else 9, int(num or 0)


def newer(candidate: str, installed: str) -> bool:
    return version_key(candidate) > version_key(installed)


@dataclass
class File:
    kind: str
    name: str
    url: str
    sha256: str
    size: int


@dataclass
class Release:
    channel: str
    release: str
    tag: str
    published: str
    notes: str
    notes_url: str
    files: list = field(default_factory=list)
    kid: str = ""
    publisher_sha256: str = ""          # Release Manager: SHA-256 of the installer's Authenticode certificate
    v2: object = None                   # Release Manager schema 2: the signed details (api_v2.Details)

    def file(self, kind: str) -> File | None:
        return next((f for f in self.files if f.kind == kind), None)


def release_keys() -> dict:
    """The build's release verification keys (public)."""
    try:
        data = json.loads((resource_dir() / "release-keys.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for item in data.get("keys", []) if isinstance(data, dict) else []:
        try:
            if crypto.kid_for(item["public_key"]) == item["kid"]:
                out[item["kid"]] = crypto.public_key(item["public_key"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _https_github(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in HOSTS


def _timestamp(text: str) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse(channel_doc, keys: dict, channel: str, dist=None, now: float | None = None) -> Release:
    """``dist``: the build's distribution (trusted hosts); ``now``: the time for the ``expires`` check."""
    import time
    trusted = dist.trusted if dist is not None else _https_github
    if not keys:
        raise ManifestError("this build has no release verification key")
    if not isinstance(channel_doc, dict) or channel_doc.get("format") != "akeno-release-channel":
        raise ManifestError("not an AKENO release channel")
    try:
        kid, m = crypto.verify(str(channel_doc.get("token", "")), keys, TYP)
    except crypto.BadSignature as exc:
        raise ManifestError(f"the release manifest is not signed by AKENO ({exc})") from exc
    if m.get("format") != FORMAT or m.get("app") != APP or int(m.get("version", 0)) != 1:
        raise ManifestError("the manifest is not for this app")
    if m.get("channel") != channel:
        raise ManifestError("the manifest is for another channel")
    version_key(m.get("release", ""))
    if m.get("expires"):
        expires = _timestamp(m["expires"])
        if expires is None or expires < (time.time() if now is None else now):
            raise ManifestError("the release manifest has expired (it is not offered again until it is re-signed)")
    files = []
    for f in m.get("files") or []:
        if not isinstance(f, dict) or f.get("kind") not in KINDS:
            continue
        if not (isinstance(f.get("url"), str) and trusted(f["url"]) and isinstance(f.get("sha256"), str)
                and SHA.match(f["sha256"]) and isinstance(f.get("size"), int) and f["size"] > 0
                and isinstance(f.get("name"), str) and re.fullmatch(r"[A-Za-z0-9._-]{1,120}", f["name"])):
            raise ManifestError(f"a file entry of the manifest is not valid: {f.get('name')}")
        files.append(File(f["kind"], f["name"], f["url"], f["sha256"], f["size"]))
    text = {k: str(m.get(k) or "")[:limit] for k, limit in (("tag", 40), ("published", 30), ("notes", 4000),
                                                              ("notes_url", 300))}
    if text["notes_url"] and not trusted(text["notes_url"]):
        text["notes_url"] = ""
    return Release(channel, m["release"], text["tag"], text["published"], text["notes"], text["notes_url"], files,
                   kid)


API_SCHEMA = 1
API_PLATFORM = "windows-x64"
API_SIGNED = ("product", "platform", "channel", "version", "download_url", "sha256", "size_bytes",
              "unattended_install_allowed")
API_VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")


def _signature_bytes(value: str) -> bytes:
    import base64
    import binascii
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{128}", text):
        return bytes.fromhex(text)
    try:
        raw = base64.b64decode(text.replace("-", "+").replace("_", "/") + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManifestError("the release signature is not readable") from exc
    if len(raw) != 64:
        raise ManifestError("the release signature is not readable")
    return raw


def _signed_document(payload: str) -> dict:
    """The JSON object inside ``signed_payload`` (plain JSON text, or base64url of it)."""
    import base64
    import binascii
    for attempt in ("json", "base64"):
        try:
            text = payload if attempt == "json" else base64.urlsafe_b64decode(
                payload + "=" * (-len(payload) % 4)).decode("utf-8")
            doc = json.loads(text)
        except (ValueError, binascii.Error, UnicodeDecodeError):
            continue
        if isinstance(doc, dict):
            return doc
    raise ManifestError("the signed release information is not readable")


def parse_api(doc, keys: dict, dist=None, now: float | None = None) -> Release:
    """A release of the AKENO Release Manager; raises ManifestError. Schema 2 (``api_v2``) is what production
    serves; schema 1 (the module docstring) is still read, and is only ever shown, never installed."""
    from cryptography.exceptions import InvalidSignature
    trusted = dist.trusted if dist is not None else _https_github
    if not keys:
        raise ManifestError("this build has no release verification key")
    schema = doc.get("schema_version") if isinstance(doc, dict) else None
    if type(schema) is not int:                       # a JSON integer: never true (== 1) or 2.0 (== 2)
        raise ManifestError("not an AKENO release (unknown format)")
    if schema == 2:
        from akeno.updater import api_v2                  # the production format since 2026-10-25
        return api_v2.parse(doc, keys, trusted, now, getattr(dist, "download_origin", "") or api_v2.ORIGIN)
    if schema != API_SCHEMA:
        raise ManifestError("not an AKENO release (unknown format)")
    sig = doc.get("signature")
    payload = doc.get("signed_payload")
    if not isinstance(sig, dict) or sig.get("algorithm") != "Ed25519" or not isinstance(payload, str):
        raise ManifestError("the release is not signed by AKENO")
    key = keys.get(str(sig.get("key_id") or ""))
    if key is None:
        raise ManifestError("the release is signed with a key this build does not trust")
    try:
        key.verify(_signature_bytes(sig.get("value")), payload.encode("utf-8"))
    except InvalidSignature as exc:
        raise ManifestError("the release manifest is not signed by AKENO (signature does not match)") from exc
    m = _signed_document(payload)
    for name in API_SIGNED:
        if name not in m:
            raise ManifestError(f"the signed release information has no {name}")
    for name, value in doc.items():
        if name not in ("signed_payload", "signature") and name in m and m[name] != value:
            raise ManifestError(f"the release field {name} differs from the signed value")
    if m.get("product") != APP or m.get("platform") != API_PLATFORM:
        raise ManifestError("the manifest is not for this app")
    if m.get("channel") != "stable":
        raise ManifestError("the manifest is for another channel")
    if m.get("unattended_install_allowed") is not False:
        raise ManifestError("the release asks to be installed without confirmation: refused")
    version = str(m.get("version") or "")
    if not API_VERSION_RE.match(version):
        raise ManifestError(f"not a version: {version}")
    url, sha, size = m.get("download_url"), m.get("sha256"), m.get("size_bytes")
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) if isinstance(url, str) else ""
    if not (isinstance(url, str) and trusted(url) and isinstance(sha, str) and SHA.match(sha)
            and isinstance(size, int) and not isinstance(size, bool) and size > 0
            and re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name)):
        raise ManifestError(f"the download of the release is not valid: {url}")
    publisher = ""
    auth = m.get("authenticode")
    if isinstance(auth, dict) and auth.get("verified") is True and SHA.match(str(auth.get("publisher_certificate_sha256"))):
        publisher = auth["publisher_certificate_sha256"]
    published = str(m.get("published_at") or m.get("release_date") or "")[:30]
    notes = str(m.get("release_notes") or "")[:4000]
    return Release("stable", version, f"v{version}", published, notes, "",
                   [File("installer", name, url, sha, size)], str(sig["key_id"]), publisher)
