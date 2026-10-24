"""Pieces shared by the sign-in screen and the account pages: error texts, the license summary, the code field."""

from __future__ import annotations

import re
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QLineEdit, QWidget

from akeno.i18n import t
from akeno.licensing import LicenseView, Status
from akeno.licensing.api import ApiError, ServerUnavailable
from akeno.licensing.manager import DeviceLimitReached
from akeno.ui.widgets import StatusPill, label

ERRORS = {
    "unverified_response": "The license server's answer could not be verified. Nothing was changed; your saved "
                           "license stays as it is.",
    "no_trusted_keys": "This copy of AKENO has no license verification keys. Download AKENO from the official "
                       "release page.",
    "invalid_credentials": "E-mail address or password is wrong.",
    "totp_required": "Enter the code from your authenticator app.",
    "invalid_totp": "The authenticator code is wrong or was already used.",
    "totp_enrolment_required": "Staff accounts set up two-factor sign-in on the website first.",
    "account_disabled": "This account is disabled. Contact support.",
    "rate_limited": "Too many attempts. Wait a few minutes and try again.",
    "email_taken": "An account with this e-mail address already exists.",
    "invalid_email": "Enter a valid e-mail address.",
    "weak_password": "Choose a password with at least 10 characters that is not your e-mail address.",
    "registration_closed": "New accounts cannot be created at the moment.",
    "invalid_code_format": "An activation code looks like AKENO-XXXX-XXXX-XXXX-XXXX (never 0, 1, O or I).",
    "invalid_code": "This activation code does not exist. Check it for typos.",
    "code_used": "This activation code has already been used.",
    "code_revoked": "This activation code has been withdrawn.",
    "code_expired": "This activation code has expired.",
    "code_not_for_account": "This activation code was issued for another account.",
    "trial_already_used": "A trial of this length was already used on this account.",
    "already_lifetime": "You already have a lifetime license, so the code was not used. Keep it for someone else.",
    "license_disabled": "This license is disabled. Contact support.",
    "device_blocked": "This PC was removed from the account by an administrator. Contact support.",
    "device_removals_exhausted": "You removed the maximum number of PCs for 30 days. Support can help.",
    "invalid_token": "This link is invalid or has expired.",
    "email_not_verified": "Confirm your e-mail address first: open the link we sent you, then try again.",
    "validation_error": "Some fields are invalid.",
}


def error_text(exc: BaseException) -> str:
    if isinstance(exc, ServerUnavailable):
        if exc.maintenance:
            return t("The license server is being maintained. Try again later; a saved license keeps working.")
        return t("The license server cannot be reached. Check the internet connection and try again.")
    if isinstance(exc, ApiError):
        if exc.code in ERRORS:
            return t(ERRORS[exc.code])
        return t("The license server refused the request: {m}", m=exc.message)
    if isinstance(exc, DeviceLimitReached):
        return t("This license allows {n} PC(s). Remove one to use AKENO here.", n=exc.limit)
    if isinstance(exc, ValueError):
        return t(str(exc))
    return t("Something went wrong: {m}", m=str(exc))


def when(ts: float | None, with_time: bool = True) -> str:
    if not ts:
        return "—"
    from akeno.i18n import language
    dt = datetime.fromtimestamp(ts)
    if language() == "de":
        return dt.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
    return dt.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def remaining_text(seconds: float | None) -> str:
    if seconds is None:
        return t("Unlimited")
    if seconds <= 0:
        return t("Ended")
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return t("{d} d {h} h {m} min", d=d, h=h, m=m)
    if h:
        return t("{h} h {m} min {s} s", h=h, m=m, s=s)
    return t("{m} min {s} s", m=m, s=s)


