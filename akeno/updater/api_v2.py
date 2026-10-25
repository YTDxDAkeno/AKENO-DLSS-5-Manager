"""Release Manager schema 2 (updater protocol 2): what ``GET /api/v1/releases/stable`` returns on production.

The contract is the one the production server publishes in its OpenAPI document (``ReleaseEnvelope``,
``PortablePackage``, ``Authenticode``, ``Signature``; read by ``tools/api_contract.py --schemas`` in the read-only
production check on 2026-10-25). Nothing here is guessed beyond what that document states; where it is silent
(see ``docs/AKENO_3_0_UPDATER_V2.md``, "Open points") the client refuses rather than assumes:

* The release counts only when ``signature`` (``Ed25519``, ``key_id`` of a release key built into this program)
  verifies over the **exact UTF-8 bytes of ``signed_payload``**, unchanged. Nothing is re-serialised.
* ``signed_payload`` must be one JSON object (as JSON text or base64url of it) without duplicate keys or
  non-finite numbers. Only its values are used. A field that appears outside it as well must be equal.
* The security fields must be inside it: schema and protocol, product, platform, channel, version, sequence,
  issue and expiry time, the installer's address, SHA-256 and size, ``unattended_install_allowed``,
  ``install_mode``, and (to install in the app) the portable package.
* Expired information (``expires_at`` in the past) is refused. Sequence and version rules against replays are
  applied by the service, which remembers what it has seen (``service.UpdateService.check``).

The app never installs anything without the user's confirmation, whatever ``unattended_install_allowed`` says.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

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
SHA = re.compile(r"^[a-f0-9]{64}$")
VERSION = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")
NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
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
KEY_ID = re.compile(r"^[a-f0-9]{16}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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
        if self.updater_protocol != PROTOCOL:
            return msg("This release needs a newer updater than this AKENO has. Install it with the installer from "
                       "the download page.")
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
    m = VERSION.match(str(text or ""))
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
    """The JSON object inside ``signed_payload``: JSON text, or base64url of JSON text."""
    import base64
    import binascii
    try:
        doc = _load(payload)
    except ValueError as exc:
        if isinstance(exc, ReleaseError):
            raise
        try:
            text = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
            doc = _load(text)
        except ReleaseError:
            raise
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc2:
            raise ReleaseError("the signed release information is not readable") from exc2
    if not isinstance(doc, dict):
        raise ReleaseError("the signed release information is not readable")
    return doc


def signature_bytes(value) -> bytes:
    """``signature.value``: 64 bytes as base64url, base64 or hex (the schema says only "string")."""
    import base64
    import binascii
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{128}", text):
        return bytes.fromhex(text)
    try:
        raw = base64.b64decode(text.replace("-", "+").replace("_", "/") + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReleaseError("the release signature is not readable") from exc
    if len(raw) != 64:
        raise ReleaseError("the release signature is not readable")
    return raw


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
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(f"the signed {name} is not a time") from exc
    if dt.tzinfo is None:
        raise ReleaseError(f"the signed {name} has no time zone")
    if dt.utcoffset().total_seconds() != 0:
        raise ReleaseError(f"the signed {name} is not in UTC")
    ts = dt.timestamp()
    if not math.isfinite(ts):
        raise ReleaseError(f"the signed {name} is not a time")
    return ts


def _authenticode(value, where: str) -> Authenticode:
    if not isinstance(value, dict) or not isinstance(value.get("verified"), bool) \
            or value.get("status") not in AUTHENTICODE:
        raise ReleaseError(f"the code signature information of the {where} is not valid")
    _only(value, AUTHENTICODE_FIELDS, f"code signature information of the {where}")
    publisher = value.get("publisher_certificate_sha256")
    if publisher is not None and not (isinstance(publisher, str) and SHA.match(publisher)):
        raise ReleaseError(f"the code signature information of the {where} is not valid")
    # "verified" and "status" say the same thing twice; an invalid signature never counts as a valid one
    if value["verified"] != (value["status"] == "verified"):
        raise ReleaseError(f"the code signature information of the {where} contradicts itself")
    return Authenticode(value["verified"], value["status"], publisher or "")


def _download(url, trusted, suffix: str, what: str) -> str:
    if not isinstance(url, str) or not trusted(url):
        raise ReleaseError(f"the {what} address is not an official AKENO download: {url}")
    parts = urllib.parse.urlsplit(url)
    if parts.query or parts.fragment:
        raise ReleaseError(f"the {what} address is not an official AKENO download: {url}")
    name = urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    if not NAME.match(name) or not name.lower().endswith(suffix):
        raise ReleaseError(f"the {what} file name is not valid: {name}")
    return name


def _portable(value, trusted) -> Portable:
    if not isinstance(value, dict):
        raise ReleaseError("the portable package information is not valid")
    _only(value, PORTABLE_FIELDS, "portable package information")
    missing = sorted(PORTABLE_FIELDS - set(value))
    if missing:
        raise ReleaseError(f"the portable package information has no {', '.join(missing)}")
    name = _download(value.get("download_url"), trusted, ".zip", "portable package")
    sha, content = value.get("sha256"), value.get("content_sha256")
    if not (isinstance(sha, str) and SHA.match(sha) and isinstance(content, str) and SHA.match(content)):
        raise ReleaseError("the portable package checksums are not valid")
    pe = _int(value, "pe_files", 1, MAX_PE)
    verified = _int(value, "verified_pe_files", 0, MAX_PE)
    if verified > pe:
        raise ReleaseError("the portable package claims more signed programs than it has")
    files = _int(value, "file_count", 1, MAX_FILES)
    if pe > files:
        raise ReleaseError("the portable package claims more programs than files")
    return Portable(value["download_url"], name, sha, _int(value, "size_bytes", 1, MAX_PORTABLE), content, files,
                    _int(value, "uncompressed_size_bytes", 1, MAX_UNPACKED), pe, verified,
                    _authenticode(value.get("authenticode"), "portable package"))


def parse(doc, keys: dict, trusted, now: float | None = None):
    """A schema-2 release (``Release`` with ``v2`` details), or ReleaseError.

    ``keys``: the build's release keys (``{kid: Ed25519PublicKey}``); ``trusted(url)``: the build's download hosts;
    ``now``: the time for the expiry check (the caller passes a clock that cannot go back, see the service)."""
    from cryptography.exceptions import InvalidSignature
    if not keys:
        raise ReleaseError("this build has no release verification key")
    if not isinstance(doc, dict) or not _is_int(doc.get("schema_version")) or doc["schema_version"] != SCHEMA:
        raise ReleaseError("not an AKENO release (unknown format)")
    sig, payload = doc.get("signature"), doc.get("signed_payload")
    if not isinstance(sig, dict) or sig.get("algorithm") != "Ed25519" or not isinstance(payload, str) or not payload:
        raise ReleaseError("the release is not signed by AKENO")
    _only(sig, SIGNATURE_FIELDS, "release signature")
    if not isinstance(sig.get("key_id"), str) or not KEY_ID.match(sig["key_id"]) or not isinstance(sig.get("value"), str):
        raise ReleaseError("the release signature is not readable")
    _only(doc, ENVELOPE, "release")
    key = keys.get(str(sig.get("key_id") or ""))
    if key is None:
        raise ReleaseError("the release is signed with a key this build does not trust")
    try:
        key.verify(signature_bytes(sig.get("value")), payload.encode("utf-8"))
    except InvalidSignature as exc:
        raise ReleaseError("the release information is not signed by AKENO (signature does not match)") from exc
    m = signed_document(payload)                    # from here on, only signed values are used
    for name in SIGNED:
        if name not in m:
            raise ReleaseError(f"the signed release information has no {name}")
    _only(m, ALLOWED, "signed release information")
    for name, value in doc.items():
        # compared as JSON: 2 and 2.0, or 1 and true, are different values here
        if name not in ("signed_payload", "signature") and name in m and \
                json.dumps(m[name], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ReleaseError(f"the release field {name} differs from the signed value")
    if not _is_int(m["schema_version"]) or m["schema_version"] != SCHEMA:
        raise ReleaseError("the signed release information has another format")
    if m["product"] != PRODUCT or m["platform"] != PLATFORM:
        raise ReleaseError("the release is not for this app")
    if m["channel"] != CHANNEL:
        raise ReleaseError("the release is for another channel")
    version = m["version"]
    if not isinstance(version, str) or not VERSION.match(version):
        raise ReleaseError(f"not a version: {version}")
    protocol = m["updater_protocol"]
    if not _is_int(protocol) or protocol < 1:
        raise ReleaseError("the signed updater_protocol is not valid")
    sequence = _int(m, "sequence", 1, MAX_SEQUENCE)
    now = time.time() if now is None else now
    issued, expires = _time(m, "issued_at"), _time(m, "expires_at")
    published = _time(m, "published_at")
    if issued > now + FUTURE_SKEW:
        raise ReleaseError("the release information was issued in the future (more than five minutes ahead)")
    if published != issued:
        raise ReleaseError("the signed published_at differs from issued_at")
    if not MIN_VALIDITY <= expires - issued <= MAX_VALIDITY:
        raise ReleaseError("the release information's validity is not between one hour and thirty days")
    if expires <= now:
        raise ReleaseError("the release information has expired (it is offered again once it is re-signed)")
    if not isinstance(m["release_date"], str) or not DATE.match(m["release_date"]):
        raise ReleaseError("the signed release_date is not a date")
    try:
        datetime.strptime(m["release_date"], "%Y-%m-%d")
    except ValueError as exc:
        raise ReleaseError("the signed release_date is not a date") from exc
    notes = m["release_notes"]
    if not isinstance(notes, str) or not 1 <= len(notes) <= 8000:
        raise ReleaseError("the signed release_notes are not valid")
    if not isinstance(m["unattended_install_allowed"], bool):
        raise ReleaseError("the signed unattended_install_allowed is not valid")
    mode = m["install_mode"]
    if mode not in MODES:
        raise ReleaseError(f"unknown install mode: {mode}")
    name = _download(m["download_url"], trusted, ".exe", "installer")
    sha = m["sha256"]
    if not (isinstance(sha, str) and SHA.match(sha)):
        raise ReleaseError("the installer checksum is not valid")
    size = _int(m, "size_bytes", 1, MAX_INSTALLER)
    minimum = m["minimum_supported_version"]                  # required; null means "any version"
    if minimum is not None and not (isinstance(minimum, str) and VERSION.match(minimum)):
        raise ReleaseError("the signed minimum_supported_version is not a version")
    request_id = m["request_id"]
    if not (isinstance(request_id, str) and REQUEST_ID.match(request_id)):
        raise ReleaseError("the signed request_id is not valid")
    portable = None
    if m.get("portable") is not None:
        portable = _portable(m["portable"], trusted)
    if mode == "portable-directory-swap" and portable is None:
        raise ReleaseError("the release names a portable update but carries no portable package")
    installer_auth = _authenticode(m["authenticode"], "installer")
    details = Details(sequence, str(m["issued_at"]), str(m["expires_at"]), issued, expires, mode, protocol,
                      m["unattended_install_allowed"], minimum, request_id, installer_auth, portable,
                      tuple(sorted(m)))
    files = [File("installer", name, m["download_url"], sha, size)]
    if portable is not None:
        files.insert(0, File("portable", portable.name, portable.url, portable.sha256, portable.size))
    return Release(CHANNEL, version, f"v{version}", str(m["published_at"])[:30], notes, "", files, str(sig["key_id"]),
                   installer_auth.publisher_sha256, details)
