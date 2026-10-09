# Beacon incremental extraction (UIFPE-168)

A Beacon feed can now run in one of two extraction modes, chosen per feed:
**full load** (every run delivers the complete dataset — the only behavior
before this feature) or **incremental** (every run delivers only the rows
changed since the last successful run). This document is the detailed design
the story deferred ("detailed design to follow when picked up"): what the
author configures, where the state lives, how a batch runs in each mode, and
the failure semantics that keep partners' data gap-free.

Owner decisions recorded 2026-08-27 (with the reporter/assignee):

1. **No deletions in v1.** Incremental = inserts + updates, selected by a
   watermark predicate on the Gold entity table. Reconcile hard-deletes rows
   from the entity table, so an entity-table watermark can never surface a
   delete; partners who need deletions run full loads. Full CDC via the
   ADR-0003 D8 change log (`<entity>_changes`, op INSERT/UPDATE/DELETE) is
   the designed follow-up slice, not this one.
2. **An empty increment delivers an empty (header-only) file** and records a
   `succeeded` run. Predictable cadence: "no file" unambiguously means
   "something broke".
3. **Bootstrap is automatic; reset is an operator action.** The first
   incremental run (no stored watermark) runs as a full **baseline** extract
   and seeds the watermark; the panel's *Reset watermark* action forces the
   next run to re-baseline — the affordance for a Gold `--rebuild` (which
   re-stamps rows without change-log rows; ADR-0003 D5: consumers resync via
   full load) or a partner resync.

## The split that shapes everything: mode vs. state

- The **mode** is authoring config, stamped into the immutable version
  (D22): `settings.extraction = {"mode": "full" | "incremental"}` on the
  draft, resolved at publish into an explicit `extraction` block in
  `export.json`. A version without the block is unambiguously pre-feature
  (= full) — the UIFPE-172 missing-key convention.
- The **watermark value** is runtime state and can never live in a version.
  It sits in its own DynamoDB table `beacon-<env>-extraction-state` (hash
  `job_id`), the same lives-outside-versions posture as schedules and
  transform sizing. One row per export: `watermark`, `updated_at`,
  `batch_id` (which batch set it).

## The extraction block (export.json)

```json
"extraction": {
  "mode": "incremental",
  "source": "product",
  "watermark_column": "_gold_updated_at"
}
```

The author only ever picks the mode. `source` defaults to the mapping's
first source; `watermark_column` is **platform-owned** — resolved at publish
from the source registry (`watermark_column` attribute, e.g.
`gold_product.product` → `_gold_updated_at`). A source with no registered
watermark column cannot publish an incremental feed: refused at publish,
never discovered at run time. This also sidesteps the authoring surface's
deliberate stripping of `_`-prefixed metadata columns — authors never see or
type the column, and the engine receives it as a run parameter, so the
panel's reference validation (which hides `_` columns) is never in the loop.

`_gold_updated_at` is the right watermark because ADR-0003 D7 moves it ONLY
on canonical-hash (business) change — silent backfills and maintenance never
touch it. The gold product master carries no business timestamps at all, so
the framework column is the only candidate.

## How a batch runs

Both starters — `publisher.start_run` (Run now) and the `scheduled_starter`
Lambda (they are separate codebases; change them in lockstep) — now supply a
fourth input-contract field alongside `job_config` and `trigger`:

```
"watermark": "" | "YYYY-MM-DD HH:MM:SS[.ffffff]"
```

For an incremental feed the starter reads the state table; `""` means no
stored watermark (first run, after a reset, state table unreadable) and the
batch runs as a **baseline**: a full extract under full-load gate semantics
that still reports a high-water mark to seed the state. Degrading to ""
is deliberate everywhere: over-delivery, never gaps.

Inside the transform (`beacon_export_job.run`):

- **Windowed extract.** The engine gains two per-run parameters (never
  config): `source_filters` — `{source: "<col> > TIMESTAMP '<watermark>'"}`
  applied to the source frame at the read, BEFORE joins and pruning (the
  post-join `filters[]` deliberately don't push down — the persist-before-
  filter segfault workaround — so a window there would cut output, not scan)
  — and `track_max` — `{"source", "column"}`. The predicate is strictly
  greater: a row AT the watermark was already delivered.
- **Empty-input policy is untouched.** Source filters apply AFTER the
  `on_empty_input` check: an empty *increment* is a legitimate no-change
  window; an empty *source* keeps today's semantics.
- **High-water mark is data-derived.** `max(watermark_column)` observed over
  the (filtered) source, full-microsecond formatted so lexicographic order
  == chronological order. Never the batch clock: gold stamps rows with the
  gold RUN's start time but commits later, so a wall-clock watermark would
  permanently skip rows committed after the read but stamped earlier. An
  empty window reports no high-water mark and the state stays put (the
  re-covered window is idempotent under strictly-greater).
