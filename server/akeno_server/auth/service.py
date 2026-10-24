"""Accounts: registration, sign-in, token rotation, sessions, password reset and e-mail verification."""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from akeno_server import mail, settings
from akeno_server.auth import passwords, tokens, totp
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.errors import ApiError, bad_request, conflict, forbidden, too_many, unauthorized, unavailable
from akeno_server.models import (EmailVerificationToken, PasswordResetToken, Role, SignInEvent, User,
                                 UserCredential, UserSession)

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
LOCK_AFTER = 5
LOCK_MINUTES = 15
ROTATION_GRACE = timedelta(seconds=60)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def check_email(email: str) -> str:
    email = (email or "").strip()
    if len(email) > 320 or not EMAIL.match(email):
        raise bad_request("invalid_email", "Enter a valid e-mail address.")
    return email


def user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email_normalized == normalize_email(email))).first()


def _policy(password: str, email: str) -> None:
    try:
        passwords.check_policy(password, email)
    except passwords.PasswordPolicyError as exc:
        raise bad_request("weak_password", str(exc)) from exc


def create_user(db: Session, email: str, password: str, display_name: str = "", language: str = "en",
                role: Role = Role.USER, verified: bool = False) -> User:
    email = check_email(email)
    _policy(password, email)
    if user_by_email(db, email) is not None:
        raise conflict("email_taken", "An account with this e-mail address already exists.")
    user = User(email=email, email_normalized=normalize_email(email), display_name=(display_name or "")[:80].strip(),
                language="de" if language == "de" else "en", role=role.value,
                email_verified_at=utcnow() if verified else None)
    user.credential = UserCredential(password_hash=passwords.hash_password(password))
    db.add(user)
    db.flush()
    return user


def _event(db: Session, user: User | None, email: str, ok: bool, reason: str, channel: str, ip: str,
           agent: str) -> None:
    db.add(SignInEvent(user_id=user.id if user else None, email=email[:320], success=ok, reason=reason,
                       channel=channel, ip=ip, user_agent=agent[:300]))


