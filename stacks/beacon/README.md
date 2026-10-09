# beacon

Self-service outbound data exports for every UIF domain — the platform behind
RELAY **Beacon**. A domain team (product today; hr, legal, identity as they
onboard) configures an export in a web control panel — pick a source, map
fields, set quality gates, choose a destination — and the platform validates,
approves (four-eyes), versions, runs, and delivers it. No domain imports this
domain's code: domains onboard by adding **configs** under their own
`src/schemas/beacon/` (see `domains/product`).

> **Migrated from `domains/beacon/` to `stacks/beacon/`** (ADR-0010) — a
> deployable stack, not a schema-owning domain.

The standard this stack implements lives in
[`capabilities/data/beacon.md`](../../capabilities/data/beacon.md);
the full design (decisions D1–D23) in
[`docs/beacon-export-design.md`](docs/beacon-export-design.md).

## Component map

| Piece | Where | Runs as |
|---|---|---|
| Control-panel API (FastAPI) | `src/apps/control_panel/` | **Lambda** behind API Gateway (Mangum, UIFPE-226) |
| Export transform (PySpark) | `src/apps/beacon_export/` | managed **Glue 5.0 job** (launched per batch by Step Functions, UIFPE-209) |
| Delivery/record/emit executors, scheduled starter | `src/apps/beacon_export/lambdas/` | Lambda functions |
| dynamic-mapping engine | `src/libs/dynamic_mapping/` | library — shipped in the API zip and the Glue engine zip, never deployed alone |
| Web UI (React, platform-shell look) | `ui/` | S3 + CloudFront |
| API contract | `src/api/openapi.yaml` | registered contract (contract-first) |
| Infra (Glue, Lambda, API Gateway, Step Functions, EventBridge Scheduler, DynamoDB, S3, CloudFront) | `infrastructure/terraform/` | Terraform |
| Deploy kit (zip/UI builds) | `infrastructure/deploy/` | PowerShell scripts |

This stack ships **zero container images** — nothing always-on, nothing to
patch. The Fargate pipeline and the last Dockerfile were deleted at
UIFPE-215's probation exit (2026-09-02); see `CHECKLIST.md` for the closed
one-image-rule deviation.

## The flow (design doc §runtime, D20)

```
UI / MCP / CI ──> control-panel API ──> draft → validate → submit → approve (4-eyes)
                                        └─> publishes immutable config pair v<N> to S3   [D22]
Run-now / schedule ──> Step Functions ──> Glue PySpark (transform → gates → render → package)
                                          └─> deliver λ ──> SFTP / S3 / email            [D16]
                                          └─> record_run λ ──> DynamoDB runs table
                                          └─> emit_event λ ──> EventBridge
```

Quality gates fail **closed**: a breach aborts before rendering and nothing
reaches the destination (D4).

## Working on this stack

Run from the monorepo root. `py` is the Windows launcher; `python3` elsewhere.

| Task | Command |
|---|---|
| Test (all three suites) | `py -m pytest stacks/beacon/qe -q` |
| Run the API locally | `py -m uvicorn control_panel.api:app --reload --port 8802` (with `src/apps` + `src/libs` on `PYTHONPATH`) |
| Run the UI locally | `npm --prefix stacks/beacon/ui run dev` (proxies `/api` → :8802; `BEACON_API_PROXY=<cloudfront-url>` to point at a deployed env) |
| Deploy UI | `infrastructure/deploy/build-ui.ps1 -Env sandbox -Region us-east-1` |
| Terraform | `cd stacks/beacon/infrastructure/terraform && terraform init -backend-config=backends/sandbox.hcl && terraform plan -var-file=envs/sandbox.tfvars` |

## Ownership (Four-in-a-Box)

FDE: `src/`, `infrastructure/` · QE Lead: `qe/` · UX Lead: `ui/` · PM: scope & Jira trace.
