from pathlib import Path

from dynamic_mapping.config_loader import load_config, parse_config
from dynamic_mapping.config_validator import (
    check_expression,
    collect_references,
    validate_expressions,
    validate_references,
    validate_structure,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "src" / "schemas" / "examples" / "customer_orders.json"


def example_config():
    return load_config(str(EXAMPLE))


# ── expression checks (the sqlglot allowlist gate) ──────────────────────────

def test_allowed_expressions_pass():
    for expr in [
        "concat_ws(' ', customer.first_name, customer.last_name)",
        "split(customer.full_address, ',')[0]",
        "trim(split(customer.full_address, ',')[1])",
        "orders.order_total + orders.tax",
        "order_id IS NOT NULL",
        "gross_amount > 0",
        "coalesce(a.x, 'unknown')",
        "cast(orders.qty as int)",
        "case when a.x > 0 then 'pos' else 'neg' end",
    ]:
        assert check_expression(expr) == [], expr


def test_statement_rejected():
    assert any("statement" in p or "parseable" in p
               for p in check_expression("DROP TABLE customers"))


def test_statement_smuggled_via_semicolon_rejected():
    assert check_expression("1; DROP TABLE customers") != []


def test_union_rejected():
    assert any("set operation" in p or "subquer" in p
               for p in check_expression("a UNION SELECT b"))


def test_subquery_rejected():
    problems = check_expression("coalesce(x, (SELECT secret FROM t))")
    assert any("subquer" in p for p in problems)


def test_non_allowlisted_function_rejected():
    problems = check_expression("reflect('java.lang.System', 'getenv')")
    assert any("allowlist" in p for p in problems)


def test_to_json_allowlisted_for_array_serialization():
    # UIFPE-237: sqlglot parses to_json as JSONFormat (canonical name
    # json_format); _func_names' spark re-render branch is what matches the
    # "to_json" allowlist entry — pinned here under the PINNED sqlglot version
    # so a canonicalization change in an upgrade fails loudly.
    for expr in [
        "to_json(price.priceSpecifications)",
        "coalesce(to_json(price.priceSpecifications), '[]')",
    ]:
        assert check_expression(expr) == [], expr


def test_from_json_still_rejected():
    # deserialization stays off the allowlist — to_json's inverse parses as
    # exp.Anonymous("from_json") and must not ride in on the UIFPE-237 entry
    assert any("allowlist" in p
               for p in check_expression("from_json(x, 'array<int>')"))


def test_presto_json_format_spelling_rejected():
    # sqlglot canonicalizes Presto's json_format() to the SAME node as
    # Spark's to_json(), so the allowlist alone would admit a spelling Spark
    # cannot execute — the written form is rejected explicitly
    problems = check_expression("json_format(price.priceSpecifications)")
    assert any("to_json" in p for p in problems)


def test_multiple_top_level_expressions_rejected():
    assert any("single expression" in p for p in check_expression("a, b"))


# ── structure validation ─────────────────────────────────────────────────────

def test_example_config_is_clean():
    config = example_config()
    assert validate_structure(config) == []
    assert validate_expressions(config) == []


def base_raw():
    return {
        "version": "1.0",
        "sources": [{"name": "a", "path": "/x", "format": "parquet"}],
        "mappings": [{"type": "one_to_one", "source": "a.c1", "target": "c1"}],
        "output": {"targets": [{"format": "json", "path": "/out"}]},
    }


def test_duplicate_source_names_flagged():
    raw = base_raw()
    raw["sources"].append({"name": "a", "path": "/y", "format": "json"})
    raw["joins"] = [{"left": "a", "right": "a", "condition": "a.id = a.id"}]
    issues = validate_structure(parse_config(raw))
    assert any("duplicate source" in i.message for i in issues)


def test_join_referencing_unknown_source_flagged():
    raw = base_raw()
    raw["joins"] = [{"left": "a", "right": "ghost", "condition": "a.id = ghost.id"}]
    issues = validate_structure(parse_config(raw))
    assert any("unknown right source 'ghost'" in i.message for i in issues)


# ── join chain (UIFPE-167: joins fold in declaration order) ─────────────────

def _chain_raw(sources, joins):
    raw = base_raw()
    raw["sources"] = [{"name": n, "path": f"/{n}", "format": "parquet"} for n in sources]
    raw["joins"] = joins
    return raw


def test_cross_join_without_condition_validates_clean():
    cfg = parse_config(_chain_raw(["a", "b"], [{"left": "a", "right": "b", "type": "cross"}]))
    assert validate_structure(cfg) == []
    assert validate_expressions(cfg) == []


def test_cross_join_with_condition_flagged():
    cfg = parse_config(_chain_raw(["a", "b"], [{"left": "a", "right": "b", "type": "cross",
                                                "condition": "a.id = b.id"}]))
    assert any("cross join takes no condition" in i.message for i in validate_structure(cfg))


def test_non_cross_join_without_condition_flagged():
    # reachable for the first time now that the loader no longer KeyErrors
    cfg = parse_config(_chain_raw(["a", "b"], [{"left": "a", "right": "b", "type": "inner"}]))
    assert any("join condition is required" in i.message for i in validate_structure(cfg))


def test_chained_join_onto_prior_alias_is_clean():
    # c attaches to b, not to the driving source — left names b, which is
    # already in the joined frame when joins[1] folds
    cfg = parse_config(_chain_raw(["a", "b", "c"], [
        {"left": "a", "right": "b", "type": "left", "condition": "a.id = b.a_id"},
        {"left": "b", "right": "c", "type": "left", "condition": "b.id = c.b_id"},
    ]))
    assert validate_structure(cfg) == []


def test_join_left_not_yet_joined_flagged():
    cfg = parse_config(_chain_raw(["a", "b", "c"], [
        {"left": "a", "right": "b", "type": "left", "condition": "a.id = b.a_id"},
        {"left": "c", "right": "a", "type": "left", "condition": "c.id = a.c_id"},
    ]))
    messages = [i.message for i in validate_structure(cfg)]
    assert any("left source 'c' is not joined yet" in m for m in messages)
    assert any("source 'a' is already joined" in m for m in messages)


def test_condition_referencing_source_before_it_is_joined_flagged():
    # joins[0]'s condition mentions c, which only arrives at joins[1] — Spark
    # would throw AnalysisException inside apply_joins after the scan
    cfg = parse_config(_chain_raw(["a", "b", "c"], [
        {"left": "a", "right": "b", "type": "left", "condition": "a.id = b.a_id AND c.x = b.y"},
        {"left": "a", "right": "c", "type": "left", "condition": "a.id = c.a_id"},
    ]))
    issues = validate_structure(cfg)
    assert any(i.where == "joins[0].condition" and "before it is joined" in i.message
               for i in issues)


def test_source_never_joined_flagged():
    cfg = parse_config(_chain_raw(["a", "b", "c"], [
        {"left": "a", "right": "b", "type": "left", "condition": "a.id = b.a_id"},
    ]))
    assert any("source(s) c are never joined" in i.message for i in validate_structure(cfg))


def test_two_sources_without_joins_flagged():
    cfg = parse_config(_chain_raw(["a", "b"], []))
    assert any(i.where == "joins" and "none declared" in i.message
               for i in validate_structure(cfg))


def test_unknown_join_source_does_not_double_report_chain():
    # an unknown right name is reported once; the chain pass stays quiet
    cfg = parse_config(_chain_raw(["a"], [{"left": "a", "right": "ghost",
                                           "condition": "a.id = ghost.id"}]))
    issues = validate_structure(cfg)
    assert [i.message for i in issues if "ghost" in i.message] == ["unknown right source 'ghost'"]


def test_duplicate_target_flagged():
    raw = base_raw()
    raw["mappings"].append({"type": "literal", "target": "c1", "value": 1})
    issues = validate_structure(parse_config(raw))
    assert any("duplicate target" in i.message for i in issues)


def test_one_to_many_length_mismatch_flagged():
    raw = base_raw()
    raw["mappings"] = [{
        "type": "one_to_many", "source": "a.c1",
        "targets": ["x", "y"], "expressions": ["split(a.c1, '-')[0]"],
    }]
    issues = validate_structure(parse_config(raw))
    assert any("same length" in i.message for i in issues)


def test_validations_require_rejected_path():
    raw = base_raw()
    raw["validations"] = [{"name": "v", "condition": "c1 IS NOT NULL"}]
    issues = validate_structure(parse_config(raw))
    assert any(i.where == "rejected_path" for i in issues)


def test_missing_output_flagged():
    raw = base_raw()
    raw["output"] = {"targets": []}
    issues = validate_structure(parse_config(raw))
    assert any(i.where == "output.targets" for i in issues)


# ── reference validation against schemas ────────────────────────────────────

SCHEMAS = {
    "customer": ["customer_id", "first_name", "last_name", "full_address"],
    "orders": ["order_id", "customer_id", "order_total", "tax"],
}


def test_example_references_resolve():
    assert validate_references(example_config(), SCHEMAS) == []


def test_column_typo_caught_before_spark():
    raw = base_raw()
    raw["sources"] = [{"name": "customer", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "custmer.first_name", "target": "fn"}]
    issues = validate_references(parse_config(raw), SCHEMAS)
    assert any("unknown source 'custmer'" in i.message for i in issues)


def test_unknown_column_caught():
    raw = base_raw()
    raw["sources"] = [{"name": "customer", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "customer.middle_name", "target": "mn"}]
    issues = validate_references(parse_config(raw), SCHEMAS)
    assert any("column 'middle_name' not found" in i.message for i in issues)


def test_validation_on_unmapped_column_caught():
    raw = base_raw()
    raw["sources"] = [{"name": "customer", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "customer.first_name", "target": "fn"}]
    raw["validations"] = [{"name": "v", "condition": "ghost_column IS NOT NULL"}]
    raw["rejected_path"] = "/rej"
    issues = validate_references(parse_config(raw), SCHEMAS)
    assert any("not produced by any mapping rule" in i.message for i in issues)


# ── case-insensitive resolution — Spark's resolver rules (UIFPE-165) ────────
# Gold Iceberg schemas are camelCase, but the Glue catalog serves authors
# LOWERCASED names, so published configs legitimately carry either casing.

GOLD_SCHEMA = {"product": ["ppid", "isbn13", "organizationCode", "itemType"]}


def _product_one_to_one(ref):
    raw = base_raw()
    raw["sources"] = [{"name": "product", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "source": ref, "target": "out"}]
    return parse_config(raw)


def test_lowercased_ref_resolves_against_camelcase_schema():
    assert validate_references(_product_one_to_one("product.organizationcode"), GOLD_SCHEMA) == []


def test_exact_case_ref_still_resolves():
    assert validate_references(_product_one_to_one("product.organizationCode"), GOLD_SCHEMA) == []


def test_real_typo_still_caught():
    issues = validate_references(_product_one_to_one("product.organizationcodes"), GOLD_SCHEMA)
    assert any("column 'organizationcodes' not found" in i.message for i in issues)


def test_case_ambiguous_columns_rejected():
    # two schema columns differing only by case: Spark's case-insensitive
    # resolver would throw at execution, so the validator rejects it up front
    schema = {"product": ["itemtype", "itemType"]}
    issues = validate_references(_product_one_to_one("product.ITEMTYPE"), schema)
    assert any("ambiguous" in i.message for i in issues)


# ── UIFPE-567: members of struct / array-of-struct columns ───────────────────
# Written alias.column.member; sqlglot parses that as db.table.column, which
# used to put the struct name in the source slot ("unknown source
# 'fulfillment_org_details'") in BOTH the reference check and the column
# pruning — the wizard's own Filter-step hint recommends exactly this syntax.

STRUCT_SCHEMA = {"product": ["product_id", "fulfillment_org_details", "standard_components"],
                 "price": ["product_id", "price_specifications"]}


def _filtered(*filters, joins=()):
    raw = base_raw()
    raw["sources"] = [{"name": "product", "path": "/p", "format": "iceberg"},
                      {"name": "price", "path": "/q", "format": "iceberg"}]
    raw["joins"] = list(joins) or [{"left": "product", "right": "price", "type": "left",
                                    "condition": "product.product_id = price.product_id"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "product.product_id", "target": "id"}]
    raw["filters"] = list(filters)
    return parse_config(raw)


def test_struct_member_reference_validates():
    cfg = _filtered("product.fulfillment_org_details.discount_code = 'X'")
    assert validate_references(cfg, STRUCT_SCHEMA) == []


def test_array_of_struct_member_reference_validates():
    cfg = _filtered("size(filter(price.price_specifications, x -> x.currency_code = 'GBP')) > 0")
    assert validate_references(cfg, STRUCT_SCHEMA) == []


def test_struct_member_keeps_the_struct_column_for_pruning():
    # the run projects each source down to collect_references — without the
    # struct column kept, the filter would fail at run time on a pruned frame
    cfg = _filtered("product.fulfillment_org_details.discount_code = 'X'")
    assert collect_references(cfg, STRUCT_SCHEMA)["product"] == ["product_id", "fulfillment_org_details"]


def test_unknown_alias_in_a_dotted_path_is_still_refused():
    issues = validate_references(_filtered("ghost.fulfillment_org_details.discount_code = 'X'"), STRUCT_SCHEMA)
    assert any("unknown source 'ghost'" in i.message for i in issues)


def test_unknown_struct_column_is_still_refused():
    issues = validate_references(_filtered("product.no_such_struct.discount_code = 'X'"), STRUCT_SCHEMA)
    assert any("column 'no_such_struct' not found in source 'product'" in i.message for i in issues)


def test_plain_two_part_references_are_unchanged():
    issues = validate_references(_filtered("product.nope = 1"), STRUCT_SCHEMA)
    assert any("column 'nope' not found" in i.message for i in issues)
    assert validate_references(_filtered("product.product_id = 'A'"), STRUCT_SCHEMA) == []


# ── UIFPE-568: pre-rename camelCase references name the snake_case column ────

SNAKE_SCHEMA = {"product": ["product_id", "item_type", "line_of_business", "isbn13"]}


def test_camelcase_ref_is_refused_but_names_the_renamed_column():
    # Spark would not resolve itemType against item_type, so the reference
    # stays refused — the message just stops making the author hunt for it
    issues = validate_references(_product_one_to_one("product.itemType"), SNAKE_SCHEMA)
    assert len(issues) == 1
    assert "did you mean 'item_type'" in issues[0].message
    assert "snake_case" in issues[0].message


def test_camelcase_ref_in_a_filter_names_the_renamed_column():
    raw = base_raw()
    raw["sources"] = [{"name": "product", "path": "/x", "format": "iceberg"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "product.product_id", "target": "id"}]
    raw["filters"] = ["product.lineOfBusiness = 'Schools'"]
    issues = validate_references(parse_config(raw), SNAKE_SCHEMA)
    assert [i.where for i in issues] == ["filters[0]"]
    assert "did you mean 'line_of_business'" in issues[0].message


def test_a_real_typo_keeps_the_column_list_message():
    issues = validate_references(_product_one_to_one("product.itemtypes"), SNAKE_SCHEMA)
    assert "did you mean" not in issues[0].message
    assert "(has: " in issues[0].message


def test_expression_ref_resolves_case_insensitively():
    raw = base_raw()
    raw["sources"] = [{"name": "product", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "expression": "upper(product.itemtype)",
                        "target": "it"}]
    assert validate_references(parse_config(raw), GOLD_SCHEMA) == []


def test_validation_ref_matches_target_case_insensitively():
    raw = base_raw()
    raw["sources"] = [{"name": "product", "path": "/x", "format": "parquet"}]
    raw["mappings"] = [{"type": "one_to_one", "source": "product.ppid", "target": "orgCode"}]
    raw["validations"] = [{"name": "v", "condition": "orgcode IS NOT NULL"}]
    raw["rejected_path"] = "/rej"
    assert validate_references(parse_config(raw), GOLD_SCHEMA) == []


# ── boolean connectors are operators, not functions (regression) ────────────

def test_boolean_connectors_allowed():
    for expr in [
        "isbn13 IS NULL OR length(isbn13) = 13",
        "a > 0 AND b < 10",
        "NOT (status = 'retired') OR override = true",
        "a = 1 OR b = 2 AND c = 3",
    ]:
        assert check_expression(expr) == [], expr


def test_connector_does_not_bypass_function_allowlist():
    problems = check_expression("a = 1 OR reflect('java.lang.System', 'getenv') = 'x'")
    assert any("allowlist" in p for p in problems)


# ── filters block (D18) ──────────────────────────────────────────────────────

def _config_with_filters(*filters):
    cfg = example_config()
    return parse_config({
        "version": "1.0",
        "sources": [{"name": s.name, "path": s.path, "format": s.format,
                     "options": dict(s.options)} for s in cfg.sources],
        "joins": [{"left": j.left, "right": j.right, "type": j.type,
                   "condition": j.condition} for j in cfg.joins],
        "mappings": [{"type": "one_to_one", "source": "orders.order_id",
                      "target": "order_id"}],
        "filters": list(filters),
        "output": {"targets": [{"format": "parquet", "path": "s3://b/out/"}]},
    })


def test_valid_filter_passes_all_layers():
    cfg = _config_with_filters("orders.order_total > 0 OR orders.tax > 0")
    assert validate_structure(cfg) == []
    assert validate_expressions(cfg) == []
    schemas = {"customer": ["customer_id", "first_name", "last_name", "full_address"],
               "orders": ["order_id", "customer_id", "order_total", "tax"]}
    assert validate_references(cfg, schemas) == []


def test_empty_filter_string_rejected():
    cfg = _config_with_filters("  ")
    assert any(i.where == "filters[0]" for i in validate_structure(cfg))


def test_filter_with_forbidden_function_rejected():
    cfg = _config_with_filters("reflect('java.lang.Runtime') = 'x'")
    assert any("allowlist" in i.message for i in validate_expressions(cfg))


def test_filter_unknown_column_rejected():
    cfg = _config_with_filters("orders.ghost_column = 1")
    schemas = {"customer": ["customer_id"], "orders": ["order_id"]}
    issues = validate_references(cfg, schemas)
    assert any("ghost_column" in i.message for i in issues)


# ── collect_references (UIFPE-174: the projection the engine prunes to) ─────

def test_collect_references_covers_joins_filters_and_mappings():
    from dynamic_mapping.config_validator import collect_references
    cfg = example_config()
    schemas = {"customer": ["customer_id", "first_name", "last_name",
                            "full_address", "loyalty_tier"],
               "orders": ["order_id", "customer_id", "order_total", "tax",
                          "internal_notes"]}
    refs = collect_references(cfg, schemas)
    # join keys, mapping sources, and expression columns are all in;
    # columns nothing references are out
    assert refs["customer"] == ["customer_id", "first_name", "last_name",
                                "full_address"]
    assert refs["orders"] == ["order_id", "customer_id", "order_total", "tax"]


def test_collect_references_resolves_true_case_names():
    from dynamic_mapping.config_validator import collect_references
    cfg = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "s3://b/p/", "format": "parquet"}],
        "mappings": [{"type": "one_to_one", "source": "product.organizationcode",
                      "target": "org"}],
        "output": {"targets": [{"format": "parquet", "path": "s3://b/out/"}]},
    })
    refs = collect_references(cfg, {"product": ["organizationCode", "ppid"]})
    # config says organizationcode; the projection must use the schema's name
    assert refs["product"] == ["organizationCode"]


