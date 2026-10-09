"""Delta compiler (UIFPE-100) — golden tests against COPIES of the real
artifacts: each test applies an intake to a temp clone of the repo's four
product files and asserts the edits landed everywhere, consistently ordered,
and still parse/validate. The real artifacts are never touched.
"""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

import compile_intake_delta as cid

REPO = Path(__file__).resolve().parents[4]
SCHEMA = REPO / "stacks" / "apicurio" / "standards" / "gold-attribute-mapping.schema.json"


def scalar(name="testAttr", column="C_EXT_ATTR33",
           flt="ATTR_GROUP_ID = 802", table="EGO_MTL_SY_ITEMS_EXT_B", **over):
    col = {
        "goldColumnName": name,
        "businessDescription": f"Test attribute {name}",
        "kind": "scalar", "dataType": "string",
        "source": {"table": table, "column": column, "filter": flt,
                   "join": None if table == "MTL_SYSTEM_ITEMS_B" else {
                       "type": "left", "on": [{"leftTable": "MTL_SYSTEM_ITEMS_B",
                       "leftColumn": "INVENTORY_ITEM_ID",
                       "rightColumn": "INVENTORY_ITEM_ID"}]}},
        "transform": None,
        "channels": {"event": False, "message": False, "api": True},
        "nullable": True, "notes": None,
    }
    col.update(over)
    return col


def intake_for(cols, transforms=None, table="gold_product.product",
               entity_key=None):
    intake = {"domain": "product", "table": table,
              "drivingTable": "MTL_SYSTEM_ITEMS_B",
              "batch": {"requestedBy": "qe", "targetDate": None,
                        "backfillMode": "silent", "newChannel": None},
              "columns": cols}
    if transforms:
        intake["transforms"] = transforms
    if entity_key:
        intake["entityKey"] = entity_key
    return intake


@pytest.fixture()
def repo_clone(tmp_path):
    """Temp repo with real copies of the four artifacts + the pipeline
    config (UIFPE-107: the schedule rides the intake envelope too)."""
    base = tmp_path / "domains" / "product" / "src" / "schemas"
    real = REPO / "domains" / "product" / "src" / "schemas"
    for rel in ("mapping/product/v1/product.mapping.json",
                "gold/product/v1/master.json",
                "mapping/product/v1/manifest.yaml",
                "mapping/product/v1/sql/product.sql"):
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(real / rel, dst)
    pdst = tmp_path / "domains" / "product" / "src" / "apps" / "pipelines" / "base" / "product.yaml"
    pdst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "domains" / "product" / "src" / "apps" / "pipelines"
                / "base" / "product.yaml", pdst)
    return tmp_path


def apply(repo, cols, intent="add", refresh=None, transforms=None,
          table="gold_product.product", entity_key=None):
    edits, log = cid.compile_delta(
        repo, intake_for(cols, transforms, table, entity_key), intent, refresh)
    for p, text in edits.items():
        p.write_text(text, encoding="utf-8", newline="")
    return {p.name: text for p, text in edits.items()}, log


def validate_structured(repo):
    """Every structured artifact still parses; mapping still validates."""
    import jsonschema
    base = repo / "domains" / "product" / "src" / "schemas"
    mapping = json.loads((base / "mapping/product/v1/product.mapping.json")
                         .read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(mapping)
    master = json.loads((base / "gold/product/v1/master.json").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((base / "mapping/product/v1/manifest.yaml").read_text(encoding="utf-8"))
    return mapping, master, manifest


class TestAddEavExistingGroup:
    def test_all_four_artifacts_updated_and_ordered(self, repo_clone):
        out, log = apply(repo_clone, [scalar()])
        assert any("[AUTO]" in l and "ag802" in l for l in log)
        mapping, master, manifest = validate_structured(repo_clone)

        assert any(c["goldColumnName"] == "testAttr" for c in mapping["columns"])
        assert master["properties"]["testAttr"]["x-channels"] == ["api"]
        assert manifest["canonical_lineage"]["testAttr"]["pdh_column"] == "C_EXT_ATTR33"
        assert manifest["output_columns"][-1] == "testAttr"
        # ORDER: last business property in master == last output column
        biz = [k for k in master["properties"] if not k.startswith("_")]
        assert biz[-1] == "testAttr"

        sql = out["product.sql"]
        assert re.search(r"(?m)^\s+max\(C_EXT_ATTR33\) AS testAttr", sql)
        # appended as the final SELECT column, before FROM items
        final = sql[sql.rfind("SELECT"):sql.rfind("\nFROM items")]
        assert final.rstrip().endswith("AS testAttr")

    def test_sql_still_balanced(self, repo_clone):
        out, _ = apply(repo_clone, [scalar()])
        sql = out["product.sql"]
        assert sql.count("(") == sql.count(")")


class TestAddEavNewGroup:
    def test_creates_cte_and_join(self, repo_clone):
        out, log = apply(repo_clone, [scalar(
            name="newGroupAttr", column="C_EXT_ATTR9",
            flt="ATTR_GROUP_ID = 999")])
        assert any("created CTE ag999" in l for l in log)
        sql = out["product.sql"]
        assert re.search(r"(?ms)^ag999 AS \(.*?ATTR_GROUP_ID = 999.*?\n\),", sql)
        assert re.search(r"(?m)^LEFT JOIN ag999\s+ON CAST\(items\.INVENTORY_ITEM_ID AS BIGINT\) = ag999\.iid", sql)
        assert "ag999.newGroupAttr" in sql
        validate_structured(repo_clone)


class TestAddDrivingScalar:
    def test_items_expression(self, repo_clone):
        out, log = apply(repo_clone, [scalar(
            name="shipWeight", column="SHIP_WEIGHT",
            flt=None, table="MTL_SYSTEM_ITEMS_B", dataType="integer")])
        assert any("[AUTO]" in l and "driving" in l for l in log)
        assert "CAST(items.SHIP_WEIGHT AS BIGINT)" in out["product.sql"]
        validate_structured(repo_clone)


class TestModifyEav:
    def test_repoints_column_and_emits_refresh_runbook(self, repo_clone):
        # lineOfBusiness lives in ag802 as max(C_EXT_ATTR40)
        col = scalar(name="lineOfBusiness", column="C_EXT_ATTR41",
                     flt="ATTR_GROUP_ID = 802")
        col["businessDescription"] = "Line of Business"
        out, log = apply(repo_clone, [col], intent="modify",
                         refresh={"historical": True, "mode": "silent"})
        sql = out["product.sql"]
        assert re.search(r"(?m)^\s+max\(C_EXT_ATTR41\) +AS lineOfBusiness", sql)
        assert not re.search(r"max\(C_EXT_ATTR40\) +AS lineOfBusiness", sql)
        assert any("--refresh_columns" in l and "lineOfBusiness" in l
                   and "silent" in l for l in log)
        mapping, _, manifest = validate_structured(repo_clone)
        entry = next(c for c in mapping["columns"]
                     if c["goldColumnName"] == "lineOfBusiness")
        assert entry["source"]["column"] == "C_EXT_ATTR41"
        assert manifest["canonical_lineage"]["lineOfBusiness"]["pdh_column"] == "C_EXT_ATTR41"
        # exactly one entry — modify replaced, not duplicated
        assert sum(1 for c in mapping["columns"]
                   if c["goldColumnName"] == "lineOfBusiness") == 1


def conditional(name="testCond", groups=("990", "991"),
                columns=("C_EXT_ATTR2", "C_EXT_ATTR3"), **over):
    """Two-branch EAV conditional: ELSE first (the Excel/intake convention),
    the named 10002 branch second — fresh groups by default so the compiler
    exercises its CTE-creation path against the real SQL."""
    col = {
        "goldColumnName": name, "businessDescription": f"cond test {name}",
        "kind": "conditional", "dataType": "string",
        "discriminator": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                          "column": "ITEM_CATALOG_GROUP_ID"},
        "channels": {"event": False, "message": False, "api": True},
        "branches": [
            {"condition": "ELSE",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": columns[0],
                        "filter": f"ATTR_GROUP_ID = {groups[0]}"},
             "fallback": "returnNull", "notes": None},
            {"condition": "10002 (Digital)",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": columns[1],
                        "filter": f"ATTR_GROUP_ID = {groups[1]}"},
             "fallback": "returnNull", "notes": None},
        ], "notes": None,
    }
    col.update(over)
    return col


