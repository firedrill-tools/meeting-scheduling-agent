"""Actual Agents SDK/OpenAI Responses HTTP; controlled replies are not live judgment."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx2
import pytest

from meeting_scheduler import native
from meeting_scheduler.agent import run_agent,run_agent_async
from meeting_scheduler.calendar import CalendarStore
from meeting_scheduler.schemas import BookingScope

FIXTURE=Path(__file__).parents[1]/'fixtures/starter.json'
SCOPE=BookingScope(day='2026-11-05',participant_ids=('mira','leo'),duration_minutes=30)
USAGE={'input_tokens':10,'output_tokens':5,'total_tokens':15}


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('OPENAI_API_KEY','synthetic-not-a-provider-key');monkeypatch.setenv('MEETING_MODEL','gpt-4.1')
    monkeypatch.delenv('OPENAI_BASE_URL',raising=False)


def seed(tmp_path):
    store=CalendarStore(tmp_path/'calendar.db');store.initialize(FIXTURE);return store


def call(name,args,number=1):return {'type':'function_call','id':f'fc_{number}','call_id':f'call_{number}','status':'completed','name':name,'arguments':json.dumps(args)}
def message(text='The synthetic calendar state is ready for inspection.'):
    return {'type':'message','id':'msg_final','status':'completed','role':'assistant','content':[{'type':'output_text','text':text,'annotations':[]}]}
def body(items=None,number=1):return {'id':f'resp_{number}','object':'response','created_at':1792110000,'model':'gpt-4.1-synthetic','status':'completed','output':items or [message()],'usage':deepcopy(USAGE),'incomplete_details':None,'unknown_field':{'retained':True}}


class Script(httpx2.AsyncBaseTransport):
    def __init__(self,items):self.items=iter(items);self.requests=[];self.closed=False
    async def handle_async_request(self,request):
        self.requests.append(json.loads(request.content));item=next(self.items)
        if callable(item):item=item(self.requests[-1])
        if isinstance(item,int):return httpx2.Response(item,json={'error':{'type':'synthetic_unavailable','message':'Synthetic provider unavailable'},'usage':USAGE},headers={'x-request-id':'failed-request'})
        if isinstance(item,bytes):return httpx2.Response(200,content=item,headers={'content-type':'application/json','x-request-id':'malformed-request'})
        return httpx2.Response(200,json=item,headers={'x-request-id':f'request-{len(self.requests)}'})
    async def aclose(self):self.closed=True


def booking_script(store):
    listed=body([call('list_available_slots',{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30},1)],1)
    booked=body([call('book_slot',{'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0},2)],2)
    def confirmation(request):
        event=store.find_request('book-native');assert event is not None
        outputs=[item for item in request['input'] if item.get('type')=='function_call_output']
        assert json.loads(outputs[-1]['output'])['event_id']==event['event_id']
        return body([call('send_confirmation',{'event_id':event['event_id']},3)],3)
    return Script([listed,booked,confirmation,body(number=4)])


def test_actual_native_booking_and_original_raw_sdk_evidence(tmp_path):
    store=seed(tmp_path);script=booking_script(store)
    result=run_agent(store,'Book and confirm 30 minutes for Mira and Leo.','book-native',allow_book=True,booking_scope=SCOPE,transport=script)
    evidence=json.loads(Path(result['receipt_path']).read_text());state=result['state']
    assert len(state['meetings'])==1 and len(state['outbox'])==1 and state['revision']==1 and script.closed and len(script.requests)==4
    assert result['actual_effects']['booking_created_during_run'] is True
    assert result['actual_effects']['confirmation']['event_id']==state['meetings'][0]['event_id']
    assert result['reported_usage']['fields']=={'input_tokens':40,'output_tokens':20,'total_tokens':60} and result['cost_usd'] is None
    assert evidence['status']=='completed' and evidence['tracing_disabled'] is True and evidence['actual_effects']==result['actual_effects']
    assert [event['tool'] for event in evidence['tool_events']]==['list_available_slots','book_slot','send_confirmation']
    assert len(evidence['normalized_sdk_events'])==4 and evidence['provider_observations'][0]['body']['unknown_field']=={'retained':True}
    assert evidence['provider_observations'][0]['request_id']=='request-1' and evidence['provider_observations'][0]['response_model']=='gpt-4.1-synthetic'
    assert all(request['max_output_tokens']==2048 and request['parallel_tool_calls'] is False and request['store'] is False for request in script.requests)
    assert 'synthetic-not-a-provider-key' not in json.dumps(evidence)
    assert Path(result['receipt_path']).stat().st_mode&0o777==0o600


@pytest.mark.parametrize('status',['incomplete','failed',None])
def test_noncompleted_native_tool_output_cannot_dispatch_a_confirmation(tmp_path,status):
    store=seed(tmp_path);event=store.book('book-native','2026-11-05T15:00:00Z',30,['mira','leo'],0)
    reply=body([call('send_confirmation',{'event_id':event['event_id']})]);reply['status']=status
    if status is None:reply.pop('status')
    if status=='incomplete':reply['incomplete_details']={'reason':'max_output_tokens'}
    script=Script([reply])
    with pytest.raises(Exception) as error:run_agent(store,'Confirm my existing event.','book-native',allow_book=True,booking_scope=SCOPE,transport=script)
    data=error.value.evidence
    assert data['status']=='failed' and data['result'] is None and data['tool_events']==[] and store.snapshot()['outbox']==[]
    assert data['provider_observations'][0]['body']==reply and data['reported_usage']['fields']==USAGE and script.closed


@pytest.mark.parametrize('kind',['unfinished-message','unfinished-call','refusal','unknown-tool','mixed','invalid-json','oversized'])
def test_unsupported_native_output_refuses_release_or_dispatch(tmp_path,kind):
    store=seed(tmp_path);reply=body()
    if kind=='unfinished-message':reply['output'][0]['status']='in_progress'
    if kind=='unfinished-call':reply['output']=[call('inspect_meeting',{})];reply['output'][0].pop('status')
    if kind=='refusal':reply['output'][0]['content']=[{'type':'refusal','refusal':'Synthetic refusal'}]
    if kind=='unknown-tool':reply['output']=[call('external_calendar_action',{})]
    if kind=='mixed':reply['output'].append(call('inspect_meeting',{}))
    if kind=='invalid-json':reply=b'{invalid synthetic JSON'
    if kind=='oversized':reply=(json.dumps(reply)+' '*1_048_576).encode()
    script=Script([reply])
    with pytest.raises(Exception) as error:run_agent(store,'Inspect availability.','invalid-native',transport=script)
    assert error.value.evidence['result'] is None and error.value.evidence['tool_events']==[] and script.closed and len(script.requests)==1


@pytest.mark.parametrize('kind',['missing','partial','zero','invalid','independent-total'])
def test_native_raw_usage_is_not_replaced_by_sdk_defaults_or_guessed_sums(tmp_path,kind):
    store=seed(tmp_path);reply=body()
    if kind=='missing':reply.pop('usage')
    if kind=='partial':reply['usage']={'input_tokens':7}
    if kind=='zero':reply['usage']={key:0 for key in USAGE}
    if kind=='invalid':reply['usage']={'input_tokens':True,'output_tokens':-1,'total_tokens':'15'}
    if kind=='independent-total':reply['usage']['total_tokens']=99
    script=Script([reply]);data=None
    try:
        result=run_agent(store,'Explain read-only planning.','usage-native',transport=script);data=json.loads(Path(result['receipt_path']).read_text())
    except Exception as error:data=error.evidence;assert data['result'] is None
    fields=data['reported_usage']['fields'];assert data['provider_observations'][0]['body']==reply
    if kind in {'missing','invalid'}:assert fields=={key:None for key in USAGE}
    if kind=='partial':assert fields=={'input_tokens':7,'output_tokens':None,'total_tokens':None}
    if kind=='zero':assert fields=={key:0 for key in USAGE}
    if kind=='independent-total':assert fields['total_tokens']==99
    assert data['reported_usage']['cost_usd'] is None and script.closed


def test_native_503_has_one_attempt_and_retains_failed_measurements(tmp_path):
    store=seed(tmp_path);script=Script([503])
    with pytest.raises(Exception) as error:run_agent(store,'Inspect calendar.','error-native',transport=script)
    assert len(script.requests)==1 and script.closed and error.value.evidence['provider_observations'][0]['http_status']==503
    assert error.value.evidence['reported_usage']['fields']==USAGE


def test_missing_status_regression_and_read_only_tool_gate_are_independent(tmp_path):
    store=seed(tmp_path);script=Script([body([call('book_slot',{'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0})]),body(number=2)])
    result=run_agent(store,'Propose a time only.','read-native',transport=script)
    assert result['state']['meetings']==[] and len(script.requests)==2 and result['actual_effects']['booking_request'] is None
    data=json.loads(Path(result['receipt_path']).read_text());assert 'disabled' in data['tool_events'][0]['error']['message']


def test_native_cancel_then_failed_model_retains_commit_and_exact_recovery(tmp_path):
    store=seed(tmp_path);event=store.book('original','2026-11-05T15:00:00Z',30,['mira','leo'],0);store.confirm(event['event_id'])
    def replies(revision):return [body([call('inspect_meeting',{},1)],1),body([call('cancel_meeting',{'expected_revision':revision,'reason':'Requester cancelled.'},2)],2)]
    failed=Script([*replies(1),503])
    with pytest.raises(Exception) as error:run_agent(store,'Cancel the authorized target and queue its notice.','cancel-native',allow_cancel=True,cancel_event_id=event['event_id'],transport=failed)
    data=error.value.evidence
    assert data['status']=='failed' and data['result'] is None and data['actual_effects']['cancellation_request']['event_id']==event['event_id']
    assert data['final_state']['meetings'][0]['status']=='cancelled' and data['final_state']['cancellation_outbox']==[]
    assert data['reported_usage']['fields']['total_tokens']==45
    recovered=Script([*replies(2),body([call('send_cancellation',{},3)],3),body(number=4)])
    result=run_agent(store,'Recover the same cancellation and queue its notice.','cancel-native',allow_cancel=True,cancel_event_id=event['event_id'],transport=recovered)
    assert len(result['state']['cancellations'])==1 and len(result['state']['cancellation_outbox'])==1 and len(result['state']['outbox'])==1
    assert result['state']['revision']==2 and result['actual_effects']['cancellation_notice'] is not None


def test_native_http_budget_stops_the_actual_agent_loop(tmp_path):
    store=seed(tmp_path);script=Script([body([call('inspect_meeting',{},i)],i) for i in range(1,11)])
    with pytest.raises(Exception) as error:run_agent(store,'Inspect repeatedly.','bounded-native',transport=script)
    assert len(script.requests)==8 and script.closed and len(error.value.evidence['tool_events'])==8 and error.value.evidence['result'] is None
    assert error.value.evidence['reported_usage']['fields']['total_tokens']==120


@pytest.mark.parametrize('kind',['cancel','deadline'])
def test_native_pending_http_cancellation_closes_owned_clients_and_prevents_writes(tmp_path,monkeypatch,kind):
    store=seed(tmp_path);http_clients=[];sdk_clients=[];original_http=native.create_http;original_client=native.create_client
    def http_factory(*args):client=original_http(*args);http_clients.append(client);return client
    def sdk_factory(*args):client=original_client(*args);sdk_clients.append(client);return client
    monkeypatch.setattr(native,'create_http',http_factory);monkeypatch.setattr(native,'create_client',sdk_factory)
    async def exercise():
        entered=asyncio.Event();closed=[];aborted=[]
        class Pending(httpx2.AsyncBaseTransport):
            async def handle_async_request(self,request):
                entered.set()
                try:await asyncio.Event().wait()
                finally:aborted.append(True)
            async def aclose(self):closed.append(True)
        task=asyncio.create_task(run_agent_async(store,'Inspect calendar.','cancel-native',transport=Pending(),timeout_seconds=0.3 if kind=='deadline' else 120))
        await asyncio.wait_for(entered.wait(),3)
        if kind=='cancel':task.cancel()
        with pytest.raises(TimeoutError if kind=='deadline' else asyncio.CancelledError) as error:await task
        assert closed and aborted and all(client.is_closed for client in http_clients) and all(client.is_closed() for client in sdk_clients)
        assert error.value.evidence['status']=='failed' and error.value.evidence['result'] is None and store.snapshot()['meetings']==[]
    asyncio.run(exercise())


@pytest.mark.parametrize('kind',['boolean-revision','float-duration','unknown-field'])
def test_raw_tool_argument_coercion_cannot_authorize_a_write(tmp_path,kind):
    store=seed(tmp_path);store.add_busy('sam','2026-11-05T18:00:00Z','2026-11-05T18:30:00Z')
    args={'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':1}
    if kind=='boolean-revision':args['expected_revision']=True
    if kind=='float-duration':args['duration_minutes']=30.0
    if kind=='unknown-field':args['unexpected_event']='outside-scope'
    script=Script([body([call('list_available_slots',{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30},1)],1),body([call('book_slot',args,2)],2),body(number=3)])
    result=run_agent(store,'Book only the authorized scope.','coercion-native',allow_book=True,booking_scope=SCOPE,transport=script)
    assert result['state']['meetings']==[] and len(script.requests)==3
    evidence=json.loads(Path(result['receipt_path']).read_text());assert json.loads(evidence['provider_observations'][1]['body']['output'][0]['arguments'])==args
    if kind!='unknown-field':assert 'coerced' in evidence['tool_events'][1]['error']['message']


def test_actual_native_tool_budget_bounds_multiple_calls_in_a_response(tmp_path):
    store=seed(tmp_path);script=Script([body([call('inspect_meeting',{},number*4+index) for index in range(4)],number) for number in range(1,10)])
    with pytest.raises(Exception) as error:run_agent(store,'Inspect repeatedly.','tool-budget',transport=script)
    assert len(script.requests)==7 and len(error.value.evidence['tool_events'])==24 and error.value.evidence['result'] is None
    assert store.snapshot()['meetings']==[] and script.closed


def test_native_client_startup_failure_closes_owned_http_without_dispatch(tmp_path,monkeypatch):
    store=seed(tmp_path);script=Script([]);clients=[];original=native.create_http
    def http_factory(*args):client=original(*args);clients.append(client);return client
    def broken(*args):raise RuntimeError('synthetic SDK startup failure')
    monkeypatch.setattr(native,'create_http',http_factory);monkeypatch.setattr(native,'create_client',broken)
    with pytest.raises(RuntimeError,match='startup failure') as error:run_agent(store,'Inspect calendar.','startup',transport=script)
    assert script.closed and all(client.is_closed for client in clients) and script.requests==[] and error.value.evidence['result'] is None


def test_replayed_native_call_id_is_rejected_before_a_second_tool_dispatch(tmp_path):
    store=seed(tmp_path);script=Script([body([call('inspect_meeting',{},1)],1),body([call('inspect_meeting',{},1)],2)])
    with pytest.raises(Exception) as error:run_agent(store,'Inspect calendar.','replay-native',transport=script)
    assert len(script.requests)==2 and len(error.value.evidence['tool_events'])==1 and error.value.evidence['result'] is None
    assert 'replayed' in error.value.evidence['provider_observations'][1]['error']['message'] and script.closed
