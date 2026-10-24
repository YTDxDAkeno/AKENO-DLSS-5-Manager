"""Structured errors: ``{"error": {"code": "...", "message": "...", "details": {...}}}`` with a fitting status.

The ``code`` values are part of the stable API: the Windows app decides on them (for example, it tells an
authentication failure from a server outage), and the website translates them.
"""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return {"error": out}


def bad_request(code: str, message: str, **details) -> ApiError:
    return ApiError(400, code, message, **details)


def unauthorized(code: str = "not_authenticated", message: str = "Sign in first.") -> ApiError:
    return ApiError(401, code, message)


def forbidden(code: str, message: str, **details) -> ApiError:
    return ApiError(403, code, message, **details)


def not_found(code: str = "not_found", message: str = "Not found.") -> ApiError:
    return ApiError(404, code, message)


def conflict(code: str, message: str, **details) -> ApiError:
    return ApiError(409, code, message, **details)


def too_many(retry_after: float) -> ApiError:
    return ApiError(429, "rate_limited", "Too many attempts. Try again later.", retry_after=int(retry_after + 0.5))


def unavailable(code: str, message: str, **details) -> ApiError:
    return ApiError(503, code, message, **details)
