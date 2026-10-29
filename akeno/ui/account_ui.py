"""Pieces shared by the welcome screen and the account pages: error texts, the license summary, the code field, and
the form parts (notice banner, password field, strength meter, field with its own error, busy buttons, the
"send again" countdown and the sign-in form).

Errors never show a raw exception: :func:`error_text` turns every failure into a sentence that says what happened
and what to do. Account existence is never revealed by these texts (the password reset says the same for every
address).
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from akeno.i18n import t
from akeno.licensing import LicenseView, Status, forms
from akeno.licensing.api import ApiError, ServerUnavailable
from akeno.licensing.manager import DeviceLimitReached
from akeno.ui import icons, theme
from akeno.ui.widgets import Spinner, StatusPill, label

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
    "email_not_verified": "Please verify your e-mail address before continuing.",
    "validation_error": "Some fields are invalid.",
    "wrong_password": "The current password is wrong.",
    "invalid_refresh_token": "Your session has expired. Please sign in again.",
    "invalid_access_token": "Your session has expired. Please sign in again.",
    "session_revoked": "You were signed out on this PC. Please sign in again.",
    "not_authenticated": "Your session has expired. Please sign in again.",
    # an older AKENO server without this function
    "not_found": "The AKENO server does not offer this yet. Please try again after its next update.",
    "method_not_allowed": "The AKENO server does not offer this yet. Please try again after its next update.",
}
# answers that mean "the service could not do it" (ServerUnavailable with the server's code)
SERVICE_ERRORS = {
    "email_unavailable": "AKENO cannot send e-mails at the moment. Please try again later.",
    "email_delivery_failed": "The e-mail could not be sent. Please try again in a few minutes.",
    "signing_unavailable": "The license service is temporarily unavailable. Please try again later.",
}
NETWORK = {
    "offline": "Unable to connect to AKENO. Please check your internet connection.",
    "timeout": "AKENO is taking too long to respond. Please try again in a moment.",
    "certificate": "The secure connection to AKENO could not be verified. Check the date and time of this PC, or "
                   "whether security software or a proxy intercepts the connection.",
    "maintenance": "AKENO is being maintained right now. Please try again later; a saved license keeps working.",
    "server": "The AKENO service is temporarily unavailable. Please try again later.",
    "network": "Your network returned an unexpected answer. A proxy or firewall may be blocking AKENO.",
}
SESSION_CODES = ("invalid_refresh_token", "invalid_access_token", "session_revoked", "not_authenticated")


def error_text(exc: BaseException) -> str:
    if isinstance(exc, ServerUnavailable):
        if exc.code in SERVICE_ERRORS:
            return t(SERVICE_ERRORS[exc.code])
        return t(NETWORK.get(exc.kind, NETWORK["offline"]))
    if isinstance(exc, ApiError):
        if exc.code == "rate_limited" and (exc.details or {}).get("retry_after"):
            return t("Too many attempts. Please wait {time} and try again.",
                     time=wait_text(float(exc.details["retry_after"])))
        if exc.code in ERRORS:
            return t(ERRORS[exc.code])
        return t("AKENO could not complete the request: {m}", m=exc.message)
    if isinstance(exc, DeviceLimitReached):
        return t("This license allows {n} PC(s). Remove one to use AKENO here.", n=exc.limit)
    if isinstance(exc, ValueError):
        return t(str(exc))
    return t("Something went wrong. Please try again. If it keeps happening, restart AKENO.")


def session_ended(exc: BaseException) -> bool:
    return isinstance(exc, ApiError) and exc.status == 401 and exc.code in SESSION_CODES


def retry_after(exc: BaseException) -> float | None:
    if isinstance(exc, ApiError) and exc.code == "rate_limited":
        value = (exc.details or {}).get("retry_after")
        return float(value) if value else None
    if isinstance(exc, ServerUnavailable):
        return exc.retry_after
    return None


def wait_text(seconds: float) -> str:
    seconds = max(1, int(seconds + 0.5))
    if seconds < 90:
        return t("{n} seconds", n=seconds)
    return t("{n} minutes", n=(seconds + 59) // 60)


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
        rows = [("modules", "Included modules"), ("type", "License type"), ("activated", "Activated"),
                ("expires", "Expires"),
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
        v["modules"].setText(modules_text(view))
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


def modules_text(view: LicenseView) -> str:
    """``AKENO Full (every module)``, or each module with how long it runs: ``AKENO VPN (lifetime)``."""
    from akeno.licensing import entitlements as ents
    if not view.unlocked:
        return "—"
    parts = []
    for ent in sorted(view.grants, key=lambda e: (e != ents.SUITE, e)):
        g = view.grants[ent]
        length = t("lifetime") if g.get("exp") is None else t("until {d}", d=when(g["exp"]))
        name = t("AKENO Full (every module)") if ent == ents.SUITE else t(ents.name(ent))
        parts.append(f"{name} · {length}")
    return "\n".join(parts) or "—"


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


# -- form parts ------------------------------------------------------------------------------------------------------
BANNER = {"info": (theme.INFO, "info"), "success": (theme.GREEN, "check"), "warn": (theme.AMBER, "warn"),
          "error": (theme.RED, "warn")}


class Banner(QFrame):
    """A notice above a form: what happened, in the state's colour, with an optional action."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.kind = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.label = label("", shrink=True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.action = QPushButton()
        self.action.setProperty("kind", "link")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.setVisible(False)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self._slot = None
        self.setVisible(False)

    def text(self) -> str:
        return self.label.text()

    def show_message(self, text: str, kind: str = "info", action: str | None = None, on_action=None) -> None:
        if not text:
            self.clear()
            return
        color, icon_name = BANNER.get(kind, BANNER["info"])
        self.kind = kind
        self.label.setText(text)
        self.icon.setPixmap(icons.pixmap(icon_name, color, 18))
        r, g, b = QColor(color).red(), QColor(color).green(), QColor(color).blue()
        self.setStyleSheet(f"#Banner {{ background: rgba({r},{g},{b},0.08); border: 1px solid rgba({r},{g},{b},0.38);"
                           f" border-radius: 8px; }}")
        self.setAccessibleName(text)
        if self._slot is not None:
            self.action.clicked.disconnect(self._slot)
            self._slot = None
        self.action.setVisible(bool(action and on_action))
        if action and on_action:
            self.action.setText(action)
            self.action.setAccessibleName(action)
            self._slot = on_action
            self.action.clicked.connect(on_action)
        self.setVisible(True)

    def clear(self) -> None:
        self.kind = ""
        self.label.setText("")
        self.setVisible(False)


