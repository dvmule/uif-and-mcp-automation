#!/usr/bin/env python3
"""Delta compiler (UIFPE-100) — applies ONE wizard intake envelope to the
product entity's four artifacts:

    domains/product/src/schemas/mapping/product/v1/product.mapping.json  (authored intake)
    domains/product/src/schemas/gold/product/v1/master.json
    domains/product/src/schemas/mapping/product/v1/manifest.yaml
    domains/product/src/schemas/mapping/product/v1/sql/product.sql

Deterministic by design (ADR-0006 D3/D6: the compiler renders, never
interprets). Coverage:

  AUTO  — scalar columns sourced from the driving table or EAV-shaped
          (items_ext + ATTR_GROUP_ID filter); CONDITIONAL columns whose
          branches are all EAV-shaped (one CTE per branch carrying the
          discriminator predicate, strict-pick COALESCE, scoped fan-out CASE
          — UIFPE-106, porting the wizard preview's proven contract); and
          LOOKUP-JOIN scalars against a registered manifest source (the orgs
          dimension precedent). All four artifacts edited, including the SQL.
  PLAN  — nested arrays and struct assembly (permanently, by design — D5:
          engineering review is the contract there); conditional STRUCT
          MEMBERS (the named_struct + all-null gate are hand-written);
          declared MULTI-HOP conditionals (the UIFPE-153 notes convention);
          fallbackToElse branches (no deterministic strict-pick rendering
          exists); and every modify-path restructure. The three structured
          artifacts are still edited automatically; the SQL edit is emitted
          as an exact, anchored patch plan for the engineer.

Column ORDER invariant: new columns append at the end of the business
columns in master.json, manifest output_columns and the SQL final SELECT —
the physical-order parity tests hold by construction.

Usage:
    python compile_intake_delta.py --intake task.intake.json [--repo-root .]
                                   [--apply]

Without --apply it prints the change plan and touches nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly (Echo's
# convention). gold_frequencies is the single definition of the cadence
# vocabulary (UIFPE-149).
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))
import gold_resources  # noqa: E402
from gold_frequencies import (  # noqa: E402
    DEFAULT_WEEKLY_DAYS,
    TRANSFORM_FREQUENCIES,
    WEEKDAYS,
    WEEKLY_FREQUENCY,
    parse_time_of_day,
    parse_weekly_days,
)
# Derived columns (ADR-0008, UIFPE-217): the query is the contract. The
# battery validates the author's SELECT, reads are computed from the parse,
# and render_wrap is the assembly the AUTO path splices.
from derivation_chain import (
    manifest_aliases as derivation_manifest_aliases,
    plan_lines as derivation_plan_lines,
    render_wrap as derivation_render_wrap,
    validate_block as derivation_validate_block,
    with_computed_reads as derivation_with_computed_reads,
)

def derive_grain(src: dict, entity_key: list | None = None) -> str:
    """pdh_grain from the explicit join's key set (UIFPE-104 — joinGrain is
    retired; the ON pairs state the attachment). With the intake's entityKey
    declared (UIFPE-107) the wording is DOMAIN-AGNOSTIC — full vs partial
    entity key — instead of the product-specific item/org labels, which
    remain as the fallback for old intakes."""
    on = (src.get("join") or {}).get("on") or []
    if not on:
        return "driving table (row grain)"
    cols = sorted({o["leftColumn"].upper() for o in on})
    keys = [p["drivingColumn"].upper() for p in (entity_key or [])]
    if keys:
        if set(keys) <= set(cols):
            return "full entity key (" + ", ".join(keys) + ")"
        hit = [k for k in keys if k in cols]
        if hit:
            return ("partial entity key (" + ", ".join(hit)
                    + ") — fans out across the remaining key columns")
    if cols == ["INVENTORY_ITEM_ID"]:
        return "item-level"
    if cols == ["INVENTORY_ITEM_ID", "ORGANIZATION_ID"]:
        return "org-level, joined on (INVENTORY_ITEM_ID, ORGANIZATION_ID)"
    return "joined on (" + ", ".join(cols) + ")"


JSON_TYPE = {"string": "string", "integer": "integer", "number": "number",
             "float": "number", "boolean": "boolean", "date": "string",
             "timestamp": "string"}

# Spark DDL types for manifest column_types (struct members) — json "number"
# gets the framework's json_type_to_spark default; tighten by hand if the
# column needs a specific scale (see the unit* DECIMAL(38,2) precedent).
SPARK_TYPE = {"string": "STRING", "integer": "BIGINT", "number": "DECIMAL(38,10)",
              "float": "DOUBLE", "boolean": "BOOLEAN", "date": "DATE",
              "timestamp": "TIMESTAMP"}


def split_struct(name: str) -> tuple[str | None, str]:
    """'fulfillmentOrgDetails.returnClass' -> ('fulfillmentOrgDetails',
    'returnClass'); plain names -> (None, name). A dotted gold name is a
    member OF a struct column — never a top-level column of its own."""
    if "." in name:
        struct, member = name.split(".", 1)
        return struct, member
    return None, name


def eav_org_level(col: dict) -> bool:
    """True when the declared join carries BOTH entity keys — the (item, org)
    grain ag755/ag800/ag822 use. The intake states this explicitly
    (UIFPE-104); the SQL writer must not collapse it to item level."""
    on = (col.get("source", {}).get("join") or {}).get("on") or []
    return {o["leftColumn"].upper() for o in on} == \
        {"INVENTORY_ITEM_ID", "ORGANIZATION_ID"}


# ── transforms (UIFPE-105) ──────────────────────────────────────────────────
# Deterministic mirror of the wizard's transformSql: cases become a CASE WHEN
# evaluated in order, steps wrap the result as a pipeline, sqlExpr
# substitutes {x}. Prose and unknown rules REFUSE to compile — the intake
# contract is structured data only.

def _lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _case_when(when: dict, x: str) -> str:
    op, vals = when["op"], when.get("values") or []
    if op == "isNull":
        return f"{x} IS NULL"
    if op == "in":
        return f"{x} IN ({', '.join(_lit(v) for v in vals)})"
    if op == "startsWith":
        return " OR ".join(f"substring({x}, 1, {len(v)}) = {_lit(v)}" for v in vals)
    if op == "like":
        return " OR ".join(f"{x} LIKE {_lit(v)}" for v in vals)
    return " OR ".join(f"{x} = {_lit(v)}" for v in vals)


def _case_then(then: dict, x: str) -> str:
    op = then["op"]
    if op == "prepend":
        return f"concat({_lit(then.get('text', ''))}, {x})"
    if op == "append":
        return f"concat({x}, {_lit(then.get('text', ''))})"
    if op == "replaceText":
        return f"replace({x}, {_lit(then.get('find', ''))}, {_lit(then.get('replaceWith', ''))})"
    if op == "literal":
        return _lit(then.get("text", ""))
    if op == "null":
        return "NULL"
    return x


# Aggregate detection (UIFPE-260): decides whether a rendered expression
# collapses rows. Word-boundary + open paren so max_price or a column named
# summary can't false-positive.
AGG_RE = re.compile(
    r"\b(sum|max|min|count|avg|first|last|collect_list|collect_set"
    r"|arbitrary|any_value|percentile|stddev|variance"
    r"|approx_count_distinct)\s*\(", re.IGNORECASE)


def is_aggregate(expr: str) -> bool:
    """True when expr collapses rows. String literals are blanked first so
    'max(unknown)' inside a quoted value can't count; a window expression
    (OVER) is grain-PRESERVING, so it never counts — a window mixed with a
    true aggregate then reads as bare, which the grouped-mode coherence
    check refuses loudly rather than compiling invalid SQL."""
    stripped = re.sub(r"'[^']*'", "''", expr)
    if re.search(r"\bover\s*\(", stripped, re.IGNORECASE):
        return False
    return bool(AGG_RE.search(stripped))


def render_transform(t, registry: dict, expr: str) -> str:
    """Wrap expr in a structured transform. Raises on shapes that do not
    compile (prose, unknown rule) — the hygiene gate keeps those out of the
    registered mapping; here they fail loudly instead of passing silently."""
    if t is None:
        return expr
    if isinstance(t, str):
        raise ValueError(f"prose transform {t!r} does not compile (UIFPE-105) "
                         "— restructure it as steps/cases or a named rule")
    if "rule" in t:
        if t["rule"] not in registry:
            raise ValueError(f"transform rule {t['rule']!r} is not in the "
                             "transforms registry")
        t = registry[t["rule"]]
    out = expr
    if t.get("cases"):
        whens = "\n".join(f"    WHEN {_case_when(c['when'], expr)} "
                          f"THEN {_case_then(c['then'], expr)}"
                          for c in t["cases"])
        fall = "NULL" if t.get("else") == "null" else expr
        out = f"CASE\n{whens}\n    ELSE {fall}\n  END"
    for s in t.get("steps") or []:
        if s["op"] == "round":
            out = f"round({out}, {s.get('precision', 0)})"
        elif s["op"] == "cast":
            out = f"CAST({out} AS {s.get('to', '')})"
        elif s["op"] == "coalesceTo":
            out = f"COALESCE({out}, {_lit(s.get('value', ''))})"
        else:  # trim / upper / lower
            out = f"{s['op']}({out})"
    if t.get("sqlExpr"):
        out = t["sqlExpr"].replace("{x}", out)
    return out


def _plan_transform_note(t, registry: dict) -> str:
    """Human-readable rendering for PLAN output — never raises."""
    try:
        return render_transform(t, registry, "{x}").replace("\n", " ").replace("    ", " ")
    except ValueError as e:
        return f"UNCOMPILABLE ({e})"


# ── envelope ────────────────────────────────────────────────────────────────

def load_envelope(path: Path) -> tuple[dict, str, dict | None, dict | None]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    intake = raw["intake"] if "intake" in raw else raw
    refresh = raw.get("refresh")
    return intake, raw.get("intent") or "add", refresh, raw.get("jobProperties")


# The UIFPE-153 notes convention: a column whose derivation needs a hop the
# intake contract cannot record (parentProduct's ISBN -> owning item -> PPID)
# is marked MULTI-HOP in its notes and stays PLAN with the SQL authoritative.
_MULTIHOP_RE = re.compile(r"multi-?hop", re.IGNORECASE)


def _bare_eav_group(src: dict) -> str | None:
    """The attribute group when the source is EXACTLY the bare EAV shape:
    items_ext + a filter that is nothing but the ATTR_GROUP_ID equality.
    A filter carrying any other conjunct is NOT AUTO-EAV — the CTE writer
    rebuilds the WHERE from the group number, so extra conjuncts would be
    silently dropped from the SQL while the manifest records them."""
    table = ((src or {}).get("table") or "").split(".")[-1].upper()
    m = re.fullmatch(r"ATTR_GROUP_ID\s*=\s*(\d+)",
                     ((src or {}).get("filter") or "").strip())
    return m.group(1) if (table.endswith("EXT_B") and m) else None


def _branch_eav_group(branch: dict) -> str | None:
    """The branch source's attribute group when it is bare-EAV-shaped."""
    return _bare_eav_group(branch.get("source") or {})


def classify(col: dict, driving: str) -> str:
    """AUTO classes: 'eav:<group>' | 'driving' | 'conditional' | 'lookup-join'.
    Everything else -> 'plan:*'.

    A conditional stays PLAN when the SQL craft is the contract (D5): it is a
    struct MEMBER (the named_struct + all-null gate are hand-written), it is
    a declared MULTI-HOP derivation (notes convention, UIFPE-153), a branch
    declares fallbackToElse (no deterministic strict-pick rendering exists
    for cross-branch fallback), a branch carries its own transform (a
    null-reviving transform outside the CTE would break the strict pick, so
    the placement is an engineering decision), or any branch is not
    bare-EAV-shaped.

    A lookup-join is AUTO only when every on-pair anchors on the DRIVING
    table and the join is LEFT — chained lookups (A -> B -> C) and other
    join types have no dimension-CTE rendering and stay PLAN.

    A column carrying a DERIVATION block (ADR-0008: the query is the
    contract) is 'derived-sql' unconditionally, whatever its kind's shape
    suggests: the author supplied the query, so the compiler splices it
    mechanically (an AUTO class with the battery's refusals) instead of
    routing the column through kind-shaped writers that would ignore the
    block. Checked FIRST so it wins over every kind dispatch. `is not None`
    rather than truthiness so an EMPTY block classifies derived-sql on both
    sides of the mirror (TS `!= null`) and is then refused by the battery
    instead of slipping through a kind-shaped writer."""
    if col.get("derivation") is not None:
        return "derived-sql"
    if col.get("kind") == "conditional":
        struct, _ = split_struct(col["goldColumnName"])
        branches = col.get("branches") or []
        if (struct is not None
                or _MULTIHOP_RE.search(col.get("notes") or "")
                or len(branches) < 2
                or any(b.get("fallback") == "fallbackToElse" for b in branches)
                or any(b.get("transform") is not None for b in branches)
                or any(_branch_eav_group(b) is None for b in branches)):
            return "plan:conditional"
        return "conditional"
    if col.get("kind") != "scalar":
        return f"plan:{col.get('kind')}"
    src = col["source"]
    table = (src.get("table") or "").split(".")[-1].upper()
    group = _bare_eav_group(src)
    if group:
        return f"eav:{group}"
    drv = driving.split(".")[-1].upper()
    if table == drv:
        return "driving"
    j = src.get("join")
    if j:
        on = j.get("on") or []
        anchored = bool(on) and all(
            (o.get("leftTable") or driving).split(".")[-1].upper() == drv
            for o in on)
        if anchored and (j.get("type") or "left") == "left":
            return "lookup-join"
        return "plan:lookup-join"
    return "plan:unrecognized-source"


# ── branch conditions & row scopes (UIFPE-106) ──────────────────────────────
# Deterministic ports of the wizard's conditions.ts — the same parsing and
# rendering rules, with one deliberate difference: everywhere the preview
# renders a {placeholder} to survive half-typed input, this compiler REFUSES.

def _cond_lit(v) -> str:
    """Condition/scope literal: bare numerics (ITEM_CATALOG_GROUP_ID = 10002,
    matching product.sql), quoted strings with '' doubling. NOT the transform
    _lit(), which always quotes. ASCII digit class so both mirrors share one
    character model with the TS regexes."""
    s = str(v)
    if re.fullmatch(r"\d+(\.\d+)?", s, re.ASCII):
        return s
    return "'" + s.replace("'", "''") + "'"


def parse_branch_condition(raw: str) -> dict:
    """'10002 (Digital Catalog)' | '10002,10003 (bundles)' | 'ELSE' | legacy
    'ELSE (<> 10002)' — the intake-compatible canonical string form. ELSE
    must be the WHOLE token (an optional parenthesized label aside): a value
    that merely starts with 'else' is a value, not the else branch."""
    s = (raw or "").strip()
    if re.fullmatch(r"(?i)else\s*(\(.*\))?", s):
        return {"op": "else", "values": [], "label": None}
    m = re.match(r"^([\w.,\s-]+?)\s*(?:\((.*)\))?$", s, re.ASCII)
    body = m.group(1) if m else s
    values = [v.strip() for v in body.split(",") if v.strip()]
    label = ((m.group(2) or "").strip() or None) if m else None
    return {"op": "in" if len(values) > 1 else "equals",
            "values": values, "label": label}


def _branch_where(name: str, disc: str, c: dict, named_values: list) -> str:
    """One branch's discriminator predicate. ELSE negates the deduped union
    of every named sibling's values (Proliferate branches may share one
    discriminator value and differ only in scope)."""
    if c["op"] == "equals":
        if not c["values"]:
            raise ValueError(f"{name}: branch condition has no value")
        return f"{disc} = {_cond_lit(c['values'][0])}"
    if c["op"] == "in":
        if not c["values"]:
            raise ValueError(f"{name}: branch IN condition has no values")
        return f"{disc} IN ({', '.join(_cond_lit(v) for v in c['values'])})"
    if not named_values:
        raise ValueError(
            f"{name}: an ELSE branch needs at least one named sibling branch")
    if len(named_values) == 1:
        return f"{disc} <> {_cond_lit(named_values[0])}"
    return f"{disc} NOT IN ({', '.join(_cond_lit(v) for v in named_values)})"


