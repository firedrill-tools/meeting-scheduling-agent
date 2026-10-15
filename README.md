# Meeting scheduling agent

A standalone OpenAI Agents SDK application that finds and books times in a synthetic calendar. Participants have named IANA time zones and local working hours. The agent sees three local function tools: list available slots, book a listed slot at the returned revision, and write a synthetic confirmation to an outbox after booking.

## Run

Requires Python 3.12+, `uv`, an OpenAI key and an enabled model name for a live agent run.

```sh
uv sync --extra test
uv run pytest -q
uv run schedule-meeting --db .runs/calendar.db init
uv run schedule-meeting --db .runs/calendar.db show
OPENAI_API_KEY=... MEETING_MODEL=... uv run schedule-meeting --db .runs/calendar.db run \
  --request-id request-1 --allow-book \
  'Find and book 30 minutes for Mira and Leo on 2026-11-05, then confirm it.'
```

Omit `--allow-book` for read-only planning. Use `--model` instead of `MEETING_MODEL` if desired. `block PERSON_ID START_UTC END_UTC` simulates changed availability between listing and booking. Use a new database path to reset. The database is local and contains no real calendar or email connection.

The booking transaction checks the calendar revision and every participant's local working hours and busy blocks, then creates one event. A stable request ID makes an exact retry return the same event. The confirmation outbox is keyed by event ID. The SDK tools expose controlled error messages so the agent can re-list after a stale revision. A model's final sentence alone is not booking evidence; inspect `show` for the event and outbox entry.

## Evidence and limits

[Scenarios](scenarios/README.md) cover time zones, conflicts, stale availability, duplicate requests, read-only operation and unknown people. Six offline tests pass, including invocation of the decorated Agents SDK tools. They do not make an OpenAI model call; live tool-choice quality and usage are pending a key. This app uses one local SQLite writer and synthetic notifications only.

Firedrill integration will be added against the qualified new release. The retired interface is not used. This repository is private and has no selected public license.

Implementation reference: [OpenAI Agents SDK tools](https://openai.github.io/openai-agents-python/tools/).

Explore related applications in the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog). Product and setup references: [Firedrill](https://firedrill.run/) and the [official developer documentation](https://docs.firedrill.run/). Use documentation matching the qualified release; this repository records its current integration limits above.
