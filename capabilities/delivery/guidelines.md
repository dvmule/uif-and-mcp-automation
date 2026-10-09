# Delivery — Guidelines (the worked standard)

This is the detailed, do-this companion to the Delivery capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a workflow/pipeline stage, document the CI/CD contract a domain must satisfy, verify that contract is actually enforced, and promote a build to production — using trunk-based development and GitOps so shipping is repeatable and gated on quality and security, not manual.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. Delivery is the odd one out in one respect: **its own "testing → automation → build" section *is* the pipeline every other capability's own §3 plugs into** — Delivery doesn't have a separate build step distinct from itself.

Grounding standards (all public): **trunk-based development** (short-lived branches, PR-gated merges to `main`), Semantic Versioning 2.0.0 (release/image tagging), and **OpenGitOps** (declarative desired state, continuous reconciliation — already the deploy-time model in `../infrastructure/kubernetes.md`). Tooling is a UIF mandate, not a portable choice: **GitHub Actions for both CI and CD** (`../../CLAUDE.md`).

Worked example: `.github/workflows/domain-ci.yml` (the root CI workflow) and `.github/workflows/domain-cd.yml` (the root CD promotion workflow, matrixed per touched domain) — a domain carries no local pipeline file for either.

---

## 1. Naming

**A workflow carries three names, and only one of them can block a merge.** The filename is an address: code and configuration reach a workflow through `/actions/workflows/{filename}/runs`, and a workflow's own path filter usually lists itself so that editing it retriggers it. The top-level `name:` is the label humans read in the Actions list, and it is also the string a `workflow_run:` trigger matches on. The job-level `name:` becomes the check-run name, which is what branch protection compares against. Renaming the first breaks callers, renaming the second silently stops downstream workflows firing, and renaming the third can hang every open pull request. Decide which name you mean before editing.

