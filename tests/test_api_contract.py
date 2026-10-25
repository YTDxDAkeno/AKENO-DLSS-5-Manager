"""The client against the Release Manager's published contract (AKENO_3_0_FINAL_API_CONTRACT, 2026-10-25).

``tests/fixtures/api_contract`` holds only public, reviewed test data from the contract bundle (no backend source):

* the five official ``content_sha256`` vectors (``vectors/*/portable.zip``, the exact canonical record bytes and
  ``vector-index.json`` with the expected values), covering UTF-8 names, ZIP entry order, a top folder kept in the
  name, and Unicode code point order against UTF-16 order;
* the official example envelope, signed with a **test-only** key (``example-test-public-key.json``). That key is
  passed to the parser here as an argument; it is not and must never be one of the build's release keys
  (``tools/check_build_config.py`` refuses a build that trusts or carries it).

The negative cases are the ones the contract lists as accepted by the run-182 client but refused by the backend
(``validation-strictness-differences``), and the further rules of the backend's ``release_protocol.validate`` and
``verify``. Every one of them is signed with a valid (disposable) key, so each test shows a rule, not a signature
failure.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno import distribution
from akeno.licensing import crypto
from akeno.updater import api_v2, manifest, service, staging
from akeno.updater.service import UpdateError, UpdateService

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "api_contract"
INDEX = json.loads((FIXTURE / "vector-index.json").read_bytes())
EXAMPLE = json.loads((FIXTURE / "example-test-envelope.json").read_bytes())
TEST_KEY = json.loads((FIXTURE / "example-test-public-key.json").read_bytes())
PRODUCTION_KID = "d6139235b220a4aa"
NOW = datetime(2026, 10, 25, 12, 1, tzinfo=timezone.utc)          # the example is valid from 12:00 to 11-24 12:00
DIST = distribution.from_dict({"update_source": "akeno-api", "release_api": "https://api.akeno.online/api/v1",
                               "release_page": "https://akeno.online/downloads/latest",
                               "download_hosts": ["akeno.online"]})
# the reviewed files, as in the contract bundle's SHA256SUMS.txt
FIXTURE_SHA256 = {
    "example-test-envelope.json": "c7586de579ff27c556e14823764b8df609e18789121ca85ab4ff952b64965666",
    "example-test-public-key.json": "b0cf233386f765598ee92165546bfb3acd74e5bf5c00163b92f23a83884b0e1e",
    "vector-index.json": "6036f4bcd771b2af80a6bab4e6441ca5efa1df45dc063677da034e0a036b55cb",
    "vectors/01-text-and-unicode/portable.zip": "62439bec59cf38f712ba8cbfacfd62df19742d70ffdd286fe7e2274e3ffcbc39",
    "vectors/02-valid-portable/portable.zip": "3c0802ee9881c7ac47740a0702c7567839cfe71f30fca411b3ec723ad7784aed",
    "vectors/03-reordered-same-contents/portable.zip":
        "47cb36001ad9ac31eb3d81f41b0b9309126dcd5698ca842f5f023b28d67632f1",
    "vectors/04-root-directory-prefix/portable.zip": "8c182b963803088d93164c343ac87d974780cf653fc431c8de269e5e1a50f84a",
    "vectors/05-unicode-scalar-sort/portable.zip": "dc6fc61f7fa59feaadc39d5a29622f02fbc32be4d879af20236242515548a4ee",
}
for _case in INDEX:          # each canonical-records file is the preimage of its content_sha256
    FIXTURE_SHA256[f"vectors/{_case['case']}/canonical-records.utf8.json"] = _case["content_sha256"]


def canonical(value) -> bytes:
    """``canonical`` of the backend: what the Release Manager signs."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class Signer:
    """A disposable key: signs exactly like the Release Manager (canonical UTF-8 payload, base64url signature)."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.kid = hashlib.sha256(raw).hexdigest()[:16]
        self.keys = {self.kid: self.key.public_key()}

    def envelope(self, m: dict, payload: str | None = None) -> dict:
        text = canonical(m).decode() if payload is None else payload
        return {**m, "signed_payload": text, "signature": {"algorithm": "Ed25519", "key_id": self.kid,
                                                           "value": b64url(self.key.sign(text.encode("utf-8")))}}


@pytest.fixture
def signer():
    return Signer()


def payload() -> dict:
    """The signed values of the official example (a valid 3.0.1 release with the portable package of vector 02)."""
    return json.loads(EXAMPLE["signed_payload"])


def parse(doc, keys, now=NOW):
    return manifest.parse_api(doc, keys, DIST, now.timestamp())


def accepted(doc, keys, now=NOW) -> bool:
    try:
        parse(doc, keys, now)
    except manifest.ManifestError:
        return False
    return True


def test_the_fixture_files_are_the_reviewed_ones():
    for name, digest in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name
    assert sorted(p.relative_to(FIXTURE).as_posix() for p in FIXTURE.rglob("*") if p.is_file()) == \
        sorted([*FIXTURE_SHA256, "README.md"]), "nothing else (in particular no backend source) is in the fixture"
    assert [c["case"] for c in INDEX] == ["01-text-and-unicode", "02-valid-portable", "03-reordered-same-contents",
                                         "04-root-directory-prefix", "05-unicode-scalar-sort"]


# -- content_sha256: the five official vectors -----------------------------------------------------------------------
@pytest.mark.parametrize("case", INDEX, ids=[c["case"] for c in INDEX])
def test_content_sha256_vectors(case, tmp_path):
    folder = FIXTURE / "vectors" / case["case"]
    archive = folder / "portable.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == case["zip_sha256"]
    assert archive.stat().st_size == case["zip_size_bytes"]
    content = staging.extract(archive, tmp_path / "x")
    # the client's own unpacking (real bytes written) gives exactly the official values
    assert content["content_sha256"] == case["content_sha256"]
    assert content["file_count"] == case["file_count"]
    assert content["bytes"] == case["uncompressed_size_bytes"]
    records = [{"name": name, "sha256": row["sha256"], "size_bytes": row["size"]}
               for name, row in content["files"].items()]
    assert sorted(records, key=lambda r: r["name"]) == sorted(case["records"], key=lambda r: r["name"])
    # byte for byte the canonical serialisation (UTF-8, not ASCII-escaped, keys name/sha256/size_bytes)
    exact = (folder / "canonical-records.utf8.json").read_bytes()
    assert staging.canonical_records(records) == exact == case["canonical_utf8_text"].encode("utf-8")
    assert exact.hex() == case["canonical_utf8_hex"]
    assert staging.content_sha256(case["records"]) == case["content_sha256"]
    for row in case["file_inputs"]:                          # the files really hold the documented bytes
        data = bytes.fromhex(row["utf8_hex_of_contents"])
        assert (tmp_path / "x" / row["path"]).read_bytes() == data


def test_vectors_entry_order_top_folder_and_code_point_order():
    by_case = {c["case"]: c for c in INDEX}
    # ZIP entry order does not matter
    assert by_case["02-valid-portable"]["content_sha256"] == by_case["03-reordered-same-contents"]["content_sha256"]
    with zipfile.ZipFile(FIXTURE / "vectors" / "03-reordered-same-contents" / "portable.zip") as z:
        assert [i.filename for i in z.infolist()][0] == "empty.dat"
    # a top folder is part of every name and of the hash; stripping it gives another value
    root = by_case["04-root-directory-prefix"]
    assert all(r["name"].startswith("AKENO/") for r in root["records"])
    stripped = [dict(r, name=r["name"][len("AKENO/"):]) for r in root["records"]]
    assert staging.content_sha256(stripped) == by_case["02-valid-portable"]["content_sha256"] != \
        root["content_sha256"]
    # code point order: U+E000 sorts before U+10000; UTF-16 code unit order (D800 DC00 < E000) would not
    scalar = by_case["05-unicode-scalar-sort"]
    names = [r["name"] for r in scalar["records"]]
    assert names.index("unicode/.txt") < names.index("unicode/\U00010000.txt")
    utf16 = sorted(scalar["records"], key=lambda r: r["name"].encode("utf-16-be"))
    wrong = hashlib.sha256(json.dumps(utf16, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                           .encode()).hexdigest()
    assert wrong != scalar["content_sha256"] == staging.content_sha256(scalar["records"])
    utf8 = sorted(scalar["records"], key=lambda r: r["name"].encode("utf-8"))
    assert staging.content_sha256(utf8) == scalar["content_sha256"], "UTF-8 byte order is the same as code points"


def stage(tmp_path, signer, case: str, **portable_changes):
    """Stage an official vector through UpdateService.stage, described by a release signed like production."""
    found = next(c for c in INDEX if c["case"] == case)
    m = payload()
    m["portable"].update(sha256=found["zip_sha256"], size_bytes=found["zip_size_bytes"],
                         file_count=found["file_count"], uncompressed_size_bytes=found["uncompressed_size_bytes"],
                         content_sha256=found["content_sha256"])
    m["portable"].update(portable_changes)
    release = parse(signer.envelope(m), signer.keys)
    svc = UpdateService(None, dist=DIST, keys=signer.keys, folder=tmp_path / "updates", version="3.0.0",
                        clock=lambda: NOW.timestamp())
    return svc, release, svc.stage(FIXTURE / "vectors" / case / "portable.zip", release)


@pytest.mark.parametrize("case", ["02-valid-portable", "03-reordered-same-contents", "04-root-directory-prefix",
                                  "05-unicode-scalar-sort"])
def test_the_updater_stages_the_official_packages(case, tmp_path, signer):
    svc, release, root = stage(tmp_path, signer, case)
    assert (root / "AKENO.exe").is_file()
    content = staging.load(svc.content_path(release))
    assert content["release"]["content_sha256"] == next(c for c in INDEX if c["case"] == case)["content_sha256"]
    assert "AKENO.exe" in content["files"], "the top folder is removed only after the comparison"


@pytest.mark.parametrize("change,match", [
    (dict(content_sha256="0" * 64), "content_sha256"),                     # the contract's test, now refused
    (dict(content_sha256=INDEX[3]["content_sha256"]), "content_sha256"),   # the value with the top folder
    (dict(file_count=3), "files"),
    (dict(uncompressed_size_bytes=1047), "unpacks to"),
])
def test_staging_refuses_a_package_that_differs_from_its_signed_contents(tmp_path, signer, change, match):
    with pytest.raises(UpdateError, match=match):
        stage(tmp_path, signer, "02-valid-portable", **change)
    assert not (tmp_path / "updates" / "staging" / "3.0.1").exists(), "nothing staged is left behind"


# -- Ed25519: the official public test signature -----------------------------------------------------------------------
def test_the_official_example_verifies_with_the_test_key_only():
    test_keys = {TEST_KEY["key_id"]: crypto.public_key(TEST_KEY["public_key"])}
    assert crypto.kid_for(TEST_KEY["public_key"]) == TEST_KEY["key_id"] == EXAMPLE["signature"]["key_id"]
    assert TEST_KEY["production_key"] is False and TEST_KEY["key_id"] != PRODUCTION_KID
    release = parse(EXAMPLE, test_keys)
    d = release.v2
    assert (release.release, d.sequence, d.install_mode, d.request_id) == (
        "3.0.1", 1, "portable-directory-swap", "0123456789abcdef0123456789abcdef")
    assert release.notes == "Public contract fixture: Größe ✓. Never publish this fixture."
    assert release.file("portable").url == "https://akeno.online/downloads/3.0.1/AKENO-Portable.zip"
    assert release.file("installer").url == "https://akeno.online/downloads/3.0.1/AKENO-Setup.exe"
    assert d.portable.content_sha256 == INDEX[1]["content_sha256"]
    # the signature is over exactly these UTF-8 bytes: the canonical form of the signed values
    assert EXAMPLE["signed_payload"].encode("utf-8") == canonical(payload())
    # the production keyring of this build does not know the test key: refused
    production = manifest.release_keys()
    assert list(production) == [PRODUCTION_KID] and TEST_KEY["key_id"] not in production
    with pytest.raises(manifest.ManifestError, match="does not trust"):
        parse(EXAMPLE, production)
    # one changed byte, an expired or not yet valid time: refused
    changed = dict(EXAMPLE, signed_payload=EXAMPLE["signed_payload"] + " ")
    with pytest.raises(manifest.ManifestError, match="signature does not match"):
        parse(changed, test_keys)
    with pytest.raises(manifest.ManifestError, match="expired"):
        parse(EXAMPLE, test_keys, datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc))
    with pytest.raises(manifest.ManifestError, match="issued in the future"):
        parse(EXAMPLE, test_keys, datetime(2026, 10, 25, 11, 54, 59, tzinfo=timezone.utc))


def test_the_signature_is_checked_over_the_exact_bytes_and_outer_values(signer):
    doc = signer.envelope(payload())
    assert accepted(doc, signer.keys)
    assert not accepted(dict(doc, signed_payload=doc["signed_payload"] + " "), signer.keys)
    outer = copy.deepcopy(doc)
    outer["portable"]["sha256"] = "0" * 64
    assert not accepted(outer, signer.keys)


@pytest.mark.parametrize("field,value", [("sequence", 2), ("issued_at", "2026-10-25T11:00:00+00:00"),
                                         ("expires_at", "2026-11-23T12:00:00+00:00"),
                                         ("install_mode", "manual-installer"), ("updater_protocol", 3),
                                         ("portable", None)])
def test_every_security_field_is_covered_by_the_signature(signer, field, value):
    doc = signer.envelope(payload())
    changed = dict(payload(), **{field: value})
    doc.update(changed)
    doc["signed_payload"] = canonical(changed).decode()       # the old signature over new bytes
    assert not accepted(doc, signer.keys)


def test_duplicate_signed_fields_are_refused(signer):
    raw = canonical(payload()).decode()
    with pytest.raises(manifest.ManifestError, match="twice"):
        parse(signer.envelope(payload(), raw[:-1] + ',"sequence":2}'), signer.keys)


# -- the contract's documented strictness differences: now refused like the backend ----------------------------------
def strictness_cases() -> dict:
    cases = {}
    for name in ("request_id", "release_date", "release_notes", "published_at", "minimum_supported_version",
                 "authenticode"):
        m = payload()
        m.pop(name)
        cases[f"missing_{name}"] = m
    cases["float_schema_version"] = dict(payload(), schema_version=2.0)
    cases["issued_more_than_5min_future"] = dict(payload(), issued_at="2026-10-25T12:11:00+00:00",
                                                 published_at="2026-10-25T12:11:00+00:00")
    cases["validity_over_30days"] = dict(payload(), expires_at="2026-12-01T12:00:00+00:00")
    cases["expiry_exactly_now"] = dict(payload(), issued_at="2026-10-25T11:01:00+00:00",
                                       published_at="2026-10-25T11:01:00+00:00", expires_at=NOW.isoformat())
    m = payload()
    m["portable"]["extra_unknown"] = "ignored"
    cases["unknown_portable_member"] = m
    cases["minimum_above_release"] = dict(payload(), minimum_supported_version="9.0.0")
    cases["different_trusted_installer_path"] = dict(payload(), download_url="https://akeno.online/other/renamed.exe")
    return cases


@pytest.mark.parametrize("name", [*strictness_cases(), "unsigned_unknown_outer_field"])
def test_the_documented_strictness_differences_are_closed(signer, name):
    if name == "unsigned_unknown_outer_field":
        doc = dict(signer.envelope(payload()), unsigned_unknown="ignored")
    else:
        doc = signer.envelope(strictness_cases()[name])
    assert not accepted(doc, signer.keys), f"{name}: the backend refuses it, so does the client now"


# -- further rules of release_protocol.validate / verify -------------------------------------------------------------
def without_portable(**changes) -> dict:
    m = payload()
    m.pop("portable")
    m.update(install_mode="manual-installer")
    m.update(changes)
    return m


def portable(**changes) -> dict:
    m = payload()
    m["portable"].update(changes)
    return m


VERIFIED = {"verified": True, "status": "verified", "publisher_certificate_sha256": "ab" * 32}

RULES = {
    # name: (signed values, accepted by the backend)
    "example": (payload(), True),
    "installer_only": (without_portable(), True),
    "installer_only_portable_null": (dict(without_portable(), portable=None), True),
    "installer_only_unattended": (without_portable(unattended_install_allowed=True), False),
    "installer_only_swap_mode": (without_portable(install_mode="portable-directory-swap"), False),
    "portable_with_manual_mode": (dict(payload(), install_mode="manual-installer"), False),
    "portable_unattended_allowed": (dict(payload(), unattended_install_allowed=True), True),
    "unknown_install_mode": (dict(payload(), install_mode="silent"), False),
    "protocol_1": (dict(payload(), updater_protocol=1), False),
    "protocol_3": (dict(payload(), updater_protocol=3), False),
    "protocol_float": (dict(payload(), updater_protocol=2.0), False),
    "schema_bool": (dict(payload(), schema_version=True), False),
    "sequence_bool": (dict(payload(), sequence=True), False),
    "sequence_max": (dict(payload(), sequence=2 ** 53 - 1), True),
    "sequence_over_max": (dict(payload(), sequence=2 ** 53), False),
    "sequence_zero": (dict(payload(), sequence=0), False),
    "version_leading_zero": (dict(payload(), version="03.0.1"), False),
    "version_trailing_newline": (dict(payload(), version="3.0.1\n"), False),
    "version_prerelease": (dict(payload(), version="3.0.1-beta.1"), False),
    "minimum_equal_to_version": (dict(payload(), minimum_supported_version="3.0.1"), True),
    "minimum_older": (dict(payload(), minimum_supported_version="2.9.9"), True),
    "minimum_not_a_version": (dict(payload(), minimum_supported_version="3"), False),
    "request_id_upper_case": (dict(payload(), request_id="0123456789ABCDEF0123456789ABCDEF"), False),
    "request_id_newline": (dict(payload(), request_id="0123456789abcdef0123456789abcdef\n"), False),
    "published_same_instant_other_text": (dict(payload(), published_at="2026-10-25T12:00:00Z"), False),
    "issued_z_suffix": (dict(payload(), issued_at="2026-10-25T12:00:00Z", published_at="2026-10-25T12:00:00Z"),
                        True),
    "issued_no_zone": (dict(payload(), issued_at="2026-10-25T12:00:00", published_at="2026-10-25T12:00:00"), False),
    "issued_other_zone": (dict(payload(), issued_at="2026-10-25T13:00:00+01:00",
                               published_at="2026-10-25T13:00:00+01:00"), False),
    "issued_over_40_chars": (dict(payload(), issued_at="2026-10-25T12:00:00.000000000000000000+00:00",
                                  published_at="2026-10-25T12:00:00.000000000000000000+00:00"), False),
    "issued_40_chars": (dict(payload(), issued_at="2026-10-25T12:00:00.00000000000000+00:00",
                             published_at="2026-10-25T12:00:00.00000000000000+00:00"), True),
    "issued_5min_ahead": (dict(payload(), issued_at="2026-10-25T12:06:00+00:00",
                               published_at="2026-10-25T12:06:00+00:00"), True),
    "validity_exactly_1h": (dict(payload(), expires_at="2026-10-25T13:00:00+00:00"), True),
    "validity_under_1h": (dict(payload(), expires_at="2026-10-25T12:59:59+00:00"), False),
    "validity_exactly_30d": (dict(payload(), expires_at="2026-11-24T12:00:00+00:00"), True),
    "release_date_tomorrow": (dict(payload(), release_date="2026-10-26"), False),
    "release_date_basic_format": (dict(payload(), release_date="20261025"), False),
    "release_date_not_a_date": (dict(payload(), release_date="2026-02-30"), False),
    "release_date_earlier": (dict(payload(), release_date="2026-10-01"), True),
    "release_notes_blank": (dict(payload(), release_notes=" \n\t "), False),
    "release_notes_8000": (dict(payload(), release_notes="ä" * 8000), True),
    "release_notes_8001": (dict(payload(), release_notes="x" * 8001), False),
    "installer_size_max": (dict(payload(), size_bytes=536870912), True),
    "installer_size_over_max": (dict(payload(), size_bytes=536870913), False),
    "installer_sha_upper_case": (dict(payload(), sha256=payload()["sha256"].upper()), False),
    "installer_url_other_version": (dict(payload(), download_url=(
        "https://akeno.online/downloads/3.0.0/AKENO-Setup.exe")), False),
    "installer_url_ci_name": (dict(payload(), download_url=(
        "https://akeno.online/downloads/3.0.1/AKENO-Gaming-Suite-3.0.1-Setup-x64.exe")), False),
    "installer_url_query": (dict(payload(), download_url="https://akeno.online/downloads/3.0.1/AKENO-Setup.exe?a=1"),
                            False),
    "installer_url_other_host": (dict(payload(), download_url="https://downloads.akeno.online/downloads/3.0.1/"
                                                              "AKENO-Setup.exe"), False),
    "portable_url_ci_name": (portable(download_url="https://akeno.online/downloads/3.0.1/"
                                                   "AKENO-Gaming-Suite-3.0.1-portable-x64.zip"), False),
    "portable_url_other_version": (portable(download_url="https://akeno.online/downloads/3.0.0/AKENO-Portable.zip"),
                                   False),
    "portable_missing_member": ({**payload(), "portable": {k: v for k, v in payload()["portable"].items()
                                                           if k != "pe_files"}}, False),
    "portable_size_over_max": (portable(size_bytes=536870913), False),
    "portable_files_over_max": (portable(file_count=4097), False),
    "portable_unpacked_max": (portable(uncompressed_size_bytes=2147483648), True),
    "portable_unpacked_over_max": (portable(uncompressed_size_bytes=2147483649), False),
    "portable_pe_zero": (portable(pe_files=0), False),
    "portable_pe_over_max": (portable(pe_files=513), False),
    "portable_more_verified_than_pe": (portable(verified_pe_files=2), False),
    "portable_more_pe_than_files": (portable(pe_files=5, verified_pe_files=0), True),   # no such backend rule
    "portable_content_upper_case": (portable(content_sha256=payload()["portable"]["content_sha256"].upper()), False),
    "portable_verified_all_signed": (portable(authenticode=VERIFIED, verified_pe_files=1), True),
    "portable_verified_not_all_signed": (portable(authenticode=VERIFIED, verified_pe_files=0), False),
    "authenticode_verified": (dict(payload(), authenticode=VERIFIED), True),
    "authenticode_verified_without_pin": (dict(payload(), authenticode=dict(VERIFIED,
                                                                            publisher_certificate_sha256=None)), False),
    "authenticode_verified_other_status": (dict(payload(), authenticode=dict(VERIFIED, status="mixed")), False),
    "authenticode_status_verified_but_not_verified": (dict(payload(), authenticode=dict(VERIFIED, verified=False)),
                                                      False),
    "authenticode_unverified_with_pin": (dict(payload(), authenticode={
        "verified": False, "status": "present_unverified", "publisher_certificate_sha256": "ab" * 32}), False),
    "authenticode_invalid": (dict(payload(), authenticode={
        "verified": False, "status": "invalid", "publisher_certificate_sha256": None}), True),
    "authenticode_missing_pin_field": (dict(payload(), authenticode={"verified": False, "status": "not_signed"}),
                                       False),
    "authenticode_extra_field": (dict(payload(), authenticode=dict(payload()["authenticode"], signer="x")), False),
    "authenticode_verified_number": (dict(payload(), authenticode=dict(payload()["authenticode"], verified=0)),
                                     False),
    "authenticode_unknown_status": (dict(payload(), authenticode=dict(payload()["authenticode"], status="ok")), False),
    "wrong_product": (dict(payload(), product="other"), False),
    "wrong_platform": (dict(payload(), platform="windows-arm64"), False),
    "wrong_channel": (dict(payload(), channel="beta"), False),
    "unknown_signed_field": (dict(payload(), extra="x"), False),
}


@pytest.mark.parametrize("name", list(RULES))
def test_validation_rules_of_the_backend(signer, name):
    m, backend = RULES[name]
    assert accepted(signer.envelope(m), signer.keys) is backend


def test_signature_envelope_rules(signer):
    doc = signer.envelope(payload())
    value = doc["signature"]["value"]
    raw = base64.urlsafe_b64decode(value + "==")
    for bad in (value + "==", raw.hex(), base64.b64encode(raw).decode(), value[:-1], value + "A", " " + value):
        assert not accepted(dict(doc, signature=dict(doc["signature"], value=bad)), signer.keys), bad
    for sig in (dict(doc["signature"], algorithm="ed25519"), dict(doc["signature"], extra=1),
                {k: v for k, v in doc["signature"].items() if k != "algorithm"},
                dict(doc["signature"], key_id=signer.kid.upper())):
        assert not accepted(dict(doc, signature=sig), signer.keys)
    # base64url of the JSON instead of the JSON text: refused (the Release Manager signs the text)
    text = canonical(payload())
    encoded = signer.envelope(payload(), b64url(text))
    assert not accepted(encoded, signer.keys)
    # at most 64 KiB of signed UTF-8 (whitespace after the JSON is still valid JSON)
    assert accepted(signer.envelope(payload(), text.decode() + " " * (65536 - len(text))), signer.keys)
    assert not accepted(signer.envelope(payload(), text.decode() + " " * (65537 - len(text))), signer.keys)
    # an outer field that is not signed, even with a null value
    assert not accepted(dict(signer.envelope(without_portable()), portable=None), signer.keys)
    assert accepted(signer.envelope(without_portable()), signer.keys)
    # a key that is offered under another key's id
    other = Signer()
    assert not accepted(doc, {other.kid: signer.key.public_key()})
    assert not accepted(doc, {signer.kid: other.key.public_key()})


def test_release_date_follows_the_utc_day(signer):
    m = dict(payload(), release_date="2026-10-26", issued_at="2026-10-25T23:59:00+00:00",
             published_at="2026-10-25T23:59:00+00:00", expires_at="2026-11-01T00:00:00+00:00")
    doc = signer.envelope(m)
    assert not accepted(doc, signer.keys, datetime(2026, 10, 25, 23, 59, 30, tzinfo=timezone.utc))
    assert accepted(doc, signer.keys, datetime(2026, 10, 26, 0, 0, 1, tzinfo=timezone.utc))


def test_the_sequence_and_version_rules_against_replays(tmp_path, signer):
    svc = UpdateService(None, dist=DIST, keys=signer.keys, folder=tmp_path / "state", version="3.0.0",
                        clock=lambda: NOW.timestamp())
    m = dict(payload(), sequence=2)
    svc._anti_replay(parse(signer.envelope(m), signer.keys))
    with pytest.raises(manifest.ManifestError, match="replay"):
        svc._anti_replay(parse(signer.envelope(dict(m, sequence=1)), signer.keys))
    with pytest.raises(manifest.ManifestError, match="same sequence"):
        svc._anti_replay(parse(signer.envelope(dict(m, request_id="f" * 32)), signer.keys))


# -- unpacking: entries as the Release Manager counts them -----------------------------------------------------------
def test_a_directory_is_only_an_entry_ending_with_a_slash(tmp_path):
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("AKENO.exe", b"MZ")
        z.writestr("folder/", b"")
        dos = zipfile.ZipInfo("marked-as-folder")
        dos.external_attr = 0x10                              # MS-DOS directory attribute, but no trailing "/"
        z.writestr(dos, b"")
    content = staging.extract(archive, tmp_path / "x")
    assert set(content["files"]) == {"AKENO.exe", "marked-as-folder"}, "is_dir() decides, as in the backend"
    with zipfile.ZipFile(archive) as z:
        records = [{"name": i.filename, "sha256": hashlib.sha256(z.read(i)).hexdigest(), "size_bytes": i.file_size}
                   for i in z.infolist() if not i.is_dir()]
    assert content["content_sha256"] == staging.content_sha256(records)


@pytest.mark.parametrize("name", ["dir\\file.txt", "Grüße.txt", "folder\\"])
def test_names_the_release_manager_refuses_are_refused(tmp_path, name):
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("AKENO.exe", b"MZ")
        z.writestr(zipfile.ZipInfo(name), b"x")
    with pytest.raises(staging.StagingError, match="refused path"):
        staging.extract(archive, tmp_path / "x")


# -- Authenticode: the signed publisher pin ---------------------------------------------------------------------------
def test_a_signed_publisher_pin_is_required_of_the_new_program(monkeypatch, tmp_path):
    from akeno.security import authenticode
    Sig = authenticode.SignatureInfo
    pin = "ab" * 32
    table = {"running.exe": Sig(True, False, None, "")}
    monkeypatch.setattr(authenticode, "verify", lambda path: table[Path(path).name])
    new, running = tmp_path / "new.exe", tmp_path / "running.exe"
    table["new.exe"] = Sig(True, True, "AKENO", "", pin)
    assert "the certificate the release names" in service.signature_check(new, running, pin)
    table["running.exe"] = Sig(True, True, "Other", "")
    with pytest.raises(UpdateError, match="not by 'Other'"):
        service.signature_check(new, running, pin)              # the signer rule of a signed build still applies
    table["running.exe"] = Sig(True, False, None, "")
    table["new.exe"] = Sig(True, True, "AKENO", "", "cd" * 32)
    with pytest.raises(UpdateError, match="not signed with the certificate the release names"):
        service.signature_check(new, running, pin)
    table["new.exe"] = Sig(True, False, None, "", None)
    with pytest.raises(UpdateError, match="not signed with the certificate"):
        service.signature_check(new, running, pin)
    table["new.exe"] = Sig(False, False, None, "")
    with pytest.raises(UpdateError, match="only Windows can check"):
        service.signature_check(new, running, pin)
    # an unsigned release (no pin) keeps the old rule: this build is not code-signed, so nothing is demanded
    table["new.exe"] = Sig(True, False, None, "")
    assert "not code-signed" in service.signature_check(new, running)


def test_staging_passes_the_signed_pin_to_the_code_signature_check(monkeypatch, tmp_path, signer):
    seen = []
    monkeypatch.setattr(service, "frozen", lambda: True)
    monkeypatch.setattr(service.sys, "platform", "win32")

    def check(new_exe, running_exe=None, pin=None):
        seen.append(pin)
        if pin == "ef" * 32:
            raise UpdateError("the new AKENO.exe is not signed with the certificate the release names")
        return "ok"
    monkeypatch.setattr(service, "signature_check", check)
    stage(tmp_path / "a", signer, "02-valid-portable")
    stage(tmp_path / "b", signer, "02-valid-portable", authenticode=dict(VERIFIED), verified_pe_files=1)
    assert seen == [None, "ab" * 32]
    with pytest.raises(UpdateError, match="certificate the release names"):
        stage(tmp_path / "c", signer, "02-valid-portable",
              authenticode=dict(VERIFIED, publisher_certificate_sha256="ef" * 32), verified_pe_files=1)
    assert not (tmp_path / "c" / "updates" / "staging" / "3.0.1").exists()


@pytest.mark.skipif(not __import__("sys").platform.startswith("win"), reason="Authenticode needs Windows")
def test_the_leaf_certificate_hash_is_the_one_windows_reports():
    """On Windows: the SHA-256 the client reads from the verified signature is the DER hash of the signer
    certificate, as PowerShell's Get-AuthenticodeSignature reports it (python.exe is code-signed)."""
    import subprocess
    import sys
    from akeno.security import authenticode
    info = authenticode.verify(sys.executable)
    if not info.valid:
        pytest.skip("this python.exe is not code-signed")
    script = ("$c = (Get-AuthenticodeSignature -LiteralPath $env:AKENO_EXE).SignerCertificate; "
              "-join ([System.Security.Cryptography.SHA256]::Create().ComputeHash($c.RawData) "
              "| ForEach-Object { $_.ToString('x2') })")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                         timeout=120, env={**__import__("os").environ, "AKENO_EXE": sys.executable}, check=True)
    assert info.leaf_sha256 == out.stdout.strip() and len(info.leaf_sha256) == 64
