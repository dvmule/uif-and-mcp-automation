#!/usr/bin/env python3
"""Derived columns — the query is the contract (ADR-0008, UIFPE-217).

A column whose value takes several steps to work out carries the author's
actual SELECT in its mapping entry's `derivation` block, plus a small declared
contract (which output column identifies the entity, which carries the value).
This module is the single Python home for everything that reads a block:

  validate_mapping()   the hygiene gate's engine — structural triggers decide
                       WHICH columns must carry a block (never prose tokens),
                       and every present block runs the validation battery:
                       one read-only SELECT, registered sources only, stable
                       results, contract columns present, computed lineage.
  computed_reads()     which tables and columns the query reads — computed
                       from the parsed query, never typed by hand. Stored in
                       the block as `reads` and re-checked here in CI, so the
                       lineage record can never be wrong or stale.
  render_wrap()        the compiler's assembly — the query becomes a CTE under
                       the column's name, LEFT-joined on the declared entity
                       columns. The wrap is the ONLY transformation: the SQL
                       the reviewer approved is the SQL that runs.
  plan_lines()         the patch-plan rendering for the one PLAN path left
                       (modifying a column whose shipped SQL predates the
                       wrap shape — the migrated legacy CTEs).

sqlglot (already a pinned dev dependency) is imported lazily so importing
this module stays cheap for callers that never touch a block.

History: the first shipped shape (UIFPE-210) described chains in a typed step
vocabulary with a render-and-diff drift gate; ADR-0008's accepted revision
replaced it — with the query itself in the mapping there is nothing to drift.
The step machinery lives in git history.
"""
from __future__ import annotations

import re

# The structural registry: columns whose derivation the gate REQUIRES even
# when no structural trigger fires (product's parentProduct hides its hops in
# conditional branches). Seeded with the five real cases (the nested member
# relatedPrograms.relationshipType is derived INSIDE relatedPrograms' query —
# nested fields carry no block of their own).
DERIVED_REGISTRY = {
    ("program", "parentProductId"),
    ("program", "associatedProducts"),
    ("program", "relatedPrograms"),
    ("product", "parentProductId"),
}


def _is_id(col: str) -> bool:
    return col.upper().endswith("_ID")


def _cast(expr_prefix: str, col: str) -> str:
    """Key-role columns are CAST AS BIGINT at every comparison — Silver
    stores ids as DECIMAL(38,10) and the uncast comparison nulls a column
    silently (check A3)."""
    ref = f"{expr_prefix}.{col}" if expr_prefix else col
    return f"CAST({ref} AS BIGINT)" if _is_id(col) else ref


def manifest_aliases(manifest_text: str) -> list[str]:
    """Every registered runtime alias, in registration order."""
    return re.findall(r"(?m)^\s*- alias:\s*(\w+)", manifest_text)


def restricted_driving_alias(manifest_text: str, driving_table: str) -> str | None:
    """The FIRST alias registered over the driving table — by convention the
    entity-restricted view Phase 2 swaps in. A derived query resolving OTHER
    entities' rows must use a later, full-scope alias (items_all); one
    enumerating the entity's own rows legitimately reads the restricted
    alias. The two are statically indistinguishable, so alias scope is a
    REVIEW point (called out in the wizard's reviewer preview), not a
    refusal."""
    drv = (driving_table or "").split(".")[-1].lower()
    for m in re.finditer(r"(?ms)^\s*- alias:\s*(\w+).*?silver_table:\s*(\S+)", manifest_text):
        if m.group(2).split(".")[-1].lower() == drv:
            return m.group(1)
    return None


def registered_tables(manifest_text: str) -> set:
    """Upper-cased last segments of every registered silver_table."""
    return {m.group(1).split(".")[-1].upper()
            for m in re.finditer(r"silver_table:\s*(\S+)", manifest_text)}


# ── the structural triggers ─────────────────────────────────────────────────

