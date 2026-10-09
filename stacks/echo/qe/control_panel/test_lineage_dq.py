# test_lineage_dq.py
# Verifies /catalog/lineage's downstream_checks field is populated from
# dq_check_config rather than the Plan-4 safe-zero stub.

import os
import sys
import unittest
from decimal import Decimal

import boto3
from moto import mock_aws


@mock_aws
class TestLineageDqLookup(unittest.TestCase):
    def setUp(self):
        os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
        os.environ["CONFIG_TABLE"]    = "eds-test-glue-job-config"
        os.environ["METRICS_TABLE"]   = "eds-test-glue-job-metrics"
        os.environ["ALERTS_TABLE"]    = "eds-test-alert-config"
        os.environ["DQ_CHECK_TABLE"]  = "eds-test-dq-check-config"

        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        for tname, hk, rk in [
            ("eds-test-glue-job-config", "pipeline_id", "version"),
            ("eds-test-dq-check-config", "check_id",    "version"),
        ]:
            ddb.create_table(
                TableName=tname,
                KeySchema=[{"AttributeName": hk, "KeyType": "HASH"},
                           {"AttributeName": rk, "KeyType": "RANGE"}],
                AttributeDefinitions=[
                    {"AttributeName": hk, "AttributeType": "S"},
                    {"AttributeName": rk, "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            ).wait_until_exists()

        ddb.Table("eds-test-dq-check-config").put_item(Item={
            "check_id": "null-rate-silver_foo-bar",
            "version":  "latest",
            "kind":     "null-rate",
            "enabled":  True,
            "subject":  {"layer": "silver", "database": "silver_foo", "table": "bar"},
            "schedule": {"frequency": "daily"},
            "parameters": {"column": "C", "threshold_pct": Decimal("0.05")},
        })

        if "pipeline_controller" in sys.modules:
            del sys.modules["pipeline_controller"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

    def test_lineage_includes_matching_dq_check(self):
        import pipeline_controller
        downstream = pipeline_controller.lookup_downstream_checks(
            layer="silver", database="silver_foo", table="bar"
        )
        self.assertEqual(len(downstream), 1)
        self.assertEqual(downstream[0]["check_id"], "null-rate-silver_foo-bar")
        self.assertEqual(downstream[0]["kind"],     "null-rate")
        self.assertTrue(downstream[0]["enabled"])

    def test_lineage_no_match_returns_empty(self):
        import pipeline_controller
        downstream = pipeline_controller.lookup_downstream_checks(
            layer="silver", database="silver_foo", table="other"
        )
        self.assertEqual(downstream, [])

    def test_lineage_safe_when_table_absent(self):
        """Drop the DQ table — function must return [] instead of raising."""
        boto3.client("dynamodb", region_name="eu-west-1").delete_table(
            TableName="eds-test-dq-check-config"
        )
        if "pipeline_controller" in sys.modules:
            del sys.modules["pipeline_controller"]
        import pipeline_controller
        downstream = pipeline_controller.lookup_downstream_checks(
            layer="silver", database="silver_foo", table="bar"
        )
        self.assertEqual(downstream, [])


if __name__ == "__main__":
    unittest.main()
