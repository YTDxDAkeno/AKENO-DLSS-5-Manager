"""Server configuration from environment variables (``AKENO_*``), normally set through ``deploy/.env``.

Secrets can also be given as files (``AKENO_TOKEN_SECRET_FILE=/run/secrets/token``), so they never have to appear
in a process listing or in ``docker inspect``. In production the server refuses to start without them; in
development and test it generates throwaway values and says so in the log.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

SECRET_FIELDS = ("token_secret", "code_pepper", "smtp_password", "vpn_operator_token")


class ConfigError(RuntimeError):
    pass


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AKENO_", extra="ignore", case_sensitive=False)

    environment: Literal["production", "development", "test"] = "production"
    # which part of the site this process serves: the API, the public/account website, the admin website, or all
    role: Literal["all", "api", "web", "admin"] = "all"
    network_enabled: bool = False  # Explicit opt-in; existing production installs expose no network API.
    network_database_path: Path = Path("/data/network.sqlite3")
    network_signing_key_path: Path = Path("/data/network-signing.key")
    # Independent opt-in: no country catalog, control API or exit is enabled by an ordinary update.
    vpn_enabled: bool = False
    vpn_database_path: Path = Path("/data/vpn.sqlite3")
    vpn_probe_keys_path: Path = Path("/data/vpn-probe-public-keys.json")
    vpn_operator_token: SecretStr = SecretStr("")
    # LAN Play data plane (UDP). Empty bind: no coordinator, rooms stay control-plane only. The host is what members'
    # WireGuard uses as the coordinator/relay endpoint (public name or address of this server).
    network_coordinator_bind: str = ""
    network_coordinator_host: str = ""
    network_coordinator_key_path: Path = Path("/data/network-coordinator.key")

    database_url: str = "sqlite:///./akeno-dev.sqlite3"
    public_url: str = ""                # e.g. https://licenses.your-domain (links in e-mails); empty: from the request
    admin_url: str = ""                 # where the admin website is reachable, if it has its own host name
    download_url: str = "https://github.com/YTDxDAkeno/AKENO-Releases/releases/latest"

    token_secret: SecretStr = SecretStr("")
    code_pepper: SecretStr = SecretStr("")
    keys_dir: Path = Path("/data/keys")

    mail_backend: Literal["smtp", "file", "console"] = "console"
    mail_from: str = "AKENO <no-reply@localhost>"
    mail_dir: Path = Path("/data/mail")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"

    # X-Forwarded-For is only believed when the direct peer is one of these (the reverse proxy)
    trusted_proxies: str = "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    cookie_secure: bool = True

    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 2

    access_token_minutes: int = Field(15, ge=1, le=120)
    refresh_token_days: int = Field(30, ge=1, le=365)
    web_session_hours: int = Field(12, ge=1, le=24 * 14)
    rate_limit_enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _secret_files(cls, data):
        if isinstance(data, dict):
            for name in SECRET_FIELDS:
                path = os.environ.get(f"AKENO_{name.upper()}_FILE")
                if path and not data.get(name):
                    data[name] = Path(path).read_text(encoding="utf-8").strip()
        return data

    @model_validator(mode="after")
    def _check(self):
        for name in ("token_secret", "code_pepper"):
            value = getattr(self, name).get_secret_value()
            if not value:
                if self.environment == "production":
                    raise ConfigError(f"AKENO_{name.upper()} is not set. deploy/install.sh generates it into .env.")
                setattr(self, name, SecretStr(secrets.token_urlsafe(48)))
                log.warning("AKENO_%s not set: using a temporary value (%s mode only)", name.upper(),
                            self.environment)
            elif len(value) < 32:
                raise ConfigError(f"AKENO_{name.upper()} must be at least 32 characters long.")
        if self.public_url and not self.public_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ConfigError("AKENO_PUBLIC_URL must be an https:// address.")
        self.public_url = self.public_url.rstrip("/")
        self.admin_url = self.admin_url.rstrip("/")
        if self.vpn_enabled and len(self.vpn_operator_token.get_secret_value()) < 48:
            raise ConfigError("AKENO_VPN_OPERATOR_TOKEN must contain at least 48 characters when VPN is enabled.")
        return self

    @property
    def proxies(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        out = []
        for part in self.trusted_proxies.split(","):
            if part.strip():
                out.append(ipaddress.ip_network(part.strip(), strict=False))
        return out

    @property
    def serves_api(self) -> bool:
        return self.role in ("all", "api")

    @property
    def serves_web(self) -> bool:
        return self.role in ("all", "web")

    @property
    def serves_admin(self) -> bool:
        return self.role in ("all", "admin")


@lru_cache
def get_config() -> ServerConfig:
    return ServerConfig()


def reset_config() -> None:
    get_config.cache_clear()
