# CLAUDE.md — Quick Reference

Serverless Oracle → S3 Bronze ingestion + Silver Iceberg transform + Gold entity materialization + hard-delete reconciliation.
Config-driven: one Glue job per layer handles all pipelines via DynamoDB config.

Gold layer architecture: see `docs/adr/0001-gold-layer.md`.
GCP-native ingestion architecture: see `docs/adr/0002-gcp-native-mode.md`.

## Setup

```bash
eval $(/Users/raj9karu/.aws/aws-runas -e pearson-esb-dev)  # Credentials (6h expiry)
cd infra && make plan ENV=dev && make apply ENV=dev        # Deploy infra
aws s3 cp glue_jobs/*.py s3://data-connect-glue-scripts-dev/glue_jobs/  # Upload scripts
```

## Common Workflows

| Task | Command |
|------|---------|
| **Setup new pipeline** | `python3 scripts/setup_new_pipeline.py --env dev` (interactive) |
| **Deploy** | `cd infra && make apply ENV=dev && aws s3 cp glue_jobs/*.py s3://...` |
| **Test** | `python3 -m pytest tests/ -v` |
| **Register pipeline** | `python3 scripts/register_pipeline.py --pipeline-id X --table SCHEMA.TABLE --s3-path s3://... --mode incremental --env dev` |
| **Register with reconcile** | `... --enable-reconcile --reconcile-pk-col PK_COL --reconcile-dry-run` |
| **Ad-hoc run** | `python3 scripts/adhoc_run.py --pipeline-id X --env dev` |
| **Trigger reconcile** | `aws lambda invoke --function-name ingestion-framework-dev-scheduler-dispatcher --payload $(echo -n '{"frequency":"reconcile"}' \| base64) /tmp/r.json && jq . /tmp/r.json` |
| **Register Gold pipeline** | `python3 scripts/register_gold_pipeline.py --gold-id product --domain product --schema-dir schemas/gold/product/v1 --mapping-dir schemas/mapping/product/v1 --gold-database gold_product --gold-table product --owner you@pearson.com --env dev` |
| **Trigger Gold transform** | `aws lambda invoke --function-name ingestion-framework-dev-scheduler-dispatcher --payload $(echo -n '{"frequency":"gold"}' \| base64) /tmp/r.json && jq . /tmp/r.json` |
| **Trigger Gold reconcile** | Same as above with `"frequency":"gold-reconcile"` |
| **Generate tier subsets locally** | `python3 scripts/generate_tier_subsets.py --master schemas/gold/product/v1/master.json --entity product --version v1 --out-dir /tmp/subsets` |
| **Publish one tier to Schema Registry** | `bash scripts/publish.sh --schema /tmp/subsets/product/event/v1/schema.json --service-urn urn:pearson:service:product:catalog-svc --artifact-id product.gold-event-value --classification internal --lifecycle active` |
| **Verify published artifact (consumer)** | `curl -sS "https://cmkimj77g5.execute-api.eu-west-1.amazonaws.com/api/v1/schemas/default/product.gold-event-value" \| jq .` |
| **Verify canonical (engineering)** | `curl -sS "https://cmkimj77g5.execute-api.eu-west-1.amazonaws.com/api/v1/schemas/engineering/product.gold-canonical-value" \| jq .` |
| **Verify mapping manifest (engineering)** | `curl -sS "https://cmkimj77g5.execute-api.eu-west-1.amazonaws.com/api/v1/schemas/engineering/product.gold-mapping-manifest" \| jq .` |
| **Build mapping artifact locally** | `python3 scripts/build_mapping_artifact.py --manifest schemas/mapping/product/v1/manifest.yaml --entity product --version v1 --out /tmp/mapping.json` |

## API Routes

```
Base: https://zhil2dkfol.execute-api.eu-west-1.amazonaws.com

# Silver pipelines (Bronze + Silver layer)
GET    /pipelines
GET    /pipelines/{id}
PUT    /pipelines/{id}                    (full replace; GET first)
PATCH  /pipelines/{id}/toggle             {"enabled": bool}
POST   /pipelines/{id}/run
GET    /pipelines/{id}/runs

# Gold pipelines (Gold materialization layer — see ADR-0001)
POST   /gold-pipelines
GET    /gold-pipelines
GET    /gold-pipelines/{gold_id}
PUT    /gold-pipelines/{gold_id}          (full replace)
PATCH  /gold-pipelines/{gold_id}/toggle   {"enabled": bool}
POST   /gold-pipelines/{gold_id}/run      {"kind": "transform"|"reconcile"}
DELETE /gold-pipelines/{gold_id}?cascade=true
```