class TestAutoConditional:
    """UIFPE-106 (D5): top-level EAV conditionals are AUTO — one CTE per
    branch carrying the discriminator predicate, strict-pick COALESCE with
    named branches first (product.sql's digital-first convention)."""

    def test_branch_ctes_and_strict_pick(self, repo_clone):
        out, log = apply(repo_clone, [conditional()])
        assert any(l.startswith("[AUTO] testCond: ") for l in log)
        sql = out["product.sql"]
        # each branch CTE folds the discriminator predicate into its WHERE
        assert re.search(
            r"(?ms)^ag991 AS \(.*?WHERE ATTR_GROUP_ID = 991 "
            r"AND ITEM_CATALOG_GROUP_ID = 10002 GROUP BY 1", sql)
        assert re.search(
            r"(?ms)^ag990 AS \(.*?WHERE ATTR_GROUP_ID = 990 "
            r"AND ITEM_CATALOG_GROUP_ID <> 10002 GROUP BY 1", sql)
        assert re.search(r"(?m)^LEFT JOIN ag991\s+ON ", sql)
        # strict pick: named branch first, ELSE last
        assert "COALESCE(ag991.testCond, ag990.testCond)" in sql
        assert sql.count("(") == sql.count(")")
        mapping, master, manifest = validate_structured(repo_clone)
        assert master["properties"]["testCond"]["x-channels"] == ["api"]
        assert manifest["output_columns"][-1] == "testCond"

    def test_second_conditional_extends_the_branch_ctes(self, repo_clone):
        apply(repo_clone, [conditional()])
        out, log = apply(repo_clone, [conditional(
            name="testCondB", columns=("C_EXT_ATTR5", "C_EXT_ATTR6"))])
        joined = "\n".join(log)
        assert "extended existing CTE ag991" in joined
        assert "extended existing CTE ag990" in joined
        assert "COALESCE(ag991.testCondB, ag990.testCondB)" in out["product.sql"]

    def test_predicate_conflict_with_existing_cte_is_refused(self, repo_clone):
        # ag802 exists WITHOUT a branch condition — extending it with one
        # would leak the other catalog group's rows through this branch
        col = conditional(name="testCondC", groups=("989", "802"))
        with pytest.raises(ValueError, match="two branch conditions"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_predicate_containment_is_word_bounded(self, repo_clone):
        """'= 1000' must not pass inside an existing '= 10002' — a substring
        match would silently extend the WRONG branch CTE."""
        apply(repo_clone, [conditional()])   # creates ag991 with '= 10002'
        col = conditional(name="testCondG", groups=("988", "991"))
        col["branches"][1]["condition"] = "1000 (Something Else)"
        with pytest.raises(ValueError, match="two branch conditions"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_stricter_hand_cte_is_not_extended(self, repo_clone):
        """The other direction: a hand CTE whose WHERE carries EXTRA conjuncts
        beyond the branch predicate must refuse too — extending it would
        silently suppress this column's values for the rows the extra
        conjunct excludes (the 2026-08-10 D1 shape)."""
        apply(repo_clone, [conditional()])
        sqlp = (repo_clone / "domains/product/src/schemas/mapping/product/v1"
                "/sql/product.sql")
        sqlp.write_text(sqlp.read_text(encoding="utf-8").replace(
            "ATTR_GROUP_ID = 991 AND ITEM_CATALOG_GROUP_ID = 10002",
            "ATTR_GROUP_ID = 991 AND ITEM_CATALOG_GROUP_ID = 10002 "
            "AND C_EXT_ATTR1 IS NOT NULL"), encoding="utf-8")
        col = conditional(name="testCondH", groups=("987", "991"),
                          columns=("C_EXT_ATTR5", "C_EXT_ATTR6"))
        with pytest.raises(ValueError, match="two branch conditions"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_malformed_condition_value_is_refused(self, repo_clone):
        """'10002 Digital' (parens forgotten) must not quote into a string
        literal that compares NULL against the numeric discriminator and
        silently empties every branch."""
        col = conditional(name="testCondI")
        col["branches"][1]["condition"] = "10002 Digital"
        with pytest.raises(ValueError, match="parse cleanly"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_overlapping_conditions_without_scopes_are_refused(self, repo_clone):
        """Two unscoped named branches sharing a discriminator value break
        the strict pick — COALESCE order would silently decide the winner."""
        col = conditional(name="testCondJ")
        col["branches"][1]["condition"] = "10002,10003 (bundles)"
        col["branches"].append(
            {"condition": "10003 (single)",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": "C_EXT_ATTR4",
                        "filter": "ATTR_GROUP_ID = 986"},
             "fallback": "returnNull", "notes": None})
        with pytest.raises(ValueError, match="overlap"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_duplicate_scope_across_branches_is_refused(self, repo_clone):
        """Two branches with the SAME scope would render a dead CASE arm —
        the second WHEN repeats the first's predicate and never fires."""
        col = conditional(name="testCondK")
        col["branches"][1]["scope"] = {"group": "ukOrgs"}
        col["branches"].append(
            {"condition": "10003 (Other)",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": "C_EXT_ATTR4",
                        "filter": "ATTR_GROUP_ID = 985"},
             "fallback": "returnNull", "notes": None,
             "scope": {"group": "ukOrgs"}})
        with pytest.raises(ValueError, match="share the same scope"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_rich_branch_filter_stays_plan(self, repo_clone):
        """A branch filter carrying any conjunct beyond the ATTR_GROUP_ID
        equality is not bare-EAV — compiling it would silently drop the
        extra predicate from the SQL while the manifest records it."""
        col = conditional(name="testCondL")
        col["branches"][1]["source"]["filter"] = \
            "ATTR_GROUP_ID = 991 AND C_EXT_ATTR9 IS NOT NULL"
        _, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testCondL (plan:conditional)")
                   for l in log)

    def test_branch_transform_stays_plan(self, repo_clone):
        """A branch-level transform outside the CTE could revive NULL for
        keys the branch has no rows for — placement is an engineering
        decision, so the column stays PLAN."""
        col = conditional(name="testCondM")
        col["branches"][1]["transform"] = {"steps": [{"op": "trim"}]}
        _, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testCondM (plan:conditional)")
                   for l in log)

    def test_scope_binds_only_to_its_declared_table(self, repo_clone):
        """An inline scope naming a column that only an UNRELATED CTE exposes
        must refuse, not silently bind the CASE to that CTE (every EAV CTE
        maxes C_EXT_ATTR* columns, so name collisions are the norm)."""
        col = conditional(name="testCondN", groups=("984", "983"))
        col["branches"][1]["scope"] = {
            "table": "MTL_PARAMETERS", "column": "C_EXT_ATTR2",
            "op": "equals", "values": ["X"], "label": "weird"}
        col["branches"].append(
            {"condition": "10002 (Digital)",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": "C_EXT_ATTR4",
                        "filter": "ATTR_GROUP_ID = 982"},
             "fallback": "returnNull", "notes": None,
             "scope": {"group": "nonUkOrgs"}})
        with pytest.raises(ValueError, match="not exposed by any compiled"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_fallback_to_else_stays_plan(self, repo_clone):
        """No deterministic strict-pick rendering exists for cross-branch
        fallback (all 60 registered branches are returnNull) — the classifier
        keeps the column PLAN rather than failing the compile."""
        col = conditional(name="testCondD")
        col["branches"][1]["fallback"] = "fallbackToElse"
        out, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testCondD (plan:conditional)")
                   for l in log)
        assert "ag991.testCondD" not in out["product.sql"]

    def test_duplicate_group_across_branches_is_refused(self, repo_clone):
        col = conditional(name="testCondE", groups=("990", "990"))
        with pytest.raises(ValueError, match="share attribute group"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_dangling_row_group_is_refused(self, repo_clone):
        col = conditional(name="testCondF")
        col["branches"][1]["scope"] = {"group": "notAGroup"}
        with pytest.raises(ValueError, match="not defined"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_scoped_branches_fan_out_via_case(self, repo_clone):
        """The ukOrgs/nonUkOrgs shape: two branches share the 10002 condition
        and differ only in scope; the complement takes the CASE's ELSE slot,
        the scope column resolves to the compiled orgs dimension."""
        col = conditional(name="testScoped", groups=("994", "992"))
        col["branches"][1]["scope"] = {"group": "ukOrgs"}
        col["branches"].append(
            {"condition": "10002 (Digital)",
             "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                        "column": "C_EXT_ATTR4",
                        "filter": "ATTR_GROUP_ID = 993"},
             "fallback": "returnNull", "notes": None,
             "scope": {"group": "nonUkOrgs"}})
        out, log = apply(repo_clone, [col])
        assert any(l.startswith("[AUTO] testScoped: ") for l in log)
        sql = out["product.sql"]
        assert ("CASE WHEN orgs.organizationCode LIKE '01%' "
                "THEN ag992.testScoped ELSE ag993.testScoped END") in sql
        assert re.search(
            r"COALESCE\(CASE WHEN .*? END, ag994\.testScoped\)", sql)
        validate_structured(repo_clone)

    def test_multihop_notes_keep_the_column_plan(self, repo_clone):
        sql_before = (repo_clone / "domains/product/src/schemas/mapping"
                      "/product/v1/sql/product.sql").read_text(encoding="utf-8")
        col = conditional(name="testHop",
                          notes="MULTI-HOP derivation — SQL authoritative")
        out, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testHop (plan:conditional)")
                   for l in log)
        assert out["product.sql"] == sql_before

    def test_modify_stays_a_plan(self, repo_clone):
        """The shipped pick may be hand-tuned — a conditional restructure
        mirrors the driving-modify posture."""
        real = json.loads(
            (REPO / "domains/product/src/schemas/mapping/product/v1"
             / "product.mapping.json").read_text(encoding="utf-8"))
        brand = next(c for c in real["columns"]
                     if c["goldColumnName"] == "brand")
        _, log = apply(repo_clone, [brand], intent="modify",
                       transforms=real.get("transforms"))
        assert any(l.startswith("[PLAN] brand (plan:conditional-modify)")
                   for l in log)

    def test_derived_sql_add_splices_the_query_verbatim(self, repo_clone):
        """ADR-0008 (the query is the contract): ADDING a derived column is
        AUTO — the author's SELECT becomes the d_<name> CTE verbatim,
        LEFT-joined on the declared contract, and `reads` is recomputed by
        the parser before the mapping is written (computed, never authored).
        A blockless MODIFY keeps the registered block and stays a plan (the
        shipped SQL may predate the wrap shape)."""
        block = {"sql": ("SELECT e.C_EXT_ATTR1 AS k, min(e.C_EXT_ATTR2) AS v\n"
                         "FROM items_ext e WHERE e.ATTR_GROUP_ID = 999 "
                         "GROUP BY 1"),
                 "keyedBy": [{"queryColumn": "k",
                              "entityColumn": "SEGMENT1"}],
                 "valueColumn": "v"}
        col = scalar(name="testDerived", derivation=block)   # driving-shaped
        col["source"] = {"table": "MTL_SYSTEM_ITEMS_B", "column": "SEGMENT1",
                         "filter": None, "join": None}
        out, log = apply(repo_clone, [col])
        joined = "\n".join(log)
        assert "[AUTO] testDerived: spliced d_testDerived" in joined
        sql = out["product.sql"]
        assert "d_testDerived AS (" in sql
        assert "  FROM items_ext e WHERE e.ATTR_GROUP_ID = 999 GROUP BY 1" \
            in sql, "the query is embedded verbatim"
        assert "LEFT JOIN d_testDerived ON items.SEGMENT1 = d_testDerived.k" \
            in sql
        assert re.search(r"(?m)^  d_testDerived\.v\s+AS testDerived$", sql)
        mapping = json.loads(out["product.mapping.json"])
        entry = next(c for c in mapping["columns"]
                     if c["goldColumnName"] == "testDerived")
        assert entry["derivation"]["reads"] == {
            "items_ext": ["ATTR_GROUP_ID", "C_EXT_ATTR1", "C_EXT_ATTR2"]}, \
            "reads is computed from the parse at compile time"
        validate_structured(repo_clone)
        # modify without the block: the registered block survives, the SQL
        # is untouched, and the change is an engineer's plan (apply already
        # wrote the add's artifacts to the clone)
        sql_before = out["product.sql"]
        col2 = scalar(name="testDerived")
        col2["source"] = col["source"]
        out2, log2 = apply(repo_clone, [col2], intent="modify",
                           refresh={"historical": False})
        joined2 = "\n".join(log2)
        assert "[PLAN] testDerived (plan:derived-sql-modify)" in joined2
        assert "the query is the contract" in joined2
        assert "GROUP BY 1" in joined2, "the plan carries the query itself"
        assert out2["product.sql"] == sql_before, "modify writes no SQL"
        mapping2 = json.loads(out2["product.mapping.json"])
        entry2 = next(c for c in mapping2["columns"]
                      if c["goldColumnName"] == "testDerived")
        assert entry2["derivation"]["sql"] == block["sql"], \
            "the registered block must survive a blockless modify"

    def test_derived_nested_add_coalesces_to_the_empty_array(self, repo_clone):
        """Empty arrays are [] and never NULL — the hash-stability commitment
        every hand-written array column keeps. A spliced derived array must
        not introduce the first NULL through its LEFT-join miss."""
        block = {"sql": ("SELECT e.C_EXT_ATTR1 AS k, "
                         "array_sort(collect_list(e.C_EXT_ATTR2)) AS v\n"
                         "FROM items_ext e WHERE e.ATTR_GROUP_ID = 998 "
                         "GROUP BY 1"),
                 "keyedBy": [{"queryColumn": "k",
                              "entityColumn": "SEGMENT1"}],
                 "valueColumn": "v"}
        col = nested_col(name="testDerivedList", group=998)
        col["derivation"] = block
        out, log = apply(repo_clone, [col])
        assert "[AUTO] testDerivedList: spliced d_testDerivedList" \
            in "\n".join(log)
        assert re.search(
            r"(?m)^  COALESCE\(d_testDerivedList\.v, array\(\)\)\s+"
            r"AS testDerivedList$", out["product.sql"])

    def test_derived_sql_battery_refuses_before_any_artifact_is_written(
            self, repo_clone):
        """Refusal-first and atomic: an unregistered table (or any battery
        failure) aborts the whole compile — no artifact text is produced."""
        block = {"sql": "SELECT a AS k, b AS v FROM not_a_view GROUP BY 1",
                 "keyedBy": [{"queryColumn": "k",
                              "entityColumn": "SEGMENT1"}],
                 "valueColumn": "v"}
        col = scalar(name="testDerived", derivation=block)
        col["source"] = {"table": "MTL_SYSTEM_ITEMS_B", "column": "SEGMENT1",
                         "filter": None, "join": None}
        with pytest.raises(ValueError, match="not a registered source"):
            apply(repo_clone, [col])

    def test_derived_sql_refuses_struct_members_and_empty_blocks(
            self, repo_clone):
        """A dotted goldColumnName would splice an unparseable CTE name; an
        EMPTY block classifies derived-sql on both sides of the mirror
        (`is not None` / `!= null`) and is refused by the battery instead of
        slipping through a kind-shaped writer with the block dropped."""
        block = {"sql": "SELECT e.A AS k, min(e.B) AS v FROM items_ext e "
                        "GROUP BY 1",
                 "keyedBy": [{"queryColumn": "k",
                              "entityColumn": "SEGMENT1"}],
                 "valueColumn": "v"}
        member = conditional(name="fulfillmentOrgDetails.testDerivedMember")
        member["derivation"] = block
        with pytest.raises(ValueError, match="struct member"):
            apply(repo_clone, [member])
        empty = scalar(name="testDerived", derivation={})
        empty["source"] = {"table": "MTL_SYSTEM_ITEMS_B",
                           "column": "SEGMENT1", "filter": None, "join": None}
        assert cid.classify(empty, "MTL_SYSTEM_ITEMS_B") == "derived-sql"
        with pytest.raises(ValueError, match="sql missing"):
            apply(repo_clone, [empty])

    def test_object_span_survives_braces_inside_sql_strings(self):
        """derivation.sql embeds arbitrary SQL — a brace in a string literal
        (regexp_replace(x, '}', ...)) must not truncate the modify span."""
        text = ('{\n  "columns": [\n'
                '    {\n      "goldColumnName": "withBrace",\n'
                '      "derivation": {\n'
                '        "sql": "SELECT regexp_replace(a, \'}\', \'\') AS k"\n'
                '      }\n    },\n'
                '    {\n      "goldColumnName": "after"\n    }\n  ]\n}')
        s, e = cid._object_span(text, "withBrace")
        assert '"goldColumnName": "withBrace"' in text[s:e]
        assert text[s:e].count('"goldColumnName"') == 1, \
            "the span must close at the column object, not inside the string"
        assert '"after"' not in text[s:e]


class TestAutoLookupJoin:
    """UIFPE-106: lookup-join scalars against a REGISTERED manifest source
    compile to a dedicated dimension CTE + LEFT JOIN (the orgs precedent)."""

    def lookup_col(self, name="orgName", column="ORGANIZATION_NAME",
                   table="MTL_PARAMETERS", flt=None,
                   on=None):
        col = scalar(name=name, column=column, flt=flt, table=table)
        col["source"]["join"] = {"type": "left", "on": on or [
            {"leftTable": "MTL_SYSTEM_ITEMS_B",
             "leftColumn": "ORGANIZATION_ID",
             "rightColumn": "ORGANIZATION_ID"}]}
        return col

    def test_single_key_lookup_creates_dimension_cte(self, repo_clone):
        out, log = apply(repo_clone, [self.lookup_col()])
        assert any(l.startswith("[AUTO] orgName: ") for l in log)
        sql = out["product.sql"]
        # the CTE reads the source's REGISTERED alias, not the PDH name
        assert re.search(
            r"(?ms)^lk_orgName AS \(.*?"
            r"SELECT CAST\(ORGANIZATION_ID AS BIGINT\) AS oid,\n"
            r"\s+max\(ORGANIZATION_NAME\) AS orgName\n"
            r"  FROM org_params GROUP BY 1", sql)
        assert ("LEFT JOIN lk_orgName ON CAST(items.ORGANIZATION_ID AS BIGINT)"
                " = lk_orgName.oid") in sql
        assert sql.count("(") == sql.count(")")
        validate_structured(repo_clone)

    def test_two_key_lookup_with_filter(self, repo_clone):
        col = self.lookup_col(
            name="testDesc", column="DESCRIPTION",
            table="MTL_SYSTEM_ITEMS_TL", flt="LANGUAGE = 'US'",
            on=[{"leftTable": "MTL_SYSTEM_ITEMS_B",
                 "leftColumn": "INVENTORY_ITEM_ID",
                 "rightColumn": "INVENTORY_ITEM_ID"},
                {"leftTable": "MTL_SYSTEM_ITEMS_B",
                 "leftColumn": "ORGANIZATION_ID",
                 "rightColumn": "ORGANIZATION_ID"}])
        out, _ = apply(repo_clone, [col])
        sql = out["product.sql"]
        assert re.search(
            r"(?ms)^lk_testDesc AS \(.*?"
            r"FROM items_tl WHERE LANGUAGE = 'US' GROUP BY 1, 2", sql)
        assert re.search(
            r"(?m)^LEFT JOIN lk_testDesc ON CAST\(items\.INVENTORY_ITEM_ID "
            r"AS BIGINT\) = lk_testDesc\.iid\n"
            r"\s+AND CAST\(items\.ORGANIZATION_ID AS BIGINT\)"
            r" = lk_testDesc\.oid", sql)

    def test_unregistered_source_is_refused(self, repo_clone):
        col = self.lookup_col(name="testBad", column="SOMETHING",
                              table="SOME_UNREGISTERED_TABLE")
        with pytest.raises(ValueError, match="not a registered manifest"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_chained_lookup_stays_plan(self, repo_clone):
        """An on-pair anchored on a NON-driving table (a chained lookup,
        A -> B -> C) has no dimension-CTE rendering — compiling it would
        silently rewire the declared hop to the driving table."""
        col = self.lookup_col(name="testChain", column="DESCRIPTION",
                              table="MTL_SYSTEM_ITEMS_TL",
                              on=[{"leftTable": "MTL_PARAMETERS",
                                   "leftColumn": "ORGANIZATION_ID",
                                   "rightColumn": "ORGANIZATION_ID"}])
        _, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testChain (plan:lookup-join)")
                   for l in log)

    def test_non_left_join_stays_plan(self, repo_clone):
        """INNER drops driving rows with no match; the max()-per-key
        dimension CTE only renders LEFT semantics."""
        col = self.lookup_col(name="testInner")
        col["source"]["join"]["type"] = "inner"
        _, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] testInner (plan:lookup-join)")
                   for l in log)

    def test_shared_left_column_keys_do_not_collide(self, repo_clone):
        """Two on-pairs sharing a left column must produce distinct key
        aliases in the CTE, not two 'AS oid' columns."""
        col = self.lookup_col(
            name="testTwoKeys", column="ORGANIZATION_NAME",
            on=[{"leftTable": "MTL_SYSTEM_ITEMS_B",
                 "leftColumn": "ORGANIZATION_ID",
                 "rightColumn": "ORGANIZATION_ID"},
                {"leftTable": "MTL_SYSTEM_ITEMS_B",
                 "leftColumn": "ORGANIZATION_ID",
                 "rightColumn": "MASTER_ORGANIZATION_ID"}])
        out, _ = apply(repo_clone, [col])
        sql = out["product.sql"]
        assert "CAST(ORGANIZATION_ID AS BIGINT) AS oid" in sql
        assert "CAST(MASTER_ORGANIZATION_ID AS BIGINT) AS k2" in sql
        assert re.search(r"(?ms)^lk_testTwoKeys AS \(.*?GROUP BY 1, 2", sql)

    def test_join_lands_after_the_orgs_join(self, repo_clone):
        out, _ = apply(repo_clone, [self.lookup_col()])
        sql = out["product.sql"]
        assert sql.find("LEFT JOIN orgs") < sql.find("LEFT JOIN lk_orgName"), \
            "generated joins follow the orgs dimension join (hand order)"

    def test_modify_stays_a_plan_naming_the_join(self, repo_clone):
        real = json.loads(
            (REPO / "domains/product/src/schemas/mapping/product/v1"
             / "product.mapping.json").read_text(encoding="utf-8"))
        col = next(c for c in real["columns"]
                   if c["goldColumnName"] == "organizationCode")
        _, log = apply(repo_clone, [col], intent="modify",
                       refresh={"historical": False},
                       transforms=real.get("transforms"))
        plan = "\n".join(log)
        assert "[PLAN] organizationCode (plan:lookup-join-modify)" in plan
        assert "LEFT JOIN MTL_PARAMETERS" in plan


