# Echo artifact name mapping — complete inventory

**Purpose:** Naming reference for the greenfield **`uif-echo-dev-*`** stack — every
AWS artifact the new stack will use, with the live `echo-dev-*` names shown for
comparison only.

**Standards reference:** `architecture-design/standards/rules/naming/` (v2.0.0),
especially `storage.md`, `catalog.md`, `compute-etl.md`, `00-core.md`.

**Scope (current phase):**

1. Stand up `uif-echo-dev-*` (`ENV=dev-uif`) beside the live `echo-dev-*` stack.
2. **After the stack is up** — register pipelines, load configs, and wire Silver
   Glue catalog databases (`uif_echo_dev_silver_tep`, `uif_echo_dev_silver_mdm`)
   through the normal API/registration flow.

**Out of scope for now:** migrating data or configs from `echo-dev-*`, legacy
cutover mapping, or destroying the old stack. **No pipeline draft files** are
copied into the monorepo.

---

## Prefix rules

| Kind | Live pattern | UIF pattern | Example (dev) |
|---|---|---|---|
| AWS resource (hyphen) | `echo-{env}-` | `uif-echo-{env}-` | `echo-dev-bronze-ingest` → `uif-echo-dev-bronze-ingest` |
| Glue catalog DB — Silver | `echo_{env}_silver_{domain}` *(legacy)* | `uif_{stack}_{env}_silver_{source_system}` | `uif_echo_dev_silver_tep`, `uif_echo_dev_silver_mdm` |
| Glue catalog DB — Gold | `echo_{env}_gold_{domain}` | `uif_{stack}_{env}_gold_{domain}` | `echo_dev_gold_product` → `uif_echo_dev_gold_product` |
| Secrets (slash, target) | `echo/{env}/` | `uif/echo/{env}/` | `echo/dev/ui/login` → `uif/echo/dev/ui/login` |
| TF state | `pearson-ingestion-framework-tfstate-{account}` *(shared legacy)* | `s3://uif-echo-{env}-data/tfstate/` | `s3://uif-echo-dev-data/tfstate/infra-terraform.tfstate` |
| TF state key | `echo/{env}/terraform.tfstate` | `infra-terraform.tfstate` | same key in each env bucket |
| TF lock | DynamoDB `ingestion-framework-tfstate-locks` *(legacy)* | S3 native `use_lockfile` on state object | same |
| UI bucket | `{account}-s3-echo-{env}-ui` | `uif-echo-{env}-ui` | `uif-echo-dev-ui` *(no account-id prefix)* |
| Pipeline scripts (S3) | `glue_jobs/{snake_case}.py` | `jobs/{layer}-{operation}[-{target}].py` | `glue_jobs/generic_bronze_ingest.py` → `jobs/bronze-ingest.py` |
| DynamoDB table | `echo-{env}-glue-job-{kind}` | `uif-echo-{env}-{subject}-{kind}` | `echo-dev-glue-job-config` → `uif-echo-dev-pipeline-config` |
| Project tag (Secrets filter) | `echo` | `echo` *(unchanged until Keeper — see §Surprises)* | |

`{env}` = `dev` | `uat` | `prod`. **Prod is still `eds-prod-*`** — separate
programme; see §Prod (out of scope).

**DynamoDB rule:** name the **concept**, not the engine — no `glue` token in table
names (`storage.md`).

**Silver catalog rule:** database keyed by **source system** (`tep`, `mdm`) only.
The UIF stack has **exactly two** Silver Glue catalog databases — no `fipm`,
`product`, or other variants (`catalog.md`).

---

## §A — DEV complete inventory (`echo-dev-*` → `uif-echo-dev-*`)

### A.1 Terraform state

| # | Artifact | Existing (live dev) | UIF proposed |
|---|---|---|---|
| 1 | State object | `pearson-ingestion-framework-tfstate-283556840805/echo/dev/…` | `uif-echo-dev-data/tfstate/infra-terraform.tfstate` |
| 2 | State object key | `echo/dev/terraform.tfstate` | `infra-terraform.tfstate` |
| 3 | State lock | DynamoDB (legacy stacks) | S3 `use_lockfile` (UIF stacks) |

Each environment gets its **own** state bucket and lock table (not shared with legacy
`pearson-ingestion-framework-*` or other stacks).

### A.2 S3 buckets

