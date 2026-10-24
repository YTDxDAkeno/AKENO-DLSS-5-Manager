"""HTTPS client for the license server's ``/api/v1``.

Errors fall into two kinds the rest of the app treats differently:

* :class:`ServerUnavailable`: no answer, a timeout, a proxy or gateway error, any 5xx, maintenance. The saved
  license keeps working; nothing is signed out or deleted.
* :class:`ApiError`: the server answered and refused (wrong password, invalid code, revoked sign-in …), with the
  stable ``code`` from the server.

:class:`ServerUnavailable` carries a ``kind`` so the app can say what went wrong (``offline``, ``timeout``,
``certificate``, ``maintenance``, ``server``, ``network``) and, when the server sent one, its ``code`` (for
example ``email_unavailable``).

Only reads (``GET``) are repeated after a dropped connection. Nothing that changes data is sent twice: a
registration or a password-reset request is the user's to repeat.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from akeno import __version__

TIMEOUT = 15
RETRY_DELAY = 0.6                 # seconds before the one repeat of a read


class ServerUnavailable(Exception):
    def __init__(self, message: str, maintenance: bool = False, kind: str | None = None, code: str = "",
                 retry_after: float | None = None):
        super().__init__(message)
        self.maintenance = maintenance
        self.kind = kind or ("maintenance" if maintenance else "offline")
        self.code = code
        self.retry_after = retry_after


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def auth_failure(self) -> bool:
        return self.status == 401


def check_url(url: str, strict: bool | None = None) -> str:
    """HTTPS only. Plain HTTP to this PC itself is allowed when running from source (tests), never in a build."""
    from akeno.licensing.config import production
    strict = production() if strict is None else strict
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local and not strict):
        raise ValueError("The license server address must start with https://")
    if not parsed.hostname:
        raise ValueError("The license server address is incomplete.")
    return url


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """The license API never redirects: following one could leave HTTPS or the official host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LicenseApi:
    def __init__(self, base_url: str, timeout: float = TIMEOUT):
        self.base = check_url(base_url)
        self.timeout = timeout
        self._ctx = ssl.create_default_context()      # system certificate store; verification always on
        self._ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._opener = urllib.request.build_opener(_NoRedirects, urllib.request.HTTPSHandler(context=self._ctx))

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict | list:
        try:
            return self._once(method, path, body, token)
        except ServerUnavailable as exc:
            if method != "GET" or exc.kind not in ("offline", "timeout"):
                raise
            time.sleep(RETRY_DELAY)                   # a read is safe to repeat once after a dropped connection
            return self._once(method, path, body, token)

    def _once(self, method: str, path: str, body: dict | None, token: str | None) -> dict | list:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/api/v1{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            return self._http_error(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(exc, ssl.SSLCertVerificationError):
                raise ServerUnavailable("The license server's certificate is not valid.", kind="certificate") from exc
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ServerUnavailable("The license server did not answer in time.", kind="timeout") from exc
            raise ServerUnavailable(f"The license server cannot be reached ({reason}).", kind="offline") from exc
        except ValueError as exc:
            raise ServerUnavailable("The license server sent an unreadable answer.", kind="network") from exc

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError):
        try:
            err = json.loads(exc.read() or b"{}").get("error") or {}
        except (ValueError, AttributeError, OSError):
            err = {}
        code = err.get("code") or f"http_{exc.code}"
        message = err.get("message") or exc.reason or "The license server refused the request."
        if exc.code >= 500 or exc.code in (407, 408) or code == "maintenance":
            kind = "maintenance" if code == "maintenance" else ("server" if exc.code >= 500 else "network")
            raise ServerUnavailable(message, maintenance=code == "maintenance", kind=kind,
                                    code=code if err else "", retry_after=_retry_after(exc)) from exc
        if not err:                                   # an answer that is not from the license server (a proxy)
            raise ServerUnavailable(f"Unexpected answer from the network ({exc.code}).", kind="network") from exc
        details = err.get("details") or {}
        if exc.code == 429 and "retry_after" not in details and _retry_after(exc) is not None:
            details = {**details, "retry_after": _retry_after(exc)}
        raise ApiError(exc.code, code, message, details) from exc


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        return float(exc.headers.get("Retry-After")) if exc.headers and exc.headers.get("Retry-After") else None
    except (TypeError, ValueError):
        return None


def _user_agent() -> str:
    """``AKENO/<version> (<system>)``: the server sees which AKENO version calls (never who or which PC)."""
    import platform
    system = "Windows" if sys.platform == "win32" else platform.system() or "unknown"
    return f"AKENO/{__version__} ({system}; desktop)"


USER_AGENT = _user_agent()
