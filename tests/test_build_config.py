"""The build's public configuration: license and release keys, official addresses, no private key material."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
LICENSE_KEY = ("f382ecda000888b2", "XlXmRZo3Tf9PDNxjNd8AQUm5Oquz8W9DPZE8PwOVmVE")
RELEASE_KEY = ("d6139235b220a4aa", "bpe9acmephsoZSYzy12R2XFy_lJZ_69MiV31ekehcdo")


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = _load("configure_licensing", "configure-licensing.py")
checker = _load("check_build_config", "check_build_config.py")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_committed_keys_are_the_production_keys_and_separate():
    lic = json.loads((ROOT / "akeno" / "data" / "licensing.json").read_text())
    rel = json.loads((ROOT / "akeno" / "data" / "release-keys.json").read_text())
    assert lic["server_url"] == "https://api.akeno.online" and lic["website_url"] == "https://akeno.online"
    assert [(k["kid"], k["public_key"]) for k in lic["keys"]] == [LICENSE_KEY]
    assert [(k["kid"], k["public_key"]) for k in rel["keys"]] == [RELEASE_KEY]
    for kid, public in (LICENSE_KEY, RELEASE_KEY):
        assert checker.kid_of(public) == kid


def test_configure_licensing_takes_the_repository_variable_form(tmp_path, capsys):
    out = tmp_path / "licensing.json"
    pair = f"{LICENSE_KEY[0]}:{LICENSE_KEY[1]}"
    assert configure.main(["--server-url", "https://api.akeno.online", "--keys", pair, "--output", str(out),
                           "--require"]) == 0
    assert json.loads(out.read_text())["keys"] == [{"kid": LICENSE_KEY[0], "public_key": LICENSE_KEY[1]}]
    # an unset variable keeps the keys already there
    assert configure.main(["--server-url", "https://api.akeno.online", "--keys", "", "--output", str(out)]) == 0
    assert len(json.loads(out.read_text())["keys"]) == 1
    with pytest.raises(SystemExit):
        configure.main(["--keys", f"{LICENSE_KEY[0]}:{RELEASE_KEY[1]}", "--output", str(out)])   # kid mismatch
    with pytest.raises(SystemExit):
        configure.main(["--keys", "no-pair", "--output", str(out)])
    empty = tmp_path / "empty.json"
    with pytest.raises(SystemExit):
        configure.main(["--server-url", "https://api.akeno.online", "--output", str(empty), "--require"])


def _build(tmp_path: Path) -> Path:
    build = tmp_path / "AKENO"
    data = build / "_internal" / "akeno" / "data"
    data.mkdir(parents=True)
    for name in ("licensing.json", "release-keys.json"):
        shutil.copy(ROOT / "akeno" / "data" / name, data / name)
    (build / "AKENO.exe").write_bytes(b"MZ program")
    return build


def test_build_check_passes_for_the_committed_configuration(tmp_path):
    build = _build(tmp_path)
    lines, problems = checker.check(build, "https://api.akeno.online", "https://akeno.online")
    assert problems == []
    assert f"License verification keys: {LICENSE_KEY[0]}" in lines
    assert f"Release verification keys: {RELEASE_KEY[0]}" in lines
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in build.rglob("*"):
            z.write(f, f.relative_to(build).as_posix())
    assert checker.check(archive, "https://api.akeno.online", "https://akeno.online")[1] == []


def test_build_check_refuses_wrong_address_mixed_keys_and_private_material(tmp_path):
    build = _build(tmp_path)
    data = build / "_internal" / "akeno" / "data"
    assert checker.check(build, "https://other.example", "https://akeno.online")[1]
    shutil.copy(data / "licensing.json", data / "release-keys.json")
    assert any("license key and as release key" in p for p in
               checker.check(build, "https://api.akeno.online", "https://akeno.online")[1])
    seed = Ed25519PrivateKey.generate()
    raw = seed.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    public = _b64(seed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    kid = hashlib.sha256(base64.urlsafe_b64decode(public + "=")).hexdigest()[:16]
    (data / "release-keys.json").write_text(json.dumps({"keys": [{"kid": kid, "public_key": public,
                                                                  "private_key": _b64(raw)}]}))
    problems = checker.check(build, "https://api.akeno.online", "https://akeno.online", _b64(raw))[1]
    assert any("exactly kid and public_key" in p for p in problems)
    assert any("private key material" in p for p in problems)
    assert any("contains the release signing key" in p for p in problems)
    (data / "release-keys.json").write_text(json.dumps({"keys": [{"kid": kid, "public_key": public}]}))
    (build / "leak.bin").write_bytes(b"\0" + raw + b"\0")
    problems = checker.check(build, "https://api.akeno.online", "https://akeno.online", _b64(raw))[1]
    assert problems == ["leak.bin contains the release signing key"]
