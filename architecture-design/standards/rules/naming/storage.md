---
paths:
  - "**/infra/**/*.tf"
  - "**/infra/**/*.tfvars"
  - "**/infrastructure/**/*.tf"
  - "**/infrastructure/**/*.tfvars"
  - "stacks/*/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tfvars"
  - "domains/*/infrastructure/**/*.tf"
  - "**/drafts/**/*.json"
  - "stacks/*/domains/**/*.json"
---
# Storage resource names and key prefixes

Vocabulary, length limits and the legacy-rename prohibition live in `00-core.md`.

## S3 buckets

| Purpose | Terraform type | Form | Example |
|---|---|---|---|
| Data lake | `aws_s3_bucket` | `uif-{stack}-{env}-{layer}` | `uif-echo-dev-bronze` |
| Supporting | `aws_s3_bucket` | `uif-{stack}-{env}-{purpose}` | `uif-echo-dev-glue-scripts` |
| UI | `aws_s3_bucket` | `uif-{stack}-{env}-ui` | `uif-echo-dev-ui` |

There is **no account-id prefix**. The previous convention put the account id first
(`283556840805-s3-uif-echo-dev-ui`); the current standard does not, and this is
confirmed rather than an omission. Bucket names are globally unique across AWS, so the
`uif-{stack}-{env}-` stem is what carries that uniqueness — do not add a suffix or an
account segment to work around a collision. Raise one with the standard's owner instead.

## Key prefixes — three shapes, one per layer

```
Bronze   {bucket}/{source_system}/{data_domain}/{table}/_extract_date=YYYY-MM-DD/
Silver   {bucket}/{source_system}/{data_domain}/{table}/
Gold     {bucket}/{entity}/
```

✅ `tep/product/mtl-system-items-b/_extract_date=2026-09-04/`
✅ `tep/product/mtl-system-items-b/`
✅ `product/`
❌ `product/mtl-system-items-b/` on a Bronze bucket — no `{source_system}`
❌ `tep/product/` on a Gold bucket — Gold carries no domain segment

Bronze is append-only with one partition per run; the `_extract_date=` segment is part
of the key, not a taxonomy segment. Silver and Gold prefixes are Iceberg roots, with
`data/` and `metadata/` beneath them. Segments are lower-case and hyphenated. A trailing
slash is required on every path field.

## DynamoDB tables

| Terraform type | Form | Example |
|---|---|---|
| `aws_dynamodb_table` | `uif-{stack}-{env}-{subject}-{kind}` | `uif-echo-dev-pipeline-config`, `uif-echo-dev-pipeline-metrics`, `uif-echo-dev-pipeline-analysis` |

## Relational databases

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| RDS instance | `aws_db_instance` | `uif-{stack}-{env}-{purpose}-db` | `uif-apicurio-dev-registry-db` |
| DB subnet group | `aws_db_subnet_group` | `uif-{stack}-{env}-{purpose}-db-subnet-group` | `uif-apicurio-dev-registry-db-subnet-group` |

`{purpose}` is what the database is *for*, not what engine it runs — `registry`, not `postgres`.
The same reasoning as the DynamoDB rule above: the engine is the most likely thing to change
and the least useful thing to encode.

**The name lives in `identifier`, not `name`.** RDS adds four constraints the convention does
not: 1–63 characters, the first must be a letter, no trailing hyphen, no two consecutive
hyphens, and the value is stored lower-case whatever you write. A compliant `uif-` name
satisfies all of them; a name that breaks them fails at apply, not at plan.

**Renaming `identifier` destroys the database.** Terraform replaces the instance, and the data
goes with it — a final snapshot is taken only if `skip_final_snapshot` is false. This is the
same posture as an S3 bucket, and the same `00-core.md` legacy rule applies: existing
non-compliant instances stay non-compliant until an explicit, planned migration.

No AWS cap is documented for `DBSubnetGroupName`, so none is asserted. The guard enforces the
stem and the suffix on it, not a length.

**Name the concept, not the engine.** The subject is what the rows are about, not what
writes them.

✅ `uif-echo-dev-pipeline-metrics` ❌ `uif-echo-dev-glue-job-metrics` — `glue-job` names
the engine that happens to produce the rows today.

## GCS — retained, not tabulated

The current standard does not cover GCS. Retained from the previous convention because
GCP-native pipelines exist: GCS keys follow Oracle schema names in snake_case
(`fipm/fpi_par_sales/`), never a business domain. Not ratified by the current standard.
