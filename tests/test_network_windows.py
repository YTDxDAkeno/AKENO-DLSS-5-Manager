"""Real Windows DPAPI/DACL checks. These do not establish tunnel/driver or DNS behavior."""
import ctypes
import sys
import pytest
from test_network_profiles import profile
from akeno.network.profiles import ProfileStore

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='requires actual Windows DPAPI and DACL APIs')


def test_profile_is_dpapi_encrypted_and_round_trips_on_real_windows(tmp_path):
    path=tmp_path/'profiles.sqlite3';text=profile();store=ProfileStore(path)
    ident=store.add('Own VPN',text)
    assert text.encode() not in path.read_bytes()
    restored=ProfileStore(path)
    assert restored._load(ident)['text']==text
    assert restored.metadata()[0]['name']=='Own VPN'


def test_plain_import_file_has_protected_dacl_before_data_is_written(tmp_path):
    from ctypes import wintypes as w
    from akeno.network.windows import private_file
    path=tmp_path/'profile.tmp';private_file(path,b'synthetic data')
    assert path.read_bytes()==b'synthetic data'
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    size=w.DWORD()
    adv.GetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    adv.GetFileSecurityW(str(path),4,None,0,ctypes.byref(size))
    descriptor=ctypes.create_string_buffer(size.value)
    assert adv.GetFileSecurityW(str(path),4,descriptor,size,ctypes.byref(size))
    control=w.WORD();revision=w.DWORD()
    adv.GetSecurityDescriptorControl.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.WORD),ctypes.POINTER(w.DWORD)]
    assert adv.GetSecurityDescriptorControl(descriptor,ctypes.byref(control),ctypes.byref(revision))
    assert control.value&0x1000  # SE_DACL_PROTECTED: no inherited world-readable ACL window