def _single_hop_lookups(fields: list, driving: str | None = None) -> bool:
    """UIFPE-262: a nested column whose every non-driving field attaches by a
    DECLARED LEFT join keyed off the driving table is compiled from that
    declaration — the join is the contract, so no block is owed. A pair whose
    leftTable is a third table (program's relatedPrograms hops through
    MTL_RELATED_ITEMS) or is missing is a chain and stays multi-hop. The
    anchor is the mapping's drivingTable when the caller has it, else the one
    join-less field table. KNOWN LIMIT: this reads the declaration, not the
    SQL — a hand-written fan-out join with the same declaration is exempt
    too; only the scaffolder's max()-per-key CTEs make the join grain-safe,
    and nothing in CI yet recompiles the intake to prove the SQL matches."""
    def bare(t):
        return (t or "").split(".")[-1].upper()
    # source: null = declared with no Silver source yet; it reads nothing, so
    # it neither anchors nor chains.
    fields = [f for f in fields if f.get("source")]
    plain = {bare(f["source"].get("table")) for f in fields
             if not f["source"].get("join")}
    if driving:
        if plain and plain != {bare(driving)}:
            return False
        anchor = bare(driving)
    else:
        if len(plain) != 1:
            return False
        (anchor,) = plain
    for f in fields:
        join = (f.get("source") or {}).get("join")
        if not join:
            continue
        on = join.get("on") or []
        if (join.get("type") or "left") != "left" or not on:
            return False
        for o in on:
            if not (o.get("leftColumn") and o.get("rightColumn")):
                return False
            if not o.get("leftTable") or bare(o["leftTable"]) != anchor:
                return False
    return True


def needs_derivation(domain: str, col: dict, entity_key: list | None,
                     reg_tables: set | None = None,
                     driving: str | None = None) -> str | None:
    """Why this column must carry a derivation block, or None. Structural,
    never prose: (T1) a join keyed off a non-entity-key column is the
    reverse-key smell; (T2) a nested column whose fields read more than one
    REGISTERED table is multi-hop by construction — unless every non-driving
    field is a declared single-hop LEFT lookup off the driving table, the
    shape the UIFPE-262 scaffolder compiles (see _single_hop_lookups); (T3) a
    nested column whose link filter is EAV-shaped while its fields read a
    non-EAV registered table resolves through a second hop; (T4) the seeded
    registry.

    T2/T3 count only tables registered as manifest sources: a CONTRACT-FIRST
    placeholder column (product's standardComponents/customComponents — bom_*
    tables not yet in Silver, SQL emits a typed empty array) cannot be
    runtime-derived at all, so no block is owed; the moment its sources
    register, the trigger starts firing and the gate demands the block."""
    name = col.get("goldColumnName")
    if (domain, name) in DERIVED_REGISTRY:
        return "registry"
    key_cols = {(k.get("drivingColumn") or "").upper() for k in (entity_key or [])}
    reg = reg_tables if reg_tables is not None else None

    def _registered(table: str | None) -> bool:
        if table is None:
            return False
        return reg is None or table.split(".")[-1].upper() in reg

    def _reverse_keyed(src: dict) -> bool:
        for o in ((src or {}).get("join") or {}).get("on") or []:
            if (o.get("leftColumn") or "").upper() not in key_cols and key_cols:
                return True
        return False

    if col.get("kind") == "scalar" and _reverse_keyed(col.get("source") or {}):
        return "reverse-keyed join"
    if col.get("kind") == "conditional":
        for b in col.get("branches") or []:
            if _reverse_keyed(b.get("source") or {}):
                return "reverse-keyed branch join"
    if col.get("kind") == "nested":
        fields = col.get("fields") or []
        tables = {(f.get("source") or {}).get("table") for f in fields}
        tables.discard(None)
        registered = {t for t in tables if _registered(t)}
        if len(registered) > 1 and not _single_hop_lookups(fields, driving):
            return "multi-table nested fields"
        flt = col.get("filter") or ""
        if "ATTR_GROUP_ID" in flt and registered \
                and not any(t.upper().endswith("EXT_B") for t in registered):
            return "EAV link filter with non-EAV field source"
    return None


# ── the validation battery (ADR-0008's submit-time refusals, re-run in CI) ──

