"""Explicit UAC helper with a fixed operation vocabulary and per-user DPAPI request/result files."""
from __future__ import annotations
import ctypes
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from akeno.paths import sub
from akeno.licensing.storage import SecureStore
from akeno.network.client import LanClient
from akeno.network.profiles import ProfileStore, ProfileError
from akeno.network.tunnels import TunnelManager
from akeno.network.windows import NativeUnavailable, WindowsWireGuard


def request_store(ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Invalid helper request.')
    return SecureStore(sub('network')/'requests'/ident)


def lan_stores():
    base=sub('network')
    return SecureStore(base/'lan-journal'),SecureStore(base/'lan-status'),SecureStore(base/'lan-stop')


def vpn_stores():
    base=sub('network')
    return SecureStore(base/'managed-vpn-journal'),SecureStore(base/'managed-vpn-status'),SecureStore(base/'managed-vpn-stop')


def _fresh(request, fields):
    if not isinstance(request,dict) or set(request)!=fields or type(request.get('created')) not in (int,float) \
            or not 0<=time.time()-request['created']<=120:
        raise ProfileError('The network request is invalid or expired.')


def _active(journal):
    state=journal.load()
    return bool(state) and state.get('phase') not in ('disconnected','stopped')


def _no_lan():
    if _active(lan_stores()[0]):
        raise ProfileError('Disconnect or recover LAN Play before connecting a VPN. Simultaneous tunnels need further testing.')


def _no_vpn():
    if _active(SecureStore(sub('network')/'tunnel')) or _active(vpn_stores()[0]):
        raise ProfileError('Disconnect or recover the VPN before starting LAN Play.')


def _new_connection(module):
    from akeno.licensing import manager as license_manager
    from akeno.components.service import NetworkModules
    licenses=license_manager()
    if not licenses.view().unlocked:
        raise ProfileError('An active AKENO license is required to connect. Disconnect and recovery remain available.')
    if not NetworkModules().enabled(module):
        raise ProfileError('Enable this network module before connecting. Disconnect and recovery remain available.')
    return licenses


@contextmanager
def network_operation():
    """Cross-process, cross-user elevated operation lock; Windows releases an abandoned mutex on process exit.

    Held for the entire LAN/managed-VPN session, including teardown. Stop flags remain unelevated and never
    acquire it. The global name also serializes helper operations in different interactive Windows sessions.
    An unknown/hostile pre-created mutex fails closed; it cannot grant the caller additional privileges.
    """
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    descriptor=ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,
        ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'D:P(A;;GA;;;SY)(A;;GA;;;BA)S:(ML;;NW;;;HI)',1,ctypes.byref(descriptor),None):
        raise NativeUnavailable('Cannot protect the Windows network operation lock.')
    class Attributes(ctypes.Structure):
        _fields_=[('size',w.DWORD),('descriptor',ctypes.c_void_p),('inherit',w.BOOL)]
    attributes=Attributes(ctypes.sizeof(Attributes),descriptor,False)
    kernel.CreateMutexW.argtypes=[ctypes.POINTER(Attributes),w.BOOL,w.LPCWSTR]
    kernel.CreateMutexW.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
    kernel.ReleaseMutex.argtypes=[w.HANDLE]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.LocalFree.argtypes=[w.HLOCAL]
    handle=None;acquired=False
    try:
        handle=kernel.CreateMutexW(ctypes.byref(attributes),False,'Global\\AKENONetworkOperations-v1')
        if not handle:
            raise NativeUnavailable('Cannot acquire the Windows network operation lock.')
        result=kernel.WaitForSingleObject(handle,0)
        if result not in (0,0x80):                # WAIT_OBJECT_0, WAIT_ABANDONED: ownership acquired in either case
            raise NativeUnavailable('Another AKENO network session or operation is running. Disconnect it before retrying.')
        acquired=True
        yield
    finally:
        if handle:
            if acquired:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
        kernel.LocalFree(descriptor)


