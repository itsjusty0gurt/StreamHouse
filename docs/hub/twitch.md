# Twitch integration architecture

Streamhouse Hub uses Twitch's current Helix and EventSub APIs rather than IRC.
Product-facing naming and
dependencies are defined in the
[Streamhouse product-family reference](../architecture/product-family.md).

## Authentication

- `products/hub/twitch/auth.py` implements the public-client Device Code flow.
- Access and refresh tokens are encrypted with Windows DPAPI by
  `products/hub/twitch/token_store.py`.
- The broadcaster and optional bot identities use separate encrypted
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

Every ready Chat Command trigger exposes `command.name` and `command.data`
through the modern Variables registry. `command.name` is the recognized name
without `!`; `command.data` is the raw text after that name with surrounding
separator whitespace trimmed. It may be empty and preserves internal spaces.
For example, `!title Tonight we're playing Vintage Story` provides
`command.name = title` and
`command.data = Tonight we're playing Vintage Story`.
Both values are contextual for that one routine execution, remain available to
every task in the routine, and are discarded afterward. Non-command triggers
do not receive stale command context.

Keyword / Phrase is a separate Twitch Chat trigger for matching ordinary chat.
One trigger handles both single words and multi-word phrases with Contains,
Exact Message, Starts With, and Ends With modes plus Ignore Case and Whole Word
controls. A match supplies the normal `user.*` and `chat.*` context plus:

- `keyword.message`: the full triggering message;
- `keyword.match`: the configured canonical text;
- `keyword.before`: trimmed text before the match;
- `keyword.after`: trimmed text after the match.

These values are routine-scoped and never create `command.*` context. For
`I think coffee is better than tea`, matching `coffee` yields `I think` and
`is better than tea` as the before/after values.

The Automation **Variables** reference keeps Command, Keyword / Phrase, Ads
Started requester, and other contextual definitions discoverable even outside
a live routine. Their current value remains explicitly unavailable until the
matching trigger context exists; the page shows their provider-owned context
requirement and Routine lifetime rather than inventing global values.

Built-in command definitions are code-owned templates, not inactive routines.
A fresh Hub displays them as **Not Configured** on the Commands page but does
not persist a trigger or create an Automation routine. Explicitly configuring
a template creates one normal managed command routine; deleting it returns the
definition to template state. A configured default can be reset to its current
template, while a custom command or alias occupying the template name is
reported as a conflict and is never overwritten.

Configured defaults and custom Chat Commands share the Automation **Commands**
group. The group is created with the first configured command and removed when
the last command routine is deleted, unless it contains another explicitly
grouped routine. Editing command settings updates the existing trigger and
routine rather than replacing their stable identities.

Reusable Twitch lookup tasks live in `products/hub/twitch/tasks.py`:

- Resolve User produces the target ID, login, display name, account creation
  time, and a controlled lookup status.
- Get Stream Information produces live/offline/error status, start time, title,
  category, stream ID, and viewer count.
- Get Follow Relationship distinguishes following, not following, missing
  permission, broadcaster-self, missing-user, and API-failure outcomes.
- Build Command List returns enabled commands visible to the invoking viewer and
  stays within the configured Twitch message budget.

Information Hub already owns does not need an Automation Get task. Cached
Twitch title/category state resolves through `stream.title` and
`stream.category`. Optional values configured in **Your Channel > Channel
Information** can be published with their independent **Expose as Variable**
checkboxes:

- `channel.schedule` and `channel.rules`;
- `socials.discord`, `socials.youtube`, `socials.tiktok`, `socials.instagram`,
  `socials.bluesky`, `socials.twitter`, `socials.facebook`, and
  `socials.website`;
- `serverinfo.details` for the current combined server description/address.

Unchecked or blank fields do not publish definitions. Valid edits update live
resolution immediately; Save persists the value and exposure choice for the
next launch. The social **Include** checkbox remains separate and controls only
the formatted `!socials` message. Default Channel Information commands use
these canonical Variables directly; obsolete pre-alpha routines containing the
removed Get tasks are rejected/reset rather than supported by a compatibility
path.

`core.format_duration` and `core.select_text` provide reusable formatting and
conditional response selection. Every output is routine-scoped and described
by typed temporary-output definitions so later task editors show friendly,
insertable values. Network-backed command routines run on a single Qt worker;
the completion signal performs command statistics and UI updates on the main
thread.

## UI responsibilities

