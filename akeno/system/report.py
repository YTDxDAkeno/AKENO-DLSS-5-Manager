"""Reports to share (support, a forum post): the latest results of a module as JSON, with private details left out
by default - file paths, local and own IP addresses, MAC addresses, user and PC names, serial numbers.

Nothing is sent anywhere; the user saves the file and decides what to do with it.
"""

from __future__ import annotations

import ipaddress
import os
import re
import time
from typing import Any

from akeno import __version__
from akeno.diagnostics.privacy import redact

SECTIONS = {
    "doctor": ("doctor.slowpc", "doctor.crashes", "doctor.hardware"),
    "security": ("security.scan", "security.files", "security.download"),
    "network": ("network.stability", "network.bufferbloat", "network.dns"),
}
# well-known public resolvers and test targets say nothing about the user
PUBLIC_TARGETS = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112", "208.67.222.222",
                  "208.67.220.220", "94.140.14.14", "2606:4700:4700::1111", "2001:4860:4860::8888"}
PRIVATE_KEYS = {"serial", "serial_number", "serialnumber", "mac", "mac_address", "hostname", "computer_name",
                "user", "username", "domain_user", "public_ip", "sid", "uniqueid", "unique_id"}
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%\w+)?(?![\w:])")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")


def _address(match: re.Match) -> str:
    text = match.group(0)
    try:
        ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return text                                    # a version number or a time, not an address
    return text if text in PUBLIC_TARGETS else "[address omitted]"


def scrub(value: Any, env: dict | None = None) -> Any:
    """``redact`` (file paths) plus addresses, MAC addresses, user and PC names and private keys."""
    env = env if env is not None else dict(os.environ)
    names = [n for n in (env.get("USERNAME"), env.get("COMPUTERNAME"), env.get("USERDOMAIN")) if n and len(n) >= 3]
    pattern = re.compile("|".join(re.escape(n) for n in names), re.I) if names else None

    def text(s: str) -> str:
        s = _MAC.sub("[MAC omitted]", s)
        s = _IPV4.sub(_address, s)
        s = _IPV6.sub(_address, s)
        if pattern is not None:
            s = pattern.sub("[name omitted]", s)
        return s

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items() if str(k).lower() not in PRIVATE_KEYS}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, str):
            return text(v)
        return v
    return walk(redact(value))


def build(store, module: str, extra: dict[str, Any] | None = None, private: bool = False,
          env: dict | None = None) -> dict[str, Any]:
    """The latest results of ``module`` (plus ``extra``, e.g. what the page shows right now)."""
    from akeno.system.model import dec
    data: dict[str, Any] = {"akeno": __version__, "module": module,
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "note": "Private details omitted: file paths, addresses, names, serial numbers."
                            if not private else "Full report including private details."}
    for kind in SECTIONS.get(module, ()):
        data[kind] = store.history(kind, 5)
    if extra:
        data.update(extra)
    data = _plain(data, dec)
    return data if private else scrub(data, env)


def _plain(v, dec):
    """Translatable texts as their English text (the report is for support, which reads English)."""
    if isinstance(v, dict):
        if set(v) == {"t", "p"}:
            return str(dec(v))
        return {k: _plain(x, dec) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x, dec) for x in v]
    return v
