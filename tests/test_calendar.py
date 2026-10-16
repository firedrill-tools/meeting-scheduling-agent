import asyncio
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

from meeting_scheduler.agent import SchedulingContext, book_slot, list_available_slots, send_confirmation
from meeting_scheduler.calendar import CalendarStore, parse_utc
from meeting_scheduler.schemas import BookingScope

SCOPE=BookingScope(day="2026-11-05",participant_ids=("mira","leo"),duration_minutes=30)


FIXTURE = Path(__file__).parents[1] / "fixtures/starter.json"


def seeded(tmp_path):
    store = CalendarStore(tmp_path / "calendar.db")
    store.initialize(FIXTURE)
    return store


def test_timezone_and_existing_conflicts_filter_slots(tmp_path):
    store = seeded(tmp_path)
    result = store.list_slots("2026-11-05", ["mira", "leo"], 30)
    assert "2026-11-05T15:00:00Z" in result["slots_utc"]
    assert "2026-11-05T14:30:00Z" not in result["slots_utc"]
    assert "2026-11-05T16:00:00Z" not in result["slots_utc"]
    assert result["revision"] == 0


def test_booking_and_confirmation_are_exact_and_idempotent(tmp_path):
    store = seeded(tmp_path)
    request = "req-1"
    start = "2026-11-05T15:00:00Z"
    event = store.book(request, start, 30, ["mira", "leo"], expected_revision=0)
    assert store.snapshot()["revision"] == 1
    repeated = store.book(request, start, 30, ["leo", "mira"], expected_revision=0)
    assert repeated["event_id"] == event["event_id"]
    confirmation = store.confirm(event["event_id"])
    assert json.loads(confirmation["recipients_json"]) == ["leo@northstar.example", "mira@harbor.example"]
    assert store.confirm(event["event_id"])["message"] == confirmation["message"]
    assert len(store.snapshot()["outbox"]) == 1
    with pytest.raises(ValueError, match="different meeting"):
        store.book(request, "2026-11-05T15:30:00Z", 30, ["mira", "leo"], 1)
    with pytest.raises(ValueError, match="slot unavailable"):
        store.book("req-2", start, 30, ["mira", "leo"], 1)


def test_changed_availability_requires_a_fresh_revision(tmp_path):
    store = seeded(tmp_path)
    listed = store.list_slots("2026-11-05", ["mira", "leo"], 30)
    store.add_busy("mira", "2026-11-05T15:00:00Z", "2026-11-05T15:30:00Z")
    with pytest.raises(ValueError, match="revision changed"):
        store.book("req-late", "2026-11-05T15:00:00Z", 30, ["mira", "leo"], listed["revision"])
    assert "2026-11-05T15:00:00Z" not in store.list_slots("2026-11-05", ["mira", "leo"], 30)["slots_utc"]


def test_invalid_time_or_participant_and_unscheduled_confirmation_rejected(tmp_path):
    store = seeded(tmp_path)
    with pytest.raises(ValueError, match="UTC"):
        parse_utc("2026-11-05T15:00:00")
    with pytest.raises(ValueError, match="unknown participant"):
        store.list_slots("2026-11-05", ["nobody"])
    with pytest.raises(ValueError, match="unknown meeting"):
        store.confirm("missing")


def test_actual_agents_sdk_tools_use_run_context_and_write_gate(tmp_path):
    store = seeded(tmp_path)
    def context(name, arguments):
        return ToolContext(SchedulingContext(store, "req-tools", False),
                           tool_name=name, tool_call_id="test-call", tool_arguments=arguments)

    lookup_args = json.dumps({
        "day": "2026-11-05", "participant_ids": ["mira", "leo"], "duration_minutes": 30
    })
    slots = json.loads(asyncio.run(list_available_slots.on_invoke_tool(
        context("list_available_slots", lookup_args), lookup_args)))
    assert slots["revision"] == 0
    book_args = json.dumps({
            "start_utc": "2026-11-05T15:00:00Z", "duration_minutes": 30,
            "participant_ids": ["mira", "leo"], "expected_revision": 0,
        })
    assert "disabled" in asyncio.run(book_slot.on_invoke_tool(
        context("book_slot", book_args), book_args))
    confirm_args = json.dumps({"event_id": "anything"})
    assert "disabled" in asyncio.run(send_confirmation.on_invoke_tool(
        context("send_confirmation", confirm_args), confirm_args))


