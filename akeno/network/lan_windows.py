"""LAN Play on Windows: the official WireGuard tunnel service, a least-privilege firewall and the session loop.

Runs only inside the explicit elevated AKENO helper (one UAC prompt per LAN session). Inputs are the server's signed
peer credentials and a stop flag; nothing else steers it.

Firewall policy, on the LAN adapter only and only for the room's /24:
* LAN Play refuses to start if Windows Defender Firewall is off for the Public profile, and the adapter stays Public
  (Windows' own default for unidentified networks: unsolicited inbound connections are blocked);
* explicit inbound block rules for Windows file/printer sharing, RPC, NetBIOS, RDP, WinRM and discovery protocols, so
  a user's own "allow" rule for those can never apply to room members (Windows lets block rules win);
* allowed inbound: the AKENO latency probe (UDP 51999) and, only after the user picks one, a single game program.
Rules carry the group "AKENO LAN Play" and are removed when the session ends or is recovered after a crash.
"""
from __future__ import annotations

import base64
import ctypes
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from akeno.network.lan import COORDINATOR_ADDRESS, LAN_NETWORK, LanError, PathController, format_endpoint, \
    interface_profile
from akeno.network.windows import NativeUnavailable

GROUP = 'AKENO LAN Play'
PROBE_PORT = 51999
BLOCKED = (('TCP', '135,139,445,3389,5985,5986,2179'), ('UDP', '137,138,1900,3702,5353,5355,3389'))
TUNNEL = re.compile(r'AkenoLAN[a-f0-9]{24}')


def tunnel_name(room: str) -> str:
    if not re.fullmatch('[a-f0-9]{32}', room or ''):
        raise LanError('Invalid room identity.')
    return 'AkenoLAN' + room[:24]


def _key(value: str) -> str:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32 or base64.b64encode(raw).decode() != value:
        raise LanError('Invalid WireGuard key.')
    return value


class WindowsLanBackend:
    """PathController backend: runtime peer changes on the official tunnel through wg.exe (fixed verbs, no shell)."""

    def __init__(self, native, name):
        if not TUNNEL.fullmatch(name):
            raise LanError('Invalid LAN tunnel name.')
        self.native, self.name = native, name

    def set_peer(self, key, endpoint, virtual_ip):
        ip = ipaddress.ip_address(virtual_ip)
        if ip not in LAN_NETWORK or str(ip) == COORDINATOR_ADDRESS:
            raise LanError('Peer address outside the LAN Play range.')
        host = str(ipaddress.ip_address(endpoint[0]))
        self.native._call('wg.exe', 'set', self.name, 'peer', _key(key), 'endpoint',
                          format_endpoint((host, int(endpoint[1]))), 'allowed-ips', f'{ip}/32',
                          'persistent-keepalive', '25')

    def remove_peer(self, key):
        self.native._call('wg.exe', 'set', self.name, 'peer', _key(key), 'remove', check=False)

    def peers(self):
        out = {}
        p = self.native._call('wg.exe', 'show', self.name, 'dump', check=False)
        if p.returncode:
            return out
        for line in p.stdout.splitlines()[1:]:
            f = line.split('\t')
            if len(f) < 8:
                continue
            endpoint = None
            if f[2] != '(none)':
                host, _, port = f[2].rpartition(':')
                try:
                    endpoint = (str(ipaddress.ip_address(host.strip('[]'))), int(port))
                except ValueError:
                    endpoint = None
            out[f[0]] = {'endpoint': endpoint, 'latest_handshake': int(f[4]) or None, 'rx': int(f[5]), 'tx': int(f[6])}
        return out


def _system_tool(*parts):
    buffer = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, 260):
        raise NativeUnavailable('Cannot locate the Windows system directory.')
    return str(Path(buffer.value, *parts))


