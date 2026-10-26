"""Release Manager schema 2 (updater protocol 2) and the hardened updater.

The release format is the production server's (OpenAPI ``ReleaseEnvelope`` / ``PortablePackage``, read on
2026-10-25). Covered: the signature over the exact ``signed_payload``, only signed values used, expiry, sequence
replays, downgrades, install modes, incompatible releases, the attacks of the hand-over list (manipulated metadata,
signatures, packages, ZIP slip, links, bombs, damaged archives, local tampering between verification and
installation, parallel updates), the journal with recovery after a power cut, the health check and rollback, user
data, and a real HTTPS round trip with redirects.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_manifest as tool  # noqa: E402

from akeno import distribution  # noqa: E402
from akeno.licensing import crypto  # noqa: E402
from akeno.updater import api_v2, apply, manifest, service, staging  # noqa: E402
from akeno.updater.service import UpdateError, UpdateService  # noqa: E402

API = "https://api.akeno.online/api/v1"
RM = distribution.from_dict({"update_source": "akeno-api", "release_api": API,
                             "release_page": "https://akeno.online/downloads/latest", "download_hosts": ["akeno.online"]})
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def signing():
    key = Ed25519PrivateKey.generate()
    public = tool.public_of(key)
    return key, {tool.kid_of(public): crypto.public_key(public)}


def healthy(version: str, ok: bool = True, then: str = "time.sleep(8)") -> str:
    return ("import json, os, pathlib, time\n"
            "m = os.environ.get('AKENO_UPDATE_MARKER')\n"
            f"m and pathlib.Path(m).write_text(json.dumps({{'version': {version!r}, 'ok': {ok!r}, "
            f"'checks': {{'window': True, 'license_state': {ok!r}}}}}))\n"
            f"{then}\n")


def portable_zip(tmp_path: Path, version: str, body: str | None = None, top: str = "AKENO") -> Path:
    out = tmp_path / f"AKENO-Gaming-Suite-{version}-portable-x64.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{top}/AKENO.exe" if top else "AKENO.exe", b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little")
                   + b"PE\0\0" + version.encode())
        z.writestr(f"{top}/AKENO.py" if top else "AKENO.py", body if body is not None else healthy(version))
        z.writestr(f"{top}/_internal/lib.txt" if top else "_internal/lib.txt", version * 100)
    return out


def reference_content_sha256(pkg: Path) -> str:
    """An independent second implementation of the Release Manager's content aggregate (not the client's code):
    records of name as stored, SHA-256 and size of the decompressed bytes, sorted by code point, compact JSON."""
    records = []
    with zipfile.ZipFile(pkg) as z:
        for info in z.infolist():
            if info.filename.endswith("/"):
                continue
            data = z.read(info)
            records.append({"name": info.filename, "sha256": hashlib.sha256(data).hexdigest(),
                            "size_bytes": len(data)})
    records.sort(key=lambda r: [ord(c) for c in r["name"]])
    text = "[" + ",".join('{"name":%s,"sha256":"%s","size_bytes":%d}' % (
        json.dumps(r["name"], ensure_ascii=False), r["sha256"], r["size_bytes"]) for r in records) + "]"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def portable_info(pkg: Path, version: str | None = None, **kw) -> dict:
    data = pkg.read_bytes()
    with zipfile.ZipFile(pkg) as z:
        files = [i for i in z.infolist() if not i.is_dir()]
    version = version or pkg.name.split("-")[3]               # AKENO-Gaming-Suite-<version>-portable-x64.zip
    out = {"download_url": f"https://akeno.online/downloads/{version}/AKENO-Portable.zip", "size_bytes": len(data),
           "sha256": hashlib.sha256(data).hexdigest(),
           "authenticode": {"verified": False, "status": "not_signed", "publisher_certificate_sha256": None},
           "pe_files": 1, "verified_pe_files": 0, "file_count": len(files),
           "uncompressed_size_bytes": sum(i.file_size for i in files),
           "content_sha256": reference_content_sha256(pkg)}
    out.update(kw)
    return out


def fields(version: str = "3.1.0", sequence: int = 7, pkg: Path | None = None, **kw) -> dict:
    out = {"schema_version": 2, "updater_protocol": 2, "product": "akeno-gaming-suite", "channel": "stable",
           "version": version, "release_notes": "Faster scans.", "release_date": "2026-11-02",
           "published_at": iso(NOW - timedelta(hours=1)), "issued_at": iso(NOW - timedelta(hours=1)),
           "expires_at": iso(NOW + timedelta(days=29)), "sequence": sequence, "request_id": "0123456789abcdef" * 2,
           "platform": "windows-x64",
           "download_url": f"https://akeno.online/downloads/{version}/AKENO-Setup.exe",
           "size_bytes": 123456789, "sha256": "cd" * 32,
           "authenticode": {"verified": False, "status": "not_signed", "publisher_certificate_sha256": None},
           "minimum_supported_version": None, "unattended_install_allowed": False,
           "install_mode": "portable-directory-swap" if pkg else "manual-installer",
           "portable": portable_info(pkg, version) if pkg else None}
    out.update(kw)
    if "issued_at" in kw and "published_at" not in kw:
        out["published_at"] = out["issued_at"]              # the backend requires both to be the same instant
    return out


def envelope(key, signed: dict, outer: dict | None = None, payload: str | None = None, sig_format="b64url") -> dict:
    text = payload if payload is not None else json.dumps(signed, sort_keys=True, separators=(",", ":"))
    raw = key.sign(text.encode("utf-8"))
    value = raw.hex() if sig_format == "hex" else base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    doc = dict(signed if outer is None else outer)
    doc.update(signed_payload=text, signature={"algorithm": "Ed25519", "key_id": tool.kid_of(tool.public_of(key)),
                                               "value": value})
    return doc


def parse(doc, keys, now=NOW.timestamp()):
    return manifest.parse_api(doc, keys, RM, now)


# -- the format ------------------------------------------------------------------------------------------------------
def test_a_signed_v2_release_uses_only_signed_values(signing, tmp_path):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    release = parse(envelope(key, fields(pkg=pkg)), keys)
    d = release.v2
    assert (release.release, release.channel, d.sequence, d.install_mode) == ("3.1.0", "stable", 7,
                                                                             "portable-directory-swap")
    assert [f.kind for f in release.files] == ["portable", "installer"]
    assert release.file("portable").sha256 == hashlib.sha256(pkg.read_bytes()).hexdigest()
    assert d.portable.file_count == 3 and d.portable.authenticode.status == "not_signed"
    assert d.in_app_refusal("3.0.0") == "", "an unsigned (no Authenticode) package is not blocked"
    assert release.notes == "Faster scans." and d.request_id == "0123456789abcdef" * 2


def test_signature_and_metadata_manipulation_is_refused(signing, tmp_path):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    good = fields(pkg=pkg)
    with pytest.raises(manifest.ManifestError, match="no release verification key"):
        parse(envelope(key, good), {})
    with pytest.raises(manifest.ManifestError, match="does not trust"):
        parse(envelope(Ed25519PrivateKey.generate(), good), keys)
    forged = envelope(key, good)
    forged["signed_payload"] = forged["signed_payload"].replace('"sequence":7', '"sequence":8')
    with pytest.raises(manifest.ManifestError, match="signature does not match"):
        parse(forged, keys)
    bad_sig = envelope(key, good)
    bad_sig["signature"]["value"] = bad_sig["signature"]["value"][:-4] + "AAAA"
    with pytest.raises(manifest.ManifestError):
        parse(bad_sig, keys)
    other = fields(pkg=pkg)
    other["portable"] = dict(other["portable"], sha256="ef" * 32)
    with pytest.raises(manifest.ManifestError, match="portable differs from the signed value"):
        parse(envelope(key, good, outer=other), keys)
    for name in api_v2.SIGNED:
        with pytest.raises(manifest.ManifestError, match=f"has no {name}"):
            parse(envelope(key, {k: v for k, v in good.items() if k != name}, outer=good), keys)
    with pytest.raises(manifest.ManifestError, match="twice"):
        parse(envelope(key, good, payload='{"sequence":1,' + json.dumps(good)[1:]), keys)
    with pytest.raises(manifest.ManifestError, match="NaN"):
        parse(envelope(key, good, payload=json.dumps(dict(good, extra=float("nan")))), keys)
    for change, match in ((dict(product="other"), "not for this app"), (dict(platform="linux-x64"), "not for this"),
                          (dict(channel="beta"), "another channel"), (dict(version="3.1"), "not a version"),
                          (dict(sequence=0), "sequence"), (dict(sequence=True), "sequence"),
                          (dict(install_mode="silent"), "unknown install mode"),
                          *((dict(download_url=url), "not the official") for url in (
                              "https://evil.example/downloads/3.1.0/AKENO-Setup.exe",
                              "http://akeno.online/downloads/3.1.0/AKENO-Setup.exe",
                              "https://akeno.online/downloads/3.1.0/AKENO-Setup.exe?x=1",
                              "https://akeno.online/downloads/3.0.9/AKENO-Setup.exe",
                              "https://akeno.online/downloads/AKENO-Setup.exe",
                              "https://akeno.online/downloads/3.1.0/akeno-setup.exe",
                              "https://akeno.online/downloads/AKENO-Gaming-Suite-3.1.0-Setup-x64.exe")),
                          (dict(unattended_install_allowed="yes"), "unattended"),
                          (dict(minimum_supported_version="3"), "minimum"),
                          (dict(updater_protocol=3), "updater protocol"),
                          (dict(updater_protocol=1), "updater protocol"),
                          (dict(portable=None), "must be a manual installer"),
                          (dict(install_mode="manual-installer"), "names another install mode")):
        with pytest.raises(manifest.ManifestError, match=match):
            parse(envelope(key, dict(good, **change)), keys)
    for url in ("https://evil.example/downloads/3.1.0/AKENO-Portable.zip",
                "https://akeno.online/downloads/3.1.0/AKENO-Gaming-Suite-3.1.0-portable-x64.zip",
                "https://akeno.online/downloads/3.1.0/AKENO-Portable.zip#x"):
        with pytest.raises(manifest.ManifestError, match="not the official"):
            parse(envelope(key, dict(good, portable=dict(good["portable"], download_url=url))), keys)
    with pytest.raises(manifest.ManifestError, match="verified_pe_files"):
        parse(envelope(key, dict(good, portable=dict(good["portable"], verified_pe_files=2))), keys)


def test_license_keys_never_verify_v2_releases(signing):
    key, _ = signing
    license_keys = {k["kid"]: crypto.public_key(k["public_key"]) for k in
                    json.loads((ROOT / "akeno" / "data" / "licensing.json").read_text())["keys"]}
    with pytest.raises(manifest.ManifestError, match="does not trust"):
        parse(envelope(key, fields()), license_keys)


def test_expired_information_is_refused_and_a_clock_set_back_does_not_help(signing, tmp_path):
    key, keys = signing
    old = fields(issued_at=iso(NOW - timedelta(days=20)), expires_at=iso(NOW - timedelta(days=1)))
    with pytest.raises(manifest.ManifestError, match="expired"):
        parse(envelope(key, old), keys)
    with pytest.raises(manifest.ManifestError, match="not between one hour and thirty days"):
        parse(envelope(key, fields(expires_at=iso(NOW - timedelta(days=2)))), keys)
    with pytest.raises(manifest.ManifestError, match="time zone"):
        parse(envelope(key, fields(expires_at="2026-12-01T10:00:00")), keys)
    docs = {"doc": envelope(key, fields("3.1.0", 7))}
    clock = {"t": NOW.timestamp()}
    svc = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: docs["doc"], version="3.0.0",
                        folder=tmp_path, clock=lambda: clock["t"])
    assert svc.check().release == "3.1.0"
    # the PC's clock is set back two months; information that expired meanwhile stays expired
    clock["t"] = (NOW - timedelta(days=60)).timestamp()
    stale = fields("3.1.0", 7, issued_at=iso(NOW - timedelta(days=70)), expires_at=iso(NOW - timedelta(days=50)))
    docs["doc"] = envelope(key, stale)
    with pytest.raises(manifest.ManifestError, match="expired"):
        svc.check()


@pytest.mark.parametrize("change,match", [
    # issued at most five minutes ahead
    (dict(issued_at=iso(NOW + timedelta(minutes=5)), expires_at=iso(NOW + timedelta(days=10))), None),
    (dict(issued_at=iso(NOW + timedelta(minutes=5, seconds=1)), expires_at=iso(NOW + timedelta(days=10))),
     "issued in the future"),
    # validity between one hour and thirty days, both ends included
    (dict(issued_at=iso(NOW - timedelta(minutes=30)), expires_at=iso(NOW + timedelta(minutes=30))), None),
    (dict(issued_at=iso(NOW - timedelta(minutes=30)), expires_at=iso(NOW + timedelta(minutes=29, seconds=59))),
     "between one hour and thirty days"),
    (dict(issued_at=iso(NOW - timedelta(days=1)), expires_at=iso(NOW + timedelta(days=29))), None),
    (dict(issued_at=iso(NOW - timedelta(days=1)), expires_at=iso(NOW + timedelta(days=29, seconds=1))),
     "between one hour and thirty days"),
    # expires strictly after now
    (dict(issued_at=iso(NOW - timedelta(hours=2)), expires_at=iso(NOW)), "expired"),
    (dict(issued_at=iso(NOW - timedelta(hours=2)), expires_at=iso(NOW + timedelta(seconds=1))), None),
    # published_at is issued_at; UTC only
    (dict(published_at=iso(NOW)), "published_at differs from issued_at"),
    (dict(issued_at="2026-11-02T10:00:00+01:00", published_at="2026-11-02T10:00:00+01:00",
          expires_at=iso(NOW + timedelta(days=2))), "not in UTC"),
    (dict(issued_at="yesterday", published_at="yesterday"), "not a time"),
    # the same text, not only the same instant (the backend compares the strings)
    (dict(issued_at="2026-11-02T09:00:00Z", published_at="2026-11-02T09:00:00+00:00"),
     "published_at differs from issued_at"),
    (dict(issued_at="2026-11-02T09:00:00.000000000000000000000000000000000+00:00"), "not a time"),
])
def test_time_limits_match_the_backend(signing, change, match):
    key, keys = signing
    doc = envelope(key, fields(**change))
    if match is None:
        assert parse(doc, keys).release == "3.1.0"
    else:
        with pytest.raises(manifest.ManifestError, match=match):
            parse(doc, keys)


def test_sequence_replays_and_downgrades_are_refused(signing, tmp_path):
    key, keys = signing
    docs = {"doc": envelope(key, fields("3.1.0", 7))}
    svc = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: docs["doc"], version="3.0.0", folder=tmp_path,
                        clock=lambda: NOW.timestamp())
    assert svc.check().release == "3.1.0"
    assert svc.check().release == "3.1.0", "the same information again is fine"
    docs["doc"] = envelope(key, fields("3.1.0", 6))
    with pytest.raises(manifest.ManifestError, match="replay"):
        svc.check()
    docs["doc"] = envelope(key, fields("3.1.0", 7, sha256="ef" * 32))
    with pytest.raises(manifest.ManifestError, match="same sequence"):
        svc.check()
    docs["doc"] = envelope(key, fields("3.0.9", 8))
    with pytest.raises(manifest.ManifestError, match="older than 3.1.0"):
        svc.check()
    docs["doc"] = envelope(key, fields("3.2.0", 9))
    assert svc.check().release == "3.2.0"
    current = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: envelope(key, fields("3.0.0", 1)),
                            version="3.0.0", folder=tmp_path / "other", clock=lambda: NOW.timestamp())
    assert current.check() is None, "the installed version is not offered as an update"


def test_incompatible_releases_are_shown_but_not_installed_by_the_app(signing, tmp_path, monkeypatch):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    monkeypatch.setattr(service, "frozen", lambda: True)
    monkeypatch.setattr(service, "install_dir", lambda: tmp_path)
    svc = UpdateService(None, dist=RM, keys=keys, version="3.0.0", folder=tmp_path / "u")
    cases = {"offered as an installer": fields(),
             "code signature is invalid": fields(pkg=pkg, portable=portable_info(
                 pkg, authenticode={"verified": False, "status": "invalid", "publisher_certificate_sha256": None})),
             "too old for an update": fields(pkg=pkg, minimum_supported_version="3.0.5")}
    for why, doc in cases.items():
        ok, reason = svc.can_install(parse(envelope(key, doc), keys))
        assert not ok and why in reason, (why, reason)
    ok, reason = svc.can_install(parse(envelope(key, fields(pkg=pkg)), keys))
    assert ok, reason
    mixed = portable_info(pkg, authenticode={"verified": False, "status": "mixed", "publisher_certificate_sha256": None})
    assert svc.can_install(parse(envelope(key, fields(pkg=pkg, portable=mixed)), keys))[0]


def test_unattended_install_allowed_never_skips_the_users_confirmation(signing, tmp_path):
    key, keys = signing
    release = parse(envelope(key, fields(pkg=portable_zip(tmp_path, "3.1.0"), unattended_install_allowed=True)), keys)
    assert release.v2.unattended_install_allowed is True     # accepted as information; the UI still asks
    import inspect
    from akeno.ui.pages import updates
    assert "confirm(" in inspect.getsource(updates.UpdatesPage._install)


# -- unpacking -------------------------------------------------------------------------------------------------------
def write_zip(path: Path, entries: list) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                z.writestr(entry, b"x")
            else:
                z.writestr(entry, b"x")
    return path


@pytest.mark.parametrize("name", ["../outside.txt", "/abs.txt", "C:/win.txt", "C:win.txt", "a/../../b.txt",
                                  "a\\..\\..\\b.txt", "file.txt:stream", "CON", "dir/nul.txt", "trail.", "trail ",
                                  "a//b.txt", "./a.txt", "bad\x01.txt", "x" * 300])
def test_unsafe_names_are_refused(tmp_path, name):
    archive = write_zip(tmp_path / "evil.zip", [name])
    with pytest.raises(staging.StagingError, match="refused path"):
        staging.extract(archive, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists() and not (tmp_path.parent / "b.txt").exists()


def test_links_special_files_and_encrypted_entries_are_refused(tmp_path):
    link = zipfile.ZipInfo("link")
    link.external_attr = (0o120777 << 16)
    with pytest.raises(staging.StagingError, match="link or special"):
        staging.extract(write_zip(tmp_path / "l.zip", [link]), tmp_path / "o1")
    fifo = zipfile.ZipInfo("fifo")
    fifo.external_attr = (0o010644 << 16)
    with pytest.raises(staging.StagingError, match="link or special"):
        staging.extract(write_zip(tmp_path / "f.zip", [fifo]), tmp_path / "o2")
    enc = write_zip(tmp_path / "e.zip", ["secret.txt"])
    data = bytearray(enc.read_bytes())
    for header, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):          # general purpose flag: encrypted
        i = data.find(header)
        data[i + offset] |= 0x1
    enc.write_bytes(bytes(data))
    with pytest.raises(staging.StagingError, match="encrypted"):
        staging.extract(enc, tmp_path / "o3")


def test_duplicates_and_file_folder_conflicts_are_refused(tmp_path):
    with pytest.raises(staging.StagingError, match="same file twice"):
        staging.extract(write_zip(tmp_path / "d.zip", ["AKENO.exe", "akeno.EXE"]), tmp_path / "o1")
    with pytest.raises(staging.StagingError, match="file as a folder"):
        staging.extract(write_zip(tmp_path / "c.zip", ["a", "a/b.txt"]), tmp_path / "o2")
    with pytest.raises(staging.StagingError, match="folder as a file"):
        staging.extract(write_zip(tmp_path / "c2.zip", ["a/b.txt", "A"]), tmp_path / "o3")


def test_zip_bombs_and_lying_archives_are_stopped(tmp_path):
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.bin", b"\0" * (8 * 2 ** 20))        # 8 MB of zeros pack into a few KB
    assert bomb.stat().st_size < 64 * 1024
    with pytest.raises(staging.StagingError, match="more data than the release states"):
        staging.extract(bomb, tmp_path / "o1", max_bytes=2 ** 20)
    with pytest.raises(staging.StagingError, match="more files"):
        staging.extract(write_zip(tmp_path / "many.zip", [f"f{i}" for i in range(5)]), tmp_path / "o2", max_files=4)
    # an entry that declares a small size but unpacks to more
    liar = tmp_path / "liar.zip"
    with zipfile.ZipFile(liar, "w", zipfile.ZIP_STORED) as z:
        z.writestr("a.bin", b"A" * 1000)
    data = bytearray(liar.read_bytes())
    for header, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):     # uncompressed size fields
        i = data.find(header)
        data[i + offset:i + offset + 4] = (10).to_bytes(4, "little")
    liar.write_bytes(bytes(data))
    with pytest.raises(staging.StagingError, match="more data than it declares|damaged"):   # never trusted
        staging.extract(liar, tmp_path / "o3")


def test_a_damaged_archive_is_refused(tmp_path):
    pkg = portable_zip(tmp_path, "3.1.0")
    data = bytearray(pkg.read_bytes())
    i = data.find(b"3.1.03.1.0")          # inside the stored or deflated data of lib.txt
    if i < 0:
        i = len(data) // 3
    data[i] ^= 0xFF
    pkg.write_bytes(bytes(data))
    with pytest.raises(staging.StagingError):
        staging.extract(pkg, tmp_path / "out")
    not_zip = tmp_path / "x.zip"
    not_zip.write_bytes(b"not a zip")
    with pytest.raises(staging.StagingError, match="not a readable ZIP"):
        staging.extract(not_zip, tmp_path / "out2")


def test_content_list_catches_changes_after_verification(tmp_path):
    pkg = portable_zip(tmp_path, "3.1.0")
    content = staging.extract(pkg, tmp_path / "out")
    root = staging.program_root(tmp_path / "out")
    content = staging.relative_to_root(content, "AKENO")
    staging.verify(root, content)
    assert staging.pe_count(root, content) == 1
    (root / "_internal" / "lib.txt").write_text("changed")
    with pytest.raises(staging.StagingError, match="differs"):
        staging.verify(root, content)
    (root / "_internal" / "lib.txt").unlink()
    with pytest.raises(staging.StagingError, match="missing"):
        staging.verify(root, content)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_links_in_a_staged_folder_and_on_the_way_are_refused(tmp_path):
    pkg = portable_zip(tmp_path, "3.1.0")
    content = staging.relative_to_root(staging.extract(pkg, tmp_path / "out"), "AKENO")
    root = tmp_path / "out" / "AKENO"
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", root / "planted")
    with pytest.raises(staging.StagingError, match="link or junction"):
        staging.verify(root, content)
    os.symlink(tmp_path / "elsewhere", tmp_path / "staging-link")
    with pytest.raises(staging.StagingError, match="link or junction"):
        staging.no_links_on_the_way(tmp_path / "staging-link", tmp_path)


# -- the service end to end (download, verify, unpack, plan) ---------------------------------------------------------
class LocalDownloads:
    def __init__(self, folder: Path):
        self.folder = folder
        self.urls = []

    def cached_path(self, sha256, name):
        return self.folder / "cache" / name

    def fetch(self, url, sha256, name, size=None, progress=None, token=None):
        from akeno.security.downloads import VerificationError
        self.urls.append(url)
        version = url.split("/downloads/", 1)[1].split("/")[0]     # …/downloads/<version>/AKENO-Portable.zip
        data = (self.folder / f"AKENO-Gaming-Suite-{version}-portable-x64.zip").read_bytes()
        progress and progress(len(data), len(data))
        if hashlib.sha256(data).hexdigest() != sha256:
            raise VerificationError("SHA-256 mismatch")
        out = self.cached_path(sha256, name)
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(data)
        return type("R", (), {"path": out, "size": len(data)})


def service_for(tmp_path, key, keys, pkg, version="3.0.0", **kw):
    doc = envelope(key, fields(pkg=pkg, **kw))
    return UpdateService(None, dist=RM, keys=keys, fetch=lambda url: doc, version=version,
                         folder=tmp_path / "updates", downloads=LocalDownloads(pkg.parent),
                         clock=lambda: NOW.timestamp())


def test_prepare_verifies_unpacks_and_plans(tmp_path, signing):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    release = svc.check()
    events = []
    plan = svc.prepare(release, lambda *e: events.append(e[0]))
    assert events[0] == "space" and "verified" in events and events[-1] == "signature"
    content = staging.load(plan["content"])
    assert set(content["files"]) == {"AKENO.exe", "AKENO.py", "_internal/lib.txt"}
    assert content["release"]["file_count"] == 3 and content["pe_files"] == 1
    assert Path(plan["source"], "AKENO.exe").is_file() and plan["journal"].endswith("journal.json")
    assert "--recover-update" in plan["recovery_command"]
    assert svc.downloaded(release)


def test_a_manipulated_package_is_never_staged(tmp_path, signing):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    release = svc.check()
    pkg.write_bytes(pkg.read_bytes() + b"tampered")
    with pytest.raises(Exception, match="SHA-256"):
        svc.prepare(release)
    assert not (tmp_path / "updates" / "staging" / "3.1.0").exists()


def test_a_package_larger_than_signed_limits_is_refused(tmp_path, signing):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg, portable=portable_info(pkg, file_count=2))
    with pytest.raises(UpdateError, match="more files"):
        svc.prepare(svc.check())


def test_not_enough_space_stops_before_the_download(tmp_path, signing, monkeypatch):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    monkeypatch.setattr(apply, "free_bytes", lambda path: 10)
    with pytest.raises(UpdateError, match="not enough free space"):
        svc.prepare(svc.check())
    assert svc.downloads().urls == []


def test_only_one_update_is_prepared_at_a_time(tmp_path, signing):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    release = svc.check()
    with apply.lock(svc.folder / "prepare.lock"):
        with pytest.raises(OSError, match="another AKENO update"):
            svc.prepare(release)
    (svc.folder / "prepare.lock").write_text(json.dumps({"pid": 999999, "created": 1}))   # left by a crash
    svc.prepare(release)


# -- applying: journal, health check, rollback, recovery -------------------------------------------------------------
def installed(tmp_path: Path, version: str) -> Path:
    target = tmp_path / "Programs" / "AKENO Gaming Suite"
    (target / "_internal").mkdir(parents=True)
    (target / "AKENO.exe").write_text(f"version {version}")
    (target / "AKENO.py").write_text(healthy(version))
    (target / "_internal" / "lib.txt").write_text(version)
    return target


def user_data(home: Path) -> dict:
    """Licenses, settings, games, wallpapers: what an update must never touch."""
    for rel, data in (("license/state.bin", b"protected license state"), ("akeno.sqlite3", b"settings and games"),
                      ("desktop/themes/neon.json", b"{}"), ("desktop/wallpapers/sky.png", b"\x89PNG")):
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest() for p in home.rglob("*")
            if p.is_file() and "updates" not in p.parts}


def plan_for(svc, release, target, **kw):
    plan = svc.prepare(release)
    plan.update(target=str(target), pid=None, timeout=20, settle=0.5, grace=1,
                start=[sys.executable, "{target}/AKENO.py"], recovery_command="")
    plan.update(kw)
    return plan


def test_update_applies_with_journal_and_keeps_user_data(tmp_path, signing, akeno_home):
    key, keys = signing
    before = user_data(akeno_home)
    target = installed(tmp_path, "3.0.0")
    (target / "unins000.exe").write_bytes(b"MZ uninstaller")      # Inno Setup's uninstaller in the program folder
    (target / "unins000.dat").write_bytes(b"uninstall log")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    outcome = apply.run(plan_for(svc, svc.check(), target))
    assert outcome["ok"], outcome
    assert (target / "AKENO.py").read_text() == healthy("3.1.0")
    assert (target / "unins000.exe").read_bytes() == b"MZ uninstaller", "uninstalling keeps working"
    assert (target / "unins000.dat").is_file() and not (svc.folder / "previous" / "unins000.exe").exists()
    assert (svc.folder / "previous" / "AKENO.exe").read_text() == "version 3.0.0"
    phases = [p for p, _ in svc.journal()["history"]]
    assert phases == ["prepared", "stopping", "copying", "swapping", "swapped", "health", "committing", "committed"]
    assert not any(p.name.startswith(".AKENO Gaming Suite.") for p in target.parent.iterdir()), "nothing left over"
    assert user_data(akeno_home) == before
    (svc.folder / "result.json").write_text(json.dumps(outcome))
    assert svc.after_start()["ok"] and not (svc.folder / "staging").exists()


@pytest.mark.parametrize("body,why", [
    (healthy("3.1.0", ok=False), "own checks failed"),
    (healthy("3.0.9"), "expected 3.1.0"),
    (healthy("3.1.0", then="raise SystemExit(0)"), "ended right after starting"),
    ("raise SystemExit(3)\n", "ended before it was ready"),
])
def test_a_new_version_that_is_not_healthy_is_rolled_back(tmp_path, signing, akeno_home, body, why):
    key, keys = signing
    before = user_data(akeno_home)
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0", body=body)
    svc = service_for(tmp_path, key, keys, pkg)
    outcome = apply.run(plan_for(svc, svc.check(), target))
    assert not outcome["ok"] and outcome["rolled_back"] and why in outcome["error"], outcome
    assert (target / "AKENO.exe").read_text() == "version 3.0.0"
    assert svc.journal()["phase"] == "rolled_back"
    assert not (svc.folder / "previous").exists(), "a failed update never replaces the kept version"
    assert user_data(akeno_home) == before


def test_staged_files_changed_after_verification_never_reach_the_program_folder(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    plan = plan_for(svc, svc.check(), target)
    Path(plan["source"], "_internal", "evil.dll").write_bytes(b"MZ planted")
    outcome = apply.run(plan)
    assert not outcome["ok"] and "nothing was changed" in outcome["error"] and "unexpected" in outcome["error"]
    assert (target / "AKENO.exe").read_text() == "version 3.0.0" and not (target / "_internal" / "evil.dll").exists()


def test_two_updaters_never_run_at_once(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    plan = plan_for(svc, svc.check(), target)
    with apply.lock(Path(plan["lock"])):
        outcome = apply.run(plan)
    assert not outcome["ok"] and "another AKENO update" in outcome["error"]
    assert (target / "AKENO.exe").read_text() == "version 3.0.0"


def test_other_akeno_processes_from_the_program_folder_are_ended(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    exe = target / Path(sys.executable).name
    shutil.copy2(sys.executable, exe)                 # like the desktop runtime, running from the folder
    proc = subprocess.Popen([str(exe), "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.5)
        assert [p.pid for p in apply.processes_in(target)] == [proc.pid]
        assert apply.stop_processes(target, grace=0.5) == []
        assert proc.wait(10) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def crash_in_a_subprocess(plan: dict, tmp_path: Path) -> int:
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan))
    code = ("import sys; sys.path.insert(0, %r); from akeno.updater import apply; "
            "sys.exit(apply.main(['--apply-update', %r]))") % (str(ROOT), str(plan_file))
    return subprocess.run([sys.executable, "-c", code], timeout=60).returncode


def test_a_power_cut_after_the_switch_is_recovered_at_the_next_start(tmp_path, signing, akeno_home):
    key, keys = signing
    before = user_data(akeno_home)
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    plan = plan_for(svc, svc.check(), target, test_crash_after="swapped")
    assert crash_in_a_subprocess(plan, tmp_path) == 9
    assert svc.journal()["phase"] == "swapped" and (target.parent / ".AKENO Gaming Suite.old").is_dir()
    assert (target / "AKENO.py").read_text() == healthy("3.1.0"), "the new version is in place, unconfirmed"
    outcome = apply.recover(Path(plan["journal"]), start=False)
    assert outcome["rolled_back"] and outcome["recovered"] == "swapped"
    assert (target / "AKENO.exe").read_text() == "version 3.0.0"
    assert apply.recover(Path(plan["journal"]), start=False) is None, "recovery runs only once"
    assert user_data(akeno_home) == before
    assert not (target.parent / ".AKENO Gaming Suite.old").exists()


def test_recovery_of_each_unfinished_phase_is_idempotent(tmp_path, signing):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    plan = plan_for(svc, svc.check(), target)
    journal = apply.Journal(Path(plan["journal"]))
    n = apply.names(target)
    # interrupted while copying: nothing changed yet
    n["new"].mkdir()
    journal.write(phase="copying", plan=plan)
    assert "before anything changed" in apply.recover(journal.path, start=False)["error"]
    assert not n["new"].exists() and (target / "AKENO.exe").read_text() == "version 3.0.0"
    # interrupted between the two renames: the program folder is missing
    os.replace(target, n["old"])
    journal.write(phase="swapping")
    assert apply.recover(journal.path, start=False)["rolled_back"]
    assert (target / "AKENO.exe").read_text() == "version 3.0.0"
    # interrupted while keeping the previous version of a confirmed update
    shutil.copytree(target, n["old"])
    (target / "AKENO.exe").write_text("version 3.1.0")
    journal.write(phase="committing")
    assert apply.recover(journal.path, start=False)["ok"]
    assert (target / "AKENO.exe").read_text() == "version 3.1.0"
    assert (Path(plan["backup"]) / "AKENO.exe").read_text() == "version 3.0.0"
    assert journal.load()["phase"] == "committed"


def test_the_installed_akeno_hands_an_interrupted_update_to_the_staged_updater(tmp_path, monkeypatch):
    folder = tmp_path / "updates"
    target = tmp_path / "Programs" / "AKENO"
    target.mkdir(parents=True)
    staged = folder / "staging" / "3.1.0"
    staged.mkdir(parents=True)
    (staged / "AKENO.exe").write_text("x")
    plan = {"target": str(target), "source": str(staged), "lock": str(folder / "apply.lock"),
            "result": str(folder / "result.json"), "version": "3.1.0"}
    apply.Journal(folder / "journal.json").write(phase="swapped", plan=plan)
    started = []
    monkeypatch.setattr(apply.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.delenv("AKENO_UPDATE_MARKER", raising=False)
    assert apply.handoff_recovery(target, folder)
    assert started and started[0][1] == "--recover-update" and started[0][0] == str(staged / "AKENO.exe")
    assert not apply.handoff_recovery(tmp_path / "another-copy", folder)
    monkeypatch.setenv("AKENO_UPDATE_MARKER", str(tmp_path / "m"))
    assert not apply.handoff_recovery(target, folder), "the health check start of the new version"
    monkeypatch.delenv("AKENO_UPDATE_MARKER")
    apply.Journal(folder / "journal.json").write(phase="committed")
    assert not apply.handoff_recovery(target, folder)


def test_retry_waits_for_files_a_scanner_holds_open():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "in use")
        return "done"
    assert apply.retry(flaky, "test", timeout=5) == "done" and len(calls) == 3
    with pytest.raises(OSError, match="test"):
        apply.retry(lambda: (_ for _ in ()).throw(PermissionError(13, "in use")), "test", timeout=0.3)


# -- the health report of the real program ---------------------------------------------------------------------------
def test_health_report_checks_without_changing_anything(akeno_home):
    from akeno.core.db import Database
    from akeno.licensing.storage import SecureStore
    Database(akeno_home / "akeno.sqlite3")
    SecureStore().save({"license": "kept"})
    report = service.health_report(expected=service.__version__)
    assert report["ok"], report
    assert set(report["checks"]) == set(service.HEALTH_CHECKS)
    assert not service.health_report(expected="9.9.9")["ok"]
    state = SecureStore().path
    state.write_bytes(b"\x00 damaged")
    report = service.health_report()
    assert not report["ok"] and report["checks"]["license_state"] is not True
    assert state.read_bytes() == b"\x00 damaged", "a health check never moves or rewrites the license state"


def test_write_health_report_does_not_leak_the_marker_to_child_processes(tmp_path, monkeypatch, akeno_home):
    marker = tmp_path / "marker"
    monkeypatch.setenv("AKENO_UPDATE_MARKER", str(marker))
    monkeypatch.setenv("AKENO_UPDATE_EXPECT", service.__version__)
    service.write_health_report()
    assert json.loads(marker.read_text())["version"] == service.__version__
    assert "AKENO_UPDATE_MARKER" not in os.environ and "AKENO_UPDATE_EXPECT" not in os.environ


# -- a real HTTPS round trip -----------------------------------------------------------------------------------------
@pytest.fixture
def https_server(tmp_path, monkeypatch):
    """A local HTTPS server with its own certificate authority (trusted through SSL_CERT_FILE)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AKENO test CA")])
    start = datetime.now(timezone.utc) - timedelta(days=1)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(start).not_valid_after(start + timedelta(days=3))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(name).public_key(key.public_key()).serial_number(2).not_valid_before(start)
            .not_valid_after(start + timedelta(days=3))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = tmp_path / "ca.pem"
    pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    chain = tmp_path / "server.pem"
    chain.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    routes: dict[str, tuple[int, dict, bytes]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, b"{}"))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(chain)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    base = f"https://localhost:{server.server_address[1]}"
    dist = distribution.Distribution("O/R", "akeno-api", "", "", frozenset({"localhost"}), base + "/api/v1", base)
    yield base, routes, dist
    server.shutdown()