def lan(store,request):
    """One LAN Play session (runs until the GUI sets the stop flag) or the recovery after a crash."""
    from akeno.network.lan import LanControl
    from akeno.network.lan_windows import LanSession,recover
    journal,status,stop=lan_stores()
    try:
        _fresh(request,{'action','room_id','created'})
        if request['action'] not in ('lan_session','lan_recover') or not isinstance(request['room_id'],str) or \
                not re.fullmatch('[0-9a-f]{32}',request['room_id']):
            raise ProfileError('The network request is invalid or expired.')
        native=WindowsWireGuard()
        if request['action']=='lan_recover':
            recover(native,journal)
            status.save({'state':'stopped','room':request['room_id'],'updated':time.time()})
            store.save({'done':True,'result':{'state':'stopped'}})
            return 0
        licenses=_new_connection('lan_play')
        _no_vpn()
        if journal.load().get('phase') not in (None,'disconnected'):
            recover(native,journal)                      # a previous session did not end cleanly
        room=request['room_id']
        stop.save({'stop':False,'room':room})
        store.save({'done':True,'result':{'state':'starting'}})
        status.save({'state':'starting','room':room,'updated':time.time()})
        client=LanClient(licenses,SecureStore(sub('network')/'lan-identity'))
        allow=SecureStore(sub('network')/'lan-allow');allow.clear()
        LanSession(room,LanControl(client,room),native,journal,status,
                   lambda:bool(stop.load().get('stop')) or stop.load().get('room')!=room,allow_request=allow.load).run()
        return 0
    except (ProfileError,NativeUnavailable,ValueError) as exc:
        status.save({'state':'failed','error':str(exc)[:300],'updated':time.time()})
        store.save({'done':True,'error':str(exc)[:300]})
    except Exception:
        status.save({'state':'failed','error':'LAN Play stopped unexpectedly; recover before retrying.','updated':time.time()})
        store.save({'done':True,'error':'LAN Play stopped unexpectedly; recover before retrying.'})
    return 1


def vpn(store,request):
    """One managed country-VPN session. Keys/state remain DPAPI-protected and the GUI never gains elevation."""
    from akeno.network.vpn import VPNClient
    from akeno.network.vpn_session import ManagedVPNSession
    journal,status,stop=vpn_stores()
    try:
        action=request.get('action')
        if action=='vpn_session':
            _fresh(request,{'action','country','session_id','created'})
            if not isinstance(request['country'],str) or not re.fullmatch('[A-Z]{2}',request['country']) or \
                    not isinstance(request['session_id'],str) or not re.fullmatch('[0-9a-f]{32}',request['session_id']):
                raise ProfileError('Invalid VPN country or session identity.')
            licenses=_new_connection('vpn')
            _no_lan()
            if _active(journal) or _active(SecureStore(sub('network')/'tunnel')):
                raise ProfileError('Disconnect or recover the previous VPN session first.')
        elif action=='vpn_recover':
            _fresh(request,{'action','created'})
            from akeno.licensing import manager as license_manager
            licenses=license_manager()           # Cleanup must work even when license/module activation expired.
        else:
            raise ProfileError('Unknown managed VPN operation.')
        profiles=ProfileStore(sub('network')/'profiles.sqlite3')
        manager=TunnelManager(profiles,SecureStore(sub('network')/'tunnel'),WindowsWireGuard())
        client=VPNClient(licenses,SecureStore(sub('network')/'vpn-identity'))
        session_id=request.get('session_id') or status.load().get('session_id') or uuid.uuid4().hex
        class Status:
            def save(self,value):status.save({**value,'session_id':session_id})
            def load(self):return status.load()
        def stopped():
            from akeno.components.service import NetworkModules
            value=stop.load()
            return value.get('session_id')!=session_id or bool(value.get('stop')) or \
                not NetworkModules().enabled('vpn') or not licenses.view().unlocked
        session=ManagedVPNSession(client,profiles,manager,journal,Status(),stopped,lan_journal=lan_stores()[0])
        if action=='vpn_recover':
            result=session.recover()
            store.save({'done':True,'result':result})
            return 0
        stop.save({'stop':False,'session_id':session_id})
        Status().save({'state':'starting','country':request['country'],'updated':time.time()})
        store.save({'done':True,'result':{'state':'starting','session_id':session_id}})
        session.run(request['country'])
        return 0
    except (ProfileError,NativeUnavailable,ValueError) as exc:
        status.save({'state':'failed','error':str(exc)[:300],'updated':time.time()})
        store.save({'done':True,'error':str(exc)[:300]})
    except Exception:
        status.save({'state':'failed','error':'AKENO VPN stopped unexpectedly; recover before retrying.','updated':time.time()})
        store.save({'done':True,'error':'AKENO VPN stopped unexpectedly; recover before retrying.'})
    return 1


