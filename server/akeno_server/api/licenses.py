"""``/api/v1/licenses``: redeem codes, read the license, validate it (leases) and get offline certificates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from akeno_server.admin.service import license_history
from akeno_server.api import schemas
from akeno_server.api.deps import Caller, client_ip, current, limit, not_in_maintenance
from akeno_server.clock import utcnow
from akeno_server.db import get_db
from akeno_server.auth import service as auth
from akeno_server.errors import forbidden, not_found, unavailable
from akeno_server.licensing import service as lic
from akeno_server.licensing.signing import SigningUnavailable

router = APIRouter(prefix="/licenses", tags=["licenses"], dependencies=[Depends(not_in_maintenance)])


@router.post("/redeem", response_model=schemas.RedeemOut)
def redeem(body: schemas.RedeemIn, request: Request, caller: Caller = Depends(current),
           db: Session = Depends(get_db)):
    limit(request, "redeem_ip")
    limit(request, "redeem_user", str(caller.user.id))
    if auth.needs_verification(db, caller.user):
        raise forbidden("email_not_verified", "Confirm your e-mail address before redeeming a code.")
    done = lic.redeem(db, caller.user, body.code, client_ip(request), "app")
    return {"license": lic.describe(done.license, utcnow()), "code_type": done.code_type,
            "already_redeemed": done.already}


@router.get("/current", response_model=schemas.LicenseOut)
def current_license(caller: Caller = Depends(current), db: Session = Depends(get_db)):
    return lic.describe(lic.license_of(db, caller.user.id), utcnow())


@router.post("/validate", response_model=schemas.ValidateOut)
def validate(body: schemas.ValidateIn, request: Request, caller: Caller = Depends(current),
             db: Session = Depends(get_db)):
    limit(request, "validate", str(caller.user.id))
    device = lic.find_device(db, caller.user, body.device_id)
    try:
        out = lic.validate(db, caller.user, device, body.app_version, client_ip(request), body.certificate_serial,
                           body.nonce)
    except SigningUnavailable as exc:
        raise unavailable("signing_unavailable", "The license server cannot sign licenses right now.") from exc
    if device is not None and caller.session.device_id != device.id:
        caller.session.device_id = device.id
    db.commit()
    return out


@router.post("/offline-certificate", response_model=schemas.CertificateOut)
def offline_certificate(body: schemas.CertificateIn, request: Request, caller: Caller = Depends(current),
                        db: Session = Depends(get_db)):
    limit(request, "device", str(caller.user.id))
    device = lic.require_device(db, caller.user, body.device_id)
    license_ = lic.license_of(db, caller.user.id)
    if license_ is None:
        raise not_found("no_license", "This account has no license.")
    try:
        token, payload = lic.issue_offline_certificate(db, caller.user, license_, device)
    except SigningUnavailable as exc:
        raise unavailable("signing_unavailable", "The license server cannot sign licenses right now.") from exc
    db.commit()
    return {"certificate": token, "serial": payload["serial"]}


@router.get("/history", response_model=list[schemas.HistoryItem])
def history(caller: Caller = Depends(current), db: Session = Depends(get_db)):
    license_ = lic.license_of(db, caller.user.id)
    return license_history(db, license_) if license_ else []
