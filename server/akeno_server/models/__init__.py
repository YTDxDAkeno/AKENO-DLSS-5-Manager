"""Database tables. Every time is stored in UTC; every secret token is stored only as a hash or verifier."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
                        Text, TypeDecorator, UniqueConstraint, Uuid)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from akeno_server.clock import utcnow


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every database (SQLite has no time zones of its own)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given to a UTC column")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# BigInteger primary keys autoincrement on PostgreSQL; SQLite only does that for INTEGER
BigId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime}


class Role(str, enum.Enum):
    USER = "user"
    SUPPORT = "support"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

    @property
    def privileged(self) -> bool:
        return self is not Role.USER

    @property
    def rank(self) -> int:
        return {"user": 0, "support": 1, "admin": 2, "super_admin": 3}[self.value]


class LicenseType(str, enum.Enum):
    TRIAL_24H = "trial_24h"
    TRIAL_72H = "trial_72h"
    MONTHLY = "monthly"
    LIFETIME = "lifetime"

    @property
    def seconds(self) -> int | None:
        return {"trial_24h": 24 * 3600, "trial_72h": 72 * 3600, "monthly": 30 * 86400, "lifetime": None}[self.value]

    @property
    def trial(self) -> bool:
        return self in (LicenseType.TRIAL_24H, LicenseType.TRIAL_72H)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True)
    email_verified_at: Mapped[datetime | None]
    display_name: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.USER.value)
    status: Mapped[str] = mapped_column(String(20), default="active")        # active | disabled
    disabled_reason: Mapped[str] = mapped_column(String(300), default="")
    language: Mapped[str] = mapped_column(String(5), default="en")
    notes: Mapped[str] = mapped_column(Text, default="")                      # admin-only notes
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None]
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]

    credential: Mapped["UserCredential"] = relationship(back_populates="user", uselist=False,
                                                        cascade="all, delete-orphan")
    license: Mapped["License | None"] = relationship(back_populates="user", uselist=False)

    __table_args__ = (CheckConstraint("role in ('user','support','admin','super_admin')", name="ck_users_role"),
                      CheckConstraint("status in ('active','disabled')", name="ck_users_status"))

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def active(self) -> bool:
        return self.status == "active"


class UserCredential(Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    password_changed_at: Mapped[datetime] = mapped_column(default=utcnow)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(255))          # AES-GCM, key derived from the pepper
    totp_enabled_at: Mapped[datetime | None]
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)            # a TOTP code is accepted only once
    recovery_codes: Mapped[list] = mapped_column(JSON, default=list)          # SHA-256 of unused recovery codes

    user: Mapped[User] = relationship(back_populates="credential")


class UserSession(Base):
    """An app sign-in (refresh token, rotated on every use) or a website sign-in (cookie)."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))                             # app | web
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    rotated_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str] = mapped_column(String(60), default="")
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    csrf_token: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))

    __table_args__ = (CheckConstraint("kind in ('app','web')", name="ck_sessions_kind"),)

    def live(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


class Device(Base):
    """A Windows PC, known only by a pseudonymous fingerprint (a hash computed by the app)."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(80), default="")
    os: Mapped[str] = mapped_column(String(80), default="")
    app_version: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[str] = mapped_column(String(20), default="")          # user | admin

    __table_args__ = (UniqueConstraint("user_id", "fingerprint", name="uq_devices_user_fingerprint"),)

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class License(Base):
    """The one license of an account. Codes extend it; its type says how long it runs, never which modules."""

    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    license_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")        # active | disabled | revoked
    activated_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None]                                       # None: lifetime
    device_limit: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="license")

    __table_args__ = (
        CheckConstraint("license_type in ('trial_24h','trial_72h','monthly','lifetime')", name="ck_licenses_type"),
        CheckConstraint("status in ('active','disabled','revoked')", name="ck_licenses_status"),
        CheckConstraint("(license_type = 'lifetime') = (expires_at IS NULL)", name="ck_licenses_lifetime_expiry"),
        CheckConstraint("device_limit >= 1", name="ck_licenses_device_limit"),
    )

    @property
    def lifetime(self) -> bool:
        return self.license_type == LicenseType.LIFETIME.value

    def running(self, now: datetime) -> bool:
        return self.status == "active" and (self.expires_at is None or self.expires_at > now)


class LicenseCode(Base):
    """An activation code. Only an HMAC verifier is stored: the code itself is shown once, when generated."""

    __tablename__ = "license_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    verifier: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(12))                             # last group, e.g. "7KQ2"
    code_type: Mapped[str] = mapped_column(String(20))
    device_allowance: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="available")     # available|reserved|redeemed|revoked
    expires_at: Mapped[datetime | None]                                       # the unused code stops working then
    note: Mapped[str] = mapped_column(String(300), default="")
    batch: Mapped[str] = mapped_column(String(80), default="", index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    reserved_at: Mapped[datetime | None]
    reserved_for: Mapped[str] = mapped_column(String(160), default="")
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime | None]
    redeemed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)

    __table_args__ = (
        CheckConstraint("code_type in ('trial_24h','trial_72h','monthly','lifetime')", name="ck_codes_type"),
        CheckConstraint("status in ('available','reserved','redeemed','revoked')", name="ck_codes_status"),
        Index("ix_license_codes_status_created", "status", "created_at"),
    )

    def display(self) -> str:
        return f"AKENO-····-····-····-{self.hint}"


class LicenseRedemption(Base):
    """A code's single use. The unique code_id is what makes a second redemption impossible, even in a race."""

    __tablename__ = "license_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    code_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("license_codes.id", ondelete="RESTRICT"), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    license_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"))
    redeemed_at: Mapped[datetime] = mapped_column(default=utcnow)
    previous_type: Mapped[str | None] = mapped_column(String(20))
    previous_expires_at: Mapped[datetime | None]
    new_type: Mapped[str] = mapped_column(String(20))
    new_expires_at: Mapped[datetime | None]
    channel: Mapped[str] = mapped_column(String(10), default="app")         # app | web
    ip: Mapped[str] = mapped_column(String(45), default="")


