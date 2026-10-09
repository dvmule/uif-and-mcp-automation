# preview.py
# Live Gold data for the wizard's Filter step (UIFPE-441): the 50 most common
# values of a column (suggestions in an attribute condition) and a preview of
# a draft's row filter — rows before/after the filter, a 10-row sample, and
# the identifiers of a pasted list that match nothing. Since UIFPE-490 also
# the matched rows themselves: one CSV of every row the filter keeps, in the
# preview's columns, capped, handed out as a short-lived presigned link the
# author clicks — never downloaded for them.
#
# Runs on ATHENA, the account's shared `primary` workgroup (no Beacon
# workgroup: the deploy role cannot create one, and every Athena user in the
# repo shares primary). Results land in Beacon's own data bucket under
# preview/ and expire after a day (s3.tf). Precedent for the shape:
# stacks/echo/src/apps/lambdas/grain_probe.py.
#
# HONESTY NOTE the UI carries: Athena speaks Trino, the real run speaks Spark.
# Plain SQL behaves identically; the compiled filters are transpiled with
# sqlglot and a clause Athena cannot run REFUSES the preview with a reason —
# never a preview of different semantics. Trino is stricter about types than
# Spark (bigint = 'x' fails here, Spark coerces), so a preview failure is not
# proof the run would fail: Check filter + the run remain the authority.
#
# ASYNC because API Gateway caps the API Lambda at 29 s and a scan of a
# 6.8M-row Gold table takes longer: POST starts the executions and returns
# their ids; GET reads their state with one BatchGetQueryExecution. Athena IS
# the state store — no DynamoDB, no self-invoke. Every query we start opens
# with MARKER and writes to our prefix, and status/cancel refuse an id whose
# execution lacks either: the role can read any primary result, this API
# must not.
#
# Fences (the grain probe's): only engine-validated configs reach Athena
# (parse_config + the three static layers — no subqueries, allowlisted
# functions, alias-qualified columns that exist); every identifier spliced
# around a transpiled expression (db, table, alias, column, list values)
# passes its own regex or literal escaping; every query text is measured
# against Athena's 256 KB ceiling before anything starts.
#
# THE CARDINALITY PROFILE (UIFPE-514, owner decisions 2026-09-22) decides
# which attributes the Filter step's picker offers: identifiers by name are
# never offered, text with more than TEXT_CARDINALITY_CAP distinct values is
# never offered, numbers/dates/booleans always are. It is asynchronous and
# PERSISTED: POST starts one Athena execution (count(*) + approx_distinct per
# scalar column), GET polls it, and a finished profile is written as JSON to
# <bucket>/<preview prefix>/profiles/<db>.<table>.json and served from there
# for a day (the prefix expires after a day anyway, s3.tf). The old design —
# one synchronous scan capped at 20 s, cached per Lambda instance — timed
# out on the real product table and answered a `reason` the UI never showed
# while hiding nothing, which is why the wizard now starts the profile on
# the Source step and the picker holds text back until it lands.

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError, UnsupportedError

from control_panel import registries, validation

try:
    import dynamic_mapping
    from dynamic_mapping import lists   # UIFPE-581: a spread list's element fields
    ENGINE_AVAILABLE = True
except Exception:  # pragma: no cover — the API image always vendors the engine
    dynamic_mapping = None
    lists = None
    ENGINE_AVAILABLE = False

MARKER = "/* uif-beacon-preview */"
SAMPLE_ROWS = 10
SAMPLE_COLUMNS_CAP = 12
VALUES_LIMIT = 50
VALUES_POLL_CAP_S = 20          # sync, inside the 29 s Lambda
VALUES_CACHE_TTL_S = 600        # per Lambda instance — a nicety, not a store
MAX_LISTS = 10
LIST_CAP = 5000                 # the wizard's per-condition rule
LIST_VALUE_MAX_CHARS = 128
MISSING_CAP = 500
MAX_QUERY_BYTES = 250_000       # Athena refuses a query over 262,144 bytes
PREVIEW_MAX_AGE_S = 300         # pending longer than this is stopped on status
# UIFPE-490: the CSV download of the matched rows. The cap keeps one click
# from writing a multi-gigabyte result of a whole Gold table; the UI says so
# when it bites. The link lives as long as the export page's Files links.
DOWNLOAD_ROW_CAP = 100_000
DOWNLOAD_LINK_TTL_S = 900

ATHENA_NOTE = ("Preview runs on Athena (Trino); the export runs on Spark. Plain SQL "
               "behaves identically, but Athena is stricter about types — a preview "
               "failure is a hint, not proof the run would fail. Check filter and the "
               "run itself remain the authority.")

ALIAS_RE = re.compile(r"^[a-z_][a-z0-9_]*$", re.ASCII)
COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$", re.ASCII)
DB_RE = re.compile(r"^[a-z0-9_]+$", re.ASCII)
# Identifiers BY NAME, whole-name and case-insensitive: never offered as an
# attribute, never suggested (an equals condition on one is an identifier
# list in disguise). The PPID is physically product_id — no Gold table has a
# column named ppid; ppid/isbn/isbn_13/isbn_10 stay for history. Widened
# from ppid/isbn* at UIFPE-514 (owner decision 2026-09-22) after the old
# list let product_id, inventory_item_id and source_item_number through in dev.
IDENTIFIER_COLUMN_RE = re.compile(
    r"^(ppid|isbn|isbn13|isbn_13|isbn10|isbn_10|product_id|parent_product_id|inventory_item_id"
    r"|source_item_number|program_id|content_id)$", re.IGNORECASE)
# The preview sample / CSV projection keeps the OLD list on purpose: those
# columns are what an author recognises a row BY, and the widened list would
# change every download's header (UIFPE-490's assertions pin it). Used by
# _projection only.
SAMPLE_IDENTIFIER_RE = re.compile(r"^(ppid|isbn13|isbn_13|isbn10|isbn_10|isbn)$", re.IGNORECASE)
_QID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PREVIEW_ID_RE = re.compile(rf"^{_QID}(\.{_QID}){{1,{MAX_LISTS + 1}}}$")
DOWNLOAD_ID_RE = re.compile(rf"^{_QID}$")   # one execution, not a dotted set
PROFILE_ID_RE = DOWNLOAD_ID_RE               # likewise one execution
_MISSING_COLUMN_RE = re.compile(r"CAST\(([a-z_][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*) AS VARCHAR\) = w\.v")
# the driving table, read back off a download's query text for its filename
_DRIVING_TABLE_RE = re.compile(r'\nFROM "[a-z0-9_]+"\."([a-z0-9_]+)" AS [a-z_][a-z0-9_]*')
# the table a profile execution scanned, read back off its query text so a
# poll through source A's route cannot persist source B's numbers under A
_PROFILE_TABLE_RE = re.compile(r'\nFROM "([a-z0-9_]+)"\."([a-z0-9_]+)" AS t$')

# UIFPE-514 (owner decisions 2026-09-22): text with more distinct values than
# this is never offered as an attribute and never suggested — descriptions,
# URLs, owner names, version strings. Mirrored by rowFilter.js.
TEXT_CARDINALITY_CAP = 1000
# A finished profile is written under the preview prefix and served from
# there for a day; the same day s3.tf gives the whole preview/ prefix.
PROFILE_PERSIST_TTL_S = 86400
PROFILE_PREFIX = "profiles"
# Kept in the payload for compatibility (distinct ≥ this share of the rows);
# the picker no longer hides on it — the product table's grain is item ×
# organisation, so the ratio measured the wrong thing (UIFPE-514 diagnosis).
PROFILE_UNIQUE_RATIO = 0.5

