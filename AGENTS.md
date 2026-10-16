# Working rules

Keep all calendar and outbox data synthetic. Preserve local time-zone checks, the optimistic revision check, booking idempotency and the read-only gate. Do not claim an agent booked a meeting unless the database contains the event; do not claim an email was sent when only the synthetic outbox changed.

Offline tool invocations are not live model proof. Add Firedrill integration only against a verified new release.

Bind confirmations to the caller's fixed request ID. New bookings must match an exact offer actually listed in that run (time/participants/duration/revision); exact previously committed request recovery remains idempotent. Preserve half-hour UTC starts and second/microsecond-accurate local work bounds. Native provider/data/distribution and cancellation follow-up remain in PLAN.md.
