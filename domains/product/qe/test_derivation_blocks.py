# Derived columns — the query is the contract (ADR-0008, UIFPE-217).
#
# Two gates in one file, hosted in the steward domain (the tooling lives in
# product per capabilities/data/gold-control-plane.md; the mappings under test
# are read cross-domain as data, the same way the census reads the real
# product mapping):
#
#   1. THE HYGIENE GATE — structural triggers (never prose tokens) decide
#      which columns must carry a derivation block. Born passing on the four
#      real blocks and asserted NON-VACUOUSLY: the trigger census pins
#      exactly which columns fire, so the gate can never quietly stop firing.
#
#   2. THE VALIDATION BATTERY — every block present passes ADR-0008's
#      refusals: one read-only Spark SELECT, registered sources only, stable
#      results (no run-varying functions; collects state their ordering),
#      contract columns present in the projection, and `reads` exactly equal
#      to what the parser computes (lineage is computed, never authored —
#      a stale entry fails here).
#
# There is no drift check any more, by design: the mapping CARRIES the query,
# so there is no description to fall out of the SQL. The step-vocabulary
# machinery this file used to exercise lives in git history (UIFPE-210).
import json
from pathlib import Path

import pytest

import derivation_chain as dc

REPO = Path(__file__).resolve().parents[3]
MAPPINGS = {
    "product": (
        REPO / "domains/product/src/schemas/mapping/product/v1/product.mapping.json",
        REPO / "domains/product/src/schemas/mapping/product/v1/manifest.yaml",
    ),
    "program": (
        REPO / "domains/program/src/schemas/mapping/program/v1/program.mapping.json",
        REPO / "domains/program/src/schemas/mapping/program/v1/manifest.yaml",
    ),
    "stock": (
        REPO / "domains/stock/src/schemas/mapping/stock/v1/stock.mapping.json",
        REPO / "domains/stock/src/schemas/mapping/stock/v1/manifest.yaml",
    ),
    "price": (
        REPO / "domains/price/src/schemas/mapping/price/v1/price.mapping.json",
        REPO / "domains/price/src/schemas/mapping/price/v1/manifest.yaml",
    ),
}

# The four real blocks (the fifth case, relatedPrograms.relationshipType, is
# derived INSIDE relatedPrograms' query — nested members carry no block).
EXPECTED_TRIGGERS = {
    ("program", "parentProductId"),
    ("program", "associatedProducts"),
    ("program", "relatedPrograms"),
    ("product", "parentProductId"),
    # Added 2026-08-31 (UIFPE-232): categories joins five tables and derives
    # its hierarchy from a bounded parent walk — exactly the shape ADR-0008
    # says cannot ride a prose note.
    ("product", "categories"),
    # Added 2026-09-07 (UIFPE-269): the BOM arrays' sources registered, so the
    # multi-table trigger fires and each carries its query.
    ("product", "standardComponents"),
    ("product", "customComponents"),
    # Added 2026-09-18 (UIFPE-353). Both fire CORRECTLY and both are EXEMPT
    # from carrying a block - see BLOCK_EXEMPT below. They stay in the census
    # because the census's job is to record what fires, and dropping them
    # would let the triggers stop firing unnoticed.
    ("price", "isbn13"),
    ("price", "priceSpecifications"),
    # Added 2026-09-20 (UIFPE-476): instructorResourceFiles joins the base and
    # translations EAV tables on EXTENSION_ID, the EAV ROW key — not on the
    # driving table's key — so it is not the declared single-hop lookup shape
    # and owes its query. It carries a block.
    ("product", "instructorResourceFiles"),
}

# ── ADR-0008 exemption, on a DERIVED condition ──────────────────────────────
# ADR-0008's no-drift guarantee is mechanical: the compiler wraps the block as
# a CTE and LEFT-joins it, "so the reviewed SQL is the running SQL and there is
# nothing to drift". That holds only while the compiler CONSUMES the block.
# compile_intake_delta REFUSES any registered entity carrying drivingFilter
# (the SQL WHERE and the manifest sot_filter must move together), so for such
# an entity a block would be an unconsumed second copy of the query with
# nothing pinning it to what actually runs - precisely the drift the
# derivation mechanism exists to prevent.
#
# For those entities the reviewed SQL is the hand-authored sql/<entity>.sql,
# and the domain's own gates pin it: the WHERE to sot_filter, the intake's
# pick.orderBy to the picked CTE's ORDER BY, and the 12-month window to
# lk_lines.
#
# priceSpecifications additionally CANNOT satisfy the battery at all: the pick
# reads the clock ("the price EFFECTIVE NOW", owner decision UIFPE-263) and
# _stability_errors refuses current_timestamp / current_date outright. Two
# owner rules collide there; that question is recorded on UIFPE-353 and is NOT
# settled by this exemption.
#
# The condition is DERIVED from the intake, never a hand-written list of
# domains, so the exemption cannot quietly widen to a compiled entity.