# ── Transform engine (UIFPE-105) ─────────────────────────────────────────────

class TestRenderTransform:
    """render_transform mirrors the wizard's transformSql — the two rules
    registered in product.mapping.json are the acceptance shapes."""

    def test_steps_render_the_unit_shape(self):
        t = {"steps": [{"op": "round", "precision": 2},
                       {"op": "cast", "to": "DECIMAL(38,2)"}]}
        assert cid.render_transform(t, {}, "items.UNIT_WEIGHT") == \
            "CAST(round(items.UNIT_WEIGHT, 2) AS DECIMAL(38,2))"

    def test_cases_render_the_cover_image_shape(self):
        t = {"cases": [
            {"when": {"op": "startsWith", "values": ["/A"]},
             "then": {"op": "prepend", "text": "https://dam"}},
            {"when": {"op": "like", "values": ["/coverimage%", "/bigcovers%"]},
             "then": {"op": "prepend", "text": "https://images.pearson.com"}},
        ], "else": "keep"}
        sql = cid.render_transform(t, {}, "x")
        assert "WHEN substring(x, 1, 2) = '/A' THEN concat('https://dam', x)" in sql
        assert ("WHEN x LIKE '/coverimage%' OR x LIKE '/bigcovers%' "
                "THEN concat('https://images.pearson.com', x)") in sql
        assert "ELSE x" in sql

    def test_else_null_and_remaining_ops(self):
        assert "ELSE NULL" in cid.render_transform(
            {"cases": [{"when": {"op": "isNull"},
                        "then": {"op": "literal", "text": "n/a"}}],
             "else": "null"}, {}, "x")
        assert cid.render_transform(
            {"steps": [{"op": "trim"}, {"op": "upper"}]}, {}, "x") == "upper(trim(x))"
        assert cid.render_transform(
            {"steps": [{"op": "coalesceTo", "value": "N/A"}]}, {}, "x") == \
            "COALESCE(x, 'N/A')"
        assert cid.render_transform({"sqlExpr": "date_format({x}, 'yyyy')"},
                                    {}, "c") == "date_format(c, 'yyyy')"

    def test_named_rules_resolve_and_dangle_loudly(self):
        reg = {"upperIt": {"steps": [{"op": "upper"}]}}
        assert cid.render_transform({"rule": "upperIt"}, reg, "x") == "upper(x)"
        with pytest.raises(ValueError, match="not in the transforms registry"):
            cid.render_transform({"rule": "ghost"}, reg, "x")

    def test_prose_refuses_to_compile(self):
        with pytest.raises(ValueError, match="prose transform"):
            cid.render_transform("round to 2 decimal places", {}, "x")


class TestTransformsInCompile:
    def test_eav_add_with_registered_rule_wraps_final_select(self, repo_clone):
        # roundTo2dp is registered in the real mapping the clone copies —
        # a {"rule"} reference resolves with NO transforms in the intake.
        out, log = apply(repo_clone, [scalar(transform={"rule": "roundTo2dp"})])
        assert any("transform in final SELECT" in l for l in log)
        sql = out["product.sql"]
        assert "CAST(round(ag802.testAttr, 2) AS DECIMAL(38,2))" in sql
        final = sql[sql.rfind("SELECT"):sql.rfind("\nFROM items")]
        assert final.rstrip().endswith("AS testAttr")
        validate_structured(repo_clone)

    def test_driving_add_with_inline_cases(self, repo_clone):
        col = scalar(name="normalizedCode", column="SEGMENT2", flt=None,
                     table="MTL_SYSTEM_ITEMS_B",
                     transform={"cases": [
                         {"when": {"op": "startsWith", "values": ["X"]},
                          "then": {"op": "prepend", "text": "LEGACY-"}}],
                         "else": "keep"})
        out, _ = apply(repo_clone, [col])
        sql = out["product.sql"]
        assert ("WHEN substring(items.SEGMENT2, 1, 1) = 'X' "
                "THEN concat('LEGACY-', items.SEGMENT2)") in sql
        assert sql.count("(") == sql.count(")")
        validate_structured(repo_clone)

    def test_new_named_rule_lands_in_the_registry(self, repo_clone):
        out, _ = apply(repo_clone,
                       [scalar(transform={"rule": "upperIt"})],
                       transforms={"upperIt": {"description": "uppercase it",
                                               "steps": [{"op": "upper"}]}})
        assert "upper(ag802.testAttr)" in out["product.sql"]
        mapping, _, _ = validate_structured(repo_clone)
        assert mapping["transforms"]["upperIt"]["steps"] == [{"op": "upper"}]
        # pre-existing registry entries survive the merge untouched
        assert "roundTo2dp" in mapping["transforms"]
        assert "pearsonImageUrl" in mapping["transforms"]

    def test_prose_transform_fails_the_compile(self, repo_clone):
        with pytest.raises(ValueError, match="prose transform"):
            apply(repo_clone, [scalar(transform="round to 2 decimal places")])

    def test_conditional_after_pick_transform_wraps_the_coalesce(self, repo_clone):
        """UIFPE-106: the column-level transform lands IN the SQL, wrapping
        the strict pick — the coverImage* hand shape."""
        cond = conditional(name="testCover", groups=("995", "996"),
                           columns=("C_EXT_ATTR3", "C_EXT_ATTR3"),
                           transform={"rule": "pearsonImageUrl"})
        out, log = apply(repo_clone, [cond])
        assert any(l.startswith("[AUTO] testCover: ")
                   and "transform after the pick" in l for l in log)
        sql = out["product.sql"]
        assert "substring(COALESCE(ag996.testCover, ag995.testCover), 1, 2)" in sql
        assert "images.pearson.com" in sql
        validate_structured(repo_clone)


# ── Reproduction contracts ───────────────────────────────────────────────────
# Found 2026-08-10 while testing whether the Transform UI's new-table flow can
# regenerate the product entity's own product.sql (see
# docs/review-transform-ui-sql-reproduction.md). The four failure classes were
# encoded here as xfail(strict=True) contracts and FIXED the same day — the
# tests now run as permanent hard gates:
#
#   GRAIN        an org-level EAV column compiles to (item, org) aggregation
#                and a two-key join, never a silent item-level collapse
#   DOTTED NAMES a struct member joins its struct (leaf alias, named_struct
#                entry, all-null gate) — never a top-level column or DDL entry
#   NESTED       array columns update the three structured artifacts and PLAN
#                the SQL
#   NEW TABLE    an unregistered entity is refused by name (scaffolder = D4)


def org_scalar(name, column, flt, **over):
    """An EAV scalar whose declared join carries BOTH entity keys — the
    (item, org) grain that ag755/ag822/ag800 use in the hand-written SQL."""
    col = scalar(name=name, column=column, flt=flt, **over)
    col["source"]["join"] = {"type": "left", "on": [
        {"leftTable": "MTL_SYSTEM_ITEMS_B", "leftColumn": "INVENTORY_ITEM_ID",
         "rightColumn": "INVENTORY_ITEM_ID"},
        {"leftTable": "MTL_SYSTEM_ITEMS_B", "leftColumn": "ORGANIZATION_ID",
         "rightColumn": "ORGANIZATION_ID"}]}
    return col


