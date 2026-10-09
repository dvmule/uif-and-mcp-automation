# test_preview.py
# UIFPE-441: the Filter step's live Gold data — value suggestions and the
# asynchronous filter preview on Athena. A fake Athena records every query
# text and plays back programmable states, so the suite pins the EXACT SQL
# the wizard's compiled filters become on Trino (escaping, LIKE ESCAPE,
# JSON for complex types, values once in the anti-join CTE), the fences that
# stop anything unvalidated reaching Athena, the ownership check on preview
# ids, and the poll/stop/cancel state machine. No AWS. UIFPE-490 adds the
# matched rows as one CSV: the same fences, one execution, and a finished
# poll answered with a presigned link from a fake S3. UIFPE-514 makes the
# cardinality profile asynchronous and PERSISTED: the same fake S3 holds the
# profiles/ objects, so the suite pins what is written, when it is served,
# when it is stale, and how the values route reads it — against the product
# table's REAL snake_case columns (GOLD_CATALOG), because the old ppid/isbn*
# rule is exactly what let product_id through in dev.

import copy
import io
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from control_panel import preview, registries, validation
from gold_fixtures import FakeGlue  # shared, non-test module
from lifecycle_fixtures import client

TooManyRequestsException = type("TooManyRequestsException", (Exception,), {})

CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {"ppid": "string", "isbn13": "string", "title": "string",
                    "list_price": "decimal(10,2)", "pub_date": "date",
                    "program_ppid": "array<string>", "_gold_updated_at": "timestamp"},
    },
    "uif_echo_dev_gold_price": {
        "price": {"ppid": "string", "vat_country": "string",
                  "price_specifications": "array<struct<price:decimal(10,2),currency:string>>",
                  "_gold_updated_at": "timestamp"},
    },
}

# UIFPE-514: the product table as Gold really names it — the PPID is
# product_id (a string), the row key is inventory_item_id, and description /
# long_description / cover_image_large_url are the text that runs to a
# million distinct values. CATALOG above keeps ppid/isbn13 for the tests
# that pin the sample projection and the older suggestion refusals.
GOLD_CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {"inventory_item_id": "bigint", "organization_id": "bigint",
                    "product_id": "string", "isbn13": "string", "isbn10": "string",
                    "source_item_number": "string", "description": "string",
                    "long_description": "string", "item_type": "string", "brand": "string",
                    "list_price": "decimal(10,2)", "publication_date": "date",
                    "is_digital": "boolean", "cover_image_large_url": "string",
                    "associated_programs": "array<string>", "_gold_updated_at": "timestamp"},
    },
    "uif_echo_dev_gold_price": CATALOG["uif_echo_dev_gold_price"],
}

# what one profile scan of that table answers: 6.8M rows at the item ×
# organisation grain, so even the PPID is far under half the rows
PROFILE_COLS = ["n", "inventory_item_id", "organization_id", "product_id", "isbn13", "isbn10",
                "source_item_number", "description", "long_description", "item_type", "brand",
                "list_price", "publication_date", "is_digital", "cover_image_large_url"]
PROFILE_ROW = ["6830112", "6830112", "3", "1204873", "1180022", "1102387",
               "1204873", "1150000", "1050000", "14", "512",
               "48000", "9400", "2", "1190000"]
PROFILE_KEY = ("demo-bucket", "preview/profiles/uif_echo_dev_gold_product.product.json")

STUB_MAPPING = {"type": "literal", "target": "_filter_check", "value": 1}


def _config(filters=(), joins=None, sources=None):
    return {
        "version": "1.0",
        "sources": sources or [
            {"name": "product", "path": "glue_catalog.uif_echo_dev_gold_product.product", "format": "iceberg"},
            {"name": "price", "path": "glue_catalog.uif_echo_dev_gold_price.price", "format": "iceberg"},
        ],
        "joins": joins if joins is not None else [
            {"left": "product", "right": "price", "type": "left", "condition": "product.ppid = price.ppid"}],
        "mappings": [STUB_MAPPING],
        "filters": list(filters),
        "validations": [],
        "rejected_path": "s3://b/rejected/${batch_id}/",
        "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                "path": "s3://b/staging/${batch_id}/canonical/"}]},
    }


def _single_source(filters=()):
    return _config(filters, joins=[], sources=[
        {"name": "product", "path": "glue_catalog.uif_echo_dev_gold_product.product", "format": "iceberg"}])


class FakeAthena:
    """start/batch_get/get/get_results/stop over in-memory executions."""

    def __init__(self):
        self.started = []          # dicts: id, QueryString, WorkGroup, OutputLocation
        self.states = {}
        self.reasons = {}
        self.results = {}          # id -> (columns, rows)
        self.stopped = []
        self.submitted = {}
        self.fail_start_after = None
        self.polls = 0

    def start_query_execution(self, QueryString, WorkGroup, ResultConfiguration):
        if self.fail_start_after is not None and len(self.started) >= self.fail_start_after:
            raise TooManyRequestsException("Rate exceeded")
        qid = str(uuid.uuid4())
        self.started.append({"id": qid, "QueryString": QueryString, "WorkGroup": WorkGroup,
                             "OutputLocation": ResultConfiguration["OutputLocation"]})
        self.states[qid] = "RUNNING"
        self.submitted[qid] = datetime.now(timezone.utc)
        return {"QueryExecutionId": qid}

    def _execution(self, qid):
        rec = next(r for r in self.started if r["id"] == qid)
        return {"QueryExecutionId": qid, "Query": rec["QueryString"],
                "ResultConfiguration": {"OutputLocation": rec["OutputLocation"]},
                "Status": {"State": self.states[qid], "StateChangeReason": self.reasons.get(qid),
                           "SubmissionDateTime": self.submitted[qid]},
                "Statistics": {"DataScannedInBytes": 1024, "TotalExecutionTimeInMillis": 1500}}

    def batch_get_query_execution(self, QueryExecutionIds):
        known = [q for q in QueryExecutionIds if q in self.states]
        return {"QueryExecutions": [self._execution(q) for q in known],
                "UnprocessedQueryExecutionIds": [{"QueryExecutionId": q}
                                                 for q in QueryExecutionIds if q not in self.states]}

    def get_query_execution(self, QueryExecutionId):
        self.polls += 1
        return {"QueryExecution": self._execution(QueryExecutionId)}

    def get_query_results(self, QueryExecutionId, MaxResults=None):
        cols, rows = self.results[QueryExecutionId]
        header = {"Data": [{"VarCharValue": c} for c in cols]}
        body = [{"Data": [({"VarCharValue": v} if v is not None else {}) for v in row]} for row in rows]
        return {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [{"Name": c} for c in cols]},
                              "Rows": [header, *body]}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped.append(QueryExecutionId)
        self.states[QueryExecutionId] = "CANCELLED"

    # test helpers
    def finish(self, qid, cols, rows):
        self.states[qid] = "SUCCEEDED"
        self.results[qid] = (cols, rows)

    def fail(self, qid, reason):
        self.states[qid] = "FAILED"
        self.reasons[qid] = reason

    def query_of(self, index):
        return self.started[index]["QueryString"]


class InstantAthena(FakeAthena):
    """Finishes every execution with `answer` the moment it starts — for the
    synchronous values route, which polls inside the request."""

    def __init__(self, cols, rows):
        super().__init__()
        self.answer = (cols, rows)

    def start_query_execution(self, **kw):
        out = super().start_query_execution(**kw)
        self.finish(out["QueryExecutionId"], *self.answer)
        return out


class FakeS3:
    """head_object + generate_presigned_url over one imaginary object — what a
    finished download needs (UIFPE-490) — and put_object/get_object over an
    in-memory dict, the profiles/ store (UIFPE-514). `size=None` makes
    head_object fail, the way a not-yet-listed object would."""

    def __init__(self, size=2_048_000):
        self.size = size
        self.presigned = []        # (ClientMethod, Params, ExpiresIn)
        self.heads = []
        self.objects = {}          # (Bucket, Key) -> {"Body": bytes, "ContentType": str}

    def head_object(self, Bucket, Key):
        self.heads.append((Bucket, Key))
        if self.size is None:
            raise Exception("An error occurred (404) when calling the HeadObject operation: Not Found")
        return {"ContentLength": self.size, "ContentType": "text/csv"}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        self.presigned.append((ClientMethod, Params, ExpiresIn))
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = {"Body": bytes(Body), "ContentType": ContentType}
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        if (Bucket, Key) not in self.objects:
            raise Exception("An error occurred (NoSuchKey) when calling the GetObject operation: "
                            "The specified key does not exist.")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["Body"])}


