# Streamhouse maintainer context

Before broad investigation or architectural changes, use
`docs/architecture.md` as the canonical implementation map.

- Read **System at a glance** and **Ownership boundaries** first.
- Use **Change-routing guide** to choose the smallest relevant file/test set.
- Do not rescan the entire repository when the architecture reference answers
  the ownership or data-flow question.
- Verify architecture-sensitive claims against current code before changing
  them.
- Update `docs/architecture.md` when ownership, persistence, protocols,
  service/trigger/task contracts, or packaging boundaries change.

Use `docs/product-family.md` for product-facing names and dependencies.
Preserve the independent Sally Bot and Sally AI Companion implementation
packages until an explicit code-and-packaging rename. Keep heavyweight AI
implementation out of the Bot bundle.
