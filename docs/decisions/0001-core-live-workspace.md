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
Activity, Chatters, and Stream Status widgets. Core also owns a compact Quick
Actions grid whose square tiles reference stable routine IDs and execute through
the normal Automation service. The grid follows dock orientation, while tile
size, logical order, labels, optional built-in icons, and colors are persisted
independently of Qt dock geometry.

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
provider controls, but its live Chat presentation now lives in Live. Core gives
Live a fixed primary-navigation position after Dashboard; user-controlled
navigation reordering, unified chat, Deck hardware, and additional providers
remain separate work.

Quick Actions schema v2 is the current runtime format. Schema-v1 files are an
upgrade input: their stable action IDs, routine IDs, labels, and list order are
preserved while the global medium tile size and empty color/icon customization
defaults are added. Missing routines do not delete or repoint tiles; the tile is
shown as unavailable and remains editable or removable.

Quick Actions constrain only the dock dimension perpendicular to their flow:
top/bottom docks require one current-size tile of height, while left/right docks
require one current-size tile of width. Action count affects wrapping, not the
dock minimum. Live disables Qt dock animations so visibility and placement
changes reflow the dock tree immediately, including WebEngine-backed panels.
The built-in default is one nested Qt dock splitter tree rather than separate
top/left/right/bottom area roots; this preserves the full-width top and bottom
rows without leaving an empty central drop band when a row is hidden. Hiding
Quick Actions assigns its released height to the middle row while preserving
the top row's current user-adjusted height.

## Affected components

- `products/hub/modules/contributions.py`
- `products/hub/ui/live_workspace.py`
- `products/hub/automation/quick_actions.py`
- `products/hub/ui/main_window.py`
- `automation/quick_actions.json` in the Hub user-data root

## Validation status

Final feature-branch acceptance on 2026-11-04 passed 46 focused tests plus 2
subtests and 238 MainWindow tests plus 7 subtests. The normal Windows package
built successfully and exited 0 in isolated offscreen startup/shutdown smoke.
Earlier packaged acceptance covered the requested default layout, reset,
custom-layout persistence, and clean shutdown; final acceptance did not repeat a
desktop visual pass.
