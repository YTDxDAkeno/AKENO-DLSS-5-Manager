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


def test_native_removal_refuses_another_tunnels_public_key(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from akeno.network import windows
    from akeno.network.windows import WindowsWireGuard,NativeUnavailable
    monkeypatch.setattr(windows,'service_state',lambda name:'running')
    native=object.__new__(WindowsWireGuard);native.configs=tmp_path
    calls=[]
    native._call=lambda *a,**kw:calls.append(a) or SimpleNamespace(returncode=0,stdout='another key')
    with pytest.raises(NativeUnavailable,match='does not belong'):
        native.stop('AkenoVPN'+'1'*24,public_key='this profiles key')
    assert all(c==('wg.exe','show','AkenoVPN'+'1'*24,'public-key') for c in calls)    # no uninstall command


class FakeTunnelService:
    """The official CLI's timing on a real runner: install returns while the service is START_PENDING."""
    def __init__(self,ready_after=3,key='KEY'):
        self.state=None;self.polls=0;self.ready_after=ready_after;self.key=key;self.calls=[]
    def scm(self,name):
        if self.state=='start_pending':
            self.polls+=1
            if self.polls>=self.ready_after:self.state='running'
        return self.state
    def call(self,exe,*args,check=True):
        from types import SimpleNamespace
        self.calls.append((exe,)+args)
        if args[0]=='/installtunnelservice':self.state='start_pending';return SimpleNamespace(returncode=0,stdout='')
        if args[0]=='/uninstalltunnelservice':
            # Like the real UninstallTunnel: a still-starting service would only be marked for deletion.
            assert self.state=='running','uninstall sent to a service that cannot be stopped yet'
            self.state=None;return SimpleNamespace(returncode=0,stdout='')
        if self.state!='running':return SimpleNamespace(returncode=1,stdout='')
        out={'public-key':self.key+'\n','latest-handshakes':'peer\t0\n'}[args[2]]
        return SimpleNamespace(returncode=0,stdout=out)


def native_with(service,monkeypatch,tmp_path):
    from akeno.network import windows
    monkeypatch.setattr(windows,'service_state',service.scm)
    monkeypatch.setattr(windows.time,'sleep',lambda s:None)
    native=object.__new__(windows.WindowsWireGuard);native.configs=tmp_path;native.root=tmp_path;native._call=service.call
    monkeypatch.setattr(windows,'no_links_on_the_way',lambda *a:None)
    return native


def test_starting_service_is_never_reported_disconnected(tmp_path,monkeypatch):
    service=FakeTunnelService(ready_after=10**9);native=native_with(service,monkeypatch,tmp_path)
    service.state='start_pending'
    assert native.status('AkenoVPN'+'2'*24)['state']=='starting'
    service.state='stop_pending'
    assert native.status('AkenoVPN'+'2'*24)['state']=='stopping'
    service.state='stopped'
    assert native.status('AkenoVPN'+'2'*24)['state']=='failed'
    service.state=None
    assert native.status('AkenoVPN'+'2'*24)['state']=='disconnected'


def test_removal_waits_for_a_stoppable_service_and_for_its_deletion(tmp_path,monkeypatch):
    service=FakeTunnelService(ready_after=4);native=native_with(service,monkeypatch,tmp_path)
    service.state='start_pending';name='AkenoVPN'+'3'*24
    (tmp_path/(name+'.conf.dpapi')).write_bytes(b'encrypted by the manager')
    native.stop(name,public_key='KEY')
    assert service.state is None and not (tmp_path/(name+'.conf.dpapi')).exists()
    assert ('wireguard.exe','/uninstalltunnelservice',name) in service.calls


def test_unresponsive_service_is_not_removed_without_key_proof(tmp_path,monkeypatch):
    from akeno.network.windows import NativeUnavailable
    service=FakeTunnelService(ready_after=10**9);native=native_with(service,monkeypatch,tmp_path)
    native.READY_TIMEOUT=0.05;service.state='start_pending';name='AkenoVPN'+'4'*24
    with pytest.raises(NativeUnavailable,match='does not respond'):native.stop(name,public_key='KEY')
    assert not any(c[1]=='/uninstalltunnelservice' for c in service.calls)


def test_start_waits_until_the_tunnel_answers(tmp_path,monkeypatch):
    from akeno.network import windows
    service=FakeTunnelService(ready_after=5);native=native_with(service,monkeypatch,tmp_path)
    native.root=tmp_path;monkeypatch.setattr(native,'_trusted',lambda exe:tmp_path/exe)
    monkeypatch.setattr(windows,'no_links_on_the_way',lambda *a:None)
    monkeypatch.setattr(windows,'private_file',lambda path,data:path.write_bytes(data))
    import types,sys
    monkeypatch.setitem(sys.modules,'winreg',types.SimpleNamespace(HKEY_LOCAL_MACHINE=0,
        OpenKey=lambda *a:(_ for _ in ()).throw(FileNotFoundError()),QueryValueEx=None))
    monkeypatch.setattr(windows.ctypes,'windll',types.SimpleNamespace(shell32=types.SimpleNamespace(IsUserAnAdmin=lambda:1)),
                        raising=False)
    name='AkenoVPN'+'5'*24
    real_replace=windows.os.replace
    def manager_encrypts(src,dst):
        real_replace(src,dst);dst.unlink();(tmp_path/(name+'.conf.dpapi')).write_bytes(b'enc')
    monkeypatch.setattr(windows.os,'replace',manager_encrypts)
    native.start(name,profile())
    assert service.state=='running' and native.status(name)['state']=='awaiting_handshake'
    assert service.polls>=5                                  # it waited for the real service state
