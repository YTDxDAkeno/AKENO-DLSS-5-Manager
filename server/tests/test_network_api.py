"""Authenticated control-plane tests over ASGI HTTP; no virtual-IP traffic claimed."""
import base64, os, sys, uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.append(str(Path(__file__).resolve().parents[2]))
from akeno_server import db as dbmod, clock
from akeno_server.auth import service as auth
from akeno_server.config import reset_config
from akeno_server.licensing import signing
from akeno_server.models import Device
from tests.helpers import bearer, register, register_device, make_codes


def key():return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def network(tmp_path,monkeypatch,keys):
    monkeypatch.setenv('AKENO_NETWORK_ENABLED','true')
    monkeypatch.setenv('AKENO_NETWORK_DATABASE_PATH',str(tmp_path/'rooms.sqlite3'))
    reset_config()
    from akeno_server.main import create_app
    with TestClient(create_app()) as client:yield client


def player(c,email):
    tokens=register(c,email)
    with dbmod.transaction() as db:auth.user_by_email(db,email).email_verified_at=clock.utcnow()
    r=c.post('/api/v1/licenses/redeem',headers=bearer(tokens),json={'code':make_codes('monthly')[0]})
    assert r.status_code==200,r.text
    return tokens,register_device(c,tokens,email)


def create(c,t,d):
    r=c.post('/api/v1/network/lan/rooms',headers=bearer(t),json={'device_id':d['id'],'public_key':key(),'name':'Private room'})
    assert r.status_code==201,r.text
    return r.json()


def test_routes_absent_by_default(client):
    assert client.post('/api/v1/network/lan/rooms',json={}).status_code==404


def test_auth_license_device_membership_and_short_lived_credential(network):
    c=network;a,da=player(c,'one@example.test');b,db=player(c,'two@example.test')
    room=create(c,a,da);rid=room['room_id']
    r=c.post('/api/v1/network/lan/join',headers=bearer(b),json={'device_id':db['id'],'invite':room['invite'],'public_key':key()})
    assert r.status_code==200,r.text
    r=c.post(f'/api/v1/network/lan/rooms/{rid}/heartbeat',headers=bearer(b),json={'device_id':db['id']})
    assert r.status_code==200,r.text
    assert len(r.json()['members'])==2 and r.json()['transport']=='not_implemented'
    from akeno.network.credentials import verify_peer
    from akeno.licensing.crypto import public_key
    with dbmod.session() as session:keys={row.kid:public_key(row.public_key) for row in signing.public_keys(session)}
    ticket=r.json()['credential'];now=clock.utcnow().timestamp()
    assert verify_peer(ticket,keys,rid,now)['device_id']==db['id']
    for room_id,at in ((rid,now+46),('wrong-room',now)):
        with pytest.raises(ValueError):verify_peer(ticket,keys,room_id,at)
    member=b['user']['id']
    r=c.delete(f'/api/v1/network/lan/rooms/{rid}/members/{member}',headers=bearer(a),params={'device_id':da['id']})
    assert r.status_code==204,r.text
    assert c.post(f'/api/v1/network/lan/rooms/{rid}/heartbeat',headers=bearer(b),json={'device_id':db['id']}).status_code==403
    assert c.post('/api/v1/network/lan/join',headers=bearer(b),json={'device_id':db['id'],'invite':room['invite'],'public_key':key()}).status_code==403


def test_missing_auth_license_verification_and_foreign_or_revoked_device_rejected(network):
    c=network
    assert c.post('/api/v1/network/lan/rooms',json={}).status_code==401
    a,da=player(c,'one@example.test');b,db=player(c,'two@example.test')
    body={'device_id':db['id'],'public_key':key(),'name':'x'}
    assert c.post('/api/v1/network/lan/rooms',headers=bearer(a),json=body).status_code==403
    body['device_id']=da['id'];body['actor']=b['user']['id']
    assert c.post('/api/v1/network/lan/rooms',headers=bearer(a),json=body).status_code==422
    body.pop('actor')
    with dbmod.transaction() as session:session.get(Device,uuid.UUID(da['id'])).revoked_at=clock.utcnow()
    assert c.post('/api/v1/network/lan/rooms',headers=bearer(a),json=body).status_code==403
    t=register(c,'unverified@example.test');d=register_device(c,t,'unverified')
    body['device_id']=d['id']
    assert c.post('/api/v1/network/lan/rooms',headers=bearer(t),json=body).status_code==403
    with dbmod.transaction() as session:auth.user_by_email(session,'unverified@example.test').email_verified_at=clock.utcnow()
    assert c.post('/api/v1/network/lan/rooms',headers=bearer(t),json=body).status_code==403


def test_session_cannot_take_over_membership_from_another_device(network):
    c=network;a,da=player(c,'one@example.test');room=create(c,a,da)
    other=register_device(c,a,'other-pc')
    path=f"/api/v1/network/lan/rooms/{room['room_id']}/members"
    assert c.get(path,headers=bearer(a),params={'device_id':da['id']}).status_code==403
    assert c.get(path,headers=bearer(a),params={'device_id':other['id']}).status_code==403
