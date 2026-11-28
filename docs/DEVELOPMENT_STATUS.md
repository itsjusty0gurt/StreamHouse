# Streamhouse development status

> Current working-state handoff. Update after meaningful milestones, branch
> changes, architecture changes, or validation results. Do not use this file as
> release history; Git tags and release notes remain authoritative.

## NEW SESSION HANDOFF

1. **What Streamhouse is:** a Windows-first, local-first streaming ecosystem.
   Hub manages integrations and Automation; Streamhouse AI is a separate optional
   local-AI application; the Soundboard Extension is separately deployed.
2. **Current architecture:** Hub Core owns Automation, registries, persistence
   orchestration, and the Qt shell. Trusted bundled modules contribute actions,
   tasks, triggers, Variables, tabs, and Backup data through Core contracts.
3. **Current architecture milestone:** OBS and Twitch are accepted bundled Hub
   modules. Twitch owns its catalogs, auth/transport lifecycle, UI, Commands,
   Variables, triggers, Backup contributions, reconnect, and shutdown.
4. **0.3 release state:** feature scope is frozen for `0.3.0-alpha`. The
   module-owned Twitch and OBS integrations, Core Live workspace, native
   dockable layout, Quick Actions, Twitch Account/onboarding, and the accepted
   Automation and stability fixes are integrated on `develop`.
5. **Live workspace milestone:** Core hosts the dockable Live workspace;
   Twitch contributes Chat, Activity, Chatters, Stream Overview, and Ad Manager,
   while Core owns persisted routine-backed Quick Actions. Native docking,
   reset/persistence, and Quick Actions strip behavior are accepted.
6. **Twitch milestone:** Twitch owns its complete module lifecycle and now
   exposes a dedicated Account tab for broadcaster and optional bot setup,
   authorization, recovery, and independent disconnect controls.
7. **Release blockers:** the accepted 0.3 blockers are cleared. Current release
   preparation is limited to metadata, notes, focused validation, and packaging.
8. **Immediate next steps:** tag and publish `v0.3.0-alpha` only after the
   documented release and upgrade checklist is completed against the exact
   release commit.
9. **Read next:** [master reference](STREAMHOUSE_MASTER_REFERENCE.md),
   [architecture](ARCHITECTURE.md), [0.3 foundation](architecture/hub-0.3-foundation.md),
   [implementation map](architecture/overview.md), and
   [development policy](architecture/development-policy.md).

## Snapshot

| Item | Verified value |
| --- | --- |
| Date | 2026-11-10 |
| 0.3 release-prep baseline | `develop` at `eb1bc1740a4c8352cd2226681f324e411e832565` |
| Release scope | Frozen; accepted 0.3 feature and blocker work is integrated |
| Upstream | `origin/develop` |
| Working tree at task start | clean |
| Hub development version | `0.3.0-alpha` / Windows `0.3.0.0` |
| Latest published Hub tag | `v0.2.1-alpha` at `b1c25a21ef34de8029d6216eb96c47274c2a4c58` |

## Active development focus

**Hub 0.4 is now in bug-fixing and stabilization.** Further feature work
(including Twitch/OBS Automation expansions and the Stream Information dock)
is deferred until stabilization is complete or priorities explicitly change.
The following are investigation priorities, not confirmed root causes:

1. Live Counters refresh verification after the lifecycle correction, including
   the Daily Yogurt redemption.
2. Historical viewer-memory native crashes.
3. Intermittent native Qt crashes not tied to a confirmed root cause.
4. Remaining audio/UI blocking risks.
5. Twitch reconnect and EventSub lifecycle verification.
6. Dynamic and multi-monitor window resizing, including native mixed-DPI
   monitor moves.
7. Twitch Ad Manager reliability.
8. Other confirmed regressions.

**Twitch Ad Manager reliability — Phase 1 (awaiting final acceptance and integration).**
The compact Live → Chat controls and remembered 30–180-second duration
(default 180) are unchanged. No OAuth scopes were added.

| Capability | Verified behavior and limits |
| --- | --- |
| Read schedule | Helix GET `/channels/ads`; broadcaster `channel:read:ads`. Empty/failed responses no longer present an actionable stale schedule. UTC countdowns accept Unix seconds and RFC3339. |
| Snooze | Helix POST `/channels/ads/schedule/snooze`; broadcaster `channel:manage:ads`. The UI requires a known upcoming automatic ad and a remaining snooze. The returned schedule is applied immediately and then refreshed. |
| Run Ads | Helix POST `/channels/commercial`; broadcaster `channel:edit:commercial` and an eligible live channel. Success comes from Twitch, not worker submission. Only Twitch's returned `retry_after` sets the local cooldown; the schedule does not establish a cooldown. |
| Ad start | The existing authenticated WebSocket subscribes to `channel.ad_break.begin` v1 with `channel:read:ads`. Delivery uses the existing EventSub message-ID deduplication; reconnect cannot recover missed events. |
| Ad warnings/end | The existing 5/3/2/1-minute warnings are estimates derived from the schedule; Ads Ended is estimated from the EventSub start and duration. Neither has an independent Twitch EventSub notification. No new triggers or schedule-change notification were added. |

