"""The welcome screen: sign in, create an account, redeem a code, reset the password.

It is all of AKENO that runs without an active license. Nothing else is loaded until the license is active (no
game scan, no mod engine, no database of games), and closing it closes AKENO.
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QStackedWidget, QVBoxLayout,
                               QWidget)

from akeno import __version__
from akeno.i18n import language, t
from akeno.licensing import LicenseManager, LicenseView, Status
from akeno.licensing.api import ApiError
from akeno.licensing.manager import DeviceLimitReached
from akeno.ui import art, assets, theme, workers
from akeno.ui.account_ui import CodeEdit, LicenseSummary, error_text
from akeno.ui.widgets import button, clear_layout, label

SUITE_NAME = "AKENO Gaming Suite"
FEATURES = ("Community DLSS 5 manager for your games and FiveM", "FPS Booster and benchmarks with real measurements",
            "Desktop Studio: wallpapers, dock and widgets", "One license for every module")


class _Art(QFrame):
    """Left half: the AKENO landscape artwork with the wordmark and what the suite contains."""

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(360)
        self._pm: QPixmap | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 36, 36, 30)
        word = art.wordmark(54)
        logo = QLabel()
        logo.setAccessibleName("AKENO")
        if not word.isNull():
            logo.setPixmap(word)
        else:
            logo.setText("AKENO")
            logo.setStyleSheet("font-size: 28pt; font-weight: 800; letter-spacing: 3px;")
        lay.addWidget(logo)
        title = label(t(SUITE_NAME), "h1")
        lay.addWidget(title)
        lay.addSpacing(8)
        for line in FEATURES:
            row = label("•  " + t(line), shrink=True)
            row.setStyleSheet("font-size: 10.5pt; color: #DCE3FF;")
            lay.addWidget(row)
        lay.addStretch(1)
        foot = label(t("Your games, mods and settings stay on this PC and are never changed by licensing."),
                     "dim", shrink=True)
        lay.addWidget(foot)
        lay.addWidget(label(f"v{__version__}", "muted"))

    def resizeEvent(self, e):
        self._pm = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        if self._pm is None or self._pm.size() != self.size() * self.devicePixelRatioF():
            dpr = self.devicePixelRatioF()
            bg = assets.cover(assets.SIDEBAR_LANDSCAPE, max(1, self.width()), max(1, self.height()), dpr,
                              h_align=0.5, v_align=0.4)
            self._pm = bg if not bg.isNull() else art.banner(max(1, self.width()), max(1, self.height()), seed=3)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)
        p.fillRect(self.rect(), theme.qc("#060D1C", 175))


class LicenseGate(QWidget):
    unlocked = Signal(object)                 # LicenseView
    language_changed = Signal(str)            # the shell builds a new gate in that language

    def __init__(self, manager: LicenseManager, parent=None):
        super().__init__(parent)
        self.m = manager
        self.setWindowTitle(t(SUITE_NAME))
        self.setWindowIcon(QIcon(art.app_icon_pixmap(256)))
        self.setObjectName("Root")
        self.setMinimumSize(900, 620)
        self.resize(1180, 760)
        self.busy = False
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_Art(), 5)
        right = QWidget()
        right.setObjectName("Root")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(44, 36, 44, 24)
        rl.setSpacing(10)
        self.heading = label("", "h1")
        rl.addWidget(self.heading)
        self.sub = label("", "dim", shrink=True)
        rl.addWidget(self.sub)
        self.notice = label("", shrink=True)
        self.notice.setVisible(False)
        rl.addWidget(self.notice)
        self.pages = QStackedWidget()
        rl.addWidget(self.pages, 1)
        foot = QHBoxLayout()
        self.server_link = button(t("License server…"), kind="link", on_click=lambda: self.show_page("server"))
        self.server_link.setVisible(not self.m.config.locked)     # a built AKENO talks to its own server only
        foot.addWidget(self.server_link)
        foot.addStretch(1)
        self.lang = QComboBox()
        self.lang.addItem("English", "en")
        self.lang.addItem("Deutsch", "de")
        self.lang.setCurrentIndex(1 if language() == "de" else 0)
        self.lang.setAccessibleName(t("Language"))
        self.lang.currentIndexChanged.connect(self._language)
        foot.addWidget(self.lang)
        rl.addLayout(foot)
        root.addWidget(right, 6)
        self._build_pages()
        self.refresh(self.m.view())

    # -- pages -------------------------------------------------------------------------------------------------------
    def _page(self, key: str) -> QVBoxLayout:
        w = QWidget()
        w.setObjectName(f"gate_{key}")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        self.pages.addWidget(w)
        self._keys.append(key)
        return lay

    def _field(self, lay: QVBoxLayout, text: str, echo: bool = False) -> QLineEdit:
        lay.addWidget(label(t(text), "key"))
        edit = QLineEdit()
        edit.setAccessibleName(t(text))
        if echo:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(edit)
        return edit

    def _build_pages(self) -> None:
        self._keys: list[str] = []
        # sign in
        lay = self._page("signin")
        self.si_email = self._field(lay, "E-mail address")
        self.si_password = self._field(lay, "Password", echo=True)
        self.si_totp_label = label(t("Authenticator code"), "key")
        self.si_totp = QLineEdit()
        self.si_totp.setAccessibleName(t("Authenticator code"))
        self.si_totp.setMaxLength(12)
        lay.addWidget(self.si_totp_label)
        lay.addWidget(self.si_totp)
        self.si_totp_label.setVisible(False)
        self.si_totp.setVisible(False)
        self.btn_signin = button(t("Sign in"), "shield", "primary", on_click=self._sign_in)
        lay.addWidget(self.btn_signin)
        for w in (self.si_email, self.si_password, self.si_totp):
            w.returnPressed.connect(self._sign_in)
        links = QHBoxLayout()
        links.addWidget(button(t("Create account"), kind="link", on_click=lambda: self.show_page("register")))
        links.addWidget(button(t("Forgot password?"), kind="link", on_click=lambda: self.show_page("forgot")))
        links.addStretch(1)
        lay.addLayout(links)
        lay.addWidget(label(t("Have an activation code? Sign in or create an account first; you redeem it in the "
                              "next step."), "muted", shrink=True))
        lay.addStretch(1)
        # register
        lay = self._page("register")
        self.rg_name = self._field(lay, "Name (optional)")
        self.rg_email = self._field(lay, "E-mail address")
        self.rg_password = self._field(lay, "Password (at least 10 characters)", echo=True)
        self.rg_repeat = self._field(lay, "Repeat the password", echo=True)
        self.btn_register = button(t("Create account"), "shield", "primary", on_click=self._register)
        lay.addWidget(self.btn_register)
        self.rg_repeat.returnPressed.connect(self._register)
        lay.addWidget(button(t("I already have an account"), kind="link", on_click=lambda: self.show_page("signin")))
        lay.addStretch(1)
        # redeem
        lay = self._page("redeem")
        self.summary = LicenseSummary()
        lay.addWidget(self.summary)
        lay.addSpacing(6)
        lay.addWidget(label(t("Activation code"), "key"))
        self.code = CodeEdit()
        self.code.submitted.connect(lambda _: self._redeem())
        lay.addWidget(self.code)
        row = QHBoxLayout()
        self.btn_redeem = button(t("Activate"), "check", "primary", on_click=self._redeem)
        row.addWidget(self.btn_redeem)
        self.btn_check = button(t("Check again"), "rotate", on_click=self._check)
        row.addWidget(self.btn_check)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.btn_site = button(t("My account on the website"), kind="link", on_click=self._open_site)
        row.addWidget(self.btn_site)
        row.addWidget(button(t("Sign out"), kind="link", on_click=self._sign_out))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        # forgot password
        lay = self._page("forgot")
        lay.addWidget(label(t("Enter the e-mail address of your account. If it exists, a link to choose a new "
                              "password is sent to it."), "dim", shrink=True))
        self.fp_email = self._field(lay, "E-mail address")
        self.btn_forgot = button(t("Send reset link"), "shield", "primary", on_click=self._forgot)
        lay.addWidget(self.btn_forgot)
        lay.addWidget(button(t("Back to sign-in"), kind="link", on_click=lambda: self.show_page("signin")))
        lay.addStretch(1)
        # device limit
        lay = self._page("devices")
        self.dev_text = label("", "dim", shrink=True)
        lay.addWidget(self.dev_text)
        self.dev_list = QVBoxLayout()
        lay.addLayout(self.dev_list)
        lay.addWidget(button(t("Back to sign-in"), kind="link", on_click=self._sign_out))
        lay.addStretch(1)
        # license server address
        lay = self._page("server")
        locked = self.m.config.locked
        lay.addWidget(label(t("This copy of AKENO accepts licenses only from the official AKENO license server it "
                              "was built with. Download AKENO from the official release page.") if locked else
                            t("The address of the AKENO license server. It comes with this copy of AKENO; change it "
                              "only if you run your own server."), "dim", shrink=True))
        self.server_edit = self._field(lay, "Server address (https://…)")
        self.server_edit.setText(self.m.config.server_url)
        self.server_edit.setReadOnly(locked)
        row = QHBoxLayout()
        save = button(t("Save"), "check", "primary", on_click=self._save_server)
        save.setVisible(not locked)
        row.addWidget(save)
        row.addWidget(button(t("Back"), kind="link", on_click=lambda: self.refresh(self.m.view())))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    TITLES = {"signin": ("Welcome to AKENO", "Sign in to continue."),
              "register": ("Create your AKENO account", "One account and one license for every AKENO module."),
              "redeem": ("Activate AKENO", "Redeem an activation code. It runs from the moment you redeem it."),
              "forgot": ("Forgot your password?", ""),
              "devices": ("Too many PCs", ""),
              "server": ("License server", "")}

    def show_page(self, key: str) -> None:
        self.pages.setCurrentIndex(self._keys.index(key))
        title, sub = self.TITLES[key]
        self.heading.setText(t(title))
        self.sub.setText(t(sub))
        self.sub.setVisible(bool(sub))
        if key != "redeem":
            self._say("")

    def current(self) -> str:
        return self._keys[self.pages.currentIndex()]

    def _say(self, text: str, error: bool = False) -> None:
        self.notice.setText(text)
        self.notice.setStyleSheet(f"color: {theme.RED if error else theme.GREEN}; font-weight: 600;")
        self.notice.setVisible(bool(text))

    def refresh(self, view: LicenseView) -> None:
        if view.unlocked:
            self.unlocked.emit(view)
            return
        if view.status is Status.NOT_CONFIGURED:
            self.show_page("server")
            self._say(t(view.message), error=True)
            return
        if view.status is Status.NOT_LOGGED_IN:
            self.show_page("signin")
            if view.email:
                self.si_email.setText(view.email)
            if view.needs_sign_in:
                self._say(t("Your sign-in ended. Please sign in again."), error=True)
            return
        self.show_page("redeem")
        self.summary.show_view(view)
        self.btn_check.setVisible(view.status in (Status.SERVER_UNAVAILABLE, Status.DISABLED, Status.EXPIRED))
        self.btn_redeem.setEnabled(view.status is not Status.DISABLED)
        if view.status is Status.EXPIRED:
            self.heading.setText(t("Your license has ended"))
        elif view.status is Status.SERVER_UNAVAILABLE:
            self.heading.setText(t("License check needed"))
        elif view.status is Status.DISABLED:
            self.heading.setText(t("License disabled"))

    # -- actions -----------------------------------------------------------------------------------------------------
    def _run(self, fn, on_done=None, buttons=()) -> None:
        if self.busy:
            return
        self.busy = True
        for b in buttons:
            b.setEnabled(False)
        self.setCursor(Qt.CursorShape.BusyCursor)

        def finish():
            self.busy = False
            for b in buttons:
                b.setEnabled(True)
            self.unsetCursor()

        def done(result):
            finish()
            (on_done or self.refresh)(result)

        def failed(exc):
            finish()
            self._failed(exc)
        workers.run(fn, done, failed)

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, DeviceLimitReached):
            self._devices(exc)
            return
        if isinstance(exc, ApiError) and exc.code == "totp_required":
            self.si_totp_label.setVisible(True)
            self.si_totp.setVisible(True)
            self.si_totp.setFocus()
        self._say(error_text(exc), error=True)
        view = self.m.view()
        if view.unlocked:
            self.unlocked.emit(view)
        elif self.current() == "redeem":
            self.summary.show_view(view)

    def _sign_in(self) -> None:
        email, password = self.si_email.text().strip(), self.si_password.text()
        if not email or not password:
            self._say(t("Enter your e-mail address and password."), error=True)
            return
        totp = self.si_totp.text().strip() or None
        self._run(lambda: self.m.sign_in(email, password, totp), self._signed_in, (self.btn_signin,))

    def _signed_in(self, view: LicenseView) -> None:
        self.si_password.clear()
        self.si_totp.clear()
        self.refresh(view)

    def _register(self) -> None:
        if self.rg_password.text() != self.rg_repeat.text():
            self._say(t("The passwords do not match."), error=True)
            return
        if len(self.rg_password.text()) < 10:
            self._say(t(ERR_SHORT), error=True)
            return
        name, email, pw = self.rg_name.text(), self.rg_email.text(), self.rg_password.text()
        lang = self.lang.currentData()
        self._run(lambda: self.m.create_account(email, pw, name, lang), self._registered, (self.btn_register,))

    def _registered(self, view: LicenseView) -> None:
        for w in (self.rg_password, self.rg_repeat):
            w.clear()
        self.refresh(view)
        self._say(t("Account created. We sent you an e-mail to confirm your address."))

    def _redeem(self) -> None:
        code = self.code.code()
        if not code:
            self._say(t("Enter your activation code."), error=True)
            return
        self._run(lambda: self.m.redeem(code), self._redeemed, (self.btn_redeem,))

    def _redeemed(self, view: LicenseView) -> None:
        self.code.clear()
        self._say(t("Code redeemed."))
        self.refresh(view)

    def _check(self) -> None:
        self._run(self.m.validate, None, (self.btn_check,))

    def _forgot(self) -> None:
        email = self.fp_email.text().strip()
        if not email:
            self._say(t("Enter your e-mail address."), error=True)
            return
        self._run(lambda: self.m.request_password_reset(email), lambda _: self._say(
            t("If an account with this address exists, a reset link is on its way. Check your inbox.")),
            (self.btn_forgot,))

    def _sign_out(self) -> None:
        self._run(self.m.sign_out)

    def _open_site(self) -> None:
        if self.m.config.website:
            webbrowser.open(self.m.config.website + "/account")

    def _devices(self, exc: DeviceLimitReached) -> None:
        self.show_page("devices")
        self.dev_text.setText(t("Your license allows {n} PC(s) and all are in use. Remove a PC you no longer use "
                                "(it can sign in again later), then AKENO continues here.", n=exc.limit))
        clear_layout(self.dev_list)
        for dev in exc.devices:
            row = QHBoxLayout()
            text = label(f"{dev.get('label') or 'PC'} · " + t("last seen {d}", d=str(dev.get("last_seen_at", ""))[:10]),
                         shrink=True)
            row.addWidget(text, 1)
            row.addWidget(button(t("Remove"), kind="danger",
                                 on_click=lambda _=False, d=dev["id"]: self._remove_device(d)))
            holder = QWidget()
            holder.setLayout(row)
            self.dev_list.addWidget(holder)

    def _remove_device(self, device_id: str) -> None:
        def work():
            self.m.remove_device(device_id)
            return self.m.retry_device()
        self._run(work)

    def _save_server(self) -> None:
        try:
            self.m.set_server(self.server_edit.text())
        except (ValueError, PermissionError) as exc:
            self._say(error_text(exc), error=True)
            return
        self._say(t("Saved."))
        self.refresh(self.m.view())

    def _language(self) -> None:
        self.language_changed.emit(self.lang.currentData())


ERR_SHORT = "Choose a password with at least 10 characters that is not your e-mail address."
