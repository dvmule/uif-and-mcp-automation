"""UIFPE-580 on local Spark: a join THROUGH a list column spreads the list
first (outer) and then key-matches its element; `unnest` spreads a list after
the joins; filters on the element narrow rows. The row counts mirror the dev
shapes in miniature (products with 0, 1 and several matches, an id the other
table does not have). In-memory only — no writes, so no winutils guard."""
import pytest

pytest.importorskip("pyspark")

from dynamic_mapping import engine
from dynamic_mapping.config_loader import parse_config
from dynamic_mapping.config_validator import collect_references, validate_references

pytestmark = pytest.mark.spark

PLACEHOLDERS = {"batch_id": "b-1", "run_timestamp": "2026-09-24T00:00:00Z"}
OUT = {"targets": [{"format": "parquet", "path": "/out"}]}


def prepare(frames, config):
    """What main.run does before mapping: schemas (sources + list elements),
    the reference check, pruning, joins, unnest."""
    schemas = {name: df.columns for name, df in frames.items()}
    schemas.update(engine.element_schemas(frames, config))
    assert validate_references(config, schemas) == []
    pruned = engine.prune_sources(frames, collect_references(config, schemas))
    joined = engine.apply_joins(pruned, config.joins, config.source_names())
    return engine.apply_unnest(joined, config.unnest)


def mapped_rows(joined, config):
    assert engine.dry_run_issues(joined, config.mappings, PLACEHOLDERS) == []
    pairs = engine.compile_mappings(config.mappings, PLACEHOLDERS)
    return [r.asDict() for r in joined.select(*[c.alias(n) for n, c in pairs]).collect()]


@pytest.fixture()
def product_copy_frames(spark):
    product = spark.createDataFrame(
        [("PP-1", "01B", [{"content_id": "C1", "audience_code": "STU"},
                          {"content_id": "C2", "audience_code": "TEA"}]),
         ("PP-2", "01B", []),                                                  # no copies
         ("PP-3", "01E", None),                                                # null list
         ("PP-4", "01B", [{"content_id": "GONE", "audience_code": "STU"}])],   # not in the library
        "product_id string, organization_code string, "
        "marketing_copy array<struct<content_id:string,audience_code:string>>").alias("product")
    copies = spark.createDataFrame(
        [("C1", "text one"), ("C2", "text two"), ("C9", "unreferenced")],
        ["content_id", "marketing_copy_content_text"]).alias("marketing_copy")
    return {"product": product, "marketing_copy": copies}


COPY_CONFIG = {
    "version": "1.0",
    "sources": [{"name": "product", "path": "/p", "format": "iceberg"},
                {"name": "marketing_copy", "path": "/m", "format": "iceberg"}],
    "joins": [{"left": "product", "right": "marketing_copy", "type": "left",
               "through": {"list": "product.marketing_copy", "alias": "product_copy"},
               "condition": "product_copy.content_id = marketing_copy.content_id"}],
    "mappings": [
        {"type": "one_to_one", "source": "product.product_id", "target": "product_id"},
        {"type": "one_to_one", "source": "product_copy.content_id", "target": "content_id"},
        {"type": "one_to_one", "source": "product_copy.audience_code", "target": "audience"},
        {"type": "one_to_one", "source": "marketing_copy.marketing_copy_content_text", "target": "text"}],
    "output": OUT,
}


def test_through_list_of_structs_is_one_row_per_copy_and_keeps_products_without_copies(product_copy_frames):
    config = parse_config(COPY_CONFIG)
    rows = mapped_rows(prepare(product_copy_frames, config), config)
    by_product = {}
    for r in rows:
        by_product.setdefault(r["product_id"], []).append(r)
    assert len(rows) == 5
    assert sorted((r["content_id"], r["text"]) for r in by_product["PP-1"]) == [("C1", "text one"), ("C2", "text two")]
    assert by_product["PP-2"] == [{"product_id": "PP-2", "content_id": None, "audience": None, "text": None}]
    assert by_product["PP-3"] == [{"product_id": "PP-3", "content_id": None, "audience": None, "text": None}]
    # a referenced id the library lacks: the product's own element survives, the text is null
    assert by_product["PP-4"] == [{"product_id": "PP-4", "content_id": "GONE", "audience": "STU", "text": None}]


def test_filters_on_the_element_narrow_the_rows(product_copy_frames):
    config = parse_config({**COPY_CONFIG, "filters": ["product_copy.audience_code = 'STU'"]})
    joined = engine.apply_filters(prepare(product_copy_frames, config), config.filters)
    rows = mapped_rows(joined, config)
    assert sorted((r["product_id"], r["content_id"]) for r in rows) == [("PP-1", "C1"), ("PP-4", "GONE")]


def test_source_qualifiers_survive_the_spread(product_copy_frames):
    # product.organization_code is not referenced by the mapping, so pruning
    # drops it — reference it to prove qualified names still resolve AFTER the
    # explode (select("*") keeps the source alias on every column)
    config = parse_config({**COPY_CONFIG, "filters": ["product.organization_code = '01B'"]})
    joined = engine.apply_filters(prepare(product_copy_frames, config), config.filters)
    assert sorted(r["product_id"] for r in mapped_rows(joined, config)) == ["PP-1", "PP-1", "PP-2", "PP-4"]


