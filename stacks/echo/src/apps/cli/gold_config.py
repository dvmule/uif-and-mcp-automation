# gold_config.py
# Gold pipeline config validation + auto-initialization — the control-plane
# rules formerly enforced by the platform's /gold-pipelines API, now owned by
# this repo (ADR-0004: independent control plane, config-as-code first).
#
# Pure functions, no boto3 — unit-testable; writers (sync_pipelines.py,
# register_gold_pipeline.py) call these before any DynamoDB put.

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# gold_frequencies lives in src/libs. Standalone CLIs get no conftest, so the
# path is set explicitly (Echo's convention); the Lambda zip is flat. UIFPE-149
# made it the single definition of the cadence vocabulary — do NOT re-inline.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))
import gold_resources  # noqa: E402
from gold_frequencies import (  # noqa: E402
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_FREQUENCY,
    DEFAULT_RECOMPUTE_EVERY_N_RUNS,
    DEFAULT_WEEKLY_DAYS,
    LEGACY_ALIASES,
    TRANSFORM_FREQUENCIES,
    WEEKDAYS,
    default_start_time,
    is_transform_frequency,
    is_weekly,
    normalize,
    parse_time_of_day,
    parse_weekly_days,
)


_REQUIRED_TOP_FIELDS = ("gold_id", "domain", "schema", "mapping", "sources", "target")
_REQUIRED_TARGET_FIELDS = ("database", "table", "s3_path", "primary_key_cols")
_REQUIRED_SCHEMA_FIELDS = ("master_s3_path", "version")
_REQUIRED_MAPPING_FIELDS = ("manifest_s3_path", "sql_s3_path")
_MODES = ("silent", "emit")


