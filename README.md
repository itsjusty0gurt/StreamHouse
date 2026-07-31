# Streamhouse

> Everything for your stream under one roof.

Streamhouse is the umbrella brand and product ecosystem. The repository
currently implements two independently packaged Windows desktop applications:

- **Streamhouse Hub**, currently implemented and packaged as **Sally Bot**,
  owns Twitch, OBS, commands, automation, and the soundboard.
- **Streamhouse AI**, currently implemented and packaged as **Sally AI
  Companion**, owns Ollama reasoning and memory extraction. Sally is the
  default AI personality inside this application.

Hub is a separate download and continues operating when Streamhouse AI is
closed or not installed. AI features use a versioned HTTP API bound to
`127.0.0.1:8765` and never expose the service to the network.

For canonical product names, responsibilities, dependencies, status, and future
products, see the [Streamhouse product family](docs/product-family.md). For the
current implementation ownership, runtime flows, persistence, extension points,
and change-routing guidance, see the
[architecture reference](docs/architecture.md).

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe companion_main.py
```

Run tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Windows package

Install `requirements-build.txt`, then run:

```powershell
.\scripts\build_windows.ps1
```

The packaged applications retain their current implementation names and are
written under `dist\SallyBot` and `dist\SallyAICompanion`. Release packaging
creates two independent ZIP files; Hub-only users do not download or install
the AI backend.

Create the release ZIP and SHA-256 checksum afterward with:

```powershell
.\scripts\package_release.ps1
```

User settings, memories, sessions, backups, logs, and encrypted credentials are
stored under `%LOCALAPPDATA%\SallyAI`, so installing or replacing the program
does not replace user data.
