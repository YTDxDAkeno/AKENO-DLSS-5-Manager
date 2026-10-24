"""Who is signed in on the website (an HttpOnly cookie that maps to a ``web`` session row)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from sqlalchemy.orm import Session

from akeno_server.auth import service as auth
from akeno_server.models import Role, User, UserSession
from akeno_server.web.render import SESSION_COOKIE


class Redirect(Exception):
    def __init__(self, url: str, flash: str | None = None, kind: str = "ok"):
        super().__init__(url)
        self.url = url
        self.flash = flash
        self.kind = kind


def load(request: Request, db: Session) -> tuple[User, UserSession] | None:
    found = auth.session_for_cookie(db, request.cookies.get(SESSION_COOKIE, ""))
    if found:
        request.state.web_user, request.state.web_session = found
        db.commit()                                  # last_used_at
    return found


def need_user(request: Request, db: Session) -> tuple[User, UserSession]:
    found = load(request, db)
    if not found:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise Redirect(f"/login?next={quote(target)}")
    return found


def need_staff(request: Request, db: Session, minimum: Role = Role.SUPPORT,
               allow_enrolment: bool = False) -> tuple[User, UserSession]:
    user, sess = need_user(request, db)
    if user.role_enum.rank < minimum.rank:
        if user.role_enum.privileged:
            raise Redirect("/admin", "Your role does not allow this.", "error")
        raise Redirect("/account")
    if not allow_enrolment and not (user.credential.totp_enabled_at and sess.mfa_verified):
        # staff use the admin area only after two-factor sign-in has been set up and used
        raise Redirect("/admin/security")
    return user, sess
