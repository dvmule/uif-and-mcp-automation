# MCP Bridge — the MuleSoft path from a registered API contract to a governed MCP server

A domain that wants its API consumable by AI agents exposes it as a **MuleSoft
MCP Bridge**: a gateway-hosted MCP server whose tools are generated from the
domain's registered OpenAPI contract. This document is the UIF standard for
that path — what is generated, what is configured, who owns which piece, and
the exact automation surfaces it runs on. Everything here was proven live in
the 2026-09 MCP Bridge PoC (mcp-prep); nothing is aspirational. The MCP
capability's general standard (naming tools, documenting tool contracts,
testing) is [`guidelines.md`](guidelines.md); this page is the MuleSoft
Bridge-specific mechanism.

## Ownership boundary

**MCP enablement owns**: everything from *(registered OpenAPI contract +
deployed upstream API URL)* to a serving, conformance-tested MCP Bridge.
**MCP enablement does not own**: building or deploying the upstream API, or
producing its URL. The URL is an input supplied by the domain per environment;
MCP automation consumes it and **fails closed when it is absent** — it never
creates, guesses, or discovers an endpoint.

| Piece | Home | Owner |
|---|---|---|
| This standard | `capabilities/mcp/` (docs only — repo rule 2) | MCP capability |
| Generic entry point — discovers every ONBOARDED domain (`domains/<d>/src/mcp/bridge-config.yaml`, which selects the domain's contract), selects the affected ones, reconciles them **serially** | `.github/workflows/mcp.yml` (no per-domain workflow, no domain list, no per-domain latch) | MCP capability |
| Reusable pipeline (one domain) | `.github/workflows/mcp-deploy.yml` (`workflow_call`, called by `mcp.yml` only) | MCP capability |
| Generator / validator / projection / publish / upstream / Bridge lifecycle / conformance tooling (generic Python) | `delivery-lab/reference-implementations/mcp-bridge/` — the shared home for capability-defined executable tooling that CI runs for every domain (the `schema-governance/` precedent; repo rule 2 keeps `capabilities/` docs-only). Consumed by reference via `TOOLING_DIR`; never copied into a domain | MCP capability |
| `src/mcp/bridge-config.yaml` — the domain's MCP input contract (its ROUTE in the shared Bridge) | `domains/<domain>/src/mcp/` | Domain FDE |
| Contract↔MCP generation gate | `domains/<domain>/qe/test_mcp_generation.py` | Domain QE |
| Anypoint platform coordinates (org/env/gateway/TLS, per UIF environment) **and the shared Bridge identity** (`shared_bridge`: instance label, the ONE MCP asset, listener base path, transport path) | `delivery-lab/reference-implementations/mcp-bridge/scripts/anypoint-environments.yaml` — shared MCP platform/environment configuration beside the tooling, configured once, reused by every domain | MCP capability |
| Anypoint credentials / deploy latch | GitHub Environment secrets & repo variables (the `AWS_ROLE_ARN_DEV` / `PRODUCT_DEPLOY_ENABLED` pattern) | Platform + domain |

**Deliberately NOT standardized yet: a shared Terraform module.** The PoC
proved the official `mulesoft/anypoint` provider (v1.0.16) creates only an
instance shell: it cannot express the HTTPS/TLS listener, the source-API
`connection`, the `metadata.generatedBy` classification, routing rules, or the
outbound transcoding policy. A complete, UI-equivalent Bridge is provisioned
through one richer API call (below). Freezing a Terraform abstraction around
today's provider gaps would standardize the wrong seam — revisit when the
provider closes them (tracked in the PoC caveat list).

## One shared MCP Bridge per environment (decided 2026-09-29)

UIF domains do **not** get an MCP Bridge each. Every domain API of an
environment is served by **ONE shared MCP Bridge** — in DEV `uif-domains-mcp`
on the shared Omni Gateway `mcp-dev-global`, agent endpoint
`https://<gateway>/<shared_bridge.base_path>/mcp`. Each domain keeps its own, independently
published and versioned REST Exchange asset, its own upstream, its own selected
API instance, its own route and its own tool mappings — but they all live in
the same Bridge instance, exactly as the manually built reference Bridge shows
(`uif-stock-api` and `uif-product-api` side by side, each with its own upstream,
route and *Tools & mapping* entry):

```
                    uif-domains-mcp          ONE shared MCP Bridge (per environment)
                          |
          +---------------+----------------+-------------- ...
          |                                |
    route uif-product-api            route uif-stock-api         route uif-<new-domain>-api
    upstream uif-product-dev         upstream uif-stock-dev       (added, never replacing)
    connection -> uif-product-api@v  connection -> uif-stock-api@v
    Product tools                    Stock tools
```

The split of ownership is strict. **Domain-owned** (`bridge-config.yaml` + the
contract): `source_asset_id` and its REST release version, `route_label`
(default: the asset id; unique across domains — the router routes by it and by
tool name), the generated tools/mappings, and an *optional* `environments.<env>.upstream_url`
override. **Shared** (`anypoint-environments.yaml`): the **common UIF API upstream**
`<env>.upstream_url` (the GitHub Environment variable `UIF_UPSTREAM_ORIGIN` - the gateway ORIGIN every
domain API is served from, scheme + host only; injected into every domain's effective
config, so a new domain never states it), and in the block `shared_bridge` the Bridge
`instance_label`, the ONE MCP Exchange asset `mcp_asset_id` (tools of every
domain; its version moves independently of any REST release), the listener
`base_path`, the `transcoding_path`, and the Anypoint org/env/gateway/TLS. A
per-domain `mcp_asset_id`, `base_path` or a different `transcoding_path` in a
domain config is **refused** (`shared_bridge.effective_config`). A domain run
therefore never treats the Bridge as its own: it **reconciles one route** into
shared infrastructure (see *Bridge lifecycle*) and preserves every other
domain's route — nothing is removed because a domain is absent from the run.


## The domain input contract — `src/mcp/bridge-config.yaml`

The only hand-written MCP file in a domain. Everything else is generated from
the OpenAPI contract, which remains the single source of truth for tools.

```yaml
domain: <domain>                            # drives the conventions below
source_asset_id: uif-<domain>-api           # convention (default when omitted); the REST Exchange asset
# Derived by convention (overridable only for controlled experiments):
#   route_label          : source_asset_id (this domain's route in the SHARED Bridge; unique across domains)
#   upstream instance    : Exchange external uif-<domain>-<env> at the COMMON UIF API upstream
#                          (anypoint-environments.yaml <env>.upstream_url - shared by ALL domains,
#                          injected here by shared_bridge.effective_config; NOT repeated per domain)
# NOT here (shared-scope, anypoint-environments.yaml; a per-domain value is refused):
#   Bridge instance      : uif-domains-mcp            MCP asset: uif-domains-mcp
#   listener base path   : /uif/domains                transcoding_path: /mcp
# OPTIONAL domain override - only if we intentionally support a domain served from another
# origin later; absent by default (DEV-only automation for now; uat/prod promotion is manual):
# environments:
#   dev:
#     upstream_url: https://<service-origin>/ # the API's service ORIGIN only (may be ANY
#                                             # reachable HTTP API; not assumed API-Manager-fronted)
#     source_api_instance_id: <id>            # EXPERIMENT / OPERATOR OVERRIDE ONLY. Normal
                                            # automation never needs it: the pipeline
                                            # creates or reuses the Exchange external
                                            # (unmanaged) instance uif-<domain>-<env> at
                                            # upstream_url and binds the Bridge to its
                                            # UUID. Setting it here pins a different
                                            # source instance for a controlled experiment.
```

**Platform coordinates are configured ONCE, not per domain**: the shared MCP
platform/environment configuration
(`delivery-lab/reference-implementations/mcp-bridge/scripts/anypoint-environments.yaml`)
holds, per UIF environment, the Anypoint `org_id`/`env_id`, the shared
gateway (`mcp-dev-global` in DEV), the shared TLS context, and the
**shared Bridge identity** (`shared_bridge`: `instance_label`, `mcp_asset_id`,
`base_path`, `transcoding_path`) — all non-secret identifiers (credentials stay
GitHub Environment secrets). Two values are deliberately NOT configured
anywhere: the **gateway version** (discovered at deployment time from the
gateway target — it drifts with upgrades) and the **shared MCP asset version**
(resolved from Exchange by `deploy_bridge.py` for the merged content of every
domain: identical → reuse, absent → `1.0.0`, changed → patch bump).

**The source association is REQUIRED (decided 2026-09-11, live-proven).** The
normal UIF lifecycle binds every Bridge to an **Exchange external (unmanaged)
instance** of the domain's REST asset — the same object the Anypoint MCP
wizard's "Create new unmanaged upstream" creates — named `uif-<domain>-<env>`,
`endpointUri` = the environment's `upstream_url`, private. `deploy_bridge.py`
reuses it when it exists with that URL, creates it when absent, fails closed on
a duplicate name or a URL drift, and passes its UUID as
`upstream.connection.apiVersionId` (`--require-connection`: a create can never
be connection-less). The UUID is stable across a version group, so patch
releases keep the same association. This is what makes the API Manager UI
show the bound API and populate *MCP tools & mapping*, and what lets later
releases update the Bridge in place. A connection-less Bridge serves
`initialize` + `tools/list` but leaves both UI panels empty; it was an early DEV
experiment (2026-09-11) and is **not an accepted deployment pattern**. No API
Manager instance of the backend is required or created.

**Stable identity — resolved (2026-09-11, shared 2026-09-29).** The shared
Bridge (`uif-domains-mcp`, listener `shared_bridge.base_path`) is one stable instance per
environment; a contract or tool change of any domain is applied to that **same**
instance in place — only that domain's route moves (see *Bridge lifecycle*
below), so the identity never has to be replaced for a version change. Delete →
create is reserved for listener/path/TLS changes of the shared Bridge and is an
explicit operator action, never part of the pipeline. DEV-only automation;
promotion beyond DEV manual.

