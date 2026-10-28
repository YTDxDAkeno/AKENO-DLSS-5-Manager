"""Independent proof cryptography + explicit measurement stand-ins. No live country or external TLS claim."""
import base64
import hashlib
import json
import os
import secrets
import sys
import types
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno_server.auth.tokens import b64e
from akeno_server.licensing.signing import raw_public, kid_for
from akeno_server.vpn.probe import measure, PinnedCountryDatabase, private_probe_key, BoundHTTPS
from akeno_server.vpn.proofs import verify_proof
from akeno_server.vpn.store import VPNError


class FakeObserver:
    def __init__(self):
        self.sources = []

    def observe(self, source):
        self.sources.append(source)
        return '93.184.215.14' if ':' not in source else '2606:4700:4700::1111'


class FakeDatabase:
    reference = 'SYNTHETIC TEST DATABASE; no actual GeoIP assertion'

    def country(self, address):
        return 'DE'


def test_measurement_produces_bounded_separately_signed_exit_proof(tmp_path):
    now, sid = 1800000000, uuid.uuid4().hex
    key = Ed25519PrivateKey.generate()
    public = raw_public(key.public_key())
    probe_keys = tmp_path / 'keys.json'
    probe_keys.write_text(json.dumps({'keys': {kid_for(public): b64e(public)}}))
    wg_key = base64.b64encode(os.urandom(32)).decode()
    observer = FakeObserver()
    token = measure(sid, wg_key, 'DE', '10.204.1.252', 'fd42:204:0:1::fc', key, FakeDatabase(),
                    observer=observer, clock=lambda: now)
    assert observer.sources == ['10.204.1.252', 'fd42:204:0:1::fc']
    claims = verify_proof(token, probe_keys, sid, wg_key, now)
    assert claims['country'] == 'DE' and claims['exp'] - claims['iat'] == 120
    assert claims['exit_ipv4'] == '93.184.215.14'
    with pytest.raises(VPNError):
        verify_proof(token, probe_keys, sid, wg_key, now + 121)


def test_measured_country_mismatch_never_produces_proof():
    with pytest.raises(VPNError, match='country'):
        measure(uuid.uuid4().hex, base64.b64encode(os.urandom(32)).decode(), 'US',
                '10.204.1.252', 'fd42:204:0:1::fc', Ed25519PrivateKey.generate(), FakeDatabase(),
                observer=FakeObserver())


@pytest.mark.parametrize('source', ['127.0.0.1', '192.168.1.1', '::1', 'fe80::1'])
def test_production_observer_rejects_non_probe_sources_before_any_socket(source):
    with pytest.raises(VPNError):
        BoundHTTPS().observe(source)


def test_country_database_pin_validated_before_reader_load(tmp_path):
    path = tmp_path / 'db.mmdb'
    path.write_bytes(b'untrusted database fixture')
    with pytest.raises(VPNError, match='checksum'):
        PinnedCountryDatabase(path, secrets.token_hex(32))


def test_country_reader_uses_exact_checksum_verified_snapshot_and_fresh_metadata(tmp_path, monkeypatch):
    payload, now = b'synthetic reader fixture, not a MaxMind database', 1800000000
    path = tmp_path / 'db.mmdb'; path.write_bytes(payload)
    pin = hashlib.sha256(payload).hexdigest()
    readers = []

    class Reader:
        closed = False

        def metadata(self):
            return types.SimpleNamespace(database_type='GeoLite2-Country', build_epoch=now - 3600)

        def get(self, address):
            return {'country': {'iso_code': 'DE'}}

        def close(self):
            self.closed = True

    def open_database(stream, mode):
        assert stream.read() == payload and mode == 16
        path.write_bytes(b'changed pathname cannot replace verified in-memory bytes')
        readers.append(Reader())
        return readers[-1]

    monkeypatch.setitem(sys.modules, 'maxminddb', types.SimpleNamespace(open_database=open_database, MODE_FD=16))
    database = PinnedCountryDatabase(path, pin, now=now)
    assert database.country('93.184.215.14') == 'DE'
    assert pin in database.reference
    database.close()
    assert readers[-1].closed


def test_private_probe_key_must_be_owner_only_and_correct_length(tmp_path):
    path = tmp_path / 'probe.key'
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    path.write_bytes(raw)
    path.chmod(0o600)
    assert raw_public(private_probe_key(path).public_key()) == raw_public(key.public_key())
    if os.name != 'nt':
        path.chmod(0o644)
        with pytest.raises(VPNError):
            private_probe_key(path)
    path.chmod(0o600); path.write_bytes(b'bad key')
    with pytest.raises(VPNError):
        private_probe_key(path)