## DynamoDB Config Schema (Key Fields)

```json
{
  "pipeline_id": "string",
  "version": "latest",
  "enabled": true,
  "source": { "secret_arn": "...", "jdbc_extra_options": "" },
  "extract": {
    "table": "SCHEMA.TABLE",
    "mode": "full|incremental",
    "watermark_col": "LAST_UPDATE_DATE",
    "watermark_value": "1970-01-01T00:00:00",
    "jdbc_partition_col": "COL" // or null for single-partition
  },
  "target": { "s3_path": "s3://dal-bronze-dev/...", "compression": "snappy" },
  "silver": {
    "enabled": true,
    "database": "silver_domain",
    "table": "TABLE_NAME",
    "s3_path": "s3://dal-silver-dev/...",
    "primary_key_cols": ["PK1", "PK2"]
  },
  "reconcile": {
    "enabled": true,  // opt-in; nightly at 3 AM UTC
    "pk_partition_col": "PK_COL",
    "max_deletes_per_run": 5000,  // safety cap
    "dry_run": true  // start here, then switch to false
  }
}
```

```jsonc
// destination.cloud = "gcp" overlay (additive on top of the AWS schema above):
{
  "destination": {
    "cloud": "gcp",
    "gcp_credentials_secret_arn": "arn:aws:secretsmanager:..."
  },
  "target": {
    "gcs_path": "gs://bronze-bucket/<domain>/<table>/"   // replaces s3_path
  },
  "silver": {
    "gcs_path":    "gs://silver-bucket/<dataset>/<table>/",  // replaces s3_path
    "bq_project":  "...",
    "bq_dataset":  "...",
    "bq_location": "EU",
    // database not used (BQ dataset acts as the database); s3_path must be absent
    // bq_native extras:
    "write_mode":          "bq_native",       // routes to eds-{env}-silver-bq-native (Python Shell + BQ slots)
    "bq_partition_field":  null,              // legacy: explicit null = unpartitioned; absent = _silver_updated_at
    "bq_partition": {                         // optional; overrides bq_partition_field when present
      "type": "date_day",                     // date_day | date_month | date_year | integer_range
      "field": "_silver_updated_at"           // BQ column; DATE/TIMESTAMP/DATETIME for date_day
    },
    // date_month / date_year example (field_type is REQUIRED — see below):
    // "bq_partition": {
    //   "type": "date_year",
    //   "field": "CREATION_DATE",
    //   "field_type": "TIMESTAMP"             // DATE | TIMESTAMP | DATETIME — the column's BQ type
    // },
    // integer_range example (field must be listed in int64_cols):
    // "bq_partition": {
    //   "type": "integer_range",
    //   "field": "SALES_ID",
    //   "range": { "start": 0, "end": 500000000, "interval": 1000000 }
    // },
    "int64_cols":          ["SALES_ID"],      // narrow BIGNUMERIC PKs to INT64 at initial CTAS
    "last_processed_date": "1970-01-01",      // sentinel = full LOAD+CTAS rebuild on next run
    "last_processed_ts":   "..."              // written by the job; exact incremental watermark — do not hand-edit
  }
}
```

## Gold Pipeline Config Schema (DynamoDB key field reference)

