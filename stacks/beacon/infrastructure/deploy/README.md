# deploy/ — manual deployment kit (sandbox / workstation)

Deploys the Beacon stack without CI: the same commands the GitHub Actions
deploy workflow runs, executed from a workstation. Terraform creates every
AWS resource; these scripts build and stage the artifacts Terraform points
at. Since UIFPE-215 the stack ships **zero container images** — the API is a
Lambda zip (UIFPE-226), the transform a Glue script + engine zip (UIFPE-209)
— so there is no Docker, ECR, or CodeBuild anywhere in this kit.

## Prerequisites

- AWS credentials for the target account (`-Profile`, default `gps-sandbox`)
- `envs/<env>.tfvars` filled in: real `vpc_id` / `private_subnet_ids`
  (from the platform team) — the placeholders fail loudly on purpose
- Terraform state backend bootstrapped (`backends/<env>.hcl` bucket + lock table)
- Node 18+ (UI build), Python 3.11+ (zips). The dynamic-mapping engine is
  vendored source in this tree — no wheel build.

## First deployment (two-phase apply)

The interlock: Lambdas need their zip to EXIST at the S3 key. Hence:

```powershell
cd ..\terraform
terraform init -backend-config=backends/dev.hcl

# Phase 1 — foundations only (buckets)
terraform apply -var-file=envs/dev.tfvars `
  -target=aws_s3_bucket.data -target=aws_s3_bucket.ui

# Phase 2 — stage the zips (lambdas + API; the deploy workflow also stages
# the Glue entry script, engine zip, and spark-xml jars — see
# .github/workflows/deploy-beacon-dev.yml for the exact commands)
cd ..\deploy
.\build-lambda-zip.ps1 -Env dev
# (build-ui.ps1 needs CloudFront from phase 3; run it after)

# Phase 3 — everything else converges
cd ..\terraform
terraform apply -var-file=envs/dev.tfvars
cd ..\deploy
.\build-ui.ps1 -Env dev             # sync UI + invalidate; prints the URL
```

## Updating after code changes

| Changed | Run |
|---|---|
| beacon package / Lambdas | `build-lambda-zip.ps1 -Env dev -UpdateFunctions` |
| UI | `build-ui.ps1 -Env dev` |
| any infra | `terraform apply -var-file=envs/dev.tfvars` |

## After deploy: point the panel at the stack

The API Lambda's environment already carries the table/bucket env vars and
`BEACON_STATE_MACHINE_ARN` (the only run target). For local dev shells, set
it from the terraform output `state_machine_arn`. Schedules are per-export
and author-owned (UIFPE-206): the API arms them in EventBridge Scheduler at
runtime — there is no Terraform switch to flip.

## Status of this kit

The GitHub Actions deploy workflow (`deploy-beacon-dev.yml`) owns dev; this
kit remains for the sandbox and for bootstrap of a fresh environment.
`build-images.ps1` and the CodeBuild buildspecs were deleted with the last
image at UIFPE-215's probation exit.