def execute(ident):
    """Called by the built AKENO.exe before Qt startup. No arbitrary command/path is accepted."""
    if sys.platform!='win32':return 2
    store=request_store(ident);request=store.load()
    try:
        with network_operation():
            return _execute(store,request)
    except (ProfileError,NativeUnavailable) as exc:
        store.save({'done':True,'error':str(exc)[:300]})
    except Exception:
        store.save({'done':True,'error':'The network operation failed; inspect the connection and recover before retrying.'})
    return 1


def _execute(store,request):
    try:
        if not isinstance(request,dict):
            raise ProfileError('The network request is invalid or expired.')
        if request.get('action') in ('lan_session','lan_recover'):
            return lan(store,request)
        if request.get('action') in ('vpn_session','vpn_recover'):
            return vpn(store,request)
        if request.get('action') == 'install_components':
            _fresh(request,{'action','consent','created'})
            if request['consent'] is not True:
                raise ProfileError('The network request is invalid or expired.')
            _no_lan();_no_vpn()
            from akeno.network.bootstrap import install
            result = install(consent=True)
            store.save({'done':True,'result':result})
            return 0
        _fresh(request,{'action','profile_id','created'})
        if request['action'] not in ('connect','disconnect','status','exit_ip') or \
                request['profile_id'] is not None and (not isinstance(request['profile_id'],str) or \
                    not re.fullmatch('[0-9a-f]{32}',request['profile_id'])):
            raise ProfileError('The network request is invalid or expired.')
        action=request['action']
        if action=='connect':
            _new_connection('vpn');_no_lan()
            if _active(vpn_stores()[0]):
                raise ProfileError('Disconnect or recover the managed VPN session first.')
            if _active(SecureStore(sub('network')/'tunnel')):
                raise ProfileError('Disconnect or recover the previous tunnel first.')
        if action=='disconnect' and _active(vpn_stores()[0]):
            raise ProfileError('Recover the managed VPN session to remove its native tunnel and temporary permissions.')
        profiles=ProfileStore(sub('network')/'profiles.sqlite3')
        manager=TunnelManager(profiles,SecureStore(sub('network')/'tunnel'),WindowsWireGuard())
        if action=='connect':
            result=manager.connect(request['profile_id'])
            deadline=time.monotonic()+30
            while result['state'] in ('starting','awaiting_handshake') and time.monotonic()<deadline:
                time.sleep(1);result=manager.status()
        elif action=='disconnect':result=manager.disconnect()
        elif action=='status':result=manager.status()
        else:result={**manager.status(),'exit_ip':manager.exit_ip()}
        store.save({'done':True,'result':result})
        return 0
    except (ProfileError,NativeUnavailable) as exc:
        store.save({'done':True,'error':str(exc)})
    except Exception:
        store.save({'done':True,'error':'The network operation failed; inspect the connection and recover before retrying.'})
    return 1


def stop_lan(room):
    """Asks a running LAN session to end (it removes its tunnel and firewall rules itself). No elevation needed."""
    _,_,stop=lan_stores()
    stop.save({'stop':True,'room':room})


def allow_game(room,program,game_id):
    """Asks the running LAN session to allow one game program for this room (it validates the path itself)."""
    from akeno.network.games import profile
    profile(game_id)
    SecureStore(sub('network')/'lan-allow').save({'room':room,'program':str(program),'game_id':game_id,'id':uuid.uuid4().hex})


def lan_status():
    _,status,_=lan_stores()
    return status.load()


