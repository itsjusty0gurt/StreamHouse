# Streamhouse release checklist

Streamhouse Hub and Streamhouse AI are independent Windows packages. Hub must
remain useful without AI, and Hub's bundle must exclude the heavyweight
`products/ai/engine/` implementation. Future Streamhouse Studio, Deck, and
Avatar products are not
part of this release.

Streamhouse is currently pre-alpha. Apply
`docs/architecture/development-policy.md`: private-development data compatibility
is not a release gate, while security, privacy, external deployment, and
third-party requirements remain binding. The first external Alpha establishes
the saved-data compatibility baseline.

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
9. Verify new data is written beneath `%LOCALAPPDATA%\Streamhouse`; Sally-era
   local-data and UI-state stores must not be loaded or migrated.
10. Prefer preserving encrypted Twitch tokens when easy, but permit an
    authentication reset if required by a cleaner design. Verify that no token,
    OAuth credential, or other secret appears in logs, artifacts, tests, or
    reports.
11. Inspect the Hub bundle to confirm it excludes `products.ai.engine` and
    `products.ai.streamhouse_ai`.
12. Inspect the AI bundle to confirm it excludes `products.hub`.
13. Run `tools\release\package_all.ps1`.
14. Publish the two independent archives and matching checksum files:
    - `StreamhouseHub-<version>-windows-x64.zip`
    - `StreamhouseHub-<version>-windows-x64.zip.sha256`
    - `StreamhouseAI-<version>-windows-x64.zip`
    - `StreamhouseAI-<version>-windows-x64.zip.sha256`

Obsolete Sally-era cross-root readers, environment aliases, protocol headers,
QSettings identifiers, command v5 migration, and the shared
activity/chatter/session development-data normalizer have been removed.
Affected stores now require their exact current schema; any remaining tolerant
store loaders are debt recorded in `overview.md`, not an Alpha compatibility
promise.
Externally deployed relay compatibility is governed by
`docs/deployment/relay-brand-migration.md` and remains until its operational
removal conditions are satisfied. New builds and documentation use Streamhouse
names.

Approved product icons live under `shared/assets/streamhouse-icons/`. Confirm
Hub uses the `H` icon and Streamhouse AI uses the `AI` icon in both Qt and the
Windows executable before publishing. The old `shared/assets/sally-icon.*`
files are Sally character artwork, not application branding.