def test_release_information_and_package_over_real_https(tmp_path, signing, https_server):
    key, keys = signing
    base, routes, dist = https_server
    pkg = portable_zip(tmp_path, "3.1.0")
    info = portable_info(pkg, download_url=f"{base}/downloads/3.1.0/AKENO-Portable.zip")
    signed = fields(pkg=pkg, portable=info, download_url=f"{base}/downloads/3.1.0/AKENO-Setup.exe")
    routes["/api/v1/releases/stable"] = (200, {"Content-Type": "application/json"},
                                         json.dumps(envelope(key, signed)).encode())
    routes["/downloads/3.1.0/AKENO-Portable.zip"] = (200, {}, pkg.read_bytes())
    svc = UpdateService(None, dist=dist, keys=keys, version="3.0.0", folder=tmp_path / "updates",
                        clock=lambda: NOW.timestamp())
    release = svc.check()
    assert release.release == "3.1.0" and release.v2.sequence == 7
    plan = svc.prepare(release)
    assert set(staging.load(plan["content"])["files"]) == {"AKENO.exe", "AKENO.py", "_internal/lib.txt"}
    # a redirect to another host is refused, for the release information and for downloads
    routes["/api/v1/releases/stable"] = (302, {"Location": "https://evil.example/stable"}, b"")
    with pytest.raises(UpdateError, match="redirect"):
        svc.check()
    routes["/api/v1/releases/stable"] = (404, {}, b"{}")
    assert svc.check() is None, "nothing published means up to date"
    routes["/api/v1/releases/stable"] = (503, {}, b"{}")
    with pytest.raises(service.ServiceUnavailable):
        svc.check()


