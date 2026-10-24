"""Templates, language, CSRF protection and cookies for the websites."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from akeno_server import __version__
from akeno_server.auth.tokens import b64d, b64e
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.errors import ApiError, forbidden
from akeno_server.web.i18n import translate

TEMPLATES = Path(__file__).parent / "templates"
SESSION_COOKIE = "akeno_session"
ADMIN_COOKIE = "akeno_admin"
LANG_COOKIE = "akeno_lang"
CSRF_COOKIE = "akeno_csrf"
PENDING_COOKIE = "akeno_pending"

env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), trim_blocks=True,
                  lstrip_blocks=True)


def language(request: Request, user=None) -> str:
    chosen = request.query_params.get("lang") or request.cookies.get(LANG_COOKIE)
    if chosen in ("de", "en"):
        return chosen
    if user is not None and getattr(user, "language", None) in ("de", "en"):
        return user.language
    accept = request.headers.get("accept-language", "").lower()
    return "de" if accept.startswith("de") or ",de" in accept.replace(" ", "") else "en"


def fmt_time(value: datetime | None, lang: str = "en") -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M UTC" if lang == "de" else "%Y-%m-%d %H:%M UTC")


def fmt_remaining(seconds: int | None, lang: str = "en") -> str:
    if seconds is None:
        return translate("unlimited", lang)
    if seconds <= 0:
        return translate("expired", lang)
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} {translate('days' if d != 1 else 'day', lang)} {h} h"
    return f"{h} h {m} min"


def csrf_value(request: Request) -> str:
    """The form token: the web session's own token, or the double-submit cookie for signed-out pages."""
    sess = getattr(request.state, "web_session", None)
    if sess is not None and sess.csrf_token:
        return sess.csrf_token
    token = request.cookies.get(CSRF_COOKIE) or getattr(request.state, "new_csrf", None)
    if not token:
        token = secrets.token_urlsafe(24)
        request.state.new_csrf = token
    return token


def check_csrf(request: Request, form) -> None:
    sent = str(form.get("csrf") or "")
    expected = ""
    sess = getattr(request.state, "web_session", None)
    if sess is not None and sess.csrf_token:
        expected = sess.csrf_token
    else:
        expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise forbidden("csrf_failed", "The form expired. Reload the page and try again.")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        host = urlparse(origin).netloc
        if host and host != request.headers.get("host", host):
            raise forbidden("csrf_failed", "The form was sent from another site.")


def render(request: Request, template: str, status: int = 200, **ctx: Any) -> HTMLResponse:
    user = ctx.get("user") or getattr(request.state, "web_user", None)
    lang = language(request, user)

    def t(text: str, **fmt) -> str:
        out = translate(text, lang)
        return out.format(**fmt) if fmt else out

    html = env.get_template(template).render(
        request=request, t=t, lang=lang, csrf=csrf_value(request), version=__version__, now=utcnow(),
        fmt_time=lambda v: fmt_time(v, lang), fmt_remaining=lambda s: fmt_remaining(s, lang),
        flash=_take_flash(request), user=user, admin_url=get_config().admin_url,
        download_url=get_config().download_url, **{k: v for k, v in ctx.items() if k != "user"})
    response = HTMLResponse(html, status_code=status)
    finish(request, response)
    return response


def finish(request: Request, response: Response) -> None:
    new = getattr(request.state, "new_csrf", None)
    if new:
        set_cookie(response, CSRF_COOKIE, new, max_age=3600 * 6)
    if request.query_params.get("lang") in ("de", "en"):
        set_cookie(response, LANG_COOKIE, request.query_params["lang"], max_age=3600 * 24 * 365, http_only=False)
    if getattr(request.state, "clear_flash", False):
        response.delete_cookie("akeno_flash", path="/")


def redirect(request: Request, url: str, flash: str | None = None, kind: str = "ok") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash:
        set_cookie(response, "akeno_flash", b64e(f"{kind}|{flash}".encode()), max_age=60)
    finish(request, response)
    return response


def _take_flash(request: Request) -> tuple[str, str] | None:
    raw = request.cookies.get("akeno_flash")
    if not raw:
        return None
    request.state.clear_flash = True
    try:
        kind, text = b64d(raw).decode().split("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    return kind, text


def set_cookie(response: Response, name: str, value: str, max_age: int | None = None, http_only: bool = True,
               same_site: str = "lax") -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=http_only, secure=get_config().cookie_secure,
                        samesite=same_site, path="/")


def error_page(request: Request, exc: ApiError) -> HTMLResponse:
    return render(request, "error.html", status=exc.status, error=exc)


# -- the step between password and authenticator code ----------------------------------------------------------------
def _pending_key() -> bytes:
    return hashlib.sha256(b"akeno pending login v1|" + get_config().token_secret.get_secret_value().encode()).digest()


def pending_token(user_id: str, target: str) -> str:
    body = b64e(f"{user_id}|{int(utcnow().timestamp()) + 300}|{target}".encode())
    return body + "." + b64e(hmac.new(_pending_key(), body.encode(), hashlib.sha256).digest())


def read_pending(token: str) -> tuple[str, str] | None:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, b64e(hmac.new(_pending_key(), body.encode(), hashlib.sha256).digest())):
            return None
        user_id, exp, target = b64d(body).decode().split("|", 2)
        if int(exp) < utcnow().timestamp():
            return None
        return user_id, target
    except (ValueError, UnicodeDecodeError):
        return None


def safe_next(url: str | None, default: str = "/account") -> str:
    """Only same-site paths after sign-in (no open redirects)."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default