- **Gate semantics are per-mode.** A windowed extract passes
  `incremental=True` to the quality gate: the `min_rows` floor and the
  row-count delta warning (both calibrated for full snapshots) don't apply;
  the rejection ceiling still does. The baseline run uses full semantics.
- **Watermark hygiene.** The value is spliced into a Spark SQL predicate, so
  the job refuses anything that isn't a plain timestamp literal, and the
  config validator holds `watermark_column` to a bare identifier.

## The commit point: report.json → record_run

The state machine discards the transform's output (`ResultPath = null`), so
the transform now writes its full summary to
`<canonical>/_report/report.json` (underscore-prefixed: Spark's file index
ignores it when the render stage reads canonical back — the same trick as
`_rendered/`). This is the report.json-through-S3 handoff the domain
CLAUDE.md carried as an open task.

On the SUCCESS branch, `record_run` re-reads it and:

1. merges the row counts flat into the run summary — the runs table (and the
   panel's Rows out / Rejected / Filtered columns) finally get real numbers;
2. commits the high-water mark to the state table with a **monotonic
   conditional update** (`attribute_not_exists(watermark) OR watermark < :w`
   — the gold `merge_watermarks` posture). Manual and scheduled runs can
   overlap (only the scheduled starter has an in-flight guard); monotonic
   advance makes the race safe: duplicates possible, gaps impossible.

Advancing HERE and only here is the failure story: a failed or stopped batch
never reaches the success branch, the watermark stays put, and the next run
re-covers the whole window. Both enrichment steps are best-effort — a report
read failure must never lose the run row.

## Panel surface

- `GET /catalog/{job_id}/extraction` — mode (latest version) + stored
  watermark (+ which batch set it, when).
- `DELETE /catalog/{job_id}/extraction/watermark` — reset (204); next run
  re-baselines. Surfaced on ExportDetail with a confirm.
- Wizard: an Extraction card on Output & delivery — incremental selectable
  only when the selected source's registry entry advertises a
  `watermark_column`; switching to an unsupported source reverts to full.

## Infrastructure delta

- New table `beacon-<env>-extraction-state` (naming per the 2026-08-26
  convention). No seeded rows: an absent row IS the baseline state.
- State machine: `watermark` added to Init/Transform (`--watermark` Glue
  argument; the Glue wrapper drops the empty string), and
  `export_config_s3_path` added to the RecordRun payload (success branch
  only — the failure branch must never advance state).
- IAM: record_run gains `dynamodb:UpdateItem` on the state table +
  `s3:GetObject` on the data bucket; scheduled-starter and the API task gain
  reads (API also `DeleteItem` for reset).

## Known limits (v1, by design)

- **Single-source watermark only (UIFPE-237, tracked as UIFPE-238).** The
  window predicate applies to ONE source (`extraction.source`) at the read,
  before joins — a multi-source config (e.g. product-export v2's
  product ⋈ price) can never see a change that only touched a joined
  source: a price-only update moves no product watermark, so the row is
  silently absent from the increment; windowing both sources pre-join is
  worse (a changed row loses its unchanged join partner). The correct
  semantics is a POST-join OR over per-source watermarks with per-source
  state — designed as UIFPE-238. Until then multi-source feeds publish as
  full load, and the wizard forces mode "full" for them with an explanation.
  Since UIFPE-167 the publisher enforces it too: `_incremental_extraction_block`
  refuses `mode: incremental` when the mapping config has more than one
  source, so an API-authored draft cannot bypass the wizard's rule.
- **Scan cost is unchanged.** The window predicate cuts what enters the
  pipeline (and what partners receive), not the Gold scan itself — read
  depth optimization (predicate pushdown at the source read) is a later
  concern at current catalog scale.
- **No deletions** (decision 1). The change-log/D8 cursor design is the
  follow-up slice.
- **Rebuild detection is manual.** Gold `--rebuild` re-stamps silently; the
  operator resets the watermark. No gold-refresh event exists to automate it
  (design doc open question Q6).
- **A reset can race an in-flight batch.** Reset deletes the state row; a
  batch already running commits its high-water mark on the success branch
  (`attribute_not_exists` passes after a delete), silently re-seeding the
  watermark — the reset looks like it didn't take. The panel disables Reset
  while it can see a run in flight, but a scheduled batch can still race.
  Operator recovery is simply reset again once the batch settles; the
  tombstone-instead-of-delete design that closes the race entirely is a
  follow-up slice.
- **Iceberg native incremental reads were evaluated and rejected**: the
  entity tables are MERGE-maintained (incremental append scans reject
  replace/delete snapshots), and snapshot ids are per-run values with the
  same state problem a timestamp has — minus its readability.