def nested_col(name="testList", group=1121):
    """A single-field value-array, modelled on webConsumingSystem."""
    return {
        "goldColumnName": name,
        "businessDescription": f"Test collection {name}",
        "kind": "nested",
        "joinKey": "INVENTORY_ITEM_ID → item (fans out to all orgs)",
        "filter": f"ATTR_GROUP_ID = {group}",
        "channels": {"event": False, "message": False, "api": True},
        "fields": [{"name": "value", "type": "string", "sortKey": True,
                    "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                               "column": "C_EXT_ATTR1"}}],
        "notes": None,
    }


class TestOrgLevelEavGrain:
    """GAP 1 (fixed 2026-08-10) — sql_add_eav used to hardcode the item-level
    shape, silently discarding a declared (item, org) grain: max() collapsed
    across ALL orgs for an item and that single value fanned out to every org
    row. Wrong values, no error. These gates keep the declared join's key set
    authoritative for the CTE shape, the join, and grain-mismatch refusal.
    """

    def test_extending_an_existing_org_level_group_keeps_the_org_key(self, repo_clone):
        """Regression guard: the extend path edits inside the CTE, so ag822's
        `GROUP BY 1, 2` survives untouched. This one passes today — it bounds
        the fix to the new-group path below."""
        out, log = apply(repo_clone, [org_scalar(
            "returnSubClass", "C_EXT_ATTR9", "ATTR_GROUP_ID = 822")])
        assert any("extended existing CTE ag822" in l for l in log)
        cte = re.search(r"(?ms)^ag822 AS \(.*?\n\),", out["product.sql"]).group(0)
        assert "max(C_EXT_ATTR9) AS returnSubClass" in cte
        assert "GROUP BY 1, 2" in cte

    def test_new_org_level_group_keeps_the_org_key(self, repo_clone):
        col = org_scalar("orgScopedAttr", "C_EXT_ATTR7", "ATTR_GROUP_ID = 998")
        # The grain IS in the intake — this assertion passes today.
        assert cid.derive_grain(col["source"]).startswith("org-level")

        out, log = apply(repo_clone, [col])
        assert any("created CTE ag998" in l for l in log)
        sql = out["product.sql"]

        cte = re.search(r"(?ms)^ag998 AS \(.*?\n\),", sql).group(0)
        assert "CAST(ORGANIZATION_ID AS BIGINT) AS oid" in cte, \
            "org-level CTE must project the org key"
        assert "GROUP BY 1, 2" in cte, \
            "org-level CTE must aggregate per (item, org), not per item"
        assert re.search(
            r"(?m)^LEFT JOIN ag998\s+ON CAST\(items\.INVENTORY_ITEM_ID AS BIGINT\)"
            r" = ag998\.iid\n\s+AND CAST\(items\.ORGANIZATION_ID AS BIGINT\)"
            r" = ag998\.oid", sql), "join must carry both entity keys"
        validate_structured(repo_clone)

    def test_grain_mismatch_with_existing_cte_is_refused(self, repo_clone):
        """An org-level column pointed at an ITEM-level attribute group (802)
        must refuse, not silently extend at the wrong grain — one attribute
        group cannot serve two grains (the ag1403/ag1403_org precedent)."""
        col = org_scalar("wrongGrain", "C_EXT_ATTR31", "ATTR_GROUP_ID = 802")
        with pytest.raises(ValueError, match="cannot serve two grains"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)


class TestStructMemberColumn:
    """GAP 2 (fixed 2026-08-10) — the compiler used to treat a dotted gold
    name as an ordinary column name: an unquoted dotted alias (invalid Spark
    identifier), a reference Spark reads as nested field access on a string,
    and a 51st manifest output_columns entry driving a bogus DDL column.

    Contract: a dotted name is a field OF the struct — bare LEAF alias in the
    CTE, a named_struct entry in the final SELECT, membership in the all-null
    gate, a `when:` row in the struct's lineage + its column_types STRUCT,
    and no new top-level column anywhere.
    """

    def test_dotted_name_becomes_a_struct_field(self, repo_clone):
        manifest_before = yaml.safe_load(
            (repo_clone / "domains/product/src/schemas/mapping/product/v1"
                          "/manifest.yaml").read_text(encoding="utf-8"))
        out, _ = apply(repo_clone, [org_scalar(
            "fulfillmentOrgDetails.testMember", "C_EXT_ATTR9",
            "ATTR_GROUP_ID = 822")])
        sql = out["product.sql"]

        assert not re.search(r"AS\s+fulfillmentOrgDetails\.", sql), \
            "a dotted alias is not a valid Spark identifier"
        cte = re.search(r"(?ms)^ag822 AS \(.*?\n\),", sql).group(0)
        assert "max(C_EXT_ATTR9) AS testMember" in cte, \
            "the member is aliased by its LEAF name inside the CTE"
        assert "'testMember', ag822.testMember" in sql, \
            "the member joins the fulfillmentOrgDetails named_struct"

        mapping, master, manifest = validate_structured(repo_clone)
        assert "fulfillmentOrgDetails.testMember" not in master["properties"], \
            "a struct member is not a top-level master property"
        assert master["properties"]["fulfillmentOrgDetails"]["properties"][
            "testMember"]["description"]
        assert len(manifest["output_columns"]) == len(
            manifest_before["output_columns"]), \
            "output_columns drives the DDL — a struct member adds no column"

    def test_conditional_dotted_member_emits_per_branch_rows(self, repo_clone):
        """FIVE of the six registered members are CONDITIONAL — the shape
        that crashed on 2026-08-10 (KeyError: 'source') because this path had
        only ever been tested with a synthetic scalar. Contract: SQL stays
        PLAN (struct assembly is hand-written by design, D5), the structured
        artifacts compile, and the struct's lineage gains one `when:` row PER
        BRANCH in the registered `member / condition` shape."""
        org_join = {"type": "left", "on": [
            {"leftTable": "MTL_SYSTEM_ITEMS_B",
             "leftColumn": "INVENTORY_ITEM_ID",
             "rightColumn": "INVENTORY_ITEM_ID"},
            {"leftTable": "MTL_SYSTEM_ITEMS_B",
             "leftColumn": "ORGANIZATION_ID",
             "rightColumn": "ORGANIZATION_ID"}]}
        col = {
            "goldColumnName": "fulfillmentOrgDetails.testCondMember",
            "businessDescription": "Test conditional struct member",
            "kind": "conditional", "dataType": "string",
            "discriminator": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                              "column": "ITEM_CATALOG_GROUP_ID"},
            "channels": {"event": False, "message": False, "api": True},
            "branches": [
                {"condition": "10002 (Digital Catalog)",
                 "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                            "column": "C_EXT_ATTR2",
                            "filter": "ATTR_GROUP_ID = 1414",
                            "join": org_join},
                 "fallback": "returnNull", "notes": None},
                {"condition": "ELSE",
                 "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B",
                            "column": "C_EXT_ATTR12",
                            "filter": "ATTR_GROUP_ID = 800",
                            "join": org_join},
                 "fallback": "returnNull", "notes": None},
            ],
            "notes": None,
        }
        paths = repo_clone / "domains/product/src/schemas"
        manifest_before = yaml.safe_load(
            (paths / "mapping/product/v1/manifest.yaml").read_text(encoding="utf-8"))
        sql_before = (paths / "mapping/product/v1/sql/product.sql").read_text(encoding="utf-8")

        out, log = apply(repo_clone, [col])
        assert any(l.startswith("[PLAN] fulfillmentOrgDetails.testCondMember")
                   for l in log)
        assert out["product.sql"] == sql_before, \
            "struct assembly of conditionals stays hand-written (D5)"

        mapping, master, manifest = validate_structured(repo_clone)
        rows = {r["when"]: r for r in
                manifest["canonical_lineage"]["fulfillmentOrgDetails"]["conditional"]}
        assert "testCondMember / 10002 (Digital Catalog)" in rows
        assert "testCondMember / ELSE" in rows
        assert rows["testCondMember / 10002 (Digital Catalog)"]["pdh_column"] == "C_EXT_ATTR2"
        assert rows["testCondMember / ELSE"]["pdh_filter"] == "ATTR_GROUP_ID = 800"
        # UIFPE-107: the mapping declares entityKey, so grain wording is
        # domain-agnostic — this branch joins both key columns.
        assert rows["testCondMember / ELSE"]["pdh_grain"] == \
            "full entity key (INVENTORY_ITEM_ID, ORGANIZATION_ID)"
        assert len(manifest["output_columns"]) == len(
            manifest_before["output_columns"])
        assert "testCondMember: STRING" in manifest["column_types"]["fulfillmentOrgDetails"]
        assert master["properties"]["fulfillmentOrgDetails"]["properties"][
            "testCondMember"]["description"]


class TestRealEntityCensus:
    """The review's methodology, made permanent: every column of the
    REGISTERED mapping must compile without crashing. Synthetic fixtures keep
    missing what the real entity exercises — the original four gaps hid
    behind an item-level scalar, and the struct-member regression (2026-08-10)
    hid behind a scalar member when five of six real members are conditional.

    The exact census also pins the D5 arithmetic: 49 AUTO / 13 PLAN.
    UIFPE-106 flipped 21 top-level EAV conditionals and 2 lookup-joins to
    AUTO (25+23=48); UIFPE-217 (ADR-0008 — the query is the contract) made
    derived columns AUTO on add, moving parentProduct across (49). The 13
    that stay PLAN are BY DESIGN — 6 nested arrays and 7 conditional struct
    members (named_struct + all-null gate are hand-written).
    Moving the numbers is a deliberate act — update them in the same PR."""

    def test_every_registered_column_compiles(self, repo_clone):
        real = json.loads(
            (REPO / "domains" / "product" / "src" / "schemas"
             / "mapping" / "product" / "v1"
             / "product.mapping.json").read_text(encoding="utf-8"))
        transforms = real.get("transforms") or {}
        census = {"AUTO": 0, "PLAN": 0}
        for col in real["columns"]:
            _, log = cid.compile_delta(
                repo_clone, intake_for([col], transforms=transforms),
                "add", None)
            joined = "\n".join(log)
            assert "[AUTO]" in joined or "[PLAN]" in joined, \
                f"{col['goldColumnName']}: no writer claimed it"
            census["AUTO" if "[AUTO]" in joined else "PLAN"] += 1
        # AUTO 49 -> 50 on 2026-08-31 (UIFPE-232, `categories`). It joins five
        # tables and derives its hierarchy from a bounded parent walk, which
        # would be PLAN on its shape alone — it compiles AUTO *because* it
        # carries a derivation block, which is precisely what ADR-0008 buys:
        # the query is the contract, so the compiler needs no human plan.
        # 2026-09-07 (UIFPE-269/270): +courseDurationValue/Unit (AUTO
        # conditionals), standardComponents/customComponents PLAN -> AUTO
        # (derived-sql, their blocks landed), +enablingTechnology and
        # fulfillmentOrgDetails.marketingInitiative (plan:nested), +url
        # (plan:conditional — its branches read the TL table, not EXT_B).
        # 2026-09-15: +itemCatalogGroupId (driving scalar), +sourceItemNumber
        # (AUTO conditional) and fulfillmentOrgDetails.readyForManufactureCode
        # (org-level struct member, AUTO since the compiler renders members).
        # 60 since UIFPE-476: instructorResourceFiles carries a derivation
        # block, so it compiles AUTO like categories and the BOM arrays.
        assert census == {"AUTO": 60, "PLAN": 14}


class TestNestedColumnArtifacts:
    """GAP 3 (fixed 2026-08-10) — master_prop() used to raise
    KeyError('dataType') on nested columns (the schema's nestedColumn has no
    dataType; element types live on the fields), so all 4 array columns died
    before any artifact was written.

    Contract: a nested column is PLAN for the SQL (arrays touch the
    hand-tuned collections section) but AUTO for mapping/master/manifest —
    including array_sort_keys and column_types (ADR-0003).
    """

    def test_nested_column_updates_the_structured_artifacts(self, repo_clone):
        sql_before = (repo_clone / "domains/product/src/schemas/mapping/product"
                      "/v1/sql/product.sql").read_text(encoding="utf-8")
        out, log = apply(repo_clone, [nested_col()])

        assert any(l.startswith("[PLAN] testList") for l in log)
        assert "joinKey" in "\n".join(log), "the plan states the array's grain"
        assert out["product.sql"] == sql_before, "arrays are hand-written"

        mapping, master, manifest = validate_structured(repo_clone)
        prop = master["properties"]["testList"]
        assert "array" in prop["type"]
        assert "items" in prop, "element encoding follows the parity test's " \
            "documented value-array allowance"
        assert prop["x-channels"] == ["api"]
        assert manifest["output_columns"][-1] == "testList"
        assert manifest["canonical_lineage"]["testList"]["pdh_column"] == \
            "C_EXT_ATTR1"
        assert any(c["goldColumnName"] == "testList" for c in mapping["columns"])


class TestNewTableIntake:
    """GAP 4 (fixed 2026-08-10, refusal half) — compile_delta used to die on
    FileNotFoundError inside a dict comprehension when the wizard's new-table
    flow produced an unregistered entity.

    Contract: either scaffold the four artifacts, or refuse with an error that
    names the entity and says what is missing. Refusal is what ships today —
    the real scaffolder is decision D4 in the review doc.
    """

    def test_new_table_scaffolds_or_refuses_cleanly(self, repo_clone):
        cols = [scalar(name="firstAttr", column="SEGMENT1", flt=None,
                       table="MTL_SYSTEM_ITEMS_B")]
        try:
            edits, log = cid.compile_delta(
                repo_clone, intake_for(cols, table="gold_product.productUi"),
                "add", None)
        except (ValueError, NotImplementedError) as exc:
            # Refusing is acceptable — but it must say WHAT and WHICH entity.
            assert "productUi" in str(exc)
            return

        names = {p.name for p in edits}
        assert names == {"product.mapping.json", "master.json",
                         "manifest.yaml", "productUi.sql"}, \
            f"a new entity needs all four artifacts, got {sorted(names)}"
        assert any("firstAttr" in l for l in log)


# ── Gold table identity (UIFPE-107) ─────────────────────────────────────────

ENTITY_KEY = [{"goldColumn": "inventoryItemId", "drivingColumn": "INVENTORY_ITEM_ID"},
              {"goldColumn": "organizationId", "drivingColumn": "ORGANIZATION_ID"}]


