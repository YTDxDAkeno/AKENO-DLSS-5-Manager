"""Desktop browser request contracts; backend behavior has separate real ASGI HTTP tests."""
from urllib.parse import parse_qs, urlsplit
import os

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


def test_library_finds_nested_game_executable_without_requiring_exact_display_title(tmp_path):
    from types import SimpleNamespace
    from akeno.network.games import installed_program
    root=tmp_path/'Factorio installation'
    exe=root/'bin'/'x64'/'factorio.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture only')
    game=SimpleNamespace(name='Factorio (Steam)',install_dir=str(root),exe_path=str(exe),exe_candidates=[])
    assert installed_program('factorio',[game])==exe
    game.exe_path=None;game.exe_candidates=['bin/x64/factorio.exe']
    assert installed_program('factorio',[game])==exe
    assert installed_program('openttd',[game]) is None


def test_library_never_suggests_outside_or_unreviewed_program(tmp_path):
    from types import SimpleNamespace
    from akeno.network.games import installed_program
    root=tmp_path/'install';root.mkdir()
    outside=tmp_path/'factorio.exe';outside.write_bytes(b'fixture only')
    unrelated=root/'other.exe';unrelated.write_bytes(b'fixture only')
    game=SimpleNamespace(install_dir=str(root),exe_path=str(outside),exe_candidates=[str(unrelated),'../factorio.exe'])
    assert installed_program('factorio',[game]) is None


@pytest.mark.skipif(os.name=='nt',reason='POSIX symlink fixture; native Windows path enforcement needs Windows evidence')
def test_library_rejects_symlink_to_an_executable_outside_installation(tmp_path):
    from types import SimpleNamespace
    from akeno.network.games import installed_program
    root=tmp_path/'install';root.mkdir()
    outside=tmp_path/'factorio.exe';outside.write_bytes(b'fixture only')
    game=SimpleNamespace(install_dir=str(root))
    # A reviewed basename on a symlink cannot authorize a file outside the recorded installation.
    (root/'factorio.exe').symlink_to(outside)
    assert installed_program('factorio',[game]) is None


def test_library_root_fallback_and_missing_metadata(tmp_path):
    from types import SimpleNamespace
    from akeno.network.games import installed_program
    root=tmp_path/'OpenTTD';root.mkdir()
    exe=root/'openttd.exe';exe.write_bytes(b'fixture only')
    game=SimpleNamespace(install_dir=str(root))
    assert installed_program('openttd',[SimpleNamespace(),game])==exe
    exe.unlink();exe.mkdir()
    assert installed_program('openttd',[game]) is None
    with pytest.raises(ValueError):installed_program('unreviewed',[game])
