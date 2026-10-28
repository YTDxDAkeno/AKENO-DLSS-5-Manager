"""Real ASGI HTTP and transactional SQLite. No Windows tunnel/game/ISP-NAT evidence is implied."""
import base64
import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from akeno_server import clock, db as dbmod
from akeno_server.config import reset_config
from akeno_server.models import Device
from akeno_server.network.rooms import RoomError, RoomStore
from tests.helpers import bearer
from tests.test_network_api import player

PREFIX = '/api/v1/network/lan/browser'


def key():
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def browser(tmp_path, monkeypatch, keys):
    monkeypatch.setenv('AKENO_NETWORK_ENABLED','true')
    monkeypatch.setenv('AKENO_NETWORK_DATABASE_PATH',str(tmp_path/'rooms.sqlite3'))
    reset_config()
    from akeno_server.main import create_app
    app=create_app()
    with TestClient(app) as client:
        yield client, app.state.lan_rooms


def create(c, tokens, device, **fields):
    response=c.post(PREFIX+'/rooms',headers=bearer(tokens),json={
        'device_id':device['id'],'public_key':key(),'name':'OpenTTD friends','game_id':'openttd',
        'region':'eu','capacity':4,**fields})
    assert response.status_code==201,response.text
    return response.json()


def browse(c, tokens, device, **params):
    response=c.get(PREFIX+'/rooms',headers=bearer(tokens),params={'device_id':device['id'],**params})
    assert response.status_code==200,response.text
    return response.json()


def test_browser_requires_existing_account_license_and_exact_device(browser):
    c,_=browser
    assert c.get(PREFIX+'/rooms',params={'device_id':str(uuid.uuid4())}).status_code==401
    a,da=player(c,'a@example.test');b,db=player(c,'b@example.test')
    assert c.get(PREFIX+'/rooms',headers=bearer(a),params={'device_id':db['id']}).status_code==403
    with dbmod.transaction() as session:
        session.get(Device,uuid.UUID(da['id'])).revoked_at=clock.utcnow()
    assert c.get(PREFIX+'/rooms',headers=bearer(a),params={'device_id':da['id']}).status_code==403


def test_browser_absent_without_opt_in(client):
    assert client.get(PREFIX+'/rooms').status_code==404


def test_publish_find_password_join_and_member_ban_no_private_metadata(browser):
    c,s=browser;a,da=player(c,'owner@example.test');b,db=player(c,'guest@example.test')
    room=create(c,a,da,password='fixture password')
    listing=browse(c,b,db)
    [entry]=listing['rooms']
    assert entry=={'room_id':room['room_id'],'name':'OpenTTD friends','game_id':'openttd','region':'eu',
                   'players':1,'capacity':4,'password_required':True,'status':'open','visibility':'public',
                   'transport_policy':'relay_only','latency_ms':None}
    encoded=json.dumps(listing)
    for private in (room['invite'],a['user']['id'],da['id'],'owner@example.test','10.203.','public_key','password_hash'):
        assert private not in encoded
    join=PREFIX+'/rooms/'+room['room_id']+'/join'
    body={'device_id':db['id'],'public_key':key(),'password':'wrong'}
    assert c.post(join,headers=bearer(b),json=body).status_code==403
    body['password']='fixture password'
    response=c.post(join,headers=bearer(b),json=body)
    assert response.status_code==200,response.text
    assert response.json()['transport_policy']=='relay_only'
    assert browse(c,a,da)['rooms'][0]['players']==2
    assert c.delete('/api/v1/network/lan/rooms/'+room['room_id']+'/members/'+b['user']['id'],
                    headers=bearer(a),params={'device_id':da['id']}).status_code==204
    assert c.post(join,headers=bearer(b),json=body).status_code==403


def test_private_rooms_never_listed_or_joinable_via_public_id_and_owner_controls(browser):
    c,_=browser;a,da=player(c,'owner@example.test');b,db=player(c,'guest@example.test')
    room=create(c,a,da,visibility='private')
    assert browse(c,b,db)['rooms']==[]
    body={'device_id':db['id'],'public_key':key()}
    assert c.post(PREFIX+'/rooms/'+room['room_id']+'/join',headers=bearer(b),json=body).status_code==403
    update={'device_id':da['id'],'visibility':'public','game_id':'openttd','region':'eu','capacity':4}
    assert c.patch(PREFIX+'/rooms/'+room['room_id'],headers=bearer(b),json={**update,'device_id':db['id']}).status_code==403
    assert c.patch(PREFIX+'/rooms/'+room['room_id'],headers=bearer(a),json=update).status_code==200
    assert len(browse(c,b,db)['rooms'])==1
    update['locked']=True
    assert c.patch(PREFIX+'/rooms/'+room['room_id'],headers=bearer(a),json=update).status_code==200
    assert browse(c,b,db)['rooms']==[]
    assert browse(c,b,db,open_only=False)['rooms'][0]['status']=='locked'
    assert c.post(PREFIX+'/rooms/'+room['room_id']+'/join',headers=bearer(b),json=body).status_code==403
    update['visibility']='private'
    assert c.patch(PREFIX+'/rooms/'+room['room_id'],headers=bearer(a),json=update).status_code==200
    assert browse(c,b,db,open_only=False)['rooms']==[]