def _resolve_scope(name: str, scope, row_groups: dict) -> dict:
    """A branch scope resolved against the mapping's rowGroups registry.
    Group references win over inline fields; a complement group IS its base
    predicate negated, and complement chaining is not allowed."""
    if isinstance(scope, str):
        scope = {"group": scope}
    g = (scope or {}).get("group")
    if g:
        rg = (row_groups or {}).get(g)
        if rg is None:
            raise ValueError(
                f"{name}: row group {g!r} is not defined in this mapping")
        base_name = rg.get("complementOf")
        if base_name:
            base = (row_groups or {}).get(base_name)
            if base is None or base.get("complementOf"):
                raise ValueError(
                    f"{name}: row group {g!r} complements {base_name!r}, "
                    "which is missing or itself a complement — chaining is "
                    "not allowed")
            return {"table": base.get("table"), "column": base.get("column"),
                    "op": base.get("op", "equals"),
                    "values": base.get("values") or [],
                    "label": g, "not": True}
        return {"table": rg.get("table"), "column": rg.get("column"),
                "op": rg.get("op", "equals"), "values": rg.get("values") or [],
                "label": g, "not": False}
    return {"table": scope.get("table"), "column": scope.get("column") or "",
            "op": scope.get("op", "equals"), "values": scope.get("values") or [],
            "label": scope.get("label"), "not": False}


def _dimension_ref(text: str, name: str, rs: dict, aliases: dict) -> str:
    """The compiled dimension column a scope predicate tests — found in the
    SQL itself as a CTE that BOTH exposes `max(<column>) AS <exposed>` AND
    reads FROM the scope table's registered view (the orgs precedent). The
    table check matters: every EAV CTE maxes C_EXT_ATTR* columns, so a
    column-name-only match could silently bind the fan-out CASE to a
    semantically unrelated pivot. No qualifying CTE REFUSES: register the
    lookup first."""
    col = rs.get("column") or ""
    table = (rs.get("table") or "").split(".")[-1].upper()
    if not col:
        raise ValueError(f"{name}: scope has no column")
    if not table:
        raise ValueError(f"{name}: scope declares no table")
    view = aliases.get(table)
    if not view:
        raise ValueError(
            f"{name}: scope table {table} is not a registered manifest "
            "source — add the source first")
    for cm in re.finditer(r"(?ms)^(\w+) AS \(.*?\n\),", text):
        block = cm.group(0)
        mm = re.search(rf"max\({re.escape(col)}\)\s+AS\s+(\w+)", block)
        if mm and re.search(rf"\bFROM {re.escape(view)}\b", block):
            return f"{cm.group(1)}.{mm.group(1)}"
    raise ValueError(
        f"{name}: scope column {col!r} of {table} is not exposed by any "
        f"compiled dimension CTE over '{view}' — register the lookup first "
        "(the orgs precedent)")


def _scope_predicate(name: str, rs: dict, col_ref: str) -> str:
    if rs.get("not"):
        inner = _scope_predicate(name, {**rs, "not": False}, col_ref)
        return f"NOT ({inner})"
    op, vals = rs["op"], rs["values"]
    if op == "startsWith":
        if not vals:
            raise ValueError(f"{name}: startsWith scope has no values")
        # escape LIKE wildcards so a prefix containing % or _ stays literal
        return " OR ".join(
            f"{col_ref} LIKE '"
            + str(v).replace("\\", "\\\\").replace("'", "''")
                    .replace("%", r"\%").replace("_", r"\_")
            + "%'"
            for v in vals)
    if op == "equals":
        if not vals:
            raise ValueError(f"{name}: scope has no value")
        return f"{col_ref} = {_cond_lit(vals[0])}"
    if op == "in":
        if not vals:
            raise ValueError(f"{name}: IN scope has no values")
        return f"{col_ref} IN ({', '.join(_cond_lit(v) for v in vals)})"
    raise ValueError(f"{name}: scope op {op!r} has no compiled form")


# ── mapping.json (hand-styled: surgical text edits) ─────────────────────────

def _entry_block(col: dict) -> str:
    body = json.dumps(col, indent=2, ensure_ascii=False)
    return "\n".join("    " + line for line in body.splitlines())


def _object_span(text: str, gold_name: str) -> tuple[int, int]:
    """Span of the column object whose goldColumnName == gold_name. Braces
    inside JSON STRING values are skipped — derivation.sql embeds arbitrary
    SQL text, and a brace in a literal (regexp_replace(x, '}', ...)) must not
    truncate the span (naive counting would corrupt the whole mapping)."""
    key = f'"goldColumnName": "{gold_name}"'
    at = text.find(key)
    if at < 0:
        raise KeyError(f"column {gold_name!r} not found in mapping")
    start = text.rfind("{", 0, at)
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise ValueError("unbalanced braces in mapping")


def mapping_upsert(text: str, col: dict, intent: str) -> str:
    if intent == "modify":
        s, e = _object_span(text, col["goldColumnName"])
        return text[:s] + _entry_block(col).lstrip() + text[e:]
    anchor = text.rfind("\n  ]")
    if anchor < 0:
        raise ValueError("mapping columns array closing not found")
    return text[:anchor] + ",\n" + _entry_block(col) + text[anchor:]


def _key_object_span(text: str, key: str) -> tuple[int, int]:
    """Span of the top-level object value of "key" (e.g. the transforms
    registry) — start of '{' to just past its matching '}'."""
    at = text.find(f'"{key}": {{')
    if at < 0:
        raise KeyError(f"{key} not found in mapping")
    start = text.index("{", at)
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise ValueError("unbalanced braces in mapping")


def mapping_upsert_transforms(text: str, rules: dict) -> str:
    """Merge the task's named transform rules into the mapping's transforms
    registry — a {"rule": ...} reference must never dangle. Existing entries
    are never overwritten (renaming/changing a shared rule is a reviewed edit
    of the registry itself, not a wizard side effect)."""
    doc = json.loads(text)
    existing = doc.get("transforms") or {}
    new = {k: v for k, v in (rules or {}).items() if k not in existing}
    if not new:
        return text

    def entry(k: str, v: dict) -> str:
        body = json.dumps(v, indent=2, ensure_ascii=False)
        lines = body.splitlines()
        body = "\n".join([lines[0]] + ["    " + ln for ln in lines[1:]])
        return f'    "{k}": {body}'

    addition = ",\n".join(entry(k, v) for k, v in new.items())
    if "transforms" in doc:
        s, e = _key_object_span(text, "transforms")
        head = text[:e - 1].rstrip()
        sep = ",\n" if existing else "\n"
        return head + sep + addition + "\n  " + text[e - 1:]
    # No registry yet: create it right before "batch" (canonical key order:
    # ... rowGroups, transforms, batch, columns).
    at = text.find('\n  "batch":')
    if at < 0:
        raise ValueError('"batch" anchor not found in mapping')
    return (text[:at] + '\n  "transforms": {\n' + addition + "\n  },"
            + text[at:])


# ── master.json (machine-formatted: JSON round-trip, indent=2) ──────────────

def master_prop(col: dict) -> dict:
    channels = [c for c in ("event", "message", "api")
                if col["channels"].get(c)]
    if col.get("kind") == "nested":
        # Arrays follow the registered encodings: a single field named by
        # itself is a VALUE array (webConsumingSystem: items {"type": ...});
        # multiple fields are a struct array (contributors). Arrays are
        # nullable at the column level; the SQL's COALESCE(..., array())
        # decides emptiness downstream.
        fields = col["fields"]

        def member(f: dict) -> dict:
            entry: dict = {"type": [JSON_TYPE[f["type"]], "null"]}
            # format keeps the date/timestamp intent the JSON type erases —
            # mirrors ui newTableArtifacts typed() so the two masters agree
            if f["type"] == "date":
                entry["format"] = "date"
            elif f["type"] == "timestamp":
                entry["format"] = "date-time"
            elif f["type"] == "float":
                # json_type_to_spark: "number" is exact DECIMAL unless the
                # property declares an approximate format — the SQL casts
                # floats to DOUBLE, so the master must say so
                entry["format"] = "double"
            return entry

        if len(fields) == 1:
            items: dict = {"type": JSON_TYPE[fields[0]["type"]]}
        else:
            items = {"type": "object", "additionalProperties": False,
                     "properties": {f["name"]: member(f) for f in fields}}
        return {"type": ["array", "null"],
                "description": col["businessDescription"],
                "items": items, "x-channels": channels}
    t = JSON_TYPE[col["dataType"]]
    prop: dict = {"type": [t, "null"] if col.get("nullable", True) else t,
                  "description": col["businessDescription"]}
    # date/timestamp map to JSON "string" — format keeps the intent the type
    # erases (registered masters carry it; ui newTableArtifacts mirrors this)
    if col["dataType"] == "date":
        prop["format"] = "date"
    elif col["dataType"] == "timestamp":
        prop["format"] = "date-time"
    elif col["dataType"] == "float":
        # approximate float: without this, json_type_to_spark derives
        # DECIMAL(38,10) while the scaffolded SQL casts to DOUBLE
        prop["format"] = "double"
    prop["x-channels"] = channels
    return prop


def _struct_member_prop(col: dict) -> dict:
    """Member entry inside a struct property. Members carry no x-channels —
    they inherit the struct's (ADR-0003 nested-channel rule; matches the six
    registered fulfillmentOrgDetails members). A NESTED member (an array
    inside the struct — fulfillmentOrgDetails.marketingInitiative, UIFPE-270)
    takes the array shape master_prop renders for a top-level array."""
    if col.get("kind") == "nested":
        entry = master_prop(col)
        entry.pop("x-channels", None)
        for f in (entry.get("items") or {}).get("properties", {}).values():
            f.pop("x-channels", None)   # element fields inherit like the member
        return entry
    entry: dict = {"type": [JSON_TYPE[col["dataType"]], "null"],
                   "description": col["businessDescription"]}
    if col["dataType"] == "date":
        entry["format"] = "date"
    return entry


def master_upsert(text: str, col: dict, intent: str) -> str:
    doc = json.loads(text)
    props = doc["properties"]
    name = col["goldColumnName"]
    struct, member = split_struct(name)
    if struct:
        # A dotted name is a field OF the struct — never a top-level property.
        if struct not in props or "properties" not in props[struct]:
            raise ValueError(f"{name}: struct column {struct!r} is not a "
                             "registered object property in master.json")
        props[struct]["properties"][member] = {
            **(props[struct]["properties"].get(member) or {}),
            **_struct_member_prop(col)}
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if intent == "modify" and name in props:
        # Merging preserves hand-added keys — but a KIND change makes the old
        # shape's keys wrong (UIFPE-108: an array losing its `items`, a scalar
        # gaining one). Detect it by the shape that actually shipped and
        # REPLACE instead of merging.
        was_array = "array" in (props[name].get("type") or [])
        now_array = col.get("kind") == "nested"
        props[name] = (master_prop(col) if was_array != now_array
                       else {**props[name], **master_prop(col)})
    else:
        # Append at the end of the BUSINESS columns: before the first
        # framework _-prefixed property, preserving everything else's order.
        rebuilt = {}
        inserted = False
        for k, v in props.items():
            if not inserted and k.startswith("_"):
                rebuilt[name] = master_prop(col)
                inserted = True
            rebuilt[k] = v
        if not inserted:
            rebuilt[name] = master_prop(col)
        doc["properties"] = rebuilt
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


# ── manifest.yaml (hand-commented: surgical text edits) ─────────────────────

def manifest_attr_block(col: dict, entity_key: list | None = None) -> str:
    kind = col.get("kind")
    if kind == "conditional":
        src = col["branches"][0]["source"]
        grain = (f"conditional ({len(col['branches'])} branches — "
                 f"see the intake mapping)")
    elif kind == "nested":
        # array rows share the column-level filter when the field carries none
        src = dict(col["fields"][0]["source"])
        src["filter"] = src.get("filter") or col.get("filter")
        grain = (f"nested array ({len(col['fields'])} fields, "
                 f"joinKey {col.get('joinKey')}) — see the intake mapping")
    else:
        src = col["source"]
        grain = derive_grain(src, entity_key)
    lines = [f"  {col['goldColumnName']}:",
             f"    pdh_table:  {src['table'].split('.')[-1].upper()}",
             f"    pdh_column: {src['column']}"]
    if src.get("filter"):
        lines.append(f'    pdh_filter: "{src["filter"]}"')
    lines.append(f"    pdh_grain:  {grain}")
    return "\n".join(lines) + "\n"


def _member_lineage_row(when: str, src: dict, scope: dict | None = None,
                        entity_key: list | None = None) -> str:
    """One `when:` row of a struct's conditional lineage list."""
    flt = f', pdh_filter: "{src["filter"]}"' if src.get("filter") else ""
    grain = derive_grain(src, entity_key)
    if scope and scope.get("group"):
        grain += f", proliferates via row group '{scope['group']}'"
    # pdh_column must be QUOTED: a nested member's column list carries commas
    # and unquoted would split into null-valued keys — valid YAML, silent loss.
    column = str(src["column"]).replace('"', "'")
    return (f'      - {{ when: "{when}", '
            f'pdh_table: {src["table"].split(".")[-1].upper()}, '
            f'pdh_column: "{column}"{flt}, '
            f'pdh_grain: "{grain}" }}\n')


def _manifest_struct_member(text: str, col: dict, struct: str, member: str,
                            entity_key: list | None = None) -> str:
    """A struct member joins the struct's EXISTING lineage entry and its
    column_types STRUCT<...> declaration — it must never become a 51st
    output_columns entry, because that list drives the physical DDL.

    Scalar members contribute ONE `when:` row (the registered
    `returnClass (scalar — not conditional)` shape); conditional members
    contribute one row PER BRANCH (`discountCode / 10002`,
    `discountCode / ELSE`) — five of the six registered members are
    conditional, so this is the majority shape, not the edge case."""
    # (?m) only — with DOTALL, `.*` would cross newlines and the block match
    # would swallow the rest of the file.
    entry_re = re.compile(rf"(?m)^  {re.escape(struct)}:\n(?:^    .*\n)+")
    m = entry_re.search(text)
    if not m:
        raise ValueError(f"{struct}: no canonical_lineage entry to attach "
                         f"member {member!r} to")
    if "conditional:" not in m.group(0):
        raise ValueError(f"{struct}: lineage entry has no conditional: list "
                         f"to receive member rows — attach {member!r} by hand")
    if col.get("kind") == "conditional":
        row = "".join(_member_lineage_row(f"{member} / {b['condition']}",
                                          b["source"], b.get("scope"),
                                          entity_key)
                      for b in col["branches"])
    elif col.get("kind") == "nested":
        # an array inside the struct: one row naming the element's fields
        src = dict(col["fields"][0]["source"])
        src["column"] = ", ".join(
            f"{f['source']['column']} ({f['name']})" for f in col["fields"])
        src["filter"] = src.get("filter") or col.get("filter")
        row = _member_lineage_row(f"{member}[] (scalar — not conditional)",
                                  src, None, entity_key)
    else:
        row = _member_lineage_row(f"{member} (scalar — not conditional)",
                                  col["source"], None, entity_key)
    text = text[:m.end()] + row + text[m.end():]
    # column_types: the struct's Spark DDL must agree with the SQL's
    # named_struct — append the member before the closing '>'.
    ct_re = re.compile(rf"(?m)^  {re.escape(struct)}: >-\n(?:^    .*\n)+")
    ct = ct_re.search(text)
    if not ct:
        raise ValueError(f"{struct}: no column_types STRUCT declaration found")
    block = ct.group(0)
    close = block.rfind(">")
    if close < 0:
        raise ValueError(f"{struct}: column_types block has no closing '>'")
    if col.get("kind") == "nested":
        fields = col["fields"]
        if len(fields) == 1:
            spark = f"ARRAY<{SPARK_TYPE[fields[0]['type']]}>"
        else:
            inner = ", ".join(f"{f['name']}: {SPARK_TYPE[f['type']]}"
                              for f in fields)
            spark = f"ARRAY<STRUCT<{inner}>>"
    else:
        spark = SPARK_TYPE[col["dataType"]]
    block2 = block[:close] + f", {member}: {spark}" + block[close:]
    return text[:ct.start()] + block2 + text[ct.end():]


