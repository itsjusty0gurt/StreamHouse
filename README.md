# Streamhouse

> Everything for your stream under one roof.

This monorepo contains two independently packaged Windows applications and one
hosted extension:

- **Streamhouse Hub** owns Twitch, OBS, commands, automation, queues,
  variables, channel tools, and soundboard control.
- **Streamhouse AI** owns Ollama reasoning, AI settings, training review, and
  memory extraction. **Sally** is its default personality.
- **Streamhouse Soundboard Twitch Extension** is a separately deployed viewer
  frontend and relay under `extensions/twitch/`.

Hub works without AI installed or running. Streamhouse AI can launch without
Hub. When both are running they communicate through a versioned localhost API
bound only to `127.0.0.1:8765`.

## Repository map

| Change | Owner |
| --- | --- |
| Twitch, OBS, automation, desktop UI | `products/hub/` |
| Ollama, response reasoning, personality execution | `products/ai/` |
| Lightweight protocol and presence contracts | `shared/streamhouse_shared/` |
| Cross-product runtime utilities | `shared/streamhouse_runtime/` |
| Hosted Twitch extension and relay | `extensions/twitch/` |
| Build, packaging, release, smoke, development tools | `tools/` |
| Cross-product and release tests | `tests/integration/`, `tests/release/` |

See [architecture](docs/architecture/overview.md) for ownership and change
routing, and [product family](docs/architecture/product-family.md) for canonical
product names and dependency rules.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m products.hub.hub_main
.\.venv\Scripts\python.exe -m products.ai.ai_main
```

Run tests and the development smoke test:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m tools.smoke.smoke_development
```

## Windows packages

Install `requirements-build.txt`, then run:

```powershell
.\tools\build\build_all.ps1
.\tools\smoke\smoke_packaged.ps1
.\tools\release\package_all.ps1
```

Product-specific `build_hub.ps1`, `build_ai.ps1`, `package_hub.ps1`, and
`package_ai.ps1` scripts are available in the same tool directories.

Independent outputs:

- `dist\StreamhouseHub\StreamhouseHub.exe`
- `dist\StreamhouseAI\StreamhouseAI.exe`
- `release\StreamhouseHub-0.1.0-windows-x64.zip`
- `release\StreamhouseAI-0.1.0-windows-x64.zip`

User data remains under `%LOCALAPPDATA%\Streamhouse`. Missing legacy files from
`%LOCALAPPDATA%\SallyAI` are copied without overwrite or deletion. The new
`STREAMHOUSE_DATA_DIR` and `STREAMHOUSE_SMOKE_TEST` environment variables take
precedence; `SALLY_DATA_DIR` and `SALLY_SMOKE_TEST` remain deprecated fallbacks.
