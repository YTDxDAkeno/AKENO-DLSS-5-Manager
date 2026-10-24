"""The versioned JSON API under ``/api/v1``. Only additive changes are made to v1; the app relies on it."""

from fastapi import APIRouter

from akeno_server.api import admin, auth, devices, licenses, system

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(licenses.router)
router.include_router(devices.router)
router.include_router(admin.router)
