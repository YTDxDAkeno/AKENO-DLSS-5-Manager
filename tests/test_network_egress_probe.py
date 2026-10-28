"""Explicit transport stand-ins verify binding/TLS policy, not an actual country exit."""
import json
import socket
from types import SimpleNamespace

import pytest

from akeno.network import egress_probe as p


@pytest.fixture
def transport(monkeypatch):
    state=SimpleNamespace(events=[],status=200,payload=b'{"ip":"8.8.8.8"}',target='1.1.1.1')
    monkeypatch.setattr(p.socket,'getaddrinfo',lambda host,port,family,kind:
                        [(family,kind,6,'',(state.target,port))])
    class Raw:
        def __init__(self,*args):state.events.append(('socket',args))
        def settimeout(self,value):state.events.append(('timeout',value))
        def setsockopt(self,*args):state.events.append(('interface',args))
        def bind(self,address):state.events.append(('bind',address))
        def connect(self,address):state.events.append(('connect',address))
        def close(self):state.events.append(('close',))
    class TLS:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def sendall(self,request):state.events.append(('request',request))
    class Context:
        def wrap_socket(self,raw,*,server_hostname):
            state.events.append(('verified_tls_sni',server_hostname));return TLS()
    def context():state.events.append(('default_trust',));return Context()
    monkeypatch.setattr(p.socket,'socket',Raw)
    monkeypatch.setattr(p.ssl,'create_default_context',context)
    class Response:
        def __init__(self,*args):self.status=state.status
        def begin(self):pass
        def read(self,size):return state.payload[:size]
    monkeypatch.setattr(p.http.client,'HTTPResponse',Response)
    return state


@pytest.mark.parametrize('version',[4,6])
def test_exit_https_is_pinned_to_assigned_source_interface_and_verified_hostname(transport,version):
    t=transport
    source='10.204.1.2' if version==4 else 'fd42:204:0:1::2'
    expected='8.8.8.8' if version==4 else '2606:4700:4700::1111'
    host='api.ipify.org' if version==4 else 'api6.ipify.org'
    if version==6:t.target='2606:4700:4700::1001'
    t.payload=json.dumps({'ip':expected}).encode()
    assert p.observe(source,17)==expected
    expected_index=socket.htonl(17) if version==4 else 17
    assert ('interface',(socket.IPPROTO_IP if version==4 else socket.IPPROTO_IPV6,31,expected_index)) in t.events
    bind=t.events.index(('bind',(source,0)))
    connect=t.events.index(('connect',(t.target,443)))
    assert bind<connect<t.events.index(('default_trust',))<t.events.index(('verified_tls_sni',host))
    request=next(e[1] for e in t.events if e[0]=='request')
    assert b'Authorization' not in request and ('Host: '+host+'\r\n').encode() in request
    assert t.events[-1]==('close',)


@pytest.mark.parametrize('payload,status',[(b'{}',200),(b'{"ip":"127.0.0.1"}',200),
    (b'{"ip":"::1"}',200),(b'x'*4097,200),(b'{"ip":"8.8.8.8"}',302)])
def test_untrusted_exit_observation_never_succeeds_or_redirects(transport,payload,status):
    transport.payload=payload;transport.status=status
    with pytest.raises(ValueError):p.observe('10.204.1.2',17)
    assert len([e for e in transport.events if e[0]=='connect'])==1
    assert transport.events[-1]==('close',)


def test_private_dns_destination_and_unassigned_source_are_rejected_before_connect(transport):
    transport.target='127.0.0.1'
    with pytest.raises(p.EgressError):p.observe('10.204.1.2',17)
    with pytest.raises(p.EgressError):p.observe('192.168.1.2',17)
    with pytest.raises(p.EgressError):p.observe('10.204.1.2',True)
    assert not transport.events