def test_collect_references_keeps_unqualified_column_on_every_owner():
    from dynamic_mapping.config_validator import collect_references
    cfg = parse_config({
        "version": "1.0",
        "sources": [{"name": "a", "path": "s3://b/a/", "format": "parquet"},
                    {"name": "b", "path": "s3://b/b/", "format": "parquet"}],
        "joins": [{"left": "a", "right": "b", "type": "inner",
                   "condition": "a.id = b.id"}],
        "mappings": [{"type": "derived", "target": "t",
                      "expression": "shared_col + 1"}],
        "output": {"targets": [{"format": "parquet", "path": "s3://b/out/"}]},
    })
    refs = collect_references(cfg, {"a": ["id", "shared_col", "a_only"],
                                    "b": ["id", "shared_col", "b_only"]})
    # unqualified: attributable to either source, so kept on both
    assert refs["a"] == ["id", "shared_col"]
    assert refs["b"] == ["id", "shared_col"]


def test_collect_references_excludes_validation_columns():
    from dynamic_mapping.config_validator import collect_references
    cfg = example_config()
    schemas = {"customer": ["customer_id", "first_name", "last_name", "full_address"],
               "orders": ["order_id", "customer_id", "order_total", "tax",
                          "gross_amount"]}
    refs = collect_references(cfg, schemas)
    # "gross_amount > 0" is a validation on the MAPPED frame; the source
    # column of the same name must not be dragged into the projection by it
    assert "gross_amount" not in refs["orders"]
