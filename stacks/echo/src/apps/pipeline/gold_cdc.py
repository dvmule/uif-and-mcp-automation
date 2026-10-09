# gold_cdc.py
# Pure-logic library for the Gold incremental CDC flow (ADR-0003).
# No Spark/boto3 imports (stdlib only) — every function either builds Spark
# SQL text or resolves config, so the whole module unit-tests without a
# cluster. Stays ONE flat file: it ships via --extra-py-files and both jobs
# import it through the dual-import fallback.
#
# Shipped to Glue via --extra-py-files (same pattern as dq_kinds/) and
# imported by generic_gold_transform.py + generic_gold_reconcile.py.
#
# Hash spec v1 (ADR-0003 D4 — any change here bumps hash_spec_version):
#   sha2(to_json(named_struct(<props sorted by name>), map('ignoreNullFields','false')), 256)
#   - property order: sorted by property name (master.json reordering never shifts hashes)
#   - nulls serialized explicitly; timestamps ISO-8601 (Spark to_json default)
#   - array properties must be pre-sorted by the mapping SQL (D11 array_sort_keys)
#   - top-level keys are LOGICAL names; a nested column a channel sees in full is
#     serialized whole, so its PHYSICAL member names are hashed (moved once at the
#     snake_case rename, UIFPE-437, 2026-09-12; re-baseline with a silent refresh)

import re
from datetime import datetime, timedelta

CANONICAL = "canonical"
EPOCH_WATERMARK = "1970-01-01T00:00:00"
HASH_SPEC_VERSION = 1
# ADR-0020 decision 1: ONE bookmark per entity under `watermark` - the start
# of the last run that processed changes, never a value derived from source
# data. The per-source map it replaces is REMOVEd by the same guarded update.
BOOKMARK_ATTR = "bookmark"
LEGACY_WATERMARK_ATTR = "last_processed_silver_updated_at"


# ─────────────────────────────────────────────────────────────────
# Glue argument parsing
# ─────────────────────────────────────────────────────────────────
_TRUE_TOKENS  = ("true", "1", "yes", "y", "on")
_FALSE_TOKENS = ("false", "0", "no", "n", "off", "")


