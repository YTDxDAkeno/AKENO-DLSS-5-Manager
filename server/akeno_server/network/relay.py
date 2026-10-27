"""Bounded opaque UDP pair forwarder for AKENO-controlled relays, disabled unless explicitly constructed.

The trusted control plane must prove endpoint ownership, verify fresh room/device credentials and provide
`authorise`. It MUST NOT take arbitrary forwarding endpoints from request JSON. This module exposes no API,
starts no listener on import and never decrypts WireGuard packets or becomes an Internet exit.
"""
from __future__ import annotations
import ipaddress,selectors,socket,threading,time


class PairedUDPRelay:
    def __init__(self,peer_a,peer_b,authorise,expires_at,bind='127.0.0.1',clock=time.time,packets_per_second=500):
        if not callable(authorise):raise ValueError('A live membership authoriser is required.')
        if not 0<expires_at-clock()<=45:raise ValueError('Relay allocation must expire within 45 seconds.')
        self.peers=(self._endpoint(peer_a),self._endpoint(peer_b))
        if self.peers[0]==self.peers[1]:raise ValueError('Distinct peer endpoints are required.')
        if not 1<=packets_per_second<=1000:raise ValueError('Invalid relay rate limit.')
        self.authorise,self.expires,self.clock,self.limit=authorise,expires_at,clock,packets_per_second
        self.sockets=[];self.selector=selectors.DefaultSelector();self.stop_event=threading.Event()
        self.forwarded=0;self.dropped=0;self.bytes=0;self._window=int(clock());self._count=0
        try:
            for index in range(2):
                sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind((bind,0));sock.setblocking(False)
                self.sockets.append(sock);self.selector.register(sock,selectors.EVENT_READ,index)
        except Exception:self.close();raise

    @staticmethod
    def _endpoint(endpoint):
        host,port=endpoint;ip=ipaddress.IPv4Address(host)
        if ip.is_multicast or ip.is_unspecified or not isinstance(port,int) or not 1024<=port<=65535:
            raise ValueError('Invalid proven peer endpoint.')
        return str(ip),port

    @property
    def ports(self):return tuple(s.getsockname()[1] for s in self.sockets)

    @staticmethod
    def wireguard_packet(data):
        if not 32<=len(data)<=2048:return False
        kind=int.from_bytes(data[:4],'little')
        return (kind==1 and len(data)==148) or (kind==2 and len(data)==92) or \
               (kind==3 and len(data)==64) or (kind==4 and (len(data)-32)%16==0)

    def serve(self):
        try:
            while not self.stop_event.is_set() and self.clock()<self.expires:
                for key,_ in self.selector.select(.1):
                    index=key.data
                    data,source=key.fileobj.recvfrom(2049)
                    now=self.clock();window=int(now)
                    if window!=self._window:self._window,self._count=window,0
                    self._count+=1
                    try:allowed=self.authorise()
                    except Exception:allowed=False
                    if not allowed or now>=self.expires or source!=self.peers[index] or \
                            self._count>self.limit or not self.wireguard_packet(data):
                        self.dropped+=1;continue
                    # Each peer sees the same relay socket/port it was configured to use. No packet amplification.
                    self.sockets[1-index].sendto(data,self.peers[1-index])
                    self.forwarded+=1;self.bytes+=len(data)
        finally:self.close()

    def close(self):
        self.stop_event.set()
        for sock in self.sockets:
            try:self.selector.unregister(sock)
            except Exception:pass
            sock.close()
        self.sockets=[]
        self.selector.close()
