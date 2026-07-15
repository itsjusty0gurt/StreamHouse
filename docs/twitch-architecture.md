# Twitch integration architecture

Sally uses Twitch's current Helix and EventSub APIs rather than IRC.

## Authentication

- `twitch/auth.py` implements the public-client Device Code flow.
- Access and refresh tokens are encrypted with Windows DPAPI by
  `twitch/token_store.py`.
- The broadcaster and optional Sally bot identities use separate encrypted
  token files. The broadcaster token owns channel analytics and moderation;
  the bot token reads and sends chat as the bot account.
- The broadcaster grants `channel:bot`. The bot login requests
  `user:read:chat`, `user:write:chat`, and `user:bot`.
- Existing tokens survive application and EventSub disconnects. Only explicit
  sign-out deletes credentials.
- When Sally adds scopes, it starts an upgrade flow once and Twitch asks the
  user to approve the additional access.

## Live transport

- `twitch/live.py` resolves Helix resources and owns the EventSub WebSocket.
- The WebSocket handles welcome, keepalive, reconnect, notification,
  revocation, and duplicate message IDs.
- With one identity, chat and authorized activity subscriptions share a socket.
  With a separate bot identity, Sally opens a bot-authorized chat socket and a
  broadcaster-authorized activity socket because Twitch binds WebSocket
  subscriptions to the authorizing user.
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

- **Your Channel** is the stream companion: Chat, Stream Sessions, and Analytics
  tabs plus the overview, grouped chatters, Activity Feed, and eligible ad
  controls. The broadcaster is excluded from the grouped chatter total. Chat
  and the chatter list share right-click local grouping and permission-aware
  Twitch moderation actions.
- **Connections** contains independent broadcaster and optional bot OAuth
  controls plus transport details.
- **Logs > Twitch Events** contains searchable raw EventSub diagnostics and
  sanitized payload details.
- **Developer Tools** contains message and event simulators.
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
