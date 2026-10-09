# ADR-0002 — GCP-native ingestion mode

**Status:** Accepted
**Date:** 2026-06-10
**Deciders:** raj.karunakaran@pearson.com (engineering owner)
**Scope:** Bronze → Silver lake architecture for pipelines whose destination is GCP (GCS + BigQuery), not AWS (S3 + Glue Catalog + Lake Formation).
**Supersedes:** none
**Superseded by:** none

---

## Context

The framework's Bronze and Silver layers were built S3-first:

- **Bronze** writes raw Parquet to `s3://dal-bronze-{env}/...`.
- **Silver** writes Apache Iceberg to `s3://dal-silver-{env}/...`, registered in the AWS Glue Catalog and governed by Lake Formation.
- A pair of replicator Lambdas (`gcs_replicator`, `silver_bq_replicator`) optionally **mirror** the S3 outputs to GCS/BigQuery for downstream consumers that live on GCP. Those mirrors are opt-in via `replication.gcs.enabled` and `replication.silver_bq.enabled`.

A new requirement landed: support pipelines whose **only** destination is GCP. For these pipelines:

- The S3 hop is unnecessary, costly, and adds operational surface (Lake Formation grants, S3 lifecycle rules, Glue Catalog hygiene) the consumer doesn't care about.
- The Glue Catalog and Lake Formation should not be involved at all — the catalog of record is BigQuery, and consumers are GCP-side.

Today the framework can't express "GCP-only" — Bronze and Silver Glue jobs hardcode S3 URIs and the AWS Glue Catalog into the Iceberg session config. We need a way to opt a pipeline into a GCP-native flow without forking the entire framework.

---

## Decision

Add a single new field on the pipeline config: **`destination.cloud`**, with values `"aws"` (default, current behaviour) or `"gcp"` (opt-in, new behaviour).

For `destination.cloud = "gcp"`:

1. **Bronze** writes Parquet **directly to `gs://`** via the GCS Hadoop connector (`gcs-connector-hadoop3-2.2.22-shaded.jar`). The transform is otherwise identical — just a different filesystem URI on `df.write.parquet(path)`. Credentials are loaded from a Secrets Manager-stored GCP service-account key, identified by `destination.gcp_credentials_secret_arn`.
2. **Silver** uses a **new sibling Glue job** (`generic-silver-transform-gcp-iceberg-{env}`) that writes Iceberg to GCS via the Iceberg **Hadoop catalog** and `GCSFileIO` (from `iceberg-gcp-bundle-1.6.1.jar`). After the MERGE, the job runs a `CREATE OR REPLACE EXTERNAL TABLE ... OPTIONS(format='ICEBERG', uris=[...])` against BigQuery to publish (or refresh) a BigLake external table over the Iceberg `metadata.json`.
3. **Reconcile** uses a parallel sibling job (`generic-silver-reconcile-gcp-{env}`) at Glue 5.1 that reads the GCS Iceberg table via the same Hadoop catalog.
4. The **AWS Glue Catalog and Lake Formation are not touched** for GCP pipelines. BigQuery's dataset is the catalog; GCS is the storage.
5. **Pipeline controller** auto-creates the BigQuery dataset server-side when a GCP pipeline is registered (same model as the current AWS Glue database auto-create).
6. **Replicator Lambdas** (`gcs_replicator`, `silver_bq_replicator`) **no-op early** when they see a GCP pipeline. The pipeline controller already rejects `replication.gcs.enabled=true` and `replication.silver_bq.enabled=true` when `cloud=gcp`, but the replicators carry their own defence-in-depth guard for misconfigured rows.

The shape of the config overlay for `cloud=gcp` is documented in `CLAUDE.md`. The field is **additive** — every existing AWS pipeline continues to work with zero changes (no `destination` block ⇒ default `aws`).

---

## Rationale

### 1. Sibling Silver/Reconcile, flag-branched Bronze

Bronze is essentially filesystem-agnostic — the body of the job is `df.write.parquet(path)` after JDBC extract. A flag branch on `destination.cloud` that picks `s3://` vs `gs://` (and configures the GCS Hadoop connector creds on the Spark session for the GCP branch) is small, localised, and reads cleanly.

Silver, by contrast, swaps out an entire catalog and FileIO stack: Glue Catalog ↔ Hadoop Catalog, `S3FileIO` ↔ `GCSFileIO`, Lake Formation governance ↔ none. The branching surface for in-place support would touch nearly every SQL statement (catalog-qualified table names differ, snapshot APIs differ at the boundary, vacuum semantics differ). A **sibling Glue job** lets each path stay idiomatic for its filesystem without per-SQL branching, and keeps the existing AWS path's blast radius at zero.

Reconcile follows the same logic as Silver — sibling job for the same reasons.

### 2. Hadoop catalog + post-write BigLake DDL fallback

The Iceberg ecosystem now has a `BigQueryMetastoreCatalog` that would let Silver write Iceberg with BigQuery as the catalog directly, eliminating the post-write DDL step. **Phase 0 probe (2026-06-10) confirmed it is MISSING from the iceberg-gcp-bundle that ships in Glue 5.1's classpath.** Adding it ourselves would require either a custom JAR build pipeline or hoping a future Glue release picks it up.

