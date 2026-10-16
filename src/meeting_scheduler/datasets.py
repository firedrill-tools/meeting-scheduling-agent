"""Hash-bound calendar cases; explicit write permission remains caller-owned."""
import json
import os
from pathlib import Path

from pydantic import BaseModel,ConfigDict
from .calendar import CalendarStore
from .schemas import BookingScope,bounded_read,identifier,load_fixture,sha


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    booking_scope:BookingScope|None
    cancel_event_id:str|None


def data_root():
    bundled=Path(__file__).parent/'sample_data'
    return bundled if bundled.exists() else Path(__file__).parents[2]/'data'


def load_manifest(root=None):
    root=Path(root) if root is not None else data_root();raw=bounded_read(root/'manifest.json');manifest=json.loads(raw)
    if manifest.get('id')!='synthetic-scheduling-cases' or type(manifest.get('version')) is not int or manifest['version']!=1 or manifest.get('synthetic') is not True:raise ValueError('unsupported manifest identity')
    cases=manifest.get('cases')
    if not isinstance(cases,list) or not cases:raise ValueError('manifest has no cases')
    ids=set();files=set()
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not case['id'].strip() or case['id'] in ids:raise ValueError('invalid/duplicate case identity')
        ids.add(case['id']);file=case.get('file')
        if not isinstance(file,str) or not file or Path(file).is_absolute() or '..' in Path(file).parts or file in files:raise ValueError('invalid case path')
        files.add(file)
        if case.get('profile') not in {'starter','busy','ambiguous','boundary'}:raise ValueError('invalid profile')
        identifier(case.get('request_id'),'case request_id')
        if not isinstance(case.get('request'),str) or not case['request'].strip() or len(case['request'])>8000:raise ValueError('invalid case request')
        target=(root/file).resolve(strict=True)
        if not target.is_relative_to(root.resolve(strict=True)):raise ValueError('case path escapes dataset')
        fixture,digest=load_fixture(target)
        if digest!=case.get('sha256'):raise ValueError('case hash differs')
        if not fixture.dataset or fixture.dataset.model_dump()!={'id':manifest['id'],'version':1,'profile':case['profile'],'case_id':case['id']}:raise ValueError('case identity differs')
        if any(type(case.get(key)) is not int for key in ('people_count','external_blocks','historical_meetings')):raise ValueError('population counts must be integers')
        if len(fixture.people)!=case.get('people_count') or len(fixture.busy)!=case.get('external_blocks') or len(fixture.meetings)!=case.get('historical_meetings'):raise ValueError('case population differs')
        selected=Selection.model_validate(case.get('selection'))
        if selected.booking_scope:
            if any(pid not in {p.id for p in fixture.people} for pid in selected.booking_scope.participant_ids):raise ValueError('case scope has unknown participant')
        if selected.cancel_event_id is not None and selected.cancel_event_id not in {m.event_id for m in fixture.meetings}:raise ValueError('case target has unknown meeting')
    return manifest,sha(raw),root


def list_cases(root=None):
    manifest,digest,_=load_manifest(root);return {**manifest,'manifest_sha256':digest}


def select(case_id,root=None):
    manifest,digest,root=load_manifest(root);case=next((item for item in manifest['cases'] if item['id']==case_id),None)
    if case is None:raise ValueError('unknown case')
    path=(root/case['file']).resolve(strict=True);fixture,file_digest=load_fixture(path)
    if file_digest!=case['sha256']:raise ValueError('case changed before selection')
    return case,path,digest


def initialize_fixture(fixture_path,path):
    # Validate before reserving a fresh DB; never erase an existing DB/WAL owner.
    fixture,digest=load_fixture(fixture_path);path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(descriptor)
    try:
        store=CalendarStore(path);store.initialize(fixture_path)
        if store.snapshot()['fixture_sha256']!=digest:raise ValueError('fixture changed during initialization')
        return store
    except BaseException:
        for suffix in ('','-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
        raise


def initialize(case_id,path,root=None):
    case,source,digest=select(case_id,root);store=initialize_fixture(source,path)
    if store.snapshot()['fixture_sha256']!=case['sha256']:raise ValueError('case changed before initialization')
    return {'database_path':store.path,'dataset':store.snapshot()['dataset'],'fixture_sha256':case['sha256'],'manifest_sha256':digest,'request':case['request'],'request_id':case['request_id'],'selection':case['selection']}
