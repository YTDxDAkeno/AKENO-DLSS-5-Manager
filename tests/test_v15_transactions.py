"""Ownership preconditions and interrupted journals use real synthetic filesystem bytes."""
from pathlib import Path
import pytest
from akeno.install.transaction import Installer
from akeno.backends.base import InstallPlan,FileOp
from akeno.core.models import Game
from tests.helpers import sha


def setup(tmp_path,db):
    root=tmp_path/'game';root.mkdir();game=Game('g','Synthetic game',str(root),'manual')
    return root,game,Installer(db,tmp_path/'backups',running_check=lambda _:[])


def test_foreign_official_swap_requires_exact_original_and_restores(db,tmp_path):
    root,g,installer=setup(tmp_path,db);old=b'original';(root/'nvngx_dlss.dll').write_bytes(old)
    plan=InstallPlan('official_dlss',None,str(root),ops=[FileOp('nvngx_dlss.dll',content=b'new')],kind='swap',expected_originals={'nvngx_dlss.dll':sha(old)})
    result=installer.apply(g,plan);assert result.ok
    assert installer.restore(result.tx_id).ok and (root/'nvngx_dlss.dll').read_bytes()==old
    (root/'nvngx_dlss.dll').write_bytes(b'foreign change')
    assert not installer.apply(g,plan).ok and (root/'nvngx_dlss.dll').read_bytes()==b'foreign change'


def test_interrupted_recovery_preflights_all_files_before_any_change(db,tmp_path):
    root,g,installer=setup(tmp_path,db);backup=tmp_path/'original';backup.write_bytes(b'old')
    tx=db.create_transaction(game_id=g.id,game_name=g.name,backend_id='fixture',target_dir=str(root),kind='install',state='applying')
    db.record_tx_file(tx,'a.dll','replace',original_sha256=sha(b'old'),backup_path=str(backup),installed_sha256=sha(b'new'))
    db.record_tx_file(tx,'b.dll','create',installed_sha256=sha(b'owned'))
    (root/'a.dll').write_bytes(b'new');(root/'b.dll').write_bytes(b'changed externally')
    assert not installer.recover(tx).ok and (root/'a.dll').read_bytes()==b'new'
    (root/'b.dll').write_bytes(b'owned')
    assert installer.recover(tx).ok and (root/'a.dll').read_bytes()==b'old' and not (root/'b.dll').exists()


def test_crash_before_replace_is_a_safe_recovery_noop(db,tmp_path):
    root,g,installer=setup(tmp_path,db);backup=tmp_path/'original';backup.write_bytes(b'old');(root/'a.dll').write_bytes(b'old')
    tx=db.create_transaction(game_id=g.id,game_name=g.name,backend_id='fixture',target_dir=str(root),kind='install',state='applying')
    db.record_tx_file(tx,'a.dll','replace',original_sha256=sha(b'old'),backup_path=str(backup),installed_sha256=sha(b'intended'))
    assert installer.recover(tx).ok and (root/'a.dll').read_bytes()==b'old'