Mocked requests demonstrated a repeated POST after an ambiguous failure, a
45-second Twitch cooldown incorrectly raised to 480 seconds by a schedule
refresh, and active-ad state surviving offline. Ad POSTs no longer replay on
failure; one write at a time is allowed across Ad Manager and Automation.
Schedule revisions and account/request guards prevent old responses overwriting
new state. Worker results use receiver-owned Qt slots and are ignored after
shutdown or account replacement. Ad triggers use the existing deferred
Automation queue rather than running routines inline from the UI clock.

Twenty retained session logs and eight log entries in four support bundles
contained no ad-specific error markers; two session log entries concerned
general channel API authorization recovery, not a proven Ads failure. Still verify
the broadcaster's granted scopes, eligibility and actual ad schedule on an
authorized test profile. A lost write response can leave the result unknown;
check Twitch before retrying. No live commercial, snooze, or reauthorization
was performed during this audit. See the
[Twitch Ads API](https://dev.twitch.tv/docs/api/reference/ads) and
[Ad Break Begin contract](https://dev.twitch.tv/docs/eventsub/reference/channel#channel-ad-break-begin).

Offscreen validation: 479 focused tests and 97 subtests; 1,718 tests and
427 subtests in one uninterrupted full suite, with no failures, skips, or
native Qt exits. The new regressions cover non-replayed writes, cooldown
preservation, stale schedule rejection, snooze eligibility, warning deduplication,
account changes, and late worker completion. Compilation and diff checks passed.

Merge review blocked integration after reproducing a Snooze race: a failed or
empty schedule read was ignored while Snooze was pending, then a timed-out POST
cleared the UI's pending flag without invalidating the cached schedule. This
restored the old countdown and enabled a possible duplicate manual Snooze.
The fix invalidates cached eligibility and advances the schedule revision
on failed writes. It distinguishes explicit rejection from an unknown outcome
and never replays the POST. Only a valid schedule requested afterward for the
current broadcaster and token can restore eligibility. That read does not
prove whether the earlier write succeeded. Deterministic regressions cover
callback ordering, obsolete reads, reconciliation, and continued write safety.
Offline packaged acceptance applied to `0fa9871cd6a1dc7b8d7e817a23b4f183cf934b39`
only; the corrected build awaits acceptance. No real Twitch Snooze, commercial,
or authenticated live ad timing was verified.

Snooze blocker revalidation: 253 focused tests and 100 subtests passed. The
first full run exited with Windows access violation `0xC0000005` at
`test_first_message_editor_shows_shared_raid_suppression_settings`, after
542 logged test passes. The dialog/lifecycle check passed on both the accepted
commit and the correction (3 tests each); the native cause is unverified.
The unchanged full-suite retry passed in one process: 1,724 tests and 444
subtests, no failures, skips, or native exits. Compilation and diff checks passed.

**Counters UI lifecycle correction — implemented, offline accepted, and merged.**
The November 25 local fault log and support-bundle copy show a GUI-thread
access violation in Counters refresh during channel snapshot application.
The Python stack identifies the line that reads the selection and clears
the list, but cannot identify the exact native instruction. Offscreen
baseline probes did not reproduce the access violation. They confirmed
that unchanged refreshes reset the list, destroyed the selected item's
native object, and dispatched detail updates during reconstruction.
A separate worker-originated notification also reached the page after
shutdown had begun.

Counters now reconciles list items by stable ID, blocks selection signals
during the update, and refreshes details once. MainWindow ignores pending
Counter refreshes after shutdown starts. Regression coverage checks item
identity, filtering and empty transitions, viewer and stream totals, repeated
channel snapshots, GUI-thread delivery, and late shutdown notifications.
The corrected page passed 500 isolated refresh cycles across 25 widget
lifetimes. The list reset is a supported failure-path candidate, not a
confirmed explanation of the packaged access violation.

Packaged Windows offline acceptance used disposable data. Selection, filtering,
Overview/Viewers/Settings, navigation away and back, seeded lifetime totals
after restart, and two clean shutdowns all passed. No crash artifacts or
lingering Hub/QtWebEngine processes were observed, and the streaming profile
was untouched. Authenticated Twitch snapshots and the original Daily Yogurt
redemption were not exercised. The correction has not been proven to prevent
the exact live access violation, and the redemption's role remains unverified.
Some Counters controls clipped at smaller window sizes; the separate
layout correction is recorded below.

The October 28 and 31 support-bundle traces show historical Viewer Memory
refresh crashes on the snapshot path. That page already received a stable-ID
and queued-refresh correction; current focused tests passed without further
changes. A shared native cause with either Viewer Memory or intermittent
OBS/MainWindow test-runner failures is unverified. No native dump was needed
or inspected, and the existing diagnostics were preserved.
Final focused validation passed 62 tests and 10 subtests. The partitioned full
gate passed 1,699 tests and 410 subtests without assertion failures or skips.
One initial MainWindow partition exited with a native access violation during
Twitch Account page construction; all seven tests passed in smaller processes.
The original order passed on untouched `develop` in a comparison run. The known
OBS order-sensitive tests ran in their own process.

**Counters Small-Window Layout — manually accepted and integrated.**
The user confirmed manual acceptance of the isolated `0.3.0-alpha` build
from `6bd11c8de4a8b4defc4de92a6e6d304bd25d9d55`. That commit was
fast-forwarded into `develop`; no merge commit was needed. No additional
manual display configurations were reported.

The clipping seen in the earlier packaged acceptance was reproduced in an
offscreen MainWindow with synthetic Counters and Segoe UI. At the existing
`520 × 360` landscape and `420 × 520` portrait logical window minimums,
the Counters area was only `288 × 186` and `374 × 316`, respectively.
The original wide action rows and lack of an overflow viewport compressed
the detail tabs to `0` and `18` pixels high, clipping totals, adjustment and
reset buttons, viewer search/table/actions, and Settings actions. The edit
dialog also forced a `500`-pixel minimum height in that fixture.

The workspace now scrolls without changing the Hub window minimums or its
orientation rules. Related channel actions stay compact, the long reset
actions have separate rows, and the list/detail splitter still resizes. The
Viewer table keeps its semantic columns and native scrollbars, and the
create/edit dialog scrolls its fields while keeping Save and Cancel visible.
Counter storage, calculations, selection reconciliation, and integrations
are unchanged.

Offscreen geometry and interaction checks used 100%, 125%, and 150% simulated
scaling, client sizes derived from 1920×1080, 1600×900, 1366×768, and 1280×720,
and the reduced landscape/portrait sizes above. At the tightest landscape
size the workspace needs both scrollbars and the full-width searches/list
do not fit on screen at once; typing and Counter selection were exercised.
Use about `900 × 600` logical client pixels or larger for more comfortable
use. Before/after captures are under the ignored
`.test-tmp/counters-small-window-layout-review/` directory. The merge review
rechecked all six sizes at each simulated scale without using a live profile
or live Counter values. Native mixed-DPI monitor moves remain unverified
under the separate multi-monitor stabilization issue.

Three new geometry regressions check reachable, non-overlapping actions,
selection stability while resizing and switching tabs, native Viewer column
scrolling and filtering, and reachability of Settings fields and dialog
actions. Final merge validation passed 112 focused tests and 23 subtests;
standalone MainWindow passed 257 tests and 20 subtests. The full suite passed
1,707 tests and 414 subtests in one process, with no failures, errors,
skips, or native exits. There were no Qt lifecycle warnings in the captured
logs. Compilation and `git diff --check` passed.

**Deferred Counters feature — Manually Add Viewer to Counter.**
Allow adding any valid Twitch username to an existing Counter, even if the
viewer has never visited the channel. Resolve the username to a permanent
Twitch user ID, default new entries to zero, and use that ID to prevent
duplicates without overwriting an existing total. Searching Hub's known
viewers may be offered as a convenience. This is backlog only; Hub 0.4
remains in stabilization.

**Qt test lifecycle, Phase 1 — accepted and integrated into `develop`.**
The integrated Phase 1 commit is `09f4013bf7ca591de6d07e8b40bc2e12526d162a`
(fast-forward, no merge commit). Acceptance was based on source review and
automated regression evidence. The isolated `0.3.0-alpha` Windows executable
was built from that commit but not launched. No packaged hands-on or live
Twitch authentication testing was performed; the streaming profile was
untouched. Account worker bookkeeping is the only production change.

The OBS callback tests followed by `test_twitch_account_page.py` aborted
twice on the baseline (Windows exit `0xC0000409`). Running just a
`QCoreApplication` followed by a QWidget produced the same exit and the
explicit Qt diagnostic that a QWidget requires QApplication. The OBS tests
now retain a QApplication and explicitly destroy their stopped service.

Holding a MainWindow reference across the old fixture teardown showed that
`close()` plus `processEvents()` left its native tree valid, including the
Account widget and an active 10-second chatter-history save timer. The
fixture now delivers the window's deferred deletion before removing
disposable storage or restoring patches. Three corrected fixture cycles
destroyed the native tree, restored the global event listeners, and removed
the disposable storage. This is a test-fixture correction, not evidence that
the timer caused the historical access violations.

A gated real Account worker delivered its unbound cleanup lambda on the GUI
thread after the page's native object was destroyed. Runtime cleanup now
lives in the page's existing Qt completion slots, which Qt disconnects at
destruction. Account rendering and generation checks are unchanged. Tests
cover gated completion before, during, and after destruction, stale identity
results, and a pending local avatar delivery. An OBS nested wait test checks
disconnect, reconnect-timer shutdown, late-response rejection, and native
socket and timer destruction; no OBS runtime changes were needed.

The archived Account construction faults show an access violation at network
manager initialization (`0xC0000005`), distinct from the missing-QApplication
abort. This phase's uninterrupted baseline run stopped at MainWindow's
`test_command_context_menu_mirrors_toolbar_for_each_source` without a
retained fault stack. The Run History validation interruption was reported
from earlier runner output. Its JUnit reports show that
`test_nested_qt_event_loop_keeps_ui_events_processing` and
`test_checked_playback_waits_for_its_own_completion_and_keeps_ui_responsive`
did not observe their scheduled UI marker before wait/playback finished;
neither result was a native Qt assertion. A shared cause has not been
established; native Qt stabilization remains open.

The baseline abort is reproducible by passing `test_obs_module.py` before
`test_twitch_account_page.py` to the offscreen test wrapper in a fresh
process. The first Account test was
`test_bot_profile_failure_marks_only_bot_api_as_error`. After the corrections,
the 136-test focused gate passed 68 subtests. Standalone Account and OBS gates
passed 15/4 and 80/50 (tests/subtests). The same 18-case lifecycle sample
passed in reverse order and in Fisher–Yates shuffles (seeds 17, 73, and
20261126), each with four subtests and no skips or native exits.
The standalone MainWindow suite passed 257 tests and 20 subtests. The feature
branch's full gate passed uninterrupted in one process: 1,704 tests and 414
subtests, with zero failures, skips, or native exits. No crash recovery or
test partitioning was needed for that gate. Compilation and diff checks
passed. The two earlier UI-marker failures did not recur in this gate; that
does not establish their cause or close the stabilization investigation.

Final integration revalidation passed the focused lifecycle gate (168 tests,
74 subtests), Account (15/4), OBS (80/50), Counters (49/10), and standalone
MainWindow (257/20). The full gate again passed 1,704 tests and 414 subtests
in one uninterrupted process, with zero failures, errors, skips, or native
exits. The captured logs contained no Qt lifecycle warnings. Python
compilation and `git diff --check` passed. The causes of the historical
`0xC0000005` access violation and wait/audio UI-marker failures remain unknown.
Hub 0.4 stays in stabilization.

Core Automation Pass 2A is complete: Scheduled
Time / Cron, Hotkey, Process Started, Process Stopped, and File Watcher all
passed packaged manual acceptance. Scheduled Time / Cron now uses the
operating system's local timezone rules rather than Qt's incorrect fallback
for an unmapped Windows timezone.

**Core Automation Pass 2B — complete and merged.** HTTP Request, Keyboard
Press, and Stop Audio passed packaged Windows acceptance. Stop Audio was
verified with a silent Hub clip; isolation from another actively playing
application was covered by automated ownership tests, not a live audio test.
Hub does not schedule HTTP retries, but Qt may resend eligible requests after
some connection failures. The Wiki warns against assuming exactly-once delivery.

The planned 0.4 Core Automation implementation is complete: Core If Trigger
and nested If/Else Tasks, Comment, Custom Event and Send Custom Event, Set
Trigger State, Log Message, the Pass 2A triggers above, and the three Pass 2B
tasks. Final regression validation passed 1,645 tests and 410 subtests.

**Twitch Automation Pass 1 — accepted and merged into `develop`.**
All 16 new triggers are implemented. The
[Twitch trigger reference](hub/twitch.md#stream-and-lifecycle-events-04)
documents EventSub versions, broadcaster scopes, filters, and Variables.
Functional manual acceptance is complete: the existing Redemption Added
trigger still worked, and Redemption Updated received real Twitch events.
Final validation passed 313 focused tests and 46 subtests, and the complete
split gate passed 1,669 tests and 410 subtests with no skips. One native Qt
runner crash during Account page setup was recovered by rerunning the
affected partition in smaller processes.

**Core Queue Stability & Controls — complete and merged into `develop`.**
During acceptance, a burst of 109 Redemption Updated events invoked a
routine containing Core Play Audio with an `.ogg` file. Hub became temporarily
unresponsive, then recovered; all 109 routine executions were observed.
Headless bursts of 25, 100, and 200 requests reproduced inline execution ahead
of queued Qt callbacks. Review also found full Run History table rebuilds for
each result. Neither proves the cause of the original live freeze; the live
burst was not repeated, and the contribution of `.ogg` playback is unknown.
Live EventSub now enqueues work instead of running it inline. The existing Qt
timer starts one ready routine per tick and rotates across queues, giving Qt
time between executions. Run History batches updates and adds rows incrementally.
Unassigned or unavailable queue references fall back to the protected Default
Queue. New or unconfigured queues default to one active routine, 25 pending
items, and Reject New; explicit saved limits are preserved. Pause/Resume,
Clear Pending, and cooperative Stop All manage work without crossing queue
boundaries. Drop Oldest Pending and Keep Latest Pending are optional. Queue
counters account for discarded work and Run History retains recent outcomes.
Pending work is not replayed after restart.
The scheduler does not preempt blocking tasks or limit playback after a
non-waiting Play Audio task completes.
Final validation passed 15 focused tests and 1,684 full-suite tests with 410
subtests, no failures or skips, and no native Qt runner crashes. The suite
included 25-, 100-, and 200-event Log Message and mocked Play Audio bursts.
The user confirmed packaged manual acceptance of queue controls, overflow,
accounting, restart behavior, and responsiveness under controlled bursts.

**Run History Variable Inspector — implemented and merged into `develop`.**
Run Details → Variables shows execution-scoped Initial and Final snapshots
of materialized values, with known origins, filtering, comparison, and safe
copy. Final includes outputs from Tasks, nested If tasks, and nested Routines.
No Variable provider is evaluated by the inspector. Each view is limited to
96 Variables, 2,048 characters per value, and 16,384 characters total. Run
History still retains the latest 200 entries, for the current session only.
Private chat content, HTTP response bodies, and detected credentials or copies
are excluded from retained values; arbitrary unlabeled or transformed secrets
are not reliably detectable.

Final validation: 90 focused tests and 10 subtests; full headless coverage of
1,696 tests and 410 subtests, with no unresolved failures or skips. Controlled
25-, 100-, and 200-execution bursts verified event-loop progress, incremental
updates, and retention. After one native MainWindow runner interruption and
two Qt timer assertion failures (Core Wait and waited audio), all affected
tests passed on isolated reruns.
The previously order-sensitive OBS callback tests passed in a dedicated
process. The actual Open Details workflow passed offscreen with disposable
routines. Merge was authorized; a dedicated packaged hands-on pass was not
reported.

The shared Automation Dependency Resolver, portable Routine sharing,
improved Routine import/export, prebuilt Automation templates, and their
associated Backup and Transfer changes are deferred to 0.5.

The accepted OBS module conversion is integrated into `develop`. It provides:

- Core module contribution registries and unavailable-provider behavior;
- bundled OBS module ID `streamhouse.obs`;
- OBS service/configuration/trigger/Variable/action/tab/Backup lifecycle ownership;
- preservation of public paths, credentials, stable task/trigger IDs, and routines;
- removal of OBS runtime construction and shutdown from `MainWindow`; and
- a packaged discovery fix that registers trusted embedded modules before
  Automation UI consumes their static catalogs.

OBS and Twitch are accepted bundled Hub modules. Twitch conversion is
**IMPLEMENTED / ACCEPTED** at feature code commit `dba19eb5` on 2026-10-27 and
integrated into `develop` by merge commit `a018ae4`.
Static task/trigger/Variable metadata remains available when the
module is disabled. When enabled, `streamhouse.twitch` solely constructs and
owns broadcaster/bot auth, protected-token maintenance policy, `TwitchService`,
Helix/EventSub/chat transport lifecycle, trigger correlation, and shutdown.
The Twitch workspace is module-contributed, Commands is contributed under
Automation while its store/runtime remain Twitch-owned, Counters is presented
under Core Automation, and Twitch data enters Backup through module
contributors. Routine token maintenance does not rebuild transports. Native
EventSub session transfers remain globally connected; unexpected interruptions
enter a concise Reconnecting state and use one owned retry timer. Raw transport
details remain in Logs and are not appended to Chat.

The Twitch workspace now opens with a module-owned **Account** tab. It provides
the primary broadcaster and optional bot connect, reauthorize, identity/avatar,
status, independent disconnect controls, and compact event-driven Connection
Health rows over the existing auth, Helix profile, EventSub, and chat state.
The management tab order is Account, Raid, Channel Points, Channel Information,
Users, and Analytics; Twitch authentication is no longer presented on the
hidden Core Connections shell.

## Completed milestones

- Core Live workspace docking is implemented with module-contributed panels,
  Qt layout persistence/reset, safe missing-provider restoration, and Core
  Quick Actions that run existing routines through Automation. Quick Actions
  now use a compact square-tile grid that follows dock orientation, persists
  four accessibility size presets and stable drag order, supports simple color
  and built-in icon customization, and preserves unavailable routine links for
  repair. Their dock minimum follows one tile at the selected size, action count
  wraps without enlarging that minimum, and dock visibility reflows without Qt
  animation stalls or a reserved empty center band. Hiding Quick Actions gives
  its freed height to the middle row without shifting that row downward.
  Preset and orientation changes auto-fit only the dock's tile-sized thickness;
  ordinary layout and manual resizing are not continuously overridden.
  Quick Actions uses native Qt dock roots: the default is a real full-width
  bottom strip, left/right placements own their corresponding corners, and Qt
  alone owns drag/drop and nested split construction.
  Schema-v1 Quick Actions upgrade to schema v2 without changing stable
  action/routine IDs or order. Twitch's live
  widgets are reparented into docks rather than duplicated; its provider tab
  remains for deeper Twitch management. Docks tile instead of tab-stacking, the
  default layout prioritizes Chat, and compact separators preserve practical
  resizing. Core navigation now orders Dashboard, Live, Automation, and enabled
  module tabs above bottom-pinned Wiki and Settings. Final acceptance passed 46
  focused tests plus 2 subtests and 238 MainWindow tests plus 7 subtests; the
  normal Windows package built and passed isolated offscreen startup/shutdown.

- Public Hub tags `v0.1.0-alpha`, `v0.2.0-alpha`, and `v0.2.1-alpha` exist.
- 0.3 development metadata is `0.3.0-alpha`.
- Core modular foundation committed at `2d8541e`.
- OBS runtime conversion committed through `46a5d1a`.
- Packaged OBS discovery/startup regression fixed at `2517ce9`.
- Packaged smoke startup now exits cleanly with OBS enabled and disabled.
- Final OBS acceptance passed at feature HEAD `f3db6f8`; the conversion is
  approved and integrated into `develop`.
- Contextual `obs.*` event Variables now use the provider-appropriate
  `OBS Context` source label while Twitch context labels remain unchanged.
- Automation routine groups can be reordered by drag-and-drop or their context
  menu, with the custom-group order persisted by stable group identity.
- Outgoing raids now expose a distinct **Raid Out Started** Automation trigger,
  and optional target-browser navigation begins as soon as Twitch accepts the
  Start Raid request instead of waiting for EventSub confirmation.
- Twitch Keyword / Phrase triggers now match the visible text of `/me` action
  messages while preserving action presentation metadata and excluding actions
  from command invocation.
- Twitch Keyword / Phrase triggers now expose per-trigger **Ignore bot
  messages**, enabled by default and backward-compatible for existing saved
  triggers. Explicitly opting one trigger in allows known bot messages without
  enabling bot Chat Commands or changing other trigger behavior.
- Hub chat now supports canonical global/provider Variables through the shared
  Variable Picker. Values resolve only at send time, while unavailable tokens
  remain visible and contextual Automation values stay excluded.
- `{stream.uptime}` reports elapsed Twitch livestream time from the existing
  Live overview state, independently of `{hub.uptime}`, without new polling.
- Picker-backed Automation editors and Live chat now suggest Variables when
  typing `{`, using the same field-specific catalog without resolving values.
- The built-in Wiki now has 28 Automation recipes in Beginner, Intermediate,
  and Advanced order. Existing examples were audited against current tasks,
  triggers, and Variables; all existing recipe IDs were preserved.
- Twitch Commands can list external commands in `!commands` without creating
  Hub triggers, routines, or chat responses for those commands.
- Live Chat Helper shares the Variable popup for Twitch slash actions, enabled
  registered commands, and known-user mentions. Broadcaster mentions are
  highlighted on new rows by default, with a saved Chat setting to disable it.
  User hands-on acceptance passed.
- Core Automation pass 1 adds the Custom Event trigger, Send Custom Event,
  Set Trigger State, and Log Message. Custom Events use normal routine
  queues and isolated contextual Variables; imported trigger-state tasks
  require explicit relinking. Isolated Windows acceptance passed, including
  explicit Enable, Disable, Toggle, and the restored disabled state shown in
  Automation after a complete restart.
- OBS-triggered routines are queued after the originating WebSocket event
  callback returns, so their OBS tasks can complete requests without reentering
  the socket callback or requiring an artificial Wait task. Real OBS acceptance
  on 2026-11-06 repeatedly verified a `CurrentProgramSceneChanged` routine with
  show/hide/toggle-source requests and no `GetSceneItemId` timeout.
- Routine deletion now preserves the routine-list position and selects the next
  nearby routine instead of rebuilding back at the top.
- Core If is now an
  expandable task-tree container with reusable AND/OR condition rows, real
  nested branch task lists, ELSE IF chaining, and cross-branch drag-and-drop.
  Released single-condition tasks normalize in place without changing IDs or
  the routine schema. The same builder now powers the Core **If** trigger, which
  watches global Variable dependencies without polling and starts routines only
  on configured Becomes True, Becomes False, or Changes transitions. Initial and
  re-enabled states establish a no-fire baseline. Continue-Routine branch
  controls remain intentionally deferred.
- Counter Variables now expose scope-aware `.unit` values for channel lifetime,
  current stream, viewer lifetime, and viewer current-stream totals while
  preserving the existing numeric Variables.
- The Twitch Users page no longer forces a full unchanged table rebuild whenever
  it is shown, existing Twitch and OBS trigger event types are fixed while
  editing, and Dev Tools test viewers use stable distinct identities so First
  Message can be simulated without weakening production eligibility rules.
- Channel-snapshot viewer-list refreshes now run on the Qt GUI thread, suppress
  transient selection callbacks while native list items are rebuilt, coalesce
  reentrant requests, and ignore shutdown/deleted-widget callbacks. This closes
  the unsafe nested-event-loop path exposed by Automation task context menus.
- Twitch reconnect/renewal now distinguishes native EventSub transfer from an
  unexpected interruption, deduplicates recovery work, preserves pending event
  correlation, and cancels late reconnect/auth work during shutdown or sign-out.
- Outgoing chat using a configured Twitch bot now recovers a rejected bot token
  independently: an HTTP 401 performs one serialized refresh and one retry with
  the same bot identity. Refresh failure or a second 401 stops without a loop,
  leaves broadcaster auth untouched, and marks bot authentication as requiring
  attention. Deterministic service/auth coverage includes concurrent sends;
  real credentials were not invalidated to force a live 401.
- Final Twitch module acceptance passed for the code at `dba19eb5`, including
  enabled, disabled, and same-data-root re-enabled packaged startup.

## Accepted module conversions

### OBS module conversion — **IMPLEMENTED / ACCEPTED**

Completed ownership changes are described in
[ARCHITECTURE.md](ARCHITECTURE.md#obs-module-conversion). Final acceptance verified:

- packaged Hub startup and catalog-before-UI ordering;
- enabled, disabled, reconnect, and re-enable lifecycle behavior;
- existing Automation task compatibility and trigger regression coverage;
- canonical OBS Variables and unavailable-provider behavior;
- safe Backup/Restore contribution and retained Default Audio Input behavior;
- clean reverse-order shutdown; and
- one module-owned OBS runtime with no duplicate legacy runtime.

Non-blocking follow-ups remain:

- Core Automation editor/simulator/transfer/Wiki adapters retain static OBS
  contract imports. They do not duplicate runtime ownership or create
  module-to-module coupling, but can become provider-neutral where justified.
- There is no user-facing Modules management page yet.

No OBS persistence schema migration was introduced. Existing
`obs/connection.json`, `obs/password.dat`, and `obs/triggers.json` remain authoritative.

### Twitch module conversion — **IMPLEMENTED / ACCEPTED**

Completed ownership changes are described in
[ARCHITECTURE.md](ARCHITECTURE.md#twitch-module-conversion). Final acceptance on
2026-10-27 verified:

- one module-owned broadcaster/bot auth, Helix, EventSub/chat, command, trigger,
  Variable, timer/worker, reconnect, Backup, UI-contribution, and shutdown path;
- static catalogs and persisted Automation definitions remain interpretable
  while Twitch is disabled, without starting auth or network runtime;
- packaged startup succeeds enabled, disabled, and after re-enable against the
  same isolated data root;
- Commands remains Twitch-owned but is contributed under Automation; Counters
  remains Core-owned under Automation;
- canonical task, trigger, command, Variable, credential, path, and routine
  contracts are unchanged; and
- native and unexpected reconnect paths are deduplicated, quiet in Chat, and
  retain detailed redacted diagnostics in Logs.

Non-blocking presentation debt remains: `MainWindow` supplies the existing
Twitch workspace/Commands widget factories and exposes module timer aliases to
UI/tests. It does not construct or start a parallel Twitch service, store,
transport, timer, worker, or shutdown path. Core Automation simulator,
transfer, Wiki, and editor adapters may remain aware of stable public Twitch
contracts until provider-neutral abstractions have a concrete consumer.

Validation limitations: disruptive live raid, moderation, ads, Channel Point,
and real token-expiry actions were not forced against a real channel. Their
focused integration/regression coverage passed. Windows UI automation was
unavailable during final acceptance, so packaged UI structure/navigation was
validated through the frozen startup and Qt UI suites rather than a visual
click-through.

## OBS startup regression finding

The reported error was:

```text
ValueError: The OBS trigger provider catalog is unavailable.
MainWindow.__init__ -> _build_automation_page -> AutomationPage.__init__
```

**Current status: FIXED and packaged-reproduced at commit
`2517ce9ee40f27aeeb04a419a9fb707af68dea9a`.** The packaged failure occurred
before catalog registration: PyInstaller embedded Python packages but did not
materialize `products/hub/modules/bundled/obs`, so directory-based discovery
returned no OBS module. Catalog registration therefore had nothing to register.
Frozen startup now follows the verified order: trusted frozen discovery, OBS
catalog registration, `AutomationPage` construction, then enabled contribution
registration/configuration/startup. Development discovery remains directory-based
and does not load arbitrary code.

Reproduction/validation requirements:

1. Build with `tools/build/build_hub.ps1`.
2. Launch with an isolated `STREAMHOUSE_DATA_DIR` and smoke-test mode.
3. Verify exit code 0 with default/enabled OBS.
4. Persist `streamhouse.obs: false`, restart, and verify exit code 0.
5. In tests, verify enabled startup contributes the OBS tab and starts runtime;
   disabled startup still contributes task/trigger catalogs but does not start runtime.

## Known issues and validation gaps

- **Resolved: Connections scrollbar regression test.**
  `MainWindowTests.test_connections_body_scrolls_instead_of_compressing_health_rows`
  previously tested the legacy Connections shell, where the visible Music
  Player group fit the viewport and the old Twitch health widgets were hidden.
  The regression now tests the live broadcaster and bot cards in Twitch →
  Account across repeated resizes, and tests the Connections shell separately
  with a deliberately constrained viewport. Production scrolling already
  behaved correctly; no production code or connection behavior changed.
  Headless validation passed: the isolated regression (1 test,
  5 subtests), the focused gate (58 tests, 16 subtests), and the full suite
  (1,568 tests, 387 subtests, zero failures). Manual acceptance was explicitly
  waived, not performed, because the correction is limited to tests and docs.
- **Resolved Backup dependency issue:** automatic and manual Routine Backup now
  preserve a routine with an explicitly inventoried unresolved trigger instead
  of abandoning all eligible user data. Restore validates the unresolved
  inventory exactly, and disabled Twitch/OBS trigger stores remain discoverable
  through static module Backup contributions. The observed stale reference
  `56dfb338dda549629707d76639def744` belongs to routine `Ban User`; its original
  trigger survives only in a quarantined Twitch store while the current routine
  also retains a valid replacement trigger.
- **Resolved Counter test cleanup issue:**
  `MainWindowTests.test_counters_page_refreshes_for_stream_and_counter_changes`
  no longer registers a per-Counter unittest cleanup that ran after the fixture
  had already removed its entire temporary data root. Production deletion
  remains intentionally strict for unknown Counter IDs.
- **Resolved Users Counter worker test timeouts:** the Users UI worker waits now
  pump queued GUI completions while yielding Python execution to the serialized
  Counter worker. They no longer use nested Qt waits that fire the page's
  periodic refresh timer and add load-dependent Counter reads during a write.
- **Fixed daily-memory shutdown leak:** closing Hub now stops the daily-memory
  expiry timer. A closed window could otherwise emit late AI expiry logs into
  subsequent tests. A focused shutdown regression verifies that the timer
  stops without changing memory expiry while Hub is open.
- **Architecture debt:** `MainWindow` remains large, and Core Automation UI
  contains provider-specific editor/presentation adapters.

## Latest validation record

| Check | Result |
| --- | --- |
| OBS/module/Automation/Backup/package-boundary slice | 165 passed, 64 subtests passed |
| MainWindow | 200 passed, 6 subtests passed in the restricted run |
| Restricted-loopback rerun | 7 passed with loopback access |
| Unique relevant acceptance total | 372 passed, 70 subtests passed |
| Packaged Hub/AI smoke | Passed |
| Packaged startup, OBS disabled | Exit code 0 |
| Packaged startup, OBS re-enabled | Exit code 0 |
| Twitch Phase 3 module/Backup/package-boundary slice | 47 passed, 4 subtests passed |
| Twitch Phase 3 UI/Automation slice | 341 passed, 32 subtests passed; one unrelated Counter cleanup failure |
| Twitch Phase 3 full gate | 1,378 passed, 373 subtests passed, one unrelated test deselected; logger-isolation failure passed alone |
| Packaged Twitch enabled / disabled smoke | Exit code 0 / 0 using isolated data roots |
| Twitch reconnect/auth/EventSub/module slice | 264 passed, 98 subtests passed |
| Twitch reconnect MainWindow slice | 41 passed, 5 subtests passed; corrected focused rerun passed |
| Twitch reconnect full gate | 1,390 passed, 374 subtests passed; one documented unrelated Counter cleanup failure |
| Packaged reconnect build, Twitch enabled / disabled | Exit code 0 / 0 using isolated data roots; normal `dist` remained externally locked, so the same spec was built under ignored `.test-tmp` |
| Final Twitch acceptance suite | 524 passed, 195 subtests passed |
| Final Twitch MainWindow acceptance slice | 95 passed, 7 subtests passed; one documented unrelated Counter worker/cleanup failure |
| Final packaged Twitch enabled / disabled / re-enabled | Exit code 0 / 0 / 0 using isolated data roots |
| Post-merge Twitch/module/Automation/Backup/Variables/OBS gate | 404 passed, 138 subtests passed |
| Post-merge MainWindow/startup slice | 55 passed, 5 subtests passed |
| Post-merge packaged smoke | Exit code 0 with Twitch re-enabled |
| OBS event/request reentrancy acceptance | Automated OBS/Automation gate passed; repeated real `CurrentProgramSceneChanged` show/hide/toggle-source routines completed without request timeout |
| Hub smoke-maintenance focused/broader gate | Users suite passed in five consecutive runs (100 tests, 125 subtests total); Users/Counter gate passed 67 tests and 35 subtests; relevant MainWindow slice passed 7 tests unrestricted after the known restricted QtWebEngine breakpoint |
| Hub smoke-maintenance package | Normal Hub build and automated packaged startup/shutdown smoke passed; the follow-up visual click-through remained blocked by an already-running installed single instance and was not forced |
| Final Live focused acceptance | 46 passed, 2 subtests passed |
| Final Live MainWindow acceptance | 238 passed, 7 subtests passed |
| Final Live package | Normal build passed; isolated offscreen startup/shutdown exited 0 with no fatal log, dump, Hub, or QtWebEngine remainder |
| 0.3 release-prep gate | 43 passed, 2 subtests passed across release/package-boundary and Dashboard coverage |
| 0.3 release-prep package | Normal Hub build produced `0.3.0-alpha`; packaged offscreen startup/shutdown smoke passed with no Hub or QtWebEngine remainder |
| Python compilation | Passed for `products/hub` and `shared` |
| `git diff --check` | Passed |

## Next recommended steps

1. Complete the public-Alpha release checklist against the exact release
   commit, including previous-version upgrade and installer verification.
2. Tag and publish `v0.3.0-alpha` as a GitHub pre-release only after those
   checks pass.
3. Keep Modules-page work, user-controlled navigation ordering, and additional
   provider Live panels outside the frozen 0.3 scope.

## Maintenance rules

- Update this file after meaningful implementation, validation, release, branch,
  or blocker changes—not after trivial internal edits.
- Record exact dates, branches, commits, and commands/results when available.
- Never convert a plan into “implemented” without code evidence.
- Never claim a test passed if it was not executed or explicitly supported by a result.
- Move durable architecture facts into [ARCHITECTURE.md](ARCHITECTURE.md); keep
  this file focused on the current handoff.
