# Streamhouse development and compatibility policy

This document is the authoritative engineering policy for compatibility,
migrations, and architectural replacement. It applies to Streamhouse Hub,
Streamhouse AI, shared packages, extensions, tools, tests, and documentation.

## Current release phase

Streamhouse Hub is distributed as a public Alpha through GitHub Releases.
Alpha 0.1 is its first external saved-data compatibility baseline. Unknown users
may have valuable data that maintainers cannot inspect, so every Hub schema or
store shipped in a public release is a supported upgrade input.

Other Streamhouse products or components may still be unreleased. Release phase
is artifact-specific: the disposable-development-data rules below apply only to
formats that can be verified as never publicly shipped.

## Public Alpha user-data policy

> User data takes precedence over implementation convenience.

For every public Hub upgrade:

- never discard user data to simplify a migration or refactor;
- never silently overwrite uncertain, incompatible, or invalid data;
- preserve user-owned configuration and credentials across upgrades;
- create a recoverable safety backup before a destructive migration;
- fail without publishing mixed or partially migrated durable state;
- preserve the Hub user-data root during installer upgrades and uninstall by
  default; and
- treat migration correctness as more important than preserving old
  implementation details.

The companion architecture rule is:

> Protect user data, not obsolete architecture.
>
> Backward compatibility means upgrading old user data into the current
> architecture — not preserving old architecture.

The current runtime still has one schema and one implementation per subsystem.
Old public schemas are isolated migration inputs only. They do not justify
permanent compatibility shims, dual reads or writes, old/new runtime branches,
or obsolete Variable aliases. After a migration safely produces and validates
current-schema state, obsolete runtime architecture should be removed.

## Replacing an unreleased system

For an unreleased system, replacement is complete only when active consumers
use the new system and the obsolete system, compatibility layer, dead code,
tests, and current documentation have been removed.

Use this progression:

```text
Old implementation
        -> new implementation proven
        -> active code migrated
        -> old implementation deleted
        -> compatibility layer deleted
        -> one authoritative path
```

Do not retain parallel implementations, deprecated APIs, adapters, parsers,
registries, serializers, storage formats, aliases, or fallback paths solely to
support private-development behavior. Identifying code as "legacy" is normally
a reason to remove it, not a reason to support it indefinitely.

If a transitional path is genuinely needed while a migration is in progress:

1. mark it explicitly as transitional and state why it exists;
2. migrate all practical active consumers in the same task;
3. define the condition for removing it; and
4. remove it before calling the migration finished whenever practical.

Historical documents may describe superseded designs, but must label them as
history rather than current support requirements.

## Unreleased development data is disposable

Data in a format that can be verified as never publicly released does not
constrain the intended architecture. A cleaner design may invalidate, reset, or
delete that unreleased development-era state.

Do not build substantial migration infrastructure just to preserve this data.
When an unreleased storage schema should change, change it cleanly, update
current consumers and tests, and document any reset needed by developers.
Never assume data is disposable merely because a feature is labeled Alpha. If
the schema may have shipped publicly, treat it as supported user data. Security
and privacy deletion requirements still apply to discarded development data.

### Credentials

Preserve existing Twitch authentication, OBS secrets, and other protected
credentials across public upgrades. A storage change may re-encrypt or relocate
credentials through a reviewed migration, but must not silently discard them.
Credential migration must preserve the security boundary and must never trade
confidentiality for compatibility.

Never expose tokens, secrets, OAuth credentials, or sensitive authentication
data in logs, documentation, commits, fixtures, tests, diagnostics, or reports.
Credential preservation does not require retaining an obsolete runtime or weak
storage design; migrate the protected value into the current owner instead.

## Alpha is the compatibility baseline

Beginning with Hub Alpha 0.1, saved user data and upgrades are explicit release
requirements. At and after that boundary:

- saved-data changes should be versioned;
- migration and rollback strategy should be considered;
- breaking changes should be intentional;
- compatibility implications should be documented; and
- destructive resets are not an acceptable release shortcut.

The first Alpha does not require feature completeness. It establishes the
baseline from which user-facing compatibility is managed deliberately.

## Architectural decision rule

When replacing an existing system, ask:

1. Did any schema or store owned by the system ship publicly?
2. Which user intent, stable identities, references, and credentials must the
   migration preserve?
3. How will a safety backup and rollback protect the original input?
4. Can migration remain isolated while the runtime moves to one clean current
   implementation?

If the old runtime is unnecessary, migrate supported user data, remove it, and
update every active consumer to the new system. If the format never shipped,
the unreleased-data rule may allow a documented reset instead.

Compatibility may still be required for reasons unrelated to disposable local
development data, including an externally deployed service, a third-party
contract, a security/privacy obligation, or an intentionally supported released
artifact. Document the concrete requirement and removal condition. The hosted
soundboard relay migration is one such operational case because it coordinates
a deployed service, Twitch Extension, and database; its runbook owns that
transition.

## Variables architecture

Streamhouse Hub has one authoritative Variables architecture:

```text
VariableRegistry
|-- typed variable definitions
|-- providers
|-- context and lifetime handling
|-- placeholder resolution
|-- validation
|-- domain-routed writes
|-- Variables UI
`-- Variable Picker
```

`VariableRegistry` and provider-owned typed definitions are the intended
runtime and metadata authority. Cleanup may remove legacy flat-variable
catalogs, flat-name validation, obsolete placeholder parsers, sample-value
definition tables, old output metadata, compatibility aliases, old saved-
routine formats, and fallbacks to obsolete variable systems that never shipped.
Publicly shipped Variable data must instead be migrated through the current
registry/provider architecture.

The former flat catalog, flat placeholder path, and compatibility-only aliases
are not part of the current runtime. Do not reintroduce them. New definitions,
context, previews, validation, picker entries, and task outputs must extend the
registry/provider/typed-output architecture rather than create a parallel
catalog or parser.

## Product naming and ownership

The current product model is:

```text
Streamhouse
|-- Streamhouse Hub
|-- Streamhouse AI
|-- shared/
`-- extensions/
```

Sally is an AI personality or character within Streamhouse AI. Sally is not the
company, umbrella platform, generic runtime, shared infrastructure, or the name
of Streamhouse Hub. SallyBot-era infrastructure that is not intentionally
character-specific should be removed or renamed rather than preserved solely
for data formats verified as never publicly released.

Product ownership remains defined by `product-family.md` and `overview.md`.
Cleanup must preserve the independent Hub and AI packages, lightweight shared
boundaries, and extension ownership.

## Public Alpha maintenance standard

Streamhouse public Alpha releases should retain:

- one authoritative implementation for each major subsystem;
- no known obsolete parallel architectures;
- no permanent compatibility layers where an isolated migration is sufficient;
- no stale SallyBot-era naming in generic infrastructure;
- no dead migration code or obsolete current documentation;
- no duplicate sources of truth;
- clear ownership among Hub, AI, shared code, and extensions;
- current storage schemas that represent the intended design, with isolated
  migrations for publicly shipped inputs; and
- tests that target the current architecture rather than discarded designs.

This is an architectural readiness standard, not a feature-completeness claim.