| # | Purpose | Existing | UIF proposed | Notes |
|---|---|---|---|---|
| 4 | Data (code + configs) | `echo-dev-glue-scripts` | `uif-echo-dev-data` | TF var `artifacts_bucket`; UIF layout: `code/` (scripts, layers, jars), `configs/` |
| 5 | Bronze | `echo-dev-bronze` | `uif-echo-dev-bronze` | |
| 6 | Silver (Iceberg) | `echo-dev-silver` | `uif-echo-dev-silver` | Iceberg paths are absolute — copy ≠ migrate |
| 7 | Gold (Iceberg) | `echo-dev-gold` | `uif-echo-dev-gold` | |
| 8 | Gold schemas / SQL | `echo-dev-gold-artifacts` | `uif-echo-dev-gold-artifacts` | |
| 9 | UI hosting | `283556840805-s3-echo-dev-ui` | `uif-echo-dev-ui` | No account-id prefix (`storage.md`) |

### A.3 Pipeline script files — repo and S3

**S3 prefix:** `glue_jobs/` → **`jobs/`** (drop `glue_` from the path).

**File naming:** kebab-case `{layer}-{operation}[-{target}]`, matching the Glue job
slug without the `uif-echo-{env}-` prefix (`compute-etl.md`). Drop `generic_` prefix
and snake_case.

| # | Existing repo file | Existing S3 key | UIF repo file | UIF S3 key | Glue job |
|---|---|---|---|---|---|
| 10 | `generic_bronze_ingest.py` | `glue_jobs/generic_bronze_ingest.py` | `bronze-ingest.py` | `jobs/bronze-ingest.py` | `uif-echo-dev-bronze-ingest` |
| 11 | `generic_silver_transform.py` | `glue_jobs/generic_silver_transform.py` | `silver-transform.py` | `jobs/silver-transform.py` | `uif-echo-dev-silver-transform` |
| 12 | `analyze_table.py` | `glue_jobs/analyze_table.py` | `analyze-table.py` | `jobs/analyze-table.py` | `uif-echo-dev-analyze-table` |
| 13 | `generic_gold_transform.py` | `glue_jobs/generic_gold_transform.py` | `gold-transform.py` | `jobs/gold-transform.py` | `uif-echo-dev-gold-transform` |
| 14 | `generic_gold_reconcile.py` | `glue_jobs/generic_gold_reconcile.py` | `gold-reconcile.py` | `jobs/gold-reconcile.py` | `uif-echo-dev-gold-reconcile` |
| 15 | `generic_silver_reconcile.py` | `glue_jobs/generic_silver_reconcile.py` | `silver-reconcile.py` | `jobs/silver-reconcile.py` | `uif-echo-dev-silver-reconcile` |
| 16 | `generic_silver_transform_gcp_iceberg.py` | `glue_jobs/generic_silver_transform_gcp_iceberg.py` | `silver-transform-gcs-iceberg.py` | `jobs/silver-transform-gcs-iceberg.py` | `uif-echo-dev-silver-transform-gcs-iceberg` |
| 17 | `generic_silver_reconcile_gcp.py` | `glue_jobs/generic_silver_reconcile_gcp.py` | `silver-reconcile-gcs.py` | `jobs/silver-reconcile-gcs.py` | `uif-echo-dev-silver-reconcile-gcs` |
| 18 | `generic_silver_transform_gcp_bq.py` | `glue_jobs/generic_silver_transform_gcp_bq.py` | `silver-transform-bq.py` | `jobs/silver-transform-bq.py` | `uif-echo-dev-silver-transform-bq` |
| 19 | `generic_silver_reconcile_gcp_bq.py` | `glue_jobs/generic_silver_reconcile_gcp_bq.py` | `silver-reconcile-bq.py` | `jobs/silver-reconcile-bq.py` | `uif-echo-dev-silver-reconcile-bq` |
| 20 | `generic_silver_bq_native.py` | `glue_jobs/generic_silver_bq_native.py` | `silver-transform-bq-native.py` | `jobs/silver-transform-bq-native.py` | `uif-echo-dev-silver-transform-bq-native` |
| 21 | `generic_silver_quality_check.py` | `glue_jobs/generic_silver_quality_check.py` | `silver-quality-check.py` | `jobs/silver-quality-check.py` | `uif-echo-dev-silver-quality-check` |
| 22 | `_phase0_iceberg_gcp_probe.py` | `glue_jobs/_phase0_iceberg_gcp_probe.py` | `iceberg-gcs-probe.py` | `jobs/iceberg-gcs-probe.py` | `uif-echo-dev-iceberg-gcs-probe` |
| 23 | `dq_kinds/__init__.py` | `glue_jobs/dq_kinds/__init__.py` | `dq-kinds/__init__.py` | `jobs/dq-kinds/__init__.py` | *(supporting)* |
| 24 | `dq_kinds/reconcile.py` | `glue_jobs/dq_kinds/reconcile.py` | `dq-kinds/reconcile.py` | `jobs/dq-kinds/reconcile.py` | *(supporting)* |
| 25 | `dq_kinds/null_rate.py` | `glue_jobs/dq_kinds/null_rate.py` | `dq-kinds/null-rate.py` | `jobs/dq-kinds/null-rate.py` | *(supporting)* |

