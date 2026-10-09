"""BigQuery Silver table PARTITION BY clause from pipeline silver config.

Shared by pipeline_controller (validation) and GCP Silver Glue jobs (DDL).
"""

from __future__ import annotations

MAX_INTEGER_RANGE_BUCKETS = 10_000
_ALLOWED_TYPES = frozenset({"date_day", "date_month", "date_year", "integer_range"})
# MONTH/YEAR granularity needs the type-matched *_TRUNC function (BigQuery's
# PARTITION BY grammar allows no casts), so those specs must declare the
# column's BQ type. date_day stays declaration-free: DATE(col) is legal over
# all three types.
_ALLOWED_FIELD_TYPES = frozenset({"DATE", "TIMESTAMP", "DATETIME"})
_TRUNC_GRANULARITY = {"date_month": "MONTH", "date_year": "YEAR"}
_TRUNC_FN = {
    "DATE": "DATE_TRUNC",
    "TIMESTAMP": "TIMESTAMP_TRUNC",
    "DATETIME": "DATETIME_TRUNC",
}
_FIELD_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _bq_ident(name: str) -> str:
    return f"`{name}`"


def _oracle_col_to_bq(name: str) -> str:
    return name.replace("#", "_")


def _field_listed_for_int64(field: str, int64_cols: list) -> bool:
    f = (field or "").strip()
    for col in int64_cols or []:
        if not isinstance(col, str):
            continue
        if f == col.strip() or f == _oracle_col_to_bq(col.strip()):
            return True
    return False


def _resolve_partition_spec(silver: dict) -> tuple[dict | None, str | None]:
    """Return (spec, error). spec None + no error => unpartitioned."""
    if not isinstance(silver, dict):
        return None, "silver must be a map"

    if "bq_partition" in silver and silver["bq_partition"] is None:
        return None, None

    part = silver.get("bq_partition")
    if part is None:
        legacy = silver.get("bq_partition_field", "_silver_updated_at")
        if legacy is None or (isinstance(legacy, str) and not legacy.strip()):
            return None, None
        if not isinstance(legacy, str):
            return None, "silver.bq_partition_field must be a string or null"
        field = legacy.strip()
        if not _FIELD_RE.match(field):
            return None, f"silver.bq_partition_field `{field}` is not a valid column name"
        return {"type": "date_day", "field": field}, None

    if not isinstance(part, dict):
        return None, "silver.bq_partition must be a map or null"

    ptype = part.get("type")
    field = part.get("field")
    if not ptype or not isinstance(ptype, str):
        return None, "silver.bq_partition.type is required"
    ptype = ptype.strip().lower()
    if ptype not in _ALLOWED_TYPES:
        return None, (
            f"silver.bq_partition.type must be one of {sorted(_ALLOWED_TYPES)} "
            f"(got {ptype!r})"
        )
    if not field or not isinstance(field, str) or not field.strip():
        return None, "silver.bq_partition.field is required"
    field = field.strip()
    if not _FIELD_RE.match(field):
        return None, f"silver.bq_partition.field `{field}` is not a valid column name"

    legacy = silver.get("bq_partition_field")
    if legacy is not None and isinstance(legacy, str) and legacy.strip():
        if legacy.strip() != field:
            return None, (
                "silver.bq_partition.field conflicts with silver.bq_partition_field "
                f"({legacy.strip()!r} vs {field!r})"
            )

    if ptype == "date_day":
        return {"type": "date_day", "field": field}, None

    if ptype in _TRUNC_GRANULARITY:
        if "range" in part:
            return None, f"silver.bq_partition.range is not allowed for {ptype}"
        ftype = part.get("field_type")
        if ftype is None or (isinstance(ftype, str) and not ftype.strip()):
            return None, (
                f"silver.bq_partition.field_type is required for {ptype} "
                f"(one of {sorted(_ALLOWED_FIELD_TYPES)})"
            )
        if not isinstance(ftype, str):
            return None, "silver.bq_partition.field_type must be a string"
        ftype = ftype.strip().upper()
        if ftype not in _ALLOWED_FIELD_TYPES:
            return None, (
                f"silver.bq_partition.field_type must be one of "
                f"{sorted(_ALLOWED_FIELD_TYPES)} (got {ftype!r})"
            )
        return {"type": ptype, "field": field, "field_type": ftype}, None

    rng = part.get("range")
    if not isinstance(rng, dict):
        return None, "silver.bq_partition.range is required for integer_range"
    for key in ("start", "end", "interval"):
        if key not in rng:
            return None, f"silver.bq_partition.range.{key} is required"
    try:
        start = int(rng["start"])
        end = int(rng["end"])
        interval = int(rng["interval"])
    except (TypeError, ValueError):
        return None, "silver.bq_partition.range start/end/interval must be integers"
    if interval <= 0:
        return None, "silver.bq_partition.range.interval must be positive"
    if start >= end:
        return None, "silver.bq_partition.range.start must be less than end"
    buckets = (end - start + interval - 1) // interval
    if buckets > MAX_INTEGER_RANGE_BUCKETS:
        return None, (
            f"silver.bq_partition.range implies {buckets} buckets; "
            f"maximum is {MAX_INTEGER_RANGE_BUCKETS}"
        )

    int64_cols = silver.get("int64_cols") or []
    if not _field_listed_for_int64(field, int64_cols):
        return None, (
            "silver.bq_partition.field must appear in silver.int64_cols for "
            "integer_range (partition column must be INT64 at CTAS)"
        )

    return {
        "type": "integer_range",
        "field": field,
        "range": {"start": start, "end": end, "interval": interval},
    }, None


def validate_silver_bq_partition(silver: dict) -> str | None:
    """Return an error message for API validation, or None if valid."""
    _, err = _resolve_partition_spec(silver)
    return err


def build_partition_clause(silver: dict) -> tuple[str, str | None]:
    """Return (clause, error). clause is '' or 'PARTITION BY ...'."""
    spec, err = _resolve_partition_spec(silver)
    if err:
        return "", err
    if spec is None:
        return "", None

    field = spec["field"]
    if spec["type"] == "date_day":
        return f"PARTITION BY DATE({_bq_ident(field)})", None

    if spec["type"] in _TRUNC_GRANULARITY:
        fn = _TRUNC_FN[spec["field_type"]]
        unit = _TRUNC_GRANULARITY[spec["type"]]
        return f"PARTITION BY {fn}({_bq_ident(field)}, {unit})", None

    rng = spec["range"]
    start, end, interval = rng["start"], rng["end"], rng["interval"]
    return (
        f"PARTITION BY RANGE_BUCKET({_bq_ident(field)}, "
        f"GENERATE_ARRAY({start}, {end}, {interval}))",
        None,
    )