def parse_bool_arg(value, name: str = "flag", default: bool = False) -> bool:
    """Parse a Glue-style boolean job argument.

    Glue always passes job arguments as `--flag <value>` — there is no bare
    presence form — so an argparse `action="store_true"` flag is enabled by
    ANY value, including the literal string "false". Both jobs therefore
    declare these as plain string args and parse them here (this is the
    pattern generic_gold_transform.py already used for --rebuild; the
    reconcile job's --dry-run was the store_true outlier — review finding
    #11). An unrecognised value raises rather than silently picking a side:
    a mistyped `--dry-run ture` must not arm a delete run."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ValueError(
        f"job argument --{name} got {value!r}; expected one of "
        f"{sorted(_TRUE_TOKENS)} / {sorted(_FALSE_TOKENS)}"
    )


# ─────────────────────────────────────────────────────────────────
# SQL literal hygiene
# ─────────────────────────────────────────────────────────────────
# Values that end up inside single-quoted Spark SQL literals (RUN_ID, channel
# names, timestamps) are not parameterised — Spark SQL has no bind parameters
# in `spark.sql(text)`. They all originate inside our own control plane rather
# than from end users, so this is defence in depth, not the primary control:
# a quote or a semicolon reaching the interpolation site must abort the job
# instead of producing surprising SQL (review MINOR suggestion).
_SQL_LITERAL_PATTERNS = {
    # Glue job run ids ("jr_9f3c…"), plus the "unknown" local default.
    "run_id":     re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$"),
    # Channel names come from x-channels tags in master.json.
    "channel":    re.compile(r"^[A-Za-z0-9_-]{1,64}$"),
    # 'YYYY-MM-DD HH:MM:SS' or ISO-8601 with T / fraction / offset.
    "timestamp":  re.compile(
        r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,9})?"
        r"(Z|[+-]\d{2}:?\d{2})?$"),
    # Bare table/column identifiers.
    "identifier": re.compile(r"^[A-Za-z0-9_]{1,255}$"),
}


def validate_sql_literal(value, kind: str, label: str = None):
    """Return `value` unchanged if it is safe to interpolate into a
    single-quoted Spark SQL literal of the given `kind`; raise otherwise.

    Used inline at the interpolation sites so the check cannot drift away
    from the SQL it protects: `f"'{validate_sql_literal(RUN_ID, 'run_id')}'"`.
    """
    pattern = _SQL_LITERAL_PATTERNS.get(kind)
    if pattern is None:
        raise ValueError(f"unknown SQL literal kind {kind!r}")
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(
            f"refusing to interpolate {label or kind}={value!r} into Spark "
            f"SQL — does not match the allowed {kind} pattern "
            f"{pattern.pattern}"
        )
    return value


def validate_sql_literals(values, kind: str, label: str = None):
    """validate_sql_literal over an iterable; returns a list."""
    return [validate_sql_literal(v, kind, label) for v in values]


def resolve_env(value, env: str):
    """Substitute the {env} token in catalog identifiers so one Git artifact
    serves every environment. Glue databases are env-qualified
    (uif_echo_dev_silver_mdm); configs/manifests write uif_echo_{env}_silver_mdm."""
    if isinstance(value, str):
        return value.replace("{env}", env)
    return value


SQL_VAR_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)


def apply_sql_vars(sql_text: str, sql_vars: dict, required=()) -> str:
    """Substitute config-supplied {token} values into the mapping SQL
    (e.g. a per-environment URL base). Values land inside SQL string
    literals, so quotes/backslashes/newlines are refused."""
    sql_vars = sql_vars or {}
    missing = [k for k in required if k not in sql_vars]
    if missing:
        raise ValueError(
            f"mapping SQL requires sql_vars {missing} — add them to the "
            "pipeline config (per-env overlay)")
    for k, v in sql_vars.items():
        if not SQL_VAR_KEY_RE.fullmatch(str(k)) or k == "env":
            raise ValueError(f"sql_vars key {k!r} is not a valid token name")
        v = str(v)
        # braces excluded too: a value carrying another {token} (or {env})
        # would substitute or not depending on dict order — nondeterministic
        if any(c in v for c in ("'", "\\", "\n", "\r", "{", "}")):
            raise ValueError(f"sql_vars[{k!r}] contains characters that "
                             "would break a SQL literal or nest a token")
        sql_text = sql_text.replace("{" + k + "}", v)
    return sql_text


def qualify_table(dotted: str, catalog: str = "glue_catalog") -> str:
    """`catalog`.`db`.`table` — every identifier part individually backticked.

    Spark parses the string form of a multipart identifier with the SQL parser
    whether it reaches it via spark.table() or inside a SQL string, so any part
    that is not a bare identifier must be quoted. A hyphen is the case that
    bites: `db.my-table` parses as `db.my` MINUS `table` and surfaces as
    TABLE_OR_VIEW_NOT_FOUND, which reads like a missing table rather than a
    quoting bug. Silver table names come from config and are not ours to
    constrain, so quote rather than assume. The Gold side already did this
    inline; this is the same discipline applied to every catalog identifier.

    Parts are stripped of pre-existing backticks first, so passing an
    already-quoted name is idempotent; an embedded backtick is escaped by
    doubling, per Spark's quoting rules.
    """
    parts = [p for p in dotted.split(".") if p]
    if catalog:
        parts.insert(0, catalog)
    return ".".join(f"`{p.strip('`').replace('`', '``')}`" for p in parts)

# Framework metadata columns — never in master.json, excluded from the
# schema conformance check, always present on the Gold table (D4/D7).
METADATA_COLUMNS = ("_channel_hashes", "_gold_updated_at", "_gold_run_id")

_JSON_TO_SPARK_TYPE = {
    "string":  "STRING",
    "integer": "BIGINT",
    "number":  "DECIMAL(38,10)",
    "boolean": "BOOLEAN",
}


# ─────────────────────────────────────────────────────────────────
# Physical column names (Schema Standards N7/N8)
#
# master.json, the intake, the mapping SQL and every contract carry the
# LOGICAL camelCase name. The Gold/Iceberg column is its snake_case rendering,
# generated here and nowhere else — nothing hand-writes a physical name. The
# transform is lossless while names carry no interior acronym runs (N3).
# ─────────────────────────────────────────────────────────────────
_PHYSICAL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")


def physical_name(name: str) -> str:
    """camelCase logical name -> snake_case physical column (N8).
    Framework metadata columns and names already in snake_case pass through."""
    if name in METADATA_COLUMNS or name.startswith("_"):
        return name
    return _PHYSICAL_RE.sub(r"_\1", name).lower()


def physical_path(path: str) -> str:
    """Dotted nested path -> physical segments; the Iceberg 'element' marker
    is kept as-is ('parent.element.child')."""
    return ".".join(seg if seg == "element" else physical_name(seg)
                    for seg in path.split("."))


def _rename_tree(tree):
    """Canonical DDL tree with every struct field name made physical."""
    if isinstance(tree, str):
        return tree
    kind = tree[0]
    if kind == "struct":
        return ("struct", {physical_name(n): _rename_tree(t) for n, t in tree[1].items()})
    if kind == "array":
        return ("array", _rename_tree(tree[1]))
    if kind == "map":
        return ("map", _rename_tree(tree[1]), _rename_tree(tree[2]))
    raise ValueError(f"unrenamable tree: {tree!r}")


def physical_ddl(ddl: str) -> str:
    """A manifest column_types DDL string with struct members made physical:
    'ARRAY<STRUCT<contributorId: STRING>>' -> 'ARRAY<STRUCT<contributor_id: STRING>>'."""
    return _render_ddl(_rename_tree(_parse_spark_ddl(str(ddl))))


def rename_schema_json(schema_json: dict) -> dict:
    """StructType.jsonValue() with every field name (top-level and nested)
    made physical. The transform casts the mapping-SQL DataFrame to the
    StructType built from this, which renames columns and struct members
    positionally in one select."""
    def walk(t):
        if isinstance(t, dict):
            kind = t.get("type")
            if kind == "struct":
                return {**t, "fields": [{**f, "name": physical_name(f["name"]),
                                         "type": walk(f["type"])}
                                        for f in t.get("fields", [])]}
            if kind == "array":
                return {**t, "elementType": walk(t["elementType"])}
            if kind == "map":
                return {**t, "keyType": walk(t["keyType"]),
                        "valueType": walk(t["valueType"])}
        return t
    return walk(schema_json)


def physical_rename_ddls(table_full: str, master_schema: dict,
                         column_types: dict, live_schema_json: dict) -> list:
    """`ALTER TABLE … RENAME COLUMN` statements that move a live table from
    logical to physical names — top-level columns and struct members, arrays
    included. Metadata-only in Iceberg (no data rewrite, history kept), so a
    table written before this rule needs no --rebuild. A column already
    physical, or unknown to the master, is left alone."""
    stmts = []
    live = {f["name"]: f["type"] for f in (live_schema_json or {}).get("fields", [])}
    for prop in (master_schema or {}).get("properties", {}):
        phys = physical_name(prop)
        if prop in live and phys != prop and phys not in live:
            stmts.append(f"ALTER TABLE {table_full} RENAME COLUMN `{prop}` TO `{phys}`")
    for column, ddl in sorted((column_types or {}).items()):
        live_name = column if column in live else physical_name(column)
        if live_name not in live:
            continue
        declared = _parse_spark_ddl(str(ddl))
        # top-level renames run first, so nested paths address the parent by
        # its physical name
        stmts += _nested_rename_ddls(table_full, [physical_name(live_name)], declared,
                                     _schema_json_to_tree(live[live_name]))
    return stmts


CHANGES_FIXED_COLUMNS = ("op", "changed_channels", "run_id",
                         "_gold_updated_at", "change_date")


def changes_key_rename_ddls(changes_full: str, primary_key_cols: list,
                            live_columns: list) -> list:
    """A change log is created once (IF NOT EXISTS), so it never follows a
    later rename of the master's primary key. Pair its stray key columns with
    the missing physical key names by position and RENAME each (metadata-only).
    Refuses when the shapes do not line up — that needs a human."""
    expected = [physical_name(c) for c in primary_key_cols]
    live_keys = [c for c in live_columns if c not in CHANGES_FIXED_COLUMNS]
    missing = [e for e in expected if e not in live_keys]
    stray = [c for c in live_keys if c not in expected]
    if not stray and not missing:
        return []
    if len(stray) != len(missing):
        raise ValueError(
            f"{changes_full}: key columns {live_keys} do not line up with the "
            f"master primary key {expected}; rename by hand")
    return [f"ALTER TABLE {changes_full} RENAME COLUMN `{s}` TO `{m}`"
            for s, m in zip(stray, missing)]


def _nested_rename_ddls(table_full, path, declared, live) -> list:
    """Struct members named logically in the live table -> physical."""
    out = []
    d_struct = isinstance(declared, tuple) and declared[0] == "struct"
    l_struct = isinstance(live, tuple) and live[0] == "struct"
    if d_struct and l_struct:
        for name, d_type in declared[1].items():
            phys = physical_name(name)
            if name in live[1] and phys != name and phys not in live[1]:
                out.append(f"ALTER TABLE {table_full} RENAME COLUMN "
                           f"{'.'.join(path + [name])} TO {phys}")
                out += _nested_rename_ddls(table_full, path + [phys], d_type, live[1][name])
            elif phys in live[1]:
                out += _nested_rename_ddls(table_full, path + [phys], d_type, live[1][phys])
        return out
    d_array = isinstance(declared, tuple) and declared[0] == "array"
    l_array = isinstance(live, tuple) and live[0] == "array"
    if d_array and l_array:
        return _nested_rename_ddls(table_full, path + ["element"], declared[1], live[1])
    return out


# ─────────────────────────────────────────────────────────────────
# Channel discovery (D4 / D10)
# ─────────────────────────────────────────────────────────────────
def discover_channels(master_schema: dict) -> list:
    """Union of all x-channels tags across properties, plus the reserved
    'canonical' channel. Sorted for determinism. Raises if 'canonical' is
    tagged explicitly (it is implicit — D10)."""
    channels = set()
    for name, prop in master_schema.get("properties", {}).items():
        for ch in prop.get("x-channels", []):
            if ch == CANONICAL:
                raise ValueError(
                    f"property '{name}' tags reserved channel '{CANONICAL}' "
                    f"explicitly — it is implicit and must not appear in x-channels"
                )
            channels.add(ch)
    channels.add(CANONICAL)
    return sorted(channels)


def channel_properties(master_schema: dict, channel: str) -> list:
    """Sorted property names carried by a channel. 'canonical' = all."""
    props = master_schema.get("properties", {})
    if channel == CANONICAL:
        return sorted(props.keys())
    return sorted(
        name for name, prop in props.items()
        if channel in prop.get("x-channels", [])
    )


# ─────────────────────────────────────────────────────────────────
# Hash expression builders (D4) — nested-channel aware
# ─────────────────────────────────────────────────────────────────
def _nested_schema(prop_schema: dict):
    """Return (kind, fields) for a nested property — kind ∈ {'array','object'},
    fields = its inner properties dict — or (None, None) for scalars/plain
    value arrays."""
    t = prop_schema.get("type")
    types = t if isinstance(t, list) else [t]
    if "array" in types:
        inner = (prop_schema.get("items") or {}).get("properties")
        if inner:
            return "array", inner
    elif "object" in types:
        inner = prop_schema.get("properties")
        if inner:
            return "object", inner
    return None, None


def nested_fields_for_channel(prop_schema: dict, channel: str):
    """For a nested array/object property, the sorted field names visible to
    `channel`. Untagged fields inherit the parent's channels (always kept);
    tagged fields are kept only when tagged with this channel. canonical
    always sees everything. Returns (kind, all_fields, kept_fields) or
    (None, None, None)."""
    kind, inner = _nested_schema(prop_schema)
    if not inner:
        return None, None, None
    all_fields = sorted(inner.keys())
    if channel == CANONICAL:
        return kind, all_fields, all_fields
    kept = sorted(
        f for f, fs in inner.items()
        if "x-channels" not in fs or channel in fs.get("x-channels", [])
    )
    return kind, all_fields, kept


def validate_nested_channels(master_schema: dict) -> None:
    """Per-field x-channels inside arrays/objects must be a SUBSET of the
    parent property's channels — a field can't be visible on a channel its
    parent doesn't reach."""
    for name, prop in master_schema.get("properties", {}).items():
        kind, inner = _nested_schema(prop)
        if not inner:
            continue
        parent = set(prop.get("x-channels", []))
        for fname, fs in inner.items():
            if "x-channels" not in fs:
                continue
            extra = set(fs["x-channels"]) - parent
            if extra:
                raise ValueError(
                    f"nested field '{name}.{fname}' is tagged for channels "
                    f"{sorted(extra)} its parent doesn't carry {sorted(parent)}"
                )