**Unchanged paths** inside artifacts bucket:

| # | Key | Notes |
|---|---|---|
| 26 | `lambda-layers/oracle_instant_client.zip` | |
| 27 | `lambda-layers/gcs_layer.zip` | |
| 28 | `lambda-layers/silver_bq_layer.zip` | |
| 29 | `lambda-layers/controller_bq_layer.zip` | |
| 30 | `jars/gcs-connector-hadoop3-2.2.22-shaded.jar` | |
| 31 | `jars/iceberg-gcp-bundle-1.6.1.jar` | |

Repo layout: files live under `stacks/echo/src/apps/pipeline/` today; rename on
disk to match the **UIF repo file** column, then sync to `s3://{artifacts}/jobs/`.

### A.4 DynamoDB tables

No `glue` in UIF table names — `{subject}-{kind}` per `storage.md`.

| # | Purpose | Existing | UIF proposed | In Terraform |
|---|---|---|---|---|
| 32 | Pipeline configs | `echo-dev-glue-job-config` | `uif-echo-dev-pipeline-config` | Yes |
| 33 | Job run metrics | `echo-dev-glue-job-metrics` | `uif-echo-dev-pipeline-metrics` | Yes |
| 34 | GSI on metrics | `pipeline_id-index` | `pipeline_id-index` | *(same name)* |
| 35 | Alert subscribers (UIFPE-543; replaced the never-created alert-config) | — | `uif-echo-dev-alert-subscribers` | n/a |
| 36 | Gold pipeline registry | `echo-dev-gold-pipelines-config` | `uif-echo-dev-gold-pipeline-config` | Yes |
| 37 | Oracle analyze results | `echo-dev-pipeline-analysis` | `uif-echo-dev-pipeline-analysis` | Yes |
| 38 | DQ check configs | `echo-dev-dq-check-config` | `uif-echo-dev-dq-check-config` | Yes |
| 39 | Partner export metrics | `echo-dev-partner-export-metrics` | `uif-echo-dev-partner-export-metrics` | **No — Lambda env only** |
| 40 | DQ run metrics | `echo-dev-dq-check-metrics` | `uif-echo-dev-dq-check-metrics` | **No — Lambda env only** |
| 41 | Partner export config | `echo-dev-partner-export-config` | `uif-echo-dev-partner-export-config` | **No — code default only** |

### A.5 Glue jobs

| # | Existing | UIF proposed |
|---|---|---|
| 42 | `echo-dev-bronze-ingest` | `uif-echo-dev-bronze-ingest` |
| 43 | `echo-dev-silver-transform` | `uif-echo-dev-silver-transform` |
| 44 | `echo-dev-analyze-table` | `uif-echo-dev-analyze-table` |
| 45 | `echo-dev-gold-transform` | `uif-echo-dev-gold-transform` |
| 46 | `echo-dev-gold-reconcile` | `uif-echo-dev-gold-reconcile` |
| 47 | `echo-dev-silver-reconcile` | `uif-echo-dev-silver-reconcile` |
| 48 | `echo-dev-silver-transform-gcp-iceberg` | `uif-echo-dev-silver-transform-gcs-iceberg` |
| 49 | `echo-dev-silver-reconcile-gcp` | `uif-echo-dev-silver-reconcile-gcs` |
| 50 | `echo-dev-silver-transform-gcp-bq` | `uif-echo-dev-silver-transform-bq` |
| 51 | `echo-dev-silver-reconcile-gcp-bq` | `uif-echo-dev-silver-reconcile-bq` |
| 52 | `echo-dev-silver-bq-native` | `uif-echo-dev-silver-transform-bq-native` |
| 53 | `echo-dev-silver-quality-check` | `uif-echo-dev-silver-quality-check` |
| 54 | `echo-dev-phase0-iceberg-gcp-probe` | `uif-echo-dev-iceberg-gcs-probe` |

