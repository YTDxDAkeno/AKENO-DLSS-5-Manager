"""Explicit UAC helper with a fixed operation vocabulary and per-user DPAPI request/result files."""
from __future__ import annotations
import ctypes
import re
import subprocess
import sys
import time
import uuid
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


def lan(store,request):
    """One LAN Play session (runs until the GUI sets the stop flag) or the recovery after a crash."""
    from akeno.network.lan import LanControl
    from akeno.network.lan_windows import LanSession,recover
    journal,status,stop=lan_stores()
    try:
        if set(request)!={'action','room_id','created'} or type(request['created']) not in (int,float) or \
                not 0<=time.time()-request['created']<=120 or not re.fullmatch('[0-9a-f]{32}',str(request['room_id'])):
            raise ProfileError('The network request is invalid or expired.')
        native=WindowsWireGuard()
        if request['action']=='lan_recover':
            recover(native,journal)
            status.save({'state':'stopped','room':request['room_id'],'updated':time.time()})
            store.save({'done':True,'result':{'state':'stopped'}})
            return 0
        from akeno.licensing import manager as license_manager
        licenses=license_manager()
        if not licenses.view().unlocked:
            raise ProfileError('An active AKENO license is required for LAN Play. Leaving and recovery remain available.')
        if journal.load().get('phase') not in (None,'disconnected'):
            recover(native,journal)                      # a previous session did not end cleanly
        room=request['room_id']
        stop.save({'stop':False,'room':room})
        store.save({'done':True,'result':{'state':'starting'}})
        status.save({'state':'starting','room':room,'updated':time.time()})
        client=LanClient(licenses,SecureStore(sub('network')/'lan-identity'))
        LanSession(room,LanControl(client,room),native,journal,status,
                   lambda:bool(stop.load().get('stop')) or stop.load().get('room')!=room).run()
        return 0
    except (ProfileError,NativeUnavailable,ValueError) as exc:
        status.save({'state':'failed','error':str(exc)[:300],'updated':time.time()})
        store.save({'done':True,'error':str(exc)[:300]})
    except Exception:
        status.save({'state':'failed','error':'LAN Play stopped unexpectedly; recover before retrying.','updated':time.time()})
        store.save({'done':True,'error':'LAN Play stopped unexpectedly; recover before retrying.'})
    return 1


def execute(ident):
    """Called by the built AKENO.exe before Qt startup. No arbitrary command/path is accepted."""
    if sys.platform!='win32':return 2
    store=request_store(ident);request=store.load()
    if request.get('action') in ('lan_session','lan_recover'):
        return lan(store,request)
    try:
        if set(request)!={'action','profile_id','created'} or request['action'] not in ('connect','disconnect','status','exit_ip') \
                or type(request['created']) not in (int,float) or not 0<=time.time()-request['created']<=120:
            raise ProfileError('The network request is invalid or expired.')
        profiles=ProfileStore(sub('network')/'profiles.sqlite3')
        manager=TunnelManager(profiles,SecureStore(sub('network')/'tunnel'),WindowsWireGuard())
        action=request['action']
        if action=='connect':
            from akeno.licensing import manager as license_manager
            if not license_manager().view().unlocked:
                raise ProfileError('An active AKENO license is required to connect. Disconnect and recovery remain available.')
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


def lan_status():
    _,status,_=lan_stores()
    return status.load()


def invoke(action,profile_id=None):
    """Only explicit UI actions call this. Administrator rights are scoped to one helper process."""
    if sys.platform!='win32' or not getattr(sys,'frozen',False):
        raise NativeUnavailable('Network elevation is available in the built Windows application only.')
    if action not in ('connect','disconnect','status','exit_ip','lan_session','lan_recover'):
        raise ProfileError('Unknown network operation.')
    ident=uuid.uuid4().hex;store=request_store(ident)
    if action.startswith('lan_'):
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
        if action=='lan_session':
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
            raise NativeUnavailable('LAN Play did not start. Recover before retrying.')
        if kernel.WaitForSingleObject(info.hProcess,90000)!=0:
            raise NativeUnavailable('The network helper did not finish. Recover the tunnel before retrying.')
        result=store.load()
        if not result.get('done'):raise NativeUnavailable('The network helper did not return a result.')
        if result.get('error'):raise NativeUnavailable(result['error'])
        return result['result']
    finally:
        if info.hProcess:kernel.CloseHandle(info.hProcess)
        if action!='lan_session' or store.load().get('done'):store.clear()