def _compiler_refuses_recompile(mapping) -> bool:
    """True when compile_intake_delta would refuse this entity, so an
    ADR-0008 block could never be the running SQL."""
    return bool(mapping.get("drivingFilter"))


def _block_exempt() -> set:
    """(domain, column) pairs excused from carrying a block, computed."""
    out = set()
    for domain in MAPPINGS:
        mapping, manifest = _load(domain)
        if not _compiler_refuses_recompile(mapping):
            continue
        reg = dc.registered_tables(manifest)
        for col in mapping["columns"]:
            if dc.needs_derivation(domain, col, mapping.get("entityKey"), reg):
                out.add((domain, col["goldColumnName"]))
    return out

ALIASES = ["items", "items_ext", "items_all"]  # for the inline battery tests


def test_every_domain_with_a_manifest_is_enrolled_in_this_gate():
    """Auto-enrollment: the gate covers every entity that HAS a runtime
    manifest (stock and price joined as their compiled manifests landed,
    UIFPE-261/263). The moment a newcomer's manifest lands, this test fails
    and enrollment is a one-line MAPPINGS entry, not a discovery."""
    with_manifest = {p.parents[5].name
                     for p in REPO.glob("domains/*/src/schemas/mapping/*/v1/manifest.yaml")}
    assert with_manifest == set(MAPPINGS), (
        f"domains with manifests {sorted(with_manifest)} != gate enrollment "
        f"{sorted(MAPPINGS)} — enroll the newcomer in MAPPINGS above")


def _load(domain):
    mp, mf = MAPPINGS[domain]
    return (json.loads(mp.read_text(encoding="utf-8")),
            mf.read_text(encoding="utf-8"))


def _col(mapping, name):
    return next(c for c in mapping["columns"] if c["goldColumnName"] == name)


# ── 1. the hygiene gate ──────────────────────────────────────────────────────

@pytest.mark.parametrize("domain", ["product", "program", "stock", "price"])
def test_gate_is_green_on_the_registered_mapping(domain):
    mapping, manifest = _load(domain)
    errors = dc.validate_mapping(mapping, manifest)
    if not _compiler_refuses_recompile(mapping):
        assert errors == [], f"{domain}: {errors}"
        return
    # An exempt entity may carry ONLY the "carries no derivation block"
    # error, and only for a column the census already records. Anything
    # else - a malformed block, an unregistered source, an unstable query -
    # must still fail, or the exemption becomes a hiding place.
    exempt = {c for d, c in _block_exempt() if d == domain}
    leftover = []
    for e in errors:
        col = str(e).split(":", 1)[0].strip()
        if "carries no derivation block" in str(e) and col in exempt:
            continue
        leftover.append(e)
    assert leftover == [], (
        f"{domain} is ADR-0008-exempt (the compiler refuses it), but these "
        f"errors are NOT the exempted missing-block case: {leftover}")


def test_trigger_census_fires_on_exactly_the_four_real_blocks():
    """Non-vacuous by construction: the gate must fire on every real case
    and on NO other column. A new derived column changes this census in the
    same PR that adds its block — deliberately, like the AUTO/PLAN census."""
    fired = set()
    for domain in MAPPINGS:
        mapping, manifest = _load(domain)
        reg = dc.registered_tables(manifest)
        for col in mapping["columns"]:
            if dc.needs_derivation(domain, col, mapping.get("entityKey"), reg):
                fired.add((domain, col["goldColumnName"]))
    assert fired == EXPECTED_TRIGGERS


