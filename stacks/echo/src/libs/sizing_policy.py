"""Cost-aware initial-load sizing for Oracle → Glue JDBC extract.

Volume-first policy: calendar date span does not drive parallelism — Oracle
NUM_ROWS × AVG_ROW_LEN does. Glue num_workers is decoupled from predicate count
and capped to limit DPU burn rate.

Imported by pipeline_controller.py and analyze_oracle_local.py.
Glue analyze_table.py duplicates these constants/functions — keep in sync.
"""

from __future__ import annotations

import math
from datetime import date as _date
from typing import Any

MAX_INITIAL_WORKERS = 8
MAX_INITIAL_PREDICATES = 16
GB_PER_SLICE = 8.0
STAGED_BACKFILL_GB = 80.0
GB_PER_WINDOW_FOR_ID_RANGE = 8.0
SMALL_TABLE_GB = 20.0
NUMERIC_RANGE_MAX_GB = 40.0
DEFAULT_HASH_BUCKETS = 8

DPU_FACTORS = {"G.1X": 1, "G.2X": 2, "G.4X": 4, "G.8X": 8}

# AWS Glue minimum NumberOfWorkers per WorkerType (G/R series require >= 2).
GLUE_MIN_WORKERS = {
    "G.025X": 1,
    "G.1X": 2,
    "G.2X": 2,
    "G.4X": 2,
    "G.8X": 2,
    "G.12X": 2,
    "G.16X": 2,
    "R.1X": 2,
    "R.2X": 2,
    "R.4X": 2,
    "R.8X": 2,
    "Standard": 1,
}

COST_NOTE = (
    "Predicates may exceed worker count; Spark runs JDBC partitions in waves. "
    "Wall-clock is longer but DPU rate stays capped."
)


def stats_total_gb(stats: dict) -> float:
    return (stats["num_rows"] * stats["avg_row_len"]) / (1024 ** 3)


def target_parallelism(total_gb: float) -> tuple[int, int]:
    """Return (target_predicates, num_workers)."""
    preds = max(1, min(MAX_INITIAL_PREDICATES, math.ceil(total_gb / GB_PER_SLICE)))
    workers = min(MAX_INITIAL_WORKERS, preds)
    return preds, workers


def choose_worker_type(total_gb: float, no_stats: bool) -> str:
    if no_stats or total_gb >= SMALL_TABLE_GB:
        return "G.2X"
    return "G.1X"


def clamp_glue_workers(worker_type: str, num_workers: int) -> int:
    """Enforce Glue StartJobRun minimum workers for the worker type."""
    return max(int(num_workers), GLUE_MIN_WORKERS.get(worker_type, 1))


def estimated_dpu_capacity(worker_type: str, num_workers: int) -> int:
    return int(num_workers) * DPU_FACTORS.get(worker_type, 1)


