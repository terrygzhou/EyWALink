# ADR-0001: Record architecture decisions

- Status: accepted
- Date: 2026-08-01

## Context

EyWALink builds self-hosted, zero lock-in private AI infrastructure. As the
organisation grows, architecture choices must be documented so agents and
humans can reason about past decisions without re-litigating them.

## Decision

We adopt Michael Nygard's lightweight ADR format: each decision lives in
`docs/adr/NNNN-title-with-dashes.md` and contains Context, Decision, and
Consequences sections. ADRs are immutable once accepted; superseding decisions
are recorded as new ADRs that reference the one they replace.

## Consequences

- New decisions are cheap to record and review.
- Old decisions stay visible, preserving rationale for future maintainers.
- Contributors must update ADRs when making significant architecture changes.