def test_declared_single_hop_lookups_do_not_owe_a_block():
    """A nested column whose every non-driving field is a declared LEFT lookup
    keyed off the DRIVING table is the compiled shape: the join IS the
    contract, so T2 must not fire. Break the declaration any way and the
    multi-table trigger comes back.

    Built SYNTHETICALLY. This used to read price's real priceSpecifications,
    whose whole premise was "three registered tables ... every non-driving
    field is a declared LEFT lookup keyed off the driving table". UIFPE-353
    retired that shape - price now spans five field tables and chains through
    non-driving ones - so the test was asserting a property of a mapping that
    no longer exists, and it broke inside price's own deploy gate. A synthetic
    fixture keeps the guard alive and independent of any one entity.
    """
    import copy

    DRIVING = "DRV_TABLE"
    entity_key = [{"goldColumn": "entityId", "drivingColumn": "DRV_KEY"}]
    reg = {DRIVING, "LOOKUP_A"}

    def lookup(table, column):
        return {"name": column.lower(), "type": "string", "sortKey": False,
                "source": {"table": table, "column": column,
                           "join": {"type": "left", "on": [{
                               "leftTable": DRIVING,
                               "leftColumn": "DRV_KEY",
                               "rightColumn": "DRV_KEY"}]}}}

    col = {
        "goldColumnName": "things", "kind": "nested",
        "channels": {"event": True, "message": True, "api": True},
        "fields": [
            {"name": "thingId", "type": "integer", "sortKey": True,
             "source": {"table": DRIVING, "column": "DRV_ID"}},
            lookup("LOOKUP_A", "LABEL"),
        ],
    }
    assert dc.needs_derivation("synthetic", col, entity_key, reg,
                               DRIVING) is None

    # chained through a NON-driving table
    chained = copy.deepcopy(col)
    chained["fields"][1]["source"]["join"]["on"][0]["leftTable"] = "LOOKUP_A"
    assert dc.needs_derivation("synthetic", chained, entity_key, reg,
                               DRIVING) == "multi-table nested fields"

    # no join declared at all
    undeclared = copy.deepcopy(col)
    undeclared["fields"][1]["source"].pop("join")
    assert dc.needs_derivation("synthetic", undeclared, entity_key, reg,
                               DRIVING) == "multi-table nested fields"

    # fail CLOSED on an incomplete pair - a missing leftTable is not "the
    # driving table by default" (review finding A1)
    anonymous = copy.deepcopy(col)
    anonymous["fields"][1]["source"]["join"]["on"][0].pop("leftTable")
    assert dc.needs_derivation("synthetic", anonymous, entity_key, reg,
                               DRIVING) == "multi-table nested fields"

    # an INNER lookup is not the compiled shape either
    inner = copy.deepcopy(col)
    inner["fields"][1]["source"]["join"]["type"] = "inner"
    assert dc.needs_derivation("synthetic", inner, entity_key, reg,
                               DRIVING) == "multi-table nested fields"

    # the given drivingTable is the anchor: the same declaration read against
    # some OTHER anchor is not single-hop
    assert dc.needs_derivation("synthetic", col, entity_key, reg,
                               "LOOKUP_A") == "multi-table nested fields"


def test_the_adr_0008_exemption_stays_narrow_and_justified():
    """The exemption is computed, but "computed" is not the same as "safe".
    Assert every part of its justification still holds, so it cannot drift
    into a general escape hatch:

      * it applies to price alone today, and to no compiled entity;
      * each exempt entity really does carry drivingFilter, which is what
        makes compile_intake_delta refuse it;
      * each exempt entity really does ship a hand-authored SQL file, which
        is the reviewed artifact standing in for the block;
      * each exempt COLUMN is covered by
        schedule.recompute.undetectable_columns, so the value the block would
        have documented is at least recomputed periodically rather than left
        to rot.

    That last condition is what ties the exemption to a mitigation. If a
    column is exempted and nothing recomputes it, this fails. It is also
    ADR-0020's rule that nothing may be both undetectable and unrepaired.
    """
    import yaml

    exempt = _block_exempt()
    assert exempt == {("price", "isbn13"), ("price", "priceSpecifications")}, (
        f"the ADR-0008 exemption set changed: {sorted(exempt)}. Widening it "
        f"is an ADR decision, not a test edit")

    for domain in sorted({d for d, _ in exempt}):
        mapping, _ = _load(domain)
        assert mapping.get("drivingFilter"), (
            f"{domain} is exempt but carries no drivingFilter, so the "
            f"compiler does NOT refuse it and the exemption has no basis")

        entity = mapping["table"].split(".")[-1]
        sql = (REPO / f"domains/{domain}/src/schemas/mapping/{entity}/v1"
                      f"/sql/{entity}.sql")
        assert sql.is_file(), (
            f"{domain} is exempt because its SQL is hand-authored, but "
            f"{sql} does not exist")

        cfg = yaml.safe_load(
            (REPO / f"domains/{domain}/src/apps/pipelines/base/{entity}.yaml")
            .read_text(encoding="utf-8"))
        repaired = set(((cfg.get("schedule") or {}).get("recompute") or {})
                       .get("undetectable_columns") or [])
        owed = {c for d, c in exempt if d == domain}
        assert owed <= repaired, (
            f"{domain}: exempt column(s) {sorted(owed - repaired)} are not "
            f"in schedule.recompute.undetectable_columns {sorted(repaired)} — "
            f"an exempt column with no recompute leg is documented nowhere AND "
            f"repaired never")


