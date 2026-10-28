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
from akeno.network.profiles import ProfileStore, ProfileError
from akeno.network.tunnels import TunnelManager
from akeno.network.windows import NativeUnavailable, WindowsWireGuard


def request_store(ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Invalid helper request.')
    return SecureStore(sub('network')/'requests'/ident)


def execute(ident):
    """Called by the built AKENO.exe before Qt startup. No arbitrary command/path is accepted."""
    if sys.platform!='win32':return 2
    store=request_store(ident);request=store.load()
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


def invoke(action,profile_id=None):
    """Only explicit UI actions call this. Administrator rights are scoped to one helper process."""
    if sys.platform!='win32' or not getattr(sys,'frozen',False):
        raise NativeUnavailable('Network elevation is available in the built Windows application only.')
    if action not in ('connect','disconnect','status','exit_ip'):raise ProfileError('Unknown network operation.')
    ident=uuid.uuid4().hex;store=request_store(ident)
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
        if kernel.WaitForSingleObject(info.hProcess,90000)!=0:
            raise NativeUnavailable('The network helper did not finish. Recover the tunnel before retrying.')
        result=store.load()
        if not result.get('done'):raise NativeUnavailable('The network helper did not return a result.')
        if result.get('error'):raise NativeUnavailable(result['error'])
        return result['result']
    finally:
        if info.hProcess:kernel.CloseHandle(info.hProcess)
        store.clear()
