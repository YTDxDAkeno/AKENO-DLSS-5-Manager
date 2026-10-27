"""Profile parser/storage tests. Crypto stand-ins are not Windows DPAPI or real tunnel evidence."""
import base64
import os
import sqlite3

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from akeno.network.profiles import ProfileError, ProfileStore, StorageUnavailable, parse_wireguard


def profile():
    # Ephemeral keys generated only in memory for tests; never a real provider profile.
    keys = [base64.b64encode(os.urandom(32)).decode() for _ in range(2)]
    return ('[Interface]\nPrivateKey = '+keys[0]+'\nAddress = 10.42.0.2/32, fd00:42::2/128\n'
            'DNS = 1.1.1.1, 2606:4700:4700::1111\nMTU = 1420\n\n'
            '[Peer]\nPublicKey = '+keys[1]+'\nAllowedIPs = 0.0.0.0/0, ::/0\n'
            'Endpoint = vpn.example.invalid:51820\nPersistentKeepalive = 25\n')


def test_valid_profile_classifies_routes_without_claiming_connection():
    text=profile()
    p=parse_wireguard(text)
    assert p.routing=='full_ipv4_ipv6'
    assert p.addresses==('10.42.0.2/32','fd00:42::2/128')
    assert 'PrivateKey' not in repr(p)
    assert parse_wireguard(text.replace('0.0.0.0/0, ::/0','10.5.0.0/16')).routing=='custom'


@pytest.mark.parametrize('directive', ['PostUp = powershell evil', 'PreDown = cmd /c evil', 'Table = 123',
                                      'SaveConfig = true', 'Include = other.conf', 'PrivateKey = duplicate'])
def test_script_and_unknown_directives_are_rejected_without_echo(directive):
    text=profile().replace('[Peer]',directive+'\n[Peer]')
    with pytest.raises(ProfileError) as exc:
        parse_wireguard(text)
    assert directive not in str(exc.value)


@pytest.mark.parametrize('old,new', [('Address = 10.42.0.2/32, fd00:42::2/128','Address = garbage'),
                                    ('vpn.example.invalid:51820','https://evil.invalid/path'),
                                    ('vpn.example.invalid:51820','host:65536'),
                                    ('MTU = 1420','MTU = 2'), ('PersistentKeepalive = 25','PersistentKeepalive = $(evil)'),
                                    ('AllowedIPs = 0.0.0.0/0, ::/0','AllowedIPs = 10.2.3.4/24')])
def test_malformed_settings_rejected(old,new):
    with pytest.raises(ProfileError):
        parse_wireguard(profile().replace(old,new))


def test_duplicate_peers_default_interpolation_multiline_and_large_profiles_rejected():
    text=profile()
    for bad in (text+'\n[Peer]\nPublicKey = x',text+'\n arbitrary-command',
                '[DEFAULT]\nPostUp=evil\n'+text, text+'x'*65536, 'client\ndev tun\nup evil.sh\n'):
        with pytest.raises(ProfileError):
            parse_wireguard(bad)


def test_encrypted_storage_restart_rename_delete_and_no_secret_metadata(tmp_path):
    aes=AESGCM(AESGCM.generate_key(bit_length=256))
    def protect(data):
        nonce=os.urandom(12)
        return nonce+aes.encrypt(nonce,data,None)
    def unprotect(blob):
        return aes.decrypt(blob[:12],blob[12:],None)
    path=tmp_path/'profiles.sqlite3'
    store=ProfileStore(path,protect,unprotect)
    text=profile()
    ident=store.add('My server',text)
    assert text.encode() not in path.read_bytes()
    store=ProfileStore(path,protect,unprotect)
    assert store.metadata()==[{'id':ident,'name':'My server','protocol':'wireguard','state':'imported',
                              'country':None,'connectable':False}]
    store.rename(ident,'Renamed')
    assert store.metadata()[0]['name']=='Renamed'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE profiles SET payload=?',(b'damaged',))
    with pytest.raises(ProfileError,match='damaged'):
        store.metadata()
    store.remove(ident)
    assert store.metadata()==[]


def test_linux_has_no_plaintext_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr('akeno.network.profiles.sys.platform','linux')
    with pytest.raises(StorageUnavailable,match='DPAPI'):
        ProfileStore(tmp_path/'profiles.sqlite3')
    assert not (tmp_path/'profiles.sqlite3').exists()
