"""tools/account_acceptance.py against a live server (API and website in one process, e-mails in the file sink)."""

from __future__ import annotations

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from akeno_server.config import reset_config
from tests.helpers import PASSWORD

REPO = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("account_acceptance", REPO / "tools" / "account_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def site(keys, monkeypatch, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("AKENO_ROLE", "all")
    monkeypatch.setenv("AKENO_PUBLIC_URL", url)
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "file")
    monkeypatch.setenv("AKENO_MAIL_DIR", str(tmp_path / "mail"))
    reset_config()
    from akeno_server.main import create_app
    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield url, tmp_path / "mail"
    srv.should_exit = True
    thread.join(5)


def test_check_and_full_flow(site, monkeypatch, capsys):
    url, mail_dir = site
    tool = _tool()
    monkeypatch.setenv("ACC_PW", PASSWORD)
    monkeypatch.setenv("ACC_NEW_PW", "a brand new pass")
    code = tool.main(["--api", url, "--website", url, "flow", "--email", "tester@example.test",
                      "--confirm-real-account", "--password-env", "ACC_PW", "--new-password-env", "ACC_NEW_PW",
                      "--mail-dir", str(mail_dir), "--links-via-api"])
    out = capsys.readouterr().out
    assert code == 0, out
    for line in ("API reachable", "account changes deployed", "website serves /reset-password",
                 "registration: confirmation e-mail: sent", "/verify-email link opens the website",
                 "the server confirms the address", "/reset-password link opens the website",
                 "sign-in with the new password", "the previous sign-in ended"):
        assert line in out, line
    assert PASSWORD not in out and "a brand new pass" not in out and "token=" not in out


def test_flow_needs_explicit_consent(site, capsys):
    url, _ = site
    assert _tool().main(["--api", url, "--website", url, "flow", "--email", "tester@example.test"]) == 2


def test_links_to_the_api_host_are_caught(site, monkeypatch, capsys):
    """The production mistake the hand-over warns about: AKENO_PUBLIC_URL unset, links built from the API host."""
    url, mail_dir = site
    monkeypatch.setenv("AKENO_PUBLIC_URL", "http://localhost:9")       # anything but the website
    reset_config()
    monkeypatch.setenv("ACC_PW", PASSWORD)
    monkeypatch.setenv("ACC_NEW_PW", "a brand new pass")
    code = _tool().main(["--api", url, "--website", url, "flow", "--email", "tester@example.test",
                         "--confirm-real-account", "--password-env", "ACC_PW", "--new-password-env", "ACC_NEW_PW",
                         "--mail-dir", str(mail_dir), "--links-via-api"])
    out = capsys.readouterr().out
    assert code == 1 and "[FAIL] /verify-email link opens the website" in out


def test_check_reports_a_server_without_email(site, monkeypatch, capsys):
    url, mail_dir = site
    monkeypatch.setenv("AKENO_ENVIRONMENT", "production")
    reset_config()
    monkeypatch.setenv("ACC_PW", PASSWORD)
    code = _tool().main(["--api", url, "--website", url, "flow", "--email", "tester@example.test",
                         "--confirm-real-account", "--password-env", "ACC_PW", "--mail-dir", str(mail_dir),
                         "--links-via-api"])
    out = capsys.readouterr().out
    assert code == 1 and "[FAIL] registration: confirmation e-mail: unavailable" in out


def test_check_probes_every_endpoint_and_compares_license_keys(site, tmp_path, capsys):
    import json
    from akeno_server import db as dbmod
    from akeno_server.licensing import signing
    url, _ = site
    with dbmod.session() as db:
        server_keys = [{"kid": k.kid, "public_key": k.public_key} for k in signing.public_keys(db)]
    good = tmp_path / "licensing.json"
    good.write_text(json.dumps({"server_url": url, "keys": server_keys}))
    assert _tool().main(["--api", url, "--website", url, "check", "--license-keys", str(good)]) == 0
    out = capsys.readouterr().out
    assert "[OK  ] endpoint POST /auth/change-email" in out and "[OK  ] endpoint POST /devices/register" in out
    assert f"[OK  ] server key {server_keys[0]['kid']}: embedded in the build" in out
    assert "[FAIL]" not in out
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from akeno_server.licensing.signing import kid_for, raw_public
    from akeno_server.auth.tokens import b64e
    raw = raw_public(Ed25519PrivateKey.generate().public_key())
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"keys": [{"kid": kid_for(raw), "public_key": b64e(raw)}]}))
    assert _tool().main(["--api", url, "--website", url, "check", "--license-keys", str(wrong)]) == 1
    assert "NOT trusted by this build" in capsys.readouterr().out
