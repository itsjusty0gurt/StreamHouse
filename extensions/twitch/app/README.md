# Streamhouse Soundboard Twitch Extension

The viewer assets in this directory are shared by Streamhouse Hub's local preview and the
published Twitch Extension. The Extension sends Twitch-signed requests to the
hosted relay, while Hub maintains an outbound polling connection to that relay.

## Relay environment

- `TWITCH_EXTENSION_SECRET`: Base64 secret from the Twitch Extension console.
- `STREAMHOUSE_RELAY_KEYS`: JSON mapping broadcaster channel IDs to random relay keys.
- `STREAMHOUSE_RELAY_DB`: Required SQLite database file path. For a replacement
  service, this must point at verified migrated/shared storage rather than a new
  empty file.
- `PORT`: Optional HTTP port; use an HTTPS reverse proxy in production.

Run the relay from the repository root with
`python -m extensions.twitch.app.relay_server`.

Current Hub and viewer requests use `/api/streamhouse/*`. The viewer authenticates
with Twitch Extension JWTs; Hub uses `X-Streamhouse-Channel` and
`X-Streamhouse-Key`.

## Render deployment

Root `render.yaml` defines the modern Python web service. Follow
`docs/deployment/relay-brand-migration.md` before creating or synchronizing it;
the production database must be backed up and its storage strategy chosen first.
Provide these values securely in Render:

- `TWITCH_EXTENSION_SECRET`: the Base64 secret generated in the Twitch
  Extension console. Never commit or paste this into the Extension ZIP.
- `STREAMHOUSE_RELAY_KEYS`: a JSON object whose keys are numeric broadcaster IDs and
  whose values are the matching private keys generated in Streamhouse Hub, for example
  `{"123456789":"private-random-key"}`.
- `STREAMHOUSE_RELAY_DB`: the explicit SQLite path selected by the reviewed
  shared/copy/migration strategy.

Render supplies `PORT` automatically and exposes `/health` as the health check.

Deprecated names are described only in the migration runbook. They are isolated
under `relay-compat-v1` and are not required when modern settings are present.

## Viewer bundle

Build an uploadable asset ZIP after the relay has a public HTTPS URL:

```powershell
.\extensions\twitch\tools\build_extension.ps1 -RelayUrl https://streamhouse-soundboard-relay.onrender.com
```

The bundle contains the shared viewer assets, Twitch's default `panel.html` and
`mobile.html` entry points, `config.html`, and the generated `config.js`. Never
place the Extension secret or Streamhouse relay key in the viewer bundle.
