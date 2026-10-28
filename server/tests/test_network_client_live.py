"""Two real desktop licensing/LAN clients against a local HTTP server; no tunnel traffic claimed here."""
import copy,sys,time,threading,socket
from pathlib import Path
import pytest,uvicorn
sys.path.append(str(Path(__file__).resolve().parents[2]))
from akeno.licensing.manager import LicenseManager
from akeno.licensing.config import LicensingConfig
from akeno.licensing.storage import SecureStore
from akeno.network.client import LanClient
from akeno_server import db as dbmod,clock
from akeno_server.auth import service as auth
from akeno_server.licensing import signing
from akeno_server.config import reset_config
from tests.helpers import make_codes,PASSWORD


class MemoryIdentity:
    def __init__(self):self.state={}
    def load(self):return copy.deepcopy(self.state)
    def save(self,value):self.state=copy.deepcopy(value)


def test_two_desktop_clients_share_authenticated_device_bound_room(tmp_path,monkeypatch,keys):
    monkeypatch.setenv('AKENO_NETWORK_ENABLED','true');monkeypatch.setenv('AKENO_NETWORK_DATABASE_PATH',str(tmp_path/'rooms.sqlite3'))
    monkeypatch.setenv('AKENO_NETWORK_SIGNING_KEY_PATH',str(tmp_path/'network-signing.key'))
    reset_config()
    from akeno_server.main import create_app
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        for _ in range(200):
            if server.started:break
            time.sleep(.01)
        assert server.started
        with dbmod.session() as session:public={row.kid:row.public_key for row in signing.public_keys(session)}
        clients=[]
        for i in range(2):
            monkeypatch.setenv('AKENO_DEVICE_ID','network-client-'+str(i))
            m=LicenseManager(LicensingConfig(f'http://127.0.0.1:{port}',public),SecureStore(tmp_path/('user'+str(i))))
            email=f'network-{i}@example.test';m.create_account(email,PASSWORD)
            with dbmod.transaction() as session:auth.user_by_email(session,email).email_verified_at=clock.utcnow()
            m.redeem(make_codes('monthly')[0]);clients.append(LanClient(m,MemoryIdentity()))
        created=clients[0].create('Private test room','password')
        joined=clients[1].join(created['invite'],'password')
        assert joined['room_id']==created['room_id']
        for c in clients:
            result=c.heartbeat(created['room_id'])
            assert len(result['members'])==2
            assert len({p['virtual_ip'] for p in result['members']})==2
            assert result['transport']=='not_implemented'
        # WireGuard private keys stay in each client's identity store, never in server member data.
        assert all('private_key' not in member for member in result['members'])
    finally:
        server.should_exit=True;thread.join(5)


def test_client_against_server_without_lan_api_reports_unavailable_not_404(tmp_path,monkeypatch,keys):
    """Regression for the "Not Found" text: the production server runs without AKENO_NETWORK_ENABLED."""
    monkeypatch.delenv('AKENO_NETWORK_ENABLED',raising=False)
    monkeypatch.setenv('AKENO_NETWORK_SIGNING_KEY_PATH',str(tmp_path/'network-signing.key'))
    reset_config()
    from akeno_server.main import create_app
    from akeno.network.client import LanUnavailable
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        for _ in range(200):
            if server.started:break
            time.sleep(.01)
        assert server.started
        with dbmod.session() as session:public={row.kid:row.public_key for row in signing.public_keys(session)}
        monkeypatch.setenv('AKENO_DEVICE_ID','network-client-off')
        m=LicenseManager(LicensingConfig(f'http://127.0.0.1:{port}',public),SecureStore(tmp_path/'user'))
        email='network-off@example.test';m.create_account(email,PASSWORD)
        with dbmod.transaction() as session:auth.user_by_email(session,email).email_verified_at=clock.utcnow()
        m.redeem(make_codes('monthly')[0])
        client=LanClient(m,MemoryIdentity())
        assert client.status()=={'enabled':False,'transport':'not_implemented','relay':False}
        for action in (lambda:client.create('Room'),lambda:client.join('x'*20),lambda:client.heartbeat('ab'*16)):
            with pytest.raises(LanUnavailable):action()
    finally:
        server.should_exit=True;thread.join(5)