Glue classifier (env suffix): `gcp-bronze-replicator-dev` → **unchanged**.

Each Glue job `ScriptLocation` must point at the new `s3://…/jobs/…` keys (§A.3).

### A.6 Glue connection and security group

| # | Existing | UIF proposed |
|---|---|---|
| 55 | `echo-dev-vpc-network` | `uif-echo-dev-vpc-network` |
| 56 | `echo-dev-glue-eni` | `uif-echo-dev-glue-eni-sg` |

Manual JDBC connections (outside TF): `echo-dev-gip-db-{system}` → `uif-echo-dev-db-{system}`
(`compute-etl.md` form: `uif-echo-dev-db-tep`).

### A.7 Glue catalog databases

**Silver — exactly two databases, keyed by source system** (`catalog.md`):

| # | UIF proposed | Source system |
|---|---|---|
| 57 | `uif_echo_dev_silver_tep` | TEP |
| 58 | `uif_echo_dev_silver_mdm` | MDM |

There is **no** `uif_echo_dev_silver_fipm`, `uif_echo_dev_silver_product`, or any
other Silver catalog database in the UIF stack.

**Gold — keyed by business domain** (`catalog.md`):

| # | UIF proposed | Notes |
|---|---|---|
| 59 | `uif_echo_dev_gold_product` | Created when Gold pipelines are registered *(example domain: product)* |

**Post-deploy (not part of infra apply):** Silver catalog assignment happens at
pipeline registration — each pipeline's config sets `silver.database` to
`uif_echo_dev_silver_tep` or `uif_echo_dev_silver_mdm` based on **source system**
(`tep` | `mdm`). No legacy catalog names are carried forward.

### A.8 Lambda functions

| # | Existing | UIF proposed | Slug change |
|---|---|---|---|
| 62 | `echo-dev-pipeline-controller` | `uif-echo-dev-pipeline-controller` | — |
| 63 | `echo-dev-scheduler-dispatcher` | `uif-echo-dev-dispatch` | **yes** |
| 64 | `echo-dev-gcs-replicator` | `uif-echo-dev-replicate-gcs` | **yes** |
| 65 | `echo-dev-silver-bq-replicator` | `uif-echo-dev-replicate-bq` | **yes** |
| 66 | `echo-dev-dq-check-controller` | `uif-echo-dev-dq-check-controller` | — |

Auto-created log groups: `/aws/lambda/echo-dev-*` → `/aws/lambda/uif-echo-dev-*`.

### A.9 Lambda layers

| # | Existing | UIF proposed |
|---|---|---|
| 67 | `echo-dev-oracle-instant-client` | `uif-echo-dev-oracle-layer` |
| 68 | `echo-dev-gcs-client` | `uif-echo-dev-gcs-layer` |
| 69 | `echo-dev-silver-bq-client` | `uif-echo-dev-replicate-bq-layer` |
| 70 | `echo-dev-controller-bq-client` | `uif-echo-dev-controller-bq-layer` |

### A.10 IAM roles and policies

| # | Existing | UIF proposed |
|---|---|---|
| 71 | `echo-dev-glue-role` | `uif-echo-dev-glue-role` |
| 72 | `echo-dev-glue-policy` | `uif-echo-dev-glue-policy` |
| 73 | `echo-dev-lambda-role` | `uif-echo-dev-lambda-role` |
| 74 | `echo-dev-lambda-policy` | `uif-echo-dev-lambda-policy` |
| 75 | `echo-dev-github-actions` | `uif-echo-dev-github-actions-role` |
| 76 | `echo-dev-github-actions-policy` | `uif-echo-dev-github-actions-policy` |

Analyst role (not Echo TF): `PCMPowerUser` — **unchanged**.

### A.11 API Gateway and CloudFront

