# Meeting scheduling scenarios

Initialize a fresh database from `fixtures/starter.json` for each independent case. The fixture has Toronto and London participants with two existing busy blocks on 2026-11-05.

| Case | Request | Expected behavior |
|---|---|---|
| Available overlap | Find a 30-minute meeting for Mira and Leo on 2026-11-05. | Offer 15:00 UTC (10:00 Toronto, 15:00 London). Do not offer Mira's 14:00–15:00 conflict or Leo's 16:00–17:00 conflict. |
| Booking | With `--allow-book`, book the listed time and confirm it. | One meeting record and one synthetic outbox entry, with the exact participant set. |
| Changed availability | List 15:00, then add a busy block for Mira at 15:00 before booking. | Reject the old revision; the agent should list again. |
| Duplicate request | Repeat the same `--request-id` and exact booking. | Return the existing event; do not add a second busy block or email. A different payload with the same ID is rejected. |
| Read-only | Omit `--allow-book`. | The agent can discuss availability but booking and confirmation tools reject writes. |
| Missing person | Request an unknown participant ID. | Tool returns a clear error; no event is created. |

The offline suite verifies the calendar and decorated SDK tools, not the model's choice of tool calls or phrasing. Live quality and usage need a provider key.
