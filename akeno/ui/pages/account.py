"""My Account and License pages (ACCOUNT section of the navigation)."""

from __future__ import annotations

import webbrowser

import shiboken6
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.licensing import LicenseManager, LicenseView, forms
from akeno.licensing import manager as default_manager
from akeno.licensing.api import ApiError
from akeno.licensing.manager import TYPE_NAMES
from akeno.ui import workers
from akeno.ui.account_ui import (STATE, STATUS_NAME, Banner, CodeEdit, CooldownButton, Field, LicenseSummary,
                                 PasswordEdit, RuleList, SignInDialog, StrengthMeter, error_text, remaining_text,
                                 retry_after, set_busy, when)
from akeno.ui.widgets import (Card, Divider, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow, Spinner, StatusPill,
                              SubCard, button, clear_layout, confirm, label)

EVENT_NAMES = {"redeem": "Code redeemed", "extend": "Extended by support", "change_type": "License type changed",
               "grant_lifetime": "Lifetime granted", "status_disabled": "Disabled", "status_active": "Enabled again",
               "status_revoked": "Revoked", "grant": "Given by support"}


def _ts(value) -> float | None:
    from akeno.licensing.manager import _ts as parse
    try:
        return parse(value)
    except ValueError:
        return None


class _LicensePageBase(QWidget):
    def __init__(self, ctl, win, manager: LicenseManager | None = None):
        super().__init__()
        self.ctl = ctl
        self.win = win
        self.m = manager or getattr(win, "license", None) or default_manager()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(16)
        self.note = label("", shrink=True)
        self.note.setVisible(False)

    def say(self, text: str, error: bool = False) -> None:
        from akeno.ui import theme
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {theme.RED if error else theme.GREEN}; font-weight: 600;")
        self.note.setVisible(bool(text))

    def run(self, fn, on_done, *buttons) -> None:
        for b in buttons:
            b.setEnabled(False)

        def finish():
            for b in buttons:
                b.setEnabled(True)

        # a redeemed code can rebuild the window (new modules) before the answer arrives: the old page is gone
        def done(res):
            if shiboken6.isValid(self):
                finish()
                on_done(res)

        def failed(exc):
            if shiboken6.isValid(self):
                finish()
                self.say(error_text(exc), error=True)
                self.show_view(self.m.view())
        workers.run(fn, done, failed)

    def show_view(self, view: LicenseView) -> None:
        pass

    def on_show(self) -> None:
        self.show_view(self.m.view())


class LicensePage(_LicensePageBase):
    def __init__(self, ctl, win, manager: LicenseManager | None = None):
        super().__init__(ctl, win, manager)
        self.root.addWidget(PageHeader(t("License")))
        self.root.addWidget(self.note)
        row = ResponsiveRow(spacing=14)
        self.card = Card(t("License"), "shield")
        self.summary = LicenseSummary()
        self.card.body.addWidget(self.summary)
        actions = QHBoxLayout()
        self.btn_check = button(t("Check now"), "rotate", on_click=self._check)
        actions.addWidget(self.btn_check)
        actions.addStretch(1)
        self.card.body.addLayout(actions)
        row.add(self.card, 3)
        redeem = Card(t("Extend or upgrade"), "check")
        redeem.body.addWidget(label(t("A code adds its time from the moment you redeem it; a lifetime code makes it "
                                      "permanent. AKENO Full unlocks every module; a module code (for example AKENO "
                                      "VPN) unlocks only that module and keeps everything else as it is."),
                                    "dim", shrink=True))
        self.code = CodeEdit()
        self.code.submitted.connect(lambda _: self._redeem())
        redeem.body.addWidget(self.code)
        self.btn_redeem = button(t("Redeem code"), "check", "primary", on_click=self._redeem)
        redeem.body.addWidget(self.btn_redeem, 0, Qt.AlignmentFlag.AlignLeft)
        row.add(redeem, 2)
        self.root.addWidget(row)
        self.history_card = Card(t("History"), "chart")
        self.history = QVBoxLayout()
        self.history_card.body.addLayout(self.history)
        self.root.addWidget(self.history_card)
        how = Card(t("Offline use"), "layers")
        how.body.addWidget(label(t("Lifetime licenses get a signed certificate for this PC: AKENO then starts without "
                                   "any connection, permanently. Time-limited licenses are verified online every few "
                                   "hours and keep working offline for the grace period the server allows (72 hours "
                                   "by default), never past their end. If the server cannot be reached, nothing is "
                                   "deleted: your games, mods, backups and settings stay exactly as they are."),
                                 "dim", shrink=True))
        self.root.addWidget(how)
        self.root.addStretch(1)
        self.show_view(self.m.view())

    def show_view(self, view: LicenseView) -> None:
        self.summary.show_view(view)

    def on_show(self) -> None:
        super().on_show()
        if self.m.state.get("refresh_token"):
            workers.run(self.m.history, self._show_history, lambda e: None)

    def _show_history(self, items: list[dict]) -> None:
        clear_layout(self.history)
        if not items:
            self.history.addWidget(label(t("No entries yet."), "muted"))
            return
        for item in items[:30]:
            kind = t(EVENT_NAMES.get(item.get("kind", ""), item.get("kind", "")))
            new_type = t(TYPE_NAMES.get(item.get("new_type") or "", item.get("new_type") or ""))
            until = item.get("new_expires_at")
            from akeno.licensing import entitlements as ents
            product = t(ents.name(item.get("entitlement") or ents.SUITE))
            text = f"{when(_ts(item.get('at')))} · {product} · {kind} · {new_type}"
            if until:
                text += " · " + t("until {d}", d=when(_ts(until)))
            self.history.addWidget(label(text, shrink=True))

    def _check(self) -> None:
        self.run(self.m.validate, lambda v: (self.say(t("License verified.")), self.show_view(v)), self.btn_check)

    def _redeem(self) -> None:
        code = self.code.code()
        if not code:
            self.say(t("Enter your activation code."), error=True)
            return

        def done(view):
            self.code.clear()
            self.say(t("Code redeemed."))
            self.show_view(view)
            self.on_show()
        self.run(lambda: self.m.redeem(code), done, self.btn_redeem)


