---
paths:
  - "**/glue_jobs/**/*.py"
  - "**/schemas/**"
  - "**/scripts/register_*.py"
  - "stacks/*/src/**/*.py"
---
# Catalog and dataset names

Vocabulary and the legacy-rename prohibition live in `00-core.md`.

| Object | Terraform type | Form | Example |
|---|---|---|---|
| Glue database — Silver | `aws_glue_catalog_database` | `uif_{stack}_{env}_{layer}_{source_system}` | `uif_echo_dev_silver_tep`, `uif_echo_dev_silver_mdm` |
| Glue database — Gold | `aws_glue_catalog_database` | `uif_{stack}_{env}_{layer}_{domain}` | `uif_echo_dev_gold_product` |
| Glue / Iceberg table | `aws_glue_catalog_table` | `{entity}` or `api_{entity}_view` | `mtl_system_items_b`, `api_product_view` |

**Silver is keyed by source system; Gold is keyed by business domain.** This is the one
place the two layers differ in how their database is named, and it follows from what each
layer is: Silver mirrors a source, Gold models an entity.

## A table name is the entity only

Never repeat what the database already states, and never carry a hyphen.

✅ `uif_echo_dev_gold_product.product`, `uif_echo_dev_gold_product.api_product_view`
❌ `uif_echo_dev_gold_product.gold_product_view` — restates the layer and domain
❌ `uif_echo_dev_gold_product.mtl-system-items-b` — a hyphenated S3 key segment carried
into a table name; convert to snake_case

Gold view names describe the tier (`api_product_view`), not the pipeline that built them.

## Where this lands in code and config

| Field | Rule |
|---|---|
| `silver.database` | `uif_{stack}_{env}_silver_{source_system}` |
| `silver.table` | entity only — no domain, no layer, no hyphen |
| `schemas/gold/{domain}/*.json` | table names inside a schema follow the entity-only rule |
| databases auto-created at registration | built from the same `{layer}_{key}` pair, so the draft must be right before registering |

## BigQuery — retained, not tabulated

The current standard does not cover BigQuery or GCS. These forms are **retained from the
previous convention** because GCP-native pipelines exist and would otherwise be
ungoverned. They are not ratified by the current standard and should be confirmed with
its owner.

| Object | Form | Example |
|---|---|---|
| BigQuery dataset | upper-underscore | `UIF_ECHO_DEV_SILVER_IPM` |
| BigQuery table | snake_case entity only | `fpi_par_lines` |
