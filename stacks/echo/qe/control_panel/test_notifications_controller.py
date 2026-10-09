# test_notifications_controller.py
# UIFPE-543: the /notifications routes in pipeline_controller - who is emailed
# on a gold alarm (self-service per table) and the alarms themselves, read live.
# boto3 is mocked; DynamoDB is a small in-memory fake so the merge semantics
# (ADD to a set, REMOVE the filter, refuse emptying it) are exercised for real.

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from unittest import mock

import pytest

ENV = {
    "ALERT_SUBSCRIBERS_TABLE": "uif-echo-dev-alert-subscribers",
    "GOLD_CONFIG_TABLE": "uif-echo-dev-gold-pipeline-config",
    "GOLD_ALARM_PREFIX": "uif-echo-dev-gold-",
}

KNOWN_ENTITIES = {"product", "price", "program", "stock", "marketing_copy"}


class ConditionalCheckFailedException(Exception):
    pass


class FakeSubscribers:
    """Just enough DynamoDB: get/put/update/delete/scan with the expressions
    the controller writes. Update supports SET a=:v, ADD set, REMOVE attr."""

    def __init__(self, rows=None):
        self.rows = {r["email"]: dict(r) for r in (rows or [])}

    def get_item(self, Key, **_):
        row = self.rows.get(Key["email"])
        return {"Item": dict(row)} if row else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression == "attribute_not_exists(email)" and Item["email"] in self.rows:
            raise ConditionalCheckFailedException()
        self.rows[Item["email"]] = dict(Item)

    def delete_item(self, Key, ConditionExpression=None):
        # Like DynamoDB: an unconditional delete of a missing key succeeds silently;
        # only the attribute_exists condition turns it into a failure.
        if Key["email"] not in self.rows:
            if ConditionExpression == "attribute_exists(email)":
                raise ConditionalCheckFailedException()
            return
        del self.rows[Key["email"]]

    def scan(self, **kwargs):
        return {"Items": [dict(r) for r in self.rows.values()]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                    ReturnValues=None, ConditionExpression=None):
        row = self.rows.get(Key["email"])
        if ConditionExpression == "attribute_exists(email)" and row is None:
            raise ConditionalCheckFailedException()
        if row is None:
            row = self.rows[Key["email"]] = {"email": Key["email"]}
        names, values = ExpressionAttributeNames, ExpressionAttributeValues
        expr = UpdateExpression
        # split into clauses by keyword
        import re
        parts = re.split(r"\b(SET|ADD|REMOVE)\b", expr)
        it = iter(parts[1:])
        for kw, body in zip(it, it):
            for piece in [p.strip() for p in body.split(",") if p.strip()]:
                if kw == "SET":
                    attr, val = [x.strip() for x in piece.split("=")]
                    row[names[attr]] = values[val]
                elif kw == "ADD":
                    attr, val = piece.split()
                    row.setdefault(names[attr], set()).update(values[val])
                elif kw == "REMOVE":
                    row.pop(names[piece], None)
        return {"Attributes": dict(row)}


@pytest.fixture
def pc():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"), mock.patch.dict(os.environ, ENV):
        sys.modules.pop("pipeline_controller", None)
        module = importlib.import_module("pipeline_controller")
    with mock.patch.dict(os.environ, ENV):
        yield module
    sys.modules.pop("pipeline_controller", None)


@pytest.fixture
def store(pc):
    """Wire the fake subscribers table and a config table that knows the five
    gold entities; return the fake so tests can inspect rows."""
    subs = FakeSubscribers()
    config = mock.MagicMock()
    config.get_item.side_effect = lambda Key, **_: (
        {"Item": {"gold_id": Key["gold_id"]}} if Key["gold_id"] in KNOWN_ENTITIES else {})

    def table(name):
        return subs if name == ENV["ALERT_SUBSCRIBERS_TABLE"] else config
    pc.dynamo = mock.MagicMock()
    pc.dynamo.Table.side_effect = table
    pc.dynamo.meta.client.exceptions.ConditionalCheckFailedException = ConditionalCheckFailedException
    return subs


def call(pc, method, path, body=None, email=None):
    event = {"requestContext": {"http": {"method": method, "path": path}},
             "rawPath": path, "pathParameters": {"email": email} if email else {}}
    if body is not None:
        event["body"] = json.dumps(body)
    resp = pc.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


# ── subscribe (POST = upsert that merges) ───────────────────────────────────

