# test_beacon_product_export.py
# Product-domain tests for the Beacon product export: lints the committed
# config artifacts in src/schemas/beacon/product-export/{v1,v2}/ and (when
# the dynamic-mapping engine / pyspark are available locally) proves the
# product mappings against the engine with sample rows. v2 (UIFPE-237) joins
# gold_price.price on ppid and ships the price array as ONE JSON-encoded
# column — proven here against the committed synthetic price fixture, since
# the real gold price table is not materialized yet.
#
# The beacon_export package and the dynamic-mapping engine live in the
# beacon stack (stacks/beacon/, shared across domains) — qe/conftest.py puts
# both on sys.path. Generic package/engine tests live there, not here.
#
# Local run of the Spark tiers (Windows):
#   $env:PYSPARK_PYTHON=<venv python with pyspark>
#   <same python> -m pytest domains/product/qe/test_beacon_product_export.py -v

import importlib.util
import json
import unittest
from pathlib import Path

from beacon_export import export_config, quality_gate

HAS_ENGINE = importlib.util.find_spec("dynamic_mapping") is not None
HAS_PYSPARK = importlib.util.find_spec("pyspark") is not None

V1_DIR = (Path(__file__).resolve().parents[1]
          / "src" / "schemas" / "beacon" / "product-export" / "v1")
V2_DIR = V1_DIR.parent / "v2"

# UIFPE-237: synthetic price rows standing in for the unmaterialized
# gold_price.price table, keyed to _sample_products' ppids and kept
# contract-true against the price domain's master schema (see
# TestPriceFixtureContract). PP-103 is deliberately absent (the
# product-without-price / left-join-NULL case).
PRICE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "price_rows.json"
PRICE_MASTER = (Path(__file__).resolve().parents[2]
                / "price" / "src" / "schemas" / "gold" / "price" / "v1"
                / "master.json")

# ONE sample-product universe, shared by the v1 and v2 transform tiers and
# the fixture-coverage assertions — a change here moves everything together
# instead of drifting silently across copies.
# 3 good rows; 1 null ppid; 1 malformed isbn13.
SAMPLE_PRODUCT_ROWS = [
    (101, 1, "PP-101", "9780134685991", "Effective Java", "Long d1",
     "BOOK", "Higher Ed", "CS", "Programming", "en", 2018),
    (102, 1, "PP-102", "9781492056355", "Fluent Python", "Long d2",
     "BOOK", "Higher Ed", "CS", "Programming", "en", 2021),
    (103, 1, "PP-103", None, "Untracked Title", "Long d3",
     "EBOOK", "Workforce", "Data", "Engineering", "en", 2024),
    (104, 1, None, "9780000000000", "No PPID", "Long d4",
     "BOOK", "Higher Ed", "Math", "Algebra", "en", 2020),
    (105, 1, "PP-105", "12345", "Bad ISBN", "Long d5",
     "BOOK", "Higher Ed", "Sci", "Physics", "en", 2019),
]
# the gold table's PHYSICAL columns (snake_case, Schema Standards N8)
SAMPLE_PRODUCT_COLS = ["inventory_item_id", "organization_id", "product_id", "isbn13",
                       "description", "long_description", "item_type",
                       "line_of_business", "discipline_level1",
                       "discipline_level2", "language_level1", "copyright_year"]
PRODUCT_PPIDS = [r[2] for r in SAMPLE_PRODUCT_ROWS]

# Amounts as double: the compiled entity lands DECIMAL(38,10) (UIFPE-263);
# 2dp values round-trip identically through to_json either way, so the
# assertions hold against both. Member set mirrors the compiled master (the
# three Zuora-sourced fields left the contract until that source lands).
_PRICE_SPECS_DDL = (
    "array<struct<"
    "price_list_name:string,list_price_amount:double,net_price_amount:double,vat_price_amount:double,"
    "vat_country_code:string,currency_code:string,price_start_timestamp:string,price_end_timestamp:string>>"
)
PRICE_DDL = f"product_id string, isbn13 string, price_specifications {_PRICE_SPECS_DDL}"


