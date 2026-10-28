"""Network and account keys have distinct signing purposes and bounded delegation trust."""
import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from akeno_server import clock, db as dbmod
from akeno_server.licensing import signing as accounts
from akeno_server.network import signing


def account_public_keys():
    with dbmod.session() as db:
        return accounts.public_key_objects(db)


def test_network_key_is_persistent_private_and_distinct_from_account_key(keys):
    first=signing.active()
    second=signing.active()
    assert first[0]==second[0] and first[0]!=accounts.store().active()[0]
    assert accounts.raw_public(first[1].public_key())==accounts.raw_public(second[1].public_key())
    from akeno_server.config import get_config
    assert get_config().network_signing_key_path.stat().st_mode & 0o777 == 0o600
    document=signing.delegation()
    kid,claims=accounts.verify(document['credential'],account_public_keys(),'akeno-network-key-delegation')
    assert kid==keys and claims['keys']==document['keys']
    assert claims['exp']-claims['iat']==3600 and claims['aud']=='akeno-network-key'


def test_client_verifies_scoped_delegation_and_rejects_raw_or_wrong_purpose_keys(keys):
    from akeno.network.credentials import verify_network_keys
    trusted=account_public_keys()
    now=clock.utcnow().timestamp()
    doc=signing.delegation()
    network_keys=verify_network_keys(doc,trusted,now)
    assert set(network_keys)==set(signing.public_keys()) and set(network_keys).isdisjoint(trusted)
    token=signing.sign_token('akeno-lan-peer',{'aud':'akeno-lan-peer'})
    accounts.verify(token,network_keys,'akeno-lan-peer')
    with pytest.raises(accounts.BadSignature):accounts.verify(token,trusted,'akeno-lan-peer')
    with pytest.raises(ValueError):verify_network_keys(doc,trusted,now+3601)
    with pytest.raises(ValueError):verify_network_keys({'keys':doc['keys']},trusted,now)
    with pytest.raises(ValueError):verify_network_keys(doc,trusted,now,purpose='akeno-license')
    with pytest.raises(ValueError):signing.sign_token('akeno-license',{})


def test_network_delegation_cannot_substitute_another_key_or_account_key(keys):
    from akeno.network.credentials import verify_network_keys
    trusted=account_public_keys()
    doc=signing.delegation()
    changed=dict(doc,keys={'attacker':'A'*43})
    with pytest.raises(ValueError):verify_network_keys(changed,trusted)
    kid,private=accounts.store().active()
    claims={'iss':'akeno-network','aud':'akeno-network-key','iat':int(clock.utcnow().timestamp()),
            'exp':int(clock.utcnow().timestamp())+90,'purposes':['akeno-lan-peer']}
    # A network delegation cannot simply present the account key again as a network-purpose key.
    from akeno_server.auth.tokens import b64e
    claims['keys']={kid:b64e(accounts.raw_public(private.public_key()))}
    token=accounts.sign(private,kid,'akeno-network-key-delegation',claims)
    with pytest.raises(ValueError):verify_network_keys({'credential':token,'keys':claims['keys']},trusted)


def test_network_private_file_must_not_be_public_or_a_symlink(tmp_path):
    if os.name=='nt':pytest.skip('POSIX mode and symlink protection')
    from akeno_server.network.signing import _private, SigningUnavailable
    path=tmp_path/'private'
    _private(path)
    path.chmod(0o644)
    with pytest.raises(SigningUnavailable):_private(path)
    path.chmod(0o600)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(SigningUnavailable):_private(link)