def _manifest_nested_extras(text: str, col: dict) -> str:
    """ADR-0003: every ARRAY property needs array_sort_keys (stable channel
    hashes) and an explicit column_types declaration (auto-DDL agreement)."""
    name = col["goldColumnName"]
    sort_field = next((f["name"] for f in col["fields"] if f.get("sortKey")),
                      col["fields"][0]["name"])
    ask = re.search(r"(?m)^array_sort_keys:\n", text)
    if not ask:
        raise ValueError("array_sort_keys section not found in manifest")
    text = text[:ask.end()] + f"  {name}: [{sort_field}]\n" + text[ask.end():]
    fields = col["fields"]
    if len(fields) == 1:
        spark = f"ARRAY<{SPARK_TYPE[fields[0]['type']]}>"
    else:
        inner = ", ".join(f"{f['name']}: {SPARK_TYPE[f['type']]}" for f in fields)
        spark = f"ARRAY<STRUCT<{inner}>>"
    ct = re.search(r"(?m)^column_types:\n", text)
    if not ct:
        raise ValueError("column_types section not found in manifest")
    return text[:ct.end()] + f"  {name}: {spark}\n" + text[ct.end():]


def manifest_upsert(text: str, col: dict, intent: str,
                    entity_key: list | None = None) -> str:
    name = col["goldColumnName"]
    struct, member = split_struct(name)
    if struct:
        return _manifest_struct_member(text, col, struct, member, entity_key)
    block = manifest_attr_block(col, entity_key)
    # (?m) only — DOTALL would let the block match run to end-of-file, so a
    # modify would replace everything below the entry (latent since UIFPE-100;
    # surfaced by the struct-member work).
    entry_re = re.compile(rf"(?m)^  {re.escape(name)}:\n(?:^    .*\n)+")
    if intent == "modify" and entry_re.search(text):
        text = entry_re.sub(block, text, count=1)
    else:
        # lineage entries live under canonical_lineage:, which runs up to the
        # next top-level key (schema:) — append at the section's end.
        anchor = text.find("\nschema:")
        if anchor < 0:
            raise ValueError("schema: anchor (end of canonical_lineage) not found")
        text = text[:anchor] + "\n" + block + text[anchor:]
        # output_columns: append after the LAST "  - name" item of the list —
        # searched within the section only (up to the next top-level key), so
        # a block-style list under a later key such as null_sentinels
        # (UIFPE-268) can never capture the new column.
        oc = text.find("\noutput_columns:")
        if oc < 0:
            raise ValueError("output_columns anchor not found in manifest")
        body_start = oc + len("\noutput_columns:")
        nxt = re.search(r"(?m)^\S", text[body_start:])
        section_end = body_start + nxt.start() if nxt else len(text)
        items = list(re.finditer(r"(?m)^  - \S+$", text[oc:section_end]))
        if not items:
            raise ValueError("output_columns list items not found")
        end = oc + items[-1].end()
        text = text[:end] + f"\n  - {name}" + text[end:]
        if col.get("kind") == "nested":
            text = _manifest_nested_extras(text, col)
    return text


# ── product.sql (AUTO classes only) ─────────────────────────────────────────

def _append_select_expr(text: str, expr: str, name: str) -> str:
    """Append `expr AS name` as the LAST column of the final SELECT."""
    at = text.rfind("\nFROM items\n")
    if at < 0:
        raise ValueError("final SELECT 'FROM items' anchor not found")
    head = text[:at]
    last_nl = head.rfind("\n")
    last_line = head[last_nl + 1:]
    if not last_line.rstrip().endswith(","):
        head = head[:last_nl + 1] + last_line.rstrip() + ","
    pad = max(1, 45 - 2 - len(expr))
    return head + f"\n  {expr}{' ' * pad}AS {name}" + text[at:]


def _insert_struct_member(text: str, struct: str, member: str,
                          expr: str) -> str:
    """Add a member to the struct's named_struct AND its all-null gate. Both
    edits happen in one pass — the gate and the struct must always agree, or
    a row whose only non-null field is the new member would collapse the
    whole struct to NULL and silently lose the value."""
    closer = re.search(rf"(?m)^  \) END\s+AS {re.escape(struct)},?$", text)
    if not closer:
        raise ValueError(f"named_struct closer for {struct!r} not found")
    # last member line gains a comma; the new member slots in before ') END'
    head = text[:closer.start()]
    last_nl = head.rfind("\n")
    last_line = head[last_nl + 1:]
    if not last_line.rstrip().endswith(","):
        head = head[:last_nl + 1] + last_line.rstrip() + ","
    text = head + f"\n    '{member}', {expr}\n" + text[closer.start():]
    # the gate: the LAST 'THEN NULL / ELSE named_struct(' before the closer
    window = re.search(rf"(?m)^  \) END\s+AS {re.escape(struct)},?$",
                       text).start()
    gates = list(re.finditer(r"(?m)^(\s+)THEN NULL\n\s+ELSE named_struct\($",
                             text[:window]))
    if not gates:
        raise ValueError(f"all-null gate for {struct!r} not found")
    g = gates[-1]
    indent = " " * (len(g.group(1)) + 1)   # AND lines sit one space deeper
    return (text[:g.start()] + f"{indent}AND {expr} IS NULL\n"
            + text[g.start():])


def _eav_cte_upsert(text: str, name: str, group: str, leaf: str, src_col: str,
                    org: bool, description: str,
                    extra_where: str = "") -> tuple[str, str, str]:
    """Ensure `max(src_col) AS leaf` exists in the right EAV CTE, creating the
    CTE + its LEFT JOIN when missing. Returns (text, cte_name, note).

    extra_where — a branch's discriminator predicate (UIFPE-106) — is part of
    the CTE's IDENTITY: extending a CTE whose WHERE does not carry it would
    leak rows across branches, so a predicate mismatch REFUSES. When the plain
    ag<N> name is taken by the other grain, a branch may take the _org-suffixed
    slot (the ag1403 vs ag1403_org hand convention)."""

    def _find(cname: str):
        return re.compile(rf"(?ms)^{cname} AS \(.*?\n\),").search(text)

    def _where_of(cte: str) -> str:
        w = re.search(r"WHERE\s+(.*?)\s+GROUP BY", cte, re.S)
        return re.sub(r"\s+", " ", w.group(1)) if w else ""

    def _predicate_matches(where_norm: str) -> bool:
        # exact conjunct-set equality, BOTH directions: a looser CTE would
        # leak rows into this branch, and a stricter hand CTE (extra
        # conjuncts) would silently suppress this column's values — the
        # 2026-08-10 D1 shape. Set comparison tolerates conjunct order.
        want = {f"ATTR_GROUP_ID = {group}"}
        if extra_where:
            want.add(extra_where)
        return set(where_norm.split(" AND ")) == want

    def _extend(mm, cname: str) -> str:
        cte = mm.group(0)
        max_lines = list(re.finditer(r"(?m)^(\s+)max\(.*?\) +AS +\w+(,?)$", cte))
        if not max_lines:
            raise ValueError(f"{cname}: no max() lines found")
        last = max_lines[-1]
        insert_at = last.end()
        cte2 = cte[:insert_at] + ("" if last.group(2) else ",") + \
            f"\n{last.group(1)}max({src_col}) AS {leaf}" + cte[insert_at:]
        return text[:mm.start()] + cte2 + text[mm.end():]

    cte_name = f"ag{group}"
    m = _find(cte_name)
    if m and org and extra_where and "GROUP BY 1, 2" not in m.group(0):
        # plain name is taken by the item grain: an org-grain branch takes
        # the _org-suffixed slot instead
        cte_name = f"ag{group}_org"
        m = _find(cte_name)
    if m:
        # existing CTE: grain must MATCH the declared join — extending an
        # item-level CTE with an org-level column (or vice versa) would
        # silently aggregate at the wrong grain (the GAP 1 failure shape).
        cte_org = "GROUP BY 1, 2" in m.group(0)
        if org != cte_org:
            raise ValueError(
                f"{name}: declared grain is "
                f"{'(item, org)' if org else 'item-level'} but CTE {cte_name} "
                f"aggregates {'per item' if org else 'per (item, org)'} — "
                "a shared attribute group cannot serve two grains; use a "
                "separate CTE (see ag1403 vs ag1403_org)")
        if not _predicate_matches(_where_of(m.group(0))):
            raise ValueError(
                f"{name}: CTE {cte_name} pivots group {group} with WHERE "
                f"'{_where_of(m.group(0))}', which does not exactly match "
                f"this column's declared predicate — one CTE cannot serve "
                "two branch conditions; write a distinct hand-named CTE")
        return _extend(m, cte_name), cte_name, f"extended existing CTE {cte_name}"
    where = f"ATTR_GROUP_ID = {group}" + \
        (f" AND {extra_where}" if extra_where else "")
    if org:
        # new org-level group: (item, org) grain — oid projection,
        # GROUP BY 1, 2, two-key join. Anchored in the org-level section
        # (after ag822) so the file keeps its item/org grouping.
        new_cte = (f"{cte_name} AS (   -- {description}\n"
                   f"  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, "
                   f"CAST(ORGANIZATION_ID AS BIGINT) AS oid,\n"
                   f"         max({src_col}) AS {leaf}\n"
                   f"  FROM items_ext WHERE {where} GROUP BY 1, 2\n"
                   f"),\n")
        anchor = re.search(r"(?ms)^ag822 AS \(.*?\n\),\n", text)
        if not anchor:
            raise ValueError("ag822 anchor CTE (org-level section) not found")
        text = text[:anchor.end()] + new_cte + text[anchor.end():]
        join = (f"LEFT JOIN {cte_name}   "
                f"ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = {cte_name}.iid\n"
                f"              AND CAST(items.ORGANIZATION_ID AS BIGINT) "
                f"= {cte_name}.oid")
        j = re.search(r"(?m)^LEFT JOIN ag822 .*\n\s+AND .*$", text)
        if not j:
            raise ValueError("ag822 two-key join anchor not found")
        text = text[:j.end()] + "\n" + join + text[j.end():]
        return text, cte_name, \
            f"created CTE {cte_name} (org-level) + two-key LEFT JOIN"
    # new item-level group: CTE after ag802's block + join after ag802's
    new_cte = (f"{cte_name} AS (   -- {description}\n"
               f"  SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,\n"
               f"         max({src_col}) AS {leaf}\n"
               f"  FROM items_ext WHERE {where} GROUP BY 1\n"
               f"),\n")
    anchor = re.search(r"(?ms)^ag802 AS \(.*?\n\),\n", text)
    if not anchor:
        raise ValueError("ag802 anchor CTE not found")
    text = text[:anchor.end()] + new_cte + text[anchor.end():]
    join = (f"LEFT JOIN {cte_name}  "
            f"ON CAST(items.INVENTORY_ITEM_ID AS BIGINT) = {cte_name}.iid")
    j = re.search(r"(?m)^LEFT JOIN ag802 .*$", text)
    if not j:
        raise ValueError("ag802 join anchor not found")
    text = text[:j.end()] + "\n" + join + text[j.end():]
    return text, cte_name, f"created CTE {cte_name} + LEFT JOIN"


def sql_add_eav(text: str, col: dict, group: str,
                registry: dict) -> tuple[str, str]:
    name, src_col = col["goldColumnName"], col["source"]["column"]
    struct, leaf = split_struct(name)
    org = eav_org_level(col)
    text, cte_name, note = _eav_cte_upsert(
        text, name, group, leaf, src_col, org, col["businessDescription"])
    # The transform wraps the PIVOTED value in the final SELECT — the CTE's
    # max() pick stays untouched, matching the hand-written unit*/coverImage
    # shapes.
    expr = render_transform(col.get("transform"), registry, f"{cte_name}.{leaf}")
    if expr != f"{cte_name}.{leaf}":
        note += " + transform in final SELECT"
    if struct:
        return _insert_struct_member(text, struct, leaf, expr), \
            note + f" + member of {struct}"
    return _append_select_expr(text, expr, name), note


def sql_add_conditional(text: str, col: dict, registry: dict,
                        row_groups: dict, manifest_text: str) -> tuple[str, str]:
    """AUTO conditional (UIFPE-106): one EAV CTE per branch carrying the
    discriminator predicate in its WHERE (the ag1408/ag1417 hand shape),
    picked by a strict COALESCE — named branches first, ELSE last, matching
    product.sql's digital-first convention — with scoped branches fanning out
    through a CASE on a compiled dimension column (the ukOrgs/nonUkOrgs
    shape). Everything the wizard preview renders with a {placeholder} to
    survive half-typed input REFUSES here instead."""
    name = col["goldColumnName"]
    struct, leaf = split_struct(name)
    if struct:
        raise ValueError(f"{name}: struct-member conditionals stay PLAN — "
                         "the named_struct + all-null gate are hand-written")
    disc = (col.get("discriminator") or {}).get("column")
    if not disc:
        raise ValueError(f"{name}: conditional declares no discriminator column")
    parsed = []
    for b in col.get("branches") or []:
        if b.get("fallback") == "fallbackToElse":
            raise ValueError(
                f"{name}: fallbackToElse has no deterministic rendering — the "
                "strict-pick contract compiles returnNull only (all 60 "
                "registered branches); restructure the branch or keep the "
                "column PLAN")
        if b.get("transform") is not None:
            raise ValueError(
                f"{name}: branch-level transforms stay PLAN — applied outside "
                "the CTE they would evaluate transform(NULL) for keys the "
                "branch has no rows for, breaking the strict pick")
        group = _branch_eav_group(b)
        if group is None:
            raise ValueError(
                f"{name}: branch {b.get('condition')!r} is not bare-EAV-shaped "
                "(items_ext + a filter that is exactly the ATTR_GROUP_ID "
                "equality) — richer filters and mixed sources are hand-written")
        cond = parse_branch_condition(b.get("condition") or "")
        for v in cond["values"]:
            # the blocker shape: '10002 Digital' (parens forgotten) would
            # otherwise quote as a string literal, compare NULL against the
            # numeric discriminator, and silently empty EVERY branch
            if not re.fullmatch(r"[\w.-]+", v, re.ASCII):
                raise ValueError(
                    f"{name}: branch condition value {v!r} does not parse "
                    "cleanly — labels go in parentheses: "
                    "'10002 (Digital Catalog)'")
        parsed.append({"branch": b, "cond": cond, "group": group,
                       "org": eav_org_level({"source": b["source"]})})
    if len(parsed) < 2:
        raise ValueError(f"{name}: a conditional needs at least two branches")
    groups = [p["group"] for p in parsed]
    dup = sorted({g for g in groups if groups.count(g) > 1})
    if dup:
        raise ValueError(
            f"{name}: branches share attribute group(s) {', '.join(dup)} — "
            "each branch needs its own CTE; hand-name the variants "
            "(the ag1403/ag1403_org precedent)")
    combos = [(p["cond"]["op"], tuple(p["cond"]["values"]),
               json.dumps(p["branch"].get("scope"), sort_keys=True))
              for p in parsed]
    if len(set(combos)) != len(combos):
        raise ValueError(f"{name}: two branches share the same condition and "
                         "scope — the strict pick cannot order them")
    # strict-pick disjointness: two named branches sharing a discriminator
    # value must BOTH be scoped (the Proliferate shape, where the scopes
    # partition the rows) — otherwise COALESCE order silently decides.
    named = [p for p in parsed if p["cond"]["op"] != "else"]
    for i, a in enumerate(named):
        for b in named[i + 1:]:
            if set(a["cond"]["values"]) & set(b["cond"]["values"]) \
                    and not (a["branch"].get("scope")
                             and b["branch"].get("scope")):
                raise ValueError(
                    f"{name}: branches {a['branch'].get('condition')!r} and "
                    f"{b['branch'].get('condition')!r} overlap on a "
                    "discriminator value — the strict pick cannot order "
                    "them; scope both branches or make the conditions "
                    "disjoint")
    # one CASE arm per scope: two branches with the SAME scope would render
    # a dead, unreachable WHEN and silently lose the second branch's values
    seen_scopes: dict = {}
    for p in parsed:
        sc = p["branch"].get("scope")
        if sc:
            k = json.dumps(sc, sort_keys=True)
            if k in seen_scopes:
                raise ValueError(
                    f"{name}: two branches share the same scope — the "
                    "fan-out CASE cannot order them")
            seen_scopes[k] = p
    named_values = list(dict.fromkeys(
        v for p in parsed if p["cond"]["op"] != "else"
        for v in p["cond"]["values"]))

    notes = []
    for p in parsed:
        pred = _branch_where(name, disc, p["cond"], named_values)
        text, cte_name, frag = _eav_cte_upsert(
            text, name, p["group"], leaf, p["branch"]["source"]["column"],
            p["org"],
            f"{name}: {p['branch'].get('condition')}",
            extra_where=pred)
        p["pick"] = f"{cte_name}.{leaf}"
        notes.append(frag)

    # scopes resolve AFTER the CTEs land, so a scope can reference a
    # dimension column the same compile just created
    scoped, unscoped, remainder = [], [], None
    for p in parsed:
        sc = p["branch"].get("scope")
        if not sc:
            unscoped.append(p)
            continue
        rs = _resolve_scope(name, sc, row_groups)
        if rs.get("not") or rs.get("op") == "else":
            if remainder is not None:
                raise ValueError(
                    f"{name}: two branches claim the remaining rows — only "
                    "one complement/else scope fits the CASE's ELSE slot")
            remainder = p
        else:
            scoped.append((p, rs))
    unscoped.sort(key=lambda p: p["cond"]["op"] == "else")
    if scoped or remainder:
        if not scoped:
            raise ValueError(
                f"{name}: a complement-scoped branch needs at least one "
                "positively scoped sibling branch")
        aliases = _manifest_source_aliases(manifest_text)
        whens = [
            f"WHEN {_scope_predicate(name, rs, _dimension_ref(text, name, rs, aliases))}"
            f" THEN {p['pick']}" for p, rs in scoped]
        case = ("CASE " + " ".join(whens)
                + (f" ELSE {remainder['pick']}" if remainder else "")
                + " END")
        args = [case] + [p["pick"] for p in unscoped]
    else:
        args = [p["pick"] for p in unscoped]
    pick = args[0] if len(args) == 1 else f"COALESCE({', '.join(args)})"
    expr = render_transform(col.get("transform"), registry, pick)
    note = (f"conditional ({len(parsed)} branches): " + "; ".join(notes)
            + "; strict pick "
            + ("CASE + COALESCE" if (scoped or remainder) else "COALESCE")
            + " in final SELECT")
    if expr != pick:
        note += " + transform after the pick"
    return _append_select_expr(text, expr, name), note


