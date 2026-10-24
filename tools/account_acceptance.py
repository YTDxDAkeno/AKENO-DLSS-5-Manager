"""Acceptance check of the account functions against a running AKENO server (production after an integration).

    python tools/account_acceptance.py check [--api https://api.akeno.online] [--website https://akeno.online]
    python tools/account_acceptance.py flow --email test-mailbox@akeno.online --confirm-real-account

``check`` only reads:
- the API answers over verified HTTPS and is not in maintenance;
- whether this branch's account changes are deployed (``require_email_verification`` in ``/system/version``);
- the website serves the pages e-mail links open (``/verify-email``, ``/reset-password``, ``/forgot-password``).

``flow`` uses a real account on that server, so it runs only with ``--confirm-real-account`` and an address whose
inbox you control. It goes through what the app does:
1. register (or sign in, if the account exists), and the server's statement about the confirmation e-mail;
2. the confirmation link: it must point to the website; you open it in the browser;
3. the server says the address is confirmed;
4. a password reset: the same link check; you choose the new password on the website;
5. signing in with the new password, and the old sign-in having ended.

Passwords are typed (never echoed) or read from an environment variable named with ``--password-env``. No password
or token is printed.

``--mail-dir`` (the server's ``file`` mail sink) and ``--links-via-api`` (open the links through the API instead
of a browser) exist for test servers and this script's own test (``server/tests/test_account_acceptance.py``).
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno.licensing import forms  # noqa: E402
from akeno.licensing.api import ApiError, LicenseApi, ServerUnavailable, _NoRedirects  # noqa: E402
from akeno.licensing.config import OFFICIAL_SERVER, website_for  # noqa: E402


class Report:
    def __init__(self, out=sys.stdout):
        self.rows: list[tuple[str, str, str]] = []
        self.out = out

    def add(self, state: str, what: str, detail: str = "") -> None:
        self.rows.append((state, what, detail))
        print(f"[{state:4}] {what}" + (f": {detail}" if detail else ""), file=self.out, flush=True)

    def ok(self, what, detail=""):
        self.add("OK", what, detail)

    def fail(self, what, detail=""):
        self.add("FAIL", what, detail)

    def info(self, what, detail=""):
        self.add("INFO", what, detail)

    @property
    def failed(self) -> bool:
        return any(state == "FAIL" for state, _, _ in self.rows)


def _get_page(url: str, timeout: float = 15) -> int:
    """Status of a website page over verified HTTPS, redirects not followed."""
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirects, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, headers={"User-Agent": "AKENO-acceptance"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def check(api_url: str, website: str, report: Report) -> dict:
    api = LicenseApi(api_url)
    try:
        api.call("GET", "/system/health")
        report.ok("API reachable over verified HTTPS", api_url)
    except ServerUnavailable as exc:
        report.fail("API reachable", f"{exc.kind}: {exc}")
        return {}
    info = api.call("GET", "/system/version")
    report.info("server", f"{info.get('server_version')} (API {info.get('api_version')})")
    (report.fail if info.get("maintenance") else report.ok)("not in maintenance")
    report.info("registration open", str(info.get("registration_enabled")))
    if "require_email_verification" in info:
        report.ok("account changes deployed", "require_email_verification = "
                                              f"{info['require_email_verification']}")
    else:
        report.info("account changes deployed", "no: the app shows 'a verification link was requested' and has no "
                                                "address change or 'sign out other devices'")
    # /verify-email without a token answers with the website's "invalid or expired link" page (HTTP 400)
    for path, expected in (("/forgot-password", (200,)), ("/reset-password", (200,)), ("/verify-email", (200, 400))):
        status = _get_page(website + path)
        (report.ok if status in expected else report.fail)(f"website serves {path}", f"HTTP {status}")
    return info


# -- the flow -----------------------------------------------------------------------------------------------------
class Links:
    """Where the links from the e-mails come from: the server's mail sink, or the person pasting them."""

    def __init__(self, mail_dir: Path | None):
        self.mail_dir = mail_dir
        self.since = time.time() - 1

    def get(self, kind: str, email: str) -> str:
        if self.mail_dir is None:
            return input(f"Paste the {kind} link from the e-mail to {forms.mask_email(email)}: ").strip()
        deadline = time.time() + 30
        while time.time() < deadline:
            found = []
            for path in self.mail_dir.glob("*.eml"):
                if path.stat().st_mtime < self.since:
                    continue
                msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                m = re.search(rf"\S+/{kind}\?token=[\w\-]+", msg.get_content())
                if m and msg["To"] == email:
                    found.append((path.stat().st_mtime, m.group(0)))
            if found:
                return max(found)[1]
            time.sleep(0.3)
        raise SystemExit(f"no {kind} e-mail to {email} arrived in {self.mail_dir}")


