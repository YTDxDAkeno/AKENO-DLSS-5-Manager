"""Real local UDP sockets for lease, source, packet-format and revocation enforcement; not a tunnel test."""
import socket,threading,time
import pytest
from akeno_server.network.relay import PairedUDPRelay


def test_relay_source_binding_revocation_and_no_arbitrary_datagram_forwarding():
    a,b,other=(socket.socket(socket.AF_INET,socket.SOCK_DGRAM) for _ in range(3))
    for s in (a,b,other):s.bind(('127.0.0.1',0));s.settimeout(.2)
    allowed=[True]
    relay=PairedUDPRelay(a.getsockname(),b.getsockname(),lambda:allowed[0],time.time()+3)
    ports=relay.ports;thread=threading.Thread(target=relay.serve,daemon=True);thread.start()
    packet=b'\x04\0\0\0'+b'\0'*28
    try:
        a.sendto(packet,('127.0.0.1',ports[0]))
        data,source=b.recvfrom(2048)
        assert data==packet and source[1]==ports[1]
        other.sendto(packet,('127.0.0.1',ports[0]))
        with pytest.raises(socket.timeout):b.recvfrom(2048)
        a.sendto(b'arbitrary non-WireGuard data',('127.0.0.1',ports[0]))
        with pytest.raises(socket.timeout):b.recvfrom(2048)
        allowed[0]=False;a.sendto(packet,('127.0.0.1',ports[0]))
        with pytest.raises(socket.timeout):b.recvfrom(2048)
        assert relay.forwarded==1 and relay.dropped>=3
    finally:
        relay.stop_event.set();thread.join(2)
        for s in (a,b,other):s.close()


def test_no_long_lived_or_unauthorised_allocations():
    with pytest.raises(ValueError):PairedUDPRelay(('127.0.0.1',10000),('127.0.0.1',10001),lambda:True,time.time()+100)
    with pytest.raises(ValueError):PairedUDPRelay(('127.0.0.1',10000),('127.0.0.1',10001),None,time.time()+1)
