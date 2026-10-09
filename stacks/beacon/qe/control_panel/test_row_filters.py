# test_row_filters.py
# The wizard's structured "Filter rows" builder, through the API that
# publishes what it compiles: every shape validates end-to-end on a typed
# catalog table, a typo INSIDE an identifier list is a 422 at submit, an
# author's bad quote is an issue rather than a 500, the builder's own model
# rides opaquely in settings.filter_model (never into export.json), the
# store's size guard refuses a draft too large for one DynamoDB item, and the
# columns route carries the catalogued types the builder picks operators
# from. Fake Glue + S3 + Step Functions, no AWS.

import copy
import json
import os
import unittest

from control_panel import lifecycle, publisher, registries, validation
from gold_fixtures import FakeGlue  # shared, non-test module
from lifecycle_fixtures import client

# a typed product table: one column per operator family the builder offers
TYPED_CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {"ppid": "string", "isbn13": "string", "title": "string",
                    "price": "decimal(10,2)", "pubDate": "date",
                    "programPpid": "array<string>", "_gold_updated_at": "timestamp"},
    },
}

MAPPING = {
    "version": "1.0",
    "sources": [{"name": "product",
                 "path": "glue_catalog.uif_echo_dev_gold_product.product",
                 "format": "iceberg"}],
    "joins": [],
    "mappings": [{"type": "one_to_one", "source": "product.ppid", "target": "ppid"}],
    "filters": [],
    "validations": [],
    "rejected_path": "s3://b/rejected/${batch_id}/",
    "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                            "path": "s3://b/staging/${batch_id}/canonical/"}]},
}


def _isbns(n):
    return [f"978{i:010d}" for i in range(n)]


def _isbn_list(n):
    return "product.isbn13 IN (" + ", ".join(f"'{v}'" for v in _isbns(n)) + ")"


BUILDER_FILTERS = [
    _isbn_list(5000),
    "array_contains(product.programPpid, 'A1')",
    "product.price BETWEEN 1 AND 2",
    "product.title LIKE '%x%'",
    "product.isbn13 IS NULL",
    "product.pubDate >= to_date('2024-01-01')",
]


def _mapping(*filters):
    mapping = copy.deepcopy(MAPPING)
    mapping["filters"] = list(filters)
    return mapping


def _list_clause(n, clause_id="c1"):
    return {"id": clause_id, "kind": "list", "column": "product.isbn13",
            "idKind": "isbn13", "values": _isbns(n), "invalid": [], "suspicious": []}


def _settings(*clauses):
    return {"formats": ["csv"], "filter_model": {"version": 1, "clauses": list(clauses)}}


def _post_draft(mapping, settings=None):
    return client.post("/drafts", json={
        "name": "filtered-export", "domain": "product", "author": "abdul",
        "mapping_config": mapping,
        "settings": settings or {"formats": ["csv"]}, "unresolved": []})


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[f"{Bucket}/{Key}"] = Body


class FakeSfn:
    def start_execution(self, stateMachineArn, name, input):
        return {"executionArn": f"arn:aws:states:eu-west-1:1:execution:m:{name}"}


