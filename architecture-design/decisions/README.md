# Architecture Decision Records (ADRs)

## What this is

The immutable log of significant architecture decisions for UIF. When a standard, pattern, or structural choice is made or changed, it is recorded here as one ADR. ADRs make the reasoning visible to engineers who weren't in the room when the decision was made.

## Convention

- One file per decision: `NNNN-short-title.md` (zero-padded sequence).
- Status: Proposed → Accepted → (Superseded by NNNN).
- Never rewrite an accepted ADR to reverse it — supersede it with a new one that references the old.

## Template

```
# NNNN. <Title>

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context
What forces are at play, what problem this decides.

## Decision
The choice made, stated plainly.

## Consequences
What becomes easier, what becomes harder, what we accept.
```
