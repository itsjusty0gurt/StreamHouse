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
   Hub module. Its conversion passed final acceptance and is integrated into the
   stable 0.3 `develop` line without changing public OBS data contracts.
4. **Active branch:** `develop`; the accepted conversion came from
   `feature/obs-module-conversion` at acceptance HEAD `f3db6f8`.
5. **Most important unresolved issues:** routine groups cannot be reordered;
   outgoing raids lack a distinct `Raid Out Started` trigger; raid landing
   navigation starts too late; normal Twitch reconnect/renewal can create
   visible interruption or chat noise; and one Windows forced-termination test
   currently retains a crash-log handle. Contextual `obs.*` event Variables also
   use the generic `Twitch Context` source label despite correct OBS context behavior.
6. **Latest OBS acceptance:** 372 unique relevant tests and 70 subtests passed;
   packaged Hub/AI smoke passed; packaged startup exited 0 with OBS disabled and
   again after re-enabling; compilation and `git diff --check` passed.
7. **Immediate next steps:** address the four confirmed Hub issues below;
   investigate the independent Windows forced-termination test; correct the
   non-blocking OBS Variable source label; and consider provider-neutral
   presentation adapters only where they materially simplify future modules.
8. **Read next:** [master reference](STREAMHOUSE_MASTER_REFERENCE.md),
   [architecture](ARCHITECTURE.md), [0.3 foundation](architecture/hub-0.3-foundation.md),
   [implementation map](architecture/overview.md), and
   [development policy](architecture/development-policy.md).

## Snapshot

| Item | Verified value |
| --- | --- |
| Date | 2026-10-25 (America/Edmonton) |
| Branch | `develop` after accepted feature integration |
| Accepted feature HEAD | `f3db6f8fa2ce22603cf974344a78144a56bcd2ff` |
| Feature upstream | synchronized with `origin/feature/obs-module-conversion` before acceptance documentation |
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

OBS is the first accepted bundled Hub module. No Twitch or Music conversion has begun.

## Completed milestones

- Public Hub tags `v0.1.0-alpha`, `v0.2.0-alpha`, and `v0.2.1-alpha` exist.
- 0.3 development metadata is `0.3.0-alpha`.
- Core modular foundation committed at `2d8541e`.
- OBS runtime conversion committed through `46a5d1a`.
- Packaged OBS discovery/startup regression fixed at `2517ce9`.
- Packaged smoke startup now exits cleanly with OBS enabled and disabled.
- Final OBS acceptance passed at feature HEAD `f3db6f8`; the conversion is
  approved and integrated into `develop`.

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
- Contextual `obs.*` event Variables use the generic `Twitch Context` source
  label in the Variables table; context, availability, and resolution are correct.
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

- **Routine groups cannot be reordered:** the Automation/Routines UI needs to
  let users reorder routine groups and persist that presentation order without
  changing stable routine or group identities.
- **Missing `Raid Out Started` trigger:** Hub needs a distinct Twitch Automation
  trigger for the moment the user confirms an outgoing raid action in Hub. It
  must remain separate from later raid-completed or Twitch-confirmed states;
  no additional Twitch API guarantee is assumed.
- **Raid landing navigation starts too late:** after the user selects a target
  and confirms the outgoing raid, Hub should immediately begin loading that
  channel in the raid landing webview/popout. The Twitch countdown and later
  confirmation continue independently so the target stream and chat can be
  ready before transfer.
- **Twitch reconnect should be seamless — verify/fix:** periodic reconnect or
  renewal can create visible interruption or chat noise. Determine whether the
  source is OAuth refresh, EventSub/WebSocket lifecycle, chat transport, or
  another layer before choosing a fix. Normal recovery should be fast and quiet
  in chat while retaining useful diagnostics in logs.
- **OBS Variable source label:** contextual `obs.*` event Variables currently
  show the generic `Twitch Context` source label. This is a presentation defect;
  their actual OBS context, status, availability, and resolution are correct.
- **Known test issue:**
  `WindowsInstanceMutexTests.test_forced_termination_releases_lock_and_preserves_abnormal_marker`
  currently fails because the force-terminated helper retains the named mutex
  and a crash-log handle long enough to block immediate reacquisition/cleanup.
  It reproduced outside the restricted sandbox and is unrelated to the OBS diff.
- **Validation gap:** the complete 1,256-test Hub/release command did not finish
  green because of that single Windows process-lifetime test. Do not report a
  fully green gate until it is resolved or independently proven environmental.
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
| Python compilation | Passed for `products/hub` and `shared` |
| `git diff --check` before acceptance documentation | Passed |

## Next recommended steps

1. Diagnose the Windows forced-termination/mutex crash-log handle issue separately.
2. Address the four active Hub behavior issues in the handoff.
3. Correct the non-blocking OBS Variable source-label presentation defect.
4. Make static OBS presentation adapters provider-neutral only where another
   module or a concrete maintenance benefit justifies the abstraction.
5. Consider Modules-page, navigation, or docking work as separate scoped changes.

## Maintenance rules

- Update this file after meaningful implementation, validation, release, branch,
  or blocker changes—not after trivial internal edits.
- Record exact dates, branches, commits, and commands/results when available.
- Never convert a plan into “implemented” without code evidence.
- Never claim a test passed if it was not executed or explicitly supported by a result.
- Move durable architecture facts into [ARCHITECTURE.md](ARCHITECTURE.md); keep
  this file focused on the current handoff.
