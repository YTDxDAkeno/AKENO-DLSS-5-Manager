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


class WindowsWireGuard:
    def __init__(self):
        if sys.platform!='win32':raise NativeUnavailable('WireGuard tunnel services require Windows.')
        self.root=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'WireGuard'
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

    def status(self,name):
        name=self._name(name)
        p=self._call('wg.exe','show',name,'latest-handshakes',check=False)
        if p.returncode:return {'state':'disconnected','last_handshake':None}
        times=[]
        for line in p.stdout.splitlines():
            parts=line.split()
            if len(parts)!=2 or not parts[1].isdigit():raise NativeUnavailable('WireGuard returned an invalid status.')
            times.append(int(parts[1]))
        latest=max(times,default=0)
        return {'state':'connected' if latest and 0<=time.time()-latest<180 else 'awaiting_handshake',
                'last_handshake':latest or None}

    def stop(self,name):
        name=self._name(name)
        self._call('wireguard.exe','/uninstalltunnelservice',name,check=False)
        if self.status(name)['state']!='disconnected':
            raise NativeUnavailable('Tunnel removal is incomplete; recovery is required.')
        no_links_on_the_way(self.configs,self.root.parent)
        (self.configs/(name+'.conf.dpapi')).unlink(missing_ok=True)
