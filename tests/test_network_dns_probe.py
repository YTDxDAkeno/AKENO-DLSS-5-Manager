"""Strict DNS measurement policy with native stand-ins; real Windows evidence is separate."""
import json
from types import SimpleNamespace

import pytest

from akeno.network import dns_probe as probe

NAME='AkenoVPN'+'a'*24


def native_document():
    return {'index':9,'dns':['1.1.1.1'],'routes':[NAME], 'addresses':[
        {'InterfaceIndex':9,'IPAddress':'10.204.1.2','InterfaceAlias':NAME},
        {'InterfaceIndex':3,'IPAddress':'192.168.1.2','InterfaceAlias':'Wi-Fi'},
        {'InterfaceIndex':3,'IPAddress':'2001:4860::1234','InterfaceAlias':'Wi-Fi'}]}


def test_dns_checks_require_positive_resolver_and_every_negative_path(monkeypatch):
    monkeypatch.setattr(probe.sys,'platform','win32')
    calls=[]
    def deny(address,**kw):calls.append((address,kw));return True
    result=probe.verify(NAME,['1.1.1.1'],run=lambda *a,**k:json.dumps(native_document()),
                        resolve=lambda address:True,deny=deny)
    assert result['dns_verified'] is True and result['denied_paths']==6
    assert all(kw['interface'] is not None for _,kw in calls)
    assert all(address!='1.1.1.1' for address,_ in calls)
    assert any(kw['source']=='192.168.1.2' and kw['port']==853 for _,kw in calls)
    assert any(kw['source']=='2001:4860::1234' for _,kw in calls)


@pytest.mark.parametrize('failure',['resolver','accepted','missing_routes','network_change'])
def test_dns_failure_never_returns_verified(monkeypatch,failure):
    monkeypatch.setattr(probe.sys,'platform','win32')
    calls=[]
    def native(*a,**kw):
        value=native_document();calls.append(1)
        if failure=='missing_routes':value['routes']=[]
        if failure=='network_change' and len(calls)>1:value['addresses'][1]['IPAddress']='192.168.1.3'
        return json.dumps(value)
    with pytest.raises(probe.DNSVerificationError):
        probe.verify(NAME,['1.1.1.1'],run=native,resolve=lambda address:failure!='resolver',
                     deny=lambda *a,**kw:failure!='accepted')


@pytest.mark.parametrize('winerror',[10013,10051,10060,None])
def test_only_explicit_send_access_denial_counts(monkeypatch,winerror):
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def sendto(self,*args):
            error=OSError('native text');error.winerror=winerror;raise error
    monkeypatch.setattr(probe,'_socket',lambda *a,**kw:Socket())
    if winerror==10013:assert probe.blocked('8.8.8.8') is True
    else:
        with pytest.raises(probe.DNSVerificationError):probe.blocked('8.8.8.8')


def test_reply_is_bound_to_nonce_question_and_response_flags():
    query=probe.question();answer=query[:2]+b'\x81\x83'+query[4:]
    assert probe.valid_reply(query,answer)
    assert not probe.valid_reply(query,query)
    assert not probe.valid_reply(query,b'bad')
    altered=bytearray(answer);altered[-5]^=1
    assert not probe.valid_reply(query,bytes(altered))
    assert not probe.valid_reply(probe.question(),answer)
