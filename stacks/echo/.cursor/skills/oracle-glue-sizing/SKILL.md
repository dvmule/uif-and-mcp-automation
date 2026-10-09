---
name: oracle-glue-sizing
description: >-
  Connects to Oracle tables via Secrets Manager, queries row volume and
  partition metadata, and recommends AWS Glue initial-load worker_type and
  num_workers. Use when sizing Oracle ingestion, initial load backfill, Glue
  workers, JDBC partition strategy, or analyzing SCHEMA.TABLE before pipeline
  registration or EDS draft creation.
---

# Oracle → Glue Initial Load Sizing

Analyze Oracle table volume and emit Glue **initial** worker recommendations.
Never guess sizing — run the project's analyzer and use its output.

## Prerequisites

1. AWS credentials for the target env (`aws-runas`, `aws sso login`, or equivalent).
2. Oracle `secret_arn` — from `GET /connections`, an EDS draft `source.secret_arn`, or the user.
3. Table as `SCHEMA.TABLE` (uppercase).

## Workflow checklist

```
- [ ] Resolve secret_arn + SCHEMA.TABLE
- [ ] Run analysis (Path A or B below)
- [ ] Report volume, partition strategy, Glue sizing
- [ ] Emit two-profile config (glue.initial fat, glue.incremental lean)
- [ ] Surface warnings (missing/stale stats, unindexed watermark)
```

## Resolve Oracle connection

**From API** (when `EDS_API_BASE` is set):

```bash
curl -s "$EDS_API_BASE/connections"
```

Returns Secrets Manager secrets tagged `app=eds`. Match `connection_name` or use the `arn`.

**From repo drafts**: check `ingestion-framework/drafts/dev/gcp/eds/*.json` (GCP) or `drafts/dev/aws/{domain}/*.json` (AWS) → `source.secret_arn`.

## Path A — Local analyzer (workstation → Oracle)

Use when the machine can reach Oracle (VPN/direct) and has Python deps.

```bash
cd ingestion-framework
pip install oracledb boto3   # if needed

python3 scripts/analyze_oracle_local.py \
  --secret-arn <ARN> \
  --table SCHEMA.TABLE
```

**Optional hints** (skip metadata dict queries when Oracle user lacks `ALL_*` access):

| Flag | Purpose |
|------|---------|
| `--partition-col COL` | Force `numeric_range` on a numeric column |
| `--date-col COL` | Force date-based strategy |
| `--id-col COL` | ID column for `date_id_range` |
| `--date-start YYYY-MM-DD` | Skip MIN date probe |
| `--date-end YYYY-MM-DD` | Skip MAX date probe |
| `--hash-buckets N` | ORA_HASH buckets for `date_hash` (default 8) |
| `--instant-client-dir PATH` | Oracle thick mode (required if thin fails with DPY-3015 / 0x939) |

Read output fields: `rec` (recommendations), `stats`, `candidates`, `no_stats`.

## Path B — API analyzer (Lambda → Oracle)

Use when local Oracle is blocked. Lambda runs `_analyze_oracle_table` in `pipeline_controller.py`.

```bash
export EDS_API_BASE=https://<api-gateway>/dev   # staging/prod as needed
cd ingestion-framework
python3 scripts/analyze_pipeline.py \
  --secret-arn <ARN> \
  --table SCHEMA.TABLE \
  --env dev
```

Or POST directly:

```bash
curl -s -X POST "$EDS_API_BASE/analyze" \
  -H "Content-Type: application/json" \
  -d '{"secret_arn":"<ARN>","table":"SCHEMA.TABLE"}'
```

Read output fields: `recommendations`, `stats`, `partition_candidates`, `apply_command`, `profiles`.

**Choose Path A vs B**: try local first if Oracle is reachable; fall back to API.

## Interpreting analyzer output

| Field | Meaning |
|-------|---------|
| `partition_strategy` | `numeric_range`, `date_id_range`, `date_hash`, or `date_range` |
| `worker_type` / `num_workers` | **Initial load only** — cost-capped via `sizing_policy.py` (max G.2X × 8) |
| `estimated_dpu_capacity` | `num_workers × DPU_factor` (G.1X=1, G.2X=2) |
| `cost_note` | Predicates run in waves; DPU rate capped |
| `staged_backfill` | Present when `total_gb ≥ 80` — run multiple date-chunk jobs |
| `jdbc_predicates` | Composite date×ID predicates for large tables |
| `jdbc_partition_col` / bounds / `jdbc_partitions` | For `numeric_range` strategy |
| `total_gb` / `stats.total_size_gb` | `NUM_ROWS × AVG_ROW_LEN` estimate |
| `warning` | Missing or stale `DBMS_STATS` — recommend `DBMS_STATS.gather_table_stats` |
| `apply_command` | Copy-paste shell to PUT two-profile config via API |
| `profiles` | Pre-built `{initial, incremental}` from `_two_profile_recommendation` |