Fallback: write Iceberg with the **Iceberg Hadoop catalog under GCS** (a simple directory-based catalog that needs no metastore service), then run a single BigQuery DDL to register a BigLake external table over the resulting `metadata.json`. The pattern is already proven in `lambda/silver_bq_replicator.py` (`_refresh_bq_external_table`) — same DDL shape, same OPTIONS map. Reusing it keeps the surface area small and the BigQuery side identical to what the replicator already produces.

### 3. Replication blocks rejected on GCP

When `cloud=gcp`, the pipeline is **already on GCP** — there is no target to mirror to. The replication blocks (`replication.gcs`, `replication.silver_bq`) are inherently mirror configs and become nonsensical (and would fail at runtime) on a GCP-native pipeline. The pipeline controller validation rejects them at registration time. The replicator Lambdas additionally short-circuit with `status: "skipped"` if a misconfigured row somehow reaches them (e.g. via direct DynamoDB write). Defence in depth — one validation gate at the front door, one guard at the worker.

---

## Alternatives considered (rejected)

- **BigQuery Metastore catalog directly.** The cleanest model on paper — Iceberg-on-BigQuery without the post-write DDL. Phase 0 probe found the catalog class missing from Glue 5.1's classpath, so the implementation would either need a custom-built JAR or block on a future Glue release. Revisit when the JAR ships.
- **Spark BigQuery connector (no Iceberg).** Write managed BigQuery tables directly from Spark. Loses Iceberg's time-travel, schema evolution, and the snapshot semantics the existing Silver framework depends on (the reconcile job relies on Iceberg's row-level MERGE). Considered for a future `cloud=gcp-bq-native` mode, but not built today.
- **Dual-write Bronze (S3 + GCS) with Silver-on-GCS-only.** Adds S3 cost with no consumer for it when GCP is the only downstream. Inconsistent: a failure on one side leaves the two stores drifted. Rejected.

---

## Consequences

- **New Glue jobs:** `generic-silver-transform-gcp-iceberg-{env}` and `generic-silver-reconcile-gcp-{env}`, deployed via Terraform alongside the existing AWS jobs.
- **New Terraform-managed JARs** uploaded to `s3://<glue-scripts-bucket>/jars/` (the GCS Hadoop connector and the Iceberg GCP bundle).
- **Bronze ingest now flag-branches** on `destination.cloud`. The Spark session is configured for GCS when `cloud=gcp`; otherwise the existing S3 code path runs unchanged.
- **Pipeline controller** auto-creates the BigQuery dataset on register for GCP pipelines (mirrors the current Glue-database auto-create for AWS pipelines).
- **Replicator Lambdas no-op** for GCP pipelines (defence-in-depth — the controller already rejects the misconfig at register time).
- **Cascade-delete for GCP pipelines** removes only the DynamoDB config row. GCS data and BigQuery tables are consumer-owned (same policy as the existing `replication.silver_bq` cleanup — the framework writes, the consumer keeps).
- **Metrics shape:** the existing `s3_target_path` metrics field is set only on AWS pipelines. GCP pipelines populate `bronze_target_path` (and analogous Silver fields) with `gs://` URIs. UI/CLI consumers that read `s3_target_path` will need to fall back to `bronze_target_path` for cross-cloud display (see Open follow-ups).

---

## Verification

End-to-end smoke test on `dev`:

1. **Register** a GCP pipeline via `PUT /pipelines/{id}` with `destination.cloud=gcp`, `target.gcs_path`, `silver.gcs_path`, `silver.bq_project`/`bq_dataset`/`bq_location`. Confirm the controller auto-creates the BigQuery dataset and the DynamoDB row contains the GCP overlay.
2. **Adhoc Bronze run** via `python3 scripts/adhoc_run.py --pipeline-id ... --env dev`. Confirm Parquet files appear under `gs://<bronze-bucket>/<domain>/<table>/_extract_date=.../`.
3. **Silver auto-trigger.** EventBridge fires the GCP Silver job. Confirm Iceberg `metadata/` and `data/` directories appear under `gs://<silver-bucket>/<dataset>/<table>/`.
4. **BigLake external table** is created in BigQuery (`bq ls <project>:<dataset>` + `bq show ...`). Run a `SELECT COUNT(*)` from BigQuery against the table and confirm row count matches Bronze.
5. **Reconcile** — the GCP reconcile job runs against the GCS Iceberg table; confirm orphan-delete metrics match expectation.
6. **Cascade delete** — `DELETE /pipelines/{id}?cascade=true` removes the DynamoDB row; GCS data and BigQuery tables remain (consumer-owned, per the policy).
7. **Replicator no-op** — manually invoke `gcs_replicator` with a GCP pipeline_id; confirm `status: "skipped"` and reason mentions `destination.cloud=gcp`.

---

## Open follow-ups

1. **`BigQueryMetastoreCatalog` revisit.** When the catalog class lands in the iceberg-gcp-bundle on Glue's classpath (either via a Glue runtime bump or a custom JAR build), reconsider dropping the post-write DDL step. The BigLake external table would then be a managed metastore object instead of an external one — cleaner ownership semantics.
2. **Metrics field unification.** `s3_target_path` is set only for AWS pipelines today. UI surfaces (and any CLI that displays the "where did this land?" path) may need to read `bronze_target_path` / `silver_target_path` for cross-cloud display. Decide on a unified field name and either rename or dual-write before the GCP path is exposed to non-engineer consumers.
