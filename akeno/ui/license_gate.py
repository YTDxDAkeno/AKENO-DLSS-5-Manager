"""The welcome screen: sign in, create an account, confirm the e-mail address, reset the password, redeem a code.

It is all of AKENO that runs without an active license. Nothing else is loaded until the license is active (no
game scan, no mod engine, no database of games), and closing it closes AKENO.

Every request runs on a worker thread: the window never freezes, the button that started it shows what is
happening and cannot be pressed twice, and the rest of the form waits until the answer is in. Nothing that changes
data (an account, a reset request) is sent again automatically.

The server decides: whether an address is confirmed comes from ``/auth/me`` (never from a local flag), a reset
request answers the same for every address, and an e-mail counts as sent only when the server says it was.
"""

from __future__ import annotations

import html
import webbrowser

import shiboken6
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from akeno import __version__
from akeno.i18n import language, t
from akeno.licensing import LicenseManager, LicenseView, Status, forms
from akeno.licensing.api import ApiError, ServerUnavailable
from akeno.licensing.manager import DeviceLimitReached
from akeno.ui import art, assets, icons, theme, workers
from akeno.ui.account_ui import (Banner, CodeEdit, CooldownButton, Field, LicenseSummary, PasswordEdit, RuleList,
                                 SignInForm, StrengthMeter, error_text, mail_badge, retry_after, set_busy)
from akeno.ui.widgets import (Divider, FlowLayout, Spinner, StatusPill, button, clear_layout, fit_to_screen,
                              label)

SUITE_NAME = "AKENO Gaming Suite"
FEATURES = ("Community DLSS 5 manager for your games and FiveM", "FPS Booster and benchmarks with real measurements",
            "Desktop Studio: wallpapers, dock and widgets", "One license for every module")
COLUMN = 460                    # the form column's width at most (wider windows centre it)
POLL_MS = 15_000                # while "Check your inbox" is open: has the address been confirmed meanwhile?


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
        lay.addSpacing(10)
        for line in FEATURES:
            row = QHBoxLayout()
            row.setSpacing(10)
            tick = QLabel()
            tick.setPixmap(icons.pixmap("check", theme.ACCENT_TEXT, 16))
            tick.setFixedSize(18, 18)
            row.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            text = label(t(line), shrink=True)
            text.setStyleSheet("font-size: 10.5pt; color: #DCE3FF;")
            row.addWidget(text, 1)
            lay.addLayout(row)
            lay.addSpacing(2)
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


class _Pages(QStackedWidget):
    """A stack as tall as the page it shows (a short page is not padded to the longest one)."""

    def __init__(self):
        super().__init__()
        self.currentChanged.connect(self._fit)

    def _fit(self, index: int) -> None:
        for i in range(self.count()):
            w = self.widget(i)
            w.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored)
        self.updateGeometry()


