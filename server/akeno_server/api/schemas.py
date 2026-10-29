"""Request and response bodies of the public API (shown in the OpenAPI document at ``/api/v1/docs``)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LicenseTypeName = Literal["trial_24h", "trial_72h", "monthly", "lifetime"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -- auth ------------------------------------------------------------------------------------------------------------
class RegisterIn(Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    display_name: str = Field("", max_length=80)
    language: Literal["en", "de"] = "en"


class LoginIn(Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    totp_code: str | None = Field(None, max_length=20)


class RefreshIn(Strict):
    refresh_token: str = Field(max_length=200)


class LogoutIn(Strict):
    refresh_token: str | None = Field(None, max_length=200)


class EmailIn(Strict):
    email: str = Field(max_length=320)


class ResetConfirmIn(Strict):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=256)


class TokenIn(Strict):
    token: str = Field(max_length=200)


class ChangePasswordIn(Strict):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ChangeEmailIn(Strict):
    password: str = Field(max_length=256)
    new_email: str = Field(max_length=320)


class PatchMeIn(Strict):
    display_name: str | None = Field(None, max_length=80)
    language: Literal["en", "de"] | None = None


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    language: str
    email_verified: bool
    two_factor: bool
    created_at: datetime


EmailDelivery = Literal["sent", "failed", "unavailable"]


class TokensOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str
    user: UserOut
    # registration only: what happened to the confirmation e-mail (sent = accepted by the mail server)
    verification_email: EmailDelivery | None = None


class EmailSentOut(BaseModel):
    status: Literal["sent", "already_verified"]
    email: str | None = None


# -- licenses --------------------------------------------------------------------------------------------------------
class LicenseOut(BaseModel):
    id: str | None = None
    active: bool
    status: str
    entitlement: str | None
    license_type: LicenseTypeName | None = None
    lifetime: bool = False
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int | None = None
    device_limit: int | None = None
    offline_eligible: bool = False
    server_time: datetime | None = None


class EntitlementOut(BaseModel):
    entitlement: str
    name: str
    type: str
    status: str
    active: bool
    lifetime: bool
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int | None = None
    device_limit: int | None = None


class AccessOut(BaseModel):
    entitlements: list[EntitlementOut]
    running: list[str]
    modules: list[str]
    server_time: datetime


class RedeemIn(Strict):
    code: str = Field(max_length=64)
    # 1: this app understands module licenses (AKENO 3.2.0+); older apps are told to update for a module code
    access: int | None = Field(None, ge=1, le=1)


class RedeemOut(BaseModel):
    license: LicenseOut
    code_type: LicenseTypeName
    already_redeemed: bool
    entitlement: str = "akeno-suite"
    access: AccessOut | None = None


class ValidateIn(Strict):
    device_id: str = Field(max_length=40)
    app_version: str = Field("", max_length=40)
    certificate_serial: str | None = Field(None, max_length=40)
    # a fresh random value per request: the signed answer must carry it (no replayed answers)
    nonce: str = Field(min_length=22, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # 1: answer with the access token family (every entitlement); absent: the original suite-only answer
    access: int | None = Field(None, ge=1, le=1)


class ValidateOut(BaseModel):
    result: str
    validation: str
    license: LicenseOut
    server_time: datetime
    lease: str | None
    offline_certificate: str | None
    certificate_revoked: bool
    validation_interval_seconds: int
    offline_grace_seconds: int
    min_client_version: str | None
    latest_client_version: str | None
    access: AccessOut | None = None


class CertificateIn(Strict):
    device_id: str = Field(max_length=40)


class CertificateOut(BaseModel):
    certificate: str
    serial: str


class HistoryItem(BaseModel):
    at: datetime
    kind: str
    entitlement: str = "akeno-suite"
    old_type: str | None
    new_type: str | None
    old_expires_at: datetime | None
    new_expires_at: datetime | None
    note: str


# -- devices ---------------------------------------------------------------------------------------------------------
class DeviceIn(Strict):
    fingerprint: str = Field(min_length=64, max_length=64)
    label: str = Field("", max_length=80)
    os: str = Field("", max_length=80)
    app_version: str = Field("", max_length=40)


class DevicePatchIn(Strict):
    label: str = Field(min_length=1, max_length=80)


class DeviceOut(BaseModel):
    id: str
    label: str
    os: str
    app_version: str
    created_at: datetime
    last_seen_at: datetime
    active: bool
    current: bool = False


# -- admin -----------------------------------------------------------------------------------------------------------
class GenerateCodesIn(Strict):
    code_type: LicenseTypeName
    entitlement: str = Field("akeno-suite", max_length=40)       # akeno-suite (AKENO Full) or one module
    duration_days: int | None = Field(None, ge=1, le=3650)       # a time code's own length (monthly codes)
    count: int = Field(1, ge=1, le=500)
    device_allowance: int | None = Field(None, ge=1, le=50)
    expires_in_days: int | None = Field(None, ge=1, le=3650)
    note: str = Field("", max_length=300)
    batch: str = Field("", max_length=80)
    assigned_email: str | None = Field(None, max_length=320)


class CodeOut(BaseModel):
    id: str
    display: str
    code_type: str
    entitlement: str = "akeno-suite"
    product: str = "AKENO Full"
    duration_days: int | None = None
    length: str = ""
    status: str
    device_allowance: int | None
    expires_at: datetime | None
    note: str
    batch: str
    assigned_email: str | None
    created_at: datetime
    redeemed_at: datetime | None
    redeemed_by_email: str | None
    reserved_for: str


class GeneratedOut(BaseModel):
    batch: str
    codes: list[str]
    items: list[CodeOut]
    warning: str = "Codes are shown only now. AKENO stores only a verifier and cannot show them again."


class ReserveIn(Strict):
    reserved_for: str = Field(min_length=1, max_length=160)


class AdminUserPatch(Strict):
    role: Literal["user", "support", "admin", "super_admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    disabled_reason: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=5000)
    display_name: str | None = Field(None, max_length=80)


class ExtendIn(Strict):
    hours: int = Field(ge=1, le=24 * 3650)
    note: str = Field("", max_length=300)


class SetLicenseIn(Strict):
    license_type: LicenseTypeName
    expires_at: datetime | None = None
    note: str = Field("", max_length=300)


class GrantIn(Strict):
    entitlement: str = Field(max_length=40)
    days: int | None = Field(None, ge=1, le=3650)                # None: lifetime
    device_limit: int | None = Field(None, ge=1, le=50)
    note: str = Field("", max_length=300)


class GrantExtendIn(Strict):
    entitlement: str = Field(max_length=40)
    days: int = Field(ge=1, le=3650)
    note: str = Field("", max_length=300)


class GrantStatusIn(Strict):
    entitlement: str = Field(max_length=40)
    status: Literal["active", "disabled", "revoked"]
    note: str = Field("", max_length=300)


class LicenseStatusIn(Strict):
    status: Literal["active", "disabled", "revoked"]
    note: str = Field("", max_length=300)


class NoteIn(Strict):
    note: str = Field("", max_length=300)


class TransferIn(Strict):
    email: str = Field(max_length=320)


class Page(BaseModel):
    items: list
    page: int
    per_page: int
    total: int