def validate(body: dict) -> str | None:
    """Return an error string if the gold config is invalid, else None.
    Superset of the legacy platform API validation, plus ADR-0003 fields."""
    for f in _REQUIRED_TOP_FIELDS:
        if f not in body:
            return f"missing required top-level field: {f}"

    target = body["target"]
    for f in _REQUIRED_TARGET_FIELDS:
        if f not in target:
            return f"missing required target.{f}"
    pks = target.get("primary_key_cols")
    if not isinstance(pks, list) or not pks:
        return "target.primary_key_cols must be a non-empty list"
    if not target["s3_path"].startswith("s3://"):
        return "target.s3_path must be an s3:// URI"

    schema = body["schema"]
    for f in _REQUIRED_SCHEMA_FIELDS:
        if f not in schema:
            return f"missing required schema.{f}"
    if not schema["master_s3_path"].startswith("s3://"):
        return "schema.master_s3_path must be an s3:// URI"

    mapping = body["mapping"]
    for f in _REQUIRED_MAPPING_FIELDS:
        if f not in mapping:
            return f"missing required mapping.{f}"
    for f in _REQUIRED_MAPPING_FIELDS:
        if not mapping[f].startswith("s3://"):
            return f"mapping.{f} must be an s3:// URI"

    sources = body["sources"]
    if not isinstance(sources, list) or not sources:
        return "sources must be a non-empty list"
    sot_count = 0
    for i, src in enumerate(sources):
        if "silver_table" not in src:
            return f"sources[{i}].silver_table is required"
        if "alias" not in src:
            return f"sources[{i}].alias is required"
        if src.get("is_source_of_truth"):
            sot_count += 1
    if sot_count != 1:
        return f"exactly one source must set is_source_of_truth (found {sot_count})"
    aliases = [s["alias"] for s in sources]
    if len(set(aliases)) != len(aliases):
        return f"source aliases must be unique (got {aliases})"

    # ADR-0020 decision 1: one bookmark, an ISO-8601 string the runtime
    # writes. The retired per-source map is not validated - the transform
    # REMOVEs it on the next run, and a row still carrying it must sync.
    bm = body.get("watermark", {}).get("bookmark")
    if bm is not None and not isinstance(bm, str):
        return "watermark.bookmark must be an ISO-8601 string"

    # ADR-0003 D7/D12: mode flags are constrained enums.
    if body.get("backfill_mode", "silent") not in _MODES:
        return f"backfill_mode must be one of {_MODES}"

    # sql_vars: config-supplied {token} values the transform splices into the
    # mapping SQL (per-env URL bases). Values land inside SQL string literals.
    sql_vars = body.get("sql_vars")
    if sql_vars is not None:
        if not isinstance(sql_vars, dict):
            return "sql_vars must be a mapping of token name -> string"
        for k, v in sql_vars.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(k)) or k == "env":
                return f"sql_vars key {k!r} is not a valid token name"
            if not isinstance(v, str) \
                    or any(c in v for c in ("'", "\\", "\n", "\r", "{", "}")):
                return (f"sql_vars[{k!r}] must be a plain string with no "
                        "quotes, backslashes, braces or newlines")
    if body.get("channels", {}).get("bootstrap_mode", "silent") not in _MODES:
        return f"channels.bootstrap_mode must be one of {_MODES}"

    # UIFPE-149: eleven cadences, defined once in gold_frequencies.py. Legacy
    # aliases are ACCEPTED here on purpose — an environment whose overlay has
    # not been migrated yet must still validate and sync, or the rename becomes
    # a hard cutover across every env at once. The dispatcher normalizes them.
    schedule = body.get("schedule") or {}
    freq = schedule.get("frequency") or DEFAULT_FREQUENCY
    if not is_transform_frequency(freq):
        return (f"schedule.frequency must be one of {TRANSFORM_FREQUENCIES} "
                f"(or a legacy alias {tuple(LEGACY_ALIASES)}), got {freq!r}")

    # The failure cooldown suppresses repeat dispatch after a failed run. 0
    # disables it, which is a legitimate choice for a daily pipeline; a
    # negative or non-integer value is a typo that would otherwise sync
    # silently and only surface as a cadence that never holds back.
    cooldown = schedule.get("failure_cooldown_minutes")
    if cooldown is not None:
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) \
                or cooldown < 0:
            return ("schedule.failure_cooldown_minutes must be a non-negative "
                    "integer (0 disables the cooldown)")

    # ADR-0020 decision 5: the recompute leg, inside the entity's ONE schedule.
    # Every failure mode here is SILENT at runtime — the dispatcher falls back
    # to defaults rather than crashing a whole sweep over one config — so this
    # is the only place a typo gets caught, and it has to be caught while a
    # human is watching the sync.
    recompute = schedule.get("recompute")
    if recompute is not None:
        if not isinstance(recompute, dict):
            return "schedule.recompute must be a map"
        allowed = {"undetectable_columns", "every_n_runs"}
        if "columns" in recompute:
            return ("schedule.recompute.columns is not the name — use "
                    "schedule.recompute.undetectable_columns. It is NOT the "
                    "set of columns to load (the run loads them all), only "
                    "those whose staleness nothing can detect")
        # A cadence of its own is exactly what decision 4 retires: the leg
        # shares the entity's single schedule and is placed on it by ratio.
        legacy = sorted({"frequency", "start_time", "mode"} & set(recompute))
        if legacy:
            return (f"schedule.recompute does not take {legacy} — the leg runs "
                    "inside the entity's one schedule, placed by "
                    "every_n_runs, and a scheduled run always announces")
        unknown = sorted(set(recompute) - allowed)
        if unknown:
            return (f"schedule.recompute only accepts {sorted(allowed)}; "
                    f"unknown: {unknown}")
        cols = recompute.get("undetectable_columns")
        if not isinstance(cols, list) or not cols \
                or not all(isinstance(c, str) and c.strip() for c in cols):
            return ("schedule.recompute.undetectable_columns must be a "
                    "non-empty list of column names — a leg with no columns "
                    "would run forever and repair nothing")
        every = recompute.get("every_n_runs", DEFAULT_RECOMPUTE_EVERY_N_RUNS)
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            return ("schedule.recompute.every_n_runs must be an integer >= 1 "
                    "(1 means every scheduled run)")

    # start_time anchors this pipeline's slot grid (UIFPE-149). REJECTED here
    # rather than silently defaulted, because sync time is the one moment a
    # human is watching: the dispatcher falls back to the cadence default so a
    # typo cannot stop a pipeline mid-flight, which means a bad value would
    # otherwise run at the wrong time forever with no error anywhere.
    start = schedule.get("start_time")
    if start is not None and parse_time_of_day(start) is None:
        return ("schedule.start_time must be a UTC time of day as \"HH:MM\" "
                f"(00:00-23:59), got {start!r}")

    # gold-weekly (UIFPE-532): the day set beside the time. Only the weekly
    # rung reads it; on any other rung it is a config that says two things.
    days = schedule.get("weekly_days")
    if days is not None:
        if not is_weekly(freq):
            return ("schedule.weekly_days is only valid when "
                    "schedule.frequency is gold-weekly")
        if parse_weekly_days(days) is None:
            return (f"schedule.weekly_days must be a non-empty list of "
                    f"{WEEKDAYS} (UTC weekdays), got {days!r}")

    # F1: snapshot pre-gate opt-out is a strict bool — a truthy string like
    # "false" silently enabling the gate is exactly the YAML footgun to catch.
    if "snapshot_gate" in body and not isinstance(body["snapshot_gate"], bool):
        return "snapshot_gate must be a boolean"

    # F2: capacity keys are validated wherever they appear, because a typo'd
    # value would otherwise sync silently and only fail (or mis-size a
    # 193.7M-row CTAS) at dispatch time.
    err = _validate_capacity_block(body.get("glue", {}), "glue")
    if err:
        return err
    fl = (body.get("glue") or {}).get("full_load")
    if fl is not None:
        if not isinstance(fl, dict):
            return "glue.full_load must be a map"
        allowed = {"worker_type", "num_workers", "timeout_minutes"}
        unknown = set(fl) - allowed
        if unknown:
            return (f"glue.full_load only carries capacity keys {sorted(allowed)} "
                    f"(shared runtime knobs stay at the top of glue); "
                    f"unknown: {sorted(unknown)}")
        err = _validate_capacity_block(fl, "glue.full_load")
        if err:
            return err

    return None


