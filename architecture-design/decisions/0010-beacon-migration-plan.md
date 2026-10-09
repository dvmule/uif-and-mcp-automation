# ADR-0010 — Beacon Migration Plan: `domains/beacon/` → `stacks/beacon/`

**Status:** Proposed
**Date:** 2026-08-28
**Author:** satyanarayana.gunavanthan@pearson.com
**Deciders:** framework owner; requires sign-off from whoever owns Beacon's live deploy before execution (Rajnivas Karunakaran / Reyas Ahamed Habib Rahuman, per Beacon's commit history) — this touches live infrastructure they built and operate
**Scope:** How to move Beacon's code and CI/CD safely, given it has live deployed Terraform state. Does **not** revisit *whether* Beacon belongs at `stacks/beacon/` — that's settled by the same reasoning as `stacks/harbor/` (UIFPE-222) and ADR-0009 (Gold): a deployable stack is not a schema-owning domain, and `domains/beacon`'s current location is the exact conflation this session's review (UIFPE-221) found in the first place.
**Supersedes:** none. Builds on UIFPE-222 (Harbor scaffold, establishes the `stacks/` shape) and ADR-0009 (Gold placement, establishes the decide-now/execute-gated-later pattern this ADR reuses). Tracked as UIFPE-224.
**Numbering note:** `0006` and `0007` are both already claimed by unmerged branches (`feature/UIFPE-97-adr-0006-control-plane`, `feature/UIFPE-36-{draft-schema,schema-registry}`). `0009` is claimed by this session's own ADR-0009 (UIFPE-223), still unmerged. This document uses `0010`, the next verified-open slot.

---

## Context

Beacon is categorically different from the other two moves in this sequence. Harbor was greenfield (UIFPE-222 — nothing existed, zero migration risk). Gold's physical move was explicitly deferred (ADR-0009 — decide the destination, gate the execution). **Beacon has real, live, deployed infrastructure today**, and any migration plan has to be honest about that instead of treating a folder move as cosmetic.

**What's actually live:** `domains/beacon/infrastructure/terraform/` — ~60 resources, two backend configs (dev: `pearson-ingestion-framework-tfstate-283556840805`, key `uif-beacon/dev/terraform.tfstate`; sandbox: `uif-terraform-state-sandbox-061206535585`, key `beacon-control-panel/sandbox/terraform.tfstate`), deployed via `.github/workflows/deploy-beacon-dev.yml`. Two container images (pipeline + API, a declared one-container-rule deviation per Beacon's own `DOMAIN-CHECKLIST.md`). A live React control-panel UI on the shared RELAY CloudFront distribution.

**What a real inventory of the repo found** — not assumed, grepped:

1. **`.github/workflows/deploy-beacon-dev.yml` has 11 hardcoded `domains/beacon` references**: the trigger path filter (line 45), three `pip install` lines, one `pytest` invocation, five separate `working-directory: domains/beacon/...` steps, and one `cache-dependency-path`. Every one of these has to change in the same commit that moves the folder, or the pipeline either silently stops triggering (path filter) or fails immediately (working-directory pointing at a folder that no longer exists there).

2. **`CODEOWNERS` won't follow the move.** Its rules are `/domains/` → `@uif-fde` and `/domains/*/{ui,ux,qe}/` → the UX/QE overrides. None of these glob patterns match `stacks/beacon/`. Without an equivalent `/stacks/` rule added in the same change, Beacon silently loses ownership coverage — no error, no CI failure, just nobody required to review changes to it anymore.

