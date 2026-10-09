# test_scheduler_dq.py
# Verifies that scheduler_dispatcher fans out to dq_check_config in addition
# to the existing pipeline scans.

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

from decimal import Decimal

import boto3
from moto import mock_aws


@mock_aws
class TestDqDispatch(unittest.TestCase):
    def setUp(self):
        os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
        os.environ["CONFIG_TABLE"] = "echo-test-glue-job-config"
        os.environ["DQ_CHECK_TABLE"] = "echo-test-dq-check-config"
        os.environ["DQ_GLUE_JOB_NAME"] = "echo-test-silver-quality-check"
        os.environ["GLUE_JOB_NAME"] = "echo-test-bronze-ingest"
        os.environ["RECONCILE_JOB_NAME"] = "echo-test-silver-reconcile"

        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        ddb.create_table(
            TableName="echo-test-glue-job-config",
            KeySchema=[{"AttributeName": "pipeline_id", "KeyType": "HASH"},
                       {"AttributeName": "version",     "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pipeline_id", "AttributeType": "S"},
                {"AttributeName": "version",     "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()
        ddb.create_table(
            TableName="echo-test-dq-check-config",
            KeySchema=[{"AttributeName": "check_id", "KeyType": "HASH"},
                       {"AttributeName": "version",  "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "check_id", "AttributeType": "S"},
                {"AttributeName": "version",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()
        ddb.Table("echo-test-dq-check-config").put_item(Item={
            "check_id": "null-rate-test-1",
            "version":  "latest",
            "kind":     "null-rate",
            "enabled":  True,
            "subject":  {"layer": "silver", "database": "silver_x", "table": "t"},
            "schedule": {"frequency": "daily"},
            "parameters": {"column": "C", "threshold_pct": Decimal("0.1")},
        })
        ddb.Table("echo-test-dq-check-config").put_item(Item={
            "check_id": "null-rate-disabled",
            "version":  "latest",
            "kind":     "null-rate",
            "enabled":  False,
            "subject":  {"layer": "silver", "database": "silver_x", "table": "t"},
            "schedule": {"frequency": "daily"},
            "parameters": {"column": "C", "threshold_pct": Decimal("0.1")},
        })
        ddb.Table("echo-test-dq-check-config").put_item(Item={
            "check_id": "null-rate-hourly",
            "version":  "latest",
            "kind":     "null-rate",
            "enabled":  True,
            "subject":  {"layer": "silver", "database": "silver_x", "table": "t"},
            "schedule": {"frequency": "hourly"},
            "parameters": {"column": "C", "threshold_pct": Decimal("0.1")},
        })
        if "scheduler_dispatcher" in sys.modules:
            del sys.modules["scheduler_dispatcher"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

    def test_daily_event_triggers_only_enabled_daily_dq_checks(self):
        import scheduler_dispatcher
        with patch.object(scheduler_dispatcher, "glue") as mock_glue:
            mock_glue.start_job_run.return_value = {"JobRunId": "jr_dq_1"}
            paginator = MagicMock()
            paginator.paginate.return_value = []
            mock_glue.get_paginator.return_value = paginator

            summary = scheduler_dispatcher.handler({"frequency": "daily"}, None)

            dq_calls = [c for c in mock_glue.start_job_run.call_args_list
                        if c.kwargs.get("JobName") == "echo-test-silver-quality-check"]
            self.assertEqual(len(dq_calls), 1, f"expected 1 DQ run, got {len(dq_calls)}")
            args = dq_calls[0].kwargs["Arguments"]
            self.assertEqual(args.get("--check_id"), "null-rate-test-1")
            self.assertEqual(summary.get("dq_triggered_count"), 1)

    def test_missing_dq_table_is_safe_zero(self):
        boto3.client("dynamodb", region_name="eu-west-1").delete_table(
            TableName="echo-test-dq-check-config"
        )
        if "scheduler_dispatcher" in sys.modules:
            del sys.modules["scheduler_dispatcher"]
        import scheduler_dispatcher
        with patch.object(scheduler_dispatcher, "glue") as mock_glue:
            mock_glue.start_job_run.return_value = {"JobRunId": "jr_x"}
            paginator = MagicMock(); paginator.paginate.return_value = []
            mock_glue.get_paginator.return_value = paginator
            summary = scheduler_dispatcher.handler({"frequency": "daily"}, None)
            self.assertEqual(summary.get("frequency"), "daily")
            self.assertEqual(summary.get("dq_triggered_count"), 0)


if __name__ == "__main__":
    unittest.main()
