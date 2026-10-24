"""Where the license server is and which signing keys this build trusts.

``akeno/data/licensing.json`` (written by the release build from the repository variables
``AKENO_LICENSE_SERVER_URL`` and ``AKENO_LICENSE_PUBLIC_KEYS``, or by ``tools/configure-licensing.py``)::

    {"server_url": "https://licenses.example", "keys": [{"kid": "…", "public_key": "<base64url>"}]}

Public keys are not secret; the matching private keys exist only on the license server. Licenses verify only
against these keys and keys endorsed by them; a build without keys accepts no license at all (it never trusts
keys a server offers on first contact).

A built program (``AKENO.exe``) always talks to the address it was built with: the environment variable and the
address field exist only when running from the source code (development and tests).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from akeno.paths import app_home, resource_dir

log = logging.getLogger(__name__)


ISSUER = "akeno-licensing"          # who signs AKENO licenses (the "iss" of every signed document)
AUDIENCE = "akeno-desktop"          # who they are for (the "aud"): this Windows app


def production() -> bool:
    """A built AKENO.exe: the license server address and keys are the ones it was built with."""
    return bool(getattr(sys, "frozen", False))


@dataclass
class LicensingConfig:
    server_url: str = ""
    keys: dict[str, str] = field(default_factory=dict)        # kid -> base64url public key
    source: str = "build"                                     # build | user (address chosen when run from source)
    locked: bool = False                                      # the address cannot be changed (built program)

    @property
    def configured(self) -> bool:
        return bool(self.server_url)

    @property
    def website(self) -> str:
        return self.server_url


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def user_file() -> Path:
    return app_home() / "license-server.json"


def load() -> LicensingConfig:
    built = _read(Path(resource_dir()) / "licensing.json")
    keys = {}
    for item in built.get("keys") or []:
        if isinstance(item, dict) and item.get("kid") and item.get("public_key"):
            keys[str(item["kid"])] = str(item["public_key"])
    cfg = LicensingConfig(str(built.get("server_url") or "").rstrip("/"), keys, locked=production())
    if cfg.locked:
        return cfg                      # no environment variable, no file: the official endpoint only
    chosen = os.environ.get("AKENO_LICENSE_SERVER") or _read(user_file()).get("server_url")
    if chosen:
        cfg.server_url = str(chosen).rstrip("/")
        cfg.source = "user"
    return cfg


def save_server_url(url: str) -> None:
    if production():
        raise PermissionError("The license server of this build cannot be changed.")
    path = user_file()
    if url:
        path.write_text(json.dumps({"server_url": url.rstrip("/")}), encoding="utf-8")
    elif path.exists():
        path.unlink()
