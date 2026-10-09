"""UIFPE-580: joins THROUGH a list column and `unnest` steps — the config
contract, the structural rules and the reference check, all Spark-free."""
import pytest

from dynamic_mapping.config_loader import parse_config
from dynamic_mapping.config_validator import (
    collect_references,
    validate_references,
    validate_structure,
)
from dynamic_mapping.errors import ConfigError

PRODUCT = {"name": "product", "path": "/p", "format": "iceberg"}
COPY = {"name": "marketing_copy", "path": "/m", "format": "iceberg"}
PROGRAM = {"name": "program", "path": "/g", "format": "iceberg"}
PRICE = {"name": "price", "path": "/q", "format": "iceberg"}
OUT = {"targets": [{"format": "parquet", "path": "/out"}]}
ID_MAPPING = [{"type": "one_to_one", "source": "product.product_id", "target": "id"}]

SCHEMAS = {
    "product": ["product_id", "organization_code", "marketing_copy", "associated_programs"],
    "marketing_copy": ["content_id", "marketing_copy_content_text"],
    "program": ["program_id", "description"],
    "price": ["product_id", "price_specifications"],
    # element aliases, as engine.element_schemas / the API supply them
    "product_copy": ["content_id", "audience_code"],
    "product_program": ["value"],
    "price_spec": ["price_list_name", "list_price_amount"],
}


def copy_join(**over):
    j = {"left": "product", "right": "marketing_copy", "type": "left",
         "through": {"list": "product.marketing_copy", "alias": "product_copy"},
         "condition": "product_copy.content_id = marketing_copy.content_id"}
    j.update(over)
    return j


def cfg(sources, joins=(), unnest=None, mappings=ID_MAPPING, filters=()):
    raw = {"version": "1.0", "sources": sources, "joins": list(joins),
           "mappings": mappings, "filters": list(filters), "output": OUT}
    if unnest is not None:
        raw["unnest"] = unnest
    return parse_config(raw)


# ── the contract parses ──────────────────────────────────────────────────────

def test_through_and_unnest_parse_into_list_steps():
    c = cfg([PRODUCT, COPY, PRICE],
            [copy_join(), {"left": "product", "right": "price", "type": "left",
                           "condition": "product.product_id = price.product_id"}],
            unnest=[{"list": "price.price_specifications", "alias": "price_spec"}])
    assert c.joins[0].through.list == "product.marketing_copy"
    assert c.joins[0].through.alias == "product_copy"
    assert c.joins[0].through.source_column() == ("product", "marketing_copy")
    assert c.joins[1].through is None
    assert c.unnest[0].alias == "price_spec"
    assert [w for w, _ in c.list_steps()] == ["joins[0].through", "unnest[0]"]
    assert c.element_aliases() == ("product_copy", "price_spec")


def test_a_config_without_list_steps_is_unchanged():
    c = cfg([PRODUCT])
    assert c.joins == () and c.unnest == () and c.element_aliases() == ()


@pytest.mark.parametrize("through", ["product.marketing_copy", {"list": "product.marketing_copy"},
                                     {"alias": "x"}, {"list": 1, "alias": "x"}])
def test_a_malformed_through_is_a_config_error(through):
    with pytest.raises(ConfigError, match=r"joins\[0\]\.through"):
        cfg([PRODUCT, COPY], [copy_join(through=through)])


def test_a_malformed_unnest_is_a_config_error():
    with pytest.raises(ConfigError, match="unnest"):
        cfg([PRODUCT], unnest={"list": "a.b", "alias": "c"})
    with pytest.raises(ConfigError, match=r"unnest\[0\]"):
        cfg([PRODUCT], unnest=["a.b"])


# ── structure ────────────────────────────────────────────────────────────────

def test_a_well_formed_through_join_is_structurally_clean():
    assert validate_structure(cfg([PRODUCT, COPY], [copy_join()])) == []


def structure_messages(config):
    return [f"{i.where}: {i.message}" for i in validate_structure(config)]


def test_list_must_be_source_dot_column_on_a_declared_source():
    assert any("must be written source.column" in m for m in
               structure_messages(cfg([PRODUCT, COPY], [copy_join(through={"list": "marketing_copy", "alias": "x"})])))
    assert any("unknown source 'ghost'" in m for m in
               structure_messages(cfg([PRODUCT, COPY], [copy_join(through={"list": "ghost.list", "alias": "x"})])))


