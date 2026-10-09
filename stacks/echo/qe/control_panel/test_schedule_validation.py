# test_schedule_validation.py
# _validate_schedule_config's docstring said "Validate schedule.frequency and
# optional daily_slot". It validated only the second half: the function returned
# None on its third line whenever daily_slot was absent, so schedule.frequency
# was never looked at on any write path. Any string was accepted.
#
# That is how a live, enabled dev pipeline came to hold frequency `"12 hr"`.
# No EventBridge rule sends that payload, the dispatcher matches on equality,
# so the pipeline has never dispatched once since registration — and nothing
# reported it, because from the API's point of view the write succeeded.
#
# These tests pin the frequency half of the contract. The grandfather clause is
# the deliberate part: strict-on-create, strict-on-change, lenient on a
# no-op re-PUT of an already-bad stored value. uat and prod configs have not
# been surveyed (no credentials at the time of writing), so a blanket reject
# could have locked an unrelated field edit out of a pipeline whose frequency
# was already wrong. Grandfathering cannot break an environment we can't see.

import sys
import unittest
from unittest.mock import MagicMock

sys.modules.setdefault("oracledb", MagicMock())

import boto3  # noqa: E402

_real_resource, _real_client = boto3.resource, boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client = MagicMock(return_value=MagicMock())

import pipeline_controller as pc  # noqa: E402
import schedule_vocabulary as sv  # noqa: E402

boto3.resource, boto3.client = _real_resource, _real_client


def _sched(freq=None, **extra):
    sched = dict(extra)
    if freq is not None:
        sched["frequency"] = freq
    return {"pipeline_id": "fipm.fpi_par_batch", "schedule": sched}


class TestFrequencyIsValidatedAtAll(unittest.TestCase):
    """The regression that let `12 hr` through."""

    def test_the_value_that_reached_dev_is_now_rejected(self):
        err = pc._validate_schedule_config(_sched("12 hr"))
        self.assertIsNotNone(err, "`12 hr` must not be accepted again")
        self.assertIn("12 hr", err)

    def test_rejected_even_though_daily_slot_is_absent(self):
        # The precise shape of the old bug: no daily_slot meant no validation.
        body = _sched("nightly")
        self.assertNotIn("daily_slot", body["schedule"])
        self.assertIsNotNone(pc._validate_schedule_config(body))

    def test_every_dispatchable_frequency_is_accepted(self):
        for freq in sv.DISPATCHABLE_FREQUENCIES:
            body = _sched(freq)
            if freq == "daily":
                body["schedule"]["daily_slot"] = 3
            self.assertIsNone(pc._validate_schedule_config(body), freq)

    def test_wrong_case_is_rejected_not_silently_accepted(self):
        # The dispatcher compares the stored string to the EventBridge payload
        # with plain `!=`, so "Hourly" would pass a case-insensitive check and
        # then never dispatch — the same class of bug as `12 hr`, just quieter.
        err = pc._validate_schedule_config(_sched("Hourly"))
        self.assertIsNotNone(err)
        self.assertIn("hourly", err)  # the canonical spelling is offered back

    def test_stray_whitespace_is_rejected(self):
        self.assertIsNotNone(pc._validate_schedule_config(_sched("hourly ")))
        self.assertIsNotNone(pc._validate_schedule_config(_sched(" 4hr")))

    def test_retired_nightly_is_rejected(self):
        self.assertIsNotNone(pc._validate_schedule_config(_sched("nightly")))

    def test_near_misses_are_rejected(self):
        for freq in ("4 hr", "12hr", "half-hourly", "everyhour", "gold_hourly"):
            self.assertIsNotNone(pc._validate_schedule_config(_sched(freq)), freq)


class TestUnscheduledPipelinesStayRegisterable(unittest.TestCase):
    """Absent frequency means adhoc, not invalid."""

    def test_no_schedule_block_is_fine(self):
        self.assertIsNone(pc._validate_schedule_config({"pipeline_id": "x"}))

    def test_empty_schedule_block_is_fine(self):
        self.assertIsNone(pc._validate_schedule_config(_sched()))

    def test_explicit_empty_string_is_fine(self):
        self.assertIsNone(pc._validate_schedule_config(_sched("")))


