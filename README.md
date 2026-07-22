# Sally Bot and AI Companion

Sally is split into two Windows desktop applications:

- **Sally Bot** owns Twitch, OBS, commands, automation, and the soundboard.
- **Sally AI Companion** owns Ollama reasoning and memory extraction.

The Bot is a separate download and continues operating when the AI Companion is
closed or not installed. AI features use a
versioned HTTP API bound to `127.0.0.1:8765` and never expose the service to the
network.

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

The packaged applications are written under `dist\SallyBot` and
`dist\SallyAICompanion`. Release packaging creates two independent ZIP files;
Bot-only users do not download or install the AI backend.

Create the release ZIP and SHA-256 checksum afterward with:

```powershell
.\scripts\package_release.ps1
```

User settings, memories, sessions, backups, logs, and encrypted credentials are
stored under `%LOCALAPPDATA%\SallyAI`, so installing or replacing the program
does not replace user data.
