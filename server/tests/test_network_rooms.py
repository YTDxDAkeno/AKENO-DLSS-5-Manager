"""Synthetic principals and keys: no Windows, NAT or tunnel traffic."""
import base64
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from akeno_server.network.rooms import RoomError, RoomStore


def key():
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def rooms(tmp_path):
    now=[1000.0]
    return RoomStore(tmp_path/'lan.sqlite3',clock=lambda:now[0]),now


def test_two_simulated_clients_reconnect_leave_and_delete(rooms):
    s,now=rooms
    room=s.create('a','Gaming',key(),'private password')
    b=key()
    ident=s.join('b',room['invite'],b,'private password')
    peers=s.members('a',ident)
    assert len({p['virtual_ip'] for p in peers})==2
    assert all(p['status']=='registered' and p['transport']=='not_implemented' for p in peers)
    now[0]+=46
    assert all(p['status']=='expired' for p in s.members('a',ident))
    s.heartbeat('a',ident)
    s.join('b',room['invite'],b,'private password')
    assert all(p['status']=='registered' for p in s.members('a',ident))
    s.leave('b',ident)
    assert len(s.members('a',ident))==1
    s.delete('a',ident)
    with pytest.raises(RoomError):
        s.members('a',ident)


def test_password_expiry_and_rotation(rooms):
    s,now=rooms
    room=s.create('a','Private',key(),'correct')
    with pytest.raises(RoomError,match='password'):
        s.join('b',room['invite'],key(),'wrong')
    new=s.rotate_invite('a',room['room_id'])
    with pytest.raises(RoomError,match='invitation'):
        s.join('b',room['invite'],key(),'correct')
    now[0]+=s.INVITE_TTL
    with pytest.raises(RoomError,match='invitation'):
        s.join('b',new,key(),'correct')


def test_removed_members_cannot_rejoin_or_heartbeat(rooms):
    s,_=rooms
    room=s.create('a','Private',key())
    b=key()
    ident=s.join('b',room['invite'],b)
    s.remove('a',ident,'b')
    for call in (lambda:s.join('b',room['invite'],b),lambda:s.members('b',ident),lambda:s.heartbeat('b',ident)):
        with pytest.raises(RoomError):
            call()
    assert len(s.members('a',ident))==1


def test_only_members_read_only_owner_controls(rooms):
    s,_=rooms
    room=s.create('a','Private',key())
    ident=room['room_id']
    for call in (lambda:s.members('attacker',ident),lambda:s.delete('attacker',ident),
                 lambda:s.rotate_invite('attacker',ident),lambda:s.remove('attacker',ident,'a'),
                 lambda:s.leave('a',ident),lambda:s.remove('a',ident,'a')):
        with pytest.raises(RoomError):
            call()


def test_persistence_does_not_store_plain_invite_or_password(rooms):
    s,now=rooms
    room=s.create('a','Private',key(),'example test password')
    raw=s.path.read_bytes()
    assert room['invite'].encode() not in raw and b'example test password' not in raw
    restored=RoomStore(s.path,clock=lambda:now[0])
    restored.join('b',room['invite'],key(),'example test password')
    assert len(restored.members('a',room['room_id']))==2


def test_concurrent_joins_allocate_unique_addresses_and_enforce_capacity(rooms):
    s,_=rooms
    room=s.create('owner','Private',key())
    def join(n):
        try:
            s.join(str(n),room['invite'],key())
            return True
        except RoomError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        passed=list(pool.map(join,range(24)))
    assert sum(passed)==s.MAX_PEERS-1
    peers=s.members('owner',room['room_id'])
    assert len(peers)==s.MAX_PEERS and len({p['virtual_ip'] for p in peers})==s.MAX_PEERS


def test_expiry_and_quota(rooms):
    s,now=rooms
    room=s.create('owner','Private',key())
    s.create('owner','Second',key())
    s.create('owner','Third',key())
    with pytest.raises(RoomError,match='limit'):
        s.create('owner','Fourth',key())
    now[0]+=s.ROOM_TTL
    with pytest.raises(RoomError):
        s.members('owner',room['room_id'])
    s.create('owner','New after expiry',key())
