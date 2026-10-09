"""Pipeline orchestration: run(spark, config, batch_id) drives all stages end to end."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from . import engine
from .config_validator import (collect_references, validate_expressions,
                               validate_references, validate_structure)
from .errors import ConfigValidationError, EmptyInputError, Issue
from .models import MappingConfig


@dataclass
class RunReport:
    batch_id: str
    config_path: str | None
    status: str = "unknown"          # succeeded | skipped_empty_input | failed
    run_timestamp: str = ""
    input_rows: dict[str, int] = field(default_factory=dict)
    filtered_rows: int = 0     # kept for the report/run-row shape; not counted since UIFPE-620
    output_rows: int = 0
    rejected_rows: int = 0
    rejected_by_rule: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    # max(track_max column) over the (window-filtered) source — the incremental
    # high-water mark (UIFPE-168). None when tracking is off or no rows matched.
    high_water_mark: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run(spark: SparkSession, config: MappingConfig, batch_id: str,
        config_path: str | None = None,
        frames: dict[str, DataFrame] | None = None,
        source_filters: dict[str, str] | None = None,
        track_max: dict[str, str] | None = None) -> RunReport:
    """Execute the full pipeline. `frames` lets tests inject in-memory DataFrames
    in place of engine.read_sources; production callers leave it None.

    `source_filters` ({source name: Spark SQL condition}) narrows a source at
    the read, before joins — per-run values (an incremental window) arrive
    here from the caller, never from the authored config, because published
    configs are immutable (D22). `track_max` ({"source", "column"}) reports
    max(column) over that source as RunReport.high_water_mark (UIFPE-168)."""
    started = time.monotonic()
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    placeholders = {"batch_id": batch_id, "run_timestamp": run_ts}
    report = RunReport(batch_id=batch_id, config_path=config_path, run_timestamp=run_ts)

    # 1. static validation — fail before any Spark activity
    issues = validate_structure(config) + validate_expressions(config)
    if issues:
        raise ConfigValidationError(issues)

    # 2. read sources
    if frames is None:
        frames = engine.read_sources(spark, config.sources)

    # 3. reference validation against real schemas (no data read)
    schemas = {name: df.columns for name, df in frames.items()}
    # UIFPE-580: list elements (alias.field) are checked like source columns
    schemas.update(engine.element_schemas(frames, config))
    issues = validate_references(config, schemas)
    if issues:
        raise ConfigValidationError(issues)

    # empty-input policy
    empty_sources = [name for name, df in frames.items() if df.isEmpty()]
    if empty_sources:
        if config.policies.on_empty_input == "fail":
            raise EmptyInputError(f"empty input source(s): {', '.join(empty_sources)}")
        if config.policies.on_empty_input == "skip":
            report.status = "skipped_empty_input"
            report.duration_seconds = round(time.monotonic() - started, 2)
            return report

    # 3aa. per-run source filters (UIFPE-168): applied at the source, before
    # joins and pruning, so an incremental window cuts what enters the
    # pipeline. Deliberately AFTER the empty-input policy — an empty INCREMENT
    # is a legitimate no-change window, not an empty source. Re-aliasing is
    # load-bearing for the same reason as prune_sources.
    if source_filters:
        frames = dict(frames)  # never mutate a caller-injected dict
        for name, condition in source_filters.items():
            if name not in frames:
                raise ConfigValidationError(
                    [Issue(f"source_filters[{name!r}]", "unknown source")])
            frames[name] = frames[name].filter(F.expr(condition)).alias(name)

    # 3ab. high-water mark (UIFPE-168): max(column) over the filtered source,
    # BEFORE prune_sources drops platform metadata columns the config never
    # references. Data-derived on purpose: a wall-clock watermark would skip
    # rows committed after this read but stamped earlier. Full-microsecond
    # formatting keeps lexicographic order == chronological order.
    if track_max:
        name, column = track_max["source"], track_max["column"]
        if name not in frames:
            raise ConfigValidationError(
                [Issue("track_max", f"unknown source {name!r}")])
        if column not in frames[name].columns:
            raise ConfigValidationError(
                [Issue("track_max", f"source {name!r} has no column {column!r}")])
        value = frames[name].agg(F.max(F.col(column)).alias("hwm")).collect()[0]["hwm"]
        if value is not None:
            report.high_water_mark = (value.strftime("%Y-%m-%d %H:%M:%S.%f")
                                      if isinstance(value, datetime) else str(value))

    # 3b. prune to config scope (UIFPE-174): each source keeps only the
    # columns the config references. persist() further down materializes
    # whatever schema it is given, so width must be cut before anything
    # caches — this is what kept a 7-column feed from caching 58 columns.
    frames = engine.prune_sources(frames, collect_references(config, schemas))

    # 4. joins
    joined = engine.apply_joins(frames, config.joins, config.source_names())
    # 4a. UIFPE-580: list columns spread into rows AFTER the joins and BEFORE
    # the filters, so a filter on the element (one price list, one org)
    # narrows rows the way the owner expects
    joined = engine.apply_unnest(joined, config.unnest)

    # 4b. row filters — scope definition (D18), applied straight to the joined
    # frame so Spark pushes a driving-table predicate down into the scan and
    # the join (and any list spread) runs on the matched rows only. UIFPE-620:
    # this step used to persist and count the UNFILTERED join to report
    # filtered_rows — which materialised 6.8M products × up to 126 prices for
    # a two-product export until the workers' disks filled ("No space left on
    # device", batch b-20260925-121328). The number it produced left every
    # screen in UIFPE-613; filtered_rows stays in the report shape as 0. One
    # scan either way, so the 2026-08-20 double-scan segfault cannot return.
    if config.filters:
        joined = engine.apply_filters(joined, config.filters)

    # 5. dry run — compile every rule against the real schema, zero rows
    issues = engine.dry_run_issues(joined, config.mappings, placeholders)
    if issues:
        raise ConfigValidationError(issues)

    # 6. mapping compilation — all rules in ONE select
    pairs = engine.compile_mappings(config.mappings, placeholders)
    mapped = joined.select(*[col.alias(name) for name, col in pairs])

    # 7. row-level validation
    valid, rejected = engine.apply_validations(mapped, config.validations)
    if rejected is not None:
        rejected = rejected.persist()
        report.rejected_rows = rejected.count()
        if report.rejected_rows:
            for row in (rejected
                        .select(F.explode(engine.FAILED_RULES_COL).alias("rule"))
                        .groupBy("rule").count().collect()):
                report.rejected_by_rule[row["rule"]] = row["count"]
            if config.rejected_path:
                rejected.write.mode("overwrite").json(
                    engine.substitute(config.rejected_path, placeholders))
        rejected.unpersist()

    # 8. write outputs
    valid = valid.persist()
    report.output_rows = valid.count()
    engine.write_outputs(valid, config.output_targets, placeholders)
    valid.unpersist()

    report.status = "succeeded"
    report.duration_seconds = round(time.monotonic() - started, 2)
    return report
