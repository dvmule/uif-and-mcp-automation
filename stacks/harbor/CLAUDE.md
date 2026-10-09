# harbor — stack skill

Context for engineers and AI assistants working in the `harbor` stack.

## What this stack owns

The import half of RELAY's self-serve exchange: pull from a partner
SFTP/API source, transform against a declared ingest template, route into
whichever Pearson domain(s) need the data. See `README.md` for the
component map and current status (scaffold only, UIFPE-222).

## Boundaries (what lives where)

- **This stack**: everything import-shaped — the pipeline (`src/apps/pipeline/`),
  the control panel (`src/apps/control_panel/` + `ui/`), the ingest-template
  engine (`src/libs/`), all Harbor infrastructure (`infrastructure/`).
- **Consuming domains**: only their import **configs**, nested under
  `domains/<domain>/` — mirrors the pattern Beacon already established for
  export configs (`stacks/beacon/CLAUDE.md` §Boundaries). They never import
  this stack's code.
- **Not a domain.** Do not add this folder to any domain registry, domain
  count, or domain-scoped CI matrix. If a workflow's domain-detection logic
  would pick up `stacks/harbor/` as if it were `domains/harbor/`, that's a
  bug in the detection logic, not a reason to move this folder.

## Why this is a stack, not a domain — read before proposing structure here

Three deployable stacks — `echo` (replicate), `beacon` (export), `harbor`
(import) — sit at `stacks/<name>/`, each independently releasing its own
API + Event + Schema Registry integration, each a full vertical slice
(compute, infra, UI) rather than one small service. Domains are
schema/config contributors that nest *inside* whichever stack needs them.
Mixing the two — putting a stack under `domains/` — is exactly the
conflation found during the UIFPE-221 review (`beacon` and `product`
currently do this) that this scaffold exists to stop repeating. If a
future change proposes moving this folder under `domains/`, that's a
regression, not a simplification.

## Conventions (inherited, apply once real code lands)

- Contract-first: `src/api/openapi.yaml` will be the registered API contract
  (Schema Registry First — register before implementing, same as every
  other stack and domain).
- No empty scaffolding for capabilities this stack doesn't use yet. Each of
  `src/`, `qe/`, `infrastructure/`, `ui/` carries a `README.md` stating what
  it will hold; none carry code until a real Story names the first partner
  feed and target domain.
- Naming will follow the same `<project>-<env>-<operation>` convention
  Beacon's owner decided for new AWS resources (2026-08-26) — no `echo-`
  prefix inherited, since this stack has no legacy naming to carry.
- Follow the FDE workflow:
  `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.

## Ownership (Four-in-a-Box)

Not yet assigned.
