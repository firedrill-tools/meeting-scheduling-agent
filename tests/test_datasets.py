import json
from pathlib import Path
import shutil

import pytest
from meeting_scheduler.calendar import CalendarStore
from meeting_scheduler.datasets import data_root,initialize,list_cases,load_manifest,select


def test_all_ten_cases_initialize_coherently_and_exclusively(tmp_path):
    manifest=list_cases();assert len(manifest['cases'])==10 and {case['profile'] for case in manifest['cases']}=={'starter','busy','ambiguous','boundary'}
    for case in manifest['cases']:
        selected,source,digest=select(case['id']);before=source.read_bytes();destination=tmp_path/case['id']/'calendar.db'
        record=initialize(case['id'],destination);store=CalendarStore(destination);state=store.snapshot()
        assert record['fixture_sha256']==case['sha256']==state['fixture_sha256'] and record['manifest_sha256']==digest
        assert state['dataset']['case_id']==case['id'] and len(state['people'])==case['people_count'] and len(state['meetings'])==case['historical_meetings']
        assert all(event['origin']=='human_import' for event in state['meetings']) and all(item['origin']=='human_import' for item in state['cancellations'])
        assert source.read_bytes()==before and destination.stat().st_mode&0o777==0o600
        with pytest.raises(FileExistsError):initialize(case['id'],destination)
        assert CalendarStore(destination).snapshot()==state


def test_busy_history_and_ambiguous_people_are_real_business_state(tmp_path):
    initialize('busy',tmp_path/'busy.db');state=CalendarStore(tmp_path/'busy.db').snapshot()
    assert len(state['people'])==12 and len(state['meetings'])==18 and len(state['busy'])==54 and len(state['outbox'])==12
    assert len(state['cancellations'])==3 and len(state['cancellation_outbox'])==2 and state['revision']==0
    initialize('ambiguous-name',tmp_path/'names.db');store=CalendarStore(tmp_path/'names.db')
    people=store.find_people('Mira Chen');assert people['matching_count']==2 and {p['id'] for p in people['matches']}=={'mira','mira-london'}
    initialize('no-overlap',tmp_path/'empty.db');assert CalendarStore(tmp_path/'empty.db').list_slots('2026-11-05',['mira','leo'])['slots_utc']==[]


def test_dst_offsets_and_utc_start_day_boundaries_use_actual_zoneinfo(tmp_path):
    initialize('dst-uk-transition',tmp_path/'uk.db');initialize('dst-us-transition',tmp_path/'us.db')
    uk=CalendarStore(tmp_path/'uk.db').list_slots('2026-10-29',['mira','leo'])['slots_utc'];us=CalendarStore(tmp_path/'us.db').list_slots('2026-11-05',['mira','leo'])['slots_utc']
    assert '2026-10-29T13:00:00Z' in uk and '2026-11-05T13:00:00Z' not in us and '2026-11-05T14:00:00Z' in us
    initialize('utc-day-boundary',tmp_path/'utc.db');store=CalendarStore(tmp_path/'utc.db');slots=store.list_slots('2026-11-05',['aria','noa'],60)['slots_utc']
    assert '2026-11-05T23:30:00Z' in slots
    event=store.book('utc-end-boundary','2026-11-05T23:30:00Z',60,['aria','noa'],0)
    assert event['end_utc']=='2026-11-06T00:30:00Z'


def test_recovery_case_exposes_original_cancel_intent_without_provider_history(tmp_path):
    initialize('cancel-recovery',tmp_path/'recover.db');store=CalendarStore(tmp_path/'recover.db');state=store.snapshot()
    assert len(state['outbox'])==1 and state['cancellation_outbox']==[] and state['meetings'][0]['status']=='cancelled'
    inspected=store.inspect('recover-cancel',cancel_event_id=state['meetings'][0]['event_id'])
    assert json.loads(inspected['original_request_intent']['payload_json'])['reason']=='Requester cancelled.'
    assert inspected['target_cancellation']['origin']=='human_import'


@pytest.mark.parametrize('fault',['hash','identity','path','symlink','population'])
def test_manifest_drift_or_escaping_path_rejects_initialization(tmp_path,fault):
    root=tmp_path/'data';shutil.copytree(data_root(),root);manifest=json.loads((root/'manifest.json').read_text());case=manifest['cases'][0];target=root/case['file']
    if fault=='hash':target.write_bytes(target.read_bytes()+b'\n')
    if fault=='identity':case['id']='wrong'
    if fault=='path':case['file']='../outside.json'
    if fault=='symlink':
        outside=tmp_path/'outside.json';outside.write_bytes(target.read_bytes());target.unlink();target.symlink_to(outside)
    if fault=='population':case['people_count']+=1
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):load_manifest(root)
