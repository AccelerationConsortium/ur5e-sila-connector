import json
import os
import pytest
from unitelabs.ur5e_connector.io.receipts import check_records,INDEX
from unitelabs.ur5e_connector.io.errors import CommandRejected


def write(path,status='verified'):
    path.write_text(json.dumps({'status':status,'samples':[{'q':[0]*6}]*30}))


def test_index_reads_only_new_or_changed(tmp_path):
    for i in range(100):write(tmp_path/f'{i}.json')
    first=check_records(tmp_path)
    assert first['json_bodies_read']==100
    second=check_records(tmp_path)
    assert second['json_bodies_read']==0
    assert second['cache_hits']==100
    write(tmp_path/'100.json')
    assert check_records(tmp_path)['json_bodies_read']==1
    write(tmp_path/'2.json','not_sent')
    assert check_records(tmp_path)['json_bodies_read']==1


def test_mutation_with_preserved_mtime_is_rechecked(tmp_path):
    path=tmp_path/'a.json';write(path);check_records(tmp_path)
    before=path.stat()
    write(path,'outcome_unknown')
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(CommandRejected,match='Unresolved'):
        check_records(tmp_path)


@pytest.mark.parametrize('status',['reserved','preflight','sent_monitoring','outcome_unknown','nonsense'])
def test_unknown_receipt_always_blocks(tmp_path,status):
    write(tmp_path/'a.json');check_records(tmp_path)
    write(tmp_path/'b.json',status)
    with pytest.raises(CommandRejected,match='Unresolved'):
        check_records(tmp_path)


def test_missing_and_corrupt_fail_closed(tmp_path):
    path=tmp_path/'a.json';write(path);check_records(tmp_path)
    path.unlink()
    with pytest.raises(CommandRejected,match='missing'):
        check_records(tmp_path)
    write(path)
    (tmp_path/INDEX).write_text('{broken')
    with pytest.raises(CommandRejected,match='corrupt'):
        check_records(tmp_path)


def test_changed_during_read_rejects(tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/'a.json';write(path)
    original=Path.read_text
    def changing_read(self,*args,**kwargs):
        content=original(self,*args,**kwargs)
        if self.name=='a.json':write(self,'outcome_unknown')
        return content
    monkeypatch.setattr(Path,'read_text',changing_read)
    with pytest.raises(CommandRejected,match='changed while'):
        check_records(tmp_path)
