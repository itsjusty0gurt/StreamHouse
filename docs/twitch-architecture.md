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

Custom commands are Twitch triggers evaluated by
`TwitchCommandTriggerDispatcher`. Ready matches publish a normalized
`TriggerEvent`; rejected matches publish a named outcome without exposing chat
content:

- `trigger_fired`
- `trigger_fired.twitch.command`
- `twitch_command_trigger_executed`
- `twitch_command_trigger_rejected`

The command domain uses `TwitchCommandTrigger`,
`TwitchCommandTriggerStore`, `TwitchCommandTriggerDispatcher`, and
`TwitchCommandTriggerResult`. Each trigger points to a managed routine whose
first task is currently `twitch.send_chat_message`. Later redemption, follow,
and event triggers can publish into the same routine and task layer.

## UI responsibilities

- **Your Channel** is the stream companion: Chat, Stream Sessions, Analytics,
  and Commands tabs plus the overview, grouped chatters, Activity Feed, and
  eligible ad controls. The broadcaster is excluded from the grouped chatter
  total. Chat and the chatter list share right-click local grouping and
  permission-aware Twitch moderation actions.
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
backup. Command triggers persist at `twitch/commands.json`; their managed
routines persist at `automation/routines.json`. Both are included in Sally
backups. Older chatter records migrate through defaulted version-three fields.

The stream companion reads Twitch's ad schedule with `channel:read:ads` and
shows the next break and duration, the last break, remaining pre-roll-free
time, available snoozes, and the next snooze refresh. A one-second local timer
updates countdowns between the normal one-minute Helix refreshes. Running and
snoozing ads continue to require `channel:edit:commercial` and
`channel:manage:ads`; the last manually used commercial duration is retained
as a local UI preference.

Editing a Twitch command updates its managed chat-response task in place. Any
additional tasks attached in Automation remain ordered and intact, even when
the response task has been moved away from the first position.

## Automation event triggers

Twitch EventSub automation triggers persist at
`twitch/event_triggers.json`. The live-ready set is follow, subscribe,
subscription gift, subscription message, cheer, incoming raid, custom channel
point redemption, stream online, and stream offline. These correspond to the
activity subscriptions Sally currently establishes with Twitch.

A trigger may optionally match exact EventSub payload fields. Dot notation
addresses nested fields, for example `reward.id` or `reward.title`. Matching is
case-insensitive. The normalized task context includes `{event_type}`, `{user}`,
`{message}`, `{input}`, `{amount}`, `{bits}`, `{viewers}`, `{tier}`, `{reward}`,
`{reward_id}`, and `{reward_cost}` in addition to the existing Twitch message
variables. Live traffic and Developer Simulation enter the same routing path.