class PreviewEnv(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("BEACON_ENV")
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = "uif_echo_dev_gold_product, uif_echo_dev_gold_price"
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_PREVIEW_WORKGROUP"] = "primary"
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())
        self.athena = FakeAthena()
        self.s3 = FakeS3()
        preview.set_client_for_testing(self.athena, self.s3)
        preview.reset_cache()

    def tearDown(self):
        for key in ("BEACON_GOLD_DATABASES", "BEACON_DATA_BUCKET",
                    "BEACON_PREVIEW_WORKGROUP", "BEACON_PREVIEW_PREFIX"):
            os.environ.pop(key, None)
        if self._env is None:
            os.environ.pop("BEACON_ENV", None)
        else:
            os.environ["BEACON_ENV"] = self._env
        registries.set_clients_for_testing(None, None)
        preview.set_client_for_testing(None)
        preview.reset_cache()

    def start(self, filters=(), lists=None, config=None):
        body = {"mapping_config": config or _config(filters), "lists": lists or []}
        return client.post("/preview/filter", json=body)

    def start_download(self, filters=(), lists=None, config=None):
        body = {"mapping_config": config or _config(filters), "lists": lists or []}
        return client.post("/preview/download", json=body)


# ── the SQL the compiled filters become on Trino ─────────────────────────────

@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestQueryTexts(PreviewEnv):
    def test_counts_sample_and_missing_queries_for_a_joined_feed(self):
        filters = ["product.title LIKE '%50\\\\%%'",          # Spark: pattern %50\%% — literal percent
                   "price.vat_country = 'O\\'Brien'"]         # Spark: backslash-escaped apostrophe
        response = self.start(filters, lists=[{"column": "product.isbn13",
                                               "values": ["9780000000001", "O'Brien"]}])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(len(self.athena.started), 3)
        for rec in self.athena.started:
            self.assertEqual(rec["WorkGroup"], "primary")
            self.assertEqual(rec["OutputLocation"], "s3://demo-bucket/preview/")
            self.assertTrue(rec["QueryString"].startswith(preview.MARKER))

        counts = self.athena.query_of(0)
        self.assertEqual(counts, (
            f"{preview.MARKER}\n"
            "SELECT count(*) AS total, count_if((product.title LIKE '%50\\%%' ESCAPE '\\') "
            "AND (price.vat_country = 'O''Brien')) AS matched\n"
            'FROM "uif_echo_dev_gold_product"."product" AS product\n'
            'LEFT JOIN "uif_echo_dev_gold_price"."price" AS price ON product.ppid = price.ppid'))

        sample = self.athena.query_of(1)
        self.assertIn('CAST(product.title AS VARCHAR) AS "product.title"', sample)
        self.assertIn('CAST(price.vat_country AS VARCHAR) AS "price.vat_country"', sample)
        self.assertIn('CAST(product.isbn13 AS VARCHAR) AS "product.isbn13"', sample)
        self.assertIn('CAST(product.ppid AS VARCHAR) AS "product.ppid"', sample)
        self.assertIn("WHERE (product.title LIKE", sample)
        self.assertTrue(sample.endswith(f"LIMIT {preview.SAMPLE_ROWS}"))

        missing = self.athena.query_of(2)
        self.assertIn("WITH wanted(v) AS (VALUES ('9780000000001'), ('O''Brien'))", missing)
        self.assertIn('NOT EXISTS (SELECT 1 FROM "uif_echo_dev_gold_product"."product" AS product '
                      "WHERE CAST(product.isbn13 AS VARCHAR) = w.v)", missing)
        self.assertEqual(missing.count("9780000000001"), 1)  # values appear ONCE
        self.assertTrue(missing.endswith(f"LIMIT {preview.MISSING_CAP + 1}"))
        self.assertNotIn("LEFT JOIN", missing)  # presence does not depend on the joins

        body = response.json()
        self.assertEqual(body["status"], "running")
        self.assertRegex(body["preview_id"], preview.PREVIEW_ID_RE.pattern)
        self.assertEqual(body["lists"], [{"column": "product.isbn13", "requested": 2}])

    def test_struct_member_filter_previews_and_samples_the_struct_as_json(self):
        # UIFPE-567: the Filter step's own hint recommends alias.struct.member
        # in Custom Spark SQL; the validator used to refuse it as an unknown
        # source and the preview never got as far as Athena
        catalog = {"uif_echo_dev_gold_product": {"product": {
            "product_id": "bigint", "isbn13": "string",
            "fulfillment_org_details": "struct<selling_restriction_code:string,discount_code:string>",
            "_gold_updated_at": "timestamp"}},
            "uif_echo_dev_gold_price": CATALOG["uif_echo_dev_gold_price"]}
        registries.set_clients_for_testing(FakeGlue(catalog), object())
        config = {"version": "1.0",
                  "sources": [{"name": "product", "format": "iceberg",
                               "path": "glue_catalog.uif_echo_dev_gold_product.product"}],
                  "joins": [],
                  "mappings": [{"type": "literal", "target": "_filter_check", "value": 1}],
                  "filters": ["product.fulfillment_org_details.discount_code = 'X'"],
                  "validations": [],
                  "rejected_path": "s3://b/rejected/${batch_id}/",
                  "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                          "path": "s3://b/staging/${batch_id}/canonical/"}]}}
        response = self.start(config=config)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn("count_if((product.fulfillment_org_details.discount_code = 'X'))",
                      self.athena.query_of(0))
        # the sample shows the struct column itself, rendered as JSON like any complex type
        self.assertIn('JSON_FORMAT(CAST(product.fulfillment_org_details AS JSON)) '
                      'AS "product.fulfillment_org_details"', self.athena.query_of(1))

    def test_complex_types_render_as_json_and_zero_filters_count_everything(self):
        response = self.start(["size(price.price_specifications) > 0", "product.pub_date >= to_date('2024-01-01')"])
        self.assertEqual(response.status_code, 202, response.text)
        counts = self.athena.query_of(0)
        self.assertIn("count_if((CARDINALITY(price.price_specifications) > 0) AND "
                      "(product.pub_date >= CAST(CAST('2024-01-01' AS TIMESTAMP) AS DATE)))", counts)
        sample = self.athena.query_of(1)
        self.assertIn('JSON_FORMAT(CAST(price.price_specifications AS JSON)) AS "price.price_specifications"', sample)

        self.athena = FakeAthena()
        preview.set_client_for_testing(self.athena, self.s3)
        response = self.start([])
        self.assertEqual(response.status_code, 202)
        counts = self.athena.query_of(0)
        self.assertIn("SELECT count(*) AS total, count(*) AS matched", counts)
        self.assertNotIn("WHERE", self.athena.query_of(1))
        # no filter columns: the driving source's identifier columns lead the sample
        self.assertIn('"product.ppid"', self.athena.query_of(1))

    def test_join_types(self):
        right = _config(["product.title = 'x'"], joins=[
            {"left": "product", "right": "price", "type": "right", "condition": "product.ppid = price.ppid"}])
        self.assertEqual(self.start(config=right).status_code, 202)
        self.assertIn("RIGHT JOIN", self.athena.query_of(0))

        cross = _config(["product.title = 'x'"], joins=[
            {"left": "product", "right": "price", "type": "cross", "condition": ""}])
        self.assertEqual(self.start(config=cross).status_code, 202)
        self.assertIn('CROSS JOIN "uif_echo_dev_gold_price"."price" AS price\n', self.athena.query_of(3) + "\n")
        self.assertNotIn(" ON ", self.athena.query_of(3).split("CROSS JOIN")[1])

        semi = _config(["product.title = 'x'"], joins=[
            {"left": "product", "right": "price", "type": "left_semi", "condition": "product.ppid = price.ppid"}])
        before = len(self.athena.started)
        response = self.start(config=semi)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "refused")
        self.assertIn("no Athena equivalent", response.json()["issues"][0]["message"])
        self.assertEqual(len(self.athena.started), before)

    def test_backticked_identifier_is_unquoted(self):
        self.assertEqual(preview.transpile_condition("product.`title` = 'x'"), "product.title = 'x'")


# ── list joins on Athena (UIFPE-581) ─────────────────────────────────────────

LIST_CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {
            "product_id": "string", "isbn13": "string", "organization_code": "string",
            "associated_programs": "array<string>",
            "marketing_copy": "array<struct<content_id:string,audience_code:string,"
                              "owning_market_code:string,tiers:array<int>>>",
            "_gold_updated_at": "timestamp"},
        "marketing_copy": {"content_id": "string", "marketing_copy_content_text": "string",
                           "_gold_updated_at": "timestamp"},
    },
    "uif_echo_dev_gold_price": {
        "price": {"product_id": "string",
                  "price_specifications": "array<struct<price_list_name:string,list_price_amount:decimal(38, 2)>>",
                  "_gold_updated_at": "timestamp"},
    },
}
PRODUCT_SRC = {"name": "product", "format": "iceberg", "path": "glue_catalog.uif_echo_dev_gold_product.product"}
COPY_SRC = {"name": "marketing_copy", "format": "iceberg", "path": "glue_catalog.uif_echo_dev_gold_product.marketing_copy"}
PRICE_SRC = {"name": "price", "format": "iceberg", "path": "glue_catalog.uif_echo_dev_gold_price.price"}


