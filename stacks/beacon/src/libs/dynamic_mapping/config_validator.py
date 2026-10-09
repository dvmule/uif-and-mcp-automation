"""Static validation of a mapping config — runs before any SparkSession activity.

Three layers:
  validate_structure(config)             — required fields, unique names, known references
  validate_expressions(config)           — every expression string parsed with sqlglot (Spark
                                           dialect): scalar expressions only, no subqueries,
                                           functions restricted to a curated allowlist
  validate_references(config, schemas)   — column references checked against real source
                                           schemas (needs sources loaded, but no data read)
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from . import xml_paths
from .errors import Issue
from .models import MappingConfig

RULE_TYPES = ("one_to_one", "many_to_one", "one_to_many", "literal", "derived")
JOIN_TYPES = ("inner", "left", "right", "full", "left_semi", "left_anti", "cross")

# Curated allowlist of Spark SQL functions permitted in config expressions.
# Anything not listed is rejected at config load time.
ALLOWED_FUNCTIONS = frozenset({
    # string
    "concat", "concat_ws", "split", "split_part", "trim", "ltrim", "rtrim",
    "upper", "lower", "initcap", "substring", "substr", "length", "lpad", "rpad",
    "replace", "regexp_replace", "regexp_extract", "format_number",
    # null handling / conditional
    "coalesce", "nvl", "nullif", "ifnull", "if", "case",
    # casting / dates
    "cast", "try_cast", "to_date", "to_timestamp", "date_format", "date_add",
    "date_sub", "datediff", "year", "month", "day", "dayofmonth", "hour",
    "minute", "second", "current_date", "current_timestamp", "unix_timestamp",
    # numeric
    "abs", "round", "floor", "ceil", "ceiling", "greatest", "least", "pow", "power",
    # hashing (masking / keys)
    "sha2", "md5", "hash",
    # collections
    "size", "element_at", "array_contains",
    # json — serialize a complex (array/struct) column into ONE JSON string
    # column: the CSV-safe way to ship a gold array attribute (Spark's csv
    # writer throws on complex types, UIFPE-237). sqlglot canonicalizes
    # Spark's to_json to JSONFormat (sql_name "json_format"); _func_names
    # also collects the Spark re-render ("TO_JSON(...)"), which is what
    # matches this entry. from_json stays off the list on purpose.
    "to_json",
})

_STRUCT_KIND = "structure"

# One filter entry's size ceiling. Not a Spark limit — Catalyst folds a large
# IN list into an InSet — but a draft/version is stored whole as ONE DynamoDB
# item (400 KB), and a filter this size is a pasted file, not a predicate.
# ~12,000 ISBN-13s; the wizard's own 5,000-values-per-condition rule sits
# well inside it even for 20-character identifiers.
MAX_FILTER_BYTES = 200 * 1024


def validate_structure(config: MappingConfig) -> list[Issue]:
    issues: list[Issue] = []

    if not config.sources:
        issues.append(Issue("sources", "at least one source is required"))
    names = [s.name for s in config.sources]
    for dup in {n for n in names if names.count(n) > 1}:
        issues.append(Issue("sources", f"duplicate source name {dup!r}"))

    known = set(names)
    for i, j in enumerate(config.joins):
        if j.left not in known:
            issues.append(Issue(f"joins[{i}]", f"unknown left source {j.left!r}"))
        if j.right not in known:
            issues.append(Issue(f"joins[{i}]", f"unknown right source {j.right!r}"))
        if j.type not in JOIN_TYPES:
            issues.append(Issue(f"joins[{i}]", f"unknown join type {j.type!r}"))
        if not j.condition and j.type != "cross":
            issues.append(Issue(f"joins[{i}]", "join condition is required"))
        # UIFPE-167: the engine's crossJoin ignores a condition — one that is
        # written down but never applied would mislead every reader of the config
        if j.condition and j.type == "cross":
            issues.append(Issue(f"joins[{i}]", "cross join takes no condition — remove "
                                               "it or choose another join type"))

    issues.extend(_list_step_issues(config))   # UIFPE-580
    issues.extend(_join_chain_issues(config))

    if not config.mappings:
        issues.append(Issue("mappings", "at least one mapping rule is required"))

    seen_targets: set[str] = set()
    for i, m in enumerate(config.mappings):
        where = f"mappings[{i}]"
        if m.type not in RULE_TYPES:
            issues.append(Issue(where, f"unknown rule type {m.type!r}"))
            continue
        issues.extend(Issue(where, msg) for msg in _rule_field_issues(m))
        for t in m.target_names():
            if t in seen_targets:
                issues.append(Issue(where, f"duplicate target column {t!r}"))
            seen_targets.add(t)

    # UIFPE-557: path-shaped targets ("/" nests XML elements, "@" names an
    # attribute) must parse and must not create mixed content. A config of
    # plain names returns [] immediately — every pre-feature config is one.
    issues.extend(Issue("mappings", msg)
                  for msg in xml_paths.path_issues(sorted(seen_targets)))

    for i, f in enumerate(config.filters):
        if not f or not f.strip():
            issues.append(Issue(f"filters[{i}]", "filter condition must be a non-empty string"))
        elif len(f.encode("utf-8")) > MAX_FILTER_BYTES:
            issues.append(Issue(f"filters[{i}]",
                                f"filter is {len(f.encode('utf-8')) // 1024} KB, over the "
                                f"{MAX_FILTER_BYTES // 1024} KB limit — narrow the identifier "
                                "list or filter on an attribute instead"))

    val_names = [v.name for v in config.validations]
    for dup in {n for n in val_names if val_names.count(n) > 1}:
        issues.append(Issue("validations", f"duplicate validation name {dup!r}"))
    if config.validations and not config.rejected_path:
        issues.append(Issue("rejected_path", "required when validations are defined"))

    if not config.output_targets:
        issues.append(Issue("output.targets", "at least one output target is required"))

    if config.policies.on_empty_input not in ("fail", "skip", "continue"):
        issues.append(Issue("policies.on_empty_input",
                            f"must be fail|skip|continue, got {config.policies.on_empty_input!r}"))

    return issues


_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _list_step_issues(config: MappingConfig) -> list[Issue]:
    """UIFPE-580: every `through` / `unnest` step names a list as
    `source.column` on a declared source and an element alias that is a plain
    identifier, is not a source name, and is used once. A cross join cannot
    carry a `through` (spreading a list only makes sense with a condition
    that matches its element)."""
    issues: list[Issue] = []
    names = set(config.source_names())
    seen: set[str] = set()
    for where, step in config.list_steps():
        source, column = step.source_column()
        if not source or not column or "." in column:
            issues.append(Issue(where, f"list must be written source.column, got {step.list!r}"))
        elif source not in names:
            issues.append(Issue(where, f"list names unknown source {source!r}"))
        if not _ALIAS_RE.match(step.alias):
            issues.append(Issue(where, f"alias {step.alias!r} is not a plain identifier"))
        elif step.alias in names:
            issues.append(Issue(where, f"alias {step.alias!r} is already a source name"))
        elif step.alias in seen:
            issues.append(Issue(where, f"alias {step.alias!r} is used by another list step"))
        seen.add(step.alias)
    for i, j in enumerate(config.joins):
        if j.through and j.type == "cross":
            issues.append(Issue(f"joins[{i}]", "a cross join cannot go through a list — "
                                               "give it a condition on the element"))
    return issues


def _join_chain_issues(config: MappingConfig) -> list[Issue]:
    """UIFPE-167: joins fold in declaration order onto the frame seeded by
    joins[0].left (engine.apply_joins), so a join may only attach to — and
    its condition may only reference — sources already in that frame, and
    every declared source must be brought in by exactly one join. Anything
    else is a Spark AnalysisException after the scan, or a source silently
    missing from the output. Structural only: validate_references checks the
    columns themselves, so an unknown left/right name (already reported
    above) short-circuits this pass rather than double-reporting."""
    names = set(config.source_names())
    if len(config.sources) > 1 and not config.joins:
        return [Issue("joins", f"{len(config.sources)} sources need "
                               f"{len(config.sources) - 1} join(s) — none declared")]
    if not config.joins or any(j.left not in names or j.right not in names
                               for j in config.joins):
        return []

    issues: list[Issue] = []
    joined = {config.joins[0].left}
    elements = set(config.element_aliases())
    introduced: set[str] = set()   # UIFPE-580: element aliases spread so far
    for i, j in enumerate(config.joins):
        where = f"joins[{i}]"
        if i > 0 and j.left not in joined:
            issues.append(Issue(where, f"left source {j.left!r} is not joined yet — left "
                                       "must name a source already in the joined frame "
                                       "(joins fold in declaration order)"))
        if j.right in joined:
            issues.append(Issue(where, f"source {j.right!r} is already joined"))
        if j.through:
            list_source, _ = j.through.source_column()
            if list_source in names and list_source not in joined:
                issues.append(Issue(f"{where}.through",
                                    f"list is on source {list_source!r}, which is not joined "
                                    "yet — the list must already be in the frame"))
            introduced.add(j.through.alias)
        for table in _condition_tables(j.condition):
            if table in names and table not in joined and table != j.right:
                issues.append(Issue(f"{where}.condition",
                                    f"references source {table!r} before it is joined "
                                    "— reorder the joins"))
            if table in elements and table not in introduced:
                issues.append(Issue(f"{where}.condition",
                                    f"references list element {table!r} before its list is "
                                    "spread — an unnest step runs after every join"))
        joined.add(j.right)

    missing = names - joined
    if missing:
        issues.append(Issue("joins", f"source(s) {', '.join(sorted(missing))} are never "
                                     "joined"))
    return issues


def _condition_tables(condition: str) -> list[str]:
    """Distinct table qualifiers referenced by a condition, in order of first
    appearance; [] when empty or unparseable (validate_expressions reports)."""
    if not condition:
        return []
    try:
        tree = sqlglot.parse_one(f"SELECT {condition}", read="spark")
    except sqlglot.errors.SqlglotError:  # incl. TokenError — reported by check_expression
        return []
    seen: list[str] = []
    for col in tree.find_all(exp.Column):
        if col.table and col.table not in seen:
            seen.append(col.table)
    return seen


def _rule_field_issues(m) -> list[str]:
    match m.type:
        case "one_to_one":
            need = [("source", m.source), ("target", m.target)]
        case "many_to_one":
            need = [("sources", m.sources), ("target", m.target), ("expression", m.expression)]
        case "one_to_many":
            need = [("source", m.source), ("targets", m.targets), ("expressions", m.expressions)]
        case "literal":
            need = [("target", m.target)]
        case "derived":
            need = [("target", m.target), ("expression", m.expression)]
        case _:
            return []
    msgs = [f"{m.type} rule requires {field!r}" for field, v in need if not v]
    if m.type == "one_to_many" and m.targets and m.expressions \
            and len(m.targets) != len(m.expressions):
        msgs.append(f"targets ({len(m.targets)}) and expressions "
                    f"({len(m.expressions)}) must be the same length")
    if m.type == "literal" and m.value is None:
        msgs.append("literal rule requires 'value'")
    return msgs


def validate_expressions(config: MappingConfig) -> list[Issue]:
    issues: list[Issue] = []
    for i, j in enumerate(config.joins):
        if j.condition:
            issues.extend(Issue(f"joins[{i}].condition", msg)
                          for msg in check_expression(j.condition))
    for i, m in enumerate(config.mappings):
        for expr in ([m.expression] if m.expression else list(m.expressions)):
            issues.extend(Issue(f"mappings[{i}]", msg) for msg in check_expression(expr))
    for i, f in enumerate(config.filters):
        if f and f.strip():
            issues.extend(Issue(f"filters[{i}]", msg) for msg in check_expression(f))
    for i, v in enumerate(config.validations):
        issues.extend(Issue(f"validations[{i}].condition", msg)
                      for msg in check_expression(v.condition))
    return issues


def check_expression(expr: str) -> list[str]:
    """Statically check one expression string. Returns human-readable problems.

    Wraps the expression in SELECT so sqlglot parses it in expression position:
    a statement smuggled in ("1; DROP TABLE x") parses as multiple statements,
    and "a UNION SELECT b" turns the root into a set operation — both rejected.
    """
    # UIFPE-237: sqlglot parses Presto's json_format() to the same node as
    # Spark's to_json() (both become exp.JSONFormat), so the allowlist alone
    # would admit a spelling Spark cannot execute — reject the foreign
    # spelling here, before it costs a failed run.
    if re.search(r"\bjson_format\s*\(", expr, re.IGNORECASE):
        return ["json_format is not a Spark function — use to_json(...)"]

    try:
        statements = sqlglot.parse(f"SELECT {expr}", read="spark")
    except sqlglot.errors.ParseError as e:
        return [f"not parseable as a Spark SQL expression: {e.errors[0]['description']}"]
    except sqlglot.errors.SqlglotError as e:
        # TokenError (an unbalanced or unterminated quote — the likeliest
        # author typo) is NOT a ParseError; uncaught it was an HTTP 500 out
        # of /validate and /submit instead of an issue the author can act on
        return [f"not parseable as a Spark SQL expression: {e} — check the quotes "
                "(a literal apostrophe is written \\')"]

    if len(statements) != 1:
        return ["multiple statements are not allowed"]
    root = statements[0]
    if isinstance(root, (exp.Union, exp.Intersect, exp.Except)):
        return ["subqueries and set operations are not allowed"]
    if not isinstance(root, exp.Select):
        return [f"must be a scalar expression, not a {root.key.upper()} statement"]
    if len(root.expressions) != 1:
        return ["must be a single expression (no commas at the top level)"]

    problems: list[str] = []
    for node in root.walk():
        if node is root:
            continue
        if isinstance(node, (exp.Select, exp.Subquery, exp.Union, exp.Intersect, exp.Except)):
            problems.append("subqueries and set operations are not allowed")
            break

    # an empty `IN ()` parses here but Spark refuses it at run time — the
    # wizard's builder never emits one; a hand-edited config might
    for node in root.find_all(exp.In):
        if not node.expressions and not any(node.args.get(k) for k in ("query", "unnest", "field")):
            problems.append("IN list is empty — Spark rejects `IN ()`; add a value or remove "
                            "the condition")

    for func in root.find_all(exp.Func):
        # sqlglot models AND/OR as both Connector and Func (function-call
        # syntax exists in some dialects); written as operators in a
        # condition they are not function calls — never allowlist-checked.
        if isinstance(func, exp.Connector):
            continue
        names = _func_names(func)
        if not names & ALLOWED_FUNCTIONS:
            problems.append(f"function {min(names)!r} is not on the allowlist")

    return problems


def _func_names(func: exp.Func) -> set[str]:
    """Candidate names for a parsed function node.

    sqlglot canonicalizes some Spark functions (split -> regexp_split), so we
    check both the canonical name and the name as re-rendered in Spark SQL."""
    if isinstance(func, exp.Anonymous):
        return {func.name.lower()}
    names = {func.sql_name().lower()}
    rendered = func.sql(dialect="spark")
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", rendered)
    if m:
        names.add(m.group(1).lower())
    return names


def _source_column(col: exp.Column) -> tuple[str | None, str]:
    """The (source, column) a parsed column reference names; source is None
    when unqualified.

    UIFPE-567: a member of a struct (or array-of-struct) column is written
    alias.column.member, which sqlglot parses as db.table.column — so the
    struct name landed in the source slot, every such reference was refused
    as an unknown source, and collect_references never kept the struct column,
    so the run would have pruned it away before the filter. A config
    reference has no catalog or database part, so with three or more parts
    the first is the source, the second the column, and the rest a path into
    it that Spark resolves itself.
    """
    if not col.table:
        return None, col.name
    parts = [p.name for p in col.parts]
    if len(parts) > 2:
        return parts[0], parts[1]
    return col.table, col.name


def collect_references(config: MappingConfig, schemas: dict[str, list[str]]) -> dict[str, list[str]]:
    """The columns each source must retain for this config to run — the same
    walk validate_references checks, collecting instead of verifying: join
    conditions, filters, mapping sources and expressions. Validations are
    excluded — they run on the mapped frame and reference target names.

    Names resolve case-insensitively (Spark's resolver) and come back as the
    schema's TRUE column names, in schema order, so a projection built from
    them is deterministic. An unqualified expression column cannot be
    attributed to one source, so it is kept on every source that has it.
    Unknown sources/columns are skipped — validate_references reports those.
    """
    wanted: dict[str, set[str]] = {name: set() for name in schemas}

    def add(table: str | None, column: str) -> None:
        tables = [table] if table else list(schemas)
        for t in tables:
            matches = [c for c in schemas.get(t, []) if c.lower() == column.lower()]
            if len(matches) == 1:
                wanted[t].add(matches[0])

    def add_expr(expr: str) -> None:
        try:
            tree = sqlglot.parse_one(f"SELECT {expr}", read="spark")
        except sqlglot.errors.SqlglotError:  # incl. TokenError — reported by check_expression
            return
        for col in tree.find_all(exp.Column):
            add(*_source_column(col))

    # UIFPE-580: a spread list column must survive pruning — references to
    # its ELEMENT alias name nothing on the source itself
    for _where, step in config.list_steps():
        add(*step.source_column())
    for j in config.joins:
        if j.condition:
            add_expr(j.condition)
    for f in config.filters:
        if f and f.strip():
            add_expr(f)
    for m in config.mappings:
        for ref in ([m.source] if m.source else []) + list(m.sources):
            if "." in ref:
                add(*ref.split(".", 1))
        for expr in ([m.expression] if m.expression else list(m.expressions)):
            add_expr(expr)

    # sources only: `schemas` may also carry element aliases (UIFPE-580),
    # which own no columns to prune
    sources = set(config.source_names())
    return {name: [c for c in schemas[name] if c in wanted[name]]
            for name in schemas if name in sources}


def validate_references(config: MappingConfig, schemas: dict[str, list[str]]) -> list[Issue]:
    """Check table-qualified column references against actual source schemas.

    `schemas` maps source name -> list of column names (from df.columns —
    no data is read). Validations run post-mapping, so their (unqualified)
    columns are checked against mapping targets instead.

    Membership is CASE-INSENSITIVE, matching Spark's resolver
    (spark.sql.caseSensitive defaults to false): the Glue catalog serves
    lowercased Iceberg column names to config authors, so a picker-supplied
    'organizationcode' must resolve against the table's true
    'organizationCode' (UIFPE-165). A name matching several columns that
    differ only by case is rejected as ambiguous — Spark would throw at
    execution.
    """
    issues: list[Issue] = []

    elements = set(config.element_aliases())

    def check_ref(where: str, ref: str) -> None:
        if "." not in ref:
            issues.append(Issue(where, f"reference {ref!r} must be table-qualified (source.column)"))
            return
        table, column = ref.split(".", 1)
        if table not in schemas:
            if table in elements:
                # UIFPE-580: the alias is declared but the caller could not
                # read the list column's element type — say that, not
                # "unknown source"
                issues.append(Issue(where, f"list element {table!r} has no known fields — its "
                                           "list column's element type could not be read"))
            else:
                issues.append(Issue(where, f"unknown source {table!r} in reference {ref!r}"))
            return
        cols = schemas[table]
        if column in cols:
            return
        matches = [c for c in cols if c.lower() == column.lower()]
        if len(matches) > 1:
            issues.append(Issue(where, f"column {column!r} is ambiguous in source {table!r} "
                                       f"(matches: {', '.join(sorted(matches))})"))
        elif not matches:
            # UIFPE-568: Gold columns were renamed camelCase -> snake_case
            # (N8, 2026-09-12); a config authored before that still says
            # `itemType`. Spark will not resolve it against `item_type`, so
            # it stays refused — but name the column it became, rather than
            # leaving the author to spot the underscore in a 60-column list.
            renamed = [c for c in cols if c.replace("_", "").lower() == column.replace("_", "").lower()]
            if len(renamed) == 1:
                issues.append(Issue(where, f"column {column!r} not found in source {table!r} — "
                                           f"Gold columns are snake_case now: did you mean "
                                           f"{renamed[0]!r}?"))
            else:
                issues.append(Issue(where, f"column {column!r} not found in source {table!r} "
                                           f"(has: {', '.join(sorted(cols))})"))

    def check_expr_columns(where: str, expr: str) -> None:
        try:
            tree = sqlglot.parse_one(f"SELECT {expr}", read="spark")
        except sqlglot.errors.SqlglotError:  # incl. TokenError — reported by check_expression
            return  # already reported by validate_expressions
        for col in tree.find_all(exp.Column):
            table, column = _source_column(col)   # UIFPE-567: struct members
            if table:
                check_ref(where, f"{table}.{column}")

    # UIFPE-580: the list a step spreads must be a real column of its source
    for where, step in config.list_steps():
        check_ref(f"{where}.list", step.list)

    for i, j in enumerate(config.joins):
        if j.condition:
            check_expr_columns(f"joins[{i}].condition", j.condition)

    # filters run on the joined frame, pre-mapping: columns must be
    # table-qualified source references, same as join conditions
    for i, f in enumerate(config.filters):
        check_expr_columns(f"filters[{i}]", f)

    for i, m in enumerate(config.mappings):
        where = f"mappings[{i}]"
        if m.type == "one_to_one" and m.source:
            check_ref(where, m.source)
        for src in m.sources:
            check_ref(where, src)
        for expr in ([m.expression] if m.expression else list(m.expressions)):
            check_expr_columns(where, expr)

    targets = {t.lower() for m in config.mappings for t in m.target_names()}
    for i, v in enumerate(config.validations):
        where = f"validations[{i}].condition"
        try:
            tree = sqlglot.parse_one(f"SELECT {v.condition}", read="spark")
        except sqlglot.errors.SqlglotError:  # incl. TokenError — reported by check_expression
            continue
        for col in tree.find_all(exp.Column):
            if not col.table and col.name.lower() not in targets:
                issues.append(Issue(where, f"column {col.name!r} is not produced by any "
                                           f"mapping rule"))

    return issues