def authenticate(db: Session, email: str, password: str, totp_code: str | None, channel: str, ip: str = "",
                 agent: str = "") -> User:
    """Check a sign-in. Privileged accounts need their second factor. Commits failure counters."""
    now = utcnow()
    user = user_by_email(db, email)
    if user is None:
        passwords.burn_time(password)
        _event(db, None, email, False, "unknown_email", channel, ip, agent)
        db.commit()
        raise unauthorized("invalid_credentials", "E-mail address or password is wrong.")
    if user.locked_until and user.locked_until > now:
        _event(db, user, email, False, "locked", channel, ip, agent)
        db.commit()
        raise too_many((user.locked_until - now).total_seconds())
    if not passwords.verify_password(user.credential.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= LOCK_AFTER:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES * (1 + (user.failed_logins - LOCK_AFTER) // 5))
        _event(db, user, email, False, "wrong_password", channel, ip, agent)
        db.commit()
        raise unauthorized("invalid_credentials", "E-mail address or password is wrong.")
    if not user.active:
        _event(db, user, email, False, "disabled", channel, ip, agent)
        db.commit()
        raise forbidden("account_disabled", "This account is disabled.")
    cred = user.credential
    if cred.totp_enabled_at:
        if not totp_code:
            raise unauthorized("totp_required", "Enter the code from your authenticator app.")
        if not totp.verify(cred, totp_code):
            user.failed_logins += 1
            _event(db, user, email, False, "wrong_totp", channel, ip, agent)
            db.commit()
            raise unauthorized("invalid_totp", "The authenticator code is wrong or was already used.")
    elif user.role_enum.privileged and channel != "web":
        # the website walks administrators through two-factor enrolment; the API never signs them in without it
        raise forbidden("totp_enrolment_required", "Set up two-factor sign-in on the website first.")
    if passwords.needs_rehash(cred.password_hash):
        cred.password_hash = passwords.hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    _event(db, user, email, True, "ok", channel, ip, agent)
    return user


def second_factor(db: Session, user: User, code: str, channel: str, ip: str = "", agent: str = "") -> None:
    """The website's second sign-in step (after the password was checked). Commits failure counters."""
    now = utcnow()
    if not user.active:
        raise forbidden("account_disabled", "This account is disabled.")
    if user.locked_until and user.locked_until > now:
        raise too_many((user.locked_until - now).total_seconds())
    if not totp.verify(user.credential, code or ""):
        user.failed_logins += 1
        if user.failed_logins >= LOCK_AFTER:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        _event(db, user, user.email, False, "wrong_totp", channel, ip, agent)
        db.commit()
        raise unauthorized("invalid_totp", "The authenticator code is wrong or was already used.")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    _event(db, user, user.email, True, "ok", channel, ip, agent)


@dataclass
class IssuedSession:
    session: UserSession
    token: str                    # refresh token (app) or cookie value (web)
    access_token: str | None = None
    expires_in: int = 0


def start_session(db: Session, user: User, kind: str, ip: str = "", agent: str = "",
                  mfa: bool = False) -> IssuedSession:
    cfg = get_config()
    secret = tokens.new_secret()
    life = timedelta(days=cfg.refresh_token_days) if kind == "app" else timedelta(hours=cfg.web_session_hours)
    sess = UserSession(user_id=user.id, kind=kind, token_hash=tokens.digest(secret), expires_at=utcnow() + life,
                       ip=ip, user_agent=agent[:300], mfa_verified=mfa,
                       csrf_token=secrets.token_urlsafe(24) if kind == "web" else "")
    db.add(sess)
    db.flush()
    issued = IssuedSession(sess, secret)
    if kind == "app":
        issued.access_token, issued.expires_in = tokens.issue_access(str(user.id), str(sess.id), user.role)
    return issued


def refresh(db: Session, refresh_token: str, ip: str = "") -> tuple[User, IssuedSession]:
    """Rotate a refresh token. A token used again after its grace period ends the whole session (theft)."""
    now = utcnow()
    digest = tokens.digest(refresh_token or "")
    sess = db.scalars(select(UserSession).where(UserSession.token_hash == digest, UserSession.kind == "app")
                      .with_for_update()).first()
    if sess is None:
        old = db.scalars(select(UserSession).where(UserSession.prev_token_hash == digest,
                                                   UserSession.kind == "app").with_for_update()).first()
        if old is None or not old.live(now):
            raise unauthorized("invalid_refresh_token", "Please sign in again.")
        if old.rotated_at is None or now - old.rotated_at > ROTATION_GRACE:
            old.revoked_at = now
            old.revoke_reason = "refresh_token_reuse"
            db.commit()
            raise unauthorized("invalid_refresh_token", "Please sign in again.")
        sess = old                    # the answer to the previous refresh got lost: rotate once more
    user = db.get(User, sess.user_id)
    if user is None:
        raise unauthorized("invalid_refresh_token", "Please sign in again.")
    if not user.active:
        # said even when disabling ended the session: the app must lock, not just ask for a new sign-in
        if sess.revoked_at is None:
            sess.revoked_at = now
            sess.revoke_reason = "account_disabled"
            db.commit()
        raise forbidden("account_disabled", "This account is disabled.")
    if not sess.live(now):
        raise unauthorized("invalid_refresh_token", "Please sign in again.")
    secret = tokens.new_secret()
    if sess.token_hash == digest:     # (on the grace path the previous hash stays; the lost token is replaced)
        sess.prev_token_hash = sess.token_hash
    sess.token_hash = tokens.digest(secret)
    sess.rotated_at = now
    sess.last_used_at = now
    if ip:
        sess.ip = ip
    access, ttl = tokens.issue_access(str(user.id), str(sess.id), user.role)
    return user, IssuedSession(sess, secret, access, ttl)


def session_for_access(db: Session, token: str) -> tuple[User, UserSession]:
    claims = tokens.read_access(token or "")
    if claims is None:
        raise unauthorized("invalid_access_token", "Your sign-in expired. Refresh it or sign in again.")
    try:
        sess = db.get(UserSession, uuid.UUID(claims.session_id))
    except ValueError:
        sess = None
    if sess is None or not sess.live(utcnow()) or str(sess.user_id) != claims.user_id:
        raise unauthorized("session_revoked", "This sign-in was ended. Please sign in again.")
    user = db.get(User, sess.user_id)
    if user is None:
        raise unauthorized("session_revoked", "This sign-in was ended. Please sign in again.")
    if not user.active:
        raise forbidden("account_disabled", "This account is disabled.")
    return user, sess


def session_for_cookie(db: Session, cookie: str) -> tuple[User, UserSession] | None:
    if not cookie:
        return None
    sess = db.scalars(select(UserSession).where(UserSession.token_hash == tokens.digest(cookie),
                                                UserSession.kind == "web")).first()
    now = utcnow()
    if sess is None or not sess.live(now):
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    if now - sess.last_used_at > timedelta(minutes=5):
        sess.last_used_at = now
    return user, sess


def revoke(sess: UserSession, reason: str) -> None:
    if sess.revoked_at is None:
        sess.revoked_at = utcnow()
        sess.revoke_reason = reason


def revoke_all(db: Session, user_id: uuid.UUID, reason: str, keep: uuid.UUID | None = None) -> int:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep is not None:
        q = q.where(UserSession.id != keep)
    return db.execute(q.values(revoked_at=utcnow(), revoke_reason=reason)).rowcount or 0


# -- links sent by e-mail --------------------------------------------------------------------------------------------
def page(path: str, base: str = "") -> str:
    return (get_config().public_url or base.rstrip("/")) + path


def link(path: str, token: str, base: str = "") -> str:
    return f"{page(path, base)}?token={token}"


def _retire(db: Session, model, user_id: uuid.UUID) -> None:
    """End every unused link of this kind for the account (a newer link or a password change replaces them)."""
    now = utcnow()
    db.execute(update(model).where(model.user_id == user_id, model.used_at.is_(None), model.expires_at > now)
               .values(expires_at=now))


def email_unavailable() -> ApiError:
    return unavailable("email_unavailable", "E-mails cannot be sent at the moment. Please try again later.")


def delivery(accepted: bool) -> str:
    """What happened to an e-mail, for the user: ``sent`` (accepted for delivery), ``failed`` (the mail server did
    not take it) or ``unavailable`` (this server cannot reach anybody by e-mail yet)."""
    if not mail.deliverable():
        return "unavailable"
    return "sent" if accepted else "failed"


def send_verification(db: Session, user: User, email: str | None = None, base: str = "") -> bool:
    """A new confirmation link. True when the e-mail was accepted for delivery; only then do the previous links
    stop working (an e-mail that could not be sent leaves the last one that was)."""
    secret = tokens.new_secret()
    accepted = mail.send(mail.compose("verify", email or user.email, user.language,
                                      link("/verify-email", secret, base), user.display_name))
    if accepted:
        _retire(db, EmailVerificationToken, user.id)
        db.add(EmailVerificationToken(user_id=user.id, email=email or user.email, token_hash=tokens.digest(secret),
                                      expires_at=utcnow() + timedelta(hours=48)))
    return accepted


def verify_email(db: Session, token: str) -> User:
    now = utcnow()
    row = db.scalars(select(EmailVerificationToken).where(
        EmailVerificationToken.token_hash == tokens.digest(token or "")).with_for_update()).first()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise bad_request("invalid_token", "This link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if user is None:
        raise bad_request("invalid_token", "This link is invalid or has expired.")
    old_email = user.email
    if normalize_email(row.email) != user.email_normalized:
        if user_by_email(db, row.email) is not None:
            raise conflict("email_taken", "An account with this e-mail address already exists.")
        user.email = row.email
        user.email_normalized = normalize_email(row.email)
    row.used_at = now
    user.email_verified_at = now
    if user.email != old_email:
        # the previous address learns about the change (it is the account owner's alarm if it was not them)
        mail.send(mail.compose("email_changed", old_email, user.language, user.email, user.display_name))
    return user


def request_reset(db: Session, email: str, ip: str = "", base: str = "") -> None:
    """Always looks the same from outside, whether or not the address has an account.

    Only a system-wide problem is reported: when no e-mail can reach anybody, every request fails alike (before the
    address is looked up), so the answer still says nothing about accounts. A new link replaces older ones."""
    if not mail.deliverable():
        raise email_unavailable()
    user = user_by_email(db, email)
    if user is None or not user.active:
        return
    secret = tokens.new_secret()
    if mail.send(mail.compose("reset", user.email, user.language, link("/reset-password", secret, base),
                              user.display_name)):
        _retire(db, PasswordResetToken, user.id)
        db.add(PasswordResetToken(user_id=user.id, token_hash=tokens.digest(secret), ip=ip,
                                  expires_at=utcnow() + timedelta(hours=1)))


def confirm_reset(db: Session, token: str, new_password: str, base: str = "") -> User:
    now = utcnow()
    row = db.scalars(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == tokens.digest(token or "")).with_for_update()).first()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise bad_request("invalid_token", "This link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise bad_request("invalid_token", "This link is invalid or has expired.")
    set_password(db, user, new_password, base=base)
    row.used_at = now
    user.failed_logins = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = now      # the reset link proved the address
    return user


def set_password(db: Session, user: User, new_password: str, keep_session: uuid.UUID | None = None,
                 base: str = "") -> None:
    _policy(new_password, user.email)
    user.credential.password_hash = passwords.hash_password(new_password)
    user.credential.password_changed_at = utcnow()
    revoke_all(db, user.id, "password_changed", keep=keep_session)
    _retire(db, PasswordResetToken, user.id)          # no older reset link can change the new password again
    mail.send(mail.compose("password_changed", user.email, user.language, page("/forgot-password", base),
                           user.display_name))


def change_password(db: Session, user: User, current: str, new_password: str, keep_session: uuid.UUID | None,
                    base: str = "") -> None:
    if not passwords.verify_password(user.credential.password_hash, current):
        raise bad_request("wrong_password", "The current password is wrong.")
    set_password(db, user, new_password, keep_session=keep_session, base=base)


def change_email(db: Session, user: User, password: str, new_email: str, base: str = "") -> bool:
    """Send a confirmation link to ``new_email``; the address changes when it is opened. True when it was sent."""
    new_email = check_email(new_email)
    if not passwords.verify_password(user.credential.password_hash, password):
        raise bad_request("wrong_password", "The current password is wrong.")
    if user_by_email(db, new_email) is not None:
        raise conflict("email_taken", "An account with this e-mail address already exists.")
    return send_verification(db, user, new_email, base=base)


def registration_allowed(db: Session) -> None:
    if not settings.get(db, "registration_enabled"):
        raise forbidden("registration_closed", "New accounts cannot be created at the moment.")


def needs_verification(db: Session, user: User) -> bool:
    return bool(settings.get(db, "require_email_verification")) and user.email_verified_at is None


def last_password_change(user: User) -> datetime:
    return user.credential.password_changed_at


__all__ = ["ApiError", "authenticate", "create_user", "refresh", "start_session"]