# Functions whose result changes between identical runs. A derived value must
# not wobble — unstable values move channel hashes and ring every downstream
# consumer for nothing (the A8 lesson, applied to authored queries).
_NONDET_ANONYMOUS = {
    "random", "shuffle", "monotonically_increasing_id", "input_file_name",
    "spark_partition_id", "now", "localtimestamp", "current_timezone",
    # arbitrary-Java escape hatches — reflect('java.lang.Math','random') is
    # nondeterminism the node scan cannot see, so the doorway is refused
    "reflect", "java_method",
}
# Argless unix_timestamp() is "now"; with an argument it is a deterministic
# conversion. Checked textually because sqlglot normalizes the name away.
_ARGLESS_UNIX = re.compile(r"\bunix_timestamp\s*\(\s*\)", re.IGNORECASE)


def _exp():
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    return sqlglot, exp, traverse_scope


def parse_derivation_sql(sql: str) -> tuple:
    """(root expression | None, errors). Battery refusal #1: exactly one
    statement, and it returns rows — no writes, no DDL, no statement lists."""
    sqlglot, exp, _ = _exp()
    if sql.rstrip().endswith(";"):
        # sqlglot tolerates a trailing terminator, but the wrap embeds the
        # text verbatim inside CTE parens, where a ';' breaks the gold SQL.
        return None, ["ends with ';' — remove the statement terminator (the "
                      "query is embedded inside the gold query)"]
    try:
        stmts = sqlglot.parse(sql, read="spark")
    except Exception as e:  # sqlglot raises ParseError subclasses
        return None, [f"does not parse as Spark SQL: {e}"]
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        return None, [f"{len(stmts)} statements — the contract is ONE SELECT"]
    root = stmts[0]
    if not isinstance(root, exp.Query):
        return None, [f"not a query ({type(root).__name__}) — the contract "
                      "is one read-only SELECT"]
    write_nodes = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create,
                   exp.Drop, exp.Alter, exp.Command)
    for n in root.find_all(*write_nodes):
        return None, [f"contains a write/DDL node ({type(n).__name__}) — "
                      "derived queries are read-only"]
    return root, []


def computed_reads(sql: str, root=None) -> dict[str, list[str]]:
    """Which tables and columns the query reads — computed from the parse,
    never typed by hand. Table keys are the names as written in the query
    (a database-qualified reference keeps its qualifier, so the registered-
    alias check refuses it — Silver is only reachable through the bare
    registered temp views).

    EVERY physical table a scope actually selects from is recorded, even
    when no column could be attributed to it — the registered-source check
    iterates these keys, so a table can never escape by being read without
    a column qualifier. Attribution uses each scope's SELECTED sources (its
    own FROM/JOIN clause), never the merely-visible ones: sqlglot exposes
    every earlier CTE as visible in later scopes, and counting those made
    unqualified columns in any second-or-later CTE vanish from the lineage."""
    _, exp, traverse_scope = _exp()
    if root is None:
        root, errors = parse_derivation_sql(sql)
        if errors:
            raise ValueError("; ".join(errors))
    reads: dict[str, set] = {}
    for scope in traverse_scope(root):
        selected = {}
        for alias, src in scope.selected_sources.items():
            node = src[1] if isinstance(src, tuple) else src
            if isinstance(node, exp.Table):
                key = f"{node.db}.{node.name}" if node.db else node.name
                selected[alias] = key
                reads.setdefault(key, set())
        n_sources = len(scope.selected_sources)
        for c in scope.columns:
            if c.table:
                if c.table in selected:
                    reads[selected[c.table]].add(c.name)
            elif n_sources == 1 and len(selected) == 1:
                # unambiguous: the scope selects from exactly one source,
                # and it is a physical table
                reads[next(iter(selected.values()))].add(c.name)
    return {t: sorted(cols) for t, cols in sorted(reads.items())}


