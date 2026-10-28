#!/usr/bin/env python3
"""Read-only Compose validation; creates no containers, networks, keys, accounts or firewall rules."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
cmd=['docker','compose','--env-file',str(ROOT/'.env.example'),'-f',str(ROOT/'compose.staging.yml'),'--profile','approved-staging','--profile','initialize-only','config','--format','json']
r=subprocess.run(cmd,capture_output=True,text=True)
if r.returncode:raise SystemExit('COMPOSE_VALIDATION_FAILED')
d=json.loads(r.stdout)
assert d['name']=='akeno-network-staging'
assert set(d['services'])=={'staging-init','staging-db','staging-api','staging-web','staging-admin'}
assert d['networks']['database']['internal'] is True
for n,s in d['services'].items():
    assert s.get('read_only') is True
    assert not s.get('privileged') and s.get('network_mode')!='host'
    assert int(s.get('mem_limit',0))>0 and float(s.get('cpus',0))>0 and s.get('pids_limit',0)>0
    assert 'ALL' in s.get('cap_drop',[])
    assert s.get('profiles')
    for mount in s.get('tmpfs',[]):
        assert mount.startswith('/') and ':rw,' in mount
        assert 'size=' in mount and 'nosuid' in mount and 'noexec' in mount
    for port in s.get('ports',[]):assert port['host_ip']=='127.0.0.1'
    for v in s.get('volumes',[]):
        if v['type']=='bind':
            assert v.get('read_only') and Path(v['source']).resolve().parent==ROOT
        else:assert v['source'].startswith('staging-')
    for network in s.get('networks',{}):assert network in ('control','database')
    env=s.get('environment',{})
    assert 'AKENO_DATABASE_URL' not in env
    assert not any(k in env for k in ('AKENO_TOKEN_SECRET','AKENO_CODE_PEPPER','POSTGRES_PASSWORD'))
    if n.startswith('staging-') and n not in ('staging-init','staging-db'):
        assert env['AKENO_MAIL_BACKEND']=='file' and env['AKENO_PUBLIC_URL']=='https://lan-staging.akeno.online'
assert set(d['services']['staging-db'].get('ports',[]))==set()
assert d['services']['staging-api']['environment']['AKENO_NETWORK_ENABLED']=='true'
assert len(d['services']['staging-api']['ports'])==2
assert not d['services']['staging-web']['environment'].get('AKENO_NETWORK_ENABLED')=='true'
assert not d['services']['staging-admin']['environment'].get('AKENO_NETWORK_ENABLED')=='true'
for n,v in d['volumes'].items():assert not v.get('external') and v['name'].startswith('akeno-network-staging_')
print('PREPARATION_CONFIG_CHECKS_OK_NO_DEPLOYMENT')