class PasswordEdit(QLineEdit):
    """Password field with a show/hide button inside it (also Alt+S while it has focus)."""

    def __init__(self, accessible: str = "", parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setMaxLength(forms.MAX_PASSWORD)
        self.setAccessibleName(accessible or t("Password"))
        self.toggle = QAction(self)
        self.toggle.setShortcut("Alt+S")
        self.toggle.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.toggle.triggered.connect(self.flip)
        self.addAction(self.toggle, QLineEdit.ActionPosition.TrailingPosition)
        self._update()

    @property
    def revealed(self) -> bool:
        return self.echoMode() == QLineEdit.EchoMode.Normal

    def flip(self) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Password if self.revealed else QLineEdit.EchoMode.Normal)
        self._update()

    def conceal(self) -> None:
        if self.revealed:
            self.flip()

    def _update(self) -> None:
        text = t("Hide password") if self.revealed else t("Show password")
        self.toggle.setIcon(icons.icon("eye-off" if self.revealed else "eye", theme.TEXT_DIM, 16))
        self.toggle.setToolTip(text + " (Alt+S)")
        self.toggle.setText(text)


class StrengthMeter(QWidget):
    """Four segments and a word: how hard the password is to guess (advice; the server's rules are the checklist)."""

    COLORS = (theme.RED, theme.RED, theme.AMBER, theme.GREEN, theme.GREEN)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.score = 0
        self.empty = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.bar = QWidget()
        self.bar.setFixedHeight(6)
        self.bar.setMinimumWidth(120)
        self.bar.paintEvent = self._paint_bar
        lay.addWidget(self.bar, 1, Qt.AlignmentFlag.AlignVCenter)
        self.word = label("", "muted")
        self.word.setMinimumWidth(60)
        lay.addWidget(self.word)

    def set(self, password: str, email: str = "") -> None:
        self.empty = not password
        self.score = forms.password_strength(password, email)
        self.word.setText("" if self.empty else t(forms.STRENGTH[self.score]))
        color = self.COLORS[self.score]
        self.word.setStyleSheet("" if self.empty else f"color: {color}; font-weight: 600;")
        self.setAccessibleName(t("Password strength: {s}", s=self.word.text()) if not self.empty else "")
        self.bar.update()

    def _paint_bar(self, e) -> None:
        p = QPainter(self.bar)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap = 4
        w = (self.bar.width() - 3 * gap) / 4
        lit = 0 if self.empty else max(1, self.score)
        for i in range(4):
            color = QColor(self.COLORS[self.score]) if i < lit else QColor(theme.BORDER_HI)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(i * (w + gap), 0, w, self.bar.height()), 3, 3)
        p.end()


