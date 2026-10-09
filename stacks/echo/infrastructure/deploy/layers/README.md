# Lambda layer archives

Four Lambda layer zips live in the **data bucket** under a prefix that depends on
the stack:

| Stack | Bucket | Layer prefix |
|---|---|---|
| UIF (`dev-uif`) | `uif-echo-dev-data` | `code/lambda-layers/` |
| Live (`dev`) | `echo-dev-glue-scripts` | `lambda-layers/` |

Terraform references them by `s3_bucket` + `s3_key` only — no local upload and
no `filemd5()` at plan time, so CI can run `terraform plan` on a clean checkout.

```
code/lambda-layers/oracle_instant_client.zip    ~127 MB   (UIF)
code/lambda-layers/gcs_layer.zip                 ~10 MB
code/lambda-layers/silver_bq_layer.zip           ~22 MB
code/lambda-layers/controller_bq_layer.zip      ~1.5 MB
```

**None of them are in git.** This directory is gitignored for optional local
copies used when uploading to S3.

## Bootstrap (Phase 5)

Before the first `terraform apply` on a new UIF stack:

```bash
eval $(aws-runas -e dev)

bash stacks/echo/infrastructure/deploy/uif-echo-dev-phase5-bootstrap.sh
```

Or manually copy from live echo-dev:

```bash
aws s3 sync s3://echo-dev-glue-scripts/lambda-layers/ /tmp/lambda-layers/ \
  --exclude "*" --include "*.zip"
aws s3 sync /tmp/lambda-layers/ s3://uif-echo-dev-data/code/lambda-layers/
```

## Bucket layout (UIF only)

```
s3://uif-echo-dev-data/
  tfstate/        Terraform remote state (infra-terraform.tfstate)
  code/           Glue scripts, lambda layers, Maven jars, Glue temp
  configs/        Pipeline YAML/JSON (when synced from drafts/)
```

## The Oracle client is not reproducible

The three GCP layers can be rebuilt via `make build-*-layer` under
`infrastructure/terraform/`. The Oracle Instant Client cannot: it is a
**licensed vendor download** and has never been in version control.

**Treat the Oracle zip in S3 as the durable source of truth.**

## Updating layer content

After uploading a new zip at the same key, replace the layer version:

```bash
terraform apply -replace=aws_lambda_layer_version.gcs_client
```

`BUILD_LAYERS=1` on `uif-echo-apps-deploy.sh` rebuilds the three pip layers,
uploads them, then runs plan/apply.
