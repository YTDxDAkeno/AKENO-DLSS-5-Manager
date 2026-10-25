"""Release Manager schema 2 (updater protocol 2): what ``GET /api/v1/releases/stable`` returns on production.

The contract is the Release Manager's own validator (``release_protocol.validate`` and ``verify`` of the
production backend, see docs/AKENO_3_0_FINAL_CLIENT_ALIGNMENT.md) and its OpenAPI document. :func:`parse` accepts
exactly what that validator accepts, and refuses everything it refuses:

* The release counts only when ``signature`` (exactly ``algorithm`` ``Ed25519``, ``key_id`` of a release key built
  into this program, ``value`` 64 bytes as unpadded base64url) verifies over the **exact UTF-8 bytes of
  ``signed_payload``** (at most 64 KiB), unchanged. Nothing is re-serialised or decoded first.
* ``signed_payload`` is one JSON object without duplicate keys or non-finite numbers, with exactly the 20 release
  fields (and optionally ``portable`` with exactly its 9 fields). Only its values are used. Every field outside
  it must be one of its fields, with the same value.
* The download addresses are exactly ``https://akeno.online/downloads/<version>/AKENO-Setup.exe`` and
  ``…/AKENO-Portable.zip``; times are UTC; the validity is one hour to thirty days, issued at most five minutes
  ahead and not yet expired; a portable package means ``portable-directory-swap``, none means
  ``manual-installer`` without unattended installation; the code signature fields agree with each other.
* Sequence and version rules against replays are applied by the service, which remembers what it has seen
  (``service.UpdateService.check``).

The app never installs anything without the user's confirmation, whatever ``unattended_install_allowed`` says.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from akeno.updater.manifest import File, ManifestError, Release

SCHEMA = 2
PROTOCOL = 2
PRODUCT = "akeno-gaming-suite"
PLATFORM = "windows-x64"
CHANNEL = "stable"
MODES = ("manual-installer", "portable-directory-swap")
AUTHENTICODE = ("verified", "not_signed", "present_unverified", "invalid", "mixed")
MAX_SEQUENCE = 2 ** 53 - 1
MAX_INSTALLER = 512 * 2 ** 20               # PortablePackage / ReleaseEnvelope size_bytes maximum
MAX_PORTABLE = 512 * 2 ** 20
MAX_UNPACKED = 2 * 2 ** 30                  # uncompressed_size_bytes maximum
MAX_FILES = 4096                            # file_count maximum
MAX_PE = 512
ORIGIN = "https://akeno.online"                # where the Release Manager's downloads are (``origin`` of verify)
INSTALLER_NAME = "AKENO-Setup.exe"              # {origin}/downloads/{version}/<name>, nothing else
PORTABLE_NAME = "AKENO-Portable.zip"
MAX_PAYLOAD = 65536                             # signed_payload, UTF-8 bytes
# always used with fullmatch: "$" would also accept a trailing newline
SHA = re.compile(r"[a-f0-9]{64}")
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
REQUEST_ID = re.compile(r"[a-f0-9]{32}")
B64URL = re.compile(r"[A-Za-z0-9_-]+")
# every field the production ReleaseEnvelope requires (OpenAPI, additionalProperties: false); "portable" is optional
SIGNED = ("schema_version", "updater_protocol", "product", "platform", "channel", "version", "sequence",
          "issued_at", "expires_at", "download_url", "sha256", "size_bytes", "unattended_install_allowed",
          "install_mode", "request_id", "release_date", "release_notes", "published_at", "minimum_supported_version",
          "authenticode")
ALLOWED = frozenset(SIGNED) | {"portable"}
ENVELOPE = ALLOWED | {"signed_payload", "signature"}
PORTABLE_FIELDS = frozenset({"download_url", "size_bytes", "sha256", "authenticode", "pe_files", "verified_pe_files",
                             "file_count", "uncompressed_size_bytes", "content_sha256"})
AUTHENTICODE_FIELDS = frozenset({"verified", "status", "publisher_certificate_sha256"})
SIGNATURE_FIELDS = frozenset({"algorithm", "key_id", "value"})
KEY_ID = re.compile(r"[a-f0-9]{16}")
FUTURE_SKEW = 5 * 60                         # issued_at at most five minutes ahead of now
MIN_VALIDITY = 3600                          # expires_at - issued_at: one hour …
MAX_VALIDITY = 30 * 86400                    # … to thirty days


class ReleaseError(ManifestError):
    """The release information is refused (the message says why, for the Updates page and the log)."""


@dataclass(frozen=True)
class Authenticode:
    verified: bool
    status: str                                     # one of AUTHENTICODE
    publisher_sha256: str = ""


@dataclass(frozen=True)
class Portable:
    url: str
    name: str
    sha256: str
    size: int
    content_sha256: str
    file_count: int
    uncompressed_size: int
    pe_files: int
    verified_pe_files: int
    authenticode: Authenticode


@dataclass(frozen=True)
class Details:
    """The signed facts of one schema-2 release."""
    sequence: int
    issued_at: str
    expires_at: str
    issued_ts: float
    expires_ts: float
    install_mode: str
    updater_protocol: int
    unattended_install_allowed: bool
    minimum_supported_version: str | None
    request_id: str
    installer_authenticode: Authenticode
    portable: Portable | None
    signed_fields: tuple = field(default_factory=tuple)

    def in_app_refusal(self, installed: str) -> str:
        """Why this release cannot be installed by the app itself ("" when it can). It can still be shown."""
        from akeno.i18n import msg
        if self.install_mode != "portable-directory-swap" or self.portable is None:
            return msg("This release is offered as an installer on the download page (AKENO installs only portable "
                       "packages itself, so it can roll back).")
        if self.portable.authenticode.status == "invalid":
            return msg("The release's code signature is invalid, so AKENO does not install it.")
        if self.minimum_supported_version and version_key(installed) < version_key(self.minimum_supported_version):
            return msg("AKENO {v} is too old for an update from here. Install the new version with the installer "
                       "from the download page.", v=installed)
        return ""


def version_key(text: str) -> tuple[int, int, int]:
    m = VERSION.fullmatch(str(text or ""))
    if not m:
        # an installed pre-release (3.0.1-beta.1) compares by its numbers
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(text or ""))
        if not m:
            raise ReleaseError(f"not a version: {text}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ReleaseError(f"the signed release information names {key} twice")
        out[key] = value
    return out


def _no_constants(name):
    raise ReleaseError(f"the signed release information contains {name}")


def _load(text: str) -> object:
    return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)


def signed_document(payload: str) -> dict:
    """The JSON object ``signed_payload`` is (JSON text; the Release Manager never encodes it otherwise)."""
    try:
        doc = _load(payload)
    except ReleaseError:
        raise
    except ValueError as exc:
        raise ReleaseError("the signed release information is not readable") from exc
    if not isinstance(doc, dict):
        raise ReleaseError("the signed release information is not readable")
    return doc


def b64url(value, length: int) -> bytes:
    """Unpadded base64url of exactly ``length`` bytes, in its one canonical spelling (``decode`` of the backend)."""
    if not isinstance(value, str) or not B64URL.fullmatch(value):
        raise ReleaseError("the release signature is not readable")
    try:
        raw = base64.b64decode(value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReleaseError("the release signature is not readable") from exc
    if len(raw) != length or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ReleaseError("the release signature is not readable")
    return raw


def signature_bytes(value) -> bytes:
    """``signature.value``: the 64 signature bytes as unpadded base64url (hex or padded base64 are refused)."""
    return b64url(value, 64)


def key_id(key) -> str:
    """The key id of an Ed25519 public key: the first 16 hex digits of the SHA-256 of its 32 raw bytes."""
    from cryptography.hazmat.primitives import serialization
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def _is_int(value) -> bool:
    """A JSON integer: not a float such as 2.0, not a boolean (Python's bool is an int)."""
    return type(value) is int


def _int(m: dict, name: str, low: int, high: int) -> int:
    value = m.get(name)
    if not _is_int(value) or not low <= value <= high:
        raise ReleaseError(f"the signed {name} is not valid")
    return value


def _only(value: dict, allowed: frozenset, where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReleaseError(f"the {where} has fields the release format does not allow: {', '.join(unknown)}")


def _time(m: dict, name: str) -> float:
    text = m.get(name)
    if not isinstance(text, str) or len(text) > 40:
        raise ReleaseError(f"the signed {name} is not a time")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(f"the signed {name} is not a time") from exc
    if dt.tzinfo is None:
        raise ReleaseError(f"the signed {name} has no time zone")
    if dt.utcoffset() != timedelta(0):
        raise ReleaseError(f"the signed {name} is not in UTC")
    ts = dt.timestamp()
    if not math.isfinite(ts):
        raise ReleaseError(f"the signed {name} is not a time")
    return ts


def _authenticode(value, where: str) -> Authenticode:
    """Exactly ``verified``, ``status`` and ``publisher_certificate_sha256``, agreeing with each other: verified
    means status ``verified`` and a certificate pin; anything else has no pin and is never status ``verified``."""
    if not isinstance(value, dict) or set(value) != AUTHENTICODE_FIELDS or type(value["verified"]) is not bool \
            or value["status"] not in AUTHENTICODE:
        raise ReleaseError(f"the code signature information of the {where} is not valid")
    publisher = value["publisher_certificate_sha256"]
    if value["verified"]:
        if value["status"] != "verified" or not isinstance(publisher, str) or not SHA.fullmatch(publisher):
            raise ReleaseError(f"the code signature information of the {where} contradicts itself")
    elif value["status"] == "verified" or publisher is not None:
        raise ReleaseError(f"the code signature information of the {where} contradicts itself")
    return Authenticode(value["verified"], value["status"], publisher or "")


def _download(url, trusted, expected: str, what: str) -> str:
    """The one address the Release Manager serves this file at (and a host of this build's distribution)."""
    if not isinstance(url, str) or url != expected or not trusted(url):
        raise ReleaseError(f"the {what} address is not the official AKENO download: {url}")
    return urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]


def _sha(value, what: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ReleaseError(f"the {what} checksum is not valid")
    return value


def _portable(value, trusted, base: str) -> Portable:
    if not isinstance(value, dict):
        raise ReleaseError("the portable package information is not valid")
    _only(value, PORTABLE_FIELDS, "portable package information")
    missing = sorted(PORTABLE_FIELDS - set(value))
    if missing:
        raise ReleaseError(f"the portable package information has no {', '.join(missing)}")
    name = _download(value["download_url"], trusted, base + PORTABLE_NAME, "portable package")
    size = _int(value, "size_bytes", 1, MAX_PORTABLE)
    sha = _sha(value["sha256"], "portable package")
    content = _sha(value["content_sha256"], "portable package content")
    pe = _int(value, "pe_files", 1, MAX_PE)
    verified = _int(value, "verified_pe_files", 0, pe)
    files = _int(value, "file_count", 1, MAX_FILES)
    unpacked = _int(value, "uncompressed_size_bytes", 1, MAX_UNPACKED)
    auth = _authenticode(value["authenticode"], "portable package")
    if auth.verified and verified != pe:
        raise ReleaseError("the portable package is called code-signed, but not all of its programs are")
    return Portable(value["download_url"], name, sha, size, content, files, unpacked, pe, verified, auth)


def parse(doc, keys: dict, trusted, now: float | None = None, origin: str = ORIGIN):
    """A schema-2 release (``Release`` with ``v2`` details), or ReleaseError.

    ``keys``: the build's release keys (``{kid: Ed25519PublicKey}``); ``trusted(url)``: the build's download hosts;
    ``now``: the time for the expiry check (the caller passes a clock that cannot go back, see the service);
    ``origin``: where the Release Manager's downloads are (``https://akeno.online``)."""
    from cryptography.exceptions import InvalidSignature
    if not keys:
        raise ReleaseError("this build has no release verification key")
    if not isinstance(doc, dict) or not _is_int(doc.get("schema_version")) or doc["schema_version"] != SCHEMA:
        raise ReleaseError("not an AKENO release (unknown format)")
    sig, payload = doc.get("signature"), doc.get("signed_payload")
    if not isinstance(payload, str) or not payload or len(payload.encode("utf-8")) > MAX_PAYLOAD \
            or not isinstance(sig, dict) or set(sig) != SIGNATURE_FIELDS or sig["algorithm"] != "Ed25519":
        raise ReleaseError("the release is not signed by AKENO")
    if not isinstance(sig["key_id"], str) or not KEY_ID.fullmatch(sig["key_id"]):
        raise ReleaseError("the release signature is not readable")
    key = keys.get(sig["key_id"])
    if key is None or key_id(key) != sig["key_id"]:
        raise ReleaseError("the release is signed with a key this build does not trust")
    try:
        key.verify(signature_bytes(sig["value"]), payload.encode("utf-8"))
    except InvalidSignature as exc:
        raise ReleaseError("the release information is not signed by AKENO (signature does not match)") from exc
    m = signed_document(payload)                    # from here on, only signed values are used
    for name in SIGNED:
        if name not in m:
            raise ReleaseError(f"the signed release information has no {name}")
    _only(m, ALLOWED, "signed release information")
    for name, value in doc.items():
        # every outer field is a signed one, compared as JSON: 2 and 2.0, or 1 and true, are different values here
        if name in ("signed_payload", "signature"):
            continue
        if name not in m:
            raise ReleaseError(f"the release field {name} is not signed")
        if json.dumps(m[name], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ReleaseError(f"the release field {name} differs from the signed value")
    if not _is_int(m["schema_version"]) or m["schema_version"] != SCHEMA:
        raise ReleaseError("the signed release information has another format")
    if not _is_int(m["updater_protocol"]) or m["updater_protocol"] != PROTOCOL:
        raise ReleaseError("the release needs another updater protocol than this AKENO has")
    if m["product"] != PRODUCT or m["platform"] != PLATFORM:
        raise ReleaseError("the release is not for this app")
    if m["channel"] != CHANNEL:
        raise ReleaseError("the release is for another channel")
    version = m["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseError(f"not a version: {version}")
    minimum = m["minimum_supported_version"]                  # required; null means "any version"
    if minimum is not None and not (isinstance(minimum, str) and VERSION.fullmatch(minimum)
                                    and version_key(minimum) <= version_key(version)):
        raise ReleaseError("the signed minimum_supported_version is not valid")
    sequence = _int(m, "sequence", 1, MAX_SEQUENCE)
    request_id = m["request_id"]
    if not (isinstance(request_id, str) and REQUEST_ID.fullmatch(request_id)):
        raise ReleaseError("the signed request_id is not valid")
    now = time.time() if now is None else now
    issued, expires = _time(m, "issued_at"), _time(m, "expires_at")
    if m["published_at"] != m["issued_at"]:                   # the same text, not only the same instant
        raise ReleaseError("the signed published_at differs from issued_at")
    if not MIN_VALIDITY <= expires - issued <= MAX_VALIDITY:
        raise ReleaseError("the release information's validity is not between one hour and thirty days")
    if issued > now + FUTURE_SKEW:
        raise ReleaseError("the release information was issued in the future (more than five minutes ahead)")
    if expires <= now:
        raise ReleaseError("the release information has expired (it is offered again once it is re-signed)")
    released = m["release_date"]
    try:
        day = date.fromisoformat(released) if isinstance(released, str) else None
    except ValueError:
        day = None
    if day is None or day.isoformat() != released or day > datetime.fromtimestamp(now, timezone.utc).date():
        raise ReleaseError("the signed release_date is not valid")
    notes = m["release_notes"]
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 8000:
        raise ReleaseError("the signed release_notes are not valid")
    size = _int(m, "size_bytes", 1, MAX_INSTALLER)
    sha = _sha(m["sha256"], "installer")
    base = f"{origin}/downloads/{version}/"
    name = _download(m["download_url"], trusted, base + INSTALLER_NAME, "installer")
    installer_auth = _authenticode(m["authenticode"], "installer")
    if type(m["unattended_install_allowed"]) is not bool:
        raise ReleaseError("the signed unattended_install_allowed is not valid")
    mode = m["install_mode"]
    if mode not in MODES:
        raise ReleaseError(f"unknown install mode: {mode}")
    portable = None
    if m.get("portable") is not None:
        portable = _portable(m["portable"], trusted, base)
        if mode != "portable-directory-swap":
            raise ReleaseError("the release carries a portable package but names another install mode")
    elif mode != "manual-installer" or m["unattended_install_allowed"]:
        raise ReleaseError("a release without a portable package must be a manual installer download")
    details = Details(sequence, m["issued_at"], m["expires_at"], issued, expires, mode, m["updater_protocol"],
                      m["unattended_install_allowed"], minimum, request_id, installer_auth, portable,
                      tuple(sorted(m)))
    files = [File("installer", name, m["download_url"], sha, size)]
    if portable is not None:
        files.insert(0, File("portable", portable.name, portable.url, portable.sha256, portable.size))
    return Release(CHANNEL, version, f"v{version}", m["published_at"][:30], notes, "", files, sig["key_id"],
                   installer_auth.publisher_sha256, details)