| # | Existing | UIF proposed | Notes |
|---|---|---|---|
| 77 | `echo-dev-api` | `uif-echo-dev-api` | New API ID and URL on parallel stack |
| 78 | `/aws/apigateway/echo-dev` | `/aws/apigateway/uif-echo-dev` | Access log group |
| 79 | `echo-dev-ui-oac` | `uif-echo-dev-ui-oac` | Origin Access Control |
| 80 | CloudFront comment `echo-dev-ui` | `uif-echo-dev-ui` | |

API route paths unchanged: `/pipelines`, `/gold-pipelines`, `/analyze`, etc.

### A.12 EventBridge schedule rules — fixed cadence

| # | Existing | UIF proposed |
|---|---|---|
| 81 | `echo-dev-bronze-ingest-30min` | `uif-echo-dev-schedule-bronze-ingest-30min` |
| 82 | `echo-dev-bronze-ingest-hourly` | `uif-echo-dev-schedule-bronze-ingest-hourly` |
| 83 | `echo-dev-bronze-ingest-4hr` | `uif-echo-dev-schedule-bronze-ingest-4hr` |
| 84 | `echo-dev-bronze-ingest-weekly` | `uif-echo-dev-schedule-bronze-ingest-weekly` |
| 85 | `echo-dev-gold-transform-hourly` | `uif-echo-dev-schedule-gold-transform-hourly` |
| 86 | `echo-dev-gold-transform-daily` | `uif-echo-dev-schedule-gold-transform-daily` |
| 87 | `echo-dev-gold-reconcile-nightly` | `uif-echo-dev-schedule-gold-reconcile-daily` |

### A.13 EventBridge — Bronze daily slots (12 rules)

| Slot | Existing | UIF proposed |
|---|---|---|
| 0 | `echo-dev-bronze-ingest-daily-slot-0` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-00` |
| 1 | `echo-dev-bronze-ingest-daily-slot-1` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-01` |
| 2 | `echo-dev-bronze-ingest-daily-slot-2` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-02` |
| 3 | `echo-dev-bronze-ingest-daily-slot-3` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-03` |
| 4 | `echo-dev-bronze-ingest-daily-slot-4` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-04` |
| 5 | `echo-dev-bronze-ingest-daily-slot-5` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-05` |
| 6 | `echo-dev-bronze-ingest-daily-slot-6` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-06` |
| 7 | `echo-dev-bronze-ingest-daily-slot-7` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-07` |
| 8 | `echo-dev-bronze-ingest-daily-slot-8` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-08` |
| 9 | `echo-dev-bronze-ingest-daily-slot-9` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-09` |
| 10 | `echo-dev-bronze-ingest-daily-slot-10` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-10` |
| 11 | `echo-dev-bronze-ingest-daily-slot-11` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-11` |

### A.14 EventBridge — Silver reconcile slots (12 rules)

