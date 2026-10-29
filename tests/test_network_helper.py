"""Fixed helper authorization/session policy with DPAPI/native/UAC stand-ins; not a Windows driver test."""
from contextlib import contextmanager
import copy
from types import SimpleNamespace
import time

import pytest

from akeno.components.service import NetworkModules
from akeno.network import helper as h
from akeno.network.profiles import ProfileError
from akeno.network.windows import NativeUnavailable


class Memory:
    def __init__(self):
        self.value = {}
    def load(self):
        return copy.deepcopy(self.value)
    def save(self, value):
        self.value = copy.deepcopy(value)
    def clear(self):
        self.value = {}


class View(SimpleNamespace):
    """The license view the helper reads: unlocked, and which modules it includes ('*': AKENO Full)."""

    def allows(self, module):
        return self.unlocked and ('*' in self.modules or module in self.modules)


@pytest.fixture
def helper(tmp_path, monkeypatch):
    stores, events = {}, []
    def storage(path):
        return stores.setdefault(str(path), Memory())
    monkeypatch.setenv('AKENO_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(h, 'SecureStore', storage)
    monkeypatch.setattr(h, 'sys', SimpleNamespace(platform='win32', frozen=True))
    license = View(unlocked=True, modules={'*'})
    import akeno.licensing
    monkeypatch.setattr(akeno.licensing, 'manager', lambda: SimpleNamespace(view=lambda: license))
    @contextmanager
    def lock():
        events.append('lock')
        try:
            yield
        finally:
            events.append('unlock')
    monkeypatch.setattr(h, 'network_operation', lock)
    monkeypatch.setattr(h, 'ProfileStore', lambda path: events.append('profiles') or object())
    monkeypatch.setattr(h, 'WindowsWireGuard', lambda: events.append('native') or object())
    manager = SimpleNamespace(status=lambda: {'state':'disconnected'}, disconnect=lambda: {'state':'disconnected'})
    monkeypatch.setattr(h, 'TunnelManager', lambda *a: manager)
    import akeno.network.vpn_session as managed
    class Session:
        def __init__(self, client, profiles, tunnel, journal, status, stopped, **kwargs):
            self.status, self.stopped = status, stopped
            events.append('session')
        def run(self, country):
            events.append(('run', country, self.stopped()))
            self.status.save({'state':'connected','country':country,'updated':time.time()})
        def recover(self):
            events.append('recover')
            self.status.save({'state':'stopped','updated':time.time()})
            return {'state':'stopped'}
    monkeypatch.setattr(managed, 'ManagedVPNSession', Session)
    return SimpleNamespace(stores=stores, events=events, license=license, session=Session, manager=manager)


def request(body):
    ident='a'*32
    h.request_store(ident).save(body)
    code=h.execute(ident)
    return code,h.request_store(ident).load()


def session(**changes):
    return {'action':'vpn_session','country':'DE','session_id':'b'*32,'created':time.time(),**changes}


def test_managed_vpn_requires_enabled_module_before_native_state(helper):
    code, reply=request(session())
    assert code==1 and 'Enable this network module' in reply['error']
    assert helper.events==['lock','unlock']


def test_expired_license_denies_new_tunnel_but_cleanup_remains_allowed(helper):
    NetworkModules().set_enabled('vpn',True)
    helper.license.unlocked=False
    code,reply=request(session())
    assert code==1 and 'active AKENO license' in reply['error']
    assert 'native' not in helper.events
    code,reply=request({'action':'vpn_recover','created':time.time()})
    assert code==0 and reply['result']['state']=='stopped' and 'recover' in helper.events


@pytest.mark.parametrize('changes',[{'country':'de'},{'country':'DE;bad'},{'session_id':'../bad'},
    {'created':time.time()-121},{'created':time.time()+60},{'created':True},{'command':'wg'}, {'session_id':1}])
def test_malformed_expired_or_extra_session_request_never_reaches_native(helper,changes):
    NetworkModules().set_enabled('vpn',True)
    code,reply=request(session(**changes))
    assert code==1 and 'native' not in helper.events and 'session' not in helper.events


def test_session_holds_shared_operation_lock_and_publishes_matching_stop_nonce(helper):
    NetworkModules().set_enabled('vpn',True)
    code,reply=request(session())
    assert code==0 and reply['result']=={'state':'starting','session_id':'b'*32}
    assert helper.events==['lock','profiles','native','session',('run','DE',False),'unlock']
    assert h.vpn_status()['session_id']=='b'*32
    h.stop_vpn()
    assert h.vpn_stores()[2].load()=={'stop':True,'session_id':'b'*32}


def test_delayed_stop_for_previous_session_cannot_stop_new_session(helper,monkeypatch):
    NetworkModules().set_enabled('vpn',True)
    def run(self,country):
        assert not self.stopped()
        h.vpn_stores()[2].save({'stop':True,'session_id':'c'*32})
        assert not self.stopped()
        h.vpn_stores()[2].save({'stop':True,'session_id':'b'*32})
        assert self.stopped()
    monkeypatch.setattr(helper.session,'run',run)
    assert request(session())[0]==0


def test_turning_off_module_or_expiring_license_requests_running_session_stop(helper,monkeypatch):
    NetworkModules().set_enabled('vpn',True)
    def run(self,country):
        assert not self.stopped()
        NetworkModules().set_enabled('vpn',False)
        assert self.stopped()
        NetworkModules().set_enabled('vpn',True)
        helper.license.unlocked=False
        assert self.stopped()
    monkeypatch.setattr(helper.session,'run',run)
    assert request(session())[0]==0


@pytest.mark.parametrize('state',[{'phase':'lan_active'},{'phase':'lan_recovery_required'},{'unknown':'damaged'}])
def test_lan_or_damaged_journal_blocks_country_vpn_before_native(helper,state):
    NetworkModules().set_enabled('vpn',True)
    h.lan_stores()[0].save(state)
    assert request(session())[0]==1 and 'native' not in helper.events


def test_any_existing_vpn_blocks_lan_and_manual_profile_connect(helper):
    NetworkModules().set_enabled('lan_play',True)
    NetworkModules().set_enabled('vpn',True)
    h.vpn_stores()[0].save({'phase':'connecting'})
    code,reply=request({'action':'lan_session','room_id':'c'*32,'created':time.time()})
    assert code==1 and 'Disconnect or recover the VPN' in reply['error']
    code,reply=request({'action':'connect','profile_id':'d'*32,'created':time.time()})
    assert code==1 and 'managed VPN' in reply['error']


def test_component_install_refuses_existing_native_tunnel(helper,monkeypatch):
    import akeno.network.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap,'install',lambda **kw:pytest.fail('must not replace live network component'))
    h.SecureStore(h.sub('network')/'tunnel').save({'phase':'connected'})
    code,reply=request({'action':'install_components','consent':True,'created':time.time()})
    assert code==1 and 'Disconnect or recover the VPN' in reply['error']


