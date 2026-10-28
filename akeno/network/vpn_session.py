"""Privileged, journaled AKENO VPN sessions. The normal GUI never handles an unencrypted managed profile.

Provisioning, a native handshake and an observed country IP are separate states. DNS/route verification is a
mandatory gate, not a success assumption: the default observer reports DNS leak verification as unavailable
until an independently tested Windows implementation supplies that evidence. Tests inject explicit stand-ins.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import sys
import time
import urllib.request
import uuid

from akeno.network.profiles import ProfileError


class VerificationRequired(ProfileError):
    pass


class _StopRequested(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        raise VerificationRequired('VPN egress verification must not redirect.')


def default_probe(claims, tunnel_name):
    """Actual, fixed HTTPS IPv4/IPv6 observations and native route/DNS configuration evidence.

    Normal proxy/TLS settings are preserved. An active HTTP proxy prevents attributing an HTTPS result to this
    tunnel, so it fails closed. DNS settings alone cannot prove absence of DNS leaks, therefore this observer
    deliberately returns ``dns_verified=False``. This is an unfinished Windows verification gate, not a fake
    connected state. Operators must never replace it with a constant True to activate country VPNs.
    """
    if sys.platform != 'win32' or not re.fullmatch(r'AkenoVPN[0-9a-f]{24}', tunnel_name):
        raise VerificationRequired('Native Windows VPN verification is required.')
    if urllib.request.getproxies():
        raise VerificationRequired('An HTTP proxy prevents attributing egress probes to the VPN tunnel.')
    opener = urllib.request.build_opener(_NoRedirect())
    addresses = {}
    for version, hostname in ((4, 'api4.ipify.org'), (6, 'api6.ipify.org')):
        with opener.open('https://' + hostname + '?format=json', timeout=5) as response:
            if response.status != 200 or response.url != 'https://' + hostname + '?format=json':
                raise VerificationRequired('VPN egress verification did not return a direct HTTPS result.')
            raw = response.read(4097)
        if len(raw) > 4096:
            raise VerificationRequired('VPN egress verification returned an oversized response.')
        try:
            data = json.loads(raw)
            address = ipaddress.ip_address(data['ip'])
            if set(data) != {'ip'} or address.version != version or not address.is_global or address.is_multicast:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise VerificationRequired('VPN egress verification returned an invalid address.') from None
        addresses['ipv' + str(version)] = str(address)
    # The executable and script implementation are fixed and trusted; only validated tunnel names/IPs enter it.
    from akeno.network.lan_windows import powershell
    dns = [str(ipaddress.ip_address(value)) for value in claims['dns']]
    script = f"$n='{tunnel_name}'; $r=@(); "
    for target in ('1.1.1.1', '2606:4700:4700::1111', *dns):
        script += f"$r += @(Find-NetRoute -RemoteIPAddress '{target}' -ErrorAction Stop | Select-Object -ExpandProperty InterfaceAlias); "
    script += "$d=@(Get-DnsClientServerAddress -InterfaceAlias $n -ErrorAction Stop | ForEach-Object {$_.ServerAddresses}); "
    script += "@{routes=$r;dns=$d} | ConvertTo-Json -Compress"
    try:
        native = json.loads(powershell(script, timeout=10))
        routes = native['routes']
        configured = {str(ipaddress.ip_address(value)) for value in native['dns']}
        routing_verified = bool(routes) and all(value == tunnel_name for value in routes)
    except (ValueError, TypeError, KeyError):
        raise VerificationRequired('The VPN route and DNS configuration could not be inspected.') from None
    return {**addresses, 'routing_verified': routing_verified, 'dns_configuration_matches': configured == set(dns),
            'dns_verified': False, 'source': 'actual_windows_https_and_configuration_observation'}


class ManagedVPNSession:
    RENEW_SECONDS = 12
    POLL_SECONDS = 1
    HANDSHAKE_SECONDS = 30
    EXIT_ACK_SECONDS = 25
    OBSERVE_SECONDS = 25
    STABLE_FIELDS = ('lease_id','server_id','country','device_id','client_public_key','server_public_key','endpoint',
                     'addresses','dns','routes','expected_exit_ipv4','expected_exit_ipv6')

    def __init__(self, client, profiles, tunnel_manager, journal, status, stop_requested, probe=None, *,
                 clock=time.time, sleep=time.sleep, lan_journal=None):
        from akeno.licensing.storage import SecureStore
        for store in (journal, status, lan_journal):
            if isinstance(store, SecureStore) and not store.protected:
                raise ProfileError('Managed VPN session state requires Windows DPAPI.')
        self.client,self.profiles,self.manager = client,profiles,tunnel_manager
        self.journal,self.status,self.stop_requested = journal,status,stop_requested
        self.probe,self.clock,self.sleep,self.lan_journal = probe or default_probe,clock,sleep,lan_journal
        self.state = {}

    def _write(self, phase, **values):
        self.state = {**self.state, **values, 'phase':phase}
        self.journal.save(self.state)

    def _status(self, state, *, claims=None, observation=None, error=None):
        result = {'state':state, 'updated':self.clock()}
        country = claims.get('country') if claims else self.state.get('country')
        if country:
            result['country'] = country
        if claims:
            result['lease_expires_at'] = claims['exp']
        if state == 'connected' and observation:
            result.update(exit_ipv4=observation['ipv4'], exit_ipv6=observation['ipv6'],
                          routing_verified=True, dns_verified=True)
        if error:
            result['error'] = error     # only a fixed code from this module, never server/native exception text
        self.status.save(result)
        return result

    def _stop(self):
        if self.stop_requested():
            raise _StopRequested()

    def _fresh(self, claims):
        self._stop()
        if not isinstance(claims, dict) or type(claims.get('exp')) is not int or claims['exp'] <= self.clock():
            raise ProfileError('The VPN permission expired.')

    def _stable(self, initial, claims):
        self._fresh(claims)
        if any(claims.get(field) != initial.get(field) for field in self.STABLE_FIELDS):
            raise ProfileError('The VPN server configuration changed; reconnect safely.')
        return claims

    def _idle(self):
        if self.journal.load().get('phase') not in (None,'disconnected'):
            raise ProfileError('Recover the previous managed VPN session first.')
        if self.manager.journal.load().get('phase') not in (None,'disconnected'):
            raise ProfileError('Disconnect or recover the existing VPN tunnel first.')
        if self.lan_journal and self.lan_journal.load().get('phase') not in (None,'disconnected'):
            raise ProfileError('Disconnect LAN Play before connecting AKENO VPN.')

    def _renew(self, initial):
        claims = self._stable(initial, self.client.renew())
        if claims['state'] != 'ready':
            raise ProfileError('The VPN exit no longer acknowledges the device lease.')
        self._write('running', lease_expires_at=claims['exp'])
        return claims

    def _observe(self, claims, name):
        self._fresh(claims)
        if self.manager.status().get('state') != 'connected':
            raise ProfileError('A fresh native WireGuard handshake is required.')
        observation = self.probe(claims, name)
        self._fresh(claims)   # expiry/stop are checked again after blocking HTTPS/native operations
        if self.manager.status().get('state') != 'connected':
            raise ProfileError('The VPN handshake was lost during egress verification.')
        if not isinstance(observation, dict) or observation.get('routing_verified') is not True or \
                observation.get('dns_verified') is not True:
            raise VerificationRequired('VPN routing and DNS leak verification are incomplete.')
        self.client.verify_observed_exit(claims, observation.get('ipv4'), observation.get('ipv6'))
        return observation

    def run(self, country):
        """Run until stop/revocation/expiry or verification failure. Always attempt owned local cleanup first."""
        if not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country):
            raise ProfileError('Select an available country.')
        self._idle()
        self.state = {'country':country,'session_id':uuid.uuid4().hex,'created':self.clock()}
        self._write('provisioning')
        final,error = 'stopped',None
        try:
            self._stop()
            self._status('provisioning')
            claims = self.client.provision(country)
            self._fresh(claims)
            initial = dict(claims)
            self._write('awaiting_exit',lease_id=claims['lease_id'],lease_expires_at=claims['exp'])
            deadline = self.clock() + self.EXIT_ACK_SECONDS
            while claims['state'] != 'ready':
                self._fresh(claims)
                if self.clock() >= deadline:
                    raise ProfileError('The VPN exit did not acknowledge its lease in time.')
                self._status('awaiting_exit',claims=claims)
                self.sleep(self.POLL_SECONDS)
                claims = self._stable(initial,self.client.poll())
            self._stop()
            self._write('preparing_profile')  # durable intent BEFORE a protected profile is created
            text = self.client.profile_text(claims)
            digest = hashlib.sha256(text.encode()).hexdigest()
            ident = self.profiles.add('AKENO '+country+' VPN (managed)',text)
            self._write('connecting',profile_id=ident,profile_digest=digest)
            self._status('connecting',claims=claims)
            self._fresh(claims)
            native = self.manager.connect(ident)
            self._write('awaiting_handshake')
            deadline = self.clock() + self.HANDSHAKE_SECONDS
            while native.get('state') != 'connected':
                self._fresh(claims)
                if self.clock() >= deadline:
                    raise ProfileError('The native WireGuard handshake did not complete.')
                self._status('awaiting_handshake',claims=claims)
                self.sleep(self.POLL_SECONDS)
                native = self.manager.status()
            # Give the potentially blocking native/egress checks a fresh short lease, never extend it locally.
            claims = self._renew(initial)
            self._status('verifying',claims=claims)
            observation = self._observe(claims,self.manager.name(ident))
            self._write('running')
            self._status('connected',claims=claims,observation=observation)
            renewed,observed = self.clock(),self.clock()
            while True:
                self._fresh(claims)
                self.sleep(self.POLL_SECONDS)
                self._fresh(claims)
                if self.manager.status().get('state') != 'connected':
                    raise ProfileError('The native VPN tunnel is no longer connected.')
                if self.clock() - renewed >= self.RENEW_SECONDS:
                    claims = self._renew(initial)
                    renewed = self.clock()
                if self.clock() - observed >= self.OBSERVE_SECONDS:
                    observation = self._observe(claims,self.manager.name(ident))
                    observed = self.clock()
                self._status('connected',claims=claims,observation=observation)
        except _StopRequested:
            pass
        except VerificationRequired:
            final,error = 'verification_required','vpn_verification_incomplete'
        except Exception:
            final,error = 'failed','vpn_session_failed'
        cleanup = self._cleanup()
        if not cleanup:
            final,error = 'recovery_required','vpn_cleanup_incomplete'
        return self._status(final,error=error)

    def _cleanup(self):
        """Keep key/profile/journal if native removal fails. Imported user profiles are never selected for removal."""
        self._write('stopping')
        ident = self.state.get('profile_id')
        try:
            native = self.manager.journal.load()
            if native.get('phase') not in (None,'disconnected'):
                if not ident or native.get('profile_id') != ident:
                    raise ProfileError('The active native tunnel is not owned by this session.')
            if ident:
                profile = self.profiles._load(ident)
                if hashlib.sha256(profile['text'].encode()).hexdigest() != self.state.get('profile_digest'):
                    raise ProfileError('The protected managed profile changed; recovery is required.')
                # The shared journal may have been lost after service installation. Reconstruct only this owned
                # profile's recovery entry; the backend still proves its public key before removing any service.
                # Even a disconnected service may have an encrypted orphan, so keep the profile until stop agrees.
                if native.get('phase') in (None,'disconnected'):
                    self.manager.journal.save({'phase':'recovery_required','profile_id':ident,
                                               'name':self.manager.name(ident)})
                self.manager.disconnect()
                if self.manager.journal.load().get('phase') != 'disconnected':
                    raise ProfileError('The native VPN tunnel still needs recovery.')
                self.profiles.remove(ident)
                self.state.pop('profile_id',None)
                self.state.pop('profile_digest',None)
                self._write('stopping')
        except Exception:
            self._write('recovery_required')
            return False
        try:
            identity = self.client._identity()
            lease = identity.get('vpn_lease_id')
            expected = self.state.get('lease_id')
            if lease:
                if expected and lease != expected:
                    raise ProfileError('The protected VPN lease changed.')
                # Once a signed lease has expired, local cleanup never needs an online licensing server.
                if self.state.get('lease_expires_at',float('inf')) + 60 <= self.clock():
                    self.client.clear_lease()
                else:
                    self.client.disconnect()
            else:
                self.client.clear_lease()
        except Exception:
            self._write('remote_cleanup_pending')
            return False
        self.state = {'phase':'disconnected'}
        self.journal.save(self.state)
        return True

    def recover(self):
        """Restore a previous session without a new lease, server connection or license requirement."""
        self.state = self.journal.load()
        if self.state.get('phase') in (None,'disconnected'):
            return self._status('stopped')
        clean = self._cleanup()
        return self._status('stopped' if clean else 'recovery_required',
                            error=None if clean else 'vpn_cleanup_incomplete')
