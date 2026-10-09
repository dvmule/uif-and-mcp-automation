# test_dq_check_controller.py
# CRUD + trigger tests for dq_check_controller Lambda. moto provides
# DynamoDB; Glue start_job_run is patched.

import json
import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch

import boto3
from moto import mock_aws


@mock_aws
class DqControllerBase(unittest.TestCase):
    """Common setup: create eds-test-dq-check-config, seed one check, import controller."""

    def setUp(self):
        os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
        os.environ["DQ_CHECK_TABLE"]    = "eds-test-dq-check-config"
        os.environ["METRICS_TABLE"]     = "eds-test-glue-job-metrics"
        os.environ["DQ_GLUE_JOB_NAME"]  = "eds-test-silver-quality-check"

        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        ddb.create_table(
            TableName="eds-test-dq-check-config",
            KeySchema=[{"AttributeName": "check_id", "KeyType": "HASH"},
                       {"AttributeName": "version",  "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "check_id", "AttributeType": "S"},
                {"AttributeName": "version",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()
        ddb.create_table(
            TableName="eds-test-glue-job-metrics",
            KeySchema=[{"AttributeName": "job_name", "KeyType": "HASH"},
                       {"AttributeName": "run_id",   "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "job_name", "AttributeType": "S"},
                {"AttributeName": "run_id",   "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()

        ddb.Table("eds-test-dq-check-config").put_item(Item={
            "check_id": "null-rate-silver_x-c",
            "version":  "latest",
            "kind":     "null-rate",
            "enabled":  True,
            "subject":  {"layer": "silver", "database": "silver_x", "table": "t_x"},
            "schedule": {"frequency": "daily"},
            "parameters": {"column": "C", "threshold_pct": Decimal("0.1")},
            "metadata": {"created_at": "2026-06-13T00:00:00+00:00",
                         "owner": "raj@pearson.com"},
        })

        if "dq_check_controller" in sys.modules:
            del sys.modules["dq_check_controller"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))


@mock_aws
class TestListChecks(DqControllerBase):
    def test_list_returns_seeded_check(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/checks",
        }, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIn("checks", body)
        self.assertEqual(body["count"], 1)
        check = body["checks"][0]
        self.assertEqual(check["check_id"], "null-rate-silver_x-c")
        self.assertEqual(check["kind"],     "null-rate")
        self.assertEqual(check["subject"]["database"], "silver_x")

    def test_list_filters_by_subject_query_param(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/checks",
            "queryStringParameters": {"subject": "silver.silver_x.t_x"},
        }, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["count"], 1)

    def test_list_with_non_matching_subject_returns_empty(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/checks",
            "queryStringParameters": {"subject": "silver.nope.nope"},
        }, None)
        body = json.loads(resp["body"])
        self.assertEqual(body["count"], 0)


@mock_aws
class TestGetCheck(DqControllerBase):
    def test_get_existing_returns_full_item(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/checks/null-rate-silver_x-c",
            "pathParameters": {"check_id": "null-rate-silver_x-c"},
        }, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["check_id"], "null-rate-silver_x-c")
        self.assertEqual(body["kind"], "null-rate")
        self.assertEqual(body["parameters"]["column"], "C")
        self.assertEqual(body["subject"]["database"], "silver_x")

    def test_get_missing_returns_404(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/checks/does-not-exist",
            "pathParameters": {"check_id": "does-not-exist"},
        }, None)
        self.assertEqual(resp["statusCode"], 404)


@mock_aws
class TestUpsertCheck(DqControllerBase):
    def _put(self, check_id, body):
        import dq_check_controller
        return dq_check_controller.handler({
            "requestContext": {"http": {"method": "PUT"}},
            "rawPath": f"/dq/checks/{check_id}",
            "pathParameters": {"check_id": check_id},
            "body": json.dumps(body),
        }, None)

    def _valid_reconcile_body(self, check_id="reconcile-new-1"):
        return {
            "check_id": check_id,
            "kind": "reconcile",
            "enabled": True,
            "subject": {"layer": "silver", "database": "silver_x", "table": "t"},
            "schedule": {"frequency": "daily"},
            "parameters": {
                "pk_cols": ["ID"],
                "max_deletes_per_run": 1000,
                "dry_run": True,
                "oracle_secret_arn": "arn:aws:secretsmanager:eu-west-1:000000000000:secret:foo-AbCdEf",
                "jdbc_url": "jdbc:oracle:thin:@//h:1521/s",
            },
        }

    def test_put_valid_reconcile_returns_201(self):
        resp = self._put("reconcile-new-1", self._valid_reconcile_body())
        self.assertEqual(resp["statusCode"], 201)
        body = json.loads(resp["body"])
        self.assertEqual(body["check_id"], "reconcile-new-1")
        self.assertIn("metadata", body)
        self.assertIn("created_at", body["metadata"])

    def test_put_reconcile_missing_pk_cols_rejected(self):
        body = self._valid_reconcile_body()
        body["parameters"].pop("pk_cols")
        resp = self._put("reconcile-broken", body)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("pk_cols", json.loads(resp["body"])["error"])

    def test_put_null_rate_threshold_out_of_range_rejected(self):
        body = {
            "check_id": "null-rate-bad",
            "kind": "null-rate",
            "enabled": True,
            "subject": {"layer": "silver", "database": "d", "table": "t"},
            "schedule": {"frequency": "daily"},
            "parameters": {"column": "C", "threshold_pct": 1.5},
        }
        resp = self._put("null-rate-bad", body)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("threshold_pct", json.loads(resp["body"])["error"])

    def test_put_unknown_kind_rejected(self):
        body = self._valid_reconcile_body()
        body["kind"] = "made-up"
        resp = self._put("made-up-1", body)
        self.assertEqual(resp["statusCode"], 400)

    def test_put_invalid_subject_layer_rejected(self):
        body = self._valid_reconcile_body()
        body["subject"]["layer"] = "bronze"
        resp = self._put("reconcile-bronze", body)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("layer", json.loads(resp["body"])["error"])

    def test_put_cron_frequency_without_cron_expression_rejected(self):
        body = self._valid_reconcile_body()
        body["schedule"] = {"frequency": "cron"}
        resp = self._put("reconcile-bad-cron", body)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("cron", json.loads(resp["body"])["error"])


@mock_aws
class TestDeleteCheck(DqControllerBase):
    def test_delete_without_cascade_returns_400(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "DELETE"}},
            "rawPath": "/dq/checks/null-rate-silver_x-c",
            "pathParameters": {"check_id": "null-rate-silver_x-c"},
        }, None)
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("cascade", json.loads(resp["body"])["error"])

    def test_delete_with_cascade_removes_item(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "DELETE"}},
            "rawPath": "/dq/checks/null-rate-silver_x-c",
            "pathParameters": {"check_id": "null-rate-silver_x-c"},
            "queryStringParameters": {"cascade": "true"},
        }, None)
        self.assertEqual(resp["statusCode"], 200)
        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        item = ddb.Table("eds-test-dq-check-config").get_item(
            Key={"check_id": "null-rate-silver_x-c", "version": "latest"}
        ).get("Item")
        self.assertIsNone(item)

    def test_delete_missing_returns_404(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "DELETE"}},
            "rawPath": "/dq/checks/does-not-exist",
            "pathParameters": {"check_id": "does-not-exist"},
            "queryStringParameters": {"cascade": "true"},
        }, None)
        self.assertEqual(resp["statusCode"], 404)