The upstream is the **common UIF API upstream ORIGIN** of the environment, configured once
in `anypoint-environments.yaml` (`<env>.upstream_url` = the GitHub Environment variable
`UIF_UPSTREAM_ORIGIN`, scheme + host only) and shared by every domain - present and future - without any domain
list, map or special case in the automation. The design rule:

```
final backend URL = shared upstream ORIGIN          anypoint-environments.yaml <env>.upstream_url
                  + domain OpenAPI servers[0] path   the CONTRACT owns its base path (e.g. /uif/v1)
                  + operation path                  OpenAPI paths (e.g. /stock/{productId})
```

`generate_mcp_metadata.py` writes `servers[0]` path + operation path into every tool mapping
and the gateway composes it with the upstream origin at call time, so a base path is never
repeated in `upstream_url` and a domain's contract remains authoritative for its base path.
A domain-specific `environments.<env>.upstream_url` in `bridge-config.yaml` is an
intentional override, reviewed as a PR - not the default. No runtime discovery.

## What is generated (one parse of the contract)

`generate_mcp_metadata.py` emits, from the domain's selected contract (bundled with its local `$ref` dependencies) alone —
regenerated by CI on every run, never committed:

- **`mcp-metadata.json` (+`.zip`)** — the MCP Exchange asset content:
  `transport {streamableHttp, /mcp}`, `tools[{name, description,
  inputSchema}]`, `connections[{kind: rest-api, ref}]`. The derivation
  (tool name = `<method>_<path-slug>`, descriptions verbatim, parameter
  schemas merged, request bodies `$ref`-inlined) was verified
  **byte-identical to MuleSoft's own UI generator output**.
