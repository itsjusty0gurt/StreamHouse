# Codex task rules

These are standing rules for repository work. They supplement `AGENTS.md` and
the authoritative compatibility policy in
[`docs/architecture/development-policy.md`](../architecture/development-policy.md).

## Start from current state

- Read every applicable `AGENTS.md`, the development policy, and the smallest
  relevant architecture references before editing.
- Inspect the current implementation, then run `git status` and `git diff`.
  Preserve unrelated work. If work was interrupted, recover and continue it;
  do not restart or reset it automatically.

## Implementation boundaries

- Find the root cause first. Reuse existing services, stores, registries, and
  UI patterns; keep one owner and avoid broad refactors or new dependencies
  unless the task requires them.
- `MainWindow` composes and wires components. Domain behavior belongs in its
  owning service/store, and substantial UI belongs in a focused widget/page.
- Never block the Qt UI thread with network, filesystem, subprocess, model,
  long-wait, or heavy CPU work. Marshal worker results through the established
  Qt-safe signal/slot boundary.
- Treat QObject, WebEngine, and Shiboken ownership and teardown conservatively:
  avoid stale wrappers, late callbacks, and unnecessary widget rebuilds.

## Data and compatibility

- **User data takes precedence over implementation convenience.** Protect user
  data, not obsolete architecture. Compatibility means migrating released data
  into the current architecture.
- Keep one current runtime schema and implementation. Old public schemas are
  isolated migration inputs only: no permanent dual reads/writes, compatibility
  shims, or parallel runtimes.
- Create a recoverable safety backup before destructive migration. Preserve the
  original on failure and never publish mixed or partially migrated state.
  Fail safely instead of overwriting uncertain data.
- Never change a durable schema silently. Stable IDs are authoritative across
  stores and references; names are presentation data.

## Streamhouse product rules

- Hub remains local and standalone wherever practical. Normal Hub features must
  not require Streamhouse-hosted cloud infrastructure; the Soundboard relay is
  the intentional exception.
- Sally is a Streamhouse AI character, not shared/runtime infrastructure.
- Scope Hub work for Windows first unless the task explicitly changes platform
  support. Preserve lightweight shared boundaries and independent Hub/AI packages.

## Automation and Variables

- Use `VariableRegistry` and canonical dotted Variables only. Do not add legacy
  flat placeholders or a second Variable/output catalog.
- Task outputs use `{automation.*}` and retain their correct root-execution
  lifetime. Preserve routine, queue, trigger, task, and referenced-object stable
  IDs and normal queue execution.

## Documentation

- Update built-in Wiki/reference metadata for relevant user-facing behavior.
- Update architecture/data-contract documentation for durable ownership,
  persistence, protocol, schema, or packaging changes.
- Pure internal fixes normally require no Wiki change.

## Validation and scope

- Keep the requested scope. Report unrelated findings rather than implementing
  them, and do not add unrelated features.
- Run focused tests first. Expand regression coverage when shared architecture,
  persistence, migration, threading, authentication, cross-store references,
  or another high-risk boundary changes.
- Use the repository's safe Qt/offscreen/WebEngine test setup when needed. Do
  not broadly alter product code to mask an unrelated native test-harness issue.
- Run `git diff --check`. Do not commit or push unless explicitly instructed.

## Finish every task

- Inspect the final diff and status. Confirm there is no debug/temp code,
  abandoned duplicate implementation, or accidental unrelated change.
- Report concisely what changed, tests/checks run, remaining issues, and final
  working-tree status.
