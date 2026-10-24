"""Updates from the AKENO Release Manager: ``GET /api/v1/releases/stable`` on the production API.

The format is the one the production server publishes in its OpenAPI document (schema_version 1: ``signed_payload``
plus an Ed25519 ``signature``). A release counts only with a valid signature by a built-in release key, only the
signed values are used, and it is never installed by the app (one installer file, no portable package, and
``unattended_install_allowed`` must be false): the Updates page shows it and offers the release page.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_manifest as tool  # noqa: E402

from akeno import distribution  # noqa: E402
from akeno.licensing import crypto  # noqa: E402
from akeno.updater import manifest  # noqa: E402
from akeno.updater.service import UpdateError, UpdateService  # noqa: E402

API = "https://api.akeno.online/api/v1"
RM = distribution.from_dict({"update_source": "akeno-api", "release_api": API,
                             "release_page": "https://akeno.online/downloads/latest", "download_hosts": ["akeno.online"]})
SHA = "ab" * 32


@pytest.fixture
def signing():
    key = Ed25519PrivateKey.generate()
    public = tool.public_of(key)
    return key, {tool.kid_of(public): crypto.public_key(public)}


def fields(version: str = "3.1.0", **kw) -> dict:
    out = {"schema_version": 1, "product": "akeno-gaming-suite", "channel": "stable", "version": version,
           "release_notes": "Faster scans.", "release_date": "2026-11-02", "published_at": "2026-11-02T10:00:00Z",
           "platform": "windows-x64",
           "download_url": f"https://akeno.online/downloads/AKENO-Gaming-Suite-{version}-Setup-x64.exe",
           "size_bytes": 123456789, "sha256": SHA,
           "authenticode": {"verified": True, "publisher_certificate_sha256": "cd" * 32},
           "minimum_supported_version": None, "unattended_install_allowed": False}
    out.update(kw)
    return out


def envelope(key, signed: dict, outer: dict | None = None, encode=lambda s: s, sig_format="b64url") -> dict:
    payload = encode(json.dumps(signed, sort_keys=True, separators=(",", ":")))
    raw = key.sign(payload.encode("utf-8"))
    value = raw.hex() if sig_format == "hex" else (
        base64.b64encode(raw).decode() if sig_format == "b64" else base64.urlsafe_b64encode(raw).rstrip(b"=").decode())
    public = tool.public_of(key)
    doc = dict(signed if outer is None else outer)
    doc.update(signed_payload=payload, signature={"algorithm": "Ed25519", "key_id": tool.kid_of(public),
                                                  "value": value})
    return doc


def test_release_manager_source():
    assert RM.source == "akeno-api" and RM.channel_manifest("stable") == f"{API}/releases/stable"
    assert RM.trusted(f"{API}/releases/stable") and RM.trusted("https://akeno.online/downloads/x.exe")
    assert not RM.trusted("https://downloads.example.com/x.exe")
    assert RM.releases == "https://akeno.online/downloads/latest" and "Release Manager" in RM.label
    for bad in ("http://api.akeno.online/api/v1", "https://api.akeno.online.evil.example/api/v1", ""):
        assert distribution.from_dict({"update_source": "akeno-api", "release_api": bad}).source == "github"


def test_committed_build_uses_the_release_manager():
    data = json.loads((ROOT / "akeno" / "data" / "distribution.json").read_text())
    dist = distribution.from_dict(data)
    assert dist.source == "akeno-api" and dist.channel_manifest("stable") == f"{API}/releases/stable"


@pytest.mark.parametrize("encode,sig_format", [
    (lambda s: s, "b64url"), (lambda s: s, "b64"), (lambda s: s, "hex"),
    (lambda s: base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode(), "b64url")])
def test_signed_release_is_read_from_the_signed_payload(signing, encode, sig_format):
    key, keys = signing
    release = manifest.parse_api(envelope(key, fields(), encode=encode, sig_format=sig_format), keys, RM)
    assert (release.release, release.channel, release.tag) == ("3.1.0", "stable", "v3.1.0")
    assert release.publisher_sha256 == "cd" * 32 and release.notes == "Faster scans."
    (f,) = release.files
    assert (f.kind, f.name, f.sha256, f.size) == ("installer", "AKENO-Gaming-Suite-3.1.0-Setup-x64.exe", SHA,
                                                  123456789)
    assert release.file("portable") is None


def test_refusals(signing):
    key, keys = signing
    good = fields()
    with pytest.raises(manifest.ManifestError, match="no release verification key"):
        manifest.parse_api(envelope(key, good), {}, RM)
    with pytest.raises(manifest.ManifestError, match="does not trust"):
        manifest.parse_api(envelope(Ed25519PrivateKey.generate(), good), keys, RM)
    forged = envelope(key, good)
    forged["signed_payload"] = forged["signed_payload"].replace(SHA, "ef" * 32)
    forged["sha256"] = "ef" * 32
    with pytest.raises(manifest.ManifestError, match="signature does not match"):
        manifest.parse_api(forged, keys, RM)
    # an outer field that differs from the signed one (a swapped download behind a valid signature)
    with pytest.raises(manifest.ManifestError, match="differs from the signed value"):
        manifest.parse_api(envelope(key, good, outer=fields(sha256="ef" * 32)), keys, RM)
    with pytest.raises(manifest.ManifestError, match="has no sha256"):
        manifest.parse_api(envelope(key, {k: v for k, v in good.items() if k != "sha256"}, outer=good), keys, RM)
    with pytest.raises(manifest.ManifestError, match="without confirmation"):
        manifest.parse_api(envelope(key, fields(unattended_install_allowed=True)), keys, RM)
    with pytest.raises(manifest.ManifestError, match="download of the release is not valid"):
        manifest.parse_api(envelope(key, fields(download_url="https://downloads.example.com/a.exe")), keys, RM)
    with pytest.raises(manifest.ManifestError, match="not for this app"):
        manifest.parse_api(envelope(key, fields(product="other")), keys, RM)
    with pytest.raises(manifest.ManifestError, match="another channel"):
        manifest.parse_api(envelope(key, fields(channel="beta")), keys, RM)
    with pytest.raises(manifest.ManifestError, match="not a version"):
        manifest.parse_api(envelope(key, fields(version="3.1.0-beta.1")), keys, RM)
    with pytest.raises(manifest.ManifestError, match="unknown format"):
        manifest.parse_api({"format": "akeno-release-channel"}, keys, RM)
    with pytest.raises(manifest.ManifestError, match="not signed"):
        manifest.parse_api(dict(good), keys, RM)


def test_license_keys_never_verify_releases(signing):
    key, _ = signing
    license_keys = {k["kid"]: crypto.public_key(k["public_key"]) for k in
                    json.loads((ROOT / "akeno" / "data" / "licensing.json").read_text())["keys"]}
    with pytest.raises(manifest.ManifestError):
        manifest.parse_api(envelope(key, fields()), license_keys, RM)


def test_service_offers_newer_never_older_and_never_installs(tmp_path, signing):
    key, keys = signing
    docs = {"doc": envelope(key, fields("3.1.0"))}
    asked = []
    svc = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: asked.append(url) or docs["doc"],
                        version="3.0.0", folder=tmp_path)
    release = svc.check()
    assert asked == [f"{API}/releases/stable"] and release.release == "3.1.0"
    ok, why = svc.can_install(release)
    assert not ok and "release page" in why
    docs["doc"] = envelope(key, fields("3.0.5"))
    with pytest.raises(manifest.ManifestError, match="older than 3.1.0"):
        svc.check()
    docs["doc"] = envelope(key, fields("3.0.0"))
    svc2 = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: docs["doc"], version="3.0.0",
                         folder=tmp_path / "fresh")
    assert svc2.check() is None


def test_service_has_no_beta_on_the_release_manager(tmp_path, signing):
    _, keys = signing

    class Settings(dict):
        def get(self, key, default=None):
            return super().get(key, default)
    svc = UpdateService(Settings(update_channel="beta"), dist=RM, keys=keys, fetch=lambda url: {}, version="3.0.0",
                        folder=tmp_path)
    with pytest.raises(UpdateError, match="only the stable channel"):
        svc.check()


def test_release_tool_writes_the_release_manager_source(tmp_path):
    out = tmp_path / "distribution.json"
    with pytest.raises(SystemExit, match="evil.example"):
        tool.main(["distribution", "--source", "akeno-api", "--release-api", API, "--download-hosts",
                   "akeno.online,evil.example", "--output", str(out)])
    with pytest.raises(SystemExit):
        tool.main(["distribution", "--source", "akeno-api", "--release-api", "https://api.example.com/api/v1",
                   "--output", str(out)])
    assert tool.main(["distribution", "--source", "akeno-api", "--release-api", API, "--release-page",
                      "https://akeno.online/downloads/latest", "--download-hosts", "akeno.online",
                      "--output", str(out)]) == 0
    assert distribution.from_dict(json.loads(out.read_text())) == RM


@pytest.fixture
def page(akeno_home, qtbot, monkeypatch, tmp_path, signing):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: True)
    win = MainWindow(Controller(AppServices(db=Database(akeno_home / "s.sqlite3"))))
    qtbot.addWidget(win)
    win.go("updates")
    p = win.pages["updates"]
    key, keys = signing
    doc = envelope(key, fields("3.1.0"))
    p.svc = UpdateService(p.s.settings, dist=RM, keys=keys, fetch=lambda url: doc, version="3.0.0",
                          folder=tmp_path / "updates")
    yield p
    win.sessions.stop()


def test_page_shows_the_release_and_offers_only_the_release_page(page, qtbot, monkeypatch):
    from akeno.updater import service
    monkeypatch.setattr(service, "frozen", lambda: True)          # even a built program does not install it
    opened = []
    monkeypatch.setattr("akeno.ui.pages.updates.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.headline.text() == "AKENO Gaming Suite 3.1.0 is available.", timeout=5000)
    assert not page.btn_install.isVisibleTo(page) and page.btn_page.isVisibleTo(page)
    page._install()                                               # nothing happens without a portable package
    assert not page.busy
    page._open_page()
    assert opened == ["https://akeno.online/downloads/latest"]


def test_production_check_compares_the_servers_release_key(monkeypatch, tmp_path, signing, capsys):
    import api_contract
    key, keys = signing
    public = tool.public_of(key)
    data = tmp_path / "data"
    data.mkdir()
    (data / "release-keys.json").write_text(json.dumps({"keys": [{"kid": tool.kid_of(public), "public_key": public}]}))
    (data / "distribution.json").write_text((ROOT / "akeno" / "data" / "distribution.json").read_text())
    answers = {f"{API}/releases/signing-key": (200, {"algorithm": "Ed25519", "key_id": tool.kid_of(public),
                                                     "public_key": public}),
               f"{API}/releases/stable": (404, None)}
    monkeypatch.setattr(api_contract, "get_json", lambda url: answers[url])
    assert api_contract.check_release_keys(API, data / "release-keys.json")
    answers[f"{API}/releases/stable"] = (200, envelope(key, fields("3.1.0")))
    assert api_contract.check_release_keys(API, data / "release-keys.json")
    assert "stable 3.1.0 verified" in capsys.readouterr().out
    answers[f"{API}/releases/stable"] = (200, envelope(Ed25519PrivateKey.generate(), fields("3.1.0")))
    assert not api_contract.check_release_keys(API, data / "release-keys.json")
    other = tool.public_of(Ed25519PrivateKey.generate())
    answers[f"{API}/releases/signing-key"] = (200, {"key_id": tool.kid_of(other), "public_key": other})
    assert not api_contract.check_release_keys(API, data / "release-keys.json")
    assert "does not trust" in capsys.readouterr().out


def test_committed_release_key_is_the_servers_published_key():
    # GET https://api.akeno.online/api/v1/releases/signing-key, read by the production check on 2026-10-24
    rel = json.loads((ROOT / "akeno" / "data" / "release-keys.json").read_text())
    assert rel["keys"] == [{"kid": "d6139235b220a4aa", "public_key": "bpe9acmephsoZSYzy12R2XFy_lJZ_69MiV31ekehcdo"}]