def test_alias_must_be_an_identifier_and_not_a_source_or_another_alias():
    bad = cfg([PRODUCT, COPY], [copy_join(through={"list": "product.marketing_copy", "alias": "product copy"})])
    assert any("not a plain identifier" in m for m in structure_messages(bad))
    clash = cfg([PRODUCT, COPY], [copy_join(through={"list": "product.marketing_copy", "alias": "product"})])
    assert any("already a source name" in m for m in structure_messages(clash))
    twice = cfg([PRODUCT, COPY], [copy_join()],
                unnest=[{"list": "marketing_copy.something", "alias": "product_copy"}])
    assert any("used by another list step" in m for m in structure_messages(twice))


def test_a_cross_join_cannot_go_through_a_list():
    msgs = structure_messages(cfg([PRODUCT, COPY], [copy_join(type="cross", condition=None)]))
    assert any("cross join cannot go through a list" in m for m in msgs)


def test_the_list_must_already_be_in_the_joined_frame():
    # product joins onto price; a through-list on marketing_copy (not yet
    # joined) cannot be spread at that point
    c = cfg([PRODUCT, COPY, PRICE],
            [{"left": "product", "right": "price", "type": "left",
              "through": {"list": "marketing_copy.something", "alias": "x"},
              "condition": "x.value = price.product_id"},
             copy_join()])
    assert any("is not joined yet — the list must already be in the frame" in m
               for m in structure_messages(c))


def test_an_element_alias_referenced_before_its_list_is_spread_is_flagged():
    # price_spec is an unnest step (after every join), so a join condition
    # cannot use it
    c = cfg([PRODUCT, PRICE],
            [{"left": "product", "right": "price", "type": "left",
              "condition": "price_spec.price_list_name = 'GBP' AND product.product_id = price.product_id"}],
            unnest=[{"list": "price.price_specifications", "alias": "price_spec"}])
    assert any("before its list is spread" in m for m in structure_messages(c))


# ── references ───────────────────────────────────────────────────────────────

def test_element_fields_validate_like_source_columns():
    c = cfg([PRODUCT, COPY, PROGRAM, PRICE],
            [copy_join(),
             {"left": "product", "right": "program", "type": "left",
              "through": {"list": "product.associated_programs", "alias": "product_program"},
              "condition": "product_program.value = program.program_id"},
             {"left": "product", "right": "price", "type": "left",
              "condition": "product.product_id = price.product_id"}],
            unnest=[{"list": "price.price_specifications", "alias": "price_spec"}],
            mappings=ID_MAPPING + [
                {"type": "one_to_one", "source": "product_copy.audience_code", "target": "audience"},
                {"type": "one_to_one", "source": "marketing_copy.marketing_copy_content_text", "target": "copy"},
                {"type": "one_to_one", "source": "program.description", "target": "program"},
                {"type": "derived", "target": "price", "expression": "price_spec.list_price_amount"}],
            filters=["product_copy.audience_code = 'STU'", "price_spec.price_list_name = 'GBP'"])
    assert validate_structure(c) == []
    assert validate_references(c, SCHEMAS) == []


def test_an_unknown_element_field_is_refused_by_name():
    c = cfg([PRODUCT, COPY], [copy_join()],
            mappings=ID_MAPPING + [{"type": "one_to_one", "source": "product_copy.nope", "target": "x"}])
    msgs = [i.message for i in validate_references(c, SCHEMAS)]
    assert any("column 'nope' not found in source 'product_copy'" in m for m in msgs)


def test_a_missing_list_column_is_refused_at_the_step():
    c = cfg([PRODUCT, COPY], [copy_join(through={"list": "product.no_such_list", "alias": "product_copy"})])
    issues = validate_references(c, SCHEMAS)
    assert any(i.where == "joins[0].through.list" and "no_such_list" in i.message for i in issues)


def test_an_element_alias_with_no_schema_says_so_instead_of_unknown_source():
    c = cfg([PRODUCT, COPY], [copy_join()])
    without_element = {k: v for k, v in SCHEMAS.items() if k != "product_copy"}
    msgs = [i.message for i in validate_references(c, without_element)]
    assert any("list element 'product_copy' has no known fields" in m for m in msgs)
    assert not any("unknown source 'product_copy'" in m for m in msgs)


def test_pruning_keeps_the_list_column_and_returns_sources_only():
    c = cfg([PRODUCT, COPY], [copy_join()],
            mappings=ID_MAPPING + [{"type": "one_to_one", "source": "product_copy.audience_code", "target": "a"}])
    refs = collect_references(c, SCHEMAS)
    assert refs["product"] == ["product_id", "marketing_copy"]   # the spread list survives
    assert refs["marketing_copy"] == ["content_id"]
    assert "product_copy" not in refs                              # element aliases own no columns
