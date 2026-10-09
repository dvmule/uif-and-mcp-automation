"""Phase-aware pipeline config resolution (initial vs incremental).

Background: every pipeline used to carry one `extract` + one `glue` block
that was applied to every run. That broke during the SPEA9I incident where
"initial-load" tuning (16-388 JDBC predicates, 12-20 G.4X workers) was being
replayed on a 30 min incremental schedule, fanning out 120+ concurrent
Oracle sessions per cycle.

The new schema splits both blocks into `initial` and `incremental` profiles.
Phase is auto-resolved from the watermark sentinel at job startup — no new
field is stored. Re-backfill = operator resets watermark_value to the
sentinel; next dispatch picks the initial profile again.

This module is the single source of truth for the resolution rule and the
lean defaults. It is imported by:
  - lambda/scheduler_dispatcher.py     (sizes start_job_run)
  - lambda/pipeline_controller.py      (validators, analyze emitter)

Glue scripts ship standalone (no shared package), so they duplicate the
SENTINEL_WATERMARKS / LEAN_* constants and the resolve_phase function
inline. Keep both copies in sync.
"""

from __future__ import annotations
from typing import Any


SENTINEL_WATERMARKS = {"", None, "1970-01-01T00:00:00"}

LEAN_INCREMENTAL_EXTRACT: dict[str, Any] = {
    "jdbc_partition_col": None,
    "jdbc_lower_bound":   None,
    "jdbc_upper_bound":   None,
    "jdbc_partitions":    None,
    "jdbc_predicates":    None,
}

LEAN_INCREMENTAL_GLUE: dict[str, Any] = {
    "worker_type":     "G.1X",
    "num_workers":     2,
    "timeout_minutes": 60,
}

SOURCE_CONTRACT_KEYS = frozenset({
    "table", "mode", "watermark_col", "watermark_fallback_col", "watermark_value", "columns", "custom_sql",
})


def resolve_phase(cfg: dict) -> str:
    """Return "initial" or "incremental" for a given pipeline config.

    Rules:
      * mode=="full"                                          -> always "initial"
      * watermark_value in SENTINEL_WATERMARKS (null/""/1970) -> "initial"
      * anything else                                          -> "incremental"
    """
    ext = cfg.get("extract") or {}
    if ext.get("mode") == "full":
        return "initial"
    if ext.get("watermark_value") in SENTINEL_WATERMARKS:
        return "initial"
    return "incremental"


def resolve_extract(cfg: dict, phase: str) -> dict:
    """Return the effective `extract` block for the given phase.

    Backward-compat: when `extract.initial` is missing (legacy flat shape),
    treat the current top-level JDBC fields as the initial profile and
    apply lean defaults for the incremental profile.

    Precedence (low -> high):
      1. LEAN_INCREMENTAL_EXTRACT (only when phase=="incremental")
      2. extract source-contract keys (table, mode, watermark_*)
      3. legacy top-level JDBC keys (only when phase=="initial" and no phase block)
      4. extract[phase] block, if present
    """
    ext = cfg.get("extract") or {}
    out: dict[str, Any] = {}

    if phase == "incremental":
        out.update(LEAN_INCREMENTAL_EXTRACT)

    for k in SOURCE_CONTRACT_KEYS:
        if k in ext:
            out[k] = ext[k]

    phase_block = ext.get(phase)
    if phase_block is None:
        if phase == "initial":
            for k, v in ext.items():
                if k in SOURCE_CONTRACT_KEYS or k in ("initial", "incremental"):
                    continue
                out[k] = v
    else:
        out.update(phase_block)

    return out


def resolve_glue(cfg: dict, phase: str) -> dict:
    """Return the effective `glue` block for the given phase.

    Backward-compat: legacy flat `glue` is treated as the initial profile;
    incremental falls back to LEAN_INCREMENTAL_GLUE.
    """
    g = cfg.get("glue") or {}
    phase_block = g.get(phase)
    if phase_block is not None:
        merged = dict(LEAN_INCREMENTAL_GLUE) if phase == "incremental" else {}
        merged.update(phase_block)
        return merged

    if phase == "initial":
        flat = {k: v for k, v in g.items() if k not in ("initial", "incremental")}
        return flat or dict(LEAN_INCREMENTAL_GLUE)
    return dict(LEAN_INCREMENTAL_GLUE)


def has_new_shape(cfg: dict) -> bool:
    """True when the config already uses the two-profile shape.

    Used by the migration script for idempotency and by validators to skip
    rules that only apply post-migration.
    """
    ext = cfg.get("extract") or {}
    g   = cfg.get("glue")    or {}
    return bool(ext.get("initial") or ext.get("incremental")
                or g.get("initial") or g.get("incremental"))