For sizing formula details, see [sizing-rules.md](sizing-rules.md).

## Required output shape (two profiles)

Always split initial vs incremental. Flat top-level `glue.worker_type` is the SPEA9I anti-pattern.

```json
{
  "glue": {
    "initial": {
      "worker_type": "<from analyze>",
      "num_workers": "<from analyze>",
      "timeout_minutes": 240
    },
    "incremental": {
      "worker_type": "G.1X",
      "num_workers": 1,
      "timeout_minutes": 60
    }
  },
  "extract": {
    "table": "SCHEMA.TABLE",
    "mode": "incremental",
    "watermark_col": "<user or detected date col>",
    "watermark_value": "1970-01-01T00:00:00",
    "initial": {
      "jdbc_partition_col": "<from analyze or null>",
      "jdbc_lower_bound": "<from analyze or null>",
      "jdbc_upper_bound": "<from analyze or null>",
      "jdbc_partitions": "<from analyze or null>",
      "jdbc_predicates": "<from analyze or null>"
    },
    "incremental": {
      "jdbc_partition_col": null,
      "jdbc_lower_bound": null,
      "jdbc_upper_bound": null,
      "jdbc_partitions": null,
      "jdbc_predicates": null
    }
  }
}
```

**Rules**:
- `glue.initial` ← analyzer `worker_type` / `num_workers`
- `glue.incremental` ← always G.1X × 1 (watermark delta only — never size from table volume)
- `extract.initial` ← JDBC partition fields from analyze
- `extract.incremental` ← all JDBC fields nulled (LEAN)
- First run uses `watermark_value: "1970-01-01T00:00:00"` → resolves to `initial` phase

Prefer `recommendations.profiles` or `apply_command` from API output when present.

## Report template

Present results to the user in this format:

```markdown
## Oracle analyze: SCHEMA.TABLE

**Connection:** <connection_name or secret ARN suffix>
**Volume:** <row_count> rows, <total_gb> GB (stats age: <stats_age_days> days)
**Strategy:** <partition_strategy> (<key columns>, <predicate count if any>)
**Initial Glue:** <worker_type> × <num_workers> (timeout 240 min)
**Incremental Glue:** G.1X × 1 (timeout 60 min)
**Warnings:** <warnings or "none">
**Next step:** <apply_command snippet, or update ingestion-framework/drafts/dev/gcp/eds/<pipeline>.json>
```

When updating EDS drafts, also record analyze metadata under `metadata.oracle_analyze` (see existing drafts in `ingestion-framework/drafts/dev/gcp/eds/`).

## Updating EDS draft JSON

After analyze, update the draft's:

- `glue.initial.worker_type` / `num_workers` / `timeout_minutes`
- `extract.initial.jdbc_*` fields per strategy
- `metadata.draft_notes` with analyze date, strategy, and any warnings
- `metadata.oracle_analyze` with full analyze snapshot for audit

Keep `glue.incremental` at G.1X × 1 unless user explicitly overrides with `extract.incremental.allow_heavy`.

## Anti-patterns

- Do NOT invent worker counts — run the analyzer
- Do NOT put initial-load workers on `glue.incremental`
- Do NOT use flat `extract.jdbc_*` at extract root for new pipelines
- Do NOT ignore `warning` — flag stale/missing Oracle stats to the user
- If watermark column is unindexed, warn that incremental runs will full-scan (SPEA9I pattern)

## Source of truth in codebase

| Component | Location |
|-----------|----------|
| **Sizing policy (source of truth)** | `ingestion-framework/lambda/sizing_policy.py` |
| Local CLI | `ingestion-framework/scripts/analyze_oracle_local.py` |
| API CLI | `ingestion-framework/scripts/analyze_pipeline.py` |
| Lambda handler | `ingestion-framework/lambda/pipeline_controller.py` → `trigger_analysis`, `_analyze_oracle_table` |
| Sizing math | `sizing_policy.compute_partition_sizing` |
| Two-profile builder | `_two_profile_recommendation`, `_build_apply_command` |
| Example drafts | `ingestion-framework/drafts/dev/gcp/eds/*.json`, path helper `scripts/draft_paths.py` |
