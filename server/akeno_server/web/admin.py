"""The admin area (staff roles, two-factor sign-in required): statistics, activation codes, users, devices,
audit log, settings and system status. Every operation goes through akeno_server.admin.service (audited)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from akeno_server import __version__, mail, settings
from akeno_server.admin import service as admin
from akeno_server.api.deps import base_url, client_ip, limit
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import ApiError
from akeno_server.licensing import entitlements, signing
from akeno_server.models import LicenseType, Role
from akeno_server.web import account as account_pages
from akeno_server.web import session as web_session
from akeno_server.web.render import check_csrf, redirect, render

router = APIRouter(prefix="/admin", include_in_schema=False)
root_router = APIRouter(include_in_schema=False)


@root_router.get("/")
def admin_home(request: Request):
    """The admin host's start page is the admin area."""
    return redirect(request, "/admin")
TYPES = [t.value for t in LicenseType]


def _staff(request: Request, db: Session, minimum: Role = Role.SUPPORT):
    return web_session.need_staff(request, db, minimum)


def _page(request: Request, template: str, actor, status: int = 200, **ctx):
    return render(request, template, status=status, area="admin", can=lambda action: admin.can(actor, action),
                  section=ctx.pop("section", ""), **ctx)


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    stats = admin.statistics(db)
    peak = max([d["redemptions"] for d in stats["daily"]] + [d["registrations"] for d in stats["daily"]] + [1])
    return _page(request, "admin/dashboard.html", actor, stats=stats, peak=peak, section="dashboard",
                 maintenance=settings.get(db, "maintenance_mode"))


