"""Official WireGuard for Windows adapter. Run only through an explicit elevated helper.

Uses the installed, Authenticode-verified WireGuard manager's secure config import and documented tunnel
service CLI. No driver downloads, registry script enablement, arbitrary shell, default adapter installation,
manual host-route changes or plaintext profile persistence. Real Windows driver validation is separate.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from akeno.network.profiles import parse_wireguard
from akeno.updater.staging import no_links_on_the_way


class NativeUnavailable(RuntimeError):
    pass


def private_file(path:Path,data:bytes):
    """Create a new file with an atomic SYSTEM/current-user-only DACL before writing any plaintext."""
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.LocalFree.argtypes=[w.HLOCAL];kernel.LocalFree.restype=w.HLOCAL
    kernel.CloseHandle.argtypes=[w.HANDLE]
    token=w.HANDLE();size=w.DWORD()
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):
        raise NativeUnavailable('Cannot determine the Windows user.')
    try:
        adv.GetTokenInformation(token,1,None,0,ctypes.byref(size))
        buffer=ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)):
            raise NativeUnavailable('Cannot determine the Windows user.')
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        text=w.LPWSTR()
        adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)]
        if not adv.ConvertSidToStringSidW(sid,ctypes.byref(text)):
            raise NativeUnavailable('Cannot determine the Windows user.')
        try:sddl=f'D:P(A;;FA;;;SY)(A;;FA;;;{text.value})'
        finally:kernel.LocalFree(ctypes.cast(text,w.HLOCAL))
    finally:kernel.CloseHandle(token)
    descriptor=ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):
        raise NativeUnavailable('Cannot secure the temporary profile.')
    class Attributes(ctypes.Structure):
        _fields_=[('size',w.DWORD),('descriptor',ctypes.c_void_p),('inherit',w.BOOL)]
    attrs=Attributes(ctypes.sizeof(Attributes),descriptor,False)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(Attributes),w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.WriteFile.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    kernel.FlushFileBuffers.argtypes=[w.HANDLE]
    handle=None
    try:
        handle=kernel.CreateFileW(str(path),0x40000000,0,ctypes.byref(attrs),1,0x80,None)
        if handle==w.HANDLE(-1).value:
            raise NativeUnavailable('Cannot create the protected temporary profile.')
        written=w.DWORD();buffer=ctypes.create_string_buffer(data)
        if not kernel.WriteFile(handle,buffer,len(data),ctypes.byref(written),None) or written.value!=len(data):
            raise NativeUnavailable('Cannot write the protected profile.')
        if not kernel.FlushFileBuffers(handle):raise NativeUnavailable('Cannot flush the protected profile.')
    finally:
        if handle and handle!=w.HANDLE(-1).value:kernel.CloseHandle(handle)
        kernel.LocalFree(descriptor)


SERVICE_STATES={1:'stopped',2:'start_pending',3:'stop_pending',4:'running',5:'continue_pending',6:'pause_pending',
                7:'paused'}


def service_state(name,prefix='WireGuardTunnel$'):
    """SCM state of the official tunnel service, or None when it does not exist. Queries the Service Control Manager
    directly: no subprocess and no PATH lookup. A service marked for deletion still counts until it is gone.
    ``prefix=''`` queries another service by its full name (the WireGuard manager for diagnostics)."""
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    adv.OpenSCManagerW.argtypes=[w.LPCWSTR,w.LPCWSTR,w.DWORD];adv.OpenSCManagerW.restype=ctypes.c_void_p
    adv.OpenServiceW.argtypes=[ctypes.c_void_p,w.LPCWSTR,w.DWORD];adv.OpenServiceW.restype=ctypes.c_void_p
    adv.CloseServiceHandle.argtypes=[ctypes.c_void_p]

    class Status(ctypes.Structure):
        _fields_=[(n,w.DWORD) for n in ('type','state','accepted','exit','service_exit','checkpoint','wait')]
    adv.QueryServiceStatus.argtypes=[ctypes.c_void_p,ctypes.POINTER(Status)]
    scm=adv.OpenSCManagerW(None,None,0x0001)                       # SC_MANAGER_CONNECT
    if not scm:raise NativeUnavailable('Cannot query Windows services.')
    try:
        service=adv.OpenServiceW(scm,prefix+name,0x0004)            # SERVICE_QUERY_STATUS
        if not service:
            error=ctypes.get_last_error()
            if error==1060:return None                            # ERROR_SERVICE_DOES_NOT_EXIST
            if error==1072:return 'deleting'                      # ERROR_SERVICE_MARKED_FOR_DELETE
            raise NativeUnavailable('Cannot query the WireGuard tunnel service.')
        try:
            status=Status()
            if not adv.QueryServiceStatus(service,ctypes.byref(status)):
                raise NativeUnavailable('Cannot query the WireGuard tunnel service.')
            return SERVICE_STATES.get(status.state,'unknown')
        finally:adv.CloseServiceHandle(service)
    finally:adv.CloseServiceHandle(scm)


class WindowsWireGuard:
    # The official CLI returns while the tunnel service is still START_PENDING; its control pipe and a stoppable
    # service state appear later. Every operation waits for the real state instead of guessing.
    READY_TIMEOUT=30

    def __init__(self):
        if sys.platform!='win32':raise NativeUnavailable('WireGuard tunnel services require Windows.')
        # Elevated code must not select an executable directory from user-controlled environment variables.
        folder=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,0x26,None,0,folder)!=0:
            raise NativeUnavailable('Cannot locate the Windows Program Files directory.')
        self.root=Path(folder.value)/'WireGuard'
        self.configs=self.root/'Data'/'Configurations'

    def _trusted(self,name):
        from akeno.security.authenticode import verify
        path=self.root/name
        no_links_on_the_way(path,self.root.parent)
        info=verify(str(path))
        if not info.checked or not info.valid or (info.signer or '').casefold()!='wireguard llc':
            raise NativeUnavailable('Install the official signed WireGuard for Windows first.')
        return path

    @staticmethod
    def _name(name):
        if not re.fullmatch(r'Akeno(?:VPN|LAN)[a-f0-9]{24}',name):
            raise NativeUnavailable('Invalid AKENO tunnel identity.')
        return name

    def _call(self,exe,*args,check=True):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise NativeUnavailable('This network operation needs the explicit elevated AKENO helper.')
        p=subprocess.run([str(self._trusted(exe)),*args],capture_output=True,text=True,timeout=30,
                         creationflags=0x08000000)
        if check and p.returncode:
            raise NativeUnavailable('The official WireGuard service refused the operation.')
        return p

    def start(self,name,text):
        name=self._name(name);profile=parse_wireguard(text)
        # A user-writable transient import must never gain script execution through a previously enabled
        # WireGuard registry override. Read only; AKENO never changes the Windows/WireGuard security policy.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'Software\WireGuard') as key:
                dangerous,_=winreg.QueryValueEx(key,'DangerousScriptExecution')
        except FileNotFoundError:dangerous=0
        if dangerous:
            raise NativeUnavailable('WireGuard script execution must be disabled before AKENO imports profiles.')
        # WireGuard manager must already exist: never silently install a background manager/adapter.
        self._trusted('wireguard.exe')
        if not self.configs.is_dir():
            raise NativeUnavailable('Start the official WireGuard manager once before importing a tunnel.')
        no_links_on_the_way(self.configs,self.root.parent)
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise NativeUnavailable('This network operation requires elevation.')
        temp=self.configs/(name+'.tmp');plain=self.configs/(name+'.conf');encrypted=self.configs/(name+'.conf.dpapi')
        if any(p.exists() for p in (temp,plain,encrypted)):
            raise NativeUnavailable('A previous tunnel needs to be disconnected or recovered first.')
        try:
            private_file(temp,profile.text.encode('utf-8'))
            os.replace(temp,plain)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and not (encrypted.is_file() and not plain.exists()):time.sleep(0.1)
            if not encrypted.is_file() or plain.exists():
                raise NativeUnavailable('The official manager did not encrypt the imported profile.')
            self._call('wireguard.exe','/installtunnelservice',str(encrypted))
        finally:
            # A failed manager import must never leave plaintext profile data on disk.
            temp.unlink(missing_ok=True);plain.unlink(missing_ok=True)
        if not self._ready(name):
            raise NativeUnavailable('The WireGuard tunnel service did not start.')

    def _ready(self,name):
        """True once the tunnel answers on its control pipe; False if its service stops or never comes up."""
        deadline=time.monotonic()+self.READY_TIMEOUT
        while time.monotonic()<deadline:
            if self._call('wg.exe','show',name,'public-key',check=False).returncode==0:return True
            if service_state(name) in (None,'stopped','deleting'):return False
            time.sleep(0.25)
        return False

    def _gone(self,name):
        deadline=time.monotonic()+self.READY_TIMEOUT
        while time.monotonic()<deadline:
            if service_state(name) is None:return True
            time.sleep(0.25)
        return False

    def status(self,name):
        name=self._name(name)
        p=self._call('wg.exe','show',name,'latest-handshakes',check=False)
        if p.returncode:
            state=service_state(name)
            if state is None:return {'state':'disconnected','last_handshake':None}
            # Installed but not answering yet (or any more): never reported as disconnected.
            return {'state':{'start_pending':'starting','running':'starting','stop_pending':'stopping',
                             'deleting':'stopping'}.get(state,'failed'),'last_handshake':None}
        times=[]
        for line in p.stdout.splitlines():
            parts=line.split()
            if len(parts)!=2 or not parts[1].isdigit():raise NativeUnavailable('WireGuard returned an invalid status.')
            times.append(int(parts[1]))
        latest=max(times,default=0)
        return {'state':'connected' if latest and 0<=time.time()-latest<180 else 'awaiting_handshake',
                'last_handshake':latest or None}

    def stop(self,name,public_key):
        name=self._name(name)
        if service_state(name) is None:
            if (self.configs/(name+'.conf.dpapi')).exists():
                raise NativeUnavailable('An encrypted orphan remains. Remove this AKENO profile in the official manager, then recover.')
            return
        # A starting service cannot be stopped yet (the official uninstall would only mark it for deletion and
        # leave it running), and its key can only be proven once its control pipe answers.
        if not self._ready(name):
            raise NativeUnavailable('The tunnel service does not respond; remove it in the official WireGuard manager, then recover.')
        actual=self._call('wg.exe','show',name,'public-key',check=False)
        if actual.returncode or actual.stdout.strip()!=public_key:
            raise NativeUnavailable('The tunnel key does not belong to this imported profile; removal refused.')
        self._call('wireguard.exe','/uninstalltunnelservice',name,check=False)
        if not self._gone(name):
            raise NativeUnavailable('Tunnel removal is incomplete; recovery is required.')
        no_links_on_the_way(self.configs,self.root.parent)
        (self.configs/(name+'.conf.dpapi')).unlink(missing_ok=True)
