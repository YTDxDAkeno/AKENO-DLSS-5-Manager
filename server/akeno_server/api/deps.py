"""Request helpers shared by the API and the websites: client address, rate limits, authentication."""

from __future__ import annotations

import ipaddress

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from akeno_server import settings
from akeno_server.auth import service as auth
from akeno_server.auth.ratelimit import LIMITER
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import forbidden, too_many, unauthorized, unavailable
from akeno_server.models import Role, User, UserSession


def client_ip(request: Request) -> str:
    """The caller's address. ``X-Forwarded-For`` counts only when it was set by a trusted reverse proxy."""
    peer = request.client.host if request.client else ""
    proxies = get_config().proxies

    def trusted(text: str) -> bool:
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            return False
        return any(addr in net for net in proxies)

    if not trusted(peer):
        return peer[:45]
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):              # right to left: the first address no trusted proxy vouches for
        if not trusted(hop):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                break
            return hop[:45]
    return (hops[0] if hops else peer)[:45]


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:300]


def limit(request: Request, name: str, key: str | None = None) -> None:
    if not get_config().rate_limit_enabled:
        return
    wait = LIMITER.hit(name, key or client_ip(request))
    if wait is not None:
        raise too_many(wait)


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


class Caller:
    def __init__(self, user: User, session: UserSession):
        self.user = user
        self.session = session


def current(request: Request, db: Session = Depends(get_db)) -> Caller:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise unauthorized("not_authenticated", "Sign in first.")
    user, sess = auth.session_for_access(db, header[7:].strip())
    return Caller(user, sess)


def staff(minimum: Role):
    def dep(caller: Caller = Depends(current)) -> Caller:
        if caller.user.role_enum.rank < minimum.rank:
            raise forbidden("insufficient_role", "Your role does not allow this.")
        if not caller.session.mfa_verified:
            raise forbidden("mfa_required", "Sign in with two-factor authentication first.")
        return caller
    return dep


def not_in_maintenance(db: Session = Depends(get_db)) -> None:
    if settings.get(db, "maintenance_mode"):
        raise unavailable("maintenance", settings.get(db, "maintenance_message")
                          or "The license server is being maintained. AKENO keeps working with your saved license.")