class Field(QWidget):
    """A labelled input with its own error line (and an optional hint), so a mistake shows where it is."""

    def __init__(self, title: str, edit: QLineEdit | None = None, hint: str = "", right: QWidget | None = None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = label(title, "key")
        head.addWidget(self.title)
        head.addStretch(1)
        if right is not None:
            head.addWidget(right)
        lay.addLayout(head)
        self.edit = edit or QLineEdit()
        if not self.edit.accessibleName():
            self.edit.setAccessibleName(title)
        self.edit.setMinimumHeight(theme.CONTROL_HEIGHT + 4)
        lay.addWidget(self.edit)
        self.extra = QVBoxLayout()
        self.extra.setContentsMargins(0, 0, 0, 0)
        self.extra.setSpacing(4)
        lay.addLayout(self.extra)
        self.hint = label(hint, "muted", shrink=True)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self.error = label("", "error", shrink=True)
        self.error.setVisible(False)
        lay.addWidget(self.error)

    def text(self) -> str:
        return self.edit.text()

    def set_error(self, text: str = "") -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))
        self.edit.setProperty("invalid", bool(text))
        self.edit.setStyleSheet(f"QLineEdit {{ border-color: {theme.RED}; }}" if text else "")
        self.edit.setAccessibleDescription(text)


class RuleList(QWidget):
    """The server's password rules, each ticked off as it is met."""

    def __init__(self, rules=forms.RULES):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(2)
        self.rows: dict[str, QLabel] = {}
        for rule in rules:
            row = label("", "muted", shrink=True)
            lay.addWidget(row)
            self.rows[rule] = row
        self.set([], empty=True)

    def set(self, problems: list[str], empty: bool = False) -> None:
        for rule, row in self.rows.items():
            ok = not empty and rule not in problems
            mark, color = ("✓", theme.GREEN) if ok else ("•", theme.TEXT_MUTED)
            row.setText(f"<span style='color:{color}'>{mark}</span>&nbsp;&nbsp;{t(rule)}")


def set_busy(button: QPushButton, spinner: Spinner | None, on: bool, busy_text: str = "") -> None:
    """A button that is working: its text says what happens, a spinner turns, and it cannot be pressed twice."""
    if on:
        if button.property("idle_text") is None:
            button.setProperty("idle_text", button.text())
        if busy_text:
            button.setText(busy_text)
        button.setEnabled(False)
        if spinner is not None:
            spinner.start()
    else:
        idle = button.property("idle_text")
        if idle is not None:
            button.setText(idle)
            button.setProperty("idle_text", None)
        button.setEnabled(True)
        if spinner is not None:
            spinner.stop()


