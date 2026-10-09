# Incident write-up: FPI_PAR_SALES BigQuery merge failures (2026-07-21)

**Pipeline:** `fipm-fpi-par-sales` — Oracle EBS `FIPM.FPI_PAR_SALES` → GCS Bronze → BigQuery Silver
`pearson-rr.ENTERPRISE_IPM_SILVER.FPI_PAR_SALES` (94.4M rows / 107 GB, clustered on `SALES_ID`)

**Status:** Resolved. Daily merges now complete in ~1 minute (previously: never completed).

---

## The problem

The pipeline could not complete an incremental merge. Every attempt since 07-18 either ran
25+ minutes and was cancelled, or died at the 60-minute Glue timeout. The failure had three
layers:

1. **A correctness bug set the trap.** The merge selected new Bronze rows with
   `_extract_date > last_processed_date` — date granularity. A Bronze re-run later on an
   already-processed day produced rows with the same date, which the filter skipped
   *forever*. That's how a 5M-row delta got stranded on 07-20.

2. **The workaround sprang it.** `silver.last_processed_date` was manually rewound to
   `2026-01-01` to force pickup. But the entire initial dump shares one `_extract_date`, so
   every merge thereafter tried to reprocess all ~99M rows — impossible within the timeout,
   repeating daily.

3. **The killer underneath both.** Even a *normal* 68K-row merge couldn't finish: the
   `IS NOT DISTINCT FROM` NULL-safe join in the MERGE `ON` clause blocks BigQuery's
   broadcast-join optimization, forcing the full 94M-row target through shuffle. On the
   small, contended slot reservation this exceeds BQ's shuffle disk/memory quota
   (`Resources exceeded during query execution: ... maximum disk and memory limit available
   for shuffle operations`). Proven by a controlled A/B with real data: the identical
   68,356-row merge failed at 20 minutes with the old operator and completed in
   **38.9 seconds** with plain `=`.

## What changed

Commits on `development`: `243644f`, `fb1c95c`, `aa5b99d`, `a9ff084`.

### Code — `glue_jobs/generic_silver_bq_native.py` (shared by all bq_native pipelines)

- **Timestamp-granular watermark**: new `silver.last_processed_ts`, filter
  `_extract_date >= DATE(ts) AND _extract_timestamp > TIMESTAMP(ts)` — the date predicate
  keeps hive-partition pruning, the timestamp makes same-day re-runs safe. Legacy date-only
  configs self-migrate on their first successful run.
- **Plain `=` merge join when provably safe**: the pre-merge count query checks
  `COUNTIF(pk IS NULL)` on the delta; zero NULLs → plain equality (broadcast join
  preserved). Composite/nullable PKs keep `IS NOT DISTINCT FROM`.
- **PK-range pruning**: single-column numeric PKs get `AND t.pk BETWEEN min AND max` on the
  `ON` clause, typed to the target column, for clustered block pruning.
- **`silver.int64_cols`**: opt-in narrowing of Oracle NUMBER PKs from BIGNUMERIC (32-byte
  keys) to INT64 at initial CTAS.
- **Live logs**: stdout is line-buffered (`sys.stdout.reconfigure(line_buffering=True)`),
  so Python Shell runs stream step-by-step to CloudWatch instead of dumping at exit.

### Config — `fipm-fpi-par-sales`

- Dropped `watermark_fallback_col` — the `COALESCE(LAST_UPDATE_DATE, CREATION_DATE)` it
  generated defeated Oracle's watermark index (full 94M-row scan per incremental);
  `LAST_UPDATE_DATE` has zero NULLs in 94.4M rows, so the fallback bought nothing.
- Added `int64_cols: ["SALES_ID"]`; table rebuilt (114s) with an INT64 clustered key.

### Infra

- Glue timeout 60 → 240 min for `eds-{env}-silver-bq-native` (live in dev + Terraform) —
  a slow merge should finish late, not die and re-burn slots.
- Updated script synced to **both** stacks' buckets (`eds-dev-glue-scripts` and legacy
  `data-connect-glue-scripts-dev`; the legacy `generic-silver-bq-native-dev` job runs the
  same file).

### Verified end-to-end

Real-data test (Bronze watermark rewound to 2026-07-15): 68,356 rows extracted in 108s via
the now-indexed predicate, picked up exactly by the timestamp filter, PK-range pruning
engaged, merged in 38.9s (1.7M slot-ms), watermarks self-restored automatically.

## What other pipelines need

All bq_native pipelines get the **code fixes for free** — timestamp watermark
(self-migrating), auto `=` join, range pruning, live logs. Remaining actions:

| Action | Who needs it | How |
|---|---|---|
| Audit `watermark_fallback_col` | Every incremental pipeline that sets it | If `COUNTIF(watermark_col IS NULL) = 0` in the Silver table, set it to `null` — otherwise every Bronze incremental full-scans Oracle |
| Opt into `int64_cols` | Pipelines with BIGNUMERIC numeric PKs (check analyzer `id_max` fits INT64) | Add to config; takes effect on the next initial rebuild (sentinel reset — ~2 min even for 190M rows). Not urgent: the `=` join fix helps BIGNUMERIC keys too |
| Composite/nullable PKs (e.g. `fpi_par_batch`) | Still on `IS NOT DISTINCT FROM` | Exposed to the same shuffle wall if their tables grow; future work — per-column NULL detection or chunked merges |
| Timeout + script sync in other envs | staging/prod | `make apply ENV=…` + `eds-deploy.sh` (dev is done, both stacks) |
| Reservation sizing | Project-level | Ask the GCP admin about the `pearson-rr` EU reservation / autoscaling — identical 112 GB jobs swung 41.6s ↔ 274s on contention |
| ⚠️ `fipm-fpi-par-stage` | Failed twice on 2026-07-21 (07:43, 08:02) | Cause unknown — diagnose next |

## Operational rules that came out of this

- **Never manually rewind `silver.last_processed_date`** to force re-merging. To reprocess:
  rewind `extract.watermark_value` (Bronze re-extracts; MERGE is PK-idempotent; both
  watermarks self-restore on success), or set `last_processed_date` to sentinel
  `1970-01-01` for a full LOAD+CTAS rebuild — far cheaper than a big MERGE.
- Killed/TIMEOUT Python Shell runs write **no metrics row** (`finally` never runs) and may
  leave the BQ job running server-side — check Glue run state, not just the metrics table.
- `aws glue update-job` **replaces** the job definition: omitting `MaxCapacity` silently
  resets a pythonshell job to 0.0625 DPU. Always get → mutate → send the full payload.

See also: `CLAUDE.md` → Known Gotchas / Troubleshooting for the condensed version.
