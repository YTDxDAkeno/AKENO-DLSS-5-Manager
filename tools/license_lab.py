"""A local, throwaway AKENO license server for end-to-end tests (never production): prepare it, run it, and act
as its administrator. Runs with the server's own Python environment (``server/requirements.txt``).

    python tools/license_lab.py prepare            database, signing key, an admin, AKENO VPN licenses on
    python tools/license_lab.py serve --port 8765   the API (uvicorn) on 127.0.0.1
    python tools/license_lab.py codes --entitlement akeno-vpn --length lifetime --count 1
    python tools/license_lab.py full-until EMAIL SECONDS      AKENO Full that ends in SECONDS (time-limited)
    python tools/license_lab.py grant-status EMAIL ENTITLEMENT STATUS

Configuration comes from the environment: ``AKENO_DATABASE_URL`` (default: SQLite in ``AKENO_LAB_DIR``),
``AKENO_KEYS_DIR`` and the throwaway secrets below. Output is JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
LAB = Path(os.environ.get("AKENO_LAB_DIR") or (Path.cwd() / "license-lab"))
os.environ.setdefault("AKENO_ENVIRONMENT", "development")
os.environ.setdefault("AKENO_DATABASE_URL", f"sqlite:///{(LAB / 'lab.sqlite3').as_posix()}")
os.environ.setdefault("AKENO_KEYS_DIR", str(LAB / "keys"))
os.environ.setdefault("AKENO_MAIL_BACKEND", "console")
os.environ.setdefault("AKENO_COOKIE_SECURE", "false")
os.environ.setdefault("AKENO_PUBLIC_URL", "http://127.0.0.1:8765")
os.environ.setdefault("AKENO_ROLE", "all")
for name in ("AKENO_TOKEN_SECRET", "AKENO_CODE_PEPPER"):         # throwaway lab secrets, kept for the lab's life
    secret_file = LAB / f"{name.lower()}.txt"
    if name not in os.environ:
        LAB.mkdir(parents=True, exist_ok=True)
        if not secret_file.exists():
            secret_file.write_text(secrets.token_hex(32), encoding="ascii")
        os.environ[name] = secret_file.read_text(encoding="ascii").strip()

ADMIN = "lab-admin@example.test"


def _admin(db):
    from akeno_server.auth import service as auth
    from akeno_server.models import Role
    user = auth.user_by_email(db, ADMIN)
    return user or auth.create_user(db, ADMIN, secrets.token_urlsafe(18), "Lab admin", role=Role.SUPER_ADMIN,
                                    verified=True)


def prepare(_args) -> dict:
    LAB.mkdir(parents=True, exist_ok=True)
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "server" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "server" / "migrations"))
    command.upgrade(cfg, "head")
    from akeno_server import db as dbmod
    from akeno_server import settings
    from akeno_server.admin import service as admin
    from akeno_server.licensing import signing
    with dbmod.transaction() as db:
        if not signing.public_keys(db):
            signing.store().create(db)
        actor = _admin(db)
        admin.update_settings(db, actor, {"vpn_module_licenses": True})
        keys = {k.kid: k.public_key for k in signing.public_keys(db)}
        out = {"keys": keys, "vpn_module_licenses": settings.get(db, "vpn_module_licenses")}
        password = os.environ.get("AKENO_LAB_ADMIN_PASSWORD")
        if password:                         # sign-in for screenshots of the admin website (lab only)
            from akeno_server.auth import service as auth
            from akeno_server.auth import totp
            auth.set_password(db, actor, password)
            if not actor.credential.totp_enabled_at:
                secret = totp.new_secret()
                totp.enable(actor.credential, secret)
                out["admin_totp_secret"] = secret
            out["admin"] = ADMIN
    return out


def serve(args) -> dict:
    import uvicorn
    from akeno_server.main import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return {}


def codes(args) -> dict:
    from akeno_server import db as dbmod
    from akeno_server.admin import service as admin
    length = args.length
    code_type, days = ("lifetime", None) if length == "lifetime" else ("monthly", int(length))
    with dbmod.transaction() as db:
        _, items = admin.generate_codes(db, _admin(db), code_type, args.count, entitlement=args.entitlement,
                                        duration_days=None if days == 30 else days, note="license lab",
                                        batch=f"lab-{args.entitlement}")
        return {"codes": [plain for plain, _ in items], "product": items[0][1].entitlement}


def full_until(args) -> dict:
    from akeno_server import db as dbmod
    from akeno_server.admin import service as admin
    from akeno_server.auth import service as auth
    from akeno_server.clock import utcnow
    with dbmod.transaction() as db:
        target = auth.user_by_email(db, args.email)
        lic = admin.set_license(db, _admin(db), target, "monthly", utcnow() + timedelta(seconds=args.seconds),
                                "license lab: AKENO Full that ends soon")
        return {"expires_at": lic.expires_at.isoformat()}


def grant_status(args) -> dict:
    from akeno_server import db as dbmod
    from akeno_server.admin import service as admin
    from akeno_server.auth import service as auth
    with dbmod.transaction() as db:
        target = auth.user_by_email(db, args.email)
        grant = admin.grant_status(db, _admin(db), target, args.entitlement, args.status, "license lab")
        return {"entitlement": grant.entitlement, "status": grant.status}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").set_defaults(fn=prepare)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=serve)
    c = sub.add_parser("codes")
    c.add_argument("--entitlement", default="akeno-suite")
    c.add_argument("--length", default="lifetime")
    c.add_argument("--count", type=int, default=1)
    c.set_defaults(fn=codes)
    f = sub.add_parser("full-until")
    f.add_argument("email")
    f.add_argument("seconds", type=int)
    f.set_defaults(fn=full_until)
    g = sub.add_parser("grant-status")
    g.add_argument("email")
    g.add_argument("entitlement")
    g.add_argument("status")
    g.set_defaults(fn=grant_status)
    args = p.parse_args()
    print(json.dumps(args.fn(args)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