@mock_aws
class TestTriggerRun(DqControllerBase):
    def test_trigger_returns_run_id_and_starts_glue(self):
        import dq_check_controller
        with patch.object(dq_check_controller, "glue") as mock_glue:
            mock_glue.start_job_run.return_value = {"JobRunId": "jr_abc"}
            resp = dq_check_controller.handler({
                "requestContext": {"http": {"method": "POST"}},
                "rawPath": "/dq/checks/null-rate-silver_x-c/trigger-run",
                "pathParameters": {"check_id": "null-rate-silver_x-c"},
            }, None)
            self.assertEqual(resp["statusCode"], 200)
            body = json.loads(resp["body"])
            self.assertEqual(body["run_id"], "jr_abc")
            self.assertEqual(body["check_id"], "null-rate-silver_x-c")
            args = mock_glue.start_job_run.call_args.kwargs
            self.assertEqual(args["JobName"], "eds-test-silver-quality-check")
            self.assertEqual(args["Arguments"]["--check_id"], "null-rate-silver_x-c")

    def test_trigger_missing_check_returns_404(self):
        import dq_check_controller
        with patch.object(dq_check_controller, "glue") as mock_glue:
            resp = dq_check_controller.handler({
                "requestContext": {"http": {"method": "POST"}},
                "rawPath": "/dq/checks/does-not-exist/trigger-run",
                "pathParameters": {"check_id": "does-not-exist"},
            }, None)
            self.assertEqual(resp["statusCode"], 404)
            mock_glue.start_job_run.assert_not_called()


@mock_aws
class TestListKinds(DqControllerBase):
    def test_list_kinds_returns_seven(self):
        import dq_check_controller
        resp = dq_check_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/dq/kinds",
        }, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(len(body["kinds"]), 7)
        live = {k["kind"] for k in body["kinds"] if k["status"] == "live"}
        self.assertEqual(live, {"reconcile", "null-rate"})


if __name__ == "__main__":
    unittest.main()
