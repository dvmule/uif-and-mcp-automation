# 0021. Repo-First Pipeline Onboarding for Echo — DynamoDB Becomes a Projection

- Status: Proposed (2026-09-21) — not yet decided
- Date: 2026-09-21
- Author: satyanarayana.gunavanthan@pearson.com
- Deciders: Reyas Ahamed Habib Rahuman, Vaibhav Yenorkar, Rajnivas Karunakaran — the three engineers who raised this
- Scope: How a new table gets onboarded to Echo's replication pipeline — where the configuration is authored, what gates it before it runs, and how it reaches every environment.
- Related: [ADR-0018](0018-gold-runtime-is-a-stack-concern.md) (foundation-vs-domain test, reused below), [ADR-0019](0019-build-tag-release-manifest-for-non-container-artifacts.md) (same proposed→accepted path), `stacks/event/README.md` (the real, working pattern this design generalizes from).

## Context

Raised in a real onboarding conversation: a user sets up a new replication job through the self-service portal. `stacks/echo/src/apps/cli/register_pipeline.py:137` writes the config straight to DynamoDB — `dynamo.Table(config_table).put_item(Item=item)` — no git commit, no PR, no review. `stacks/echo/CLAUDE.md` already names the danger this creates in its own words: *"any config file carrying a watermark is... a loaded gun"* and *"no script protects runtime-owned fields."*

The three engineers converged, live, on the right instinct — Rajnivas: *"everything has to be in the repository"*; Reyas: *"putting in the repo and then workflow should insert into the DynamoDB... that will be the cleaner approach"* — but stopped short of a concrete design, and raised three unresolved questions in the same conversation: what needs a human reviewer versus a mechanical check, whether a table onboarded from the production portal should fan out to dev/UAT too, and what a user sees while a review is pending.

This is not a hypothetical risk. It is how Echo's onboarding works today, for every table, in every environment.

## Decision — the rule (proposed)

**A pipeline's configuration is authored in git; DynamoDB is a projection of it, never written by hand.** The record of truth for "which table replicates, from where, with what settings" is a file in this repository. The runtime reads DynamoDB only because a Glue job cannot read git at execution time — that is an implementation detail of the runtime, not a statement about where the config actually lives.

## Proposed mechanics

The rule is the point; the following is open for the three deciders to adjust:

1. **Portal submission opens a PR**, not a `put_item`. The changed artifact is one file: `domains/<domain>/infrastructure/terraform/pipeline-configs/<table>.yaml`. This is the same shape as `stacks/event`'s own real, working `pipeline-configs` row — Echo generalizes a pattern that already ships, it does not invent a new one.

2. **CI validates mechanically, before any human looks at it**: duplicate-table detection, the same required-field check `register_pipeline.py` already performs, and the naming convention (`.claude/rules/naming/`). A request that fails these never reaches a reviewer.

3. **Human review is reserved for judgment calls only** — is this table's volume going to cost something, does it cross a domain boundary, is the partition/PK choice defensible — CODEOWNERS-routed to the domain that owns the table, never one central queue. Everything mechanical in §2 is not re-litigated by a human.

4. **Merge triggers a workflow that upserts the DynamoDB row** from the merged YAML — the same "build once, the workflow writes the projection" shape Vaibhav proposed directly in the original conversation. Nothing calls `put_item` by hand again.

5. **A `status` field on the record makes the async gap visible**: `DRAFT → PENDING_REVIEW → ACTIVE`. The scheduler only picks up `ACTIVE` rows. The portal reads this field so a user sees real progress instead of a spinner with no state behind it.

6. **Fan-out is automatic; activation is not.** The same merged config seeds the row in dev, UAT and prod — answering Reyas's question directly: yes, a table onboarded from the production portal becomes available everywhere. But each environment carries its own `enabled` flag, defaulting **off** unless that environment's source table actually exists — otherwise a prod-only table creates a job that fails nightly in dev and UAT for a source that was never there.

7. **A scheduled reconciler diffs live DynamoDB against the git-declared config and alarms on drift.** This is what stops "repo is truth" from becoming fiction the first time someone hand-edits a row during an incident — the failure mode the original conversation never raised but the design cannot skip.

## What this does not decide

- **Rejection UX.** What a user sees when a reviewer declines a request — a closed PR with a comment is invisible to someone watching a portal. Left to whoever implements §5's status surface.
- **Offboarding.** The same pipeline in reverse, for retiring a table. Not designed here; flagged so it isn't forgotten the way it usually is.
- **Whether this generalizes to Beacon.** Beacon's onboarding shape has not been checked against this design. A separate decision if and when Beacon's own onboarding shows the same gap.

## Consequences if accepted

- `register_pipeline.py` stops calling `put_item` directly; it becomes (or is replaced by) the workflow that runs on merge.
- The portal's "create" action stops being synchronous — a submission is a PR, not an immediate row. The portal needs the `status` field wired in before this ships, or a user will assume it's broken and resubmit.
- CODEOWNERS needs real per-domain routing for `domains/*/infrastructure/terraform/pipeline-configs/` before review load is distributed rather than bottlenecked.
- The reconciler (§7) is new operational surface — this is the concrete first building block for whoever is picking up SRE-shaped work on Echo.
