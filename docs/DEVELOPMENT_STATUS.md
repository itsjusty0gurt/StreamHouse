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

1. Counters UI native access violation.
2. Historical viewer-memory native crashes.
3. Intermittent OBS/Qt test-order crashes.
4. Remaining audio/UI blocking risks.
5. Twitch reconnect and EventSub lifecycle verification.
6. Dynamic window resizing.
7. Twitch Ad Manager reliability.
8. Other confirmed regressions.

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
