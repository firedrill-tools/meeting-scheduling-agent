import json
from pathlib import Path

import pytest
from meeting_scheduler.agent import run_agent
from meeting_scheduler.evidence import publish
from test_native import SCOPE,booking_script,seed


@pytest.fixture(autouse=True)
def credentials(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('OPENAI_API_KEY','synthetic-not-a-provider-key');monkeypatch.setenv('MEETING_MODEL','gpt-4.1')


@pytest.mark.parametrize('phase',['request','response','before-book','before-confirm','final'])
def test_recording_fault_blocks_later_mutation_and_preserves_actual_commit(tmp_path,phase):
    store=seed(tmp_path);script=booking_script(store)
    def writer(path,data):
        observations=data['provider_observations'];events=data['tool_events']
        reject=(phase=='request' and observations and observations[-1]['status']=='prepared'
            or phase=='response' and observations and observations[-1]['body'] is not None
            or phase=='before-book' and any(event['tool']=='book_slot' and event['status']=='started' for event in events)
            or phase=='before-confirm' and any(event['tool']=='send_confirmation' and event['status']=='started' for event in events)
            or phase=='final' and data['status']=='completed')
        if reject:raise OSError('synthetic evidence publication failure')
        publish(path,data)
    with pytest.raises(Exception) as error:run_agent(store,'Book and queue a synthetic confirmation.','book-native',allow_book=True,booking_scope=SCOPE,transport=script,publish=writer)
    data=error.value.evidence;state=store.snapshot()
    assert data['status']=='failed' and data['result'] is None and script.closed
    assert len(script.requests)=={'request':0,'response':1,'before-book':2,'before-confirm':3,'final':4}[phase]
    assert len(state['meetings'])==(1 if phase in {'before-confirm','final'} else 0)
    assert len(state['outbox'])==(1 if phase=='final' else 0)
    assert data['actual_effects']['booking_request'] is not None if phase in {'before-confirm','final'} else data['actual_effects']['booking_request'] is None
    if phase=='before-confirm':
        original=state['meetings'][0]['event_id'];recovery=booking_script(store)
        result=run_agent(store,'Recover the same booking and its missing confirmation.','book-native',allow_book=True,booking_scope=SCOPE,transport=recovery)
        assert result['actual_effects']['booking_created_during_run'] is False and result['state']['meetings'][0]['event_id']==original
        assert len(result['state']['meetings'])==1 and len(result['state']['outbox'])==1