def powershell(script: str, timeout=60) -> str:
    """Runs a fixed script built only from validated values, read from stdin. Deliberately no -EncodedCommand or
    execution-policy override: those are what malware uses, and security products rightly flag them."""
    p = subprocess.run([_system_tool('WindowsPowerShell', 'v1.0', 'powershell.exe'), '-NoProfile', '-NonInteractive',
                        '-Command', '-'], input="$ErrorActionPreference = 'Stop'\n" + script + '\n', capture_output=True, text=True, timeout=timeout,
                       creationflags=0x08000000)
    if p.returncode:
        raise NativeUnavailable('Windows refused the firewall configuration for LAN Play.')
    return p.stdout


def _program(path: str) -> str:
    p = Path(path)
    text = str(p)
    if not p.is_absolute() or p.suffix.lower() != '.exe' or not p.is_file() or len(text) > 260 or \
            any(c in text for c in '\'"`$;\r\n\0') or any(ord(c) < 32 for c in text):
        raise LanError('Choose the game program (.exe) to allow.')
    return text


class LanFirewall:
    def __init__(self, name: str, subnet: str, run=powershell):
        if not TUNNEL.fullmatch(name):
            raise LanError('Invalid LAN tunnel name.')
        net = ipaddress.ip_network(subnet)
        if net.prefixlen != 24 or not net.subnet_of(LAN_NETWORK):
            raise LanError('Room subnet outside the LAN Play range.')
        self.name, self.subnet, self.run = name, str(net), run

    def require_enabled(self):
        enabled = self.run('(Get-NetFirewallProfile -Name Domain,Private,Public).Enabled').split()
        if len(enabled) != 3 or any(value != 'True' for value in enabled):
            raise LanError('Windows Defender Firewall is off for a network profile. LAN Play stays disabled so that file '
                           'sharing, Remote Desktop and other services cannot be reached by room members.')

    def relay_only(self, listen_port: int, relay: tuple[str, int]):
        """Outer UDP isolation, installed before the tunnel starts.

        WireGuardNT sockets may be kernel-owned, so these rules bind to the tunnel's explicit random UDP port,
        rather than assuming Windows attributes packets to a user process/service. A peer cannot cause endpoint
        roaming to bypass the relay: all other remote addresses AND ports are blocked inbound and outbound.
        """
        address = ipaddress.ip_address(relay[0])
        if address.version != 4 or type(listen_port) is not int or not 1024 <= listen_port <= 65535 \
                or type(relay[1]) is not int or not 1 <= relay[1] <= 65535:
            raise LanError('Invalid relay firewall boundary.')
        others = list(ipaddress.ip_network('0.0.0.0/0').address_exclude(ipaddress.ip_network(f'{address}/32')))
        addresses = ','.join("'" + str(value) + "'" for value in [*others, '::/0'])
        ports = []
        if relay[1] > 1: ports.append(f'1-{relay[1]-1}')
        if relay[1] < 65535: ports.append(f'{relay[1]+1}-65535')
        port_list = ','.join("'" + value + "'" for value in ports)
        lines = []
        for direction in ('Inbound', 'Outbound'):
            base = (f"New-NetFirewallRule -DisplayName '{GROUP} {self.name} relay {direction} "
                    f"{{suffix}}' -Group '{GROUP}' -Direction {direction} -Action Block -Protocol UDP "
                    f"-LocalPort {listen_port} -Profile Any ")
            lines.append(base.format(suffix='addresses') + f'-RemoteAddress {addresses} | Out-Null')
            lines.append(base.format(suffix='ports') + f"-RemoteAddress '{address}' -RemotePort {port_list} | Out-Null")
        # This narrow inbound exception is only the controlled relay's socket, never another member.
        lines.append(f"New-NetFirewallRule -DisplayName '{GROUP} {self.name} relay receive' -Group '{GROUP}' "
                     f"-Direction Inbound -Action Allow -Protocol UDP -LocalPort {listen_port} "
                     f"-RemoteAddress '{address}' -RemotePort {relay[1]} -Profile Any | Out-Null")
        self.run('; '.join(lines))

    def apply(self):
        n, s, g = self.name, self.subnet, GROUP
        lines = [f"Set-NetConnectionProfile -InterfaceAlias '{n}' -NetworkCategory Public -ErrorAction SilentlyContinue"]
        for proto, ports in BLOCKED:
            lines.append(f"New-NetFirewallRule -DisplayName '{g} {n} block {proto}' -Group '{g}' -Direction Inbound "
                         f"-Action Block -InterfaceAlias '{n}' -Protocol {proto} -LocalPort {ports} -Profile Any | Out-Null")
        lines.append(f"New-NetFirewallRule -DisplayName '{g} {n} latency probe' -Group '{g}' -Direction Inbound "
                     f"-Action Allow -InterfaceAlias '{n}' -Protocol UDP -LocalPort {PROBE_PORT} -RemoteAddress {s} "
                     "-Profile Any | Out-Null")
        self.run('; '.join(lines))

    def allow_program(self, program: str):
        exe = _program(program)
        n, s, g = self.name, self.subnet, GROUP
        self.run('; '.join(
            f"New-NetFirewallRule -DisplayName '{g} {n} game {proto}' -Group '{g}' -Direction Inbound -Action Allow "
            f"-InterfaceAlias '{n}' -Program '{exe}' -Protocol {proto} -RemoteAddress {s} -Profile Any | Out-Null"
            for proto in ('TCP', 'UDP')))

    def rules(self) -> str:
        return self.run(f"Get-NetFirewallRule -Group '{GROUP}' | Where-Object DisplayName -like '*{self.name}*' | "
                        "Select-Object -ExpandProperty DisplayName")

    def remove(self):
        self.run(f"Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | "
                 f"Where-Object DisplayName -like '*{self.name}*' | Remove-NetFirewallRule")

    @staticmethod
    def remove_all(run=powershell):
        run(f"Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | Remove-NetFirewallRule")