def test_public_quota_filters_and_safe_bounds(browser):
    c,_=browser;a,da=player(c,'first@example.test');b,db=player(c,'second@example.test')
    room=create(c,a,da,name='100% training')
    denied=c.post(PREFIX+'/rooms',headers=bearer(a),json={'device_id':da['id'],'public_key':key(),
                 'name':'spam','game_id':'openttd','region':'eu'})
    assert denied.status_code==403 and 'limit' in denied.text
    second=create(c,b,db,name='Other game',game_id='factorio',region='na')
    assert len(browse(c,a,da)['rooms'])==2
    assert browse(c,a,da,search='%')['rooms'][0]['room_id']==room['room_id']
    assert browse(c,a,da,game_id='factorio',region='na')['rooms'][0]['room_id']==second['room_id']
    assert browse(c,a,da,minimum_players=2)['rooms']==[]
    one=browse(c,a,da,limit=1)
    assert len(one['rooms'])==1 and one['next_offset']==1
    assert len(browse(c,a,da,limit=1,offset=1)['rooms'])==1
    for query in ({'limit':51},{'offset':1001},{'minimum_players':17},{'search':'a'*65}):
        assert c.get(PREFIX+'/rooms',headers=bearer(a),params={'device_id':da['id'],**query}).status_code==422
    for fields in ({'actor':b['user']['id']},{'game_id':'unknown-game'},{'public_key':'bad'},{'capacity':99}):
        response=c.post(PREFIX+'/rooms',headers=bearer(b),json={'device_id':db['id'],'public_key':key(),
                        'name':'bad','game_id':'openttd','region':'eu',**fields})
        assert response.status_code in (403,422)


def test_reports_are_bounded_deduplicated_and_private_rooms_require_membership(browser):
    c,s=browser;a,da=player(c,'owner@example.test');b,db=player(c,'guest@example.test')
    room=create(c,a,da)
    target=PREFIX+'/rooms/'+room['room_id']+'/reports'
    body={'device_id':db['id'],'category':'spam'}
    assert c.post(target,headers=bearer(b),json=body).status_code==202
    assert c.post(target,headers=bearer(b),json=body).status_code==202
    with sqlite3.connect(s.path) as sql:
        assert sql.execute('SELECT count(*) FROM room_reports').fetchone()[0]==1
        assert sql.execute('SELECT category FROM room_reports').fetchone()[0]=='spam'
    assert c.post(target,headers=bearer(b),json={**body,'text':'unbounded user data'}).status_code==422
    update={'device_id':da['id'],'visibility':'private','game_id':'openttd','region':'eu','capacity':4}
    assert c.patch(PREFIX+'/rooms/'+room['room_id'],headers=bearer(a),json=update).status_code==200
    assert c.post(target,headers=bearer(b),json=body).status_code==403


def test_old_schema_migrates_to_private_without_losing_members(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE rooms (id TEXT PRIMARY KEY, owner TEXT NOT NULL,name TEXT NOT NULL,slot INTEGER UNIQUE NOT NULL,'
                   'password TEXT,invite TEXT UNIQUE NOT NULL,invite_expiry REAL NOT NULL,expiry REAL NOT NULL)')
        db.execute('INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?)',('old','owner','Legacy',1,None,'hashed',2000,3000))
    s=RoomStore(path,clock=lambda:1000)
    assert s.browse()['rooms']==[]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT visibility,capacity FROM rooms WHERE id=?',('old',)).fetchone()==('private',16)
    new=s.create('new','New',key())
    assert s.info('new',new['room_id'])['visibility']=='private'


def test_concurrent_public_joins_enforce_capacity_and_expired_owner_disappears(tmp_path):
    now=[1000.0];s=RoomStore(tmp_path/'rooms',clock=lambda:now[0])
    room=s.create('owner','Public',key(),visibility='public',game_id='openttd',region='eu',capacity=3)
    def join(n):
        try:
            s.join_public('guest'+str(n),room['room_id'],key());return True
        except RoomError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(join,range(10)))==2
    assert s.browse()['rooms']==[]
    assert s.browse(open_only=False)['rooms'][0]['status']=='full'
    now[0]+=46
    assert s.browse(open_only=False)['rooms']==[]
    with pytest.raises(RoomError,match='unavailable'):
        s.join_public('late',room['room_id'],key())
    s.heartbeat('owner',room['room_id'])
    assert len(s.browse(open_only=False)['rooms'])==1
    now[0]+=s.ROOM_TTL
    assert s.browse(open_only=False)['rooms']==[]
    with sqlite3.connect(s.path) as db:
        assert db.execute('SELECT count(*) FROM rooms').fetchone()[0]==0