def _stability_errors(sql: str, root) -> list[str]:
    """Battery refusal #3: no run-varying functions, and anything that builds
    a list states its ordering (collect wrapped in a sort)."""
    _, exp, _ = _exp()
    errors = []
    nondet_types = tuple(t for t in (
        getattr(exp, "Rand", None), getattr(exp, "Randn", None),
        getattr(exp, "Uuid", None), getattr(exp, "CurrentTimestamp", None),
        getattr(exp, "CurrentDate", None), getattr(exp, "CurrentTime", None),
        # names sqlglot parses to typed nodes rather than Anonymous — the
        # _NONDET_ANONYMOUS list never sees these, so listing them there
        # alone was a blind spot (found adversarially, UIFPE-219)
        getattr(exp, "Localtimestamp", None),
        getattr(exp, "CurrentTimezone", None),
        getattr(exp, "Shuffle", None),
    ) if t is not None)
    for n in root.find_all(*nondet_types):
        errors.append(f"unstable function {n.sql(dialect='spark')!r} — the "
                      "value would change between identical runs")
    for n in root.find_all(exp.Anonymous):
        if (n.name or "").lower() in _NONDET_ANONYMOUS:
            errors.append(f"unstable function {n.name!r} — the value would "
                          "change between identical runs")
    if _ARGLESS_UNIX.search(sql):
        errors.append("unstable function 'unix_timestamp()' (argless) — the "
                      "value would change between identical runs")
    collect_types = tuple(t for t in (
        getattr(exp, "ArrayAgg", None), getattr(exp, "ArrayUniqueAgg", None),
    ) if t is not None)
    sort_types = tuple(t for t in (
        getattr(exp, "ArraySort", None), getattr(exp, "SortArray", None),
    ) if t is not None)
    for n in root.find_all(*collect_types):
        p, sorted_ = n.parent, False
        while p is not None:
            if isinstance(p, sort_types):
                sorted_ = True
                break
            p = p.parent
        if not sorted_:
            errors.append("collect without a stated ordering — wrap the "
                          "collect in array_sort()/sort_array(), or identical "
                          "runs produce differently-ordered lists")
    return errors


def validate_block(name: str, block: dict, aliases: list[str]) -> list[str]:
    """The full battery for one column's block (refusals 1–4; the #5 grain
    probe is a live test run, offered in the wizard, not a static check).
    Returns [] when the block passes."""
    errors: list[str] = []
    if "." in name:
        # A struct MEMBER cannot carry its own query — the named_struct
        # assembly is the parent's; a d_<parent.member> CTE name and select
        # alias would not even parse. Derive the parent column instead.
        return [f"{name}: a struct member cannot carry a derivation block — "
                "put the query on the parent column"]
    sql = block.get("sql")
    keyed = block.get("keyedBy")
    if not isinstance(sql, str) or not sql.strip():
        return [f"{name}: derivation.sql missing or empty"]
    if not isinstance(keyed, list) or not keyed:
        errors.append(f"{name}: derivation.keyedBy missing — declare which "
                      "output column identifies the entity")
        keyed = []
    for k in keyed:
        if not (isinstance(k, dict) and k.get("queryColumn")
                and k.get("entityColumn")):
            errors.append(f"{name}: keyedBy entries need queryColumn and "
                          "entityColumn")
    root, perr = parse_derivation_sql(sql)
    if perr:
        return errors + [f"{name}: {e}" for e in perr]
    reads = computed_reads(sql, root)
    lower_aliases = {a.lower() for a in aliases}
    for table in reads:
        if table.lower() not in lower_aliases:
            errors.append(
                f"{name}: reads {table!r}, which is not a registered source "
                f"for this entity — register it in the manifest first "
                f"(registered: {', '.join(aliases)})")
    errors += [f"{name}: {e}" for e in _stability_errors(sql, root)]
    out_cols = set(root.named_selects)
    value_col = block.get("valueColumn") or name
    for k in keyed:
        qc = (k or {}).get("queryColumn")
        if qc and qc not in out_cols:
            errors.append(f"{name}: declared key column {qc!r} is not in the "
                          f"query's output ({sorted(out_cols)})")
    if value_col not in out_cols:
        errors.append(f"{name}: value column {value_col!r} is not in the "
                      f"query's output ({sorted(out_cols)})")
    stored = block.get("reads")
    if stored is None:
        errors.append(f"{name}: derivation.reads missing — it is computed by "
                      "the validator from the parsed query (never typed); "
                      "recompile the intake or run with_computed_reads()")
    elif {t: list(c) for t, c in stored.items()} != reads:
        errors.append(f"{name}: derivation.reads is stale — stored {stored} "
                      f"!= computed {reads}. reads is computed, never "
                      "authored; regenerate it")
    return errors


