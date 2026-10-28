"""Managed session state-machine tests with explicit HTTP/native/egress stand-ins.

No test below proves a Windows route, DNS leak prevention, country exit or actual WireGuard connection.
The real TunnelManager and strict profile parser are used with an in-memory/native substitute.
"""
import base64
import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from akeno.network.profiles import ProfileError, ProfileStore, parse_wireguard
from akeno.network.tunnels import TunnelManager
from akeno.network.vpn_session import ManagedVPNSession


class Memory:
    def __init__(self, value=None, events=None):self.value=value or {};self.history=[];self.events=events
    def load(self):return copy.deepcopy(self.value)
    def save(self, value):
        self.value=copy.deepcopy(value);self.history.append(copy.deepcopy(value))
        if self.events is not None:self.events.append(('journal',value.get('phase'),value.get('profile_id')))


class Clock:
    def __init__(self):self.now=1800000000.0
    def __call__(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class NativeStandIn:
    def __init__(self,events):self.active=False;self.events=events;self.fail_stop=False;self.handshake=True
    def start(self,name,text):self.events.append(('start',name));self.active=True
    def stop(self,name,public_key=None):
        self.events.append(('stop',name,public_key))
        if self.fail_stop:raise OSError('SECRET native output')
        self.active=False
    def status(self,name):return {'state':'connected' if self.active and self.handshake else 'awaiting_handshake'}


class ClientStandIn:
    def __init__(self, clock):
        key=X25519PrivateKey.generate()
        self.identity={'device_id':str(uuid.uuid4()),
                       'private_key':base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
                                     serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode(),
                       'public_key':base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                    serialization.PublicFormat.Raw)).decode()}
        self.clock=clock;self.renewals=0;self.disconnects=0;self.provisions=0;self.pending=False
        self.fail_renew=False;self.fail_disconnect=False;self.change_address=False
        server=X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        self.claims={'lease_id':uuid.uuid4().hex,'server_id':uuid.uuid4().hex,'country':'DE',
                     'device_id':self.identity['device_id'],'client_public_key':self.identity['public_key'],
                     'server_public_key':base64.b64encode(server).decode(),'endpoint':'93.184.215.14:51820',
                     'addresses':['10.204.1.2/32','fd42:204:0:1::2/128'],'dns':['1.1.1.1'],
                     'routes':['0.0.0.0/0','::/0'],'expected_exit_ipv4':'93.184.215.14',
                     'expected_exit_ipv6':'2606:4700:4700::1111','exp':int(clock())+45,'state':'ready'}
    def _identity(self):return dict(self.identity)
    def provision(self,country):
        self.provisions+=1
        self.claims['country']=country
        self.claims['state']='awaiting_exit' if self.pending else 'ready'
        self.identity['vpn_lease_id']=self.claims['lease_id']
        return copy.deepcopy(self.claims)
    def poll(self):return copy.deepcopy(self.claims)
    def renew(self):
        self.renewals+=1
        if self.fail_renew and self.renewals>1:raise ValueError('SECRET HTTP response')
        self.claims['exp']=int(self.clock())+45
        if self.change_address:self.claims['addresses'][0]='10.204.1.9/32'
        return copy.deepcopy(self.claims)
    def profile_text(self,claims):
        text='[Interface]\nPrivateKey = '+self.identity['private_key']+'\nAddress = '+', '.join(claims['addresses'])
        text+='\nDNS = '+', '.join(claims['dns'])+'\n\n[Peer]\nPublicKey = '+claims['server_public_key']
        text+='\nEndpoint = '+claims['endpoint']+'\nAllowedIPs = 0.0.0.0/0, ::/0\n'
        return parse_wireguard(text).text
    def verify_observed_exit(self,claims,ip4,ip6):
        if ip4!=claims['expected_exit_ipv4'] or ip6!=claims['expected_exit_ipv6']:
            raise ProfileError('Observed exit mismatch.')
    def disconnect(self):
        self.disconnects+=1
        if self.fail_disconnect:raise ValueError('SECRET HTTP response')
        self.clear_lease()
    def clear_lease(self):self.identity={k:v for k,v in self.identity.items() if not k.startswith('vpn_')}