| Slot | Existing | UIF proposed |
|---|---|---|
| 0 | `echo-dev-reconcile-slot-0` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-00` |
| 1 | `echo-dev-reconcile-slot-1` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-01` |
| 2 | `echo-dev-reconcile-slot-2` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-02` |
| 3 | `echo-dev-reconcile-slot-3` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-03` |
| 4 | `echo-dev-reconcile-slot-4` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-04` |
| 5 | `echo-dev-reconcile-slot-5` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-05` |
| 6 | `echo-dev-reconcile-slot-6` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-06` |
| 7 | `echo-dev-reconcile-slot-7` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-07` |
| 8 | `echo-dev-reconcile-slot-8` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-08` |
| 9 | `echo-dev-reconcile-slot-9` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-09` |
| 10 | `echo-dev-reconcile-slot-10` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-10` |
| 11 | `echo-dev-reconcile-slot-11` | `uif-echo-dev-schedule-silver-reconcile-daily-slot-11` |

**Total EventBridge rules per env: 33.**

### A.15 EventBridge event rules (Glue state change)

| # | Existing | UIF proposed |
|---|---|---|
| 88 | `echo-dev-bronze-succeeded-replicate` | `uif-echo-dev-on-bronze-succeeded-replicate-gcs` |
| 89 | `echo-dev-silver-succeeded-bq` | `uif-echo-dev-on-silver-succeeded-replicate-bq` |

### A.16 CloudWatch dashboards and alarms

| # | Existing | UIF proposed |
|---|---|---|
| 90 | `echo-dev-gcs-replication` | `uif-echo-dev-bronze-gcs-replication` |
| 91 | `echo-dev-silver-bq-replication` | `uif-echo-dev-silver-bq-replication` |
| 92 | `echo-dev-silver-bq-replicator-failed` | `uif-echo-dev-replicate-bq-failed` |

### A.17 VPC endpoint Name tags *(when created)*

| # | Existing | UIF proposed |
|---|---|---|
| 93 | `echo-dev-s3-endpoint` | `uif-echo-dev-s3-endpoint` |
| 94 | `echo-dev-dynamodb-endpoint` | `uif-echo-dev-dynamodb-endpoint` |
| 95 | `echo-dev-secretsmanager-endpoint` | `uif-echo-dev-secretsmanager-endpoint` |

### A.18 Secrets Manager

| # | Purpose | Existing (in use) | UIF target |
|---|---|---|---|
| 96 | UI login | `echo/dev/ui/login` | `uif/echo/dev/ui/login` |
| 97 | Oracle connections | `echo/dev/gip/db/{system}` | `uif/echo/dev/db/{system}` |
| 98 | GCP replicate PDH | `echo/dev/gip/cloud/gcp-rp-pdh` | `uif/echo/dev/cloud/gcp-rp-pdh` |
| 99 | GCP IPM | `echo/dev/gip/cloud/gcp-ipm` | `uif/echo/dev/cloud/gcp-ipm` |

Parallel dev keeps `echo/dev/…` paths until Keeper migration (IAM + tags).

### A.19 Lambda environment variables

| Variable | Existing (live) | UIF proposed |
|---|---|---|
| `RESOURCE_PREFIX` | `echo-dev` | `uif-echo-dev` |
| `LEGACY_RESOURCE_PREFIX` | `echo-dev` | `uif-echo-dev` *(same as RESOURCE_PREFIX on greenfield stack)* |
| `CONFIG_TABLE` | `echo-dev-glue-job-config` | `uif-echo-dev-pipeline-config` |
| `METRICS_TABLE` | `echo-dev-glue-job-metrics` | `uif-echo-dev-pipeline-metrics` |
| `ALERT_SUBSCRIBERS_TABLE` | — | `uif-echo-dev-alert-subscribers` |
| `ANALYSIS_TABLE` | `echo-dev-pipeline-analysis` | `uif-echo-dev-pipeline-analysis` |
| `GOLD_CONFIG_TABLE` | `echo-dev-gold-pipelines-config` | `uif-echo-dev-gold-pipeline-config` |
| `DQ_CHECK_TABLE` | `echo-dev-dq-check-config` | `uif-echo-dev-dq-check-config` |
| `EXPORT_METRICS_TABLE` | `echo-dev-partner-export-metrics` | `uif-echo-dev-partner-export-metrics` |
| `DQ_METRICS_TABLE` | `echo-dev-dq-check-metrics` | `uif-echo-dev-dq-check-metrics` |
| `GLUE_JOB_NAME` | `echo-dev-bronze-ingest` | `uif-echo-dev-bronze-ingest` |
| `SILVER_JOB_NAME` | `echo-dev-silver-transform` | `uif-echo-dev-silver-transform` |
| `SILVER_JOB_NAME_GCP` | `echo-dev-silver-transform-gcp-iceberg` | `uif-echo-dev-silver-transform-gcs-iceberg` |
| `SILVER_JOB_NAME_GCP_BQ` | `echo-dev-silver-bq-native` | `uif-echo-dev-silver-transform-bq-native` |
| `GOLD_JOB_NAME` | `echo-dev-gold-transform` | `uif-echo-dev-gold-transform` |
| `GOLD_RECONCILE_JOB_NAME` | `echo-dev-gold-reconcile` | `uif-echo-dev-gold-reconcile` |
| `RECONCILE_JOB_NAME` | `echo-dev-silver-reconcile` | `uif-echo-dev-silver-reconcile` |
| `RECONCILE_JOB_NAME_GCP` | `echo-dev-silver-reconcile-gcp` | `uif-echo-dev-silver-reconcile-gcs` |
| `ANALYZE_GLUE_JOB_NAME` | `echo-dev-analyze-table` | `uif-echo-dev-analyze-table` |
| `GOLD_ARTIFACTS_BUCKET` | `echo-dev-gold-artifacts` | `uif-echo-dev-gold-artifacts` |
| `UI_LOGIN_SECRET` | `echo/dev/ui/login` | `uif/echo/dev/ui/login` |
| `SECRET_TAG_VAL` | `echo` | `echo` *(tag unchanged; uif/echo paths tagged app=echo)* |
| `JOB_NAME` (replicators) | `echo-dev-gcs-replicator` / `echo-dev-silver-bq-replicator` | `uif-echo-dev-replicate-gcs` / `uif-echo-dev-replicate-bq` |

New pipeline configs written after stack stand-up use UIF names throughout (S3 paths,
`silver.database`, `jobs/` script paths, secret ARNs).

---

## §B — UAT complete inventory (`echo-uat-*` → `uif-echo-uat-*`)

Same transforms as §A with `{env}=uat`.

### B.1 Terraform state

| Artifact | Existing | UIF proposed |
|---|---|---|
| State bucket | `pearson-ingestion-framework-tfstate-283556840805` | `uif-echo-uat-tfstate` |
| State key | `echo/uat/terraform.tfstate` | `infra-terraform.tfstate` |
| Lock table | `ingestion-framework-tfstate-locks` | `uif-echo-uat-tfstate-locks` |

### B.2 S3 buckets

| Purpose | Existing | UIF proposed |
|---|---|---|
| Artifacts | `echo-uat-glue-scripts` | `uif-echo-uat-artifacts` |
| Bronze | `echo-uat-bronze` | `uif-echo-uat-bronze` |
| Silver | `echo-uat-silver` | `uif-echo-uat-silver` |
| Gold | `echo-uat-gold` | `uif-echo-uat-gold` |
| Gold artifacts | `echo-uat-gold-artifacts` | `uif-echo-uat-gold-artifacts` |
| UI hosting | `283556840805-s3-echo-uat-ui` | `uif-echo-uat-ui` |

### B.3 Pipeline scripts

Same renames as §A.3 — S3 prefix `jobs/`, kebab-case filenames. Example:
`s3://uif-echo-uat-artifacts/jobs/bronze-ingest.py`.

