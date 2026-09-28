# Hub local integrations

Streamhouse Hub owns routine definitions, stable routine IDs, task execution,
and queues. External adapters never read Hub persistence or execute tasks
directly.

The experimental Alpha boundary is a versioned JSON API bound only to
`127.0.0.1:8766`. Protocol version 1 supports only:

- listing enabled routines as stable ID plus presentation name/group; and
- requesting a run by stable routine ID.

Requests cross a queued Qt boundary before they reach `AutomationService`.
Accepted runs use the routine's normal assigned queue and Default Queue fallback.
The external origin is represented as the `integration.touch_portal` trigger
source; no Twitch, user, command, or keyword context is fabricated.

The listener starts only after the primary Hub process has acquired exclusive
data-root ownership and normal Hub composition has completed. It stops before
writable automation teardown. A port conflict disables this optional boundary
without crashing Hub. The API is loopback-only, has no account/pairing layer,
and is not yet a frozen public inter-app protocol.

The Touch Portal adapter is a client of this boundary. Touch Portal choice lists
contain display strings only, so the adapter maintains a runtime mapping from
clean routine labels to stable IDs and submits only the ID to Hub. Duplicate
names use group context and, only for identical name/group pairs, a short opaque
stable suffix. Stale labels are rejected rather than executed by name.