```json
{
  "gold_id": "product",
  "version": "latest",
  "enabled": true,
  "domain":  "product",
  "schema": {
    "master_s3_path": "s3://gold-artifacts-dev/schemas/gold/product/v1/master.json",
    "version":        "v1"
  },
  "mapping": {
    "manifest_s3_path": "s3://gold-artifacts-dev/mappings/product/v1/manifest.yaml",
    "sql_s3_path":      "s3://gold-artifacts-dev/mappings/product/v1/sql/product.sql"
  },
  "sources": [
    {"alias": "items",    "silver_table": "silver_product.mtl_system_items_b",
     "watermark_col": "_silver_updated_at", "is_source_of_truth": true},
    {"alias": "items_tl", "silver_table": "silver_product.mtl_system_items_tl",
     "watermark_col": "_silver_updated_at"},
    {"alias": "bom",      "silver_table": "silver_product.bom_components_b",
     "watermark_col": "_silver_updated_at"},
    {"alias": "ext",      "silver_table": "silver_product.ego_mtl_sy_items_ext_b",
     "watermark_col": "_silver_updated_at"}
  ],
  "target": {
    "database":         "gold_product",
    "table":            "product",
    "s3_path":          "s3://dal-gold-dev/product/",
    "primary_key_cols": ["product_id", "org_id"]
  },
  "watermark": {                       // runtime-owned, ADR-0020: ONE bookmark = the start of the
    "bookmark": "2026-09-23T06:31:13.947992+00:00",   // last run that processed changes
    "silver_snapshots": { "<alias>": "s3://…/metadata/00042.metadata.json" }
  },
  "reconcile": {
    "enabled":             false,  // opt-in
    "dry_run":             true,
    "max_deletes_per_run": 5000
  }
}
```

## File Guide

See `codebase_tour.md` for detailed layout. Quick navigation:

- `glue_jobs/` — ETL scripts (Bronze, Silver, Gold, Reconcile)
- `schemas/` — Gold master schemas + mapping artifacts (one dir per domain)
- `docs/adr/` — Architecture Decision Records (start at `0001-gold-layer.md`)
- `infra/` — Terraform (IAM, Glue, EventBridge, Lambda, DynamoDB, S3)
- `lambda/` — Dispatcher (triggers Glue), Controller (API)
- `scripts/` — CLI tools (register, test, debug, schema generator, Apicurio publisher)
- `tests/` — Unit tests (mock AWS/Spark deps)

## Known Gotchas

