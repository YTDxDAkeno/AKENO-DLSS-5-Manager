"""Windows firewall construction/cleanup contracts with native stand-ins; no Windows enforcement claim."""
import ipaddress
import re

import pytest

from akeno.network.lan import LanError
from akeno.network.lan_windows import LanFirewall, LanSession, tunnel_name

ROOM = 'ab' * 16
NAME = tunnel_name(ROOM)


def test_relay_boundary_blocks_every_other_v4_v6_address_and_remote_port():
    commands=[]
    f=LanFirewall(NAME,'10.203.5.0/24',run=lambda script:commands.append(script))
    f.relay_only(49173,('45.11.12.13',3478))
    script=commands[0]
    rules=script.split('; ')
    assert len(rules)==5
    for direction in ('Inbound','Outbound'):
        [address_rule]=[line for line in rules if f'relay {direction} addresses' in line]
        addresses=re.findall("'([^']+)'",address_rule.split('-RemoteAddress ',1)[1])
        nets=[ipaddress.ip_network(value) for value in addresses]
        assert not any(ipaddress.ip_address('45.11.12.13') in net for net in nets)
        assert any(ipaddress.ip_address('45.11.12.12') in net for net in nets)
        assert any(ipaddress.ip_address('45.11.12.14') in net for net in nets)
        assert any(ipaddress.ip_address('2001:db8::1') in net for net in nets)
        [port_rule]=[line for line in rules if f'relay {direction} ports' in line]
        assert "-RemotePort '1-3477','3479-65535'" in port_rule
        assert '-LocalPort 49173' in address_rule and '-LocalPort 49173' in port_rule


def test_relay_rule_rejects_unvalidated_command_values():
    f=LanFirewall(NAME,'10.203.5.0/24',run=lambda script:pytest.fail('must not run'))
    for endpoint in [('45.1.2.3; evil',3478),('::1',3478),('45.1.2.3',0),('45.1.2.3','3478')]:
        with pytest.raises(ValueError):f.relay_only(49173,endpoint)
    with pytest.raises(ValueError):f.relay_only('49173',('45.1.2.3',3478))


def test_all_firewall_profiles_must_be_enabled_and_none_is_changed():
    for result in ('True\nTrue\nTrue','True\nFalse\nTrue','True',''):
        commands=[]
        f=LanFirewall(NAME,'10.203.5.0/24',run=lambda script:commands.append(script) or result)
        if result=='True\nTrue\nTrue':f.require_enabled()
        else:
            with pytest.raises(LanError):f.require_enabled()
        assert commands==['(Get-NetFirewallProfile -Name Domain,Private,Public).Enabled']


def test_unreviewed_game_never_gets_all_port_permissions(tmp_path):
    commands=[]
    f=LanFirewall(NAME,'10.203.5.0/24',run=lambda script:commands.append(script))
    exe=tmp_path/'openttd.exe';exe.write_bytes(b'fixture')
    with pytest.raises(ValueError):f.allow_program(str(exe),'unreviewed')
    assert commands==[]
    f.apply()
    f.allow_program(str(exe),'openttd')
    game_rules=[line for script in commands for line in script.split('; ') if ' game ' in line]
    assert len(game_rules)==2 and all('-LocalPort 3979' in line for line in game_rules)
    assert any('default deny 2 TCP' in script and "-LocalPort '1-3978','3980-65535'" in script for script in commands)
    assert any('default deny 2 UDP' in script and "'3980-51998','52000-65535'" in script for script in commands)
    created=next(i for i,s in enumerate(commands) if 'New-NetFirewallRule' in s and 'default deny 2' in s)
    removed=next(i for i,s in enumerate(commands) if 'Remove-NetFirewallRule' in s and 'default deny 1' in s)
    assert created<removed


class Store:
    def __init__(self,value=None):self.value=value or {}
    def load(self):return dict(self.value)
    def save(self,value):self.value=value


@pytest.mark.parametrize('fail',[False,True])
def test_tunnel_stops_before_privacy_rules_are_removed_and_failures_keep_recovery(fail):
    calls=[]
    class Native:
        def stop(self,name,public_key):
            calls.append('stop')
            if fail:raise RuntimeError('driver stop refused')
    class Firewall:
        def __init__(self,*a):pass
        def remove(self):calls.append('remove')
    journal=Store({'phase':'lan_active','name':NAME,'public_key':'own key'})
    session=LanSession(ROOM,None,Native(),journal,Store(),lambda:False,firewall=Firewall)
    if fail:
        with pytest.raises(RuntimeError):session.stop()
        assert calls==['stop'] and journal.value['phase']=='lan_recovery_required'
    else:
        session.stop()
        assert calls==['stop','remove'] and journal.value=={'phase':'disconnected'}
