"""Spark-backed tests. The SparkSession seam means the same engine code that
runs in Glue runs here against local in-memory frames."""
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyspark")

from dynamic_mapping import engine
from dynamic_mapping.config_loader import load_config, parse_config
from dynamic_mapping.errors import ConfigValidationError, EmptyInputError
from dynamic_mapping.main import run

pytestmark = pytest.mark.spark

EXAMPLE = Path(__file__).resolve().parents[2] / "src" / "schemas" / "examples" / "customer_orders.json"
PLACEHOLDERS = {"batch_id": "b-1", "run_timestamp": "2026-08-04T00:00:00Z"}


def local_config(tmp_path, validations=True, on_empty="fail"):
    """Example config rewired to local output paths."""
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    raw["output"]["targets"] = [
        {"format": "json", "path": (tmp_path / "out_json").as_posix(), "mode": "overwrite"},
        {"format": "csv", "path": (tmp_path / "out_csv").as_posix(), "mode": "overwrite",
         "options": {"header": "true"}},
    ]
    raw["rejected_path"] = (tmp_path / "rejected" / "${batch_id}").as_posix()
    if not validations:
        raw["validations"] = []
        raw.pop("rejected_path")
    raw["policies"] = {"on_empty_input": on_empty}
    return parse_config(raw)


def test_join_and_single_select_mapping(spark, customer_orders_frames):
    config = load_config(str(EXAMPLE))
    joined = engine.apply_joins(customer_orders_frames, config.joins, config.source_names())
    pairs = engine.compile_mappings(config.mappings, PLACEHOLDERS)
    mapped = joined.select(*[col.alias(name) for name, col in pairs])

    assert mapped.columns == [
        "order_id", "customer_name", "address_line1", "address_line2",
        "source_system", "load_ts", "gross_amount",
    ]
    rows = {r["order_id"]: r for r in mapped.collect()}
    assert rows["o-100"]["customer_name"] == "Ada Lovelace"
    assert rows["o-100"]["address_line1"] == "1 Analytical Way"
    assert rows["o-100"]["address_line2"] == "London"
    assert rows["o-100"]["gross_amount"] == 120.0
    assert rows["o-100"]["source_system"] == "crm"
    assert rows["o-100"]["load_ts"] == "2026-08-04T00:00:00Z"


def test_dry_run_names_the_broken_rule(spark, customer_orders_frames):
    config = load_config(str(EXAMPLE))
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    raw["mappings"][5]["expression"] = "orders.order_total + orders.ghost_column"
    broken = parse_config(raw)

    joined = engine.apply_joins(customer_orders_frames, config.joins, config.source_names())
    issues = engine.dry_run_issues(joined, broken.mappings, PLACEHOLDERS)
    assert len(issues) == 1
    assert issues[0].where == "mappings[5]"


def test_validation_splits_and_null_fails(spark, customer_orders_frames):
    config = load_config(str(EXAMPLE))
    joined = engine.apply_joins(customer_orders_frames, config.joins, config.source_names())
    pairs = engine.compile_mappings(config.mappings, PLACEHOLDERS)
    mapped = joined.select(*[col.alias(name) for name, col in pairs])

    valid, rejected = engine.apply_validations(mapped, config.validations)
    assert valid.count() == 2                      # o-100, o-101
    rej = {tuple(r[engine.FAILED_RULES_COL]) for r in rejected.collect()}
    # NULL order_id fails the IS NOT NULL rule; negative total fails positivity
    assert ("order_id_not_null",) in rej
    assert ("gross_amount_positive",) in rej


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("HADOOP_HOME"),
    reason="local file writes need winutils.exe (set HADOOP_HOME) on Windows",
)
def test_end_to_end_run(spark, customer_orders_frames, tmp_path):
    config = local_config(tmp_path)
    report = run(spark, config, batch_id="b-1", frames=customer_orders_frames)

    assert report.status == "succeeded"
    assert report.output_rows == 2
    assert report.rejected_rows == 2
    assert report.rejected_by_rule == {"order_id_not_null": 1, "gross_amount_positive": 1}

    out = spark.read.json((tmp_path / "out_json").as_posix())
    assert out.count() == 2
    rejected = spark.read.json((tmp_path / "rejected" / "b-1").as_posix())
    assert rejected.count() == 2
    csv = spark.read.option("header", "true").csv((tmp_path / "out_csv").as_posix())
    assert csv.count() == 2