class AccountPage(_LicensePageBase):
    """Profile, sign-in security, the license at a glance and the account's PCs.

    Everything here is done by the server for the signed-in account: the page sends a request and shows what the
    server answered. Nothing on this page can grant a license or a role."""

    def __init__(self, ctl, win, manager: LicenseManager | None = None):
        super().__init__(ctl, win, manager)
        self.root.addWidget(PageHeader(t("My Account"), t("Your profile, sign-in security, license and PCs.")))
        self.banner = Banner()
        self.root.addWidget(self.banner)
        row = ResponsiveRow(spacing=16)
        row.add(self._profile_card(), 1)
        row.add(self._security_card(), 1)
        self.root.addWidget(row)
        row = ResponsiveRow(spacing=16)
        row.add(self._license_card(), 1)
        row.add(self._devices_card(), 1)
        self.root.addWidget(row)
        self.root.addStretch(1)
        self.show_view(self.m.view())

    # -- cards -------------------------------------------------------------------------------------------------------
    def _profile_card(self) -> Card:
        card = Card(t("Profile"), "user")
        top = QHBoxLayout()
        top.setSpacing(14)
        self.avatar = QLabel()
        self.avatar.setFixedSize(48, 48)
        top.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label("", "h3", shrink=True)
        col.addWidget(self.name)
        self.email = label("—", "dim", selectable=True, shrink=True)
        col.addWidget(self.email)
        top.addLayout(col, 1)
        card.body.addLayout(top)
        line = QHBoxLayout()
        line.setSpacing(10)
        self.verified_pill = StatusPill("", "pending")
        line.addWidget(self.verified_pill)
        self.btn_verify_mail = CooldownButton(t("Resend verification e-mail"), kind="link")
        self.btn_verify_mail.clicked.connect(self._resend)
        line.addWidget(self.btn_verify_mail)
        line.addStretch(1)
        card.body.addLayout(line)
        self.verified = label("", "muted", shrink=True)
        card.body.addWidget(self.verified)
        self.facts = KeyValueGrid(150)
        card.body.addWidget(self.facts)
        card.body.addWidget(Divider())
        card.body.addWidget(label(t("Display name"), "key"))
        line = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(forms.MAX_NAME)
        self.name_edit.setAccessibleName(t("Display name"))
        line.addWidget(self.name_edit, 1)
        self.btn_name = button(t("Save"), on_click=self._save_name)
        line.addWidget(self.btn_name)
        card.body.addLayout(line)
        return card

    def _security_card(self) -> Card:
        card = Card(t("Sign-in and security"), "shield")
        # password
        card.body.addWidget(label(t("Password"), "h3"))
        line = QHBoxLayout()
        line.addWidget(label(t("Changing it signs you out on your other devices."), "dim", shrink=True), 1)
        self.btn_pw_open = button(t("Change password"), "key", on_click=lambda: self._toggle(self.pw_box))
        line.addWidget(self.btn_pw_open)
        card.body.addLayout(line)
        self.pw_box = SubCard()
        self.f_current = Field(t("Current password"), PasswordEdit(t("Current password")))
        self.f_new = Field(t("New password"), PasswordEdit(t("New password")))
        self.pw_strength = StrengthMeter()
        self.pw_rules = RuleList()
        self.f_new.extra.addWidget(self.pw_strength)
        self.f_new.extra.addWidget(self.pw_rules)
        self.f_confirm = Field(t("Confirm new password"), PasswordEdit(t("Confirm new password")))
        for f in (self.f_current, self.f_new, self.f_confirm):
            self.pw_box.lay.addWidget(f)
            f.edit.returnPressed.connect(self._change_password)
        self.f_new.edit.textChanged.connect(self._new_password_changed)
        self.btn_pw = button(t("Save new password"), "check", "primary", on_click=self._change_password)
        self.pw_spinner = Spinner()
        self.pw_box.lay.addLayout(_row(self.btn_pw, self.pw_spinner,
                                       button(t("Cancel"), kind="ghost", on_click=lambda: self._toggle(self.pw_box))))
        self.pw_box.setVisible(False)
        card.body.addWidget(self.pw_box)
        card.body.addWidget(Divider())
        # e-mail address
        card.body.addWidget(label(t("E-mail address"), "h3"))
        line = QHBoxLayout()
        line.addWidget(label(t("A confirmation link goes to the new address; it counts once you open it."), "dim",
                             shrink=True), 1)
        self.btn_mail_open = button(t("Change e-mail"), "mail", on_click=lambda: self._toggle(self.mail_box))
        line.addWidget(self.btn_mail_open)
        card.body.addLayout(line)
        self.mail_box = SubCard()
        self.f_mail = Field(t("New e-mail address"))
        self.f_mail.edit.setMaxLength(forms.MAX_EMAIL)
        self.f_mail_pw = Field(t("Current password"), PasswordEdit(t("Current password")))
        for f in (self.f_mail, self.f_mail_pw):
            self.mail_box.lay.addWidget(f)
            f.edit.returnPressed.connect(self._change_email)
        self.btn_mail = button(t("Send confirmation link"), "mail", "primary", on_click=self._change_email)
        self.mail_spinner = Spinner()
        self.mail_box.lay.addLayout(_row(self.btn_mail, self.mail_spinner, button(
            t("Cancel"), kind="ghost", on_click=lambda: self._toggle(self.mail_box))))
        self.mail_box.setVisible(False)
        card.body.addWidget(self.mail_box)
        card.body.addWidget(Divider())
        # sessions
        card.body.addWidget(label(t("Sessions"), "h3"))
        self.session_text = label("", "dim", shrink=True)
        card.body.addWidget(self.session_text)
        line = FlowLayout(spacing=8)
        self.btn_others = button(t("Sign out of other devices"), "lock", on_click=self._sign_out_others)
        line.addWidget(self.btn_others)
        self.btn_out = button(t("Sign out"), kind="danger", on_click=self._sign_out)
        line.addWidget(self.btn_out)
        card.body.addLayout(line)
        self.btn_site = button(t("Sign-in history and two-factor sign-in on the website"), "external", kind="link",
                               on_click=lambda: self._website("/account/security"))
        card.body.addWidget(self.btn_site, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _license_card(self) -> Card:
        card = Card(t("License"), "key")
        self.lic_pill = StatusPill("", "pending")
        card.body.addWidget(self.lic_pill, 0, Qt.AlignmentFlag.AlignLeft)
        self.lic_facts = KeyValueGrid(150)
        card.body.addWidget(self.lic_facts)
        card.body.addWidget(label(t("Licenses are granted by the AKENO server only. Redeem a code on the License "
                                    "page to extend or upgrade."), "muted", shrink=True))
        card.body.addWidget(button(t("Open the License page"), "chevron-right", on_click=lambda: self._go("license")),
                            0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _devices_card(self) -> Card:
        card = Card(t("Your PCs"), "cpu")
        self.device_usage = label("", "dim", shrink=True)
        card.body.addWidget(self.device_usage)
        card.body.addWidget(label(t("Name of this PC in your account"), "key"))
        self.label_edit = QLineEdit()
        self.label_edit.setMaxLength(80)
        self.label_edit.setAccessibleName(t("Name of this PC in your account"))
        line = QHBoxLayout()
        line.addWidget(self.label_edit, 1)
        self.btn_rename = button(t("Rename"), on_click=self._rename)
        line.addWidget(self.btn_rename)
        card.body.addLayout(line)
        self.devices_box = QWidget()
        self.devices = QVBoxLayout(self.devices_box)
        self.devices.setContentsMargins(0, 0, 0, 0)
        self.devices.setSpacing(8)
        card.body.addWidget(self.devices_box)
        card.body.addWidget(label(t("A removed PC can sign in again later while a slot is free."), "muted",
                                  shrink=True))
        return card

    # -- state -------------------------------------------------------------------------------------------------------
    def say(self, text: str, error: bool = False, kind: str | None = None) -> None:
        self.banner.show_message(text, kind or ("error" if error else "success"))

    def show_view(self, view: LicenseView) -> None:
        account = self.m.account
        signed_in = bool(view.email)
        name = view.display_name or (view.email or "").split("@")[0] or t("Not signed in")
        self.name.setText(name)
        self.email.setText(view.email or t("Not signed in"))
        self.avatar.setPixmap(_avatar(name, 48))
        if not self.name_edit.hasFocus():
            self.name_edit.setText(view.display_name or "")
        verified = account.get("email_verified")
        if not signed_in:
            self.verified_pill.set(t("Not signed in"), "pending")
            self.verified.setText("")
        elif verified:
            self.verified_pill.set(t("E-mail verified"), "ok")
            self.verified.setText("")
        else:
            self.verified_pill.set(t("E-mail not verified"), "warn")
            pending = self.m.verification.get("email")
            self.verified.setText(t("Open the link we sent to {email}.", email=forms.mask_email(pending))
                                  if pending else t("Open the link in the e-mail we sent you."))
        self.btn_verify_mail.setVisible(signed_in and not verified)
        self.btn_verify_mail.start_from(self.m.verification.get("sent_at"))
        rows = [(t("Member since"), when(_ts(account.get("created_at")), with_time=False)),
                (t("Two-factor sign-in"), t("On") if account.get("two_factor") else t("Off"))]
        self.facts.set_rows(rows)
        self.verified.setVisible(bool(self.verified.text()))
        if self.m.remember:
            self.session_text.setText(t("Signed in on this PC. The sign-in is kept encrypted for your Windows account; "
                                        "your password is never saved."))
        else:
            self.session_text.setText(t("Signed in on this PC until AKENO closes (not kept for the next start)."))
        ended = view.needs_sign_in
        for b in (self.btn_pw_open, self.btn_mail_open, self.btn_others, self.btn_name, self.btn_rename, self.btn_pw,
                  self.btn_mail, self.devices_box):
            b.setEnabled(signed_in and not ended)
        if ended:
            self.btn_verify_mail.setVisible(False)
            self.pw_box.setVisible(False)
            self.mail_box.setVisible(False)
        if ended:
            self.session_text.setText(t("The sign-in on this PC has ended. Your license keeps working; sign in again "
                                        "to manage your account."))
            self.banner.show_message(t("You were signed out on this PC. Please sign in again.")
                                     if view.session_ended == "session_revoked" else
                                     t("Your session has expired. Please sign in again."), "warn",
                                     t("Sign in again"), self._sign_in_again)
        elif self.banner.kind == "warn" and not self.banner.action.isHidden():
            self.banner.clear()
        # the license at a glance
        self.lic_pill.set(t(STATUS_NAME[view.status]), STATE[view.status])
        lic_rows = [(t("License type"), t(view.type_name) if view.license_type else "—"),
                    (t("Expires"), t("Never") if view.lifetime else when(view.expires_at))]
        if view.remaining is not None and not view.lifetime:
            lic_rows.append((t("Remaining"), remaining_text(view.remaining)))
        self.lic_facts.set_rows(lic_rows)
        if not self.label_edit.hasFocus():
            self.label_edit.setText(view.device_label or "")
        info = self.m.state.get("license_info") or {}
        self._limit = info.get("device_limit")
        self.btn_site.setEnabled(bool(self.m.config.website))

    def on_show(self) -> None:
        super().on_show()
        if self.m.state.get("refresh_token"):
            workers.run(self.m.refresh_account, self._account_loaded, self._account_loaded)
            workers.run(self.m.devices, self._show_devices, lambda e: None)

    def _account_loaded(self, _result) -> None:
        """After the server's answer (or its refusal, such as an ended sign-in): show what is true now."""
        if shiboken6.isValid(self):
            self.show_view(self.m.view())

    def _show_devices(self, devices: list[dict]) -> None:
        if not shiboken6.isValid(self):
            return
        clear_layout(self.devices)
        current = self.m.state.get("device_id")
        limit = getattr(self, "_limit", None)
        self.device_usage.setText(t("{n} of {limit} PCs in use", n=len(devices), limit=limit) if limit else
                                  t("{n} PC(s) on this account", n=len(devices)))
        for dev in devices:
            box = SubCard()
            line = QHBoxLayout()
            line.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(1)
            name = dev.get("label") or "PC"
            if dev.get("id") == current:
                name += " · " + t("this PC")
            col.addWidget(label(name, "h3", shrink=True))
            col.addWidget(label(" · ".join(x for x in (dev.get("os") or "", t(
                "last seen {d}", d=when(_ts(dev.get("last_seen_at")), with_time=False))) if x), "muted", shrink=True))
            line.addLayout(col, 1)
            if dev.get("id") != current:
                remove = (lambda _=False, d=dev["id"], n=dev.get("label") or "PC": self._remove(d, n))
                line.addWidget(button(t("Remove"), kind="danger", on_click=remove))
            box.lay.addLayout(line)
            self.devices.addWidget(box)

    # -- actions -----------------------------------------------------------------------------------------------------
    def run(self, fn, on_done, *buttons, spinner: Spinner | None = None, busy_text: str = "") -> None:
        first = buttons[0] if buttons else None
        for b in buttons[1:]:
            b.setEnabled(False)
        if first is not None:
            set_busy(first, spinner, True, busy_text)

        def finish():
            if not shiboken6.isValid(self):
                return False
            if first is not None:
                set_busy(first, spinner, False)
            for b in buttons[1:]:
                b.setEnabled(True)
            return True

        def done(res):
            if finish():
                on_done(res)

        def failed(exc):
            if finish():
                self.say(error_text(exc), error=True)
                self.show_view(self.m.view())
        workers.run(fn, done, failed)

    def _toggle(self, box: QWidget) -> None:
        show = box.isHidden()
        box.setVisible(show)
        for f in box.findChildren(Field):
            f.edit.clear()
            f.set_error("")
        if box is self.pw_box:
            self.pw_strength.set("")
            self.pw_rules.set([], empty=True)
        if show:
            box.findChildren(Field)[0].edit.setFocus()

    def _new_password_changed(self, text: str) -> None:
        email = self.m.account.get("email") or ""
        self.pw_strength.set(text, email)
        self.pw_rules.set(forms.password_problems(text, email), empty=not text)

    def _change_password(self) -> None:
        current, new, confirm = self.f_current.edit.text(), self.f_new.edit.text(), self.f_confirm.edit.text()
        self.f_current.set_error("" if current else t("Enter your current password."))
        problems = forms.password_problems(new, self.m.account.get("email") or "")
        self.f_new.set_error(t("The password does not meet every rule yet.") if problems else "")
        if new == current and new:
            self.f_new.set_error(t("Choose a password you have not used here before."))
        self.f_confirm.set_error("" if confirm == new else t("The passwords do not match."))
        if any(not f.error.isHidden() for f in (self.f_current, self.f_new, self.f_confirm)):
            return

        def done(_):
            self._toggle(self.pw_box)
            self.say(t("Password changed. Your other devices were signed out; this PC stays signed in."))

        def failed_field(exc):
            if isinstance(exc, ApiError) and exc.code == "wrong_password":
                self.f_current.set_error(error_text(exc))
            elif isinstance(exc, ApiError) and exc.code == "weak_password":
                self.f_new.set_error(error_text(exc))
            else:
                self.say(error_text(exc), error=True)
                self.show_view(self.m.view())
        self._run_form(lambda: self.m.change_password(current, new), done, failed_field, self.btn_pw, self.pw_spinner,
                       t("Saving…"))

    def _change_email(self) -> None:
        email, pw = self.f_mail.edit.text().strip(), self.f_mail_pw.edit.text()
        self.f_mail.set_error("" if forms.valid_email(email) else
                              t("Enter a valid e-mail address, for example name@example.com."))
        self.f_mail_pw.set_error("" if pw else t("Enter your current password."))
        if any(not f.error.isHidden() for f in (self.f_mail, self.f_mail_pw)):
            return

        def done(out):
            self._toggle(self.mail_box)
            self.say(t("We sent a confirmation link to {email}. Your address changes when you open it.",
                       email=forms.mask_email(out.get("email") or email)))
            self.show_view(self.m.view())

        def failed_field(exc):
            if isinstance(exc, ApiError) and exc.code == "wrong_password":
                self.f_mail_pw.set_error(error_text(exc))
            elif isinstance(exc, ApiError) and exc.code in ("email_taken", "invalid_email"):
                self.f_mail.set_error(error_text(exc))
            else:
                self.say(error_text(exc), error=True)
                self.show_view(self.m.view())
        self._run_form(lambda: self.m.change_email(pw, email), done, failed_field, self.btn_mail, self.mail_spinner,
                       t("Sending…"))

    def _run_form(self, fn, done, failed, btn, spinner, text) -> None:
        set_busy(btn, spinner, True, text)

        def ok(res):
            if shiboken6.isValid(self):
                set_busy(btn, spinner, False)
                done(res)

        def bad(exc):
            if shiboken6.isValid(self):
                set_busy(btn, spinner, False)
                failed(exc)
        workers.run(fn, ok, bad)

    def _resend(self) -> None:
        def done(out):
            if out.get("status") == "already_verified":
                self.say(t("Your e-mail address is verified."))
            else:
                self.btn_verify_mail.start()
                self.say(t("We've sent a new verification link to {email}.",
                           email=forms.mask_email(out.get("email") or "")))
            self.show_view(self.m.view())

        def failed(exc):
            self.btn_verify_mail.set_busy(False)
            wait = retry_after(exc)
            if wait:
                self.btn_verify_mail.start(wait)
            self.say(error_text(exc), error=True)
        self.btn_verify_mail.set_busy(True, t("Sending…"))

        def ok(out):
            if shiboken6.isValid(self):
                self.btn_verify_mail.set_busy(False)
                done(out)
        workers.run(self.m.resend_verification, ok, lambda e: shiboken6.isValid(self) and failed(e))

    def _save_name(self) -> None:
        name = self.name_edit.text().strip()
        self.run(lambda: self.m.update_profile(name), lambda _: (self.say(t("Saved.")), self.show_view(self.m.view())),
                 self.btn_name)

    def _sign_out_others(self) -> None:
        if not confirm(self, t("Sign out of other devices"),
                       t("End every other sign-in of your account (other PCs and the website)? This PC stays signed "
                         "in."), ok_text=t("Sign out other devices")):
            return
        self.run(self.m.revoke_other_sessions,
                 lambda n: self.say(t("Signed out of {n} other sign-in(s).", n=n) if n else
                                    t("There were no other sign-ins.")), self.btn_others)

    def _sign_in_again(self) -> None:
        dialog = SignInDialog(self.m, self, "")
        if dialog.exec():
            self.banner.clear()
            self.say(t("Signed in again."))
            self.on_show()

    def _rename(self) -> None:
        text = self.label_edit.text().strip()
        if text:
            self.run(lambda: self.m.rename_device(text), lambda _: self.say(t("Saved.")), self.btn_rename)

    def _remove(self, device_id: str, name: str) -> None:
        if confirm(self, t("Remove PC"), t("Remove {name} from your account? It can sign in again later while a slot "
                                           "is free.", name=name), ok_text=t("Remove")):
            self.run(lambda: (self.m.remove_device(device_id), self.m.devices())[1], self._show_devices)

    def _website(self, path: str = "/account") -> None:
        page = self.m.config.page(path)
        if page:
            webbrowser.open(page)

    def _go(self, key: str) -> None:
        if hasattr(self.win, "go"):
            self.win.go(key)

    def _sign_out(self) -> None:
        if confirm(self, t("Sign out"), t("Sign out of AKENO on this PC? AKENO locks until you sign in again. Your "
                                          "games, mods, backups and settings are kept."), ok_text=t("Sign out")):
            self.run(self.m.sign_out, lambda _: None, self.btn_out)


def _row(*widgets) -> FlowLayout:
    row = FlowLayout(spacing=10)
    for w in widgets:
        row.addWidget(w)
    return row


def _avatar(name: str, size: int):
    """A round badge with the first letter of the name, in the accent colour."""
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from akeno.ui import icons, theme
    ratio = icons.device_ratio()
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ACCENT_STRONG))
    p.drawEllipse(0, 0, size, size)
    font = QFont(theme.ui_font(14, QFont.Weight.DemiBold))
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, (name.strip()[:1] or "A").upper())
    p.end()
    return pm
