"""Licenses: redeeming codes, stacking time, devices, signed leases and lifetime offline certificates.

Stacking is deterministic:

* a time code adds its duration to the later of *now* and the current expiry (an expired license restarts now);
* the license type becomes the code's type unless the running type is longer (a 72-hour code on a running
  monthly license adds 72 hours and stays *monthly*);
* a lifetime code makes the license permanent; a time code cannot be redeemed onto a lifetime license
  (it would be wasted, so it stays unused);
* every time comes from the server clock (UTC) at the moment of redemption.

Redemption is safe under concurrency: the code row and the license row are locked (``SELECT … FOR UPDATE`` on
PostgreSQL, an immediate write transaction on SQLite), the code is switched with a compare-and-set update, and
``license_redemptions.code_id`` is unique, so a code can never be used twice.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from akeno_server import ENTITLEMENT, settings
from akeno_server.clock import utcnow
from akeno_server.errors import ApiError, bad_request, conflict, forbidden, not_found
from akeno_server.licensing import codes, signing
from akeno_server.models import (Device, License, LicenseCode, LicenseEvent, LicenseRedemption, LicenseType,
                                 LicenseValidation, OfflineCertificate, User)

ISSUER = "akeno-licensing"
AUDIENCE = "akeno-desktop"          # the Windows app; tokens for anything else must never verify there
RANK = {LicenseType.TRIAL_24H: 0, LicenseType.TRIAL_72H: 1, LicenseType.MONTHLY: 2, LicenseType.LIFETIME: 3}


# -- pure rules ------------------------------------------------------------------------------------------------------
def stacked(current: License | None, code_type: LicenseType, now: datetime) -> tuple[LicenseType, datetime | None]:
    """The license type and expiry after redeeming a code of ``code_type`` (raises for a wasted code)."""
    if current is not None and current.lifetime:
        raise conflict("already_lifetime", "This account already has a lifetime license; the code was not used.")
    if code_type is LicenseType.LIFETIME:
        return LicenseType.LIFETIME, None
    running = current is not None and current.status == "active" and current.expires_at is not None \
        and current.expires_at > now
    base = current.expires_at if running else now
    new_type = code_type
    if running and RANK[LicenseType(current.license_type)] > RANK[code_type]:
        new_type = LicenseType(current.license_type)
    return new_type, base + timedelta(seconds=code_type.seconds)


def describe(lic: License | None, now: datetime) -> dict:
    if lic is None:
        return {"active": False, "status": "none", "entitlement": None}
    running = lic.running(now)
    remaining = None if lic.expires_at is None else max(0, int((lic.expires_at - now).total_seconds()))
    status = lic.status if lic.status != "active" else ("active" if running else "expired")
    return {"id": str(lic.id), "active": running, "status": status, "entitlement": ENTITLEMENT if running else None,
            "license_type": lic.license_type, "lifetime": lic.lifetime, "activated_at": lic.activated_at,
            "expires_at": lic.expires_at, "remaining_seconds": remaining, "device_limit": lic.device_limit,
            "offline_eligible": lic.lifetime and running, "server_time": now}


# -- queries ---------------------------------------------------------------------------------------------------------
def license_of(db: Session, user_id: uuid.UUID, lock: bool = False) -> License | None:
    q = select(License).where(License.user_id == user_id)
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


def active_devices(db: Session, user_id: uuid.UUID) -> list[Device]:
    return list(db.scalars(select(Device).where(Device.user_id == user_id, Device.revoked_at.is_(None))
                           .order_by(Device.created_at)))


def event(db: Session, lic: License, kind: str, actor: User | None, old_type, old_exp, note: str = "",
          code: LicenseCode | None = None) -> None:
    db.add(LicenseEvent(license_id=lic.id, kind=kind, actor_id=actor.id if actor else None,
                        old_type=old_type, new_type=lic.license_type, old_expires_at=old_exp,
                        new_expires_at=lic.expires_at, code_id=code.id if code else None, note=note[:300]))


# -- redemption ------------------------------------------------------------------------------------------------------
@dataclass
class Redeemed:
    license: License
    code_type: str
    already: bool = False        # the same account redeemed this code before (a retry): nothing changed


def _lock_or_create_license(db: Session, user: User, now: datetime, code_type: LicenseType) -> tuple[License, bool]:
    lic = license_of(db, user.id, lock=True)
    if lic is not None:
        return lic, False
    placeholder = License(user_id=user.id, license_type=code_type.value, status="active", activated_at=now,
                          expires_at=None if code_type is LicenseType.LIFETIME else now,
                          device_limit=settings.get(db, "default_device_limit"))
    nested = db.begin_nested()
    try:
        db.add(placeholder)
        db.flush()
        nested.commit()
        return placeholder, True
    except IntegrityError:
        nested.rollback()                    # created at the same moment by a parallel redemption
        lic = license_of(db, user.id, lock=True)
        assert lic is not None
        return lic, False


def redeem(db: Session, user: User, code_text: str, ip: str = "", channel: str = "app") -> Redeemed:
    """Redeem a code for ``user``; commits. Raises ApiError with a stable code on every refusal."""
    if not user.active:
        raise forbidden("account_disabled", "This account is disabled.")
    try:
        body = codes.normalize(code_text)
    except codes.CodeFormatError as exc:
        if str(exc) == "lookalike":
            raise bad_request("invalid_code_format", "Activation codes never contain 0, 1, O or I.") from exc
        raise bad_request("invalid_code_format", "An activation code looks like AKENO-XXXX-XXXX-XXXX-XXXX.") from exc
    now = utcnow()
    code = db.scalars(select(LicenseCode).where(LicenseCode.verifier == codes.verifier(body))
                      .with_for_update()).first()
    if code is None:
        raise bad_request("invalid_code", "This activation code does not exist.")
    if code.status == "redeemed":
        if code.redeemed_by == user.id:
            lic = license_of(db, user.id)
            db.rollback()
            if lic is not None:
                return Redeemed(lic, code.code_type, already=True)
        raise conflict("code_used", "This activation code has already been used.")
    if code.status == "revoked":
        raise bad_request("code_revoked", "This activation code has been withdrawn.")
    if code.expires_at is not None and code.expires_at <= now:
        raise bad_request("code_expired", "This activation code has expired.")
    if code.assigned_user_id is not None and code.assigned_user_id != user.id:
        raise forbidden("code_not_for_account", "This activation code was issued for another account.")
    code_type = LicenseType(code.code_type)
    if code_type.trial and settings.get(db, "trial_once_per_account"):
        used = db.scalar(select(func.count()).select_from(LicenseRedemption).join(
            LicenseCode, LicenseCode.id == LicenseRedemption.code_id).where(
            LicenseRedemption.user_id == user.id, LicenseCode.code_type == code_type.value))
        if used:
            raise conflict("trial_already_used", "A trial of this length was already used on this account.")

    lic, created = _lock_or_create_license(db, user, now, code_type)
    if lic.status != "active":
        db.rollback()
        raise forbidden("license_disabled", "This license is disabled. Please contact support.")
    old_type = None if created else lic.license_type
    old_exp = None if created else lic.expires_at
    new_type, new_exp = stacked(None if created else lic, code_type, now)

    switched = db.execute(update(LicenseCode).where(LicenseCode.id == code.id,
                                                    LicenseCode.status.in_(("available", "reserved")))
                          .values(status="redeemed", redeemed_at=now, redeemed_by=user.id))
    if switched.rowcount != 1:
        db.rollback()
        raise conflict("code_used", "This activation code has already been used.")
    lic.license_type = new_type.value
    lic.expires_at = new_exp
    if code.device_allowance:
        lic.device_limit = max(lic.device_limit, code.device_allowance)
    db.add(LicenseRedemption(code_id=code.id, user_id=user.id, license_id=lic.id, redeemed_at=now,
                             previous_type=old_type, previous_expires_at=old_exp, new_type=new_type.value,
                             new_expires_at=new_exp, channel=channel, ip=ip))
    event(db, lic, "redeem", user, old_type, old_exp, note=f"{code.code_type} code …{code.hint}", code=code)
    try:
        db.commit()
    except IntegrityError as exc:           # the unique code_id: a parallel redemption won
        db.rollback()
        raise conflict("code_used", "This activation code has already been used.") from exc
    return Redeemed(lic, code.code_type)


# -- administration --------------------------------------------------------------------------------------------------
def ensure_license(db: Session, user: User, kind: LicenseType = LicenseType.MONTHLY) -> tuple[License, bool]:
    now = utcnow()
    return _lock_or_create_license(db, user, now, kind)


def admin_extend(db: Session, actor: User, user: User, seconds: int, note: str = "") -> License:
    if seconds <= 0 or seconds > 10 * 365 * 86400:
        raise bad_request("invalid_duration", "Choose a duration between one hour and ten years.")
    now = utcnow()
    lic, created = ensure_license(db, user)
    if lic.lifetime and not created:
        raise conflict("already_lifetime", "This account already has a lifetime license.")
    old_type, old_exp = (None, None) if created else (lic.license_type, lic.expires_at)
    base = lic.expires_at if (not created and lic.expires_at and lic.expires_at > now) else now
    lic.expires_at = base + timedelta(seconds=seconds)
    if created:
        lic.license_type = LicenseType.MONTHLY.value
    event(db, lic, "extend", actor, old_type, old_exp, note=note or f"+{seconds // 3600} h")
    return lic


def admin_set_type(db: Session, actor: User, user: User, kind: LicenseType, expires_at: datetime | None,
                   note: str = "") -> License:
    now = utcnow()
    lic, created = ensure_license(db, user, kind)
    old_type, old_exp = (None, None) if created else (lic.license_type, lic.expires_at)
    if kind is LicenseType.LIFETIME:
        expires_at = None
    elif expires_at is None:
        # a running time license keeps its expiry; anything else starts a full period of the new type now
        keep = not created and old_exp is not None and old_exp > now
        expires_at = old_exp if keep else now + timedelta(seconds=kind.seconds)
    elif expires_at.tzinfo is None:
        raise bad_request("invalid_expiry", "The expiry needs a time zone.")
    lic.license_type = kind.value
    lic.expires_at = expires_at
    if old_type == LicenseType.LIFETIME.value and kind is not LicenseType.LIFETIME:
        revoke_certificates(db, lic, "type_changed")
    event(db, lic, "grant_lifetime" if kind is LicenseType.LIFETIME else "change_type", actor, old_type, old_exp,
          note=note)
    return lic


def admin_set_status(db: Session, actor: User, user: User, status: str, note: str = "") -> License:
    if status not in ("active", "disabled", "revoked"):
        raise bad_request("invalid_status", "Unknown license status.")
    lic = license_of(db, user.id, lock=True)
    if lic is None:
        raise not_found("no_license", "This account has no license.")
    old = lic.status
    lic.status = status
    if status != "active":
        revoke_certificates(db, lic, f"license_{status}")
    event(db, lic, f"status_{status}", actor, lic.license_type, lic.expires_at, note=note or f"{old} → {status}")
    return lic


# -- devices ---------------------------------------------------------------------------------------------------------
def register_device(db: Session, user: User, fingerprint: str, label: str = "", os_name: str = "",
                    app_version: str = "") -> Device:
    fingerprint = (fingerprint or "").strip().lower()
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise bad_request("invalid_fingerprint", "The device identifier must be a SHA-256 value.")
    now = utcnow()
    dev = db.scalars(select(Device).where(Device.user_id == user.id, Device.fingerprint == fingerprint)
                     .with_for_update()).first()
    lic = license_of(db, user.id, lock=True)
    limit = lic.device_limit if lic else settings.get(db, "default_device_limit")
    if dev is None or not dev.active:
        if dev is not None and dev.revoked_by == "admin":
            raise forbidden("device_blocked", "An administrator removed this PC from the account.")
        others = [d for d in active_devices(db, user.id) if dev is None or d.id != dev.id]
        if len(others) >= limit:
            raise conflict("device_limit_reached", f"This license allows {limit} PC(s). Remove one in your "
                           "account first.", limit=limit, devices=[{"id": str(d.id), "label": d.label,
                                                                   "last_seen_at": d.last_seen_at.isoformat()}
                                                                  for d in others])
    if dev is None:
        dev = Device(user_id=user.id, fingerprint=fingerprint, created_at=now)
        db.add(dev)
    dev.revoked_at = None
    dev.revoked_by = ""
    dev.label = (label or dev.label or "PC")[:80]
    dev.os = (os_name or dev.os)[:80]
    dev.app_version = (app_version or dev.app_version)[:40]
    dev.last_seen_at = now
    db.flush()
    return dev


def user_remove_device(db: Session, user: User, device: Device) -> None:
    now = utcnow()
    limit = settings.get(db, "device_removals_per_30_days")
    recent = db.scalar(select(func.count()).select_from(Device).where(
        Device.user_id == user.id, Device.revoked_by == "user", Device.revoked_at > now - timedelta(days=30)))
    if recent >= limit:
        raise conflict("device_removals_exhausted", "You removed the maximum number of PCs for 30 days. "
                       "Support can help.", limit=limit)
    revoke_device(db, device, "user")


def revoke_device(db: Session, device: Device, by: str) -> None:
    device.revoked_at = utcnow()
    device.revoked_by = by
    for cert in db.scalars(select(OfflineCertificate).where(OfflineCertificate.device_id == device.id,
                                                            OfflineCertificate.revoked_at.is_(None))):
        cert.revoked_at = device.revoked_at
        cert.revoke_reason = f"device_removed_{by}"


def revoke_certificates(db: Session, lic: License, reason: str) -> None:
    now = utcnow()
    for cert in db.scalars(select(OfflineCertificate).where(OfflineCertificate.license_id == lic.id,
                                                            OfflineCertificate.revoked_at.is_(None))):
        cert.revoked_at = now
        cert.revoke_reason = reason


# -- signed documents ------------------------------------------------------------------------------------------------
def _ts(value: datetime | None) -> int | None:
    return None if value is None else int(value.timestamp())


def issue_lease(db: Session, user: User, lic: License, device: Device, nonce: str | None = None) -> tuple[str, dict]:
    """A signed, device-bound statement that the license runs; valid offline until ``exp``. ``nonce`` is the
    app's random value of this request: the app accepts the lease as fresh only with its own nonce."""
    now = utcnow()
    grace = settings.get(db, "offline_grace_hours") * 3600
    exp = now + timedelta(seconds=grace)
    if lic.expires_at is not None:
        exp = min(exp, lic.expires_at)
    payload = {"typ": "akeno-lease", "v": 2, "iss": ISSUER, "aud": AUDIENCE, "sub": str(user.id),
               "lic": str(lic.id), "dev": str(device.id), "dfp": device.fingerprint, "ent": ENTITLEMENT,
               "lt": lic.license_type, "lexp": _ts(lic.expires_at), "act": _ts(lic.activated_at), "iat": _ts(now),
               "nbf": _ts(now), "exp": _ts(exp), "grace": grace, "jti": secrets.token_hex(8)}
    if nonce:
        payload["nonce"] = nonce
    kid, key = signing.store().active()
    return signing.sign(key, kid, "akeno-lease", payload), payload