- **`tool-mappings.json`** — bridge policy wiring: inbound router routes
  `{upstreamName, tools[]}` + `transcodingPath`, and the outbound tool→HTTP
  mappings (method, path = server base + operation path, query/uri/header
  DataWeave, `body`). Header wiring is live-verified; query/uri wiring uses
  the same pattern by extrapolation — verify on each contract's first bridge.

`validate_mcp_metadata.py` gates both: MuleSoft shape, MCP tool-name rules,
JSON Schema 2020-12 validity, no unresolved `$ref`s, metadata↔mappings
consistency.

## Bridge anatomy and lifecycle (live-verified)

A serving Bridge = API instance (spec → the MCP Exchange asset; HTTPS/443
listener + inbound TLS context; routing with labeled upstream +
`X-UPSTREAM-NAME` rule; upstream `connection` → the source REST asset AND the
selected API instance; `metadata.generatedBy: "mcp_bridge"`) + four policies:

| Policy | Direction | Version (pin) | Carries |
|---|---|---|---|
| `mcp-support` | inbound | 1.0.1 | `{}` |
| `mcp-schema-validation` | inbound | 1.1.0 | `validateToolSchema: true` |
| `mcp-transcoding-router` | inbound | **1.0.1** — 1.0.0 stores but cannot render `transcodingPath`; the provider registry default is a broken snapshot, always pin | `transcodingPath` + routes |
| `mcp-transcoding` | **outbound** | 1.0.1 | the tool→HTTP mappings, bound to the upstream id |

In the shared Bridge there is **one route per domain** (`routing[]` entry with
its `X-UPSTREAM-NAME` rule), **one upstream per route** (uri = the domain's
`upstream_url`, `connection` → the domain's REST asset @ its release version +
its external instance UUID), **one outbound `mcp-transcoding` policy per
upstream** (that domain's tool → HTTP mappings), and **one inbound router**
whose `routes[]` maps every domain's tool names to its route label.

**Naming is decided (2026-09-29): ONE shared instance per environment, from
`anypoint-environments.yaml` `shared_bridge`** — never derived from a domain:

| Environment | Bridge instance | MCP asset | Base path | Agent endpoint |
|---|---|---|---|---|
| `dev` | `uif-domains-mcp` on `mcp-dev-global` | `uif-domains-mcp` | `/uif/domains` (`shared_bridge.base_path`, the existing Bridge's listener path, confirmed 2026-09-30) | `https://<gateway>/uif/domains/mcp` |
| any other | configured when the environment is decided | | | |

The identity is defined **once**, in the platform configuration, and consumed
by the reusable workflow (preview, deploy and conformance URL) through
`shared_bridge.load_shared` / `effective_config`, so no step can compute a
different label, asset or path; `provision_bridge.py`'s own `uif-<domain>-mcp` /
`/<domain>` fallbacks are never reached from the pipeline (the orchestrator
hands it an *effective* config = domain inputs + shared identity). Changing the
Bridge's base path is a listener change: an operator action (delete → create),
never something the pipeline does.

## Bridge lifecycle (live-proven 2026-09-11)

MuleSoft's documentation describes MCP Bridges as immutable (update = create
new → repoint → delete old). The Anypoint UI's own *Apply changes* does not do
that: traced from the API Manager client bundle, it publishes a new MCP asset
version, **PATCHes the same instance** (`xapi …/apis/{id}`, isPatch shape:
new `spec.version`/`assetVersion`, upstream `connection` at the new REST
version with the **same** external-instance UUID, routing, inline policies)
and reconciles the attached router and outbound-transcoding policies through
the documented v1 policies API — label, base path, listener, TLS and instance
id unchanged. UIF reproduces exactly that, as one orchestrated step, **for one
domain's route of the shared Bridge** (generalized 2026-09-29 from the
single-upstream Bridge; the PATCH shapes are the same, only the number of
routes grew):

