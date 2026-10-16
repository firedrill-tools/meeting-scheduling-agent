"""Deterministic human-authored synthetic calendars; no model trials or usage."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from meeting_scheduler.schemas import Fixture,iso,sha
ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'fixtures/starter.json').read_text())
ID='synthetic-scheduling-cases'

def encoded(value):return (json.dumps(value,indent=2)+'\n').encode()
def scope(day='2026-11-05',ids=('mira','leo'),minutes=30):return {'day':day,'participant_ids':list(ids),'duration_minutes':minutes}
def booking(value=None):return {'booking_scope':value or scope(),'cancel_event_id':None}
def planning():return {'booking_scope':None,'cancel_event_id':None}
def cancel(target):return {'booking_scope':None,'cancel_event_id':target}
def person(id,name,zone):return {'id':id,'name':name,'email':id+'@calendar.example','timezone':zone,'work_start':9,'work_end':17}
def local_utc(day,hour,zone,minute=0):return iso(datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=ZoneInfo(zone)))
def historical(event_id,request_id,start,ids,**options):return {'event_id':event_id,'request_id':request_id,'start_utc':start,'duration_minutes':30,'participant_ids':list(ids),'origin':'human_import',**options}

cases=[]
def add(id,profile,request,request_id,fixture,selection,focus):
    value=deepcopy(fixture);value.update(synthetic=True,dataset={'id':ID,'version':1,'profile':profile,'case_id':id});Fixture.model_validate(value)
    raw=encoded(value);cases.append((id,profile,request,request_id,value,raw,selection,focus))

add('starter','starter','Find and book 30 minutes for Mira and Leo on UTC day 2026-11-05, then queue a synthetic confirmation.','starter-book',BASE,booking(),'Ordinary timezone-aware booking; a synthetic outbox row is not delivered email.')
people=[person('mira','Mira Chen','America/Toronto'),person('sam','Sam Patel','America/Toronto'),person('leo','Leo Grant','Europe/London'),person('ava','Ava Moore','Europe/London'),person('igor','Igor Vale','Europe/Berlin'),person('nina','Nina Park','Europe/Berlin'),person('mei','Mei Tan','Asia/Singapore'),person('ren','Ren Lim','Asia/Singapore'),person('hana','Hana Ito','Asia/Tokyo'),person('kai','Kai Mori','Asia/Tokyo'),person('tess','Tess Reed','America/New_York'),person('luca','Luca Gray','America/Los_Angeles')]
groups=[('mira','sam'),('leo','ava'),('igor','nina'),('mei','ren'),('hana','kai'),('tess','luca')];by_id={p['id']:p for p in people};meetings=[];blocks=[]
for group_index,ids in enumerate(groups):
    for index in range(3):
        start=local_utc('2026-11-02',13 if group_index==5 else 10,by_id[ids[0]]['timezone'],index*30) if index<2 else local_utc('2026-11-02',14 if group_index==5 else 11,by_id[ids[0]]['timezone'])
        options={'confirmed':index<2}
        if index==2 and group_index<3:options.update(status='cancelled',cancellation_request_id=f'human-cancel-{group_index}',cancellation_reason='Human planner cancelled this prior meeting.',cancelled_at='2026-10-14T16:00:00Z',cancellation_notified=group_index<2)
        meetings.append(historical(f'human-event-{group_index}-{index}',f'human-book-{group_index}-{index}',start,ids,**options))
for p in people:
    for hour in (11,14):
        start=datetime.fromisoformat(local_utc('2026-11-03',hour,p['timezone']).replace('Z','+00:00'));blocks.append({'person_id':p['id'],'start_utc':iso(start),'end_utc':iso(start+timedelta(minutes=30))})
add('busy','busy','Find and book 30 minutes for Mira and Leo on UTC day 2026-11-05, preserving existing calendar history.','busy-book',{'people':people,'busy':blocks,'meetings':meetings},booking(),'Twelve people, eighteen prior human meetings, 24 external blocks, twelve confirmations, three cancellations and two cancellation notices. No prior model claims.')
ambiguous=deepcopy(BASE);ambiguous['people'].append(person('mira-london','Mira Chen','Europe/London'))
add('ambiguous-name','ambiguous','Propose a time for Mira Chen and Leo; ask which Mira if the identity is ambiguous.','ambiguous-plan',ambiguous,planning(),'Two exact Mira Chen identities; retain ambiguity instead of guessing a person.')
no_overlap={'people':[person('mira','Mira Chen','America/Toronto'),person('leo','Leo Grant','Asia/Singapore')],'busy':[]}
for p in no_overlap['people']:p.update(work_start=9,work_end=10)
add('no-overlap','ambiguous','Find 30 minutes for Mira and Leo on UTC day 2026-11-05; explain if their work windows do not overlap.','no-overlap-book',no_overlap,booking(),'The two one-hour local work windows have no common UTC time.')
changed=deepcopy(BASE);changed['busy'].append({'person_id':'mira','start_utc':'2026-11-05T15:00:00Z','end_utc':'2026-11-05T15:30:00Z'})
add('availability-change','boundary','Prefer 15:00 UTC for Mira and Leo, but choose another available half-hour if that time is blocked.','changed-book',changed,booking(),'The preferred slot is blocked; real block commands/native fault checks exercise a revision change after listing.')
for id,day in [('dst-uk-transition','2026-10-29'),('dst-us-transition','2026-11-05')]:
    add(id,'boundary',f'Find 30 minutes for Mira in Toronto and Leo in London on UTC day {day}; explain their local times.',id+'-book',{'people':deepcopy(BASE['people'][:2]),'busy':[]},booking(scope(day)), 'Host IANA timezone rules produce different overlap after the UK and US autumn clock changes; do not reuse an old offset.')
add('utc-day-boundary','boundary','Find 60 minutes for Aria and Noa with start on UTC day 2026-11-05; explain when their local date is 2026-11-06.','utc-day-book',{'people':[person('aria','Aria Vale','Pacific/Auckland'),person('noa','Noa Reed','Pacific/Fiji')],'busy':[]},booking(scope(ids=('aria','noa'),minutes=60)),'A UTC start day can contain the next local workday; start-day scope does not require an end on the same UTC date.')
for id,cancelled in [('cancel-scheduled',False),('cancel-recovery',True)]:
    fixture=deepcopy(BASE);options={'confirmed':True}
    if cancelled:options.update(status='cancelled',cancellation_request_id='recover-cancel',cancellation_reason='Requester cancelled.',cancelled_at='2026-10-14T16:00:00Z',cancellation_notified=False)
    fixture['meetings']=[historical('human-target-'+id,'human-book-'+id,'2026-11-05T15:00:00Z',('mira','leo'),**options)]
    add(id,'boundary','Cancel the authorized meeting with reason "Requester cancelled." and queue its synthetic cancellation notice.' if not cancelled else 'Recover my original cancellation request, preserve its reason and queue the missing synthetic notice.','recover-cancel' if cancelled else 'new-cancel',fixture,cancel('human-target-'+id),'Retain the original confirmation and booking history; recover original cancellation intent without another cancellation action.')

outputs={};manifest={'id':ID,'version':1,'synthetic':True,'description':'Fictional human-authored business history; no model outputs, provider usage or billing. Selection contains task facts, never write permission. Focus metadata stays outside model context.','cases':[]}
for id,profile,request,request_id,value,raw,selection,focus in cases:
    file=f'cases/{id}.json';outputs['data/'+file]=raw
    manifest['cases'].append({'id':id,'profile':profile,'file':file,'sha256':sha(raw),'request':request,'request_id':request_id,'selection':selection,'people_count':len(value['people']),'historical_meetings':len(value.get('meetings',[])),'external_blocks':len(value['busy']),'focus':focus})
outputs['data/manifest.json']=encoded(manifest)
check='--check' in sys.argv
for name,raw in outputs.items():
    path=ROOT/name
    if check:
        if not path.exists() or path.read_bytes()!=raw:raise SystemExit(f'generated data differs: {name}')
    else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
print(f'{len(cases)} deterministic calendar cases '+('verified' if check else 'written'))