class LatencyProbe:
    """UDP echo on this PC's LAN address plus round-trip measurements to members (both ends run AKENO)."""

    def __init__(self, own_ip: str, wait=15):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        deadline = time.monotonic() + wait
        while True:                          # Windows assigns the LAN address shortly after the tunnel is up
            try:
                self.sock.bind((own_ip, PROBE_PORT))
                break
            except OSError:
                if time.monotonic() > deadline:
                    self.sock.close()
                    raise
                time.sleep(0.25)
        self.sock.settimeout(0.2)
        self.subnet = ipaddress.ip_network(f'{own_ip}/24', strict=False)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) == 16 and data[:4] == b'AKLP' and ipaddress.ip_address(addr[0]) in self.subnet:
                self.sock.sendto(b'AKLR' + data[4:], addr)

    @staticmethod
    def measure(ip: str, timeout=1.0) -> float | None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            nonce = os.urandom(12)
            start = time.perf_counter()
            try:
                s.sendto(b'AKLP' + nonce, (ip, PROBE_PORT))
                while True:
                    data, _ = s.recvfrom(64)
                    if data == b'AKLR' + nonce:
                        return round((time.perf_counter() - start) * 1000, 1)
            except OSError:
                return None

    def close(self):
        self._stop.set()
        self.sock.close()