def stop_vpn():
    """Stop only the current managed session nonce. No elevation, routes or native commands in the GUI."""
    _,status,stop=vpn_stores()
    session_id=status.load().get('session_id')
    if not re.fullmatch('[0-9a-f]{32}',str(session_id)):
        raise ProfileError('No managed VPN session is available. Recover its network state before retrying.')
    stop.save({'stop':True,'session_id':session_id})


def vpn_status():
    return vpn_stores()[1].load()


def invoke(action,profile_id=None,*,consent=False):
    """Only explicit UI actions call this. Administrator rights are scoped to one helper process."""
    if sys.platform!='win32' or not getattr(sys,'frozen',False):
        raise NativeUnavailable('Network elevation is available in the built Windows application only.')
    if action not in ('connect','disconnect','status','exit_ip','lan_session','lan_recover','install_components',
                      'vpn_session','vpn_recover'):
        raise ProfileError('Unknown network operation.')
    ident=uuid.uuid4().hex;store=request_store(ident)
    if action == 'install_components':
        if consent is not True:
            raise ProfileError('Explicit consent is required to install the network components.')
        store.save({'action':action,'consent':True,'created':time.time()})
    elif action=='vpn_session':
        if not isinstance(profile_id,str) or not re.fullmatch('[A-Z]{2}',profile_id):
            raise ProfileError('Select an available VPN country.')
        store.save({'action':action,'country':profile_id,'session_id':uuid.uuid4().hex,'created':time.time()})
    elif action=='vpn_recover':
        if profile_id is not None:raise ProfileError('VPN recovery does not accept a profile or country.')
        store.save({'action':action,'created':time.time()})
    elif action.startswith('lan_'):
        if not re.fullmatch('[0-9a-f]{32}',str(profile_id)):raise ProfileError('Invalid room identity.')
        store.save({'action':action,'room_id':profile_id,'created':time.time()})
    else:
        store.save({'action':action,'profile_id':profile_id,'created':time.time()})
    from ctypes import wintypes as w
    class ShellInfo(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),
                  ('lpFile',w.LPCWSTR),('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),('nShow',ctypes.c_int),
                  ('hInstApp',w.HINSTANCE),('lpIDList',ctypes.c_void_p),('lpClass',w.LPCWSTR),
                  ('hkeyClass',w.HKEY),('dwHotKey',w.DWORD),('hIcon',w.HANDLE),('hProcess',w.HANDLE)]
    info=ShellInfo();info.cbSize=ctypes.sizeof(ShellInfo);info.fMask=0x40
    info.lpVerb='runas';info.lpFile=sys.executable
    info.lpParameters=subprocess.list2cmdline(['--network-helper',ident]);info.nShow=0
    shell=ctypes.WinDLL('shell32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ShellInfo)];shell.ShellExecuteExW.restype=w.BOOL
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
    kernel.CloseHandle.argtypes=[w.HANDLE]
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise NativeUnavailable('Network elevation was declined or could not start.')
        if action in ('lan_session','vpn_session'):
            # The session keeps running in its own elevated process; wait only until it has accepted the request.
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                result=store.load()
                if result.get('done'):
                    if result.get('error'):raise NativeUnavailable(result['error'])
                    return result['result']
                if kernel.WaitForSingleObject(info.hProcess,250)==0:break
            result=store.load()
            if result.get('error'):raise NativeUnavailable(result['error'])
            raise NativeUnavailable('The network session did not start. Recover before retrying.')
        timeout=300000 if action=='install_components' else 90000
        if kernel.WaitForSingleObject(info.hProcess,timeout)!=0:
            raise NativeUnavailable('The network helper did not finish. Recover the tunnel before retrying.')
        result=store.load()
        if not result.get('done'):raise NativeUnavailable('The network helper did not return a result.')
        if result.get('error'):raise NativeUnavailable(result['error'])
        return result['result']
    finally:
        if info.hProcess:kernel.CloseHandle(info.hProcess)
        if action not in ('lan_session','vpn_session') or store.load().get('done'):store.clear()