def _channel_column_expr(prop_name: str, prop_schema: dict, channel: str,
                         qualifier: str = "") -> str:
    """Column expression for one property in a channel hash. Nested
    properties with per-field channel tags are PROJECTED to the channel's
    visible fields, so invisible-field changes never move this channel's
    hash."""
    # column refs are PHYSICAL (the DataFrame is renamed before hashing);
    # named_struct keys stay LOGICAL. A full nested column is passed whole,
    # so its physical member names do reach to_json (see header note)
    base = f"{qualifier}`{physical_name(prop_name)}`"
    kind, all_fields, kept = nested_fields_for_channel(prop_schema, channel)
    if all_fields is None or kept == all_fields:
        return base
    if not kept:  # no visible fields for this channel → constant
        return "NULL"
    if kind == "array":
        fields = ", ".join(f"'{f}', x.`{physical_name(f)}`" for f in kept)
        return f"transform({base}, x -> named_struct({fields}))"
    fields = ", ".join(f"'{f}', {base}.`{physical_name(f)}`" for f in kept)
    return f"named_struct({fields})"


def build_channel_hash_expr(master_schema: dict, channel: str,
                            qualifier: str = None,
                            qualifiers: dict = None) -> str:
    """Spark SQL expression producing the channel's SHA-256 hash for a row.
    qualifier optionally prefixes column refs (e.g. 's' → s.`col`);
    qualifiers overrides it per property ({col: alias}) — the refresh path
    hashes a row composed from two relations (fresh named columns, stored
    everything else)."""
    props = channel_properties(master_schema, channel)
    if not props:
        raise ValueError(f"channel '{channel}' carries no properties")
    default_q = f"{qualifier}." if qualifier else ""
    schema_props = master_schema.get("properties", {})

    def q_for(p):
        alias = (qualifiers or {}).get(p)
        return f"{alias}." if alias else default_q

    pairs = ", ".join(
        f"'{p}', {_channel_column_expr(p, schema_props.get(p, {}), channel, q_for(p))}"
        for p in props
    )
    return (
        f"sha2(to_json(named_struct({pairs}), "
        f"map('ignoreNullFields', 'false')), 256)"
    )


def build_hash_map_expr(master_schema: dict, channels: list = None,
                        qualifier: str = None, qualifiers: dict = None) -> str:
    """Spark SQL expression producing the full _channel_hashes map."""
    channels = channels or discover_channels(master_schema)
    entries = ", ".join(
        f"'{ch}', {build_channel_hash_expr(master_schema, ch, qualifier, qualifiers)}"
        for ch in sorted(channels)
    )
    return f"map({entries})"


def build_changed_channels_expr(channels: list, new_alias: str = "u",
                                old_alias: str = "g") -> str:
    """Spark SQL expression: array of channel names whose hash differs
    between the freshly rebuilt row (new_alias) and the current Gold row
    (old_alias). A missing Gold row (INSERT) marks every channel changed."""
    cases = ", ".join(
        f"CASE WHEN {old_alias}.`_channel_hashes` IS NULL "
        f"OR {old_alias}.`_channel_hashes`['{ch}'] IS DISTINCT FROM "
        f"{new_alias}.`_channel_hashes`['{ch}'] THEN '{ch}' END"
        for ch in sorted(channels)
    )
    return f"filter(array({cases}), x -> x IS NOT NULL)"


# ─────────────────────────────────────────────────────────────────
# Per-source watermarks (D2)
# ─────────────────────────────────────────────────────────────────
def resolve_bookmark(watermark_cfg):
    """The stored bookmark (ADR-0020 decision 1), or None when the row has
    none yet - a never-run entity, or the first run after the per-source
    map retired. The retired map is deliberately not consulted."""
    if not isinstance(watermark_cfg, dict):
        return None
    raw = watermark_cfg.get(BOOKMARK_ATTR)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"watermark.{BOOKMARK_ATTR} must be an ISO-8601 string, "
            f"got {type(raw).__name__}")
    _parse_watermark(raw, f"watermark.{BOOKMARK_ATTR}")
    return raw


def latest_successful_start(runs, gold_id: str):
    """Seed for a row with no bookmark (UIFPE-522 migration, owner decision
    2026-09-23): the start of the newest SUCCEEDED transform run for this
    entity, read from its metrics rows. Anything Silver wrote since that
    instant is > (seed - lag), and everything older was consumed by that
    run. Failed runs advanced nothing and skipped runs processed nothing,
    so neither counts. None when the entity has never succeeded."""
    starts = [r.get("started_at") for r in (runs or ())
              if r.get("gold_id") == gold_id
              and r.get("status") == "SUCCEEDED" and r.get("started_at")]
    return max(starts) if starts else None


def stored_silver_snapshots(watermark_cfg, gate=True) -> dict:
    """Per-alias metadata_location map persisted by the last successful run.
    {} for first runs / legacy scalar configs — no skip, normal scan.
    `gate` takes the pipeline's snapshot_gate knob: opting out (False) must
    disable this skip too, not just the dispatcher's — it is the operator's
    escape hatch when pointer equality is suspect in an environment."""
    if gate is False or not isinstance(watermark_cfg, dict):
        return {}
    snaps = watermark_cfg.get(SNAPSHOT_ATTR)
    return snaps if isinstance(snaps, dict) else {}


def source_snapshot_unchanged(stored: dict, captured: dict, alias: str) -> bool:
    """True when both the stored map and this run's capture carry the alias
    with the same metadata_location. Every Iceberg commit moves it, so
    equality proves no commits landed since the last successful run — the
    delta scan could only re-detect lag-window rows the hash gate discards
    (UIFPE-256). Missing or empty values fail open to a normal scan."""
    stored_loc = (stored or {}).get(alias)
    return bool(stored_loc) and stored_loc == (captured or {}).get(alias)


def _parse_watermark(value, label: str = "watermark") -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"{label} {value!r} is not an ISO-8601 timestamp"
        ) from e