def test_structural_triggers_fire_without_the_registry():
    """Three of the four columns must be caught STRUCTURALLY (the registry is
    the backstop for shapes like product's parentProduct, whose hops hide in
    conditional branches)."""
    saved = dc.DERIVED_REGISTRY
    dc.DERIVED_REGISTRY = set()
    try:
        program, manifest = _load("program")
        reg = dc.registered_tables(manifest)
        reasons = {c["goldColumnName"]:
                   dc.needs_derivation("program", c, program.get("entityKey"), reg)
                   for c in program["columns"]}
        assert reasons["parentProductId"] == "reverse-keyed join"
        assert reasons["associatedProducts"] is not None
        assert reasons["relatedPrograms"] == "multi-table nested fields"
    finally:
        dc.DERIVED_REGISTRY = saved


def test_bom_columns_owe_their_blocks_now_that_the_sources_registered():
    """Until 2026-09-07 standardComponents/customComponents were CONTRACT-FIRST
    placeholders (bom_* not registered, SQL emitting a typed empty array) and
    the registration check exempted them. UIFPE-269 registered the sources:
    the multi-table trigger fires, and each column carries a block whose
    query reads only registered aliases. The exemption is still the
    registration check — strip the bom_* aliases and the trigger goes quiet."""
    product, manifest = _load("product")
    reg = dc.registered_tables(manifest)
    for name in ("standardComponents", "customComponents"):
        col = _col(product, name)
        assert dc.needs_derivation("product", col, product.get("entityKey"),
                                   reg) == "multi-table nested fields", name
        assert col.get("derivation"), f"{name}: block missing"
        # strip the bom_* aliases and the multi-table trigger goes quiet —
        # the exemption is the registration check, not a blind spot
        unregistered = {t for t in reg if not t.startswith("BOM_")}
        assert dc.needs_derivation("product", col, product.get("entityKey"),
                                   unregistered) != "multi-table nested fields", name


def test_gate_refuses_a_missing_block():
    mapping, manifest = _load("program")
    col = _col(mapping, "parentProductId")
    del col["derivation"]
    errors = dc.validate_mapping(mapping, manifest)
    assert any("parentProductId" in e and "no derivation block" in e
               for e in errors)


# ── 2. the validation battery (ADR-0008's refusals 1–4) ─────────────────────

def _block(sql, keyed=None, value="v", reads="auto"):
    b = {"sql": sql,
         "keyedBy": keyed if keyed is not None
         else [{"queryColumn": "k", "entityColumn": "SEGMENT1"}]}
    if value is not None:
        b["valueColumn"] = value
    if reads == "auto":
        try:
            b["reads"] = dc.computed_reads(sql)
        except Exception:
            pass  # unparseable — the battery reports it
    elif reads is not None:
        b["reads"] = reads
    return b


GOOD = ("SELECT e.C_EXT_ATTR1 AS k, min(e.C_EXT_ATTR2) AS v\n"
        "FROM items_ext e WHERE e.ATTR_GROUP_ID = 999 GROUP BY 1")


def test_battery_accepts_the_good_shape():
    assert dc.validate_block("c", _block(GOOD), ALIASES) == []


def test_battery_refuses_non_parsing_sql():
    errors = dc.validate_block("c", _block("SELEC oops FRM y"), ALIASES)
    assert any("does not parse" in e for e in errors)


def test_battery_refuses_statement_lists():
    errors = dc.validate_block("c", _block("SELECT 1 AS k; SELECT 2 AS v"),
                               ALIASES)
    assert any("ONE SELECT" in e for e in errors)


def test_battery_refuses_writes():
    errors = dc.validate_block(
        "c", _block("DELETE FROM items_ext WHERE 1 = 1"), ALIASES)
    assert any("read-only" in e for e in errors)


