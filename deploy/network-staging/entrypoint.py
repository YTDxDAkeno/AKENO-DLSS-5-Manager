"""Staging-only guard. Never read deploy/.env or production secrets; never print credentials."""
import os
from pathlib import Path
from urllib.parse import quote

def prepare_environment():
    if os.environ.get("AKENO_NETWORK_STAGING") != "1":
        raise SystemExit("STAGING_GUARD_REFUSED")
    if os.environ.get("AKENO_ROLE") not in ("api", "web", "admin"):
        raise SystemExit("STAGING_ROLE_REFUSED")
    if os.environ.get("AKENO_PUBLIC_URL") != "https://lan-staging.akeno.online":
        raise SystemExit("STAGING_ORIGIN_REFUSED")
    if "AKENO_DATABASE_URL" in os.environ:
        raise SystemExit("EXTERNAL_DATABASE_URL_REFUSED")
    password = Path("/run/secrets/db_app").read_text().strip()
    if len(password) < 32:
        raise SystemExit("STAGING_DB_SECRET_REFUSED")
    # Fixed staging service/database; no caller-supplied hostname or DSN.
    os.environ["AKENO_DATABASE_URL"] = (
        "postgresql://akeno_network_staging:" + quote(password, safe="") +
        "@staging-db:5432/akeno_network_staging")
    for field, path in (("TOKEN_SECRET", "/run/secrets/token"), ("CODE_PEPPER", "/run/secrets/pepper")):
        if "AKENO_" + field in os.environ:
            raise SystemExit("INLINE_SECRET_REFUSED")
        value = Path(path).read_text().strip()
        if len(value) < 32:
            raise SystemExit("STAGING_SECRET_REFUSED")
        os.environ["AKENO_" + field] = value
    os.environ["AKENO_KEYS_DIR"] = "/data/keys"
    os.environ["AKENO_MAIL_BACKEND"] = "file"
    os.environ["AKENO_MAIL_DIR"] = "/data/mail"
    os.environ["AKENO_COOKIE_SECURE"] = "false" if os.environ["AKENO_ROLE"] == "admin" else "true"

def main():
    prepare_environment()
    import sys
    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])
    if os.environ["AKENO_ROLE"] == "api":
        from akeno_server.cli import main as cli
        if cli(["migrate"]) != 0:
            raise SystemExit("STAGING_MIGRATION_FAILED")
    os.execvp("uvicorn", ["uvicorn", "akeno_server.main:get_app", "--factory", "--host", "0.0.0.0",
                          "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips",
                          "172.30.78.1", "--no-access-log", "--no-server-header"])

if __name__ == "__main__":
    main()