class TestCommittedArtifacts(unittest.TestCase):
    """The v1 config artifacts must always pass validation — schema drift
    fails CI, not a Glue run."""

    def test_export_json_is_valid(self):
        text = (V1_DIR / "export.json").read_text(encoding="utf-8")
        body = export_config.load(text)  # raises on invalid
        self.assertEqual(body["job_id"], "product-export")

    def test_mapping_json_targets_canonical_prefix(self):
        mapping = json.loads((V1_DIR / "mapping.json").read_text(encoding="utf-8"))
        export = json.loads((V1_DIR / "export.json").read_text(encoding="utf-8"))
        # The engine writes canonical output where the export job reads it
        # back. Same prefix, two placeholder syntaxes: the engine uses
        # ${batch_id}, the export config {batch_id}.
        engine_target = mapping["output"]["targets"][0]["path"]
        export_prefix = export["canonical_s3_prefix"]
        self.assertEqual(engine_target.replace("${batch_id}", "X"),
                         export_prefix.replace("{batch_id}", "X"))

    def test_mapping_rejected_path_is_batch_scoped(self):
        mapping = json.loads((V1_DIR / "mapping.json").read_text(encoding="utf-8"))
        self.assertIn("${batch_id}", mapping["rejected_path"])

    def test_gate_breaches_on_high_rejection_sample(self):
        # 2 of 5 rejected = 40%: the committed 2% ceiling must breach.
        export = json.loads((V1_DIR / "export.json").read_text(encoding="utf-8"))
        report = {"status": "succeeded", "output_rows": 3, "rejected_rows": 2}
        result = quality_gate.evaluate(report, export["quality_gates"])
        self.assertFalse(result.passed)
        self.assertIn("rejection ceiling", result.breaches[0])


@unittest.skipUnless(HAS_ENGINE, "dynamic_mapping not importable")
class TestMappingConfigStatic(unittest.TestCase):
    def test_committed_mapping_passes_engine_validation(self):
        import dynamic_mapping
        config = dynamic_mapping.load_config(str(V1_DIR / "mapping.json"))
        issues = (dynamic_mapping.validate_structure(config)
                  + dynamic_mapping.validate_expressions(config))
        self.assertEqual(issues, [],
                         f"engine rejected committed mapping.json: {issues}")


@unittest.skipUnless(HAS_ENGINE and HAS_PYSPARK,
                     "needs dynamic_mapping + pyspark")
class TestTransformInMemory(unittest.TestCase):
    """Drives the engine's compile path with injected DataFrames — the same
    stages run() uses, minus source reads and output writes (which need S3 /
    winutils). Proves the committed mappings and validations against data."""

    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master("local[1]")
                     .appName("beacon-product-local")
                     .config("spark.ui.enabled", "false")
                     .config("spark.sql.shuffle.partitions", "1")
                     .getOrCreate())

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _sample_products(self):
        return self.spark.createDataFrame(SAMPLE_PRODUCT_ROWS,
                                          SAMPLE_PRODUCT_COLS)

    def test_mapping_compiles_and_validations_split_rows(self):
        import dynamic_mapping
        from dynamic_mapping import engine

        config = dynamic_mapping.load_config(str(V1_DIR / "mapping.json"))
        frames = {"product": self._sample_products().alias("product")}
        placeholders = {"batch_id": "local-e2e-001",
                        "run_timestamp": "2026-08-05T00:00:00Z"}

        joined = engine.apply_joins(frames, config.joins, config.source_names())
        self.assertEqual(engine.dry_run_issues(joined, config.mappings,
                                               placeholders), [])

        pairs = engine.compile_mappings(config.mappings, placeholders)
        mapped = joined.select(*[col.alias(name) for name, col in pairs])
        valid, rejected = engine.apply_validations(mapped, config.validations)

        self.assertEqual(valid.count(), 3)
        self.assertEqual(rejected.count(), 2)

        # export schema shape
        self.assertIn("productId", valid.columns)
        self.assertIn("discipline", valid.columns)
        self.assertEqual(valid.filter("sourceSystem = 'gold-product'").count(), 3)

        # discipline concat behaves
        row = valid.filter("inventoryItemId = 101").first()
        self.assertEqual(row["discipline"], "CS / Programming")

        # each bad row rejected by the right rule
        bad = {r["productId"]: r["_failed_rules"] for r in rejected.collect()}
        self.assertIn("product_id_not_null", bad[None])
        self.assertIn("isbn13_shape", bad["PP-105"])


