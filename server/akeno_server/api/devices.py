"""``/api/v1/devices``: the PCs of an account (pseudonymous fingerprints, a label, the AKENO version)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from akeno_server.admin.service import device_out
from akeno_server.api import schemas
from akeno_server.api.deps import Caller, current, limit, not_in_maintenance
from akeno_server.db import get_db
from akeno_server.licensing import service as lic
from akeno_server.models import Device

router = APIRouter(prefix="/devices", tags=["devices"], dependencies=[Depends(not_in_maintenance)])


@router.get("", response_model=list[schemas.DeviceOut])
def list_devices(caller: Caller = Depends(current), db: Session = Depends(get_db)):
    rows = db.scalars(select(Device).where(Device.user_id == caller.user.id, Device.revoked_at.is_(None))
                      .order_by(Device.created_at))
    return [device_out(d, caller.session.device_id) for d in rows]


@router.post("/register", response_model=schemas.DeviceOut)
def register(body: schemas.DeviceIn, request: Request, caller: Caller = Depends(current),
             db: Session = Depends(get_db)):
    limit(request, "device", str(caller.user.id))
    dev = lic.register_device(db, caller.user, body.fingerprint, body.label, body.os, body.app_version)
    caller.session.device_id = dev.id
    db.commit()
    return device_out(dev, dev.id)


@router.patch("/{device_id}", response_model=schemas.DeviceOut)
def rename(device_id: str, body: schemas.DevicePatchIn, caller: Caller = Depends(current),
           db: Session = Depends(get_db)):
    dev = lic.require_device(db, caller.user, device_id)
    dev.label = body.label.strip()[:80]
    db.commit()
    return device_out(dev, caller.session.device_id)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(device_id: str, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    dev = lic.require_device(db, caller.user, device_id)
    if dev.active:
        lic.user_remove_device(db, caller.user, dev)
        db.commit()