# -- the Updates page ------------------------------------------------------------------------------------------------
@pytest.fixture
def page(akeno_home, qtbot, monkeypatch, tmp_path, signing):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: True)
    monkeypatch.setattr(service, "frozen", lambda: True)            # as in the built program
    monkeypatch.setattr(service, "install_dir", lambda: tmp_path / "Programs" / "AKENO Gaming Suite")
    # the "running program" here is python.exe, which is code-signed on Windows; the rule has its own tests
    monkeypatch.setattr(service, "signature_check",
                        lambda new_exe, running_exe=None, pin=None: "not checked in this test")
    (tmp_path / "Programs" / "AKENO Gaming Suite").mkdir(parents=True)
    win = MainWindow(Controller(AppServices(db=Database(akeno_home / "s.sqlite3"))))
    qtbot.addWidget(win)
    win.resize(1400, 900)
    win.show()
    win.go("updates")
    p = win.pages["updates"]
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    p.svc = service_for(tmp_path, key, keys, pkg)
    p.svc.settings = p.s.settings
    yield p
    win.sessions.stop()


def rows_of(grid) -> dict:
    return {grid.grid.itemAtPosition(i, 0).widget().text(): grid.grid.itemAtPosition(i, 1).widget().text()
            for i in range(grid.grid.rowCount()) if grid.grid.itemAtPosition(i, 0)}