def lagged_watermark(watermark, lag_minutes: int = 0) -> str:
    """Return the timestamp the delta scan should start FROM: the bookmark
    rewound by `lag_minutes` (review finding #5, kept by ADR-0020).

    Why a lag at all. Silver stamps a row when its write begins and commits
    it later, so a row stamped just before the previous run started can
    still land after that run read the table; with a strict `>` it would be
    excluded on every later run. Rewinding the lower bound by more than the
    longest Silver write turns that gap into a small deliberate overlap.

    Re-reading rows is free of side effects: Phase 2 rebuilds the affected
    entities and the per-channel hash gate (D3/D4) drops any row whose
    canonical hash is unchanged — no MERGE write, no change-log row, no
    `_gold_updated_at` bump. So the overlap costs scan work only.

    The `>` comparison is kept, now against the lagged bound. The bookmark
    itself is the run's own start (ADR-0020), so the lag never feeds back
    into it."""
    if not lag_minutes or int(lag_minutes) <= 0:
        return str(watermark)
    lagged = _parse_watermark(watermark) - timedelta(minutes=int(lag_minutes))
    epoch = _parse_watermark(EPOCH_WATERMARK)
    if lagged.tzinfo is not None:
        epoch = epoch.replace(tzinfo=lagged.tzinfo)
    if lagged < epoch:
        return EPOCH_WATERMARK
    return lagged.isoformat()


# Snapshot-gate state (F1): {alias: Iceberg metadata_location} captured by the
# transform BEFORE its first Silver read, persisted in the same guarded update
# as the watermarks. Lives under the `watermark` attribute deliberately —
# sync_pipelines preserves `watermark` wholesale (RUNTIME_PATHS), so the map
# survives config syncs with no sync-side changes.
SNAPSHOT_ATTR = "silver_snapshots"
# ADR-0020 decision 5: when the recompute leg last ran, under `watermark`
# so RUNTIME_PATHS preserves it across every config sync.
RECOMPUTE_ATTR = "last_recompute_at"


