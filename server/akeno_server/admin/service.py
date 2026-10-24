"""Administrative operations, shared by the admin API and the admin website. Every change is audited."""

from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from akeno_server import audit, settings
from akeno_server.auth import service as auth
from akeno_server.auth import totp
from akeno_server.clock import utcnow
from akeno_server.errors import bad_request, conflict, forbidden, not_found
from akeno_server.licensing import codes as codegen
from akeno_server.licensing import service as lic_service
from akeno_server.models import (AdminAuditEvent, Device, License, LicenseCode, LicenseEvent, LicenseRedemption,
                                 LicenseType, LicenseValidation, OfflineCertificate, Role, SignInEvent, User,
                                 UserSession)

S, A, SA = Role.SUPPORT, Role.ADMIN, Role.SUPER_ADMIN
PERMISSIONS: dict[str, Role] = {
    "view": S, "notes": S, "reset_devices": S, "revoke_sessions": S, "send_reset": S, "resend_verification": S,
    "rename_device": S, "revoke_device": S,
    "generate_codes": A, "revoke_codes": A, "reserve_codes": A, "extend": A, "set_license": A, "grant_lifetime": A,
    "license_status": A, "user_status": A, "export": A, "transfer_device": A, "audit": A, "settings": A,
    "change_role": SA, "disable_2fa": SA, "keys": SA,
}


def can(actor: User, action: str) -> bool:
    return actor.role_enum.rank >= PERMISSIONS[action].rank


def require(actor: User, action: str) -> None:
    if not can(actor, action):
        raise forbidden("insufficient_role", "Your role does not allow this.")


def _guard_target(actor: User, target: User) -> None:
    """Staff can act on accounts below their own rank (a super admin on everyone, including other super admins)."""
    if actor.role_enum is Role.SUPER_ADMIN:
        return
    if target.id != actor.id and target.role_enum.rank >= actor.role_enum.rank:
        raise forbidden("insufficient_role", "You cannot change an account with an equal or higher role.")


def get_user(db: Session, user_id: str | uuid.UUID) -> User:
    try:
        user = db.get(User, uuid.UUID(str(user_id)))
    except ValueError:
        user = None
    if user is None:
        raise not_found("user_not_found", "No such account.")
    return user


def paginate(db: Session, query, page: int, per_page: int) -> tuple[list, int]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = list(db.scalars(query.offset((page - 1) * per_page).limit(per_page)))
    return rows, total


# -- codes -----------------------------------------------------------------------------------------------------------
def generate_codes(db: Session, actor: User, code_type: str, count: int, device_allowance: int | None = None,
                   expires_in_days: int | None = None, note: str = "", batch: str = "",
                   assigned_email: str | None = None, ip: str = "") -> tuple[str, list[tuple[str, LicenseCode]]]:
    require(actor, "generate_codes")
    kind = LicenseType(code_type)
    if not 1 <= count <= 500:
        raise bad_request("invalid_count", "Generate between 1 and 500 codes at a time.")
    assigned = None
    if assigned_email:
        assigned = auth.user_by_email(db, assigned_email)
        if assigned is None:
            raise not_found("user_not_found", "No account with this e-mail address.")
    now = utcnow()
    batch = batch.strip() or f"{now:%Y%m%d-%H%M}-{secrets.token_hex(2)}"
    expires = now + timedelta(days=expires_in_days) if expires_in_days else None
    out = []
    for _ in range(count):
        while True:
            plain = codegen.generate()
            body = codegen.normalize(plain)
            verifier = codegen.verifier(body)
            if db.scalar(select(LicenseCode.id).where(LicenseCode.verifier == verifier)) is None:
                break
        row = LicenseCode(verifier=verifier, hint=codegen.hint(body), code_type=kind.value,
                          device_allowance=device_allowance, expires_at=expires, note=note.strip()[:300],
                          batch=batch[:80], assigned_user_id=assigned.id if assigned else None, created_by=actor.id,
                          created_at=now)
        db.add(row)
        out.append((plain, row))
    db.flush()
    audit.record(db, actor, "codes.generate", "batch", batch, ip, code_type=kind.value, count=count,
                 device_allowance=device_allowance, expires_at=expires, note=note[:300],
                 assigned=assigned.email if assigned else None)
    return batch, out


