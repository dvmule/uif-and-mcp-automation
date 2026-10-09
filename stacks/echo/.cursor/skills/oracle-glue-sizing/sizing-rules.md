# Oracle → Glue Sizing Rules

Source: [`ingestion-framework/lambda/sizing_policy.py`](../../../ingestion-framework/lambda/sizing_policy.py)
(mirrored in `analyze_oracle_local.py`, `pipeline_controller.py`, `glue_jobs/analyze_table.py`).

## Cost-aware policy (initial load)

**Calendar date span does not drive parallelism — Oracle `NUM_ROWS × AVG_ROW_LEN` does.**

| Constant | Value | Purpose |
|----------|-------|---------|
| `GB_PER_SLICE` | 8 | Target data per JDBC slice |
| `MAX_INITIAL_PREDICATES` | 16 | Hard cap on `jdbc_predicates` length |
| `MAX_INITIAL_WORKERS` | 8 | Hard cap on `glue.initial.num_workers` |
| `STAGED_BACKFILL_GB` | 80 | Recommend multi-run backfill above this |

## Volume estimate

```
total_gb = (num_rows × avg_row_len) / 1024³
```

If `NUM_ROWS` is NULL → `no_stats = true` → use G.2X, conservative slices, warn to run `DBMS_STATS`.

## Worker type (initial only)

| Condition | worker_type |
|-----------|-------------|
| `total_gb < 20` and stats present | G.1X |
| `total_gb ≥ 20` or `no_stats` | G.2X |

G.4X is not auto-selected. Use explicit operator override if needed.

## Parallelism (decoupled)

```
target_predicates = min(16, max(1, ceil(total_gb / 8)))
num_workers       = min(8, target_predicates)
estimated_dpu_capacity = num_workers × DPU_factor   # G.1X=1, G.2X=2
```

`num_workers` is a **DPU cost cap**. Predicates may equal `target_predicates`; Spark runs them in waves on available executors.

## Partition strategy (cost-aware)

1. **numeric_range** — single numeric PK when `total_gb < 40`
2. **date_id_range** — only when `total_gb ≥ 20` and ~8+ GB per date window
3. **date_hash** — preferred for small tables with long date history (sparse data)
4. **date_range** — fallback date windows only

Date windows use volume-driven `_date_window_ranges` (month → quarter → year coarsening), **not** one window per calendar month.

## Predicate caps

- `jdbc_predicates` length ≤ **16** (Spark plan safety; see `tasks/lessons.md`)
- `jdbc_partitions` (numeric_range) ≤ **16**
- `glue.initial.num_workers` ≤ **8**

## Staged backfill

When `total_gb ≥ 80`, analyze adds `staged_backfill` with date chunks. Operator runs multiple adhoc jobs with `--date-start` / `--date-end` per chunk.

## Incremental profile (fixed)

```
worker_type: G.1X
num_workers: 1
timeout_minutes: 60
```

No JDBC predicates on incremental — watermark filter only.

## Example DPU capacity

| Table size | Typical initial | DPU capacity |
|------------|-----------------|--------------|
| 0.5 GB (sparse dim) | G.1X × 1 | 1 DPU |
| 25 GB | G.2X × 4 | 8 DPUs |
| 120 GB | G.2X × 8 | 16 DPUs |

Compare to old model: 120 GB `date_id_range` could recommend G.4X × 68 = **272 DPUs**.

## Stale stats warning

- `no_stats` → `DBMS_STATS.gather_table_stats('SCHEMA', 'TABLE')`
- `stats_age_days > 7` → warn; bounds may use live MIN/MAX probe