class CooldownButton(QPushButton):
    """"Send again": disabled with a countdown while the pause after the last e-mail runs."""

    def __init__(self, text: str, kind: str | None = None, icon_name: str | None = None):
        super().__init__(text)
        self.base = text
        self.cooldown = forms.Cooldown()
        self.busy = False
        if kind:
            self.setProperty("kind", kind)
        if icon_name:
            self.setIcon(icons.icon(icon_name, theme.TEXT, 16))
            self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)

    def start(self, seconds: float | None = None) -> None:
        self.cooldown.start(seconds)
        self.refresh()

    def start_from(self, sent_at: float | None) -> None:
        self.cooldown.start_from(sent_at)
        self.refresh()

    def set_busy(self, on: bool, text: str = "") -> None:
        self.busy = on
        if on and text:
            self.setText(text)
        self.refresh()

    def refresh(self) -> None:
        left = self.cooldown.remaining()
        if self.busy:
            self.setEnabled(False)
            return
        if left:
            self.setText(t("{action} ({time})", action=self.base, time=forms.clock_text(left)))
            self.setToolTip(t("You can send it again in {time}.", time=forms.clock_text(left)))
            self.setEnabled(False)
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.setText(self.base)
            self.setToolTip("")
            self.setEnabled(True)


def mail_badge(size: int = 46, icon_name: str = "mail", color: str | None = None) -> QLabel:
    """The round icon at the top of a confirmation screen."""
    from PySide6.QtGui import QPixmap
    ratio = icons.device_ratio()
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QColor(color or theme.ACCENT)
    fill = QColor(base)
    fill.setAlpha(36)
    p.setBrush(fill)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(0, 0, size, size))
    inner = int(size * 0.5)
    p.drawPixmap(int((size - inner) / 2), int((size - inner) / 2), icons.pixmap(icon_name, color or theme.ACCENT_TEXT,
                                                                              inner))
    p.end()
    out = QLabel()
    out.setPixmap(pm)
    out.setFixedSize(size, size)
    return out


class SignInForm(QWidget):
    """E-mail, password (with show/hide), "keep me signed in", and the authenticator code when the account needs it.

    It only collects and checks the input; whoever shows it runs the sign-in (on a worker thread) and reports back
    with :meth:`failed` or :meth:`done`."""

    submitted = Signal()
    forgot = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.email_field = Field(t("E-mail address"))
        self.email = self.email_field.edit
        self.email.setMaxLength(forms.MAX_EMAIL)
        self.email.setPlaceholderText("name@example.com")
        lay.addWidget(self.email_field)
        self.forgot_link = QPushButton(t("Forgot password?"))
        self.forgot_link.setProperty("kind", "link")
        self.forgot_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_link.clicked.connect(self.forgot.emit)
        self.password_field = Field(t("Password"), PasswordEdit(t("Password")), right=self.forgot_link)
        self.password: PasswordEdit = self.password_field.edit
        lay.addWidget(self.password_field)
        self.totp_field = Field(t("Authenticator code"), hint=t("The 6-digit code from your authenticator app."))
        self.totp = self.totp_field.edit
        self.totp.setMaxLength(12)
        self.totp.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.totp_field.setVisible(False)
        lay.addWidget(self.totp_field)
        self.remember = QCheckBox(t("Keep me signed in on this PC"))
        self.remember.setChecked(True)
        self.remember.setToolTip(t("AKENO keeps your sign-in encrypted for your Windows account, so you do not have to "
                                   "sign in at every start. Your password is never saved."))
        lay.addWidget(self.remember)
        for w in (self.email, self.password, self.totp):
            w.returnPressed.connect(self.submitted.emit)
            w.textEdited.connect(lambda _=None, f=w: self._edited(f))

    def _edited(self, edit: QLineEdit) -> None:
        for f in (self.email_field, self.password_field, self.totp_field):
            if f.edit is edit:
                f.set_error("")

    def check(self) -> bool:
        """Mark what is missing or malformed; True when the input can be sent."""
        for f in (self.email_field, self.password_field, self.totp_field):
            f.set_error("")
        ok = True
        email = self.email.text().strip()
        if not email:
            self.email_field.set_error(t("Enter your e-mail address."))
            ok = False
        elif not forms.valid_email(email):
            self.email_field.set_error(t("Enter a valid e-mail address, for example name@example.com."))
            ok = False
        if not self.password.text():
            self.password_field.set_error(t("Enter your password."))
            ok = False
        if not self.totp_field.isHidden() and not self.totp.text().strip():
            self.totp_field.set_error(t("Enter the code from your authenticator app."))
            ok = False
        if not ok:
            for f in (self.email_field, self.password_field, self.totp_field):
                if not f.error.isHidden():
                    f.edit.setFocus()
                    break
        return ok

    def values(self) -> tuple[str, str, str | None, bool]:
        return (self.email.text().strip(), self.password.text(), self.totp.text().strip() or None,
                self.remember.isChecked())

    def set_enabled(self, on: bool) -> None:
        for w in (self.email, self.password, self.totp, self.remember, self.forgot_link):
            w.setEnabled(on)

    def failed(self, exc: BaseException) -> None:
        """Put the error where it belongs; returns nothing, the caller shows general errors in its banner."""
        if isinstance(exc, ApiError) and exc.code in ("totp_required", "invalid_totp"):
            self.totp_field.setVisible(True)
            self.totp.selectAll()
            self.totp.setFocus()
        elif isinstance(exc, ApiError) and exc.code == "invalid_credentials":
            self.password.selectAll()
            self.password.setFocus()

    def done(self) -> None:
        self.password.clear()
        self.password.conceal()
        self.totp.clear()


