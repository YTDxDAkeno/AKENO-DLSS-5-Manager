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