def _list_config(sources, joins, filters=(), unnest=None):
    cfg = {"version": "1.0", "sources": sources, "joins": joins,
           "mappings": [{"type": "literal", "target": "_filter_check", "value": 1}],
           "filters": list(filters), "validations": [],
           "rejected_path": "s3://b/rejected/${batch_id}/",
           "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                   "path": "s3://b/staging/${batch_id}/canonical/"}]}}
    if unnest:
        cfg["unnest"] = unnest
    return cfg


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestListJoins(PreviewEnv):
    """The wizard's relationship joins (UIFPE-580/581) planned on Athena: a
    through-list join spreads the list with LEFT JOIN UNNEST … ON TRUE (an
    empty list keeps its row, like the engine's explode_outer) and then
    key-matches the element; unnest steps follow every join."""

    def setUp(self):
        super().setUp()
        registries.set_clients_for_testing(FakeGlue(LIST_CATALOG), object())

    def test_a_join_through_a_list_of_structs_spreads_then_matches(self):
        cfg = _list_config([PRODUCT_SRC, COPY_SRC], [
            {"left": "product", "right": "marketing_copy", "type": "left",
             "through": {"list": "product.marketing_copy", "alias": "product_copy"},
             "condition": "product_copy.content_id = marketing_copy.content_id"}],
            filters=["product_copy.audience_code = 'STU'"])
        response = self.start(config=cfg)
        self.assertEqual(response.status_code, 202, response.text)
        counts = self.athena.query_of(0)
        # UIFPE-601: one array per struct field, zipped — Athena returns an
        # unnested record as a single ROW column, so naming its fields fails
        self.assertIn('FROM "uif_echo_dev_gold_product"."product" AS product\n'
                      "LEFT JOIN UNNEST(transform(product.marketing_copy, x -> x.content_id), "
                      "transform(product.marketing_copy, x -> x.audience_code), "
                      "transform(product.marketing_copy, x -> x.owning_market_code), "
                      "transform(product.marketing_copy, x -> x.tiers)) AS product_copy "
                      "(content_id, audience_code, owning_market_code, tiers) ON TRUE\n"
                      'LEFT JOIN "uif_echo_dev_gold_product"."marketing_copy" AS marketing_copy '
                      "ON product_copy.content_id = marketing_copy.content_id", counts)
        self.assertIn("count_if((product_copy.audience_code = 'STU'))", counts)
        sample = self.athena.query_of(1)
        # the element's field is rendered like a source column, by its type
        self.assertIn('CAST(product_copy.audience_code AS VARCHAR) AS "product_copy.audience_code"', sample)

    def test_a_list_of_scalars_exposes_value(self):
        cfg = _list_config([PRODUCT_SRC, {"name": "program", "format": "iceberg",
                                          "path": "glue_catalog.uif_echo_dev_gold_program.program"}], [
            {"left": "product", "right": "program", "type": "left",
             "through": {"list": "product.associated_programs", "alias": "product_program"},
             "condition": "product_program.value = program.program_id"}])
        # PreviewEnv configures product and price only — add program to the
        # configured databases and to the catalog for this case
        os.environ["BEACON_GOLD_DATABASES"] += ", uif_echo_dev_gold_program"
        catalog = dict(LIST_CATALOG)
        catalog["uif_echo_dev_gold_program"] = {"program": {"program_id": "string", "description": "string",
                                                            "_gold_updated_at": "timestamp"}}
        registries.set_clients_for_testing(FakeGlue(catalog), object())
        response = self.start(config=cfg)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn("LEFT JOIN UNNEST(product.associated_programs) AS product_program (value) ON TRUE\n"
                      'LEFT JOIN "uif_echo_dev_gold_program"."program" AS program '
                      "ON product_program.value = program.program_id", self.athena.query_of(0))

    def test_unnest_steps_follow_every_join_and_complex_fields_render_as_json(self):
        cfg = _list_config([PRODUCT_SRC, PRICE_SRC], [
            {"left": "product", "right": "price", "type": "left",
             "condition": "product.product_id = price.product_id"}],
            filters=["price_spec.price_list_name = 'GBP'", "product_copy.owning_market_code = 'UK'"],
            unnest=[{"list": "price.price_specifications", "alias": "price_spec"},
                    {"list": "product.marketing_copy", "alias": "product_copy"}])
        response = self.start(config=cfg)
        self.assertEqual(response.status_code, 202, response.text)
        counts = self.athena.query_of(0)
        self.assertIn('LEFT JOIN "uif_echo_dev_gold_price"."price" AS price ON product.product_id = price.product_id\n'
                      "LEFT JOIN UNNEST(transform(price.price_specifications, x -> x.price_list_name), "
                      "transform(price.price_specifications, x -> x.list_price_amount)) AS price_spec "
                      "(price_list_name, list_price_amount) ON TRUE\n"
                      "LEFT JOIN UNNEST(transform(product.marketing_copy, x -> x.content_id), ", counts)
        self.assertIn("AS product_copy (content_id, audience_code, owning_market_code, tiers) ON TRUE", counts)
        # the download reuses the same FROM
        download = self.start_download(config=cfg)
        self.assertEqual(download.status_code, 202, download.text)
        self.assertIn("LEFT JOIN UNNEST(transform(price.price_specifications, x -> x.price_list_name)",
                      self.athena.query_of(2))

    def test_an_unknown_element_field_and_a_list_clause_on_an_element_are_refused(self):
        cfg = _list_config([PRODUCT_SRC, COPY_SRC], [
            {"left": "product", "right": "marketing_copy", "type": "left",
             "through": {"list": "product.marketing_copy", "alias": "product_copy"},
             "condition": "product_copy.content_id = marketing_copy.content_id"}],
            filters=["product_copy.nope = 'x'"])
        body = self.start(config=cfg).json()
        self.assertEqual(body["status"], "refused")
        self.assertTrue(any("column 'nope' not found in source 'product_copy'" in i["message"] for i in body["issues"]))
        cfg["filters"] = []
        body = self.start(config=cfg, lists=[{"column": "product_copy.content_id", "values": ["C1"]}]).json()
        self.assertEqual(body["status"], "refused")
        self.assertTrue(any("is a list element" in i["message"] for i in body["issues"]))
        self.assertEqual(self.athena.started, [])


# ── refusals: nothing unvalidated reaches Athena ─────────────────────────────

@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestRefusals(PreviewEnv):
    def assertRefused(self, response, where, fragment):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "refused")
        self.assertTrue(any(i["where"] == where and fragment in i["message"] for i in body["issues"]),
                        body["issues"])
        self.assertEqual(self.athena.started, [])

    def test_csv_source_is_refused(self):
        cfg = _single_source(["p.a = 1"])
        cfg["sources"] = [{"name": "p", "path": "s3://bucket/raw/x/", "format": "csv"}]
        self.assertRefused(self.start(config=cfg), "sources.p", "csv source")

    def test_a_list_column_that_is_not_an_array_on_athena_is_refused(self):
        # UIFPE-581: the spread needs the catalogued array type for the
        # element's fields; product.title is a string in CATALOG
        cfg = _config(["product.title = 'x'"])
        cfg["joins"] = [{"left": "product", "right": "price", "type": "left",
                         "through": {"list": "product.title", "alias": "link"},
                         "condition": "link.value = price.ppid"}]
        self.assertRefused(self.start(config=cfg), "joins[0].through.list", "is not a list column on Athena")

    def test_unknown_catalog_table_is_refused(self):
        cfg = _single_source(["product.title = 'x'"])
        cfg["sources"][0]["path"] = "glue_catalog.uif_echo_dev_gold_finance.ledger"
        self.assertRefused(self.start(config=cfg), "sources.product", "not in a configured gold database")

    def test_registry_fallback_schema_is_refused(self):
        registries.set_clients_for_testing(FakeGlue(CATALOG, fail=True), object())
        self.assertRefused(self.start(["product.title = 'x'"], config=_single_source(["product.title = 'x'"])),
                           "sources.product", "live schema unavailable")

    # UIFPE-569: a table the catalog does not have is not an outage — the
    # registry-listed source's first load has not landed — so the refusal
    # says that instead of "try again shortly"
    def test_a_gold_table_missing_from_the_catalog_says_so(self):
        without_product = {"uif_echo_dev_gold_product": {},
                           "uif_echo_dev_gold_price": CATALOG["uif_echo_dev_gold_price"]}
        registries.set_clients_for_testing(FakeGlue(without_product), object())
        live = registries.source_columns("product.product")
        self.assertEqual(live["provenance"], "registry")
        self.assertTrue(live["missing"])
        response = self.start(config=_single_source(["product.title = 'x'"]))
        self.assertRefused(response, "sources.product",
                           "the Gold table uif_echo_dev_gold_product.product does not exist in this environment yet")
        self.assertFalse(any("try again" in i["message"] for i in response.json()["issues"]))

    def test_a_gold_database_missing_from_the_catalog_says_so_too(self):
        registries.set_clients_for_testing(FakeGlue({"uif_echo_dev_gold_price": CATALOG["uif_echo_dev_gold_price"]}),
                                           object())
        self.assertRefused(self.start(config=_single_source(["product.title = 'x'"])), "sources.product",
                           "does not exist in this environment yet")

    def test_an_outage_is_still_transient_and_carries_no_missing_flag(self):
        registries.set_clients_for_testing(FakeGlue(CATALOG, fail=True), object())
        self.assertNotIn("missing", registries.source_columns("product.product"))

    def test_functions_without_a_trino_equivalent_are_refused_by_name(self):
        self.assertRefused(self.start(["hash(product.title) = 1"]), "filters[0]", "'hash'")
        self.assertRefused(self.start(["format_number(product.list_price, 2) = '1.00'"]), "filters[0]",
                           "'format_number'")

    def test_reserved_word_alias_is_refused(self):
        cfg = _single_source(["order.title = 'x'"])
        cfg["sources"][0]["name"] = "order"
        cfg["mappings"] = [STUB_MAPPING]
        self.assertRefused(self.start(config=cfg), "sources.order", "reserved word")

    def test_validator_issues_come_back_in_the_validator_shape(self):
        self.assertRefused(self.start(["product.ghost = 'x'"]), "filters[0]", "'ghost' not found")
        self.assertRefused(self.start(["product.ppid IN (SELECT 1)"]), "filters[0]", "subquer")
        self.assertRefused(self.start(["reflect('java.lang.System', 'getenv') = 'x'"]), "filters[0]", "allowlist")

    def test_oversized_query_is_refused_naming_the_clause(self):
        big = "product.isbn13 IN (" + ", ".join(f"'978{i:010d}'" for i in range(9000)) + ")"
        response = self.start([big, big])
        self.assertRefused(response, "counts", "KB")

    def test_hostile_identifiers_never_reach_athena(self):
        cfg = _single_source(["product.title = 'x'"])
        cfg["sources"][0]["path"] = 'glue_catalog.uif_echo_dev_gold_product.product"; DROP TABLE x; --'
        self.assertRefused(self.start(config=cfg), "sources.product", "not a plain catalog name")
        response = self.start(["product.title = 'x'"], lists=[{"column": "product.title) OR 1=1 --",
                                                               "values": ["a"]}])
        self.assertRefused(response, "lists[0]", "not a plain column name")

    def test_list_validation(self):
        bad = [
            ([{"column": "title", "values": ["a"]}], "alias-qualified"),
            ([{"column": "ghost.title", "values": ["a"]}], "unknown source"),
            ([{"column": "product.nope", "values": ["a"]}], "not found"),
            ([{"column": "product.title", "values": []}], "non-empty"),
            ([{"column": "product.title", "values": ["a", 3]}], "plain string"),
            ([{"column": "product.title", "values": ["a\nb"]}], "plain string"),
            ([{"column": "product.title", "values": [f"v{i}" for i in range(preview.LIST_CAP + 1)]}], "limit"),
        ]
        for lists, fragment in bad:
            self.assertRefused(self.start(["product.title = 'x'"], lists=lists), "lists[0]", fragment)
        # case-insensitive column resolution to the catalog's true name
        response = self.start(["product.title = 'x'"], lists=[{"column": "product.ISBN13", "values": ["a"]}])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn("CAST(product.isbn13 AS VARCHAR) = w.v", self.athena.query_of(2))

    def test_too_many_lists_is_a_422(self):
        lists = [{"column": "product.title", "values": ["a"]}] * (preview.MAX_LISTS + 1)
        self.assertEqual(self.start(["product.title = 'x'"], lists=lists).status_code, 422)
        self.assertEqual(self.athena.started, [])


