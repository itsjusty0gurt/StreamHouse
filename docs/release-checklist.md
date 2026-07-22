# Sally 0.1.0 release checklist

Everything except the PyInstaller build can be verified with the existing
development environment.

1. Run `.venv\Scripts\python.exe -m unittest discover -s tests`.
2. Run `.venv\Scripts\python.exe scripts\smoke_test.py`.
3. Install `requirements-build.txt` when ready.
4. Run `scripts\build_windows.ps1`.
5. Launch `dist\SallyAICompanion\SallyAICompanion.exe`, then
   `dist\SallyBot\SallyBot.exe`; verify Companion status, Twitch sign-in, and
   WebEngine chat rendering.
6. Verify data appears under `%LOCALAPPDATA%\SallyAI`.
7. Run `scripts\package_release.ps1`.
8. Publish the independent Sally Bot and AI Companion ZIPs together with their
   `.sha256` files and `CHANGELOG.md`. The Bot ZIP must not contain `ai.*`.

Replacing the application does not remove user data. On first launch, files
from the old workspace `config` and `memory` paths are copied into LocalAppData
only when the destination does not already exist.