def _validate_capacity_block(block: dict, where: str) -> str | None:
    if not isinstance(block, dict):
        return f"{where} must be a map"
    if "worker_type" in block and not isinstance(block["worker_type"], str):
        return f"{where}.worker_type must be a string"
    for key in ("num_workers", "timeout_minutes"):
        if key in block:
            v = block[key]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                return f"{where}.{key} must be a positive integer"
    return None


def auto_init(body: dict, now: str = None) -> dict:
    """Fill canonical defaults in-place (mirrors the legacy API's
    auto-initialization, plus the ADR-0003 registries) and return the config.
    Idempotent — safe to run on an already-initialized config."""
    now = now or datetime.now(timezone.utc).isoformat()

    body.setdefault("enabled", True)
    body["version"] = "latest"

    # Runtime-owned (ADR-0020): the first run writes the bookmark. The map
    # must exist for the transform's document-path update.
    body.setdefault("watermark", {})

    body.setdefault("schedule", {})
    body["schedule"].setdefault("frequency", DEFAULT_FREQUENCY)
    # Explicit rather than left to the dispatcher's fallback, so a synced config
    # always states its own schedule — the same reason the F1/F2 capacity keys
    # are filled in below. A config that says what it does is one an operator can
    # read off the table instead of reconstructing from code defaults.
    #
    # The start_time default is per-cadence and preserves the pre-UIFPE-149
    # clocks (gold-24h -> 06:30, gold-1h -> :45), so filling it in cannot move a
    # running pipeline.
    body["schedule"].setdefault(
        "start_time",
        default_start_time(normalize(body["schedule"]["frequency"]))
        or default_start_time(DEFAULT_FREQUENCY))
    # Weekly states its day set for the same reason. Console-owned after that
    # (sync_pipelines.RUNTIME_PATHS), like start_time.
    if is_weekly(body["schedule"]["frequency"]):
        body["schedule"].setdefault("weekly_days", list(DEFAULT_WEEKLY_DAYS))
    body["schedule"].setdefault("failure_cooldown_minutes",
                                DEFAULT_COOLDOWN_MINUTES)
    # Same reason as start_time: a synced config states its own ratio rather
    # than leaving an operator to reconstruct it from a code default.
    if isinstance(body["schedule"].get("recompute"), dict):
        body["schedule"]["recompute"].setdefault(
            "every_n_runs", DEFAULT_RECOMPUTE_EVERY_N_RUNS)

    body.setdefault("reconcile", {
        "enabled": False, "dry_run": True, "max_deletes_per_run": 5000})

    # F1/F2 defaults. The dispatcher has its own fallbacks, but filling the
    # keys here means a synced config is always explicit about what runs get.
    # full_load defaults are the HEAVY constants, never copies of the lean
    # block: an undersized initial CTAS is an outage loop (timeout + retry at
    # the same size), whereas an oversized CDC run is only money.
    body.setdefault("snapshot_gate", True)
    body.setdefault("glue", {})
    body["glue"].setdefault("worker_type", "G.2X")
    body["glue"].setdefault("num_workers", 16)
    body["glue"].setdefault("timeout_minutes", 180)
    body["glue"].setdefault("full_load", {})
    body["glue"]["full_load"].setdefault("worker_type", "G.2X")
    body["glue"]["full_load"].setdefault("num_workers", 16)
    body["glue"]["full_load"].setdefault("timeout_minutes", 180)

    # ADR-0003 registries + outbound contract (D4/D7/D8/D9/D12). Registries
    # start empty — the transform's maintenance diff populates them.
    body.setdefault("channels", {})
    body["channels"].setdefault("registered", [])
    body["channels"].setdefault("bootstrap_mode", "silent")
    body.setdefault("materialized_properties", [])
    body.setdefault("backfill_mode", "silent")
    body.setdefault("publish_cursors", {})
    body.setdefault("hash_spec_version", 1)
    body.setdefault("retention", {"changes_days": 365, "snapshot_days": 14})

    body.setdefault("metadata", {})
    body["metadata"].setdefault("registered_at", now)
    body["metadata"].setdefault("owner", "unknown")
    body["metadata"].setdefault("last_run_id", None)
    body["metadata"].setdefault("last_run_at", None)

    return body


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge for config-as-code overlays: overlay wins;
    nested dicts merge; lists and scalars replace wholesale."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_gold_config_table(environment: str) -> str:
    """DynamoDB gold config table for an environment (gold_resources decides
    which naming convention the environment runs on)."""
    return gold_resources.gold_config_table(environment)
