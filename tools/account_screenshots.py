"""Render the welcome screen and My Account for a visual check, without a server.

    QT_QPA_PLATFORM=offscreen python tools/account_screenshots.py OUT_DIR [--lang en|de] [--gate 1180x780]
                                                                  [--window 1280x800]

A scripted API answers like the AKENO server (the license answers are really signed, by a throwaway key that this
script trusts), so every state can be shown: sign-in errors, registration checks, "Check your inbox" after an
e-mail was sent or could not be sent, the password reset confirmation, and the account page with an ended session.
Nothing is sent anywhere; the images show the interface, not a real account.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CLAIMS = {"iss": "akeno-licensing", "aud": "akeno-desktop", "ent": "akeno-suite"}
EMAIL = "player@example.com"


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public, self.kid = b64e(raw), hashlib.sha256(raw).hexdigest()[:16]

    def sign(self, typ: str, payload: dict) -> str:
        h = b64e(json.dumps({"alg": "EdDSA", "kid": self.kid, "typ": typ}, separators=(",", ":")).encode())
        b = b64e(json.dumps(payload, separators=(",", ":")).encode())
        return f"{h}.{b}.{b64e(self.key.sign(f'{h}.{b}'.encode()))}"


class ScriptedApi:
    answers: dict = {}

    def __init__(self, url):
        pass

    def call(self, method, path, body=None, token=None):
        answer = ScriptedApi.answers.get(path, {})
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--lang", default="en", choices=("en", "de"))
    ap.add_argument("--gate", default="1180x780")
    ap.add_argument("--window", default="1280x800")
    args = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    home = Path(tempfile.mkdtemp(prefix="akeno-shots-"))
    os.environ["AKENO_HOME"] = str(home)
    os.environ["AKENO_DEVICE_ID"] = "screenshot-pc"
    args.out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from akeno import i18n
    from akeno.licensing import LicenseManager, device
    from akeno.licensing import config as cfgmod
    from akeno.licensing.api import ApiError, ServerUnavailable
    from akeno.licensing.storage import SecureStore
    from akeno.ui import interaction, theme, workers
    i18n.set_language(args.lang)
    theme.apply(app)
    interaction.install(app)
    signer = Signer()
    now = time.time()

    def validate(body):
        nonce, lexp = body["nonce"], now + 26 * 86400
        common = dict(CLAIMS, dev="dev-1", dfp=device.fingerprint(), nonce=nonce, iat=int(now), act=int(now - 4 * 86400))
        lease = signer.sign("akeno-lease", dict(common, lt="monthly", nbf=int(now), exp=int(now + 72 * 3600),
                                                lexp=int(lexp)))
        st = signer.sign("akeno-validation", dict(common, sub="u1", res="ok", crev=False, st="active", lt="monthly",
                                                  lexp=int(lexp), exp=int(now + 600)))
        return {"result": "ok", "validation": st, "lease": lease, "offline_certificate": None,
                "license": {"status": "active", "license_type": "monthly", "device_limit": 3},
                "validation_interval_seconds": 3600}

    def no_license(body):
        nonce = body["nonce"]
        st = signer.sign("akeno-validation", dict(CLAIMS, sub="u1", dev="dev-1", dfp=device.fingerprint(),
                                                  nonce=nonce, res="no_license", crev=False, st="none", lt=None,
                                                  lexp=None, act=None, iat=int(now), exp=int(now + 600)))
        return {"result": "no_license", "validation": st, "lease": None, "offline_certificate": None,
                "license": {"status": "none"}, "validation_interval_seconds": 3600}

    user = {"id": "u1", "email": EMAIL, "display_name": "Akeno", "email_verified": False, "two_factor": False,
            "created_at": "2026-10-01T12:00:00Z"}
    tokens = {"access_token": "a", "refresh_token": "r", "expires_in": 900, "verification_email": "sent",
              "user": user}
    ScriptedApi.answers = {
        "/system/version": {"maintenance": False, "registration_enabled": True, "require_email_verification": False},
        "/auth/register": tokens, "/auth/login": tokens, "/auth/me": user,
        "/devices/register": {"id": "dev-1", "label": "GAMING-PC"}, "/licenses/validate": no_license,
        "/auth/password-reset/request": {"status": "sent_if_account_exists"},
        "/devices": [{"id": "dev-1", "label": "GAMING-PC", "os": "Windows 11 (build 26100)",
                      "last_seen_at": "2026-10-24T10:00:00Z"},
                     {"id": "dev-2", "label": "Laptop", "os": "Windows 10 (build 19045)",
                      "last_seen_at": "2026-10-12T10:00:00Z"}]}

    def manager():
        cfg = cfgmod.LicensingConfig("https://api.akeno.online", {signer.kid: signer.public})
        return LicenseManager(cfg, SecureStore(home / "license"), ScriptedApi)

    def settle(widget, name):
        workers.drain(3000)
        for _ in range(6):
            app.processEvents()
            time.sleep(0.03)
        widget.grab().save(str(args.out / f"{args.lang}-{name}.png"))

    from akeno.ui.license_gate import LicenseGate
    m = manager()
    gate = LicenseGate(m)
    gate.resize(*(int(x) for x in args.gate.split("x")))
    gate.show()
    settle(gate, "01-signin")
    gate.si_email.setText(EMAIL)
    gate.si_password.setText("wrong password")
    ScriptedApi.answers["/auth/login"] = ApiError(401, "invalid_credentials", "x")
    gate._sign_in()
    settle(gate, "02-signin-wrong-password")
    ScriptedApi.answers["/auth/login"] = ServerUnavailable("x", kind="offline")
    gate._sign_in()
    settle(gate, "03-signin-offline")
    gate.show_page("register")
    gate.rg_name.setText("Akeno")
    gate.rg_email.setText("player@example")
    gate.rg_password.setText("short")
    gate.rg_repeat.setText("shorter")
    gate._register()
    settle(gate, "04-register-checks")
    gate.rg_email.setText(EMAIL)
    gate.rg_password.setText("Correct-Horse-42!")
    gate.rg_repeat.setText("Correct-Horse-42!")
    gate._check_email_field()
    settle(gate, "05-register-ready")
    gate._register()
    settle(gate, "06-check-your-inbox")
    m.state["verification"]["delivery"] = "failed"
    gate.btn_resend.cooldown.until = 0
    gate.show_verify()
    settle(gate, "07-verification-email-not-sent")
    m.sign_out()
    gate.refresh(m.view())
    gate._to_forgot(EMAIL)
    settle(gate, "08-forgot-password")
    gate._forgot()
    settle(gate, "09-reset-requested")
    ScriptedApi.answers["/auth/password-reset/request"] = ServerUnavailable("x", kind="server",
                                                                             code="email_unavailable")
    gate._to_forgot(EMAIL)
    gate._forgot()
    settle(gate, "10-reset-email-unavailable")
    gate.close()

    from akeno.core.db import Database
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    ScriptedApi.answers["/auth/login"] = tokens
    ScriptedApi.answers["/licenses/validate"] = validate
    m = manager()
    m.sign_in(EMAIL, "pw")
    svc = AppServices(db=Database(home / "db.sqlite3"))
    win = MainWindow(Controller(svc), m)
    win.resize(*(int(x) for x in args.window.split("x")))
    win.show()
    win.go("account")
    settle(win, "11-my-account")
    page = win.pages["account"]
    page._toggle(page.pw_box)
    page.f_new.edit.setText("weak")
    win.hosts["account"].scroll.verticalScrollBar().setValue(250)
    settle(win, "12-change-password")
    page._toggle(page.pw_box)
    m.state.update(needs_sign_in=True, session_end="invalid_refresh_token")
    page.show_view(m.view())
    workers.drain(3000)
    app.processEvents()
    win.hosts["account"].scroll.verticalScrollBar().setValue(0)
    settle(win, "13-session-expired")
    workers.drain(3000)
    svc.db.close()
    print(f"{args.out}: {len(list(args.out.glob(args.lang + '-*.png')))} images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