def test_busy_mutex_prevents_dispatch_without_overwriting_current_session_status(helper,monkeypatch):
    h.vpn_stores()[1].save({'state':'connected','session_id':'c'*32})
    @contextmanager
    def busy():
        raise NativeUnavailable('Another AKENO network session is running.')
        yield
    monkeypatch.setattr(h,'network_operation',busy)
    code,reply=request(session())
    assert code==1 and 'Another AKENO' in reply['error'] and not helper.events
    assert h.vpn_status()=={'state':'connected','session_id':'c'*32}


def test_status_and_manual_cleanup_available_after_module_license_expiry(helper):
    helper.license.unlocked=False
    for action in ('status','disconnect'):
        code,reply=request({'action':action,'profile_id':None,'created':time.time()})
        assert code==0 and reply['result']['state']=='disconnected'


@pytest.mark.parametrize('result',[0,0x80,0x102,0xffffffff])
def test_windows_mutex_releases_only_owned_lock_and_closes_handle(monkeypatch,result):
    events=[]
    class Fn:
        def __init__(self,name,value=1):self.name,self.value=name,value
        def __call__(self,*args):
            events.append(self.name)
            if self.name=='descriptor':args[2]._obj.value=123
            return self.value
    kernel=SimpleNamespace(CreateMutexW=Fn('create',42),WaitForSingleObject=Fn('wait',result),
        ReleaseMutex=Fn('release'),CloseHandle=Fn('close'),LocalFree=Fn('free'))
    adv=SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=Fn('descriptor'))
    monkeypatch.setattr(h.ctypes,'WinDLL',lambda name,**kw:kernel if name=='kernel32' else adv,raising=False)
    if result in (0,0x80):
        with h.network_operation():events.append('operation')
        assert events[-4:]==['operation','release','close','free']
    else:
        with pytest.raises(NativeUnavailable):
            with h.network_operation():pytest.fail('unowned lock')
        assert 'release' not in events and events[-2:]==['close','free']


def test_mutex_release_on_native_exception(monkeypatch):
    events=[]
    class Fn:
        def __init__(self,name,value):self.name,self.value=name,value
        def __call__(self,*args):events.append(self.name);return self.value
    kernel=SimpleNamespace(CreateMutexW=Fn('create',42),WaitForSingleObject=Fn('wait',0),
        ReleaseMutex=Fn('release',1),CloseHandle=Fn('close',1),LocalFree=Fn('free',0))
    adv=SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=Fn('descriptor',1))
    monkeypatch.setattr(h.ctypes,'WinDLL',lambda name,**kw:kernel if name=='kernel32' else adv,raising=False)
    with pytest.raises(RuntimeError):
        with h.network_operation():raise RuntimeError('native failure')
    assert events[-3:]==['release','close','free']


