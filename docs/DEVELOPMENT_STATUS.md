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
3. **Current architecture milestone:** OBS is the first accepted real bundled
   Hub module. Twitch conversion Phase 3 now has trusted discovery, static
   catalogs, module-owned auth/transport lifecycle, module-contributed UI and
   Commands, and Twitch Backup contributions.
4. **Active branch:** `feature/twitch-module-conversion`; Phase 3 started from
   Twitch runtime-lifecycle HEAD `c5319fceb3b1251d29ee0aa36f7066cf7a4ea3f9`.
5. **Most important unresolved issues:** normal Twitch reconnect/renewal can
   create visible interruption or chat noise, and one Windows
   forced-termination test currently retains a crash-log handle.
6. **Latest OBS acceptance:** 372 unique relevant tests and 70 subtests passed;
   packaged Hub/AI smoke passed; packaged startup exited 0 with OBS disabled and
   again after re-enabling; compilation and `git diff --check` passed.
7. **Immediate next steps:** complete packaged Phase 3 acceptance, then address
   quiet/seamless Twitch reconnect behavior separately; investigate the
   independent Windows forced-termination test as its own task.
8. **Read next:** [master reference](STREAMHOUSE_MASTER_REFERENCE.md),
   [architecture](ARCHITECTURE.md), [0.3 foundation](architecture/hub-0.3-foundation.md),
   [implementation map](architecture/overview.md), and
   [development policy](architecture/development-policy.md).

## Snapshot

| Item | Verified value |
| --- | --- |
| Date | 2026-10-27 (America/Edmonton) |
| Branch | `feature/twitch-module-conversion` from `develop` at `94f4c78` |
| Twitch Phase 3 starting HEAD | `c5319fceb3b1251d29ee0aa36f7066cf7a4ea3f9` |
| Feature upstream | `origin/feature/twitch-module-conversion` |
| Working tree at task start | clean |
| Hub development version | `0.3.0-alpha` / Windows `0.3.0.0` |
| Pre-merge integration baseline | `develop` at `a91f7850e133b6a19240a61938d18b0ca3a0621b` |
| Latest published Hub tag | `v0.2.1-alpha` at `b1c25a21ef34de8029d6216eb96c47274c2a4c58` |

## Active development focus

The accepted OBS module conversion is integrated into `develop`. It provides:

- Core module contribution registries and unavailable-provider behavior;
- bundled OBS module ID `streamhouse.obs`;
- OBS service/configuration/trigger/Variable/action/tab/Backup lifecycle ownership;
- preservation of public paths, credentials, stable task/trigger IDs, and routines;
- removal of OBS runtime construction and shutdown from `MainWindow`; and
- a packaged discovery fix that registers trusted embedded modules before
  Automation UI consumes their static catalogs.

OBS is the first accepted bundled Hub module. Twitch conversion is now
**PARTIAL — STATIC CATALOGS + RUNTIME + UI/COMMANDS/BACKUP MODULE-OWNED** on its dedicated
feature branch. Static task/trigger/Variable metadata remains available when the
module is disabled. When enabled, `streamhouse.twitch` solely constructs and
owns broadcaster/bot auth, protected-token maintenance policy, `TwitchService`,
Helix/EventSub/chat transport lifecycle, trigger correlation, and shutdown.
The Twitch workspace is module-contributed, Commands is contributed under
Automation while its store/runtime remain Twitch-owned, Counters is presented
under Core Automation, and Twitch data enters Backup through module
contributors. Reconnect behavior is intentionally unchanged pending focused
cleanup and final acceptance.

## Completed milestones

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

- **Twitch reconnect should be seamless — verify/fix:** periodic reconnect or
  renewal can create visible interruption or chat noise. Determine whether the
  source is OAuth refresh, EventSub/WebSocket lifecycle, chat transport, or
  another layer before choosing a fix. Normal recovery should be fast and quiet
  in chat while retaining useful diagnostics in logs.
- **Known test issue:**
  `MainWindowTests.test_counters_page_refreshes_for_stream_and_counter_changes`
  currently raises `KeyError` during test cleanup after its Counter definition
  has already been removed. It predates and is unrelated to Twitch Phase 3.
- **Known test isolation issue:** the complete gate can deliver late AI
  memory-expiry log callbacks into the Python-script logger mock. The affected
  Python-script logging test passes in isolation; no assertion was weakened.
- **Acceptance limitation:** no preexisting live OBS trigger was available for
  nondestructive manual firing. Automated persistence, matching, filtering,
  context, and MainWindow dispatch coverage passed.
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
| Python compilation | Passed for `products/hub` and `shared` |
| `git diff --check` | Passed |

## Next recommended steps

1. Complete final Twitch conversion acceptance, then address quiet/seamless
   reconnect and renewal behavior as a separate task.
2. Diagnose the independent Counter cleanup and logger-isolation tests.
3. Make static OBS presentation adapters provider-neutral only where another
   module or a concrete maintenance benefit justifies the abstraction.
4. Consider Modules-page, navigation, or docking work as separate scoped changes.

## Maintenance rules

- Update this file after meaningful implementation, validation, release, branch,
  or blocker changes—not after trivial internal edits.
- Record exact dates, branches, commits, and commands/results when available.
- Never convert a plan into “implemented” without code evidence.
- Never claim a test passed if it was not executed or explicitly supported by a result.
- Move durable architecture facts into [ARCHITECTURE.md](ARCHITECTURE.md); keep
  this file focused on the current handoff.