def month_ranges(start_date: _date, end_date: _date) -> list[tuple[_date, _date]]:
    y, m, result = start_date.year, start_date.month, []
    while _date(y, m, 1) <= end_date:
        s = _date(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        result.append((s, _date(ny, nm, 1)))
        y, m = ny, nm
    return result


def date_window_ranges(
    start_date: _date,
    end_date: _date,
    target: int,
) -> list[tuple[_date, _date]]:
    """Bucket [start_date, end_date] into roughly `target` windows."""
    target = max(1, min(MAX_INITIAL_PREDICATES, target))
    total_months = (end_date.year - start_date.year) * 12 \
                   + (end_date.month - start_date.month) + 1
    if total_months <= target:
        return month_ranges(start_date, end_date)

    if total_months // 3 <= target:
        windows, y, m = [], start_date.year, ((start_date.month - 1) // 3) * 3 + 1
        while _date(y, m, 1) <= end_date:
            s = _date(y, m, 1)
            nm = m + 3
            ny = y + (nm - 1) // 12
            nm = ((nm - 1) % 12) + 1
            windows.append((s, _date(ny, nm, 1)))
            y, m = ny, nm
        return windows

    total_years = end_date.year - start_date.year + 1
    bucket_years = max(1, math.ceil(total_years / target))
    windows, y = [], start_date.year
    while y <= end_date.year:
        windows.append((_date(y, 1, 1), _date(y + bucket_years, 1, 1)))
        y += bucket_years
    return windows


def score_numeric_candidate(c: dict) -> int:
    return (
        (2 if c["column"].endswith("_ID") else 0)
        + (1 if c["nulls"] == 0 else 0)
        + (1 if (c["max"] - c["min"]) > 100_000 else 0)
    )


def choose_partition_strategy(
    hint_col: str | None,
    pk_info: dict,
    date_cols: list,
    numeric_candidates: list,
    total_gb: float = 0.0,
) -> dict:
    """Pick JDBC partition strategy with cost-aware bias."""
    base: dict[str, Any] = {
        "numeric_col": None,
        "date_col": None,
        "id_col": None,
        "hash_col": None,
    }
    if hint_col:
        return {**base, "strategy": "numeric_range", "numeric_col": hint_col}

    pk_cols = pk_info.get("pk_cols", [])
    numeric_col_names = {c["column"] for c in numeric_candidates}

    if len(pk_cols) == 1 and pk_cols[0] in numeric_col_names and total_gb < NUMERIC_RANGE_MAX_GB:
        return {**base, "strategy": "numeric_range", "numeric_col": pk_cols[0]}

    if date_cols:
        not_null = [d for d in date_cols if d.get("nullable") == "N"]
        best_date_col = (not_null[0] if not_null else date_cols[0])["column"]
        id_cands = [c for c in numeric_candidates if c["column"].endswith("_ID")]

        target_windows, _ = target_parallelism(total_gb)
        gb_per_window = total_gb / max(1, target_windows)

        if id_cands and total_gb >= SMALL_TABLE_GB and gb_per_window >= GB_PER_WINDOW_FOR_ID_RANGE:
            best_id = max(id_cands, key=score_numeric_candidate)
            return {
                **base,
                "strategy": "date_id_range",
                "date_col": best_date_col,
                "id_col": best_id["column"],
            }

        if numeric_candidates and total_gb >= SMALL_TABLE_GB and gb_per_window >= GB_PER_WINDOW_FOR_ID_RANGE:
            best_num = max(numeric_candidates, key=score_numeric_candidate)
            return {
                **base,
                "strategy": "date_id_range",
                "date_col": best_date_col,
                "id_col": best_num["column"],
            }

        hash_col = None
        if id_cands:
            hash_col = max(id_cands, key=score_numeric_candidate)["column"]
        elif numeric_candidates:
            hash_col = max(numeric_candidates, key=score_numeric_candidate)["column"]
        else:
            hash_col = best_date_col
        return {
            **base,
            "strategy": "date_hash",
            "date_col": best_date_col,
            "hash_col": hash_col,
        }

    if numeric_candidates:
        best = max(numeric_candidates, key=score_numeric_candidate)
        return {**base, "strategy": "numeric_range", "numeric_col": best["column"]}
    return {**base, "strategy": "numeric_range"}


def compute_partition_sizing(
    stats: dict,
    strategy_cfg: dict,
    date_bounds: dict | None,
    hash_buckets: int = DEFAULT_HASH_BUCKETS,
) -> dict:
    total_gb = stats_total_gb(stats)
    target_preds, num_workers = target_parallelism(total_gb)
    worker_type = choose_worker_type(total_gb, stats["no_stats"])
    num_workers = clamp_glue_workers(worker_type, num_workers)
    strategy = strategy_cfg["strategy"]

    if strategy == "numeric_range":
        partitions = target_preds
        return {
            "total_gb": total_gb,
            "jdbc_partitions": partitions,
            "num_workers": num_workers,
            "worker_type": worker_type,
            "id_splits": None,
            "target_predicates": target_preds,
            "effective_hash_buckets": None,
        }

    windows_count = 1
    if date_bounds:
        windows = date_window_ranges(
            date_bounds["min_date"],
            date_bounds["max_date"],
            target=target_preds,
        )
        windows_count = max(1, len(windows))

    id_splits = None
    effective_hash_buckets = hash_buckets

    if strategy == "date_id_range":
        id_splits = max(1, min(
            MAX_INITIAL_PREDICATES // windows_count,
            max(1, math.ceil(target_preds / windows_count)),
        ))
    elif strategy == "date_hash":
        id_splits = None
        per_window = max(1, MAX_INITIAL_PREDICATES // windows_count)
        effective_hash_buckets = max(1, min(hash_buckets, per_window))
    else:
        id_splits = None

    return {
        "total_gb": total_gb,
        "jdbc_partitions": None,
        "num_workers": num_workers,
        "worker_type": worker_type,
        "id_splits": id_splits,
        "target_predicates": target_preds,
        "effective_hash_buckets": effective_hash_buckets,
        "windows_count": windows_count,
    }


def generate_predicates(
    strategy: str,
    date_col: str,
    date_bounds: dict,
    total_gb: float,
    id_col: str | None = None,
    id_min: int | None = None,
    id_max: int | None = None,
    id_splits: int = 4,
    hash_col: str | None = None,
    hash_buckets: int = DEFAULT_HASH_BUCKETS,
) -> list[str]:
    target_preds, _ = target_parallelism(total_gb)
    windows = date_window_ranges(
        date_bounds["min_date"],
        date_bounds["max_date"],
        target=target_preds,
    )
    predicates: list[str] = []

    if strategy == "date_range":
        for s, e in windows:
            predicates.append(f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}'")

    elif strategy == "date_id_range":
        if id_min is None or id_max is None or not id_col:
            return generate_predicates(
                "date_range", date_col, date_bounds, total_gb,
            )
        effective_id_splits = max(1, min(id_splits, MAX_INITIAL_PREDICATES // max(1, len(windows))))
        step = max(1, math.ceil((id_max - id_min + 1) / effective_id_splits))
        id_ranges = [
            (id_min + i * step, min(id_min + (i + 1) * step - 1, id_max))
            for i in range(effective_id_splits)
        ]
        for s, e in windows:
            for lo, hi in id_ranges:
                predicates.append(
                    f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}' "
                    f"AND {id_col} BETWEEN {lo} AND {hi}"
                )

    elif strategy == "date_hash":
        hcol = hash_col or date_col
        effective_buckets = max(1, min(hash_buckets, MAX_INITIAL_PREDICATES // max(1, len(windows))))
        for s, e in windows:
            for b in range(effective_buckets):
                predicates.append(
                    f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}' "
                    f"AND MOD(ORA_HASH({hcol}, {effective_buckets - 1}), {effective_buckets}) = {b}"
                )

    if len(predicates) > MAX_INITIAL_PREDICATES:
        predicates = predicates[:MAX_INITIAL_PREDICATES]
    return predicates


def maybe_staged_backfill(
    total_gb: float,
    date_bounds: dict | None,
    worker_type: str,
    num_workers: int,
) -> dict | None:
    if total_gb < STAGED_BACKFILL_GB or not date_bounds:
        return None

    min_d = date_bounds["min_date"]
    max_d = date_bounds["max_date"]
    num_chunks = max(2, math.ceil(total_gb / STAGED_BACKFILL_GB))
    total_years = max_d.year - min_d.year + 1
    years_per_chunk = max(1, math.ceil(total_years / num_chunks))
    est_gb_per_chunk = round(total_gb / num_chunks, 2)

    chunks: list[dict] = []
    y = min_d.year
    while y <= max_d.year:
        chunk_end_year = min(y + years_per_chunk - 1, max_d.year)
        d_start = min_d if y == min_d.year else _date(y, 1, 1)
        d_end = max_d if chunk_end_year >= max_d.year else _date(chunk_end_year, 12, 31)
        chunks.append({
            "date_start": str(d_start),
            "date_end":   str(d_end),
            "est_gb":     est_gb_per_chunk,
        })
        y = chunk_end_year + 1

    return {
        "recommended": True,
        "reason": (
            f"{round(total_gb, 1)} GB exceeds single-job cost target "
            f"({STAGED_BACKFILL_GB} GB). Run {len(chunks)} staged backfill jobs."
        ),
        "chunks": chunks,
        "per_chunk": {
            "worker_type":     worker_type,
            "num_workers":     num_workers,
            "max_predicates":  MAX_INITIAL_PREDICATES,
        },
    }


def enrich_recommendations(rec: dict, sizing: dict, date_bounds: dict | None) -> dict:
    """Add cost transparency fields to a flat analyze recommendation dict."""
    wt = rec.get("worker_type", "G.1X")
    nw = clamp_glue_workers(wt, rec.get("num_workers", 1))
    rec["num_workers"] = nw
    total_gb = sizing.get("total_gb", rec.get("total_gb", 0))

    rec["estimated_dpu_capacity"] = estimated_dpu_capacity(wt, nw)
    rec["cost_note"] = COST_NOTE
    if rec.get("jdbc_predicates_count") is None and rec.get("jdbc_predicates"):
        rec["jdbc_predicates_count"] = len(rec["jdbc_predicates"])

    staged = maybe_staged_backfill(total_gb, date_bounds, wt, nw)
    if staged:
        rec["staged_backfill"] = staged
    return rec