def issue_offline_certificate(db: Session, user: User, lic: License, device: Device) -> tuple[str, dict]:
    if not (lic.lifetime and lic.running(utcnow())):
        raise forbidden("not_lifetime", "Offline certificates exist for lifetime licenses only.")
    if not device.active or device.user_id != user.id:
        raise forbidden("device_not_registered", "Register this PC first.")
    kid, key = signing.store().active()
    now = utcnow()
    for old in db.scalars(select(OfflineCertificate).where(OfflineCertificate.device_id == device.id,
                                                           OfflineCertificate.revoked_at.is_(None))):
        old.revoked_at = now                 # one certificate per PC: the newest replaces the others
        old.revoke_reason = "replaced"
    cert = OfflineCertificate(license_id=lic.id, device_id=device.id, kid=kid, issued_at=now, payload_sha256="")
    db.add(cert)
    db.flush()
    payload = {"typ": "akeno-offline-license", "v": 2, "iss": ISSUER, "aud": AUDIENCE, "sub": str(user.id),
               "lic": str(lic.id), "dev": str(device.id), "dfp": device.fingerprint, "ent": ENTITLEMENT,
               "lt": lic.license_type, "act": _ts(lic.activated_at), "iat": _ts(now), "nbf": _ts(now),
               "serial": str(cert.id)}
    token = signing.sign(key, kid, "akeno-offline-license", payload)
    cert.payload_sha256 = hashlib.sha256(token.encode()).hexdigest()
    return token, payload


