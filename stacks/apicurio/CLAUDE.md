# apicurio — stack skill

Context for engineers and AI assistants working in the `apicurio` stack.

## What this stack owns

Three things, since [ADR-0012](../../architecture-design/decisions/0012-schema-registry-moves-to-stacks-apicurio.md):

1. **The Apicurio Registry deployment** — the running service every domain's
   schema registration and the guardrail governance layer depend on.
2. **The governance layer in front of it** — the FastAPI BFF (`src/`) and the
   React dashboard (`ui/`). These answer *who owns this schema, is it
   compliant, and who breaks if I change it*; Apicurio alone cannot.
3. **The Schema capability's standards** (`standards/`) — `guidelines.md`,
   `apicurio-governance.md`, `gold-attribute-mapping.*`. These moved here
   from `capabilities/schema/`, which no longer exists.

See `README.md` for the component map and current status, and
`ARCHITECTURE.md` for the structure, request flow and known gaps.

## Boundaries (what lives where)

- **This stack**: the registry's deployment (`infrastructure/terraform/`), the
  BFF (`src/`), the dashboard (`ui/`), their tests (`qe/`), and the Schema
  standards (`standards/`).
- **Not owned here**: the governance *tooling* CI actually runs — the Spectral
  rulesets and Python scripts in
  `delivery-lab/reference-implementations/schema-governance/`. Note the split
  this leaves: since ADR-0012 the governance *policy* documents live here in
  `standards/` while the tooling that enforces them does not. That boundary
  used to be clean and is now blurred; ADR-0012 §2 records it as an accepted
  cost, not an oversight.
- **Still not a domain, and still not a RELAY stack.** `echo`/`beacon`/`harbor`
  are RELAY's three application stacks, each rebuilding its own API + Event +
  Schema Registry integration per release. Apicurio is a fourth, separate kind
  of `stacks/` member — cross-cutting platform infrastructure every domain and
  every RELAY stack depends on, not a RELAY application. Don't fold it into
  RELAY's component count, and don't add it to any domain-scoped CI matrix or
  domain registry.
- **The registry container is not ours.** It is an off-the-shelf image. `src/`
  is the layer in front of it, never a fork of Apicurio — do not add code here
  that belongs upstream.

## Why this is a stack, not a `capabilities/` folder — read before proposing structure here

`capabilities/infrastructure/` holds standards and reference material only
(`docker.md`, `kubernetes.md`, one example IAM policy) — every capability
folder in this repo does, per the root `CLAUDE.md` rule that `capabilities/`
and `architecture-design/` never contain executable code. Real, applied
Terraform for a running service always lives with that service's own
deployable unit (`domains/<domain>/infrastructure/`,
`stacks/<name>/infrastructure/`) — never in `capabilities/`.

**This rule now holds with no exceptions anywhere in the repo.** It briefly
did not: [ADR-0006](../../architecture-design/decisions/0006-schema-registry-under-capabilities.md)
put this service's code under `capabilities/schema/` as a scoped carve-out,
because when it was written there was no category for a deployable unit that
isn't a business domain. `stacks/` arrived later (ADR-0009, ADR-0010) and is
exactly that category, so
[ADR-0012](../../architecture-design/decisions/0012-schema-registry-moves-to-stacks-apicurio.md)
moved the code here and retired the exception. If a future change proposes
moving any of this back under `capabilities/`, it is re-opening a decision
that has already been made and reversed once — read ADR-0006 and ADR-0012
before arguing for it.

**The one asymmetry to know about:** ADR-0012 also moved the Schema
*standards* here, so Schema is the only one of the thirteen capabilities with
no `capabilities/` folder. That was a deliberate owner decision with costs
recorded in ADR-0012 §2 — it is not a pattern to copy for the other twelve.

## Conventions