class LicenseEvent(Base):
    """License history shown to the user and to administrators: redemptions and every administrative change."""

    __tablename__ = "license_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    license_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    old_type: Mapped[str | None] = mapped_column(String(20))
    new_type: Mapped[str | None] = mapped_column(String(20))
    old_expires_at: Mapped[datetime | None]
    new_expires_at: Mapped[datetime | None]
    code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("license_codes.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(String(300), default="")


class OfflineCertificate(Base):
    """A signed lifetime license for one device. It has no expiry; revocation takes effect when the app is online."""

    __tablename__ = "offline_certificates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    kid: Mapped[str] = mapped_column(String(32))
    issued_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str] = mapped_column(String(60), default="")
    payload_sha256: Mapped[str] = mapped_column(String(64))


class LicenseValidation(Base):
    __tablename__ = "license_validations"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    license_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    result: Mapped[str] = mapped_column(String(30))
    app_version: Mapped[str] = mapped_column(String(40), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="")
    actor_role: Mapped[str] = mapped_column(String(20), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(30), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class SignInEvent(Base):
    __tablename__ = "sign_in_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    success: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(40), default="")
    channel: Mapped[str] = mapped_column(String(10), default="app")
    ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    ip: Mapped[str] = mapped_column(String(45), default="")


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))                        # the address being confirmed
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class SigningKey(Base):
    """Public half of a license signing key. The private key exists only in the server's key directory."""

    __tablename__ = "signing_keys"

    kid: Mapped[str] = mapped_column(String(32), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(64))                     # raw Ed25519 key, base64url
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    retired_at: Mapped[datetime | None]
    endorsement: Mapped[str | None] = mapped_column(Text)                  # signed by the previous key (rotation)
    endorsed_by: Mapped[str | None] = mapped_column(String(32))


class ServerSetting(Base):
    __tablename__ = "server_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