class RowFilterEnv(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self._env = os.environ.get("BEACON_ENV")
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = "uif_echo_dev_gold_product"
        registries.set_clients_for_testing(FakeGlue(TYPED_CATALOG), object())
        self.s3 = FakeS3()
        publisher.set_clients_for_testing(self.s3, FakeSfn())
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_CONFIG_PREFIX"] = "configs"
        os.environ["BEACON_STATE_MACHINE_ARN"] = \
            "arn:aws:states:eu-west-1:1:stateMachine:m"

    def tearDown(self):
        for key in ("BEACON_GOLD_DATABASES", "BEACON_DATA_BUCKET",
                    "BEACON_CONFIG_PREFIX", "BEACON_STATE_MACHINE_ARN"):
            os.environ.pop(key, None)
        if self._env is None:
            os.environ.pop("BEACON_ENV", None)
        else:
            os.environ["BEACON_ENV"] = self._env
        registries.set_clients_for_testing(None, None)
        publisher.set_clients_for_testing(None, None)


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestCompiledFiltersThroughTheApi(RowFilterEnv):
    def test_compiled_filter_shapes_validate_end_to_end(self):
        result = client.post("/validate/mapping-config",
                             json=_mapping(*BUILDER_FILTERS)).json()
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["valid"])

    def test_unknown_column_inside_an_identifier_list_is_a_422_at_submit(self):
        draft = _post_draft(_mapping("product.ppidd IN ('A1')")).json()
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        detail = str(response.json()["detail"])
        self.assertIn("'ppidd' not found", detail)
        self.assertIn("filters[0]", detail)
        self.assertEqual(client.get("/catalog").json(), [])
        self.assertEqual(self.s3.objects, {})

    def test_unbalanced_quote_in_a_filter_is_an_issue_not_a_500(self):
        mapping = _mapping("product.title = 'O'Brien'")
        result = client.post("/validate/mapping-config", json=mapping)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["valid"])
        self.assertTrue(any("not parseable" in i["message"] for i in result.json()["issues"]))
        draft = _post_draft(mapping).json()
        self.assertEqual(client.post(f"/drafts/{draft['id']}/submit").status_code, 422)

    def test_filter_model_round_trips_opaquely_and_the_compiled_filters_publish(self):
        model_settings = _settings(_list_clause(3))
        mapping = _mapping("product.isbn13 IN ('9780000000000', '9780000000001', "
                           "'9780000000002')")
        draft = _post_draft(mapping, model_settings).json()
        self.assertEqual(client.get(f"/drafts/{draft['id']}").json()["settings"],
                         model_settings)

        edited = _settings(_list_clause(2))
        client.put(f"/drafts/{draft['id']}", json={"settings": edited})
        self.assertEqual(client.get(f"/drafts/{draft['id']}").json()["settings"], edited)

        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 200, response.text)
        version = response.json()
        # stored as authored, with the publisher's defaults stamped beside it
        self.assertEqual(version["settings"]["filter_model"], edited["filter_model"])
        self.assertIn("packaging", version["settings"])
        self.assertIn("extraction", version["settings"])
        published = json.loads(self.s3.objects["demo-bucket/configs/100000/v1/mapping.json"])
        self.assertEqual(published["filters"], mapping["filters"])
        # the render/deliver contract has no filter concept: the engine reads
        # filters from mapping.json and the builder's model never leaves settings
        export = json.loads(self.s3.objects["demo-bucket/configs/100000/v1/export.json"])
        self.assertNotIn("filters", export)
        self.assertNotIn("filter_model", json.dumps(export))

    def test_an_edit_draft_inherits_the_filter_model(self):
        settings = _settings(_list_clause(2))
        draft = _post_draft(_mapping("product.isbn13 IN ('9780000000000', "
                                     "'9780000000001')"), settings).json()
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        edit = client.post(f"/catalog/{version['job_id']}/edit",
                           json={"author": "abdul"}).json()
        self.assertEqual(edit["settings"]["filter_model"], settings["filter_model"])

    def test_oversize_draft_is_a_readable_422(self):
        # a 5,000-value clause costs ~165 KB stored twice (compiled + model):
        # two fit inside one 400 KB DynamoDB item, three do not
        three = _post_draft(_mapping(*[_isbn_list(5000)] * 3),
                            _settings(*[_list_clause(5000, f"c{i}") for i in range(3)]))
        self.assertEqual(three.status_code, 422, three.text)
        self.assertIn("KB", three.json()["detail"])
        self.assertIn("filter_model", three.json()["detail"])
        two = _post_draft(_mapping(*[_isbn_list(5000)] * 2),
                          _settings(*[_list_clause(5000, f"c{i}") for i in range(2)]))
        self.assertLess(two.status_code, 300, two.text)


class TestColumnsRouteTypes(RowFilterEnv):
    def test_columns_route_returns_the_catalogued_types_for_the_builder(self):
        result = client.get("/registries/sources/product.product/columns").json()
        self.assertEqual(result["provenance"], "live")
        self.assertEqual(result["types"]["programPpid"], "array<string>")
        self.assertEqual(result["types"]["price"], "decimal(10,2)")
        self.assertEqual(result["types"]["pubDate"], "date")
        self.assertNotIn("_gold_updated_at", result["types"])
        self.assertEqual(set(result["types"]), set(result["columns"]))