class TestCommittedArtifactsV2(unittest.TestCase):
    """UIFPE-237: the v2 artifacts (product + price left join, JSON price
    column) must pass the pipeline's own validators, and the first 13 mapping
    rules must equal v1's — the partner's existing columns are a stability
    contract, and v1 itself is immutable (D22)."""

    def test_export_json_v2_is_valid(self):
        text = (V2_DIR / "export.json").read_text(encoding="utf-8")
        body = export_config.load(text)  # raises on invalid
        self.assertEqual(body["job_id"], "product-export")
        # full-load is a DECISION here, not a default: the incremental window
        # applies to one source pre-join, so price-only changes cannot ship
        # incrementally (see beacon-incremental-extraction.md known limits)
        self.assertEqual(body["extraction"], {"mode": "full"})

    def test_mapping_v2_targets_canonical_prefix(self):
        mapping = json.loads((V2_DIR / "mapping.json").read_text(encoding="utf-8"))
        export = json.loads((V2_DIR / "export.json").read_text(encoding="utf-8"))
        engine_target = mapping["output"]["targets"][0]["path"]
        export_prefix = export["canonical_s3_prefix"]
        self.assertEqual(engine_target.replace("${batch_id}", "X"),
                         export_prefix.replace("{batch_id}", "X"))

    def test_mapping_v2_is_two_source_left_join(self):
        v2 = json.loads((V2_DIR / "mapping.json").read_text(encoding="utf-8"))
        self.assertEqual([s["name"] for s in v2["sources"]],
                         ["product", "price"])
        self.assertEqual(v2["joins"], [
            {"left": "product", "right": "price", "type": "left",
             "condition": "product.product_id = price.product_id"}])

    def test_first_13_rules_equal_v1_then_price_columns_append(self):
        v1 = json.loads((V1_DIR / "mapping.json").read_text(encoding="utf-8"))
        v2 = json.loads((V2_DIR / "mapping.json").read_text(encoding="utf-8"))
        self.assertEqual(v2["mappings"][:len(v1["mappings"])], v1["mappings"])
        self.assertEqual([m["target"] for m in v2["mappings"][len(v1["mappings"]):]],
                         ["priceSpecifications", "priceSpecCount"])
        # validations and staging paths are carried verbatim too
        self.assertEqual(v2["validations"], v1["validations"])
        self.assertEqual(v2["rejected_path"], v1["rejected_path"])
        self.assertEqual(v2["output"], v1["output"])


class TestPriceFixtureContract(unittest.TestCase):
    """The synthetic price rows must stay valid against the price domain's
    master.json (Draft-07) — when the real gold_price.price table lands, the
    fixture is already contract-true. This runs in PRODUCT CI (product-path
    triggers only); the price domain carries the mirror gate
    (domains/price/qe/test_beacon_consumer_fixture.py) so a price-only master
    change is caught in the CI that runs for it. jsonschema pin:
    qe/requirements-dev.txt."""

    def test_every_fixture_row_validates_against_price_master(self):
        import jsonschema
        schema = json.loads(PRICE_MASTER.read_text(encoding="utf-8"))
        validator = jsonschema.validators.Draft7Validator(schema)
        for row in json.loads(PRICE_FIXTURE.read_text(encoding="utf-8")):
            validator.validate(row)

    def test_fixture_covers_the_left_join_cases(self):
        rows = json.loads(PRICE_FIXTURE.read_text(encoding="utf-8"))
        by_ppid = {r["productId"]: r for r in rows}
        self.assertEqual(len(by_ppid), len(rows))     # 1 row per ppid (the PK)
        counts = {p: len(r["priceSpecifications"]) for p, r in by_ppid.items()}
        self.assertEqual(counts, {"PP-101": 2, "PP-102": 1,
                                  "PP-105": 0, "PP-999": 1})
        self.assertNotIn("PP-103", by_ppid)           # product without price
        self.assertNotIn("PP-999", PRODUCT_PPIDS)     # price without product


@unittest.skipUnless(HAS_ENGINE, "dynamic_mapping not importable")
class TestMappingConfigStaticV2(unittest.TestCase):
    def test_committed_v2_mapping_passes_engine_validation(self):
        # the CI pin that the to_json allowlist entry (UIFPE-237) and the
        # committed v2 expression agree
        import dynamic_mapping
        config = dynamic_mapping.load_config(str(V2_DIR / "mapping.json"))
        issues = (dynamic_mapping.validate_structure(config)
                  + dynamic_mapping.validate_expressions(config))
        self.assertEqual(issues, [],
                         f"engine rejected committed v2 mapping.json: {issues}")