def test_bad_config_fails_before_spark(spark, customer_orders_frames, tmp_path):
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    raw["mappings"].append({"type": "derived", "target": "boom",
                            "expression": "reflect('java.lang.System', 'getenv')"})
    config = parse_config(raw)
    with pytest.raises(ConfigValidationError, match="allowlist"):
        run(spark, config, batch_id="b-1", frames=customer_orders_frames)


def test_empty_input_policies(spark, customer_orders_frames, tmp_path):
    empty = customer_orders_frames["orders"].limit(0)
    frames = {**customer_orders_frames, "orders": empty}

    with pytest.raises(EmptyInputError, match="orders"):
        run(spark, local_config(tmp_path, on_empty="fail"), batch_id="b-1", frames=frames)

    report = run(spark, local_config(tmp_path, on_empty="skip"), batch_id="b-1", frames=frames)
    assert report.status == "skipped_empty_input"
    assert report.output_rows == 0


# â”€â”€ apply_filters (D18) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_apply_filters_excludes_rows_silently(customer_orders_frames):
    from dynamic_mapping.models import JoinConfig
    joined = engine.apply_joins(
        customer_orders_frames,
        (JoinConfig(left="customer", right="orders", type="inner",
                    condition="customer.customer_id = orders.customer_id"),),
        ("customer", "orders"),
    )
    filtered = engine.apply_filters(joined, ("orders.order_total > 0",))
    assert joined.count() == 4
    assert filtered.count() == 3  # the -75.0 order is out of scope, not rejected


def test_apply_filters_multiple_conditions_and_or(customer_orders_frames):
    from dynamic_mapping import engine
    df = customer_orders_frames["orders"]
    filtered = engine.apply_filters(
        df, ("order_total > 0 OR tax > 1", "customer_id IS NOT NULL"))
    # o-102 (-75.0 total, 0.0 tax) fails both sides of the OR -> excluded
    assert filtered.count() == 3


# ── prune to config scope (UIFPE-174) ────────────────────────────────────────