def test_actual_agents_sdk_tools_book_and_confirm_when_enabled(tmp_path):
    store = seeded(tmp_path)
    shared=SchedulingContext(store, "req-enabled", True,booking_scope=SCOPE)
    def invoke(tool, args):
        encoded = json.dumps(args)
        context = ToolContext(shared,
                              tool_name=tool.name, tool_call_id="enabled-call", tool_arguments=encoded)
        return asyncio.run(tool.on_invoke_tool(context, encoded))

    invoke(list_available_slots,{"day":"2026-11-05","participant_ids":["mira","leo"],"duration_minutes":30})
    event = json.loads(invoke(book_slot, {
        "start_utc": "2026-11-05T15:00:00Z", "duration_minutes": 30,
        "participant_ids": ["mira", "leo"], "expected_revision": 0,
    }))
    confirmation = json.loads(invoke(send_confirmation, {"event_id": event["event_id"]}))
    assert confirmation["event_id"] == event["event_id"]
    assert len(store.snapshot()["outbox"]) == 1


def invoke_as(tool,context,args):
    encoded=json.dumps(args)
    wrapper=ToolContext(context,tool_name=tool.name,tool_call_id='scope-regression',tool_arguments=encoded)
    return asyncio.run(tool.on_invoke_tool(wrapper,encoded))


def test_confirmation_tool_cannot_use_another_requests_event(tmp_path):
    store=seeded(tmp_path)
    event=store.book('other-request','2026-11-05T15:00:00Z',30,['mira','leo'],0)
    result=invoke_as(send_confirmation,SchedulingContext(store,'new-request',True,booking_scope=SCOPE),{'event_id':event['event_id']})
    assert 'another request' in result and store.snapshot()['outbox']==[]
    own=invoke_as(send_confirmation,SchedulingContext(store,'other-request',True,booking_scope=SCOPE),{'event_id':event['event_id']})
    assert json.loads(own)['event_id']==event['event_id'] and len(store.snapshot()['outbox'])==1


def test_new_booking_requires_the_actual_run_to_have_listed_its_exact_offer(tmp_path):
    store=seeded(tmp_path);context=SchedulingContext(store,'scoped-request',True,booking_scope=SCOPE)
    args={'start_utc':'2026-11-05T15:00:00Z','duration_minutes':30,'participant_ids':['mira','leo'],'expected_revision':0}
    assert 'not listed' in invoke_as(book_slot,context,args) and store.snapshot()['meetings']==[]
    invoke_as(list_available_slots,context,{'day':'2026-11-05','participant_ids':['mira','leo'],'duration_minutes':30})
    wrong={**args,'participant_ids':['mira','sam']}
    assert 'outside the caller' in invoke_as(book_slot,context,wrong) and store.snapshot()['meetings']==[]
    event=json.loads(invoke_as(book_slot,context,args));assert len(store.snapshot()['meetings'])==1
    # A restart can read the exact already-committed request without rebooking.
    retried=json.loads(invoke_as(book_slot,SchedulingContext(store,'scoped-request',True,booking_scope=SCOPE),args))
    assert retried['event_id']==event['event_id'] and len(store.snapshot()['meetings'])==1


@pytest.mark.parametrize('start',['2026-11-05T15:07:01Z','2026-11-05T15:00:00.000001Z'])
def test_unlisted_fractional_or_off_grid_start_is_rejected(tmp_path,start):
    store=seeded(tmp_path)
    with pytest.raises(ValueError,match='half-hour'):store.book('unlisted',start,30,['mira','leo'],0)
    assert store.snapshot()['meetings']==[]


def test_workday_end_includes_seconds_and_microseconds(tmp_path):
    from datetime import timedelta
    store=seeded(tmp_path)
    with store._db() as db:
        start=parse_utc('2026-11-05T21:30:00Z')
        assert store._available(db,['sam'],start,start+timedelta(minutes=30))
        assert not store._available(db,['sam'],start,start+timedelta(minutes=30,microseconds=1))
