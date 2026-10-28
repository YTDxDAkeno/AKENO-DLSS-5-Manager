"""Desktop browser request contracts; backend behavior has separate real ASGI HTTP tests."""
from urllib.parse import parse_qs, urlsplit

import pytest

from akeno.network.browser import RoomBrowser
from akeno.network.games import GameProfile, catalog, profile


class Identity:
    def __init__(self):self.state={'device_id':'device','public_key':'public'}
    def save(self,state):self.state=dict(state)


class Client:
    def __init__(self):self.identity=Identity();self.calls=[]
    def _identity(self):return dict(self.identity.state)
    def _room(self,room):
        if room!='a'*32:raise ValueError('bad room')
        return room
    def _call(self,method,path,body=None):
        self.calls.append((method,path,body))
        return {'room_id':'a'*32}


def test_browser_uses_same_device_and_persists_membership():
    lan=Client();browser=RoomBrowser(lan)
    browser.create('Test','openttd','eu','password',3)
    assert lan.identity.state['room_id']=='a'*32
    method,path,body=lan.calls[-1]
    assert method=='POST' and path=='browser/rooms' and body['device_id']=='device' and body['public_key']=='public'
    browser.publish('a'*32,game_id='openttd',region='eu',visibility='private',locked=True)
    assert lan.calls[-1][0]=='PATCH' and lan.calls[-1][2]['visibility']=='private'
    browser.join('a'*32,'password')
    assert lan.calls[-1][1]=='browser/rooms/'+'a'*32+'/join'
    browser.report('a'*32,'spam')
    assert lan.calls[-1][2]=={'device_id':'device','category':'spam'}


def test_search_is_url_encoded_and_cannot_add_query_fields():
    lan=Client();RoomBrowser(lan).browse(search='x&device_id=other/? ü',region='eu',limit=1,offset=2)
    method,path,body=lan.calls[-1]
    query=parse_qs(urlsplit(path).query)
    assert method=='GET' and body is None and query['device_id']==['device']
    assert query['search']==['x&device_id=other/? ü'] and query['offset']==['2']


def test_only_reviewed_game_ports_and_truthful_compatibility():
    assert profile('openttd').ports==(('TCP',3979),('UDP',3979))
    assert all(not p['discovery_verified_windows'] for p in catalog())
    assert profile('openttd').evidence=='linux_wireguard_relay_lab'
    with pytest.raises(ValueError):profile('unknown')
    for proto,port in (('UDP',445),('TCP',3389),('UDP',5353),('TCP',53),('ALL',4000),('UDP',65536)):
        with pytest.raises(ValueError):GameProfile('bad','bad',(),((proto,port),)).validate()
