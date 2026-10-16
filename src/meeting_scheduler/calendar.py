"""Transactional synthetic calendar, cancellation history and notification outboxes."""
from __future__ import annotations
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .schemas import duration,grid,identifier,iso,load_fixture,parse_utc,participants,revision


def booking_payload(start,minutes,ids):
    return json.dumps({'start_utc':iso(start),'duration_minutes':minutes,'participant_ids':sorted(ids)},sort_keys=True)


def cancellation_payload(event_id,reason):return json.dumps({'event_id':event_id,'reason':reason},sort_keys=True)


class CalendarStore:
    def __init__(self,path):
        self.path=str(Path(path).resolve());Path(self.path).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:descriptor=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        except FileExistsError:pass
        else:os.close(descriptor)
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('revision',0);
                INSERT OR IGNORE INTO meta VALUES('initialized',0);
                CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL,timezone TEXT NOT NULL,work_start INTEGER NOT NULL,work_end INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS busy(person_id TEXT NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,event_id TEXT);
                CREATE TABLE IF NOT EXISTS meetings(event_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,participant_ids TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY,recipients_json TEXT NOT NULL,message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cancellations(event_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,reason TEXT NOT NULL,cancelled_at TEXT NOT NULL,origin TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cancellation_outbox(event_id TEXT PRIMARY KEY,recipients_json TEXT NOT NULL,message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,operation TEXT NOT NULL,payload_json TEXT NOT NULL,event_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dataset_metadata(id INTEGER PRIMARY KEY CHECK(id=1),value_json TEXT,fixture_sha256 TEXT NOT NULL);
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(meetings)')}
            if 'status' not in columns:db.execute("ALTER TABLE meetings ADD COLUMN status TEXT NOT NULL DEFAULT 'scheduled'")
            if 'origin' not in columns:db.execute("ALTER TABLE meetings ADD COLUMN origin TEXT NOT NULL DEFAULT 'application'")
            db.execute("UPDATE meta SET value=1 WHERE key='initialized' AND EXISTS(SELECT 1 FROM people)")
            # Legacy meeting requests acquire the same original payload identity.
            db.execute("INSERT OR IGNORE INTO requests SELECT request_id,'book',payload_json,event_id FROM meetings")

    @contextmanager
    def _db(self,commit_guard=None):
        if commit_guard:commit_guard()
        db=sqlite3.connect(self.path,isolation_level=None,timeout=0.1);db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=100');db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            if db.in_transaction and commit_guard:commit_guard()
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def initialize(self,fixture_path):
        fixture,digest=load_fixture(fixture_path)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone()[0]:raise ValueError('calendar already initialized')
            for person in fixture.people:
                db.execute('INSERT INTO people VALUES(?,?,?,?,?,?)',(person.id,person.name,person.email,person.timezone,person.work_start,person.work_end))
            for block in fixture.busy:db.execute('INSERT INTO busy VALUES(?,?,?,NULL)',(block.person_id,iso(parse_utc(block.start_utc)),iso(parse_utc(block.end_utc))))
            for event in fixture.meetings:
                start=grid(event.start_utc);end=start+timedelta(minutes=event.duration_minutes);ids=list(event.participant_ids)
                if event.status=='scheduled' and not self._available(db,ids,start,end):raise ValueError('imported active meeting conflicts with work hours or busy state')
                payload=booking_payload(start,event.duration_minutes,ids)
                db.execute('INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?)',(event.event_id,event.request_id,payload,iso(start),iso(end),json.dumps(sorted(ids)),event.status,'human_import'))
                db.execute("INSERT INTO requests VALUES(?,'book',?,?)",(event.request_id,payload,event.event_id))
                if event.status=='scheduled':
                    for pid in ids:db.execute('INSERT INTO busy VALUES(?,?,?,?)',(pid,iso(start),iso(end),event.event_id))
                if event.confirmed:self._confirmation(db,dict(db.execute('SELECT * FROM meetings WHERE event_id=?',(event.event_id,)).fetchone()))
                if event.status=='cancelled':
                    db.execute('INSERT INTO cancellations VALUES(?,?,?,?,?)',(event.event_id,event.cancellation_request_id,event.cancellation_reason,iso(parse_utc(event.cancelled_at)),'human_import'))
                    db.execute("INSERT INTO requests VALUES(?,'cancel',?,?)",(event.cancellation_request_id,cancellation_payload(event.event_id,event.cancellation_reason),event.event_id))
                    if event.cancellation_notified:self._cancellation_notice(db,event.event_id)
            db.execute("UPDATE meta SET value=1 WHERE key='initialized'")
            db.execute('INSERT INTO dataset_metadata VALUES(1,?,?)',(json.dumps(fixture.dataset.model_dump(mode='json')) if fixture.dataset else None,digest))

    def _revision(self,db):return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def _people(self,db,participant_ids):
        people=[]
        for pid in participants(participant_ids):
            row=db.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
            if row is None:raise ValueError(f'unknown participant: {pid}')
            people.append(dict(row))
        return people

    def _available(self,db,participant_ids,start,end):
        for person in self._people(db,participant_ids):
            zone=ZoneInfo(person['timezone']);local_start,local_end=start.astimezone(zone),end.astimezone(zone)
            if local_start.weekday()>=5:return False
            midnight=datetime.combine(local_start.date(),datetime.min.time(),zone)
            if local_start<midnight+timedelta(hours=person['work_start']) or local_end>midnight+timedelta(hours=person['work_end']):return False
            for block in db.execute('SELECT start_utc,end_utc FROM busy WHERE person_id=?',(person['id'],)):
                if start<parse_utc(block['end_utc']) and end>parse_utc(block['start_utc']):return False
        return True

    def find_people(self,query):
        identifier(query,'participant query');terms=set(query.casefold().split())
        with self._db() as db:
            rows=[dict(row) for row in db.execute('SELECT * FROM people ORDER BY id') if all(term in ' '.join(str(row[key]) for key in ('id','name','email')).casefold() for term in terms)]
        return {'matches':rows[:20],'matching_count':len(rows),'truncated':len(rows)>20}

    def list_slots(self,day,participant_ids,duration_minutes=30):
        if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError('day must be YYYY-MM-DD in UTC')
        requested_day=date.fromisoformat(day);duration(duration_minutes);participants(participant_ids)
        with self._db() as db:
            db.execute('BEGIN');self._people(db,participant_ids);start=datetime.combine(requested_day,datetime.min.time(),timezone.utc)
            slots=[iso(start+timedelta(minutes=offset)) for offset in range(0,1440,30) if self._available(db,participant_ids,start+timedelta(minutes=offset),start+timedelta(minutes=offset+duration_minutes))]
            return {'revision':self._revision(db),'slots_utc':slots,'day_utc':day,'participant_ids':participant_ids,'duration_minutes':duration_minutes}

    def _request(self,db,request_id,operation,payload):
        previous=db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
        if previous and (previous['operation']!=operation or previous['payload_json']!=payload):raise ValueError('request_id reused for a different meeting or operation')
        return previous

    def book(self,request_id,start_utc,duration_minutes,participant_ids,expected_revision,*,commit_guard=None):
        identifier(request_id,'request_id');duration(duration_minutes);participants(participant_ids);revision(expected_revision)
        start=grid(start_utc);end=start+timedelta(minutes=duration_minutes);payload=booking_payload(start,duration_minutes,participant_ids)
        with self._db(commit_guard) as db:
            db.execute('BEGIN IMMEDIATE');previous=self._request(db,request_id,'book',payload)
            if previous:return dict(db.execute('SELECT * FROM meetings WHERE event_id=?',(previous['event_id'],)).fetchone())
            if self._revision(db)!=expected_revision:raise ValueError('calendar revision changed; list slots again')
            if not self._available(db,participant_ids,start,end):raise ValueError('slot unavailable; list slots again')
            event_id=str(uuid.uuid4())
            db.execute('INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?)',(event_id,request_id,payload,iso(start),iso(end),json.dumps(sorted(participant_ids)),'scheduled','application'))
            for pid in participant_ids:db.execute('INSERT INTO busy VALUES(?,?,?,?)',(pid,iso(start),iso(end),event_id))
            db.execute("INSERT INTO requests VALUES(?,'book',?,?)",(request_id,payload,event_id));db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
            return dict(db.execute('SELECT * FROM meetings WHERE event_id=?',(event_id,)).fetchone())

    def add_busy(self,person_id,start_utc,end_utc):
        start,end=parse_utc(start_utc),parse_utc(end_utc)
        if end<=start:raise ValueError('busy end must follow start')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._people(db,[person_id]);db.execute('INSERT INTO busy VALUES(?,?,?,NULL)',(person_id,iso(start),iso(end)));db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def find_request(self,request_id):
        identifier(request_id,'request_id')
        with self._db() as db:
            row=db.execute('SELECT * FROM meetings WHERE request_id=?',(request_id,)).fetchone();return dict(row) if row else None

    def inspect(self,request_id,*,cancel_event_id=None):
        identifier(request_id,'request_id')
        with self._db() as db:
            db.execute('BEGIN')
            event=db.execute('SELECT * FROM meetings WHERE request_id=?',(request_id,)).fetchone()
            target=db.execute('SELECT * FROM meetings WHERE event_id=?',(cancel_event_id,)).fetchone() if cancel_event_id else None
            intent=db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
            cancellation=db.execute('SELECT * FROM cancellations WHERE event_id=?',(cancel_event_id,)).fetchone() if cancel_event_id else None
            return {'revision':self._revision(db),'request_meeting':dict(event) if event else None,'cancellation_target':dict(target) if target else None,'original_request_intent':dict(intent) if intent else None,'target_cancellation':dict(cancellation) if cancellation else None}

    def _confirmation(self,db,event):
        prior=db.execute('SELECT * FROM outbox WHERE event_id=?',(event['event_id'],)).fetchone()
        if prior:return dict(prior)
        recipients=[person['email'] for person in self._people(db,json.loads(event['participant_ids']))]
        message=f"Meeting confirmed for {event['start_utc']} to {event['end_utc']} UTC."
        db.execute('INSERT INTO outbox VALUES(?,?,?)',(event['event_id'],json.dumps(recipients),message))
        return dict(db.execute('SELECT * FROM outbox WHERE event_id=?',(event['event_id'],)).fetchone())

    def confirm(self,event_id,*,request_id=None,commit_guard=None):
        identifier(event_id,'event_id')
        with self._db(commit_guard) as db:
            db.execute('BEGIN IMMEDIATE');event=db.execute('SELECT * FROM meetings WHERE event_id=?',(event_id,)).fetchone()
            if event is None:raise ValueError('unknown meeting; cannot send confirmation')
            if request_id is not None and event['request_id']!=request_id:raise ValueError('meeting belongs to another request; cannot confirm it')
            if event['status']!='scheduled':raise ValueError('cancelled meeting cannot be confirmed')
            return self._confirmation(db,event)

    def cancel(self,request_id,event_id,expected_revision,reason,*,commit_guard=None):
        identifier(request_id,'request_id');identifier(event_id,'event_id');revision(expected_revision)
        if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:raise ValueError('cancellation reason requires 1–1000 characters')
        payload=cancellation_payload(event_id,reason)
        with self._db(commit_guard) as db:
            db.execute('BEGIN IMMEDIATE');previous=self._request(db,request_id,'cancel',payload)
            if previous:return {**dict(db.execute('SELECT * FROM cancellations WHERE event_id=?',(event_id,)).fetchone()),'already_cancelled':True}
            event=db.execute('SELECT * FROM meetings WHERE event_id=?',(event_id,)).fetchone()
            if event is None:raise ValueError('unknown meeting; cannot cancel')
            if event['status']=='cancelled':return {**dict(db.execute('SELECT * FROM cancellations WHERE event_id=?',(event_id,)).fetchone()),'already_cancelled':True}
            if self._revision(db)!=expected_revision:raise ValueError('calendar revision changed; inspect meeting again')
            db.execute("UPDATE meetings SET status='cancelled' WHERE event_id=?",(event_id,));db.execute('DELETE FROM busy WHERE event_id=?',(event_id,))
            db.execute('INSERT INTO cancellations VALUES(?,?,?,?,?)',(event_id,request_id,reason,iso(datetime.now(timezone.utc)),'application'))
            db.execute("INSERT INTO requests VALUES(?,'cancel',?,?)",(request_id,payload,event_id));db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
            return {**dict(db.execute('SELECT * FROM cancellations WHERE event_id=?',(event_id,)).fetchone()),'already_cancelled':False}

    def _cancellation_notice(self,db,event_id):
        prior=db.execute('SELECT * FROM cancellation_outbox WHERE event_id=?',(event_id,)).fetchone()
        if prior:return dict(prior)
        event=db.execute('SELECT * FROM meetings WHERE event_id=?',(event_id,)).fetchone();recipients=[p['email'] for p in self._people(db,json.loads(event['participant_ids']))]
        db.execute('INSERT INTO cancellation_outbox VALUES(?,?,?)',(event_id,json.dumps(recipients),f"Meeting {event_id} at {event['start_utc']} was cancelled. This is a synthetic outbox notice."))
        return dict(db.execute('SELECT * FROM cancellation_outbox WHERE event_id=?',(event_id,)).fetchone())

    def notify_cancellation(self,event_id,*,request_id=None,commit_guard=None):
        identifier(event_id,'event_id')
        with self._db(commit_guard) as db:
            db.execute('BEGIN IMMEDIATE');cancellation=db.execute('SELECT * FROM cancellations WHERE event_id=?',(event_id,)).fetchone()
            if cancellation is None:raise ValueError('meeting has no cancellation; cannot notify')
            if request_id is not None and cancellation['request_id']!=request_id:raise ValueError('cancellation belongs to another request')
            return self._cancellation_notice(db,event_id)

    def snapshot(self):
        with self._db() as db:
            db.execute('BEGIN');metadata=db.execute('SELECT * FROM dataset_metadata WHERE id=1').fetchone()
            return {'schema_version':2,'revision':self._revision(db),'dataset':json.loads(metadata['value_json']) if metadata and metadata['value_json'] else None,'fixture_sha256':metadata['fixture_sha256'] if metadata else None,
                'people':[dict(r) for r in db.execute('SELECT * FROM people ORDER BY id')],
                'busy':[dict(r) for r in db.execute('SELECT rowid AS block_id,* FROM busy ORDER BY person_id,start_utc,end_utc,rowid')],
                'meetings':[dict(r) for r in db.execute('SELECT * FROM meetings ORDER BY start_utc,event_id')],
                'outbox':[dict(r) for r in db.execute('SELECT * FROM outbox ORDER BY event_id')],
                'cancellations':[dict(r) for r in db.execute('SELECT * FROM cancellations ORDER BY event_id')],
                'cancellation_outbox':[dict(r) for r in db.execute('SELECT * FROM cancellation_outbox ORDER BY event_id')],
                'requests':[dict(r) for r in db.execute('SELECT * FROM requests ORDER BY request_id')]}