def shot(page, name: str) -> None:
    out = os.environ.get("AKENO_UPDATE_SCREENSHOTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        page.window().grab().save(str(Path(out) / f"{name}.png"))


def test_page_shows_a_v2_release_with_its_verification(page, qtbot):
    shot(page, "updates-not-checked")
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.headline.text() == "AKENO Gaming Suite 3.1.0 is available.", timeout=5000)
    assert rows_of(page.facts)["Latest version"] == "3.1.0"
    verify = rows_of(page.verify_kv)
    assert "valid" in verify["Release signature"] and "sequence 7" in verify["Release information"]
    assert "Not code-signed" in verify["Code signature"]
    this_pc = rows_of(page.kv)
    assert this_pc["Installed version"] == service.__version__ and this_pc["Latest version"] == "3.1.0"
    assert this_pc["Last successful check"] != "—"
    assert page.btn_install.isVisibleTo(page) and page.btn_download.isVisibleTo(page)
    assert page.btn_notes.isVisibleTo(page) and page.btn_later.isVisibleTo(page)
    assert not page.btn_page.isVisibleTo(page) and not page.channel_row.isVisibleTo(page), "only stable on the RM"
    shot(page, "updates-available")


def test_page_downloads_in_the_background_and_can_cancel(page, qtbot, monkeypatch):
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.release is not None, timeout=5000)
    page._download()
    qtbot.waitUntil(lambda: not page.busy, timeout=5000)
    assert page.svc.downloaded(page.release) and not page.btn_download.isVisibleTo(page)
    assert "Ready to install" in page.pill.text()
    assert "match the signed values" in rows_of(page.verify_kv)["Package checksum"]
    shot(page, "updates-ready")
    # a slow download is cancelled by the user: nothing is kept, the page is usable again
    from akeno.security.downloads import Cancelled

    def slow(url, sha256, name, size=None, progress=None, token=None):
        while not token.cancelled:
            progress and progress(1, 2)
            time.sleep(0.05)
        raise Cancelled("Download cancelled.")
    monkeypatch.setattr(page.svc.downloads(), "fetch", slow)
    page.svc._store(downloaded=None)
    page._download()
    qtbot.waitUntil(lambda: page.btn_cancel.isVisible(), timeout=5000)
    assert page.busy and not page.btn_check.isEnabled()
    page._cancel()
    qtbot.waitUntil(lambda: not page.busy, timeout=5000)
    assert "cancelled" in page.banner.text() and page.btn_check.isEnabled()


