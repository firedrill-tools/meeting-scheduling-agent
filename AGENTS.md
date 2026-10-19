# Working rules

Keep all calendar/outbox data synthetic and use the actual Agents SDK Runner/OpenAI Responses path. No retired Firedrill client, fake provider result or fabricated email delivery. Raw provider completion must precede tool dispatch; preserve independent original usage/failures without guessing totals or billing. Own clients/cancellation and disable tracing per run without global client/cache patches.

Caller write permission is separate from task data. A booking needs exact caller day/participants/duration and an offer actually observed in the run. Cancellation needs its fixed target and a separate grant. Stable request IDs bind original payload/operation; exact retries recover, changed intent fails. Never resurrect a cancelled booking or confirm another request's event. Retain old confirmations, cancellation history and unrelated busy rows.

Tools must remain true async functions: the pinned SDK offloads sync functions to threads that can outlive cancellation. Keep run/deadline guards inside every mutation transaction before commit, bounded SQLite waits, owned tool concurrency/budgets and exact time boundaries. A cancelled await does not prove an earlier action was rolled back. Recording failure must stop later writes; preserve actual state after committed/uncertain failures and never blindly retry.

Use strict coherent bounded fixtures and private exclusive new DB initialization. Ten hash-bound profiles include human_import history with no model measurements. Manifest focus stays out of target context; case selection never grants write permission. Preserve dataset/source hashes, actual SQL snapshots and separate normalized SDK/raw HTTP/tool events.

Run deterministic data checks, meaningful native/state/CLI tests and fresh locked wheel/sdist qualification after relevant changes. Live semantic judgment, authenticated persisted Firedrill runs and publication/license remain gates in PLAN.md. Keep the repo private.

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks exercise actual state and native key preflight; authenticated persisted runs and live quality/cost remain separate gates.
