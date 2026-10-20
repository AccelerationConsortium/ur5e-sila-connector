"""Offline, verified-copy migration; stop real control and panel services before use."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from unitelabs.ur5e_connector.io.receipts import atomic_json,check_records,INDEX


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('motion.local.json'))
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    assert config['simulation_mode'] is False
    source=Path(config['joint_test_receipt_directory']).resolve()
    target=args.target.resolve()
    assert source!=target and source.is_dir()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError('Target is not empty; inspect an earlier migration before continuing.')
    target.mkdir(parents=True,exist_ok=True)
    source_lock=source/'.active';target_lock=target/'.active'
    source_owned=target_owned=False
    manifest={'source':str(source),'target':str(target),'status':'copying','files':[]}
    try:
        source_lock.mkdir();source_owned=True
        target_lock.mkdir();target_owned=True
        # Existing index is authoritative for missing/corrupt source history.
        # Without an index, the full copy loop below validates every JSON body once.
        if (source/INDEX).exists():
            manifest["source_index_validation"]=check_records(source)
        else:
            manifest["source_index_validation"]="absent; every source receipt validated by copy loop"
        names=sorted(source.glob('*.json'))
        atomic_json(target/'migration-manifest',manifest)
        for index,path in enumerate(names):
            if path.is_symlink():raise RuntimeError(f'Symlink receipt rejected: {path.name}')
            started=time.perf_counter();data=path.read_bytes();read_seconds=time.perf_counter()-started
            parsed_started=time.perf_counter();record=json.loads(data);parse_seconds=time.perf_counter()-parsed_started
            if not isinstance(record,dict) or record.get('status') not in {'verified','not_sent'}:
                raise RuntimeError(f'Unresolved receipt {path.name}; migration will not clear or alter it.')
            digest=hashlib.sha256(data).hexdigest()
            destination=target/path.name
            with destination.open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            if hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:raise RuntimeError('Copy hash mismatch.')
            manifest['files'].append({'name':path.name,'bytes':len(data),'sha256':digest,'status':record['status'],'source_read_seconds':read_seconds,'parse_seconds':parse_seconds})
            if (index+1)%20==0 or read_seconds>.5:print(f'Copied and verified {index+1}/{len(names)}; latest source read {read_seconds:.3f}s',flush=True)
        if {p.name for p in source.glob('*.json')}!={p.name for p in names}:raise RuntimeError('Source file set changed during migration.')
        manifest['initial_local_scan']=check_records(target)
        manifest['warm_local_scans']=[check_records(target) for _ in range(5)]
        manifest['status']='verified_copy'
        manifest['original_records_preserved']=True
        atomic_json(target/'migration-manifest',manifest)
        atomic_json(Path('receipt-storage-migration.json').resolve(),manifest)
        # Last step switches only the journal location; continuous hardware execution remains disabled.
        backup=args.config.with_name(args.config.stem+'.before-receipt-migration.json')
        with backup.open('x') as stream:json.dump(config,stream,indent=2)
        config['joint_test_receipt_directory']=str(target)
        config['enable_continuous_motion']=False
        atomic_json(args.config.resolve(),config)
        print(f'Migration verified: {len(names)} receipts. Local repeated scans: '+str([round(r['elapsed_ms'],2) for r in manifest['warm_local_scans']])+' ms. Real motion not started.',flush=True)
    finally:
        if target_owned:target_lock.rmdir()
        if source_owned:source_lock.rmdir()

if __name__=='__main__':main()
