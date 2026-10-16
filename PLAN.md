# Meeting scheduling requirement audit

## Completed standalone work

1. **Calendar state:** strict bounded synthetic fixtures, private DB/journals, coherent people/blocks/imported meetings, single-transaction snapshots, exact local/UTC/DST/date endpoints and true concurrent request/revision checks. Legacy schema upgrades preserve history without invented model evidence.
2. **Cancellation:** explicit separate caller target grant, original-intent inspection, transactional cancellation, exact retry/cross-operation identity, retained confirmations/history and separate cancellation notices. Cancelled original requests never resurrect an event.
3. **Native runtime:** actual Agents SDK/OpenAIResponsesModel/owned AsyncOpenAI/HTTPX2 clients, JSON completion before dispatch, raw argument coercion rejection, eight prepared requests/twelve turns/24 tools, no retries, one tool at a time, 2048 output tokens and bounded deadline/cleanup. Per-run tracing disabled. Native failure/partial-commit recovery retains exact business state.
4. **Data:** ten synthetic-scheduling-cases v1 cases/four profiles. Busy: twelve people, eighteen human-import meetings, 24 external blocks, twelve confirmations, three cancellations and two notices. Manifest focus is outside context; task selection does not grant permission.
5. **Operations/evidence:** private per-run original HTTP/SDK/tool/source/state receipts; persistence faults refuse later writes and retain already-committed effects. Actual loopback CLI booking/cancellation, error and SIGINT/SIGTERM; packaged case selection/init and fresh locked wheel/sdist outside checkout; exact-head CI/catalog proof.

## Reproduced gaps fixed

- Foreign-request confirmation and unlisted 15:07:01 booking. Tools now bind request/event and exact observed scope; store starts stay on the listed grid.
- Working-hour comparisons lost seconds/microseconds; exact endpoints now apply, including closing midnight.
- Actual synchronous SDK tool worker committed after its awaited task was cancelled. Tools are now owned async functions with an inside-transaction run/deadline fence; real expiry rolls back.
- Native SDK already stopped incomplete/failed Responses but accepted absent status before dispatch. Raw completion guards preserve original measurements and stop that path.
- SDK-normalized argument coercion could erase raw boolean/float/extra-field identity. Raw types are checked before mutation.
- The original project lacked cancellation lifecycle, explicit caller write scope, retained cancellation history and richer human state. These are now implemented independently of any model quality claim.

## Parked external gates

Live OpenAI participant interpretation, timezone explanation and scheduling choices need owner-supplied model/key/budget and human review. Current protocol/state fixtures do not establish semantic success, provider retention or billing.

New official Firedrill release/contract, persisted ordinary/negative/recovery runs and exact-release CI remain external. Publication and license ownership remain undecided; repository stays private. Continue the remaining portfolio projects.