- **Your Channel** is the current Hub channel workspace. Its current top-level tabs
  are Chat, Analytics, Soundboard, Channel Information, Commands, Channel
  Points, Counters, and User. Session data is
  currently presented within Analytics. Chat includes the overview, grouped
  chatters, Activity Feed, and eligible ad controls. The broadcaster is
  excluded from the grouped chatter total. Chat and the chatter list share
  right-click local grouping and permission-aware Twitch moderation actions.
  Chat is a bounded structured timeline: normal rows remain compact and
  borderless, while Twitch, moderation, and Hub-system notices may use an
  accent. It follows new messages only while the viewer is already at the
  bottom. Message menus provide reply/copy/user details plus service-backed
  delete, timeout, ban, and unban controls when OAuth scopes permit them. The
  User tab shows roles and recent messages from the current in-memory session.
  Local Regulars/Bots/Viewers assignments are Hub-owned classifications stored
  in `memory/twitch_chatters.json` by stable Twitch user ID. Twitch snapshot
  refreshes update account names and roles without replacing those assignments.
  The same saved Bots classification drives chat, memory, and counter bot
  filtering; there is no separate known-bots list.
  Counters lives here because it is Twitch/stream interaction, while its store,
  service, and task providers remain under `products/hub/counters/`.
- **Connections** contains independent broadcaster and optional bot OAuth
  controls plus transport details.
- **Logs > Twitch Events** contains searchable raw EventSub diagnostics and
  sanitized payload details.
- **Developer Tools** contains message and event simulators.
- The channel splitter, activity filter, window geometry, and dock state are
  restored with `products/hub/core/window_state.py`.

## Simulation

The local HTTP listener validates Twitch-style signatures and routes simulated
events through the same parser, normalized bus, Activity Feed, and diagnostics
used by live traffic.

## Health and persistence

Connection health is modeled separately from page widgets. It tracks auth,
EventSub, missing scopes, Streamhouse AI refresh success, and endpoint-specific
failures. Local JSON histories use atomic replacement plus one last-known-good
backup. Configured command triggers persist at `twitch/commands.json`; their
managed routines and the Commands group persist at
`automation/routines.json`. Unconfigured built-in templates are not persisted
in either file. Both stores are included in current Streamhouse Hub backups.
Chatter records use the current version-six schema;
malformed identities and unsupported local group values are discarded or
normalized at the store boundary.

The channel snapshot refresh reads Twitch's ad schedule with `channel:read:ads`.
`AdsService` owns the single cached `AdsState`, including the next/last break,
duration, preroll-free time, snoozes, manual-commercial retry time, and active
break timing. The compact Ad Manager remains on the Chat tab and shows the next
or active countdown, next duration, snooze count/refresh, a remembered
30–180-second commercial duration (180 seconds by default), Run Ads, and
Snooze. It intentionally has no preroll-free progress bar or separate Ads
settings workspace. API operations run on the existing Qt worker pattern and
call `AdsService`, not Helix from the widget.

With `channel:read:ads`, Hub subscribes to `channel.ad_break.begin`. The service
retains Twitch's start time, duration, automatic/manual flag, and requester
identity when supplied. Twitch has no public ad-break-end EventSub event, so
Hub calculates the estimated end as start time plus duration, clears active
state then, refreshes the schedule, and publishes Ads Ended. This is a Hub
timing estimate, not confirmation of each viewer's playback completion.

`AdsVariableProvider` exposes the authoritative state as typed `ads.*` values:
`ads.next_at`, `ads.next_in`, `ads.next_duration`, `ads.last_at`, snooze and
preroll fields, `ads.in_progress`, `ads.remaining`, active duration/start/mode,
and `ads.manual_retry_after`. `ads.requester.id` and
`ads.requester.name` are contextual to Ads Started when Twitch supplies them.

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

The Twitch trigger tree also provides an Ads category with 5-, 3-, 2-, and
1-minute warnings, Ads Started, and Ads Ended. Warning state belongs to
`AdsService`: each threshold fires at most once for one scheduled timestamp,
and a successful snooze replaces that timestamp and recalculates warning
eligibility. There are no built-in chat messages; users compose a warning
trigger with an ordinary Chat task and any registry Variables they need.

Counter actions do not require a counter-specific trigger system. The four
registered Counter tasks—Increase, Decrease, Set, and Reset—may be placed in
any routine reached by a Twitch chat command, supported EventSub trigger,
OBS/Core trigger, or manual execution. Amount/Value fields accept literals or
modern Variables. Thus `!counterset 4.5` plus Set value `{command.data}` and
`!coffee 0.5` plus Increase amount `{command.data}` are ordinary composed
automations, not hardcoded Counter commands.

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
