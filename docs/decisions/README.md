# Architecture Decision Records

This directory is the permanent home for accepted Streamhouse Architecture
Decision Records (ADRs). ADRs explain durable decisions; they do not replace
the current implementation map, development status, or Git history.

## When to write an ADR

Create an ADR when accepting a significant decision about product boundaries,
public data compatibility, module contracts, protocols, security/privacy,
cross-store identity, lifecycle, packaging, or a difficult alternative whose
rationale future maintainers will otherwise lose.

Do not create an ADR for routine implementation details, reversible local UI
choices, bug fixes with no durable contract, or ideas that have not been accepted.
Use **Proposed** status for a concrete decision awaiting approval; do not write
speculative history as if it were accepted.

## File naming

Use a monotonically increasing four-digit ID and a concise slug:

```text
0001-short-decision-title.md
0002-next-decision.md
```

IDs are never reused. A superseded ADR remains in place and links to its
replacement. Dates use `YYYY-MM-DD` in the project timezone when known.

## Required format

```markdown
# ADR-0001: Decision title

- Date: YYYY-MM-DD
- Status: Proposed | Accepted | Superseded | Rejected
- Supersedes: ADR-NNNN (optional)
- Superseded by: ADR-NNNN (optional)

## Context

What verified problem, constraint, or decision pressure exists?

## Decision

What is being accepted? Separate current behavior from intended work.

## Rationale

Why does this choice best satisfy the constraints?

## Alternatives considered

- Alternative and why it was not selected.

## Consequences

Positive effects, tradeoffs, risks, migrations, and follow-up work.

## Affected components

Real repository paths, public contracts, stores, protocols, or products.

## Validation status

Tests, builds, manual validation, open gaps, and the commit/release when known.
```

## Evidence and maintenance rules

- Link to real code, tests, issues, commits, or existing design documents.
- Never invent historical dates, rationale, participants, or alternatives.
- Do not silently rewrite an accepted ADR. Add clarifying notes or supersede it.
- Keep current operational truth in [../ARCHITECTURE.md](../ARCHITECTURE.md)
  and current work state in [../DEVELOPMENT_STATUS.md](../DEVELOPMENT_STATUS.md).
- Update [../STREAMHOUSE_MASTER_REFERENCE.md](../STREAMHOUSE_MASTER_REFERENCE.md)
  when an accepted ADR materially changes product direction.

No retrospective ADRs were created in this task: existing documents clearly
record several decisions, but their original acceptance dates and full
alternatives are not consistently evidenced. Future maintainers may create
carefully sourced retrospective ADRs when the repository provides enough proof.
