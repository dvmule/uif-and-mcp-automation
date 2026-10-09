# test_scheduler.py
# Per-export schedules (UIFPE-206): the DynamoDB record and the EventBridge
# Scheduler schedule stay in sync, with fake clients — no AWS.

import os
import json
import unittest

from control_panel import scheduler


class ConflictException(Exception):
    pass


class ResourceNotFoundException(Exception):
    pass


class ValidationException(Exception):
    pass


class _Exceptions:
    ConflictException = ConflictException
    ResourceNotFoundException = ResourceNotFoundException


class FakeScheduler:
    exceptions = _Exceptions

    def __init__(self, reject=False):
        self.schedules = {}
        self.reject = reject
        self.calls = []

    def create_schedule(self, **kwargs):
        self.calls.append("create")
        if self.reject:
            raise ValidationException("Timezone must be a valid IANA value")
        if kwargs["Name"] in self.schedules:
            raise ConflictException("exists")
        self.schedules[kwargs["Name"]] = kwargs

    def update_schedule(self, **kwargs):
        self.calls.append("update")
        self.schedules[kwargs["Name"]] = kwargs

    def delete_schedule(self, Name, GroupName):
        self.calls.append("delete")
        if Name not in self.schedules:
            raise ResourceNotFoundException("missing")
        del self.schedules[Name]


class FakeDdb:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[Item["job_id"]["S"]] = Item

    def get_item(self, TableName, Key):
        item = self.items.get(Key["job_id"]["S"])
        return {"Item": item} if item else {}

    def delete_item(self, TableName, Key):
        self.items.pop(Key["job_id"]["S"], None)

    def scan(self, TableName, **kwargs):
        return {"Items": list(self.items.values())}


ENV = {
    "BEACON_TABLE_SCHEDULES": "schedules-table",
    "BEACON_SCHEDULER_GROUP": "default",  # the account's shared group
    "BEACON_SCHEDULE_NAME_PREFIX": "beacon-dev-export-",
    "BEACON_SCHEDULER_TARGET_ARN": "arn:lambda:starter",
    "BEACON_SCHEDULER_ROLE_ARN": "arn:iam:scheduler-invoke",
}

DAILY_6 = {"expression": "cron(0 6 * * ? *)", "timezone": "Europe/London",
           "enabled": True}


