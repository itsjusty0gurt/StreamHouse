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

## Temporary compatibility shims

These are the only intentional relay-infrastructure Sally names. They belong to
`relay-compat-v1` and may be removed no earlier than version `0.3.0` after the
production criteria in `relay-brand-migration.md` are satisfied.

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

`shared/streamhouse_shared/protocol.py` and its architecture documentation use
`X-Sally-Protocol` for the separate local Streamhouse AI protocol migration.
That is historical compatibility outside the relay contract. It remains in the
current implementation but is transitional pre-alpha debt, not a preservation
requirement; remove it when active Hub and AI consumers use the current header.

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
  `products/hub/twitch/eventsub.py`, `products/hub/twitch/service.py`,
  `products/hub/twitch/simulator.py`, `products/hub/twitch/token_store.py`,
  `products/hub/ui/main_window.py`, `products/hub/ui/mainwindow.ui`,
  `products/hub/ui/generated/ui_mainwindow.py`, and
  `products/hub/ui/twitch_chat_view.py`, plus their Hub tests.
- Sally character artwork retained intentionally:
  `shared/assets/sally-icon.svg`, `shared/assets/sally-icon.png`, and
  `shared/assets/sally-icon.ico`. Current relay/Extension tooling no longer uses
  these files.
- Sally-named sample text and fixtures in Hub automation modules/tests are
  character-facing examples, not relay branding.

## Historical migration support or fixtures

This section inventories current or historical files; it does not grant them
ongoing support status. Under `docs/architecture/development-policy.md`, local
Sally-era migration code and fixtures should be removed when no external,
security, or post-Alpha requirement justifies them. The deployed relay shim is
the operational exception described below.

- Legacy data/QSettings/backup paths: `shared/streamhouse_runtime/paths.py`,
  `shared/streamhouse_runtime/qt_settings.py`, `products/hub/core/backup.py`,
  `products/hub/streamhouse_hub/app.py`, `tests/integration/test_paths.py`, and
  release branding/icon tests.
- Legacy automation formats and environment aliases:
  `products/hub/automation/transfer.py`, `products/hub/automation/core_tasks.py`,
  related automation UI/tests, and `.sally-routine.json` fixtures.
- Legacy local AI protocol compatibility: `shared/streamhouse_shared/protocol.py`,
  `shared/streamhouse_shared/presence_protocol.py`, shared models/policy, and
  integration protocol/remote-store tests.
- `.gitignore` and historical changelog/release-checklist references describe
  old files, data roots, or migration behavior; they are not active branding.
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
