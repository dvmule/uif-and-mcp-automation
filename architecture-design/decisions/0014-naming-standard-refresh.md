# ADR-0014 — UIF Resource Name Lookup Becomes the Naming Standard

**Status:** Accepted (2026-09-11)
**Date:** 2026-09-08 (proposed) · 2026-09-11 (accepted)
**Author:** vaibhav.yenorkar@pearson.com
**Deciders:** framework owner (Reyas Ahamed Habib Rahuman) — ratified on UIFPE-358, whose
step 1 was this ratification: the implementation had been merged and blocking CI since
2026-09-08 while the decision behind it was still Proposed.
**Scope:** Which document is the naming standard of record, the three assertions it
supersedes, and what happens to the rules it does not tabulate. Does **not** rename any
deployed resource.
**Supersedes:** the naming convention encoded in `standards/` v1.0.0.

---

## Context

`architecture-design/standards/` shipped machine-readable rules and a `PreToolUse` hook
derived from an earlier naming convention. The **UIF Resource Name Lookup** is now the
standard, and the package had drifted from it in three ways: three rule assertions became
the opposite of the standard, roughly twenty-five resource types had no coverage, and one
hook check began denying edits the standard requires — a compliant Bronze key prefix
carries a `_extract_date=` partition segment, which the kebab-case check rejected.

Drift was possible because nothing tied the rules to the hook, and nothing ran a check on
either.

## Decision

1. The lookup table is the standard of record. Where it and the v1 rules disagree, it
   wins. Three assertions are superseded: S3 buckets lose the account-id prefix, secret
   paths lose the `gip` segment, and DynamoDB tables are named for the concept rather
   than the engine. Silver Glue databases are keyed by source system, not domain, and
   data-lake key prefixes take three layer-specific shapes.
2. Rule files mirror the standard's six groups, plus core, catalog and DynamoDB
   attributes — nine files. `catalog.md` stays separate from `compute-etl.md`, where the
   standard groups it, because catalog names appear in Python and schema files while
   compute names appear in Terraform; merging them would mis-scope both.
3. The hook is driven by one `PATTERNS` table covering 29 resource types, and a test
   asserts every pattern is documented in a rule file and every vocabulary matches. A CI
   job runs it on changes to the package.
4. **BigQuery and GCS rules are retained, not ratified.** The standard does not cover
   them; GCP-native pipelines exist and would otherwise be ungoverned. They are marked as
   retained pending an owner decision.
5. **Banned tokens, the closed cadence set, zero-padding and the AWS length limits are
   kept.** The lookup table does not tabulate them and does not contradict them. Length
   limits in particular are platform facts — dropping the 63-character bucket check would
   let the hook approve names AWS rejects.
6. **`{service}` means the workload, and `{purpose}` means the contents** (added
   2026-09-10). Both slots were used in the rule files without a vocabulary entry, and
   both sit where `{operation}` sits in other forms, so the three were indistinguishable
   in practice. `{service}` is the workload that assumes or uses an identity — not the
   engine it runs on — with the engine name retained where a stack has exactly one
   identity for that engine. `{purpose}` is what a resource holds, fronts or attaches to.
   Both are open, like `{operation}`. Recorded as new scope: the lookup does not define
   either slot, so this is a decision rather than a derivation.

## Consequences

**Becomes easier.** A resource type maps to one rule file and one hook pattern. Twenty-five
previously ungoverned types get both. The drift that prompted this becomes a test failure
rather than a discovery.

**Becomes harder.** Nine files to maintain instead of five. Two forms the estate already
uses are now non-compliant for new resources, so engineers must read the upgrade note
rather than pattern-matching on what they see nearby.

**Accepted.** Retained BigQuery and GCS rules are unsourced until an owner ratifies or
retires them.

**Amended 2026-09-11 (UIFPE-358).** This section previously accepted a second limitation:
*"The hook cannot judge interpolated names, and much of the estate is built from
`local.name` — so its reach is narrower than 29 patterns suggests."* That was the reason
the gold estate drifted without CI noticing, so it has been fixed rather than kept: the
hook now resolves a name built from `locals {}` in the same directory and judges it once
per environment. Two consequences of the amendment:

- The reach is no longer narrower *by design*, but it is still partial. Names whose prefix
  is an expression rather than a string — the echo stack's `name_prefix`, whose value
  comes from `envs/*.tfvars` — stay unjudged, because resolving the fallback branch would
  flag names that are correct in that environment. The rule files remain the primary
  mechanism.
- The gate now refuses a **new** resource whose interpolated name resolves to a
  non-compliant value. This does not reopen the "non-compliant by design" ruling above:
  deployed names are still exempt, and exemption is scoped to the Terraform module so a
  refactor that moves a block between files is not read as a new name. What it stops is
  *adding* to the debt, which is actionable because `local.uif_prefix` already exists
  alongside the frozen `local.name_prefix`.

Two things AWS forces have no compliant value and no exemption yet: a log group derived
from a legacy Lambda's name (`/aws/lambda/echo-dev-*`), and any name a provider computes.
Those need an escape-hatch decision; until then the gate can refuse them, so they are
called out rather than papered over.

## Confirmed during review

**S3 bucket global uniqueness.** Dropping the account-id prefix means a bucket name must
be unique across all of AWS, not merely within the account — which is what an account-id
prefix conventionally guarantees. Raised with the standard's owner and **confirmed
deliberate**: the `uif-{stack}-{env}-` stem carries the uniqueness. No account segment
and no collision suffix.