class TestScheduler(unittest.TestCase):
    def setUp(self):
        os.environ.update(ENV)
        self.ddb, self.sched = FakeDdb(), FakeScheduler()
        scheduler.set_clients_for_testing(self.ddb, self.sched)

    def tearDown(self):
        for k in ENV:
            os.environ.pop(k, None)
        scheduler.set_clients_for_testing(None, None)

    def test_put_creates_schedule_and_record(self):
        record, error = scheduler.put_schedule("100001", DAILY_6)
        self.assertIsNone(error)
        self.assertEqual(record["expression"], "cron(0 6 * * ? *)")
        armed = self.sched.schedules["beacon-dev-export-100001"]
        self.assertEqual(armed["ScheduleExpression"], "cron(0 6 * * ? *)")
        self.assertEqual(armed["ScheduleExpressionTimezone"], "Europe/London")
        self.assertEqual(armed["State"], "ENABLED")
        self.assertEqual(armed["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertEqual(json.loads(armed["Target"]["Input"]),
                         {"job_id": "100001"})
        self.assertIn("100001", self.ddb.items)

    def test_put_existing_updates_in_place(self):
        scheduler.put_schedule("100001", DAILY_6)
        record, error = scheduler.put_schedule(
            "100001", dict(DAILY_6, expression="cron(0 7 * * ? *)"))
        self.assertIsNone(error)
        self.assertIn("update", self.sched.calls)
        self.assertEqual(self.sched.schedules["beacon-dev-export-100001"]
                         ["ScheduleExpression"], "cron(0 7 * * ? *)")

    def test_enabled_false_disables_the_schedule(self):
        scheduler.put_schedule("100001", dict(DAILY_6, enabled=False))
        self.assertEqual(self.sched.schedules["beacon-dev-export-100001"]["State"],
                         "DISABLED")
        self.assertFalse(self.ddb.items["100001"]["enabled"]["BOOL"])

    def test_rejects_non_cron_and_wrong_field_count(self):
        for bad in ("rate(1 day)", "cron(0 6 * * ?)", "0 6 * * ? *", "", None):
            _, error = scheduler.put_schedule(
                "100001", dict(DAILY_6, expression=bad))
            self.assertIsNotNone(error, f"accepted {bad!r}")
        self.assertEqual(self.sched.schedules, {})  # nothing armed

    def test_rejects_bad_timezone_shape(self):
        _, error = scheduler.put_schedule(
            "100001", dict(DAILY_6, timezone="not a zone!"))
        self.assertIn("timezone", error)

    def test_service_rejection_is_surfaced_and_nothing_stored(self):
        # the Scheduler service is the authority on cron detail / zone names —
        # a rejection must not leave a DynamoDB record claiming it is armed
        scheduler.set_clients_for_testing(self.ddb, FakeScheduler(reject=True))
        _, error = scheduler.put_schedule("100001", DAILY_6)
        self.assertIn("scheduler rejected", error)
        self.assertEqual(self.ddb.items, {})

    def test_get_returns_record_or_none(self):
        self.assertIsNone(scheduler.get_schedule("100001"))
        scheduler.put_schedule("100001", DAILY_6)
        record = scheduler.get_schedule("100001")
        self.assertEqual(record["timezone"], "Europe/London")
        self.assertTrue(record["enabled"])

    def test_delete_removes_both_and_is_idempotent(self):
        scheduler.put_schedule("100001", DAILY_6)
        ok, error = scheduler.delete_schedule("100001")
        self.assertTrue(ok)
        self.assertEqual(self.sched.schedules, {})
        self.assertIsNone(scheduler.get_schedule("100001"))
        ok, error = scheduler.delete_schedule("100001")  # already gone = fine
        self.assertTrue(ok)

    def test_pause_window_is_stored_and_returned(self):
        # UIFPE-231: the AWS schedule stays ENABLED — the window is record
        # data the scheduled-starter reads at fire time
        record, error = scheduler.put_schedule(
            "100001", dict(DAILY_6, paused_from="2026-09-01",
                           paused_until="2026-09-05"))
        self.assertIsNone(error)
        self.assertEqual(record["paused_until"], "2026-09-05")
        self.assertEqual(self.sched.schedules["beacon-dev-export-100001"]["State"],
                         "ENABLED")  # paused ≠ disabled
        stored = scheduler.get_schedule("100001")
        self.assertEqual(stored["paused_from"], "2026-09-01")
        self.assertEqual(stored["paused_until"], "2026-09-05")

    def test_resume_is_a_put_without_the_window(self):
        # PUT is a full replace: omitting the dates clears the stored window
        scheduler.put_schedule("100001", dict(DAILY_6, paused_until="2026-09-05"))
        record, error = scheduler.put_schedule("100001", DAILY_6)
        self.assertIsNone(error)
        stored = scheduler.get_schedule("100001")
        self.assertIsNone(stored["paused_until"])
        self.assertIsNone(stored["paused_from"])

    def test_pause_window_shapes_are_validated(self):
        for bad in (dict(DAILY_6, paused_from="2026-09-01"),  # until missing
                    dict(DAILY_6, paused_until="05-09-2026"),  # not ISO
                    dict(DAILY_6, paused_from="2026-09-09",
                         paused_until="2026-09-01")):  # until before from
            _, error = scheduler.put_schedule("100001", bad)
            self.assertIsNotNone(error, f"accepted {bad!r}")
        self.assertEqual(self.sched.schedules, {})  # nothing armed

    def test_list_returns_every_record(self):
        # the Exports list renders one schedule per row from a single call
        scheduler.put_schedule("100001", DAILY_6)
        scheduler.put_schedule("100002", dict(DAILY_6, enabled=False))
        rows = {r["job_id"]: r for r in scheduler.list_schedules()}
        self.assertEqual(set(rows), {"100001", "100002"})
        self.assertTrue(rows["100001"]["enabled"])
        self.assertFalse(rows["100002"]["enabled"])

    def test_unconfigured_env_reports_disabled(self):
        os.environ.pop("BEACON_SCHEDULER_GROUP")
        self.assertFalse(scheduler.enabled())
        _, error = scheduler.put_schedule("100001", DAILY_6)
        self.assertIn("not configured", error)


class TestScheduleRoutes(unittest.TestCase):
    def test_unpublished_export_has_no_schedule_surface(self):
        # the schedule routes hang off /catalog/{job_id} — an export that was
        # never published cannot be scheduled
        from fastapi.testclient import TestClient
        from control_panel.api import app
        client = TestClient(app)
        for method, url in (("get", "/catalog/999999/schedule"),
                            ("put", "/catalog/999999/schedule"),
                            ("delete", "/catalog/999999/schedule")):
            response = getattr(client, method)(
                url, **({"json": DAILY_6} if method == "put" else {}))
            self.assertEqual(response.status_code, 404, f"{method} {url}")


if __name__ == "__main__":
    unittest.main()
