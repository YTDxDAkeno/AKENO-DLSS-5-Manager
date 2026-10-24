"""``/api/v1/auth``: accounts and sign-in for the Windows app."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from akeno_server.api import schemas
from akeno_server.api.common import user_out
from akeno_server.api.deps import Caller, base_url, client_ip, current, limit, not_in_maintenance, user_agent
from akeno_server.auth import service as auth
from akeno_server.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(not_in_maintenance)])


def _tokens(issued: auth.IssuedSession, user) -> dict:
    return {"access_token": issued.access_token, "token_type": "bearer", "expires_in": issued.expires_in,
            "refresh_token": issued.token, "user": user_out(user)}


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=schemas.TokensOut)
def register(body: schemas.RegisterIn, request: Request, db: Session = Depends(get_db)):
    limit(request, "register")
    auth.registration_allowed(db)
    user = auth.create_user(db, body.email, body.password, body.display_name, body.language)
    auth.send_verification(db, user, base=base_url(request))
    issued = auth.start_session(db, user, "app", client_ip(request), user_agent(request))
    db.commit()
    return _tokens(issued, user)


@router.post("/login", response_model=schemas.TokensOut)
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    limit(request, "login")
    limit(request, "login", "email:" + auth.normalize_email(body.email))
    user = auth.authenticate(db, body.email, body.password, body.totp_code, "app", ip, user_agent(request))
    issued = auth.start_session(db, user, "app", ip, user_agent(request), mfa=bool(user.credential.totp_enabled_at))
    db.commit()
    return _tokens(issued, user)


@router.post("/refresh", response_model=schemas.TokensOut)
def refresh(body: schemas.RefreshIn, request: Request, db: Session = Depends(get_db)):
    limit(request, "refresh")
    user, issued = auth.refresh(db, body.refresh_token, client_ip(request))
    db.commit()
    return _tokens(issued, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: schemas.LogoutIn, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    sess = db.merge(caller.session)
    auth.revoke(sess, "signed_out")
    db.commit()


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def reset_request(body: schemas.EmailIn, request: Request, db: Session = Depends(get_db)):
    limit(request, "reset_ip")
    limit(request, "reset_email", "email:" + auth.normalize_email(body.email))
    auth.request_reset(db, body.email, client_ip(request), base=base_url(request))
    db.commit()
    return {"status": "sent_if_account_exists"}


@router.post("/password-reset/confirm")
def reset_confirm(body: schemas.ResetConfirmIn, request: Request, db: Session = Depends(get_db)):
    limit(request, "verify")
    auth.confirm_reset(db, body.token, body.new_password, base=base_url(request))
    db.commit()
    return {"status": "password_changed"}


@router.post("/verify-email")
def verify_email(body: schemas.TokenIn, request: Request, db: Session = Depends(get_db)):
    limit(request, "verify")
    user = auth.verify_email(db, body.token)
    db.commit()
    return {"status": "verified", "email": user.email}


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
def resend(request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    limit(request, "reset_email", "verify:" + str(caller.user.id))
    user = db.merge(caller.user)
    if user.email_verified_at is None:
        auth.send_verification(db, user, base=base_url(request))
        db.commit()
    return {"status": "sent" if user.email_verified_at is None else "already_verified"}


@router.get("/me", response_model=schemas.UserOut)
def me(caller: Caller = Depends(current)):
    return user_out(caller.user)


@router.patch("/me", response_model=schemas.UserOut)
def patch_me(body: schemas.PatchMeIn, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    user = db.merge(caller.user)
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.language is not None:
        user.language = body.language
    db.commit()
    return user_out(user)


@router.post("/change-password")
def change_password(body: schemas.ChangePasswordIn, request: Request, caller: Caller = Depends(current),
                    db: Session = Depends(get_db)):
    limit(request, "login", "pw:" + str(caller.user.id))
    user = db.merge(caller.user)
    auth.change_password(db, user, body.current_password, body.new_password, caller.session.id,
                         base=base_url(request))
    db.commit()
    return {"status": "password_changed"}
