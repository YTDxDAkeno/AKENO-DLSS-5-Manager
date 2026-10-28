import importlib.util,io,json,os,sys
from pathlib import Path
from datetime import timedelta
import pytest
PREP=Path('/preparation');sys.path.insert(0,str(PREP))
import entrypoint
from akeno_server import db as dbmod
from akeno_server.models import User,License
from sqlalchemy import select

def clean_env(monkeypatch):
    for n in ['AKENO_DATABASE_URL','AKENO_TOKEN_SECRET','AKENO_CODE_PEPPER']:
        monkeypatch.delenv(n,raising=False)
    monkeypatch.setenv('AKENO_NETWORK_STAGING','1');monkeypatch.setenv('AKENO_ROLE','api')
    monkeypatch.setenv('AKENO_PUBLIC_URL','https://lan-staging.akeno.online')

@pytest.mark.parametrize('name,value,expected',[
    ('AKENO_NETWORK_STAGING','0','STAGING_GUARD_REFUSED'),
    ('AKENO_ROLE','all','STAGING_ROLE_REFUSED'),
    ('AKENO_PUBLIC_URL','https://akeno.online','STAGING_ORIGIN_REFUSED'),
    ('AKENO_DATABASE_URL','postgresql://production','EXTERNAL_DATABASE_URL_REFUSED')])
def test_entrypoint_fails_closed(monkeypatch,name,value,expected):
    clean_env(monkeypatch);monkeypatch.setenv(name,value)
    with pytest.raises(SystemExit,match=expected):entrypoint.prepare_environment()

def test_fixed_staging_database_secret_files_no_values_printed(monkeypatch,capsys):
    clean_env(monkeypatch)
    class TestPath:
        def __init__(self,name):assert name in ['/run/secrets/db_app','/run/secrets/token','/run/secrets/pepper']
        def read_text(self):return 'test-only-synthetic-secret-0123456789abcdef:@'
    monkeypatch.setattr(entrypoint,'Path',TestPath);entrypoint.prepare_environment()
    assert '@staging-db:5432/akeno_network_staging' in os.environ['AKENO_DATABASE_URL']
    assert '%3A%40' in os.environ['AKENO_DATABASE_URL']
    assert os.environ['AKENO_MAIL_BACKEND']=='file'
    assert 'synthetic' not in capsys.readouterr().out

def load_seed():
    spec=importlib.util.spec_from_file_location('staging_seed',PREP/'seed-test-users.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_seed_ordinary_accounts_and_no_overwrite(monkeypatch,keys,capsys):
    seed=load_seed();monkeypatch.setattr(seed,'prepare_environment',lambda:None)
    body=json.dumps({'pc_a_password':'Synthetic-Only-Passphrase-A-837192','pc_b_password':'Synthetic-Only-Passphrase-B-936284'}).encode()
    class Input:
        buffer=io.BytesIO(body)
    monkeypatch.setattr(sys,'stdin',Input());seed.main()
    with dbmod.session() as db:
        users=list(db.scalars(select(User)));licenses=list(db.scalars(select(License)))
        assert len(users)==len(licenses)==2
        assert all(u.role=='user' and u.email_verified_at for u in users)
        assert all(u.credential.password_hash.startswith('$argon2id$') for u in users)
        assert all(l.device_limit==1 and 6<=(l.expires_at-l.created_at).total_seconds()/86400<=8 for l in licenses)
        before={u.id:u.credential.password_hash for u in users}
    Input.buffer=io.BytesIO(body)
    with pytest.raises(SystemExit,match='STAGING_USERS_ALREADY_EXIST'):seed.main()
    with dbmod.session() as db:assert {u.id:u.credential.password_hash for u in db.scalars(select(User))}==before
    assert 'Synthetic-Only-Passphrase' not in capsys.readouterr().out