def current_certificate_ok(db: Session, serial: str | None, device: Device) -> bool:
    if not serial:
        return False
    try:
        cert = db.get(OfflineCertificate, uuid.UUID(serial))
    except ValueError:
        return False
    return cert is not None and cert.revoked_at is None and cert.device_id == device.id


def validation_statement(user: User, lic: License | None, device: Device | None, result: str, nonce: str,
                         certificate_revoked: bool, now) -> str:
    """The signed answer to one validation request. The app acts on a validation (unlock, lock, delete a
    certificate) only through this statement, never through the unsigned JSON next to it."""
    payload = {"typ": "akeno-validation", "v": 2, "iss": ISSUER, "aud": AUDIENCE, "sub": str(user.id),
               "dev": str(device.id) if device else None, "dfp": device.fingerprint if device else None,
               "ent": ENTITLEMENT, "nonce": nonce, "res": result, "crev": bool(certificate_revoked),
               "st": lic.status if lic else "none", "lt": lic.license_type if lic else None,
               "lexp": _ts(lic.expires_at) if lic else None, "act": _ts(lic.activated_at) if lic else None,
               "iat": _ts(now), "exp": _ts(now + timedelta(minutes=10)), "jti": secrets.token_hex(8)}
    kid, key = signing.store().active()
    return signing.sign(key, kid, "akeno-validation", payload)


