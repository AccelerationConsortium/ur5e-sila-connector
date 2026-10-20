import hashlib
import json
from pathlib import Path
import runpy
import sys
import pytest
from unitelabs.ur5e_connector.io.receipts import check_records,INDEX
from unitelabs.ur5e_connector.io.errors import CommandRejected

SCRIPT=Path('examples/migrate_receipts.py').resolve()


def setup_migration(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    target=tmp_path/'local-data'/'receipts'
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'simulation_mode':False,'joint_test_receipt_directory':str(source),'enable_continuous_motion':False}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'argv',['migrate_receipts.py','--config',str(config),'--target',str(target)])
    return source,target,config


def test_migration_preserves_bytes_and_disables_execution(tmp_path,monkeypatch):
    main=runpy.run_path(str(SCRIPT))['main']
    source,target,config=setup_migration(tmp_path,monkeypatch)
    data=json.dumps({'status':'verified','samples':[1,2,3]}).encode()
    (source/'one.json').write_bytes(data)
    main()
    assert (source/'one.json').read_bytes()==data
    assert (target/'one.json').read_bytes()==data
    copied=json.loads((target/'migration-manifest').read_text())
    assert copied['files'][0]['sha256']==hashlib.sha256(data).hexdigest()
    assert copied['warm_local_scans'][0]['json_bodies_read']==0
    updated=json.loads(config.read_text())
    assert updated['enable_continuous_motion'] is False
    assert updated['joint_test_receipt_directory']==str(target)
    assert not (source/'.active').exists()


@pytest.mark.parametrize('bad_index',['missing_receipt','corrupt_index'])
def test_migration_cannot_clear_invalid_source_history(tmp_path,monkeypatch,bad_index):
    main=runpy.run_path(str(SCRIPT))['main']
    source,target,config=setup_migration(tmp_path,monkeypatch)
    (source/'one.json').write_text(json.dumps({'status':'verified'}))
    check_records(source)
    if bad_index=='missing_receipt':(source/'one.json').unlink()
    else:(source/INDEX).write_text('corrupt')
    original=config.read_bytes()
    with pytest.raises(CommandRejected):main()
    assert config.read_bytes()==original
    assert list(target.glob('*.json'))==[]
    assert not (source/'.active').exists()
