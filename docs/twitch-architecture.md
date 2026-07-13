# Twitch integration architecture

Sally uses Twitch's current Helix and EventSub APIs rather than IRC.

## Authentication

- `twitch/auth.py` implements the public-client Device Code flow.
- Access and refresh tokens are encrypted with Windows DPAPI by
  `twitch/token_store.py`.
- Existing tokens survive application and EventSub disconnects. Only explicit
  sign-out deletes credentials.
- When Sally adds scopes, it starts an upgrade flow once and Twitch asks the
  user to approve the additional access.

## Live transport

- `twitch/live.py` resolves Helix resources and owns the EventSub WebSocket.
- The WebSocket handles welcome, keepalive, reconnect, notification,
  revocation, and duplicate message IDs.
- Chat and authorized activity subscriptions share one socket.
- Paginated Helix collections are followed until Twitch omits the next cursor.
- Temporary GET failures and rate limits use bounded retry/backoff.

## Event pipeline

Every accepted notification produces a normalized `TwitchEvent` and is
published to both:

- `twitch_event`
- `twitch_event.<subscription_type>`

Chat messages also produce typed `TwitchMessage` objects. The raw developer
diagnostic stream is separate from the human-readable Activity Feed.

## UI responsibilities

- **Your Channel** is the stream companion: overview, chat, grouped chatters,
  Activity Feed, and eligible ad controls.
- **Connections** contains OAuth and transport details.
- **Developer Tools** contains simulators and raw EventSub diagnostics.
- The companion splitter, activity filter, window geometry, and dock state are
  restored with `core/window_state.py`.

## Simulation

The local HTTP listener validates Twitch-style signatures and routes simulated
events through the same parser, normalized bus, Activity Feed, and diagnostics
used by live traffic.

## Health and persistence

Connection health is modeled separately from page widgets. It tracks auth,
EventSub, missing scopes, companion refresh success, and endpoint-specific
failures. Local JSON histories use atomic replacement plus one last-known-good
backup. Older chatter records migrate through defaulted version-three fields.
