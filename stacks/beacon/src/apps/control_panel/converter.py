# converter.py
# Door 2: deterministic query -> draft-mapping-config conversion (design doc
# §10.3, D11/D12). Built on sqlglot — the same parser the engine's validator
# uses, so "what converts" and "what validates" cannot drift.
#
# Hard rules:
#   - The query is parsed and transpiled, NEVER executed (D11).
#   - Anything the engine cannot express (window functions, subqueries,
#     GROUP BY/aggregates, set operations) is returned as an `unresolved`
#     item with a reason — never silently dropped (D12).
#   - WHERE becomes `filters` (Spark SQL booleans, ANDed) — the engine's D18
#     block, applied to the joined frame before mapping; the note in `notes`
#     tells callers where the excluded rows are counted.

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Order matters: first dialect that parses wins. Spark last so that
# dialect-specific constructs (Oracle NVL, T-SQL brackets) are claimed by
# their own dialect before the generic fallback.
_AUTO_DIALECTS = ("oracle", "tsql", "mysql", "postgres", "spark")

_JOIN_TYPES = {"": "inner", "inner": "inner", "left": "left",
               "right": "right", "full": "full", "cross": "cross"}


class ConversionError(ValueError):
    """Query cannot be converted at all (parse failure, not a SELECT, …)."""


def convert(sql: str, dialect: str | None = None) -> dict:
    tree, detected = _parse(sql, dialect)

    result: dict = {"sources": [], "joins": [], "mappings": [],
                    "filters": [], "unresolved": [], "notes": [],
                    "dialect_detected": detected}

    _collect_sources_and_joins(tree, result)
    _collect_projections(tree, result)
    _collect_where(tree, result)
    _flag_unsupported_clauses(tree, result)
    return result


def _parse(sql: str, dialect: str | None):
    candidates = (dialect,) if dialect else _AUTO_DIALECTS
    last_err: Exception | None = None
    for d in candidates:
        try:
            statements = sqlglot.parse(sql, read=d)
        except sqlglot.errors.SqlglotError as e:  # ParseError or TokenError (bad quotes)
            last_err = e
            continue
        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            raise ConversionError("exactly one SQL statement is expected")
        stmt = statements[0]
        if not isinstance(stmt, exp.Select):
            if isinstance(stmt, (exp.Union, exp.Intersect, exp.Except)):
                raise ConversionError(
                    "set operations (UNION/INTERSECT/EXCEPT) are not supported")
            raise ConversionError(
                f"only SELECT statements can be converted, got {stmt.key.upper()}")
        return stmt, d
    raise ConversionError(
        f"query could not be parsed in any supported dialect: {last_err}")


def _spark(node: exp.Expression) -> str:
    """Generate Spark SQL, normalized for the mapping grid: `a || b` becomes
    concat(a, b) — both are valid Spark, but the explicit function is what
    the engine allowlist names and what authors see in the palette."""
    def _depipe(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.DPipe):
            return exp.func("concat", *n.flatten())
        return n
    return node.transform(_depipe).sql(dialect="spark")