class TestEntityKeyGrain:
    """derive_grain speaks the declared entityKey's language — domain-agnostic
    full/partial key wording; the product item/org labels remain the fallback
    for intakes that predate the declaration."""

    def test_full_and_partial_key_wording(self):
        org = org_scalar("x", "C_EXT_ATTR1", "ATTR_GROUP_ID = 1")["source"]
        item = scalar()["source"]
        assert cid.derive_grain(org, ENTITY_KEY) == \
            "full entity key (INVENTORY_ITEM_ID, ORGANIZATION_ID)"
        assert cid.derive_grain(item, ENTITY_KEY).startswith(
            "partial entity key (INVENTORY_ITEM_ID)")
        assert "fans out" in cid.derive_grain(item, ENTITY_KEY)

    def test_legacy_fallback_without_entity_key(self):
        assert cid.derive_grain(scalar()["source"]) == "item-level"
        assert cid.derive_grain(
            org_scalar("x", "C", "ATTR_GROUP_ID = 1")["source"]).startswith("org-level")

    def test_intake_entity_key_reaches_the_manifest(self, repo_clone):
        out, _ = apply(repo_clone, [scalar(name="ekAttr")],
                       entity_key=ENTITY_KEY)
        assert "partial entity key (INVENTORY_ITEM_ID)" in out["manifest.yaml"]


class TestScheduleFrequency:
    """UIFPE-107: which dispatcher cadence a table subscribes to is
    config-as-code — the envelope's jobProperties.frequency compiles into
    pipelines/base/<domain>.yaml. Clock times are EventBridge infra.

    UIFPE-149 widened the vocabulary from two cadences to eleven and renamed
    both originals (gold -> gold-24h, gold-hourly -> gold-1h)."""

    def test_frequency_change_edits_the_pipeline_yaml(self, repo_clone):
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-1h"})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert re.search(r"(?m)^  frequency: gold-1h", ptext)
        assert any("schedule: frequency -> gold-1h" in l for l in log)

    def test_a_sub_hourly_cadence_compiles(self, repo_clone):
        """The whole point of UIFPE-149: the rungs below an hour are writable
        from the wizard, not just theoretically valid."""
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-15m"})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert re.search(r"(?m)^  frequency: gold-15m", ptext)

    def test_unchanged_frequency_is_a_noop(self, repo_clone):
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-24h"})
        assert not any(p.name == "product.yaml" for p in edits)
        assert any("already gold-24h" in l for l in log)

    def test_invalid_frequency_is_refused(self, repo_clone):
        with pytest.raises(ValueError, match="dispatcher"):
            cid.compile_delta(repo_clone, intake_for([scalar()]), "add", None,
                              job_props={"frequency": "every-5-minutes"})

    def test_start_time_is_compiled_into_the_pipeline_yaml(self, repo_clone):
        """UIFPE-149: the wizard sets WHEN as well as how often, with no manual
        step — so the compiler has to write both halves."""
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-4h", "start_time": "14:45"})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert re.search(r'(?m)^  frequency: gold-4h', ptext)
        assert re.search(r'(?m)^  start_time: "14:45"', ptext)
        assert any("schedule: start_time -> 14:45" in l for l in log)

    def test_start_time_is_inserted_when_the_yaml_predates_the_field(self, repo_clone):
        """An older pipeline YAML has no start_time key at all. It must be
        inserted next to `frequency:` rather than appended below the block, where
        a reviewer would miss it — and it must not corrupt the file."""
        ppath = (repo_clone / "domains" / "product" / "src" / "apps"
                 / "pipelines" / "base" / "product.yaml")
        stripped = re.sub(r'(?m)^  start_time: .*\n', "", ppath.read_text())
        assert not re.search(r'(?m)^  start_time:', stripped)
        ppath.write_text(stripped, encoding="utf-8", newline="")
        edits, _log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"start_time": "09:15"})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert re.search(r'(?m)^  frequency: \S+\n  start_time: "09:15"', ptext)
        # and the rest of the block survived
        assert "failure_cooldown_minutes" in ptext
        assert "reconcile:" in ptext

    def test_unchanged_start_time_is_a_noop(self, repo_clone):
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"start_time": "06:30"})
        assert not any(p.name == "product.yaml" for p in edits)
        assert any("start_time already 06:30" in l for l in log)

    def test_an_invalid_start_time_is_refused(self, repo_clone):
        for bad in ("25:00", "half six", "0915"):
            with pytest.raises(ValueError, match="time of day"):
                cid.compile_delta(repo_clone, intake_for([scalar()]), "add",
                                  None, job_props={"start_time": bad})

    # ── gold-weekly (UIFPE-532): the day set rides the envelope too ──

    def test_weekly_days_compile_beside_the_time(self, repo_clone):
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-weekly", "start_time": "03:00",
                       "weekly_days": ["thu", "TUE"]})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert re.search(r'(?m)^  frequency: gold-weekly$', ptext)
        # the day set sits directly beneath the time it belongs to
        assert re.search(r'(?m)^  start_time: "03:00"\n  weekly_days: \[TUE, THU\]$', ptext)
        assert any("weekly_days -> " in l for l in log)
        # the YAML still parses, and the validator would accept the block
        import yaml
        sched = yaml.safe_load(ptext)["schedule"]
        assert sched["weekly_days"] == ["TUE", "THU"]
        assert sched["start_time"] == "03:00"

    def test_weekly_days_replace_an_existing_set_and_are_a_noop_when_unchanged(self, repo_clone):
        edits, _ = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-weekly", "weekly_days": ["MON"]})
        ppath, ptext = next((p, t) for p, t in edits.items() if p.name == "product.yaml")
        ppath.write_text(ptext, encoding="utf-8", newline="")
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"weekly_days": ["SAT", "SUN"]})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert ptext.count("weekly_days:") == 1
        assert re.search(r"(?m)^  weekly_days: \[SAT, SUN\]$", ptext)
        ppath.write_text(ptext, encoding="utf-8", newline="")
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"weekly_days": ["SUN", "SAT"]})
        assert not any(p.name == "product.yaml" for p in edits)
        assert any("weekly_days already" in l for l in log)

    def test_weekly_days_are_refused_on_a_non_weekly_cadence(self, repo_clone):
        """gold_config.validate would refuse the sync; refuse at compile time,
        while a human is watching the review."""
        with pytest.raises(ValueError, match="only apply to gold-weekly"):
            cid.compile_delta(repo_clone, intake_for([scalar()]), "add", None,
                              job_props={"weekly_days": ["TUE"]})    # product is gold-24h
        with pytest.raises(ValueError, match="only apply to gold-weekly"):
            cid.compile_delta(repo_clone, intake_for([scalar()]), "add", None,
                              job_props={"frequency": "gold-4h", "weekly_days": ["TUE"]})

    def test_bad_weekly_days_are_refused(self, repo_clone):
        for bad in ([], ["FUNDAY"], "TUE", ["TUE", 3]):
            with pytest.raises(ValueError, match="weekdays"):
                cid.compile_delta(repo_clone, intake_for([scalar()]), "add", None,
                                  job_props={"frequency": "gold-weekly",
                                             "weekly_days": bad})

    def test_leaving_the_weekly_rung_drops_the_day_set(self, repo_clone):
        edits, _ = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-weekly", "weekly_days": ["TUE", "THU"]})
        ppath, ptext = next((p, t) for p, t in edits.items() if p.name == "product.yaml")
        ppath.write_text(ptext, encoding="utf-8", newline="")
        edits, log = cid.compile_delta(
            repo_clone, intake_for([scalar()]), "add", None,
            job_props={"frequency": "gold-24h"})
        ptext = next(t for p, t in edits.items() if p.name == "product.yaml")
        assert "weekly_days" not in ptext
        assert re.search(r"(?m)^  frequency: gold-24h$", ptext)
        assert "failure_cooldown_minutes" in ptext
        assert any("weekly_days removed" in l for l in log)

    def test_legacy_alias_is_refused_by_the_compiler(self, repo_clone):
        """The dispatcher and the validator both ACCEPT `gold` for configs
        already deployed, but nothing should author it into a YAML again — that
        would keep the retired vocabulary alive in the repo indefinitely."""
        with pytest.raises(ValueError, match="dispatcher"):
            cid.compile_delta(repo_clone, intake_for([scalar()]), "add", None,
                              job_props={"frequency": "gold"})


class TestKindChangeOnModify:
    """UIFPE-108 companion: the wizard can now convert a column's kind, so a
    modify may arrive with a DIFFERENT shape than the one that shipped.
    master_upsert merges by default (preserving hand-added keys) — but across
    a kind change the old shape's keys are wrong, so it must replace."""

    def test_array_to_scalar_drops_the_stale_items_key(self, repo_clone):
        # webConsumingSystem ships as an ARRAY; re-declare it as a scalar
        sql_before = (repo_clone / "domains/product/src/schemas/mapping/product"
                      "/v1/sql/product.sql").read_text(encoding="utf-8")
        col = scalar(name="webConsumingSystem", column="C_EXT_ATTR1",
                     flt="ATTR_GROUP_ID = 1121")
        col["businessDescription"] = "Web consuming system (single value)"
        out, log = apply(repo_clone, [col], intent="modify")
        # A kind change rewrites the SQL's SHAPE — that is a hand edit, not a
        # text patch, so the SQL is PLANned and left untouched.
        assert any("CHANGES SHAPE: nested -> scalar" in l for l in log)
        assert out["product.sql"] == sql_before
        _, master, _ = validate_structured(repo_clone)
        prop = master["properties"]["webConsumingSystem"]
        assert "items" not in prop, "the array's items must not survive"
        assert "array" not in prop["type"]
        assert prop["type"] == ["string", "null"]

    def test_scalar_to_array_replaces_the_scalar_shape(self, repo_clone):
        col = nested_col(name="itemType", group=1121)
        out, log = apply(repo_clone, [col], intent="modify")
        assert any("CHANGES SHAPE: scalar -> nested" in l for l in log)
        assert any("collect_list" in l for l in log)
        _, master, _ = validate_structured(repo_clone)
        prop = master["properties"]["itemType"]
        assert "array" in prop["type"] and "items" in prop

    def test_same_kind_modify_still_MERGES(self, repo_clone):
        """A same-kind modify must keep hand-added keys (the merge behaviour
        UIFPE-100 relies on) — the replace path is kind-change only."""
        path = (repo_clone / "domains/product/src/schemas/gold/product/v1"
                / "master.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["properties"]["lineOfBusiness"]["x-hand-tuned"] = "keep me"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8", newline="")
        col = scalar(name="lineOfBusiness", column="C_EXT_ATTR41",
                     flt="ATTR_GROUP_ID = 802")
        col["businessDescription"] = "Line of Business"
        apply(repo_clone, [col], intent="modify")
        _, master, _ = validate_structured(repo_clone)
        assert master["properties"]["lineOfBusiness"]["x-hand-tuned"] == "keep me"


# ── UIFPE-260: the new-entity scaffolder ─────────────────────────────────────

def stock_scalar(name, column, transform=None, nullable=True, channels=None):
    return {
        "goldColumnName": name,
        "businessDescription": f"Stock attribute {name}",
        "kind": "scalar", "dataType": "string",
        "source": {"table": "XXPSO_INV_STOCK_ATP_STG", "column": column,
                   "filter": None},
        "transform": transform,
        "channels": channels or {"event": True, "message": True, "api": True},
        "nullable": nullable, "notes": None,
    }


def scaffold_intake(**over):
    """The stock shape (UIFPE-261): composite key, driving filter, grain
    collapsed by aggregates, one api-only column."""
    intake = {
        "domain": "stock", "table": "gold_stock.stock",
        "drivingTable": "XXPSO_INV_STOCK_ATP_STG",
        "drivingSilverTable":
            "uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg",
        "drivingFilter": "PPID IS NOT NULL",
        "entityKey": [
            {"goldColumn": "ppid", "drivingColumn": "PPID"},
            {"goldColumn": "organizationCode",
             "drivingColumn": "ORGANIZATION_CODE"},
        ],
        "transforms": {
            "isbn13Pick": {"sqlExpr":
                "MAX(CASE WHEN length({x}) = 13 AND {x} LIKE '9%' "
                "THEN {x} END)"},
            "sumRounded": {"sqlExpr": "CAST(ROUND(SUM({x})) AS BIGINT)"},
            "maxOf": {"sqlExpr": "MAX({x})"},
        },
        "batch": {"requestedBy": "qe", "targetDate": None,
                  "backfillMode": "silent", "newChannel": None},
        "columns": [
            stock_scalar("ppid", "PPID", nullable=False),
            stock_scalar("isbn13", "ISBN", transform={"rule": "isbn13Pick"}),
            stock_scalar("organizationCode", "ORGANIZATION_CODE",
                         nullable=False),
            dict(stock_scalar("qtyOnHand", "ONHAND_QUANTITY",
                              transform={"rule": "sumRounded"},
                              nullable=False), dataType="integer"),
            dict(stock_scalar("atpDate", "ATP_DATE",
                              transform={"rule": "maxOf"}),
                 dataType="timestamp"),
            stock_scalar("atpFlag", "ATP_FLAG", transform={"rule": "maxOf"},
                         channels={"event": False, "message": False,
                                   "api": True}),
        ],
    }
    intake.update(over)
    return intake