@pytest.fixture
def world(tmp_path):
    clock=Clock();events=[];client=ClientStandIn(clock)
    class Profiles(ProfileStore):
        def add(self,name,text):events.append(('add',name));return super().add(name,text)
        def remove(self,ident):events.append(('remove',ident));return super().remove(ident)
    # Deliberate reversible storage transform for this test only; not Windows DPAPI evidence.
    profiles=Profiles(tmp_path/'profiles.sqlite3',protect=lambda b:b'FIXTURE'+b,unprotect=lambda b:b[7:])
    imported=profiles.add('User imported profile',client.profile_text(client.claims))
    native=NativeStandIn(events);manager=TunnelManager(profiles,Memory(),native)
    journal,status,lan=Memory(events=events),Memory(),Memory()
    probes=[]
    def stand_in_probe(claims,name):
        probes.append((clock(),name))
        return {'ipv4':claims['expected_exit_ipv4'],'ipv6':claims['expected_exit_ipv6'],
                'routing_verified':True,'dns_verified':True,'source':'SYNTHETIC_TEST_STAND_IN'}
    session=ManagedVPNSession(client,profiles,manager,journal,status,lambda:clock()>=1800000017,
                              probe=stand_in_probe,clock=clock,sleep=clock.sleep,lan_journal=lan)
    return SimpleNamespace(clock=clock,events=events,client=client,profiles=profiles,imported=imported,native=native,
                           manager=manager,journal=journal,status=status,lan=lan,probes=probes,session=session)


def test_verified_stand_in_session_renews_and_stops_without_removing_imported_profile(world):
    w=world
    assert w.session.run('DE')['state']=='stopped'
    assert w.client.renewals==2 and w.client.disconnects==1 and not w.native.active
    assert w.profiles.metadata()[0]['id']==w.imported and len(w.profiles.metadata())==1
    connected=[value for value in w.status.history if value['state']=='connected']
    assert connected and all(value['country']=='DE' and value['dns_verified'] is True for value in connected)
    assert w.probes and w.status.history.index(connected[0])>0
    before=next(i for i,e in enumerate(w.events) if e[:2]==('journal','preparing_profile'))
    added=next(i for i,e in enumerate(w.events) if i>before and e[0]=='add')
    ownership=next(i for i,e in enumerate(w.events) if i>added and e[:2]==('journal','connecting') and e[2])
    native=next(i for i,e in enumerate(w.events) if e[0]=='start')
    assert before<added<ownership<native
    assert w.journal.load()=={'phase':'disconnected'}
    assert 'private_key' not in json.dumps(w.status.history) and w.client.identity['private_key'] not in json.dumps(w.status.history)


@pytest.mark.parametrize('evidence',[
    {'routing_verified':True,'dns_verified':False},
    {'routing_verified':False,'dns_verified':True},
    {'routing_verified':1,'dns_verified':True},
])
def test_missing_route_or_dns_evidence_never_becomes_connected(world,evidence):
    w=world
    w.session.probe=lambda claims,name:{**evidence,'ipv4':claims['expected_exit_ipv4'],'ipv6':claims['expected_exit_ipv6']}
    assert w.session.run('DE')['state']=='verification_required'
    assert not w.native.active and w.client.disconnects==1
    assert not any(value['state']=='connected' for value in w.status.history)


def test_wrong_country_exit_address_never_becomes_connected(world):
    w=world
    w.session.probe=lambda claims,name:{'routing_verified':True,'dns_verified':True,'ipv4':'8.8.8.8','ipv6':claims['expected_exit_ipv6']}
    assert w.session.run('DE')['state']=='failed'
    assert not w.native.active and not any(value['state']=='connected' for value in w.status.history)


def test_blocking_probe_cannot_extend_expired_permission(world):
    w=world
    def slow(claims,name):
        w.clock.now+=46
        return {'routing_verified':True,'dns_verified':True,'ipv4':claims['expected_exit_ipv4'],'ipv6':claims['expected_exit_ipv6']}
    w.session.probe=slow
    w.session.stop_requested=lambda:False
    assert w.session.run('DE')['state']=='failed'
    assert not w.native.active and not any(value['state']=='connected' for value in w.status.history)


def test_revocation_during_renewal_closes_tunnel_and_never_leaks_server_text(world):
    w=world;w.client.fail_renew=True
    result=w.session.run('DE')
    assert result['state']=='failed' and not w.native.active and w.client.disconnects==1
    assert 'SECRET' not in json.dumps(w.status.history)
    assert w.profiles.metadata()[0]['id']==w.imported


