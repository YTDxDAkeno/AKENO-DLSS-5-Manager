"""Native-backend stand-ins: state/cleanup contracts, not Windows driver or routing evidence."""
import copy
import pytest
from test_network_profiles import profile
from akeno.network.profiles import ProfileError
from akeno.network.tunnels import TunnelManager


class Journal:
    def __init__(self):self.state={}
    def load(self):return copy.deepcopy(self.state)
    def save(self,value):self.state=copy.deepcopy(value)


class Profiles:
    def _load(self,ident):return {'text':profile()}


class Backend:
    def __init__(self):self.state='disconnected';self.fail_start=False;self.fail_stop=False
    def start(self,name,text):
        self.state='awaiting_handshake'
        if self.fail_start:raise RuntimeError('native fixture failure')
    def status(self,name):return {'state':self.state,'last_handshake':None}
    def stop(self,name,public_key):
        if self.fail_stop:raise RuntimeError('native fixture stop failure')
        self.state='disconnected'


def test_handshake_required_no_fake_exit_ip_and_clean_disconnect():
    backend=Backend();journal=Journal();manager=TunnelManager(Profiles(),journal,backend)
    assert manager.connect('1'*32)['state']=='awaiting_handshake'
    with pytest.raises(ProfileError):manager.exit_ip()
    backend.state='connected'
    assert manager.status()['state']=='connected' and manager.status()['exit_ip'] is None
    assert manager.disconnect()['state']=='disconnected'
    assert journal.load()=={'phase':'disconnected'}


def test_start_failure_rolls_back_and_failed_cleanup_requires_recovery():
    b=Backend();b.fail_start=True;b.fail_stop=True;j=Journal();m=TunnelManager(Profiles(),j,b)
    with pytest.raises(RuntimeError):m.connect('2'*32)
    assert j.load()['phase']=='recovery_required'
    with pytest.raises(ProfileError):m.connect('3'*32)
    b.fail_stop=False
    assert m.disconnect()['state']=='disconnected'


def test_restart_can_recover_an_interrupted_connection():
    b=Backend();j=Journal();j.save({'phase':'connecting','profile_id':'4'*32,'name':'AkenoVPN'+'4'*24})
    assert TunnelManager(Profiles(),j,b).disconnect()['state']=='disconnected'


def test_invalid_tunnel_id_cannot_become_a_native_command():
    with pytest.raises(ProfileError):TunnelManager.name('../other-profile')
    from akeno.network.windows import WindowsWireGuard,NativeUnavailable
    with pytest.raises(NativeUnavailable):WindowsWireGuard._name('anything /uninstallmanagerservice')


def test_native_removal_refuses_another_tunnels_public_key(tmp_path):
    from types import SimpleNamespace
    from akeno.network.windows import WindowsWireGuard,NativeUnavailable
    native=object.__new__(WindowsWireGuard);native.configs=tmp_path
    calls=[]
    native._call=lambda *a,**kw:calls.append(a) or SimpleNamespace(returncode=0,stdout='another key')
    with pytest.raises(NativeUnavailable,match='does not belong'):
        native.stop('AkenoVPN'+'1'*24,public_key='this profiles key')
    assert calls==[('wg.exe','show','AkenoVPN'+'1'*24,'public-key')]
