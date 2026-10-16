"""Cancellation, transaction fencing and real concurrent SQLite operations."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest
from agents.tool_context import ToolContext
from meeting_scheduler.agent import (SchedulingContext,RunStopped,book_slot,cancel_meeting,inspect_meeting,list_available_slots,send_cancellation,send_confirmation)
from meeting_scheduler.calendar import CalendarStore,booking_payload,parse_utc
from meeting_scheduler.schemas import BookingScope

FIXTURE=Path(__file__).parents[1]/'fixtures/starter.json'
SCOPE=BookingScope(day='2026-11-05',participant_ids=('mira','leo'),duration_minutes=30)


def seeded(tmp_path):
    store=CalendarStore(tmp_path/'private/calendar.db');store.initialize(FIXTURE);return store


def invoke(tool,context,args):
    text=json.dumps(args);wrapper=ToolContext(context,tool_name=tool.name,tool_call_id='lifecycle-proof',tool_arguments=text)
    return tool.on_invoke_tool(wrapper,text)


def test_cancellation_retains_original_confirmation_and_unrelated_busy_state(tmp_path):
    store=seeded(tmp_path)
    first=store.book('original','2026-11-05T15:00:00Z',30,['mira','leo'],0);store.confirm(first['event_id'])
    second=store.book('untouched','2026-11-05T15:30:00Z',30,['mira','leo'],1)
    original=store.snapshot();context=SchedulingContext(store,'cancel-1',False,allow_cancel=True,cancel_event_id=first['event_id'])
    inspected=json.loads(asyncio.run(invoke(inspect_meeting,context,{})));assert inspected['revision']==2
    cancelled=json.loads(asyncio.run(invoke(cancel_meeting,context,{'expected_revision':2,'reason':'Requester cancelled the meeting.'})))
    assert cancelled['event_id']==first['event_id'] and cancelled['already_cancelled'] is False
    notice=json.loads(asyncio.run(invoke(send_cancellation,context,{})))
    assert notice['event_id']==first['event_id'] and json.loads(notice['recipients_json'])==['leo@northstar.example','mira@harbor.example']
    state=store.snapshot();assert state['revision']==3 and len(state['meetings'])==2 and len(state['cancellations'])==1
    assert state['outbox']==original['outbox'] and len(state['cancellation_outbox'])==1
    assert [row for row in state['busy'] if row['event_id']!=first['event_id']]==[row for row in original['busy'] if row['event_id']!=first['event_id']]
    assert next(event for event in state['meetings'] if event['event_id']==second['event_id'])['status']=='scheduled'
    booking_context=SchedulingContext(store,'original',True,booking_scope=SCOPE)
    assert 'cancelled' in asyncio.run(invoke(send_confirmation,booking_context,{'event_id':first['event_id']}))
    # Original booking replay is a state read; it must not resurrect an event.
    assert store.book('original','2026-11-05T15:00:00Z',30,['leo','mira'],0)['status']=='cancelled'
    assert store.cancel('cancel-1',first['event_id'],2,'Requester cancelled the meeting.')['already_cancelled'] is True
    assert store.notify_cancellation(first['event_id'],request_id='cancel-1')==notice
    assert store.snapshot()['revision']==3


def test_cancellation_permission_is_separate_and_targets_only_the_caller_event(tmp_path):
    store=seeded(tmp_path);event=store.book('original','2026-11-05T15:00:00Z',30,['mira','leo'],0)
    denied=SchedulingContext(store,'denied',True,booking_scope=SCOPE,cancel_event_id=event['event_id'])
    assert 'disabled' in asyncio.run(invoke(cancel_meeting,denied,{'expected_revision':1,'reason':'Cancel'}))
    missing=SchedulingContext(store,'missing',False,allow_cancel=True)
    assert 'explicit caller' in asyncio.run(invoke(cancel_meeting,missing,{'expected_revision':1,'reason':'Cancel'}))
    assert 'disabled' in asyncio.run(invoke(book_slot,SchedulingContext(store,'cancel-only',False,allow_cancel=True,cancel_event_id=event['event_id']),{'start_utc':'2026-11-05T15:30:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':1}))
    assert store.snapshot()['cancellations']==[] and store.snapshot()['revision']==1


def test_cancel_changed_revision_and_cross_operation_request_identity(tmp_path):
    store=seeded(tmp_path);event=store.book('original','2026-11-05T15:00:00Z',30,['mira','leo'],0)
    store.add_busy('sam','2026-11-05T15:00:00Z','2026-11-05T15:30:00Z')
    with pytest.raises(ValueError,match='revision changed'):store.cancel('cancelled',event['event_id'],1,'Cancel')
    with pytest.raises(ValueError,match='different meeting or operation'):store.cancel('original',event['event_id'],2,'Cancel')
    with pytest.raises(ValueError,match='no cancellation'):store.notify_cancellation(event['event_id'])
    result=store.cancel('cancelled',event['event_id'],2,'Cancel')
    with pytest.raises(ValueError,match='different meeting or operation'):store.cancel('cancelled',event['event_id'],3,'Changed reason')
    with pytest.raises(ValueError,match='different meeting or operation'):store.book('cancelled','2026-11-05T15:30:00Z',30,['mira','leo'],3)
    with pytest.raises(ValueError,match='another request'):store.notify_cancellation(event['event_id'],request_id='foreign-cancel')
    assert store.snapshot()['revision']==3 and result['already_cancelled'] is False


def test_concurrent_cancellations_commit_once(tmp_path):
    store=seeded(tmp_path);event=store.book('original','2026-11-05T15:00:00Z',30,['mira','leo'],0);ready=threading.Barrier(2)
    def cancel(index):ready.wait();return store.cancel(f'cancel-{index}',event['event_id'],1,'Cancel')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(cancel,[1,2]))
    assert sorted(result['already_cancelled'] for result in results)==[False,True]
    state=store.snapshot();assert state['revision']==2 and len(state['cancellations'])==1 and len(state['requests'])==2


@pytest.mark.parametrize('same_request',[True,False])
def test_concurrent_bookings_preserve_exact_retry_and_revision_exclusion(tmp_path,same_request):
    store=seeded(tmp_path);ready=threading.Barrier(2)
    def book(index):
        ready.wait()
        try:return store.book('one-request' if same_request else f'request-{index}','2026-11-05T15:00:00Z',30,['mira','leo'],0)
        except ValueError as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(book,[1,2]))
    successes=[result for result in results if isinstance(result,dict)]
    assert len(successes)==(2 if same_request else 1)
    if same_request:assert successes[0]['event_id']==successes[1]['event_id']
    else:assert any(isinstance(result,str) and 'revision changed' in result for result in results)
    assert store.snapshot()['revision']==1 and len(store.snapshot()['meetings'])==1


def test_actual_async_sdk_tools_never_offload_database_operations(tmp_path,monkeypatch):
    store=seeded(tmp_path);context=SchedulingContext(store,'async-owned',True,booking_scope=SCOPE)
    async def forbidden(*args,**kwargs):raise AssertionError('calendar SDK tools must not spawn detached write threads')
    monkeypatch.setattr(asyncio,'to_thread',forbidden)
    async def exercise():
        await invoke(list_available_slots,context,{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30})
        result=json.loads(await invoke(book_slot,context,{'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0}))
        assert result['status']=='scheduled'
    asyncio.run(exercise());assert len(store.snapshot()['meetings'])==1


def test_deadline_expiring_inside_transaction_rolls_back_before_commit(tmp_path,monkeypatch):
    store=seeded(tmp_path);context=SchedulingContext(store,'expired',True,booking_scope=SCOPE)
    original=store._request;entered=[]
    def slow(*args):
        entered.append(True);context.deadline=time.monotonic()+0.005;time.sleep(0.015);return original(*args)
    monkeypatch.setattr(store,'_request',slow)
    async def exercise():
        await invoke(list_available_slots,context,{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30})
        with pytest.raises(RunStopped,match='deadline expired'):await invoke(book_slot,context,{'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0})
    asyncio.run(exercise());state=store.snapshot();assert entered and state['revision']==0 and state['meetings']==[] and state['requests']==[]


def test_closed_or_cancelled_tool_cannot_schedule_a_late_write(tmp_path):
    store=seeded(tmp_path);context=SchedulingContext(store,'closed',True,booking_scope=SCOPE)
    async def exercise():
        await invoke(list_available_slots,context,{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30})
        args={'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0}
        task=asyncio.create_task(invoke(book_slot,context,args));task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        context.close()
        with pytest.raises(RunStopped,match='closed'):await invoke(book_slot,context,args)
    asyncio.run(exercise());assert store.snapshot()['meetings']==[]


@pytest.mark.parametrize('fault',['unknown-busy','reversed-block','work-hour','duplicate-email','boolean-hours','overlap-import'])
def test_invalid_fixture_rolls_back_and_can_be_reinitialized(tmp_path,fault):
    source=json.loads(FIXTURE.read_text())
    if fault=='unknown-busy':source['busy'][0]['person_id']='unknown'
    if fault=='reversed-block':source['busy'][0]['end_utc']=source['busy'][0]['start_utc']
    if fault=='work-hour':source['people'][0]['work_end']=8
    if fault=='duplicate-email':source['people'][1]['email']=source['people'][0]['email'].upper()
    if fault=='boolean-hours':source['people'][0]['work_start']=True
    if fault=='overlap-import':source['meetings']=[{'event_id':'human-meeting','request_id':'human-request','start_utc':'2026-11-05T14:00:00Z','duration_minutes':30,'participant_ids':['mira','leo']}]
    file=tmp_path/'bad.json';file.write_text(json.dumps(source));store=CalendarStore(tmp_path/'calendar.db')
    with pytest.raises(ValueError):store.initialize(file)
    assert store.snapshot()['people']==[] and store.snapshot()['busy']==[]
    store.initialize(FIXTURE);assert len(store.snapshot()['people'])==3


@pytest.mark.parametrize('operation,value',[('duration',30.0),('duration',True),('revision',True),('revision',-1)])
def test_typed_mutation_values_do_not_silently_coerce(tmp_path,operation,value):
    store=seeded(tmp_path)
    with pytest.raises(ValueError):store.book('strict','2026-11-05T15:00:00Z',value if operation=='duration' else 30,['mira','leo'],value if operation=='revision' else 0)
    assert store.snapshot()['meetings']==[]


def test_new_database_and_wal_files_are_private(tmp_path):
    store=seeded(tmp_path)
    assert Path(store.path).stat().st_mode&0o777==0o600 and Path(store.path).parent.stat().st_mode&0o777==0o700
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE');db.execute("UPDATE meta SET value=value WHERE key='revision'")
        for suffix in ('-wal','-shm'):
            path=Path(store.path+suffix)
            if path.exists():assert path.stat().st_mode&0o777==0o600


def test_legacy_schema_migrates_without_inventing_model_or_dataset_history(tmp_path):
    path=tmp_path/'legacy.db';payload=booking_payload(parse_utc('2026-11-05T15:00:00Z'),30,['mira','leo']);source=json.loads(FIXTURE.read_text())
    db=sqlite3.connect(path);db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);INSERT INTO meta VALUES('revision',1);
    CREATE TABLE people(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL,timezone TEXT NOT NULL,work_start INTEGER NOT NULL,work_end INTEGER NOT NULL);
    CREATE TABLE busy(person_id TEXT NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,event_id TEXT);
    CREATE TABLE meetings(event_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,participant_ids TEXT NOT NULL);
    CREATE TABLE outbox(event_id TEXT PRIMARY KEY,recipients_json TEXT NOT NULL,message TEXT NOT NULL);''')
    for person in source['people']:db.execute('INSERT INTO people VALUES(?,?,?,?,?,?)',tuple(person[key] for key in ('id','name','email','timezone','work_start','work_end')))
    db.execute('INSERT INTO meetings VALUES(?,?,?,?,?,?)',('legacy-event','legacy-request',payload,'2026-11-05T15:00:00Z','2026-11-05T15:30:00Z','["leo","mira"]'))
    for pid in ('mira','leo'):db.execute('INSERT INTO busy VALUES(?,?,?,?)',(pid,'2026-11-05T15:00:00Z','2026-11-05T15:30:00Z','legacy-event'))
    db.commit();db.close();store=CalendarStore(path);state=store.snapshot()
    assert state['revision']==1 and state['dataset'] is None and state['fixture_sha256'] is None
    assert state['meetings'][0]['status']=='scheduled' and state['meetings'][0]['origin']=='application'
    assert store.book('legacy-request','2026-11-05T15:00:00Z',30,['mira','leo'],0)['event_id']=='legacy-event'
    with pytest.raises(ValueError,match='already initialized'):store.initialize(FIXTURE)


def test_workday_ending_at_24_allows_exact_midnight_without_an_overnight_shift(tmp_path):
    fixture={'people':[{'id':'late','name':'Late Shift','email':'late@calendar.example','timezone':'UTC','work_start':23,'work_end':24}],'busy':[]}
    file=tmp_path/'late.json';file.write_text(json.dumps(fixture));store=CalendarStore(tmp_path/'late.db');store.initialize(file)
    slots=store.list_slots('2026-11-06',['late'])['slots_utc'];assert '2026-11-06T23:30:00Z' in slots
    event=store.book('late-request','2026-11-06T23:30:00Z',30,['late'],0);assert event['end_utc']=='2026-11-07T00:00:00Z'
