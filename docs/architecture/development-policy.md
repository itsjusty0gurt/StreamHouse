# Streamhouse development and compatibility policy

This document is the authoritative engineering policy for compatibility,
migrations, and architectural replacement. It applies to Streamhouse Hub,
Streamhouse AI, shared packages, extensions, tools, tests, and documentation.

## Current release phase

Streamhouse has not reached its first external Alpha release. Until that release:

> Architecture cleanliness and the intended Alpha design take priority over
> backwards compatibility with development-era data, formats, implementations,
> or APIs.

The repository should enter Alpha with a clean baseline, not with compatibility
baggage accumulated during private development. This policy does not remove
security obligations or externally imposed contracts.

## Replacing a pre-alpha system

Before Alpha, a replacement is complete only when active consumers use the new
system and the obsolete system, compatibility layer, dead code, tests, and
current documentation have been removed.

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

## Development data is disposable

Private pre-alpha data does not constrain the intended architecture. A cleaner
design may invalidate, reset, or delete development-era routines, automations,
custom variables, task definitions, generated outputs, profiles, test
configuration, counters, saved UI state, caches, and similar locally created
state.

Do not build substantial migration infrastructure just to preserve this data.
When a pre-alpha storage schema should change, change it cleanly, update current
consumers and tests, and document any reset needed by developers. Security and
privacy deletion requirements still apply to discarded data.

### Twitch authentication convenience exception

Preserve existing Twitch authentication and encrypted tokens when doing so is
easy and does not distort the architecture. Reconnecting Twitch repeatedly is
inconvenient, but token compatibility is not a hard requirement before Alpha.
Authentication storage may be reset when a clean design genuinely requires it.

Never expose tokens, secrets, OAuth credentials, or sensitive authentication
data in logs, documentation, commits, fixtures, tests, diagnostics, or reports.
Token preservation is preferable when easy, but never more important than
clean architecture.

## Alpha is the compatibility baseline

Before the first external Alpha:

> Clean design > old development-data compatibility.

Beginning with the first external Alpha, saved user data and upgrades become
explicit design concerns. At and after that boundary:

- saved-data changes should be versioned;
- migration and rollback strategy should be considered;
- breaking changes should be intentional;
- compatibility implications should be documented; and
- destructive resets must not be assumed acceptable.

The first Alpha does not require feature completeness. It establishes the
baseline from which user-facing compatibility is managed deliberately.

## Architectural decision rule

When replacing an existing pre-alpha system, ask:

1. Is the old system part of the intended Alpha architecture?
2. Is data that depends on it worth preserving before Alpha?
3. Would preserving it add compatibility code or architectural complexity?
4. Can the current development data simply be reset?

If the old system is unnecessary, remove it and update every active consumer to
the new system. Do not create migration infrastructure by reflex.

Compatibility may still be required for reasons unrelated to disposable local
development data, including an externally deployed service, a third-party
contract, a security/privacy obligation, or an intentionally supported released
artifact. Document the concrete requirement and removal condition. The hosted
soundboard relay migration is one such operational case because it coordinates
a deployed service, Twitch Extension, and database; its runbook owns that
transition.

## Variables architecture before Alpha

Streamhouse Hub must enter Alpha with one authoritative Variables architecture:

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
routine formats, and fallbacks to obsolete variable systems. Existing private
development routines and variables may be reset instead of migrated.

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
for private pre-alpha data.

Product ownership remains defined by `product-family.md` and `overview.md`.
Cleanup must preserve the independent Hub and AI packages, lightweight shared
boundaries, and extension ownership.

## Definition of clean for Alpha

Streamhouse should enter its first Alpha with:

- one authoritative implementation for each major subsystem;
- no known obsolete parallel architectures;
- no development-era compatibility layers without an Alpha requirement;
- no stale SallyBot-era naming in generic infrastructure;
- no dead migration code or obsolete current documentation;
- no duplicate sources of truth;
- clear ownership among Hub, AI, shared code, and extensions;
- storage schemas that represent the intended Alpha design; and
- tests that target the current architecture rather than discarded designs.

This is an architectural readiness standard, not a feature-completeness claim.
