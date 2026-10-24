"""Conversions between database rows and API bodies."""

from __future__ import annotations

from akeno_server.models import User


def user_out(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name, "role": user.role,
            "language": user.language, "email_verified": user.email_verified_at is not None,
            "two_factor": bool(user.credential and user.credential.totp_enabled_at), "created_at": user.created_at}


def page_out(items: list, page: int, per_page: int, total: int) -> dict:
    return {"items": items, "page": page, "per_page": per_page, "total": total}