def test_prune_sources_drops_unreferenced_columns_and_keeps_alias(spark, customer_orders_frames):
    from pyspark.sql import functions as F
    from dynamic_mapping.config_validator import collect_references

    config = load_config(str(EXAMPLE))
    wide = {
        "customer": customer_orders_frames["customer"]
        .withColumn("loyalty_tier", F.lit("gold")).alias("customer"),
        "orders": customer_orders_frames["orders"]
        .withColumn("internal_notes", F.lit("n/a")).alias("orders"),
    }
    schemas = {name: df.columns for name, df in wide.items()}
    pruned = engine.prune_sources(wide, collect_references(config, schemas))

    assert pruned["customer"].columns == [
        "customer_id", "first_name", "last_name", "full_address"]
    assert pruned["orders"].columns == [
        "order_id", "customer_id", "order_total", "tax"]

    # the alias survives pruning: the qualified join condition still resolves
    joined = engine.apply_joins(pruned, config.joins, config.source_names())
    assert joined.count() == 4


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("HADOOP_HOME"),
    reason="local file writes need winutils.exe (set HADOOP_HOME) on Windows",
)
def test_run_prunes_and_filters_with_same_results(spark, customer_orders_frames, tmp_path):
    """End to end through run() with extra source columns and a row filter:
    the reorder (prune -> filter -> persist) must not change any reported
    number or the written output."""
    import json as _json
    from pyspark.sql import functions as F

    raw = _json.loads(EXAMPLE.read_text(encoding="utf-8"))
    raw["filters"] = ["orders.order_total > 0"]
    raw["output"]["targets"] = [
        {"format": "json", "path": (tmp_path / "out").as_posix(), "mode": "overwrite"}]
    raw["rejected_path"] = (tmp_path / "rejected" / "${batch_id}").as_posix()
    config = parse_config(raw)

    frames = {
        "customer": customer_orders_frames["customer"]
        .withColumn("loyalty_tier", F.lit("gold")).alias("customer"),
        "orders": customer_orders_frames["orders"]
        .withColumn("internal_notes", F.lit("n/a")).alias("orders"),
    }
    report = run(spark, config, batch_id="b-1", frames=frames)

    assert report.status == "succeeded"
    # UIFPE-620: the -75.0 order is still out of scope (output_rows below),
    # but the engine no longer materialises the unfiltered join to count it
    assert report.filtered_rows == 0
    assert report.output_rows == 2          # o-100, o-101
    assert report.rejected_rows == 1        # the NULL order_id row
    out = spark.read.json((tmp_path / "out").as_posix())
    assert out.count() == 2


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("HADOOP_HOME"),
    reason="local file writes need winutils.exe (set HADOOP_HOME) on Windows",
)
def test_filter_reaches_the_source_scan(spark, tmp_path, capsys):
    """Engine-level property: apply_filters on a fresh (un-persisted) frame
    lets Catalyst push the predicate into the file scan.

    NOTE: run() deliberately does NOT rely on this any more — it persists the
    pruned join BEFORE filtering, trading pushdown for a single Iceberg scan
    per batch (count-then-persist scanned twice per JVM and segfaulted the
    pipeline: b-20260820-113904, b-20260820-130250). Pushdown still applies
    wherever a filter runs before any persist, which this test pins."""
    src = (tmp_path / "orders_parquet").as_posix()
    spark.createDataFrame(
        [("o-1", 10.0), ("o-2", -5.0)], ["order_id", "order_total"]
    ).write.mode("overwrite").parquet(src)

    df = spark.read.parquet(src).alias("orders")
    filtered = engine.apply_filters(df, ("orders.order_total > 0",))
    filtered.explain(True)
    plan = capsys.readouterr().out
    assert "PushedFilters:" in plan
    assert "GreaterThan(order_total" in plan


def test_left_join_to_json_renders_array_column_as_json_string(spark):
    """Platform capability pin (UIFPE-237): a left-joined array<struct> column
    mapped through to_json survives the dry run and compiles to a JSON STRING
    column — the CSV-safe shape for nested gold attributes. The unmatched
    left row carries NULL (to_json(NULL) = NULL). In-memory only: no writes,
    so no winutils guard."""
    product = spark.createDataFrame(
        [("PP-1",), ("PP-2",)], ["ppid"]).alias("product")
    price = spark.createDataFrame(
        [("PP-1", [{"priceListName": "STANDARD_GBP", "listPrice": 49.99}])],
        "ppid string, priceSpecifications "
        "array<struct<priceListName:string,listPrice:double>>",
    ).alias("price")
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "/p", "format": "parquet"},
                    {"name": "price", "path": "/q", "format": "parquet"}],
        "joins": [{"left": "product", "right": "price", "type": "left",
                   "condition": "product.ppid = price.ppid"}],
        "mappings": [
            {"type": "one_to_one", "source": "product.ppid", "target": "ppid"},
            {"type": "derived", "target": "priceSpecifications",
             "expression": "to_json(price.priceSpecifications)"}],
        "output": {"targets": [{"format": "parquet", "path": "/out"}]},
    })
    frames = {"product": product, "price": price}
    joined = engine.apply_joins(frames, config.joins, config.source_names())
    assert engine.dry_run_issues(joined, config.mappings, PLACEHOLDERS) == []
    pairs = engine.compile_mappings(config.mappings, PLACEHOLDERS)
    mapped = joined.select(*[col.alias(name) for name, col in pairs])
    rows = {r["ppid"]: r["priceSpecifications"] for r in mapped.collect()}
    assert mapped.count() == 2                       # left join, no fan-out
    parsed = json.loads(rows["PP-1"])                # a real JSON string
    assert parsed == [{"priceListName": "STANDARD_GBP", "listPrice": 49.99}]
    assert rows["PP-2"] is None                      # unmatched left row