def list_codes(db: Session, status: str = "", code_type: str = "", batch: str = "", q: str = "", page: int = 1,
               per_page: int = 50):
    query = select(LicenseCode).order_by(LicenseCode.created_at.desc(), LicenseCode.id)
    if status == "expired":
        query = query.where(LicenseCode.status.in_(("available", "reserved")), LicenseCode.expires_at <= utcnow())
    elif status:
        query = query.where(LicenseCode.status == status)
    if code_type:
        query = query.where(LicenseCode.code_type == code_type)
    if batch:
        query = query.where(LicenseCode.batch == batch)
    if q:
        text = q.strip().upper()
        body_hint = text.replace("-", "")[-4:]
        like = f"%{q.strip()}%"
        query = query.where(or_(LicenseCode.note.ilike(like), LicenseCode.batch.ilike(like),
                                LicenseCode.reserved_for.ilike(like), LicenseCode.hint == body_hint))
        # a full code typed into the search finds exactly that code
        try:
            query = select(LicenseCode).where(LicenseCode.verifier == codegen.verifier(codegen.normalize(q)))
        except codegen.CodeFormatError:
            pass
    return paginate(db, query, page, per_page)


def code_out(db: Session, row: LicenseCode) -> dict:
    assigned = db.get(User, row.assigned_user_id) if row.assigned_user_id else None
    redeemer = db.get(User, row.redeemed_by) if row.redeemed_by else None
    status = row.status
    if status in ("available", "reserved") and row.expires_at is not None and row.expires_at <= utcnow():
        status = "expired"
    return {"id": str(row.id), "display": row.display(), "code_type": row.code_type, "status": status,
            "device_allowance": row.device_allowance, "expires_at": row.expires_at, "note": row.note,
            "batch": row.batch, "assigned_email": assigned.email if assigned else None, "created_at": row.created_at,
            "redeemed_at": row.redeemed_at, "redeemed_by_email": redeemer.email if redeemer else None,
            "reserved_for": row.reserved_for}


def get_code(db: Session, code_id: str) -> LicenseCode:
    try:
        row = db.get(LicenseCode, uuid.UUID(code_id), with_for_update=True)
    except ValueError:
        row = None
    if row is None:
        raise not_found("code_not_found", "No such activation code.")
    return row


def revoke_code(db: Session, actor: User, code_id: str, ip: str = "") -> LicenseCode:
    require(actor, "revoke_codes")
    row = get_code(db, code_id)
    if row.status == "redeemed":
        raise conflict("code_used", "A redeemed code cannot be withdrawn; change the license instead.")
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at = utcnow()
        row.revoked_by = actor.id
        audit.record(db, actor, "codes.revoke", "code", row.id, ip, hint=row.hint, code_type=row.code_type)
    return row


def revoke_batch(db: Session, actor: User, batch: str, ip: str = "") -> int:
    require(actor, "revoke_codes")
    rows = list(db.scalars(select(LicenseCode).where(LicenseCode.batch == batch,
                                                     LicenseCode.status.in_(("available", "reserved")))
                           .with_for_update()))
    now = utcnow()
    for row in rows:
        row.status, row.revoked_at, row.revoked_by = "revoked", now, actor.id
    audit.record(db, actor, "codes.revoke_batch", "batch", batch, ip, count=len(rows))
    return len(rows)


def reserve_code(db: Session, actor: User, code_id: str, reserved_for: str, ip: str = "") -> LicenseCode:
    require(actor, "reserve_codes")
    row = get_code(db, code_id)
    if row.status not in ("available", "reserved"):
        raise conflict("code_not_available", "Only unused codes can be reserved.")
    row.status = "reserved"
    row.reserved_at = utcnow()
    row.reserved_for = reserved_for.strip()[:160]
    audit.record(db, actor, "codes.reserve", "code", row.id, ip, hint=row.hint, reserved_for=row.reserved_for)
    return row


def release_code(db: Session, actor: User, code_id: str, ip: str = "") -> LicenseCode:
    require(actor, "reserve_codes")
    row = get_code(db, code_id)
    if row.status != "reserved":
        raise conflict("code_not_reserved", "This code is not reserved.")
    row.status, row.reserved_at, row.reserved_for = "available", None, ""
    audit.record(db, actor, "codes.release", "code", row.id, ip, hint=row.hint)
    return row


