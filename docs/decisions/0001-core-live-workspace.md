# ADR-0001: Core owns the dockable Live workspace

- Date: 2026-10-30
- Status: Accepted

## Context

Hub needs a workspace for panels used while actively streaming. Twitch already
has a working Chat experience, while future YouTube, Kick, Broadcaster, and
other providers must coexist without importing one another or creating duplicate
connections. Normal navigation pages remain appropriate for configuration and
authoring, so docking must supplement rather than replace the main navigation.

## Decision

Hub Core owns a **Live** navigation page, its Qt dock host, contribution
registry, layout persistence, visibility controls, and reset behavior. Modules
own their panel widgets and provider behavior and contribute those widgets
through stable dock IDs. The initial Twitch panels reuse the existing Chat,
Activity, Chatters, and Stream Status widgets. Core also owns a small Quick
Actions panel whose buttons reference stable routine IDs and execute through
the normal Automation service.

## Rationale

Qt docking supplies mature rearrange, float, show/hide, and state-serialization
behavior. A Core registry keeps modules independent and preserves one runtime
per provider. Reparenting existing Twitch widgets avoids a second chat UI and
keeps their established event and thread boundaries intact.

## Alternatives considered

- Keep Chat only in Twitch: rejected because it cannot form a multi-provider
  live-use workspace.
- Copy Twitch Chat into a new Core page: rejected because Core would gain
  Twitch-specific behavior and two implementations would diverge.
- Build a custom docking engine: rejected because Qt already provides the
  required behavior and persistence identity.

## Consequences

Dock IDs and Quick Action IDs are stable persistence identities. Disabled or
missing modules do not prevent Live from opening; saved state referencing their
docks is not overwritten. Twitch's main tab remains available for deeper
provider controls, but its live Chat presentation now lives in Live. Navigation
reordering, unified chat, Deck hardware, and additional providers remain
separate work.

## Affected components

- `products/hub/modules/contributions.py`
- `products/hub/ui/live_workspace.py`
- `products/hub/automation/quick_actions.py`
- `products/hub/ui/main_window.py`
- `automation/quick_actions.json` in the Hub user-data root

## Validation status

Focused module, persistence, Live workspace, MainWindow, Automation, packaged
build, and manual smoke results are recorded in the implementing task and Git
commit.