def _manifest_source_aliases(manifest_text: str) -> dict:
    """silver table name (last dot segment, uppercased) -> registered runtime
    alias. Only manifest sources exist as views at runtime, so a lookup
    against anything else must refuse. First registration wins (items over
    items_all for the driving table)."""
    aliases: dict = {}
    for part in re.split(r"(?m)^\s*- alias:\s*", manifest_text)[1:]:
        alias = part.split(None, 1)[0]
        tm = re.search(r"silver_table:\s*(\S+)", part)
        if tm:
            aliases.setdefault(tm.group(1).split(".")[-1].upper(), alias)
    return aliases


def sql_add_lookup(text: str, col: dict, registry: dict,
                   manifest_text: str) -> tuple[str, str]:
    """AUTO lookup-join (UIFPE-106): a dedicated dimension CTE over a
    REGISTERED manifest source (the orgs precedent) — max() per key so the
    join can never fan the driving rows out, _ID keys CAST to BIGINT on both
    sides, the declared filter folded into the CTE's WHERE, LEFT JOIN first
    in the FROM block."""
    name = col["goldColumnName"]
    struct, leaf = split_struct(name)
    src = col["source"]
    table = (src.get("table") or "").split(".")[-1].upper()
    view = _manifest_source_aliases(manifest_text).get(table)
    if not view:
        raise ValueError(
            f"{name}: lookup table {table} is not a registered manifest "
            "source — the runtime only registers manifest sources as views; "
            "add the source (manifest sources + key_mapping) first")
    join_type = (src.get("join") or {}).get("type") or "left"
    if join_type != "left":
        raise ValueError(f"{name}: join type {join_type!r} — the max()-per-key "
                         "dimension-CTE shape only supports LEFT")
    on = (src.get("join") or {}).get("on") or []
    if not on:
        raise ValueError(f"{name}: lookup-join declares no join.on pairs")
    cte_name = f"lk_{leaf}"
    if re.search(rf"(?m)^{cte_name} AS \(", text):
        raise ValueError(f"{name}: CTE {cte_name} already exists in the SQL")
    keyname = {"INVENTORY_ITEM_ID": "iid", "ORGANIZATION_ID": "oid"}
    keys, terms, used = [], [], set()
    for i, o in enumerate(on):
        lc = (o.get("leftColumn") or "").upper()
        if not lc:
            raise ValueError(f"{name}: lookup-join on-pair declares no "
                             "leftColumn")
        rc = (o.get("rightColumn") or lc).upper()
        k = keyname.get(lc, f"k{i + 1}")
        if k in used:   # two pairs sharing a left column must not collide
            k = f"k{i + 1}"
        used.add(k)
        keys.append((f"CAST({rc} AS BIGINT)" if rc.endswith("_ID") else rc)
                    + f" AS {k}")
        left = f"CAST(items.{lc} AS BIGINT)" if lc.endswith("_ID") \
            else f"items.{lc}"
        terms.append(f"{left} = {cte_name}.{k}")
    where = f" WHERE {src['filter']}" if src.get("filter") else ""
    group_by = ", ".join(str(i + 1) for i in range(len(keys)))
    new_cte = (f"{cte_name} AS (   -- {col.get('businessDescription') or name}\n"
               f"  SELECT {', '.join(keys)},\n"
               f"         max({src['column']}) AS {leaf}\n"
               f"  FROM {view}{where} GROUP BY {group_by}\n"
               f"),\n")
    # dimension CTEs live at the top of the file: after the orgs block when
    # it exists, directly under WITH otherwise
    anchor = re.search(r"(?ms)^orgs AS \(.*?\n\),\n", text)
    if anchor:
        at = anchor.end()
    else:
        w = re.search(r"(?m)^WITH\n", text)
        if not w:
            raise ValueError("WITH anchor not found")
        at = w.end()
    text = text[:at] + new_cte + text[at:]
    join = f"LEFT JOIN {cte_name} ON {terms[0]}"
    for t in terms[1:]:
        join += f"\n              AND {t}"
    fm = text.find("\nFROM items\n")
    if fm < 0:
        raise ValueError("final SELECT 'FROM items' anchor not found")
    # after the orgs dimension join when it exists (the hand file's join
    # order), directly after FROM items otherwise
    oj = re.compile(r"(?m)^LEFT JOIN orgs .*$").search(text, fm)
    at = oj.end() if oj else fm + len("\nFROM items")
    text = text[:at] + "\n" + join + text[at:]
    expr = render_transform(col.get("transform"), registry,
                            f"{cte_name}.{leaf}")
    note = (f"lookup: created dimension CTE {cte_name} over registered "
            f"source '{view}' + LEFT JOIN")
    if expr != f"{cte_name}.{leaf}":
        note += " + transform in final SELECT"
    if struct:
        return _insert_struct_member(text, struct, leaf, expr), \
            note + f" + member of {struct}"
    return _append_select_expr(text, expr, name), note


def sql_modify_eav(text: str, col: dict, group: str,
                   registry: dict) -> tuple[str, str]:
    name, src_col = col["goldColumnName"], col["source"]["column"]
    _, leaf = split_struct(name)   # CTE aliases always use the LEAF name
    line_re = re.compile(rf"(?m)^(\s+)max\(\w+\)( +)AS {re.escape(leaf)}(,?)$")
    if not line_re.search(text):
        raise ValueError(f"max(...) AS {leaf} not found — modify needs the "
                         f"column to exist in ag{group}")
    text = line_re.sub(rf"\1max({src_col})\2AS {leaf}\3", text, count=1)
    note = f"repointed max() to {src_col} in ag{group}"
    if col.get("transform") is not None:
        # The final-SELECT expression for an existing column may be a
        # hand-tuned block — rewriting it in place is a manual edit.
        note += ("\n[PLAN] transform on modify — ensure the final SELECT "
                 f"reads: {render_transform(col['transform'], registry, f'ag{group}.{leaf}')}"
                 f" AS {leaf}")
    return text, note


def sql_add_driving(text: str, col: dict, registry: dict) -> tuple[str, str]:
    name, src_col = col["goldColumnName"], col["source"]["column"]
    if col["source"].get("filter"):
        # UIFPE-260 review finding: this writer never applied a filter, so a
        # declared one was silently dropped from the SQL while the manifest
        # recorded it — the exact drift _bare_eav_group refuses. A filter the
        # shipped WHERE already applies (product's SEGMENT1 domain) is fine;
        # a predicate the SQL does not contain is not.
        want = re.sub(r"\s+", " ", col["source"]["filter"].strip())
        want = re.sub(r"\b[A-Za-z_]\w*\.(?=[A-Za-z_])", "", want)
        # only the FINAL SELECT restricts driving rows — a match inside an
        # EAV CTE's WHERE or a comment is not the driving domain
        region = text[text.rfind("\nSELECT"):]
        # whole-line AND trailing comments out; word-bounded match so a
        # near-miss predicate (= 224 inside = 2240) cannot false-accept
        region = "\n".join(l.split("--", 1)[0] for l in region.splitlines())
        region = re.sub(r"\s+", " ", region)
        # the shipped WHERE may alias-qualify its columns (items.SEGMENT1);
        # the intake never does — compare with the qualifiers removed
        region = re.sub(r"\b[A-Za-z_]\w*\.(?=[A-Za-z_])", "", region)
        if not re.search(rf"(?<![\w]){re.escape(want)}(?![\w])", region):
            raise ValueError(
                f"column {name!r}: filter {want!r} is not applied by the "
                f"entity's SQL — the driving SELECT has no per-column WHERE, "
                f"so it would be recorded in the manifest and silently "
                f"dropped from the query. Restrict a NEW entity's rows with "
                f"drivingFilter; a shipped entity's domain is an engineer "
                f"change.")
    struct, leaf = split_struct(name)
    expr = f"items.{src_col}"
    if col["dataType"] == "integer":
        expr = f"CAST(items.{src_col} AS BIGINT)"
    expr = render_transform(col.get("transform"), registry, expr)
    if is_aggregate(expr):
        # An aggregate in a grain-preserving SELECT is invalid SQL (no GROUP
        # BY exists to absorb it) — previously this compiled and broke at
        # runtime. Aggregates are legal only where the scaffolder owns the
        # whole statement shape and groups by the entity key.
        raise ValueError(
            f"column {name!r}: transform renders an aggregate ({expr!r}) — "
            f"a registered entity's SELECT is one row per driving row, so "
            f"an aggregate cannot compile here. Aggregates are supported "
            f"when scaffolding a new entity (grouped by its entity key).")
    if struct:
        return _insert_struct_member(text, struct, leaf, expr), \
            f"driving-table expression added as member of {struct}"
    return _append_select_expr(text, expr, name), "driving-table expression added"


# ── pipeline config (schedule) ──────────────────────────────────────────────

# The dispatcher's cadences (EventBridge rules are infra; which bucket a
# table subscribes to is config-as-code — pipelines/base/<domain>.yaml).
# UIFPE-149: defined once in gold_frequencies.py, imported here rather than
# re-listed. The compiler WRITES canonical names only — legacy aliases are
# accepted by the dispatcher and the validator for already-deployed configs,
# but nothing should author a new one into a YAML.
VALID_FREQUENCIES = set(TRANSFORM_FREQUENCIES)


def sql_add_derived(text: str, col: dict) -> tuple[str, str]:
    """Splice a derived column (ADR-0008: the query is the contract) into the
    entity's gold SQL. The mapping's derivation.sql becomes the d_<name> CTE
    VERBATIM, LEFT-joined on the declared key contract and selected as the
    column — the wrap is the only transformation, so the SQL the reviewer
    approved is the SQL that runs. Anchors are the shared file conventions
    (final `)\\n\\nSELECT`, `FROM items`); a file that breaks them refuses
    loudly rather than splicing somewhere surprising."""
    name = col["goldColumnName"]
    cte, join, select_expr = derivation_render_wrap(
        name, col["derivation"], kind=col.get("kind"))
    if f"d_{name} AS (" in text:
        raise ValueError(
            f"{name}: CTE d_{name} already exists in the SQL — a derived "
            "column is added once; changing it is plan:derived-sql-modify")
    closers = list(re.finditer(r"(?m)^\)(?=\n\nSELECT\b)", text))
    if not closers:
        raise ValueError("derived splice: final ')\\n\\nSELECT' anchor not "
                         "found — the gold SQL does not follow the shared "
                         "CTE-block convention")
    at = closers[-1]
    text = text[:at.start()] + "),\n" + cte + text[at.end():]
    text = _append_select_expr(text, select_expr, name)
    from_at = text.rfind("\nFROM items\n")
    wm = re.compile(r"(?m)^WHERE ").search(text, from_at)
    if wm:
        text = text[:wm.start()] + join + "\n" + text[wm.start():]
    else:
        text = text.rstrip("\n") + "\n" + join + "\n"
    n_lines = len(col["derivation"]["sql"].splitlines())
    pairs = ", ".join(f"{k['entityColumn']}={k['queryColumn']}"
                      for k in col["derivation"]["keyedBy"])
    return text, (f"spliced d_{name} ({n_lines}-line query, verbatim) + "
                  f"LEFT JOIN on {pairs} + select")


def pipeline_set_frequency(text: str, frequency: str) -> str:
    """Point schedule.frequency at a dispatcher cadence (UIFPE-107 — the
    wizard's Job properties step edits this; sync_pipelines ships it)."""
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"frequency {frequency!r} is not a dispatcher "
                         f"cadence — one of {sorted(VALID_FREQUENCIES)}")
    m = re.search(r"(?sm)^schedule:.*?^(  frequency: )(\S+)", text)
    if not m:
        raise ValueError("schedule.frequency not found in the pipeline config")
    return text[:m.start(2)] + frequency + text[m.end(2):]


def pipeline_set_start_time(text: str, start_time: str) -> str:
    """Point schedule.start_time at a UTC time of day (UIFPE-149).

    The cadence says how OFTEN; this says from WHEN, and together they define the
    pipeline's slots. Two daily tables can now run at different times, which is
    the whole reason the field exists.

    Unlike frequency, the key may be ABSENT from an older YAML — it is inserted
    directly beneath `frequency:` in that case, which keeps the two halves of the
    schedule adjacent and readable rather than appending it at the end of the
    block where a reviewer would miss it.

    THE VALUE IS ALWAYS QUOTED, and that is not cosmetic. YAML 1.1 — which PyYAML
    implements, and sync_pipelines.py uses PyYAML — parses an unquoted sexagesimal
    like `6:30` as the INTEGER 390. The config would then carry a number where a
    string belongs, parse_time_of_day would reject it, and the pipeline would
    silently fall back to its cadence default instead of the time someone chose.
    gold_config.validate catches that at sync time, but writing quotes means it
    never has to."""
    if parse_time_of_day(start_time) is None:
        raise ValueError(f"start_time {start_time!r} is not a UTC time of day "
                         f'as "HH:MM" (00:00-23:59)')
    quoted = f'"{start_time}"'
    # Existing value may or may not be quoted; match either and normalize.
    m = re.search(r'(?sm)^schedule:.*?^(  start_time: )("?[^"\s]+"?)', text)
    if m:
        return text[:m.start(2)] + quoted + text[m.end(2):]
    f = re.search(r"(?m)^(  frequency: )(\S+)(.*)$", text)
    if not f:
        raise ValueError("schedule.frequency not found in the pipeline config")
    return text[:f.end()] + f"\n  start_time: {quoted}" + text[f.end():]