class SignInDialog(QDialog):
    """Sign in again without leaving the suite (the license still runs, but the session ended)."""

    def __init__(self, manager, parent=None, reason: str = ""):
        super().__init__(parent)
        from akeno.ui import workers
        from akeno.ui.widgets import fit_to_screen
        self._workers = workers
        self.m = manager
        self.setWindowTitle(t("Sign in again"))
        self.setModal(True)
        fit_to_screen(self, 460, 520, 360, 360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)
        lay.addWidget(label(t("Sign in again"), "h2"))
        self.banner = Banner()
        lay.addWidget(self.banner)
        if reason:
            self.banner.show_message(reason, "warn")
        self.form = SignInForm()
        email = (manager.account or {}).get("email") or ""
        self.form.email.setText(email)
        self.form.remember.setChecked(manager.remember)
        self.form.submitted.connect(self.submit)
        self.form.forgot.connect(self._forgot)
        lay.addWidget(self.form)
        row = QHBoxLayout()
        self.spinner = Spinner()
        self.button = QPushButton(t("Sign in"))
        self.button.setProperty("kind", "primary")
        self.button.setDefault(True)
        self.button.clicked.connect(self.submit)
        cancel = QPushButton(t("Cancel"))
        cancel.setProperty("kind", "ghost")
        cancel.clicked.connect(self.reject)
        self.cancel = cancel
        row.addWidget(self.button)
        row.addWidget(self.spinner)
        row.addStretch(1)
        row.addWidget(cancel)
        lay.addLayout(row)
        lay.addStretch(1)
        (self.form.password if email else self.form.email).setFocus()
        self.busy = False

    def submit(self) -> None:
        if self.busy or not self.form.check():
            return
        email, password, code, remember = self.form.values()
        self.busy = True
        self.banner.clear()
        self.form.set_enabled(False)
        self.cancel.setEnabled(False)
        set_busy(self.button, self.spinner, True, t("Signing in…"))

        def finish():
            self.busy = False
            self.form.set_enabled(True)
            self.cancel.setEnabled(True)
            set_busy(self.button, self.spinner, False)

        def done(_view):
            finish()
            self.form.done()
            self.accept()

        def failed(exc):
            finish()
            self.form.failed(exc)
            self.banner.show_message(error_text(exc), "error")
        self._workers.run(lambda: self.m.sign_in(email, password, code, remember), done, failed)

    def _forgot(self) -> None:
        import webbrowser
        page = self.m.config.page("/forgot-password")
        if page:
            webbrowser.open(page)