def codes_csv(items: list[tuple[str, LicenseCode]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "type", "device_allowance", "expires_at", "batch", "note"])
    for plain, row in items:
        w.writerow([plain, row.code_type, row.device_allowance or "", row.expires_at.isoformat() if row.expires_at
                    else "", row.batch, _csv_safe(row.note)])
    return buf.getvalue()


def _csv_safe(text: str) -> str:
    """Spreadsheet programs run cells starting with = + - @ as formulas."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# -- users -----------------------------------------------------------------------------------------------------------
def list_users(db: Session, q: str = "", role: str = "", status: str = "", license_state: str = "", page: int = 1,
               per_page: int = 50):
    query = select(User).order_by(User.created_at.desc(), User.id)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(User.email_normalized.like(like), func.lower(User.display_name).like(like)))
        try:
            query = select(User).where(User.id == uuid.UUID(q.strip())) if len(q.strip()) == 36 else query
        except ValueError:
            pass
    if role:
        query = query.where(User.role == role)
    if status:
        query = query.where(User.status == status)
    now = utcnow()
    if license_state == "none":
        query = query.where(~User.id.in_(select(License.user_id)))
    elif license_state == "lifetime":
        query = query.where(User.id.in_(select(License.user_id).where(License.license_type == "lifetime")))
    elif license_state == "active":
        query = query.where(User.id.in_(select(License.user_id).where(
            License.status == "active", or_(License.expires_at.is_(None), License.expires_at > now))))
    elif license_state == "expired":
        query = query.where(User.id.in_(select(License.user_id).where(License.expires_at <= now)))
    return paginate(db, query, page, per_page)


def user_summary(db: Session, user: User) -> dict:
    lic = lic_service.license_of(db, user.id)
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name, "role": user.role,
            "status": user.status, "email_verified": user.email_verified_at is not None,
            "two_factor": bool(user.credential.totp_enabled_at), "created_at": user.created_at,
            "last_login_at": user.last_login_at, "license": lic_service.describe(lic, utcnow()),
            "devices": len(lic_service.active_devices(db, user.id))}


def user_detail(db: Session, user: User) -> dict:
    out = user_summary(db, user)
    lic = lic_service.license_of(db, user.id)
    out["notes"] = user.notes
    out["disabled_reason"] = user.disabled_reason
    out["locked_until"] = user.locked_until
    out["devices_list"] = [device_out(d) for d in db.scalars(
        select(Device).where(Device.user_id == user.id).order_by(Device.created_at.desc()))]
    out["sessions"] = [session_out(s) for s in db.scalars(
        select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.created_at.desc()).limit(30))]
    out["history"] = license_history(db, lic) if lic else []
    out["sign_ins"] = [{"at": e.at, "success": e.success, "reason": e.reason, "channel": e.channel, "ip": e.ip}
                       for e in db.scalars(select(SignInEvent).where(SignInEvent.user_id == user.id)
                                           .order_by(SignInEvent.at.desc()).limit(30))]
    return out


def license_history(db: Session, lic: License) -> list[dict]:
    return [{"at": e.at, "kind": e.kind, "old_type": e.old_type, "new_type": e.new_type,
             "old_expires_at": e.old_expires_at, "new_expires_at": e.new_expires_at, "note": e.note}
            for e in db.scalars(select(LicenseEvent).where(LicenseEvent.license_id == lic.id)
                                .order_by(LicenseEvent.at.desc(), LicenseEvent.id.desc()))]


def device_out(d: Device, current: uuid.UUID | None = None) -> dict:
    return {"id": str(d.id), "label": d.label, "os": d.os, "app_version": d.app_version, "created_at": d.created_at,
            "last_seen_at": d.last_seen_at, "active": d.active, "current": d.id == current,
            "fingerprint": d.fingerprint[:12], "revoked_by": d.revoked_by}


def session_out(s: UserSession) -> dict:
    return {"id": str(s.id), "kind": s.kind, "created_at": s.created_at, "last_used_at": s.last_used_at,
            "expires_at": s.expires_at, "revoked_at": s.revoked_at, "ip": s.ip, "user_agent": s.user_agent,
            "active": s.live(utcnow())}


def patch_user(db: Session, actor: User, user: User, ip: str = "", role: str | None = None,
               status: str | None = None, disabled_reason: str | None = None, notes: str | None = None,
               display_name: str | None = None) -> User:
    require(actor, "view")
    changes = {}
    if notes is not None and notes != user.notes:
        require(actor, "notes")
        user.notes = notes
        changes["notes"] = "changed"
    if display_name is not None and display_name != user.display_name:
        require(actor, "notes")
        _guard_target(actor, user)
        changes["display_name"] = [user.display_name, display_name]
        user.display_name = display_name.strip()[:80]
    if role is not None and role != user.role:
        require(actor, "change_role")
        if user.id == actor.id:
            raise bad_request("own_role", "You cannot change your own role.")
        if user.role == Role.SUPER_ADMIN.value and _super_admins(db) <= 1:
            raise conflict("last_super_admin", "The last super admin cannot be demoted.")
        changes["role"] = [user.role, role]
        user.role = Role(role).value
        auth.revoke_all(db, user.id, "role_changed")
    if status is not None and status != user.status:
        require(actor, "user_status")
        _guard_target(actor, user)
        if user.id == actor.id:
            raise bad_request("own_status", "You cannot disable your own account.")
        if status == "disabled" and user.role == Role.SUPER_ADMIN.value and _super_admins(db) <= 1:
            raise conflict("last_super_admin", "The last super admin cannot be disabled.")
        changes["status"] = [user.status, status]
        user.status = status
        user.disabled_reason = (disabled_reason or "").strip()[:300] if status == "disabled" else ""
        if status == "disabled":
            auth.revoke_all(db, user.id, "account_disabled")
    if changes:
        audit.record(db, actor, "users.update", "user", user.id, ip, email=user.email, **changes)
    return user


def _super_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN.value,
                                                                  User.status == "active")) or 0


def extend(db: Session, actor: User, user: User, hours: int, note: str = "", ip: str = "") -> License:
    require(actor, "extend")
    _guard_target(actor, user)
    lic = lic_service.admin_extend(db, actor, user, hours * 3600, note)
    audit.record(db, actor, "license.extend", "user", user.id, ip, email=user.email, hours=hours, note=note,
                 expires_at=lic.expires_at)
    return lic


def set_license(db: Session, actor: User, user: User, license_type: str, expires_at=None, note: str = "",
                ip: str = "") -> License:
    kind = LicenseType(license_type)
    require(actor, "grant_lifetime" if kind is LicenseType.LIFETIME else "set_license")
    _guard_target(actor, user)
    lic = lic_service.admin_set_type(db, actor, user, kind, expires_at, note)
    audit.record(db, actor, "license.grant_lifetime" if kind is LicenseType.LIFETIME else "license.set_type",
                 "user", user.id, ip, email=user.email, license_type=kind.value, expires_at=lic.expires_at, note=note)
    return lic


def license_status(db: Session, actor: User, user: User, status: str, note: str = "", ip: str = "") -> License:
    require(actor, "license_status")
    _guard_target(actor, user)
    lic = lic_service.admin_set_status(db, actor, user, status, note)
    audit.record(db, actor, f"license.{status}", "user", user.id, ip, email=user.email, note=note)
    return lic


def reset_devices(db: Session, actor: User, user: User, ip: str = "") -> int:
    require(actor, "reset_devices")
    _guard_target(actor, user)
    n = 0
    for d in lic_service.active_devices(db, user.id):
        lic_service.revoke_device(db, d, "reset")
        n += 1
    audit.record(db, actor, "devices.reset", "user", user.id, ip, email=user.email, count=n)
    return n


def revoke_sessions(db: Session, actor: User, user: User, ip: str = "") -> int:
    require(actor, "revoke_sessions")
    _guard_target(actor, user)
    n = auth.revoke_all(db, user.id, "revoked_by_staff")
    audit.record(db, actor, "sessions.revoke_all", "user", user.id, ip, email=user.email, count=n)
    return n


def send_reset(db: Session, actor: User, user: User, ip: str = "", base: str = "") -> None:
    require(actor, "send_reset")
    _guard_target(actor, user)
    auth.request_reset(db, user.email, ip, base=base)
    audit.record(db, actor, "users.send_password_reset", "user", user.id, ip, email=user.email)


def resend_verification(db: Session, actor: User, user: User, ip: str = "", base: str = "") -> None:
    require(actor, "resend_verification")
    if user.email_verified_at is None:
        auth.send_verification(db, user, base=base)
        audit.record(db, actor, "users.resend_verification", "user", user.id, ip, email=user.email)


def disable_2fa(db: Session, actor: User, user: User, ip: str = "") -> None:
    require(actor, "disable_2fa")
    if user.id == actor.id:
        raise bad_request("own_2fa", "Use your own security page to change your two-factor sign-in.")
    totp.disable(user.credential)
    auth.revoke_all(db, user.id, "2fa_reset")
    audit.record(db, actor, "users.disable_2fa", "user", user.id, ip, email=user.email)


def users_csv(db: Session, actor: User, ip: str = "") -> str:
    require(actor, "export")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "email", "display_name", "role", "status", "email_verified", "created_at", "last_login_at",
                "license_type", "license_status", "expires_at", "devices"])
    now = utcnow()
    for user in db.scalars(select(User).order_by(User.created_at)):
        lic = lic_service.license_of(db, user.id)
        d = lic_service.describe(lic, now)
        w.writerow([user.id, _csv_safe(user.email), _csv_safe(user.display_name), user.role, user.status,
                    user.email_verified_at is not None, user.created_at.isoformat(),
                    user.last_login_at.isoformat() if user.last_login_at else "", d.get("license_type") or "",
                    d["status"], d["expires_at"].isoformat() if d.get("expires_at") else "",
                    len(lic_service.active_devices(db, user.id))])
    audit.record(db, actor, "users.export", "users", "", ip)
    return buf.getvalue()


# -- devices ---------------------------------------------------------------------------------------------------------
def list_devices(db: Session, q: str = "", page: int = 1, per_page: int = 50, active_only: bool = False):
    query = select(Device).order_by(Device.last_seen_at.desc(), Device.id)
    if active_only:
        query = query.where(Device.revoked_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.join(User, User.id == Device.user_id).where(or_(
            func.lower(Device.label).like(like), User.email_normalized.like(like), Device.fingerprint.like(like)))
    return paginate(db, query, page, per_page)


def get_device(db: Session, device_id: str) -> Device:
    try:
        dev = db.get(Device, uuid.UUID(device_id))
    except ValueError:
        dev = None
    if dev is None:
        raise not_found("device_not_found", "No such device.")
    return dev


def admin_revoke_device(db: Session, actor: User, device: Device, ip: str = "") -> None:
    require(actor, "revoke_device")
    owner = db.get(User, device.user_id)
    _guard_target(actor, owner)
    lic_service.revoke_device(db, device, "admin")
    audit.record(db, actor, "devices.revoke", "device", device.id, ip, email=owner.email, label=device.label)


def admin_unblock_device(db: Session, actor: User, device: Device, ip: str = "") -> None:
    """Let a PC an administrator removed register again (it still needs a free device slot)."""
    require(actor, "revoke_device")
    if device.revoked_by == "admin":
        device.revoked_by = "unblocked"
        audit.record(db, actor, "devices.unblock", "device", device.id, ip, label=device.label)


def admin_rename_device(db: Session, actor: User, device: Device, label: str, ip: str = "") -> None:
    require(actor, "rename_device")
    old = device.label
    device.label = label.strip()[:80] or old
    audit.record(db, actor, "devices.rename", "device", device.id, ip, old=old, new=device.label)


def transfer_device(db: Session, actor: User, device: Device, email: str, ip: str = "") -> Device:
    """Move a PC to another account (for example after a family member got their own account)."""
    require(actor, "transfer_device")
    target = auth.user_by_email(db, email)
    if target is None:
        raise not_found("user_not_found", "No account with this e-mail address.")
    if target.id == device.user_id:
        raise bad_request("same_account", "The device already belongs to this account.")
    source = db.get(User, device.user_id)
    lic_service.revoke_device(db, device, "transfer")
    moved = lic_service.register_device(db, target, device.fingerprint, device.label, device.os, device.app_version)
    audit.record(db, actor, "devices.transfer", "device", moved.id, ip, from_email=source.email if source else "",
                 to_email=target.email)
    return moved


# -- audit and statistics --------------------------------------------------------------------------------------------
def list_audit(db: Session, actor: User, action: str = "", q: str = "", page: int = 1, per_page: int = 50):
    require(actor, "audit")
    query = select(AdminAuditEvent).order_by(AdminAuditEvent.at.desc(), AdminAuditEvent.id.desc())
    if action:
        query = query.where(AdminAuditEvent.action.like(f"{action}%"))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(AdminAuditEvent.actor_email.ilike(like), AdminAuditEvent.target_id.ilike(like)))
    return paginate(db, query, page, per_page)


def audit_out(e: AdminAuditEvent) -> dict:
    return {"id": e.id, "at": e.at, "actor_email": e.actor_email, "actor_role": e.actor_role, "action": e.action,
            "target_type": e.target_type, "target_id": e.target_id, "ip": e.ip, "details": e.details}


def statistics(db: Session) -> dict:
    now = utcnow()

    def count(model, *where) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    running = or_(License.expires_at.is_(None), License.expires_at > now)
    by_type = {t.value: count(License, License.license_type == t.value, License.status == "active", running)
               for t in LicenseType}
    codes_by = {s: count(LicenseCode, LicenseCode.status == s) for s in ("available", "reserved", "redeemed",
                                                                        "revoked")}
    days = []
    for i in range(13, -1, -1):
        start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        days.append({"day": start.date().isoformat(),
                     "redemptions": count(LicenseRedemption, LicenseRedemption.redeemed_at >= start,
                                          LicenseRedemption.redeemed_at < start + timedelta(days=1)),
                     "registrations": count(User, User.created_at >= start,
                                            User.created_at < start + timedelta(days=1))})
    return {
        "users": count(User), "users_verified": count(User, User.email_verified_at.is_not(None)),
        "users_disabled": count(User, User.status == "disabled"),
        "licenses_active": sum(by_type.values()), "licenses_by_type": by_type,
        "licenses_expired": count(License, License.expires_at <= now),
        "licenses_disabled": count(License, License.status != "active"),
        "expiring_7_days": count(License, License.status == "active", License.expires_at > now,
                                 License.expires_at <= now + timedelta(days=7)),
        "codes": codes_by,
        "devices_active": count(Device, Device.revoked_at.is_(None)),
        "offline_certificates": count(OfflineCertificate, OfflineCertificate.revoked_at.is_(None)),
        "validations_24h": count(LicenseValidation, LicenseValidation.at > now - timedelta(hours=24)),
        "sign_ins_24h": count(SignInEvent, SignInEvent.success.is_(True), SignInEvent.at > now - timedelta(hours=24)),
        "failed_sign_ins_24h": count(SignInEvent, SignInEvent.success.is_(False),
                                     SignInEvent.at > now - timedelta(hours=24)),
        "daily": days, "server_time": now,
    }


def update_settings(db: Session, actor: User, values: dict, ip: str = "") -> dict:
    require(actor, "settings")
    changed = {}
    for key, value in values.items():
        old = settings.get(db, key)
        new = settings.put(db, key, value, actor.id)
        if new != old:
            changed[key] = [old, new]
    if changed:
        audit.record(db, actor, "settings.update", "settings", "", ip, **changed)
    return settings.all_settings(db)


def cleanup(db: Session) -> dict:
    """Retention: old validations, sign-in events, used tokens and long-ended sessions."""
    from akeno_server.models import EmailVerificationToken, PasswordResetToken
    days = settings.get(db, "retention_days")
    cutoff = utcnow() - timedelta(days=days)
    out = {}
    for name, model, col in (("validations", LicenseValidation, LicenseValidation.at),
                             ("sign_ins", SignInEvent, SignInEvent.at),
                             ("reset_tokens", PasswordResetToken, PasswordResetToken.expires_at),
                             ("verify_tokens", EmailVerificationToken, EmailVerificationToken.expires_at),
                             ("sessions", UserSession, UserSession.expires_at)):
        out[name] = db.query(model).filter(col < cutoff).delete(synchronize_session=False)
    return out
