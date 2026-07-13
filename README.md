# Sally AI Bot

Sally is a Windows desktop stream companion built with Python, PySide6, Twitch
Helix, and EventSub.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
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

The packaged application is written under `dist\SallyAI`.

Create the release ZIP and SHA-256 checksum afterward with:

```powershell
.\scripts\package_release.ps1
```

User settings, memories, sessions, backups, logs, and encrypted credentials are
stored under `%LOCALAPPDATA%\SallyAI`, so installing or replacing the program
does not replace user data.
