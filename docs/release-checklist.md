# Streamhouse current implementation release checklist

Everything except the PyInstaller build can be verified with the existing
development environment. Streamhouse Hub is still built as Sally Bot, and
Streamhouse AI is still built as Sally AI Companion. Future Streamhouse Studio,
Deck, and Avatar products are not part of this release.

1. Run `.venv\Scripts\python.exe -m unittest discover -s tests`.
2. Run `.venv\Scripts\python.exe scripts\smoke_test.py`.
3. Install `requirements-build.txt` when ready.
4. Run `scripts\build_windows.ps1`.
5. Launch Streamhouse AI's current build,
   `dist\SallyAICompanion\SallyAICompanion.exe`, then Streamhouse Hub's current
   build, `dist\SallyBot\SallyBot.exe`; verify Companion status, Twitch sign-in,
   and WebEngine chat rendering.
6. Verify data appears under `%LOCALAPPDATA%\SallyAI`.
7. Run `scripts\package_release.ps1`.
8. Publish the independent Sally Bot and Sally AI Companion ZIPs together with
   their `.sha256` files and `CHANGELOG.md`. These legacy artifact names are
   intentional until a separate implementation rename. The Bot ZIP must not
   contain `ai.*`.

Replacing the application does not remove user data. On first launch, files
from the old workspace `config` and `memory` paths are copied into LocalAppData
only when the destination does not already exist.