def test_subscribe_creates_a_row_for_one_table(pc, store):
    status, body = call(pc, "POST", "/notifications/subscribers",
                        {"email": "Owner@Pearson.com", "gold_ids": ["product"]})
    assert status == 201, body
    assert body["email"] == "owner@pearson.com"          # normalised
    assert body["gold_ids"] == ["product"] and body["enabled"] is True
    assert store.rows["owner@pearson.com"]["gold_ids"] == {"product"}
    assert store.rows["owner@pearson.com"]["added_by"] == "console"


def test_subscribing_a_second_table_merges_into_the_set(pc, store):
    call(pc, "POST", "/notifications/subscribers", {"email": "a@x.io", "gold_ids": ["product"]})
    status, body = call(pc, "POST", "/notifications/subscribers", {"email": "a@x.io", "gold_ids": ["price"]})
    assert status == 200, body
    assert body["gold_ids"] == ["price", "product"]
    assert store.rows["a@x.io"]["gold_ids"] == {"price", "product"}


def test_subscribing_with_no_tables_means_everything_and_reenables(pc, store):
    store.rows["ops@x.io"] = {"email": "ops@x.io", "enabled": False, "gold_ids": {"stock"}}
    status, body = call(pc, "POST", "/notifications/subscribers", {"email": "ops@x.io"})
    assert status == 200, body
    assert body["gold_ids"] is None and body["enabled"] is True
    assert "gold_ids" not in store.rows["ops@x.io"]


def test_subscribe_refuses_bad_input(pc, store):
    for body, needle in (
        ({"email": "not-an-address"}, "valid address"),
        ({}, "valid address"),
        ({"email": "a@x.io", "gold_ids": "product"}, "must be a list"),
        ({"email": "a@x.io", "gold_ids": ["Product!"]}, "not a gold id"),
        ({"email": "a@x.io", "gold_ids": ["unicorn"]}, "unknown gold entity"),
    ):
        status, resp = call(pc, "POST", "/notifications/subscribers", body)
        assert status == 400, (body, resp)
        assert needle in resp["error"]
    assert store.rows == {}


# ── list ────────────────────────────────────────────────────────────────────

def test_list_is_sorted_and_shaped_for_the_page(pc, store):
    store.rows["z@x.io"] = {"email": "z@x.io", "enabled": True, "gold_ids": {"stock", "price"}, "added_by": "terraform"}
    store.rows["a@x.io"] = {"email": "a@x.io"}
    status, body = call(pc, "GET", "/notifications/subscribers")
    assert status == 200
    assert body["total"] == 2
    assert [r["email"] for r in body["items"]] == ["a@x.io", "z@x.io"]
    assert body["items"][0] == {"email": "a@x.io", "enabled": True, "gold_ids": None,
                                "added_by": None, "added_at": None, "updated_at": None}
    assert body["items"][1]["gold_ids"] == ["price", "stock"]  # sorted, set -> list


# ── change (PATCH) ──────────────────────────────────────────────────────────

def test_disable_and_enable(pc, store):
    store.rows["a@x.io"] = {"email": "a@x.io", "gold_ids": {"product"}}
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"enabled": False}, email="a@x.io")
    assert status == 200 and body["enabled"] is False
    assert store.rows["a@x.io"]["enabled"] is False
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"enabled": "false"}, email="a@x.io")
    assert status == 400 and "JSON boolean" in body["error"]


def test_replace_tables_and_widen_to_everything(pc, store):
    store.rows["a@x.io"] = {"email": "a@x.io", "gold_ids": {"product"}}
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"gold_ids": ["price", "stock"]}, email="a@x.io")
    assert status == 200 and body["gold_ids"] == ["price", "stock"]
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"gold_ids": None}, email="a@x.io")
    assert status == 200 and body["gold_ids"] is None
    assert "gold_ids" not in store.rows["a@x.io"]


def test_remove_one_table_keeps_the_rest(pc, store):
    store.rows["a@x.io"] = {"email": "a@x.io", "gold_ids": {"product", "price"}}
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"remove_gold_ids": ["product"]}, email="a@x.io")
    assert status == 200 and body["gold_ids"] == ["price"]


def test_removing_the_last_table_is_refused_not_widened(pc, store):
    """An empty set would silently mean 'everything' - the opposite of what
    the person asked for. The page removes the address instead."""
    store.rows["a@x.io"] = {"email": "a@x.io", "gold_ids": {"product"}}
    status, body = call(pc, "PATCH", "/notifications/subscribers/a@x.io", {"remove_gold_ids": ["product"]}, email="a@x.io")
    assert status == 409, body
    assert "remove the address" in body["error"]
    assert store.rows["a@x.io"]["gold_ids"] == {"product"}


def test_remove_table_from_an_everything_row_is_refused(pc, store):
    store.rows["ops@x.io"] = {"email": "ops@x.io"}
    status, body = call(pc, "PATCH", "/notifications/subscribers/ops@x.io", {"remove_gold_ids": ["product"]}, email="ops@x.io")
    assert status == 409 and "everything" in body["error"]


