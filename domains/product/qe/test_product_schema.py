# test_product_schema.py
# Consistency tests for the REAL Product artifacts: master.json, the mapping
# manifest, the SQL, and the fixtures must agree with each other and with the
# gold_cdc engine's expectations. Mirrors the CI gold-schemas lints so drift
# is caught locally before a PR.

import json
import re
import unittest
from pathlib import Path

import yaml

import gold_cdc

# qe/ -> parents[1] == domains/product. The compiled runtime artifacts
# (master.json, manifest.yaml, product.sql) live under src/schemas/; the test
# fixtures travelled with the tests into qe/fixtures/.
QE_DIR  = Path(__file__).resolve().parent
SCHEMAS = QE_DIR.parent / "src" / "schemas"

MASTER = json.loads(
    (SCHEMAS / "gold/product/v1/master.json").read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load(
    (SCHEMAS / "mapping/product/v1/manifest.yaml").read_text(encoding="utf-8"))
SQL = (SCHEMAS / "mapping/product/v1/sql/product.sql").read_text(encoding="utf-8")
PIPELINE = yaml.safe_load(
    (QE_DIR.parent / "src/apps/pipelines/base/product.yaml")
    .read_text(encoding="utf-8"))
FIXTURE = json.loads(
    (QE_DIR / "fixtures/expected_gold.json").read_text(encoding="utf-8"))


class TestMasterManifestConsistency(unittest.TestCase):
    def test_lineage_covers_every_property_exactly(self):
        self.assertEqual(set(MANIFEST["canonical_lineage"]),
                         set(MASTER["properties"]))

    def test_output_columns_match_properties(self):
        self.assertEqual(set(MANIFEST["output_columns"]),
                         set(MASTER["properties"]))

    def test_output_columns_order_matches_master_order(self):
        # Column realignment: output_columns (SQL select order / physical
        # order on rebuild) must track master.json property order exactly.
        self.assertEqual(MANIFEST["output_columns"],
                         list(MASTER["properties"].keys()))

    def test_pk_roles_consistent(self):
        pk_lineage = {c for c, e in MANIFEST["canonical_lineage"].items()
                      if isinstance(e, dict) and e.get("role") == "pk"}
        self.assertEqual(pk_lineage,
                         set(MANIFEST["target"]["primary_key_cols"]))
        self.assertEqual(pk_lineage, {"inventoryItemId", "organizationId"})

    def test_array_sort_keys_present_for_all_arrays(self):
        gold_cdc.validate_array_sort_keys(MASTER, MANIFEST)

    def test_complex_types_declared_in_column_types(self):
        col_types = MANIFEST["column_types"]
        for name, prop in MASTER["properties"].items():
            t = prop.get("type")
            types = t if isinstance(t, list) else [t]
            if "array" in types or "object" in types:
                self.assertIn(name, col_types,
                              f"{name} needs a column_types entry")
                # the auto-DDL type mapper must resolve every property
                gold_cdc.json_type_to_spark(name, prop, col_types)

    def test_conditional_lineage_entries_are_well_formed(self):
        for col, ent in MANIFEST["canonical_lineage"].items():
            if "conditional" in ent:
                self.assertTrue(ent.get("discriminator", {}).get("pdh_column"),
                                f"{col}: conditional needs a discriminator")
                for br in ent["conditional"]:
                    for f in ("when", "pdh_table", "pdh_column"):
                        self.assertTrue(br.get(f), f"{col}: branch missing {f}")
            else:
                self.assertTrue(ent.get("pdh_table"), f"{col}: pdh_table required")
                self.assertTrue(ent.get("pdh_column"), f"{col}: pdh_column required")

    def test_key_mappings_valid_for_engine(self):
        gold_cdc.validate_key_mappings(MANIFEST["sources"],
                                       MANIFEST["target"]["primary_key_cols"])

    def test_catalog_names_are_env_tokenised(self):
        # EDS Glue databases are env-qualified; artifacts must carry {env}
        # so one Git file serves dev/uat/prod.
        self.assertEqual(MANIFEST["target"]["database"], "uif_echo_{env}_gold_product")
        for src in MANIFEST["sources"]:
            self.assertTrue(src["silver_table"].startswith("uif_echo_{env}_silver_"),
                            f"{src['alias']}: {src['silver_table']}")
            self.assertEqual(
                gold_cdc.resolve_env(src["silver_table"], "dev").split(".")[0],
                "uif_echo_dev_silver_mdm")

    def test_channels_discoverable(self):
        self.assertEqual(gold_cdc.discover_channels(MASTER),
                         ["api", "canonical", "event", "message"])

    def test_nested_channels_valid(self):
        gold_cdc.validate_nested_channels(MASTER)

    def test_affiliations_hidden_from_event_channel(self):
        kind, all_f, kept = gold_cdc.nested_fields_for_channel(
            MASTER["properties"]["contributors"], "event")
        self.assertEqual(kind, "array")
        hidden = set(all_f) - set(kept)
        # 2026-08-17 (framework owner): affiliationType1/affiliation1 are
        # promoted to the event tier; only the 2/3 pairs stay hidden.
        # 2026-09-11: contributorId leaves every channel (x-channels: []).
        self.assertEqual(hidden, {"contributorId",
                                  "affiliationType2", "affiliation2",
                                  "affiliationType3", "affiliation3"})
        # message + api see everything but contributorId; canonical sees all
        for ch in ("message", "api"):
            _, af, kf = gold_cdc.nested_fields_for_channel(
                MASTER["properties"]["contributors"], ch)
            self.assertEqual(set(af) - set(kf), {"contributorId"})
        _, af, kf = gold_cdc.nested_fields_for_channel(
            MASTER["properties"]["contributors"], "canonical")
        self.assertEqual(af, kf)


def _strip_sql_comments(sql_text: str) -> str:
    """Drop everything from '--' to end-of-line on every line. Good enough for
    this dialect (no '--' appears inside a string/identifier in this file)."""
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


# Direct-Silver-reference pattern (finding #20 root cause): the previous
# check assumed Silver tables are named literally "silver_*" and matched
# FROM-clause identifiers with `[A-Za-z_][A-Za-z0-9_]*` — a character class
# that stops at the first `{`. Real Silver tables are named
# "echo_{env}_silver_*" (see manifest.yaml `sources[].silver_table` /
# `target.database`), so the old regex silently truncated at "echo_" before
# ever reaching "silver", and `"echo_".startswith("silver_")` is always False.
# The check never had a chance to fire. This pattern matches the real naming
# convention directly, in both templated ("uif_echo_{env}_silver_...") and
# resolved ("uif_echo_dev_silver_...") form. The uif_ prefix is optional and
# matched rather than skipped: without it the pattern still fires, but on the
# substring, and reports a table name the SQL does not contain.
DIRECT_SILVER_REF = re.compile(r"(?:uif_)?echo_[\w{}]+_silver_[\w.]+", re.IGNORECASE)


def find_direct_silver_references(sql_text: str) -> list[str]:
    """Return every direct Silver-table reference found in `sql_text`,
    ignoring SQL comments. Empty list == compliant (only pre-registered
    aliases like `items` / `items_ext` / `items_tl` — created as temp views
    over Silver elsewhere — may appear in FROM/JOIN)."""
    code_only = _strip_sql_comments(sql_text)
    return DIRECT_SILVER_REF.findall(code_only)


class TestSqlConsistency(unittest.TestCase):
    def test_sql_aliases_every_output_column(self):
        for col in MANIFEST["output_columns"]:
            self.assertRegex(SQL, rf"AS\s+{re.escape(col)}\b",
                             f"SQL does not produce column {col}")

    def test_sql_references_only_registered_aliases_or_ctes(self):
        # FROM/JOIN targets must be a manifest-registered alias (items,
        # items_ext, items_tl, org_params — pre-registered temp views over
        # Silver) or a CTE defined earlier in this same WITH block. Anything
        # else is a stray/undeclared table reference.
        aliases = {s["alias"] for s in MANIFEST["sources"]}
        cte_names = set(re.findall(r"(?m)^(\w+)\s+AS\s*\(", SQL))
        known = aliases | cte_names
        for m in re.finditer(r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", SQL):
            name = m.group(1)
            self.assertIn(name, known,
                         f"SQL references an unregistered table/alias: {name}")

    def test_sql_has_no_direct_silver_references(self):
        # The real, compliant SQL: this must pass. If it ever fails, that's a
        # genuine violation (someone referenced echo_{env}_silver_* directly
        # instead of the pre-registered items/items_ext/items_tl/org_params
        # views) — fix the SQL, do not weaken this check.
        violations = find_direct_silver_references(SQL)
        self.assertEqual(violations, [],
                         f"SQL references Silver table(s) directly: {violations}. "
                         f"Read from the registered aliases (items, items_ext, "
                         f"items_tl, org_params) instead.")

    def test_direct_silver_reference_check_actually_fires(self):
        # Proves the check above is not a dead assertion (finding #20's bug
        # was exactly this: a check that could never fail no matter what the
        # SQL said). Inject a direct Silver reference into a copy of the real
        # SQL and confirm detection fires on it.
        injected = SQL.replace(
            "FROM items_ext WHERE ATTR_GROUP_ID = 802",
            "FROM uif_echo_{env}_silver_mdm.ego_mtl_sy_items_ext_b WHERE ATTR_GROUP_ID = 802",
            1,
        )
        self.assertNotEqual(injected, SQL, "injection did not change the SQL text")
        violations = find_direct_silver_references(injected)
        self.assertTrue(violations,
                        "direct-Silver-reference check failed to detect an "
                        "injected echo_{env}_silver_* reference")
        self.assertIn("uif_echo_{env}_silver_mdm.ego_mtl_sy_items_ext_b", violations)

        # Also prove the resolved-env form (no literal braces) is caught.
        injected_resolved = SQL.replace(
            "FROM items_ext WHERE ATTR_GROUP_ID = 802",
            "FROM uif_echo_dev_silver_mdm.ego_mtl_sy_items_ext_b WHERE ATTR_GROUP_ID = 802",
            1,
        )
        self.assertTrue(find_direct_silver_references(injected_resolved),
                        "direct-Silver-reference check failed on resolved-env form")


# ── org dimension (replaced the interim literal map, ADR-0005 item 3) ────────
# The three tests these replace guarded the literal org map against drifting
# from manifest `org_groups`. Both the map and that block are gone; what needs
# guarding now is that they do not come back, and that the join that replaced
# them is wired end to end.

class TestDomainFilter(unittest.TestCase):
    """The product table is PRODUCTS ONLY (SEGMENT1 'A%'; 'P%' = programs,
    UIFPE-62 — business decision 2026-08-06). The rule is enforced in two
    places that MUST agree: the mapping SQL's WHERE (what gets written) and
    the manifest's sot_filter (what reconcile deletes). If they diverge,
    rows reclassified out of the domain leak forever, or valid rows get
    reconcile-deleted."""

    SOT = next(s for s in MANIFEST["sources"] if s.get("is_source_of_truth"))
    # Owner decisions: products only (2026-08-06) + DRAFT / ERROR / DELETE /
    # RETIRED excluded (2026-09-07, UIFPE-271) + TEMPLATE excluded (2026-09-15:
    # Oracle item templates carry the bare 'A' prefix and no catalog group).
    FILTER = ("SEGMENT1 LIKE 'A%' AND INVENTORY_ITEM_STATUS_CODE "
              "NOT IN ('DRAFT', 'ERROR', 'DELETE', 'RETIRED', 'TEMPLATE')")

    def test_manifest_declares_the_domain_filter(self):
        self.assertEqual(self.SOT.get("sot_filter"), self.FILTER)

    def test_mapping_sql_applies_the_same_filter(self):
        code = _strip_sql_comments(SQL)
        self.assertRegex(
            code, r"WHERE\s+items\.SEGMENT1\s+LIKE\s+'A%'",
            "the mapping SQL no longer filters the driving table to the "
            "product domain — it must match the manifest's sot_filter")
        self.assertRegex(
            code, r"items\.INVENTORY_ITEM_STATUS_CODE\s+NOT\s+IN\s+"
                  r"\('DRAFT',\s*'ERROR',\s*'DELETE',\s*'RETIRED',\s*'TEMPLATE'\)",
            "the status exclusion (UIFPE-271) left the SQL WHERE")

    def test_filters_textually_consistent(self):
        # The SQL side is alias-qualified; strip the alias and compare to the
        # manifest verbatim so neither can drift silently.
        code = _strip_sql_comments(SQL)
        where = code[code.rindex("WHERE items."):]
        sql_filter = " ".join(where.replace("WHERE ", "", 1)
                              .replace("items.", "").split())
        self.assertEqual(sql_filter, self.SOT["sot_filter"])

    def test_reconcile_projection_carries_the_filter(self):
        sql = gold_cdc.build_sot_pk_sql(
            "sot", self.SOT["sot_pk_mapping"],
            MANIFEST["target"]["primary_key_cols"],
            self.SOT.get("sot_filter"))
        self.assertTrue(sql.endswith("WHERE " + self.FILTER))


class TestPhysicalColumnOrder(unittest.TestCase):
    """The physical column order is business-specified (2026-08-06) and comes
    from the mapping SQL's SELECT order at CTAS time. output_columns and
    master.json are already pinned to each other; this adds the two missing
    legs — the business list itself, and the SQL."""

    BUSINESS_ORDER = [
        # 2026-08-17 batch: parentProduct (after isbn10), inventoryItemStatus
        # (after longDescription), ndaFlag (after learnerStageLevel2) and the
        # two BOM collections (last) — placements specified by the framework
        # owner with the batch.
        # 2026-09-15 batch: itemCatalogGroupId (after organizationId) and
        # sourceItemNumber (after isbn10) at the owner's explicit request —
        # a mid-list insert, so the batch ships with a --rebuild.
        "inventoryItemId", "organizationId", "itemCatalogGroupId", "productId",
        "isbn13", "isbn10", "sourceItemNumber",
        "parentProductId",
        "organizationCode", "description", "longDescription",
        "inventoryItemStatus", "itemType",
        "lineOfBusiness", "ipOwnerCategory", "copyrightYear", "editionNumber",
        "owningSubsidiaryCountryCode", "owningSubsidiaryName", "deliveryMethod",
        "brand", "subBrand", "digitalPlatform", "disciplineLevel1",
        "disciplineLevel2", "disciplineLevel3", "disciplineLevel4",
        "productClassificationLevel1", "productClassificationLevel2",
        "languageLevel1", "languageLevel2", "learnerStageLevel1", "learnerStageLevel2",
        "nonDisclosureAgreementIndicator",
        "digitalEffectiveStartDate", "digitalVersionNumber", "unitWeightQuantity",
        "weightUomCode", "unitLengthQuantity", "unitWidthQuantity", "unitHeightQuantity",
        "dimensionUomCode", "qualificationLevelName", "finalTextPageCount",
        "fulfillmentOrgDetails", "accessDurationType", "accessDurationQuantity",
        "accessDurationUnitCode",
        # Inserted 2026-09-07 (UIFPE-270) at the owner's explicit request
        # ("next to accessDurationUnit") — the batch ships with a --rebuild, which
        # is what a physical reorder costs.
        "courseDurationQuantity", "courseDurationUnitCode",
        "coverImageLargeUrl", "coverImageRegularUrl",
        "coverImageHighResolutionUrl", "contributors", "webConsumingSystem",
        "associatedPrograms", "marketingCopy", "standardComponents",
        "customComponents",
        # Appended 2026-08-31 (categories). New columns go on the END of this
        # list: it is the physical column order on rebuild, so inserting one
        # mid-list moves every column after it for no business reason.
        "categories",
        # Appended 2026-09-07 (UIFPE-270).
        "enablingTechnology", "productUrl",
        # Appended 2026-09-20 (UIFPE-476) - Instructor Resource Content files.
        "instructorResourceFiles",
    ]

    def test_output_columns_is_the_business_order(self):
        self.assertEqual(MANIFEST["output_columns"], self.BUSINESS_ORDER)

    def test_sql_select_order_is_the_business_order(self):
        # The final SELECT begins after the last CTE closes at column 0.
        # Every select item ends its line with `AS <alias>` (single- and
        # multi-line expressions alike), so line-anchored extraction over the
        # comment-stripped final SELECT recovers the physical order.
        code = _strip_sql_comments(SQL)
        starts = [m.start() for m in re.finditer(r"(?m)^SELECT\b", code)]
        self.assertEqual(len(starts), 1,
                         "expected exactly one column-0 SELECT (the final "
                         "one); CTE SELECTs are indented")
        final = code[starts[0]:]
        final = final[:final.index("\nFROM items")]
        aliases = re.findall(r"AS\s+([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*$",
                             final, re.MULTILINE)
        self.assertEqual(aliases, self.BUSINESS_ORDER)


class TestFulfillmentStructNullGate(unittest.TestCase):
    """Business rule 2026-08-07: fulfillmentOrgDetails is NULL when every
    field is null — named_struct otherwise always constructs a value and a
    fully empty struct serializes as {"field": null, ...} instead of null."""

    def test_all_null_gate_wraps_the_struct(self):
        # The expression nests CASEs (the proliferation branches appear inside
        # the gate conditions), so regex-matching the whole CASE is fragile;
        # instead anchor on the output alias and inspect the window before it.
        code = _strip_sql_comments(SQL)
        end = code.index("AS fulfillmentOrgDetails")
        window = code[max(0, end - 3000):end]
        self.assertIn("THEN NULL", window,
                      "fulfillmentOrgDetails lost its all-null -> NULL gate")
        # The gate's conditions all precede the (unique) "THEN NULL"; slicing
        # on ELSE would stop at a nested proliferation CASE's own ELSE.
        gate = window[:window.index("THEN NULL")]
        self.assertLess(window.index("THEN NULL"), window.index("named_struct"),
                        "the NULL arm must come before the struct construction")
        # every struct field participates in the null check
        for field in ("sellingRestrictionCode", "discountCode",
                      "titleStatusCode", "regionalPublicationDate",
                      "returnClassCode", "imprintName"):
            self.assertIn(field, gate,
                          f"{field} missing from the all-null gate")


class TestCoverImageNormalization(unittest.TestCase):
    """URL normalization on the three cover images (business rule 2026-08-06).
    Pins the SQL to the rule so a regeneration from the intake workbook (which
    predates the rule) cannot silently drop it."""

    DAM = "https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a"
    CODE = None

    @classmethod
    def setUpClass(cls):
        cls.CODE = _strip_sql_comments(SQL)

    def _case_for(self, col):
        # The CASE expression that ends with `AS <col>` — non-greedy back from
        # the alias to the nearest preceding CASE.
        m = re.search(rf"(CASE(?:(?!CASE).)*?END)\s+AS\s+{col}\b",
                      self.CODE, re.DOTALL)
        self.assertIsNotNone(m, f"no CASE expression feeds {col}")
        return m.group(1)

    def test_dam_base_applied_to_all_three(self):
        for col in ("coverImageLargeUrl", "coverImageRegularUrl",
                    "coverImageHighResolutionUrl"):
            case = self._case_for(col)
            self.assertIn(self.DAM, case, f"{col} lost the DAM base URL")
            self.assertIn("'/A'", case, f"{col} lost the '/A' DAM-path gate")

    def test_site_relative_prefixes_are_size_specific(self):
        # Large maps '/bigcovers', Regular maps '/coverimage' — asymmetric on
        # purpose. Regular's prefix was corrected 2026-08-07 from the business
        # rule's original '/covers' (0 dev rows) to the real site-relative
        # shape '/coverimage/<isbn>.jpg' (1,567,297 dev rows).
        self.assertIn("'/bigcovers%'", self._case_for("coverImageLargeUrl"))
        self.assertNotIn("'/coverimage%'", self._case_for("coverImageLargeUrl"))
        self.assertIn("'/coverimage%'", self._case_for("coverImageRegularUrl"))
        self.assertNotIn("'/bigcovers%'", self._case_for("coverImageRegularUrl"))

    def test_highres_nulls_everything_that_is_not_a_dam_path(self):
        case = self._case_for("coverImageHighResolutionUrl")
        self.assertRegex(case, r"ELSE\s+NULL", "coverImageHighRes must null "
                         "non-DAM values — it has no legacy URL form")
        self.assertNotIn("'/coverimage%'", case)
        self.assertNotIn("'/bigcovers%'", case)

    def test_large_and_regular_pass_unmatched_values_through(self):
        # ELSE returns the branch-pick COALESCE, not NULL: TBD/N-A sentinels
        # and absolute URLs are kept as-is per the keep-source-data decision.
        for col in ("coverImageLargeUrl", "coverImageRegularUrl"):
            case = self._case_for(col)
            m = re.search(r"ELSE\s+(COALESCE\([^)]*\))", case)
            self.assertIsNotNone(m, f"{col} ELSE must pass through the "
                                 f"branch-pick value, not literal NULL")


class TestOrgDimension(unittest.TestCase):
    def test_no_literal_org_map_remains(self):
        code_only = _strip_sql_comments(SQL)
        self.assertNotRegex(
            code_only, r"WHEN\s+\d+\s+THEN\s+'[A-Z0-9]{3}'",
            "a literal ORGANIZATION_ID -> code CASE branch is back in the SQL; "
            "organizationCode comes from the org dimension (ADR-0005 item 3)")
        self.assertNotRegex(
            code_only, r"ORGANIZATION_ID\s+AS\s+BIGINT\)\s+IN\s*\(",
            "a hard-coded org-id IN-list is back in the SQL; UK membership is "
            "ORGANIZATION_CODE LIKE '01%' from the org dimension")

    def test_org_groups_block_is_gone_from_the_manifest(self):
        self.assertNotIn(
            "org_groups", MANIFEST,
            "org_groups was retired with the literal map — if something needs "
            "it again, say what in the manifest comment")

    def test_org_dimension_is_a_declared_source_keyed_on_org_only(self):
        by_alias = {s["alias"]: s for s in MANIFEST["sources"]}
        self.assertIn("org_params", by_alias,
                      "the org dimension must be a declared source, not an "
                      "undeclared table read")
        org = by_alias["org_params"]
        self.assertEqual(org["silver_table"],
                         "uif_echo_{env}_silver_mdm.mtl_parameters")
        # A dimension has no INVENTORY_ITEM_ID: the org half of the PK is the
        # only mapping it can make, and it must fan out to every row for the
        # org rather than being narrowed to look cheaper.
        self.assertEqual(set(org["key_mapping"]), {"organizationId"},
                         "org_params must bind exactly the organizationId "
                         "subset of the Gold PK")
        self.assertFalse(org.get("is_source_of_truth"),
                         "the org dimension is not the source of truth — "
                         "reconcile would treat its orgs as the item universe")

    def test_organization_code_lineage_points_at_the_dimension(self):
        ent = MANIFEST["canonical_lineage"]["organizationCode"]
        self.assertEqual(ent["pdh_table"], "MTL_PARAMETERS")
        self.assertEqual(ent["pdh_column"], "ORGANIZATION_CODE")

    def test_sql_reads_the_dimension_and_derives_uk_by_code_prefix(self):
        code_only = _strip_sql_comments(SQL)
        self.assertRegex(code_only, r"FROM\s+org_params\b",
                         "the SQL must read the registered org_params alias")
        self.assertRegex(code_only, r"orgs\.organizationCode\s+AS\s+organizationCode",
                         "organizationCode must come from the org dimension join")
        # Both proliferation branches (titleStatusCode, regionalPublicationDate)
        # must use the code-prefix rule — one left behind as an id list would
        # silently disagree with the other.
        # 4 = the two proliferation branches inside named_struct PLUS the
        # same two expressions re-evaluated in the all-null gate that wraps
        # the struct (2026-08-07). If this drifts, check both places agree.
        self.assertEqual(
            len(re.findall(r"orgs\.organizationCode\s+LIKE\s+'01%'", code_only)), 4,
            "expected the '01' UK-prefix test in both proliferation branches "
            "and both gate conditions")


class TestManifestPipelineSourceParity(unittest.TestCase):
    """The source list is split across two files and the transform joins them
    by alias (generic_gold_transform, `manifest_sources.get(alias, {})`).
    Neither half validates the other, and each way of getting it wrong fails
    late, in Glue:
      * in the pipeline config only  -> no key_mapping, validate_key_mappings
                                        refuses the run (ADR-0003 D1);
      * in the manifest only         -> never registered as a temp view, the
                                        mapping SQL dies at
                                        TABLE_OR_VIEW_NOT_FOUND.
    Adding the org dimension was the first source added since the migration and
    made this gap live, so it is checked here rather than discovered again.
    """

    def test_alias_sets_match(self):
        self.assertEqual({s["alias"] for s in MANIFEST["sources"]},
                         {s["alias"] for s in PIPELINE["sources"]})

    def test_silver_tables_and_watermarks_agree_per_alias(self):
        manifest_by_alias = {s["alias"]: s for s in MANIFEST["sources"]}
        for src in PIPELINE["sources"]:
            m = manifest_by_alias[src["alias"]]
            self.assertEqual(src["silver_table"], m["silver_table"],
                             f"{src['alias']}: silver_table disagrees")
            self.assertEqual(src.get("watermark_col"), m.get("watermark_col"),
                             f"{src['alias']}: watermark_col disagrees")

    def test_source_of_truth_agrees(self):
        def sot(srcs):
            return {s["alias"] for s in srcs if s.get("is_source_of_truth")}
        self.assertEqual(sot(MANIFEST["sources"]), sot(PIPELINE["sources"]))
        self.assertEqual(sot(PIPELINE["sources"]), {"items"})

    def test_pipeline_pk_matches_manifest_pk(self):
        self.assertEqual(PIPELINE["target"]["primary_key_cols"],
                         MANIFEST["target"]["primary_key_cols"])


class TestCategories(unittest.TestCase):
    """The categories column (2026-08-31). Four business decisions and one
    engine invariant live here; each has a silent failure mode, which is why
    each gets a test rather than a comment."""

    IN_SCOPE_SETS = ["EPT", "POA", "Private Pay (POA)"]

    def test_scope_is_the_three_agreed_sets(self):
        # Business scope 2026-08-31. Adding a set is a deliberate edit to this
        # list AND to the SQL — not something that drifts in from a rebuild.
        code = _strip_sql_comments(SQL)
        # Extract from the line, not with a bracket-matching regex: one of the
        # set names ("Private Pay (POA)") contains parentheses of its own.
        line = next((l for l in code.splitlines()
                     if "CATEGORY_SET_NAME IN (" in l), None)
        self.assertIsNotNone(line, "no CATEGORY_SET_NAME allow-list in the SQL")
        self.assertEqual(re.findall(r"'([^']+)'", line), self.IN_SCOPE_SETS)

    def test_scope_is_declared_in_exactly_one_place_in_the_sql(self):
        # One allow-list, one edit point. Two copies drift.
        code = _strip_sql_comments(SQL)
        self.assertEqual(code.count("CATEGORY_SET_NAME IN ("), 1)

    def test_assignment_source_binds_item_only_not_the_full_pk(self):
        # THE invariant. mtl_item_categories carries both PK columns, so
        # binding both is syntactically valid and looks more precise — but
        # categories are read from the master org and applied to every org row,
        # so a changed master-org assignment invalidates ALL org rows for that
        # item. Binding both would rebuild only the master-org row and leave
        # the rest serving a stale category list, permanently and silently.
        src = next(s for s in MANIFEST["sources"]
                   if s["alias"] == "item_categories")
        self.assertEqual(set(src["key_mapping"]), {"inventoryItemId"})

    def test_taxonomy_dimensions_are_lookup_only_without_key_mappings(self):
        dims = {"category_sets", "category_valid_cats",
                "categories_b", "categories_tl"}
        for alias in dims:
            src = next(s for s in MANIFEST["sources"] if s["alias"] == alias)
            self.assertTrue(src.get("lookup_only"), f"{alias}: lookup_only")
            self.assertIsNone(src.get("key_mapping"),
                              f"{alias}: a lookup dimension must not fake one")
        # and the engine accepts the combination
        gold_cdc.validate_key_mappings(MANIFEST["sources"],
                                       MANIFEST["target"]["primary_key_cols"])

    def test_master_org_is_a_rule_not_the_literal_82(self):
        # 82 is the DEV master org id. Hard-coding it ships a dev constant to
        # UAT/prod; the portable form is the self-master row in MTL_PARAMETERS,
        # the same move that retired the literal UK org id list.
        code = _strip_sql_comments(SQL)
        self.assertIn("MASTER_ORGANIZATION_ID", code)
        self.assertNotRegex(code, r"ORGANIZATION_ID\s*=\s*82\b")

    def test_hierarchy_walk_is_bounded_and_uses_concat_ws(self):
        # Spark has no recursive CTE: the walk is a fixed chain. concat_ws is
        # load-bearing (it skips nulls, so one expression serves every depth)
        # and nullif turns the all-null case back into NULL rather than ''.
        # Six levels is measured, not guessed: max depth in dev is 3 across
        # the three in-scope sets, with zero categories at depth 4-7.
        code = _strip_sql_comments(SQL)
        hops = len(re.findall(r"LEFT JOIN cat_edge e\d", code))
        self.assertEqual(hops, 5, "six-level walk = the mapped row + 5 hops")
        self.assertIn("nullif(concat_ws(' > '", code)

    def test_hierarchy_separator_is_not_a_slash(self):
        # Measured 2026-08-31: 106 of the 2,944 in-scope categories (3.6%)
        # carry a '/' INSIDE their own US label — "DC / AC Circuits",
        # "CAD / CAM / CIM", "ESL / EAL". A slash separator therefore renders
        # paths that lie about their depth ("Business & Economics / Marketing /
        # Public Relations / Marketing" is THREE levels, four parts) and cannot
        # be split by a consumer. '>' appears in none of the 132,048 US labels
        # in the catalog. This test exists because the slash is what the
        # business writes, so it is the "obvious" thing to restore.
        code = _strip_sql_comments(SQL)
        self.assertNotIn("concat_ws(' / '", code,
                         "the hierarchy separator must not be a slash")
        sep = MASTER["properties"]["categories"]["items"]["properties"]
        self.assertIn("' > '", sep["categoryHierarchyPath"]["description"])
        for row in FIXTURE:
            for cat in row.get("categories") or []:
                if cat["categoryHierarchyPath"] and " > " not in cat["categoryHierarchyPath"]:
                    # single-level path: hierarchy IS the mapped category
                    self.assertEqual(cat["categoryHierarchyPath"], cat["categoryName"])

    def test_sort_tuple_is_provably_unique(self):
        self.assertEqual(MANIFEST["array_sort_keys"]["categories"],
                         ["categorySetName", "categoryId"])
        item_props = set(MASTER["properties"]["categories"]["items"]["properties"])
        for k in MANIFEST["array_sort_keys"]["categories"]:
            self.assertIn(k, item_props)

    def test_struct_field_order_matches_the_sql_named_struct(self):
        # column_types drives the auto-DDL when the maintenance diff adds the
        # column; the named_struct drives a rebuild's CTAS. Disagree on ORDER
        # and the two paths produce different schemas for the same column.
        declared = re.findall(r"(\w+):\s*(?:STRING|BIGINT|INT|DATE|BOOLEAN)",
                              MANIFEST["column_types"]["categories"])
        code = _strip_sql_comments(SQL)
        # Anchor on a field unique to THIS struct — contributors and
        # marketingCopy use the same "'p', named_struct(" wrapper shape.
        block = code[code.index("'categorySetName',"):]
        block = block[:block.index("))))")]
        in_sql = re.findall(r"'(\w+)',", block)
        self.assertEqual(declared, in_sql)
        self.assertEqual(
            declared,
            list(MASTER["properties"]["categories"]["items"]["properties"]))


class TestComplexColumnFieldOrder(unittest.TestCase):
    """column_types drives the auto-DDL; master.json drives the contract; the
    SQL's named_struct drives a rebuild's CTAS. Field ORDER must agree across
    all three for EVERY complex column, or a maintenance-added column and a
    rebuilt one carry different shapes (the categories lesson, generalized
    2026-09-07 — UIFPE-269 review)."""

    @staticmethod
    def _declared_fields(spark_type: str) -> list:
        # top-level fields of a STRUCT<...> or ARRAY<STRUCT<...>> declaration,
        # ignoring nested angle brackets
        inner = spark_type.strip()
        inner = inner[inner.index("STRUCT<") + len("STRUCT<"):]
        depth, buf, out = 0, "", []
        for ch in inner:
            if ch in "<(":
                depth += 1
            elif ch in ">)":
                if depth == 0:
                    break
                depth -= 1
            if ch == "," and depth == 0:
                out.append(buf.split(":")[0].strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(buf.split(":")[0].strip())
        return out

    def test_column_types_field_order_matches_master_for_every_struct(self):
        for name, spark_type in MANIFEST["column_types"].items():
            if "STRUCT<" not in spark_type:
                continue
            prop = MASTER["properties"][name]
            container = prop["properties"] if "object" in prop["type"] \
                else prop["items"]["properties"]
            self.assertEqual(self._declared_fields(spark_type), list(container),
                             f"{name}: column_types vs master field order")

    def test_sql_named_struct_order_matches_master_for_the_new_arrays(self):
        code = _strip_sql_comments(SQL)
        for name, anchor in (("enablingTechnology", "'enablingTechnologyLevel1',"),
                             ("webConsumingSystem", "'wcsName',"),
                             ("standardComponents", "'sequenceNumber',"),
                             ("customComponents", "'originalWorkPercent',")):
            fields = list(MASTER["properties"][name]["items"]["properties"])
            block = code[code.index(anchor) - 200:]
            block = block[block.index("'p', named_struct(") + len("'p', named_struct("):]
            block = block[:block.index(")))")]
            in_sql = re.findall(r"'(\w+)',", block)
            self.assertEqual(in_sql, fields, f"{name}: SQL named_struct order")
        # the array INSIDE the struct
        fields = list(MASTER["properties"]["fulfillmentOrgDetails"]["properties"]
                      ["marketingInitiative"]["items"]["properties"])
        block = code[code.index("'marketingInitiativeName', C_EXT_ATTR1") - 60:]
        block = block[block.index("'p', named_struct(") + len("'p', named_struct("):]
        block = block[:block.index(")))")]
        self.assertEqual(re.findall(r"'(\w+)',", block), fields)


class TestBomComponents(unittest.TestCase):
    """UIFPE-269: the ids and names the BOM CTEs are pinned by — EGO_ATTR_GROUPS_V
    is not in Silver, so the UDA group ids stand for their names."""

    def test_uda_groups_and_structure_types_are_the_agreed_ones(self):
        code = _strip_sql_comments(SQL)
        self.assertRegex(code, r"bom_uda_std AS \([\s\S]*?ATTR_GROUP_ID = 1184")
        self.assertRegex(code, r"bom_uda_cust AS \([\s\S]*?ATTR_GROUP_ID = 1185")
        self.assertIn("IN ('Pearson Standard', 'PEARSON_CUSTOM')", code)
        self.assertIn("ALTERNATE_BOM_DESIGNATOR IS NULL", code)
        self.assertIn("COALESCE(b.COMMON_BILL_SEQUENCE_ID, b.BILL_SEQUENCE_ID)", code)
        mapping = json.loads((SCHEMAS / "mapping/product/v1/product.mapping.json")
                             .read_text(encoding="utf-8"))
        for col in mapping["columns"]:
            if col["goldColumnName"] in ("standardComponents", "customComponents"):
                blk = col["derivation"]["sql"]
                self.assertIn("IN ('Pearson Standard', 'PEARSON_CUSTOM')", blk)
                self.assertIn("COMMON_BILL_SEQUENCE_ID", blk)
                group = "1184" if col["goldColumnName"] == "standardComponents" else "1185"
                self.assertIn(f"ATTR_GROUP_ID = {group}", blk)

    def test_bom_sources_are_keyed_and_lookup_only_as_designed(self):
        by_alias = {s["alias"]: s for s in MANIFEST["sources"]}
        self.assertEqual(set(by_alias["bom_bills"]["key_mapping"]), {"inventoryItemId"})
        for alias in ("bom_components", "bom_components_ext", "bom_structure_types"):
            self.assertTrue(by_alias[alias].get("lookup_only"), alias)
        # categories joined the list in the same PR (review finding 5): its
        # four taxonomy sources are lookup_only, so a RELABEL moves no keyed
        # row and nothing carried it until the recompute leg did.
        self.assertEqual(
            PIPELINE["schedule"]["recompute"]["undetectable_columns"],
            ["standardComponents", "customComponents", "categories"])

    def test_alternate_designator_is_excluded_from_sentinel_cleaning(self):
        self.assertIn("bom_bills.ALTERNATE_BOM_DESIGNATOR",
                      MANIFEST["null_sentinels"]["exclude_columns"])


class TestFixtureConformance(unittest.TestCase):
    def test_fixture_rows_validate_against_master(self):
        import jsonschema
        validator = jsonschema.Draft7Validator(MASTER)
        for i, row in enumerate(FIXTURE):
            errs = list(validator.iter_errors(row))
            self.assertFalse(errs, f"fixture row {i}: {[e.message for e in errs]}")


if __name__ == "__main__":
    unittest.main()