def test_install_asks_first_then_starts_the_updater(page, qtbot, monkeypatch):
    from akeno.ui.pages import updates
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.release is not None, timeout=5000)
    answers = iter([False, True])
    monkeypatch.setattr(updates, "confirm", lambda *a, **k: next(answers))
    launched, stopped = [], []
    monkeypatch.setattr(page.svc, "launch", lambda plan: launched.append(plan))
    monkeypatch.setattr(page.svc, "updater_started", lambda proc, since=None: True)
    monkeypatch.setattr(page.win.desktop, "stop", lambda: stopped.append(1))
    quit_calls = []
    app = type("App", (), {"quit": lambda self: quit_calls.append(1)})()
    monkeypatch.setattr(updates, "QApplication", type("Q", (), {"instance": staticmethod(lambda: app)}))
    page._install()                              # "Cancel" in the confirmation: nothing happens
    assert not page.busy and not launched
    page._install()
    qtbot.waitUntil(lambda: bool(launched), timeout=10000)
    plan = launched[0]
    assert plan["version"] == "3.1.0" and Path(plan["content"]).is_file() and stopped and quit_calls
    assert "content_sha256 match the signed values" in rows_of(page.verify_kv)["Package contents"]


def test_page_reports_a_refused_release(page, qtbot, signing):
    key, keys = signing
    forged = envelope(Ed25519PrivateKey.generate(), fields())
    page.svc.fetch = lambda url: forged
    page.check(quiet=False)
    qtbot.waitUntil(lambda: bool(page.banner.text()), timeout=5000)
    assert "could not be verified" in page.banner.text() and page.headline.isHidden()
    assert "refused" in rows_of(page.verify_kv)["Release signature"]
    shot(page, "updates-refused")


