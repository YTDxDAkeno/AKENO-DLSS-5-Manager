"""My Account and License pages (ACCOUNT section of the navigation)."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.licensing import LicenseManager, LicenseView
from akeno.licensing import manager as default_manager
from akeno.licensing.manager import TYPE_NAMES
from akeno.ui import workers
from akeno.ui.account_ui import CodeEdit, LicenseSummary, error_text, when
from akeno.ui.widgets import Card, ResponsiveRow, button, clear_layout, label

EVENT_NAMES = {"redeem": "Code redeemed", "extend": "Extended by support", "change_type": "License type changed",
               "grant_lifetime": "Lifetime granted", "status_disabled": "Disabled", "status_active": "Enabled again",
               "status_revoked": "Revoked"}


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
        self.root.setSpacing(14)
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

        def done(res):
            finish()
            on_done(res)

        def failed(exc):
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
        redeem.body.addWidget(label(t("A code adds its time to your license from the moment you redeem it; a lifetime "
                                      "code makes the license permanent. One license unlocks every AKENO module."),
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
            text = f"{when(_ts(item.get('at')))} · {kind} · {new_type}"
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
    def __init__(self, ctl, win, manager: LicenseManager | None = None):
        super().__init__(ctl, win, manager)
        self.root.addWidget(self.note)
        row = ResponsiveRow(spacing=14)
        acct = Card(t("Account"), "shield")
        self.email = label("—", "h3", selectable=True)
        acct.body.addWidget(self.email)
        self.name = label("", "dim")
        acct.body.addWidget(self.name)
        self.verified = label("", "muted", shrink=True)
        acct.body.addWidget(self.verified)
        buttons = QHBoxLayout()
        self.btn_site = button(t("Manage on the website"), "settings", on_click=self._website)
        buttons.addWidget(self.btn_site)
        self.btn_out = button(t("Sign out"), kind="danger", on_click=self._sign_out)
        buttons.addWidget(self.btn_out)
        buttons.addStretch(1)
        acct.body.addLayout(buttons)
        acct.body.addWidget(label(t("Password, e-mail address, sign-in history and open sessions are managed on the "
                                    "website."), "muted", shrink=True))
        row.add(acct, 1)
        pc = Card(t("This PC"), "cpu")
        pc.body.addWidget(label(t("Name shown in your account"), "key"))
        self.label_edit = QLineEdit()
        self.label_edit.setMaxLength(80)
        self.label_edit.setAccessibleName(t("Name shown in your account"))
        line = QHBoxLayout()
        line.addWidget(self.label_edit, 1)
        self.btn_rename = button(t("Rename"), on_click=self._rename)
        line.addWidget(self.btn_rename)
        pc.body.addLayout(line)
        pc.body.addWidget(label(t("Your PCs"), "key"))
        self.devices = QVBoxLayout()
        pc.body.addLayout(self.devices)
        row.add(pc, 1)
        self.root.addWidget(row)
        self.root.addStretch(1)
        self.show_view(self.m.view())

    def show_view(self, view: LicenseView) -> None:
        self.email.setText(view.email or t("Not signed in"))
        self.name.setText(view.display_name or "")
        account = self.m.state.get("account") or {}
        if view.email:
            self.verified.setText(t("E-mail address confirmed.") if account.get("email_verified")
                                  else t("E-mail address not confirmed yet: open the link we sent you."))
        else:
            self.verified.setText("")
        self.label_edit.setText(view.device_label or "")
        self.btn_site.setEnabled(bool(self.m.config.website))

    def on_show(self) -> None:
        super().on_show()
        if self.m.state.get("refresh_token"):
            workers.run(self.m.devices, self._show_devices, lambda e: None)

    def _show_devices(self, devices: list[dict]) -> None:
        clear_layout(self.devices)
        current = self.m.state.get("device_id")
        for dev in devices:
            line = QHBoxLayout()
            text = f"{dev.get('label') or 'PC'} · {dev.get('os') or ''} · " + t(
                "last seen {d}", d=when(_ts(dev.get("last_seen_at")), with_time=False))
            if dev.get("id") == current:
                text += " · " + t("this PC")
            line.addWidget(label(text, shrink=True), 1)
            if dev.get("id") != current:
                line.addWidget(button(t("Remove"), kind="danger",
                                      on_click=lambda _=False, d=dev["id"]: self._remove(d)))
            holder = QWidget()
            holder.setLayout(line)
            self.devices.addWidget(holder)

    def _rename(self) -> None:
        text = self.label_edit.text().strip()
        if text:
            self.run(lambda: self.m.rename_device(text), lambda _: self.say(t("Saved.")), self.btn_rename)

    def _remove(self, device_id: str) -> None:
        self.run(lambda: (self.m.remove_device(device_id), self.m.devices())[1], self._show_devices)

    def _website(self) -> None:
        if self.m.config.website:
            webbrowser.open(self.m.config.website + "/account")

    def _sign_out(self) -> None:
        from akeno.ui.widgets import confirm
        if confirm(self, t("Sign out"), t("Sign out of AKENO on this PC? AKENO locks until you sign in again. Your "
                                          "games, mods, backups and settings are kept."), ok_text=t("Sign out")):
            self.run(self.m.sign_out, lambda _: None, self.btn_out)