```
OpenAPI content change in git (info.version untouched)               — per domain, in parallel
  → resolve REST release version   (release_version.py, Exchange READS: absent → 1.0.0 |
                                    latest == contract → NO-OP, same version | changed → patch bump)
  → generate / validate            (tools + mappings from the release-stamped contract copy)
  → publish REST asset             (OAS 3.0.3 projection, Exchange v2, <rest asset>@<release>)
  ── shared-Bridge mutation: SERIALIZED per environment (job concurrency group) ──
  → deploy_bridge
       → ensure / reuse Exchange unmanaged upstream   (uif-<domain>-<env>; UUID stable across the version group)
       → read the shared Bridge (uif-domains-mcp): identity guards BEFORE any write
       → shared MCP asset       merge THIS domain's tools + connection into the metadata the Bridge
                                binds (downloaded from Exchange; every other domain's tools preserved);
                                version: identical → reuse | absent → 1.0.0 | changed → patch bump;
                                publish only if the Bridge will change (frozen publish guard)
       ├─ Bridge absent          → CREATE  (full UI-equivalent payload, --require-connection; this route is the first)
       ├─ this route absent      → ADD     (PATCH the same instance: every existing route by id + the new route)
       ├─ this route changed     → UPDATE IN PLACE  (same instance id / label / path / TLS; only this route's
       │                                             upstream / connection / tools / router route move)
       ├─ identical              → NO-OP  (no write - not even an Exchange publish)
       └─ unexpected state       → REFUSE (identity guards, ambiguous label, missing upstream, foreign route,
                                           tool-name collision, concurrent modification, rejected write)
  → conformance                    (initialize + tools/list must show EVERY domain's tools — the merged set)
```

**Reconciliation, not replacement.** The current state is read per route
(routes paired to upstreams by id, outbound policy per upstream, router routes
by `upstreamName`); this domain's target route comes from the same CREATE
builder; the decision compares only this route and the Bridge-level binding;
the PATCH body carries every other route **verbatim** (same upstream id, uri,
connection, label) and the router's routes with only this domain's entry
replaced; the read-back then verifies, route by route, that every other
domain's upstream id, uri, connection, tools, outbound policy id and router
route are **unchanged** and that **no route was removed**. Before: `Product →
1.0.0, Stock → 1.0.0`; after a Product-only change: `Product → 1.0.1, Stock →
1.0.0`. Idempotent: re-running without a change is a NO-OP.