# ── the state machine: running → done / failed / cancelled ───────────────────

MAPPINGS = [
    {"type": "one_to_one", "source": "product.title", "target": "Title"},
    {"type": "one_to_one", "source": "price.price_specifications", "target": "prices"},
    {"type": "derived", "expression": "upper(product.title)", "target": "title_upper"},
    {"type": "literal", "value": "UK", "target": "market"},
    # allowlisted for the export, but sqlglot has no Trino form for it
    {"type": "derived", "expression": "format_number(product.list_price, 2)", "target": "price_text"},
    {"type": "one_to_many", "source": "product.pub_date", "targets": ["y", "m"],
     "expressions": ["year(product.pub_date)", "month(product.pub_date)"]},
]


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestMappingProjection(PreviewEnv):
    """UIFPE-603 (owner decision 2026-09-25): the sample and the CSV are the
    export, ten rows of it — the mapped fields as columns, in mapping order,
    headed by the target names; the filter decides the rows. The placeholder
    mapping keeps the diagnostic projection (the tests above)."""

    def mapped(self, filters=("product.title LIKE 'Intro%'",)):
        cfg = _config(filters)
        cfg["mappings"] = MAPPINGS
        return cfg

    def test_the_sample_is_the_mapping_in_order_headed_by_the_targets(self):
        response = self.start(config=self.mapped())
        self.assertEqual(response.status_code, 202, response.text)
        sample = self.athena.query_of(1)
        self.assertIn('CAST(product.title AS VARCHAR) AS "Title",\n', sample)
        self.assertIn('JSON_FORMAT(CAST(price.price_specifications AS JSON)) AS "prices",\n', sample)
        self.assertIn('UPPER(product.title) AS "title_upper",\n', sample)
        self.assertIn("'UK' AS \"market\",\n", sample)
        # the untransformable expression shows its source column, by type
        self.assertIn('CAST(product.list_price AS VARCHAR) AS "price_text",\n', sample)
        self.assertIn('YEAR(CAST(CAST(product.pub_date AS TIMESTAMP) AS DATE)) AS "y",\n       '
                      'MONTH(CAST(CAST(product.pub_date AS TIMESTAMP) AS DATE)) AS "m"\nFROM', sample)
        # nothing diagnostic is added — no ids, no filter or join columns
        self.assertNotIn('"product.ppid"', sample)
        self.assertNotIn('"product.isbn13"', sample)
        self.assertIn("WHERE (product.title LIKE 'Intro%')", sample)
        self.assertEqual(response.json()["untransformed"], ["price_text"])
        # the counts query has no projection and is unchanged
        self.assertIn("SELECT count(*) AS total, count_if((product.title LIKE 'Intro%')) AS matched", self.athena.query_of(0))

    def test_the_download_header_is_the_mapping_too(self):
        response = self.start_download(config=self.mapped())
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["columns"], ["Title", "prices", "title_upper", "market", "price_text", "y", "m"])
        self.assertEqual(body["untransformed"], ["price_text"])
        self.assertTrue(self.athena.query_of(0).endswith(f"LIMIT {preview.DOWNLOAD_ROW_CAP}"))

    def test_a_struct_member_expression_and_a_quoted_target(self):
        catalog = {"uif_echo_dev_gold_product": {"product": {
            "ppid": "string", "fulfillment_org_details": "struct<discount_code:string>",
            "_gold_updated_at": "timestamp"}}}
        registries.set_clients_for_testing(FakeGlue(catalog), object())
        cfg = _single_source()
        # UIFPE-567: alias.struct.member is an expression the validator accepts
        cfg["mappings"] = [{"type": "derived", "expression": "product.fulfillment_org_details.discount_code",
                            "target": 'Discount "code"'}]
        response = self.start(config=cfg)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn('product.fulfillment_org_details.discount_code AS "Discount ""code"""', self.athena.query_of(1))
        self.assertEqual(response.json()["untransformed"], [])

    # UIFPE-605: Athena cannot cast a date or timestamp to JSON, so a list of
    # records carrying one (every price record) is rendered with those
    # fields made text first, names kept so the JSON stays an object
    PRICE_TYPE = ("array<struct<price_list_name:string,list_price_amount:decimal(38, 2),"
                  "price_start_timestamp:timestamp,tiers:array<struct<qty:int,valid_from:date>>>>")
    PRICE_SAFE = ("transform(price.price_specifications, x0 -> CAST(ROW(x0.price_list_name, x0.list_price_amount, "
                  "CAST(x0.price_start_timestamp AS VARCHAR), "
                  "transform(x0.tiers, x1 -> CAST(ROW(x1.qty, CAST(x1.valid_from AS VARCHAR)) "
                  "AS ROW(qty INTEGER, valid_from VARCHAR)))) "
                  "AS ROW(price_list_name VARCHAR, list_price_amount DECIMAL(38,2), price_start_timestamp VARCHAR, "
                  "tiers ARRAY(ROW(qty INTEGER, valid_from VARCHAR)))))")

    def test_json_value_makes_dates_and_timestamps_text_and_leaves_the_rest(self):
        self.assertEqual(preview._json_value("price.price_specifications", self.PRICE_TYPE),
                         (self.PRICE_SAFE, "ARRAY(ROW(price_list_name VARCHAR, list_price_amount DECIMAL(38,2), "
                                           "price_start_timestamp VARCHAR, tiers ARRAY(ROW(qty INTEGER, valid_from VARCHAR))))",
                          True))
        # nothing temporal: the value is untouched
        self.assertEqual(preview._json_value("p.x", "array<struct<price:decimal(10,2),currency:string>>"),
                         ("p.x", "ARRAY(ROW(price DECIMAL(10,2), currency VARCHAR))", False))
        self.assertEqual(preview._json_value("p.tags", "array<string>"), ("p.tags", "ARRAY(VARCHAR)", False))
        self.assertEqual(preview._json_value("p.m", "map<string,timestamp>"),
                         ("transform_values(p.m, (k, v0) -> CAST(v0 AS VARCHAR))", "MAP(VARCHAR, VARCHAR)", True))
        self.assertIsNone(preview._json_value("p.q", "struct<a:mystery>"))

    def test_a_list_of_records_with_timestamps_renders_safely_mapped_directly_via_to_json_and_in_the_diagnostic_sample(self):
        catalog = {"uif_echo_dev_gold_product": CATALOG["uif_echo_dev_gold_product"],
                   "uif_echo_dev_gold_price": {"price": {"ppid": "string", "vat_country": "string",
                                                         "price_specifications": self.PRICE_TYPE,
                                                         "_gold_updated_at": "timestamp"}}}
        registries.set_clients_for_testing(FakeGlue(catalog), object())
        cfg = _config(["product.title = 'x'"])
        cfg["mappings"] = [{"type": "one_to_one", "source": "price.price_specifications", "target": "prices"},
                           {"type": "derived", "expression": "to_json(price.price_specifications)", "target": "prices_json"}]
        response = self.start(config=cfg)
        self.assertEqual(response.status_code, 202, response.text)
        sample = self.athena.query_of(1)
        self.assertIn(f'JSON_FORMAT(CAST({self.PRICE_SAFE} AS JSON)) AS "prices",\n', sample)
        self.assertIn(f'JSON_FORMAT(CAST({self.PRICE_SAFE} AS JSON)) AS "prices_json"\n', sample)
        self.assertNotIn("CAST(price.price_specifications AS JSON)", sample)
        # the diagnostic projection (placeholder mapping) renders it the same way
        self.athena = FakeAthena()
        preview.set_client_for_testing(self.athena, self.s3)
        response = self.start(["size(price.price_specifications) > 0"])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn(f'JSON_FORMAT(CAST({self.PRICE_SAFE} AS JSON)) AS "price.price_specifications"',
                      self.athena.query_of(1))

    def test_a_bare_list_column_in_a_csv_export_is_refused_before_athena(self):
        # UIFPE-609: the sample would have rendered it as JSON text and the
        # Glue run would then have died at rendering — refuse here instead
        cfg = _config(["product.title = 'x'"])
        cfg["mappings"] = [{"type": "one_to_one", "source": "price.price_specifications", "target": "prices"}]
        refused = client.post("/preview/filter", json={"mapping_config": cfg, "lists": [], "format": "csv"}).json()
        self.assertEqual(refused["status"], "refused")
        self.assertIn("a CSV cell cannot carry it", refused["issues"][0]["message"])
        self.assertEqual(self.athena.started, [])
        # json export, or no format given: previews as before
        self.assertEqual(client.post("/preview/filter", json={"mapping_config": cfg, "lists": [], "format": "json"}).status_code, 202)
        self.assertEqual(self.start(config=cfg).status_code, 202)

    def test_the_placeholder_mapping_keeps_the_diagnostic_projection(self):
        response = self.start(["product.title = 'x'"])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn('CAST(product.title AS VARCHAR) AS "product.title"', self.athena.query_of(1))
        self.assertIn('"product.ppid"', self.athena.query_of(1))
        self.assertEqual(response.json()["untransformed"], [])


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestStatusAndCancel(PreviewEnv):
    def started(self):
        response = self.start(["product.title = 'x'"],
                              lists=[{"column": "product.isbn13", "values": ["a", "b", "c"]}])
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["preview_id"], [r["id"] for r in self.athena.started]

    def test_running_then_done(self):
        pid, (counts, sample, missing) = self.started()
        status = client.get(f"/preview/{pid}").json()
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["queries"], {"counts": "RUNNING", "sample": "RUNNING", "missing[0]": "RUNNING"})
        self.assertIsNotNone(status["elapsed_s"])

        self.athena.finish(counts, ["total", "matched"], [["6830112", "12480"]])
        self.athena.finish(sample, ["product.title", "product.isbn13", "product.ppid"],
                           [["Intro", "978x", "A1"], ["Advanced", None, "A2"]])
        self.athena.finish(missing, ["v"], [["b"]])
        done = client.get(f"/preview/{pid}").json()
        self.assertEqual(done["status"], "done")
        self.assertEqual((done["total"], done["matched"]), (6830112, 12480))
        self.assertEqual(done["sample"]["columns"], ["product.title", "product.isbn13", "product.ppid"])
        self.assertEqual(done["sample"]["rows"], [["Intro", "978x", "A1"], ["Advanced", None, "A2"]])
        self.assertEqual(done["missing"], [{"column": "product.isbn13", "missing": ["b"], "truncated": False}])
        self.assertEqual(done["scanned_bytes"], 3 * 1024)
        self.assertIn("Trino", done["note"])

    def test_one_failed_query_fails_the_preview_and_stops_the_rest(self):
        pid, (counts, sample, missing) = self.started()
        self.athena.fail(sample, "TYPE_MISMATCH: Cannot apply operator: bigint = varchar")
        failed = client.get(f"/preview/{pid}").json()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["query"], "sample")
        self.assertIn("bigint = varchar", failed["error"]["message"])
        self.assertEqual(sorted(self.athena.stopped), sorted([counts, missing]))

    def test_pending_longer_than_the_age_cap_is_stopped(self):
        pid, ids = self.started()
        for qid in ids:
            self.athena.submitted[qid] = datetime.now(timezone.utc) - timedelta(seconds=preview.PREVIEW_MAX_AGE_S + 60)
        status = client.get(f"/preview/{pid}").json()
        self.assertEqual(status["status"], "cancelled")
        self.assertIn("longer than", status["reason"])
        self.assertEqual(sorted(self.athena.stopped), sorted(ids))

    def test_cancel_stops_only_pending_and_is_idempotent(self):
        pid, (counts, sample, missing) = self.started()
        self.athena.finish(counts, ["total", "matched"], [["1", "1"]])
        first = client.delete(f"/preview/{pid}").json()
        self.assertEqual(first, {"cancelled": True, "stopped": ["sample", "missing[0]"]})
        second = client.delete(f"/preview/{pid}").json()
        self.assertEqual(second, {"cancelled": True, "stopped": []})
        self.assertEqual(client.get(f"/preview/{pid}").json()["status"], "cancelled")

    def test_ownership_check_refuses_foreign_executions(self):
        pid, ids = self.started()
        # someone else's query in the shared workgroup: no marker
        self.athena.started[0]["QueryString"] = "SELECT * FROM secrets"
        self.assertEqual(client.get(f"/preview/{pid}").status_code, 404)
        self.assertEqual(client.delete(f"/preview/{pid}").status_code, 404)
        # ours by marker but written elsewhere
        self.athena.started[0]["QueryString"] = f"{preview.MARKER}\nSELECT 1"
        self.athena.started[0]["OutputLocation"] = "s3://other-bucket/results/"
        self.assertEqual(client.get(f"/preview/{pid}").status_code, 404)
        # malformed and unknown ids
        self.assertEqual(client.get("/preview/not-an-id").status_code, 404)
        self.assertEqual(client.get(f"/preview/{uuid.uuid4()}.{uuid.uuid4()}").status_code, 404)

    def test_start_throttle_cleans_up_and_is_a_503(self):
        self.athena.fail_start_after = 1
        response = self.start(["product.title = 'x'"])
        self.assertEqual(response.status_code, 503)
        self.assertIn("busy", response.json()["detail"])
        self.assertEqual(len(self.athena.started), 1)
        self.assertEqual(self.athena.stopped, [self.athena.started[0]["id"]])

    def test_disabled_environment(self):
        os.environ.pop("BEACON_PREVIEW_WORKGROUP", None)
        self.assertFalse(client.get("/health").json()["preview_enabled"])
        response = self.start(["product.title = 'x'"])
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"])
        self.assertEqual(client.get(f"/preview/{uuid.uuid4()}.{uuid.uuid4()}").status_code, 503)
        os.environ["BEACON_PREVIEW_WORKGROUP"] = "primary"
        self.assertTrue(client.get("/health").json()["preview_enabled"])