class LanSession:
    """One LAN Play session in the elevated helper. ``control.refresh()`` returns the verified state:
    {'self': {'public_key','private_key','virtual_ip'}, 'coordinator': {...}, 'peers': [...]}."""
    HEARTBEAT = 15
    TICK = 2

    def __init__(self, room, control, native, journal, status, stop_requested, firewall=LanFirewall, clock=time.time,
                 allow_request=lambda: None):
        self.room, self.control, self.native = room, control, native
        self.journal, self.status, self.stop_requested, self.clock = journal, status, stop_requested, clock
        # The GUI's only other input: "allow this game program" (validated .exe path, applied once per request id).
        self.allow_request = allow_request
        self.name = tunnel_name(room)
        self.firewall_type = firewall

    def run(self):
        state = self.control.refresh()
        own, coord = state['self'], state['coordinator']
        relay_ip = socket.getaddrinfo(coord['host'], coord['port'], socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
        subnet = str(ipaddress.ip_network(f"{own['virtual_ip']}/24", strict=False))
        firewall = self.firewall_type(self.name, subnet)
        firewall.require_enabled()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind(('0.0.0.0', 0))
            listen_port = reservation.getsockname()[1]
        self.journal.save({'phase': 'lan_starting', 'room': self.room, 'name': self.name, 'public_key': own['public_key']})
        probe = None
        try:
            firewall.relay_only(listen_port, (relay_ip, coord['port']))
            self.native.start(self.name, interface_profile(own['private_key'], own['virtual_ip'],
                                                           dict(coord, host=relay_ip), listen_port=listen_port))
            actual_port = self.native._call('wg.exe', 'show', self.name, 'listen-port').stdout.strip()
            if actual_port != str(listen_port):
                raise LanError('WireGuard did not retain the protected LAN listen port.')
            firewall.apply()
            self.journal.save({'phase': 'lan_active', 'room': self.room, 'name': self.name,
                               'public_key': own['public_key']})
            controller = PathController(WindowsLanBackend(self.native, self.name), (relay_ip, coord['port']), subnet,
                                        clock=self.clock)
            controller.apply(state['peers'])
            probe = LatencyProbe(own['virtual_ip'])
            last_refresh, error = self.clock(), None
            handled, allowed = set(), []
            while not self.stop_requested():
                request = self.allow_request()
                if isinstance(request, dict) and request.get('room') == self.room and request.get('id') not in handled:
                    handled.add(request.get('id'))
                    try:
                        firewall.allow_program(request.get('program', ''))
                        allowed.append(Path(request['program']).name)
                    except (LanError, NativeUnavailable) as exc:
                        error = str(exc)[:200]
                if self.clock() - last_refresh >= self.HEARTBEAT:
                    try:
                        state = self.control.refresh()
                        controller.apply(state['peers'])
                        error = None
                    except Exception as exc:           # keep running on leases; expire() removes stale members
                        error = str(exc)[:200]
                    last_refresh = self.clock()
                peers = controller.tick()
                for key, peer in peers.items():
                    peer['latency_ms'] = probe.measure(peer['virtual_ip'], 0.5) if peer['mode'] == 'relay' \
                        else None
                self.status.save({'state': 'active', 'room': self.room, 'virtual_ip': own['virtual_ip'],
                                  'peers': peers, 'error': error, 'allowed': allowed, 'updated': self.clock()})
                time.sleep(self.TICK)
        finally:
            if probe:
                probe.close()
            self.stop()

    def stop(self):
        state = self.journal.load()
        name = state.get('name') or self.name
        try:
            self.native.stop(name, public_key=state.get('public_key'))
        except Exception:
            self.journal.save({**state, 'phase': 'lan_recovery_required'})
            raise
        else:
            self.firewall_type(name, str(ipaddress.ip_network('10.203.0.0/24'))).remove()
            self.journal.save({'phase': 'disconnected'})
            self.status.save({'state': 'stopped', 'room': self.room, 'updated': self.clock()})


def recover(native, journal, run=powershell):
    """After a crash or power loss: remove AKENO LAN firewall rules and the LAN tunnel recorded in the journal."""
    state = journal.load()
    if state.get('phase') in ('lan_starting', 'lan_active', 'lan_recovery_required') and TUNNEL.fullmatch(state.get('name', '')):
        native.stop(state['name'], public_key=state.get('public_key'))
        LanFirewall(state['name'], '10.203.0.0/24', run=run).remove()
    journal.save({'phase': 'disconnected'})

