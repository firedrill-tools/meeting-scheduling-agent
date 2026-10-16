# Scheduling scenarios

All records and product/person names are synthetic. Prior meetings/notifications are human_import, not model executions. Initialize a new private DB for an independent journey; preserve receipts when resetting. Focus below describes review criteria, not seeded model replies or semantic pass scores. Run without a write flag for planning; --allow-book/--allow-cancel explicitly grants the selected operation.

| Case | Focus |
|---|---|
| starter | Named timezone lookup, observed slot, exact caller scope, committed booking and synthetic confirmation. |
| busy | Twelve people/eighteen prior meetings/24 external blocks with retained human confirmation/cancellation history. |
| ambiguous-name | Two Mira Chen identities require clarification; do not guess. |
| no-overlap | Toronto/Singapore one-hour local windows have no common slot. |
| availability-change | Preferred 15:00 is blocked; choose another current offer. Use block between list/book for a real revision-change fault. |
| dst-uk-transition | Toronto/London offsets after the UK autumn change; do not assume a constant difference. |
| dst-us-transition | Same participants after the US/Canada change, with a different overlap. |
| utc-day-boundary | UTC start day includes another local date; a valid local-workday booking can end on the next UTC date. |
| cancel-scheduled | Fixed target, current revision, retained old confirmation and a separate synthetic cancellation notice. |
| cancel-recovery | Original human cancellation already exists; inspect original reason and queue only its missing notice. |

Actual SDK/HTTP checks cover missing/incomplete/failed status, refusal, unsupported/mixed/replayed tool output, raw usage, model/tool budgets, raw type coercion, client startup/error/cancel/deadline cleanup and committed cancellation followed by provider failure. State checks cover separate-process-style SQLite writer connections, identical request recovery, competing revisions/cancellations and inside-transaction deadline rollback. Tools no longer detach write threads. Loopback CLI checks prove native booking/cancellation and both signals.

Receipt faults before request/response/book/confirmation and final persistence preserve whether a meeting already exists; no automatic second mutation runs. Generated prose remains a draft explanation. Verify request-specific actual_effects and SQLite state, and use live/human review for identity resolution, intent, unsupported promises and local-time explanation. Outbox state never proves email delivery.

The versioned manifest binds exact fixture bytes/identity/populations and task selection. Assessment focus never enters the prompt, and selected tasks do not grant writes. No raw usage, model trials or costs are seeded.
