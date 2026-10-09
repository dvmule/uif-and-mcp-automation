# Schema governance — Apicurio guardrail reference implementation

Runnable, teaching-quality tooling for the Apicurio-as-guardrail lifecycle documented in [`../../../stacks/apicurio/standards/apicurio-governance.md`](../../../stacks/apicurio/standards/apicurio-governance.md). Per this repo's rule that `capabilities/` never holds executable code, everything that actually *runs* — Spectral rulesets and the Python scripts CI calls — lives here, not in `stacks/apicurio/standards/`. This is not deployed as a domain; it is imported by `.github/workflows/contract-governance.yml` and `.github/workflows/generate-sdks.yml`, and can be run locally exactly as CI runs it.

## Layout

```
spectral/           lint rulesets, one per contract type + a root aggregator
scripts/            the 4 pipeline steps (detect / lint-adjacent compat check / register / dual-sync) + SDK generation
tests/               pytest unit tests, Apicurio/AWS calls mocked
```

## Running locally

```bash
pip install -r requirements.txt

# Step 1 — detect which contracts a diff touches
python scripts/detect_changed_contracts.py --base origin/develop --head HEAD

# Step 2 — lint (per contract type)
npx @stoplight/spectral-cli lint -r spectral/openapi.spectral.yaml domains/product/src/api/product/openapi.yaml

# Step 3 — compatibility dry-run against a live registry (no-op, prints a
# skip message, if APICURIO_REGISTRY_URL is unset — see below)
export APICURIO_REGISTRY_URL=https://apicurio.example.internal
python scripts/check_apicurio_compatibility.py domains/product/src/api/product/openapi.yaml

# Step 4 — register + dual-sync (merge-to-main only, in CI)
python scripts/register_to_apicurio.py domains/product/src/api/product/openapi.yaml
python scripts/sync_to_glue_schema_registry.py --group product --artifact product-changed-data

pytest tests/
```

## A note on `invalid-ref` lint errors

Spectral's built-in `spectral:oas`/`spectral:asyncapi` base rulesets (which the three per-type rulesets here extend, with their own rules turned off — only the `uif-*` rules in this folder are active by default) resolve every `$ref` during lint, including the `https://schemas.pearson.com/...` canonical-schema and envelope references (`apicurio-governance.md` §3). That resolution needs network reachability to wherever those coordinates actually resolve — it will correctly flag a genuinely broken `$ref`, but it also means a lint run with no route to that host reports every such reference as `invalid-ref` even when the reference itself is written correctly. Confirmed locally against `domains/product/src/events/asyncapi.yaml` (real, shipped file): the `uif-*` rules ran clean and correctly, while `invalid-ref` failed only because this sandbox has no DNS route to `schemas.pearson.com`. Point CI at wherever these coordinates are actually served (the real registry, or a caching proxy in front of it) before treating `invalid-ref` as a hard gate.

## Why every script no-ops instead of failing when unconfigured

`APICURIO_REGISTRY_URL` is not provisioned in this repo yet. Every script here checks for it (or the equivalent AWS config) up front and exits 0 with a clear "skipped: registry not configured" message rather than failing — the same "scaffold-safe until wired" posture `.github/workflows/domain-ci.yml` already uses for its CI-telemetry step (`if: vars.UIF_CI_TELEMETRY_ROLE_ARN != ''`). This lets `contract-governance.yml` ship and run green today, and start actually gating the moment the team points `APICURIO_REGISTRY_URL` (a repo variable) at a real registry — no workflow change required at that point, only a variable.

## What each script does

| Script | Runs when | Does |
|---|---|---|
| `detect_changed_contracts.py` | Every PR/push | Diffs `base..head`, filters to `domains/*/src/{api,events,schemas,mcp,agents}/**`, resolves each file's `x-registry`/`registry` coordinate block, emits a JSON list for the workflow's matrix |
| `check_apicurio_compatibility.py` | Every PR | Dry-run POST to Apicurio's version-create endpoint (`dryRun=true`) for each changed artifact; exits non-zero with the registry's rejection reason on an incompatible change |
| `register_to_apicurio.py` | Push to `main` only | Real POST — registers/promotes the new version. Idempotent: Apicurio content-hashes, so re-registering unchanged content is a no-op on the registry's side |
| `sync_to_glue_schema_registry.py` | After a successful register, push to `main` only | The Dual-Sync Bridge (`apicurio-governance.md` §4) — fetches the just-registered artifact + its compatibility rule from Apicurio, maps the compatibility mode, and creates/registers the matching schema in AWS Glue Schema Registry |
| `generate_sdk.py` | `generate-sdks.yml`, push to `main` | Fetches the registered artifact fresh from Apicurio (never the local file) and invokes the language-appropriate generator; output is a CI build artifact, never committed |

`scripts/apicurio_client.py` is the shared HTTP client the first four scripts import — one place that knows the Apicurio REST API v3 shape (`/apis/registry/v3/groups/{group}/artifacts/{artifact}/versions/...`), so a future API version bump touches one file.
