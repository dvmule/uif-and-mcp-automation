# product / src/mcp - MCP surface for the Product API

MCP tools this domain exposes (`../../../../capabilities/mcp/`), served by the **shared UIF MCP
Bridge** of each environment (DEV: `uif-domains-mcp`, listener `shared_bridge.base_path` =
`/uif/domains`, on the shared gateway `mcp-dev-global`). The authoritative contract
`src/api/product/openapi.yaml` is the **single source of truth**: every MCP artifact is *generated* from it (plus
its local `$ref` dependencies under `src/`), never hand-maintained, and nothing generated is
committed here.

> **Domain realization only.** This folder holds what is Product-specific: the MCP onboarding file
> `bridge-config.yaml` (domain, selected contract) and this README. The generic MCP tooling and
> the shared Anypoint platform/environment coordinates (`shared_bridge`, the common upstream)
> live in `../../../../delivery-lab/reference-implementations/mcp-bridge/` and are consumed by
> reference. No domain carries a copy.

| Product owns | Shared (anypoint-environments.yaml) |
|---|---|
| REST asset `uif-product-api` (convention `uif-<domain>-api`) and its release version (`release_version.py`) | Bridge instance and the ONE MCP asset `uif-domains-mcp` (tools of every domain) |
| route label `uif-product-api` (unique across domains; `X-UPSTREAM-NAME`) | listener base path `shared_bridge.base_path` (`/uif/domains`), transport path `/mcp` |
| external instance `uif-product-<env>` (convention `uif-<domain>-<env>`) at the shared upstream | the common UIF API upstream ORIGIN (`<env>.upstream_url`) |
| the contract's server base path (`/uif/v1`) and operation paths | Anypoint org / env / gateway / TLS |

## Path composition

```
backend request = upstream ORIGIN (shared)        ${UIF_UPSTREAM_ORIGIN}           (GitHub Environment variable via anypoint-environments.yaml)
                + servers[0] base path (contract) /uif/v1
                + operation path (contract)
```

## Current tools (generated from `src/api/product/openapi.yaml` v3.0.0)

- `get_product_productid_organization_organizationcode` - GET `/product/{productId}/organization/{organizationCode}`
- `get_product_productid` - GET `/product/{productId}`

Tool names derive from method + path and must be unique across every domain of the shared
Bridge (the router routes by tool name); a collision fails the deployment closed.

## Lifecycle (what a contract change does)

```
contract change (the OpenAPI root or a $ref-erenced schema under src/)
  -> release_version.py: uif-product-api absent -> 1.0.0 | identical -> NO-OP | changed -> patch bump
  -> bundle + generate + validate from the release-stamped build copy (never committed)
  -> publish REST asset uif-product-api@<release> (OAS 3.0.3 projection of the bundle)
  -> deploy_bridge (serialized per environment): ensure/reuse upstream uif-product-<env>; merge this
     domain's tools into the shared MCP asset; shared Bridge absent -> CREATE | route absent -> ADD |
     route changed -> UPDATE IN PLACE | identical -> NO-OP | unexpected -> REFUSE
  -> conformance (tools/list of the shared Bridge)
```

Every other domain's route is preserved on every path; the pipeline never deletes or recreates
a Bridge (`capabilities/mcp/mcp-bridge.md`).