def _collect_sources_and_joins(tree: exp.Select, result: dict) -> None:
    # sqlglot >= 30 stores the FROM clause under "from_"; older under "from"
    from_clause = tree.args.get("from_") or tree.args.get("from")
    if from_clause is None:
        raise ConversionError("query has no FROM clause")

    def add_source(table: exp.Table):
        if not isinstance(table, exp.Table):
            raise ConversionError(
                "FROM/JOIN items must be plain tables — subqueries and CTEs "
                "are not supported")
        name = table.alias_or_name
        fq = ".".join(p.name for p in table.parts)
        result["sources"].append({
            "name": name,
            "registry_hint": fq,   # the UI matches this against the source registry
            "format": "iceberg",   # default; author confirms in the grid
        })

    add_source(from_clause.this)
    for join in tree.args.get("joins", []):
        add_source(join.this)
        side = (join.side or "").lower()
        kind = (join.kind or "").lower()
        jtype = _JOIN_TYPES.get(side or kind, "inner")
        condition = join.args.get("on")
        if condition is None and jtype != "cross":
            # UIFPE-167: flag THIS join and keep going — returning here used
            # to drop every later JOIN's table from `sources` with no
            # unresolved item naming it (a D12 violation). The source is
            # already collected; the wizard builds a default join for the
            # author to complete, and the unresolved item blocks submit.
            result["unresolved"].append({
                "target": None,
                "expression": _spark(join),
                "reason": "join without an ON condition (implicit/USING joins "
                          "are not supported)"})
            continue
        # UIFPE-167: `left` names the source this join attaches to — the ONE
        # prior table its condition references, else the driving table (a
        # condition spanning several prior tables attaches to the frame the
        # engine has folded so far, which the driving alias denotes). The
        # engine seeds from joins[0].left and folds in declaration order;
        # validate_structure checks the chain.
        prior = [s["name"] for s in result["sources"][:-1]]
        referenced = ([] if condition is None else
                      [t for t in _condition_tables(condition) if t in prior])
        left = referenced[0] if len(referenced) == 1 else result["sources"][0]["name"]
        entry = {"left": left, "right": join.this.alias_or_name, "type": jtype}
        if condition is not None:
            entry["condition"] = _spark(condition)
        result["joins"].append(entry)


def _condition_tables(condition: exp.Expression) -> list[str]:
    """Distinct table qualifiers in a parsed condition, first-appearance order."""
    seen: list[str] = []
    for col in condition.find_all(exp.Column):
        if col.table and col.table not in seen:
            seen.append(col.table)
    return seen


def _collect_projections(tree: exp.Select, result: dict) -> None:
    for proj in tree.selects:
        target = proj.alias_or_name
        inner = proj.this if isinstance(proj, exp.Alias) else proj

        if isinstance(inner, exp.Star):
            result["unresolved"].append({
                "target": "*", "expression": "*",
                "reason": "SELECT * cannot be converted — mappings are "
                          "explicit per field; pick columns in the grid"})
            continue
        if inner.find(exp.Window):
            result["unresolved"].append({
                "target": target, "expression": _spark(inner),
                "reason": "window functions are not supported in mapping "
                          "expressions (engine compiles scalar expressions "
                          "only); if this is deduplication, the Gold layer "
                          "usually already provides it"})
            continue
        if inner.find(exp.Select):
            result["unresolved"].append({
                "target": target, "expression": _spark(inner),
                "reason": "subqueries are not supported in mapping expressions"})
            continue
        if isinstance(inner, exp.Column):
            source = _spark(inner)
            result["mappings"].append(
                {"type": "one_to_one", "source": source, "target": target})
            continue
        if isinstance(inner, exp.Literal):
            value = inner.this if inner.is_string else _spark(inner)
            result["mappings"].append(
                {"type": "literal", "target": target, "value": value})
            continue
        result["mappings"].append(
            {"type": "derived", "target": target, "expression": _spark(inner)})


def _collect_where(tree: exp.Select, result: dict) -> None:
    where = tree.args.get("where")
    if where is not None:
        result["filters"].append(_spark(where.this))
        result["notes"].append(
            "WHERE became a row filter (D18: scope definition, distinct from "
            "validations). Filters run on the joined sources before mapping; "
            "excluded rows are dropped, not quarantined.")


def _flag_unsupported_clauses(tree: exp.Select, result: dict) -> None:
    if tree.args.get("group"):
        result["unresolved"].append({
            "target": None, "expression": _spark(tree.args["group"]),
            "reason": "GROUP BY / aggregation is not supported — exports map "
                      "row-level data; aggregate upstream in Gold if needed"})
    if tree.args.get("having"):
        result["unresolved"].append({
            "target": None, "expression": _spark(tree.args["having"]),
            "reason": "HAVING is not supported (no aggregation)"})
    if tree.args.get("order"):
        result["notes"].append(
            "ORDER BY was ignored: output row order is not part of the "
            "export contract (files are written distributed).")
    if tree.args.get("limit"):
        result["notes"].append(
            "LIMIT was ignored: exports ship full in-scope data; use a "
            "filter to bound scope, or Preview for a bounded look.")