class TestGrandfatherClause(unittest.TestCase):
    """Strict on create and on change; lenient on an unchanged bad value."""

    def test_create_rejects_a_bad_value(self):
        # No `existing` — this is a create, nothing to grandfather.
        self.assertIsNotNone(pc._validate_schedule_config(_sched("12 hr")))

    def test_update_allows_an_unchanged_bad_value(self):
        existing = {"schedule": {"frequency": "12 hr"}}
        err = pc._validate_schedule_config(_sched("12 hr"), existing)
        self.assertIsNone(
            err,
            "editing an unrelated field on a pipeline whose frequency was "
            "already wrong must not be blocked",
        )

    def test_grandfathering_requires_an_exact_match(self):
        # " 12 HR " is a different bad string, i.e. a change to another value
        # that also never dispatches. Only a genuine no-op is grandfathered.
        existing = {"schedule": {"frequency": "12 hr"}}
        self.assertIsNotNone(pc._validate_schedule_config(_sched(" 12 HR "), existing))

    def test_update_rejects_a_change_from_one_bad_value_to_another(self):
        existing = {"schedule": {"frequency": "12 hr"}}
        self.assertIsNotNone(pc._validate_schedule_config(_sched("13 hr"), existing))

    def test_update_accepts_a_change_from_bad_to_good(self):
        # The remediation path for the live record.
        existing = {"schedule": {"frequency": "12 hr"}}
        self.assertIsNone(pc._validate_schedule_config(_sched("4hr"), existing))

    def test_update_rejects_a_change_from_good_to_bad(self):
        existing = {"schedule": {"frequency": "4hr"}}
        self.assertIsNotNone(pc._validate_schedule_config(_sched("12 hr"), existing))

    def test_a_missing_existing_record_is_a_create(self):
        # First-time PUT: update_pipeline's get_item returns None.
        self.assertIsNotNone(pc._validate_schedule_config(_sched("12 hr"), None))

    def test_an_existing_record_without_a_schedule_is_not_a_licence(self):
        self.assertIsNotNone(pc._validate_schedule_config(_sched("12 hr"), {}))

    def test_grandfathering_is_logged(self):
        existing = {"schedule": {"frequency": "12 hr"}}
        with self.assertLogs(pc.logger, level="WARNING") as caught:
            pc._validate_schedule_config(_sched("12 hr"), existing)
        self.assertTrue(
            any("12 hr" in line for line in caught.output),
            "a grandfathered bad frequency must leave a trace",
        )


class TestDailySlotRulesStillHold(unittest.TestCase):
    """The half that already worked, kept working."""

    def test_slot_without_daily_is_rejected(self):
        err = pc._validate_schedule_config(_sched("hourly", daily_slot=3))
        self.assertIsNotNone(err)
        self.assertIn("daily", err)

    def test_slot_must_be_an_integer(self):
        err = pc._validate_schedule_config(_sched("daily", daily_slot="three"))
        self.assertIsNotNone(err)
        self.assertIn("integer", err)

    def test_slot_out_of_range_is_rejected(self):
        for slot in (-1, 12, 99):
            self.assertIsNotNone(
                pc._validate_schedule_config(_sched("daily", daily_slot=slot)), slot
            )

    def test_slot_in_range_is_accepted(self):
        for slot in range(pc.DAILY_SLOT_COUNT):
            self.assertIsNone(
                pc._validate_schedule_config(_sched("daily", daily_slot=slot)), slot
            )

    def test_daily_without_a_slot_is_accepted_and_auto_assigned(self):
        # _ensure_daily_slot fills it in after validation.
        self.assertIsNone(pc._validate_schedule_config(_sched("daily")))
        item = _sched("daily")
        pc._ensure_daily_slot(item)
        self.assertIn(item["schedule"]["daily_slot"], range(pc.DAILY_SLOT_COUNT))

    def test_a_bad_frequency_is_reported_before_a_bad_slot(self):
        # Both wrong: the frequency is the root cause and the more useful error.
        err = pc._validate_schedule_config(_sched("12 hr", daily_slot=99))
        self.assertIsNotNone(err)
        self.assertIn("12 hr", err)


if __name__ == "__main__":
    unittest.main()
