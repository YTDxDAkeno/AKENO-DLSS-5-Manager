"""``/api/v1/system``: health, readiness, version and the public license keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from akeno_server import API_VERSION, ENTITLEMENT, __version__, settings
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import unavailable
from akeno_server.licensing import signing

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health():
    """The process answers (used by Docker's health check)."""
    return {"status": "ok", "time": utcnow()}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    """The database answers and, for the API, a signing key is available."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # any driver error means not ready
        raise unavailable("database_unavailable", "The database does not answer.") from exc
    checks = {"database": "ok"}
    if get_config().serves_api:
        try:
            signing.store().active()
            checks["signing_key"] = "ok"
        except signing.SigningUnavailable as exc:
            raise unavailable("signing_unavailable", str(exc)) from exc
    return {"status": "ready", "checks": checks}


@router.get("/version")
def version(db: Session = Depends(get_db)):
    return {"server_version": __version__, "api_version": API_VERSION, "entitlement": ENTITLEMENT,
            "maintenance": settings.get(db, "maintenance_mode"),
            "min_client_version": settings.get(db, "min_client_version") or None,
            "latest_client_version": settings.get(db, "latest_client_version") or None,
            "registration_enabled": settings.get(db, "registration_enabled"),
            "require_email_verification": settings.get(db, "require_email_verification"),
            "download_url": get_config().download_url, "server_time": utcnow()}


@router.get("/keys")
def keys(db: Session = Depends(get_db)):
    """Public Ed25519 keys with their rotation endorsements (the app trusts a key only through this chain)."""
    return signing.key_document(signing.public_keys(db))