# ── the matched rows as one CSV (UIFPE-490) ──────────────────────────────────

@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestDownload(PreviewEnv):
    FILTERS = ["product.title LIKE 'Intro%'", "price.vat_country = 'GB'"]

    def started(self, filters=None):
        response = self.start_download(filters or self.FILTERS)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json(), self.athena.started[-1]["id"]

    def test_the_download_is_the_sample_query_with_the_cap_in_place_of_its_limit(self):
        body = {"mapping_config": _config(self.FILTERS), "lists": []}
        plan, issues = preview.build_plan(body)
        self.assertEqual(issues, [])
        sample, download = preview.sample_query(plan), preview.download_query(plan)
        self.assertTrue(sample.endswith(f"\nLIMIT {preview.SAMPLE_ROWS}"))
        self.assertTrue(download.endswith(f"\nLIMIT {preview.DOWNLOAD_ROW_CAP}"))
        self.assertEqual(download.removesuffix(f"LIMIT {preview.DOWNLOAD_ROW_CAP}"),
                         sample.removesuffix(f"LIMIT {preview.SAMPLE_ROWS}"))
        self.assertTrue(download.startswith(preview.MARKER))
        self.assertNotIn("ORDER BY", download)

    def test_start_is_one_execution_with_the_cap_and_the_columns(self):
        started, qid = self.started()
        self.assertEqual(len(self.athena.started), 1)
        self.assertEqual(started["status"], "running")
        self.assertEqual(started["download_id"], qid)
        self.assertEqual(started["row_cap"], preview.DOWNLOAD_ROW_CAP)
        # the preview's projection: filter columns, the driving table's
        # identifiers, then the join's columns (product.ppid deduped)
        self.assertEqual(started["columns"],
                         ["product.title", "price.vat_country", "product.ppid", "product.isbn13", "price.ppid"])
        self.assertEqual(self.athena.started[0]["OutputLocation"], "s3://demo-bucket/preview/")
        self.assertEqual(self.athena.started[0]["WorkGroup"], "primary")

    def test_running_then_done_with_a_signed_link_to_the_csv(self):
        _, qid = self.started()
        running = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["elapsed_s"])
        self.assertEqual(running["row_cap"], preview.DOWNLOAD_ROW_CAP)
        self.assertEqual(self.s3.presigned, [])

        self.athena.states[qid] = "SUCCEEDED"
        done = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["expires_seconds"], preview.DOWNLOAD_LINK_TTL_S)
        self.assertEqual(done["bytes"], 2_048_000)
        self.assertEqual(done["scanned_bytes"], 1024)
        self.assertEqual(done["row_cap"], preview.DOWNLOAD_ROW_CAP)
        self.assertIn("Trino", done["note"])
        # the file Athena wrote: <id>.csv under our prefix, named after the driving table
        self.assertRegex(done["filename"], r"^beacon-product-matched-rows-\d{8}-\d{6}Z\.csv$")
        (method, params, ttl), = self.s3.presigned
        self.assertEqual(method, "get_object")
        self.assertEqual(ttl, preview.DOWNLOAD_LINK_TTL_S)
        self.assertEqual((params["Bucket"], params["Key"]), ("demo-bucket", f"preview/{qid}.csv"))
        self.assertEqual(params["ResponseContentDisposition"], f'attachment; filename="{done["filename"]}"')
        self.assertEqual(params["ResponseContentType"], "text/csv")
        self.assertEqual(self.s3.heads, [("demo-bucket", f"preview/{qid}.csv")])
        self.assertIn(f"preview/{qid}.csv", done["url"])

    def test_a_finished_execution_reporting_the_file_itself_is_used_as_is(self):
        _, qid = self.started()
        self.athena.started[0]["OutputLocation"] = f"s3://demo-bucket/preview/{qid}.csv"
        self.athena.states[qid] = "SUCCEEDED"
        done = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.s3.presigned[0][1]["Key"], f"preview/{qid}.csv")

    def test_an_unmeasurable_size_never_hides_the_link(self):
        self.s3.size = None
        _, qid = self.started()
        self.athena.states[qid] = "SUCCEEDED"
        done = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(done["status"], "done")
        self.assertIsNone(done["bytes"])
        self.assertTrue(done["url"].startswith("https://"))

    def test_failed_download_carries_athenas_reason(self):
        _, qid = self.started()
        self.athena.fail(qid, "TYPE_MISMATCH: Cannot apply operator: bigint = varchar")
        failed = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["query"], "download")
        self.assertIn("bigint = varchar", failed["error"]["message"])
        self.assertEqual(self.s3.presigned, [])

    def test_cancel_stops_a_pending_download_and_is_idempotent(self):
        _, qid = self.started()
        first = client.delete(f"/preview/download/{qid}").json()
        self.assertEqual(first, {"cancelled": True, "stopped": ["download"]})
        self.assertEqual(self.athena.stopped, [qid])
        second = client.delete(f"/preview/download/{qid}").json()
        self.assertEqual(second, {"cancelled": True, "stopped": []})
        self.assertEqual(client.get(f"/preview/download/{qid}").json()["status"], "cancelled")

    def test_pending_longer_than_the_age_cap_is_stopped(self):
        _, qid = self.started()
        self.athena.submitted[qid] = datetime.now(timezone.utc) - timedelta(seconds=preview.PREVIEW_MAX_AGE_S + 60)
        status = client.get(f"/preview/download/{qid}").json()
        self.assertEqual(status["status"], "cancelled")
        self.assertIn("longer than", status["reason"])
        self.assertEqual(self.athena.stopped, [qid])

    def test_only_a_download_of_ours_can_be_presigned(self):
        # a PREVIEW's sample execution: ours, but not a download — never presigned through this door
        response = self.start(self.FILTERS)
        self.assertEqual(response.status_code, 202, response.text)
        sample_qid = self.athena.started[1]["id"]
        self.athena.states[sample_qid] = "SUCCEEDED"
        self.assertEqual(client.get(f"/preview/download/{sample_qid}").status_code, 404)
        self.assertEqual(client.delete(f"/preview/download/{sample_qid}").status_code, 404)
        # someone else's execution in the shared workgroup: no marker
        _, qid = self.started()
        self.athena.started[-1]["QueryString"] = f"SELECT * FROM secrets\nLIMIT {preview.DOWNLOAD_ROW_CAP}"
        self.assertEqual(client.get(f"/preview/download/{qid}").status_code, 404)
        # ours by marker but written elsewhere
        self.athena.started[-1]["QueryString"] = f"{preview.MARKER}\nSELECT 1\nLIMIT {preview.DOWNLOAD_ROW_CAP}"
        self.athena.started[-1]["OutputLocation"] = "s3://other-bucket/results/"
        self.assertEqual(client.get(f"/preview/download/{qid}").status_code, 404)
        # malformed, dotted (a preview id) and unknown ids
        self.assertEqual(client.get("/preview/download/not-an-id").status_code, 404)
        self.assertEqual(client.get(f"/preview/download/{uuid.uuid4()}.{uuid.uuid4()}").status_code, 404)
        self.assertEqual(client.get(f"/preview/download/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.s3.presigned, [])

    def test_refused_config_is_a_200_with_the_validators_issues(self):
        response = self.start_download(["hash(product.title) = 1"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "refused")
        self.assertEqual(body["issues"][0]["where"], "filters[0]")
        self.assertIn("'hash'", body["issues"][0]["message"])
        self.assertEqual(self.athena.started, [])

    def test_start_throttle_is_a_503(self):
        self.athena.fail_start_after = 0
        response = self.start_download(self.FILTERS)
        self.assertEqual(response.status_code, 503)
        self.assertIn("busy", response.json()["detail"])

    def test_disabled_environment(self):
        os.environ.pop("BEACON_PREVIEW_WORKGROUP", None)
        self.assertEqual(self.start_download(self.FILTERS).status_code, 503)
        self.assertEqual(client.get(f"/preview/download/{uuid.uuid4()}").status_code, 503)
        self.assertEqual(client.delete(f"/preview/download/{uuid.uuid4()}").status_code, 503)


# ── value suggestions ────────────────────────────────────────────────────────

class TestColumnValues(PreviewEnv):
    def values(self, source="product.product", column="title", **params):
        return client.get(f"/registries/sources/{source}/columns/{column}/values", params=params)

    def test_query_text_result_and_cache(self):
        # the fake answers instantly: finish the execution as soon as it starts
        athena = self.athena

        class InstantAthena(FakeAthena):
            def start_query_execution(self, **kw):
                out = super().start_query_execution(**kw)
                self.finish(out["QueryExecutionId"], ["v", "n"],
                            [["BOOK", "5100"], ["EBOOK", "2200"], [None, "9"]])
                return out
        instant = InstantAthena()
        preview.set_client_for_testing(instant, self.s3)

        result = self.values().json()
        self.assertEqual(instant.query_of(0), (
            f"{preview.MARKER}\n"
            "SELECT CAST(t.title AS VARCHAR) AS v, count(*) AS n\n"
            'FROM "uif_echo_dev_gold_product"."product" AS t\n'
            "WHERE t.title IS NOT NULL\n"
            f"GROUP BY 1\nORDER BY 2 DESC, 1\nLIMIT {preview.VALUES_LIMIT + 1}"))
        self.assertEqual(result["values"], ["BOOK", "EBOOK"])
        self.assertEqual(result["counts"], [5100, 2200])
        self.assertFalse(result["truncated"])
        self.assertFalse(result["cached"])
        self.assertIn("sampled_at", result)

        again = self.values().json()
        self.assertTrue(again["cached"])
        self.assertEqual(len(instant.started), 1)  # no second Athena call within the TTL
        self.assertEqual(athena.started, [])

    def test_excluded_columns_answer_with_a_reason(self):
        for column, fragment in (("ppid", "identifier"), ("isbn13", "identifier"),
                                 ("program_ppid", "structured"), ("pub_date", "dates")):
            result = self.values(column=column).json()
            self.assertEqual(result["values"], [])
            self.assertIn(fragment, result["reason"])
        self.assertEqual(self.athena.started, [])

    def test_unknown_source_or_column_is_a_404_and_bad_column_a_422(self):
        self.assertEqual(self.values(source="ghost.table").status_code, 404)
        self.assertEqual(self.values(column="nope").status_code, 404)
        self.assertEqual(self.values(column="a;b").status_code, 422)

    def test_failure_and_disabled_are_reasons_not_errors(self):
        class FailingAthena(FakeAthena):
            def start_query_execution(self, **kw):
                out = super().start_query_execution(**kw)
                self.fail(out["QueryExecutionId"], "SYNTAX_ERROR: line 2")
                return out
        preview.set_client_for_testing(FailingAthena(), self.s3)
        result = self.values().json()
        self.assertEqual(result["values"], [])
        self.assertIn("SYNTAX_ERROR", result["reason"])

        os.environ.pop("BEACON_PREVIEW_WORKGROUP", None)
        result = self.values().json()
        self.assertEqual(result["values"], [])
        self.assertIn("not configured", result["reason"])

    def test_registry_fallback_schema_is_a_reason(self):
        registries.set_clients_for_testing(FakeGlue(CATALOG, fail=True), object())
        result = self.values(source="gold_product.product", column="description").json()
        self.assertEqual(result["values"], [])
        self.assertIn("live schema unavailable", result["reason"])


# ── the cardinality profile: asynchronous and persisted (UIFPE-514) ─────────

class GoldEnv(PreviewEnv):
    """PreviewEnv over the REAL product columns (GOLD_CATALOG)."""

    SOURCE = "product.product"

    def setUp(self):
        super().setUp()
        registries.set_clients_for_testing(FakeGlue(GOLD_CATALOG), object())

    def get_profile(self, source=SOURCE):
        return client.get(f"/registries/sources/{source}/profile")

    def post_profile(self, source=SOURCE):
        return client.post(f"/registries/sources/{source}/profile")

    def poll(self, profile_id, source=SOURCE):
        return client.get(f"/registries/sources/{source}/profile/{profile_id}")

    def started(self, source=SOURCE):
        response = self.post_profile(source)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["profile_id"]

    def seed_profile(self, age=timedelta(hours=1)):
        """A profile persisted `age` ago, as a finished poll would write it."""
        columns = {c: {"distinct": int(v), "unique_like": False,
                       "identifier": bool(preview.IDENTIFIER_COLUMN_RE.fullmatch(c))}
                   for c, v in zip(PROFILE_COLS[1:], PROFILE_ROW[1:])}
        obj = {"source_id": self.SOURCE, "db": "uif_echo_dev_gold_product", "table": "product",
               "rows": int(PROFILE_ROW[0]), "columns": columns,
               "sampled_at": (datetime.now(timezone.utc) - age).isoformat(),
               "query_execution_id": str(uuid.uuid4())}
        self.s3.put_object(*PROFILE_KEY, Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")
        return obj


class TestColumnProfile(GoldEnv):
    def test_absent_then_start_poll_done_persisted_and_served(self):
        # GET never starts Athena
        self.assertEqual(self.get_profile().json(), {"status": "absent", "source_id": self.SOURCE})
        self.assertEqual(self.athena.started, [])

        started = self.post_profile()
        self.assertEqual(started.status_code, 202, started.text)
        qid = started.json()["profile_id"]
        self.assertEqual(started.json(), {"status": "running", "source_id": self.SOURCE, "profile_id": qid})
        self.assertEqual([r["id"] for r in self.athena.started], [qid])
        self.assertEqual(self.athena.started[0]["WorkGroup"], "primary")
        self.assertEqual(self.athena.started[0]["OutputLocation"], "s3://demo-bucket/preview/")
        # one scan: count(*) + approx_distinct per SCALAR column, in catalog
        # order — no array (associated_programs), no platform metadata
        self.assertEqual(self.athena.query_of(0), (
            f"{preview.MARKER}\n"
            "SELECT count(*) AS n,\n"
            + ",\n".join(f'       approx_distinct(t.{c}) AS "{c}"' for c in PROFILE_COLS[1:]) + "\n"
            'FROM "uif_echo_dev_gold_product"."product" AS t'))
        self.assertNotIn("associated_programs", self.athena.query_of(0))
        self.assertNotIn("_gold_updated_at", self.athena.query_of(0))

        running = self.poll(qid).json()
        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["elapsed_s"])
        self.assertEqual(self.s3.objects, {})
        self.assertEqual(self.get_profile().json()["status"], "absent")

        self.athena.finish(qid, PROFILE_COLS, [PROFILE_ROW])
        done = self.poll(qid).json()
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["persisted"])
        self.assertEqual((done["source_id"], done["rows"], done["query_execution_id"]),
                         (self.SOURCE, 6830112, qid))
        self.assertEqual(done["scanned_bytes"], 1024)
        # identifiers BY NAME, not by ratio: at the item × organisation grain
        # even the PPID is far under half the rows, which is why unique_like
        # stays in the payload but decides nothing any more
        for column in ("product_id", "inventory_item_id", "source_item_number", "isbn13", "isbn10"):
            self.assertTrue(done["columns"][column]["identifier"], column)
        for column in ("product_id", "source_item_number", "isbn13", "isbn10"):
            self.assertFalse(done["columns"][column]["unique_like"], column)
        self.assertTrue(done["columns"]["inventory_item_id"]["unique_like"])   # the row key, and only it
        self.assertEqual(done["columns"]["product_id"], {"distinct": 1204873, "unique_like": False, "identifier": True})
        self.assertEqual(done["columns"]["item_type"], {"distinct": 14, "unique_like": False, "identifier": False})
        self.assertEqual(done["columns"]["description"]["distinct"], 1150000)
        self.assertEqual(done["columns"]["is_digital"]["distinct"], 2)
        self.assertNotIn("associated_programs", done["columns"])
        self.assertEqual(set(done["columns"]), set(PROFILE_COLS[1:]))

        # the object written: preview/profiles/<db>.<table>.json, JSON
        self.assertEqual(list(self.s3.objects), [PROFILE_KEY])
        self.assertEqual(self.s3.objects[PROFILE_KEY]["ContentType"], "application/json")
        stored = json.loads(self.s3.objects[PROFILE_KEY]["Body"])
        self.assertEqual({k: stored[k] for k in ("source_id", "db", "table", "rows", "query_execution_id")},
                         {"source_id": self.SOURCE, "db": "uif_echo_dev_gold_product", "table": "product",
                          "rows": 6830112, "query_execution_id": qid})
        self.assertEqual(stored["columns"], done["columns"])
        self.assertEqual(stored["sampled_at"], done["sampled_at"])

        # a second POST is served from the object — no new scan
        again = self.post_profile()
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((again.json()["status"], again.json()["persisted"]), ("done", True))
        self.assertEqual(again.json()["columns"], done["columns"])
        self.assertEqual(len(self.athena.started), 1)

        served = self.get_profile().json()
        self.assertEqual((served["status"], served["persisted"], served["source_id"]), ("done", True, self.SOURCE))
        self.assertEqual(served["columns"], done["columns"])
        self.assertEqual(served["rows"], 6830112)
        self.assertEqual(len(self.athena.started), 1)

    def test_a_fresh_persisted_profile_is_served_under_any_source_id_for_the_table(self):
        seeded = self.seed_profile()
        # the registry row (gold_product.product, whose dev path is this very
        # table) and the catalog (product.product) name the same table
        # differently; the object is keyed by the table, the answer carries
        # the caller's id
        os.environ["BEACON_ENV"] = "dev"
        served = self.get_profile(source="gold_product.product").json()
        self.assertEqual((served["status"], served["source_id"]), ("done", "gold_product.product"))
        self.assertEqual(served["columns"], seeded["columns"])
        self.assertEqual(self.post_profile().status_code, 200)
        self.assertEqual(self.athena.started, [])

    def test_a_stale_persisted_profile_is_measured_again(self):
        self.seed_profile(age=timedelta(seconds=preview.PROFILE_PERSIST_TTL_S + 60))
        self.assertEqual(self.get_profile().json()["status"], "absent")
        response = self.post_profile()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(len(self.athena.started), 1)
        # and an unreadable object counts as absent, never as an error
        self.s3.objects[PROFILE_KEY] = {"Body": b"not json", "ContentType": "application/json"}
        self.assertEqual(self.get_profile().json()["status"], "absent")

    def test_failed_scan_carries_athenas_reason_and_persists_nothing(self):
        qid = self.started()
        self.athena.fail(qid, "INTERNAL_ERROR: Query exhausted resources at this scale factor")
        failed = self.poll(qid).json()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["query"], "profile")
        self.assertIn("INTERNAL_ERROR", failed["error"]["message"])
        self.assertEqual(self.s3.objects, {})
        self.assertEqual(self.get_profile().json()["status"], "absent")

    def test_pending_longer_than_the_age_cap_is_stopped(self):
        qid = self.started()
        self.athena.submitted[qid] = datetime.now(timezone.utc) - timedelta(seconds=preview.PREVIEW_MAX_AGE_S + 60)
        status = self.poll(qid).json()
        self.assertEqual(status["status"], "cancelled")
        self.assertIn("longer than", status["reason"])
        self.assertEqual(self.athena.stopped, [qid])
        self.assertEqual(self.poll(qid).json()["status"], "cancelled")

    def test_a_persist_failure_still_answers_the_numbers(self):
        class ReadOnlyS3(FakeS3):
            def put_object(self, **kw):
                raise Exception("An error occurred (AccessDenied) when calling the PutObject operation")
        preview.set_client_for_testing(self.athena, ReadOnlyS3())
        qid = self.started()
        self.athena.finish(qid, PROFILE_COLS, [PROFILE_ROW])
        done = self.poll(qid).json()
        self.assertEqual((done["status"], done["persisted"], done["rows"]), ("done", False, 6830112))
        self.assertEqual(self.get_profile().json()["status"], "absent")

    def test_only_a_profile_of_ours_for_this_table_is_answered(self):
        cfg = _single_source(["product.item_type = 'BOOK'"])
        # a PREVIEW's execution: ours by marker and prefix, but not a profile
        self.assertEqual(self.start(config=cfg).status_code, 202)
        counts_qid = self.athena.started[0]["id"]
        self.assertEqual(self.poll(counts_qid).status_code, 404)
        # a DOWNLOAD's execution, likewise
        self.assertEqual(self.start_download(config=cfg).status_code, 202)
        self.assertEqual(self.poll(self.athena.started[-1]["id"]).status_code, 404)
        # another TABLE's profile, through this source's route
        price_qid = self.started(source="price.price")
        self.assertIn('FROM "uif_echo_dev_gold_price"."price" AS t', self.athena.query_of(-1))
        self.assertEqual(self.poll(price_qid).status_code, 404)
        self.assertEqual(self.poll(price_qid, source="price.price").json()["status"], "running")
        # someone else's approx_distinct in the shared workgroup: no marker
        qid = self.started()
        self.athena.started[-1]["QueryString"] = ('SELECT count(*) AS n, approx_distinct(t.x) AS "x"\n'
                                                  'FROM "uif_echo_dev_gold_product"."product" AS t')
        self.assertEqual(self.poll(qid).status_code, 404)
        # ours by marker but written elsewhere
        self.athena.started[-1]["QueryString"] = preview.MARKER + "\n" + self.athena.started[-1]["QueryString"]
        self.athena.started[-1]["OutputLocation"] = "s3://other-bucket/results/"
        self.assertEqual(self.poll(qid).status_code, 404)
        # malformed, dotted (a preview id) and unknown ids; a source with no table
        self.assertEqual(self.poll("not-an-id").status_code, 404)
        self.assertEqual(self.poll(f"{uuid.uuid4()}.{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.poll(str(uuid.uuid4())).status_code, 404)
        self.assertEqual(self.poll(qid, source="ghost.table").status_code, 404)
        self.assertEqual(self.s3.objects, {})

    def test_unavailable_cases_say_so_and_start_nothing(self):
        self.assertEqual(self.get_profile(source="ghost.table").status_code, 404)
        self.assertEqual(self.post_profile(source="ghost.table").status_code, 404)

        os.environ.pop("BEACON_PREVIEW_WORKGROUP", None)
        self.assertEqual(self.post_profile().status_code, 503)
        self.assertEqual(self.poll(str(uuid.uuid4())).status_code, 503)
        got = self.get_profile().json()
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("not configured", got["reason"])
        os.environ["BEACON_PREVIEW_WORKGROUP"] = "primary"

        # registry-fallback schema: the profile needs the live catalog
        registries.set_clients_for_testing(FakeGlue(GOLD_CATALOG, fail=True), object())
        posted = self.post_profile(source="gold_product.product")
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertEqual(posted.json()["status"], "unavailable")
        self.assertIn("live schema", posted.json()["reason"])
        got = self.get_profile(source="gold_product.product").json()
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("live schema", got["reason"])
        registries.set_clients_for_testing(FakeGlue(GOLD_CATALOG), object())

        # a CSV source is not a Gold table
        got = self.get_profile(source="demo.products_csv").json()
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("Gold", got["reason"])
        self.assertEqual(self.athena.started, [])

    def test_start_throttle_is_a_503(self):
        self.athena.fail_start_after = 0
        response = self.post_profile()
        self.assertEqual(response.status_code, 503)
        self.assertIn("busy", response.json()["detail"])


class TestColumnValuesWithProfile(GoldEnv):
    """The values route over the real columns: identifiers refused by the
    widened name rule, over-cap text refused by the persisted profile."""

    def values(self, column, source=GoldEnv.SOURCE):
        return client.get(f"/registries/sources/{source}/columns/{column}/values")

    def test_identifiers_are_refused_by_name_whole_word_case_insensitive(self):
        for column in ("product_id", "PRODUCT_ID", "inventory_item_id", "source_item_number", "isbn13", "isbn10"):
            result = self.values(column).json()
            self.assertEqual(result["values"], [], column)
            self.assertIn("identifier", result["reason"])
        self.assertEqual(self.athena.started, [])
        # a name that merely CONTAINS an identifier is not one
        self.assertFalse(preview.IDENTIFIER_COLUMN_RE.fullmatch("product_id_source"))
        self.assertFalse(preview.IDENTIFIER_COLUMN_RE.fullmatch("brand"))
        for name in ("parent_product_id", "program_id", "content_id", "ppid", "isbn", "isbn_13", "isbn_10"):
            self.assertTrue(preview.IDENTIFIER_COLUMN_RE.fullmatch(name), name)

    def test_the_persisted_profile_refuses_over_cap_text_without_athena(self):
        self.seed_profile()   # description 1,150,000 distinct; item_type 14
        result = self.values("description").json()
        self.assertEqual(result["values"], [])
        self.assertIn("more than 1,000 distinct values", result["reason"])
        self.assertIn("Custom Spark SQL", result["reason"])
        self.assertEqual(self.athena.started, [])

        instant = InstantAthena(["v", "n"], [["BOOK", "5100"], ["EBOOK", "2200"]])
        preview.set_client_for_testing(instant, self.s3)
        result = self.values("item_type").json()
        self.assertEqual(result["values"], ["BOOK", "EBOOK"])
        self.assertNotIn("reason", result)
        self.assertEqual(len(instant.started), 1)
        self.assertIn("CAST(t.item_type AS VARCHAR)", instant.query_of(0))

    def test_without_a_persisted_profile_the_values_call_proceeds(self):
        instant = InstantAthena(["v", "n"], [["A long description", "3"]])
        preview.set_client_for_testing(instant, self.s3)
        self.assertEqual(self.values("description").json()["values"], ["A long description"])
        self.assertEqual(len(instant.started), 1)
        # a stale profile is no profile
        preview.reset_cache()
        self.seed_profile(age=timedelta(seconds=preview.PROFILE_PERSIST_TTL_S + 60))
        self.assertEqual(self.values("description").json()["values"], ["A long description"])
        self.assertEqual(len(instant.started), 2)

    def test_the_sample_projection_keeps_the_old_identifier_list(self):
        """The widened rule is the picker's; the preview's sample/CSV columns
        must not change (UIFPE-490's header is pinned by TestDownload)."""
        self.assertEqual(preview.SAMPLE_IDENTIFIER_RE.pattern, r"^(ppid|isbn13|isbn_13|isbn10|isbn_10|isbn)$")
        body = {"mapping_config": _single_source(["product.item_type = 'BOOK'"]), "lists": []}
        plan, issues = preview.build_plan(body)
        self.assertEqual(issues, [])
        # item_type from the filter, isbn13/isbn10 by the OLD list — product_id
        # and inventory_item_id are NOT pulled in by the widened one
        self.assertEqual([label for label, _e, _t in plan.projection],
                         ["product.item_type", "product.isbn13", "product.isbn10"])


class TestTranspile(unittest.TestCase):
    def test_builder_shapes(self):
        cases = {
            "product.ppid IN ('A1', 'A2')": "product.ppid IN ('A1', 'A2')",
            "product.title = 'O\\'Brien'": "product.title = 'O''Brien'",
            "product.title LIKE '%50\\\\%%'": "product.title LIKE '%50\\%%' ESCAPE '\\'",
            "product.title LIKE 'Intro%'": "product.title LIKE 'Intro%'",
            "array_contains(product.program_ppid, 'A1')": "CONTAINS(product.program_ppid, 'A1')",
            "(product.program_ppid IS NULL OR size(product.program_ppid) = 0)":
                "(product.program_ppid IS NULL OR CARDINALITY(product.program_ppid) = 0)",
            "product.ts > to_timestamp('2024-01-01 09:30:00')": "product.ts > CAST('2024-01-01 09:30:00' AS TIMESTAMP)",
            "product.flag = true": "product.flag = TRUE",
            "product.isbn13 IS NOT NULL": "NOT product.isbn13 IS NULL",
        }
        for spark, trino in cases.items():
            self.assertEqual(preview.transpile_condition(spark), trino, spark)

    def test_refusals(self):
        with self.assertRaises(preview.PreviewError) as ctx:
            preview.transpile_condition("hash(product.title) = 1")
        self.assertIn("'hash'", ctx.exception.message)
        with self.assertRaises(preview.PreviewError):
            preview.transpile_condition("product.title = 'unterminated")