class TestScaffolder:
    def compile(self, tmp_path, intake=None, intent="add",
                job_props={"frequency": "gold-24h", "start_time": "08:15"}):
        return cid.compile_delta(tmp_path, intake or scaffold_intake(),
                                 intent, None, job_props)

    def test_generates_all_six_files(self, tmp_path):
        edits, log = self.compile(tmp_path)
        names = sorted(str(p).replace("\\", "/").split("src/")[-1]
                       for p in edits)
        assert names == [
            "apps/pipelines/base/stock.yaml",
            "apps/pipelines/overlays/dev/stock.yaml",
            "schemas/gold/stock/v1/master.json",
            "schemas/mapping/stock/v1/manifest.yaml",
            "schemas/mapping/stock/v1/sql/stock.sql",
            "schemas/mapping/stock/v1/stock.mapping.json",
        ]
        assert any(l.startswith("[SCAFFOLD] new entity stock.stock")
                   for l in log)

    def _text(self, edits, suffix):
        return next(t for p, t in edits.items()
                    if str(p).replace("\\", "/").endswith(suffix))

    def test_sql_groups_by_the_entity_key_and_applies_the_filter(
            self, tmp_path):
        edits, _ = self.compile(tmp_path)
        sql = self._text(edits, "sql/stock.sql")
        assert "WHERE PPID IS NOT NULL" in sql
        assert "GROUP BY PPID, ORGANIZATION_CODE" in sql
        assert "CAST(ROUND(SUM(ONHAND_QUANTITY)) AS BIGINT)" in sql
        assert "MAX(CASE WHEN length(ISBN) = 13 AND ISBN LIKE '9%'" in sql
        assert "FROM stock" in sql
        # key columns stay bare — never aggregated
        assert "MAX(PPID)" not in sql

    def test_master_channels_key_and_required(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        master = json.loads(self._text(edits, "master.json"))
        assert master["metadata"]["lifecycle"] == "draft"
        assert [p["goldColumn"] for p in master["metadata"]["primaryKey"]] \
            == ["ppid", "organizationCode"]
        # N12 (UIFPE-518): EVERY element is required, nullable ones included.
        # This used to be ["ppid", "organizationCode", "qtyOnHand"] because the
        # compiler derived `required` from `nullable`; the three named below
        # are the nullable ones it silently left optional.
        assert master["required"] == list(master["properties"])
        for previously_omitted in ("isbn13", "atpDate", "atpFlag"):
            assert previously_omitted in master["required"]
        assert master["properties"]["atpFlag"]["x-channels"] == ["api"]

    def test_a_nullable_column_is_required_and_keeps_its_null_type(
            self, tmp_path):
        """N12, the half that is easy to get backwards: a nullable column is
        still REQUIRED - the key is always sent - and the null lives in the
        TYPE union, not in the key's absence.

        The compiler used to derive `required` from `nullable`, so a nullable
        column was left optional. A producer could then omit the key, and a
        consumer had no way to tell a value that was CLEARED from one that was
        never sent - so it kept the stale value indefinitely. Same failure as a
        gold merge that COALESCEs source over target and never clears.
        """
        edits, _ = self.compile(tmp_path)
        master = json.loads(self._text(edits, "master.json"))

        def types(p):
            t = p["type"]
            return t if isinstance(t, list) else [t]

        nullable = [n for n, p in master["properties"].items()
                    if "null" in types(p)]
        assert nullable, ("the fixture has no nullable column, so this would "
                          "pass without proving anything")
        for name in nullable:
            assert name in master["required"], (
                f"{name} is nullable and must STILL be required - `required` "
                f"constrains the key, not the value")

    def test_manifest_carries_sot_filter_key_mapping_and_types(
            self, tmp_path):
        edits, _ = self.compile(tmp_path)
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        src = man["sources"][0]
        assert src["alias"] == "stock"
        assert src["silver_table"] \
            == "uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg"
        assert src["sot_filter"] == "PPID IS NOT NULL"
        assert src["key_mapping"] == {"ppid": "PPID",
                                      "organizationCode": "ORGANIZATION_CODE"}
        assert man["target"]["database"] == "uif_echo_{env}_gold_stock"
        assert man["column_types"] == {"atpDate": "TIMESTAMP"}
        assert man["output_columns"][-1] == "atpFlag"

    def test_pipeline_and_overlay_shapes(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        base = yaml.safe_load(self._text(edits, "base/stock.yaml"))
        assert base["gold_id"] == "stock" and base["domain"] == "stock"
        assert base["schedule"] == {"frequency": "gold-24h",
                                    "start_time": "08:15",
                                    "failure_cooldown_minutes": 30}
        assert base["reconcile"]["enabled"] is False
        assert base["snapshot_gate"] is True
        overlay = yaml.safe_load(self._text(edits, "overlays/dev/stock.yaml"))
        assert overlay["enabled"] is True
        assert overlay["target"]["s3_path"] == "s3://uif-echo-dev-gold/stock/"

    def test_mapping_json_round_trips_the_intake(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        mapping = json.loads(self._text(edits, "stock.mapping.json"))
        assert mapping["drivingFilter"] == "PPID IS NOT NULL"
        assert mapping["drivingSilverTable"].startswith("uif_echo_{env}_")
        assert len(mapping["columns"]) == 6

    # ── refusals: every doubtful shape names the entity ──────────────────

    def test_refuses_modify_intent(self, tmp_path):
        with pytest.raises(ValueError, match="stock.stock"):
            self.compile(tmp_path, intent="modify")

    def test_refuses_missing_identity(self, tmp_path):
        for drop in ("entityKey", "drivingSilverTable", "drivingTable"):
            intake = scaffold_intake()
            intake.pop(drop)
            with pytest.raises(ValueError, match=drop):
                self.compile(tmp_path, intake)

    def test_refuses_nullable_key_column(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][0]["nullable"] = True
        with pytest.raises(ValueError, match="nullable"):
            self.compile(tmp_path, intake)

    def test_refuses_mixed_bare_and_aggregate_columns(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][1]["transform"] = None    # isbn13 bare
        with pytest.raises(ValueError, match="isbn13"):
            self.compile(tmp_path, intake)

    def test_refuses_aggregated_key_column(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][0]["transform"] = {"rule": "maxOf"}
        with pytest.raises(ValueError, match="aggregate"):
            self.compile(tmp_path, intake)

    def test_refuses_foreign_source_table(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][1]["source"]["table"] = "SOME_OTHER_TABLE"
        with pytest.raises(ValueError, match="driving table"):
            self.compile(tmp_path, intake)

    def test_refuses_live_entity_master(self, tmp_path):
        base = tmp_path / "domains" / "stock" / "src" / "schemas" \
            / "gold" / "stock" / "v1"
        base.mkdir(parents=True)
        (base / "master.json").write_text(json.dumps(
            {"metadata": {"lifecycle": "active"}}), encoding="utf-8")
        with pytest.raises(ValueError, match="draft"):
            self.compile(tmp_path)

    def test_regenerates_draft_master(self, tmp_path):
        base = tmp_path / "domains" / "stock" / "src" / "schemas" \
            / "gold" / "stock" / "v1"
        base.mkdir(parents=True)
        (base / "master.json").write_text(json.dumps(
            {"metadata": {"lifecycle": "draft"}}), encoding="utf-8")
        edits, log = self.compile(tmp_path)
        master = json.loads(self._text(edits, "master.json"))
        assert master["properties"]  # regenerated wholesale
        assert any("draft regenerated" in l for l in log)

    def test_refuses_registered_pipeline(self, tmp_path):
        p = tmp_path / "domains" / "stock" / "src" / "apps" / "pipelines" \
            / "base"
        p.mkdir(parents=True)
        (p / "stock.yaml").write_text("gold_id: stock\n", encoding="utf-8")
        with pytest.raises(ValueError, match="live"):
            self.compile(tmp_path)

    def test_refuses_bad_frequency(self, tmp_path):
        with pytest.raises(ValueError, match="cadence"):
            self.compile(tmp_path,
                         job_props={"frequency": "hourly-ish",
                                    "start_time": "08:15"})

    def test_no_aggregates_means_plain_grain(self, tmp_path):
        intake = scaffold_intake()
        for c in intake["columns"]:
            c["transform"] = None
        edits, log = self.compile(tmp_path, intake)
        sql = self._text(edits, "sql/stock.sql")
        assert "GROUP BY" not in sql
        assert any("one row per driving row" in l for l in log)


class TestDrivingGuards:
    """UIFPE-260 round findings on the EXISTING add path."""

    def test_new_driving_filter_refuses_instead_of_silently_dropping(
            self, repo_clone):
        col = scalar(name="brandNew", column="ATTRIBUTE1",
                     table="MTL_SYSTEM_ITEMS_B",
                     flt="ATTRIBUTE1 = 'NEVER_IN_THE_SQL'")
        with pytest.raises(ValueError,
                           match="not applied by the entity"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_aggregate_transform_refuses_on_registered_entity(
            self, repo_clone):
        col = scalar(name="aggAttr", column="ATTRIBUTE2",
                     table="MTL_SYSTEM_ITEMS_B", flt=None,
                     transform={"rule": "badAgg"})
        with pytest.raises(ValueError, match="aggregate"):
            cid.compile_delta(
                repo_clone,
                intake_for([col], transforms={"badAgg":
                                              {"sqlExpr": "SUM({x})"}}),
                "add", None)

    def test_driving_filter_on_registered_entity_refuses(self, repo_clone):
        intake = intake_for([scalar(table="MTL_SYSTEM_ITEMS_B", flt=None)])
        intake["drivingFilter"] = "SEGMENT1 LIKE 'A%'"
        with pytest.raises(ValueError, match="already registered"):
            cid.compile_delta(repo_clone, intake, "add", None)


class TestScaffolderRoundFixes:
    """Pins for the adversarial-round findings (UIFPE-260)."""

    def compile(self, tmp_path, intake=None, intent="add",
                job_props={"frequency": "gold-24h", "start_time": "08:15"}):
        return cid.compile_delta(tmp_path, intake or scaffold_intake(),
                                 intent, None, job_props)

    def test_apply_creates_new_directories_end_to_end(self, tmp_path):
        # C1: the CLI write path was untested and crashed on the scaffolder's
        # own directories
        import subprocess, sys
        env_path = tmp_path / "envelope.json"
        env_path.write_text(json.dumps({
            "task": "t", "intent": "add",
            "jobProperties": {"frequency": "gold-24h", "start_time": "08:15"},
            "intake": scaffold_intake()}), encoding="utf-8")
        script = REPO / "stacks" / "echo" / "src" / "apps" / "cli" \
            / "compile_intake_delta.py"
        r = subprocess.run([sys.executable, str(script),
                            "--intake", str(env_path),
                            "--repo-root", str(tmp_path), "--apply"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "domains" / "stock" / "src" / "schemas"
                / "mapping" / "stock" / "v1" / "sql" / "stock.sql").exists()
        assert (tmp_path / "domains" / "stock" / "src" / "apps" / "pipelines"
                / "overlays" / "dev" / "stock.yaml").exists()

    def test_manifest_has_source_system_and_survives_quoted_filter(
            self, tmp_path):
        intake = scaffold_intake(
            drivingFilter='ORGANIZATION_CODE = "01E" AND PPID IS NOT NULL')
        edits, _ = self.compile(tmp_path, intake)
        text = next(t for p, t in edits.items()
                    if str(p).endswith("manifest.yaml"))
        man = yaml.safe_load(text)  # a quoted filter must not break the YAML
        assert man["source_system"]["name"].startswith("Silver (")
        assert man["sources"][0]["sot_filter"] \
            == 'ORGANIZATION_CODE = "01E" AND PPID IS NOT NULL'

    def test_pipeline_owner_is_yaml_safe(self, tmp_path):
        intake = scaffold_intake()
        intake["batch"]["requestedBy"] = \
            "mallory\nglue:\n  num_workers: 99"
        edits, _ = self.compile(tmp_path, intake)
        base = yaml.safe_load(next(t for p, t in edits.items()
                                   if str(p).endswith("base\\stock.yaml")
                                   or str(p).endswith("base/stock.yaml")))
        assert base["glue"]["num_workers"] == 4          # not injected
        assert "mallory" in base["metadata"]["owner"]

    def test_bad_start_time_refuses(self, tmp_path):
        with pytest.raises(ValueError, match="HH:MM"):
            self.compile(tmp_path, job_props={"frequency": "gold-24h",
                                              "start_time": "25:99"})

    def test_weekly_scaffold_states_its_day_set(self, tmp_path):
        """UIFPE-532: a new weekly table's YAML carries weekly_days beside the
        time; the default is Monday; a day set on another cadence refuses."""
        edits, _ = self.compile(tmp_path, job_props={
            "frequency": "gold-weekly", "start_time": "03:00",
            "weekly_days": ["thu", "TUE"]})
        base = yaml.safe_load(next(t for p, t in edits.items()
                                   if str(p).replace("\\", "/").endswith("base/stock.yaml")))
        assert base["schedule"]["frequency"] == "gold-weekly"
        assert base["schedule"]["weekly_days"] == ["TUE", "THU"]
        assert base["schedule"]["start_time"] == "03:00"
        edits, _ = self.compile(tmp_path, job_props={
            "frequency": "gold-weekly", "start_time": "03:00"})
        base = yaml.safe_load(next(t for p, t in edits.items()
                                   if str(p).replace("\\", "/").endswith("base/stock.yaml")))
        assert base["schedule"]["weekly_days"] == ["MON"]
        edits, _ = self.compile(tmp_path, job_props={
            "frequency": "gold-24h", "start_time": "08:15"})
        text = next(t for p, t in edits.items()
                    if str(p).replace("\\", "/").endswith("base/stock.yaml"))
        assert "weekly_days" not in text
        with pytest.raises(ValueError, match="only gold-weekly"):
            self.compile(tmp_path, job_props={"frequency": "gold-24h",
                                              "start_time": "08:15",
                                              "weekly_days": ["TUE"]})
        with pytest.raises(ValueError, match="weekdays"):
            self.compile(tmp_path, job_props={"frequency": "gold-weekly",
                                              "start_time": "03:00",
                                              "weekly_days": []})

    def test_key_pair_source_mismatch_refuses(self, tmp_path):
        intake = scaffold_intake()
        intake["entityKey"][0]["drivingColumn"] = "TOTALLY_DIFFERENT"
        with pytest.raises(ValueError, match="must agree"):
            self.compile(tmp_path, intake)

    def test_column_hygiene_refusals(self, tmp_path):
        dup = scaffold_intake()
        dup["columns"].append(dict(dup["columns"][1],
                                   goldColumnName="ISBN13"))
        with pytest.raises(ValueError, match="collides"):
            self.compile(tmp_path, dup)
        reserved = scaffold_intake()
        reserved["columns"][1]["goldColumnName"] = "_gold_updated_at"
        with pytest.raises(ValueError, match="identifier"):
            self.compile(tmp_path, reserved)
        dupkey = scaffold_intake()
        dupkey["entityKey"].append(dict(dupkey["entityKey"][0]))
        with pytest.raises(ValueError, match="once"):
            self.compile(tmp_path, dupkey)

    def test_key_column_filter_refuses_too(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][0]["source"]["filter"] = "STATUS = 'ACTIVE'"
        with pytest.raises(ValueError, match="per-column"):
            self.compile(tmp_path, intake)

    def test_master_format_owner_and_contact(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        master = json.loads(next(t for p, t in edits.items()
                                 if str(p).endswith("master.json")))
        assert master["properties"]["atpDate"]["format"] == "date-time"
        assert master["metadata"]["owner"]["service"] \
            == "urn:pearson:service:stock:stock-svc"
        assert master["metadata"]["owner"]["contact"] == "qe"

    def test_cli_identifier_fence(self, tmp_path):
        bad = scaffold_intake(domain="../../outside")
        with pytest.raises(ValueError, match="identifier"):
            self.compile(tmp_path, bad)
        bad2 = scaffold_intake(table="gold_stock.C:/evil")
        with pytest.raises(ValueError, match="identifier"):
            self.compile(tmp_path, bad2)


class TestIsAggregate:
    def test_true_aggregates(self):
        assert cid.is_aggregate("SUM(X)")
        assert cid.is_aggregate("first(X)")
        assert cid.is_aggregate("COLLECT_LIST(x)")
        assert cid.is_aggregate("CAST(ROUND(SUM(Q)) AS BIGINT)")

    def test_window_and_literals_do_not_count(self):
        assert not cid.is_aggregate("SUM(X) OVER (PARTITION BY K)")
        assert not cid.is_aggregate("COALESCE(ISBN, 'max(unknown)')")
        assert not cid.is_aggregate("summary(x)")
        assert not cid.is_aggregate("MAX_QTY + 1")


class TestDrivingFilterRegion:
    def test_eav_cte_where_no_longer_false_accepts(self, repo_clone):
        # round finding 7: 'ATTR_GROUP_ID = 802' matches an EAV CTE's WHERE,
        # which never restricts driving rows — must refuse now
        col = scalar(name="brandNew", column="ATTRIBUTE1",
                     table="MTL_SYSTEM_ITEMS_B",
                     flt="ATTR_GROUP_ID = 802")
        with pytest.raises(ValueError, match="not applied"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_real_driving_domain_still_accepted(self, repo_clone):
        col = scalar(name="stillFine", column="ATTRIBUTE1",
                     table="MTL_SYSTEM_ITEMS_B",
                     flt="items.SEGMENT1 LIKE 'A%'")
        edits, log = cid.compile_delta(repo_clone, intake_for([col]),
                                       "add", None)
        assert any("driving-table expression added" in l for l in log)


class TestScaffolderSecondPass:
    """Residual gaps from the re-verification (UIFPE-260)."""

    def compile(self, tmp_path, intake=None,
                job_props={"frequency": "gold-24h", "start_time": "08:15"}):
        return cid.compile_delta(tmp_path, intake or scaffold_intake(),
                                 "add", None, job_props)

    def test_window_expressions_refuse(self, tmp_path):
        intake = scaffold_intake()
        intake["transforms"]["winMix"] = {
            "sqlExpr": "SUM({x}) + MAX({x}) OVER ()"}
        intake["columns"][3]["transform"] = {"rule": "winMix"}
        with pytest.raises(ValueError, match="window"):
            self.compile(tmp_path, intake)

    def test_reserved_word_column_refuses(self, tmp_path):
        intake = scaffold_intake()
        intake["columns"][1]["goldColumnName"] = "select"
        with pytest.raises(ValueError, match="identifier"):
            self.compile(tmp_path, intake)

    def test_metachar_driving_names_refuse(self, tmp_path):
        bad = scaffold_intake(drivingTable='T1"]\n evil')
        with pytest.raises(ValueError, match="drivingTable"):
            self.compile(tmp_path, bad)
        bad2 = scaffold_intake(
            drivingSilverTable="echo_{env}_x.bad\nname")
        with pytest.raises(ValueError, match="drivingSilverTable"):
            self.compile(tmp_path, bad2)

    def test_near_miss_filter_refuses_on_add_path(self, repo_clone):
        # 'ORGANIZATION_ID = 224' must not match inside '= 2240'
        col = scalar(name="nearMiss", column="ATTRIBUTE1",
                     table="MTL_SYSTEM_ITEMS_B",
                     flt="SEGMENT1 LIKE 'A%' AND 1 = 10")
        with pytest.raises(ValueError, match="not applied"):
            cid.compile_delta(repo_clone, intake_for([col]), "add", None)

    def test_master_byte_parity_shape(self, tmp_path):
        # the compiler side of the UI parity contract: ensure_ascii off,
        # trailing newline, description/format key order on timestamps
        edits, _ = self.compile(tmp_path)
        text = next(t for p, t in edits.items()
                    if str(p).endswith("master.json"))
        assert text.endswith("}\n")
        assert "\\u2014" not in text         # em-dash serialized raw
        assert "\u2014" in text              # ... as the actual character
        atp = json.loads(text)["properties"]["atpDate"]
        assert list(atp.keys()) == ["type", "description", "format",
                                    "x-channels"]


# ── UIFPE-262: scaffolder v2 — nested arrays, ranked pick, lookups ──────────

def price_nested(**over):
    col = {
        "goldColumnName": "priceSpecifications",
        "businessDescription": "Current price per (currency, VAT country)",
        "kind": "nested",
        "joinKey": "PPID -> price rows attach by product PPID",
        "channels": {"event": True, "message": True, "api": True},
        "pick": {
            "partitionBy": ["currency", "vatCountry"],
            "orderBy": [
                {"expr": "CASE WHEN END_DATE IS NULL OR END_DATE >= "
                         "current_timestamp THEN 0 ELSE 1 END",
                 "direction": "asc"},
                {"expr": "END_DATE", "direction": "desc"},
            ],
        },
        "fields": [
            {"name": "priceListName", "type": "string", "sortKey": True,
             "source": {"table": "QP_LIST_HEADERS_TL", "column": "NAME",
                        "filter": "LANGUAGE = 'US'",
                        "join": {"type": "left",
                                 "on": [{"leftColumn": "LIST_HEADER_ID",
                                         "rightColumn": "LIST_HEADER_ID"}]}}},
            {"name": "currency", "type": "string", "sortKey": True,
             "source": {"table": "QP_LIST_HEADERS_B",
                        "column": "CURRENCY_CODE",
                        "join": {"type": "left",
                                 "on": [{"leftColumn": "LIST_HEADER_ID",
                                         "rightColumn": "LIST_HEADER_ID"}]}}},
            {"name": "vatCountry", "type": "string", "sortKey": True,
             "source": {"table": "XXPSO_QP_VAT_PRICE_STG",
                        "column": "VAT_COUNTRY"}},
            {"name": "netPrice", "type": "number", "sortKey": False,
             "source": {"table": "XXPSO_QP_VAT_PRICE_STG",
                        "column": "NET_PRICE"}},
            {"name": "endDate", "type": "timestamp", "sortKey": False,
             "source": {"table": "XXPSO_QP_VAT_PRICE_STG",
                        "column": "END_DATE"}},
        ],
    }
    col.update(over)
    return col


def price_intake(**over):
    intake = {
        "domain": "price", "table": "gold_price.price",
        "drivingTable": "XXPSO_QP_VAT_PRICE_STG",
        "drivingSilverTable":
            "uif_echo_{env}_silver_tep.xxpso_qp_vat_price_stg",
        "silverTables": {
            "QP_LIST_HEADERS_B":
                "uif_echo_{env}_silver_tep.qp_list_headers_b",
            "QP_LIST_HEADERS_TL":
                "uif_echo_{env}_silver_tep.qp_list_headers_tl",
        },
        "drivingFilter": "PPID IS NOT NULL",
        "entityKey": [{"goldColumn": "ppid", "drivingColumn": "PPID"}],
        "transforms": {"isbn13Pick": {"sqlExpr":
            "MAX(CASE WHEN length({x}) = 13 THEN {x} END)"}},
        "batch": {"requestedBy": "qe", "targetDate": None,
                  "backfillMode": "silent", "newChannel": None},
        "columns": [
            stock_scalar("ppid", "PPID", nullable=False),
            stock_scalar("isbn13", "ISBN", transform={"rule": "isbn13Pick"}),
            price_nested(),
        ],
    }
    intake.update(over)
    # the stock helper stamps the stock driving table — repoint to price's
    for c in intake["columns"]:
        if c.get("kind") == "scalar":
            c["source"]["table"] = "XXPSO_QP_VAT_PRICE_STG"
    return intake


class TestScaffolderV2:
    def compile(self, tmp_path, intake=None,
                job_props={"frequency": "gold-24h", "start_time": "08:45"}):
        return cid.compile_delta(tmp_path, intake or price_intake(),
                                 "add", None, job_props)

    def _text(self, edits, suffix):
        return next(t for p, t in edits.items()
                    if str(p).replace("\\", "/").endswith(suffix))

    def test_price_shape_compiles_end_to_end(self, tmp_path):
        edits, log = self.compile(tmp_path)
        sql = self._text(edits, "sql/price.sql")
        assert "lk_qp_list_headers_tl AS (" in sql
        assert "WHERE LANGUAGE = 'US' GROUP BY 1" in sql
        # every projection carries its declared-type cast (round finding 5)
        assert ("PARTITION BY PPID, CAST(lk_currency AS STRING), "
                "CAST(VAT_COUNTRY AS STRING)") in sql
        # __pick_rn: a fenced alias — staging tables commonly carry RN
        assert "WHERE __pick_rn = 1" in sql
        assert "GROUP BY PPID" in sql
        assert "'s1', CAST(lk_priceListName AS STRING)" in sql
        assert "'currency', CAST(lk_currency AS STRING)" in sql
        # the driving filter lives in base, not duplicated in the final WHERE
        assert sql.count("PPID IS NOT NULL") == 1

    def test_sql_parses_as_spark(self, tmp_path):
        pytest.importorskip("sqlglot")
        import sqlglot
        edits, _ = self.compile(tmp_path)
        assert len(sqlglot.parse(self._text(edits, "sql/price.sql"),
                                 dialect="spark")) == 1

    def test_manifest_lookups_types_and_sort_keys(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        aliases = {s["alias"]: s for s in man["sources"]}
        assert aliases["price"]["is_source_of_truth"] is True
        assert aliases["qp_list_headers_b"]["lookup_only"] is True
        assert aliases["qp_list_headers_tl"]["lookup_only"] is True
        assert man["array_sort_keys"]["priceSpecifications"] == [
            "priceListName", "currency", "vatCountry"]
        assert man["column_types"]["priceSpecifications"].startswith(
            "ARRAY<STRUCT<priceListName: STRING")
        assert "endDate: TIMESTAMP" in man["column_types"][
            "priceSpecifications"]

    def test_master_array_and_pipeline_sources(self, tmp_path):
        edits, _ = self.compile(tmp_path)
        master = json.loads(self._text(edits, "master.json"))
        ps = master["properties"]["priceSpecifications"]
        assert ps["type"] == ["array", "null"]
        assert ps["items"]["properties"]["endDate"]["format"] == "date-time"
        base = yaml.safe_load(self._text(edits, "base/price.yaml"))
        assert [s["alias"] for s in base["sources"]] == [
            "price", "qp_list_headers_tl", "qp_list_headers_b"]

    # ── refusals ──────────────────────────────────────────────────────────

    def test_refuses_no_sort_keys(self, tmp_path):
        nested = price_nested()
        for f in nested["fields"]:
            f["sortKey"] = False
        with pytest.raises(ValueError, match="sortKey"):
            self.compile(tmp_path, price_intake(columns=[
                stock_scalar("ppid", "PPID", nullable=False), nested]))

    def test_refuses_partition_not_covered_by_sort_keys(self, tmp_path):
        nested = price_nested()
        # vatCountry partitions but stops being a sort key
        nested["fields"][2]["sortKey"] = False
        intake = price_intake(columns=[
            stock_scalar("ppid", "PPID", nullable=False), nested])
        for c in intake["columns"]:
            if c.get("kind") == "scalar":
                c["source"]["table"] = "XXPSO_QP_VAT_PRICE_STG"
        with pytest.raises(ValueError, match="not.*sort keys"):
            self.compile(tmp_path, intake)

    def test_refuses_lookup_without_silver_entry(self, tmp_path):
        intake = price_intake()
        intake["silverTables"].pop("QP_LIST_HEADERS_B")
        with pytest.raises(ValueError, match="QP_LIST_HEADERS_B"):
            self.compile(tmp_path, intake)

    def test_refuses_lookup_without_join(self, tmp_path):
        nested = price_nested()
        nested["fields"][1]["source"].pop("join")
        intake = price_intake(columns=[
            stock_scalar("ppid", "PPID", nullable=False), nested])
        for c in intake["columns"]:
            if c.get("kind") == "scalar":
                c["source"]["table"] = "XXPSO_QP_VAT_PRICE_STG"
        with pytest.raises(ValueError, match="join.on"):
            self.compile(tmp_path, intake)

    def test_refuses_two_nested_columns(self, tmp_path):
        second = price_nested(goldColumnName="otherSpecs")
        intake = price_intake()
        intake["columns"].append(second)
        with pytest.raises(ValueError, match="one struct array"):
            self.compile(tmp_path, intake)

    def test_refuses_value_list(self, tmp_path):
        nested = price_nested()
        nested["fields"] = nested["fields"][:1]
        nested.pop("pick")
        intake = price_intake(columns=[
            stock_scalar("ppid", "PPID", nullable=False), nested])
        with pytest.raises(ValueError, match="value list"):
            self.compile(tmp_path, intake)

    def test_no_pick_no_lookup_nested_still_assembles(self, tmp_path):
        nested = price_nested()
        nested.pop("pick")
        # all-driving fields: strip the lookups
        nested["fields"] = [f for f in nested["fields"]
                            if "join" not in (f.get("source") or {})]
        for f in nested["fields"]:
            f["sortKey"] = True
        intake = price_intake(columns=[
            stock_scalar("ppid", "PPID", nullable=False), nested])
        for c in intake["columns"]:
            if c.get("kind") == "scalar":
                c["source"]["table"] = "XXPSO_QP_VAT_PRICE_STG"
        edits, _ = self.compile(tmp_path, intake)
        sql = self._text(edits, "sql/price.sql")
        assert "picked" not in sql and "base AS (" not in sql
        assert "collect_list" in sql and "GROUP BY PPID" in sql

    # ── consolidation pins (UIFPE-262 adversarial round) ──────────────────

    def test_declared_types_are_cast_in_the_sql(self, tmp_path):
        # struct members must match manifest column_types exactly, or the
        # live table diverges from the declaration on the first CTAS and
        # diff_nested_field_additions refuses every later run
        edits, _ = self.compile(tmp_path)
        sql = self._text(edits, "sql/price.sql")
        assert "CAST(NET_PRICE AS DECIMAL(38,10))" in sql
        assert "CAST(END_DATE AS TIMESTAMP)" in sql

    def test_field_transform_renders_and_aggregates_refuse(self, tmp_path):
        intake = price_intake()
        intake["transforms"]["centsRound"] = {"sqlExpr": "ROUND({x}, 2)"}
        intake["columns"][2]["fields"][3]["transform"] = {"rule": "centsRound"}
        edits, _ = self.compile(tmp_path, intake)
        assert "CAST(ROUND(NET_PRICE, 2) AS DECIMAL(38,10))" in \
            self._text(edits, "sql/price.sql")
        # an aggregate transform on a per-row array element is a refusal
        intake["columns"][2]["fields"][3]["transform"] = {"rule": "isbn13Pick"}
        with pytest.raises(ValueError, match="renders an aggregate"):
            self.compile(tmp_path, intake)

    def test_nested_column_level_filter_refuses(self, tmp_path):
        intake = price_intake()
        intake["columns"][2] = price_nested(
            filter="LIST_LINE_TYPE_CODE = 'PLL'")
        with pytest.raises(ValueError, match="column-level filter"):
            self.compile(tmp_path, intake)

    def test_driving_filter_may_not_reference_lookups(self, tmp_path):
        # sot_filter runs against the driving silver ALONE — a lookup token
        # in the shared predicate diverges transform and reconcile domains
        intake = price_intake(
            drivingFilter="PPID IS NOT NULL AND currency = 'USD'")
        with pytest.raises(ValueError, match="lookup name"):
            self.compile(tmp_path, intake)

    def test_order_by_field_names_resolve_to_projections(self, tmp_path):
        nested = price_nested()
        nested["pick"]["orderBy"] = [{"expr": "endDate", "direction": "DESC"}]
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        # field name -> its cast expression; direction case-insensitive
        assert "ORDER BY CAST(END_DATE AS TIMESTAMP) DESC" in \
            self._text(edits, "sql/price.sql")

    def test_order_by_aggregate_refuses(self, tmp_path):
        nested = price_nested()
        nested["pick"]["orderBy"].append(
            {"expr": "max(START_DATE)", "direction": "desc"})
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="aggregate or window"):
            self.compile(tmp_path, intake)

    def test_join_left_table_must_be_the_driving_table(self, tmp_path):
        nested = price_nested()
        nested["fields"][0]["source"]["join"]["on"][0]["leftTable"] = \
            "QP_LIST_HEADERS_B"
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="not the driving table"):
            self.compile(tmp_path, intake)

    def test_silver_tables_value_fence(self, tmp_path):
        intake = price_intake()
        intake["silverTables"]["QP_LIST_HEADERS_B"] = \
            "echo_{env}_silver.x; DROP TABLE y"
        with pytest.raises(ValueError, match="silverTables"):
            self.compile(tmp_path, intake)

    def test_empty_pick_partition_refuses(self, tmp_path):
        nested = price_nested()
        nested["pick"]["partitionBy"] = []
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="partitionBy and orderBy"):
            self.compile(tmp_path, intake)

    def test_same_lookup_table_dedupes_to_one_cte_and_source(self, tmp_path):
        nested = price_nested()
        nested["fields"].insert(2, {
            "name": "priceListType", "type": "string", "sortKey": False,
            "source": {"table": "QP_LIST_HEADERS_B",
                       "column": "LIST_TYPE_CODE",
                       "join": {"type": "left",
                                "on": [{"leftColumn": "LIST_HEADER_ID",
                                        "rightColumn": "LIST_HEADER_ID"}]}}})
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        sql = self._text(edits, "sql/price.sql")
        assert sql.count("lk_qp_list_headers_b AS (") == 1
        assert "max(LIST_TYPE_CODE) AS priceListType" in sql
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        assert [s["alias"] for s in man["sources"]].count(
            "qp_list_headers_b") == 1
        base = yaml.safe_load(self._text(edits, "base/price.yaml"))
        assert [s["alias"] for s in base["sources"]].count(
            "qp_list_headers_b") == 1

    def test_lineage_parents_match_master_properties(self, tmp_path):
        # the exact set-compare validate_gold_schemas enforces in CI
        edits, _ = self.compile(tmp_path)
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        master = json.loads(self._text(edits, "master.json"))
        assert set(man["canonical_lineage"]) == set(master["properties"])

    def test_final_select_keeps_intake_column_order(self, tmp_path):
        intake = price_intake()
        intake["columns"] = [intake["columns"][2], intake["columns"][0],
                             intake["columns"][1]]
        edits, _ = self.compile(tmp_path, intake)
        sql = self._text(edits, "sql/price.sql")
        assert sql.index("AS priceSpecifications") < sql.index("AS ppid") \
            < sql.index("AS isbn13")
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        assert man["output_columns"] == ["priceSpecifications", "ppid",
                                         "isbn13"]

    def test_emitted_mapping_validates_and_is_normalized(self, tmp_path):
        jsonschema = pytest.importorskip("jsonschema")
        intake = price_intake()
        intake["batch"]["targetDate"] = "2026-09-30"
        edits, _ = self.compile(tmp_path, intake)
        mapping = json.loads(self._text(edits, "price.mapping.json"))
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(mapping)
        # the wizard omits leftTable; the emitted artifact must not fail
        # its own $schema, so every on-pair is stamped with the driving
        nested = next(c for c in mapping["columns"]
                      if c["kind"] == "nested")
        pairs = [o for f in nested["fields"]
                 for o in ((f.get("source") or {}).get("join") or {})
                 .get("on", [])]
        assert pairs and all(o["leftTable"] == "XXPSO_QP_VAT_PRICE_STG"
                             for o in pairs)

    # ── re-verification round pins ─────────────────────────────────────────

    def test_order_by_resolution_is_single_pass(self, tmp_path):
        # a field named exactly like another field's SOURCE column must not
        # be substituted into the already-resolved expression (blocker 1)
        nested = price_nested()
        nested["fields"][4] = {
            "name": "effectiveDate", "type": "timestamp", "sortKey": False,
            "source": {"table": "XXPSO_QP_VAT_PRICE_STG",
                       "column": "effective"}}
        nested["fields"].append({
            "name": "effective", "type": "string", "sortKey": False,
            "source": {"table": "XXPSO_QP_VAT_PRICE_STG",
                       "column": "EFF_CODE"}})
        nested["pick"]["orderBy"] = [{"expr": "effectiveDate",
                                      "direction": "desc"}]
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        sql = self._text(edits, "sql/price.sql")
        assert "ORDER BY CAST(effective AS TIMESTAMP) DESC" in sql
        assert "CAST(CAST(EFF_CODE" not in sql

    def test_order_by_never_rewrites_string_literals(self, tmp_path):
        # 'currency' here is a literal, not a reference to the field
        nested = price_nested()
        nested["pick"]["orderBy"] = [{"expr":
            "CASE WHEN VAT_COUNTRY = 'currency' THEN 0 ELSE 1 END"}]
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        assert "= 'currency' THEN" in self._text(edits, "sql/price.sql")

    def test_driving_side_field_filter_refuses(self, tmp_path):
        # a lookup filter becomes its CTE WHERE; a driving-side one has no
        # home in the SQL — recorded-but-unapplied config is refused
        nested = price_nested()
        nested["fields"][3]["source"]["filter"] = "LIST_LINE_TYPE_CODE='PLL'"
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="filter on\nthe driving|"
                                             "filter on the driving"):
            self.compile(tmp_path, intake)

    def test_lookup_table_name_fence(self, tmp_path):
        nested = price_nested()
        nested["fields"][1]["source"]["table"] = "EVIL\n  - alias: injected"
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="plain table identifier"):
            self.compile(tmp_path, intake)

    def test_garbage_direction_refuses(self, tmp_path):
        nested = price_nested()
        nested["pick"]["orderBy"][1]["direction"] = "DESCENDING"
        intake = price_intake()
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="asc or desc"):
            self.compile(tmp_path, intake)

    def test_lookup_colliding_with_entity_alias_refuses(self, tmp_path):
        nested = price_nested()
        nested["fields"][1]["source"]["table"] = "PRICE"
        intake = price_intake()
        intake["silverTables"]["PRICE"] = "uif_echo_{env}_silver_tep.price"
        intake["columns"][2] = nested
        with pytest.raises(ValueError, match="entity alias"):
            self.compile(tmp_path, intake)

    def test_scalar_transform_output_is_cast_to_declared_type(self, tmp_path):
        # the manifest declares the type unconditionally — an uncast
        # transform output is a permanent auto-DDL refuse loop
        edits, _ = self.compile(tmp_path)
        assert re.search(r"CAST\(MAX\(CASE WHEN length\(ISBN\) = 13 "
                         r"THEN ISBN END\) AS STRING\)\s+AS isbn13",
                         self._text(edits, "sql/price.sql"))

    def test_driving_filter_literals_are_not_lookup_refs(self, tmp_path):
        intake = price_intake(
            drivingFilter="PPID IS NOT NULL AND STATUS = 'currency'")
        edits, _ = self.compile(tmp_path, intake)
        assert "STATUS = 'currency'" in self._text(edits, "sql/price.sql")

    def test_float_declares_double_everywhere(self, tmp_path):
        # float is approximate: master format "double" (json_type_to_spark
        # derives exact DECIMAL without it), SQL CAST + column_types DOUBLE
        nested = price_nested()
        nested["fields"][3]["type"] = "float"
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        assert "CAST(NET_PRICE AS DOUBLE)" in self._text(edits,
                                                         "sql/price.sql")
        man = yaml.safe_load(self._text(edits, "manifest.yaml"))
        assert "netPrice: DOUBLE" in man["column_types"][
            "priceSpecifications"]
        master = json.loads(self._text(edits, "master.json"))
        item = master["properties"]["priceSpecifications"]["items"]
        assert item["properties"]["netPrice"]["format"] == "double"

    def test_emitted_mapping_normalizes_direction_case(self, tmp_path):
        nested = price_nested()
        nested["pick"]["orderBy"][1]["direction"] = "DESC"
        intake = price_intake()
        intake["columns"][2] = nested
        edits, _ = self.compile(tmp_path, intake)
        mapping = json.loads(self._text(edits, "price.mapping.json"))
        out = next(c for c in mapping["columns"] if c["kind"] == "nested")
        assert [o.get("direction") for o in out["pick"]["orderBy"]] == \
            ["asc", "desc"]