def _check_link(link: str, path: str, website: str, report: Report) -> str:
    """The link must open the website over HTTPS (or the test server), never the API host. Returns the token."""
    parsed, site = urlparse(link), urlparse(website)
    token = (parse_qs(parsed.query).get("token") or [""])[0]
    same_site = (parsed.scheme, parsed.netloc) == (site.scheme, site.netloc)
    (report.ok if same_site and parsed.path == path and token else report.fail)(
        f"{path} link opens the website", f"{parsed.scheme}://{parsed.netloc}{parsed.path} (token not shown)")
    return token


def _password(env_name: str | None, prompt: str) -> str:
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return getpass.getpass(prompt)


def flow(api_url: str, website: str, email: str, report: Report, password_env: str | None = None,
         new_password_env: str | None = None, mail_dir: Path | None = None, links_via_api: bool = False) -> None:
    api = LicenseApi(api_url)
    links = Links(mail_dir)
    password = _password(password_env, f"Password for {forms.mask_email(email)}: ")
    problems = forms.password_problems(password, email)
    if problems:
        raise SystemExit("the password breaks the server's rules: " + ", ".join(problems))
    # 1. register, or sign in when the account exists
    try:
        tokens = api.call("POST", "/auth/register", {"email": email, "password": password,
                                                      "display_name": "AKENO acceptance", "language": "en"})
        delivery = tokens.get("verification_email")
        (report.ok if delivery == "sent" else report.fail)("registration: confirmation e-mail", str(delivery))
    except ApiError as exc:
        if exc.code != "email_taken":
            report.fail("registration", exc.code)
            return
        tokens = api.call("POST", "/auth/login", {"email": email, "password": password})
        report.info("registration", "the account exists: signed in instead")
    session = tokens["refresh_token"]
    access = tokens["access_token"]
    # 2. + 3. confirm the address
    if not tokens["user"].get("email_verified"):
        if tokens.get("verification_email") != "sent":
            api.call("POST", "/auth/verify-email/resend", token=access)
        token = _check_link(links.get("verify-email", email), "/verify-email", website, report)
        if links_via_api:
            api.call("POST", "/auth/verify-email", {"token": token})
        else:
            input("Open that link in your browser, then press Enter here.")
    me = api.call("GET", "/auth/me", token=access)
    (report.ok if me.get("email_verified") else report.fail)("the server confirms the address")
    # 4. password reset
    links.since = time.time() - 1
    try:
        api.call("POST", "/auth/password-reset/request", {"email": email})
        report.ok("reset requested", "202")
    except ServerUnavailable as exc:
        report.fail("reset requested", exc.code or exc.kind)
        return
    token = _check_link(links.get("reset-password", email), "/reset-password", website, report)
    if links_via_api:
        new_password = os.environ.get(new_password_env or "") or "Acc-" + secrets.token_urlsafe(12)
        api.call("POST", "/auth/password-reset/confirm", {"token": token, "new_password": new_password})
    else:
        print("Open that link in your browser and choose a new password there.")
        new_password = _password(new_password_env, "The new password you chose: ")
    # 5. the new password works, the old sign-in ended
    tokens = api.call("POST", "/auth/login", {"email": email, "password": new_password})
    report.ok("sign-in with the new password")
    try:
        api.call("POST", "/auth/refresh", {"refresh_token": session})
        report.fail("the previous sign-in ended", "it still works")
    except ApiError as exc:
        (report.ok if exc.status == 401 else report.fail)("the previous sign-in ended", exc.code)
    api.call("POST", "/auth/logout", {}, token=tokens["access_token"])
    report.ok("signed out")
    if new_password_env is None and links_via_api:
        report.info("the account's password is now a random one; reset it again to use the account")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default=OFFICIAL_SERVER)
    ap.add_argument("--website", default="", help="default: derived from --api (api.<domain> → https://<domain>)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="read-only checks")
    fl = sub.add_parser("flow", help="register, confirm, reset and sign in with a real test account")
    fl.add_argument("--email", required=True)
    fl.add_argument("--confirm-real-account", action="store_true",
                    help="required: this creates or uses a real account on that server")
    fl.add_argument("--password-env", help="read the password from this environment variable")
    fl.add_argument("--new-password-env", help="read the new password from this environment variable")
    fl.add_argument("--mail-dir", type=Path, help="read links from the server's file mail sink (test servers)")
    fl.add_argument("--links-via-api", action="store_true", help="open links through the API (test servers)")
    args = ap.parse_args(argv)
    website = (args.website or website_for(args.api)).rstrip("/")
    report = Report()
    info = check(args.api.rstrip("/"), website, report)
    if args.cmd == "flow" and info:
        if not args.confirm_real_account:
            print("flow creates or uses a real account: add --confirm-real-account", file=sys.stderr)
            return 2
        try:
            flow(args.api.rstrip("/"), website, args.email.strip(), report, args.password_env, args.new_password_env,
                 args.mail_dir, args.links_via_api)
        except (ApiError, ServerUnavailable) as exc:
            report.fail("request refused", f"{getattr(exc, 'code', '')} {exc}")
    print(f"\n{'FAILED' if report.failed else 'PASSED'}: {sum(r[0] == 'OK' for r in report.rows)} checks passed, "
          f"{sum(r[0] == 'FAIL' for r in report.rows)} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
