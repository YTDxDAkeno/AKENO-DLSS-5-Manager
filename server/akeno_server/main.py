"""ASGI application. One image, three services: ``AKENO_ROLE=api`` (the app's JSON API), ``web`` (landing page and
account website) and ``admin`` (the admin website), so the admin site can get its own host name and access rules.
``all`` serves everything from one process (development).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from akeno_server import API_VERSION, __version__
from akeno_server.auth.ratelimit import LIMITER
from akeno_server.config import get_config
from akeno_server.errors import ApiError

log = logging.getLogger("akeno_server")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def create_app() -> FastAPI:
    cfg = get_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    LIMITER.enabled = cfg.rate_limit_enabled
    docs = cfg.serves_api
    app = FastAPI(title="AKENO Licensing API", version=__version__,
                  description="Accounts, activation codes, licenses, devices and signed offline licenses for the "
                              "AKENO Gaming Suite. The server grants one generic entitlement, `akeno-suite`.",
                  docs_url=f"/api/{API_VERSION}/docs" if docs else None,
                  redoc_url=None, openapi_url=f"/api/{API_VERSION}/openapi.json" if docs else None)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        headers = {"Retry-After": str(exc.details["retry_after"])} if "retry_after" in exc.details else None
        if request.url.path.startswith("/api/"):
            return JSONResponse(exc.body(), status_code=exc.status, headers=headers)
        from akeno_server.web.render import error_page
        return error_page(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "problem": e.get("msg", "")}
                  for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Some fields are invalid.",
                                       "details": {"fields": fields}}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        err = ApiError(exc.status_code, code, str(exc.detail))
        if request.url.path.startswith("/api/"):
            return JSONResponse(err.body(), status_code=exc.status_code)
        from akeno_server.web.render import error_page
        return error_page(request, err)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        err = ApiError(500, "internal_error", "Something went wrong on the server.")
        if request.url.path.startswith("/api/"):
            return JSONResponse(err.body(), status_code=500)
        from akeno_server.web.render import error_page
        return error_page(request, err)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            if key == "Content-Security-Policy" and request.url.path.endswith("/docs"):
                continue            # the OpenAPI page loads Swagger UI's own scripts
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/") and "docs" not in request.url.path:
            response.headers.setdefault("Cache-Control", "no-store")
        if cfg.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    from akeno_server.api import router as api_router
    from akeno_server.api import system
    if cfg.serves_api:
        app.include_router(api_router)
        if cfg.network_enabled:
            from akeno_server.network.api import router as network_router
            from akeno_server.network.rooms import RoomStore
            from akeno_server.clock import utcnow
            app.state.lan_rooms = RoomStore(cfg.network_database_path, clock=lambda: utcnow().timestamp())
            app.include_router(network_router)
    else:
        app.include_router(system.router, prefix=f"/api/{API_VERSION}")     # health checks on every service
    from akeno_server.web import mount
    mount(app, cfg)
    return app


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
