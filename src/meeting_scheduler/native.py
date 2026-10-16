"""Original Responses JSON evidence and owned public OpenAI/HTTP clients."""
from dataclasses import asdict,is_dataclass
from pydantic import BaseModel
from datetime import datetime,timezone
import json
from urllib.parse import urlsplit

from agents import RunHooks
import httpx2
from openai import AsyncOpenAI
from .schemas import sha

def invalid_constant(value):raise ValueError('nonfinite JSON constant')

MAX_BODY=1024*1024
FIELDS=('input_tokens','output_tokens','total_tokens')


def sdk_value(value):
    if isinstance(value,BaseModel):return value.model_dump(mode='json')
    if is_dataclass(value):return sdk_value(asdict(value))
    if isinstance(value,dict):return {key:sdk_value(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [sdk_value(item) for item in value]
    return value


def now():return datetime.now(timezone.utc).isoformat()


def reported_usage(body):
    raw=body.get('usage') if isinstance(body,dict) else None
    fields={name:raw.get(name) if isinstance(raw,dict) and type(raw.get(name)) is int and 0<=raw[name]<=2**53-1 else None for name in FIELDS}
    return {'fields':fields,'complete':all(value is not None for value in fields.values()),'cost_usd':None}


def aggregate_usage(observations):
    values=[item.get('reported_usage') for item in observations]
    fields={name:sum(value['fields'][name] for value in values) if values and all(value and value['fields'][name] is not None for value in values) else None for name in FIELDS}
    fields={key:value if value is None or value<=2**53-1 else None for key,value in fields.items()}
    return {'prepared_attempts':len(observations),'complete':bool(values) and all(value is not None for value in fields.values()),'fields':fields,'cost_usd':None}


def validate_config(model,base_url):
    if not isinstance(model,str) or not model.strip() or len(model)>200 or any(c.isspace() for c in model):raise ValueError('MEETING_MODEL must name an enabled model without whitespace')
    url=urlsplit(base_url)
    if not url.hostname or url.username or url.password or url.query or url.fragment or not (url.scheme=='https' or url.scheme=='http' and url.hostname in {'127.0.0.1','localhost','::1'}):raise ValueError('base_url requires HTTPS or loopback HTTP without credentials/query/fragment')


class NativeEvidence:
    def __init__(self,receipt,context,names):self.receipt=receipt;self.context=context;self.names=set(names);self.observations=[];self.call_ids=set();self.sdk_events=[]

    def guard(self):self.receipt.guard();self.context.guard()

    async def request(self,request):
        self.guard()
        if request.method!='POST' or not request.url.path.endswith('/responses'):raise ValueError('only JSON Responses POST is qualified')
        if len(self.observations)>=8:raise ValueError('model generation limit (8) reached')
        raw=await request.aread()
        if len(raw)>MAX_BODY:raise ValueError('provider request exceeds 1 MiB')
        body=json.loads(raw)
        if body.get('stream') is True:raise ValueError('wire streaming is not qualified')
        self.observations.append({'attempt':len(self.observations)+1,'status':'prepared','started_at':now(),'finished_at':None,'path':request.url.path,'request_sha256':sha(raw),'request_body':body,'http_status':None,'request_id':None,'response_id':None,'response_model':None,'completion':None,'body':None,'reported_usage':None,'error':None})
        await self.receipt.save();self.guard()

    def validate_completion(self,body):
        if not isinstance(body,dict) or body.get('status')!='completed' or body.get('error') is not None or body.get('incomplete_details') is not None:raise ValueError('OpenAI response is not fully completed')
        output=body.get('output')
        if not isinstance(output,list) or not output:raise ValueError('OpenAI response has no output')
        functions=[];messages=[]
        for item in output:
            if not isinstance(item,dict):raise ValueError('malformed OpenAI output')
            kind=item.get('type')
            if kind=='reasoning':
                if item.get('status') not in {None,'completed'}:raise ValueError('unfinished reasoning output')
            elif kind=='function_call':
                call_id=item.get('call_id')
                if item.get('status')!='completed' or item.get('name') not in self.names or not isinstance(call_id,str) or not call_id or call_id in self.call_ids or call_id in functions:raise ValueError('unfinished, unknown or replayed tool call')
                args=json.loads(item.get('arguments',''))
                if not isinstance(args,dict):raise ValueError('tool call arguments must be an object')
                functions.append(call_id)
            elif kind=='message':
                content=item.get('content')
                if item.get('role')!='assistant' or item.get('status')!='completed' or not isinstance(content,list) or not content or any(not isinstance(part,dict) or part.get('type')!='output_text' or not isinstance(part.get('text'),str) for part in content) or not any(part['text'].strip() for part in content):raise ValueError('unfinished, refused or malformed final message')
                messages.append(item)
            else:raise ValueError('unsupported provider output type')
        if functions and messages or len(messages)>1 or not functions and not messages or len(functions)>4:raise ValueError('ambiguous tool/final output')
        self.call_ids.update(functions)

    async def response(self,response):
        item=self.observations[-1];item.update(http_status=response.status_code,request_id=response.headers.get('x-request-id'))
        try:
            raw=await response.aread();truncated=len(raw)>MAX_BODY;captured=raw[:MAX_BODY]
            try:body=json.loads(captured,parse_constant=invalid_constant)
            except ValueError:body=captured.decode('utf8',errors='replace')
            item.update(status='received',body=body,truncated=truncated,captured_sha256=sha(captured),body_sha256=None if truncated else sha(raw),reported_usage=reported_usage(body))
            if isinstance(body,dict):item.update(response_id=body.get('id'),response_model=body.get('model'),completion=body.get('status'))
            await self.receipt.save();self.guard()
            if truncated:raise ValueError('provider response exceeds 1 MiB evidence limit')
            if response.is_success:self.validate_completion(body);item['status']='accepted'
            else:item['status']='http_error'
            await self.receipt.save()
        except BaseException as error:
            item.update(status='failed',error={'name':type(error).__name__,'message':str(error)})
            await self.receipt.save();raise
        finally:item['finished_at']=now();await response.aclose()

    def fail(self,error):
        if self.observations and self.observations[-1]['status'] in {'prepared','received'}:self.observations[-1].update(status='failed',finished_at=now(),error={'name':type(error).__name__,'message':str(error)})
        if self.sdk_events and self.sdk_events[-1]['status']=='started':self.sdk_events[-1].update(status='failed',finished_at=now(),error={'name':type(error).__name__,'message':str(error)})


class NativeHooks(RunHooks):
    def __init__(self,native):self.native=native
    async def on_llm_start(self,context,agent,system_prompt,input_items):
        self.native.guard();self.native.sdk_events.append({'number':len(self.native.sdk_events)+1,'status':'started','started_at':now(),'finished_at':None,'usage':None,'response_id':None,'output':None,'error':None});await self.native.receipt.save()
    async def on_llm_end(self,context,agent,response):
        self.native.guard();self.native.sdk_events[-1].update(status='returned',finished_at=now(),usage=sdk_value(response.usage),response_id=response.response_id,output=[item.model_dump(mode='json') for item in response.output]);await self.native.receipt.save()


def create_http(native,transport=None):
    return httpx2.AsyncClient(transport=transport,event_hooks={'request':[native.request],'response':[native.response]},timeout=httpx2.Timeout(30,connect=5),follow_redirects=False)


def create_client(key,base_url,http):return AsyncOpenAI(api_key=key,base_url=base_url,max_retries=0,http_client=http)