STATE = {Status.ACTIVE_ONLINE: "ok", Status.ACTIVE_CACHED: "ok", Status.LIFETIME_OFFLINE: "ok",
         Status.EXPIRING: "warn", Status.EXPIRED: "fail", Status.DISABLED: "fail",
         Status.SERVER_UNAVAILABLE: "warn", Status.NOT_ACTIVATED: "pending", Status.NOT_LOGGED_IN: "pending",
         Status.NOT_CONFIGURED: "pending"}
STATUS_NAME = {Status.ACTIVE_ONLINE: "Active · verified online", Status.ACTIVE_CACHED: "Active · saved verification",
               Status.LIFETIME_OFFLINE: "Lifetime · offline", Status.EXPIRING: "Active · ends soon",
               Status.EXPIRED: "Expired", Status.DISABLED: "Disabled", Status.SERVER_UNAVAILABLE: "Needs a check",
               Status.NOT_ACTIVATED: "Not activated", Status.NOT_LOGGED_IN: "Not signed in",
               Status.NOT_CONFIGURED: "No license server"}


class LicenseSummary(QWidget):
    """License type, dates, the remaining time (counting down locally, without asking the server), the PC,
    the last verification and whether the license works offline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view: LicenseView | None = None
        self._shown_at = time.monotonic()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.pill = StatusPill("", "pending")
        grid.addWidget(self.pill, 0, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        self.message = label("", "dim", shrink=True)
        grid.addWidget(self.message, 1, 0, 1, 2)
        self.values: dict[str, object] = {}
        rows = [("type", "License type"), ("activated", "Activated"), ("expires", "Expires"),
                ("remaining", "Remaining"), ("device", "This PC"), ("verified", "Last verified"),
                ("offline", "Works offline")]
        for i, (key, text) in enumerate(rows, start=2):
            grid.addWidget(label(t(text), "key"), i, 0)
            value = label("—", "value", selectable=True, shrink=True)
            value.setAccessibleName(t(text))
            grid.addWidget(value, i, 1)
            self.values[key] = value
        grid.setColumnStretch(1, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def show_view(self, view: LicenseView) -> None:
        self.view = view
        self._shown_at = time.monotonic()
        self.pill.set(t(STATUS_NAME[view.status]), STATE[view.status])
        self.message.setText(t(view.message))
        v = self.values
        v["type"].setText(t(view.type_name) if view.license_type else "—")
        v["activated"].setText(when(view.activated_at))
        v["expires"].setText(t("Never") if view.lifetime else when(view.expires_at))
        v["device"].setText(view.device_label or "—")
        v["verified"].setText(when(view.last_verified))
        if view.lifetime and view.offline_eligible:
            offline = t("Yes, permanently (signed offline certificate)")
        elif view.offline_until and view.unlocked:
            offline = t("Until {d} without a connection", d=when(view.offline_until))
        else:
            offline = t("No")
        v["offline"].setText(offline)
        self._tick()
        if view.remaining is not None and view.unlocked:
            self.timer.start()
        else:
            self.timer.stop()

    def _tick(self) -> None:
        if self.view is None:
            return
        rem = self.view.remaining
        if rem is not None:
            rem = max(0.0, rem - (time.monotonic() - self._shown_at))
        self.values["remaining"].setText(remaining_text(rem) if (rem is not None or self.view.lifetime)
                                         else "—")


class CodeEdit(QLineEdit):
    """Activation code field: upper case and dashes as you type, any pasted form accepted."""

    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("AKENO-XXXX-XXXX-XXXX-XXXX")
        self.setMaxLength(40)
        self.setAccessibleName(t("Activation code"))
        self.textEdited.connect(self._format)
        self.returnPressed.connect(lambda: self.submitted.emit(self.code()))

    def code(self) -> str:
        return self.text().strip()

    def _format(self, text: str) -> None:
        body = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if body.startswith("AKENO"):
            body = body[5:]
        body = body[:16]
        groups = [body[i:i + 4] for i in range(0, len(body), 4)]
        out = "AKENO-" + "-".join(groups) if body else ""
        if out != text:
            self.setText(out)
            self.setCursorPosition(len(out))
