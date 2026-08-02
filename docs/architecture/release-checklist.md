# Streamhouse release checklist

Streamhouse Hub and Streamhouse AI are independent Windows packages. Hub must
remain useful without AI, and Hub's bundle must exclude the heavyweight
`products/ai/engine/` implementation. Future Streamhouse Studio, Deck, and
Avatar products are not
part of this release.

1. Run `.venv\Scripts\python.exe -m pytest -q`.
2. Run `.venv\Scripts\python.exe -m tools.smoke.smoke_development`.
3. Run `git diff --check`.
4. Install `requirements-build.txt` if PyInstaller is unavailable.
5. Run `tools\build\build_all.ps1`.
6. Run `tools\smoke\smoke_packaged.ps1`.
7. Verify these independent executables exist:
   - `dist\StreamhouseHub\StreamhouseHub.exe`
   - `dist\StreamhouseAI\StreamhouseAI.exe`
8. Verify Hub starts with AI absent, AI starts with Hub absent, and discovery
   succeeds when both applications are running.
9. Verify new data is written beneath `%LOCALAPPDATA%\Streamhouse`, while a
   legacy `%LOCALAPPDATA%\SallyAI` tree is copied without deletion or overwrite.
10. Verify legacy window geometry and splitter/dock state survive the QSettings
    migration.
11. Inspect the Hub bundle to confirm it excludes `products.ai.engine` and
    `products.ai.streamhouse_ai`.
12. Inspect the AI bundle to confirm it excludes `products.hub`.
13. Run `tools\release\package_all.ps1`.
14. Publish the two independent archives and matching checksum files:
    - `StreamhouseHub-<version>-windows-x64.zip`
    - `StreamhouseHub-<version>-windows-x64.zip.sha256`
    - `StreamhouseAI-<version>-windows-x64.zip`
    - `StreamhouseAI-<version>-windows-x64.zip.sha256`

Replacing either application does not remove user data. Legacy environment
variables, protocol headers, relay routes, and QSettings identifiers are
temporary compatibility fallbacks; new builds always emit Streamhouse names.

Approved product icons live under `shared/assets/streamhouse-icons/`. Confirm
Hub uses the `H` icon and Streamhouse AI uses the `AI` icon in both Qt and the
Windows executable before publishing. The old `shared/assets/sally-icon.*`
files are Sally character artwork, not application branding.
