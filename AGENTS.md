# Working rules

Keep all calendar and outbox data synthetic. Preserve local time-zone checks, the optimistic revision check, booking idempotency and the read-only gate. Do not claim an agent booked a meeting unless the database contains the event; do not claim an email was sent when only the synthetic outbox changed.

Offline tool invocations are not live model proof. Add Firedrill integration only against a verified new release.
