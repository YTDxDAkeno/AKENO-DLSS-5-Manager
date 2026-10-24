"""The account area: license and countdown, redeeming codes, PCs, security (password, e-mail, sessions, 2FA)."""

from __future__ import annotations

import io
import uuid

import segno
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from akeno_server import mail
from akeno_server.admin.service import device_out, license_history, session_out
from akeno_server.api.deps import base_url, client_ip, limit
from akeno_server.auth import passwords, totp
from akeno_server.auth import service as auth
from akeno_server.auth.secretbox import seal, unseal
from akeno_server.clock import utcnow
from akeno_server.db import get_db
from akeno_server.errors import ApiError
from akeno_server.licensing import service as lic
from akeno_server.models import Device, SignInEvent, User, UserSession
from akeno_server.web import session as web_session
from akeno_server.web.render import check_csrf, redirect, render, set_cookie

router = APIRouter(prefix="/account", include_in_schema=False)
ENROL_COOKIE = "akeno_totp_enrol"


def _context(db: Session, user: User) -> dict:
    license_ = lic.license_of(db, user.id)
    return {"license": lic.describe(license_, utcnow()), "history": license_history(db, license_)[:12] if license_
            else [], "devices": [device_out(d) for d in lic.active_devices(db, user.id)]}


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    return render(request, "account/dashboard.html", area="account", **_context(db, user),
                  needs_verification=auth.needs_verification(db, user))


@router.post("/redeem")
async def redeem(request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    try:
        limit(request, "redeem_ip")
        limit(request, "redeem_user", str(user.id))
        if auth.needs_verification(db, user):
            raise ApiError(403, "email_not_verified", "Confirm your e-mail address before redeeming a code.")
        done = lic.redeem(db, user, str(form.get("code", "")), client_ip(request), "web")
    except ApiError as exc:
        db.rollback()
        return render(request, "account/dashboard.html", status=exc.status, area="account", redeem_error=exc.message,
                      code=str(form.get("code", "")), **_context(db, user),
                      needs_verification=auth.needs_verification(db, user))
    if done.already:
        return redirect(request, "/account", "You had already redeemed this code. Nothing changed.", "warn")
    return redirect(request, "/account", "Code redeemed. Your license was updated.")


def _own_device(db: Session, user: User, device_id: str) -> Device:
    dev = lic.find_device(db, user, device_id)
    if dev is None:
        raise ApiError(404, "device_not_found", "This PC is not registered on the account.")
    return dev


@router.post("/devices/{device_id}/rename")
async def rename_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    dev = _own_device(db, user, device_id)
    label = str(form.get("label", "")).strip()[:80]
    if label:
        dev.label = label
        db.commit()
    return redirect(request, "/account", "Saved.")


@router.post("/devices/{device_id}/remove")
async def remove_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    dev = _own_device(db, user, device_id)
    try:
        lic.user_remove_device(db, user, dev)
        db.commit()
    except ApiError as exc:
        return redirect(request, "/account", exc.message, "error")
    return redirect(request, "/account", "The PC was removed. It can sign in again while a slot is free.")


# -- security --------------------------------------------------------------------------------------------------------
def _security(db: Session, user: User, sess: UserSession) -> dict:
    sessions = [session_out(s) | {"current": s.id == sess.id} for s in db.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
                                  UserSession.expires_at > utcnow()).order_by(UserSession.last_used_at.desc()))]
    sign_ins = list(db.scalars(select(SignInEvent).where(SignInEvent.user_id == user.id)
                               .order_by(SignInEvent.at.desc()).limit(20)))
    return {"sessions": sessions, "sign_ins": sign_ins, "two_factor": bool(user.credential.totp_enabled_at),
            "recovery_left": len(user.credential.recovery_codes or [])}


def security_page(request: Request, db: Session, user: User, sess: UserSession, template: str, area: str,
                  status: int = 200, **extra):
    return render(request, template, status=status, area=area, **_security(db, user, sess), **extra)


