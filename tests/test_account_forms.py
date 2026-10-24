"""The account forms' checks: they must agree with the server, so the app never promises what the server refuses."""

from __future__ import annotations

import pytest

from akeno.licensing import forms


@pytest.mark.parametrize("email,ok", [
    ("player@example.test", True), ("  player@example.test ", True), ("a.b+c@sub.example.online", True),
    ("player@example", False), ("player@@example.test", False), ("player example@x.de", False), ("", False),
    ("@example.test", False), ("player@.x", False), ("x" * 65 + "@example.test", False),
])
def test_email_check_matches_the_server(email, ok):
    assert forms.valid_email(email) is ok


def test_email_check_is_the_servers_pattern():
    import re
    from pathlib import Path
    server = Path(__file__).resolve().parents[1] / "server" / "akeno_server" / "auth" / "service.py"
    pattern = re.search(r'EMAIL = re.compile\(r"([^"]+)"\)', server.read_text(encoding="utf-8")).group(1)
    assert pattern == forms.EMAIL.pattern, "the app and the server check e-mail addresses alike"


def test_password_rules_match_the_server_policy():
    assert forms.password_problems("short") == [forms.TOO_SHORT]
    assert forms.password_problems("aaaaaaaaaaaa") == [forms.TOO_SIMPLE]
    assert forms.password_problems("Player@Example.test", "player@example.test") == [forms.IS_EMAIL]
    assert forms.password_problems("x" * 200 + "abcd" * 20) == [forms.TOO_LONG]
    assert forms.password_problems("correct horse battery") == []
    assert set(forms.RULES) == {forms.TOO_SHORT, forms.IS_EMAIL, forms.TOO_SIMPLE}


def test_password_rules_are_the_servers_constants():
    """The server's policy (server/tests/test_client_e2e.py runs the same passwords against the real server)."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "server" / "akeno_server" / "auth" / "passwords.py"
    source = path.read_text(encoding="utf-8")
    assert f"MIN_LENGTH = {forms.MIN_PASSWORD}" in source and f"MAX_LENGTH = {forms.MAX_PASSWORD}" in source
    assert "len(set(password)) < 4" in source


def test_strength_never_praises_a_refused_password():
    assert forms.password_strength("") == 0
    assert forms.password_strength("short") == 0
    assert forms.password_strength("password12345") <= 1                    # a common word
    assert forms.password_strength("aaabbbcccddd") <= 1                     # repeats
    assert forms.password_strength("correct horse battery") >= 2
    assert forms.password_strength("Correct-Horse-42!") == 4
    assert forms.password_strength("playerplayer1!", "player@example.test") <= 2, "the e-mail's name in it"


@pytest.mark.parametrize("email,masked", [
    ("player@example.test", "p••••r@example.test"), ("ab@example.test", "a•••@example.test"),
    ("abc@example.test", "a•••c@example.test"), ("averyveryverylongname@example.test", "a••••••e@example.test"),
    ("broken", "•••"),
])
def test_masked_addresses(email, masked):
    assert forms.mask_email(email) == masked
    assert "player" not in forms.mask_email("player@example.test")


def test_cooldown():
    now = [100.0]
    c = forms.Cooldown(seconds=60, clock=lambda: now[0])
    assert c.ready and c.remaining() == 0
    c.start()
    assert not c.ready and c.remaining() == 60
    now[0] += 59.5
    assert c.remaining() == 1
    c.start(300)                                       # the server asks for longer
    assert c.remaining() == 300
    c.start(10)                                        # never shorter than what already runs
    assert c.remaining() == 300
    now[0] += 300
    assert c.ready


def test_cooldown_continues_after_a_restart():
    c = forms.Cooldown(seconds=60, clock=lambda: 0.0)
    c.start_from(sent_at=1000.0, wall=lambda: 1020.0)
    assert c.remaining() == 40
    fresh = forms.Cooldown(seconds=60, clock=lambda: 0.0)
    fresh.start_from(sent_at=1000.0, wall=lambda: 2000.0)      # long ago
    assert fresh.ready
    future = forms.Cooldown(seconds=60, clock=lambda: 0.0)
    future.start_from(sent_at=5000.0, wall=lambda: 1000.0)     # a clock turned back cannot block it for hours
    assert future.ready


def test_clock_text():
    assert forms.clock_text(42) == "0:42"
    assert forms.clock_text(605) == "10:05"
    assert forms.clock_text(3720) == "1:02:00"
    assert forms.clock_text(0.2) == "0:01"
