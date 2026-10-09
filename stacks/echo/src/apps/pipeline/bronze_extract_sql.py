"""The Oracle query a Bronze run reads, and the watermark predicate inside it.

A module of its own, with no Spark, boto3 or Glue imports, for one reason:
`bronze-ingest.py` does heavy work at import time (getResolvedOptions, boto3
clients, a DynamoDB config read), so a test cannot import it. The SQL builder
therefore used to be **copied** into qe/pipeline/test_bronze_ingest.py, and the
tests exercised the copy. A change to the real builder could ship with a green
suite, because nothing compared the two.

One implementation now, imported by bronze-ingest.py at runtime (shipped to
Glue through --extra-py-files, like silver_routing.py) and by
qe/pipeline/test_bronze_extract_sql.py.

It also holds the one place that knows the shape of the watermark comparison,
`watermark_predicate`. The table path and the `{{WATERMARK}}` token in
`extract.custom_sql` both call it, so the two cannot drift apart (UIFPE-665).
"""

from __future__ import annotations

# A watermark_value in this set means "no watermark yet": the run is the
# initial backfill and reads everything. Mirrored in
# src/libs/pipeline_profiles.py (a known duplicate; see bronze-ingest.py STEP 2a).
SENTINEL_WATERMARKS = {"", None, "1970-01-01T00:00:00"}

# Placed by the engineer inside extract.custom_sql where the watermark filter
# belongs. Matched exactly: case and whitespace variants are not recognised.
WATERMARK_TOKEN = "{{WATERMARK}}"

_EPOCH = "1970-01-01T00:00:00"


def optional_watermark_fallback(ext: dict) -> str | None:
    """Return fallback column name only when set in config (null/blank → skip COALESCE)."""
    fb = ext.get("watermark_fallback_col")
    if fb is None:
        return None
    if isinstance(fb, str):
        fb = fb.strip()
    return fb or None


def effective_watermark_sql(wm_col: str, wm_fallback: str | None = None) -> str:
    """Oracle SQL expression for incremental watermark comparison."""
    if wm_fallback:
        return f"COALESCE({wm_col}, {wm_fallback})"
    return wm_col


def _filters_on_watermark(ext: dict) -> bool:
    """True when this run must filter on the watermark: incremental mode with a
    real (non-sentinel) watermark value. The same test for both paths."""
    if ext["mode"] != "incremental":
        return False
    return ext.get("watermark_value", _EPOCH) not in SENTINEL_WATERMARKS


def watermark_predicate(ext: dict) -> str:
    """`<watermark expr> > TO_TIMESTAMP('<watermark_value>', ...)`.

    The only place the comparison's shape is written. Callers decide whether a
    predicate applies (`_filters_on_watermark`); this only renders it.
    """
    wm_val = ext.get("watermark_value", _EPOCH)
    wm_expr = effective_watermark_sql(
        ext["watermark_col"], optional_watermark_fallback(ext)
    )
    return f"{wm_expr} > TO_TIMESTAMP('{wm_val}', 'YYYY-MM-DD\"T\"HH24:MI:SS')"


def build_extract_sql(ext: dict) -> str:
    """Build the JDBC subquery for Bronze extract (pure — testable without Spark)."""
    custom = ext.get("custom_sql")
    if custom:
        if WATERMARK_TOKEN not in custom:
            # Without the token an incremental run would silently re-read
            # everything. The controller rejects this at registration, but
            # register_pipeline.py and direct DynamoDB writes bypass the
            # controller, so the job refuses too. The first (sentinel) run is
            # a plain full read either way; it is the next one that fails.
            if _filters_on_watermark(ext):
                raise ValueError(
                    f"extract.custom_sql has no {WATERMARK_TOKEN} token; "
                    "incremental runs require it (e.g. `... AND "
                    f"{WATERMARK_TOKEN}`)"
                )
            return f"({custom}) t"
        # Sentinel watermark = initial backfill: no real filter. The token
        # renders as `1=1`, never as `<expr> > TO_TIMESTAMP(<epoch>)`: adding
        # COALESCE(wm_col, fallback) > epoch can change Oracle's view plan and
        # trigger ORA-01427 on some EBS views even when SELECT * works. `1=1`
        # keeps `AND {{WATERMARK}}` grammatical while filtering nothing, which
        # is what the table path achieves by omitting its WHERE. mode: full
        # renders `1=1` too, so one config can flip between full and
        # incremental without editing its SQL. Every occurrence is replaced:
        # the token may appear in a subquery as well as the outer WHERE.
        rendered = watermark_predicate(ext) if _filters_on_watermark(ext) else "1=1"
        return f"({custom.replace(WATERMARK_TOKEN, rendered)}) t"

    table   = ext["table"]
    columns = ext.get("columns")
    col_list = ", ".join(columns) if columns else "*"
    base    = f"SELECT {col_list} FROM {table}"

    # Sentinel watermark = initial backfill: read full table (no WHERE), for
    # the ORA-01427 reason given above.
    if _filters_on_watermark(ext):
        return f"({base} WHERE {watermark_predicate(ext)}) t"

    return f"({base}) t"


def require_watermark_column(
    wm_col: str, wm_fallback: str | None, columns: list[str]
) -> None:
    """Raise unless the extracted columns include the watermark column(s).

    The new watermark is MAX(watermark) over what was read. If the column is
    not in the projection there is nothing to take the MAX of, and the run used
    to write None: no error, the stored watermark never advanced, and every run
    re-read the same delta forever. `custom_sql` controls its own projection,
    so this is easy to hit (UIFPE-665).

    The fallback column counts too. The SQL filters on
    COALESCE(wm_col, wm_fallback); without the fallback in the projection the
    MAX would be taken over wm_col alone and would disagree with the filter.
    """
    missing = [c for c in (wm_col, wm_fallback) if c and c not in columns]
    if missing:
        raise ValueError(
            f"watermark column(s) {missing} not in the extracted columns "
            f"{sorted(columns)}. Add them to extract.custom_sql's SELECT list "
            "(or extract.columns); without them the watermark cannot advance."
        )