def test_through_list_of_scalars_exposes_value(spark):
    product = spark.createDataFrame(
        [("PP-1", ["PG-1", "PG-2"]), ("PP-2", ["PG-1"]), ("PP-3", [])],
        "product_id string, associated_programs array<string>").alias("product")
    program = spark.createDataFrame(
        [("PG-1", "Maths"), ("PG-2", "Science")], ["program_id", "description"]).alias("program")
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "iceberg"},
                    {"name": "program", "path": "/g", "format": "iceberg"}],
        "joins": [{"left": "product", "right": "program", "type": "left",
                   "through": {"list": "product.associated_programs", "alias": "product_program"},
                   "condition": "product_program.value = program.program_id"}],
        "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "product_id"},
                     {"type": "one_to_one", "source": "product_program.value", "target": "program_id"},
                     {"type": "one_to_one", "source": "program.description", "target": "program"}],
        "output": OUT,
    })
    rows = mapped_rows(prepare({"product": product, "program": program}, config), config)
    assert sorted((r["product_id"], r["program_id"], r["program"]) for r in rows) == [
        ("PP-1", "PG-1", "Maths"), ("PP-1", "PG-2", "Science"), ("PP-2", "PG-1", "Maths"), ("PP-3", None, None)]


def test_unnest_after_a_key_join_is_one_row_per_price(spark):
    product = spark.createDataFrame([("PP-1",), ("PP-2",)], ["product_id"]).alias("product")
    price = spark.createDataFrame(
        [("PP-1", [{"price_list_name": "GBP", "list_price_amount": 49.99},
                   {"price_list_name": "USD", "list_price_amount": 59.99}])],
        "product_id string, price_specifications array<struct<price_list_name:string,list_price_amount:double>>",
    ).alias("price")
    base = {
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "iceberg"},
                    {"name": "price", "path": "/q", "format": "iceberg"}],
        "joins": [{"left": "product", "right": "price", "type": "left",
                   "condition": "product.product_id = price.product_id"}],
        "unnest": [{"list": "price.price_specifications", "alias": "price_spec"}],
        "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "product_id"},
                     {"type": "one_to_one", "source": "price_spec.price_list_name", "target": "price_list"},
                     {"type": "one_to_one", "source": "price_spec.list_price_amount", "target": "amount"}],
        "output": OUT,
    }
    config = parse_config(base)
    frames = {"product": product, "price": price}
    rows = mapped_rows(prepare(frames, config), config)
    assert sorted((r["product_id"], r["price_list"]) for r in rows) == [("PP-1", "GBP"), ("PP-1", "USD"), ("PP-2", None)]
    # the owner's model: a filter on the price list keeps one price per product
    narrowed = parse_config({**base, "filters": ["price_spec.price_list_name = 'GBP'"]})
    joined = engine.apply_filters(prepare(frames, narrowed), narrowed.filters)
    assert [(r["product_id"], r["amount"]) for r in mapped_rows(joined, narrowed)] == [("PP-1", 49.99)]


def test_stock_joins_on_both_keys_without_multiplying(spark):
    product = spark.createDataFrame(
        [("PP-1", "01B"), ("PP-1", "01E"), ("PP-2", "01B")], ["product_id", "organization_code"]).alias("product")
    stock = spark.createDataFrame(
        [("PP-1", "01B", 5), ("PP-1", "01E", 7), ("PP-1", "02A", 9)],
        ["product_id", "organization_code", "on_hand_quantity"]).alias("stock")
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "iceberg"},
                    {"name": "stock", "path": "/s", "format": "iceberg"}],
        "joins": [{"left": "product", "right": "stock", "type": "left",
                   "condition": "product.product_id = stock.product_id AND "
                                "product.organization_code = stock.organization_code"}],
        "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "product_id"},
                     {"type": "one_to_one", "source": "product.organization_code", "target": "org"},
                     {"type": "one_to_one", "source": "stock.on_hand_quantity", "target": "qty"}],
        "output": OUT,
    })
    rows = mapped_rows(prepare({"product": product, "stock": stock}, config), config)
    assert sorted((r["product_id"], r["org"], r["qty"]) for r in rows) == [
        ("PP-1", "01B", 5), ("PP-1", "01E", 7), ("PP-2", "01B", None)]


def test_a_non_list_column_cannot_be_spread(spark):
    from dynamic_mapping.errors import ConfigValidationError
    product = spark.createDataFrame([("PP-1", "x")], ["product_id", "description"]).alias("product")
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "iceberg"}],
        "unnest": [{"list": "product.description", "alias": "d"}],
        "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "id"}],
        "output": OUT,
    })
    with pytest.raises(ConfigValidationError, match="not a list"):
        engine.apply_unnest(product, config.unnest)
