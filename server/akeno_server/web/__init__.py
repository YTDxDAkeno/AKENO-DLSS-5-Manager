"""Server-rendered websites: the landing page and account area (``web``) and the admin area (``admin``)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from akeno_server.config import ServerConfig

STATIC = Path(__file__).parent / "static"


def mount(app: FastAPI, cfg: ServerConfig) -> None:
    if cfg.serves_web or cfg.serves_admin:
        from akeno_server.web.render import redirect
        from akeno_server.web.session import Redirect

        @app.exception_handler(Redirect)
        async def _redirect(request, exc: Redirect):
            return redirect(request, exc.url, exc.flash, exc.kind)
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    if cfg.serves_web:
        from akeno_server.web import account, public
        app.include_router(public.router)
        app.include_router(account.router)
    if cfg.serves_admin:
        from akeno_server.web import admin, public
        if not cfg.serves_web:
            app.include_router(public.auth_router)       # sign-in pages for the admin host
        app.include_router(admin.router)
