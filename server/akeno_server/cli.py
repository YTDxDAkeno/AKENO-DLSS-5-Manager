"""``akeno-server`` command line: migrations, the one-time super admin, signing keys, checks and cleanup.

Run inside the API container, for example ``docker compose exec akeno-api akeno-server keys list``
(``deploy/create-admin.sh`` and ``deploy/install.sh`` wrap the common ones).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from sqlalchemy import func, select, text


def _migrate(args) -> int:
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, args.revision)
    print(f"database migrated to {args.revision}")
    return 0


def _migrate_down(args) -> int:
    """Take the database back to an older migration, for a rollback to an older server version (update.sh
    --rollback runs it with the newer code before the older code returns). 0002 → 0001 removes module grants and
    withdraws unused module codes, so the older server can never treat them as AKENO Full codes."""
    from alembic import command
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.downgrade(cfg, args.revision)
    print(f"database taken back to migration {args.revision}")
    return 0


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    if not sys.stdin.isatty():
        raise SystemExit("No terminal: pass --password-stdin and pipe the password in.")
    first = getpass.getpass("Password (at least 10 characters): ")
    if first != getpass.getpass("Repeat the password: "):
        raise SystemExit("The passwords do not match.")
    return first


def _create_super_admin(args) -> int:
    from akeno_server import audit
    from akeno_server.auth import service as auth
    from akeno_server.db import transaction
    from akeno_server.errors import ApiError
    from akeno_server.models import Role, User
    with transaction() as db:
        existing = db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN.value))
        if existing:
            print("A super admin exists already. Further administrators are appointed on the admin website "
                  "(Users → role).", file=sys.stderr)
            return 2
        password = _read_password(args)
        try:
            user = auth.create_user(db, args.email, password, args.name or "", args.language, role=Role.SUPER_ADMIN,
                                    verified=True)
        except ApiError as exc:
            print(f"Not created: {exc.message}", file=sys.stderr)
            return 1
        audit.record(db, None, "users.bootstrap_super_admin", "user", user.id, email=user.email)
    print(f"Super admin {args.email} created. Sign in on the admin website; it asks you to set up two-factor "
          "sign-in (an authenticator app) before anything else.")
    return 0


def _keys(args) -> int:
    from akeno_server import audit
    from akeno_server.db import transaction
    from akeno_server.licensing import signing
    store = signing.KeyStore()
    with transaction() as db:
        if args.action == "init":
            if store.active_kid():
                print(f"A signing key exists already: {store.active_kid()}")
                return 0
            row = store.create(db)
            audit.record(db, None, "keys.create", "key", row.kid)
            print(f"Created license signing key {row.kid} in {store.dir}.")
            print("Back up this directory (deploy/backup.sh does). Put the public key into the app build:")
            print(json.dumps({"kid": row.kid, "public_key": row.public_key}))
        elif args.action == "rotate":
            row = store.create(db)
            audit.record(db, None, "keys.rotate", "key", row.kid, endorsed_by=row.endorsed_by)
            print(f"New signing key {row.kid}, endorsed by {row.endorsed_by}. Apps accept it automatically; "
                  "add it to the next app build as well.")
        elif args.action == "list":
            active = store.active_kid()
            for row in signing.public_keys(db):
                state = "active" if row.kid == active else ("retired" if row.retired_at else "inactive")
                print(f"{row.kid}  {state:8}  created {row.created_at:%Y-%m-%d}  public {row.public_key}")
        elif args.action == "export":
            doc = {"server_url": args.server_url or "", "keys": [{"kid": k.kid, "public_key": k.public_key}
                                                                 for k in signing.public_keys(db)]}
            print(json.dumps(doc, indent=2))
    return 0


def _schema(session) -> bool:
    """The database's migration against this server's newest one, and what the licensing tables hold (counts only:
    compare them before and after a migration)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with session() as db:
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        counts = {}
        for table in ("users", "licenses", "license_codes", "license_redemptions", "devices", "offline_certificates",
                      "feature_grants"):
            try:
                counts[table] = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            except Exception:           # a table of a newer migration that is not there yet
                db.rollback()
                counts[table] = "—"
    print(f"schema          {current}" + (" (current)" if current == head else f" (this server needs {head}: "
                                                                                 "akeno-server migrate)"))
    print("records         " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return current == head


def _check(args) -> int:
    from akeno_server.config import get_config
    from akeno_server.db import session
    from akeno_server.licensing import signing
    cfg = get_config()
    ok = True
    print(f"environment     {cfg.environment}")
    print(f"role            {cfg.role}")
    print(f"public url      {cfg.public_url or '(from the request)'}")
    print(f"mail            {cfg.mail_backend}" + (f" via {cfg.smtp_host}:{cfg.smtp_port}" if cfg.mail_backend ==
                                                    "smtp" else ""))
    try:
        with session() as db:
            db.execute(text("SELECT 1"))
        print("database        ok")
        ok = _schema(session) and ok
    except Exception as exc:  # report, do not crash
        ok = False
        print(f"database        FAILED: {exc}")
    try:
        kid, _ = signing.KeyStore().active()
        print(f"signing key     {kid}")
    except signing.SigningUnavailable as exc:
        ok = cfg.role not in ("all", "api") and ok
        print(f"signing key     missing ({exc})")
    return 0 if ok else 1


def _cleanup(args) -> int:
    from akeno_server.admin.service import cleanup
    from akeno_server.db import transaction
    with transaction() as db:
        print(json.dumps(cleanup(db)))
    return 0


def _reset_2fa(args) -> int:
    from akeno_server import audit
    from akeno_server.auth import service as auth
    from akeno_server.auth import totp
    from akeno_server.db import transaction
    with transaction() as db:
        user = auth.user_by_email(db, args.email)
        if user is None:
            print("No such account.", file=sys.stderr)
            return 1
        totp.disable(user.credential)
        auth.revoke_all(db, user.id, "2fa_reset_cli")
        audit.record(db, None, "users.disable_2fa_cli", "user", user.id, email=user.email)
    print(f"Two-factor sign-in of {args.email} removed; it is set up again at the next sign-in.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="akeno-server", description="AKENO licensing server administration")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("migrate", help="apply database migrations")
    p.add_argument("revision", nargs="?", default="head")
    p.set_defaults(fn=_migrate)
    p = sub.add_parser("migrate-down", help="take the database back to an older migration (rollback only)")
    p.add_argument("revision")
    p.set_defaults(fn=_migrate_down)
    p = sub.add_parser("create-super-admin", help="create the first super admin (only while none exists)")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--language", choices=("en", "de"), default="en")
    p.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    p.set_defaults(fn=_create_super_admin)
    p = sub.add_parser("keys", help="license signing keys")
    p.add_argument("action", choices=("init", "rotate", "list", "export"))
    p.add_argument("--server-url", default="", help="export: the server address to put next to the keys")
    p.set_defaults(fn=_keys)
    p = sub.add_parser("check", help="check configuration, database and signing key")
    p.set_defaults(fn=_check)
    p = sub.add_parser("cleanup", help="delete old validations, sign-in events and expired tokens")
    p.set_defaults(fn=_cleanup)
    p = sub.add_parser("reset-2fa", help="remove an account's two-factor sign-in (lost authenticator)")
    p.add_argument("--email", required=True)
    p.set_defaults(fn=_reset_2fa)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