def test_signed_configuration_change_requires_fresh_connection(world):
    w=world;w.client.change_address=True
    assert w.session.run('DE')['state']=='failed'
    assert not w.native.active and not any(value['state']=='connected' for value in w.status.history)


def test_failed_native_stop_keeps_key_profile_and_journal_for_recovery(world):
    w=world;w.native.fail_stop=True
    result=w.session.run('DE')
    assert result['state']=='recovery_required' and w.native.active
    state=w.journal.load()
    assert state['phase']=='recovery_required' and state['profile_id']!=w.imported
    assert len(w.profiles.metadata())==2 and w.client.disconnects==0
    w.native.fail_stop=False
    assert w.session.recover()['state']=='stopped'
    assert not w.native.active and len(w.profiles.metadata())==1


def test_offline_recovery_after_expiry_removes_native_before_clearing_remote_state(world):
    w=world;w.native.fail_stop=True
    assert w.session.run('DE')['state']=='recovery_required'
    w.native.fail_stop=False;w.client.fail_disconnect=True;w.clock.now+=150
    assert w.session.recover()['state']=='stopped'
    assert not w.native.active and w.client.disconnects==0 and 'vpn_lease_id' not in w.client.identity


def test_recovery_reconstructs_lost_shared_journal_before_removing_owned_native_service(world):
    w=world;w.native.fail_stop=True
    assert w.session.run('DE')['state']=='recovery_required'
    assert w.native.active
    w.manager.journal.save({})
    w.native.fail_stop=False
    assert w.session.recover()['state']=='stopped'
    assert not w.native.active and len(w.profiles.metadata())==1


def test_profile_ownership_digest_mismatch_keeps_key_and_does_not_touch_native_service(world):
    w=world;w.native.fail_stop=True
    assert w.session.run('DE')['state']=='recovery_required'
    state=w.journal.load();state['profile_digest']='0'*64;w.journal.save(state)
    w.native.fail_stop=False
    count=len(w.events)
    assert w.session.recover()['state']=='recovery_required'
    assert w.native.active and len(w.profiles.metadata())==2
    assert not any(event[0] in ('remove','stop') for event in w.events[count:])


def test_remote_outage_keeps_recovery_metadata_but_local_profile_is_safely_removed(world):
    w=world;w.client.fail_disconnect=True
    result=w.session.run('DE')
    assert result['state']=='recovery_required' and not w.native.active
    assert w.journal.load()['phase']=='remote_cleanup_pending' and 'profile_id' not in w.journal.load()
    assert len(w.profiles.metadata())==1
    w.clock.now+=150
    assert w.session.recover()['state']=='stopped'


@pytest.mark.parametrize('existing',['lan','own_vpn','managed_vpn'])
def test_existing_lan_or_other_vpn_is_never_reconfigured(world,existing):
    w=world
    store={'lan':w.lan,'own_vpn':w.manager.journal,'managed_vpn':w.journal}[existing]
    store.save({'phase':'running','profile_id':w.imported})
    with pytest.raises(ProfileError):w.session.run('DE')
    assert w.client.provisions==0 and not w.native.active and len(w.profiles.metadata())==1


def test_ack_timeout_never_creates_a_native_profile(world):
    w=world;w.client.pending=True;w.session.stop_requested=lambda:False
    assert w.session.run('DE')['state']=='failed'
    assert not any(event[0]=='start' for event in w.events) and len(w.profiles.metadata())==1


def test_unverifiable_default_observer_is_not_replaced_by_synthetic_success(world,monkeypatch):
    w=world
    monkeypatch.setattr('akeno.network.vpn_session.sys.platform','linux')
    from akeno.network.vpn_session import default_probe
    w.session.probe=default_probe
    assert w.session.run('DE')['state']=='verification_required'
    assert not w.native.active and not any(value['state']=='connected' for value in w.status.history)


def test_tunnel_latency_is_the_median_of_successful_dns_round_trips_only():
    from akeno.network.vpn_session import tunnel_latency
    answers = iter([True, OSError('timeout'), True])

    def measure(resolver):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value
    latency = tunnel_latency('203.0.113.53', samples=3, measure=measure)
    assert isinstance(latency, float) and latency >= 0
    assert tunnel_latency('203.0.113.53', samples=2, measure=lambda r: False) is None
