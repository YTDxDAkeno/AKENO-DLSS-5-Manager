"""Updates from AKENO's own release server (akeno.online): the build's release source, trusted hosts, signed and
expiring manifests, progress phases, the code-signature rule, "Later", throttled checks and the Updates page.

Nothing here weakens the existing rules (tests/test_updater.py): a manifest counts only with AKENO's release
signature, every file only with its SHA-256, and no source can point the updater at a host outside GitHub's release
hosts or akeno.online.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_manifest as tool  # noqa: E402

from akeno import distribution  # noqa: E402
from akeno.licensing import crypto  # noqa: E402
from akeno.updater import manifest, service  # noqa: E402
from akeno.updater.service import ServiceUnavailable, UpdateError, UpdateService  # noqa: E402
from test_updater import package  # noqa: E402

BASE = "https://downloads.akeno.online/releases"
VPS = distribution.from_dict({"update_source": "akeno", "release_base": BASE, "release_page":
                              "https://akeno.online/download", "download_hosts": ["akeno.online"]})


@pytest.fixture
def signing():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    public = tool.public_of(key)
    return key, {tool.kid_of(public): crypto.public_key(public)}


def vps_doc(key, version: str, files: list[Path], channel: str = "stable", **kw) -> dict:
    payload = tool.build(files, channel, version, f"v{version}", base_url=f"{BASE}/{version}",
                         notes_url="https://akeno.online/download", **kw)
    return {"format": "akeno-release-channel", "token": tool.sign_payload(key, payload)}


# -- the release source -----------------------------------------------------------------------------------------------
def test_default_source_stays_github():
    d = distribution.from_dict({})
    assert d.source == "github" and d.hosts == distribution.HOSTS
    assert d.channel_manifest("stable").startswith("https://raw.githubusercontent.com/")


def test_akeno_source():
    assert VPS.source == "akeno" and VPS.channel_manifest("beta") == f"{BASE}/channels/beta.json"
    assert VPS.hosts >= {"downloads.akeno.online", "akeno.online"} and VPS.releases == "https://akeno.online/download"
    assert VPS.trusted(f"{BASE}/3.1.0/x.zip") and not VPS.trusted("http://downloads.akeno.online/x.zip")
    assert not VPS.trusted("https://evil.example/x.zip")
    assert not VPS.trusted("https://user@downloads.akeno.online/x.zip"), "no credentials in an update address"


@pytest.mark.parametrize("bad", [
    {"update_source": "akeno", "release_base": "http://downloads.akeno.online/r"},          # not HTTPS
    {"update_source": "akeno", "release_base": "https://akeno.online.evil.example/r"},      # look-alike
    {"update_source": "akeno", "release_base": "https://evil-akeno.online/r"},
    {"update_source": "akeno", "release_base": ""},
])
def test_a_source_outside_akeno_online_is_refused(bad):
    d = distribution.from_dict(bad)
    assert d.source == "github" and d.hosts == distribution.HOSTS, "falls back to GitHub, never to that host"


def test_foreign_download_hosts_are_dropped():
    d = distribution.from_dict({"update_source": "akeno", "release_base": BASE,
                                "download_hosts": ["cdn.example.com", "files.akeno.online"]})
    assert "files.akeno.online" in d.hosts and "cdn.example.com" not in d.hosts


def test_release_tool_writes_and_validates_the_source(tmp_path):
    out = tmp_path / "distribution.json"
    assert tool.main(["distribution", "--source", "akeno", "--release-base", BASE, "--release-page",
                      "https://akeno.online/download", "--download-hosts", "akeno.online", "--output", str(out)]) == 0
    assert distribution.from_dict(json.loads(out.read_text())) == VPS
    with pytest.raises(SystemExit):
        tool.main(["distribution", "--source", "akeno", "--release-base", "https://evil.example/r", "--output",
                   str(out)])
    with pytest.raises(SystemExit):
        tool.main(["distribution", "--source", "akeno", "--release-base", BASE, "--download-hosts", "cdn.example.com",
                   "--output", str(out)])


# -- manifests --------------------------------------------------------------------------------------------------------
def test_vps_manifest_verifies_against_the_vps_source_only(tmp_path, signing):
    key, keys = signing
    doc = vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")])
    release = manifest.parse(doc, keys, "stable", VPS)
    assert release.file("portable").url == f"{BASE}/3.1.0/AKENO-Gaming-Suite-3.1.0-portable-x64.zip"
    assert release.notes_url == "https://akeno.online/download"
    with pytest.raises(manifest.ManifestError):
        manifest.parse(doc, keys, "stable", distribution.from_dict({}))       # a GitHub build: not its hosts


def test_signature_stays_mandatory_on_the_vps(tmp_path, signing):
    key, keys = signing
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    stranger = Ed25519PrivateKey.generate()
    doc = vps_doc(stranger, "9.9.9", [package(tmp_path, "9.9.9")])
    with pytest.raises(manifest.ManifestError):
        manifest.parse(doc, keys, "stable", VPS)
    with pytest.raises(manifest.ManifestError):
        manifest.parse(doc, {}, "stable", VPS)


def test_expiring_manifests(tmp_path, signing):
    key, keys = signing
    doc = vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")], expires_days=30)
    payload = json.loads(crypto.b64d(doc["token"].split(".")[1]))
    assert payload["expires"]
    assert manifest.parse(doc, keys, "stable", VPS).release == "3.1.0"
    with pytest.raises(manifest.ManifestError, match="expired"):
        manifest.parse(doc, keys, "stable", VPS, now=time.time() + 31 * 86400)


def test_tool_refuses_addresses_outside_akeno_online(tmp_path):
    pkg = package(tmp_path, "3.1.0")
    with pytest.raises(SystemExit):
        tool.build([pkg], "stable", "3.1.0", "v3.1.0", base_url="https://cdn.example.com/r")
    with pytest.raises(SystemExit):
        tool.build([pkg], "stable", "3.1.0", "v3.1.0", repo="O/R", base_url=BASE)


# -- the service -----------------------------------------------------------------------------------------------------
class LocalDownloads:
    """Serves the release files from a folder, verifying SHA-256 like the real download manager."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.urls = []

    def fetch(self, url, sha256, name, size=None, progress=None, token=None):
        self.urls.append(url)
        data = (self.folder / name).read_bytes()
        for done in (len(data) // 2, len(data)):
            progress and progress(done, len(data))
        if hashlib.sha256(data).hexdigest() != sha256:
            raise UpdateError("checksum mismatch")
        out = self.folder / "cache" / name
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(data)
        return type("R", (), {"path": out, "size": len(data)})

    def cached_path(self, sha256, name):
        return self.folder / "cache" / name


def test_check_prepare_and_phases_from_the_vps(tmp_path, signing):
    key, keys = signing
    pkg = package(tmp_path, "3.1.0")
    doc = vps_doc(key, "3.1.0", [pkg])
    seen = []
    svc = UpdateService(dist=VPS, keys=keys, fetch=lambda url: seen.append(url) or doc, version="3.0.0",
                        folder=tmp_path / "updates", downloads=LocalDownloads(tmp_path))
    release = svc.check()
    assert seen == [f"{BASE}/channels/stable.json"] and release.release == "3.1.0"
    events = []
    plan = svc.prepare(release, lambda *e: events.append(e))
    assert [e[0] for e in events if e[0] != "unpack" or not e[1]] == \
        ["space", "download", "download", "verified", "unpack", "signature"]
    assert Path(plan["source"], "AKENO.exe").is_file() and plan["version"] == "3.1.0"
    assert svc.downloads().urls == [f"{BASE}/3.1.0/{pkg.name}"]


def test_a_tampered_package_is_never_staged(tmp_path, signing):
    key, keys = signing
    pkg = package(tmp_path, "3.1.0")
    doc = vps_doc(key, "3.1.0", [pkg])
    pkg.write_bytes(pkg.read_bytes() + b"tampered")
    svc = UpdateService(dist=VPS, keys=keys, fetch=lambda url: doc, version="3.0.0", folder=tmp_path / "updates",
                        downloads=LocalDownloads(tmp_path))
    with pytest.raises(UpdateError):
        svc.prepare(svc.check())
    assert not (tmp_path / "updates" / "staging" / "3.1.0").exists()


def test_replayed_older_vps_manifest_is_refused(tmp_path, signing):
    key, keys = signing
    docs = {"doc": vps_doc(key, "3.2.0", [package(tmp_path, "3.2.0")])}
    svc = UpdateService(dist=VPS, keys=keys, fetch=lambda url: docs["doc"], version="3.0.0",
                        folder=tmp_path / "updates")
    assert svc.check().release == "3.2.0"
    docs["doc"] = vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")])
    with pytest.raises(manifest.ManifestError, match="older"):
        svc.check()


def test_checks_are_throttled_and_later_is_remembered(tmp_path, signing):
    key, keys = signing
    doc = vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")])
    svc = UpdateService(dist=VPS, keys=keys, fetch=lambda url: doc, version="3.0.0", folder=tmp_path / "updates")
    assert svc.due()
    svc.check()
    assert not svc.due() and svc.due(now=time.time() + service.CHECK_HOURS * 3600 + 1)
    failing = UpdateService(dist=VPS, keys=keys, version="3.0.0", folder=tmp_path / "u2",
                            fetch=lambda url: (_ for _ in ()).throw(ServiceUnavailable("offline")))
    with pytest.raises(ServiceUnavailable):
        failing.check()
    assert not failing.due(), "an outage is not retried in a loop"
    assert not svc.snoozed("3.1.0")
    svc.snooze("3.1.0")
    assert svc.snoozed("3.1.0") and not svc.snoozed("3.2.0")


def test_fetch_refuses_untrusted_addresses():
    with pytest.raises(UpdateError):
        service.fetch_json("https://evil.example/channels/stable.json", VPS.trusted)
    with pytest.raises(UpdateError):
        service.fetch_json("http://downloads.akeno.online/channels/stable.json", VPS.trusted)


def test_unreachable_update_service_is_reported_as_such(monkeypatch):
    import socket
    import urllib.request

    def refuse(self, req, timeout=None):
        raise urllib.error.URLError(socket.gaierror("name not known"))
    import urllib.error
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    with pytest.raises(ServiceUnavailable):
        service.fetch_json(f"{BASE}/channels/stable.json", VPS.trusted)


# -- code signature ---------------------------------------------------------------------------------------------------
def test_code_signature_rule(monkeypatch, tmp_path):
    from akeno.security import authenticode
    Sig = authenticode.SignatureInfo
    table = {}
    monkeypatch.setattr(authenticode, "verify", lambda path: table[Path(path).name])
    new = tmp_path / "new.exe"
    table.update({"running.exe": Sig(True, True, "AKENO", ""), "new.exe": Sig(True, False, None, "")})
    with pytest.raises(UpdateError, match="no valid code signature"):
        service.signature_check(new, tmp_path / "running.exe")
    table["new.exe"] = Sig(True, True, "Someone Else", "")
    with pytest.raises(UpdateError, match="signed by"):
        service.signature_check(new, tmp_path / "running.exe")
    table["new.exe"] = Sig(True, True, "AKENO", "")
    assert "valid" in service.signature_check(new, tmp_path / "running.exe")
    table["running.exe"] = Sig(True, False, None, "")
    assert "not code-signed" in service.signature_check(new, tmp_path / "running.exe")
    table["running.exe"] = Sig(False, False, None, "")
    assert "not checked" in service.signature_check(new, tmp_path / "running.exe")


# -- the page ---------------------------------------------------------------------------------------------------------
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
    doc = vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")])
    p.svc = UpdateService(p.s.settings, dist=VPS, keys=keys, fetch=lambda url: doc, version="3.0.0",
                          folder=tmp_path / "updates")
    yield p
    win.sessions.stop()


def test_page_announces_a_new_version(page, qtbot):
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.headline.text() == "AKENO Gaming Suite 3.1.0 is available.", timeout=5000)
    rows = {page.facts.grid.itemAtPosition(i, 0).widget().text(): page.facts.grid.itemAtPosition(i, 1).widget().text()
            for i in range(page.facts.grid.rowCount()) if page.facts.grid.itemAtPosition(i, 0)}
    assert rows["Latest version"] == "3.1.0" and rows["Channel"] == "Stable" and rows["Download size"].endswith("MB")
    assert not page.btn_install.isVisibleTo(page), "from source: never installs, offers the download page"
    assert page.btn_page.isVisibleTo(page) and page.btn_notes.isVisibleTo(page)
    page._later()
    assert page.svc.snoozed("3.1.0") and not page.btn_later.isVisibleTo(page)


