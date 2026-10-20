# Meeting scheduling agent with OpenAI Agents SDK

A standalone OpenAI Agents SDK application for a synthetic calendar. It resolves people, finds timezone-aware slots, books an observed offer, queues a confirmation, and cancels an explicitly authorized event while retaining history. All calendar/outbox data is local. An outbox row never means real email was sent.

This example repository is publicly viewable. A project license has not been selected.

## Simulate with Firedrill

[Firedrill](https://firedrill.run/) is a simulation platform for AI agents.
Start from this repository’s root:

```sh
npx firedrill-run init
```

Sign in and choose your project, then follow the [simulation run guide](firedrill/README.md)
for application dependencies, a preview and the reviewed scenarios. Your agent runs
locally or in your CI; model credentials stay separate from Firedrill sign-in.
The included recipes record outcomes without an automatic quality score or CI gate.

## Run

Python 3.12+ and uv:

```sh
uv sync --locked --extra test
uv run --locked schedule-meeting list-cases
uv run --locked schedule-meeting --db .runs/calendar.db init --case starter
uv run --locked schedule-meeting --db .runs/calendar.db show
```

These commands need no model key. Export OPENAI_API_KEY and MEETING_MODEL (an enabled account model), then:

```sh
# Read-only planning is the default, even when a case asks to book.
uv run --locked schedule-meeting --db .runs/calendar.db run --case starter
# Explicitly authorize the case's fixed day/participants/duration.
uv run --locked schedule-meeting --db .runs/calendar.db run --case starter --allow-book
# A custom request needs a stable caller request ID and explicit booking scope.
uv run --locked schedule-meeting --db .runs/calendar.db run \
  'Book 30 minutes for Mira and Leo, then queue confirmation.' \
  --request-id custom-1 --allow-book --day 2026-11-05 \
  --participant mira --participant leo --duration 30
```

Use `--model` to override MEETING_MODEL. There is no model default. `--base-url` selects an explicit HTTPS or loopback HTTP Responses endpoint, normally ending in /v1; the app does not borrow OPENAI_BASE_URL or a default SDK client. `--timeout` supports a positive deadline up to 120 seconds, and `--output-root` chooses private run evidence. `.env.example` describes exported variables; .env is not automatically loaded.

## Cancellation and recovery

```sh
uv run --locked schedule-meeting --db .runs/cancel.db init --case cancel-scheduled
uv run --locked schedule-meeting --db .runs/cancel.db run --case cancel-scheduled --allow-cancel
```

Custom cancellation uses `--allow-cancel --cancel-event-id EXACT_EVENT_ID` plus a stable new request ID. Booking and cancellation permissions are separate; one run cannot enable both. Without a write flag, the selected case only supplies task facts and a read scope. The model cannot select a different cancellation target. It inspects current revision/original request intent, cancels transactionally, and queues a separate synthetic notice. Original meetings and confirmations remain; only that event's busy rows are released. Cancelled events cannot be confirmed or resurrected by an original booking retry.

An exact request retry returns the same committed event/cancellation. A reused request ID with another payload/operation fails. A later provider or evidence failure does not roll back an earlier tool commit. Inspect the receipt's actual_effects and `show`, then retry the original ID/scope/intent when appropriate. The app never blindly repeats a mutation after failure. A cancellation already performed by another request is reported as existing state; its original notification ownership is retained.

## Tools and actual state

The real Agents SDK Runner exposes seven decorated tools: participant lookup, request/target inspection, slot listing, booking, confirmation, cancellation and cancellation notice. Participant lookup returns multiple identities when ambiguous. A new booking must match an offer actually observed in that run and the caller's day/participants/duration grant. A changed revision requires a fresh read. Starts use the listed half-hour UTC grid, durations are integer 15-minute steps from 15 to 120, and at most twelve unique participants may attend.

Slots are scoped by UTC start day. Every participant must be within a named IANA timezone's local weekday/work window; a UTC end can be on the next date. An end exactly at work_end=24 is the closing midnight, with no overnight shift. Working-hour boundaries retain seconds/microseconds. Host timezone data governs offsets; fixtures/checks include the UK/US autumn transition and next-local-day cases.

Results contain draft model `response`, actual SQLite `state`, and request/target-specific `actual_effects`. Prose is not booking or delivery evidence. The application does not infer semantic success from a plausible answer; identity ambiguity, request interpretation, timezone explanation and model choices still need live/human review. `show` includes busy blocks, retained meetings, request identities, both outboxes and cancellation history.

## Runtime and evidence

Actual OpenAIResponsesModel, AsyncOpenAI, native function tools and Runner are retained. Explicit owned HTTP/SDK clients close on success, startup/error, cancellation and deadline. Per-run tracing is disabled. SDK and client retries are zero; up to eight prepared Responses requests, twelve agent turns and 24 tool invocations are allowed. Output is capped at 2048 tokens per model call, HTTP phases at 30 seconds/five-second connect, and synthesis at 120 seconds maximum.

Only completed Responses with finished known tool calls or a finished assistant text message can dispatch/release output. Missing status, refusals, mixed final/tool output, unknown/replayed calls and unsupported content fail before tool dispatch. Raw tool arguments are checked against SDK-normalized values, so booleans/floats or unknown fields cannot silently authorize writes. Incoming/outgoing JSON evidence is capped at 1 MiB; response evidence is checked after HTTP buffering, not at a wire/memory limit. JSON Responses is qualified; streaming/provider-hosted tools are not.

The original synchronous SDK tools spawned worker threads that could commit after their awaited task was cancelled. Tools now execute as owned async functions without detached database threads. Each SQLite write checks the run's active/deadline fence before commit; expiry rolls the transaction back. SQLite lock waits are bounded to 100 ms. This is cooperative local database ownership, not remote calendar cancellation.

Each run writes `.runs/<uuid>/receipt.json` (new directories 0700, file 0600), including actual versions, request/instruction/initial/final-state hashes, explicit permissions, raw decoded HTTP request/response/IDs/completion, normalized SDK events separately, tool arguments/results/errors and committed effects. Receipts contain private business text; headers and keys are not captured. Missing/partial/invalid/raw-zero usage stays distinct, including failed measurements; totals and costs are never guessed. A prepared attempt is not proof of paid provider execution. Synthetic test counts are not live usage or billing.

Recording failure blocks later calls and writes. If final persistence fails, the last file may still show running and the exception carries failed in-memory evidence_error plus actual final state. A failed run exposes no result; committed SQLite state remains authoritative. Native run errors print JSON/receipt paths to stderr; SIGINT/SIGTERM abort work and exit 130. Library calls do not alter host logging or global SDK clients; the CLI owns its JSON logging surface. Use `run_agent_async` inside an event loop; `run_agent` is the synchronous entry point.

## Data, isolation and checks

Ten `synthetic-scheduling-cases` v1 cases span starter, busy, ambiguous and boundary profiles. Busy has twelve people, eighteen human-authored prior meetings, 24 external blocks, twelve confirmations, three cancellations and two cancellation notices. Import history is labeled human_import and contains no model trials/usage. Initial fixture hashes and declarative identity are retained after working state changes. Manifest focus stays outside model context; write permission always comes from the caller.

Initialization reserves a fresh private DB exclusively and never overwrites an existing DB/WAL owner. Different DBs isolate journeys; SQLite transactions/revisions/request identities protect concurrent writers to one DB. New DB and journal files are private. Legacy schema upgrades preserve original records without inventing provider/dataset history. Reset by choosing a new DB path; stop all owners before discarding a DB and its WAL/SHM files, preserving receipts. Reset does not erase provider retention or billing.

```sh
uv run --locked python scripts/generate_data.py --check
uv run --locked --extra test pytest -q
uv build
uv run --locked python scripts/check_distribution.py
```

82 offline checks cover actual SDK HTTP/CLI booking/cancellation, original failure measurements, concurrent writes, deadline rollback, retries, raw type coercion, persistence faults, private data and signals. A synthetic malformed usage case exposes an SDK serializer warning; original values remain in evidence. Fresh locked wheel/sdist checks run outside the checkout with all ten calendars and real booking/cancellation/recovery operations. These are protocol/state proofs, not live tool-choice or scheduling-quality scores. See [scenarios](scenarios/README.md), [compatibility](COMPATIBILITY.md) and [plan](PLAN.md).

Live provider judgment/budget, authenticated persisted Firedrill scenarios, and license choice remain parked. This repository is publicly viewable; no project license has been selected.

References: [OpenAI Agents SDK tools](https://openai.github.io/openai-agents-python/tools/), [models and providers](https://developers.openai.com/api/docs/guides/agents/models), [Runner configuration](https://openai.github.io/openai-agents-python/ref/run/).

Explore the [Firedrill examples](https://docs.firedrill.run/guides/examples), [Firedrill](https://firedrill.run/) and [official documentation](https://docs.firedrill.run/). Use documentation matching the qualified release.

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks exercise actual state and native key preflight; authenticated persisted runs and live quality/cost remain separate gates.
