# ADR-0009 — Gold's Placement: Extract to `stacks/gold/`, or Formalize the Steward Model in Place

**Status:** Superseded by [ADR-0018](0018-gold-runtime-is-a-stack-concern.md) (2026-09-13) — the owner placed the gold runtime in `stacks/echo`, the one stack for all three medallion layers; no `stacks/gold/`. The preconditions below still applied to the move and were met in UIFPE-358 phase 2.
**Date:** 2026-08-28
**Author:** satyanarayana.gunavanthan@pearson.com
**Deciders:** framework owner (satyanarayana.gunavanthan@pearson.com); requires sign-off from whoever owns `domains/product`'s current deploy (Reyas Ahamed Habib Rahuman, per commit history) before execution, since this touches live infrastructure they built
**Scope:** Where Gold's control-plane tooling and runtime *should* live, architecturally. Does **not** cover the physical migration of live Terraform state — that is explicitly out of scope here and would be its own follow-up story if this ADR decides to extract.
**Supersedes:** none. Builds on ADR-0001 (gold foundations), ADR-0004 (independent control plane), ADR-0005 (gold layer shape). Tracked as UIFPE-223.
**Numbering note:** `0006` is contested — two unrelated, unmerged branches both claim it (`feature/UIFPE-97-adr-0006-control-plane`, about the Transform *UI*'s placement, and `feature/UIFPE-36-schema-registry`, about schema-registry placement under `capabilities/`). This document is deliberately numbered `0009` (the next open slot after `0008`) to avoid becoming a third collision. It addresses a **different question** than UIFPE-97's draft: this is about the *runtime/control-plane's* placement, not the Transform UI's. See §6 for how the two interact.

---

## Context

Gold — the Silver→Gold materialization tooling, currently hosted in `domains/product/` — was not always a domain's private concern, and evidence gathered across this session says it still isn't one in practice.

**History.** Gold was originally its own repository, `uif-dal-gold`, split from `uif-relay-echo` (the Bronze/Silver + UI platform repo) — this is recorded in ADR-0004's own context section. It was migrated into this monorepo on 2026-07-31 and relabeled a "domain" (`domains/product`), but the code's actual scope never changed: Silver→Gold materialization, entity-agnostic by design.

**It is already being consumed as a shared platform, not a domain's private logic.** `domains/program/CLAUDE.md` instructs engineers to invoke `domains/product/src/apps/scripts/sync_pipelines.py` directly — confirmed real, not aspirational. `capabilities/data/gold-control-plane.md` (UIFPE-122, merged) formally records this as the **"steward model"**: product hosts, other gold domains (`program` today; `price`/`stock` next) reference at sanctioned paths, with extraction to a shared home deferred to an explicit trigger.

**The Terraform is already named as shared infrastructure, not domain-scoped.** `domains/product/infrastructure/terraform/glue_jobs.tf` defines `generic_gold_transform` and `generic_gold_reconcile` — generic names, chosen before this ADR, by whoever wrote them recognizing the code as shared.

**The steward model's own extraction trigger has arguably already fired.** UIFPE-122 states extraction happens at "a **third** gold domain [going] live, or ADR-0006 lands." `program` went live in dev 2026-08-20 (ADR-0005's acceptance record: 14,239 rows, A1–A8 accepted) — a second gold domain, not a third by the letter of that trigger, but the separate UIFPE-97 ADR-0006 draft (dated 2026-08-08, still Proposed) sets its *own* looser re-trigger as "a second domain grows gold tables" — which is met.

**The dependency has already caused a real production incident.** Beacon's Terraform (`domains/beacon/infrastructure/terraform/iam.tf`) carries wildcard IAM grants to read `*gold*` databases/tables, with a comment reading: *"UIFPE-165: every gold export failed with s3:GetObject AccessDenied on the gold bucket until this read existed."* The cross-unit dependency on Gold was not designed ahead of time — it was discovered by an outage.

**The blueprint's own logical architecture treats this as a separate layer.** The enterprise architecture diagram (Fig 2, shared 2026-08-27) draws "Curate (medallion)" as its own box, distinct from "Relay platform — replicate," sitting between the replicate layer and the eventing/API/MCP layers above it.

**A real blocker exists to any physical move, found during this same review.** `domains/product/ui/src/lib/repo.ts` discovers every domain's registered mapping file via a build-time filesystem glob across the whole monorepo (`import.meta.glob("../../../../*/src/schemas/mapping/*/v1/*.mapping.json")`). This is a hard, load-bearing dependency on monorepo co-location that would break on day one of any physical extraction, unless replaced with a registry-backed lookup first. There is no reason to assume this is the only such assumption in the codebase.

**`stacks/harbor/` already exists as the target shape.** UIFPE-222 scaffolded Harbor at `stacks/harbor/` — a deployable stack, not a domain — establishing the pattern this ADR would extend to Gold: `stacks/<name>/` for deployable things, `domains/<name>/` for schema-owning things, never mixed.

---

## Options

### Option A — Extract now: physically move Gold into `stacks/gold/`

**For:** Resolves the conflation immediately. Matches the historical precedent (Gold was independent before) and the blueprint's own layering (Curate as its own layer).

