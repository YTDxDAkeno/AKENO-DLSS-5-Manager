"""HTTPS client for the license server's ``/api/v1``.

Errors fall into two kinds the rest of the app treats differently:

* :class:`ServerUnavailable`: no answer, a timeout, a proxy or gateway error, any 5xx, maintenance. The saved
  license keeps working; nothing is signed out or deleted.
* :class:`ApiError`: the server answered and refused (wrong password, invalid code, revoked sign-in …), with the
  stable ``code`` from the server.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

from akeno import __version__

TIMEOUT = 15


class ServerUnavailable(Exception):
    def __init__(self, message: str, maintenance: bool = False):
        super().__init__(message)
        self.maintenance = maintenance


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


def check_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("The license server address must start with https://")
    if not parsed.hostname:
        raise ValueError("The license server address is incomplete.")
    return url


class LicenseApi:
    def __init__(self, base_url: str, timeout: float = TIMEOUT):
        self.base = check_url(base_url)
        self.timeout = timeout
        self._ctx = ssl.create_default_context()      # system certificate store; verification always on

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict | list:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/api/v1{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"AKENO/{__version__}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            return self._http_error(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ServerUnavailable("The license server's certificate is not valid.") from exc
            raise ServerUnavailable(f"The license server cannot be reached ({reason}).") from exc
        except ValueError as exc:
            raise ServerUnavailable("The license server sent an unreadable answer.") from exc

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError):
        try:
            err = json.loads(exc.read() or b"{}").get("error") or {}
        except (ValueError, AttributeError, OSError):
            err = {}
        code = err.get("code") or f"http_{exc.code}"
        message = err.get("message") or exc.reason or "The license server refused the request."
        if exc.code >= 500 or exc.code in (407, 408) or code == "maintenance":
            raise ServerUnavailable(message, maintenance=code == "maintenance") from exc
        if not err:                                   # an answer that is not from the license server (a proxy)
            raise ServerUnavailable(f"Unexpected answer from the network ({exc.code}).") from exc
        raise ApiError(exc.code, code, message, err.get("details")) from exc
