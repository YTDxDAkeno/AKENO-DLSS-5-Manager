"""Wiring of the LAN coordinator into the server: its key, its bind address and its live membership authority."""
from __future__ import annotations

import ipaddress
import os
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

from akeno_server.clock import utcnow
from akeno_server.network.coordinator import Coordinator, Grant


def coordinator_key(path: Path) -> X25519PrivateKey:
    """The coordinator's static WireGuard key: created once (owner-only file), then reused across restarts."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = X25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:                  # another worker created it first
            return coordinator_key(path)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
        return key
    if len(raw) != 32:
        raise ValueError('the LAN coordinator key file is damaged')
    return X25519PrivateKey.from_private_bytes(raw)


def bind_address(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(':')
    host = host.strip('[]')
    ipaddress.ip_address(host)
    if not port.isdigit() or not 0 <= int(port) <= 65535:          # 0: an ephemeral port (tests)
        raise ValueError('invalid LAN coordinator bind address')
    return host, int(port)


def authority(rooms):
    """Live membership of a WireGuard key: a fresh room lease AND an active account, device and suite license."""
    def check(public_key: str):
        grant = rooms.grant_for(public_key)
        if grant is None:
            return None
        from akeno_server.db import session
        from akeno_server.licensing import service as licenses
        from akeno_server.models import Device, User
        try:
            uid, did = uuid.UUID(grant['actor']), uuid.UUID(grant['device'])
        except ValueError:
            return None
        with session() as db:
            user, device = db.get(User, uid), db.get(Device, did)
            license_ = licenses.license_of(db, uid)
            if not (user and user.active and device and device.active and device.user_id == uid and license_
                    and license_.running(utcnow())):
                return None
        return Grant(grant['room'], grant['expires'])
    return check


def build(cfg, rooms) -> Coordinator | None:
    if not cfg.network_coordinator_bind:
        return None
    return Coordinator(coordinator_key(cfg.network_coordinator_key_path), authority(rooms),
                       bind=bind_address(cfg.network_coordinator_bind), wall=lambda: utcnow().timestamp())