def test_options_follow_the_settings(page, qtbot):
    calls = []
    page.check = lambda quiet=False: calls.append(quiet)
    page.ctl.set_setting("check_updates_on_start", False)
    assert not page.opt_start.isChecked()
    page.auto_check(on_start=True)
    page.ctl.set_setting("check_updates_on_start", True)
    page.auto_check(on_start=True)
    page.ctl.set_setting("check_app_updates", False)
    assert not page.opt_start.isEnabled()
    page.auto_check(on_start=True)
    assert calls == [True]
    assert page.opt_ask.isChecked() and not page.opt_ask.isEnabled(), "asking before installing cannot be switched off"


# -- the maintainer's tool -------------------------------------------------------------------------------------------
def test_release_tool_describes_and_verifies_against_the_built_files(tmp_path, signing, capsys):
    import release_v2
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    exe = tmp_path / "AKENO-Gaming-Suite-3.1.0-Setup-x64.exe"
    exe.write_bytes(b"MZ installer")
    assert release_v2.main(["describe", str(pkg), str(exe)]) == 0
    described = json.loads(capsys.readouterr().out)
    assert described["portable"]["file_count"] == 3 and described["portable"]["pe_files"] == 1
    assert described["portable"]["content_sha256"] == reference_content_sha256(pkg)
    assert described["installer"]["sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest()
    now = datetime.now(timezone.utc)
    signed = fields(pkg=pkg, issued_at=iso(now - timedelta(hours=1)), expires_at=iso(now + timedelta(days=29)),
                    release_date=(now - timedelta(hours=1)).date().isoformat(),
                    sha256=described["installer"]["sha256"], size_bytes=described["installer"]["size_bytes"])
    doc = tmp_path / "release.json"
    doc.write_text(json.dumps(envelope(key, signed)))
    keys_file = tmp_path / "keys.json"
    public = tool.public_of(key)
    keys_file.write_text(json.dumps({"keys": [{"kid": tool.kid_of(public), "public_key": public}]}))
    assert release_v2.main(["verify", str(doc), "--zip", str(pkg), "--exe", str(exe), "--keys", str(keys_file)]) == 0
    assert "can install it itself" in capsys.readouterr().out
    seq = signed["sequence"]                             # clients that saw the published release refuse an equal one
    base = ["verify", str(doc), "--zip", str(pkg), "--exe", str(exe), "--keys", str(keys_file)]
    assert release_v2.main(base + ["--previous-sequence", str(seq - 1)]) == 0
    assert release_v2.main(base + ["--previous-sequence", str(seq)]) == 1
    assert f"[FAIL] sequence {seq} after the published {seq}" in capsys.readouterr().out
    exe.write_bytes(b"MZ another installer")             # a file changed after the signing request
    assert release_v2.main(["verify", str(doc), "--zip", str(pkg), "--exe", str(exe), "--keys", str(keys_file)]) == 1
    assert "[FAIL] installer" in capsys.readouterr().out
    assert release_v2.main(["verify", str(doc), "--keys", str(ROOT / "akeno" / "data" / "release-keys.json")]) == 1
    assert "does not trust" in capsys.readouterr().out


def test_release_tool_verifies_the_offline_signature_against_the_tested_build(tmp_path, signing, capsys):
    """Before publishing: the signing request's signed_payload and the offline signature, against the recorded
    describe values of the tested build (no download of the files needed)."""
    import release_v2
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    exe = tmp_path / "AKENO-Gaming-Suite-3.1.0-Setup-x64.exe"
    exe.write_bytes(b"MZ installer")
    assert release_v2.main(["describe", str(pkg), str(exe)]) == 0
    expect = json.loads(capsys.readouterr().out)
    expect["version"] = "3.1.0"
    expect_file = tmp_path / "values.json"
    expect_file.write_text(json.dumps(expect))
    now = datetime.now(timezone.utc)
    signed = fields(pkg=pkg, sequence=2, issued_at=iso(now - timedelta(hours=1)),
                    expires_at=iso(now + timedelta(days=29)), release_date=(now - timedelta(hours=1)).date().isoformat(),
                    sha256=expect["installer"]["sha256"], size_bytes=expect["installer"]["size_bytes"])
    doc = envelope(key, signed)
    payload = tmp_path / "signed_payload.txt"
    payload.write_bytes(doc["signed_payload"].encode("utf-8"))
    public = tool.public_of(key)
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"keys": [{"kid": tool.kid_of(public), "public_key": public}]}))
    base = ["verify", "--payload", str(payload), "--keys", str(keys_file), "--key-id", tool.kid_of(public),
            "--expect", str(expect_file), "--installed", "3.0.0", "--previous-sequence", "1"]
    assert release_v2.main(base + ["--signature", doc["signature"]["value"]]) == 0
    out = capsys.readouterr().out
    assert "[OK  ] signature valid" in out and "[OK  ] sequence 2 after the published 1" in out
    assert "[OK  ] content_sha256" in out and "[OK  ] installer" in out and "FAIL" not in out
    other = base64.urlsafe_b64encode(key.sign(b"something else")).rstrip(b"=").decode()
    assert release_v2.main(base + ["--signature", other]) == 1                 # a signature over other bytes
    assert "signature does not match" in capsys.readouterr().out
    payload.write_bytes(doc["signed_payload"].encode("utf-8") + b"\n")        # one byte more than was signed
    assert release_v2.main(base + ["--signature", doc["signature"]["value"]]) == 1
    capsys.readouterr()
    payload.write_bytes(doc["signed_payload"].encode("utf-8"))
    expect["portable"]["content_sha256"] = "0" * 64                          # not the tested package
    expect_file.write_text(json.dumps(expect))
    assert release_v2.main(base + ["--signature", doc["signature"]["value"]]) == 1
    assert "[FAIL] content_sha256" in capsys.readouterr().out
    manual = envelope(key, fields(pkg=None, sequence=2, issued_at=iso(now - timedelta(hours=1)),
                                  expires_at=iso(now + timedelta(days=29)),
                                  release_date=(now - timedelta(hours=1)).date().isoformat()))
    payload.write_bytes(manual["signed_payload"].encode("utf-8"))            # no portable package: no in-app update
    assert release_v2.main(base + ["--signature", manual["signature"]["value"]]) == 1
    assert "does not install it itself" in capsys.readouterr().out