@unittest.skipUnless(HAS_ENGINE and HAS_PYSPARK,
                     "needs dynamic_mapping + pyspark")
class TestTransformInMemoryV2(unittest.TestCase):
    """v2 through the engine's compile path with injected frames: the left
    join preserves row count (no fan-out — price is one row per ppid), the
    price array lands as a parseable JSON string, and the v1 columns and
    validation outcomes are untouched."""

    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master("local[1]")
                     .appName("beacon-product-local-v2")
                     .config("spark.ui.enabled", "false")
                     .config("spark.sql.shuffle.partitions", "1")
                     .getOrCreate())

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def _sample_products(self):
        return self.spark.createDataFrame(SAMPLE_PRODUCT_ROWS,
                                          SAMPLE_PRODUCT_COLS)

    def _price_frame(self):
        rows = json.loads(PRICE_FIXTURE.read_text(encoding="utf-8"))
        return self.spark.createDataFrame(rows, schema=PRICE_DDL)

    def _mapped(self):
        import dynamic_mapping
        from dynamic_mapping import engine

        config = dynamic_mapping.load_config(str(V2_DIR / "mapping.json"))
        frames = {"product": self._sample_products().alias("product"),
                  "price": self._price_frame().alias("price")}
        placeholders = {"batch_id": "local-e2e-v2",
                        "run_timestamp": "2026-08-28T00:00:00Z"}
        joined = engine.apply_joins(frames, config.joins, config.source_names())
        self.assertEqual(engine.dry_run_issues(joined, config.mappings,
                                               placeholders), [])
        pairs = engine.compile_mappings(config.mappings, placeholders)
        return config, engine, joined.select(
            *[col.alias(name) for name, col in pairs])

    def test_v2_left_join_maps_price_json_and_preserves_rows(self):
        _, _, mapped = self._mapped()
        rows = {r["productId"]: r for r in mapped.collect()}

        # left join: every product row survives, PP-999 (price with no
        # matching product) is dropped, and nothing fans out
        self.assertEqual(mapped.count(), 5)
        self.assertEqual(set(rows), {"PP-101", "PP-102", "PP-103", None, "PP-105"})

        parsed = json.loads(rows["PP-101"]["priceSpecifications"])
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["priceListName"], "STANDARD_GBP")
        self.assertEqual(parsed[0]["listPriceAmount"], 49.99)
        self.assertEqual(parsed[1]["priceListName"], "STANDARD_USD")
        # Spark's to_json OMITS null-valued fields: the open-ended endDate is
        # absent from the delivered JSON until it carries a value —
        # partner-visible behavior, pinned on purpose
        self.assertNotIn("endDate", parsed[0])
        self.assertEqual(rows["PP-101"]["priceSpecCount"], 2)

        self.assertEqual(len(json.loads(rows["PP-102"]["priceSpecifications"])), 1)
        self.assertEqual(rows["PP-102"]["priceSpecCount"], 1)

        # product without a price row: both new columns NULL. The count
        # expression guards NULL explicitly — bare size(NULL) is -1 under
        # Spark 3.x defaults (spark.sql.legacy.sizeOfNull=true, ANSI off,
        # the Glue 5.0 posture too), which no partner should ever see.
        self.assertIsNone(rows["PP-103"]["priceSpecifications"])
        self.assertIsNone(rows["PP-103"]["priceSpecCount"])

        # empty-but-present price list is distinct from absent
        self.assertEqual(rows["PP-105"]["priceSpecifications"], "[]")
        self.assertEqual(rows["PP-105"]["priceSpecCount"], 0)

    def test_v2_validations_and_columns_unchanged(self):
        config, engine, mapped = self._mapped()
        valid, rejected = engine.apply_validations(mapped, config.validations)

        self.assertEqual(valid.count(), 3)
        self.assertEqual(rejected.count(), 2)
        bad = {r["productId"]: r["_failed_rules"] for r in rejected.collect()}
        self.assertIn("product_id_not_null", bad[None])
        self.assertIn("isbn13_shape", bad["PP-105"])

        # the first 13 columns are v1's, in v1's order; the price columns append
        self.assertEqual(valid.columns[:13][-2:], ["sourceSystem", "exportedAt"])
        self.assertEqual(valid.columns[13:], ["priceSpecifications", "priceSpecCount"])
        row = valid.filter("inventoryItemId = 101").first()
        self.assertEqual(row["discipline"], "CS / Programming")
