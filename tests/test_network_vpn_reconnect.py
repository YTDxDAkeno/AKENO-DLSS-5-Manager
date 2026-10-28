"""Recovery policy uses the real managed session and explicit native/API stand-ins."""
import pytest

from test_network_vpn_session import world  # fixture using real TunnelManager/profile parser


def test_lost_handshake_cleans_and_reprovisions_before_connected_again(world):
    w=world; original=w.manager.status; lost=[]
    def status():
        value=original()
        if w.clock()>=1800000003 and not lost:
            lost.append(True);return {**value,'state':'awaiting_handshake'}
        return value
    w.manager.status=status
    assert w.session.run('DE')['state']=='stopped'
    assert w.client.provisions==2 and w.client.disconnects==2
    reconnect=next(i for i,x in enumerate(w.status.history) if x['state']=='reconnecting')
    assert not w.native.active and len(w.probes)==2
    assert any(x['state']=='connected' for x in w.status.history[reconnect+1:])
    assert len(w.profiles.metadata())==1  # unrelated imported profile retained


def test_standby_expired_lease_is_never_used_after_resume(world):
    w=world;original=w.session.sleep;jumped=[]
    provision=w.client.provision
    def fresh_provision(country):
        w.client.claims['exp']=int(w.clock())+45
        return provision(country)
    w.client.provision=fresh_provision
    def sleep(seconds):
        original(seconds)
        if w.status.load().get('state')=='connected' and not jumped:
            w.clock.now+=120;jumped.append(True)
    w.session.sleep=sleep
    w.session.stop_requested=lambda:w.client.provisions>1 and w.status.load().get('state')=='connected'
    assert w.session.run('DE')['state']=='stopped'
    assert w.client.provisions==2 and len(w.probes)==2


def test_cancel_during_backoff_does_not_create_another_tunnel(world):
    w=world;original=w.manager.status
    w.manager.status=lambda:{**original(),'state':'awaiting_handshake'} if w.clock()>=1800000002 else original()
    w.session.stop_requested=lambda:w.status.load().get('state')=='reconnecting'
    assert w.session.run('DE')['state']=='stopped'
    assert w.client.provisions==1 and w.client.disconnects==1 and not w.native.active