def test_battery_refuses_unregistered_tables():
    sql = "SELECT a AS k, b AS v FROM not_a_view GROUP BY 1"
    errors = dc.validate_block("c", _block(sql), ALIASES)
    assert any("not_a_view" in e and "not a registered source" in e
               for e in errors)


def test_battery_refuses_an_unregistered_table_hidden_in_a_later_cte():
    """The verification round's blocker: sqlglot exposes every earlier CTE as
    VISIBLE in later scopes, so an attribution keyed on visible-source count
    dropped every unqualified column in a 2nd+ CTE — and a table read only
    through unqualified columns never entered `reads`, which is the set the
    registered-source refusal iterates. Both halves are pinned here."""
    sql = ("WITH a AS (SELECT e.C_EXT_ATTR1 AS iid FROM items_ext e "
           "WHERE e.ATTR_GROUP_ID = 1),\n"
           "b AS (SELECT SOME_ID AS iid, max(SECRET_COL) AS w "
           "FROM not_registered GROUP BY 1)\n"
           "SELECT a.iid AS k, min(b.w) AS v FROM a JOIN b ON a.iid = b.iid "
           "GROUP BY 1")
    reads = dc.computed_reads(sql)
    assert reads["not_registered"] == ["SECRET_COL", "SOME_ID"], \
        "unqualified columns in a later CTE must still be attributed"
    errors = dc.validate_block("c", _block(sql), ALIASES)
    assert any("not_registered" in e and "not a registered source" in e
               for e in errors)


def test_battery_refuses_a_table_read_with_zero_column_references():
    sql = "SELECT 1 AS k, 2 AS v FROM not_registered"
    assert "not_registered" in dc.computed_reads(sql)
    errors = dc.validate_block("c", _block(sql), ALIASES)
    assert any("not_registered" in e for e in errors)


def test_battery_refuses_database_qualified_references():
    """`some_db.items_ext` is a direct catalog read, not the registered temp
    view — the qualifier is kept in the reads key so it can never pass the
    bare-alias check by name coincidence."""
    sql = "SELECT t.A AS k, t.B AS v FROM some_db.items_ext t"
    errors = dc.validate_block("c", _block(sql), ALIASES)
    assert any("some_db.items_ext" in e and "not a registered source" in e
               for e in errors)


def test_battery_refuses_a_trailing_semicolon():
    errors = dc.validate_block("c", _block(GOOD + ";"), ALIASES)
    assert any("';'" in e for e in errors)


def test_battery_refuses_the_java_escape_hatch():
    sql = ("SELECT e.X AS k, reflect('java.lang.Math', 'random') AS v "
           "FROM items_ext e")
    errors = dc.validate_block("c", _block(sql), ALIASES)
    assert any("unstable function" in e and "reflect" in e for e in errors)


def test_battery_refuses_a_struct_member_column():
    errors = dc.validate_block("parent.member", _block(GOOD), ALIASES)
    assert any("struct member" in e for e in errors)


def test_battery_refuses_unstable_functions():
    # localtimestamp/current_timezone/shuffle parse to TYPED sqlglot nodes,
    # never Anonymous — listing them only in _NONDET_ANONYMOUS was a blind
    # spot the UI-mirror verification round exposed (UIFPE-219)
    for bad in ("rand()", "uuid()", "current_timestamp",
                "localtimestamp()", "current_timezone()",
                "shuffle(array(e.Y))"):
        sql = f"SELECT e.X AS k, {bad} AS v FROM items_ext e"
        errors = dc.validate_block("c", _block(sql), ALIASES)
        assert any("unstable function" in e for e in errors), bad


def test_battery_refuses_unordered_collects_and_accepts_sorted_ones():
    base = "SELECT e.X AS k, {} AS v FROM items_ext e GROUP BY 1"
    errors = dc.validate_block(
        "c", _block(base.format("collect_list(e.Y)")), ALIASES)
    assert any("without a stated ordering" in e for e in errors)
    assert dc.validate_block(
        "c", _block(base.format("array_sort(collect_list(e.Y))")),
        ALIASES) == []
    errors = dc.validate_block(
        "c", _block(base.format("collect_set(e.Y)")), ALIASES)
    assert any("without a stated ordering" in e for e in errors)