_WEEKLY_DAYS_LINE = re.compile(r"(?m)^  weekly_days: .*\n")


def _pipeline_frequency(text: str) -> str | None:
    m = re.search(r"(?sm)^schedule:.*?^  frequency: (\S+)", text)
    return m.group(1) if m else None


def pipeline_set_weekly_days(text: str, days) -> str:
    """Write the weekly rung's UTC day set (UIFPE-532) as a YAML flow list,
    `weekly_days: [TUE, THU]`, beside the time it belongs to.

    Only the weekly rung reads the field, and gold_config.validate rejects it
    anywhere else - so the compiler refuses to write it for another cadence
    rather than producing a YAML that cannot sync. The frequency setter runs
    first in compile_delta, so this sees the cadence the envelope asked for."""
    parsed = parse_weekly_days(days)
    if parsed is None:
        raise ValueError(f"weekly_days {days!r} is not a non-empty list of "
                         f"UTC weekdays {WEEKDAYS}")
    freq = _pipeline_frequency(text)
    if freq != WEEKLY_FREQUENCY:
        raise ValueError(f"weekly_days only apply to {WEEKLY_FREQUENCY}; the "
                         f"pipeline is on {freq!r}")
    line = f"  weekly_days: [{', '.join(parsed)}]"
    if _WEEKLY_DAYS_LINE.search(text):
        return _WEEKLY_DAYS_LINE.sub(line + "\n", text, count=1)
    anchor = re.search(r'(?m)^  start_time: .*$', text) \
        or re.search(r"(?m)^  frequency: .*$", text)
    if not anchor:
        raise ValueError("schedule.frequency not found in the pipeline config")
    return text[:anchor.end()] + "\n" + line + text[anchor.end():]


def pipeline_clear_weekly_days(text: str) -> str:
    """Drop the day set when an entity leaves the weekly rung - a stale
    weekly_days beside a daily cadence would fail gold_config.validate."""
    return _WEEKLY_DAYS_LINE.sub("", text, count=1)


# ── PLAN rendering for the manual classes ───────────────────────────────────

def render_plan(col: dict, cls: str, registry: dict | None = None,
                entity_key: list | None = None) -> str:
    src = col.get("source", {})
    registry = registry or {}
    out = [f"[PLAN] {col['goldColumnName']} ({cls}) — structured artifacts "
           f"updated automatically; apply this SQL by hand:"]
    if cls == "plan:derived-sql-modify":
        # ADR-0008: the query is the contract, and ADDING a derived column
        # splices it automatically (sql_add_derived). The one PLAN path left
        # is a MODIFY: the migrated columns' shipped SQL predates the
        # d_<name> wrap shape, and machine-editing hand-shaped CTEs is the
        # silent-damage class this platform refuses — the engineer places
        # the new query by hand instead.
        out.append("  derived column (ADR-0008 — the query is the contract):")
        out.extend(derivation_plan_lines(col["derivation"],
                                         col["goldColumnName"],
                                         col.get("kind")))
        out.append("  modify: replace the column's existing CTE/expression "
                   "with the query above, keep the terminal LEFT join on the "
                   "declared contract, then run --refresh_columns for "
                   "historical rows.")
        return "\n".join(out)
    if cls in ("plan:lookup-join", "plan:lookup-join-modify"):
        j = src["join"]
        on = " AND ".join(f"{o['leftTable'].split('.')[-1]}.{o['leftColumn']} "
                          f"= {src['table'].split('.')[-1]}.{o['rightColumn']}"
                          for o in j["on"])
        out += [f"  {j.get('type', 'left').upper()} JOIN "
                f"{src['table'].split('.')[-1]} ON {on}",
                f"  SELECT: {src['table'].split('.')[-1].lower()}"
                f".{src['column']} AS {col['goldColumnName']}",
                "  anchor: org dimension section of product.sql (see orgs CTE)"]
    elif cls in ("plan:conditional", "plan:conditional-modify"):
        for i, b in enumerate(col.get("branches", []), 1):
            bs = b["source"]
            out.append(f"  branch {i}: {b['condition']} <- "
                       f"{bs['table'].split('.')[-1]}.{bs['column']}"
                       f"{' WHERE ' + bs['filter'] if bs.get('filter') else ''}"
                       f" [{derive_grain(bs, entity_key)}]"
                       + (f" scope={b['scope']}" if b.get("scope") else "")
                       + (f" transform: {_plan_transform_note(b['transform'], registry)}"
                          if b.get("transform") is not None else ""))
        out.append("  pattern: one CTE per branch + strict-pick COALESCE "
                   "(scoped branches fan out via CASE) — see ag1408/ag1417.")
        if col.get("transform") is not None:
            out.append("  after the pick, wrap the COALESCE in: "
                       + _plan_transform_note(col["transform"], registry)
                       + "  (the coverImage* shape)")
    elif cls.startswith("plan:kind-change-"):
        was, _, now = cls[len("plan:kind-change-"):].partition("-to-")
        out.append(f"  the column CHANGES SHAPE: {was} -> {now}. The three "
                   "structured artifacts are updated; the SQL needs a hand "
                   "rewrite (the expression's whole form changes, not just a "
                   "column reference) — remove the old shape's CTE/expression "
                   "and write the new one, then refresh historical rows.")
        if now == "nested":
            out.append(f"  new shape: collect_list(named_struct(...)) sorted "
                       f"by the sortKey field, joinKey {col.get('joinKey')!r}")
        elif now == "conditional":
            out.append(f"  new shape: {len(col.get('branches') or [])} branch "
                       "CTEs + strict-pick COALESCE")
        else:
            out.append("  new shape: a single scalar expression")
    elif cls == "plan:nested":
        out.append(f"  joinKey: {col.get('joinKey')} — collect_list("
                   f"named_struct(...)) sorted by the sortKey field; "
                   f"see the collections section of product.sql.")
        for f in col.get("fields") or []:
            if f.get("transform") is not None:
                out.append(f"  field {f['name']} transform: "
                           + _plan_transform_note(f["transform"], registry))
    if cls.startswith("plan:") and col.get("kind") == "scalar" \
            and col.get("transform") is not None:
        out.append("  transform: "
                   + _plan_transform_note(col["transform"], registry))
    if cls in ("plan:conditional-modify", "plan:lookup-join-modify"):
        out.append("  modify: the shipped expression may be hand-tuned — "
                   "rewrite the existing CTE/pick lines in place rather than "
                   "appending, then run --refresh_columns for historical rows.")
    return "\n".join(out)


# ── driver ──────────────────────────────────────────────────────────────────

def compile_delta(repo: Path, intake: dict, intent: str,
                  refresh: dict | None,
                  job_props: dict | None = None) -> tuple[dict[Path, str], list[str]]:
    domain = intake["domain"]
    entity = intake["table"].split(".")[-1]
    # both become PATH SEGMENTS this compiler WRITES to — identifier-lock
    # them exactly like the preview fence, so a hostile envelope can never
    # steer --apply outside the repo (UIFPE-260 review)
    ident = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
    if not ident.fullmatch(domain):
        raise ValueError(f"domain {domain!r} is not a plain lowercase "
                         f"identifier")
    if not ident.fullmatch(entity):
        raise ValueError(f"entity {entity!r} is not a plain lowercase "
                         f"identifier")
    base = repo / "domains" / domain / "src" / "schemas"
    paths = {
        "mapping": base / "mapping" / entity / "v1" / f"{domain}.mapping.json",
        "master": base / "gold" / entity / "v1" / "master.json",
        "manifest": base / "mapping" / entity / "v1" / "manifest.yaml",
        "sql": base / "mapping" / entity / "v1" / "sql" / f"{entity}.sql",
    }
    missing = sorted(k for k, p in paths.items() if not p.exists())
    if missing:
        # UIFPE-260: an unregistered entity goes to the scaffolder (review §5
        # D4, resolved by framework-owner decision 2026-09-04) — it generates
        # all four artifacts plus the pipeline config from the intake, or
        # refuses with the reason named.
        return scaffold_entity(repo, paths, intake, intent, job_props, missing)
    if intake.get("drivingFilter"):
        # A domain filter on a SHIPPED entity is a hand-reviewed change: the
        # SQL WHERE and the manifest sot_filter move together, and machine-
        # editing a shipped WHERE is the risk class this compiler refuses.
        raise ValueError(
            f"entity {entity!r} is already registered — drivingFilter is only "
            "consumed when scaffolding a new entity. Changing a shipped "
            "entity's row domain edits the SQL WHERE and the manifest "
            "sot_filter together; land it as an engineer change.")
    texts = {k: p.read_text(encoding="utf-8") for k, p in paths.items()}
    driving = intake.get("drivingTable") or ""
    log: list[str] = []

    # UIFPE-105: named rules resolve against the registered registry merged
    # with the task's own (the wizard embeds registered + task-defined rules
    # in the intake); new rules land in the mapping so references never dangle.
    registry = {**(json.loads(texts["mapping"]).get("transforms") or {}),
                **(intake.get("transforms") or {})}
    if intake.get("transforms"):
        texts["mapping"] = mapping_upsert_transforms(
            texts["mapping"], intake["transforms"])
    # UIFPE-107: the entity key comes from the intake, falling back to the
    # registered mapping's declaration — grain wording derives from it.
    entity_key = (intake.get("entityKey")
                  or json.loads(texts["mapping"]).get("entityKey"))
    # UIFPE-106: branch scopes resolve against the mapping's row groups,
    # merged with any the task itself declares (task wins on a name clash,
    # mirroring the transforms registry).
    row_groups = {**(json.loads(texts["mapping"]).get("rowGroups") or {}),
                  **(intake.get("rowGroups") or {})}

    # UIFPE-108: the wizard can convert a column's kind, so a modify may
    # arrive with a different SHAPE than the one that shipped. The structured
    # artifacts absorb that automatically, but the SQL does not — an array
    # becoming a scalar means a collect_list CTE becomes a max() pivot, which
    # is a structural rewrite, not a text edit. Classify those as PLAN.
    registered_kind = {c["goldColumnName"]: c.get("kind")
                       for c in json.loads(texts["mapping"]).get("columns") or []}
    # ADR-0008: a registered column's derivation block survives a modify —
    # mapping_upsert REPLACES the entry, so without this merge an intake that
    # doesn't carry the block would silently strip it (and the derivation
    # hygiene gate would fail the PR). An intake that DOES carry one wins.
    registered_derivation = {c["goldColumnName"]: c["derivation"]
                             for c in json.loads(texts["mapping"]).get("columns") or []
                             if c.get("derivation")}

    for col in intake["columns"]:
        name = col["goldColumnName"]
        if intent == "modify" and name in registered_derivation \
                and not col.get("derivation"):
            col = dict(col, derivation=registered_derivation[name])
        if col.get("derivation") is not None:
            # ADR-0008 battery — refusal-first and ATOMIC: any error aborts
            # the whole compile before a single artifact is written. reads
            # is recomputed here (computed, never authored), so the stored
            # lineage is always the parser's own. `is not None` so an empty
            # block is refused here rather than silently dropped.
            block = dict(col["derivation"])
            try:
                block = derivation_with_computed_reads(block)
            except (ValueError, KeyError):
                pass  # unparseable sql — validate_block reports it below
            errors = derivation_validate_block(
                name, block, derivation_manifest_aliases(texts["manifest"]))
            if errors:
                raise ValueError(
                    "derived column refused (ADR-0008 battery):\n  "
                    + "\n  ".join(errors))
            col = dict(col, derivation=block)
        cls = classify(col, driving)
        if intent == "modify" and name in registered_kind \
                and registered_kind[name] != col.get("kind"):
            cls = f"plan:kind-change-{registered_kind[name]}-to-{col.get('kind')}"
        texts["mapping"] = mapping_upsert(texts["mapping"], col, intent)
        texts["master"] = master_upsert(texts["master"], col, intent)
        texts["manifest"] = manifest_upsert(texts["manifest"], col, intent,
                                            entity_key)
        if cls.startswith("eav:"):
            group = cls.split(":")[1]
            if intent == "modify":
                texts["sql"], note = sql_modify_eav(texts["sql"], col, group, registry)
            else:
                texts["sql"], note = sql_add_eav(texts["sql"], col, group, registry)
            log.append(f"[AUTO] {name}: {note}")
        elif cls == "driving":
            if intent == "modify":
                log.append(render_plan(col, "plan:driving-modify", registry,
                                       entity_key))
            else:
                texts["sql"], note = sql_add_driving(texts["sql"], col, registry)
                log.append(f"[AUTO] {name}: {note}")
        elif cls == "conditional":
            # UIFPE-106. Modify mirrors the driving-modify posture: the
            # shipped pick may be hand-tuned, so a restructure is a plan.
            if intent == "modify":
                log.append(render_plan(col, "plan:conditional-modify",
                                       registry, entity_key))
            else:
                texts["sql"], note = sql_add_conditional(
                    texts["sql"], col, registry, row_groups, texts["manifest"])
                log.append(f"[AUTO] {name}: {note}")
        elif cls == "lookup-join":
            if intent == "modify":
                log.append(render_plan(col, "plan:lookup-join-modify",
                                       registry, entity_key))
            else:
                texts["sql"], note = sql_add_lookup(
                    texts["sql"], col, registry, texts["manifest"])
                log.append(f"[AUTO] {name}: {note}")
        elif cls == "derived-sql":
            # ADR-0008: ADD splices the author's query verbatim. MODIFY stays
            # a plan — the migrated columns' shipped SQL predates the d_<name>
            # wrap shape, and machine-editing hand-shaped CTEs is the risk
            # class this platform refuses (the UIFPE-106 modify posture).
            if intent == "modify":
                log.append(render_plan(col, "plan:derived-sql-modify",
                                       registry, entity_key))
            else:
                texts["sql"], note = sql_add_derived(texts["sql"], col)
                log.append(f"[AUTO] {name}: {note}")
        else:
            log.append(render_plan(col, cls, registry, entity_key))

    # UIFPE-107 / UIFPE-149: the schedule rides the same envelope. How OFTEN
    # (frequency) and from WHEN (start_time) are both config-as-code, and both
    # are authored in the wizard — there is no manual step for either.
    # weekly_days is checked for PRESENCE, not truth: an explicit empty list is
    # a request the compiler must refuse, not silently drop.
    wanted_schedule = [(k, job_props.get(k))
                       for k in ("frequency", "start_time", "weekly_days")
                       if job_props and (job_props.get(k) is not None
                                         if k == "weekly_days" else job_props.get(k))]
    if wanted_schedule:
        # The pipeline file is keyed by GOLD_ID — which equals the entity
        # (the gold table's own name) and the filename stem, both enforced by
        # sync_pipelines — never by domain: a domain can carry several
        # pipelines on different clocks (product + marketing_copy since
        # UIFPE-252), and the old {domain}.yaml path meant any entity's
        # envelope rescheduled the DOMAIN-named pipeline instead of its own.
        ppath = (repo / "domains" / domain / "src" / "apps" / "pipelines"
                 / "base" / f"{entity}.yaml")
        if not ppath.exists():
            log.append(
                f"[PLAN] schedule: pipelines/base/{entity}.yaml not found — set "
                + ", ".join(f"{k} {v!r}" for k, v in wanted_schedule)
                + " when the pipeline is registered")
        else:
            ptext = ppath.read_text(encoding="utf-8")
            new = ptext
            setter = {"frequency": pipeline_set_frequency,
                      "start_time": pipeline_set_start_time,
                      "weekly_days": pipeline_set_weekly_days}
            for key, value in wanted_schedule:
                before = new
                new = setter[key](new, value)
                if new != before:
                    log.append(f"[AUTO] schedule: {key} -> {value} "
                               f"(pipelines/base/{entity}.yaml — ships on the "
                               f"next sync_pipelines --apply)")
                else:
                    log.append(f"[INFO] schedule: {key} already {value} "
                               f"— no change")
            # UIFPE-532: leaving the weekly rung retires its day set - a
            # stale weekly_days beside a daily cadence fails validation.
            if (_pipeline_frequency(new) != WEEKLY_FREQUENCY
                    and _WEEKLY_DAYS_LINE.search(new)):
                new = pipeline_clear_weekly_days(new)
                log.append(f"[AUTO] schedule: weekly_days removed — the "
                           f"cadence is {_pipeline_frequency(new)}, which "
                           f"has no day set")
            if new != ptext:
                paths["pipeline"] = ppath
                texts["pipeline"] = new

    if intent == "modify" and (refresh or {}).get("historical", True):
        cols = ",".join(c["goldColumnName"] for c in intake["columns"])
        mode = (refresh or {}).get("mode", "emit")
        log.append(
            "[RUNBOOK] historical repair after deploy:\n"
            f"  aws glue start-job-run --job-name {gold_resources.gold_transform_job('dev')} "
            f"--arguments '{{\"--gold_id\":\"{entity}\","
            f"\"--refresh_columns\":\"{cols}\","
            f"\"--refresh_mode\":\"{mode}\"}}'")

    return {paths[k]: v for k, v in texts.items()}, log