**Against:** Gold has **live deployed Terraform state in dev** (ADR-0004 §CP-5 cutover completed 2026-08-04) and real consumers already wired to specific paths (`program`'s deploy workflow calls `domains/product/src/apps/scripts/*` directly). A physical move now, before the `repo.ts` monorepo-coupling blocker (and any siblings it may have) is fixed, risks breaking a live system for an architectural cleanup — exactly the kind of "decide now, execute later" split this repo's own ADR-0005 already modeled for a lower-stakes question.

### Option B — Keep the steward model, add stronger formal guardrails

**For:** Zero migration risk. UIFPE-122's steward model already works in practice — `program`'s first load, refresh, and acceptance all ran on the shared jobs with zero program-owned runtime.

**Against:** Does not fix the structural signal problem: `domains/product` still reads, in the folder tree, exactly like `domains/tax` — a schema-owning domain — when it is manifestly not one. Every new engineer has to be told, not shown, that this domain is different. It also does not close the gap UIFPE-165 already exposed: guardrails added *after* an incident are not the same as a dependency designed to be explicit from the start, and nothing in Option B forces that discipline for the *next* consumer (Beacon already found this the hard way; Harbor or a future consumer could find it the same way again).

### Option C — Decide the placement now; execute the physical move as separate, sequenced follow-up work

**For:** Gets the benefit of Option A's clarity (the target is named, decided, and can be planned toward) without Option A's risk (nothing live moves until the blocker in §Context is closed and a real migration plan exists). This is the same shape of decision ADR-0005 made for Gold's *internal* shape: "adopt now, explicitly as deferral, with a named trigger for the next step" — not a cheaper equivalent to extracting, a deliberate sequencing of it.

**Against:** Leaves `domains/product` looking like a domain for a period after the decision is made, which could read as indecision to someone who hasn't read this ADR. Mitigated by recording the decision here and referencing it from `domains/product/README.md` and `CLAUDE.md` directly (§Recommendation, item 2).

---

## Recommendation

**Adopt Option C: the target is `stacks/gold/`, decided now; the physical move is separate follow-up work, gated on explicit preconditions.**

1. **This ADR settles the destination, not the timeline.** Gold's control-plane tooling and runtime belong in `stacks/gold/`, following the same shape `stacks/harbor/` already establishes (`src/{control_plane,engine}/`, `src/apps/{pipeline,control_panel,lambdas}/`, `domains/<real-gold-domains>/` nested, `infrastructure/terraform/`, `qe/`). The real gold-domains that nest under it, per ADR-0005's own acceptance record, are `product`, `program`, `price`, `stock` — no others; `tax`/`legal`/etc. do not touch Gold and stay uninvolved.

2. **Update `domains/product/README.md` and `CLAUDE.md` now**, even before any code moves, to state plainly: this folder hosts Gold under the steward model (UIFPE-122) as an interim state, its target home is `stacks/gold/` per this ADR, and it should not be treated as a template for how a schema-owning domain is structured. This closes the "looks undecided" gap from Option C's own listed weakness, immediately, at zero migration risk.

3. **Two explicit preconditions before the physical move executes, tracked as their own follow-up story:**
   - The `domains/product/ui/src/lib/repo.ts` monorepo-coupling assumption (and a real audit for siblings — this is one confirmed instance, not a confirmed complete inventory) is replaced with a registry-backed lookup, or the physical move accepts breaking it and fixes it in the same change.
   - `program`'s (and any future consumer's) direct references to `domains/product/src/apps/scripts/*` are repointed to `stacks/gold/...` in the same change that moves the code — not as a follow-up, since a moved-but-not-repointed reference is worse than not moving at all.

4. **UIFPE-97's ADR-0006 (Transform UI placement) is a separate decision, not overridden by this one.** If Gold's runtime moves to `stacks/gold/`, the Transform UI's own placement question (currently recommending `domains/product/ui/`, deferred pending "a second domain grows gold tables" — already met) should be revisited *together with* whoever owns that draft, not decided unilaterally here. Flagging this dependency is this ADR's only claim on that question.

5. **Extraction trigger for the physical move**: both preconditions in item 3 are met, **and** a Story exists naming who owns the migration window (the same care given to Beacon's planned migration — it has the same live-state risk profile).

---

## Consequences

**Becomes easier**

- New engineers stop learning "some domains aren't really domains" by trial and error — the folder tree will eventually say it directly.
- Future gold-domains (a fourth, fifth) inherit a stack that's honestly named for what it does, instead of extending the steward-model workaround further.
- Beacon's and Harbor's eventual dependencies on Gold become a designed contract (registered, versioned) instead of an ad hoc IAM patch discovered by outage.

**Becomes harder**

- Two explicit preconditions (repo.ts, consumer repointing) must be tracked and closed before any code moves — this is real, non-trivial work, not a rename.
- `domains/product` carries a "this is interim" note for an unspecified period between this ADR and the follow-up migration story — a real, acknowledged awkwardness, not hidden.

**Accepted**

- This ADR does not itself close UIFPE-97's stale trigger question — that's named as a dependency for whoever owns that draft, not resolved here.
- The physical migration is real future work with real risk (live Terraform state, active consumers) — this ADR names the destination and the preconditions, it does not make the move safe by itself.

---

## Decision Required

1. **The destination** — `stacks/gold/`, nesting `product`/`program`/`price`/`stock` as the real gold-domains (recommended), or keep Option B (steward model, guardrails only) instead.
2. **Item 2 of the recommendation** — update `domains/product/README.md`/`CLAUDE.md` now, regardless of when the physical move happens.
3. **The two preconditions in item 3** — agreed as blocking, or is there a reason the `repo.ts` coupling (or the consumer-repointing risk) is lower than assessed here?
4. **Who owns the eventual migration story** — and whether it's sequenced before or after Beacon's own planned migration into `stacks/beacon/`.