**Workflow files.** `kebab-case`, purpose-named, at the repo root only (`.github/workflows/<purpose>.yml`) — a domain never carries its own CI folder; monorepo CI is centralized and path-filtered (`domain-ci.yml`'s own header comment states this explicitly).

**CD promotion jobs.** `kebab-case`, promotion-environment named, in strict order in `domain-cd.yml`: `deploy-dev`, `deploy-uat`, `deploy-prod` — matching the mandated Development → UAT → Production path (`../infrastructure/kubernetes.md` "Sync & promote"). Each job's `environment:` targets `<domain>-<env>` so GitHub Environment protection rules (required reviewers on `-production`) gate the promotion.

> **`domain-cd.yml` has not yet been corrected to match.** It still declares four jobs against `<domain>-dev`, `-qa`, `-stage` and `-production`, with no UAT stage — which is why the only domain that can reach UAT or production, Product, does so through hand-written `deploy-product-uat.yml` and `deploy-product-prod.yml` rather than through this reusable workflow. Correcting the workflow and extending it to all four go-live domains is tracked on **UIFPE-378**. Until that lands, this section states the target, not the current behaviour.

**Filenames are frozen; display names are free.** Existing filenames stay as they are — they are live addresses, and nothing warns you when one goes stale. This repository's own caller shows the shape of it: the submit Lambda lists runs at `/actions/workflows/{filename}/runs`, its HTTP helper wraps no error, and its handler turns the resulting 404 into a 502 for the entire request — so renaming a workflow file breaks a page that has nothing to do with workflows. Apply new conventions to newly created workflows only, and fix readability by changing the display name instead. This mirrors the forward-only posture `../../architecture-design/standards/rules/naming/00-core.md` takes for deployed resource names, for the same reason in a different mechanism.

**A deploy workflow names its scope in both places.** File `deploy-{scope}-{env}.yml`, display name `Deploy {scope} — {Env}`, where `{scope}` names what is deployed — normally the directory under `stacks/` or `domains/`, but where two workflows deploy different substrates out of one directory it names the substrate and the two must differ. `deploy-uif-echo-dev.yml` and `deploy-echo-gold-dev.yml` are the live case: both deploy out of `stacks/echo/`, one the `uif-echo-dev-*` estate on the broad `stacks/echo/**`, the other the gold layer's own Terraform root on the narrower `stacks/echo/infrastructure/terraform-gold/**`. `{env}` is one of `dev`, `uat`, `prod`. The scope token is not optional: the path filter that carries it is inside the file, while the Actions list shows only the display name, so an unscoped `Deploy — Dev` beside several scoped siblings leaves the reader unable to tell which one just went red.

**A check workflow is `{scope}-{purpose}.yml`, displayed `{Scope} {Purpose}`,** plus an optional `— {qualifier}` where siblings genuinely do different amounts of work (`Product Gold Schemas — Validate & Publish` beside `Program Gold Schemas — Validate`). A repo-wide workflow that belongs to no single scope drops the scope token and just names its purpose (`Pull Request`, `Commit Identity`, `Generate SDKs`) — there is no sibling to confuse it with. Within one family express the scope one way, in the words or in a parenthetical but not both across siblings, or the family stops sorting together in the list where people look for it.

**Every job sets `name:`, and that name is unique across the repository.** A job without one still produces a check run, using its job id — which quietly makes an internal identifier a user-visible, possibly pinned string, so a routine refactor renames a check. Uniqueness matters because branch protection stores a required check as a bare name with no workflow attached: the same generic job name in several per-scope workflows is indistinguishable both to branch protection and to the person reading the pull request.

**The environment tokens are `dev`, `uat`, `prod` — the same three everywhere.** They match `00-core.md`, and they are what a deploy filename and display name use. The `qa` and `stage` job ids still present in `domain-cd.yml` are the deviation UIFPE-378 corrects, not a second vocabulary to follow.

**A job name containing a `${{ }}` expression cannot be a required check.** Its check-run name is not a fixed string, and a job whose matrix is built from changed paths can produce no check run at all — a pull request then waits forever for a status nothing will report. Keep templated names for fan-out and advisory reporting; when a gate must be required, give it its own job with a literal name.

> **None of this is machine-checked.** `validate_naming.py` gates only files ending `.tf`, `.tfvars` or `.json` and returns before any check runs on a `.yml` path, and its `PATTERNS` table is keyed by Terraform resource type, so no entry there can reach a workflow file. Enforcement, if it is ever wanted, means a separate checker over `.github/workflows/*.yml` plus adding that path to the standards CI trigger, which is today filtered to the standards package only. Until then these names are held by human review — the same posture § 2 below sets out for any naming convention CI's two enforced tiers do not cover.

**Image tags/releases.** SemVer 2.0.0 for anything human-facing (a release note, a changelog); the actual promoted artifact is referenced by **digest**, never a mutable tag (`../infrastructure/docker.md`).

### Renaming an existing workflow — the order that matters

1. **Display name.** Grep for `workflow_run:` and for the old string inside `workflows:` lists, then grep the whole repository for the old string again: docs and runbooks name workflows in prose, and no trigger will tell you those went stale. Update every hit in the same commit.
2. **Job name.** Check whether it is pinned as a required check. Branch protection lives in GitHub settings, not in the repository, so no grep answers this — read the repository's rulesets and branch protection through the API, or ask the owner. If it is pinned: add the new name to the required list, land the rename, let one pull request go green, then remove the old name. Never reverse that order.
3. **Filename.** Prefer not to. If you must: update the workflow's own path filter in the same commit, repoint every caller that addresses it through the GitHub API (usually both an injected variable and a hardcoded fallback in code, plus a deploy), and check that any test mentioning the file is not existence-guarded — a test reading `if path.exists():` turns the rename into a vacuous pass, so the suite stays green while the assertion no longer runs.

Do a rename in its own pull request, never alongside a behaviour change.

---

## 2. Contract documentation (the pipeline is a contract every domain must satisfy)

**A domain's CI/CD contract is not implicit — it's mechanically checked.** `domain-ci.yml`'s structural gate is the concrete, current instance of this: it fails a PR if a touched domain is missing `src/schemas/` (Schema Registry First), `src/Dockerfile` (one image = the build contract), or `qe/` (tests required) — and separately fails if `src/api/openapi.yaml` doesn't declare Entra ID as its OIDC provider, or an `ExternalSecret` doesn't reference the `keeper-secrets-manager` store (`../infrastructure/kubernetes.md` "Identity & secrets").

**What CI checks vs. what human review checks — stated plainly, not overclaimed.** CI enforces two tiers only: (1) structural presence of required files/folders, and (2) contract-specific checks where a concrete tool exists (a contract test against a registered schema, a registry compatibility dry-run, `../schema/guidelines.md` §3). Everything else in another capability's `guidelines.md` — naming conventions, resource modelling, doc quality — is enforced by **human PR review**, because a generic pipeline cannot mechanically check every capability's full prose ruleset. Don't claim CI enforces more than this.

**There is no third, advisory tier — it was tried and removed.** `.github/workflows/standards-review.yml` used to post a PR comment reviewing the diff against the relevant `capabilities/*/guidelines.md`. It never worked: it failed at startup on all 29 runs it ever had (2026-08-25 to 08-28), was disabled by hand rather than fixed, and was deleted on 2026-09-18. Two things had to be true for it to run and neither was — the action it used is refused by the enterprise Actions policy, and the framework owner had already recorded (2026-08-28) that the org would not provision its API key, an indefinite state rather than a pending one. So the two tiers above are the whole of CI, and everything else is human PR review. If an advisory tier is wanted again, it needs a mechanism that clears the Actions policy; do not re-add this one.

**The root `domain-cd.yml` documents the one promotion pipeline every domain shares** — there is no per-domain pipeline file to fill in (`../../create-domain.sh` stamps no CD skeleton); the platform standard for what each stage must do (gates, smoke tests, rollback) lives here, in this document, not reinvented per domain.

---

## 3. Testing → automation → build

> **Two promotion models exist, split by substrate — read this before following
> the CD half of this section.** What follows (`domain-cd.yml` + Argo CD +
> `values-<env>.yaml`) is scoped to **`domains/` on Kubernetes**, and as of
> 2026-09-21 it has **never executed**: `gh run list --workflow=domain-cd.yml`
> answers *"workflow not found on the default branch"*, the same dead-workflow
> condition `domain-doc-sync.yml` is in. Correcting and adopting it is tracked
> on UIFPE-378.
>
> **Anything under `stacks/` follows
> [`stack-promotion-model.md`](stack-promotion-model.md) instead** — ECS and
> Terraform, two workflows, a build tag whose annotation carries the release
> manifest. That model is live and proven through dev and uat. A stack has no
> `values-<env>.yaml` and no Argo CD application; do not mix the two.

This is the canonical shape every other capability's own §3 assembles from — restated here once, centrally:
```
lint  →  unit  →  contract/compatibility check  →  security scan  →  build (src/Dockerfile → one image)  →  publish
```
Then, on promotion (CD, GitHub Actions):
```
deploy-dev  →  smoke + SLO check  →  deploy-uat (gated)  →  smoke + SLO check  →  deploy-prod (gated, ≥2 replicas + PDB)
```
1. **CI (GitHub Actions, `domain-ci.yml`)** — runs per-domain, triggered only when that domain's path changed (monorepo path-filter, no third-party detection action, plain `git diff`). The structural + Entra ID/Keeper gates (§2) run here; contract tests specific to a capability (API/Event/Schema/Security §3s) run here too, plugged into this same job.
2. **CD for `domains/` (GitHub Actions, `domain-cd.yml`)** — promotes the **built image**, unchanged, through Development → UAT → Production; it does not rebuild per environment. Each stage's job checks readiness, runs a smoke test, and checks the SLO (`../observability/guidelines.md` §3) before advancing, and assumes AWS credentials via OIDC (no long-lived keys) scoped to that GitHub Environment.
3. **GitOps sync (Argo CD), `domains/` only** — the workflow advances the *desired state* (image digest + `values-<env>.yaml`); Argo CD reconciles the cluster to it and flags drift (`../infrastructure/kubernetes.md` "Sync & promote") — GitHub Actions orchestrates the promotion decision, Argo CD is what actually converges the cluster.
4. **Plan/code sync** — every commit/PR/merge auto-transitions its Jira work item via Smart Commits or the `jira-transition.yml` workflow (`./jira-github-integration.md`) — this is Delivery's own traceability signal, not optional bookkeeping.

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **CI — GitHub Actions, mandated.** Build, test, security scan, and schema validation run on every PR; merge only on green — no domain opts out.
- **CD — GitHub Actions, mandated.** Promotion runs Development → UAT → Production as jobs in the reusable root `domain-cd.yml`; each stage gated — no domain ships by manually applying manifests.
- **Trunk-based flow with PR-gated CI.** Short-lived branches, no long-running feature branches diverging from `main` for weeks.
- **Progressive promotion with automated gates per environment** — a failing smoke test or SLO check blocks promotion to the next environment automatically, it is not a manual judgment call at 2am.
- **CI enforces structure and known contracts; PR review enforces everything else (§2).** Don't design a capability standard assuming CI will catch a violation of it unless a concrete check for that violation actually exists.
- **Plan/code sync is bidirectional** (`./jira-github-integration.md`) — code state drives Jira state automatically; it is not the FDE's job to remember to update a ticket by hand.

---

## 5. Definition of done (Delivery)

- [ ] CI workflow is centralized at the repo root, path-filtered per domain (§1) — no per-domain CI folder exists.
- [ ] The domain's job in `domain-cd.yml` follows the mandated stage order and environment names (§1) — no local pipeline file exists to check instead.
- [ ] `domain-ci.yml`'s structural gate (`src/schemas/`, `src/Dockerfile`, `qe/`) and Entra ID/Keeper gate pass (§2–3).
- [ ] Every capability-specific contract/compatibility check relevant to the changed paths is wired into the same CI job, not a separate untracked pipeline (§3).
- [ ] Each CD promotion stage runs a smoke test and an SLO check before advancing (§3).
- [ ] Argo CD reflects the promoted desired state with no unresolved drift (§3).
- [ ] Commits/PRs/merges transition the linked Jira issue automatically (§3, `./jira-github-integration.md`).
- [ ] Maps to a Jira Story (`capability:delivery` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instances: `.github/workflows/domain-ci.yml` (CI), `.github/workflows/domain-cd.yml` (CD), `.github/workflows/domain-doc-sync.yml` (checklist sync), `.pre-commit-config.yaml` (local mirror of the CI gates, repo root). Plan/code sync: `./jira-github-integration.md`. Deploy mechanics this pipeline builds and promotes: `../infrastructure/docker.md`, `../infrastructure/kubernetes.md`.