# ── New-entity scaffolder (UIFPE-260) ───────────────────────────────────────
# Review §5 D4, resolved by framework-owner decision 2026-09-04: a new gold
# table is authored in the wizard and COMPILED, not hand-scaffolded. The
# scaffolder owns the whole artifact set, so it can do what the delta writers
# must not: emit a driving-row WHERE (drivingFilter) and collapse a finer
# source grain with aggregates GROUPed BY the entity key. v1 deliberately
# covers the single-source scalar entity (the stock shape); joins, nested
# columns and conditionals on a NEW table stay engineer work.

SCAFFOLD_DEFAULT_START = "08:15"  # threads the daily ladder; provisional


def _scaffold_refuse(reason: str, entity: str = "") -> ValueError:
    tag = f" ({entity})" if entity else ""
    return ValueError(f"new-entity scaffold refused{tag}: {reason}")


# dataType -> the Spark type the manifest column_types will declare. The SQL
# must CAST to exactly these, or the live table's inferred type diverges from
# the declaration and diff_nested_field_additions refuses every run after the
# CTAS (round finding: --rebuild recreates the same types, a permanent loop).
_SCAFFOLD_CAST = {"string": "STRING", "integer": "BIGINT",
                  "number": "DECIMAL(38,10)", "float": "DOUBLE",
                  "boolean": "BOOLEAN",
                  "date": "DATE", "timestamp": "TIMESTAMP"}


def _scaffold_expr(col: dict, registry: dict) -> str:
    """One SELECT expression: transform first, then ALWAYS cast to the
    declared type — the manifest declares it unconditionally, so an uncast
    transform output diverges the live table from the declaration (re-verify
    finding 5; a double CAST is harmless, a divergence is a permanent
    refuse loop)."""
    expr = render_transform(col.get("transform"), registry,
                            col["source"]["column"])
    cast = _SCAFFOLD_CAST.get(col["dataType"])
    return f"CAST({expr} AS {cast})" if cast else expr


def _scaffold_guards(paths: dict, intake: dict, intent: str,
                     ppath: Path) -> tuple[str, list, str]:
    """Refusal-first: returns (driving, entity_key, silver_table) or raises."""
    if intent != "add":
        raise _scaffold_refuse(
            f"intent {intent!r} targets an unregistered entity — only an "
            f"'add' (new-table) intake can scaffold")
    driving = intake.get("drivingTable") or ""
    entity_key = intake.get("entityKey") or []
    silver = intake.get("drivingSilverTable") or ""
    if not driving or not entity_key or not silver:
        absent = [n for n, v in [("drivingTable", driving),
                                 ("entityKey", entity_key),
                                 ("drivingSilverTable", silver)] if not v]
        raise _scaffold_refuse(
            f"{', '.join(absent)} missing — the wizard's new-table step "
            f"collects all three; the compiler will not guess an identity")
    if "{env}" not in silver or "." not in silver:
        raise _scaffold_refuse(
            f"drivingSilverTable {silver!r} must be database.table with the "
            f"environment templated (e.g. echo_{{env}}_silver_x.y)")
    if ppath.exists():
        raise _scaffold_refuse(
            f"{ppath.name} already exists — the entity has a registered "
            f"pipeline, so it is live, not new")
    if paths["master"].exists():
        try:
            meta = json.loads(paths["master"].read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise _scaffold_refuse(
                f"the existing {paths['master']} is not parseable JSON "
                f"({e}) — fix or remove it before scaffolding")
        if (meta.get("metadata") or {}).get("lifecycle") != "draft":
            raise _scaffold_refuse(
                f"an existing master.json with lifecycle "
                f"{(meta.get('metadata') or {}).get('lifecycle')!r} — only "
                f"draft artifacts are regenerated; a live contract is not")
    ident = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    # both are spliced verbatim into YAML/SQL templates — same fence standard
    # as every other free-ish field (round finding, second pass)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}", driving):
        raise _scaffold_refuse(
            f"drivingTable {driving!r} is not a plain table identifier")
    if not re.fullmatch(r"[a-z0-9_]*\{env\}[a-z0-9_]*\.[a-z0-9_]{1,128}",
                        silver):
        raise _scaffold_refuse(
            f"drivingSilverTable {silver!r} must be database.table, lowercase "
            f"identifiers with the environment templated as {{env}}")
    # words Spark/Athena cannot take as bare output identifiers
    reserved = {"select", "from", "where", "group", "order", "by", "having",
                "join", "on", "as", "case", "when", "then", "else", "end",
                "and", "or", "not", "null", "true", "false", "table",
                "create", "insert", "update", "delete", "distinct", "union",
                "cast", "in", "is", "like", "between", "exists", "all", "any"}
    seen: dict = {}
    for c in intake["columns"]:
        name = c.get("goldColumnName") or ""
        src_col = (c.get("source") or {}).get("column") or ""
        if not ident.fullmatch(name) or name.startswith("_") \
                or name.lower() in reserved:
            raise _scaffold_refuse(
                f"column name {name!r} is not a plain identifier — framework "
                f"metadata names (leading underscore), SQL keywords and "
                f"punctuation are not scaffoldable")
        if c.get("kind") == "scalar" and not ident.fullmatch(src_col):
            # nested fields fence their own sources in _scaffold_nested_guards
            raise _scaffold_refuse(
                f"column {name!r}: source column {src_col!r} is not a plain "
                f"identifier")
        # Athena lowercases unquoted identifiers, so isbn13/ISBN13 collide
        if name.lower() in seen:
            raise _scaffold_refuse(
                f"column name {name!r} collides with "
                f"{seen[name.lower()]!r} (identifiers are case-insensitive "
                f"physically)")
        seen[name.lower()] = name
    key_golds = {p["goldColumn"] for p in entity_key}
    if len(key_golds) != len(entity_key):
        raise _scaffold_refuse("entityKey repeats a goldColumn — a composite "
                               "key lists each column once")
    by_name = {c["goldColumnName"]: c for c in intake["columns"]}
    for pair in entity_key:
        col = by_name.get(pair["goldColumn"])
        if col is None:
            raise _scaffold_refuse(
                f"entityKey column {pair['goldColumn']!r} is not among the "
                f"intake's columns")
        if col.get("nullable", True):
            raise _scaffold_refuse(
                f"entityKey column {pair['goldColumn']!r} is nullable — a "
                f"primary-key column cannot be")
        if (col.get("source") or {}).get("column") != pair["drivingColumn"]:
            # the GROUP BY, key_mapping and sot_pk_mapping all use the pair's
            # drivingColumn — a mismatch corrupts CDC keys silently
            raise _scaffold_refuse(
                f"entityKey column {pair['goldColumn']!r} maps to "
                f"{pair['drivingColumn']!r} but the column reads "
                f"{(col.get('source') or {}).get('column')!r} — the key "
                f"mapping and the column's source must agree")
    same_table = (lambda a, b:
                  (a or "").split(".")[-1].lower()
                  == (b or "").split(".")[-1].lower())
    for c in intake["columns"]:
        kind = c.get("kind")
        if c.get("derivation") or c.get("branches") \
                or kind not in ("scalar", "nested"):
            raise _scaffold_refuse(
                f"column {c['goldColumnName']!r} is not a scalar or nested "
                f"array — conditional and derived columns on a new table "
                f"are engineer work")
        if kind == "nested":
            _scaffold_nested_guards(c, driving, intake, ident)
            continue
        src_table = (c.get("source") or {}).get("table") or ""
        # the wizard stores the picked table QUALIFIED (db.table, lowercase);
        # the intake convention is the bare Oracle-style name — compare bare
        if not same_table(src_table, driving):
            raise _scaffold_refuse(
                f"column {c['goldColumnName']!r} reads {src_table!r} — a "
                f"scaffolded SCALAR must read the driving table "
                f"({driving!r}); lookup values belong as nested-array "
                f"fields (UIFPE-262) or engineer work")
        if (c.get("source") or {}).get("filter"):
            raise _scaffold_refuse(
                f"column {c['goldColumnName']!r} declares a per-column "
                f"filter — restrict rows with drivingFilter instead (one "
                f"domain, one predicate)")
    return driving, entity_key, silver


def _scaffold_nested_guards(col: dict, driving: str, intake: dict,
                            ident) -> None:
    """UIFPE-262: one nested struct-array column — fields read the driving
    table or a header-level lookup; sort keys must exist and totally order
    the entries; the pick (if any) must be covered by the sort keys."""
    name = col["goldColumnName"]
    fields = col.get("fields") or []
    if len(fields) < 2:
        raise _scaffold_refuse(
            f"nested column {name!r} has {len(fields)} field(s) — a value "
            f"list (single field) is not scaffolded yet; declare a struct "
            f"array (2+ fields)")
    if col.get("filter"):
        # same rule as scalar per-column filters: one domain, one predicate —
        # a column-level WHERE fragment would otherwise be silently ignored
        raise _scaffold_refuse(
            f"nested column {name!r} declares a column-level filter — "
            f"restrict rows with drivingFilter instead")
    field_names = []
    for f in fields:
        fname = f.get("name") or ""
        src = f.get("source") or {}
        if not ident.fullmatch(fname) or fname.startswith("_"):
            raise _scaffold_refuse(
                f"nested field {fname!r} on {name!r} is not a plain "
                f"identifier")
        if not ident.fullmatch(src.get("column") or ""):
            raise _scaffold_refuse(
                f"nested field {fname!r} on {name!r}: source column "
                f"{src.get('column')!r} is not a plain identifier")
        if fname.lower() in (x.lower() for x in field_names):
            raise _scaffold_refuse(
                f"nested field {fname!r} on {name!r} repeats a field name")
        field_names.append(fname)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}",
                            src.get("table") or ""):
            # spliced into CTE names, FROM clauses and both YAML alias
            # lists — same fence standard as drivingTable
            raise _scaffold_refuse(
                f"nested field {fname!r} on {name!r}: source table "
                f"{src.get('table')!r} is not a plain table identifier")
        same = (src.get("table") or "").split(".")[-1].lower() \
            == driving.split(".")[-1].lower()
        if same and src.get("filter"):
            # a lookup filter becomes its CTE's WHERE; a driving-side field
            # filter has no home in the generated SQL — recording it in the
            # mapping while silently not applying it is the drift this
            # compiler refuses everywhere else
            raise _scaffold_refuse(
                f"nested field {fname!r} on {name!r} declares a filter on "
                f"the driving table — restrict rows with drivingFilter "
                f"instead")
        if not same:
            join = src.get("join") or {}
            on = join.get("on") or []
            if (join.get("type") or "left") != "left" or not on:
                raise _scaffold_refuse(
                    f"nested field {fname!r} on {name!r} reads "
                    f"{src.get('table')!r} without a LEFT join.on — a "
                    f"lookup field must say how it attaches to the driving "
                    f"table")
            for o in on:
                lt = (o.get("leftTable") or "").split(".")[-1].lower()
                if lt and lt != driving.split(".")[-1].lower():
                    raise _scaffold_refuse(
                        f"nested field {fname!r}: join.on leftTable "
                        f"{o.get('leftTable')!r} is not the driving table — "
                        f"join chains on a new table are engineer work")
                for side in ("leftColumn", "rightColumn"):
                    val = o.get(side) or o.get("leftColumn") or ""
                    if side in o and not ident.fullmatch(o[side] or ""):
                        raise _scaffold_refuse(
                            f"nested field {fname!r}: join.on {side} "
                            f"{o.get(side)!r} is not a plain identifier")
                if not o.get("leftColumn"):
                    raise _scaffold_refuse(
                        f"nested field {fname!r}: a join.on pair declares "
                        f"no leftColumn")
            bare = (src.get("table") or "").split(".")[-1].upper()
            silver_map = intake.get("silverTables") or {}
            if bare not in silver_map:
                raise _scaffold_refuse(
                    f"lookup table {bare} (field {fname!r}) has no "
                    f"silverTables entry — the scaffolder needs its "
                    f"qualified, env-templated Silver name")
            if not re.fullmatch(
                    r"[a-z0-9_]*\{env\}[a-z0-9_]*\.[a-z0-9_]{1,128}",
                    silver_map[bare]):
                # same fence standard as drivingSilverTable — this value is
                # spliced verbatim into manifest AND pipeline YAML
                raise _scaffold_refuse(
                    f"silverTables[{bare}] = {silver_map[bare]!r} must be "
                    f"database.table, lowercase identifiers with the "
                    f"environment templated as {{env}}")
    sort_keys = [f["name"] for f in fields if f.get("sortKey")]
    if not sort_keys:
        raise _scaffold_refuse(
            f"nested column {name!r} declares no sortKey fields — array "
            f"order feeds the canonical hash, so an unsorted array churns "
            f"every channel every run (A8)")
    pick = col.get("pick")
    if pick:
        part = pick.get("partitionBy") or []
        order = pick.get("orderBy") or []
        if not part or not order:
            raise _scaffold_refuse(
                f"pick on {name!r} needs both partitionBy and orderBy — an "
                f"empty partition keeps one arbitrary row per entity, an "
                f"empty order keeps an arbitrary one per partition")
        missing = [p for p in part if p not in field_names]
        if missing:
            raise _scaffold_refuse(
                f"pick.partitionBy names {missing} that are not fields of "
                f"{name!r}")
        uncovered = [p for p in part if p not in sort_keys]
        if uncovered:
            raise _scaffold_refuse(
                f"pick.partitionBy field(s) {uncovered} on {name!r} are not "
                f"sort keys — after the pick, entries are unique per "
                f"partition, so the sort keys must cover it or the array "
                f"order is not total (A8)")
        for o in order:
            expr = (o.get("expr") or "").strip()
            if not expr:
                raise _scaffold_refuse(
                    f"pick.orderBy on {name!r} carries an empty expression")
            stripped = re.sub(r"'[^']*'", "''", expr)
            if is_aggregate(stripped) \
                    or re.search(r"\bover\s*\(", stripped, re.IGNORECASE):
                raise _scaffold_refuse(
                    f"pick.orderBy expression {expr!r} on {name!r} uses an "
                    f"aggregate or window — order by row-level expressions; "
                    f"the pick's window is the only one")
            if (o.get("direction") or "asc").lower() not in ("asc", "desc"):
                # anything else silently compiling as ASC flips which row
                # the pick keeps (re-verify finding 6)
                raise _scaffold_refuse(
                    f"pick.orderBy direction {o.get('direction')!r} on "
                    f"{name!r} — asc or desc only")