def with_computed_reads(block: dict) -> dict:
    """A copy of the block with `reads` recomputed from the query — the
    submit path calls this so the stored lineage is always the parser's."""
    new = dict(block)
    new["reads"] = computed_reads(block["sql"])
    return new


# ── assembly (what "form the final query" means) ────────────────────────────

def render_wrap(name: str, block: dict, driving_alias: str = "items",
                kind: str | None = None) -> tuple[str, str, str]:
    """(cte, join, select_expr) for splicing a derived column into the
    entity's gold SQL. The wrap is the ONLY transformation: the query is
    embedded verbatim as `d_<name>`, LEFT-joined on the declared pairs so an
    entity with no derived result keeps its row and reports NULL. Entity-side
    _ID columns are CAST AS BIGINT (the A3 rule). A NESTED column's select is
    COALESCEd to array() — empty arrays are [] and never NULL, the
    hash-stability commitment every hand-written array column keeps — which
    is why the SINGLE definition of the wrap lives here: the compiler, the
    patch plan, and the UI preview must all show the same spliced text."""
    sql = block["sql"].strip()
    body = "\n".join(f"  {line}" if line.strip() else ""
                     for line in sql.splitlines())
    cte = (f"d_{name} AS (  -- derived (ADR-0008): the mapping's "
           f"derivation.sql, spliced verbatim\n{body}\n)")
    pairs = [f"{_cast(driving_alias, k['entityColumn'])} = d_{name}.{k['queryColumn']}"
             for k in block["keyedBy"]]
    join = (f"-- derived column — joined on the declared key contract\n"
            f"LEFT JOIN d_{name} ON " + f"\n{' ' * 19}AND ".join(pairs))
    select_expr = f"d_{name}.{block.get('valueColumn') or name}"
    if kind == "nested":
        select_expr = f"COALESCE({select_expr}, array())"
    return cte, join, select_expr


# ── patch-plan rendering (the modify-of-legacy-SQL PLAN path) ───────────────

def plan_lines(block: dict, name: str = "",
               kind: str | None = None) -> list[str]:
    """The engineer-facing plan for a derived column whose shipped SQL
    predates the d_<name> wrap shape (the migrated legacy CTEs): the new
    query and its contract, ready to place by hand."""
    out = ["  contract: one row per entity — "
           + ", ".join(f"{k.get('queryColumn')} -> {k.get('entityColumn')}"
                       for k in block.get("keyedBy") or [])
           + f"; value column {block.get('valueColumn') or name or '(column name)'!r}"]
    out.append("  the query (the mapping's derivation.sql — place it as the "
               "column's CTE and join it LEFT on the contract):")
    out += [f"    {line}" for line in (block.get("sql") or "").splitlines()]
    if kind == "nested":
        out.append("  array column: COALESCE the select to array() — empty "
                   "arrays are [] and never NULL (hash-stability commitment).")
    return out


# ── the gate ────────────────────────────────────────────────────────────────

def validate_mapping(mapping: dict, manifest_text: str) -> list[str]:
    """The hygiene gate: every structurally-derived column must carry a
    passing derivation block, and every block present (trigger-fired or not)
    must pass the battery. Complex logic can never hide in a free-text note.
    Returns a list of errors; [] is a passing mapping."""
    errors: list[str] = []
    domain = mapping.get("domain") or ""
    entity_key = mapping.get("entityKey")
    aliases = manifest_aliases(manifest_text)
    reg = registered_tables(manifest_text)
    for col in mapping.get("columns") or []:
        name = col.get("goldColumnName") or "?"
        reason = needs_derivation(domain, col, entity_key, reg,
                                  mapping.get("drivingTable"))
        block = col.get("derivation")
        if reason and not block:
            errors.append(
                f"{name}: structurally derived ({reason}) but carries no "
                "derivation block — the query is the contract (ADR-0008); "
                "complex logic cannot ride a prose note")
            continue
        if block:
            errors += validate_block(name, block, aliases)
    return errors