_athena = None
_athena_initialized = False
_s3 = None                      # presigns a download's CSV (UIFPE-490); reads/writes profiles (UIFPE-514)
_s3_initialized = False
_values_cache: dict[tuple, tuple[float, dict]] = {}


class PreviewError(Exception):
    """An HTTP-shaped refusal: code + a message the author can act on."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _client():
    global _athena, _athena_initialized
    if not _athena_initialized:
        import boto3
        _athena = boto3.client("athena")
        _athena_initialized = True
    return _athena


def _s3_client():
    global _s3, _s3_initialized
    if not _s3_initialized:
        import boto3
        _s3 = boto3.client("s3")
        _s3_initialized = True
    return _s3


def set_client_for_testing(athena, s3=None) -> None:
    """Inject fakes (None, None resets to lazy boto3). `s3` is reached by a
    finished download's presign and by every profile read/write — including
    the one column_values makes — so tests that touch those pass a fake."""
    global _athena, _athena_initialized, _s3, _s3_initialized
    _athena = athena
    _athena_initialized = athena is not None
    _s3 = s3
    _s3_initialized = s3 is not None


def reset_cache() -> None:
    _values_cache.clear()


def enabled() -> bool:
    """Presence of BEACON_PREVIEW_WORKGROUP is the switch (Terraform supplies
    the value); the data bucket is where results go."""
    return bool(os.environ.get("BEACON_DATA_BUCKET")) and bool(os.environ.get("BEACON_PREVIEW_WORKGROUP"))


def _workgroup() -> str:
    return os.environ["BEACON_PREVIEW_WORKGROUP"]


def _prefix() -> str:
    return (os.environ.get("BEACON_PREVIEW_PREFIX") or "preview").strip("/")


def _output() -> str:
    return f"s3://{os.environ['BEACON_DATA_BUCKET']}/{_prefix()}/"


def _not_configured() -> PreviewError:
    return PreviewError(503, "live preview is not configured on this environment "
                             "(BEACON_PREVIEW_WORKGROUP) — Check filter still works")


# ---- the plan: everything decided BEFORE Athena is touched ------------------

@dataclass(frozen=True)
class SourceRef:
    alias: str
    db: str
    table: str
    columns: tuple[str, ...]          # true names, as the catalog lists them
    types: dict[str, str]             # true name -> Glue type ("" when unknown)


@dataclass(frozen=True)
class ListSpec:
    index: int
    alias: str
    column: str                       # true name
    values: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    from_sql: str                     # FROM "db"."t" AS a LEFT JOIN ... ON <trino>
    where_sql: str | None             # "(f1) AND (f2)" in Trino, or None
    # (label, Trino expression, glue type): the sample's columns. UIFPE-603 —
    # the export's mapping when the config carries one (label = target field,
    # type None for a transpiled expression, rendered as it is), else the
    # diagnostic projection (label = alias.column).
    projection: tuple[tuple[str, str, str | None], ...]
    lists: tuple[ListSpec, ...]
    refs: dict[str, SourceRef]
    untransformed: tuple[str, ...] = ()   # UIFPE-603: targets shown as their source value


def _issue(where: str, message: str) -> dict:
    return {"where": where, "message": message}


def _table(ref: SourceRef) -> str:
    # db/table are fenced to [a-z0-9_]; quoting keeps a leading digit legal
    return f'"{ref.db}"."{ref.table}"'


def _alias_ok(alias: str) -> bool:
    """A plain lowercase identifier that Trino would not quote — a reserved
    word (order, table, …) renders quoted in column references but is
    spliced bare as `AS alias`, which is a syntax error."""
    if not ALIAS_RE.fullmatch(alias):
        return False
    return exp.to_identifier(alias).sql(dialect="trino") == alias


def _resolve_column(ref: SourceRef, name: str) -> str | None:
    """The catalog's true column name for `name`, case-insensitively (Glue
    lowercases; authors and stored configs may carry the camelCase)."""
    if name in ref.columns:
        return name
    matches = [c for c in ref.columns if c.lower() == name.lower()]
    return matches[0] if len(matches) == 1 else None


def transpile_condition(spark_sql: str) -> str:
    """One Spark SQL boolean → Trino, or PreviewError(422) with the reason
    Athena could not run it. Three adjustments sqlglot does not make:
    backtick-quoted identifiers are un-quoted (quoted = case-sensitive on
    Trino, and Glue stores lowercase); a LIKE pattern carrying a backslash
    gets an explicit ESCAPE '\\' (Spark's default escape, none on Trino);
    a function sqlglot passes through unknown (exp.Anonymous — hash,
    format_number) is refused rather than sent to fail on Athena."""
    try:
        tree = sqlglot.parse_one(spark_sql, read="spark")
    except SqlglotError as e:
        raise PreviewError(422, f"not parseable as Spark SQL: {e}")
    if tree is None:
        raise PreviewError(422, "empty condition")

    for ident in tree.find_all(exp.Identifier):
        if ident.quoted and COLUMN_RE.fullmatch(ident.this or ""):
            ident.set("quoted", False)

    def _escaped(like: exp.Expression) -> exp.Expression | None:
        if isinstance(like.parent, exp.Escape):
            return None
        pattern = like.expression
        if isinstance(pattern, exp.Literal) and pattern.is_string and "\\" in pattern.this:
            return exp.Escape(this=like.copy(), expression=exp.Literal.string("\\"))
        return None

    if isinstance(tree, (exp.Like, exp.ILike)):
        # a root node has no parent to replace it in — swap the tree itself
        tree = _escaped(tree) or tree
    else:
        # collect first: replacing nodes while walking the generator skips them
        for like in list(tree.find_all(exp.Like, exp.ILike)):
            wrapped = _escaped(like)
            if wrapped is not None:
                like.replace(wrapped)

    anonymous = sorted({node.name for node in tree.find_all(exp.Anonymous) if node.name})
    if anonymous:
        raise PreviewError(422, f"function {anonymous[0]!r} has no Athena equivalent — remove "
                                "it to preview (the export itself still accepts it)")
    try:
        return tree.sql(dialect="trino", unsupported_level=ErrorLevel.RAISE)
    except UnsupportedError as e:
        raise PreviewError(422, f"cannot run on Athena: {e}")


def _condition_columns(sql: str) -> list[tuple[str, str]]:
    """(alias, column) pairs a Spark condition references — for the sample
    projection. Unparseable text is skipped here; validation caught it."""
    try:
        tree = sqlglot.parse_one(sql, read="spark")
    except SqlglotError:
        return []
    out: list[tuple[str, str]] = []
    for col in tree.find_all(exp.Column):
        if not col.table:
            continue
        # UIFPE-567: alias.struct.member — the sample shows the struct column
        # (rendered as JSON), the same rule the validator applies
        parts = [p.name for p in col.parts]
        out.append((parts[0], parts[1]) if len(parts) > 2 else (col.table, col.name))
    return out


def _trino_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_lists(raw_lists, refs: dict[str, SourceRef]) -> tuple[list[ListSpec], list[dict]]:
    specs: list[ListSpec] = []
    for i, item in enumerate(raw_lists):
        where = f"lists[{i}]"
        if not isinstance(item, dict):
            return [], [_issue(where, "each list is {column, values}")]
        column = item.get("column")
        values = item.get("values")
        if not isinstance(column, str) or "." not in column:
            return [], [_issue(where, "column must be alias-qualified (alias.column)")]
        alias, _, name = column.partition(".")
        if alias not in refs:
            return [], [_issue(where, f"unknown source {alias!r}")]
        if not refs[alias].table:
            # UIFPE-581: an identifier list checks presence in the list's OWN
            # table (missing_query); a spread element has none
            return [], [_issue(where, f"{alias!r} is a list element — paste identifiers for a "
                                      "table's own column instead")]
        if not COLUMN_RE.fullmatch(name):
            return [], [_issue(where, f"{name!r} is not a plain column name")]
        true = _resolve_column(refs[alias], name)
        if true is None:
            return [], [_issue(where, f"column {name!r} not found in source {alias!r}")]
        if not isinstance(values, list) or not values:
            return [], [_issue(where, "values must be a non-empty list")]
        if len(values) > LIST_CAP:
            return [], [_issue(where, f"{len(values):,} identifiers — the limit is {LIST_CAP:,} per condition")]
        clean: list[str] = []
        for v in values:
            if not isinstance(v, str) or not v.strip() or len(v) > LIST_VALUE_MAX_CHARS \
                    or any(ord(ch) < 32 for ch in v):
                return [], [_issue(where, "every identifier must be a plain string of at most "
                                          f"{LIST_VALUE_MAX_CHARS} characters")]
            clean.append(v)
        specs.append(ListSpec(index=i, alias=alias, column=true, values=tuple(clean)))
    return specs, []


STUB_TARGET = "_filter_check"   # the wizard's placeholder mapping (lib/rowFilter.js)


def _label(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _mapping_projection(config, refs: dict[str, SourceRef]) \
        -> tuple[tuple[tuple[str, str, str | None], ...], tuple[str, ...]]:
    """UIFPE-603 (owner decision 2026-09-25): the sample and the CSV are the
    EXPORT, ten rows of it — the mapped fields as columns, in mapping order,
    headed by the target names. A column mapping renders its source by type
    (a complex one as JSON); a literal is the literal; an expression is
    transpiled Spark → Trino like a filter. An expression Athena cannot run
    is never previewed with different semantics: its column shows the first
    source column it reads (NULL when none) and the target is reported in
    `untransformed` so the UI says so. `data_type` casts are not applied —
    they change a value's type, not the text the sample shows."""
    out: list[tuple[str, str, str | None]] = []
    untransformed: list[str] = []

    def column(alias: str, name: str) -> tuple[str, str | None] | None:
        ref = refs.get(alias)
        true = _resolve_column(ref, name) if ref else None
        return (f"{alias}.{true}", ref.types.get(true, "")) if true else None

    def expression(target: str, spark_sql: str) -> None:
        try:
            out.append((target, _json_safe_casts(transpile_condition(spark_sql), spark_sql, refs), None))
            return
        except PreviewError:
            pass
        cols = _condition_columns(spark_sql)
        fallback = column(*cols[0]) if cols else None
        out.append((target, *fallback) if fallback else (target, "CAST(NULL AS VARCHAR)", None))
        untransformed.append(target)

    for m in config.mappings:
        if m.type == "one_to_one":
            alias, _, name = str(m.source or "").partition(".")
            resolved = column(alias, name)       # validate_references vouched for it
            out.append((m.target, *resolved) if resolved else (m.target, str(m.source), None))
        elif m.type == "literal":
            v = m.value
            sql = ("NULL" if v is None else "TRUE" if v is True else "FALSE" if v is False
                   else _trino_literal(v) if isinstance(v, str) else str(v))
            out.append((m.target, sql, None))
        elif m.type == "one_to_many":
            for target, e in zip(m.targets, m.expressions):
                expression(target, e)
        else:                                    # derived / many_to_one
            expression(m.target, m.expression or "")
    return tuple(out), tuple(untransformed)


def _projection(config, refs: dict[str, SourceRef], driving: str,
                specs: list[ListSpec]) -> tuple[tuple[str, str, str], ...]:
    """The sample's columns when the config carries only the placeholder
    mapping (Check filter's shape): what the filter, the lists and the joins
    touch, plus the driving source's identifier columns — deduped, capped."""
    wanted: list[tuple[str, str]] = []

    def add(alias: str, name: str) -> None:
        ref = refs.get(alias)
        true = _resolve_column(ref, name) if ref else None
        if true and (alias, true) not in wanted:
            wanted.append((alias, true))

    for f in config.filters:
        for alias, name in _condition_columns(f):
            add(alias, name)
    for spec in specs:
        add(spec.alias, spec.column)
    for c in refs[driving].columns:
        if SAMPLE_IDENTIFIER_RE.fullmatch(c):   # the OLD list, deliberately — see its comment
            add(driving, c)
    for j in config.joins:
        for alias, name in _condition_columns(j.condition or ""):
            add(alias, name)
    if not wanted:
        for c in refs[driving].columns[:6]:
            add(driving, c)
    return tuple((f"{a}.{c}", f"{a}.{c}", refs[a].types.get(c, "")) for a, c in wanted[:SAMPLE_COLUMNS_CAP])


def build_plan(body: dict) -> tuple[Plan | None, list[dict]]:
    """Validate the wizard's request and plan every query — WITHOUT calling
    Athena. Returns (plan, []) or (None, issues) in the validator's issue
    shape so the UI routes filters[i] issues to their clause. Malformed
    bodies raise PreviewError(422)."""
    if not ENGINE_AVAILABLE:
        raise PreviewError(503, "the mapping engine is not installed in this deployment")
    if not isinstance(body, dict) or not isinstance(body.get("mapping_config"), dict):
        raise PreviewError(422, "body must carry mapping_config (the draft's mapping config) "
                                "and, optionally, lists")
    raw_lists = body.get("lists") or []
    if not isinstance(raw_lists, list):
        raise PreviewError(422, "lists must be a list of {column, values}")
    if len(raw_lists) > MAX_LISTS:
        raise PreviewError(422, f"at most {MAX_LISTS} identifier lists per preview")

    try:
        config = dynamic_mapping.parse_config(body["mapping_config"])
    except dynamic_mapping.ConfigError as e:
        return None, [_issue("config", str(e))]
    issues = dynamic_mapping.validate_structure(config) + dynamic_mapping.validate_expressions(config)
    if issues:
        return None, [_issue(i.where, i.message) for i in issues]
    refs: dict[str, SourceRef] = {}
    for s in config.sources:
        where = f"sources.{s.name}"
        if s.format != "iceberg":
            return None, [_issue(where, f"{s.name!r} is a {s.format} source — only Gold (iceberg) "
                                        "tables can be previewed on Athena")]
        parts = s.path.split(".")
        if len(parts) != 3 or parts[0] != "glue_catalog":
            return None, [_issue(where, f"{s.path!r} is not a gold catalog table")]
        _, db, table = parts
        if not DB_RE.fullmatch(db) or not DB_RE.fullmatch(table):
            return None, [_issue(where, f"{s.path!r} is not a plain catalog name")]
        if not _alias_ok(s.name):
            return None, [_issue(where, f"alias {s.name!r} is a reserved word or not a plain "
                                        "lowercase name on Athena — rename the source alias")]
        source_id = registries.source_id_for_path(s.path)
        if source_id is None:
            return None, [_issue(where, f"{s.path!r} is not in a configured gold database")]
        live = registries.source_columns(source_id)
        if live.get("missing"):
            # UIFPE-569: the table has not landed in this environment — not
            # an outage, and retrying cannot help
            return None, [_issue(where, f"the Gold table {db}.{table} does not exist in this "
                                        "environment yet — it can be previewed once the domain's "
                                        "first load lands")]
        if live.get("provenance") != "live" or not live.get("columns"):
            return None, [_issue(where, f"live schema unavailable for {s.name!r} — the preview "
                                        "reads the same catalog Athena does; try again shortly")]
        refs[s.name] = SourceRef(alias=s.name, db=db, table=table,
                                 columns=tuple(live["columns"]),
                                 types={c: str(t) for c, t in (live.get("types") or {}).items()})

    # UIFPE-581: a spread list's element is a source-like alias whose fields
    # (and their types) come from the list column's catalogued array type
    unnest_sql: dict[str, str] = {}
    for where, step in config.list_steps():
        src, col = step.source_column()
        ref = refs.get(src)
        if ref is None:
            return None, [_issue(where, f"list names unknown source {src!r}")]
        true = _resolve_column(ref, col)
        if true is None:
            return None, [_issue(f"{where}.list", f"column {col!r} not found in source {src!r}")]
        fields = lists.element_field_types_from_glue_type(ref.types.get(true, ""))
        if not fields:
            return None, [_issue(f"{where}.list", f"{src}.{true} is not a list column on Athena "
                                                  f"(catalogued type {ref.types.get(true) or 'unknown'!r})")]
        if not _alias_ok(step.alias):
            return None, [_issue(where, f"alias {step.alias!r} is a reserved word or not a plain "
                                        "lowercase name on Athena — rename the list element")]
        refs[step.alias] = SourceRef(alias=step.alias, db="", table="",
                                     columns=tuple(fields), types=dict(fields))
        # LEFT JOIN … ON TRUE is Trino's outer UNNEST: an empty list keeps its
        # row, as the engine's explode_outer does. UIFPE-601: Athena hands an
        # unnested RECORD back as one ROW column, not one column per field
        # (MISMATCHED_COLUMN_ALIASES in dev), so a list of structs is spread
        # as one array per field — UNNEST zips same-length arrays into
        # side-by-side columns — and alias.field references stay as they are.
        # A list of scalars is the plain single-column form.
        if list(fields) == [lists.ELEMENT_VALUE]:
            arrays = f"{src}.{true}"
        else:
            arrays = ", ".join(f"transform({src}.{true}, x -> x.{f})" for f in fields)
        unnest_sql[step.alias] = (f"\nLEFT JOIN UNNEST({arrays}) AS {step.alias} "
                                  f"({', '.join(fields)}) ON TRUE")

    ref_issues = dynamic_mapping.validate_references(config, {a: list(r.columns) for a, r in refs.items()})
    if ref_issues:
        return None, [_issue(i.where, i.message) for i in ref_issues]
    # UIFPE-609: a bare list/record column in a CSV export would pass here
    # (Athena renders it as JSON text) and die at the run's rendering guard —
    # refuse it with the same remedy, when the caller says the format
    fmt_issues = validation.complex_column_issues(config, {a: r.types for a, r in refs.items()}, body.get("format"))
    if fmt_issues:
        return None, [_issue(i.where, i.message) for i in fmt_issues]

    driving = config.joins[0].left if config.joins else config.sources[0].name
    from_sql = f"{_table(refs[driving])} AS {driving}"
    for i, j in enumerate(config.joins):
        where = f"joins[{i}]"
        if j.type in ("left_semi", "left_anti"):
            return None, [_issue(where, f"a {j.type} join has no Athena equivalent — preview "
                                        "without it, or use a left/inner join")]
        if j.through:
            from_sql += unnest_sql[j.through.alias]   # spread before the match (UIFPE-580)
        if j.type == "cross":
            from_sql += f"\nCROSS JOIN {_table(refs[j.right])} AS {j.right}"
            continue
        try:
            cond = transpile_condition(j.condition)
        except PreviewError as e:
            return None, [_issue(f"{where}.condition", e.message)]
        from_sql += f"\n{j.type.upper()} JOIN {_table(refs[j.right])} AS {j.right} ON {cond}"
    for step in config.unnest:
        from_sql += unnest_sql[step.alias]            # after every join, before the filters

    where_parts: list[str] = []
    for i, f in enumerate(config.filters):
        try:
            where_parts.append(f"({transpile_condition(f)})")
        except PreviewError as e:
            return None, [_issue(f"filters[{i}]", e.message)]
    where_sql = " AND ".join(where_parts) if where_parts else None

    specs, list_issues = _validate_lists(raw_lists, refs)
    if list_issues:
        return None, list_issues

    # UIFPE-603: a real mapping IS the sample's columns; the placeholder
    # mapping (Check filter's shape, older callers) keeps the diagnostic set
    if all(m.type == "literal" and m.target == STUB_TARGET for m in config.mappings):
        projection, untransformed = _projection(config, refs, driving, specs), ()
    else:
        projection, untransformed = _mapping_projection(config, refs)
    plan = Plan(from_sql=from_sql, where_sql=where_sql, projection=projection,
                lists=tuple(specs), refs=refs, untransformed=untransformed)

    for name, query in _queries(plan):
        size = len(query.encode("utf-8"))
        if size > MAX_QUERY_BYTES:
            return None, [_issue(name, f"the preview query is {size // 1024} KB; Athena accepts "
                                       f"{MAX_QUERY_BYTES // 1024} KB — trim the identifier list "
                                       "or filter on an attribute")]
    return plan, []


# ---- query texts -------------------------------------------------------------

_TRINO_SCALAR = {"string": "VARCHAR", "varchar": "VARCHAR", "char": "VARCHAR", "boolean": "BOOLEAN",
                 "tinyint": "TINYINT", "smallint": "SMALLINT", "int": "INTEGER", "integer": "INTEGER",
                 "bigint": "BIGINT", "float": "REAL", "double": "DOUBLE", "binary": "VARBINARY"}
_ARRAY_TYPE_RE = re.compile(r"^array\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_STRUCT_TYPE_RE = re.compile(r"^struct\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_MAP_TYPE_RE = re.compile(r"^map\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _json_value(ref: str, glue_type: str, depth: int = 0) -> tuple[str, str, bool] | None:
    """UIFPE-605: `ref` rewritten so Athena can CAST it to JSON — (sql, Trino
    type, changed). Athena casts text, numbers, decimals and booleans to
    JSON but refuses a date or timestamp (Iceberg's `timestamp(6) with time
    zone` in every price record), so those become VARCHAR, field by field:
    `transform` over an array, `transform_values` over a map, and a struct
    rebuilt as CAST(ROW(…) AS ROW(name type, …)) so its field names survive
    and the JSON stays an object, as Spark's to_json writes it. Unchanged
    parts are left as they are. None when the Glue type cannot be read."""
    t = (glue_type or "").strip()
    low = t.lower()
    if low.startswith(("date", "timestamp")):
        return f"CAST({ref} AS VARCHAR)", "VARCHAR", True
    if low.startswith("decimal"):
        return ref, t.upper().replace(" ", ""), False
    if low.startswith(("varchar(", "char(")):
        return ref, "VARCHAR", False
    if low in _TRINO_SCALAR:
        return ref, _TRINO_SCALAR[low], False
    if m := _ARRAY_TYPE_RE.match(t):
        var = f"x{depth}"
        inner = _json_value(var, m.group(1), depth + 1)
        if inner is None:
            return None
        sql, typ, changed = inner
        return (f"transform({ref}, {var} -> {sql})" if changed else ref), f"ARRAY({typ})", changed
    if m := _MAP_TYPE_RE.match(t):
        parts = lists.split_top_level(m.group(1))
        key = _json_value("k", parts[0], depth + 1) if len(parts) == 2 else None
        val = _json_value(f"v{depth}", parts[1], depth + 1) if len(parts) == 2 else None
        if key is None or val is None or key[2]:
            return None
        sql, typ, changed = val
        return (f"transform_values({ref}, (k, v{depth}) -> {sql})" if changed else ref), \
            f"MAP({key[1]}, {typ})", changed
    if m := _STRUCT_TYPE_RE.match(t):
        sqls, types, changed = [], [], False
        for part in lists.split_top_level(m.group(1)):
            name, _, ftype = part.partition(":")
            name = name.strip().strip("`")
            inner = _json_value(f"{ref}.{name}", ftype, depth) if _FIELD_NAME_RE.fullmatch(name) else None
            if inner is None:
                return None
            sqls.append(inner[0])
            types.append(f"{name} {inner[1]}")
            changed = changed or inner[2]
        row_type = f"ROW({', '.join(types)})"
        return (f"CAST(ROW({', '.join(sqls)}) AS {row_type})" if changed else ref), row_type, changed
    return None


def _json_safe_casts(trino_sql: str, spark_sql: str, refs: dict[str, SourceRef]) -> str:
    """UIFPE-605: a computed field's `to_json(alias.col)` transpiles to
    `CAST(alias.col AS JSON)`; give it the same date/timestamp-safe value a
    directly mapped complex column gets."""
    for alias, name in _condition_columns(spark_sql):
        ref = refs.get(alias)
        true = _resolve_column(ref, name) if ref else None
        if not true or not ref.types.get(true, "").lower().startswith(("array<", "struct<", "map<")):
            continue
        safe = _json_value(f"{alias}.{true}", ref.types[true])
        if safe and safe[2]:
            trino_sql = trino_sql.replace(f"CAST({alias}.{true} AS JSON)", f"CAST({safe[0]} AS JSON)")
    return trino_sql


def _render_column(label: str, ref: str, glue_type: str | None) -> str:
    if glue_type is None:
        # a transpiled expression or a literal: its type is Athena's to know,
        # and Athena returns every cell as text anyway
        return f"{ref} AS {_label(label)}"
    if glue_type.lower().startswith(("array<", "struct<", "map<")):
        # Trino cannot CAST a complex type to VARCHAR — render it as JSON,
        # dates and timestamps made text first (UIFPE-605); a type we cannot
        # read is selected as it is and Athena prints its own text form
        safe = _json_value(ref, glue_type)
        if safe is None:
            return f"{ref} AS {_label(label)}"
        return f"JSON_FORMAT(CAST({safe[0]} AS JSON)) AS {_label(label)}"
    return f"CAST({ref} AS VARCHAR) AS {_label(label)}"


def counts_query(plan: Plan) -> str:
    # count_if counts TRUE only — a comparison against a left-join NULL is
    # not a match, exactly DataFrame.filter's semantics
    matched = f"count_if({plan.where_sql})" if plan.where_sql else "count(*)"
    return f"{MARKER}\nSELECT count(*) AS total, {matched} AS matched\nFROM {plan.from_sql}"


def _select_query(plan: Plan, limit: int) -> str:
    projection = ",\n       ".join(_render_column(*c) for c in plan.projection)
    where = f"\nWHERE {plan.where_sql}" if plan.where_sql else ""
    return f"{MARKER}\nSELECT {projection}\nFROM {plan.from_sql}{where}\nLIMIT {limit}"


def sample_query(plan: Plan) -> str:
    return _select_query(plan, SAMPLE_ROWS)


def download_query(plan: Plan) -> str:
    """The sample's SELECT with the download cap in place of its 10-row LIMIT
    (UIFPE-490): every matched row, in the preview's columns, written by
    Athena as ONE CSV under our prefix. No ORDER BY — sorting millions of
    rows to cut 100,000 off the top would cost more than the scan itself, so
    a capped file is "some" of the matches, and the UI says so."""
    return _select_query(plan, DOWNLOAD_ROW_CAP)


def missing_query(plan: Plan, spec: ListSpec) -> str:
    """The list's identifiers that match nothing in the list's own table —
    presence does not depend on the joins, and the values appear ONCE (an
    IN-list copy would double the query size)."""
    ref = plan.refs[spec.alias]
    values = ", ".join(f"({_trino_literal(v)})" for v in spec.values)
    return (f"{MARKER}\nWITH wanted(v) AS (VALUES {values})\n"
            f"SELECT w.v FROM wanted AS w\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM {_table(ref)} AS {spec.alias} "
            f"WHERE CAST({spec.alias}.{spec.column} AS VARCHAR) = w.v)\n"
            f"LIMIT {MISSING_CAP + 1}")


def values_query(db: str, table: str, column: str, limit: int) -> str:
    return (f"{MARKER}\nSELECT CAST(t.{column} AS VARCHAR) AS v, count(*) AS n\n"
            f'FROM "{db}"."{table}" AS t\n'
            f"WHERE t.{column} IS NOT NULL\n"
            f"GROUP BY 1\nORDER BY 2 DESC, 1\nLIMIT {limit + 1}")


def profile_query(db: str, table: str, columns: list[str]) -> str:
    """One scan: the row count and an approximate distinct count per scalar
    column — what decides whether an attribute is worth a condition."""
    parts = ",\n       ".join(f'approx_distinct(t.{c}) AS "{c}"' for c in columns)
    return (f"{MARKER}\nSELECT count(*) AS n,\n       {parts}\n"
            f'FROM "{db}"."{table}" AS t')


def _queries(plan: Plan) -> list[tuple[str, str]]:
    return ([("counts", counts_query(plan)), ("sample", sample_query(plan))]
            + [(f"lists[{s.index}]", missing_query(plan, s)) for s in plan.lists])


# ---- Athena calls --------------------------------------------------------------

def _start(query: str) -> str:
    return _client().start_query_execution(
        QueryString=query,
        WorkGroup=_workgroup(),
        ResultConfiguration={"OutputLocation": _output()},
    )["QueryExecutionId"]


def _stop(qid: str) -> None:
    try:
        _client().stop_query_execution(QueryExecutionId=qid)
    except Exception:
        pass  # best effort — the age stop and Athena's own limit are the backstops


def _rows(qid: str, max_results: int) -> tuple[list[str], list[list]]:
    """(columns, rows) with the header row Athena repeats stripped; a
    missing cell is None."""
    r = _client().get_query_results(QueryExecutionId=qid, MaxResults=max_results)
    cols = [c["Name"] for c in r["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    data: list[list] = []
    for i, row in enumerate(r["ResultSet"]["Rows"]):
        vals = [c.get("VarCharValue") for c in row.get("Data", [])]
        if i == 0 and vals == cols:
            continue
        data.append(vals)
    return cols, data


def start_preview(body: dict) -> dict:
    """Plan, then start every execution — counts, sample, one per identifier
    list. Returns {status: running, preview_id, queries} or
    {status: refused, issues}. A start failure stops what was started."""
    if not enabled():
        raise _not_configured()
    plan, issues = build_plan(body)
    if issues:
        return {"status": "refused", "issues": issues}
    started: list[str] = []
    try:
        for _name, query in _queries(plan):
            started.append(_start(query))
    except Exception as e:
        for qid in started:
            _stop(qid)
        if type(e).__name__ == "TooManyRequestsException":
            raise PreviewError(503, "the preview service is busy — try again in a moment")
        raise PreviewError(502, f"Athena refused to start the preview ({type(e).__name__})")
    return {"status": "running",
            "preview_id": ".".join(started),
            "queries": {"counts": started[0], "sample": started[1], "missing": started[2:]},
            "lists": [{"column": f"{s.alias}.{s.column}", "requested": len(s.values)} for s in plan.lists],
            "untransformed": list(plan.untransformed)}


def _parse_id(preview_id: str) -> list[str]:
    if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
        raise PreviewError(404, "unknown preview")
    return preview_id.split(".")


def _fetch(ids: list[str], what: str = "preview") -> list[dict]:
    """The executions behind a preview (or download) id, in id order — and
    the ownership check: every one must carry our marker and write to our
    prefix, or the id is treated as unknown (never read another team's
    primary results)."""
    r = _client().batch_get_query_execution(QueryExecutionIds=ids)
    by_id = {e["QueryExecutionId"]: e for e in r.get("QueryExecutions", [])}
    if r.get("UnprocessedQueryExecutionIds") or any(i not in by_id for i in ids):
        raise PreviewError(404, f"unknown {what}")
    execs = [by_id[i] for i in ids]
    for e in execs:
        location = (e.get("ResultConfiguration") or {}).get("OutputLocation") or ""
        if not str(e.get("Query", "")).startswith(MARKER) or not location.startswith(_output()):
            raise PreviewError(404, f"unknown {what}")
    return execs


def _names(count: int) -> list[str]:
    return ["counts", "sample"] + [f"missing[{i}]" for i in range(count - 2)]


def _submitted(e: dict) -> datetime | None:
    ts = (e.get("Status") or {}).get("SubmissionDateTime")
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def preview_status(preview_id: str) -> dict:
    if not enabled():
        raise _not_configured()
    ids = _parse_id(preview_id)
    execs = _fetch(ids)
    names = _names(len(ids))
    states = [(e.get("Status") or {}).get("State", "UNKNOWN") for e in execs]
    submitted = [t for t in (_submitted(e) for e in execs) if t]
    started_at = min(submitted) if submitted else None
    now = datetime.now(timezone.utc)
    elapsed = round((now - started_at).total_seconds(), 1) if started_at else None
    pending = [ids[i] for i, s in enumerate(states) if s in ("QUEUED", "RUNNING")]

    failed = next((i for i, s in enumerate(states) if s == "FAILED"), None)
    if failed is not None:
        for qid in pending:
            _stop(qid)
        reason = (execs[failed].get("Status") or {}).get("StateChangeReason") or "query failed"
        return {"status": "failed", "error": {"query": names[failed], "message": reason},
                "started_at": started_at.isoformat() if started_at else None,
                "elapsed_s": elapsed, "note": ATHENA_NOTE}
    if any(s == "CANCELLED" for s in states):
        for qid in pending:
            _stop(qid)
        return {"status": "cancelled", "started_at": started_at.isoformat() if started_at else None}
    if pending:
        if elapsed is not None and elapsed > PREVIEW_MAX_AGE_S:
            for qid in pending:
                _stop(qid)
            return {"status": "cancelled",
                    "reason": f"the preview took longer than {PREVIEW_MAX_AGE_S // 60} minutes and was "
                              "stopped — narrow the filter or preview a smaller source",
                    "started_at": started_at.isoformat() if started_at else None}
        return {"status": "running", "started_at": started_at.isoformat() if started_at else None,
                "elapsed_s": elapsed, "queries": dict(zip(names, states))}
    if not all(s == "SUCCEEDED" for s in states):
        return {"status": "failed", "error": {"query": names[0], "message": f"unexpected state {states}"},
                "note": ATHENA_NOTE}

    _, count_rows = _rows(ids[0], 2)
    total = int(count_rows[0][0]) if count_rows and count_rows[0][0] is not None else 0
    matched = int(count_rows[0][1]) if count_rows and len(count_rows[0]) > 1 and count_rows[0][1] is not None else 0
    columns, rows = _rows(ids[1], SAMPLE_ROWS + 1)
    missing = []
    for qid, e in zip(ids[2:], execs[2:]):
        _, m_rows = _rows(qid, MISSING_CAP + 2)
        values = [r[0] for r in m_rows if r]
        m = _MISSING_COLUMN_RE.search(str(e.get("Query", "")))
        missing.append({"column": f"{m.group(1)}.{m.group(2)}" if m else None,
                        "missing": values[:MISSING_CAP],
                        "truncated": len(values) > MISSING_CAP})
    scanned = sum(int((e.get("Statistics") or {}).get("DataScannedInBytes") or 0) for e in execs)
    return {"status": "done", "total": total, "matched": matched,
            "sample": {"columns": columns, "rows": rows[:SAMPLE_ROWS]},
            "missing": missing, "scanned_bytes": scanned,
            "started_at": started_at.isoformat() if started_at else None,
            "elapsed_s": elapsed, "note": ATHENA_NOTE}


def cancel_preview(preview_id: str) -> dict:
    if not enabled():
        raise _not_configured()
    ids = _parse_id(preview_id)
    execs = _fetch(ids)
    names = _names(len(ids))
    stopped = []
    for name, qid, e in zip(names, ids, execs):
        if (e.get("Status") or {}).get("State") in ("QUEUED", "RUNNING"):
            _stop(qid)
            stopped.append(name)
    return {"cancelled": True, "stopped": stopped}


# ---- the matched rows as one CSV (UIFPE-490) ------------------------------------
# Same fences and the same shape as the preview (plan first, Athena as the
# state store, marker + prefix ownership on every read), one execution
# instead of several, and a finished one is answered with a presigned GET on
# the CSV Athena wrote — the only time this API points a browser at S3.

def start_download(body: dict) -> dict:
    """Plan (the preview's own validation) and start the one execution.
    Returns {status: running, download_id, row_cap, columns} or
    {status: refused, issues} in the validator's shape."""
    if not enabled():
        raise _not_configured()
    plan, issues = build_plan(body)
    if issues:
        return {"status": "refused", "issues": issues}
    query = download_query(plan)
    size = len(query.encode("utf-8"))
    if size > MAX_QUERY_BYTES:  # build_plan measured the sample; this differs by its LIMIT only
        return {"status": "refused",
                "issues": [_issue("download", f"the download query is {size // 1024} KB; Athena accepts "
                                              f"{MAX_QUERY_BYTES // 1024} KB — trim the identifier list")]}
    try:
        qid = _start(query)
    except Exception as e:
        if type(e).__name__ == "TooManyRequestsException":
            raise PreviewError(503, "the preview service is busy — try again in a moment")
        raise PreviewError(502, f"Athena refused to start the download ({type(e).__name__})")
    return {"status": "running", "download_id": qid, "row_cap": DOWNLOAD_ROW_CAP,
            "columns": [label for label, _e, _t in plan.projection],
            "untransformed": list(plan.untransformed)}


def _download_execution(download_id: str) -> dict:
    """The execution behind a download id, after the ownership check — and
    one more: it must BE a download (the capped SELECT), so a preview's
    sample id cannot be presigned through this door."""
    if not isinstance(download_id, str) or not DOWNLOAD_ID_RE.fullmatch(download_id):
        raise PreviewError(404, "unknown download")
    (e,) = _fetch([download_id], what="download")
    if not str(e.get("Query", "")).rstrip().endswith(f"\nLIMIT {DOWNLOAD_ROW_CAP}"):
        raise PreviewError(404, "unknown download")
    return e


def _result_object(e: dict, qid: str) -> tuple[str, str]:
    """(bucket, key) of the CSV. A finished execution reports the file
    itself as its OutputLocation; a prefix means the file is <id>.csv under
    it (the fake and the docs' wording) — both end at the same object."""
    location = (e.get("ResultConfiguration") or {}).get("OutputLocation") or ""
    if location.endswith("/"):
        location = f"{location}{qid}.csv"
    bucket, _, key = location.removeprefix("s3://").partition("/")
    return bucket, key


def _download_filename(e: dict, started_at: datetime | None) -> str:
    m = _DRIVING_TABLE_RE.search(str(e.get("Query", "")))
    table = m.group(1) if m else "gold"
    stamp = (started_at or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%SZ")
    return f"beacon-{table}-matched-rows-{stamp}.csv"


def download_status(download_id: str) -> dict:
    if not enabled():
        raise _not_configured()
    e = _download_execution(download_id)
    state = (e.get("Status") or {}).get("State", "UNKNOWN")
    started_at = _submitted(e)
    now = datetime.now(timezone.utc)
    elapsed = round((now - started_at).total_seconds(), 1) if started_at else None
    base = {"started_at": started_at.isoformat() if started_at else None, "elapsed_s": elapsed,
            "row_cap": DOWNLOAD_ROW_CAP}

    if state == "FAILED":
        reason = (e.get("Status") or {}).get("StateChangeReason") or "query failed"
        return {**base, "status": "failed", "error": {"query": "download", "message": reason},
                "note": ATHENA_NOTE}
    if state == "CANCELLED":
        return {**base, "status": "cancelled"}
    if state in ("QUEUED", "RUNNING"):
        if elapsed is not None and elapsed > PREVIEW_MAX_AGE_S:
            _stop(download_id)
            return {**base, "status": "cancelled",
                    "reason": f"the download took longer than {PREVIEW_MAX_AGE_S // 60} minutes and was "
                              "stopped — narrow the filter or download a smaller source"}
        return {**base, "status": "running"}
    if state != "SUCCEEDED":
        return {**base, "status": "failed", "error": {"query": "download", "message": f"unexpected state {state}"},
                "note": ATHENA_NOTE}

    bucket, key = _result_object(e, download_id)
    filename = _download_filename(e, started_at)
    s3 = _s3_client()
    size = None
    try:  # best effort — a missing size never hides the link
        size = int(s3.head_object(Bucket=bucket, Key=key).get("ContentLength"))
    except Exception:
        pass
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key,
                # S3 sets the header, so the browser saves the file under a
                # readable name instead of the execution id
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": "text/csv"},
        ExpiresIn=DOWNLOAD_LINK_TTL_S)
    scanned = int((e.get("Statistics") or {}).get("DataScannedInBytes") or 0)
    return {**base, "status": "done", "url": url, "expires_seconds": DOWNLOAD_LINK_TTL_S,
            "filename": filename, "bytes": size, "scanned_bytes": scanned, "note": ATHENA_NOTE}


def cancel_download(download_id: str) -> dict:
    if not enabled():
        raise _not_configured()
    e = _download_execution(download_id)
    pending = (e.get("Status") or {}).get("State") in ("QUEUED", "RUNNING")
    if pending:
        _stop(download_id)
    return {"cancelled": True, "stopped": ["download"] if pending else []}


# ---- shared helpers: the table behind a source, the in-request poll -----------

def _values_reason(base: dict, reason: str) -> dict:
    return {**base, "reason": reason}


def _gold_table(source_id: str) -> tuple[str, str] | None:
    """(db, table) of a source that is a Gold catalog table, else None."""
    row = next((s for s in registries.sources() if s["id"] == source_id), None) \
        or registries._gold_source(source_id)
    parts = str((row or {}).get("path", "")).split(".")
    if (row or {}).get("kind") != "iceberg" or len(parts) != 3 or parts[0] != "glue_catalog":
        return None
    _, db, table = parts
    if not DB_RE.fullmatch(db) or not DB_RE.fullmatch(table):
        return None
    return db, table


def _await_sync(qid: str, cap_s: int, what: str) -> str | None:
    """Poll one execution inside the request (0.5 s, then 1 s) up to cap_s.
    None on success; otherwise the reason it did not answer (the execution
    is stopped on the cap)."""
    athena = _client()
    deadline = time.monotonic() + cap_s
    polls = 0
    while True:
        state = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if state["State"] == "SUCCEEDED":
            return None
        if state["State"] in ("FAILED", "CANCELLED"):
            return f"Athena could not {what} ({state.get('StateChangeReason') or state['State']})"
        if time.monotonic() > deadline:
            _stop(qid)
            return f"{what} took longer than {cap_s} s — type the value instead"
        polls += 1
        time.sleep(0.5 if polls <= 10 else 1.0)


# ---- the cardinality profile (UIFPE-514) ---------------------------------------
# One execution per table, the download's shape (Athena as the state store,
# marker + prefix ownership on every read, the age stop) — and the result
# OUTLIVES the execution: a finished profile is written as JSON under the
# preview prefix and served from there for a day, so every Lambda instance
# and every author sees the same numbers and the table is scanned once a
# day, not once per instance. No per-instance cache: S3 is the one store.

def _profile_key(db: str, table: str) -> str:
    return f"{_prefix()}/{PROFILE_PREFIX}/{db}.{table}.json"


def _fresh(obj: dict) -> bool:
    """Persisted within PROFILE_PERSIST_TTL_S; an unreadable sampled_at is stale."""
    try:
        ts = datetime.fromisoformat(str(obj.get("sampled_at")))
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() <= PROFILE_PERSIST_TTL_S


def _read_profile(db: str, table: str) -> dict | None:
    """The fresh persisted profile of one table, or None — a missing object,
    a read failure, malformed JSON and a stale profile all count as none."""
    try:
        body = _s3_client().get_object(Bucket=os.environ["BEACON_DATA_BUCKET"],
                                       Key=_profile_key(db, table))["Body"].read()
        obj = json.loads(body)
    except Exception:
        return None
    return obj if isinstance(obj, dict) and _fresh(obj) else None


def _write_profile(db: str, table: str, obj: dict) -> bool:
    """Best effort: a profile that could not be persisted is still answered
    (the next GET finds it absent and the wizard starts another scan)."""
    try:
        _s3_client().put_object(Bucket=os.environ["BEACON_DATA_BUCKET"], Key=_profile_key(db, table),
                                Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")
        return True
    except Exception:
        return False


def _profile_done(source_id: str, obj: dict, persisted: bool) -> dict:
    # source_id is the caller's: a registry row and the catalog can name the
    # same table differently, and the object is keyed by the table
    return {"status": "done", **obj, "source_id": source_id, "persisted": persisted}


def _profile_unavailable(source_id: str, reason: str) -> dict:
    return {"status": "unavailable", "source_id": source_id, "reason": reason}


def _profile_target(source_id: str) -> tuple[tuple[str, str, list[str]] | None, dict | None]:
    """((db, table, scalar columns), None) when the table can be profiled,
    else (None, the `unavailable` answer). 404 for an unknown source. The
    schema must be LIVE: the registry fallback cannot say which columns are
    scalar, and the profile reads the same catalog Athena does."""
    live = registries.source_columns(source_id)
    if "error" in live:
        raise PreviewError(404, live["error"])
    if not enabled():
        return None, _profile_unavailable(source_id, "live profiling is not configured on this environment")
    located = _gold_table(source_id)
    if located is None:   # before the live check: a CSV source has no live catalog schema either
        return None, _profile_unavailable(source_id, "only Gold (iceberg) tables are profiled")
    if live.get("provenance") != "live" or not live.get("columns"):
        return None, _profile_unavailable(source_id, "live schema unavailable — the profile reads the same "
                                                     "catalog Athena does; try again shortly")
    types = live.get("types") or {}
    scalar = [c for c in live["columns"]
              if COLUMN_RE.fullmatch(c)
              and not str(types.get(c, "")).lower().startswith(("array<", "struct<", "map<"))]
    if not scalar:
        return None, _profile_unavailable(source_id, "no scalar columns to profile")
    return (*located, scalar), None


def column_profile(source_id: str) -> dict:
    """GET: the persisted profile when a fresh one exists ({status: done,
    persisted: true}), {status: absent} when none does, {status:
    unavailable, reason} when the table cannot be profiled at all. NEVER
    starts Athena — that is POST's job, so a page load costs a GET on S3."""
    target, unavailable = _profile_target(source_id)
    if unavailable:
        return unavailable
    db, table, _scalar = target
    obj = _read_profile(db, table)
    if obj is None:
        return {"status": "absent", "source_id": source_id}
    return _profile_done(source_id, obj, persisted=True)


def start_profile(source_id: str) -> dict:
    """POST: {status: done, ...} from the persisted object when it is fresh
    (idempotent — the wizard fires this on every source pick), else start
    the one execution and answer {status: running, profile_id}. 503 when the
    preview is not configured, like the preview routes; unavailable cases
    answer 200 with the reason."""
    if not enabled():
        raise _not_configured()
    target, unavailable = _profile_target(source_id)
    if unavailable:
        return unavailable
    db, table, scalar = target
    obj = _read_profile(db, table)
    if obj is not None:
        return _profile_done(source_id, obj, persisted=True)
    try:
        qid = _start(profile_query(db, table, scalar))
    except Exception as e:
        if type(e).__name__ == "TooManyRequestsException":
            raise PreviewError(503, "the preview service is busy — try again in a moment")
        raise PreviewError(502, f"Athena refused to start the profile ({type(e).__name__})")
    return {"status": "running", "source_id": source_id, "profile_id": qid}


def _profile_execution(profile_id: str, db: str, table: str) -> dict:
    """The execution behind a profile id, after the ownership check — and
    two more: it must BE a profile (approx_distinct) of THIS table, so a
    preview's or a download's id, or another table's profile, is unknown
    through this door."""
    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise PreviewError(404, "unknown profile")
    (e,) = _fetch([profile_id], what="profile")
    query = str(e.get("Query", ""))
    m = _PROFILE_TABLE_RE.search(query)
    if "approx_distinct(" not in query or m is None or (m.group(1), m.group(2)) != (db, table):
        raise PreviewError(404, "unknown profile")
    return e


def _profile_columns(cols: list[str], row: list) -> tuple[int, dict]:
    """(rows, {column: {distinct, unique_like, identifier}}) from the one
    result row — the columns come from the result itself, so a poll needs
    no catalog read."""
    got = dict(zip(cols, row))
    n = int(got.get("n") or 0)
    columns = {}
    for c in cols:
        if c == "n":
            continue
        raw = got.get(c)
        distinct = int(raw) if raw not in (None, "") else None
        columns[c] = {
            "distinct": distinct,
            "unique_like": bool(n and distinct is not None and distinct >= PROFILE_UNIQUE_RATIO * n),
            "identifier": bool(IDENTIFIER_COLUMN_RE.fullmatch(c)),
        }
    return n, columns


def profile_status(source_id: str, profile_id: str) -> dict:
    """GET /profile/{id}: running | done | failed | cancelled. The first
    poll that sees SUCCEEDED reads the one result row, persists the profile
    and answers it; a later poll does the same (identical content) — the
    wizard stops polling on done."""
    if not enabled():
        raise _not_configured()
    located = _gold_table(source_id)
    if located is None:
        raise PreviewError(404, f"no profile for source {source_id!r}")
    db, table = located
    e = _profile_execution(profile_id, db, table)
    state = (e.get("Status") or {}).get("State", "UNKNOWN")
    started_at = _submitted(e)
    now = datetime.now(timezone.utc)
    elapsed = round((now - started_at).total_seconds(), 1) if started_at else None
    base = {"source_id": source_id, "started_at": started_at.isoformat() if started_at else None,
            "elapsed_s": elapsed}

    if state == "FAILED":
        reason = (e.get("Status") or {}).get("StateChangeReason") or "query failed"
        return {**base, "status": "failed", "error": {"query": "profile", "message": reason}}
    if state == "CANCELLED":
        return {**base, "status": "cancelled"}
    if state in ("QUEUED", "RUNNING"):
        if elapsed is not None and elapsed > PREVIEW_MAX_AGE_S:
            _stop(profile_id)
            return {**base, "status": "cancelled",
                    "reason": f"measuring took longer than {PREVIEW_MAX_AGE_S // 60} minutes and was "
                              "stopped — text attributes are offered without value counts"}
        return {**base, "status": "running"}
    if state != "SUCCEEDED":
        return {**base, "status": "failed", "error": {"query": "profile", "message": f"unexpected state {state}"}}

    cols, rows = _rows(profile_id, 2)
    if not rows:
        return {**base, "status": "failed", "error": {"query": "profile", "message": "the profile came back empty"}}
    n, columns = _profile_columns(cols, rows[0])
    obj = {"source_id": source_id, "db": db, "table": table, "rows": n, "columns": columns,
           "sampled_at": now.isoformat(), "query_execution_id": profile_id}
    persisted = _write_profile(db, table, obj)
    scanned = int((e.get("Statistics") or {}).get("DataScannedInBytes") or 0)
    return {**base, **_profile_done(source_id, obj, persisted), "scanned_bytes": scanned}


# ---- value suggestions (synchronous) ---------------------------------------------

def column_values(source_id: str, column: str, limit: int = VALUES_LIMIT) -> dict:
    """The `limit` most common non-null values of one column, synchronously
    (Athena answers a single-column aggregate in seconds). Suggestions are a
    nicety, so every way this cannot answer is a `reason`, never an error —
    except an unknown source or column, which is the caller's mistake."""
    if not COLUMN_RE.fullmatch(column or ""):
        raise PreviewError(422, "column must be a plain identifier")
    limit = max(1, min(int(limit), VALUES_LIMIT))
    live = registries.source_columns(source_id)
    if "error" in live:
        raise PreviewError(404, live["error"])
    base = {"source_id": source_id, "column": column, "values": [], "counts": [],
            "truncated": False, "cached": False}
    if not enabled():
        return _values_reason(base, "live suggestions are not configured on this environment")
    if live.get("provenance") != "live" or not live.get("columns"):
        return _values_reason(base, "live schema unavailable — suggestions read the same catalog Athena does")
    true = next((c for c in live["columns"] if c == column), None) \
        or next((c for c in live["columns"] if c.lower() == column.lower()), None)
    if true is None:
        raise PreviewError(404, f"column {column!r} not found in source {source_id!r}")
    if IDENTIFIER_COLUMN_RE.fullmatch(true):
        return _values_reason(base, "identifiers are unique per product — paste them as an identifier list instead")
    glue_type = str((live.get("types") or {}).get(true, "")).lower()
    if glue_type.startswith(("array<", "struct<", "map<")):
        return _values_reason(base, "a structured column has no value list — use contains / is empty")
    if glue_type.startswith(("date", "timestamp")):
        return _values_reason(base, "dates are picked, not suggested")

    located = _gold_table(source_id)
    if located is None:
        return _values_reason(base, "only Gold (iceberg) tables have live suggestions")
    db, table = located

    # UIFPE-514: the persisted profile, when there is one, says whether a
    # value list means anything — over the cap it is descriptions and URLs,
    # and the picker does not offer the column either. No profile = proceed
    # as before (the profile is a nicety the wizard starts, not a gate).
    profile = _read_profile(db, table)
    distinct = ((profile or {}).get("columns") or {}).get(true, {}).get("distinct") if profile else None
    if isinstance(distinct, int) and distinct > TEXT_CARDINALITY_CAP:
        return _values_reason(base, f"more than {TEXT_CARDINALITY_CAP:,} distinct values — type the value, "
                                    "or use Custom Spark SQL")

    key = (source_id, true, limit)
    hit = _values_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return {**hit[1], "cached": True}

    qid = _start(values_query(db, table, true, limit))
    reason = _await_sync(qid, VALUES_POLL_CAP_S, "read the column")
    if reason:
        return _values_reason(base, reason)
    _, rows = _rows(qid, limit + 2)
    values = [r[0] for r in rows if r and r[0] is not None]
    counts = [int(r[1]) if len(r) > 1 and r[1] is not None else 0 for r in rows if r and r[0] is not None]
    result = {**base, "values": values[:limit], "counts": counts[:limit],
              "truncated": len(values) > limit,
              "sampled_at": datetime.now(timezone.utc).isoformat()}
    _values_cache[key] = (time.monotonic() + VALUES_CACHE_TTL_S, result)
    return result