class LicenseGate(QWidget):
    unlocked = Signal(object)                 # LicenseView
    language_changed = Signal(str)            # the shell builds a new gate in that language

    TITLES = {"signin": ("Welcome to AKENO", "Sign in with your AKENO account to continue."),
              "register": ("Create your AKENO account", "One account and one license for every AKENO module."),
              "verify": ("Check your inbox", ""),
              "redeem": ("Activate AKENO", "Redeem an activation code. It runs from the moment you redeem it."),
              "forgot": ("Forgot your password?", "We'll e-mail you a link to choose a new one."),
              "forgot_sent": ("Check your inbox", ""),
              "devices": ("Too many PCs", ""),
              "server": ("License server", "")}

    def __init__(self, manager: LicenseManager, parent=None, probe: bool = True, recovery=None):
        super().__init__(parent)
        self.m = manager
        self._recovery = recovery                 # () -> AppServices: "Restore game files" without a license
        self.setWindowTitle(t(SUITE_NAME))
        self.setWindowIcon(QIcon(art.app_icon_pixmap(256)))
        self.setObjectName("Root")
        fit_to_screen(self, 1180, 780, 900, 620)
        self.busy = False
        self.info: dict = {}
        self._probe = probe
        self._probed = False
        self._skip_verify = False
        self._reset_email = ""
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_Art(), 5)
        right = QWidget()
        right.setObjectName("Root")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        body = QWidget()
        body.setObjectName("Root")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(44, 32, 44, 20)
        bl.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(COLUMN)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        self.heading = label("", "h1")
        self.heading.setAccessibleName(t("Page title"))
        cl.addWidget(self.heading)
        self.sub = label("", "dim", shrink=True)
        cl.addWidget(self.sub)
        cl.addSpacing(4)
        self.notice = Banner()
        cl.addWidget(self.notice)
        self.pages = _Pages()
        cl.addWidget(self.pages)
        centre = QHBoxLayout()
        centre.addStretch(1)
        centre.addWidget(column, 100)
        centre.addStretch(1)
        bl.addLayout(centre)
        bl.addStretch(2)
        scroll.setWidget(body)
        rl.addWidget(scroll, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(44, 8, 44, 16)
        foot.setSpacing(12)
        self.net = StatusPill(t("Checking the connection…"), "pending")
        foot.addWidget(self.net)
        self.server_link = button(t("License server…"), kind="link", on_click=lambda: self.show_page("server"))
        self.server_link.setVisible(not self.m.config.locked)     # a built AKENO talks to its own server only
        foot.addWidget(self.server_link)
        self.restore_link = button(t("Restore game files…"), kind="link", on_click=self._restore)
        self.restore_link.setToolTip(t("Put back the original game files and settings AKENO changed. Works without "
                                       "an active license."))
        self.restore_link.setVisible(recovery is not None)
        foot.addWidget(self.restore_link)
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
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._poll_verified)
        self._build_pages()
        self.refresh(self.m.view())

    # -- pages -------------------------------------------------------------------------------------------------------
    def _page(self, key: str) -> QVBoxLayout:
        w = QWidget()
        w.setObjectName(f"gate_{key}")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(12)
        self.pages.addWidget(w)
        self._keys.append(key)
        return lay

    @staticmethod
    def _action_row(main, spinner: Spinner | None = None, *rest) -> FlowLayout:
        """Buttons side by side, wrapping onto a second line when a translation is too long for one."""
        row = FlowLayout(spacing=10)
        row.addWidget(main)
        if spinner is not None:
            row.addWidget(spinner)
        for w in rest:
            row.addWidget(w)
        return row

    @staticmethod
    def _primary(text: str, on_click, icon_name: str | None = None):
        b = button(text, icon_name, "primary", on_click=on_click)
        b.setMinimumHeight(theme.CONTROL_HEIGHT + 6)
        b.setStyleSheet("QPushButton { padding-left: 22px; padding-right: 22px; }")
        return b

    @staticmethod
    def _link_row(text: str, link_text: str, on_click) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        if text:
            row.addWidget(label(text, "dim"))
        link = button(link_text, kind="link", on_click=on_click)
        row.addWidget(link)
        row.addStretch(1)
        return row

    def _build_pages(self) -> None:
        self._keys: list[str] = []
        self._build_signin()
        self._build_register()
        self._build_verify()
        self._build_redeem()
        self._build_forgot()
        self._build_devices()
        self._build_server()
        for i in range(self.pages.count()):
            self.pages.widget(i).layout().addStretch(1)     # wrapped text asks for more height than it uses
        self._last_page = ""
        self.pages._fit(self.pages.currentIndex())

    def _build_signin(self) -> None:
        lay = self._page("signin")
        self.signin = SignInForm()
        self.signin.remember.setChecked(self.m.remember)
        self.signin.submitted.connect(self._sign_in)
        self.signin.forgot.connect(lambda: self._to_forgot(self.signin.email.text()))
        self.si_email, self.si_password, self.si_totp = self.signin.email, self.signin.password, self.signin.totp
        lay.addWidget(self.signin)
        self.btn_signin = self._primary(t("Sign in"), self._sign_in)
        self.si_spinner = Spinner()
        lay.addLayout(self._action_row(self.btn_signin, self.si_spinner))
        lay.addWidget(Divider())
        lay.addLayout(self._link_row(t("New to AKENO?"), t("Create an account"), lambda: self.show_page("register")))
        lay.addWidget(label(t("Have an activation code? Sign in or create an account first; you redeem it in the "
                              "next step."), "muted", shrink=True))

    def _build_register(self) -> None:
        lay = self._page("register")
        self.f_name = Field(t("Display name (optional)"),
                            hint=t("Shown in your account. You sign in with your e-mail address."))
        self.rg_name = self.f_name.edit
        self.rg_name.setMaxLength(forms.MAX_NAME)
        self.f_email = Field(t("E-mail address"), hint=t("We send a link to confirm it."))
        self.rg_email = self.f_email.edit
        self.rg_email.setMaxLength(forms.MAX_EMAIL)
        self.rg_email.setPlaceholderText("name@example.com")
        self.f_password = Field(t("Password"), PasswordEdit(t("Password")))
        self.rg_password = self.f_password.edit
        self.strength = StrengthMeter()
        self.rules = RuleList()
        self.f_password.extra.addWidget(self.strength)
        self.f_password.extra.addWidget(self.rules)
        self.f_repeat = Field(t("Confirm password"), PasswordEdit(t("Confirm password")))
        self.rg_repeat = self.f_repeat.edit
        for f in (self.f_name, self.f_email, self.f_password, self.f_repeat):
            lay.addWidget(f)
        self.rg_remember = QCheckBox(t("Keep me signed in on this PC"))
        self.rg_remember.setChecked(self.m.remember)
        lay.addWidget(self.rg_remember)
        self.btn_register = self._primary(t("Create account"), self._register)
        self.rg_spinner = Spinner()
        lay.addLayout(self._action_row(self.btn_register, self.rg_spinner))
        lay.addWidget(Divider())
        lay.addLayout(self._link_row(t("Already have an account?"), t("Sign in"), lambda: self.show_page("signin")))
        self.rg_password.textChanged.connect(self._password_changed)
        self.rg_email.textChanged.connect(self._password_changed)
        self.rg_repeat.textChanged.connect(self._repeat_changed)
        self.rg_email.editingFinished.connect(self._check_email_field)
        for w in (self.rg_name, self.rg_email, self.rg_password, self.rg_repeat):
            w.returnPressed.connect(self._register)
        self._tried_register = False

    def _build_verify(self) -> None:
        lay = self._page("verify")
        lay.addWidget(mail_badge())
        self.vf_text = label("", shrink=True)
        self.vf_text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.vf_text)
        self.vf_hint = label(t("The link is valid for 48 hours. Can't find the e-mail? Check your spam or junk "
                               "folder."), "muted", shrink=True)
        lay.addWidget(self.vf_hint)
        self.btn_verified = self._primary(t("I've verified my e-mail"), self._check_verified)
        self.vf_spinner = Spinner()
        self.btn_resend = CooldownButton(t("Resend e-mail"), icon_name="mail")
        self.btn_resend.clicked.connect(self._resend)
        lay.addLayout(self._action_row(self.btn_verified, self.vf_spinner, self.btn_resend))
        self.vf_change_link = button(t("Wrong address? Change e-mail"), kind="link", on_click=self._toggle_change)
        lay.addLayout(self._action_row(self.vf_change_link))
        self.vf_change = QFrame()
        self.vf_change.setObjectName("SubCard")
        cl = QVBoxLayout(self.vf_change)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(10)
        cl.addWidget(label(t("We send a new link to the new address. Your account keeps its current address until "
                             "you open it."), "dim", shrink=True))
        self.f_new_email = Field(t("New e-mail address"))
        self.f_new_email.edit.setMaxLength(forms.MAX_EMAIL)
        self.f_change_pw = Field(t("Current password"), PasswordEdit(t("Current password")))
        cl.addWidget(self.f_new_email)
        cl.addWidget(self.f_change_pw)
        self.btn_change = button(t("Send verification link"), "mail", "primary", on_click=self._change_email)
        self.ch_spinner = Spinner()
        cl.addLayout(self._action_row(self.btn_change, self.ch_spinner,
                                      button(t("Cancel"), kind="ghost", on_click=self._toggle_change)))
        for w in (self.f_new_email.edit, self.f_change_pw.edit):
            w.returnPressed.connect(self._change_email)
        self.vf_change.setVisible(False)
        lay.addWidget(self.vf_change)
        lay.addWidget(Divider())
        row = QHBoxLayout()
        self.vf_skip = button(t("Continue without verifying"), kind="link", on_click=self._skip)
        row.addWidget(self.vf_skip)
        row.addStretch(1)
        row.addWidget(button(t("Sign out"), kind="link", on_click=self._sign_out))
        lay.addLayout(row)

    def _build_redeem(self) -> None:
        lay = self._page("redeem")
        self.summary = LicenseSummary()
        lay.addWidget(self.summary)
        lay.addSpacing(2)
        self.f_code = Field(t("Activation code"), CodeEdit())
        self.code: CodeEdit = self.f_code.edit
        self.code.submitted.connect(lambda _: self._redeem())
        lay.addWidget(self.f_code)
        self.btn_redeem = self._primary(t("Activate"), self._redeem, "check")
        self.rd_spinner = Spinner()
        self.btn_check = button(t("Check again"), "rotate", on_click=self._check)
        lay.addLayout(self._action_row(self.btn_redeem, self.rd_spinner, self.btn_check))
        lay.addWidget(Divider())
        row = QHBoxLayout()
        self.btn_site = button(t("My account on the website"), "external", kind="link", on_click=self._open_site)
        row.addWidget(self.btn_site)
        row.addStretch(1)
        row.addWidget(button(t("Sign out"), kind="link", on_click=self._sign_out))
        lay.addLayout(row)

    def _build_forgot(self) -> None:
        lay = self._page("forgot")
        lay.addWidget(label(t("Enter the e-mail address of your AKENO account. If an account exists for it, we'll "
                              "send a link to choose a new password."), "dim", shrink=True))
        self.f_reset = Field(t("E-mail address"))
        self.fp_email = self.f_reset.edit
        self.fp_email.setMaxLength(forms.MAX_EMAIL)
        self.fp_email.setPlaceholderText("name@example.com")
        self.fp_email.returnPressed.connect(self._forgot)
        self.fp_email.textEdited.connect(lambda _: self.f_reset.set_error(""))
        lay.addWidget(self.f_reset)
        self.btn_forgot = self._primary(t("Send reset link"), self._forgot, "mail")
        self.fp_spinner = Spinner()
        lay.addLayout(self._action_row(self.btn_forgot, self.fp_spinner))
        lay.addWidget(Divider())
        lay.addLayout(self._link_row("", t("Back to sign-in"), lambda: self.show_page("signin")))
        # the confirmation
        lay = self._page("forgot_sent")
        lay.addWidget(mail_badge(icon_name="mail-check"))
        self.fs_text = label("", shrink=True)
        self.fs_text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.fs_text)
        lay.addWidget(label(t("The link is valid for one hour and works once. It can take a few minutes to arrive; "
                              "check your spam or junk folder too."), "muted", shrink=True))
        lay.addWidget(label(t("Choose your new password on the AKENO website, then sign in here with it."),
                            "muted", shrink=True))
        self.btn_back = self._primary(t("Back to sign-in"), self._back_to_sign_in)
        self.btn_again = CooldownButton(t("Send again"), icon_name="mail")
        self.btn_again.clicked.connect(lambda: self._forgot(again=True))
        self.fs_spinner = Spinner()
        lay.addLayout(self._action_row(self.btn_back, None, self.btn_again, self.fs_spinner))
        lay.addLayout(self._link_row("", t("Use a different e-mail address"), lambda: self._to_forgot("")))

    def _build_devices(self) -> None:
        lay = self._page("devices")
        self.dev_text = label("", "dim", shrink=True)
        lay.addWidget(self.dev_text)
        self.dev_list = QVBoxLayout()
        self.dev_list.setSpacing(8)
        lay.addLayout(self.dev_list)
        lay.addWidget(button(t("Back to sign-in"), kind="link", on_click=self._sign_out))

    def _build_server(self) -> None:
        lay = self._page("server")
        locked = self.m.config.locked
        lay.addWidget(label(t("This copy of AKENO accepts licenses only from the official AKENO license server it "
                              "was built with. Download AKENO from the official release page.") if locked else
                            t("The address of the AKENO license server. It comes with this copy of AKENO; change it "
                              "only if you run your own server."), "dim", shrink=True))
        self.f_server = Field(t("Server address (https://…)"))
        self.server_edit = self.f_server.edit
        self.server_edit.setText(self.m.config.server_url)
        self.server_edit.setReadOnly(locked)
        lay.addWidget(self.f_server)
        save = button(t("Save"), "check", "primary", on_click=self._save_server)
        save.setVisible(not locked)
        lay.addLayout(self._action_row(save, None, button(t("Back"), kind="link",
                                                          on_click=lambda: self.refresh(self.m.view()))))

    # -- navigation --------------------------------------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        if self.busy:
            return
        self.pages.setCurrentIndex(self._keys.index(key))
        title, sub = self.TITLES[key]
        self.heading.setText(t(title))
        self.sub.setText(t(sub))
        self.sub.setVisible(bool(sub))
        if key != self._last_page and key != "redeem":
            self._say("")
        self._last_page = key
        if key == "verify":
            self.poll.start()
        else:
            self.poll.stop()
        if key == "register":
            self._registration_state()
        QTimer.singleShot(0, self._focus_first)
        self.scroll.verticalScrollBar().setValue(0)

    def current(self) -> str:
        return self._keys[self.pages.currentIndex()]

    def _focus_first(self) -> None:
        if not shiboken6.isValid(self) or not self.isVisible():
            return
        page = self.pages.currentWidget()
        for edit in page.findChildren(QLineEdit):
            if edit.isVisible() and edit.isEnabled() and not edit.isReadOnly() and not edit.text():
                edit.setFocus()
                return

    def _say(self, text: str, error: bool = False, kind: str | None = None, action: str | None = None,
             on_action=None) -> None:
        self.notice.show_message(text, kind or ("error" if error else "success"), action, on_action)

    def refresh(self, view: LicenseView) -> None:
        if self.busy:
            return
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
                self.heading.setText(t("Welcome back"))         # someone who has signed in on this PC before
                if not self.si_email.text():
                    self.si_email.setText(view.email)
            if view.needs_sign_in and view.session_ended != "not_remembered":
                self._say(t("You were signed out on this PC. Please sign in again.")
                          if view.session_ended == "session_revoked" else
                          t("Your session has expired. Please sign in again."), kind="warn")
            return
        if self._wants_verification(view):
            self.show_verify()
            return
        self.show_page("redeem")
        self.summary.show_view(view)
        self.btn_check.setVisible(view.status in (Status.SERVER_UNAVAILABLE, Status.DISABLED, Status.EXPIRED))
        self.btn_redeem.setEnabled(view.status is not Status.DISABLED)
        self.code.setEnabled(view.status is not Status.DISABLED)
        if view.status is Status.EXPIRED:
            self.heading.setText(t("Your license has ended"))
        elif view.status is Status.SERVER_UNAVAILABLE:
            self.heading.setText(t("License check needed"))
        elif view.status is Status.DISABLED:
            self.heading.setText(t("License disabled"))
        if view.email_verified is False and self.notice.kind in ("", "info"):
            self._say(t("Your e-mail address is not verified yet."), kind="info", action=t("Verify now"),
                      on_action=self.show_verify)

    def _wants_verification(self, view: LicenseView) -> bool:
        if view.email_verified is not False or view.status is Status.DISABLED or self._skip_verify:
            return False
        return bool(self.m.verification) or self.m.verification_required()

    # -- running a request -------------------------------------------------------------------------------------------
    def _run(self, fn, on_done=None, busy: tuple = (), on_error=None) -> None:
        """``busy``: (button, spinner, text) of the action. The whole form waits until the answer is in."""
        if self.busy:
            return
        self.busy = True
        page = self.pages.currentWidget()
        page.setEnabled(False)
        self.lang.setEnabled(False)
        btn = spinner = None
        if busy:
            btn, spinner, text = busy
            if isinstance(btn, CooldownButton):
                btn.set_busy(True, text)
                if spinner is not None:
                    spinner.start()
            else:
                set_busy(btn, spinner, True, text)
        self.setCursor(Qt.CursorShape.BusyCursor)

        def finish():
            if not shiboken6.isValid(self):
                return False
            self.busy = False
            page.setEnabled(True)
            self.lang.setEnabled(True)
            if btn is not None:
                if isinstance(btn, CooldownButton):
                    btn.set_busy(False)
                    if spinner is not None:
                        spinner.stop()
                else:
                    set_busy(btn, spinner, False)
            self.unsetCursor()
            return True

        def done(result):
            if finish():
                self._online()
                (on_done or self.refresh)(result)

        def failed(exc):
            if finish():
                self._network(exc)
                (on_error or self._failed)(exc)
        workers.run(fn, done, failed)

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, DeviceLimitReached):
            self._devices(exc)
            return
        if isinstance(exc, ApiError) and exc.code == "email_not_verified":
            self._skip_verify = False
            self.show_verify()
            self._say(error_text(exc), kind="warn")
            return
        if self.current() == "signin":
            self.signin.failed(exc)
        self._say(error_text(exc), error=True)
        view = self.m.view()
        if view.unlocked:
            self.unlocked.emit(view)
        elif view.status is Status.NOT_LOGGED_IN and self.current() not in ("signin", "register", "forgot",
                                                                           "forgot_sent", "server"):
            text = self.notice.label.text()
            self.refresh(view)
            self._say(text, kind="warn")
        elif self.current() == "redeem":
            self.summary.show_view(view)

    # -- connection status -------------------------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if self._probe and not self._probed:
            self._probed = True
            workers.run(lambda: self.m.server_info(fresh=True), self._info, self._info_failed)

    def _info(self, info: dict) -> None:
        if not shiboken6.isValid(self):
            return
        self.info = info or {}
        if self.info.get("maintenance"):
            self.net.set(t("AKENO is being maintained"), "warn")
        else:
            self._online()
        if self.current() == "register":
            self._registration_state()
        view = self.m.view()
        if not view.unlocked and self.current() == "redeem" and self._wants_verification(view):
            self.show_verify()

    def _info_failed(self, exc: Exception) -> None:
        if shiboken6.isValid(self):
            self._network(exc)

    def _online(self) -> None:
        if not self.info.get("maintenance"):
            self.net.set(t("Connected to AKENO"), "ok")

    def _network(self, exc: Exception) -> None:
        if isinstance(exc, ServerUnavailable) and exc.code and exc.kind != "maintenance":
            self._online()                  # the server answered: only one of its services is unavailable
        elif isinstance(exc, ServerUnavailable):
            if exc.kind == "maintenance":
                self.net.set(t("AKENO is being maintained"), "warn")
            elif exc.kind in ("offline", "timeout", "certificate", "network"):
                self.net.set(t("No connection to AKENO"), "fail")
            else:
                self.net.set(t("AKENO service unavailable"), "warn")
        elif isinstance(exc, ApiError):
            self._online()

    def _registration_state(self) -> None:
        closed = self.info.get("registration_enabled") is False
        self.btn_register.setEnabled(not closed and not self.busy)
        if closed:
            self._say(t("New accounts cannot be created at the moment."), kind="info")

    # -- sign in -----------------------------------------------------------------------------------------------------
    def _sign_in(self) -> None:
        if self.busy or not self.signin.check():
            return
        email, password, totp, remember = self.signin.values()
        self._say("")
        self._run(lambda: self.m.sign_in(email, password, totp, remember), self._signed_in,
                  (self.btn_signin, self.si_spinner, t("Signing in…")))

    def _signed_in(self, view: LicenseView) -> None:
        self.signin.done()
        self._skip_verify = False
        self.refresh(view)

    # -- register ----------------------------------------------------------------------------------------------------
    def _password_changed(self, *_):
        pw, email = self.rg_password.text(), self.rg_email.text()
        problems = forms.password_problems(pw, email)
        self.strength.set(pw, email)
        self.rules.set(problems, empty=not pw)
        if self._tried_register:
            self.f_password.set_error("" if not problems else t("The password does not meet every rule yet."))
        self._repeat_changed()

    def _repeat_changed(self, *_):
        repeat = self.rg_repeat.text()
        if repeat and repeat != self.rg_password.text() and (self._tried_register or len(repeat) >=
                                                             len(self.rg_password.text())):
            self.f_repeat.set_error(t("The passwords do not match."))
        else:
            self.f_repeat.set_error("")

    def _check_email_field(self) -> bool:
        email = self.rg_email.text().strip()
        if not email:
            if self._tried_register:
                self.f_email.set_error(t("Enter your e-mail address."))
            return False
        if not forms.valid_email(email):
            self.f_email.set_error(t("Enter a valid e-mail address, for example name@example.com."))
            return False
        self.f_email.set_error("")
        return True

    def _register(self) -> None:
        if self.busy:
            return
        self._tried_register = True
        email_ok = self._check_email_field()
        pw, repeat = self.rg_password.text(), self.rg_repeat.text()
        problems = forms.password_problems(pw, self.rg_email.text())
        self.f_password.set_error("" if not problems else t("The password does not meet every rule yet."))
        if not repeat:
            self.f_repeat.set_error(t("Repeat the password."))
        elif repeat != pw:
            self.f_repeat.set_error(t("The passwords do not match."))
        else:
            self.f_repeat.set_error("")
        bad = [f for f in (self.f_email, self.f_password, self.f_repeat) if not f.error.isHidden()]
        if bad or not email_ok:
            first = bad[0] if bad else self.f_email
            first.edit.setFocus()
            self.scroll.ensureWidgetVisible(first)
            return
        name, email = self.rg_name.text(), self.rg_email.text().strip()
        lang, remember = self.lang.currentData(), self.rg_remember.isChecked()
        self._say("")
        self._run(lambda: self.m.create_account(email, pw, name, lang, remember), self._registered,
                  (self.btn_register, self.rg_spinner, t("Creating your account…")),
                  lambda exc: self._register_failed(exc, email))

    def _register_failed(self, exc: Exception, email: str) -> None:
        fields = {"email_taken": self.f_email, "invalid_email": self.f_email, "weak_password": self.f_password}
        if isinstance(exc, ApiError) and exc.code in fields:
            fields[exc.code].set_error(error_text(exc))
            fields[exc.code].edit.setFocus()
        created = (self.m.account.get("email") or "").strip().lower() == email.strip().lower()
        if created and not isinstance(exc, DeviceLimitReached):
            # the account exists and a later step (this PC's slot, the license check) failed: go on from there
            self._clear_register()
            self.refresh(self.m.view())
            self._say(error_text(exc), error=True)
            return
        self._failed(exc)

    def _clear_register(self) -> None:
        for w in (self.rg_password, self.rg_repeat):
            w.clear()
            w.conceal()
        self._tried_register = False

    def _registered(self, view: LicenseView) -> None:
        self._clear_register()
        self._skip_verify = False
        if view.unlocked:
            self.unlocked.emit(view)
            return
        if view.email_verified is False:
            self.show_verify()
        else:
            self.refresh(view)
            self._say(t("Account created."))

    # -- verify the e-mail address -----------------------------------------------------------------------------------
    def show_verify(self) -> None:
        if self.busy:
            return
        self.show_page("verify")
        record = self.m.verification
        email = record.get("email") or self.m.account.get("email") or ""
        masked = f"<b>{html.escape(forms.mask_email(email))}</b>"
        delivery = record.get("delivery")
        if delivery == "sent":
            text = t("We've sent an email verification link to {email}. Please verify your email before continuing.",
                     email=masked)
            self._say("")
        elif delivery == "failed":
            text = t("Please verify {email} before continuing.", email=masked)
            self._say(t("Your account was created, but the verification e-mail could not be sent. Send it again in "
                        "a moment."), kind="warn")
        elif delivery == "unavailable":
            text = t("Please verify {email} before continuing.", email=masked)
            self._say(t("AKENO cannot send e-mails at the moment, so no verification link was sent yet. Try again "
                        "later."), kind="warn")
        elif delivery == "unknown":
            text = t("A verification link was requested for {email}. Please verify your email before continuing.",
                     email=masked)
        else:
            text = t("Your e-mail address {email} is not verified yet. Open the link in the e-mail we sent you, or "
                     "send a new one.", email=masked)
        self.vf_text.setText(text)
        if record.get("change"):
            self.heading.setText(t("Confirm your new address"))
        self.btn_resend.start_from(record.get("sent_at") if delivery == "sent" else None)
        self.vf_skip.setVisible(not self.m.verification_required())
        self.vf_change.setVisible(False)

    def _poll_verified(self) -> None:
        if self.busy or self.current() != "verify" or not self.isVisible():
            return
        workers.run(self.m.refresh_account, self._polled, lambda e: None)

    def _polled(self, account: dict) -> None:
        if shiboken6.isValid(self) and not self.busy and self.current() == "verify" and account.get("email_verified"):
            self._verified()

    def _check_verified(self) -> None:
        def done(account: dict) -> None:
            if account.get("email_verified"):
                self._verified()
            else:
                self._say(t("Your e-mail address isn't verified yet. Open the link in the e-mail, then try again."),
                          kind="info")
        self._run(self.m.refresh_account, done, (self.btn_verified, self.vf_spinner, t("Checking…")))

    def _verified(self) -> None:
        self.poll.stop()
        self._say(t("Your e-mail address is verified."), kind="success")
        QTimer.singleShot(1200, lambda: shiboken6.isValid(self) and not self.busy and self.refresh(self.m.view()))

    def _resend(self) -> None:
        def done(out: dict) -> None:
            if out.get("status") == "already_verified":
                self._verified()
                return
            self.btn_resend.start()
            self.show_verify()
            self._say(t("We've sent a new verification link to {email}.",
                        email=forms.mask_email(out.get("email") or self.m.verification.get("email", ""))))

        def failed(exc: Exception) -> None:
            wait = retry_after(exc)
            if wait:
                self.btn_resend.start(wait)
            self._failed(exc)
        self._run(self.m.resend_verification, done, (self.btn_resend, self.vf_spinner, t("Sending…")), failed)

    def _toggle_change(self) -> None:
        show = self.vf_change.isHidden()
        self.vf_change.setVisible(show)
        if show:
            self.f_new_email.edit.clear()
            self.f_change_pw.edit.clear()
            self.f_new_email.edit.setFocus()

    def _change_email(self) -> None:
        if self.busy:
            return
        email, pw = self.f_new_email.edit.text().strip(), self.f_change_pw.edit.text()
        ok = True
        if not forms.valid_email(email):
            self.f_new_email.set_error(t("Enter a valid e-mail address, for example name@example.com."))
            ok = False
        else:
            self.f_new_email.set_error("")
        if not pw:
            self.f_change_pw.set_error(t("Enter your current password."))
            ok = False
        else:
            self.f_change_pw.set_error("")
        if not ok:
            return

        def done(out: dict) -> None:
            self.f_change_pw.edit.clear()
            self.show_verify()
            self.btn_resend.start()
            self._say(t("We've sent a verification link to {email}.",
                        email=forms.mask_email(out.get("email") or email)))

        def failed(exc: Exception) -> None:
            if isinstance(exc, ApiError) and exc.code == "wrong_password":
                self.f_change_pw.set_error(error_text(exc))
            elif isinstance(exc, ApiError) and exc.code in ("email_taken", "invalid_email"):
                self.f_new_email.set_error(error_text(exc))
            self._failed(exc)
        self._run(lambda: self.m.change_email(pw, email), done, (self.btn_change, self.ch_spinner, t("Sending…")),
                  failed)

    def _skip(self) -> None:
        self._skip_verify = True
        self.refresh(self.m.view())

    # -- activation --------------------------------------------------------------------------------------------------
    def _redeem(self) -> None:
        code = self.code.code()
        if not code:
            self.f_code.set_error(t("Enter your activation code."))
            return
        self.f_code.set_error("")
        self._run(lambda: self.m.redeem(code), self._redeemed, (self.btn_redeem, self.rd_spinner, t("Activating…")))

    def _redeemed(self, view: LicenseView) -> None:
        self.code.clear()
        self._say(t("Code redeemed."))
        self.refresh(view)

    def _check(self) -> None:
        self._run(self.m.validate, None, (self.btn_check, None, t("Checking…")))

    # -- password reset ----------------------------------------------------------------------------------------------
    def _to_forgot(self, email: str) -> None:
        self.show_page("forgot")
        self.fp_email.setText(email.strip())
        self.f_reset.set_error("")

    def _forgot(self, again: bool = False) -> None:
        if self.busy:
            return
        email = self._reset_email if again else self.fp_email.text().strip()
        if not email:
            self.f_reset.set_error(t("Enter your e-mail address."))
            return
        if not forms.valid_email(email):
            self.f_reset.set_error(t("Enter a valid e-mail address, for example name@example.com."))
            return
        self.f_reset.set_error("")

        def done(_):
            self._reset_email = email
            self.show_page("forgot_sent")
            self.fs_text.setText(t("If an account exists for {email}, you will receive password reset instructions.",
                                   email=f"<b>{html.escape(forms.mask_email(email))}</b>"))
            self.btn_again.start()
            if again:
                self._say(t("We asked for a new link. Only the newest link works."), kind="info")

        def failed(exc: Exception) -> None:
            wait = retry_after(exc)
            if wait and again:
                self.btn_again.start(wait)
            self._failed(exc)
        if again:
            self._run(lambda: self.m.request_password_reset(email), done,
                      (self.btn_again, self.fs_spinner, t("Sending…")), failed)
        else:
            self._run(lambda: self.m.request_password_reset(email), done,
                      (self.btn_forgot, self.fp_spinner, t("Sending…")), failed)

    def _back_to_sign_in(self) -> None:
        self.show_page("signin")
        if self._reset_email:
            self.si_email.setText(self._reset_email)
            self.si_password.clear()
            self.si_password.setFocus()

    # -- account -----------------------------------------------------------------------------------------------------
    def _sign_out(self) -> None:
        def done(view):
            self._skip_verify = False
            self.signin.email.setText(view.email or "")
            self.refresh(view)
        self._run(self.m.sign_out, done)

    def _open_site(self) -> None:
        page = self.m.config.page("/account")
        if page:
            webbrowser.open(page)

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
            holder = QFrame()
            holder.setObjectName("SubCard")
            holder.setLayout(row)
            row.setContentsMargins(12, 8, 12, 8)
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
            self.f_server.set_error(error_text(exc))
            return
        self.f_server.set_error("")
        self._say(t("Saved."))
        self._probed = False
        self.refresh(self.m.view())

    def _restore(self) -> None:
        if self._recovery is None:
            return
        from akeno.ui.recovery_dialog import RecoveryDialog
        RecoveryDialog(self._recovery, self).exec()

    def _language(self) -> None:
        self.language_changed.emit(self.lang.currentData())


ERR_SHORT = "Choose a password with at least 10 characters that is not your e-mail address."