def build_bookmark_update_args(bookmark, run_id: str, run_ts: str,
                               snapshots: dict = None,
                               recompute_at: str = None) -> dict:
    """DynamoDB update_item kwargs for the end-of-run state write (D6 step
    3). The caller adds `Key=`. One guarded write carries:

    - `watermark.bookmark = <this run's start>` (ADR-0020 decision 1), or
      nothing when `bookmark` is None - a run that processed nothing
      advances nothing;
    - the F1 snapshot map and decision-5 recompute marker, riding the same
      update so a lost race skips them together (see the callers);
    - `metadata.last_run_id/last_run_at` (the run END - never the bookmark);
    - `metadata.last_transform_at`, the same END, which the reconcile never
      writes - the dispatcher's freshness metric reads it (UIFPE-543);
    - REMOVE of the retired per-source map, so the row is clean after the
      first run that lands here (UIFPE-522).

    The condition, only when a bookmark is written:

        attribute_not_exists(#wm.#bm) OR #wm.#bm <= :b

    Bookmarks are ISO-8601 strings from one formatter, so lexicographic and
    chronological order coincide and the stored value can only advance.
    Losing the condition means a run that started later has already
    finished; its bookmark covers this run's window, so skipping is correct
    and needs no retry. The change log and MERGE are already committed, so a
    skip only costs a re-detection the hash gate drops (at-least-once)."""
    names = {"#wm": "watermark", "#meta": "metadata",
             "#legacy": LEGACY_WATERMARK_ATTR}
    values = {":rid": run_id, ":ts": run_ts}
    sets = []
    if bookmark:
        names["#bm"] = BOOKMARK_ATTR
        values[":b"] = bookmark
        sets.append("#wm.#bm = :b")
    if snapshots:
        names["#snap"] = SNAPSHOT_ATTR
        values[":snap"] = snapshots
        sets.append("#wm.#snap = :snap")
    if recompute_at:
        names["#rc"] = RECOMPUTE_ATTR
        values[":rc"] = recompute_at
        sets.append("#wm.#rc = :rc")
    sets += ["#meta.last_run_id = :rid", "#meta.last_run_at = :ts",
             "#meta.last_transform_at = :ts"]
    args = {
        "UpdateExpression": "SET " + ", ".join(sets) + " REMOVE #wm.#legacy",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if bookmark:
        args["ConditionExpression"] = \
            "attribute_not_exists(#wm.#bm) OR #wm.#bm <= :b"
    return args


# ─────────────────────────────────────────────────────────────────
# Registry diffs (D7 / D12)
# ─────────────────────────────────────────────────────────────────
def diff_new_channels(master_schema: dict, registered: list) -> list:
    """Channels present in the schema but not yet bootstrapped."""
    return sorted(set(discover_channels(master_schema)) - set(registered or []))


def diff_new_properties(master_schema: dict, materialized: list) -> list:
    """Master properties not yet materialized as Gold columns."""
    props = set(master_schema.get("properties", {}).keys())
    return sorted(props - set(materialized or []))


def build_add_column_ddls(table_full: str, specs: list,
                          live_field_names: list) -> list:
    """One `ALTER TABLE … ADD COLUMN` per new business column, placed AFTER
    the last business column so the metadata columns stay last (UIFPE-268 —
    a plain ADD COLUMNS appends, which put `categories` behind them).

    specs: [(name, spark_ddl_type)] in order; live_field_names: current order.
    """
    names = list(live_field_names or [])
    meta_positions = [i for i, n in enumerate(names) if n in METADATA_COLUMNS]
    anchor, first = None, False
    if meta_positions:
        first_meta = min(meta_positions)
        if first_meta > 0:
            anchor = names[first_meta - 1]
        else:
            first = True
    stmts = []
    for name, ddl in specs:
        if anchor:
            pos = f" AFTER `{anchor}`"
        elif first:
            pos = " FIRST"
        else:
            pos = ""
        stmts.append(f"ALTER TABLE {table_full} ADD COLUMN `{name}` {ddl}{pos}")
        anchor, first = name, False
    return stmts


# ─────────────────────────────────────────────────────────────────
# JSON Schema → Spark/Iceberg column types (D12 auto-DDL)
# ─────────────────────────────────────────────────────────────────
def json_type_to_spark(prop_name: str, prop_schema: dict,
                       column_types: dict = None) -> str:
    """Map a master.json property to a Spark SQL column type for
    ALTER TABLE ADD COLUMNS. Complex types (array/object) must be declared
    in the manifest's column_types block; an explicit entry always wins.

    JSON Schema "number" is exact DECIMAL by default; approximate floats
    are declared with `"format": "float" | "double"` on the property."""
    if column_types and prop_name in column_types:
        return column_types[prop_name]
    t = prop_schema.get("type")
    if isinstance(t, list):  # nullable unions like ["string", "null"]
        non_null = [x for x in t if x != "null"]
        if len(non_null) != 1:
            raise ValueError(
                f"property '{prop_name}': ambiguous type union {t} — "
                f"declare it in manifest column_types"
            )
        t = non_null[0]
    if t in ("array", "object"):
        raise ValueError(
            f"property '{prop_name}' is {t}-typed — declare its Spark type "
            f"in the manifest column_types block (D12)"
        )
    if t == "number":
        fmt = prop_schema.get("format")
        if fmt == "float":
            return "FLOAT"
        if fmt == "double":
            return "DOUBLE"
    if t not in _JSON_TO_SPARK_TYPE:
        raise ValueError(f"property '{prop_name}': unmapped JSON type {t!r}")
    return _JSON_TO_SPARK_TYPE[t]


# ─────────────────────────────────────────────────────────────────
# Nested (struct) schema evolution — UIFPE-157
#
# The top-level auto-DDL above adds MISSING columns; it never alters an
# EXISTING column's type. Widening a STRUCT (adding nested fields) is a type
# change on an existing column, so before UIFPE-157 the table kept its narrow
# struct while the SQL produced the widened one and the MERGE failed with
# INCOMPATIBLE_DATA_FOR_TABLE.EXTRA_STRUCT_FIELDS (first hit live 2026-08-18:
# fulfillmentOrgDetails 6 → 8 fields, remediated only by a full --rebuild —
# which writes no change-log rows, so consumers were never told).
#
# Iceberg supports nested field addition natively (ADD COLUMN parent.child,
# and arr.element.child inside array elements), so the maintenance diff now
# compares the manifest-declared complex types (column_types — the same block
# json_type_to_spark treats as authoritative for array/object columns)
# against the LIVE table schema and:
#   - returns ADDITIVE nested differences as ADD COLUMN specs;
#   - returns every NON-additive difference (nested type change, field
#     removal, kind mismatch) as a refusal — the caller must fail closed and
#     name --rebuild, the same posture as the rest of auto-DDL.
#
# Everything here is pure data-in/data-out: the live schema arrives as the
# plain dict from StructType.jsonValue(), the declared side as the manifest's
# Spark DDL strings — no pyspark import (this module must stay Spark-free).
# ─────────────────────────────────────────────────────────────────

# StructType.jsonValue() primitive names → canonical DDL tokens.
_SCHEMA_JSON_PRIMITIVES = {
    "string": "STRING", "long": "BIGINT", "integer": "INT",
    "short": "SMALLINT", "byte": "TINYINT", "double": "DOUBLE",
    "float": "FLOAT", "boolean": "BOOLEAN", "date": "DATE",
    "timestamp": "TIMESTAMP", "timestamp_ntz": "TIMESTAMP_NTZ",
    "binary": "BINARY",
}

# DDL spellings → canonical DDL tokens (aliases collapse, so a live 'long'
# never falsely refuses against a declared BIGINT).
_DDL_TOKEN_CANON = {
    "STRING": "STRING", "BIGINT": "BIGINT", "LONG": "BIGINT",
    "INT": "INT", "INTEGER": "INT", "SMALLINT": "SMALLINT",
    "TINYINT": "TINYINT", "DOUBLE": "DOUBLE", "FLOAT": "FLOAT",
    "REAL": "FLOAT", "BOOLEAN": "BOOLEAN", "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP", "TIMESTAMP_NTZ": "TIMESTAMP_NTZ",
    "BINARY": "BINARY",
}


def _split_top_level(s: str) -> list:
    """Split on commas at angle/paren depth 0 ('a: INT, b: DECIMAL(38,10)')."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(s):
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    tail = s[start:].strip()
    if tail:
        parts.append(tail)
    return [p.strip() for p in parts]


def _parse_spark_ddl(ddl: str):
    """Parse a Spark DDL type string into the canonical tree:
    primitive → 'TOKEN'; struct → ('struct', {name: tree}); array →
    ('array', tree); map → ('map', key_tree, value_tree)."""
    s = ddl.strip()
    u = s.upper()
    if u.startswith("STRUCT<") and u.endswith(">"):
        fields = {}
        for part in _split_top_level(s[len("STRUCT<"):-1]):
            name, _, ftype = part.partition(":")
            if not _:
                raise ValueError(f"struct field without ':' in DDL: {part!r}")
            fields[name.strip().strip("`")] = _parse_spark_ddl(ftype)
        return ("struct", fields)
    if u.startswith("ARRAY<") and u.endswith(">"):
        return ("array", _parse_spark_ddl(s[len("ARRAY<"):-1]))
    if u.startswith("MAP<") and u.endswith(">"):
        kv = _split_top_level(s[len("MAP<"):-1])
        if len(kv) != 2:
            raise ValueError(f"MAP type needs exactly key, value: {ddl!r}")
        return ("map", _parse_spark_ddl(kv[0]), _parse_spark_ddl(kv[1]))
    if u.startswith("DECIMAL"):
        return re.sub(r"\s+", "", u)
    return _DDL_TOKEN_CANON.get(u, u)


def _schema_json_to_tree(tj):
    """StructType.jsonValue() 'type' → the same canonical tree shape."""
    if isinstance(tj, str):
        if tj.startswith("decimal("):
            return re.sub(r"\s+", "", tj.upper())
        return _SCHEMA_JSON_PRIMITIVES.get(tj, tj.upper())
    t = tj.get("type")
    if t == "struct":
        return ("struct",
                {f["name"]: _schema_json_to_tree(f["type"])
                 for f in tj.get("fields", [])})
    if t == "array":
        return ("array", _schema_json_to_tree(tj["elementType"]))
    if t == "map":
        return ("map", _schema_json_to_tree(tj["keyType"]),
                _schema_json_to_tree(tj["valueType"]))
    raise ValueError(f"unrecognized schema-json type: {tj!r}")


def _render_ddl(tree) -> str:
    """Canonical tree → Spark DDL (for ADD COLUMN specs of complex fields)."""
    if isinstance(tree, str):
        return tree
    kind = tree[0]
    if kind == "struct":
        inner = ", ".join(f"{n}: {_render_ddl(t)}" for n, t in tree[1].items())
        return f"STRUCT<{inner}>"
    if kind == "array":
        return f"ARRAY<{_render_ddl(tree[1])}>"
    if kind == "map":
        return f"MAP<{_render_ddl(tree[1])}, {_render_ddl(tree[2])}>"
    raise ValueError(f"unrenderable tree: {tree!r}")


def _diff_trees(column: str, path: list, declared, live,
                additions: list, refusals: list) -> None:
    dpath = ".".join(path)
    d_struct = isinstance(declared, tuple) and declared[0] == "struct"
    l_struct = isinstance(live, tuple) and live[0] == "struct"
    if d_struct and l_struct:
        d_fields, l_fields = declared[1], live[1]
        for name, d_type in d_fields.items():
            if name not in l_fields:
                additions.append({
                    "column": column,
                    "path": f"{dpath}.{name}",
                    "field": name,
                    "ddl": _render_ddl(d_type),
                })
            else:
                _diff_trees(column, path + [name], d_type, l_fields[name],
                            additions, refusals)
        for name in l_fields:
            if name not in d_fields:
                refusals.append(
                    f"{dpath}.{name}: exists in the table but not in the "
                    f"declared type — removing a nested field is not additive")
        return
    d_array = isinstance(declared, tuple) and declared[0] == "array"
    l_array = isinstance(live, tuple) and live[0] == "array"
    if d_array and l_array:
        _diff_trees(column, path + ["element"], declared[1], live[1],
                    additions, refusals)
        return
    if declared != live:
        refusals.append(
            f"{dpath}: table has {_render_ddl(live)} but the manifest "
            f"declares {_render_ddl(declared)} — a nested type change is "
            f"not additive")


def diff_nested_field_additions(column_types: dict,
                                live_schema_json: dict) -> tuple:
    """Compare manifest-declared complex column types against the live table
    schema (StructType.jsonValue()). Returns (additions, refusals):

      additions — [{column, path, field, ddl}] where path is the nested
                  ADD COLUMN path ('parent.child', 'arr.element.child') and
                  ddl the Spark type to add. Additive-only; the caller issues
                  ALTER TABLE … ADD COLUMN path ddl per entry.
      refusals  — human-readable non-additive differences (nested type
                  change, field removal, kind mismatch). The caller must
                  fail closed and point at --rebuild.

    Columns absent from the live table are ignored — the top-level auto-DDL
    owns those. Non-complex declared types (DATE, STRING …) compare as
    primitives and only ever surface as refusals if they diverge."""
    additions, refusals = [], []
    live_cols = {f["name"]: _schema_json_to_tree(f["type"])
                 for f in (live_schema_json or {}).get("fields", [])}
    for column, ddl in sorted((column_types or {}).items()):
        # the live table is physical; a not-yet-migrated table still carries
        # the logical name and is diffed under it (physical_rename_ddls moves it)
        live_name = physical_name(column) if physical_name(column) in live_cols else column
        if live_name not in live_cols:
            continue
        declared = _rename_tree(_parse_spark_ddl(str(ddl)))
        _diff_trees(live_name, [live_name], declared, live_cols[live_name],
                    additions, refusals)
    return additions, refusals


def nested_field_channels(master_schema: dict, column: str,
                          field: str) -> list:
    """x-channels of one nested field, for emit-mode change-log rows on a
    struct widening. Hand-authored masters tag every nested field explicitly
    (product/program/stock gates enforce it); compiled masters (UIFPE-262
    scaffolder — price) carry none and inherit the parent's tags, which is
    the fallback here either way."""
    props = (master_schema or {}).get("properties", {})
    # callers may hold the physical rendering (auto-DDL specs come from the
    # live table); resolve either spelling
    prop = props.get(column) or next(
        (p for n, p in props.items() if physical_name(n) == column), {})
    for container in (prop.get("properties"),
                      (prop.get("items") or {}).get("properties")):
        if not container:
            continue
        match = container.get(field) or next(
            (fs for f, fs in container.items() if physical_name(f) == field), None)
        if match is not None:
            return match.get("x-channels", prop.get("x-channels", []))
    return prop.get("x-channels", [])


# ─────────────────────────────────────────────────────────────────
# Manifest validation (D1 / D11)
# ─────────────────────────────────────────────────────────────────
def validate_key_mappings(sources: list, primary_key_cols: list) -> None:
    """Every source must declare key_mapping; every mapped column must be a
    Gold PK column; the source-of-truth must map the FULL primary key.

    ONE exception — `lookup_only: true`. A pure dimension carries none of the
    Gold PK columns: a taxonomy table (MTL_CATEGORIES_B, MTL_CATEGORY_SETS)
    has no item and no org, so NO expression over a changed row can name the
    Gold rows it affects, and any key_mapping written for it would be a
    fiction. Such a source is registered as a FULL temp view for the mapping
    SQL to join, and is SKIPPED by Phase 1 key detection.

    The consequence is deliberate and must stay documented: an edit confined
    to a lookup table rebuilds NOTHING. Values denormalized from it move on
    the next `--refresh_columns` run. New and removed rows in the tables that
    DO carry a PK column (e.g. the category ASSIGNMENT table) stay fully
    incremental — only relabelling is deferred.

    `lookup_only` alongside a key_mapping is refused rather than silently
    preferring one: the tempting hack is a key_mapping of
    `{organizationId: "CAST(NULL AS BIGINT)"}`, which passes the PK check,
    advances the watermark and detects nothing — working CDC to read, a
    permanent no-op to run.
    """
    pk = set(primary_key_cols)
    for src in sources:
        alias = src.get("alias", "<missing alias>")
        km = src.get("key_mapping")
        if src.get("lookup_only"):
            if src.get("is_source_of_truth"):
                raise ValueError(
                    f"source '{alias}' is both lookup_only and "
                    f"is_source_of_truth — the driving source must map the "
                    f"full primary key {sorted(pk)}"
                )
            if km:
                raise ValueError(
                    f"source '{alias}' declares BOTH lookup_only and "
                    f"key_mapping — pick one: lookup_only skips Phase 1 key "
                    f"detection entirely, a key_mapping is what makes a "
                    f"source participate in it"
                )
            continue
        if not km or not isinstance(km, dict):
            raise ValueError(
                f"source '{alias}' has no key_mapping — every source must "
                f"declare how a changed row projects to Gold key columns (D1). "
                f"A pure dimension that carries no Gold PK column declares "
                f"lookup_only: true instead"
            )
        unknown = sorted(set(km.keys()) - pk)
        if unknown:
            raise ValueError(
                f"source '{alias}' key_mapping maps non-PK columns {unknown} "
                f"(primary_key_cols = {sorted(pk)})"
            )
        if src.get("is_source_of_truth") and set(km.keys()) != pk:
            raise ValueError(
                f"source-of-truth '{alias}' must map the full primary key "
                f"{sorted(pk)}, got {sorted(km.keys())}"
            )


def validate_array_sort_keys(master_schema: dict, manifest: dict) -> None:
    """Every ARRAY-typed master property needs an array_sort_keys entry —
    unordered arrays produce phantom hash changes (D11)."""
    sort_keys = manifest.get("array_sort_keys") or {}
    for name, prop in master_schema.get("properties", {}).items():
        t = prop.get("type")
        types = t if isinstance(t, list) else [t]
        if "array" in types and name not in sort_keys:
            raise ValueError(
                f"array property '{name}' has no array_sort_keys entry in "
                f"the manifest — required for stable hashing (D11)"
            )


# ─────────────────────────────────────────────────────────────────
# Null sentinels (UIFPE-268) — declared once, applied at the view boundary
# ─────────────────────────────────────────────────────────────────
# Oracle PDH writes placeholders instead of NULL ('TBD'/'N/A', 99998/99999).
# The manifest declares the set once (string / numeric / exclude_columns) and
# every Silver view is re-projected so they read as NULL — the mapping SQL is
# untouched. Never cleaned: key/discriminator names (_ID, _NUM), Silver
# metadata, the watermark column, exclude_columns.
_SENTINEL_STRING_TYPES = ("string", "varchar", "char")
# Exact Spark simpleString names — a prefix match on "int" would also catch
# the interval types ("interval day to second"), which cannot be compared
# to a number. DECIMAL carries its precision, hence the one prefix.
_SENTINEL_NUMERIC_TYPES = {"tinyint", "smallint", "int", "bigint", "float",
                           "double"}
_SENTINEL_PROTECTED_SUFFIXES = ("_id", "_num")
_SENTINEL_STRING_RE = re.compile(r"^[^'\\\r\n]{1,64}$")
# exclude_columns entries: a bare column (every source) or alias.COLUMN
# (that source only) — SEGMENT1 is the ppid on items but a category CODE on
# categories_b, so a global exclusion is not always what is meant.
_SENTINEL_EXCLUDE_RE = re.compile(r"^([A-Za-z0-9_]{1,255})(?:\.([A-Za-z0-9_]{1,255}))?$")
_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")


def validate_null_sentinels(manifest: dict):
    """Normalize `manifest.null_sentinels` to
    {'string': [...], 'numeric': [...], 'exclude_columns': [...]} or None
    when the manifest declares none. Refuses shapes the SQL builder could
    not interpolate safely (the values become SQL literals)."""
    block = (manifest or {}).get("null_sentinels")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("manifest null_sentinels must be a mapping with "
                         "'string' and/or 'numeric' lists")
    unknown = sorted(set(block) - {"string", "numeric", "exclude_columns"})
    if unknown:
        raise ValueError(f"manifest null_sentinels has unknown keys {unknown}; "
                         f"allowed: string, numeric, exclude_columns")
    strings = block.get("string") or []
    numerics = block.get("numeric") or []
    excludes = block.get("exclude_columns") or []
    for label, values in (("string", strings), ("numeric", numerics),
                          ("exclude_columns", excludes)):
        if not isinstance(values, list):
            raise ValueError(f"null_sentinels.{label} must be a list")
    for v in strings:
        if not isinstance(v, str) or not _SENTINEL_STRING_RE.match(v):
            raise ValueError(
                f"null_sentinels.string value {v!r} must be a short plain "
                f"string without quotes, backslashes or line breaks")
    for v in numerics:
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or v != v or v in (float("inf"), float("-inf")):
            raise ValueError(
                f"null_sentinels.numeric value {v!r} must be a finite number")
    for v in excludes:
        if not isinstance(v, str) or not _SENTINEL_EXCLUDE_RE.match(v):
            raise ValueError(
                f"null_sentinels.exclude_columns entry {v!r} must be a bare "
                f"column name or alias.COLUMN (identifiers only)")
    if not strings and not numerics:
        raise ValueError("manifest null_sentinels declares no string and no "
                         "numeric values — remove the block or fill it")
    return {"string": list(strings), "numeric": list(numerics),
            "exclude_columns": list(excludes)}


def is_sentinel_protected(column: str, protected=()) -> bool:
    """Columns the sentinel rule must never touch: identity / discriminator
    names (_ID, _NUM), Silver metadata (leading underscore) and the caller's
    explicit list (key columns, watermark column, manifest exclude_columns)."""
    low = column.lower()
    if low.startswith("_") or low.endswith(_SENTINEL_PROTECTED_SUFFIXES):
        return True
    return low in {p.lower() for p in protected}


def source_protected_columns(src: dict) -> list:
    """Every column a source's OWN contract reads raw: the identifiers named
    in its key_mapping and sot_pk_mapping expressions, its sot_filter, and
    its watermark column. Naming rules are not enough — price keys on PPID
    and stock on PPID + ORGANIZATION_CODE, none of which end in _ID — and a
    cleaned key would make Phase 2's key filter miss the row, the CTAS write
    a NULL key, and reconcile's anti-join flag it forever. The sot_filter is
    included so the SQL WHERE (cleaned view) and reconcile (raw table) keep
    agreeing on the domain."""
    texts = []
    for block in ("key_mapping", "sot_pk_mapping"):
        texts.extend((src.get(block) or {}).values())
    if src.get("sot_filter"):
        texts.append(src["sot_filter"])
    # SQL words are dropped for readability of the log line only — protecting
    # a name no column carries costs nothing.
    names = {tok for t in texts if isinstance(t, str)
             for tok in _IDENTIFIER_TOKEN_RE.findall(t)
             if tok.upper() not in _SQL_WORDS}
    names.add(src.get("watermark_col") or "_silver_updated_at")
    return sorted(names)


_SQL_WORDS = {
    "AND", "AS", "BETWEEN", "BIGINT", "BOOLEAN", "CASE", "CAST", "COALESCE",
    "DATE", "DECIMAL", "DOUBLE", "ELSE", "END", "FALSE", "IN", "INT", "IS",
    "LIKE", "LOWER", "NOT", "NULL", "OR", "STRING", "THEN", "TIMESTAMP",
    "TRIM", "TRUE", "UPPER", "WHEN",
}


def _sentinel_kind(spark_type: str):
    t = (spark_type or "").lower()
    if t.startswith(_SENTINEL_STRING_TYPES):
        return "string"
    if t in _SENTINEL_NUMERIC_TYPES or t.startswith("decimal"):
        return "numeric"
    return None


def sentinel_select_exprs(fields, sentinels: dict, protected=(),
                          alias: str = None) -> tuple:
    """SELECT expressions that re-project a Silver table with its declared
    sentinels replaced by NULL.

    fields: [(column_name, spark_type_simple_string)] in table order.
    alias: the source alias, so `alias.COLUMN` exclude_columns entries apply
    to this source only (bare entries apply everywhere).
    Returns (select_exprs, cleaned_columns). Every column is kept, in order,
    under its own name — only string / numeric value columns are wrapped.
    A column is wrapped ONLY when its kind has at least one declared value,
    so a numeric-only declaration leaves strings untouched, and vice versa.
    """
    literals = {
        "string": ", ".join(
            "'" + v + "'" for v in sentinels.get("string") or []),
        "numeric": ", ".join(
            str(v) for v in sentinels.get("numeric") or []),
    }
    protect = list(protected)
    for entry in sentinels.get("exclude_columns") or []:
        scope, _, column = entry.rpartition(".")
        if not scope or (alias and scope.lower() == alias.lower()):
            protect.append(column)
    exprs, cleaned = [], []
    for name, spark_type in fields:
        col = f"`{name}`"
        kind = _sentinel_kind(spark_type)
        if kind is None or not literals[kind] \
                or is_sentinel_protected(name, protect):
            exprs.append(col)
            continue
        exprs.append(f"CASE WHEN {col} IN ({literals[kind]}) THEN NULL "
                     f"ELSE {col} END AS {col}")
        cleaned.append(name)
    return exprs, cleaned


# ─────────────────────────────────────────────────────────────────
# Phase 1 — affected-key detection SQL (D1)
# ─────────────────────────────────────────────────────────────────
def build_key_detection_sql(silver_table: str, key_mapping: dict,
                            watermark_col: str, watermark_value: str,
                            catalog: str = "glue_catalog") -> str:
    """SQL projecting each changed Silver row to the Gold key column(s) it
    affects. key_mapping may bind a SUBSET of the Gold PK (item-level
    sources fan out to all rows sharing that subset — D1)."""
    select = ", ".join(
        f"{expr} AS `{col}`" for col, expr in sorted(key_mapping.items())
    )
    return (
        f"SELECT DISTINCT {select} FROM {catalog}.{silver_table} "
        f"WHERE `{watermark_col}` > TIMESTAMP '{watermark_value}'"
    )


def build_driving_filter(driving_key_mapping: dict, keysets: list,
                         driving_alias: str = "src") -> str:
    """WHERE-clause fragment restricting the driving source to entities
    affected by ANY source's keyset (D1 Phase 2 semi-join).

    keysets: [(view_name, [gold_key_cols...]), ...] — one per source that
    had changes. Each becomes an EXISTS on the key columns that keyset
    binds, compared against the driving source's own key_mapping exprs."""
    if not keysets:
        return "FALSE"
    clauses = []
    for view, cols in keysets:
        conds = " AND ".join(
            f"{driving_key_mapping[c]} = k.`{c}`" for c in sorted(cols)
        )
        clauses.append(f"EXISTS (SELECT 1 FROM {view} k WHERE {conds})")
    return " OR ".join(clauses)


# ─────────────────────────────────────────────────────────────────
# MERGE / DDL builders (D3 / D5)
# ─────────────────────────────────────────────────────────────────
def build_pk_conditions(primary_key_cols: list, left: str = "t",
                        right: str = "s") -> str:
    """Join predicate over the PHYSICAL key columns — both sides of every
    MERGE/JOIN are physical (the table, and views renamed before use)."""
    return " AND ".join(
        f"{left}.`{physical_name(c)}` = {right}.`{physical_name(c)}`"
        for c in primary_key_cols
    )


def build_gated_merge_sql(table_full: str, primary_key_cols: list,
                          source_view: str = "gold_updates") -> str:
    """MERGE gated on the canonical hash (D3): unchanged rows are skipped
    entirely — no write, no snapshot noise."""
    pk_join = build_pk_conditions(primary_key_cols)
    return f"""
        MERGE INTO {table_full} t
        USING {source_view} s
        ON {pk_join}
        WHEN MATCHED AND t.`_channel_hashes`['{CANONICAL}'] IS DISTINCT FROM
                         s.`_channel_hashes`['{CANONICAL}']
            THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """


# ─────────────────────────────────────────────────────────────────
# Exception classification (D5 change-log durability)
# ─────────────────────────────────────────────────────────────────
# Substrings that unambiguously mean "that table does not exist". Spark 3.5
# raises AnalysisException with error class TABLE_OR_VIEW_NOT_FOUND; the
# Iceberg GlueCatalog surfaces NoSuchTableException, and the Glue catalog
# itself EntityNotFoundException. Deliberately conservative: a false negative
# aborts the run (safe — nothing is deleted), while a false positive would
# delete rows with no change-log entry and break the downstream contract.
_MISSING_TABLE_MARKERS = (
    "table_or_view_not_found",
    "table or view not found",
    "nosuchtableexception",
    "table does not exist",
    "entitynotfoundexception",
)


def is_missing_table_error(exc, table_name: str = None) -> bool:
    """True only when `exc` unambiguously reports that `table_name` is absent.

    Used by generic_gold_reconcile.py to narrow the change-log append's
    `except Exception` (review finding #9). The optional `table_name` guard
    matters: the same INSERT also references the `orphans` temp view, and a
    TABLE_OR_VIEW_NOT_FOUND naming `orphans` is a real bug that must abort,
    not a benign missing change-log table."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if not any(marker in text for marker in _MISSING_TABLE_MARKERS):
        return False
    if table_name:
        bare = str(table_name).strip("`").split(".")[-1].strip("`").lower()
        if bare and bare not in text:
            return False
    return True


# ─────────────────────────────────────────────────────────────────
# Reconcile safety window (orphan detection)
# ─────────────────────────────────────────────────────────────────
def orphan_grace_cutoff(started_at, grace_hours) -> str:
    """Spark SQL timestamp literal text for the newest `_gold_updated_at` an
    orphan candidate may carry: `started_at - grace_hours`.

    Belt-and-braces half of review finding #1. Even with the Gold-first read
    order, a Gold row written moments before this job started could in
    principle be anti-joined against a Silver snapshot that a concurrent
    transform had not yet fully committed. Excluding freshly-written rows from
    deletion costs one extra reconcile cycle for a genuine orphan and removes
    the whole class of races. Returned in 'YYYY-MM-DD HH:MM:SS' form to match
    the timestamp literals the jobs already emit."""
    hours = float(grace_hours or 0)
    if hours < 0:
        raise ValueError(f"orphan grace window must be >= 0, got {grace_hours}")
    cutoff = started_at - timedelta(hours=hours)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def build_sot_pk_sql(sot_table_full: str, sot_pk_mapping: dict,
                     primary_key_cols: list, sot_filter: str = None) -> str:
    """The reconcile job's SoT PK projection, as one SQL string.

    sot_filter is the manifest's domain filter for the source of truth (e.g.
    "SEGMENT1 LIKE 'A%'" — products only). It must match the WHERE clause of
    the mapping SQL: the transform only upserts, so a row whose source values
    leave the domain goes stale in Gold, and this anti-join projection is the
    only thing that ever deletes it. An unfiltered projection would keep
    finding the reclassified item in the SoT and never emit the DELETE.

    Deterministic column order (primary_key_cols order, not dict order) so
    the emitted SQL is stable across runs and testable by string equality.
    """
    # aliased to the PHYSICAL key name so the anti-join lines up with Gold
    pk_select = [f"{sot_pk_mapping[col]} AS {physical_name(col)}"
                 for col in primary_key_cols if col in sot_pk_mapping]
    sql = f"SELECT {', '.join(pk_select)} FROM {sot_table_full}"
    if sot_filter and sot_filter.strip():
        sql += f" WHERE {sot_filter.strip()}"
    return sql


# ─────────────────────────────────────────────────────────────────
# F3 — --refresh_columns (column-scoped historical repair)
# ─────────────────────────────────────────────────────────────────
# Change-log op for refresh rows. Distinct from BACKFILL (schema maintenance)
# and INSERT/UPDATE (incremental) so consumers can tell a logic fix from a
# data change.
REFRESH_OP = "REFRESH"
REFRESH_MODES = ("silent", "emit")


def parse_refresh_columns(raw: str) -> list:
    """Comma-separated arg -> ordered, de-duplicated column list."""
    if not raw or not raw.strip():
        return []
    out = []
    for part in raw.split(","):
        c = part.strip()
        if c and c not in out:
            out.append(c)
    return out


def validate_refresh_args(refresh_columns: list, rebuild: bool,
                          mode: str) -> None:
    """Arg-level invariants, checked before any AWS call.

    --rebuild and --refresh_columns are mutually exclusive: rebuild replaces
    the whole table (no change-log rows, consumers full-resync), refresh
    repairs named columns in place (hash-gated, optionally change-logged).
    Asking for both has no coherent semantics."""
    if refresh_columns and rebuild:
        raise ValueError(
            "--refresh_columns and --rebuild are mutually exclusive: "
            "a rebuild already recomputes every column and consumers resync "
            "via full load. Drop one of the two arguments.")
    if refresh_columns and mode not in REFRESH_MODES:
        raise ValueError(
            f"--refresh_mode must be one of {REFRESH_MODES}, got {mode!r}. "
            f"'emit' (default) writes REFRESH change-log rows for the "
            f"channels whose hash moved; 'silent' repairs values without "
            f"notifying any channel — only correct when no consumer is "
            f"bound to the refreshed columns yet.")


def validate_refresh_columns(cols: list, master_schema: dict,
                             primary_key_cols: list) -> None:
    """Column-level invariants against the master schema.

    PK columns are refused outright: 'refreshing' a key is an identity
    change, which is a delete+insert, not a repair. Metadata columns are
    framework-owned and recomputed as part of every refresh anyway."""
    props = set((master_schema or {}).get("properties", {}))
    unknown = [c for c in cols if c not in props]
    if unknown:
        raise ValueError(
            f"--refresh_columns names columns not in the master schema: "
            f"{unknown}. Exact camelCase property names required.")
    pks = [c for c in cols if c in set(primary_key_cols)]
    if pks:
        raise ValueError(
            f"--refresh_columns must not name primary-key columns: {pks}.")
    meta = [c for c in cols if c in METADATA_COLUMNS]
    if meta:
        raise ValueError(
            f"--refresh_columns must not name framework metadata columns: "
            f"{meta} — hashes are recomputed on every refresh already.")


def build_refresh_row_sql(table_full: str, primary_key_cols: list,
                          fresh_view: str, cols: list, master_schema: dict,
                          channels: list, run_ts_sql: str, run_id: str) -> str:
    """The row a --refresh_columns run compares and persists: NAMED columns
    fresh from the mapping SQL, the rest as Gold stores them, hashed over
    that composite so the hash always matches what the row will hold
    (UIFPE-269 — replaces the pending-delta guard)."""
    named = set(cols)
    props = list((master_schema or {}).get("properties", {}).keys())
    select = [f"{'u' if p in named else 'g'}.`{physical_name(p)}`" for p in props]
    qualifiers = {p: ("u" if p in named else "g") for p in props}
    hashes = build_hash_map_expr(master_schema, channels, qualifiers=qualifiers)
    select.append(f"{hashes} AS `_channel_hashes`")
    select.append(f"{run_ts_sql} AS `_gold_updated_at`")
    select.append(f"'{validate_sql_literal(run_id, 'run_id')}' AS `_gold_run_id`")
    return (f"SELECT {', '.join(select)} FROM {fresh_view} u "
            f"JOIN {table_full} g ON {build_pk_conditions(primary_key_cols, 'g', 'u')}")


def build_refresh_merge_sql(table_full: str, primary_key_cols: list,
                            src_view: str, cols: list,
                            bump_metadata: bool) -> str:
    """MERGE for a refresh: UPDATE only the named columns plus the hash map.

    In emit mode `_gold_updated_at`/`_gold_run_id` move too (the row's change
    is being announced); silent mode leaves them untouched, mirroring the
    silent-backfill contract (values repaired, no observable churn)."""
    set_parts = [f"t.`{physical_name(c)}` = s.`{physical_name(c)}`" for c in cols]
    set_parts.append("t.`_channel_hashes` = s.`_channel_hashes`")
    if bump_metadata:
        set_parts.append("t.`_gold_updated_at` = s.`_gold_updated_at`")
        set_parts.append("t.`_gold_run_id` = s.`_gold_run_id`")
    return (f"MERGE INTO {table_full} t USING {src_view} s "
            f"ON {build_pk_conditions(primary_key_cols)} "
            f"WHEN MATCHED THEN UPDATE SET {', '.join(set_parts)}")


def changes_table_name(gold_table: str) -> str:
    return f"{gold_table}_changes"


def changes_table_location(gold_s3_path: str) -> str:
    return gold_s3_path.rstrip("/") + "_changes/"


def build_changes_table_ddl(catalog_db: str, gold_table: str,
                            primary_key_cols: list, pk_types: dict,
                            location: str) -> str:
    """CREATE TABLE IF NOT EXISTS for the append-only change log (D5).
    pk_types: {pk_col: spark_type} — mirrors the entity table's key types."""
    pk_cols_ddl = ",\n            ".join(
        f"`{physical_name(c)}` {pk_types[c]}" for c in primary_key_cols
    )
    return f"""
        CREATE TABLE IF NOT EXISTS {catalog_db}.`{changes_table_name(gold_table)}` (
            {pk_cols_ddl},
            `op`               STRING,
            `changed_channels` ARRAY<STRING>,
            `run_id`           STRING,
            `_gold_updated_at` TIMESTAMP,
            `change_date`      DATE
        )
        USING iceberg
        PARTITIONED BY (`change_date`)
        LOCATION '{location}'
        TBLPROPERTIES ('format-version' = '2')
    """