3. **`domain-ci.yml`'s two structural gates only watch `domains/**`.** The "Definition-of-done gate" (checks `DOMAIN-CHECKLIST.md`, `src/schemas/`, `src/Dockerfile`, `qe/` all exist) and the "Entra ID + Keeper gate" (identity/secrets, fires once a domain ships `openapi.yaml` or Helm templates — Beacon ships `openapi.yaml`, so this gate is live for it today) both stop running for Beacon the moment it's outside `domains/`. This is the same effect we *wanted* for Harbor (a stack shouldn't be domain-detected) — but Harbor had nothing real to lose by losing that gate. Beacon does.

4. **Terraform's own internal references are not at risk.** Verified directly: both backend `.hcl` files hold the state `key` as a literal string, independent of the `.tf` files' location on disk — moving the folder doesn't move or orphan the remote state. Any `${path.module}`-relative reference inside the `.tf` files (e.g. to `../../src/...`) stays correct as long as the *relative* shape between `infrastructure/terraform/` and `src/` is preserved, which a straight folder move does preserve. **The risk is not to the state itself — it's to whatever points at the state from outside** (CI's working directory, CODEOWNERS, the structural gates).

5. **Low-risk, cosmetic-only references**: `PLANNING.md` (2 mentions, both about an unrelated open question — "Analytics workstream has no stated domain; `domains/beacon/` is the closest fit" — needs a wording update, not a technical fix) and `capabilities/data/beacon.md` (2 relative markdown links). Neither blocks anything; both should be swept in the same PR for hygiene.

6. **What does *not* need to change**: relative markdown links inside Beacon's own docs. `domains/beacon/../../capabilities/` and `stacks/beacon/../../capabilities/` resolve to the same place — same tree depth, so internal cross-references inside Beacon's own `README.md`/`CLAUDE.md` survive the move untouched.

---

## The one real decision this ADR has to make

Item 3 above isn't just a checklist entry — it's a genuine fork: **does `stacks/**` need its own equivalent structural + identity gate before Beacon moves, or does `deploy-beacon-dev.yml`'s own test/build/plan steps already cover the same ground?**

Looking at what each actually checks: `domain-ci.yml`'s Definition-of-done gate is a coarse, generic check (do these 4 things exist). `deploy-beacon-dev.yml` already runs the real test suite (`pytest domains/beacon/qe`), a real Terraform plan, and real image builds — strictly more rigorous than the generic gate for the things it covers. What it does **not** replace is the generic gate's simplicity as a catch-all for *any* stack (today Beacon, tomorrow Harbor once it ships code, eventually Gold) — without it, each stack's dedicated deploy workflow is the *only* thing standing between a missing `Dockerfile` and a broken deploy, with no cheap early check.

**Recommendation: build a minimal `stacks-ci.yml` mirroring `domain-ci.yml`'s two gates, scoped to `stacks/**`, before Beacon moves — not after.** It's a small, low-risk addition (the same ~30 lines domain-ci.yml already has, retargeted), and it means Beacon's move doesn't have to be the thing that first proves whether the generic gate was actually load-bearing.

---

## Recommendation: the sequencing

**One atomic PR, not staged.** Per the same principle ADR-0009 already named for Gold's consumer-repointing: a moved-but-not-repointed reference is worse than not moving at all. Splitting this across multiple PRs creates a window where the pipeline is either broken or silently ungated.

1. **Precondition, done first, separately**: build `stacks-ci.yml` (mirrors `domain-ci.yml`'s Definition-of-done + Entra ID/Keeper gates, retargeted to `stacks/**`, checking for `CHECKLIST.md` not `DOMAIN-CHECKLIST.md` — matching the naming `stacks/harbor/` already established). Land this before the move; it's inert until something exists under `stacks/**` that it can check, and it already has a real subject in `stacks/harbor/`.

2. **The move itself, one PR:**
   - `git mv domains/beacon stacks/beacon`
   - Rename `stacks/beacon/DOMAIN-CHECKLIST.md` → `stacks/beacon/CHECKLIST.md` (matches `stacks/harbor/`'s convention; update its own internal "Domain Definition of Done" heading to "Beacon Definition of Done" the way this ADR's sibling did for Harbor).
   - Update all 11 references in `deploy-beacon-dev.yml` (or the workflow's filename itself, if the team wants `deploy-stacks-beacon-dev.yml` — cosmetic, not blocking).
   - Add `/stacks/beacon/` → `@uif-fde` and the `/stacks/beacon/{ui,ux,qe}/` overrides to `CODEOWNERS`, mirroring the existing `/domains/*/...` pattern.
   - Sweep `PLANNING.md` and `capabilities/data/beacon.md`'s references.
   - **Do not touch** the backend `.hcl` files' `key` values — verified independent of folder location (§Context item 4).

3. **Verify, before merging**: `terraform plan` from the new working directory resolves the same existing state (no resources proposed for replacement) — this is the actual proof the move was safe, not just an inference from reading the backend config.

4. **Timing**: merge away from Beacon's live deploy cadence, the same discipline `domains/product/CLAUDE.md` already documents for its own dispatcher ticks — a CI path-filter change landing mid-deploy is exactly the kind of race that discipline exists to avoid.

---

## Consequences

**Becomes easier**

- `stacks/` now has all three of its named members either built (`harbor`) or planned (`gold`, `beacon`) — the conflation UIFPE-221's review found is fully closed, not just partially.
- `stacks-ci.yml` benefits Harbor and any future stack immediately, not just Beacon — built once, ahead of need being proven the hard way.

**Becomes harder**

- This is the highest-risk of the three moves — real live state, a real deploy cadence to work around, real ownership rules to carry over correctly. Nothing about that risk is invented for this ADR; it's the actual shape of moving something that's genuinely running.

**Accepted**

- The `stacks-ci.yml` precondition is new work this ADR is introducing, not merely inheriting — it wasn't required for Harbor (nothing to check yet) or for Gold (physical move deferred). Beacon is the first case that actually forces the question, and the recommendation above answers it rather than deferring it again.

---

## Decision Required

1. **Sequencing** — build `stacks-ci.yml` first (recommended), or accept `deploy-beacon-dev.yml`'s own checks as sufficient and skip it.
2. **One atomic PR** for the move itself, or a different sequencing if the team has a reason to stage it.
3. **Who owns the migration window** and its timing relative to Beacon's live deploy cadence.
4. **File naming**: keep `deploy-beacon-dev.yml`'s current name, or rename to reflect its new path — cosmetic, but worth deciding once rather than drifting.
