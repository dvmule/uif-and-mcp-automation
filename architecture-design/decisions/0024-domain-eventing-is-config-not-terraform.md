# 0024. A Domain's Eventing Plane Is Configuration, Not Terraform

- Status: Proposed (2026-09-24) — not yet decided
- Date: 2026-09-24
- Author: reyasahamed.habibrahman@pearson.com
- Deciders: framework owner
- Scope: How the **per-domain** half of the eventing platform is created — the bus, the dead-letter queue and its policy, the queue-depth alarm, the recorder hook, the two data uploads and the pipeline-config row. The shared platform is out of scope and does not change.
- Related: [ADR-0018](0018-gold-runtime-is-a-stack-concern.md) — the foundation-versus-domain test applied here. [ADR-0021](0021-repo-first-pipeline-onboarding-for-echo.md) — establishes that configuration is authored in git and the datastore is a projection of it; this decision extends that rule from a config row to the resources themselves, and everything ADR-0021 settles about authorship, review and drift is assumed rather than restated. `stacks/event/README.md` — documents the onboarding steps this would replace.

---

## Context

**Onboarding a domain to eventing currently means writing Terraform.** The event stack's own README says so in its onboarding section: instantiate the per-domain module, then seed the domain's config row. Both are Terraform, held in that domain's own state.

That instruction was followed exactly, and the result showed the cost. A change onboarding three domains at once produced **three Terraform files of 369 lines each which, once the domain name is normalised, differ in three lines — two of them comments.** The configuration files beside them were byte-identical. Each of the three also carried 28 state-migration blocks copied from the first domain, describing a history those three never had. The same change had to add a Terraform step to three deployment workflows, each requiring a build file, a state backend, a variables file and an environment file that did not exist.

None of that is conceptually hard. All of it is scaffolding a team must reproduce, correctly, for every domain that ever wants to publish an event — and one mistake in the template is replicated as many times as the template is copied.

**The platform already creates cloud resources from configuration, in two places, today.** The replication stack's controller creates a catalog database and its access grants through the cloud provider's SDK at the moment a table is registered, which is how dozens of source tables were onboarded with no Terraform per table. The eventing stack's own subscription provisioner already creates queues, rules and targets the same way, driven by rows in a table — and it is already permitted to, because its access policy is written against the naming convention rather than against a list of resources. A new domain's resources are therefore already covered the day they are created.

So what is missing is neither capability nor access. Seven resources were placed on the Terraform side of a line the platform had already drawn elsewhere.

**Why that placement is wrong.** ADR-0018 settled that shared, deployed-once machinery belongs to a stack, and a domain keeps only what is genuinely its own. The per-domain eventing resources pass that test as *domain* concerns — but they were built with the *stack's* tool and deployed once per domain. That is what turns onboarding into an infrastructure exercise instead of a configuration change.

---

## Decision

**A domain's eventing plane is declared in one version-controlled configuration file and created by the deployment that ships the domain. Terraform owns only what is deployed once for the whole estate.**

The line sits here:

| What | Created by | How often |
|---|---|---|
| Shared platform — controller, publish job, provisioner, recorder, redrive function, shared tables, shared roles | Terraform | Once per estate |
| A domain's plane — bus, dead-letter queue and policy, depth alarm, recorder hook, data uploads, config row | Configuration, applied at deploy time through the provider's SDK | Once per domain, as a configuration change |

A domain that wants to publish events adds a file and opens a pull request. It does not acquire a Terraform root, a state backend, a provider lock or a build file.

---

## Mechanics

Only what ADR-0021 does not already settle.

1. **A sync command applies the file**, run as a step in the domain's existing deployment workflow — the same position and shape as the command that already syncs gold configuration. It **plans by default and writes only when explicitly asked**, so a deployment step is unambiguous about changing state and a pull request can show the diff before approval.

2. **It reconciles; it does not only create.** Anything the file no longer declares is removed, and anything the runtime owns is carried over rather than overwritten. The gold sync already maintains a list of runtime-owned paths for exactly this reason, and that list is the model. Without this, the decision trades Terraform's ability to remove what you stop declaring for a slow accumulation of resources nobody owns.

3. **Drift is detected on a schedule.** A periodic run in plan-only mode reports any difference between the declared plane and the live one. This is the reconciler ADR-0021 already proposes, serving both concerns from one mechanism, and it is the answer to the fair objection that Terraform would have noticed a hand-edit where a create-only script will not.

4. **The alarm stays declarative.** A metric alarm is the one resource here where a declarative tool is genuinely better. The observability work already derives one alarm set per gold entity from the domains' own files, so the alarm is produced by the shared stack reading the domain registry — not created by the sync, and not hand-maintained per domain.

5. **Subscriptions do not move.** The provisioner already creates them from configuration at runtime, and nothing here changes that.

---

## Migration

The domains with no Terraform state simply onboard by the new path.

**The domain that already holds its plane in Terraform state is the only real risk, and its resources must be adopted rather than recreated.** Removing them from state and letting the sync claim them in place is safe. Destroying and recreating them is not: recreating a bus drops the rules attached to it, and recreating a queue loses whatever is sitting in it.

The migration step is therefore a state removal followed by a plan-only sync that **must report no changes** before anything is applied. If that plan shows a create, the adoption is wrong and the migration stops.

Sequence: prove the sync on a domain with no state first, then adopt the established domain once the command has been exercised. Not the other way round.

---

## Consequences

**Easier.** Onboarding a domain becomes a reviewed file rather than an infrastructure exercise. The duplication disappears — one code path instead of one copy per domain, so a fix lands once. A domain team needs no Terraform knowledge, no state and no deployment permissions beyond what the shared stack already holds. It also removes the situation where editing a schema file triggers an infrastructure apply, because the domain no longer has an infrastructure root for a path filter to match.

**Harder.** Three things Terraform gives for free must be built and kept working: removal of what is no longer declared, preservation of runtime-owned fields, and drift detection. Each is a named mechanic above precisely because the decision is unsound without it. Reviewers also lose `terraform plan` as the preview, replaced by the sync's own plan — a deliberately weaker guarantee, because that plan covers only what the sync manages.

**Accepted.** A domain can no longer express a one-off resource by dropping a Terraform file into its own root, because it no longer has one. Anything genuinely specific to a single domain and outside the declared plane becomes a request to widen the shared schema. That is the intended pressure, not an oversight.

---

## What this does not decide

- **The shared platform's mechanism.** It stays in Terraform.
- **Whether the replication stack moves the same way.** ADR-0021 covers that side and is still open. The two should agree, but this does not settle it.
- **The configuration file's schema.** That belongs in the implementation and is reviewed on its own.
- **Anything about the gold layer, the registry, or subscriptions**, all of which keep their current mechanisms.