def _scaffold_lookups(nested: dict | None, intake: dict,
                      driving: str) -> dict:
    """UIFPE-262: lookup specs from a nested column's non-driving fields.
    One CTE per distinct (table, filter, join signature):
    {cte_name: {bare, silver, filter, on, cols: {field: src_col}}}."""
    lookups: dict = {}
    if not nested:
        return lookups
    driving_bare = driving.split(".")[-1].lower()
    entity_alias = intake["table"].split(".")[-1].lower()
    silver_map = intake.get("silverTables") or {}
    for f in nested.get("fields") or []:
        src = f.get("source") or {}
        bare = (src.get("table") or "").split(".")[-1].upper()
        if bare.lower() == driving_bare:
            continue
        if bare.lower() == entity_alias:
            # the driving view is registered under the entity alias — a
            # lookup CTE's FROM would silently read IT, and the manifest
            # would carry the alias twice (re-verify finding 7)
            raise _scaffold_refuse(
                f"lookup table {bare} (field {f.get('name')!r}) collides "
                f"with the entity alias {entity_alias!r} — rename the "
                f"entity or land this lookup as engineer work")
        join = src.get("join") or {}
        sig = (bare, src.get("filter") or "",
               json.dumps(join.get("on") or [], sort_keys=True))
        cte = next((k for k, v in lookups.items() if v["sig"] == sig), None)
        if cte is None:
            cte = base_cte = f"lk_{bare.lower()}"
            n = 2
            while cte in lookups:      # loop until free — suffixes can clash
                cte = f"{base_cte}_{n}"
                n += 1
            lookups[cte] = {"sig": sig, "bare": bare,
                            "silver": silver_map[bare],
                            "filter": src.get("filter") or "",
                            "on": join.get("on") or [], "cols": {}}
        lookups[cte]["cols"][f["name"]] = src["column"]
    return lookups


def _scaffold_field_expr(f: dict, driving: str,
                         registry: dict | None = None) -> str:
    """A nested field's expression in the picked/base scope: driving fields
    read their source column; lookup fields read the base's lk_ projection
    (prefixed — Spark identifiers are case-insensitive, so a field named
    priceListName would otherwise collide with a PRICE_LIST_NAME column).
    Every declared type is CAST so the live struct member matches the
    manifest's column_types exactly (see _SCAFFOLD_CAST); a field transform
    applies before the cast, mirroring _scaffold_expr's contract."""
    src = f.get("source") or {}
    bare = (src.get("table") or "").split(".")[-1].lower()
    e = src["column"] if bare == driving.split(".")[-1].lower() \
        else f"lk_{f['name']}"
    t = f.get("transform")
    if t is not None:
        e = render_transform(t, registry or {}, e)
        if is_aggregate(e):
            raise _scaffold_refuse(
                f"nested field {f['name']!r}: its transform renders an "
                f"aggregate — array elements are per source row; collapse "
                f"rows with the pick, not a field transform")
    cast = _SCAFFOLD_CAST.get(f["type"])
    if cast:
        e = f"CAST({e} AS {cast})"
    return e


def _scaffold_sql(intake: dict, registry: dict, entity_key: list,
                  entity: str, domain: str,
                  driving: str) -> tuple[str, bool, dict]:
    """The full mapping SQL. Returns (text, grouped, lookups). Grouped mode:
    ANY aggregate expression — or a nested array column — forces every
    non-key scalar to aggregate and adds GROUP BY over the key's source
    columns; a nested column assembles via the platform's sorted-wrapper
    collect_list pattern, optionally after a declarative ranked pick."""
    cols = intake["columns"]
    nested_cols = [c for c in cols if c.get("kind") == "nested"]
    if len(nested_cols) > 1:
        raise _scaffold_refuse(
            f"{len(nested_cols)} nested columns — the scaffolder v2 covers "
            f"one struct array per entity; more is engineer work")
    nested = nested_cols[0] if nested_cols else None
    scalars = [c for c in cols if c.get("kind") == "scalar"]
    lookups = _scaffold_lookups(nested, intake, driving)
    key_golds = {p["goldColumn"] for p in entity_key}
    exprs = {c["goldColumnName"]: _scaffold_expr(c, registry)
             for c in scalars}
    windowed = sorted(n for n, e in exprs.items()
                      if re.search(r"\bover\s*\(",
                                   re.sub(r"'[^']*'", "''", e),
                                   re.IGNORECASE))
    if windowed:
        # a window mixed with a true aggregate defeats is_aggregate's OVER
        # carve-out and would compile invalid ungrouped SQL — v1 keeps
        # windows off the scaffold entirely rather than half-classifying them
        raise _scaffold_refuse(
            f"column(s) {', '.join(windowed)} use window expressions (OVER) "
            f"— not supported when scaffolding a new entity; land them as "
            f"an engineer change")
    agg = {n: is_aggregate(e) for n, e in exprs.items()}
    grouped = any(agg.values()) or nested is not None
    if grouped:
        bad_key = sorted(n for n in key_golds if agg.get(n))
        if bad_key:
            raise _scaffold_refuse(
                f"entityKey column(s) {', '.join(bad_key)} carry aggregate "
                f"transforms — the GROUP BY grain cannot be aggregated")
        bare = sorted(n for n, a in agg.items()
                      if not a and n not in key_golds)
        if bare:
            raise _scaffold_refuse(
                f"column(s) {', '.join(bare)} are not aggregated while "
                f"others are — at a collapsed grain every non-key column "
                f"needs an aggregate (SUM/MAX/…) or a deterministic pick")
    filt = (intake.get("drivingFilter") or "").strip()
    if filt and lookups:
        # the SAME string becomes base's WHERE (a scope containing the
        # lookup joins) AND the manifest sot_filter, which reconcile applies
        # against the DRIVING silver alone — a lookup reference diverges the
        # two domains (round finding F2: silent wrong rows or mass-orphan
        # candidates). Refuse any lookup token in the filter.
        forbidden = set()
        for cte, spec in lookups.items():
            forbidden.add(cte)
            forbidden.add(spec["bare"])
            for fname in spec["cols"]:
                forbidden.update({fname, f"lk_{fname}"})
        filt_scan = re.sub(r"'[^']*'", "''", filt)  # literals are not refs
        hit = sorted(t for t in forbidden
                     if re.search(rf"(?i)(?<![\w]){re.escape(t)}(?![\w])",
                                  filt_scan))
        if hit:
            raise _scaffold_refuse(
                f"drivingFilter references lookup name(s) {', '.join(hit)} "
                f"— reconcile applies this filter against the driving table "
                f"alone, so it may only use driving columns")
    key_list = ", ".join(p["goldColumn"] for p in entity_key)
    width = max(len(e) for e in exprs.values())
    lines = [f"-- Gold {entity.replace('_', ' ').title()} transform — v1 "
             f"({len(cols)} attributes, compiled by the UIFPE-260 scaffolder)",
             f"-- Generated from the submitted intake "
             f"(mapping/{entity}/v1/{domain}.mapping.json) — edit the intake",
             f"-- and recompile; do not hand-edit this file.",
             f"-- One row per ({key_list})"
             + (" — the source is finer-grained and the"
                if grouped else " of the driving table."),
             ]
    if grouped:
        lines.append("-- GROUP BY collapses it with the intake's aggregates.")
    if filt:
        lines += [
            "--",
            "-- The WHERE below MUST stay in sync with the manifest's "
            "sot_filter: the",
            "-- transform only upserts, so a row leaving this domain is "
            "removed by",
            "-- RECONCILE, whose SoT anti-join applies sot_filter.",
        ]
    lines += [
        "--",
        "-- Watermark contract: the runtime registers the aliases below "
        f"({entity}",
        "-- and any lookups) as temp views — the driving view is restricted "
        "to affected",
        "-- entities on incremental runs. Output column names MUST exactly "
        "match the",
        "-- master schema property names.",
        "",
    ]
    ctes: list[str] = []
    for cte, spec in lookups.items():
        # max() per key so the join can never fan the driving rows out —
        # the sql_add_lookup dimension-CTE shape
        keys, terms = [], []
        for i, o in enumerate(spec["on"]):
            lc = (o.get("leftColumn") or "").upper()
            rc = (o.get("rightColumn") or lc).upper()
            k = f"k{i + 1}"
            keys.append((f"CAST({rc} AS BIGINT)" if rc.endswith("_ID")
                         else rc) + f" AS {k}")
            left = f"CAST(d.{lc} AS BIGINT)" if lc.endswith("_ID") \
                else f"d.{lc}"
            terms.append(f"{left} = {cte}.{k}")
        spec["join_terms"] = terms
        where = f" WHERE {spec['filter']}" if spec["filter"] else ""
        picks = ",\n         ".join(
            f"max({src}) AS {fname}" for fname, src in spec["cols"].items())
        group_by = ", ".join(str(i + 1) for i in range(len(keys)))
        ctes.append(f"{cte} AS (\n  SELECT {', '.join(keys)},\n"
                    f"         {picks}\n"
                    f"  FROM {spec['bare'].lower()}{where} "
                    f"GROUP BY {group_by}\n)")
    pick = (nested or {}).get("pick")
    from_alias = entity
    if lookups or pick:
        base_proj = ["d.*"]
        for cte, spec in lookups.items():
            base_proj += [f"{cte}.{fname} AS lk_{fname}"
                          for fname in spec["cols"]]
        base = ["base AS (",
                f"  SELECT {', '.join(base_proj)}",
                f"  FROM {entity} d"]
        for cte, spec in lookups.items():
            base.append(f"  LEFT JOIN {cte} ON "
                        + "\n              AND ".join(spec["join_terms"]))
        if filt:
            base.append(f"  WHERE {filt}")
        base.append(")")
        ctes.append("\n".join(base))
        from_alias = "base"
    if pick:
        by_name = {f["name"]: f for f in nested["fields"]}
        part = [f"{p['drivingColumn']}" for p in entity_key] + \
               [_scaffold_field_expr(by_name[p], driving, registry)
                for p in pick["partitionBy"]]

        def resolve_order(expr: str) -> str:
            # authors write FIELD names; lookup fields only exist as lk_
            # projections in this scope, so bare field tokens resolve to
            # their expressions — in ONE pass, so an inserted expression is
            # never re-scanned, and never inside string literals (re-verify
            # findings 1+2: sequential substitution rewrote a resolved
            # expression's columns, and literals' contents)
            names = sorted(by_name, key=len, reverse=True)
            pat = re.compile("|".join(
                rf"(?<![\w]){re.escape(n)}(?![\w])" for n in names))
            return "".join(
                part if part.startswith("'")
                else pat.sub(lambda m: _scaffold_field_expr(
                    by_name[m.group(0)], driving, registry), part)
                for part in re.split(r"('[^']*')", expr))

        order = ", ".join(
            resolve_order(o["expr"])
            + (" DESC" if (o.get("direction") or "").lower() == "desc"
               else "")
            for o in pick["orderBy"])
        ctes.append("picked AS (\n"
                    "  -- declarative ranked pick: ONE row per partition —\n"
                    "  -- deterministic by the ordered expressions below.\n"
                    "  -- NOTE: the whole final SELECT reads picked rows, so\n"
                    "  -- scalar aggregates also see only pick winners\n"
                    "  SELECT *, row_number() OVER (\n"
                    f"    PARTITION BY {', '.join(part)}\n"
                    f"    ORDER BY {order}\n"
                    "  ) AS __pick_rn\n"
                    f"  FROM {from_alias}\n)")
        from_alias = "picked"
    if ctes:
        lines.append("WITH\n" + ",\n".join(ctes))
        lines.append("")
    lines.append("SELECT")
    # INTAKE column order — manifest output_columns and master properties
    # keep it, and the three artifacts must agree (their own stated contract)
    select = []
    for c in cols:
        if c.get("kind") == "scalar":
            select.append(f"  {exprs[c['goldColumnName']]:<{width}} AS "
                          f"{c['goldColumnName']}")
            continue
        by_name = {f["name"]: f for f in nested["fields"]}
        sort_fields = [f["name"] for f in nested["fields"]
                       if f.get("sortKey")]
        skeys = ",\n             ".join(
            f"'s{i + 1}', "
            f"{_scaffold_field_expr(by_name[s], driving, registry)}"
            for i, s in enumerate(sort_fields))
        members = ",\n               ".join(
            f"'{f['name']}', {_scaffold_field_expr(f, driving, registry)}"
            for f in nested["fields"])
        select.append(
            "  -- sorted-wrapper pattern: the s-keys order the elements,\n"
            "  -- the payload struct is the OUTPUT shape (A8 determinism)\n"
            "  transform(\n"
            "    array_sort(collect_list(named_struct(\n"
            f"             {skeys},\n"
            "             'p', named_struct(\n"
            f"               {members})))),\n"
            f"    x -> x.p) AS {nested['goldColumnName']}")
    lines.append(",\n".join(select))
    lines.append(f"FROM {from_alias}")
    where_final = []
    if pick:
        where_final.append("__pick_rn = 1")
    if filt and from_alias == entity:
        where_final.append(filt)
    if where_final:
        lines.append("WHERE " + " AND ".join(where_final))
    if grouped:
        group_cols = ", ".join(p["drivingColumn"] for p in entity_key)
        lines.append(f"GROUP BY {group_cols}")
    text = "\n".join(lines) + "\n"
    try:
        import sqlglot
        import sqlglot.errors
    except ImportError:
        return text, grouped, lookups  # parse gate is best-effort locally
    try:
        sqlglot.parse(text, dialect="spark")
    except sqlglot.errors.ParseError as e:
        # ONLY a real parse failure refuses — an incompatible sqlglot API
        # (TypeError etc.) must not masquerade as "your SQL is broken"
        raise _scaffold_refuse(
            f"the generated SQL does not parse as Spark ({e}) — check "
            f"custom expressions (pick.orderBy, transforms, drivingFilter)")
    except Exception:
        pass
    return text, grouped, lookups


def _scaffold_master(intake: dict, entity_key: list, entity: str,
                     domain: str) -> str:
    cols = intake["columns"]
    # ADR-0023: artifact = {domain}.{entity}.{type}, type 'gold' for a master.
    # The gold table name is snake_case; a subject renders it kebab. The former
    # f"{domain}-gold-master" form is superseded, and it was wrong for a second
    # entity in one domain anyway — it keyed on domain, so product's master and
    # marketing_copy's would have collided on one coordinate.
    # Compatibility and tier come from the normative Pearson rules (D21).
    registry = intake.get("registry") or {
        "group": domain,
        "artifact": f"{domain}.{entity.replace('_', '-')}.gold",
        "compatibility": "BACKWARD_TRANSITIVE",
        "tier": "T2"}
    contact = (intake.get("batch") or {}).get("requestedBy", "")
    # first-letter capitalization per word, NOT str.title() — .title()
    # uppercases after digits ("stock2go" -> "Stock2Go") and the UI's
    # generator does not; the two must stay byte-identical
    titled = " ".join(w[:1].upper() + w[1:] for w in entity.split("_"))
    master = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": f"https://schemas.pearson.com/gold/{entity}/v1/master.json",
        "registry": registry,
        "title": titled,
        "description": (
            f"Gold entity: {titled}. Compiled by "
            f"the UIFPE-260 scaffolder from the authored intake "
            f"(mapping/{entity}/v1/{domain}.mapping.json) — that file is the "
            f"source of truth for the rules. x-channels declares which "
            f"downstream tier(s) consume each attribute."),
        "metadata": {
            # owner mirrors ui newTableArtifacts.ts exactly, so the wizard's
            # preview and the compiled master agree byte-for-byte
            "owner": {
                "service": f"urn:pearson:service:{domain}:{entity}-svc",
                "team": "Enterprise Data Connect",
                **({"contact": contact} if contact.strip() else {}),
            },
            "domain": domain,
            "entity": entity,
            "version": "v1",
            "primaryKey": [{"goldColumn": p["goldColumn"],
                            "drivingColumn": p["drivingColumn"]}
                           for p in entity_key],
            "dataClassification": "internal",
            "audience": "engineering",
            "lifecycle": "draft",
        },
        "type": "object",
        "additionalProperties": False,
        # N12 (UIFPE-518): `required` constrains the KEY, not the value, so
        # EVERY element is required and nullability is carried solely by the
        # type union master_prop() already emits (`[t, "null"]`). Deriving
        # `required` from `nullable` conflated the two: a nullable column was
        # left optional, the producer could omit the key, and a consumer had no
        # way to tell a value that was CLEARED from one that was simply not
        # sent — so it keeps the stale value forever. Same failure as a gold
        # merge that COALESCEs source over target and never clears anything.
        "required": [c["goldColumnName"] for c in cols],
        "properties": {c["goldColumnName"]: master_prop(c) for c in cols},
    }
    # ensure_ascii=False + trailing newline: byte-parity with the UI's
    # JSON.stringify output (master_upsert already writes non-ASCII raw)
    return json.dumps(master, indent=2, ensure_ascii=False) + "\n"


