# Streamhouse maintainer context

Streamhouse is pre-alpha. Read and follow
`docs/architecture/development-policy.md` for every architecture, persistence,
migration, compatibility, Variables, or rebrand change. Before the first
external Alpha, clean intended architecture takes priority over compatibility
with disposable development-era data.

- Do not preserve obsolete systems, aliases, formats, or fallback paths solely
  for private pre-alpha data. Migrate active consumers and remove the replaced
  implementation, compatibility code, dead tests, and obsolete documentation.
- Development routines, variables, counters, profiles, UI state, and other
  local development data may be reset when that materially improves the design.
- Prefer preserving encrypted Twitch tokens when easy, but never at the cost of
  poor architecture. Never expose credentials or secrets.
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
