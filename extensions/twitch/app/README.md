# Streamhouse Soundboard Twitch Extension

The viewer assets in this directory are shared by Streamhouse Hub's local preview and the
published Twitch Extension. The Extension sends Twitch-signed requests to the
hosted relay, while Hub maintains an outbound polling connection to that relay.

## Relay environment

- `TWITCH_EXTENSION_SECRET`: Base64 secret from the Twitch Extension console.
- `STREAMHOUSE_RELAY_KEYS`: JSON mapping broadcaster channel IDs to random relay keys.
- `STREAMHOUSE_RELAY_DB`: Optional SQLite database path.
- `PORT`: Optional HTTP port; use an HTTPS reverse proxy in production.

Run the relay from the repository root with
`python -m extensions.twitch.app.relay_server`.

## Free Render deployment

`extensions/twitch/render.yaml` defines one free Python web service. In
Render, create a Blueprint from this repository and provide the two secret
environment values when prompted:

- `TWITCH_EXTENSION_SECRET`: the Base64 secret generated in the Twitch
  Extension console. Never commit or paste this into the Extension ZIP.
- `STREAMHOUSE_RELAY_KEYS`: a JSON object whose keys are numeric broadcaster IDs and
  whose values are the matching private keys generated in Streamhouse Hub, for example
  `{"123456789":"private-random-key"}`.

Render supplies `PORT` automatically and exposes `/health` as the service
health check. The free filesystem is temporary, so Hub re-sends the public
soundboard configuration whenever it reconnects.

Legacy `SALLY_RELAY_KEYS` and `SALLY_RELAY_DB` values remain temporary
fallbacks. Hub emits only the Streamhouse HTTP routes and headers; the relay
temporarily accepts the legacy `/api/sally/*` routes and `X-Sally-*` headers.

## Viewer bundle

Build an uploadable asset ZIP after the relay has a public HTTPS URL:

```powershell
.\extensions\twitch\tools\build_extension.ps1 -RelayUrl https://relay.example.com
```

The bundle contains the shared viewer assets, Twitch's default `panel.html` and
`mobile.html` entry points, `config.html`, and the generated `config.js`. Never
place the Extension secret or Streamhouse relay key in the viewer bundle.
