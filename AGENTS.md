# Streamhouse maintainer context

Streamhouse Hub is in public Alpha. Read and follow
`docs/architecture/development-policy.md` for every architecture, persistence,
migration, compatibility, Variables, or rebrand change. User data takes
precedence over implementation convenience. Every publicly shipped Hub schema
is a supported upgrade input, even when the runtime implementation is replaced.

- Protect user data, not obsolete architecture. Upgrade publicly shipped data
  through isolated migration boundaries, then run only the current schema and
  implementation. Do not add permanent shims, dual reads/writes, or parallel
  old/new runtimes.
- Before a destructive migration, create a recoverable safety backup. A failed
  migration must leave the original user data intact and must not publish mixed
  or partially migrated durable state.
- Never reset or discard publicly released routines, Variables, counters,
  profiles, settings, credentials, or other user-owned state as a development
  shortcut. Only formats that were never publicly released remain disposable,
  and that exception must be verified and documented.
- Preserve encrypted Twitch tokens and other credentials across upgrades.
  Never expose credentials or secrets.
- Treat compatibility for deployed external services, third-party contracts,
  security obligations, or intentionally supported releases separately and
  document the concrete requirement.
- `VariableRegistry`, typed definitions, providers, context/lifetime handling,
  placeholder resolution, validation, domain-routed writes, the Variables UI,
  and Variable Picker are the intended sole Variables architecture for Alpha.
  Do not extend compatibility-only flat-variable infrastructure.
- `Events.emit()` is synchronous and invokes subscribers on the emitting
  thread. A subscriber that may receive a worker-thread event must cross a
  subsystem Qt bridge or queued Signal/Slot boundary before touching widgets.
- Never perform potentially blocking network, filesystem, subprocess, model,
  long-wait, or heavy CPU work on the Qt UI thread. Use the owning service or
  existing worker pattern and return results through Qt signals.
- Treat `MainWindow` as the composition root: it may construct, inject, and
  wire components, but substantial new domain behavior belongs in an owning
  service/store and substantial UI belongs in a focused page, panel, or widget.
- Automation task outputs use canonical `automation.<name>` definitions and
  are available only after their producer within the root routine execution.
  Do not introduce a second output catalog or flat output placeholder.

Before broad investigation or architectural changes, use
`docs/architecture/overview.md` as the canonical implementation map.

- Read **System at a glance** and **Ownership boundaries** first.
- Use **Change-routing guide** to choose the smallest relevant file/test set.
- Do not rescan the entire repository when the architecture reference answers
  the ownership or data-flow question.
- Verify architecture-sensitive claims against current code before changing
  them.
- Update `docs/architecture/overview.md` when ownership, persistence,
  protocols, service/trigger/task contracts, or packaging boundaries change.

Use `docs/architecture/product-family.md` for product-facing names and
dependencies. Preserve the independent `products/hub/` and `products/ai/`
packages. Keep heavyweight `products/ai/engine/` implementation out of the Hub
bundle. Shared packages must remain lightweight and genuinely cross-product.
