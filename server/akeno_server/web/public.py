"""Public pages: landing page, sign-in (with the two-factor step), registration, password reset, e-mail links."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from akeno_server import settings
from akeno_server.api.deps import base_url, client_ip, limit, user_agent
from akeno_server.auth import service as auth
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import ApiError
from akeno_server.models import User
from akeno_server.web import session as web_session
from akeno_server.web.render import (PENDING_COOKIE, SESSION_COOKIE, check_csrf, pending_token, read_pending,
                                     redirect, render, safe_next, set_cookie)

auth_router = APIRouter(include_in_schema=False)
REGISTERED = "Account created. We sent you an e-mail to confirm your address."
REGISTERED_NO_MAIL = ("Account created, but the confirmation e-mail could not be sent. Request a new one on this "
                      "page later.")
router = APIRouter(include_in_schema=False)


def _home_for(user: User) -> str:
    return "/admin" if user.role_enum.privileged else "/account"


def _signed_in(request: Request, db: Session, user: User, mfa: bool, target: str | None, flash: str | None = None):
    issued = auth.start_session(db, user, "web", client_ip(request), user_agent(request), mfa=mfa)
    db.commit()
    response = redirect(request, safe_next(target, _home_for(user)), flash)
    set_cookie(response, SESSION_COOKIE, issued.token, max_age=get_config().web_session_hours * 3600)
    response.delete_cookie(PENDING_COOKIE, path="/")
    return response


@auth_router.get("/login")
def login_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    found = web_session.load(request, db)
    if found:
        return redirect(request, safe_next(next, _home_for(found[0])))
    return render(request, "auth/login.html", next=next, registration=settings.get(db, "registration_enabled"),
                  email="")


@auth_router.post("/login")
async def login(request: Request, next: str = "", db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form)
    email = str(form.get("email", "")).strip()
    try:
        limit(request, "login")
        limit(request, "login", "email:" + auth.normalize_email(email))
        user = auth.authenticate(db, email, str(form.get("password", "")), None, "web", client_ip(request),
                                 user_agent(request))
    except ApiError as exc:
        if exc.code == "totp_required":
            user = auth.user_by_email(db, email)
            response = redirect(request, "/login/verify")
            set_cookie(response, PENDING_COOKIE, pending_token(str(user.id), safe_next(next, "")), max_age=300,
                       same_site="strict")
            return response
        return render(request, "auth/login.html", status=exc.status, error=exc.message, next=next, email=email,
                      registration=settings.get(db, "registration_enabled"))
    return _signed_in(request, db, user, mfa=False, target=next)


@auth_router.get("/login/verify")
def verify_page(request: Request):
    if not read_pending(request.cookies.get(PENDING_COOKIE, "")):
        return redirect(request, "/login")
    return render(request, "auth/totp.html")


@auth_router.post("/login/verify")
async def verify(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form)
    pending = read_pending(request.cookies.get(PENDING_COOKIE, ""))
    if not pending:
        return redirect(request, "/login", "The sign-in took too long. Please sign in again.", "error")
    user = db.get(User, uuid.UUID(pending[0]))
    if user is None:
        return redirect(request, "/login")
    try:
        limit(request, "login", f"totp:{user.id}")
        auth.second_factor(db, user, str(form.get("code", "")), "web", client_ip(request), user_agent(request))
    except ApiError as exc:
        return render(request, "auth/totp.html", status=exc.status, error=exc.message)
    return _signed_in(request, db, user, mfa=True, target=pending[1] or None)


@auth_router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    found = web_session.load(request, db)
    form = await request.form()
    if found:
        check_csrf(request, form)
        auth.revoke(found[1], "signed_out")
        db.commit()
    response = redirect(request, "/", "You are signed out.")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    web_session.load(request, db)
    return render(request, "index.html")


@router.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    if web_session.load(request, db):
        return redirect(request, "/account")
    return render(request, "auth/register.html", registration=settings.get(db, "registration_enabled"), form={})


@router.post("/register")
async def register(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form)
    values = {k: str(form.get(k, "")) for k in ("display_name", "email", "password", "password2", "language")}
    allowed = settings.get(db, "registration_enabled")
    try:
        limit(request, "register")
        auth.registration_allowed(db)
        if values["password"] != values["password2"]:
            raise ApiError(400, "password_mismatch", "The passwords do not match.")
        user = auth.create_user(db, values["email"], values["password"], values["display_name"],
                                values["language"])
        sent = auth.delivery(auth.send_verification(db, user, base=base_url(request))) == "sent"
        user.last_login_at = user.created_at
    except ApiError as exc:
        return render(request, "auth/register.html", status=exc.status, error=exc.message, registration=allowed,
                      form={"display_name": values["display_name"], "email": values["email"]})
    return _signed_in(request, db, user, mfa=False, target="/account",
                      flash=REGISTERED if sent else REGISTERED_NO_MAIL)


@router.get("/forgot-password")
def forgot_page(request: Request):
    return render(request, "auth/forgot.html", sent=False)


@router.post("/forgot-password")
async def forgot(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form)
    email = str(form.get("email", ""))
    try:
        limit(request, "reset_ip")
        limit(request, "reset_email", "email:" + auth.normalize_email(email))
        auth.request_reset(db, email, client_ip(request), base=base_url(request))
        db.commit()
    except ApiError as exc:
        if exc.code != "rate_limited":
            raise
    return render(request, "auth/forgot.html", sent=True)


@router.get("/reset-password")
def reset_page(request: Request, token: str = ""):
    return render(request, "auth/reset.html", token=token)


@router.post("/reset-password")
async def reset(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form)
    token = str(form.get("token", ""))
    try:
        limit(request, "verify")
        if form.get("password") != form.get("password2"):
            raise ApiError(400, "password_mismatch", "The passwords do not match.")
        auth.confirm_reset(db, token, str(form.get("password", "")), base=base_url(request))
        db.commit()
    except ApiError as exc:
        return render(request, "auth/reset.html", status=exc.status, error=exc.message, token=token)
    response = redirect(request, "/login", "Your password was changed. Sign in with the new password.")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/verify-email")
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    web_session.load(request, db)
    try:
        limit(request, "verify")
        user = auth.verify_email(db, token)
        db.commit()
    except ApiError as exc:
        return render(request, "message.html", status=exc.status, title="This link is invalid or has expired.",
                      text="Request a new e-mail from your account page.", link=("/account", "My account"))
    return render(request, "message.html", title="E-mail address confirmed.",
                  text=f"{user.email}", link=("/account", "My account"))


router.include_router(auth_router)