def validate(db: Session, user: User, device: Device | None, app_version: str = "", ip: str = "",
             certificate_serial: str | None = None, nonce: str = "") -> dict:
    """The app's periodic check: license state plus a fresh lease (and, for lifetime, the certificate), all
    bound to the app's nonce in a signed validation statement."""
    now = utcnow()
    lic = license_of(db, user.id)
    result = "ok"
    out: dict = {"license": describe(lic, now), "server_time": now,
                 "validation_interval_seconds": settings.get(db, "validation_interval_hours") * 3600,
                 "offline_grace_seconds": settings.get(db, "offline_grace_hours") * 3600,
                 "min_client_version": settings.get(db, "min_client_version") or None,
                 "latest_client_version": settings.get(db, "latest_client_version") or None,
                 "lease": None, "offline_certificate": None, "certificate_revoked": False}
    if not user.active:
        result = "account_disabled"
    elif device is None or not device.active or device.user_id != user.id:
        result = "device_not_registered"
    elif lic is None:
        result = "no_license"
    elif lic.status != "active":
        result = f"license_{lic.status}"
    elif not lic.running(now):
        result = "expired"
    else:
        device.last_seen_at = now
        if app_version:
            device.app_version = app_version[:40]
        out["lease"], _ = issue_lease(db, user, lic, device, nonce)
        if lic.lifetime:
            if current_certificate_ok(db, certificate_serial, device):
                out["offline_certificate"] = "unchanged"
            else:
                out["offline_certificate"], _ = issue_offline_certificate(db, user, lic, device)
    # the app deletes the certificate it holds: the license, account or PC no longer qualifies for it
    out["certificate_revoked"] = bool(certificate_serial) and (result != "ok" or not lic.lifetime)
    out["result"] = result
    out["validation"] = validation_statement(user, lic, device, result, nonce, out["certificate_revoked"], now)
    db.add(LicenseValidation(user_id=user.id, license_id=lic.id if lic else None,
                             device_id=device.id if device else None, result=result, app_version=app_version[:40],
                             ip=ip))
    return out


def find_device(db: Session, user: User, device_id: str | None) -> Device | None:
    if not device_id:
        return None
    try:
        dev = db.get(Device, uuid.UUID(str(device_id)))
    except ValueError:
        return None
    return dev if dev is not None and dev.user_id == user.id else None


def require_device(db: Session, user: User, device_id: str) -> Device:
    dev = find_device(db, user, device_id)
    if dev is None:
        raise not_found("device_not_found", "This PC is not registered on the account.")
    return dev


__all__ = ["ApiError", "Redeemed", "redeem", "stacked", "describe", "validate"]