def test_patch_validation(pc, store):
    store.rows["a@x.io"] = {"email": "a@x.io", "gold_ids": {"product"}}
    for body, code, needle in (
        ({}, 400, "must carry"),
        ({"colour": "blue"}, 400, "unknown field"),
        ({"gold_ids": ["price"], "remove_gold_ids": ["product"]}, 400, "not both"),
        ({"gold_ids": ["unicorn"]}, 400, "unknown gold entity"),
    ):
        status, resp = call(pc, "PATCH", "/notifications/subscribers/a@x.io", body, email="a@x.io")
        assert (status, needle in resp["error"]) == (code, True), (body, resp)
    status, resp = call(pc, "PATCH", "/notifications/subscribers/nobody@x.io", {"enabled": True}, email="nobody@x.io")
    assert status == 404


# ── remove ──────────────────────────────────────────────────────────────────

def test_remove(pc, store):
    store.rows["a@x.io"] = {"email": "a@x.io"}
    status, body = call(pc, "DELETE", "/notifications/subscribers/A@X.io", email="A@X.io")
    assert status == 200 and body == {"email": "a@x.io", "removed": True}
    assert store.rows == {}
    status, body = call(pc, "DELETE", "/notifications/subscribers/a@x.io", email="a@x.io")
    assert status == 404


def test_unconfigured_environment_is_named_not_a_traceback(pc, store):
    with mock.patch.object(pc, "ALERT_SUBSCRIBERS_TABLE", ""):
        status, body = call(pc, "GET", "/notifications/subscribers")
    assert status == 500 and "ALERT_SUBSCRIBERS_TABLE" in body["error"]


# ── rules (the alarms, read-only) ───────────────────────────────────────────

def _alarm(name, gold_id=None, state="OK", actions=True):
    dims = [{"Name": "Env", "Value": "dev"}]
    if gold_id:
        dims.append({"Name": "GoldId", "Value": gold_id})
    return {"AlarmName": name, "StateValue": state, "StateReason": "r",
            "StateUpdatedTimestamp": datetime(2026, 9, 24, 6, 43, tzinfo=timezone.utc),
            "AlarmDescription": "d", "MetricName": "RunCount", "Namespace": "UIF/Gold",
            "Threshold": 0.0, "ComparisonOperator": "GreaterThanThreshold", "Period": 300,
            "EvaluationPeriods": 1, "Dimensions": dims,
            "AlarmActions": ["arn:aws:lambda:eu-west-1:1:function:n"] if actions else []}


def test_rules_are_the_gold_alarms_grouped_by_entity(pc, store):
    pc.cloudwatch = mock.MagicMock()
    pc.cloudwatch.describe_alarms.side_effect = [
        {"MetricAlarms": [_alarm("uif-echo-dev-gold-product-run-failed", "product", "ALARM"),
                          _alarm("uif-echo-dev-gold-dispatch-silent")], "NextToken": "n1"},
        {"MetricAlarms": [_alarm("uif-echo-dev-gold-marketing-copy-freshness-breached", "marketing_copy")]},
    ]
    status, body = call(pc, "GET", "/notifications/rules")
    assert status == 200, body
    assert pc.cloudwatch.describe_alarms.call_args_list[0].kwargs["AlarmNamePrefix"] == "uif-echo-dev-gold-"
    assert pc.cloudwatch.describe_alarms.call_args_list[1].kwargs["NextToken"] == "n1"
    rows = body["items"]
    assert [(r["gold_id"], r["condition"]) for r in rows] == [
        ("marketing_copy", "freshness breached"),   # entity rows first, by entity
        ("product", "run failed"),
        (None, "dispatch silent"),                  # fleet rows last
    ]
    assert rows[1]["state"] == "ALARM" and rows[1]["emails"] is True
    assert rows[1]["state_updated_at"] == "2026-09-24T06:43:00+00:00"


def test_rules_unconfigured_is_named(pc, store):
    with mock.patch.object(pc, "GOLD_ALARM_PREFIX", ""):
        status, body = call(pc, "GET", "/notifications/rules")
    assert status == 500 and "GOLD_ALARM_PREFIX" in body["error"]


# ── the decoy is gone ───────────────────────────────────────────────────────

def test_the_alerts_decoy_routes_are_retired(pc, store):
    for method in ("GET", "PUT"):
        status, body = call(pc, method, "/alerts", {} if method == "PUT" else None)
        assert status == 400 and "Unknown route" in body["error"]
    assert not hasattr(pc, "get_alert_config") and not hasattr(pc, "ALERTS_TABLE")
