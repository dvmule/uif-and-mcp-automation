# 0018. The Gold Runtime Is a Stack Concern; Contracts Stay With the Domain

- Status: Accepted (2026-09-13) — owner decision, recorded here
- Date: 2026-09-13
- Author: reyasahamed.habibrahman@pearson.com (framework owner)
- Deciders: framework owner
- Scope: Which parts of the Silver → Gold capability are foundation (shared, deployable once) and which are a domain's own; therefore where each lives in this repository and which Terraform state owns it.
- Supersedes: ADR-0009's target of a separate `stacks/gold/`. Resolves ADR-0015's parked decision D11 ("the Gold coupling"). ADR-0004's CP-1 – CP-6 (how Gold runs) stand; its CP-3 and CP-4 (a shared role and a shared scripts bucket borrowed from another team) become internal to one stack and stop being cross-team dependencies.

## Context

Gold materialisation arrived in this repository as `domains/product` because that was the first domain to need it. The code was never product-specific: one transform job, one reconcile job, one dispatcher and one set of control-plane tools serve every domain that declares a gold entity, driven by that domain's schema, mapping and pipeline config. Other domains already invoke the tools by path, a "steward model" that UIFPE-122 wrote down as an interim state and ADR-0009 proposed to end by extracting Gold into its own stack.

Meanwhile Echo — the Bronze and Silver replication stack — moved into this repository (ADR-0015) carrying its own, older gold job definitions and, in its greenfield parallel build, Glue jobs, a config table and buckets already named to the UIF standard for gold. Two Terraform roots now described gold compute, and the naming migration (UIFPE-358) could not rename anything until one owner was named. ADR-0015 parked exactly this as D11 because Gold's owner had not been consulted.

The owner's decision, 2026-09-13:

> Foundation deployable units come under a stack: the pipeline for silver to gold, common architecture, the reusable framework. Domain-specific components and deployable units stay with the domain: schema, mapping, event delivery. Echo is common for bronze, silver and gold.

## Decision

1. **The medallion pipeline is one stack, `stacks/echo`, for all three layers.** The gold transform and reconcile jobs, the pure-logic CDC library, the gold dispatcher, the config-as-code control plane (sync, compiler, validators, artifact builders), the authoring UI and its backing Lambdas, the gold config table and the gold data and artifact buckets are Echo's. They are built and deployed once and serve every domain.

2. **A domain owns what only it can know.** Its gold master schema and intake, its mapping manifest and SQL, its pipeline configuration, and its event delivery (contracts, processor configuration, clients) live in `domains/<domain>/` and deploy from there. So do serving surfaces built for one domain's consumers, such as a read API or a serving copy of one entity: they are consumers of gold, not part of it.

3. **The split is by question, not by folder history.** "Would a second domain need exactly this, unchanged?" — then it is foundation and belongs to the stack. "Does this encode one domain's data or consumers?" — then it is the domain's. A file that answers both is split, not placed by majority.

4. **Terraform follows ownership, one root per resource.** Foundation resources are declared in the stack's Terraform and in no domain's. The move of live state is sequenced (below), never done by declaring the same resource twice.

5. **ADR-0009's destination changes; its preconditions stand.** There is no `stacks/gold/`. The coupling it flagged — the authoring UI discovering domain artifacts by a build-time path — is still a coupling to this monorepo's layout and stays acceptable while the UI and the domains share the repository.

## Sequence

The decision lands in steps, each merged and validated before the next:

| Step | What moves | Behaviour change |
|---|---|---|
| 1 | Code, tests and the authoring UI move into the stack; workflows and Terraform read the new paths | none — same jobs, same deploys |
| 2 | Foundation resources leave the domain's Terraform state and enter the stack's | none — `removed` blocks in the domain root, `import` blocks in the stack root |
| 3 | The live pipelines cut over to the stack's standard-named jobs, dispatcher, config table and artifacts bucket; the domain-side dispatcher rules stop; roles and the Lake Formation tag take their standard names | the names consumers see |
| 4 | The previous-named estate is retired after a verification window | none |

Step 1 is UIFPE-358's phase 2 first pull request. Data stores had already moved to standard names in phase 1 (databases and the data bucket) while the compute stayed put, which is what made the ordering above possible without a second rebuild.

## Consequences

- **One deployable for the pipeline.** Adding a gold domain becomes a domain concern only: a schema, a mapping, a pipeline config, a deploy workflow that ships artifacts and syncs config. No domain hosts runtime code for another.
- **The domain that hosted the runtime shrinks to a domain.** Its folder holds contracts, configuration, event delivery and its own serving surfaces. Its Terraform keeps only those once step 2 completes.
- **Tests split the same way.** Runtime and control-plane tests live with the stack; a domain's tests exercise its artifacts against the runtime by import. Two test roots ran on a domain change until step 2 moved the deploy; the stack's workflow now gates on the runtime suite, the domain's on its own.
- **Two dispatchers exist for one step.** Echo's scheduler and the gold dispatcher differ in one behaviour — the gold one refuses to run without an explicit environment — so they merge at step 3, not step 1, and the gold one keeps a sibling resource-name helper until then.
- **What gets harder.** A change to the shared runtime now fires the deploy of every domain whose workflow triggers on the stack paths; that is the correct cost of a shared component and is bounded by the path filters naming only the gold files.

## What this does not decide

- The authoring UI's merge into Echo's own UI shell. It moves as a sibling app and is deployed unchanged; folding it into the shell is UIFPE-97's question.
- The cross-team ask in UIFPE-358 (the shared execution role's name) — unchanged by this ADR, only now a question inside one stack.
