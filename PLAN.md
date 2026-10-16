# Meeting scheduling requirement audit

## Reproduced and corrected first gaps

- The actual decorated confirmation tool accepted an event belonging to another request and added its outbox entry. Tool calls now bind confirmation to the caller's fixed request ID.
- A booking at 15:07:01 was accepted even though listing offered only a half-hour grid. New bookings now require an exact offer actually observed in the run, with participant/duration/revision identity. The store rejects off-grid/seconds/microseconds starts. Exact already-committed request recovery remains idempotent.
- Working-hour comparison discarded end seconds/microseconds; an end after 17:00 was accepted. Exact local clock bounds now apply.

## Next independent tasks

1. Strict bounded fixture/participants/durations/revisions, coherent busy/calendar/outbox state, private SQLite ownership and snapshots that include blocks. Validate DST/local/UTC day boundaries and true concurrency/idempotent recovery.
2. Explicit cancellation lifecycle and appropriate caller authorization, including no confirmation of cancelled meetings and retained cancellation/outbox history. The current project only creates/confirms, so cancellation coverage remains implementation work.
3. Actual OpenAI Agents SDK/Responses execution with explicit model/client/backend/key, per-run tracing disabled, native pre-dispatch completion/usage observations, request/tool/time/retry bounds, async cancellation/cleanup and durable evidence. No fabricated model or sent-email claims.
4. Hash-bound starter/busy/ambiguous/boundary calendars and private initialization/reset, actual CLI HTTP/failure/signal checks, fresh locked wheel/sdist, exact-head CI and catalog proof.

## Parked external gates

Live provider tool choices and user-intent interpretation need an owner-supplied enabled model/key/budget. Released Firedrill persisted scenarios, publication and licensing remain external gates. All calendar and outbox data stay synthetic; an outbox row is not email delivery. Continue independent work.
