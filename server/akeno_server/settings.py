"""Settings administrators change at run time (stored in the database, edited on the admin website)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from akeno_server.models import ServerSetting

# key: (default, type, lower bound, upper bound)
DEFAULTS: dict[str, tuple[Any, type, float | None, float | None]] = {
    "registration_enabled": (True, bool, None, None),
    "require_email_verification": (False, bool, None, None),
    "default_device_limit": (2, int, 1, 50),
    "device_removals_per_30_days": (3, int, 0, 100),
    "trial_once_per_account": (True, bool, None, None),
    "validation_interval_hours": (12, int, 1, 24 * 7),
    "offline_grace_hours": (72, int, 1, 24 * 30),
    "maintenance_mode": (False, bool, None, None),
    "maintenance_message": ("", str, None, 300),
    "min_client_version": ("", str, None, 40),
    "latest_client_version": ("", str, None, 40),
    "retention_days": (180, int, 30, 3650),
    # AKENO VPN licenses (codes and grants) stay off until the use of VPN Gate in AKENO is cleared
    "vpn_module_licenses": (False, bool, None, None),
}

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class SettingError(ValueError):
    pass


def coerce(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise SettingError(f"unknown setting {key}")
    default, kind, lo, hi = DEFAULTS[key]
    if kind is bool:
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if kind is int:
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingError(f"{key} must be a whole number") from exc
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise SettingError(f"{key} must be between {lo:g} and {hi:g}")
        return value
    value = str(value or "").strip()
    if hi is not None and len(value) > hi:
        raise SettingError(f"{key} is too long")
    if key.endswith("client_version") and value and not VERSION.match(value):
        raise SettingError(f"{key} must look like 3.0.0")
    return value


def get(db: Session, key: str) -> Any:
    row = db.get(ServerSetting, key)
    if row is None:
        return DEFAULTS[key][0]
    try:
        return coerce(key, row.value.get("v"))
    except SettingError:
        return DEFAULTS[key][0]


def all_settings(db: Session) -> dict[str, Any]:
    return {key: get(db, key) for key in DEFAULTS}


def put(db: Session, key: str, value: Any, actor: uuid.UUID | None = None) -> Any:
    value = coerce(key, value)
    row = db.get(ServerSetting, key)
    if row is None:
        db.add(ServerSetting(key=key, value={"v": value}, updated_by=actor))
    else:
        row.value = {"v": value}
        row.updated_by = actor
    return value


def version_tuple(text: str) -> tuple[int, int, int] | None:
    m = VERSION.match(text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None