@router.get("/security")
def security(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    return security_page(request, db, user, sess, "account/security.html", "account")


@router.post("/profile")
async def profile(request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    user.display_name = str(form.get("display_name", "")).strip()[:80]
    if form.get("language") in ("en", "de"):
        user.language = str(form["language"])
    db.commit()
    response = redirect(request, "/account/security", "Saved.")
    set_cookie(response, "akeno_lang", user.language, max_age=3600 * 24 * 365, http_only=False)
    return response


@router.post("/password")
async def change_password(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    try:
        limit(request, "login", f"pw:{user.id}")
        if form.get("new_password") != form.get("new_password2"):
            raise ApiError(400, "password_mismatch", "The passwords do not match.")
        auth.change_password(db, user, str(form.get("current_password", "")), str(form.get("new_password", "")),
                             sess.id, base=base_url(request))
        db.commit()
    except ApiError as exc:
        db.rollback()
        return security_page(request, db, user, sess, "account/security.html", "account", exc.status,
                             password_error=exc.message)
    return redirect(request, "/account/security", "Password changed. Your other sign-ins were ended.")


@router.post("/email")
async def change_email(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    try:
        limit(request, "reset_email", f"email-change:{user.id}")
        if not mail.deliverable():
            raise auth.email_unavailable()
        sent = auth.change_email(db, user, str(form.get("password", "")), str(form.get("email", "")),
                                 base=base_url(request))
        db.commit()
    except ApiError as exc:
        db.rollback()
        return security_page(request, db, user, sess, "account/security.html", "account", exc.status,
                             email_error=exc.message)
    if not sent:
        return redirect(request, "/account/security", "The confirmation e-mail could not be sent. Please try again "
                        "later.", "error")
    return redirect(request, "/account/security", "We sent a confirmation link to the new address. The change "
                    "takes effect when you open it.")


@router.post("/verify-email")
async def resend_verification(request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    if user.email_verified_at is None:
        try:
            limit(request, "reset_email", f"verify:{user.id}")
            if not mail.deliverable():
                raise auth.email_unavailable()
        except ApiError as exc:
            return redirect(request, "/account", exc.message, "error")
        sent = auth.send_verification(db, user, base=base_url(request))
        db.commit()
        if not sent:
            return redirect(request, "/account", "The confirmation e-mail could not be sent. Please try again "
                            "later.", "error")
    return redirect(request, "/account", "We sent you a new confirmation e-mail.")


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    try:
        target = db.get(UserSession, uuid.UUID(session_id))
    except ValueError:
        target = None
    if target is not None and target.user_id == user.id:
        auth.revoke(target, "revoked_by_user")
        db.commit()
    if target is not None and target.id == sess.id:
        return redirect(request, "/login", "You are signed out.")
    return redirect(request, "/account/security", "The sign-in was ended.")


@router.post("/sessions/revoke-others")
async def revoke_others(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    n = auth.revoke_all(db, user.id, "revoked_by_user", keep=sess.id)
    db.commit()
    return redirect(request, "/account/security", "Your other sign-ins were ended." if n else "No other sign-ins.")


# -- two-factor sign-in ----------------------------------------------------------------------------------------------
def _qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=1, xmldecl=False, svgns=True,
                                    dark="#060d1c", light="#ffffff")
    return buf.getvalue().decode()


def totp_setup_page(request: Request, db: Session, user: User, template: str, area: str, error: str = "",
                    status: int = 200):
    secret = totp.new_secret()
    response = render(request, template, status=status, area=area, secret=secret, error=error,
                      qr=_qr_svg(totp.provisioning_uri(secret, user.email)), staff=user.role_enum.privileged)
    # the secret waits in an encrypted, short-lived cookie until the first code proves the app has it
    set_cookie(response, ENROL_COOKIE, seal(f"{user.id}|{secret}", b"totp-enrol"), max_age=900, same_site="strict")
    return response


def totp_confirm(request: Request, db: Session, user: User, sess: UserSession, form, template: str, area: str):
    try:
        uid, secret = unseal(request.cookies.get(ENROL_COOKIE, ""), b"totp-enrol").split("|", 1)
    except Exception:                       # missing, expired or tampered cookie
        uid, secret = "", ""
    if uid != str(user.id) or not secret:
        return totp_setup_page(request, db, user, template, area, "The set-up took too long. Scan the new code.",
                               400)
    if not totp.check_new(secret, str(form.get("code", ""))):
        return totp_setup_page(request, db, user, template, area,
                               "The code did not match. Scan the new QR code and try again.", 400)
    codes = totp.enable(user.credential, secret)
    sess.mfa_verified = True
    auth.revoke_all(db, user.id, "2fa_enabled", keep=sess.id)
    db.commit()
    response = render(request, "account/recovery.html", area=area, codes=codes,
                      next_url="/admin" if user.role_enum.privileged else "/account/security")
    response.delete_cookie(ENROL_COOKIE, path="/")
    return response


@router.get("/2fa/setup")
def totp_setup(request: Request, db: Session = Depends(get_db)):
    user, _ = web_session.need_user(request, db)
    if user.credential.totp_enabled_at:
        return redirect(request, "/account/security", "Two-factor sign-in is already on.", "warn")
    return totp_setup_page(request, db, user, "account/totp_setup.html", "account")


@router.post("/2fa/setup")
async def totp_setup_confirm(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    return totp_confirm(request, db, user, sess, form, "account/totp_setup.html", "account")


@router.post("/2fa/disable")
async def totp_disable(request: Request, db: Session = Depends(get_db)):
    user, sess = web_session.need_user(request, db)
    form = await request.form()
    check_csrf(request, form)
    if user.role_enum.privileged:
        return redirect(request, "/account/security", "Staff accounts must keep two-factor sign-in.", "error")
    if not passwords.verify_password(user.credential.password_hash, str(form.get("password", ""))) or \
            not totp.verify(user.credential, str(form.get("code", ""))):
        db.commit()
        return redirect(request, "/account/security", "Password or code is wrong.", "error")
    totp.disable(user.credential)
    db.commit()
    return redirect(request, "/account/security", "Two-factor sign-in is off.")
