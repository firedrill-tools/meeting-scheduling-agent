"""OpenAI Agents SDK boundary over the synthetic calendar."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from agents import Agent, RunContextWrapper, Runner, function_tool

from .calendar import CalendarStore


@dataclass
class SchedulingContext:
    store: CalendarStore
    request_id: str
    allow_book: bool


def calendar_tool_error(_ctx, error: Exception) -> str:
    return str(error) if isinstance(error, ValueError) else "Calendar tool failed."


@function_tool(failure_error_function=calendar_tool_error)
def list_available_slots(ctx: RunContextWrapper[SchedulingContext], day: str,
                         participant_ids: list[str], duration_minutes: int = 30) -> str:
    """List available UTC start times for people on a UTC calendar day; return a revision for booking."""
    return json.dumps(ctx.context.store.list_slots(day, participant_ids, duration_minutes))


@function_tool(failure_error_function=calendar_tool_error)
def book_slot(ctx: RunContextWrapper[SchedulingContext], start_utc: str,
              duration_minutes: int, participant_ids: list[str], expected_revision: int) -> str:
    """Book one listed slot using its calendar revision; fails if availability has changed."""
    if not ctx.context.allow_book:
        raise ValueError("booking is disabled for this run")
    return json.dumps(ctx.context.store.book(
        ctx.context.request_id, start_utc, duration_minutes, participant_ids, expected_revision
    ))


@function_tool(failure_error_function=calendar_tool_error)
def send_confirmation(ctx: RunContextWrapper[SchedulingContext], event_id: str) -> str:
    """Write one synthetic confirmation to the outbox after an event has been booked."""
    if not ctx.context.allow_book:
        raise ValueError("sending is disabled for this run")
    return json.dumps(ctx.context.store.confirm(event_id))


def run_agent(store: CalendarStore, request: str, request_id: str,
              model: str | None = None, allow_book: bool = False) -> dict:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for a model run")
    model_name = model or os.environ.get("MEETING_MODEL")
    if not model_name:
        raise RuntimeError("MEETING_MODEL must name an enabled model")
    if not request_id.strip() or not request.strip():
        raise ValueError("request and request_id are required")
    agent = Agent[SchedulingContext](
        name="Meeting Scheduler",
        model=model_name,
        instructions=(
            "Schedule only with the synthetic calendar tools. Work in UTC for tool calls, "
            "and explain local time zones clearly to the requester. List slots before booking, "
            "use the returned revision, and re-list if it changes. Never claim a meeting is booked "
            "until book_slot returns an event. Send confirmation only for that event. "
            "If no suitable time exists, explain the conflict; do not invent availability."
        ),
        tools=[list_available_slots, book_slot, send_confirmation],
    )
    result = Runner.run_sync(
        agent, request, context=SchedulingContext(store, request_id, allow_book), max_turns=12
    )
    usage = result.context_wrapper.usage
    return {
        "response": result.final_output,
        "usage": {"requests": usage.requests, "input_tokens": usage.input_tokens,
                  "output_tokens": usage.output_tokens},
        "state": store.snapshot(),
    }
