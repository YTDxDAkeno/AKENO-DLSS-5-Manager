"""Actual local UDP discovery and malformed-response checks; not ISP NAT or successful P2P evidence."""
import socket,struct,threading
import pytest
from akeno.network.stun import MAGIC,discover,parse_response,request


def response(tx,ip='127.0.0.1',port=51820):
    encoded=bytes(a^b for a,b in zip(socket.inet_aton(ip),struct.pack('!I',MAGIC)))
    value=b'\0\x01'+struct.pack('!H',port^(MAGIC>>16))+encoded
    return struct.pack('!HHI',0x0101,12,MAGIC)+tx+struct.pack('!HH',0x0020,8)+value


def test_local_udp_mapping_matches_actual_source_address_and_port():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('127.0.0.1',0));s.settimeout(2)
    observed=[]
    def server():
        try:
            raw,peer=s.recvfrom(1024);observed.append(peer);s.sendto(response(raw[8:20],*peer),peer)
        finally:s.close()
    port=s.getsockname()[1];thread=threading.Thread(target=server);thread.start()
    result=discover('127.0.0.1',port);thread.join(3)
    assert result==observed[0]


def test_wrong_transaction_truncation_and_invalid_size_refused():
    _,tx=request();data=response(tx)
    assert parse_response(data,tx)==('127.0.0.1',51820)
    for bad in (data[:-1],data+b'x',b'bad',response(b'x'*12)):
        with pytest.raises(ValueError):parse_response(bad,tx)
