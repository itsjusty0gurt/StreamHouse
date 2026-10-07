# Streamhouse Hub public Alpha release checklist

This is the standing checklist for every public Streamhouse Hub release.
GitHub Releases is the canonical download source, and Alpha releases remain
marked as pre-releases. Streamhouse Hub and Streamhouse AI are independent
Windows packages; this checklist covers Hub unless a release explicitly
includes another product.

The release gate is:

> User data takes precedence over implementation convenience.

Protect user data, not obsolete architecture. Backward compatibility means
upgrading old user data into the current architecture — not preserving old
architecture. Never use a user-data reset as a release shortcut. See
[`development-policy.md`](development-policy.md) for the authoritative
compatibility and migration rules.

## Source and Git state

- [ ] Confirm the intended release branch and upstream.
- [ ] Confirm the working tree is clean and contains no uncommitted or generated
      source changes.
- [ ] Identify and record the exact release commit.
- [ ] Choose the version and annotated release tag.
- [ ] Run the release regression suite and `git diff --check`.
- [ ] Confirm release/package-boundary and branding tests pass: Hub excludes the
      Streamhouse AI engine/package and retains the approved Hub product icon.
- [ ] Verify final branch/tag topology without rewriting published history.

## Compatibility and user data

- [ ] Test an upgrade from the previous public Hub release using a representative
      copy of its data root.
- [ ] Preserve user-created routines, task configuration, stable IDs, links, and
      group placement.
- [ ] Preserve Commands and their trigger/routine relationships.
- [ ] Preserve Core, Twitch, OBS, Music, and other publicly shipped trigger
      definitions and links.
- [ ] Preserve Default Queue/custom queues and routine assignments.
- [ ] Preserve Counter definitions and values for all applicable scopes.
- [ ] Preserve durable custom Variables and current canonical names.
- [ ] Preserve Channel Information content and inclusion state.
- [ ] Preserve Users/management state where the released schema applies.
- [ ] Preserve settings intended to survive upgrades, plus Twitch, OBS, relay,
      and other protected credentials.
- [ ] Confirm no migration or startup path resets public user data for
      implementation convenience.

## Migration safety

- [ ] Accept a publicly shipped old schema only as an isolated old-to-current
      migration input; normal runtime loading and saving use the current schema.
- [ ] Create and verify a recoverable safety backup before any destructive
      migration step.
- [ ] Fully validate the source before transforming it.
- [ ] Preserve stable identities, references, user intent, and secrets owned by
      the migrated component.
- [ ] Stage and validate all current-schema output before publication.
- [ ] Ensure failure leaves the original data recoverable and publishes no
      mixed or partially migrated state.
- [ ] Save only current-schema output; do not add dual reads, dual writes,
      permanent compatibility shims, or parallel old/new runtimes.
- [ ] Start Hub a second time and confirm the current schema loads without
      running the migration again.

## Installer and upgrade

- [ ] Build the installer from the exact release commit/tag.
- [ ] Install over the previous public release and verify the Hub data root is
      untouched except for intentional application-owned migrations.
- [ ] Confirm the install directory update does not delete, move, or recreate the
      user-data root.
- [ ] Confirm Start Menu shortcuts, Programs and Features metadata, and uninstall
      entries are correct and not duplicated.
- [ ] Confirm uninstall preserves user data by default.
- [ ] Test a fresh install separately from the upgrade path.
- [ ] Document expected unsigned Windows SmartScreen behavior while applicable.

## Backup and Restore

- [ ] Create a manual Backup and validate its manifest/checksums.
- [ ] Restore representative components and restart Hub.
- [ ] Confirm restored data reloads in the current schema with relationships
      intact.
- [ ] Confirm Backup excludes credentials, chat/message history, logs,
      diagnostics, and other ineligible data.
- [ ] Confirm Restore creates a safety backup before commit and rolls back
      cleanly on failure.
- [ ] Run the standard Backup/Restore regression suite.

## Application smoke

- [ ] Start Hub, shut down cleanly, and restart it against the upgraded data.
- [ ] Confirm Hub starts and remains useful with Streamhouse AI absent.
- [ ] Verify Twitch authentication, chat, EventSub, and representative channel
      actions.
- [ ] Verify OBS connection and a representative OBS action when available.
- [ ] Verify Automation/Routines execute through their normal queues.
- [ ] Verify Commands, Timers, Variables, Counters, and Channel Information.
- [ ] Verify Raid and Raid Landing behavior where the test account permits it.
- [ ] Verify current major optional integrations, including the local Music
      Player when available; absence of an optional integration must remain safe.
- [ ] Run the development and packaged smoke suites referenced in
      [`overview.md`](overview.md#build-and-verification).

## Diagnostics and support

- [ ] Create and inspect a Support Bundle.
- [ ] Confirm no tokens, passwords, authorization headers, or other secrets are
      present.
- [ ] Confirm no Twitch viewer chat/message content is present.
- [ ] Verify the built-in bug-report path and current feedback links.
- [ ] Confirm release version/build information appears correctly in diagnostics.

External-user reports deserve prompt investigation because they expose upgrade,
hardware, account, and workflow assumptions that the maintainer's setup may not
exercise. Keep feedback accessible through GitHub Issues, Discord, and Hub's
built-in bug-report flow.

## Release notes and distribution

- [ ] Write short user-facing release notes.
- [ ] State migration or breaking behavior clearly and list known limitations.
- [ ] Build/package Hub using the repository release tools.
- [ ] Generate and verify the SHA-256 checksum.
- [ ] Create the GitHub Release as a pre-release while Hub remains Alpha.
- [ ] Upload the installer and checksum, plus any intentionally supported archive
      artifacts.
- [ ] Verify the canonical GitHub Release download link from a signed-out browser
      or clean session.

## Final verification

- [ ] Confirm the installer and checksum came from the exact release commit/tag.
- [ ] Repeat clean-install and reinstall smoke tests with the published artifact.
- [ ] Repeat the previous-version upgrade smoke with the published artifact.
- [ ] Confirm the working tree remains clean after packaging.
- [ ] Verify the pushed release tag, release branch, and next-development branch
      point to the intended commits.

## Public Alpha cadence

Prefer smaller, regular public Alpha releases over allowing `develop` to build a
large, risky feature batch. Use patch Alpha versions for focused bugfix releases
and minor Alpha versions for coherent feature batches, for example
`v0.2.0-alpha`, `v0.2.1-alpha`, and `v0.3.0-alpha`. Stability and upgrade safety,
not a rigid calendar, determine release timing.