- **JDBC partition column**: Must be real numeric column, not ROWID. Omit for single-partition.
- **Glue script upload**: Terraform tracks via `filemd5()` but doesn't re-upload. Manually run `aws s3 cp` after edits.
- **Ancient dates**: Spark requires `spark.sql.parquet.int96RebaseModeInWrite = CORRECTED` for Oracle dates pre-1900. Already set globally.
- **VPC endpoints**: Dev VPC has S3/DynamoDB endpoints; use `create_s3_endpoint = false` in tfvars.
- **Backticks in Oracle SQL**: Use bare identifiers (uppercase), not backticks. Backticks only for Spark SQL.
- **Anti-join aliases**: Reconcile job uses `i.col = o.col` (Iceberg vs Oracle), not `t.col = o.col`.
- **Gold x-channels invariant**: every property must satisfy `event ⊂ message ⊂ api`. CI `gold-schemas.yml` enforces this; locally run `scripts/generate_tier_subsets.py` to verify.
- **Gold mapping `sot_pk_mapping`**: the source-of-truth source in the mapping manifest must declare how each Gold PK column projects from its Silver columns. Reconcile depends on this.
- **Gold conformance check**: `generic_gold_transform.py` fails the run if SQL output columns don't match the master schema. Update mapping or bump schema version, then re-deploy.
- **PDH→canonical lineage** (ADR-13): the mapping manifest has a `canonical_lineage` block keyed by master-schema property, with `pdh_table` + `pdh_column` (required) and optional `role: pk`, `pdh_filter`, `pdh_grain`. CI lints coverage against `master.json` and `role: pk` consistency with `target.primary_key_cols`. Adding a Gold attribute = update master.json *and* canonical_lineage in the same PR.
- **`destination.cloud=gcp` rejects `replication.gcs` and `replication.silver_bq` blocks** — they're mirror configs and become nonsensical when GCP is already primary. The pipeline controller rejects them at register; the replicator Lambdas additionally short-circuit with `status: "skipped"` if a misconfigured row reaches them.
- **BQ MERGE + `IS NOT DISTINCT FROM` = shuffle death on small reservations**: the NULL-safe operator blocks BigQuery's broadcast join, forcing a full shuffle of the target — a 68K-row merge into a 94M-row table fails after 20 min with `Resources exceeded ... shuffle operations`. `generic_silver_bq_native.py` auto-switches to plain `=` when the delta slice has zero NULL PKs (COUNTIF in the count query); composite/nullable PKs keep the NULL-safe form. Same merge: 38.9s.
- **Never manually rewind `silver.last_processed_date`** to force re-merging — Bronze initial dumps share one `_extract_date`, so a rewind makes every MERGE reprocess the entire Bronze history and die at the Glue timeout, forever. To reprocess: rewind `extract.watermark_value` instead (Bronze re-extracts; MERGE is PK-idempotent; both watermarks self-restore on success), or set `last_processed_date` to sentinel `1970-01-01` for a full LOAD+CTAS rebuild (~2 min for 94M rows — far cheaper than a big MERGE).
- **`silver.last_processed_ts`** (timestamp-granular watermark) is written by every successful bq_native Silver run and makes the incremental filter exact — same-day Bronze re-runs are picked up. Configs without it fall back to date-only `>` filtering until their first success.
- **`watermark_fallback_col` wraps the watermark in `COALESCE(...)`, defeating Oracle's index** → full table scan per incremental. Only set it when the watermark column actually has NULLs (check first: EBS WHO columns are typically NOT NULL).
- **`silver.int64_cols`**: Oracle NUMBER PKs land as BIGNUMERIC (32-byte keys). List PK columns here to narrow them to INT64 at initial CTAS — smaller clustering key, cheaper merge join. Verify `id_max` fits INT64 via the analyzer output first.
- **`silver.bq_partition_field: null` (explicit) disables partitioning** when `silver.bq_partition` is absent; absent field → defaults to `_silver_updated_at` with daily `DATE(column)` partitions. **`silver.bq_partition: null`** disables partitioning regardless of `bq_partition_field`.
- **`silver.bq_partition`**: `type: date_day` → `PARTITION BY DATE(field)` (DATE/TIMESTAMP/DATETIME). `type: integer_range` → `RANGE_BUCKET` + `GENERATE_ARRAY(start, end, interval)`; **`field` must be in `int64_cols`**. Partition type is fixed at initial CTAS — changing it requires a full rebuild (`last_processed_date` sentinel).
- **`type: date_month` / `date_year`** → `PARTITION BY {DATE|TIMESTAMP|DATETIME}_TRUNC(field, MONTH|YEAR)`. These **require `field_type`** (`DATE`, `TIMESTAMP` or `DATETIME`): BigQuery's `PARTITION BY` grammar admits no casts, so the `_TRUNC` function must match the column's own type, and a mismatch fails the CTAS. `date_day` needs no declaration because `DATE(col)` is legal over all three types. `range` is rejected for these types.
- **Glue Python Shell ≠ Spark**: stdout is block-buffered (fixed via `sys.stdout.reconfigure(line_buffering=True)` — keep it when adding new pythonshell jobs) and `--JOB_RUN_ID` is NOT auto-passed, so metrics rows get `run_id=local_<epoch>` that can't be joined to Glue run IDs.
- **`aws glue update-job` resets omitted fields**: leaving out `MaxCapacity` silently drops a pythonshell job to 0.0625 DPU. Always round-trip get-job → mutate → update with the full payload.
- **Test suite is not green at baseline**: `test_home_summary.py` and `test_scheduler_dq.py` hang forever (`--ignore` them), and ~16 tests fail on a clean checkout. To judge a change, diff FAILED lists against a `git stash` baseline, not the raw count.
- **BQ slot reservation on `pearson-rr` is small and contended**: identical 112 GB CTAS ran 41.6s vs 274s depending on the hour; shuffle quota scales with slots. Suspect contention before suspecting query design — but fix `IS NOT DISTINCT FROM` first (see above).

## Troubleshooting

| Error | Fix |
|-------|-----|
| `no credentials` | Re-run `eval $(aws-runas ...)` |
| `ORA-00911: invalid character` | Check for backticks in Oracle JDBC subquery |
| Column reference error in anti-join | Verify table aliases in `build_pk_conditions()` |
| Glue script not updated after edit | Run `aws s3 cp` manually |
| `Resources exceeded ... shuffle operations` on MERGE | NULL-safe join blocked broadcast — ensure delta PKs are NULL-free so the job uses `=`; else chunk the merge or request more slots |
| bq_native MERGE reprocessing all of Bronze | `silver.last_processed_date` was rewound manually — reset to `1970-01-01` (rebuild) or restore true watermarks |
| Silver metrics row missing after TIMEOUT/STOPPED | Killed Python Shell never runs `finally` — check Glue run state, and note the BQ job may still be running server-side |
