# Streamhouse relay production migration

Streamhouse is the infrastructure and product brand. Sally is an AI character
and may also be a separately authenticated Twitch bot identity. The soundboard
relay and Twitch Extension are owned by Streamhouse Hub and use Streamhouse
names in current configuration, routes, headers, UI, and bundles.

## Canonical contract

- Service: `streamhouse-soundboard-relay`
- Expected hostname: `https://streamhouse-soundboard-relay.onrender.com`
- Environment: `STREAMHOUSE_RELAY_KEYS`, `STREAMHOUSE_RELAY_DB`, and (for Hub
  or build-time clients) `STREAMHOUSE_RELAY_BASE`
- Hub routes: `/api/streamhouse/config`, `/api/streamhouse/poll`, and
  `/api/streamhouse/ack`
- Viewer routes: `/api/streamhouse/config` and `/api/streamhouse/trigger`
- Hub headers: `X-Streamhouse-Channel` and `X-Streamhouse-Key`

Viewer GET/POST requests use Twitch Extension JWTs. Previously deployed neutral
`/api/config` and `/api/trigger` paths remain compatibility aliases.

## Compatibility window

`relay-compat-v1` remains supported no earlier than Streamhouse `0.3.0` and is
limited to the former relay environment variables, Hub route aliases, Hub
headers, Extension base setting, and old hostname fallback. Modern values win
when both names are supplied. Conflicts and legacy-only use emit one structured,
value-free warning per process/browser session. Current documentation, UI, and
generated bundles do not advertise legacy names.

Operational check on 2026-08-21: the old relay `/health` endpoint returned
`{"status":"ok"}`, while the expected modern hostname returned HTTP 404. The
modern deployment and cutover gates are therefore not complete, so the isolated
wire compatibility and old Render entry-point shim must remain.

## Render transition

Do not synchronize the updated blueprint against the production workspace until
the service and database plan below has been reviewed. A Render service rename
may change or recreate its hostname; verify Render's current behavior in the
dashboard first.

Option A — rename in place:

1. Back up the SQLite file configured by the existing relay database variable.
2. Record the current service ID, build/start commands, health check, secrets,
   disk mounts, and rollback name.
3. If Render confirms an in-place rename preserves the service, disk, and
   hostname behavior, rename it to `streamhouse-soundboard-relay`.
4. Add the modern variables with the same values, deploy, and verify the modern
   endpoints before removing either legacy variable.

Option B — parallel service (the safer default and the root blueprint target):

1. Keep `sally-soundboard-relay.onrender.com` online.
2. Create `streamhouse-soundboard-relay` from root `render.yaml`.
3. Copy non-secret configuration and securely set `TWITCH_EXTENSION_SECRET`
   and `STREAMHOUSE_RELAY_KEYS`; never put their values in Git or build assets.
4. Determine the database strategy before setting `STREAMHOUSE_RELAY_DB`:
   attach shared persistent storage if SQLite locking and Render mounts make
   that safe, stop writes and copy the existing SQLite file plus `-wal`/`-shm`
   files consistently, or perform a reviewed database migration. Do not point
   the new service at an empty path.
5. Run a SQLite integrity check on the backup and migrated database. Compare
   configuration/event row counts and verify a known broadcaster configuration.
6. Deploy and verify `/health`, all `/api/streamhouse/*` operations, modern
   headers, invalid-key rejection, Twitch JWT verification, and event expiry.
7. Update the Twitch Extension bundle and allowed URL configuration, then submit
   and deploy it through Twitch's review/release process.
8. Update supported Hub releases/defaults, while leaving the old service online.
9. Monitor both services and `relay_compatibility_used` warnings through the
   transition window.
10. Retire the old service only after the removal criteria below are satisfied.

The existing blueprint used `/tmp/sally-soundboard-relay.sqlite3`. That value is
a SQLite file path on Render's ephemeral filesystem, not a URL or external
database connection. Confirm the live service's actual environment and disk
mount before assuming production uses that blueprint value. The modern
blueprint requires `STREAMHOUSE_RELAY_DB` to be set explicitly so a replacement
cannot silently select a new default database.

## Twitch Extension transition

1. Verify the new relay over HTTPS and record its exact origin.
2. Build with
   `build_extension.ps1 -RelayUrl https://streamhouse-soundboard-relay.onrender.com`.
3. Inspect the ZIP: `config.js` must define only `STREAMHOUSE_RELAY_BASE`, and
   no secret or private relay key may be present.
4. Add the new origin to every Twitch Extension allowlist/CSP setting in the
   Twitch developer console. The repository HTML does not define a fetch CSP;
   Twitch's hosted-asset policy and console configuration remain authoritative.
5. Test panel, mobile, and configuration views with viewer and broadcaster JWTs.
6. Submit/approve/deploy the Extension version, then monitor requests to both
   relay hosts. Keep the previous approved Extension version available for
   rollback.

## Rollback

- Keep the old Render service, its environment, and its database unchanged
  during the window.
- Preserve a timestamped, integrity-checked database backup before migration.
- If the new relay fails, restore the prior Twitch Extension version/base URL
  and direct affected Hub configurations to the old service.
- If data was copied, stop the new service before restoring the backup. Do not
  merge two independently written SQLite files without a reviewed procedure.
- Record events accepted during rollback so requests are not assumed replayed;
  the relay's event IDs and five-minute expiry remain authoritative.

## Compatibility removal checklist

Remove `SALLY_RELAY_KEYS`, `SALLY_RELAY_DB`, `SALLY_RELAY_BASE`,
`/api/sally/*`, `X-Sally-*`, the old hostname fallback, and their tests/fixtures
only after all of the following are true:

- the modern Render relay is deployed and healthy;
- the production database and backups are verified;
- the Twitch Extension using the new URL is approved and deployed;
- every supported Hub release uses modern routes and headers;
- the documented transition period and rollback window have completed;
- logs show no meaningful legacy route, header, environment, or hostname use;
- rollback no longer depends on the old service; and
- the old service can be disabled and observed safely before deletion.

After removal, delete the thin `twitch_extension/relay_server.py` entry-point
shim only if Render no longer invokes it, remove the compatibility allowlist
entries/tests, and update the architecture reference.
