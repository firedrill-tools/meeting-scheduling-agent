"""Real child CLI and actual Agents SDK/OpenAI HTTP over loopback."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
from threading import Event,Thread

import pytest
from meeting_scheduler.calendar import CalendarStore
from meeting_scheduler.datasets import initialize
from test_native import USAGE,body,call


def environment(keys=True):
    env={**os.environ,'MEETING_MODEL':'gpt-4.1','NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost'}
    env.pop('OPENAI_API_KEY',None);env.pop('OPENAI_BASE_URL',None)
    if keys:env['OPENAI_API_KEY']='synthetic-cli-key'
    return env


def command(database,*args):return [sys.executable,'-m','meeting_scheduler.cli','--db',str(database),*args]


@contextmanager
def server(items):
    items=iter(items);requests=Queue();release=Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['content-length'])));requests.put(request);value=next(items)
            if value=='pending':release.wait(25);value=body()
            if callable(value):value=value(request)
            status=503 if value==503 else 200
            if status==503:value={'error':{'message':'Synthetic unavailable','type':'synthetic_error'},'usage':USAGE}
            raw=json.dumps(value).encode()
            try:self.send_response(status);self.send_header('content-type','application/json');self.send_header('content-length',str(len(raw)));self.send_header('x-request-id','cli-synthetic');self.end_headers();self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.daemon_threads=True;thread=Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}/v1',requests
    finally:release.set();http.shutdown();http.server_close();thread.join(3)


def test_real_cli_native_booking_uses_explicit_case_write_permission(tmp_path):
    database=tmp_path/'calendar.db';initialize('starter',database);store=CalendarStore(database)
    def confirmation(request):
        event=store.find_request('starter-book');assert event is not None
        return body([call('send_confirmation',{'event_id':event['event_id']},3)],3)
    items=[body([call('list_available_slots',{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30},1)],1),body([call('book_slot',{'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0},2)],2),confirmation,body(number=4)]
    with server(items) as (endpoint,requests):
        child=subprocess.run(command(database,'run','--case','starter','--allow-book','--base-url',endpoint),cwd=tmp_path,env=environment(),capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    result=json.loads(child.stdout);assert len(result['state']['meetings'])==1 and len(result['state']['outbox'])==1 and requests.qsize()==4
    evidence=json.loads(Path(result['receipt_path']).read_text());assert evidence['permissions']['allow_book'] is True and evidence['provider_observations'][0]['request_id']=='cli-synthetic'
    assert 'synthetic-cli-key' not in json.dumps(evidence)


def test_real_cli_cancel_retains_prior_human_confirmation(tmp_path):
    database=tmp_path/'calendar.db';initialize('cancel-scheduled',database)
    items=[body([call('inspect_meeting',{},1)],1),body([call('cancel_meeting',{'expected_revision':0,'reason':'Requester cancelled.'},2)],2),body([call('send_cancellation',{},3)],3),body(number=4)]
    with server(items) as (endpoint,requests):
        child=subprocess.run(command(database,'run','--case','cancel-scheduled','--allow-cancel','--base-url',endpoint),cwd=tmp_path,env=environment(),capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    result=json.loads(child.stdout);assert result['state']['meetings'][0]['status']=='cancelled' and len(result['state']['outbox'])==1 and len(result['state']['cancellation_outbox'])==1 and requests.qsize()==4


@pytest.mark.parametrize('kind',['missing-status','503'])
def test_real_cli_native_failure_preserves_raw_evidence_and_state(tmp_path,kind):
    database=tmp_path/'calendar.db';initialize('starter',database);reply=body()
    if kind=='missing-status':reply.pop('status')
    with server([503 if kind=='503' else reply]) as (endpoint,requests):
        child=subprocess.run(command(database,'run','--case','starter','--base-url',endpoint),cwd=tmp_path,env=environment(),capture_output=True,text=True,timeout=25)
    assert child.returncode==1 and child.stdout==''
    data=json.loads(Path(json.loads(child.stderr)['receipt_path']).read_text());assert data['status']=='failed' and data['result'] is None and data['final_state']['meetings']==[] and requests.qsize()==1
    assert data['reported_usage']['fields']['total_tokens']==15


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
def test_real_cli_signal_cancels_native_http_without_a_late_calendar_write(tmp_path,sig):
    database=tmp_path/'calendar.db';initialize('starter',database)
    with server(['pending']) as (endpoint,requests):
        child=subprocess.Popen(command(database,'run','--case','starter','--base-url',endpoint),cwd=tmp_path,env=environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:requests.get(timeout=20);child.send_signal(sig);out,err=child.communicate(timeout=10)
        finally:
            if child.poll() is None:child.kill();child.communicate()
    assert child.returncode==130 and out==''
    data=json.loads(Path(json.loads(err)['receipt_path']).read_text());assert data['status']=='failed' and data['provider_observations'][0]['status']=='failed' and data['final_state']['meetings']==[]
    assert CalendarStore(database).snapshot()['meetings']==[]


def test_real_cli_model_free_case_listing_initialization_and_key_preflight(tmp_path):
    database=tmp_path/'busy.db'
    def run(*args):return subprocess.run(command(database,*args),cwd=tmp_path,env=environment(False),capture_output=True,text=True,timeout=20)
    listing=run('list-cases');assert listing.returncode==0 and len(json.loads(listing.stdout)['cases'])==10
    created=run('init','--case','busy');assert created.returncode==0 and database.stat().st_mode&0o777==0o600
    assert run('init','--case','busy').returncode==1
    state=run('show');assert len(json.loads(state.stdout)['meetings'])==18
    failed=run('run','--case','busy');assert failed.returncode==1
    receipt=json.loads(Path(json.loads(failed.stderr)['receipt_path']).read_text());assert receipt['status']=='failed' and receipt['provider_observations']==[] and len(receipt['final_state']['meetings'])==18
