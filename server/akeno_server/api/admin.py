"""``/api/v1/admin``: the same operations as the admin website, for scripts. Staff roles with two-factor only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from akeno_server import settings
from akeno_server.admin import service as admin
from akeno_server.api import schemas
from akeno_server.api.common import page_out
from akeno_server.api.deps import Caller, base_url, client_ip, limit, staff
from akeno_server.db import get_db
from akeno_server.models import Role

router = APIRouter(prefix="/admin", tags=["admin"])
support = staff(Role.SUPPORT)


@router.post("/codes/generate", response_model=schemas.GeneratedOut)
def generate(body: schemas.GenerateCodesIn, request: Request, caller: Caller = Depends(support),
             db: Session = Depends(get_db)):
    limit(request, "admin_codes", str(caller.user.id))
    batch, items = admin.generate_codes(db, caller.user, body.code_type, body.count, body.device_allowance,
                                        body.expires_in_days, body.note, body.batch, body.assigned_email,
                                        client_ip(request))
    out = {"batch": batch, "codes": [plain for plain, _ in items], "items": [admin.code_out(db, r) for _, r in items]}
    db.commit()
    return out


@router.get("/codes", response_model=schemas.Page)
def codes(status: str = "", code_type: str = "", batch: str = "", q: str = "", page: int = Query(1, ge=1),
          per_page: int = Query(50, ge=1, le=200), caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "view")
    rows, total = admin.list_codes(db, status, code_type, batch, q, page, per_page)
    return page_out([admin.code_out(db, r) for r in rows], page, per_page, total)


@router.post("/codes/{code_id}/revoke")
def revoke(code_id: str, request: Request, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    row = admin.revoke_code(db, caller.user, code_id, client_ip(request))
    db.commit()
    return admin.code_out(db, row)


@router.post("/codes/{code_id}/reserve")
def reserve(code_id: str, body: schemas.ReserveIn, request: Request, caller: Caller = Depends(support),
            db: Session = Depends(get_db)):
    row = admin.reserve_code(db, caller.user, code_id, body.reserved_for, client_ip(request))
    db.commit()
    return admin.code_out(db, row)


@router.post("/codes/{code_id}/release")
def release(code_id: str, request: Request, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    row = admin.release_code(db, caller.user, code_id, client_ip(request))
    db.commit()
    return admin.code_out(db, row)


@router.get("/users", response_model=schemas.Page)
def users(q: str = "", role: str = "", status: str = "", license: str = "", page: int = Query(1, ge=1),
          per_page: int = Query(50, ge=1, le=200), caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "view")
    rows, total = admin.list_users(db, q, role, status, license, page, per_page)
    return page_out([admin.user_summary(db, u) for u in rows], page, per_page, total)


@router.get("/users/export", response_class=PlainTextResponse)
def export(request: Request, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    text = admin.users_csv(db, caller.user, client_ip(request))
    db.commit()
    return PlainTextResponse(text, media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="akeno-users.csv"'})


@router.get("/users/{user_id}")
def user(user_id: str, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "view")
    return admin.user_detail(db, admin.get_user(db, user_id))


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: schemas.AdminUserPatch, request: Request, caller: Caller = Depends(support),
               db: Session = Depends(get_db)):
    target = admin.get_user(db, user_id)
    admin.patch_user(db, caller.user, target, client_ip(request), **body.model_dump(exclude_none=True))
    db.commit()
    return admin.user_summary(db, target)


@router.post("/users/{user_id}/extend")
def extend(user_id: str, body: schemas.ExtendIn, request: Request, caller: Caller = Depends(support),
           db: Session = Depends(get_db)):
    target = admin.get_user(db, user_id)
    admin.extend(db, caller.user, target, body.hours, body.note, client_ip(request))
    db.commit()
    return admin.user_summary(db, target)


@router.post("/users/{user_id}/license")
def set_license(user_id: str, body: schemas.SetLicenseIn, request: Request, caller: Caller = Depends(support),
                db: Session = Depends(get_db)):
    target = admin.get_user(db, user_id)
    admin.set_license(db, caller.user, target, body.license_type, body.expires_at, body.note, client_ip(request))
    db.commit()
    return admin.user_summary(db, target)


@router.post("/users/{user_id}/grant-lifetime")
def grant_lifetime(user_id: str, body: schemas.NoteIn, request: Request, caller: Caller = Depends(support),
                   db: Session = Depends(get_db)):
    target = admin.get_user(db, user_id)
    admin.set_license(db, caller.user, target, "lifetime", None, body.note, client_ip(request))
    db.commit()
    return admin.user_summary(db, target)


@router.post("/users/{user_id}/license-status")
def license_status(user_id: str, body: schemas.LicenseStatusIn, request: Request, caller: Caller = Depends(support),
                   db: Session = Depends(get_db)):
    target = admin.get_user(db, user_id)
    admin.license_status(db, caller.user, target, body.status, body.note, client_ip(request))
    db.commit()
    return admin.user_summary(db, target)


@router.post("/users/{user_id}/reset-devices")
def reset_devices(user_id: str, request: Request, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    n = admin.reset_devices(db, caller.user, admin.get_user(db, user_id), client_ip(request))
    db.commit()
    return {"removed": n}


@router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: str, request: Request, caller: Caller = Depends(support),
                    db: Session = Depends(get_db)):
    n = admin.revoke_sessions(db, caller.user, admin.get_user(db, user_id), client_ip(request))
    db.commit()
    return {"revoked": n}


@router.post("/users/{user_id}/password-reset")
def password_reset(user_id: str, request: Request, caller: Caller = Depends(support),
                   db: Session = Depends(get_db)):
    admin.send_reset(db, caller.user, admin.get_user(db, user_id), client_ip(request), base_url(request))
    db.commit()
    return {"status": "sent"}


@router.get("/devices", response_model=schemas.Page)
def devices(q: str = "", page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
            caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "view")
    rows, total = admin.list_devices(db, q, page, per_page)
    return page_out([admin.device_out(d) | {"user_id": str(d.user_id)} for d in rows], page, per_page, total)


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: str, request: Request, caller: Caller = Depends(support),
                  db: Session = Depends(get_db)):
    dev = admin.get_device(db, device_id)
    admin.admin_revoke_device(db, caller.user, dev, client_ip(request))
    db.commit()
    return admin.device_out(dev)


@router.post("/devices/{device_id}/transfer")
def transfer_device(device_id: str, body: schemas.TransferIn, request: Request, caller: Caller = Depends(support),
                    db: Session = Depends(get_db)):
    dev = admin.transfer_device(db, caller.user, admin.get_device(db, device_id), body.email, client_ip(request))
    db.commit()
    return admin.device_out(dev)


@router.get("/audit", response_model=schemas.Page)
def audit_log(action: str = "", q: str = "", page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
              caller: Caller = Depends(support), db: Session = Depends(get_db)):
    rows, total = admin.list_audit(db, caller.user, action, q, page, per_page)
    return page_out([admin.audit_out(e) for e in rows], page, per_page, total)


@router.get("/statistics")
def statistics(caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "view")
    return admin.statistics(db)


@router.get("/settings")
def get_settings(caller: Caller = Depends(support), db: Session = Depends(get_db)):
    admin.require(caller.user, "settings")
    return settings.all_settings(db)


@router.put("/settings")
def put_settings(body: dict, request: Request, caller: Caller = Depends(support), db: Session = Depends(get_db)):
    try:
        out = admin.update_settings(db, caller.user, body, client_ip(request))
    except settings.SettingError as exc:
        from akeno_server.errors import bad_request
        raise bad_request("invalid_setting", str(exc)) from exc
    db.commit()
    return out
