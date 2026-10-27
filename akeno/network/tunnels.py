"""Journaled profile connections through a fixed native backend; no false connected/exit-IP states."""
from __future__ import annotations
import ipaddress
import json
import re
import threading
import urllib.request
from akeno.network.profiles import ProfileError, parse_wireguard


class TunnelManager:
    def __init__(self,profiles,journal,backend):
        self.profiles,self.journal,self.backend=profiles,journal,backend
        self._lock=threading.RLock()

    @staticmethod
    def name(ident):
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise ProfileError('Invalid profile identity.')
        return 'AkenoVPN'+ident[:24]

    def connect(self,ident):
        with self._lock:
            if self.journal.load().get('phase') not in (None,'disconnected'):
                raise ProfileError('Disconnect or recover the previous tunnel first.')
            data=self.profiles._load(ident);p=parse_wireguard(data['text']);name=self.name(ident)
            if p.routing=='full_ipv4' or p.routing=='full_ipv4_ipv6' and \
                    (not p.dns or {ipaddress.ip_interface(a).version for a in p.addresses}!={4,6}):
                raise ProfileError('Full tunnel requires IPv4 and IPv6 default routes, addresses and explicit DNS.')
            self.journal.save({'phase':'connecting','profile_id':ident,'name':name})
            try:
                self.backend.start(name,p.text)
                self.journal.save({'phase':'awaiting_handshake','profile_id':ident,'name':name})
                return self.status()
            except Exception:
                self.journal.save({'phase':'recovery_required','profile_id':ident,'name':name})
                try:self.disconnect()
                except Exception:pass
                raise

    def disconnect(self):
        with self._lock:
            state=self.journal.load()
            if not state or state.get('phase')=='disconnected':return {'state':'disconnected'}
            name=self.name(state.get('profile_id'))
            if state.get('name')!=name:raise ProfileError('Tunnel recovery state is damaged.')
            self.journal.save({**state,'phase':'disconnecting'})
            try:self.backend.stop(name)
            except Exception:
                self.journal.save({**state,'phase':'recovery_required'});raise
            self.journal.save({'phase':'disconnected'})
            return {'state':'disconnected'}

    def status(self):
        with self._lock:
            state=self.journal.load()
            if not state or state.get('phase')=='disconnected':return {'state':'disconnected','exit_ip':None}
            name=self.name(state.get('profile_id'))
            if state.get('name')!=name:raise ProfileError('Tunnel recovery state is damaged.')
            actual=self.backend.status(name)
            return {**actual,'profile_id':state['profile_id'],'recovery_required':state['phase']=='recovery_required',
                    'exit_ip':None}

    def exit_ip(self):
        if self.status()['state']!='connected':raise ProfileError('A fresh WireGuard handshake is required first.')
        # Explicit action only; uses normal verified HTTPS/proxy policy. This is an observed public address,
        # not proof that every process/DNS/IPv6 route uses the tunnel.
        with urllib.request.urlopen('https://api.ipify.org?format=json',timeout=10) as response:
            raw=response.read(4097)
        if len(raw)>4096:raise ProfileError('Exit-IP response is too large.')
        try:return str(ipaddress.ip_address(json.loads(raw)['ip']))
        except (ValueError,KeyError,TypeError):raise ProfileError('Invalid exit-IP response.') from None
