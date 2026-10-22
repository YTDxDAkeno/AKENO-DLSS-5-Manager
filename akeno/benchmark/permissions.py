"""Read current Windows token groups. Never changes membership or requests elevation."""
from akeno.paths import is_windows


def capture_permission():
    if not is_windows():return {'state':'unavailable','detail':'PresentMon capture needs Windows.'}
    try:
        import ctypes
        from ctypes import wintypes
        advapi=ctypes.WinDLL('advapi32',use_last_error=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        advapi.ConvertStringSidToSidW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_void_p)]
        advapi.CheckTokenMembership.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(wintypes.BOOL)]
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        found=[]
        for name,sid_string in [('Administrators','S-1-5-32-544'),('Performance Log Users','S-1-5-32-559')]:
            sid=ctypes.c_void_p();member=wintypes.BOOL()
            if not advapi.ConvertStringSidToSidW(sid_string,ctypes.byref(sid)):return {'state':'unknown','detail':'Token SID inspection failed.'}
            try:
                if not advapi.CheckTokenMembership(None,sid,ctypes.byref(member)):return {'state':'unknown','detail':'Token membership could not be read.'}
                if member.value:found.append(name)
            finally:kernel.LocalFree(sid)
        return {'state':'allowed' if found else 'permission_required',
                'detail':', '.join(found) if found else 'ETW capture needs an elevated token or Performance Log Users membership. Group changes require an administrator and a new sign-in; AKENO does not change membership.'}
    except (OSError,AttributeError):return {'state':'unknown','detail':'ETW token permission could not be inspected.'}