### B.4 DynamoDB tables

| Existing | UIF proposed |
|---|---|
| `echo-uat-glue-job-config` | `uif-echo-uat-pipeline-config` |
| `echo-uat-glue-job-metrics` | `uif-echo-uat-pipeline-metrics` |
| — | `uif-echo-uat-alert-subscribers` |
| `echo-uat-gold-pipelines-config` | `uif-echo-uat-gold-pipeline-config` |
| `echo-uat-pipeline-analysis` | `uif-echo-uat-pipeline-analysis` |
| `echo-uat-dq-check-config` | `uif-echo-uat-dq-check-config` |
| `echo-uat-partner-export-metrics` | `uif-echo-uat-partner-export-metrics` |
| `echo-uat-dq-check-metrics` | `uif-echo-uat-dq-check-metrics` |
| `echo-uat-partner-export-config` | `uif-echo-uat-partner-export-config` |

### B.5 Glue catalog databases

**Silver — exactly two:**

| UIF proposed | Source system |
|---|---|
| `uif_echo_uat_silver_tep` | TEP |
| `uif_echo_uat_silver_mdm` | MDM |

**Gold:** `uif_echo_uat_gold_product` *(and other domains as Gold pipelines are registered)*

Pipeline and catalog setup for UAT follows the same post-deploy flow as dev (§A.7).

### B.6 All other resources

Apply §A.5–A.18 with `echo-uat` → `uif-echo-uat` and `echo_uat` → `uif_echo_uat`:

- 13 Glue jobs, 5 Lambdas, 4 layers, 6 IAM roles/policies
- 33 EventBridge rules (same slot patterns as §A.12–A.15)
- API `echo-uat-api` → `uif-echo-uat-api`
- Secrets `echo/uat/…` → `uif/echo/uat/…` *(target)*

Known live UAT API (2026-09): `https://i7ydat65yg.execute-api.eu-west-1.amazonaws.com` —
the UIF stack gets its own API endpoint when deployed.

---

## §C — Shared / unchanged artifacts