# -- staff security (two-factor enrolment) ---------------------------------------------------------------------------
@router.get("/security")
def security(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_staff(request, db, allow_enrolment=True)
    if not user.credential.totp_enabled_at:
        return account_pages.totp_setup_page(request, db, user, "account/totp_setup.html", "admin")
    if not sess.mfa_verified:
        return redirect(request, "/login?next=/admin", "Sign in again with your authenticator code.", "warn")
    from akeno_server.web.render import site_links
    return redirect(request, site_links()["account_href"] + "/security")


@router.post("/security")
async def security_confirm(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_staff(request, db, allow_enrolment=True)
    form = await request.form()
    check_csrf(request, form)
    if user.credential.totp_enabled_at:
        return redirect(request, "/admin")
    return account_pages.totp_confirm(request, db, user, sess, form, "account/totp_setup.html", "admin")


# -- activation codes ------------------------------------------------------------------------------------------------
@router.get("/codes")
def codes(request: Request, status: str = "", code_type: str = "", batch: str = "", q: str = "",
          entitlement: str = "", page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    rows, total = admin.list_codes(db, status, code_type, batch, q, page, 50, entitlement)
    base = f"/admin/codes?status={status}&code_type={code_type}&batch={batch}&q={q}&entitlement={entitlement}&"
    return _page(request, "admin/codes.html", actor, items=[admin.code_out(db, r) for r in rows], total=total,
                 page=page, filters={"status": status, "code_type": code_type, "batch": batch, "q": q,
                                     "entitlement": entitlement}, base=base,
                 types=TYPES, section="codes", default_devices=settings.get(db, "default_device_limit"),
                 products=_products(db), vpn_locked=not settings.get(db, "vpn_module_licenses"))


def _products(db: Session) -> list[dict]:
    """AKENO Full and every offered module; AKENO VPN is locked while its licenses are switched off."""
    vpn_on = settings.get(db, "vpn_module_licenses")
    return [{"id": e.id, "name": e.name, "locked": e.id == entitlements.VPN and not vpn_on}
            for e in entitlements.offered()]


LENGTHS = {"lifetime": ("lifetime", None), "30": ("monthly", None), "90": ("monthly", 90),
           "365": ("monthly", 365), "trial_24h": ("trial_24h", None), "trial_72h": ("trial_72h", None)}


def code_length(form) -> tuple[str, int | None]:
    """The form's duration as (code type, days): lifetime, 30/90/365 days, a custom number of days or a trial.
    A form of the earlier admin page (``code_type`` only) keeps working."""
    length = str(form.get("length", "")).strip()
    if not length:
        return str(form.get("code_type", "")), None
    if length == "custom":
        days = str(form.get("custom_days", "")).strip()
        if not days.isdigit():
            raise ValueError("custom duration")
        return "monthly", int(days)
    if length not in LENGTHS:
        raise ValueError("duration")
    return LENGTHS[length]


@router.post("/codes/generate")
async def generate(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    form = await request.form()
    check_csrf(request, form)

    def number(name: str) -> int | None:
        value = str(form.get(name, "")).strip()
        return int(value) if value.isdigit() else None
    try:
        limit(request, "admin_codes", str(actor.id))
        code_type, days = code_length(form)
        batch, items = admin.generate_codes(
            db, actor, code_type, number("count") or 1, number("device_allowance"),
            number("expires_in_days"), str(form.get("note", "")), str(form.get("batch", "")),
            str(form.get("assigned_email", "")).strip() or None, client_ip(request),
            entitlement=str(form.get("entitlement", "")).strip() or entitlements.SUITE, duration_days=days)
        db.commit()
    except (ApiError, ValueError) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, ApiError) else "Choose a license type."
        return redirect(request, "/admin/codes", message, "error")
    first = items[0][1]
    return _page(request, "admin/codes_generated.html", actor, batch=batch, codes=[p for p, _ in items],
                 csv=admin.codes_csv(items), code_type=first.code_type, section="codes",
                 product=entitlements.name(first.entitlement), length=admin.code_length(first))


@router.post("/codes/{code_id}/{action}")
async def code_action(code_id: str, action: str, request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    form = await request.form()
    check_csrf(request, form)
    ip = client_ip(request)
    try:
        if action == "revoke":
            admin.revoke_code(db, actor, code_id, ip)
        elif action == "reserve":
            admin.reserve_code(db, actor, code_id, str(form.get("reserved_for", "")) or "reserved", ip)
        elif action == "release":
            admin.release_code(db, actor, code_id, ip)
        else:
            return redirect(request, "/admin/codes", "Unknown action.", "error")
        db.commit()
    except ApiError as exc:
        db.rollback()
        return redirect(request, _back(request, "/admin/codes"), exc.message, "error")
    return redirect(request, _back(request, "/admin/codes"), "Saved.")


@router.post("/batches/revoke")
async def revoke_batch(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    form = await request.form()
    check_csrf(request, form)
    try:
        admin.revoke_batch(db, actor, str(form.get("batch", "")), client_ip(request))
        db.commit()
    except ApiError as exc:
        return redirect(request, "/admin/codes", exc.message, "error")
    return redirect(request, f"/admin/codes?batch={form.get('batch', '')}", "The unused codes of the batch were "
                    "withdrawn.")


def _back(request: Request, default: str) -> str:
    from urllib.parse import urlparse
    ref = request.headers.get("referer", "")
    parsed = urlparse(ref)
    if parsed.path.startswith("/admin") and (not parsed.netloc or parsed.netloc == request.headers.get("host")):
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return default


# -- users -----------------------------------------------------------------------------------------------------------
@router.get("/users")
def users(request: Request, q: str = "", role: str = "", status: str = "", license: str = "",
          page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    rows, total = admin.list_users(db, q, role, status, license, page, 50)
    base = f"/admin/users?q={q}&role={role}&status={status}&license={license}&"
    return _page(request, "admin/users.html", actor, items=[admin.user_summary(db, u) for u in rows], total=total,
                 page=page, base=base, filters={"q": q, "role": role, "status": status, "license": license},
                 section="users")


@router.get("/users/export.csv")
def export(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    try:
        data = admin.users_csv(db, actor, client_ip(request))
    except ApiError as exc:
        return redirect(request, "/admin/users", exc.message, "error")
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="akeno-users-{stamp}.csv"'})


@router.get("/users/{user_id}")
def user_detail(user_id: str, request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    target = admin.get_user(db, user_id)
    audit_rows = []
    if admin.can(actor, "audit"):
        rows, _ = admin.list_audit(db, actor, q=str(target.id), page=1, per_page=30)
        audit_rows = [admin.audit_out(e) for e in rows]
    detail = admin.user_detail(db, target)
    owned = {e["entitlement"]: e for e in detail["access"]["entitlements"]}
    modules = [p | {"entry": owned.get(p["id"])} for p in _products(db) if p["id"] != entitlements.SUITE]
    return _page(request, "admin/user.html", actor, target=target, detail=detail, modules=modules,
                 types=TYPES, audit=audit_rows, section="users")


@router.post("/users/{user_id}/action")
async def user_action(user_id: str, request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    form = await request.form()
    check_csrf(request, form)
    target = admin.get_user(db, user_id)
    action = str(form.get("action", ""))
    note = str(form.get("note", ""))[:300]
    ip = client_ip(request)
    done = "Saved."
    try:
        if action == "extend":
            hours = int(str(form.get("amount", "0")) or 0) * (24 if form.get("unit") == "days" else 1)
            admin.extend(db, actor, target, hours, note, ip)
        elif action == "set_license":
            expires = None
            raw = str(form.get("expires_at", "")).strip()
            if raw:
                expires = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
            admin.set_license(db, actor, target, str(form.get("license_type", "")), expires, note, ip)
        elif action == "grant_lifetime":
            admin.set_license(db, actor, target, "lifetime", None, note, ip)
        elif action in ("license_disable", "license_enable", "license_revoke"):
            admin.license_status(db, actor, target, {"license_disable": "disabled", "license_enable": "active",
                                                     "license_revoke": "revoked"}[action], note, ip)
        elif action == "module_grant":
            ent = str(form.get("entitlement", ""))
            length = str(form.get("length", "lifetime"))
            days = None if length == "lifetime" else int(str(form.get("days", "")) or length)
            admin.set_grant(db, actor, target, ent, days, note, ip)
        elif action == "module_extend":
            admin.extend_grant(db, actor, target, str(form.get("entitlement", "")),
                               int(str(form.get("days", "0")) or 0), note, ip)
        elif action in ("module_disable", "module_enable", "module_revoke"):
            admin.grant_status(db, actor, target, str(form.get("entitlement", "")),
                               {"module_disable": "disabled", "module_enable": "active",
                                "module_revoke": "revoked"}[action], note, ip)
        elif action in ("disable", "enable"):
            admin.patch_user(db, actor, target, ip, status="disabled" if action == "disable" else "active",
                             disabled_reason=note)
        elif action == "role":
            admin.patch_user(db, actor, target, ip, role=str(form.get("role", "")))
        elif action == "notes":
            admin.patch_user(db, actor, target, ip, notes=str(form.get("notes", ""))[:5000])
        elif action == "reset_devices":
            admin.reset_devices(db, actor, target, ip)
        elif action == "revoke_sessions":
            admin.revoke_sessions(db, actor, target, ip)
        elif action == "send_reset":
            admin.send_reset(db, actor, target, ip, base_url(request))
            done = "A password reset link was sent."
        elif action == "resend_verification":
            admin.resend_verification(db, actor, target, ip, base_url(request))
            done = "The confirmation e-mail was sent again."
        elif action == "disable_2fa":
            admin.disable_2fa(db, actor, target, ip)
        else:
            return redirect(request, f"/admin/users/{user_id}", "Unknown action.", "error")
        db.commit()
    except ApiError as exc:
        db.rollback()
        return redirect(request, f"/admin/users/{user_id}", exc.message, "error")
    except ValueError:
        db.rollback()
        return redirect(request, f"/admin/users/{user_id}", "Some fields are invalid.", "error")
    return redirect(request, f"/admin/users/{user_id}", done)


# -- devices ---------------------------------------------------------------------------------------------------------
@router.get("/devices")
def devices(request: Request, q: str = "", active: str = "", page: int = Query(1, ge=1),
            db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    rows, total = admin.list_devices(db, q, page, 50, active_only=active == "1")
    from akeno_server.models import User
    items = [admin.device_out(d) | {"user_id": str(d.user_id), "email": db.get(User, d.user_id).email} for d in rows]
    return _page(request, "admin/devices.html", actor, items=items, total=total, page=page,
                 base=f"/admin/devices?q={q}&active={active}&", filters={"q": q, "active": active},
                 section="devices")


@router.post("/devices/{device_id}/{action}")
async def device_action(device_id: str, action: str, request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db)
    form = await request.form()
    check_csrf(request, form)
    dev = admin.get_device(db, device_id)
    ip = client_ip(request)
    try:
        if action == "revoke":
            admin.admin_revoke_device(db, actor, dev, ip)
        elif action == "unblock":
            admin.admin_unblock_device(db, actor, dev, ip)
        elif action == "rename":
            admin.admin_rename_device(db, actor, dev, str(form.get("label", "")), ip)
        elif action == "transfer":
            admin.transfer_device(db, actor, dev, str(form.get("email", "")), ip)
        else:
            return redirect(request, "/admin/devices", "Unknown action.", "error")
        db.commit()
    except ApiError as exc:
        db.rollback()
        return redirect(request, _back(request, "/admin/devices"), exc.message, "error")
    return redirect(request, _back(request, "/admin/devices"), "Saved.")


# -- audit, settings, system -----------------------------------------------------------------------------------------
@router.get("/audit")
def audit_log(request: Request, action: str = "", q: str = "", page: int = Query(1, ge=1),
              db: Session = Depends(get_db)):
    actor, _ = _staff(request, db, Role.ADMIN)
    rows, total = admin.list_audit(db, actor, action, q, page, 50)
    return _page(request, "admin/audit.html", actor, items=[admin.audit_out(e) for e in rows], total=total,
                 page=page, base=f"/admin/audit?action={action}&q={q}&", filters={"action": action, "q": q},
                 section="audit")


SETTING_LABELS = {
    "registration_enabled": "New accounts can register",
    "require_email_verification": "Codes can be redeemed only after the e-mail address is confirmed",
    "default_device_limit": "PCs per license (unless a code allows more)",
    "device_removals_per_30_days": "PCs a user may remove per 30 days",
    "trial_once_per_account": "Each trial length once per account",
    "validation_interval_hours": "Online check interval of the app (hours)",
    "offline_grace_hours": "Offline grace period of time-limited licenses (hours)",
    "maintenance_mode": "Maintenance mode (the app keeps working with saved licenses)",
    "maintenance_message": "Maintenance message",
    "min_client_version": "Minimum app version (empty: any)",
    "latest_client_version": "Latest app version (shown in the app)",
    "retention_days": "Keep validations, sign-in events and old sessions for (days)",
    "vpn_module_licenses": "AKENO VPN licenses can be issued (only after the use of VPN Gate is cleared; super admin)",
}


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db, Role.ADMIN)
    return _page(request, "admin/settings.html", actor, values=settings.all_settings(db), labels=SETTING_LABELS,
                 kinds={k: v[1].__name__ for k, v in settings.DEFAULTS.items()}, section="settings")


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db, Role.ADMIN)
    form = await request.form()
    check_csrf(request, form)
    values = {}
    for key, (_, kind, _, _) in settings.DEFAULTS.items():
        values[key] = key in form if kind is bool else str(form.get(key, ""))
    try:
        admin.update_settings(db, actor, values, client_ip(request))
        db.commit()
    except settings.SettingError as exc:
        db.rollback()
        return redirect(request, "/admin/settings", str(exc), "error")
    return redirect(request, "/admin/settings", "Saved.")


@router.get("/system")
def system(request: Request, db: Session = Depends(get_db)):
    actor, _ = _staff(request, db, Role.ADMIN)
    cfg = get_config()
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = ("ok", db.get_bind().dialect.name)
    except Exception as exc:  # shown, not raised
        checks["database"] = ("fail", str(exc))
    keys = signing.public_keys(db)
    active = signing.store().active_kid()
    checks["signing_key"] = ("ok", active) if active else ("warn", "only the API service holds the private key")
    checks["mail"] = ("ok" if mail.configured() and cfg.mail_backend == "smtp" else "warn",
                      cfg.mail_backend + (f" · {cfg.smtp_host}:{cfg.smtp_port}" if cfg.mail_backend == "smtp" else ""))
    backup = None
    status_file = Path("/backups/last-backup.json")
    if status_file.exists():
        try:
            backup = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            backup = None
    checks["backup"] = ("ok", backup.get("finished_at", "")) if backup else ("warn", "no backup status found")
    return _page(request, "admin/system.html", actor, checks=checks, keys=keys, active=active, backup=backup,
                 cfg={"environment": cfg.environment, "role": cfg.role, "public_url": cfg.public_url or "—",
                      "admin_url": cfg.admin_url or "—", "server_version": __version__}, section="system")
