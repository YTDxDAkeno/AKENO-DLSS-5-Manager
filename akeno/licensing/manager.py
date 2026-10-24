"""The app's license state: sign-in, activation, online checks and offline use.

Every AKENO module needs an active license; this class decides whether there is one. It runs without the network
whenever it can:

* a **lifetime** license comes with a signed, device-bound offline certificate: AKENO starts fully offline,
  for good, and never needs to reach the server again (it still checks now and then when online, so a refund
  or a removed PC takes effect);
* a **time-limited** license (24 h, 72 h, 30 days) comes with a signed lease valid for the server's offline grace
  period (72 h by default) but never beyond the license's own expiry;
* every check that fails because the server cannot be reached keeps the saved state as it is. Only an answer
  from the server can end a license, and only its own data: settings, games, mods, backups and
  customisations are never touched by licensing.

Methods that talk to the server block; the UI calls them from worker threads.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from akeno import __version__
from akeno.i18n import Msg, msg
from akeno.licensing import config as cfgmod
from akeno.licensing import crypto, device
from akeno.licensing.api import ApiError, LicenseApi, ServerUnavailable
from akeno.licensing.clock import TOLERANCE, ClockGuard
from akeno.licensing.storage import SecureStore

log = logging.getLogger(__name__)

ENTITLEMENT = "akeno-suite"
TYPE_NAMES = {"trial_24h": "Trial (24 hours)", "trial_72h": "Extended trial (72 hours)", "monthly": "Monthly (30 days)",
              "lifetime": "Lifetime"}
EXPIRING_SOON = {"trial_24h": 6 * 3600, "trial_72h": 6 * 3600, "monthly": 3 * 86400}
KEEP_ON_SIGN_OUT = ("clock", "keys")


class Status(str, enum.Enum):
    NOT_CONFIGURED = "not_configured"
    NOT_LOGGED_IN = "not_logged_in"
    NOT_ACTIVATED = "not_activated"
    ACTIVE_ONLINE = "active_online"
    ACTIVE_CACHED = "active_cached"
    LIFETIME_OFFLINE = "lifetime_offline"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    DISABLED = "disabled"
    SERVER_UNAVAILABLE = "server_unavailable"


UNLOCKED = {Status.ACTIVE_ONLINE, Status.ACTIVE_CACHED, Status.LIFETIME_OFFLINE, Status.EXPIRING}

STATUS_TEXT = {
    Status.NOT_CONFIGURED: "This copy of AKENO has no license server address yet.",
    Status.NOT_LOGGED_IN: "Sign in or create an account to use AKENO.",
    Status.NOT_ACTIVATED: "Signed in. Redeem an activation code to unlock AKENO.",
    Status.ACTIVE_ONLINE: "License active and verified with the server.",
    Status.ACTIVE_CACHED: "License active (saved verification; the server is checked again later).",
    Status.LIFETIME_OFFLINE: "Lifetime license, verified on this PC. No connection needed.",
    Status.EXPIRING: "Your license ends soon. Redeem a code to extend it.",
    Status.EXPIRED: "Your license has ended. Redeem a code to continue; your settings and mods are kept.",
    Status.DISABLED: "This license or account was disabled. Contact support.",
    Status.SERVER_UNAVAILABLE: "The license needs a check with the server, which cannot be reached right now.",
}


@dataclass
class LicenseView:
    status: Status
    message: Msg | str
    email: str | None = None
    display_name: str = ""
    license_type: str | None = None
    activated_at: float | None = None
    expires_at: float | None = None
    remaining: float | None = None
    offline_until: float | None = None
    device_id: str | None = None
    device_label: str = ""
    last_verified: float | None = None
    offline_eligible: bool = False
    needs_sign_in: bool = False
    clock_problem: bool = False
    source: str | None = None                 # certificate | lease
    server_url: str = ""
    details: dict = field(default_factory=dict)

    @property
    def unlocked(self) -> bool:
        return self.status in UNLOCKED

    @property
    def lifetime(self) -> bool:
        return self.license_type == "lifetime"

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.license_type or "", self.license_type or "—")


class DeviceLimitReached(Exception):
    def __init__(self, limit: int, devices: list[dict]):
        super().__init__(f"This license allows {limit} PC(s).")
        self.limit = limit
        self.devices = devices


def _ts(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


class LicenseManager:
    def __init__(self, config: cfgmod.LicensingConfig | None = None, store: SecureStore | None = None,
                 api_factory: Callable[[str], LicenseApi] = LicenseApi):
        self.config = config or cfgmod.load()
        self.store = store or SecureStore()
        self._api_factory = api_factory
        self._lock = threading.RLock()
        self.state: dict = self.store.load()
        self.clock = ClockGuard(self.state.setdefault("clock", {}))
        self._access: tuple[str, float] | None = None
        self._verified_this_run = False
        self.listeners: list[Callable[[LicenseView], None]] = []

    # -- helpers -----------------------------------------------------------------------------------------------------
    def _api(self) -> LicenseApi:
        if not self.config.server_url:
            raise ServerUnavailable("No license server address is set.")
        return self._api_factory(self.config.server_url)

    def _save(self) -> None:
        self.store.save(self.state)

    def _notify(self) -> LicenseView:
        view = self.view()
        for cb in list(self.listeners):
            try:
                cb(view)
            except Exception:  # a listener must not break licensing
                log.exception("license listener failed")
        return view

    def trusted_keys(self) -> dict:
        out = {}
        for kid, b64 in {**self.config.keys, **{k["kid"]: k["public_key"] for k in self.state.get("keys", [])}}.items():
            try:
                out[kid] = crypto.public_key(b64)
            except ValueError:
                log.warning("ignoring malformed license key %s", kid)
        return out

    def _learn_keys(self, api: LicenseApi) -> None:
        """New server keys are trusted only through an endorsement by a key already trusted (rotation)."""
        doc = api.call("GET", "/system/keys")
        rows = [k for k in doc.get("keys", []) if isinstance(k, dict) and k.get("kid") and k.get("public_key")]
        learned = self.state.setdefault("keys", [])
        trusted = self.trusted_keys()
        if not trusted:
            # a build without embedded keys: pin the server's keys on this first HTTPS connection
            for k in rows:
                if crypto.kid_for(k["public_key"]) == k["kid"]:
                    learned.append({"kid": k["kid"], "public_key": k["public_key"], "via": "pinned"})
            log.warning("no license keys in this build: pinned %d key(s) from %s", len(learned),
                        self.config.server_url)
            return
        progress = True
        while progress:
            progress = False
            for k in rows:
                if k["kid"] in trusted or not k.get("endorsement"):
                    continue
                try:
                    _, endorsed = crypto.verify(k["endorsement"], trusted, "akeno-key-endorsement")
                except crypto.BadSignature:
                    continue
                if endorsed.get("kid") == k["kid"] and endorsed.get("public_key") == k["public_key"] \
                        and crypto.kid_for(k["public_key"]) == k["kid"]:
                    learned.append({"kid": k["kid"], "public_key": k["public_key"], "via": "endorsed"})
                    trusted[k["kid"]] = crypto.public_key(k["public_key"])
                    progress = True

    def _verify(self, api: LicenseApi | None, token: str, typ: str) -> dict:
        try:
            return crypto.verify(token, self.trusted_keys(), typ)[1]
        except crypto.BadSignature:
            if api is None or crypto.header(token).get("kid") in self.trusted_keys():
                raise
        self._learn_keys(api)
        return crypto.verify(token, self.trusted_keys(), typ)[1]

    def _bound_here(self, payload: dict) -> bool:
        return (payload.get("ent") == ENTITLEMENT and payload.get("dfp") == device.fingerprint()
                and payload.get("dev") == self.state.get("device_id"))

    def _certificate(self) -> dict | None:
        token = self.state.get("certificate")
        if not token:
            return None
        try:
            payload = crypto.verify(token, self.trusted_keys(), "akeno-offline-license")[1]
        except crypto.BadSignature:
            return None
        if payload.get("lt") != "lifetime" or not self._bound_here(payload):
            return None
        return payload

    def _lease(self, now: float, rolled_back: bool) -> dict | None:
        token = self.state.get("lease")
        if not token or rolled_back:
            return None
        try:
            payload = crypto.verify(token, self.trusted_keys(), "akeno-lease")[1]
        except crypto.BadSignature:
            return None
        if not self._bound_here(payload):
            return None
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)) or now >= exp or payload.get("iat", 0) > now + TOLERANCE:
            return None
        return payload

    # -- the state ---------------------------------------------------------------------------------------------------
    def view(self) -> LicenseView:
        with self._lock:
            trusted = self.clock.now()
            now = trusted.now
            account = self.state.get("account") or {}
            known = self.state.get("license") or {}
            base = dict(email=account.get("email"), display_name=account.get("display_name", ""),
                        device_id=self.state.get("device_id"), device_label=self.state.get("device_label", ""),
                        last_verified=self.state.get("last_validation"),
                        needs_sign_in=bool(self.state.get("needs_sign_in")), clock_problem=trusted.rolled_back,
                        server_url=self.config.server_url,
                        activated_at=_ts(known.get("activated_at")), license_type=known.get("license_type"))
            if self.state.get("disabled"):
                return LicenseView(Status.DISABLED, msg(STATUS_TEXT[Status.DISABLED]), **base)
            cert = self._certificate()
            if cert is not None:
                base.update(license_type="lifetime", activated_at=_ts(cert.get("act")), offline_eligible=True,
                            source="certificate")
                status = Status.ACTIVE_ONLINE if self._verified_this_run else Status.LIFETIME_OFFLINE
                return LicenseView(status, msg(STATUS_TEXT[status]), **base)
            lease = self._lease(now, trusted.rolled_back)
            if lease is not None:
                lexp = lease.get("lexp")
                remaining = None if lexp is None else max(0.0, lexp - now)
                base.update(license_type=lease.get("lt"), activated_at=_ts(lease.get("act")) or base["activated_at"],
                            expires_at=lexp, remaining=remaining, offline_until=lease["exp"], source="lease",
                            offline_eligible=lease.get("lt") == "lifetime")
                status = Status.ACTIVE_ONLINE if self._verified_this_run else Status.ACTIVE_CACHED
                if remaining is not None and remaining < EXPIRING_SOON.get(lease.get("lt"), 0):
                    status = Status.EXPIRING
                return LicenseView(status, msg(STATUS_TEXT[status]), **base)
            expires = _ts(known.get("expires_at"))
            base.update(expires_at=expires, remaining=None if expires is None else max(0.0, expires - now))
            if not self.config.server_url and not account:
                return LicenseView(Status.NOT_CONFIGURED, msg(STATUS_TEXT[Status.NOT_CONFIGURED]), **base)
            if not account or (self.state.get("needs_sign_in") and not known):
                return LicenseView(Status.NOT_LOGGED_IN, msg(STATUS_TEXT[Status.NOT_LOGGED_IN]), **base)
            if known.get("status") in ("disabled", "revoked"):
                return LicenseView(Status.DISABLED, msg(STATUS_TEXT[Status.DISABLED]), **base)
            if trusted.rolled_back and known.get("status") == "active":
                return LicenseView(Status.SERVER_UNAVAILABLE, msg(
                    "The PC's clock was turned back. Connect to the internet once so AKENO can verify the license."),
                    **base)
            if known.get("status") == "expired" or (expires is not None and expires <= now):
                return LicenseView(Status.EXPIRED, msg(STATUS_TEXT[Status.EXPIRED]), **base)
            if not known or known.get("status") == "none":
                return LicenseView(Status.NOT_ACTIVATED, msg(STATUS_TEXT[Status.NOT_ACTIVATED]), **base)
            if self.state.get("needs_sign_in"):
                return LicenseView(Status.NOT_LOGGED_IN, msg("Sign in again to verify your license."), **base)
            return LicenseView(Status.SERVER_UNAVAILABLE, msg(STATUS_TEXT[Status.SERVER_UNAVAILABLE]), **base)

    def due(self) -> bool:
        """An online check is due (on start, and then every validation interval)."""
        with self._lock:
            if not self.state.get("refresh_token") or not self.config.server_url:
                return False
            if not self._verified_this_run:
                last = self.state.get("last_attempt", 0)
                return time.time() - last > 60          # once per start, not in a loop when offline
            interval = self.state.get("validation_interval", 12 * 3600)
            return self.clock.now().now - (self.state.get("last_validation") or 0) >= interval

    # -- tokens ------------------------------------------------------------------------------------------------------
    def _store_tokens(self, tokens: dict) -> None:
        self.state["refresh_token"] = tokens["refresh_token"]
        self._access = (tokens["access_token"], time.monotonic() + max(30, int(tokens.get("expires_in", 900)) - 30))
        user = tokens.get("user") or {}
        if user:
            self.state["account"] = {k: user.get(k) for k in ("id", "email", "display_name", "role", "language",
                                                              "email_verified")}
        self.state.pop("needs_sign_in", None)
        self._save()                       # the old refresh token is no longer valid: keep the new one at once

    def _access_token(self, api: LicenseApi) -> str:
        if self._access and time.monotonic() < self._access[1]:
            return self._access[0]
        refresh = self.state.get("refresh_token")
        if not refresh:
            raise ApiError(401, "not_authenticated", "Sign in first.")
        self._store_tokens(api.call("POST", "/auth/refresh", {"refresh_token": refresh}))
        return self._access[0]

    def _authed(self, api: LicenseApi, method: str, path: str, body: dict | None = None):
        try:
            return api.call(method, path, body, self._access_token(api))
        except ApiError as exc:
            if exc.status == 401 and exc.code in ("invalid_access_token", "session_revoked") and self._access:
                self._access = None                     # the access token ended early: refresh once and retry
                return api.call(method, path, body, self._access_token(api))
            raise

    def _handle_refusal(self, exc: ApiError) -> None:
        if exc.status == 401:
            self.state.pop("refresh_token", None)
            self._access = None
            self.state["needs_sign_in"] = True
        elif exc.code == "account_disabled":
            self.state["disabled"] = True
            self.state.pop("lease", None)
            self.state.pop("certificate", None)
        self._save()

    # -- account actions (blocking) ----------------------------------------------------------------------------------
    def _begin_session(self, tokens: dict) -> None:
        user_id = (tokens.get("user") or {}).get("id")
        if (self.state.get("account") or {}).get("id") not in (None, user_id):
            self._forget(keep_device=False)          # another account signs in on this PC
        self.state.pop("disabled", None)
        self._store_tokens(tokens)

    def sign_in(self, email: str, password: str, totp_code: str | None = None) -> LicenseView:
        with self._lock:
            api = self._api()
            body = {"email": email.strip(), "password": password}
            if totp_code:
                body["totp_code"] = totp_code.strip()
            self._begin_session(api.call("POST", "/auth/login", body))
            return self._after_sign_in(api)

    def create_account(self, email: str, password: str, display_name: str = "", language: str = "en") -> LicenseView:
        with self._lock:
            api = self._api()
            self._begin_session(api.call("POST", "/auth/register", {
                "email": email.strip(), "password": password, "display_name": display_name.strip(),
                "language": "de" if language == "de" else "en"}))
            return self._after_sign_in(api)

    def _after_sign_in(self, api: LicenseApi) -> LicenseView:
        if not self.trusted_keys():
            self._learn_keys(api)
        self._register_device(api)
        return self.validate(api)

    def _register_device(self, api: LicenseApi) -> None:
        try:
            dev = self._authed(api, "POST", "/devices/register", {
                "fingerprint": device.fingerprint(), "label": self.state.get("device_label") or device.label(),
                "os": device.os_name(), "app_version": __version__})
        except ApiError as exc:
            if exc.code == "device_limit_reached":
                self._save()
                raise DeviceLimitReached(exc.details.get("limit", 0), exc.details.get("devices", [])) from exc
            raise
        self.state["device_id"] = dev["id"]
        self.state["device_label"] = dev.get("label", "")
        self._save()

    def validate(self, api: LicenseApi | None = None) -> LicenseView:
        """Check the license online and refresh the lease (and the lifetime certificate)."""
        with self._lock:
            self.state["last_attempt"] = time.time()
            try:
                api = api or self._api()
                if not self.state.get("device_id"):
                    self._register_device(api)
                out = self._validate_once(api)
                if out["result"] == "device_not_registered":
                    self.state.pop("device_id", None)     # e.g. an administrator reset the PCs: take a slot again
                    self._register_device(api)
                    out = self._validate_once(api)
                self._apply(api, out)
            except ServerUnavailable as exc:
                self.state["last_error"] = {"kind": "maintenance" if exc.maintenance else "offline",
                                            "message": str(exc), "at": time.time()}
                self._save()
                raise
            except ApiError as exc:
                self.state["last_error"] = {"kind": "refused", "code": exc.code, "message": exc.message,
                                            "at": time.time()}
                self._handle_refusal(exc)
                raise
            return self._notify()

    def _validate_once(self, api: LicenseApi) -> dict:
        serial = None
        cert = self.state.get("certificate")
        if cert:
            try:
                serial = crypto.verify(cert, self.trusted_keys(), "akeno-offline-license")[1].get("serial")
            except crypto.BadSignature:
                serial = None
        return self._authed(api, "POST", "/licenses/validate", {
            "device_id": self.state["device_id"], "app_version": __version__, "certificate_serial": serial})

    def _apply(self, api: LicenseApi, out: dict) -> None:
        server_time = _ts(out.get("server_time")) or time.time()
        self.clock.learn(server_time)
        self.state["license"] = out.get("license") or {}
        self.state["validation_interval"] = int(out.get("validation_interval_seconds") or 12 * 3600)
        self.state["min_client_version"] = out.get("min_client_version")
        self.state["latest_client_version"] = out.get("latest_client_version")
        if out.get("certificate_revoked") or out.get("result") != "ok":
            self.state.pop("certificate", None)
        lease = out.get("lease")
        if lease:
            payload = self._verify(api, lease, "akeno-lease")
            old = self.state.get("lease_iat", 0)
            if payload.get("iat", 0) >= old:          # never go back to an older lease
                self.state["lease"] = lease
                self.state["lease_iat"] = payload.get("iat", 0)
        else:
            self.state.pop("lease", None)
            self.state.pop("lease_iat", None)
        cert = out.get("offline_certificate")
        if cert and cert != "unchanged":
            self._verify(api, cert, "akeno-offline-license")
            self.state["certificate"] = cert
        self.state.pop("disabled", None)
        if out.get("result") == "account_disabled":
            self.state["disabled"] = True
        self.state["last_validation"] = server_time
        self.state.pop("last_error", None)
        self._verified_this_run = True
        self._save()

    def check_if_due(self) -> LicenseView:
        """Background check: never raises; offline simply keeps the saved state."""
        if self.due():
            try:
                return self.validate()
            except (ServerUnavailable, ApiError, DeviceLimitReached) as exc:
                log.info("license check postponed: %s", exc)
            except crypto.BadSignature as exc:
                log.warning("license server answer failed verification: %s", exc)
        return self.view()

    def redeem(self, code: str) -> LicenseView:
        with self._lock:
            api = self._api()
            out = self._authed(api, "POST", "/licenses/redeem", {"code": code})
            self.state["license"] = out.get("license") or {}
            self.state["last_redeemed"] = out.get("code_type")
            self._save()
            return self.validate(api)

    def request_password_reset(self, email: str) -> None:
        self._api().call("POST", "/auth/password-reset/request", {"email": email.strip()})

    def devices(self) -> list[dict]:
        with self._lock:
            return list(self._authed(self._api(), "GET", "/devices"))

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            self._authed(self._api(), "DELETE", f"/devices/{device_id}")
            if device_id == self.state.get("device_id"):
                self._forget(keep_device=False)
                self._save()

    def rename_device(self, label: str) -> None:
        with self._lock:
            dev = self._authed(self._api(), "PATCH", f"/devices/{self.state['device_id']}", {"label": label})
            self.state["device_label"] = dev.get("label", label)
            self._save()

    def retry_device(self) -> LicenseView:
        """After removing a PC in the device-limit dialog: take the free slot and verify."""
        with self._lock:
            api = self._api()
            self._register_device(api)
            return self.validate(api)

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._authed(self._api(), "GET", "/licenses/history"))

    def sign_out(self) -> LicenseView:
        with self._lock:
            if self.state.get("refresh_token") and self.config.server_url:
                try:
                    self._authed(self._api(), "POST", "/auth/logout", {})
                except (ServerUnavailable, ApiError):
                    pass                        # signing out on this PC works offline too
            self._forget(keep_device=False)
            self._save()
            return self._notify()

    def _forget(self, keep_device: bool) -> None:
        keep = {k: self.state[k] for k in KEEP_ON_SIGN_OUT if k in self.state}
        if keep_device and "device_id" in self.state:
            keep["device_id"] = self.state["device_id"]
        self.state.clear()
        self.state.update(keep)
        self.clock = ClockGuard(self.state.setdefault("clock", {}))
        self._access = None
        self._verified_this_run = False

    def set_server(self, url: str) -> None:
        from akeno.licensing.api import check_url
        with self._lock:
            url = check_url(url) if url else ""
            cfgmod.save_server_url(url)
            self.config = cfgmod.load()


_manager: LicenseManager | None = None


def manager() -> LicenseManager:
    global _manager
    if _manager is None:
        _manager = LicenseManager()
    return _manager


def set_manager(m: LicenseManager | None) -> None:
    global _manager
    _manager = m
