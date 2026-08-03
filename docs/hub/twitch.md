# Twitch integration architecture

Streamhouse Hub uses Twitch's current Helix and EventSub APIs rather than IRC.
Product-facing naming and
dependencies are defined in the
[Streamhouse product-family reference](../architecture/product-family.md).

## Authentication

- `products/hub/twitch/auth.py` implements the public-client Device Code flow.
- Access and refresh tokens are encrypted with Windows DPAPI by
  `products/hub/twitch/token_store.py`.
- The broadcaster and optional Sally bot identities use separate encrypted
  token files. The broadcaster token owns channel analytics and moderation;
  the bot token reads and sends chat as the bot account.
- The broadcaster grants `channel:bot`. The bot login requests
  `user:read:chat`, `user:write:chat`, and `user:bot`.
- Existing tokens survive application and EventSub disconnects. Only explicit
  sign-out deletes credentials.
- When Hub adds scopes, it starts an upgrade flow once and Twitch asks the
  user to approve the additional access.

## Live transport

- `products/hub/twitch/live.py` resolves Helix resources and owns the EventSub WebSocket.
- The WebSocket handles welcome, keepalive, reconnect, notification,
  revocation, and duplicate message IDs.
- With one identity, chat and authorized activity subscriptions share a socket.
  With a separate bot identity, Hub opens a bot-authorized chat socket and a
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
ordered tasks are ordinary registered automation providers. The built-in
`!uptime`, `!followage`, `!accountage`, `!title`, `!game`, and `!commands`
definitions use the same editable routines as custom commands rather than
dispatcher branches.

Built-in commands have stable default IDs. Startup seeds only missing defaults,
never overwrites an existing default, and records deletion tombstones so a
streamer's removed default stays removed. The Commands page can reset one
existing default to its current definition or explicitly restore missing
defaults. A custom command or alias occupying a default name is reported as a
conflict and is never overwritten.

Reusable information providers live in `products/hub/twitch/tasks.py`:

- Resolve User produces the target ID, login, display name, account creation
  time, and a controlled lookup status.
- Get Stream Information produces live/offline/error status, start time, title,
  category, stream ID, and viewer count.
- Get Channel Information produces title and category even while offline.
- Get Follow Relationship distinguishes following, not following, missing
  permission, broadcaster-self, missing-user, and API-failure outcomes.
- Build Command List returns enabled commands visible to the invoking viewer and
  stays within the configured Twitch message budget.

`core.format_duration` and `core.select_text` provide reusable formatting and
conditional response selection. Every output is routine-scoped and registered
with the generated-variable catalog so later task editors show friendly,
insertable values. Network-backed command routines run on a single Qt worker;
the completion signal performs command statistics and UI updates on the main
thread.

## UI responsibilities

- **Your Channel** is the current stream companion. Its current top-level tabs
  are Chat, Analytics, Soundboard, Commands, and Channel Points. Session data is
  currently presented within Analytics. Chat includes the overview, grouped
  chatters, Activity Feed, and eligible ad controls. The broadcaster is
  excluded from the grouped chatter total. Chat and the chatter list share
  right-click local grouping and permission-aware Twitch moderation actions.
- **Connections** contains independent broadcaster and optional bot OAuth
  controls plus transport details.
- **Logs > Twitch Events** contains searchable raw EventSub diagnostics and
  sanitized payload details.
- **Developer Tools** contains message and event simulators.
- The companion splitter, activity filter, window geometry, and dock state are
  restored with `products/hub/core/window_state.py`.

## Simulation

The local HTTP listener validates Twitch-style signatures and routes simulated
events through the same parser, normalized bus, Activity Feed, and diagnostics
used by live traffic.

## Health and persistence

Connection health is modeled separately from page widgets. It tracks auth,
EventSub, missing scopes, Streamhouse AI refresh success, and endpoint-specific
failures. Local JSON histories use atomic replacement plus one last-known-good
backup. Command triggers persist at `twitch/commands.json`; their managed
routines persist at `automation/routines.json`. Both are included in current
Streamhouse Hub backups. Older chatter records migrate through defaulted
version-three fields.

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

`!followage` uses Helix Get Channel Followers with `user_id`. Twitch requires
the broadcaster user token to include `moderator:read:followers`, and the token
identity must be the broadcaster or a moderator for that channel. Hub requests
that scope in its existing broadcaster permission set and reports missing or
rejected authorization distinctly from a genuine not-following result.

## Automation event triggers

Twitch EventSub automation triggers persist at
`twitch/event_triggers.json`. The live-ready set is follow, subscribe,
subscription gift, subscription message, cheer, incoming raid, custom channel
point redemption, stream online, and stream offline. These correspond to the
activity subscriptions Hub currently establishes with Twitch.

A trigger may optionally match exact EventSub payload fields. Dot notation
addresses nested fields, for example `reward.id` or `reward.title`. Matching is
case-insensitive. The normalized task context includes `{event_type}`, `{user}`,
`{message}`, `{input}`, `{amount}`, `{bits}`, `{viewers}`, `{tier}`, `{reward}`,
`{reward_id}`, and `{reward_cost}` in addition to the existing Twitch message
variables. Live traffic and Developer Simulation enter the same routing path.

## Planned Hub channel workspace

The planned **Your Channel** structure is:

- Overview
- Stream Info
- Chat
- Analytics
- Engagement
  - Polls
  - Predictions
- Raids
- Moderation
- Soundboard
- Commands
- Channel Points

Analytics contains stream-session history as well as aggregate reporting;
Stream Sessions is not a separate workspace. This is a product plan, not the
current tab list.

### Stream Info and overview

Use **Stream Info**, not “Twitch Controls.” Native Hub controls should use
documented Twitch APIs for fields such as title, category, tags, language,
content classification labels, and branded-content status. OBS's Stream Info
dock embeds Twitch's web dashboard; Hub instead should prefer native controls.
Unsupported advanced settings may open Twitch's dashboard. Do not depend on
undocumented Twitch dashboard requests.

Planned dashboard items include stream uptime, follower count, estimated hours
watched, and a stream health summary. If Hub calculates hours watched locally
from sampled viewer counts, the UI and exports must label it as an estimate
rather than official Twitch analytics.

### Engagement

Polls and Predictions belong under Engagement. Manual controls and automation
should eventually share the same Twitch Service and Routine/Task provider
architecture. Potential future task types are:

- `twitch.create_poll`
- `twitch.end_poll`
- `twitch.create_prediction`
- `twitch.lock_prediction`
- `twitch.resolve_prediction`
- `twitch.cancel_prediction`

Repository inspection confirms that these task types are not currently
registered with a Task provider; they are plans, not current capability.

### Raids

Planned raid controls and normalized events must distinguish:

- **Raid Initiated**: Hub successfully starts the Twitch raid countdown.
- **Outgoing Raid Sent**: Twitch confirms that the outgoing raid occurred.
- **Incoming Raid**: another broadcaster raids the channel.

The `channel.raid` incoming-raid trigger is currently implemented. Outgoing
raid controls, Raid Initiated, and Outgoing Raid Sent are planned.

### Stream Health and Moderation

Stream Health remains separate from Moderation. Stream Health may summarize
Twitch authentication, EventSub health, missing scopes, OBS or broadcaster
connection, broadcast state, Streamhouse AI availability, relay status, and
recent API failures. Moderation remains its own workspace for channel and
viewer moderation.

New manual controls and product integrations should enter through Services,
normalized trigger events, Routines, registered task providers, Queues, and
documented versioned APIs. They should not create direct special-case calls
between unrelated UI pages. Plugins remain a late-stage capability.