def test_battery_refuses_contract_columns_missing_from_the_projection():
    errors = dc.validate_block(
        "c", _block(GOOD, keyed=[{"queryColumn": "nope",
                                  "entityColumn": "SEGMENT1"}]), ALIASES)
    assert any("'nope' is not in the query's output" in e for e in errors)
    errors = dc.validate_block("c", _block(GOOD, value="absent"), ALIASES)
    assert any("'absent' is not in the query's output" in e for e in errors)
    # valueColumn defaults to the gold column name
    errors = dc.validate_block("ghostCol", _block(GOOD, value=None), ALIASES)
    assert any("'ghostCol' is not in the query's output" in e for e in errors)


def test_battery_requires_the_key_contract():
    errors = dc.validate_block("c", {"sql": GOOD, "valueColumn": "v"},
                               ALIASES)
    assert any("keyedBy missing" in e for e in errors)


def test_reads_is_computed_never_authored():
    stale = _block(GOOD, reads={"items_ext": ["WRONG"]})
    errors = dc.validate_block("c", stale, ALIASES)
    assert any("reads is stale" in e for e in errors)
    missing = _block(GOOD, reads=None)
    errors = dc.validate_block("c", missing, ALIASES)
    assert any("reads missing" in e for e in errors)


def test_computed_reads_attributes_columns_through_aliases_and_scopes():
    """CTE names are excluded, qualified columns resolve through the alias
    map, and unqualified columns attribute to a single-source scope's table."""
    sql = ("WITH inner_cte AS (\n"
           "  SELECT C_EXT_ATTR9 AS p FROM items_ext WHERE ATTR_GROUP_ID = 1\n"
           ")\n"
           "SELECT l.C_EXT_ATTR1 AS k, min(a.SEGMENT1) AS v\n"
           "FROM items_ext l\n"
           "JOIN items_all a ON a.INVENTORY_ITEM_ID = l.INVENTORY_ITEM_ID\n"
           "LEFT JOIN inner_cte i ON i.p = a.SEGMENT1\n"
           "GROUP BY 1")
    assert dc.computed_reads(sql) == {
        "items_all": ["INVENTORY_ITEM_ID", "SEGMENT1"],
        "items_ext": ["ATTR_GROUP_ID", "C_EXT_ATTR1", "C_EXT_ATTR9",
                      "INVENTORY_ITEM_ID"],
    }


def test_registered_blocks_lineage_is_the_real_one():
    """The migrated blocks' reads survive round-tripping through the parser
    (validate_mapping already asserts equality; this pins the CONTENT so a
    lineage regression is visible in the diff, not just 'stale')."""
    program, _ = _load("program")
    assert set(_col(program, "parentProductId")["derivation"]["reads"]) \
        == {"items_ext", "items_all"}
    assert set(_col(program, "relatedPrograms")["derivation"]["reads"]) \
        == {"related", "items_all", "rel_types"}
    product, _ = _load("product")
    assert set(_col(product, "parentProductId")["derivation"]["reads"]) \
        == {"items", "items_ext", "items_all"}


# ── 3. assembly (the wrap the compiler splices) ─────────────────────────────

def test_render_wrap_embeds_the_query_verbatim_and_joins_left():
    block = _block(GOOD)
    cte, join, select_expr = dc.render_wrap("myCol", block)
    assert cte.startswith("d_myCol AS (")
    for line in GOOD.splitlines():
        assert f"  {line}" in cte, "the query is embedded verbatim, indented"
    assert join.splitlines()[-1].startswith("LEFT JOIN d_myCol ON ")
    assert "items.SEGMENT1 = d_myCol.k" in join   # no cast: not an _ID column
    assert select_expr == "d_myCol.v"


def test_render_wrap_casts_id_entity_columns():
    block = _block(GOOD, keyed=[
        {"queryColumn": "k", "entityColumn": "INVENTORY_ITEM_ID"},
        {"queryColumn": "k2", "entityColumn": "ORGANIZATION_ID"}])
    _, join, _ = dc.render_wrap("myCol", block)
    assert "CAST(items.INVENTORY_ITEM_ID AS BIGINT) = d_myCol.k" in join
    assert "CAST(items.ORGANIZATION_ID AS BIGINT) = d_myCol.k2" in join
    assert "AND" in join


def test_plan_lines_carry_the_contract_and_the_query():
    lines = dc.plan_lines(_block(GOOD), "myCol")
    joined = "\n".join(lines)
    assert "k -> SEGMENT1" in joined
    assert "GROUP BY 1" in joined
