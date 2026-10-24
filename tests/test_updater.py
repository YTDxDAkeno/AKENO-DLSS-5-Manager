"""AKENO's own updates: signed release manifests, version order, refusals, the separate updater process with
a real program folder swap, the health check and the rollback, and the release tool."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_manifest as tool  # noqa: E402

from akeno.licensing import crypto  # noqa: E402
from akeno.updater import apply, manifest  # noqa: E402
from akeno.updater.service import UpdateService, UpdateError  # noqa: E402

REPO = "OWNER/AKENO-Releases"


@pytest.fixture
def signing(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    public = tool.public_of(key)
    keys = {tool.kid_of(public): crypto.public_key(public)}
    return key, keys


def package(tmp_path: Path, version: str, body: str = "") -> Path:
    """A portable package: AKENO.exe is a small Python program here (it reports its start like the real one)."""
    out = tmp_path / f"AKENO-Gaming-Suite-{version}-portable-x64.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("AKENO.exe", f"version {version}")
        z.writestr("AKENO.py", body or (
            "import os, pathlib\n"
            "m = os.environ.get('AKENO_UPDATE_MARKER')\n"
            "m and pathlib.Path(m).write_text('ok')\n"))
        z.writestr("_internal/lib.txt", version)
    return out


def channel_doc(key, version: str, files: list[Path], channel: str = "stable", **extra) -> dict:
    payload = tool.build(files, channel, version, f"v{version}", REPO)
    payload.update(extra)
    return {"format": "akeno-release-channel", "token": tool.sign_payload(key, payload)}


def test_version_order():
    order = ["2.1.0", "3.0.0-beta.1", "3.0.0-rc.1", "3.0.0", "3.0.1", "3.10.0"]
    assert sorted(order, key=manifest.version_key) == order
    assert manifest.newer("3.0.1", "3.0.0") and not manifest.newer("3.0.0", "3.0.0")
    with pytest.raises(manifest.ManifestError):
        manifest.version_key("3.0")


def test_manifest_signature_and_refusals(tmp_path, signing):
    key, keys = signing
    pkg = package(tmp_path, "3.0.1")
    doc = channel_doc(key, "3.0.1", [pkg])
    release = manifest.parse(doc, keys, "stable")
    assert release.release == "3.0.1" and release.file("portable").sha256 == tool.hashlib.sha256(
        pkg.read_bytes()).hexdigest()
    assert release.file("portable").url.startswith(f"https://github.com/{REPO}/releases/download/v3.0.1/")
    with pytest.raises(manifest.ManifestError, match="no release verification key"):
        manifest.parse(doc, {}, "stable")
    with pytest.raises(manifest.ManifestError, match="another channel"):
        manifest.parse(doc, keys, "beta")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with pytest.raises(manifest.ManifestError, match="not signed by AKENO"):
        manifest.parse(channel_doc(Ed25519PrivateKey.generate(), "9.9.9", [pkg]), keys, "stable")
    h, b, sig = doc["token"].split(".")
    payload = json.loads(crypto.b64d(b))
    payload["files"][0]["sha256"] = "0" * 64                         # a swapped download, old signature
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"format": "akeno-release-channel", "token": f"{h}.{tool.b64e(json.dumps(payload).encode())}"
                                                                      f".{sig}"}, keys, "stable")
    with pytest.raises(manifest.ManifestError, match="not for this app"):
        manifest.parse(channel_doc(key, "3.0.1", [pkg], app="other-app"), keys, "stable")
    bad = tool.build([pkg], "stable", "3.0.1", "v3.0.1", REPO)
    bad["files"][0]["url"] = "https://evil.example/AKENO.zip"
    with pytest.raises(manifest.ManifestError, match="not valid"):
        manifest.parse({"format": "akeno-release-channel", "token": tool.sign_payload(key, bad)}, keys, "stable")


def test_license_keys_cannot_sign_releases(tmp_path, signing):
    """A license token (typ akeno-lease) never passes as a release manifest, even from a trusted key."""
    key, keys = signing
    payload = tool.build([package(tmp_path, "3.0.1")], "stable", "3.0.1", "v3.0.1", REPO)
    token = tool.sign_payload(key, payload).split(".")
    header = json.loads(crypto.b64d(token[0]))
    header["typ"] = "akeno-lease"
    h = tool.b64e(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    forged = f"{h}.{token[1]}.{tool.b64e(key.sign(f'{h}.{token[1]}'.encode()))}"
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"format": "akeno-release-channel", "token": forged}, keys, "stable")


def test_check_offers_newer_only_and_refuses_going_back(tmp_path, signing):
    key, keys = signing
    docs = {"doc": channel_doc(key, "3.0.1", [package(tmp_path, "3.0.1")])}
    svc = UpdateService(keys=keys, fetch=lambda url: docs["doc"], version="3.0.0", folder=tmp_path / "updates")
    release = svc.check()
    assert release is not None and release.release == "3.0.1"
    assert UpdateService(keys=keys, fetch=lambda url: docs["doc"], version="3.0.1",
                         folder=tmp_path / "updates").check() is None
    docs["doc"] = channel_doc(key, "3.0.0", [package(tmp_path, "3.0.0")])     # an old manifest replayed
    with pytest.raises(manifest.ManifestError, match="older than"):
        svc.check()
    svc._store(failed=["3.0.2"])
    docs["doc"] = channel_doc(key, "3.0.2", [package(tmp_path, "3.0.2")])
    assert svc.check() is None, "a version that did not start here is not offered again"
    assert svc.dist.channel_manifest("beta").startswith("https://raw.githubusercontent.com/")


def test_stage_refuses_unsafe_packages(tmp_path, signing):
    key, keys = signing
    svc = UpdateService(keys=keys, version="3.0.0", folder=tmp_path / "updates")
    release = manifest.Release("stable", "3.0.1", "v3.0.1", "", "", "")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../outside.txt", "x")
    with pytest.raises(UpdateError, match="refused path"):
        svc.stage(evil, release)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(UpdateError, match="no AKENO.exe"):
        svc.stage(empty, release)
    staged = svc.stage(package(tmp_path, "3.0.1"), release)
    assert (staged / "AKENO.exe").read_text() == "version 3.0.1"


def installed(tmp_path: Path, version: str) -> Path:
    target = tmp_path / "Programs" / "AKENO"
    target.mkdir(parents=True)
    (target / "AKENO.exe").write_text(f"version {version}")
    (target / "AKENO.py").write_text("import os, pathlib\nm = os.environ.get('AKENO_UPDATE_MARKER')\n"
                                     "m and pathlib.Path(m).write_text('old ok')\n")
    (target / "_internal").mkdir()
    (target / "_internal" / "lib.txt").write_text(version)
    (target / "data").mkdir()
    (target / "data" / "settings.txt").write_text("portable user data")
    return target


def updater_plan(svc, release, staged, target) -> dict:
    plan = svc.plan(release, staged, target)
    plan.update(keep=["data"], pid=None, timeout=20, start=[sys.executable, "{target}/AKENO.py"])
    return plan


def test_update_swaps_the_program_folder_and_keeps_the_previous_version(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    svc = UpdateService(keys=keys, version="3.0.0", folder=tmp_path / "updates")
    release = manifest.Release("stable", "3.0.1", "v3.0.1", "", "", "")
    staged = svc.stage(package(tmp_path, "3.0.1"), release)
    outcome = apply.run(updater_plan(svc, release, staged, target))
    assert outcome["ok"] and not outcome["rolled_back"], outcome
    assert (target / "AKENO.exe").read_text() == "version 3.0.1"
    assert (target / "data" / "settings.txt").read_text() == "portable user data", "user data kept"
    backup = Path(svc.folder / "previous")
    assert (backup / "AKENO.exe").read_text() == "version 3.0.0", "the previous version is kept"
    (svc.folder / "result.json").write_text(json.dumps(outcome))
    assert svc.after_start()["ok"] and svc.previous() == "3.0.0"
    rollback = svc.rollback_plan()
    rollback.update(keep=["data"], pid=None, timeout=20, start=[sys.executable, "{target}/AKENO.py"])
    rollback["target"] = str(target)
    assert apply.run(rollback)["ok"]
    assert (target / "AKENO.exe").read_text() == "version 3.0.0" and (target / "data" / "settings.txt").exists()


def test_a_version_that_does_not_start_is_rolled_back(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    svc = UpdateService(keys=keys, version="3.0.0", folder=tmp_path / "updates")
    release = manifest.Release("stable", "3.0.1", "v3.0.1", "", "", "")
    broken = package(tmp_path, "3.0.1", body="raise SystemExit(3)\n")    # crashes before its window
    outcome = apply.run(updater_plan(svc, release, svc.stage(broken, release), target))
    assert not outcome["ok"] and outcome["rolled_back"]
    assert (target / "AKENO.exe").read_text() == "version 3.0.0"
    assert (target / "data" / "settings.txt").read_text() == "portable user data"
    assert (svc.folder / "started.marker").read_text() == "old ok", "the previous version runs again"
    (svc.folder / "result.json").write_text(json.dumps(outcome))
    svc.after_start()
    assert "3.0.1" in svc.state()["failed"]


def test_updater_waits_for_the_old_process(tmp_path):
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not apply.wait_for_exit(proc.pid, timeout=0.5)
    finally:
        proc.kill()
        proc.wait()
    assert apply.wait_for_exit(proc.pid, timeout=5)
    assert apply.wait_for_exit(None)


def test_release_tool_round_trip(tmp_path, monkeypatch, capsys):
    keyfile = tmp_path / "release.key"
    assert tool.main(["keygen", "--private-out", str(keyfile)]) == 0
    printed = capsys.readouterr().out
    pair = printed.strip().splitlines()[-1].split(": ", 1)[1]
    if sys.platform != "win32":                   # Windows protects it through the profile folder's ACL
        assert (keyfile.stat().st_mode & 0o077) == 0, "the private key file is private"
    keys_file = tmp_path / "release-keys.json"
    assert tool.main(["embed", "--keys", pair, "--output", str(keys_file), "--require"]) == 0
    monkeypatch.setenv("AKENO_RELEASE_SIGNING_KEY", keyfile.read_text().strip())
    pkg = package(tmp_path, "3.0.1")
    source = tmp_path / "AKENO-Gaming-Suite-3.0.1-source.zip"
    source.write_bytes(b"source archive")
    out = tmp_path / "channels" / "stable.json"
    assert tool.main(["sign", "--channel", "stable", "--release", "3.0.1", "--tag", "v3.0.1", "--repo", REPO,
                      "--out", str(out), str(pkg), str(source)]) == 0
    assert tool.main(["verify", str(out), "--keys", str(keys_file)]) == 0
    kinds = {f["kind"] for f in json.loads(out.read_text())["files"]}
    assert kinds == {"portable", "source"}
    with pytest.raises(SystemExit):
        tool.main(["embed", "--keys", "", "--output", str(tmp_path / "x.json"), "--require"])


def test_updates_page_builds(akeno_home, qtbot, monkeypatch):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: True)
    win = MainWindow(Controller(AppServices(db=Database(akeno_home / "s.sqlite3"))))
    qtbot.addWidget(win)
    win.go("updates")
    page = win.pages["updates"]
    assert win.current_page() == "updates" and not page.btn_install.isVisible()
    ok, why = page.svc.can_install()
    assert not ok and "source code" in why, "running from source never replaces files"
    win.sessions.stop()
