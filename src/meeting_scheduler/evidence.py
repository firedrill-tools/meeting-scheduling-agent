"""Private durable run evidence, separate from committed SQLite business state."""
from copy import deepcopy
from importlib.metadata import version
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from .native import aggregate_usage,now
from .schemas import sha


def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def publish(path,data):
    temporary=None
    try:
        with NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,prefix='.receipt-',suffix='.tmp',delete=False) as file:
            temporary=Path(file.name);json.dump(data,file,indent=2,allow_nan=False);file.write('\n');file.flush();os.fsync(file.fileno())
        os.replace(temporary,path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def actual_effects(before,after,request_id,cancel_event_id):
    original=next((event for event in before['meetings'] if event['request_id']==request_id),None)
    booking=next((event for event in after['meetings'] if event['request_id']==request_id),None)
    cancellation=next((event for event in after['cancellations'] if event['request_id']==request_id),None)
    target=next((event for event in after['meetings'] if event['event_id']==cancel_event_id),None) if cancel_event_id else None
    return {'booking_request':booking,'booking_created_during_run':booking is not None and original is None,
        'cancellation_request':cancellation,'cancellation_target':target,
        'confirmation':next((item for item in after['outbox'] if booking and item['event_id']==booking['event_id']),None),
        'cancellation_notice':next((item for item in after['cancellation_outbox'] if cancellation and item['event_id']==cancellation['event_id']),None)}


class Receipt:
    def __init__(self,context,request,model,output_root=None,writer=publish):
        root=(Path(output_root) if output_root else Path.cwd()/'.runs').resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
        run_id=str(uuid4());directory=root/run_id;directory.mkdir(mode=0o700);self.path=directory/'receipt.json'
        self.context=context;self.writer=writer;self.native=None;self.recording_error=None
        before=context.store.snapshot()
        self.data={'schema_version':1,'run_id':run_id,'receipt_path':str(self.path),'status':'running','execution_kind':'native_agents_responses',
            'started_at':now(),'finished_at':None,'request':request,'request_sha256':sha(request.encode()),'request_id':context.request_id,'requested_model':model,
            'versions':{name:version(name) for name in ('openai-agents','openai','httpx2','pydantic')},'database_path':context.store.path,
            'permissions':{'allow_book':context.allow_book,'booking_scope':context.booking_scope.model_dump(mode='json') if context.booking_scope else None,'allow_cancel':context.allow_cancel,'cancel_event_id':context.cancel_event_id},
            'initial_state':before,'initial_state_sha256':sha(encode(before)),'dataset':before['dataset'],'fixture_sha256':before['fixture_sha256'],
            'final_state':None,'final_state_sha256':None,'actual_effects':None,'provider_observations':[],'normalized_sdk_events':[],
            'reported_usage':aggregate_usage([]),'tool_events':[],'raw_final_output':None,'result':None,'error':None,'tracing_disabled':True}

    def guard(self):
        if self.recording_error:raise self.recording_error

    async def save(self):
        self.data['tool_events']=deepcopy(self.context.tool_events)
        if self.native:
            self.data['provider_observations']=deepcopy(self.native.observations)
            self.data['normalized_sdk_events']=deepcopy(self.native.sdk_events)
        self.data['reported_usage']=aggregate_usage(self.data['provider_observations'])
        try:self.writer(self.path,self.data)
        except BaseException as error:self.recording_error=error;raise

    async def finish(self,result,error):
        self.data.update(status='failed' if error else 'completed',finished_at=now(),result=result if error is None else None)
        try:
            state=self.context.store.snapshot();self.data.update(final_state=state,final_state_sha256=sha(encode(state)),actual_effects=actual_effects(self.data['initial_state'],state,self.context.request_id,self.context.cancel_event_id))
        except BaseException as snapshot_error:
            self.data['final_state_error']={'name':type(snapshot_error).__name__,'message':str(snapshot_error)}
            if error is None:raise
        if error:
            causes=[];current=error
            while current is not None and len(causes)<6:
                causes.append({'name':type(current).__name__,'message':str(current),'http_status':getattr(current,'status_code',None)})
                current=current.__cause__
            self.data['error']={'name':type(error).__name__,'message':str(error),'causes':causes}
        await self.save()
