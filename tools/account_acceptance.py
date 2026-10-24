"""Acceptance check of the account functions against a running AKENO server (production after an integration).

    python tools/account_acceptance.py check [--api https://api.akeno.online] [--website https://akeno.online]
    python tools/account_acceptance.py flow --email test-mailbox@akeno.online --confirm-real-account

``check`` only reads (and probes every endpoint the app uses without credentials, see ``ENDPOINTS``):
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
    probe_endpoints(api, report)
    return info


# The app's endpoints, probed without credentials and with an empty body. A request like that never reaches the
# endpoint's code: the server answers 401 (sign-in required) or 422 (fields missing) when the route exists, and 404 or
# 405 when it does not. No account, session, e-mail or license is created or changed.
ENDPOINTS = [
    ("POST", "/auth/register", "registration", True), ("POST", "/auth/login", "sign-in", True),
    ("POST", "/auth/refresh", "session renewal", True), ("POST", "/auth/logout", "sign-out", True),
    ("GET", "/auth/me", "account information", True), ("PATCH", "/auth/me", "display name", True),
    ("POST", "/auth/verify-email", "e-mail confirmation", True),
    ("POST", "/auth/verify-email/resend", "resend confirmation", True),
    ("POST", "/auth/password-reset/request", "forgot password", True),
    ("POST", "/auth/password-reset/confirm", "password reset", True),
    ("POST", "/auth/change-password", "change password", True),
    ("POST", "/auth/change-email", "change e-mail (PR #14)", False),
    ("POST", "/auth/sessions/revoke-others", "sign out other devices (PR #14)", False),
    ("GET", "/licenses/current", "license status", True), ("POST", "/licenses/validate", "license check", True),
    ("POST", "/licenses/redeem", "redeem code", True), ("POST", "/licenses/offline-certificate", "offline license", True),
    ("GET", "/devices", "devices", True), ("POST", "/devices/register", "device activation", True),
    ("GET", "/system/keys", "license keys", True),
]


def probe(api_url: str, method: str, path: str, timeout: float = 20) -> int:
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirects, urllib.request.HTTPSHandler(context=ctx))
    data = b"{}" if method in ("POST", "PATCH") else None
    req = urllib.request.Request(f"{api_url}/api/v1{path}", data=data, method=method,
                                 headers={"User-Agent": "AKENO-acceptance", "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def probe_endpoints(api: LicenseApi, report: Report) -> dict:
    found = {}
    for method, path, what, required in ENDPOINTS:
        status = probe(api.base, method, path)
        exists = status not in (404, 405) and status < 500
        found[path] = exists
        if exists:
            report.ok(f"endpoint {method} {path} ({what})", f"present, HTTP {status} without credentials")
        elif required:
            report.fail(f"endpoint {method} {path} ({what})", f"missing, HTTP {status}")
        else:
            report.info(f"endpoint {method} {path} ({what})", f"not deployed yet, HTTP {status}: the app says "
                                                              "'not offered yet'")
    return found


def check_license_keys(api: LicenseApi, licensing_file: Path, report: Report) -> None:
    """Every key the server signs with must be trusted by the build: embedded, or endorsed by an embedded key."""
    import json
    from akeno.licensing import LicenseManager
    from akeno.licensing import config as cfgmod
    built = json.loads(licensing_file.read_text(encoding="utf-8"))
    embedded = {k["kid"]: k["public_key"] for k in built.get("keys", []) if k.get("kid")}
    report.info("license keys in the build", ", ".join(sorted(embedded)) or "none")
    doc = api.call("GET", "/system/keys")
    server = [k for k in doc.get("keys", []) if isinstance(k, dict) and k.get("kid")]
    for k in server:
        state = "retired" if k.get("retired_at") or k.get("retired") else "active"
        report.info(f"server publishes key {k['kid']} ({state})", f"public key {k.get('public_key', '?')}")
    if not embedded:
        report.fail("license keys in the build", "none: a build without keys accepts no license "
                    "(set the repository variable AKENO_LICENSE_PUBLIC_KEYS to the server's active keys above)")
        return
    m = LicenseManager(cfgmod.LicensingConfig(api.base, embedded), store=_NullStore())
    trusted = m.trusted_keys()
    for k in server:
        if k["kid"] in trusted and embedded.get(k["kid"], k["public_key"]) == k["public_key"]:
            report.ok(f"server key {k['kid']}", "embedded in the build")
        elif k.get("endorsement") and m._endorsed(k, trusted):
            report.ok(f"server key {k['kid']}", "endorsed by an embedded key (rotation)")
        elif k.get("retired_at") or k.get("retired"):
            report.info(f"server key {k['kid']}", "retired and not trusted by this build")
        else:
            report.fail(f"server key {k['kid']}", "NOT trusted by this build: licenses signed with it are refused")
    for kid in sorted(set(embedded) - {k["kid"] for k in server}):
        report.info(f"build key {kid}", "not published by the server (an old key, or another server)")


class _NullStore:
    def load(self):
        return {}

    def save(self, state):
        pass


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
    ck = sub.add_parser("check", help="read-only checks")
    ck.add_argument("--license-keys", type=Path, help="the build's licensing.json: compare with the server's keys")
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
    if args.cmd == "check" and info and args.license_keys:
        check_license_keys(LicenseApi(args.api.rstrip("/")), args.license_keys, report)
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
