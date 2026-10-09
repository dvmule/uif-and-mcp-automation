# test_catalog_lineage.py
# Unit tests for the GET /catalog/lineage route.
#
# Dataset grammar (resolved decision #3): <layer>.<cloud>.<database>.<table>
# Examples:
#   silver.gcp.smoke_silver.author_data
#   bronze.aws.XXPSO.XXPSO_EGO_AUTHOR_DATA
#
# Cloud is explicit so the same logical Silver table replicated across both
# clouds is unambiguous.

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.modules.setdefault("oracledb", MagicMock())

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "lambda"))

import boto3  # noqa: E402

_real_resource = boto3.resource
_real_client = boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client = MagicMock(return_value=MagicMock())

import pipeline_controller as pc  # noqa: E402

boto3.resource = _real_resource
boto3.client = _real_client


def _mk_event(qs):
    return {
        "requestContext": {"http": {"method": "GET"}},
        "rawPath": "/catalog/lineage",
        "pathParameters": {},
        "queryStringParameters": qs,
        "body": None,
    }


class TestCatalogLineageUpstream(unittest.TestCase):
    """Resolve `dataset` → upstream sync. 4-part grammar."""

    def test_silver_gcp_dataset_resolves_to_pipeline(self):
        fake_pipelines = [
            {
                "pipeline_id": "smoke_author",
                "version": "latest",
                "destination": {"cloud": "gcp"},
                "extract": {"table": "AUTHOR.AUTHOR_DATA"},
                "target": {"gcs_path": "gs://bronze/smoke/author_data/"},
                "silver": {
                    "enabled": True,
                    "bq_project": "p",
                    "bq_dataset": "smoke_silver",
                    "table": "author_data",
                    "primary_key_cols": ["AUTHOR_ID"],
                },
            }
        ]

        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            tbl.scan.return_value = {"Items": fake_pipelines}
            mock_dynamo.Table.return_value = tbl

            resp = pc.handler(
                _mk_event({"dataset": "silver.gcp.smoke_silver.author_data"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["upstream"]["sync_id"], "smoke_author")
        self.assertEqual(body["upstream"]["sync_name"], "smoke_author")

    def test_silver_aws_dataset_resolves_to_pipeline(self):
        fake_pipelines = [
            {
                "pipeline_id": "mtl_items",
                "version": "latest",
                # destination block absent → defaults to AWS
                "extract": {"table": "INV.MTL_SYSTEM_ITEMS_B"},
                "target": {"s3_path": "s3://bronze/inv/mtl_system_items_b/"},
                "silver": {
                    "enabled": True,
                    "database": "echo_dev_silver_product",
                    "table": "mtl_system_items_b",
                    "s3_path": "s3://silver/...",
                    "primary_key_cols": ["INVENTORY_ITEM_ID"],
                },
            }
        ]

        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            tbl.scan.return_value = {"Items": fake_pipelines}
            mock_dynamo.Table.return_value = tbl

            resp = pc.handler(
                _mk_event({"dataset": "silver.aws.echo_dev_silver_product.mtl_system_items_b"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["upstream"]["sync_id"], "mtl_items")

    def test_silver_cloud_mismatch_returns_null(self):
        """A silver.aws.<db>.<table> request should NOT match a GCP pipeline
        with the same db+table (cloud is part of the grammar to disambiguate)."""
        fake_pipelines = [
            {
                "pipeline_id": "gcp_only",
                "version": "latest",
                "destination": {"cloud": "gcp"},
                "extract": {"table": "AUTHOR.AUTHOR_DATA"},
                "silver": {
                    "enabled": True,
                    "bq_dataset": "shared_dataset",
                    "table": "shared_table",
                    "primary_key_cols": ["ID"],
                },
            }
        ]
        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            tbl.scan.return_value = {"Items": fake_pipelines}
            mock_dynamo.Table.return_value = tbl

            resp = pc.handler(
                _mk_event({"dataset": "silver.aws.shared_dataset.shared_table"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIsNone(body["upstream"])

    def test_bronze_dataset_resolves_to_pipeline(self):
        fake_pipelines = [
            {
                "pipeline_id": "smoke_author",
                "version": "latest",
                "extract": {"table": "AUTHOR.AUTHOR_DATA"},
                "target": {"s3_path": "s3://echo-dev-bronze/author/author_data/"},
            }
        ]

        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            tbl.scan.return_value = {"Items": fake_pipelines}
            mock_dynamo.Table.return_value = tbl

            resp = pc.handler(
                _mk_event({"dataset": "bronze.aws.author.author_data"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["upstream"]["sync_id"], "smoke_author")

    def test_unmatched_dataset_returns_null_upstream(self):
        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            tbl.scan.return_value = {"Items": []}
            mock_dynamo.Table.return_value = tbl

            resp = pc.handler(
                _mk_event({"dataset": "silver.aws.does_not_exist.foo"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIsNone(body["upstream"])

    def test_missing_dataset_query_param_returns_400(self):
        resp = pc.handler(_mk_event({}), None)
        self.assertEqual(resp["statusCode"], 400)

    def test_malformed_3_part_dataset_returns_400(self):
        # 3-part is the OLD grammar — should be rejected.
        resp = pc.handler(_mk_event({"dataset": "silver.smoke_silver.author_data"}), None)
        self.assertEqual(resp["statusCode"], 400)


class TestCatalogLineageDownstreamSafeZero(unittest.TestCase):
    """Plan 6 (DQ checks) and Plan 7 (Partner Exports) may not have shipped.
    When the underlying tables don't exist, the lineage route must return
    empty arrays instead of 500."""

    def test_missing_export_table_returns_empty_array(self):
        class _RNF(Exception):
            pass

        with patch.object(pc, "dynamo") as mock_dynamo:
            tbl = MagicMock()
            mock_dynamo.Table.return_value = tbl
            mock_dynamo.meta.client.exceptions.ResourceNotFoundException = _RNF

            def scan_side_effect(*a, **k):
                last_table = mock_dynamo.Table.call_args[0][0]
                if last_table in (pc.PARTNER_EXPORT_CONFIG_TABLE,
                                  pc.DQ_CHECK_CONFIG_TABLE):
                    raise _RNF("table not found")
                return {"Items": []}

            tbl.scan.side_effect = scan_side_effect

            resp = pc.handler(
                _mk_event({"dataset": "silver.gcp.smoke_silver.author_data"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["downstream_exports"], [])
        self.assertEqual(body["downstream_checks"], [])


class TestCatalogLineageDownstreamPopulated(unittest.TestCase):
    """When partner_export_config and dq_check_config exist with matching
    rows, those rows appear in downstream_exports and downstream_checks."""

    def test_populated_downstream(self):
        fake_pipelines = [
            {
                "pipeline_id": "smoke_author",
                "version": "latest",
                "destination": {"cloud": "gcp"},
                "extract": {"table": "AUTHOR.AUTHOR_DATA"},
                "silver": {
                    "enabled": True,
                    "bq_dataset": "smoke_silver",
                    "table": "author_data",
                    "primary_key_cols": ["AUTHOR_ID"],
                },
            }
        ]
        fake_exports = [
            {
                "export_id": "ex_partner_a",
                "version": "latest",
                "name": "Partner A daily CSV",
                "source": {"dataset": "silver.gcp.smoke_silver.author_data"},
            },
            {
                "export_id": "ex_partner_b",
                "version": "latest",
                "name": "Partner B JSON",
                "source": {"dataset": "silver.aws.other_dataset.other_table"},
            },
        ]
        fake_checks = [
            {
                "check_id": "chk_author_null",
                "version": "latest",
                "name": "Author null-rate",
                "kind": "null-rate",
                "enabled": True,
                "subject": {"layer": "silver", "database": "smoke_silver", "table": "author_data"},
            }
        ]

        with patch.object(pc, "dynamo") as mock_dynamo:
            def mk_table(table_name):
                t = MagicMock()
                if table_name == pc.CONFIG_TABLE:
                    t.scan.return_value = {"Items": fake_pipelines}
                elif table_name == pc.PARTNER_EXPORT_CONFIG_TABLE:
                    t.scan.return_value = {"Items": fake_exports}
                elif table_name == pc.DQ_CHECK_CONFIG_TABLE:
                    t.scan.return_value = {"Items": fake_checks}
                else:
                    t.scan.return_value = {"Items": []}
                return t

            mock_dynamo.Table.side_effect = mk_table

            resp = pc.handler(
                _mk_event({"dataset": "silver.gcp.smoke_silver.author_data"}), None
            )

        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["upstream"]["sync_id"], "smoke_author")
        self.assertEqual(len(body["downstream_exports"]), 1)
        self.assertEqual(body["downstream_exports"][0]["export_id"], "ex_partner_a")
        self.assertEqual(len(body["downstream_checks"]), 1)
        self.assertEqual(body["downstream_checks"][0]["check_id"], "chk_author_null")


if __name__ == "__main__":
    unittest.main()
