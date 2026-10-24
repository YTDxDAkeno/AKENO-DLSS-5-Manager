"""Audit trail of administrative actions (who did what to which account, code, device or setting)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from akeno_server.models import AdminAuditEvent, User


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def record(db: Session, actor: User | None, action: str, target_type: str = "", target_id: Any = "",
           ip: str = "", **details) -> AdminAuditEvent:
    event = AdminAuditEvent(actor_id=actor.id if actor else None, actor_email=actor.email if actor else "",
                            actor_role=actor.role if actor else "system", action=action, target_type=target_type,
                            target_id=str(target_id or ""), ip=ip, details=_plain(details))
    db.add(event)
    return event