**Concurrency.** Several domain pipelines mutate the same Bridge, so the
mutation is protected twice. (1) In the reusable workflow only the `bridge`
job — `deploy_bridge` + conformance — runs in the concurrency group
`mcp-shared-bridge-<environment>` with `cancel-in-progress: false`: while one
domain's bridge job runs, another's is queued, never cancelled mid-write, so the
lost-update sequence (A reads, B reads, A writes, B writes A's changes away)
cannot happen through the pipeline; validation, generation and the REST publish
of different domains are NOT serialized. GitHub keeps one pending job per group
— a third domain arriving while one runs and one waits replaces the waiting one,
which needs a re-run (nothing half-written). (2) Inside `update_bridge.py` the
Bridge is re-read right before the first PATCH; if its fingerprint differs from
the state the decision was made on, the run REFUSES and nothing is written.

Guarantees: **a normal contract/tool version change never deletes or
recreates a Bridge** and never touches another domain's route; the update
touches only the one instance that passed the identity guards (label,
environment, `generatedBy`, `endpoint.type`, base path, bound shared MCP asset,
every route paired to exactly one connection-bearing upstream, one router + one
transcoding policy per upstream, this route bound to this domain's REST asset —
a route label owned by another asset is refused, as is a second route already
serving this asset); read-back requires the new binding, this route's new
connection version, tool mappings and router route exactly as sent, **unchanged
policy ids**, and every other route preserved; a rejected write stops the run
immediately (no later PATCH, no fallback to delete/recreate). A rebinding of
this route to a different instance of the **same** REST asset (e.g. from a
hand-selected instance to the pipeline's `uif-<domain>-<env>`) is an explicit,
reported UPDATE, never silent. Proven live on the Product PoC Bridge
(single route): create, upstream reuse, in-place `1.0.0 → 1.0.1` with a third
tool served by `tools/list`, and an idempotent NO-OP re-run. **ADD is four
operations, not one PATCH (live-verified 2026-09-30, Bridge `21206639`):** the
xapi PATCH's routing schema is a strict *reference* schema — `routing[].upstreams[]`
items require `id` and reject `uri`/`connection`/`policies` (HTTP 400
`RoutingValidationError`, "unevaluated properties"); the inline upstream object
is accepted only on the create POST. Upstreams are a separate sub-resource of
the instance, exactly as the documented Anypoint CLI models them
(`api-mgr:upstream:add <apiInstanceId> <uri> --label`, `api-mgr:api:edit
--routing` = routes with *upstream references and weights*). So `update_bridge`
adds a route as: **A1** `POST` v1 `…/apis/{id}/upstreams` `{label, uri}` → id
(an unrouted upstream already carrying this domain's label, left by an
interrupted ADD, is reused, never duplicated); **A2** the proven isPatch `PATCH`
with the new upstream in `upstreams[]` *by id* (uri + connection) and the new
route as `{label, upstreams:[{id, weight}], rules}`, every other route
verbatim; **A3** `POST` xapi `…/apis/{id}/policies/outbound-policies`
`{configurationData:{tools}, groupId, assetId, assetVersion, upstreamIds:[id]}`
— the outbound `mcp-transcoding` policy bound to the new upstream (the surface
the official Terraform provider uses for outbound policies, live-proven in the
2026-09 PoC on Bridges 21162919 / 21164592); **A4** the router policy `PATCH`
with the merged routes. The read-back then requires the new route to be served
by the A1 upstream, its connection, tools, outbound policy and router route
persisted, no unrouted upstream left behind, and every other route unchanged.
A2–A4 are the first time these surfaces run in this sequence in CI; run
`--preflight` first, it prints all four requests. Delete → create
(`provision_bridge.py --delete-instance`, guarded) remains available only for
listener/path/TLS changes of the shared Bridge and is never automatic.
**Versioning (decided 2026-09-15 — the Exchange RELEASE version).** The
GitHub contract is the source of truth for **content**; **Exchange state** decides
whether a new release is needed and what its version is. One owner,
`release_version.py` in the shared tooling, resolves it with targeted reads of
the domain's own two assets (never a search):

| Exchange state of `<rest asset>` | Release version |
|---|---|
| asset absent | `1.0.0` (and `<rest>@1.0.0` must not exist) |
| latest `L` exists, published projection == GitHub contract projected at `L` | **NO-OP**: `L` (a CI rerun never creates a version) |
| latest `L` exists, content differs | patch bump `L+1` (e.g. `1.0.8 → 1.0.9`; `<rest>@L+1` must not exist) |

The resolved version is stamped into a **build copy** of the contract
(`build/mcp/openapi-release.yaml`) — the authoritative OpenAPI is never
modified, and the developer never bumps `info.version` because Exchange needs
another immutable version. That ONE version is `<rest asset>@v` == this
domain's route `connection.version` in the shared Bridge; no stage computes a
version of its own (the Bridge step refuses artifacts whose embedded version
differs from `release.json`). The **shared MCP asset** (`uif-domains-mcp`,
the Bridge's `spec.version`) is versioned separately by `deploy_bridge.py`
(`shared_bridge.resolve_shared_version`) for the MERGED content of every
domain: Bridge binds `B` and merged == published(`B`) → reuse `B`; asset absent
→ `1.0.0`; else latest `L`: identical → `L`, different → `L+1` (`L+1` must not
exist). A Product-only change therefore moves `uif-product-api` `1.0.0 → 1.0.1`
and `uif-domains-mcp` e.g. `1.0.1 → 1.0.2` while `uif-stock-api` stays at `1.0.0`.
Comparison = the deterministic
OAS 3.0.3 projection of the stamped copy against the latest published `oas`
file: byte-identical, or the same YAML document when Exchange re-serialized it.
Fail closed, never guess: non-semver versions, a listed version above the
reported latest, an already-existing next version (non-linear history), an MCP
history ahead of the REST asset, or an unreadable published file all stop the
run before anything is published. The per-asset publish guard still applies at
the resolved version: absent → publish; byte-identical → no-op; **different**
content at an existing version → **fail closed** ("the immutable MCP asset
version conflicts with newly generated content and requires an explicit
versioning decision"). Dry runs (no credentials) preview with the contract's
own `info.version` and mark the release `preview`. Keep the change classes
distinct: an MCP **metadata/tool-schema**
change requires a new MCP Exchange asset version and an in-place Bridge
update; a **mapping/policy-only** change is an in-place Bridge update without
a new MCP asset (the asset holds only `mcp-metadata.json`; mappings and policy
pins live in the instance). Exchange asset versions are immutable and never
deleted — retired versions remain reproducible.

## Platform coordinates come from GitHub Environment variables

`anypoint-environments.yaml` declares, per UIF environment, WHICH coordinates the automation
needs; the environment-specific VALUES are not in Git. They are `${VAR}` placeholders resolved
at run time by `platform_config.py` - one generic resolver, no per-variable code - from the
selected GitHub **Environment**'s variables (locally: the shell). A missing variable fails the
run closed, naming it; keys ending in `_id` must be UUIDs; the upstream must be an https origin
with no path; the public host a bare host. The resolved copy is written only under `build/mcp/`
and handed to every downstream script through `--platform-config`, so the frozen provisioning
code has no environment-variable handling of its own. uat/prod later = a new block in the file
plus a GitHub Environment carrying the same variable names.

| GitHub Environment VARIABLE | Resolves |
|---|---|
| `ANYPOINT_ORG_ID` | `org_id` (UUID) |
| `ANYPOINT_ENV_ID` | `env_id` (UUID) |
| `ANYPOINT_GATEWAY_ID` | `gateway_id` (UUID) |
| `ANYPOINT_GATEWAY_NAME` | `gateway_name` |
| `ANYPOINT_GATEWAY_PUBLIC_HOST` | `gateway_public_host` (bare host) |
| `ANYPOINT_TLS_SECRET_GROUP_ID` | `tls_secret_group_id` (UUID) |
| `ANYPOINT_TLS_CONTEXT_ID` | `tls_context_id` (UUID) |
| `UIF_UPSTREAM_ORIGIN` | `upstream_url` - the common UIF API upstream ORIGIN (https, no path) |

GitHub Environment SECRETS (never variables, never in Git): `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`.
Architecture stays literal in Git: `shared_bridge` (instance `uif-domains-mcp`, MCP asset `uif-domains-mcp`,
listener `/uif/domains`, transport `/mcp`) and the naming conventions `uif-<domain>-api` / `uif-<domain>-<env>`.
Jobs that need coordinates (`generate`, `live`, `bridge`) run under the Environment; `validate` and `preview` do not.

## Multi-file contracts (the authoritative contract and its generated bundle)

A domain's REST contract is **the OpenAPI root its `bridge-config.yaml` selects
(`contract: src/api/<api>/openapi.yaml`; auto-resolved only when the domain ships exactly
one `src/api/*/openapi.yaml`) plus every recursively reachable LOCAL external `$ref`
dependency** under the domain's **`src/` directory** (the repository pattern is
`src/api/<api>/openapi.yaml` referencing `../../schemas/channels/<domain>/<schema>.json`;
`./schemas/foo.yaml#/Foo` and `../common/bar.json#/Bar` from inside a dependency work the
same way, each resolved relative to the file that references it). Developers keep authoring
those files as they are; nothing rewrites a source contract. An unreferenced file under
`src/` is not part of the contract.

The shared tooling (`contract_bundle.py`) discovers that graph and produces ONE
self-contained OpenAPI 3.1 document, the **generated bundle**: every external target
is lifted into `components/schemas/<deterministic name>` and the reference rewritten to
an internal one. That bundle - `build/mcp/openapi-release.yaml` - is a TRANSIENT BUILD
ARTIFACT (git-ignored, never committed, never written into `src/`) consumed by every
downstream stage: release comparison (a dependency-only change is a contract change and
patch-bumps; an unreferenced-file change is a NO-OP), the OAS 3.0.3 projection (which
now fails closed on any external `$ref`), Exchange publication (unchanged single-file
request), MCP generation and the contract gate. Bridge reconciliation never sees it.
`release.json` lists the bundled files (`contract_files`) for auditability.

Rules and limits (fail closed outside them): references resolve against the physical
referring file, so a dependency that declares `$id` AND contains relative external
refs is refused (JSON Schema base-URI semantics are not in the supported subset); the
destination section of an external target follows the reference's semantic context
(schema, parameters, responses, requestBody, headers, examples, links, callbacks,
securitySchemes), never the target's content - an unknown context or an external PathItem
reference is refused; absolute paths, drive letters,
`file://`, `http://`, `https://` and any reference whose canonical real path leaves the
domain's `src/` directory (symlinks resolved) are refused; a generated component name that collides with an authored one is
refused (never renamed). Lifted content keeps every JSON Schema / OAS keyword verbatim
(the projection still decides what it can translate) and relocates the document
identifiers `$schema`, `$id`, `$comment` and ANY unknown non-keyword key to `x-<key>`
vendor extensions - a generic policy, lossless, never key-specific; property names are
never treated as keywords. Recursive request-body schemas remain unsupported by the MCP
tool-schema inliner regardless of file layout (pre-existing limit).

## Automation surfaces (classified — carry this into every review)

| Operation | Surface | Class |
|---|---|---|
| Publish REST asset (classifier `oas`, `files.oas.yaml`, `properties.mainFile`, `properties.apiVersion`) — content is the **OAS 3.0.3 projection** of the 3.1 contract (see below) | Exchange API v2 asset creation | Officially documented |
| Publish MCP asset (`type=mcp`, `files.mcp-metadata.json`) | Exchange API v2 asset creation | Officially documented |
| Ensure source instance (`POST …/assets/{g}/{a}/versionGroups/{vg}/instances/external`, `{name, endpointUri, isPublic:false}`; descriptor read for `versionGroup` + `instances[]`) | Exchange API v2 | Officially documented (and the exact call the MCP wizard makes) |
| Full Bridge create (instance + listener/TLS + routing + `connection` + `generatedBy` + all four policies, **one POST**) | `POST /apimanager/xapi/v1/.../apis` — the identical call the Anypoint UI makes (payload reverse-engineered from the published UI source and reproduced live) | UI-observed API, not publicly documented |
| Reads (instance, upstreams+`connection`, policies) | API Manager v1 GETs (the UI's own read path; the xapi item endpoint is write-only) | Documented family |
| Add an upstream to an existing Bridge (`POST …/apis/{id}/upstreams` `{label, uri}`) | API Manager v1 — the Anypoint CLI's `api-mgr:upstream:add` | Officially documented (CLI) |
| In-place update / route add (`PATCH xapi …/apis/{id}`, isPatch: `upstreams[{id, uri, connection}]`, `routing[{label, upstreams[{id, weight}], rules}]`, inline policies) | The identical call the UI's *Apply changes* makes; routing items are upstream REFERENCES (live 400 otherwise, 2026-09-30) | UI-observed API, not publicly documented |
| Bind the outbound `mcp-transcoding` policy to one upstream (`POST xapi …/apis/{id}/policies/outbound-policies`, `upstreamIds` required) | The surface the official Terraform provider uses for outbound policies; live-proven in the 2026-09 PoC | Provider-used, not publicly documented |
| Router / transcoding policy configuration (`PATCH …/apis/{id}/policies/{policyId}`) | API Manager v1 | Officially documented |
| Runtime conformance | MCP streamable-HTTP `initialize` + `tools/list` against `<gateway>/<base-path>/mcp` | MCP spec |

**Exchange accepts OAS 2.0/3.0 only (decided 2026-09-10).** UIF contracts are
OpenAPI 3.1 and stay 3.1 — the git contract is the single source of truth and
is never edited for Exchange. Exchange parses specs server-side and rejects
3.1 (the retired API Catalog CLI failed on the same limit: its AMF "OAS 3.0"
parser fed a 3.1 document). The MCP capability therefore owns a **derived
build artifact**: a deterministic OAS 3.1 → 3.0.3 projection
(`oas_projection.py` in the shared tooling home) that is generated in CI, published as
the REST asset content, and never committed. Rules: paths, operations,
schemas, descriptions, examples, `servers` (base-path semantics) and
`info.version` are preserved verbatim; the only rewrite is a nullable type
array `type: [T, 'null']` → `type: T` + `nullable: true`, applied only when it
is exactly representable; **any other 3.1-only construct fails the publish
closed** (multi-type arrays, bare `type: 'null'`, `const`, `prefixItems`,
`unevaluated*`, `dependent*`, `patternProperties`, `contentEncoding`/
`contentMediaType`, schema-level `examples`, numeric `exclusiveMinimum`/
`exclusiveMaximum`, `webhooks`, `jsonSchemaDialect`, `info.summary`,
`license.identifier`, `mutualTLS`, `pathItems`). A fail-closed projection is a
contract-authoring decision for the domain, never something the pipeline
rewrites around. The publisher requires the asset version to equal the
`info.version` of the file it projects; the pipeline satisfies that with the
release-stamped build copy (`openapi-release.yaml`, see *Versioning*), so the
REST asset version is the resolved release version, and the same
absent/identical/different publish guard applies to the projection bytes.

Provider/platform caveats a pipeline must encode (all live-verified):
`Provider produced inconsistent result after apply` on instance updates =
retry-with-refresh, not failure; typed-policy HCL keys are snake_case
(camelCase = permanent plan drift); collision guards must exclude
self-managed instances; the auth scheme in the contract is a securityScheme,
not a tool input — bridge→upstream token handling is a deployment concern
decided with the upstream, never generated.

## The reusable pipeline (`.github/workflows/mcp-deploy.yml`)

`workflow_call` only; its single caller is the generic entry point
`.github/workflows/mcp.yml`. **Discovery is a convention, not a registry**: a
`detect` job lists every ONBOARDED domain (`domains/<d>/src/mcp/bridge-config.yaml`)
and selects the affected ones (`affected_domains.py`: anything under a domain's
`src/api/**`, `src/schemas/**` or `src/mcp/**` changed → that domain; the shared tooling or
either workflow changed → every discovered domain; `workflow_dispatch` takes `domains=a,b`
or `all`; a selected domain whose contract cannot be resolved - no candidate, or several
`src/api/*/openapi.yaml` without a `contract:` choice - fails the run there, clearly;
contracts without an onboarding file are listed as NOT onboarded and never deployed). Then **one matrix entry per selected domain, named after it,
runs this pipeline SERIALLY** (`max-parallel: 1`, `fail-fast: true`): a domain is
released, published, merged into the shared MCP asset, reconciled into the
shared Bridge and read back before the next one starts, and every domain
re-reads the live shared state before it writes — so Product, Price and a new
Customer in one commit can never overwrite each other, and a failing domain
fails the run with the remaining entries cancelled, never skipped silently.
Whether a selected domain actually changed is decided downstream by the
whole-contract comparison (release NO-OP) and the live route comparison (Bridge
NO-OP), so a domain selected by a tooling change ends as NO-OP. Stages:

```
validate     contract gate (qe/test_mcp_generation.py) — always
generate     artifacts from the contract + bridge-config; shared identity from anypoint-environments.yaml;
             deploy_bridge --dry-run preview; uploaded for review — always
preview      resolved plan: assets, versions, shared Bridge + route, upstream — always
── everything below requires: dry_run=false AND the domain latch
   <DOMAIN>_MCP_DEPLOY_ENABLED=true AND upstream_url set for the target env ──
live job (domain-scoped, runs in parallel across domains):
resolve-release  release_version.py --resolve: Exchange READS only → release.json +
                 openapi-release.yaml (absent → 1.0.0 | identical → NO-OP | changed → patch bump)
generate         artifacts from the release-stamped copy at the resolved version
publish-rest     idempotent Exchange publish of the source asset (OAS 3.0.3 projection) @release
hand-over        the run's build/mcp is passed to the bridge job (same run, nothing re-derived)
bridge job (concurrency group mcp-shared-bridge-<environment>, cancel-in-progress: false):
deploy-bridge    ONE step (deploy_bridge.py):
                 ensure/reuse the Exchange EXTERNAL instance of the source asset
                 (uif-<domain>-<env>, endpointUri = upstream_url, private) — the
                 wizard's own "Create new unmanaged upstream"; its UUID becomes
                 upstream.connection.apiVersionId so the APIs table and the
                 'MCP tools & mapping' panel are populated;
                 read the shared Bridge, identity guards; merge this domain into the
                 shared MCP asset (uif-domains-mcp) and resolve/publish its version;
                 then Bridge absent → CREATE (preflight: gateway target +
                 gatewayVersion discovery, TLS, marker sweep, collision scan; ONE
                 xapi POST; read-back) | route absent → ADD | route changed →
                 UPDATE IN PLACE (PATCH same instance + router + this route's
                 transcoding policy; read-back incl. every other route unchanged) |
                 identical → NO-OP | unexpected → REFUSE. Never delete/recreate,
                 never remove another domain's route.
conformance      initialize + tools/list diff vs the MERGED metadata
                 (build/mcp/shared-mcp-metadata.json), with a BOUNDED propagation
                 retry (see below)
delete/recreate  NOT part of the pipeline — explicit operator action
                 (provision_bridge.py --delete-instance, guarded) for
                 listener/path/TLS changes of the shared Bridge only
```

Secrets per GitHub Environment: `ANYPOINT_CLIENT_ID`/`ANYPOINT_CLIENT_SECRET`
(Connected App). **Live stages are implemented and five-way gated**: an
a push on `develop` (the merge of a reviewed PR — feature/bugfix/hotfix pushes
and the PR itself never run this workflow) or an explicit `workflow_dispatch`
with `dry_run=false`, the ONE shared `MCP_DEPLOY_ENABLED` latch - a REPOSITORY variable,
never a per-domain one; while it is off the `live` and `bridge` jobs are SKIPPED at job level
and the run finishes green with zero Anypoint calls - `MCP_LIVE_ENABLED` set only
inside the live jobs (the scripts' own `--execute` gate), the Anypoint
credentials present, and a resolved `upstream_url`; the bridge job depends on
the live job, so no gate can be bypassed. Validate/generate/preview run on
every run of the workflow, including dry runs; contract validation on PRs is
the domains' CI's job. Acceptance for a deployment is the conformance
stage (`initialize` + `tools/list` against the shared endpoint, expecting every
domain's tools); `tools/call` is never an acceptance criterion — a domain's
backend may not be reachable from the conformance client, and listing is the maximum
the conformance client can do by construction.

**Gateway propagation retry (live-proven 2026-09-15).** Flex Gateway pulls a
Bridge's configuration from API Manager asynchronously, so right after a
CREATE or in-place UPDATE the endpoint may answer 404 or still serve the
previous tool set (the Price CREATE converged on attempt 4). The conformance
stage therefore retries the whole `initialize` + `tools/list` check within a
**bounded** window: CI passes `--retries 6 --retry-delay 10`, i.e. up to six
extra attempts, ten seconds apart, after the initial one (60 s ceiling). Only
transport errors, JSON-RPC errors and a tool-set mismatch are retried; the
**final attempt determines** success or failure, and the run never waits
beyond the ceiling. The CLI default is a single attempt.

## Onboarding a domain (proven by Price, 2026-09-15)

A domain onboards with **domain-owned inputs only**; no generic code is copied
and nothing domain-specific is added to the shared tooling, the reusable
workflow or the shared Bridge configuration. Everything else — the REST asset
`<source_asset_id>` and its release version, upstream `uif-<domain>-<env>`, the
route label — is derived from the domain config; the Bridge, its MCP asset and
its path are the shared ones and need no change when a domain joins.

| # | The domain adds | Purpose |
|---|---|---|
| 1 | `domains/<domain>/src/api/<api>/openapi.yaml` (+ its `$ref` dependencies under `src/`) | the OpenAPI 3.1 contract — the source of truth for tools (tool names = method + path; must not collide with another domain's — the deployment fails closed if they do) |
| 2 | `domains/<domain>/src/mcp/bridge-config.yaml` | the ONBOARDING file - EVERY folder under `domains/` carries it from the moment the domain is onboarded, contract or not (the repository tests fail otherwise). `domain` only by default; `contract: src/api/<api>/openapi.yaml` when the domain ships several contracts (auto-resolved when exactly one exists; none yet → the domain is SKIPPED visibly as "contract not available" and processed automatically once it appears); `mcp_enabled: false` keeps an onboarded domain out of MCP until approved. `source_asset_id` defaults to `uif-<domain>-api`, the route label to `source_asset_id`, the upstream to the common UIF API upstream (`anypoint-environments.yaml` `<env>.upstream_url`); NO `mcp_asset_id` / `base_path` — those are the shared Bridge's |
| – | *(optional)* `domains/<domain>/qe/test_mcp_generation.py` + `conftest.py` + `requirements-dev.txt` | a domain-owned gate on top of the shared contract-derived gate the validate job always runs |
| – | *nothing in `.github/workflows/`* | `mcp.yml` discovers the domain from its onboarding file; there is no per-domain caller and no domain list to edit |
| – | *no per-domain variable* | the shared `MCP_DEPLOY_ENABLED` latch and the `dev` Environment secrets already gate every domain |

First live run of a domain: the resolver finds no REST asset and releases
`1.0.0`; the upstream is CREATEd; the shared MCP asset gains the domain's
tools (new version); the shared Bridge is CREATEd if it is the first domain,
otherwise the domain's route is ADDed; conformance lists every domain's tools.
Every later run is UPDATE in place or NO-OP. Before the first live run, a
read-only clean-state check (`release_version.py --resolve` must report
`initial`; `deploy_bridge.py --check-existing` shows which instance carries the
shared label / base path — the shared Bridge itself is expected; `deploy_bridge.py
--preflight` reports CREATE / ADD / UPDATE / NO-OP without writing) confirms
what the run would do.