| Artifact | Value | Notes |
|---|---|---|
| Legacy TF state bucket | `pearson-ingestion-framework-tfstate-283556840805` | Used by live `echo-dev` / `echo-uat` — unrelated to UIF state buckets |
| Legacy lock table | `ingestion-framework-tfstate-locks` | Used by live stacks — unrelated to `uif-echo-{env}-tfstate-locks` |
| Glue log groups | `/aws-glue/jobs/output`, `…/error`, `…/logs-v2` | Account-wide |
| Glue classifier | `gcp-bronze-replicator-{env}` | |
| VPC / subnet | Per env tfvars | New SG per UIF stack |
| Gateway VPC endpoints | Pre-existing, shared on dev | |
| Analyst IAM role | `PCMPowerUser` | |
| GCP GCS / BQ | Unchanged | AWS naming only |
| Legacy EDS stacks | `eds-{env}-*` | Separate programme |
| Pipeline drafts in repo | Not copied | DynamoDB is source of truth |

---

## §D — Prod (out of scope)

Live prod: **`eds-prod-*`**. Target when migrated: **`uif-echo-prod-*`**, with
`uif-echo-prod-tfstate`, `infra-terraform.tfstate`, `uif-echo-prod-tfstate-locks`,
`uif-echo-prod-ui`.

---

## §E — Repository layout

| Existing (`uif-relay-echo`) | UIF monorepo (`stacks/echo`) |
|---|---|
| `ingestion-framework/glue_jobs/*.py` | `src/apps/pipeline/{layer}-{operation}.py` |
| `ingestion-framework/lambda/` | `src/apps/control_panel/`, `src/apps/lambdas/` |
| `ingestion-framework/scripts/` | `src/apps/cli/` |
| `ingestion-framework/infra/` | `infrastructure/terraform/` |
| `ui/` | `ui/` |

---

## §F — Deploy scripts

| Script | Purpose |
|---|---|
| `uif-echo-infra-deploy.sh [ENV]` | Terraform init → plan → apply |
| `uif-echo-apps-deploy.sh [ENV]` | Sync `jobs/` scripts + Terraform apply for code |
| `uif-echo-ui-deploy.sh [ENV] [API_URL]` | UI build, S3 sync, CloudFront invalidation |

---

## §G — Known surprises

1. **TF state in data bucket** — UIF stacks store state at
   `s3://uif-echo-{env}-data/tfstate/infra-terraform.tfstate` (S3 `use_lockfile`).
   Bootstrap the data bucket before first `terraform init`.

2. **UI bucket drops account prefix** — Greenfield bucket is `uif-echo-dev-ui`
   (not `{account}-s3-…`). Confirm global S3 name uniqueness before apply.

3. **Script path** — UIF data bucket uses `code/` (live stacks use `jobs/`). Terraform,
   Glue `ScriptLocation`, and `uif-echo-apps-deploy.sh` must agree.

4. **DynamoDB: no `glue` token** — Tables are `pipeline-config`, `pipeline-metrics`, etc.

5. **Silver: only two catalogs** — `uif_echo_dev_silver_tep` and
   `uif_echo_dev_silver_mdm`. Assigned per pipeline at registration time.

6. **Schedules start disabled** — `event_rule_state = DISABLED` in `dev-uif.tfvars`
   until pipelines are registered and you deliberately enable dispatch.

7. **Secrets stay on `echo/{env}` paths** until Keeper — IAM grants use `echo/*`.

8. **Tables not in Terraform** — partner-export and dq metrics tables may need manual
   creation before those features are used.

9. **GitHub OIDC** — Role name is `uif-echo-dev-github-actions-role`; update
   `AWS_ROLE_ARN_*` in GitHub secrets when wiring CI.

---

## §H — Verification (dev)

```bash
aws s3 ls s3://uif-echo-dev-data/tfstate/
aws s3 ls s3://uif-echo-dev-ui/
aws s3 ls s3://uif-echo-dev-data/code/
aws s3 ls s3://uif-echo-dev-data/configs/
aws dynamodb list-tables --query 'TableNames[?starts_with(@, `uif-echo-dev`)]'
aws glue get-databases --query 'DatabaseList[?Name==`uif_echo_dev_silver_tep` || Name==`uif_echo_dev_silver_mdm`]'
```

---

*Last updated: UIFPE-355 — greenfield naming only; pipeline/catalog setup is post-deploy.*