def test_page_explains_an_unreachable_service(page, qtbot):
    page.svc.fetch = lambda url: (_ for _ in ()).throw(ServiceUnavailable("offline"))
    page.check(quiet=False)
    qtbot.waitUntil(lambda: bool(page.banner.text()), timeout=5000)
    assert "cannot be reached" in page.banner.text() and page.banner.kind == "warn"


def test_page_refuses_an_unverified_manifest(page, qtbot, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    forged = vps_doc(Ed25519PrivateKey.generate(), "9.0.0", [package(tmp_path, "9.0.0")])
    page.svc.fetch = lambda url: forged
    page.check(quiet=False)
    qtbot.waitUntil(lambda: bool(page.banner.text()), timeout=5000)
    assert "could not be verified" in page.banner.text() and page.headline.isHidden()


def test_notes_dialog_shows_plain_text(page, qtbot, tmp_path, signing):
    from akeno.ui.pages.updates import NotesDialog
    key, keys = signing
    release = manifest.parse(vps_doc(key, "3.1.0", [package(tmp_path, "3.1.0")], notes="<b>bold</b> & more"),
                             keys, "stable", VPS)
    dialog = NotesDialog(release, page)
    qtbot.addWidget(dialog)
    from PySide6.QtWidgets import QPlainTextEdit
    assert dialog.findChild(QPlainTextEdit).toPlainText() == "<b>bold</b> & more"


def test_auto_check_respects_the_setting_and_the_interval(page, qtbot):
    calls = []
    page.check = lambda quiet=False: calls.append(quiet)
    page.s.settings.set("check_app_updates", False)
    page.auto_check()
    page.s.settings.set("check_app_updates", True)
    page.auto_check()
    page.svc._store(last_check=time.time())
    page.auto_check()
    assert calls == [True]


def test_only_the_programs_portable_package_is_portable(tmp_path):
    names = ["AKENO-Gaming-Suite-3.1.0-portable-x64.zip", "AKENO-community-dependencies-3.1.0-x64.zip",
             "AKENO-Gaming-Suite-3.1.0-source.zip", "AKENO-Gaming-Suite-3.1.0-Setup-x64.exe", "SHA256SUMS.txt"]
    assert [tool.kind_of(n) for n in names] == ["portable", "", "source", "installer", "checksums"]
    files = []
    for name in sorted(names, key=str.lower):            # whatever order the files come in
        (tmp_path / name).write_bytes(name.encode())
        files.append(tmp_path / name)
    payload = tool.build(files, "stable", "3.1.0", "v3.1.0", base_url=BASE + "/3.1.0")
    portable = [f for f in payload["files"] if f["kind"] == "portable"]
    assert [f["name"] for f in portable] == ["AKENO-Gaming-Suite-3.1.0-portable-x64.zip"]
