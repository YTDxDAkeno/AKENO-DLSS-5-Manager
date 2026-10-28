"""Owned Windows-container fixture for native host firewall boundary tests.

All addresses are on a dedicated private NAT switch. No port is published on a
public NIC. Cleanup removes only this instance's named resources.
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
import threading
import time
import uuid

import wgpeer
from windows_peer_rpc import encode, decode


class ContainerPeer:
    ipv4 = staticmethod(wgpeer.Responder.ipv4)
    udp_packet = wgpeer.Responder.udp_packet
    tcp_syn = wgpeer.Responder.tcp_syn

    def __init__(self, image='akeno-windows-peer:e2e'):
        self.name = 'akeno-peer-' + uuid.uuid4().hex[:12]
        self.image = image
        self.created_network = self.created_container = self.created_rule = False
        self.lock = threading.Lock()

    @staticmethod
    def _docker(*args, input=None, timeout=120):
        result = subprocess.run(['docker', *args], input=input, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('Isolated Windows peer operation failed: ' + ' '.join(args[:2]))
        return result.stdout

    def topology(self):
        # No host port mappings and no host mounts. This is a disposable CI network.
        self._docker('network', 'create', '-d', 'nat', '--subnet', '172.29.77.0/24', self.name)
        self.created_network = True
        info = json.loads(self._docker('network', 'inspect', self.name))[0]
        self.gateway = str(ipaddress.IPv4Address(info['IPAM']['Config'][0]['Gateway']))
        self._docker('run', '-d', '--name', self.name, '--network', self.name, '--isolation', 'process', self.image)
        self.created_container = True
        info = json.loads(self._docker('inspect', self.name))[0]
        self.address = str(ipaddress.IPv4Address(info['NetworkSettings']['Networks'][self.name]['IPAddress']))
        if not ipaddress.ip_address(self.address).is_private or self.address == self.gateway:
            raise RuntimeError('Fixture did not acquire a distinct private network stack.')
        return self

    def allow_relay(self, address, powershell):
        if address[0] != self.gateway or not 1 <= address[1] <= 65535:
            raise ValueError('Fixture relay must bind only its private switch gateway.')
        powershell(f"New-NetFirewallRule -Name '{self.name}' -DisplayName '{self.name}' "
                   f"-Direction Inbound -Action Allow -Protocol UDP -LocalAddress '{self.gateway}' "
                   f"-RemoteAddress '{self.address}' -LocalPort {address[1]} -Profile Any | Out-Null")
        self.created_rule = True

    def start(self, private_key, client_public, addresses):
        data = json.dumps({'private_key': private_key, 'client_public': client_public, 'addresses': addresses})
        self._docker('exec', '-i', self.name, 'python', '-c',
                     "import sys;from pathlib import Path;Path('C:/fixture/keys.json').write_text(sys.stdin.read(),encoding='utf-8')",
                     input=data)
        self._docker('exec', '-d', self.name, 'python', 'C:/fixture/windows_peer_rpc.py', '--serve', 'C:/fixture/keys.json')
        deadline = time.monotonic() + 30
        while True:
            try:
                self._rpc('ready')
                return self
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Isolated Windows peer did not become ready.') from None
                time.sleep(.5)

    def _rpc(self, method, *args):
        with self.lock:
            raw = self._docker('exec', '-i', self.name, 'python', 'C:/fixture/windows_peer_rpc.py', '--call',
                               input=json.dumps(encode({'method': method, 'args': args})), timeout=15)
        data = decode(json.loads(raw))
        if 'error' in data:
            raise RuntimeError('Isolated peer rejected fixture request: ' + data['error'])
        return data['result']

    def prove(self, key, address):
        return self._rpc('prove', key, address)

    def keepalive(self):
        return self._rpc('keepalive')

    def inbox_snapshot(self):
        return self._rpc('inbox')

    def send_ip(self, packet):
        return self._rpc('send_ip', packet)

    @property
    def accept_from(self):
        raise AttributeError('Write-only fixture property.')

    @accept_from.setter
    def accept_from(self, addresses):
        self._rpc('accept_from', list(addresses))

    def _member_session(self):
        if self._rpc('session') is None:
            return None
        peer = self

        class Session:
            @property
            def addr(self):
                return tuple(peer._rpc('session'))

            @addr.setter
            def addr(self, address):
                peer._rpc('set_address', address)
        return Session()

    def stop(self):
        # The outer finally also calls close, including failures during topology/start.
        pass

    def close(self, powershell):
        errors = []
        for created, action in (
            (self.created_container, lambda: self._docker('rm', '-f', self.name)),
            (self.created_rule, lambda: powershell(f"Remove-NetFirewallRule -Name '{self.name}' -ErrorAction Stop")),
            (self.created_network, lambda: self._docker('network', 'rm', self.name)),
        ):
            if created:
                try:
                    action()
                except Exception:
                    errors.append('fixture cleanup failed')
        if errors:
            raise RuntimeError('; '.join(errors))