def test_live_check_reports_what_installed_clients_are_offered(tmp_path, signing, monkeypatch, capsys):
    """tools/live_release_check.py with the production answers replaced by test-signed ones (no network)."""
    import live_release_check
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.1")
    now = datetime.now(timezone.utc)
    when = dict(issued_at=iso(now - timedelta(hours=1)), expires_at=iso(now + timedelta(days=29)),
                release_date=(now - timedelta(hours=1)).date().isoformat())
    new = envelope(key, fields("3.1.1", pkg=pkg, sequence=3, **when))

    def published(pub):
        return envelope(key, fields(pub["version"], sequence=pub["sequence"], sha256=pub["installer_sha256"],
                                    size_bytes=pub["installer_size"],
                                    portable={**portable_info(pkg, pub["version"]), "sha256": pub["portable_sha256"],
                                              "size_bytes": pub["portable_size"]},
                                    install_mode="portable-directory-swap", **when))
    old = {pub["version"]: published(pub) for pub in live_release_check.RELEASES}
    live = {"stable": new}
    answers = {f"{API}/releases/stable": lambda: live["stable"]}
    answers.update({f"{API}/releases/{v}": (lambda doc=doc: doc) for v, doc in old.items()})
    monkeypatch.setattr(service, "fetch_json", lambda url, trusted=None: answers[url]())
    monkeypatch.setattr(manifest, "release_keys", lambda: keys)
    monkeypatch.setattr(distribution, "load", lambda: RM)
    expect = {"version": "3.1.1", "ci_run": 1, "portable": {
        "size_bytes": pkg.stat().st_size, "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest(),
        "file_count": 3, "uncompressed_size_bytes": portable_info(pkg)["uncompressed_size_bytes"],
        "content_sha256": reference_content_sha256(pkg)},
        "installer": {"size_bytes": 123456789, "sha256": "cd" * 32}}
    values = tmp_path / "values.json"
    values.write_text(json.dumps(expect))
    assert live_release_check.main(["--expect", str(values)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    for installed in ("3.0.0", "3.1.0"):
        assert f"AKENO {installed}, first check after installing: KONTO → Updates offers 3.1.1" in out
        assert f"AKENO {installed}, after having seen 3.1.0 (sequence 2): KONTO → Updates offers 3.1.1" in out
        assert f"AKENO {installed} installs it itself after the user confirms" in out
    assert "release 3.0.0 unchanged" in out and "release 3.1.0 unchanged" in out
    live["stable"] = old["3.1.0"]                               # before publishing: 3.1.0 is still live
    assert live_release_check.main(["--expect", str(values)]) == 0
    assert "3.1.1 is not the live stable release yet" in capsys.readouterr().out
    assert live_release_check.main(["--expect", str(values), "--require"]) == 1
    capsys.readouterr()
    live["stable"] = envelope(key, fields("3.1.1", pkg=pkg, sequence=2, **when))   # the published sequence again
    assert live_release_check.main(["--expect", str(values)]) == 1
    assert "same sequence 2" in capsys.readouterr().out


def test_recorded_release_values_of_3_1_0_match_the_release_report():
    values = json.loads((ROOT / "docs" / "release" / "3.1.0" / "release-manager-values.json").read_text("utf-8"))
    report = (ROOT / "docs" / "release" / "3.1.0" / "RELEASE_BERICHT.md").read_text("utf-8")
    assert values["version"] == "3.1.0"
    for kind in ("portable", "installer"):
        assert values[kind]["sha256"] in report
    assert values["portable"]["content_sha256"] in report


def test_the_kept_previous_version_stays_known_after_a_failed_update(tmp_path, signing, akeno_home):
    key, keys = signing
    target = installed(tmp_path, "3.0.0")
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    outcome = apply.run(plan_for(svc, svc.check(), target))
    (svc.folder / "result.json").write_text(json.dumps(outcome))
    assert svc.previous() == "3.0.0", "known before the start is reported"
    svc.after_start()
    assert svc.previous() == "3.0.0"
    (tmp_path / "b").mkdir()
    broken = portable_zip(tmp_path / "b", "3.2.0", body="raise SystemExit(3)\n")
    doc = envelope(key, fields("3.2.0", 8, pkg=broken))
    svc2 = UpdateService(None, dist=RM, keys=keys, fetch=lambda url: doc, version="3.1.0", folder=svc.folder,
                         downloads=LocalDownloads(broken.parent), clock=lambda: NOW.timestamp())
    failed = apply.run(plan_for(svc2, svc2.check(), target))
    assert failed["rolled_back"]
    (svc.folder / "result.json").write_text(json.dumps(failed))
    svc.after_start()
    assert svc.previous() == "3.0.0", "a failed attempt does not forget the kept version"
    plan = svc.rollback_plan()
    assert plan["version"] == "3.0.0" and plan["rollback"]


def test_akeno_stays_open_when_the_updater_does_not_start(page, qtbot, monkeypatch):
    from akeno.ui.pages import updates
    page.check(quiet=False)
    qtbot.waitUntil(lambda: page.release is not None, timeout=5000)
    monkeypatch.setattr(updates, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(updates, "UPDATER_START_S", 1)
    quit_calls = []
    app = type("App", (), {"quit": lambda self: quit_calls.append(1)})()
    monkeypatch.setattr(updates, "QApplication", type("Q", (), {"instance": staticmethod(lambda: app)}))
    monkeypatch.setattr(page.win.desktop, "stop", lambda: None)
    broken = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])   # never writes its journal
    monkeypatch.setattr(page.svc, "launch", lambda plan: broken)
    page._install()
    qtbot.waitUntil(lambda: not page.busy, timeout=15000)
    assert not quit_calls and broken.wait(5) is not None, "the hanging updater was ended, AKENO stays open"
    assert "did not start" in page.banner.text()


def test_updater_started_reads_the_journal(tmp_path):
    svc = UpdateService(None, dist=RM, keys={}, folder=tmp_path)
    since = time.time()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert svc.updater_started(proc, since) is None
        apply.Journal(tmp_path / "journal.json").write(phase="prepared", started=time.time())
        assert svc.updater_started(proc, since) is True
    finally:
        proc.kill()
        proc.wait()
    apply.Journal(tmp_path / "journal.json").write(started=since - 100)
    assert svc.updater_started(proc, since) is False, "ended without a fresh journal"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions exist on Windows only")
def test_junctions_in_a_staged_folder_and_on_the_way_are_refused_on_windows(tmp_path):
    import _winapi
    pkg = portable_zip(tmp_path, "3.1.0")
    content = staging.relative_to_root(staging.extract(pkg, tmp_path / "out"), "AKENO")
    root = tmp_path / "out" / "AKENO"
    (tmp_path / "elsewhere").mkdir()
    _winapi.CreateJunction(str(tmp_path / "elsewhere"), str(root / "planted"))
    with pytest.raises(staging.StagingError, match="link or junction"):
        staging.verify(root, content)
    _winapi.CreateJunction(str(tmp_path / "elsewhere"), str(tmp_path / "staging-junction"))
    with pytest.raises(staging.StagingError, match="link or junction"):
        staging.no_links_on_the_way(tmp_path / "staging-junction" / "x", tmp_path)


# -- the backend contract: content_sha256, strict fields, types --------------------------------------------------------
def zip_of(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return path


def test_content_sha256_encoding_is_pinned(tmp_path):
    """A hand-written vector: the exact JSON bytes the aggregate hashes (compact, keys sorted, no newline)."""
    pkg = zip_of(tmp_path / "v.zip", [("AKENO/b.txt", b"x"), ("AKENO/", b"")])
    text = ('[{"name":"AKENO/b.txt","sha256":"%s","size_bytes":1}]' % hashlib.sha256(b"x").hexdigest())
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert staging.extract(pkg, tmp_path / "out")["content_sha256"] == expected == reference_content_sha256(pkg)


def test_content_sha256_unicode_names_order_and_top_folder(tmp_path):
    files = [("AKENO/zebra.txt", b"1"), ("AKENO/Zulu.txt", b"22"), ("AKENO/ä.txt", b"333"), ("AKENO/alpha.txt", b""),
             ("AKENO/日本/語.txt", "ü".encode()), ("AKENO/_internal/lib.bin", bytes(range(256)))]
    forward = zip_of(tmp_path / "f.zip", files)
    backward = zip_of(tmp_path / "b.zip", list(reversed(files)))
    a = staging.extract(forward, tmp_path / "o1")["content_sha256"]
    b = staging.extract(backward, tmp_path / "o2")["content_sha256"]
    assert a == b == reference_content_sha256(forward), "entry order does not matter; code point order does"
    flat = zip_of(tmp_path / "flat.zip", [(n[len("AKENO/"):], d) for n, d in files])
    assert staging.extract(flat, tmp_path / "o3")["content_sha256"] != a, "the top folder is part of each name"
    records = sorted(n for n, _ in files)        # code point order: upper case, lower case, then ä, then CJK
    assert records.index("AKENO/Zulu.txt") < records.index("AKENO/alpha.txt") < records.index("AKENO/zebra.txt") \
        < records.index("AKENO/ä.txt") < records.index("AKENO/日本/語.txt")


@pytest.mark.parametrize("change,match", [
    (lambda p, info: dict(info, content_sha256="0" * 64), "do not match the signed content_sha256"),
    (lambda p, info: dict(info, file_count=info["file_count"] + 1), "has 3 files, the signed release says 4"),
    (lambda p, info: dict(info, uncompressed_size_bytes=info["uncompressed_size_bytes"] + 1), "unpacks to"),
    (lambda p, info: dict(info, uncompressed_size_bytes=info["uncompressed_size_bytes"] - 1),
     "more data than the release states"),
])
def test_staging_compares_the_real_package_with_its_signed_description(tmp_path, signing, change, match):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg, portable=change(pkg, portable_info(pkg)))
    with pytest.raises(UpdateError, match=match):
        svc.prepare(svc.check())
    assert not (tmp_path / "updates" / "staging" / "3.1.0").exists(), "nothing staged is left behind"


def test_a_correct_package_with_its_aggregate_is_staged(tmp_path, signing):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    svc = service_for(tmp_path, key, keys, pkg)
    plan = svc.prepare(svc.check())
    assert staging.load(plan["content"])["content_sha256"] == reference_content_sha256(pkg)


def _signed_with(key, signed, outer=None):
    return envelope(key, signed, outer=outer)


@pytest.mark.parametrize("mutate,match", [
    (lambda m: m.update(extra_field=1), "does not allow: extra_field"),
    (lambda m: m["portable"].update(note="x"), "portable package information has fields"),
    (lambda m: m["portable"].pop("verified_pe_files"), "portable package information has no verified_pe_files"),
    (lambda m: m["authenticode"].update(signer="x"), "code signature information of the installer is not valid"),
    (lambda m: m.update(schema_version=2.0), "format"),
    (lambda m: m.update(schema_version=True), "format"),          # True == 1 in Python: not schema 1 either
    (lambda m: m.update(updater_protocol=2.0), "updater protocol"),
    (lambda m: m.update(sequence=7.0), "sequence"),
    (lambda m: m.update(sequence=True), "sequence"),
    (lambda m: m.update(size_bytes=True), "size_bytes"),
    (lambda m: m["portable"].update(file_count=3.0), "file_count"),
    (lambda m: m.update(request_id="ABC"), "request_id"),
    (lambda m: m.update(release_date="2026-13-40"), "release_date"),
    (lambda m: m.update(release_date="02.11.2026"), "release_date"),
    (lambda m: m.update(release_notes=""), "release_notes"),
    (lambda m: m.update(release_notes="x" * 8001), "release_notes"),
    (lambda m: m.update(minimum_supported_version="3.0"), "minimum"),
    (lambda m: m.update(authenticode={"verified": True, "status": "not_signed", "publisher_certificate_sha256": None}),
     "contradicts itself"),
    (lambda m: m.update(authenticode={"verified": False, "status": "verified",
                                      "publisher_certificate_sha256": "ab" * 32}), "contradicts itself"),
    (lambda m: m["portable"].update(authenticode={"verified": True, "status": "invalid",
                                                  "publisher_certificate_sha256": None}), "contradicts itself"),
    (lambda m: m.update(authenticode={"verified": False, "status": "unknown"}), "code signature information"),
    (lambda m: m.update(authenticode={"verified": False, "status": "not_signed",
                                      "publisher_certificate_sha256": "XYZ"}), "code signature information"),
    (lambda m: m.update(authenticode=None), "code signature information"),
])
def test_signed_fields_follow_the_backend_schema_exactly(signing, tmp_path, mutate, match):
    key, keys = signing
    m = fields(pkg=portable_zip(tmp_path, "3.1.0"))
    m = json.loads(json.dumps(m))
    mutate(m)
    with pytest.raises(manifest.ManifestError, match=match):
        parse(envelope(key, m), keys)


def test_the_outer_envelope_is_strict_too(signing, tmp_path):
    key, keys = signing
    good = fields(pkg=portable_zip(tmp_path, "3.1.0"))
    doc = envelope(key, good)
    doc["unexpected"] = True
    with pytest.raises(manifest.ManifestError, match="release field unexpected is not signed"):
        parse(doc, keys)
    doc = envelope(key, good)
    doc["schema_version"] = 2.0
    with pytest.raises(manifest.ManifestError, match="unknown format"):
        parse(doc, keys)
    doc = envelope(key, good)
    doc["signature"]["extra"] = "x"
    with pytest.raises(manifest.ManifestError, match="not signed by AKENO"):
        parse(doc, keys)
    doc = envelope(key, good, outer=dict(good, sequence=7.0))           # outer 7.0 vs signed 7
    with pytest.raises(manifest.ManifestError, match="sequence differs from the signed value"):
        parse(doc, keys)
    doc = envelope(key, good)
    doc["signature"]["key_id"] = "D6139235B220A4AA"
    with pytest.raises(manifest.ManifestError, match="signature is not readable"):
        parse(doc, keys)


def test_authenticode_is_optional_but_never_misread(signing, tmp_path, monkeypatch):
    key, keys = signing
    pkg = portable_zip(tmp_path, "3.1.0")
    monkeypatch.setattr(service, "frozen", lambda: True)
    monkeypatch.setattr(service, "install_dir", lambda: tmp_path)
    svc = UpdateService(None, dist=RM, keys=keys, version="3.0.0", folder=tmp_path / "u")
    signed = {"verified": True, "status": "verified", "publisher_certificate_sha256": "cd" * 32}
    release = parse(envelope(key, fields(pkg=pkg, authenticode=signed,
                                         portable=portable_info(pkg, authenticode=signed, verified_pe_files=1))), keys)
    assert release.publisher_sha256 == "cd" * 32 and svc.can_install(release)[0]
    unsigned = parse(envelope(key, fields(pkg=pkg)), keys)
    assert unsigned.v2.portable.authenticode.status == "not_signed" and svc.can_install(unsigned)[0]
    invalid = {"verified": False, "status": "invalid", "publisher_certificate_sha256": None}
    bad = parse(envelope(key, fields(pkg=pkg, portable=portable_info(pkg, authenticode=invalid))), keys)
    assert not svc.can_install(bad)[0], "an invalid code signature is never treated as valid"
