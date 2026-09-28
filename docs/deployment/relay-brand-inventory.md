# Relay and Twitch Extension legacy-brand inventory

This inventory classifies the repository-wide, case-insensitive Sally search
performed for the Streamhouse relay migration. It distinguishes infrastructure
compatibility from the Sally AI character and Sally's separate Twitch bot.

## Migrated infrastructure branding

- `render.yaml`: service and environment keys migrated to
  `streamhouse-soundboard-relay`, `STREAMHOUSE_RELAY_KEYS`, and
  `STREAMHOUSE_RELAY_DB`; the database path must be supplied deliberately.
- `extensions/twitch/tools/build_listing_assets.py` and generated files under
  `extensions/twitch/app/listing/`: Sally character artwork replaced by the
  Streamhouse umbrella brand artwork.
- `extensions/twitch/app/README.md`: current setup/build examples now advertise
  only Streamhouse names and the expected modern hostname.
- `docs/architecture/overview.md`: deployment ownership and the modern
  configuration source of truth updated.
- `tests/release/test_release_tools.py`: deployment assertions migrated to the
  modern service and environment names.
- `products/hub/soundboard/relay.py`: new/default Hub configuration is the
  modern hostname; all ordinary requests already use modern routes/headers.

## Isolated external wire compatibility

These are the only intentional generic relay-infrastructure Sally names. They
belong to the externally deployed `relay-compat-v1` wire contract and may be
removed no earlier than version `0.3.0` after the production criteria in
`relay-brand-migration.md` are satisfied. They are not internal Streamhouse
architecture and must not be copied into new code.

- `shared/streamhouse_runtime/relay_config.py`: centralized fallback constants
  for `SALLY_RELAY_BASE`, `SALLY_RELAY_KEYS`, `SALLY_RELAY_DB`, and the old
  hostname. Modern values are authoritative.
- `extensions/twitch/app/relay_server.py`: deprecated environment fallbacks,
  `/api/sally/config`, `/api/sally/poll`, `/api/sally/ack`, and `X-Sally-Channel`
  / `X-Sally-Key` header aliases, plus neutral viewer-route aliases from already
  deployed bundles. All normalize into canonical implementations.
- `products/hub/soundboard/relay.py`: fallback for `SALLY_RELAY_BASE`, the old
  hostname, and pre-rebrand route/header emission only after a 404 from an old
  server.
- `extensions/twitch/app/viewer.js`: isolated fallback for a previously built
  bundle defining only `window.SALLY_RELAY_BASE`.
- `products/hub/tests/test_soundboard.py` and
  `shared/tests/test_relay_config.py`: compatibility, conflict, security, and
  removal-regression fixtures.
- `tests/release/test_relay_branding.py` and
  `tests/release/test_release_tools.py`: explicit allowlist/absence assertions.
- `docs/deployment/relay-brand-migration.md` and the relay section of
  `docs/architecture/overview.md`: clearly labeled transition documentation.

The separate local Streamhouse AI protocol now accepts only
`X-Streamhouse-Protocol`; its pre-rebrand header and version fallback have been
removed. Relay compatibility is the only active generic Sally-named contract.

## Intentional Sally AI or Twitch-bot identity

- Product and behavior documentation: `README.md`, `CHANGELOG.md`,
  `docs/ai/local-ai.md`, `docs/architecture/product-family.md`,
  `docs/architecture/offline-design-checkpoint-2026-07-20.md`,
  `docs/hub/twitch.md`, and `docs/shared/viewer-memories.md`.
- AI personality implementation/tests: `products/ai/engine/response_engine.py`,
  `products/ai/streamhouse_ai/app.py`, `products/ai/tests/test_response_engine.py`,
  and `products/ai/tests/test_training_store.py`.
- Sally-specific chat, memory, training, and separate bot identity:
  `products/hub/core/settings.py`, `products/hub/twitch/commands.py`,
  `products/hub/twitch/simulator.py`,
  `products/hub/ui/main_window.py`, `products/hub/ui/mainwindow.ui`,
  `products/hub/ui/generated/ui_mainwindow.py`, plus their Hub tests.
- Sally character artwork retained intentionally:
  `shared/assets/sally-icon.svg`, `shared/assets/sally-icon.png`, and
  `shared/assets/sally-icon.ico`. Current relay/Extension tooling no longer uses
  these files.
- Remaining Sally-named sample text and fixtures exercise character-facing
  behavior rather than generic infrastructure.

## Removed internal pre-alpha compatibility

The final pre-alpha purge removed the internal compatibility paths formerly
listed here:

- Sally-era data-root/environment fallbacks and recursive local-data copying;
- Sally-era QSettings application stores and Hub window-title discovery;
- pre-rebrand automation routine/task formats and Python environment aliases;
- the pre-rebrand local AI protocol header/version path;
- old chat-context URL schemes, backup filename globs, and generic Companion
  service/settings names.

Private development data that depends on those paths must be reset. Twitch
token storage under the current Streamhouse data root was not changed.

## External deployment shim

- `twitch_extension/relay_server.py` is a neutral thin import shim retained only
  for the old Render dashboard start command.

## Removed dead or obsolete references

- Removed the README claim that `extensions/twitch/render.yaml` exists; the
  canonical blueprint is root `render.yaml`.
- Removed the old Render service identity and legacy environment keys from the
  active blueprint.
- Removed Sally character artwork from Twitch Extension listing generation.

The regression test intentionally checks relay-specific phrases instead of
banning the word Sally across the repository.