@pytest.mark.parametrize('ident',['not-a-request-id','A'*32,'a'*31,'../'+'a'*29,'',None])
def test_malformed_request_id_is_refused_without_store_lock_or_exception(helper,ident):
    """The hidden elevated helper must exit; an exception would open the start guard's dialog nobody can close."""
    assert h.execute(ident)==2
    assert not helper.stores and not helper.events


def test_helper_never_raises_even_if_the_result_cannot_be_saved(helper,monkeypatch):
    class Broken:
        def load(self):raise OSError('unreadable request')
        def save(self,value):raise OSError('read-only store')
    monkeypatch.setattr(h,'request_store',lambda ident:Broken())
    assert h.execute('a'*32)==1 and not helper.events


@pytest.mark.parametrize('body',[{'action':'free_vpn_session','country':'US','consent':0,'created':time.time()},
    {'action':'free_vpn_session','country':'us','consent':1,'created':time.time()},
    {'action':'free_vpn_session','country':'US','consent':1,'created':time.time()-600},
    {'action':'free_vpn_session','country':'US','consent':1,'created':time.time(),'server':'1.2.3.4'},
    {'action':'install_vpn_component','consent':False,'created':time.time()}])
def test_free_vpn_requests_need_country_consent_and_freshness(helper,body):
    NetworkModules().set_enabled('vpn',True)
    code,reply=request(body)
    assert code==1 and 'error' in reply and 'native' not in helper.events


def test_free_vpn_refused_while_lan_play_runs_and_lan_refused_while_free_vpn_runs(helper):
    NetworkModules().set_enabled('vpn',True)
    h.lan_stores()[0].save({'phase':'active'})
    code,reply=request({'action':'free_vpn_session','country':'US','consent':1,'created':time.time()})
    assert code==1 and 'LAN Play' in reply['error']
    h.lan_stores()[0].save({'phase':'disconnected'})
    h.free_vpn_stores()[0].save({'phase':'connected'})
    with pytest.raises(ProfileError):
        h._no_vpn()
    with pytest.raises(ProfileError):
        h._no_free_vpn()


def test_free_vpn_requires_an_active_license_but_recovery_does_not(helper,monkeypatch):
    NetworkModules().set_enabled('vpn',True)
    helper.license.unlocked=False
    code,reply=request({'action':'free_vpn_session','country':'US','consent':1,'created':time.time()})
    assert code==1 and 'active AKENO license' in reply['error']
    import akeno.network.free_vpn as free
    monkeypatch.setattr(free,'_kill_akeno_openvpn',lambda:True)
    monkeypatch.setattr(free,'cleanup_routes',lambda servers,run=None:True)
    h.free_vpn_stores()[0].save({'phase':'connected'})
    code,reply=request({'action':'free_vpn_recover','created':time.time()})
    assert code==0 and reply['result']['state']=='stopped' and h.free_vpn_stores()[0].load()=={'phase':'disconnected'}


def test_gui_requests_only_reach_the_running_session(helper):
    with pytest.raises(ProfileError):
        h.free_vpn_request(stop=True)
    h.free_vpn_stores()[1].save({'state':'connected','session_id':'d'*32})
    h.free_vpn_request(switch=True)
    h.free_vpn_request(switch=True)
    h.free_vpn_request(stop=True)
    assert h.free_vpn_stores()[2].load()=={'session_id':'d'*32,'switch':2,'stop':True}


def test_a_vpn_only_license_connects_the_vpn_but_never_lan_play(helper):
    NetworkModules().set_enabled('vpn',True)
    NetworkModules().set_enabled('lan_play',True)
    helper.license.modules={'vpn'}
    assert h._new_connection('vpn') is not None
    with pytest.raises(ProfileError) as exc:
        h._new_connection('lan_play')
    assert 'does not include this module' in str(exc.value)
    code,reply=request({'action':'lan_session','room_id':'c'*32,'created':time.time()})
    assert code==1 and 'error' in reply and 'native' not in helper.events


def test_a_lan_only_license_never_starts_or_installs_a_vpn(helper):
    NetworkModules().set_enabled('vpn',True)
    helper.license.modules={'lan_play'}
    code,reply=request({'action':'free_vpn_session','country':'JP','consent':1,'created':time.time()})
    assert code==1 and 'does not include this module' in reply['error']
    code,reply=request({'action':'install_vpn_component','consent':True,'created':time.time()})
    assert code==1 and 'does not include this module' in reply['error']
    with pytest.raises(ProfileError):
        h._new_connection('vpn')
    assert 'native' not in helper.events and 'session' not in helper.events
