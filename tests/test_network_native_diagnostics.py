"""Native error classification must never expose native output or script arguments."""
import json
from types import SimpleNamespace

import pytest

from akeno.network import lan_windows as native


@pytest.mark.parametrize('code',[87,5,1])
def test_native_failure_preserves_safe_error_identifier(monkeypatch,code):
    monkeypatch.setattr(native,'_system_tool',lambda *parts:'powershell.exe')
    data={'command':'New-NetFirewallRule','id':'Windows System Error 87,New-NetFirewallRule','hresult':code}
    def run(argv,**kwargs):
        assert 'catch' in kwargs['input'] and 'InvocationInfo.MyCommand.Name' in kwargs['input']
        return SimpleNamespace(returncode=1,stdout='SECRET',stderr='SECRET\nAKENO_NATIVE_ERROR:'+json.dumps(data))
    monkeypatch.setattr(native.subprocess,'run',run)
    with pytest.raises(native.NativeUnavailable) as error:native.powershell('SECRET script')
    assert 'cmdlet=New-NetFirewallRule' in str(error.value) and f'hresult={code}' in str(error.value)
    assert 'SECRET' not in str(error.value)


def test_unrecognized_native_output_is_never_displayed(monkeypatch):
    monkeypatch.setattr(native,'_system_tool',lambda *parts:'powershell.exe')
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=1,stdout='',stderr='AKENO_NATIVE_ERROR:{"command":"SECRET command"}\nSECRET private output'))
    with pytest.raises(native.NativeUnavailable,match='exit=1') as error:native.powershell('anything')
    assert 'SECRET' not in str(error.value)
