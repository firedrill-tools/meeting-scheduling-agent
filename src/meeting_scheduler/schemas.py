"""Strict synthetic calendar inputs and caller-owned action scope."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BYTES=4*1024*1024


def sha(raw):return hashlib.sha256(raw).hexdigest()


def bounded_read(path):
    with Path(path).open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('calendar fixture exceeds 4 MiB')
    return raw


def identifier(value,name='identifier'):
    if not isinstance(value,str) or not value.strip() or len(value)>100:raise ValueError(f'{name} requires 1–100 characters')
    return value


def parse_utc(value:str)->datetime:
    if not isinstance(value,str) or len(value)>64:raise ValueError('UTC timestamp required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset()!=timedelta(0):raise ValueError('UTC timestamp with Z or +00:00 required')
    return parsed.astimezone(timezone.utc)


def iso(value):return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def duration(value):
    if type(value) is not int or not 15<=value<=120 or value%15:raise ValueError('duration must be 15-120 integer minutes in 15-minute steps')
    return value


def revision(value):
    if type(value) is not int or not 0<=value<=2**53-1:raise ValueError('revision must be a nonnegative safe integer')
    return value


def participants(value):
    if not isinstance(value,(list,tuple)) or not 1<=len(value)<=12 or any(not isinstance(item,str) or not item.strip() or len(item)>100 for item in value) or len(value)!=len(set(value)):raise ValueError('1–12 unique participant IDs required')
    return list(value)


def grid(value):
    parsed=parse_utc(value)
    if parsed.minute%30 or parsed.second or parsed.microsecond:raise ValueError('start must be on the listed half-hour UTC grid')
    return parsed


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)


class Person(StrictModel):
    id:str=Field(min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=150)
    email:str=Field(min_length=1,max_length=200)
    timezone:str=Field(min_length=1,max_length=100)
    work_start:int=Field(ge=0,le=23,strict=True)
    work_end:int=Field(ge=1,le=24,strict=True)

    @field_validator('id','name')
    @classmethod
    def meaningful(cls,value):return identifier(value)

    @field_validator('timezone')
    @classmethod
    def real_zone(cls,value):ZoneInfo(value);return value

    @field_validator('email')
    @classmethod
    def fictional_address(cls,value):
        if value.count('@')!=1 or any(c.isspace() for c in value) or not value.rsplit('@',1)[1].endswith('.example'):raise ValueError('synthetic .example email address required')
        return value

    @model_validator(mode='after')
    def workday(self):
        if self.work_start>=self.work_end:raise ValueError('work end must follow start; overnight shifts are unsupported')
        return self


class BusyBlock(StrictModel):
    person_id:str
    start_utc:str
    end_utc:str

    @model_validator(mode='after')
    def interval(self):
        identifier(self.person_id)
        if parse_utc(self.end_utc)<=parse_utc(self.start_utc):raise ValueError('busy end must follow start')
        return self


class HistoricalMeeting(StrictModel):
    event_id:str=Field(min_length=1,max_length=100)
    request_id:str=Field(min_length=1,max_length=100)
    start_utc:str
    duration_minutes:int=Field(strict=True)
    participant_ids:tuple[str,...]
    status:Literal['scheduled','cancelled']='scheduled'
    confirmed:bool=Field(default=False,strict=True)
    origin:Literal['human_import']='human_import'
    cancellation_request_id:str|None=None
    cancellation_reason:str|None=Field(default=None,max_length=1000)
    cancelled_at:str|None=None
    cancellation_notified:bool=Field(default=False,strict=True)

    @model_validator(mode='after')
    def coherent(self):
        identifier(self.event_id);identifier(self.request_id);grid(self.start_utc);duration(self.duration_minutes);participants(self.participant_ids)
        cancellation=(self.cancellation_request_id,self.cancellation_reason,self.cancelled_at)
        if self.status=='scheduled' and (any(item is not None for item in cancellation) or self.cancellation_notified):raise ValueError('scheduled meeting cannot have cancellation history')
        if self.status=='cancelled':
            identifier(self.cancellation_request_id,'cancellation request');
            if not self.cancellation_reason or not self.cancellation_reason.strip():raise ValueError('cancellation reason required')
            parse_utc(self.cancelled_at)
        return self


class DatasetIdentity(StrictModel):
    id:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1,strict=True)
    profile:Literal['starter','busy','ambiguous','boundary']
    case_id:str=Field(min_length=1,max_length=100)


class Fixture(StrictModel):
    people:tuple[Person,...]=Field(min_length=1,max_length=1000)
    busy:tuple[BusyBlock,...]=Field(max_length=10000)
    meetings:tuple[HistoricalMeeting,...]=Field(default=(),max_length=1000)
    synthetic:Literal[True]=True
    dataset:DatasetIdentity|None=None

    @field_validator('synthetic',mode='before')
    @classmethod
    def explicit_synthetic(cls,value):
        if value is not True:raise ValueError('synthetic must be true')
        return value

    @model_validator(mode='after')
    def relationships(self):
        ids={p.id for p in self.people}
        if len(ids)!=len(self.people):raise ValueError('duplicate participant identity')
        emails=[p.email.casefold() for p in self.people]
        if len(set(emails))!=len(emails):raise ValueError('duplicate participant email')
        blocks=set()
        for block in self.busy:
            if block.person_id not in ids:raise ValueError('busy block has unknown participant')
            key=(block.person_id,iso(parse_utc(block.start_utc)),iso(parse_utc(block.end_utc)))
            if key in blocks:raise ValueError('duplicate busy block')
            blocks.add(key)
        events=set();requests=set()
        for event in self.meetings:
            if event.event_id in events or event.request_id in requests:raise ValueError('duplicate meeting/request identity')
            events.add(event.event_id);requests.add(event.request_id)
            if any(pid not in ids for pid in event.participant_ids):raise ValueError('meeting has unknown participant')
            if event.cancellation_request_id:
                if event.cancellation_request_id in requests:raise ValueError('request identity reused across operations')
                requests.add(event.cancellation_request_id)
        return self


class BookingScope(StrictModel):
    day:date
    participant_ids:tuple[str,...]
    duration_minutes:int=Field(strict=True)

    @model_validator(mode='after')
    def coherent(self):participants(self.participant_ids);duration(self.duration_minutes);return self

    def allows(self,start,minutes,ids):
        return parse_utc(start).date()==self.day and minutes==self.duration_minutes and sorted(ids)==sorted(self.participant_ids)


def load_fixture(path):
    raw=bounded_read(path);return Fixture.model_validate_json(raw),sha(raw)