- **Naming**: the authority is
  **`architecture-design/standards/rules/naming/`** — `00-core.md` (vocabulary,
  slot table, delimiters, length limits) plus eight per-domain files:
  `storage.md`, `networking.md`, `identity-secrets.md`, `compute-etl.md`,
  `eventing.md`, `observability.md`, `catalog.md`, `dynamodb-attributes.md`.
  This file previously cited `aws-resources.md` and `secrets.md`; neither has
  ever existed, so both citations sent readers to a 404 and the rules they
  named were never consulted. Its
  `paths:` frontmatter covers `stacks/*/infrastructure/**`, so it governs this
  stack. The shared "UIF Resource Name Lookup" artifact is a rendering of the
  same rules from `.docx` sources held outside this repo; where the two differ,
  the in-repo files are what CI and review can actually check against.

  Base form `uif-{stack}-{env}-{operation}`, with the per-type suffixes Beacon's
  Conventions name: `-sg`, `-alb`, `-tg`, `-api`, paired `-role`/`-policy`.
  Three specifics that are easy to get wrong:
  - **Log groups are platform-prefixed**, and for ECS that prefix is `/ecs/`,
    not `/aws/ecs/` — `00-core.md`'s delimiter table gives
    `/ecs/uif-echo-dev-api`.
  - **S3 buckets take the base form, with NO account-id prefix.** This file
    used to assert the opposite -- that the account id came first, as the one
    exception -- which `storage.md:25` explicitly retired: "There is no
    account-id prefix. The previous convention put the account id first." The
    bucket is `uif-apicurio-dev-ui` (`frontend.tf`), and the old form
    `283556840805-s3-uif-apicurio-dev-ui` is now actively DENIED by the
    installed naming guard, so following this file as written would have
    produced a name the hook refuses.
  - **Secrets Manager is slash-delimited** —
    `uif/{stack}/{env}/{system-type}/{system}`, per `identity-secrets.md`. A
    hyphen where a slash belongs drops the secret out of every wildcard-scoped
    IAM policy and fails later at `GetSecretValue`, not at creation. Keeper
    Universal Secret Sync matches vault records to AWS secrets by name, which
    is why the form matters beyond tidiness.
    There is no `local.secrets_prefix` any more: it was removed when the
    database credential became RDS-managed, since RDS names its own secret
    `rds!db-<resource-id>` and that name cannot be set. The one secret this
    stack does own is the CI authorizer secret in `authorizer.tf`, which
    carries the slash form directly.

  This file previously documented `apicurio-<env>-<operation>` with no `uif-`
  prefix, citing Beacon's 2026-08-26 `<project>-<env>` decision, which the owner
  instruction of 2026-09-07 superseded (`stacks/beacon/CLAUDE.md` Conventions).

  The `apicurio` stack token is sanctioned — added to `00-core.md`'s `{stack}`
  slot by owner decision on 2026-09-08. Worth knowing the route it took: a new
  stack directory is **not** a valid `{stack}` token until it is added there,
  so a future stack needs that edit as well as its folder.
- **Secrets via ECS's native `secrets` block** (`infrastructure/terraform/ecs.tf`),
  not Beacon's self-fetch-at-runtime pattern
  (`stacks/beacon/infrastructure/terraform/iam.tf`) — deliberate deviation,
  documented at the point of use: there's no application code of ours
  here to do the fetching, since this is an off-the-shelf image.
- **RDS is new to this repo.** No other domain or stack provisions a
  managed relational database; `infrastructure/terraform/rds.tf` is the
  first instance of that pattern, not a copy of an existing one — read it
  with that in mind before treating anything in it as settled convention
  for a future RDS use elsewhere.
- **Apicurio v3, not v2.** The image is `apicurio/apicurio-registry:3.3.2`
  and the BFF targets `/apis/registry/v3`. Do not reintroduce
  `apicurio-registry-sql` — that variant has no 3.x tags, and the
  `-sql:3.0.6` this stack once pinned was an image that does not exist. The
  v2→v3 wire differences are absorbed entirely inside
  `src/app/clients/apicurio_client.py`; its module docstring is the reference.
- **Layering, one direction only.** `src/app/api/v1/` → `src/app/services/` →
  `src/app/clients/`. Routers hold no business logic; services never import
  `httpx` or FastAPI; clients are the only place that knows an external
  system's shape — which is why the v3 port touched one file. In `ui/`:
  `service` → TanStack Query hook → component. Never `fetch` in a component,
  never server state in Zustand, never a default export.
- **The schema body is authoritative** (ADR-0001). Apicurio `labels` are a
  derived index. The validation rule id is `properties_mismatch` and stays
  that name even though v3 renamed the field to `labels` — it is a published
  contract the UI and the governance docs key on.
- **`bff_desired_count` stays at 1.** The compliance cache and the Delivery
  Lab watermark store are in process memory, so a second task behind the ALB
  would serve divergent state. Fix the state before raising the count.
- **Tests live in `qe/`**, and `qe/conftest.py` is the only place that edits
  `sys.path`. The stack-root `pytest.ini` must restate
  `--import-mode=importlib` because pytest reads exactly one inifile.
- Follow the FDE workflow:
  `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.

## Ownership (Four-in-a-Box)

Not yet assigned.