_SPARK_MEMBER_TYPE = {"string": "STRING", "integer": "BIGINT",
                      "number": "DECIMAL(38,10)", "float": "DOUBLE",
                      "boolean": "BOOLEAN", "date": "DATE",
                      "timestamp": "TIMESTAMP"}


def _scaffold_manifest(intake: dict, entity_key: list, entity: str,
                       domain: str, silver: str, lookups: dict) -> str:
    cols = intake["columns"]
    driving = intake["drivingTable"]
    filt = (intake.get("drivingFilter") or "").strip()
    key_golds = {p["goldColumn"] for p in entity_key}
    lineage = []
    for c in cols:
        if c.get("kind") == "nested":
            # ONE parent entry — validate_gold_schemas set-compares lineage
            # keys against master property names; per-field detail rides as
            # a comment block under it
            lineage.append(f"  {c['goldColumnName']}:")
            lineage.append(f"    pdh_table:  {driving}")
            first = (c["fields"][0].get("source") or {})
            lineage.append(f"    pdh_column: {first.get('column', '')}")
            for f in c["fields"]:
                src = f.get("source") or {}
                lineage.append(
                    f"    # {f['name']} <- "
                    f"{(src.get('table') or driving).split('.')[-1].upper()}"
                    f".{src.get('column', '')}")
            lineage.append("")
            continue
        lineage.append(f"  {c['goldColumnName']}:")
        lineage.append(f"    pdh_table:  {driving}")
        lineage.append(f"    pdh_column: {c['source']['column']}")
        if c["goldColumnName"] in key_golds:
            lineage.append("    role:       pk")
        lineage.append("")
    pk_map = "\n".join(f"      {p['goldColumn']}: \"{p['drivingColumn']}\""
                       for p in entity_key)
    # json.dumps: a JSON string is a valid YAML double-quoted scalar, so a
    # filter carrying quotes cannot break the document
    sot_filter = f"\n    sot_filter: {json.dumps(filt)}" if filt else ""
    # lookup sources: registered FULL for the mapping SQL, no Gold PK
    # projection — lookup_only, exactly the product taxonomy-dims posture.
    # Deduped by table: two CTE signatures over one table are still ONE
    # runtime view (sync refuses duplicate aliases post-merge otherwise)
    lookup_sources, seen_tables = "", set()
    for spec in lookups.values():
        if spec["bare"] in seen_tables:
            continue
        seen_tables.add(spec["bare"])
        lookup_sources += (
            f"\n  - alias: {spec['bare'].lower()}\n"
            f"    silver_table: {spec['silver']}\n"
            f"    watermark_col: _silver_updated_at\n"
            f"    lookup_only: true")
    date_types = {"date": "DATE", "timestamp": "TIMESTAMP"}
    typed = [(c["goldColumnName"], date_types[c["dataType"]])
             for c in cols if c.get("kind") == "scalar"
             and c["dataType"] in date_types]
    sort_keys_block = ""
    for c in cols:
        if c.get("kind") != "nested":
            continue
        members = ", ".join(
            f"{f['name']}: {_SPARK_MEMBER_TYPE[f['type']]}"
            for f in c["fields"])
        typed.append((c["goldColumnName"], f">-\n    ARRAY<STRUCT<{members}>>"))
        skeys = [f["name"] for f in c["fields"] if f.get("sortKey")]
        sort_keys_block += (f"\n# D11: the mapping SQL pre-sorts every array "
                            f"by these keys (A8 determinism).\n"
                            f"array_sort_keys:\n"
                            f"  {c['goldColumnName']}: "
                            f"[{', '.join(skeys)}]\n")
    column_types = ""
    if typed:
        column_types = ("\n# ADR-0003 D12: Spark types for date/time and "
                        "complex columns — used by the auto-DDL.\n"
                        "column_types:\n"
                        + "\n".join(f"  {n}: {t}" for n, t in typed) + "\n")
    column_types += sort_keys_block
    out_cols = "\n".join(f"  - {c['goldColumnName']}" for c in cols)
    contact = json.dumps((intake.get("batch") or {}).get("requestedBy", ""))
    db_part = silver.split(".", 1)[0]
    return f"""domain: {domain}
version: v1

# Compiled by the UIFPE-260 scaffolder from the authored intake at
# ./{domain}.mapping.json — that file is the source of truth for the RULES;
# this one is what the runtime consumes. Edit the intake and recompile.

source_system:
  name: "Silver ({db_part})"
  modules: ["{driving}"]
  contact: {contact}

canonical_lineage:
{chr(10).join(lineage)}
schema:
  master: ../../../gold/{entity}/v1/master.json

sql:
  file: sql/{entity}.sql

# The driving alias is the source of truth; lookup aliases (if any) are
# registered FULL for the mapping SQL and skipped by Phase 1 (lookup_only).
# Every alias MUST also appear in the pipeline config
# (src/apps/pipelines/base/{entity}.yaml).
sources:
  - alias: {entity}
    silver_table: {silver}
    watermark_col: _silver_updated_at
    is_source_of_truth: true
    sot_pk_mapping:
{pk_map}{sot_filter}
    key_mapping:
{pk_map}{lookup_sources}

target:
  database: uif_echo_{{env}}_gold_{domain}
  table: {entity}
  primary_key_cols:
{chr(10).join(f'    - {p["goldColumn"]}' for p in entity_key)}
{column_types}
# Physical column order. MUST match master.json property order and the mapping
# SQL SELECT order; framework metadata columns are appended by the runtime.
output_columns:
{out_cols}
"""


def _scaffold_pipeline(intake: dict, entity: str, domain: str, silver: str,
                       entity_key: list, job_props: dict | None,
                       lookups: dict | None = None) -> str:
    freq = (job_props or {}).get("frequency") or "gold-24h"
    start = (job_props or {}).get("start_time") or SCAFFOLD_DEFAULT_START
    if freq not in VALID_FREQUENCIES:
        raise _scaffold_refuse(f"frequency {freq!r} is not a dispatcher "
                               f"cadence ({sorted(VALID_FREQUENCIES)})")
    if parse_time_of_day(start) is None:
        raise _scaffold_refuse(f"start_time {start!r} is not HH:MM — the "
                               f"dispatcher would silently fall back to the "
                               f"cadence default")
    # UIFPE-532: the weekly rung states its UTC day set; any other cadence
    # must not carry one (gold_config.validate would refuse the sync).
    raw_days = (job_props or {}).get("weekly_days")
    weekly_line = ""
    if freq == WEEKLY_FREQUENCY:
        days = (tuple(DEFAULT_WEEKLY_DAYS) if raw_days is None
                else parse_weekly_days(raw_days))
        if days is None:
            raise _scaffold_refuse(f"weekly_days {raw_days!r} is not a "
                                   f"non-empty list of UTC weekdays {WEEKDAYS}")
        weekly_line = f"\n  weekly_days: [{', '.join(days)}]"
    elif raw_days is not None:
        raise _scaffold_refuse(f"weekly_days given for {freq!r} — only "
                               f"{WEEKLY_FREQUENCY} has a day set")
    lookup_sources, seen_tables = "", set()
    for spec in (lookups or {}).values():
        if spec["bare"] in seen_tables:      # dedupe: one view per table
            continue
        seen_tables.add(spec["bare"])
        lookup_sources += (
            f"\n  - alias: {spec['bare'].lower()}\n"
            f"    silver_table: {spec['silver']}\n"
            f"    watermark_col: _silver_updated_at")
    return f"""# Gold pipeline: {entity} — environment-agnostic base definition
# (UIFPE-260 scaffolder). Per-env values live in
# pipelines/overlays/{{env}}/{entity}.yaml. Synced by sync_pipelines.py.
# Runtime-owned fields (watermarks, channel registry) are NEVER defined here.

gold_id: {entity}
domain: {domain}

schema:
  version: v1

# Aliases MUST match src/schemas/mapping/{entity}/v1/manifest.yaml exactly
# (lookup_only lives in the manifest — it describes the table, not the env).
sources:
  - alias: {entity}
    silver_table: {silver}
    watermark_col: _silver_updated_at
    is_source_of_truth: true{lookup_sources}

target:
  database: uif_echo_{{env}}_gold_{domain}
  table: {entity}
  primary_key_cols:
{chr(10).join(f'    - {p["goldColumn"]}' for p in entity_key)}

schedule:
  frequency: {freq}
  # Quote it: YAML 1.1 parses an unquoted time as an integer.
  start_time: "{start}"{weekly_line}
  failure_cooldown_minutes: 30

# OFF for the first load (product/program/marketing_copy precedent): rows
# leaving the domain are only DELETED by reconcile — enable once the first
# load's counts are trusted.
reconcile:
  enabled: false
  dry_run: true
  max_deletes_per_run: 5000

channels:
  bootstrap_mode: silent
backfill_mode: silent

snapshot_gate: true

glue:
  worker_type: G.1X
  num_workers: 4
  timeout_minutes: 120
  max_affected_fraction: 0.5
  full_load:
    worker_type: G.2X
    num_workers: 8
    timeout_minutes: 180

metadata:
  owner: {json.dumps((intake.get('batch') or {}).get('requestedBy', ''))}
  domain: {domain}
"""


def _scaffold_overlay(entity: str) -> str:
    artifacts = gold_resources.gold_artifacts_bucket("dev")
    data = gold_resources.gold_data_bucket("dev")
    return f"""# Gold pipeline: {entity} — dev overlay (deep-merged over
# pipelines/base/{entity}.yaml). Only per-environment values belong here.

enabled: true

schema:
  master_s3_path: s3://{artifacts}/schemas/gold/{entity}/v1/master.json

mapping:
  manifest_s3_path: s3://{artifacts}/mappings/{entity}/v1/manifest.yaml
  sql_s3_path: s3://{artifacts}/mappings/{entity}/v1/sql/{entity}.sql

target:
  s3_path: s3://{data}/{entity}/
"""


def scaffold_entity(repo: Path, paths: dict, intake: dict, intent: str,
                    job_props: dict | None,
                    missing: list) -> tuple[dict[Path, str], list[str]]:
    domain, entity = intake["domain"], intake["table"].split(".")[-1]
    apps = repo / "domains" / domain / "src" / "apps" / "pipelines"
    ppath = apps / "base" / f"{entity}.yaml"
    try:
        driving, entity_key, silver = _scaffold_guards(paths, intake, intent,
                                                       ppath)
        registry = intake.get("transforms") or {}
        sql_text, grouped, lookups = _scaffold_sql(
            intake, registry, entity_key, entity, domain, driving)
        pipeline_text = _scaffold_pipeline(intake, entity, domain, silver,
                                           entity_key, job_props, lookups)
    except ValueError as e:
        # every refusal names the entity — the wizard shows this verbatim
        raise ValueError(f"{e} — entity {domain}.{entity}") from None
    mapping = {k: intake[k] for k in
               ("domain", "table", "registry", "drivingTable",
                "drivingSilverTable", "silverTables", "drivingFilter",
                "entityKey",
                "transforms", "batch", "columns") if intake.get(k)}
    mapping = {"$schema":
               "../../../../../../../stacks/apicurio/standards/"
               "gold-attribute-mapping.schema.json", **mapping}
    # normalize join pairs: the shared schema requires leftTable on every
    # on-pair; the wizard omits it (the guard above pins it to the driving
    # table anyway), so the committed mapping must not fail its own $schema
    mapping = json.loads(json.dumps(mapping))     # deep copy, intake untouched
    for c in mapping.get("columns") or []:
        for f in c.get("fields") or []:
            for o in ((f.get("source") or {}).get("join") or {}) \
                    .get("on") or []:
                o.setdefault("leftTable", driving)
        for o in (c.get("pick") or {}).get("orderBy") or []:
            # the schema enum is lowercase; the compiler accepted either case
            if o.get("direction"):
                o["direction"] = o["direction"].lower()
    texts = {
        "mapping": json.dumps(mapping, indent=2, ensure_ascii=False) + "\n",
        "master": _scaffold_master(intake, entity_key, entity, domain),
        "manifest": _scaffold_manifest(intake, entity_key, entity, domain,
                                       silver, lookups),
        "sql": sql_text,
    }
    edits = {paths[k]: v for k, v in texts.items()}
    edits[ppath] = pipeline_text
    edits[apps / "overlays" / "dev" / f"{entity}.yaml"] = \
        _scaffold_overlay(entity)
    key_list = ", ".join(p["goldColumn"] for p in entity_key)
    log = [
        f"[SCAFFOLD] new entity {domain}.{entity}: "
        f"{len(intake['columns'])} columns, key ({key_list}), "
        + ("grain collapsed by GROUP BY" if grouped
           else "one row per driving row"),
    ]
    if intake.get("drivingFilter"):
        log.append(f"[SCAFFOLD] row domain: WHERE "
                   f"{intake['drivingFilter']} (== manifest sot_filter)")
    nested_col = next((c for c in intake["columns"]
                       if c.get("kind") == "nested"), None)
    if nested_col and nested_col.get("pick"):
        log.append(
            f"[SCAFFOLD] pick on {nested_col['goldColumnName']}: the WHOLE "
            f"final SELECT reads pick winners — scalar aggregates see only "
            f"the kept row per partition, not every filtered source row")
        lk_parts = [p for p in nested_col["pick"].get("partitionBy") or []
                    if any(p in spec["cols"] for spec in lookups.values())]
        if lk_parts:
            log.append(
                f"[SCAFFOLD] pick partitions on lookup field(s) "
                f"{', '.join(lk_parts)}: rows whose lookup misses share one "
                f"NULL partition (one survivor), and a late-arriving lookup "
                f"row does NOT recompute gold (lookup_only) — repair is "
                f"--refresh_columns {nested_col['goldColumnName']}")
    for k in ("mapping", "master", "manifest", "sql"):
        log.append(f"[SCAFFOLD] {paths[k].name} generated"
                   + (" (draft regenerated)" if k not in missing else ""))
    log.append(f"[SCAFFOLD] pipelines/base/{entity}.yaml + overlays/dev — "
               f"enabled in dev; once the domain's deploy ships these, the "
               f"dispatcher fires the full load on its first sweep")
    log.append(f"[RUNBOOK] before this entity can run, the {domain} domain "
               f"needs (one-time): a deploy workflow that ships its schema/"
               f"mapping artifacts to S3 and runs sync_pipelines for "
               f"domains/{domain} (clone the program domain's), the Glue "
               f"database {gold_resources.gold_database(domain, 'dev')}, and the uif-echo-gold-layer LF "
               f"tag (applied automatically at sync)")
    return edits, log


def main() -> int:
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--intake", required=True, type=Path)
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--apply", action="store_true",
                    help="write the edits (default: print the plan only)")
    args = ap.parse_args()

    intake, intent, refresh, job_props = load_envelope(args.intake)
    edits, log = compile_delta(args.repo_root, intake, intent, refresh, job_props)
    print(f"═══ Delta compile — intent={intent} entity={intake['table']} "
          f"columns={len(intake['columns'])} ═══")
    for line in log:
        print(line)
    if args.apply:
        for p, text in edits.items():
            # a scaffolded entity's directories do not exist yet (UIFPE-260)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="")
            print(f"[WRITTEN] {p}")
        print("Next: py -m pytest domains/product/qe -q  (parity gates)")
    else:
        print("(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
