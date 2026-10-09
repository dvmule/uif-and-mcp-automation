# test_schedule_vocabulary.py
# The cadence vocabulary is a closed set, and this file is the thing that keeps
# it closed. Before schedule_vocabulary.py existed the same list was written out
# by hand in six places and no two of them agreed:
#
#   scheduler.tf                     30min hourly 4hr daily weekly reconcile
#                                    gold gold-hourly gold-reconcile
#   scheduler_dispatcher.py:253      ... no gold-hourly
#   register_pipeline.py:144         ... no 4hr
#   set_pipeline_frequency.py:20     ... no 4hr
#   pipeline_controller.py:355       30min hourly 4hr        (sizing guard)
#   ui/.../profileHelpers.ts:109     30min hourly            (same guard, drifted)
#
# and pipeline_controller._validate_schedule_config, the one place a bad value
# could have been caught on the way in, validated the frequency not at all — so
# `"12 hr"` reached a live enabled dev pipeline that has never once dispatched.
#
# The first test below is the important one: it reads the EventBridge rules out
# of the Terraform and asserts the constant equals what AWS is actually wired to
# send. A rule added without updating the vocabulary fails here, and so does a
# vocabulary entry no rule dispatches.

from __future__ import annotations

import re
import unittest
from pathlib import Path

import schedule_vocabulary as sv

_SCHEDULER_TF = (
    Path(__file__).resolve().parents[2]
    / "infrastructure" / "terraform" / "scheduler.tf"
)


def _frequencies_dispatched_by_terraform() -> set[str]:
    """Every frequency literal an EventBridge target actually sends.

    Matches the payload on aws_cloudwatch_event_target.input, e.g.
        input = jsonencode({ frequency = "4hr" })
        input = jsonencode({ frequency = "daily", daily_slot = tonumber(...) })
    """
    text = _SCHEDULER_TF.read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(r'jsonencode\(\{\s*frequency\s*=\s*"([^"]+)"', text)
    }


class TestVocabularyMatchesTerraform(unittest.TestCase):
    """The constant and the infrastructure must not drift apart."""

    def test_scheduler_tf_exists(self):
        # A rename that moves this file should fail loudly here rather than
        # silently turn the drift test into a no-op over an empty set.
        self.assertTrue(_SCHEDULER_TF.is_file(), f"missing {_SCHEDULER_TF}")

    def test_every_dispatched_frequency_is_in_the_vocabulary(self):
        dispatched = _frequencies_dispatched_by_terraform()
        self.assertTrue(dispatched, "parsed no frequencies out of scheduler.tf")
        undeclared = dispatched - set(sv.DISPATCHABLE_FREQUENCIES)
        self.assertEqual(
            set(), undeclared,
            f"scheduler.tf dispatches {sorted(undeclared)}, which "
            f"schedule_vocabulary does not declare",
        )

    def test_every_vocabulary_entry_is_actually_dispatched(self):
        dispatched = _frequencies_dispatched_by_terraform()
        undispatched = set(sv.DISPATCHABLE_FREQUENCIES) - dispatched
        self.assertEqual(
            set(), undispatched,
            f"schedule_vocabulary declares {sorted(undispatched)}, which no "
            f"EventBridge rule in scheduler.tf sends — a pipeline set to one "
            f"of those would validate and then never run",
        )


class TestVocabularyShape(unittest.TestCase):
    def test_the_two_groups_partition_the_whole(self):
        self.assertEqual(
            set(sv.DISPATCHABLE_FREQUENCIES),
            set(sv.BRONZE_SILVER_FREQUENCIES) | set(sv.RECONCILE_FREQUENCIES),
        )

    def test_the_groups_do_not_overlap(self):
        groups = (
            set(sv.BRONZE_SILVER_FREQUENCIES),
            set(sv.RECONCILE_FREQUENCIES),
        )
        total = sum(len(g) for g in groups)
        self.assertEqual(total, len(set().union(*groups)))

    def test_high_cadence_is_a_subset_of_bronze_silver(self):
        self.assertLessEqual(
            set(sv.HIGH_CADENCE_FREQUENCIES), set(sv.BRONZE_SILVER_FREQUENCIES)
        )

    def test_nightly_is_retired(self):
        # ADR-0015 D5 closed the vocabulary and retired `nightly`.
        self.assertNotIn("nightly", sv.DISPATCHABLE_FREQUENCIES)


class TestIsDispatchable(unittest.TestCase):
    def test_accepts_every_declared_value(self):
        for freq in sv.DISPATCHABLE_FREQUENCIES:
            self.assertTrue(sv.is_dispatchable(freq), freq)

    def test_rejects_the_value_that_reached_production(self):
        self.assertFalse(sv.is_dispatchable("12 hr"))

    def test_rejects_retired_and_misspelled_values(self):
        for freq in ("nightly", "12hr", "4 hr", "hourly ", "", "daily-ish"):
            self.assertFalse(sv.is_dispatchable(freq), repr(freq))

    def test_rejects_none_and_non_strings(self):
        for freq in (None, 4, [], {}):
            self.assertFalse(sv.is_dispatchable(freq), repr(freq))

    def test_matching_is_exact_because_the_dispatcher_is_exact(self):
        # scheduler_dispatcher compares `schedule.get("frequency") != frequency`
        # against the literal EventBridge payload — no .lower(), no .strip(),
        # in either the Bronze/Silver branch (L383) or the Gold one (L108).
        # So a stored "HOURLY" or "hourly " never dispatches, and accepting
        # either here would recreate the `12 hr` bug in a subtler form: a
        # config that passes validation and then silently never runs.
        for freq in ("HOURLY", "Hourly", "Gold-Reconcile", "30MIN"):
            self.assertFalse(sv.is_dispatchable(freq), repr(freq))

    def test_the_canonical_values_are_all_lowercase(self):
        # Which is what makes exact matching a usable rule rather than a trap.
        for freq in sv.DISPATCHABLE_FREQUENCIES:
            self.assertEqual(freq, freq.lower().strip(), repr(freq))


class TestUnknownFrequencyError(unittest.TestCase):
    def test_returns_none_for_a_good_value(self):
        self.assertIsNone(sv.unknown_frequency_error("4hr"))

    def test_returns_none_when_absent(self):
        # An absent frequency means "not scheduled" — adhoc pipelines driven by
        # POST /pipelines/{id}/run are legitimate and must stay registerable.
        self.assertIsNone(sv.unknown_frequency_error(None))
        self.assertIsNone(sv.unknown_frequency_error(""))

    def test_names_the_offending_value_and_the_alternatives(self):
        err = sv.unknown_frequency_error("12 hr")
        self.assertIsNotNone(err)
        self.assertIn("12 hr", err)
        self.assertIn("4hr", err)

    def test_says_the_pipeline_would_never_run(self):
        # The error has to explain the consequence, not just say "invalid" —
        # this is the message that would have prevented the live `12 hr` record.
        self.assertIn("never", sv.unknown_frequency_error("12 hr").lower())


class TestSingleSourceOfTruth(unittest.TestCase):
    """The six hand-written copies must now defer to the constant."""

    def test_controller_sizing_guard_uses_the_vocabulary(self):
        import pipeline_controller as pc
        self.assertEqual(
            set(pc.HIGH_CADENCE_FREQUENCIES), set(sv.HIGH_CADENCE_FREQUENCIES)
        )

    def test_controller_daily_slot_count_is_not_a_second_copy(self):
        # DAILY_SLOT_COUNT was defined twice — pipeline_controller.py:424 and
        # daily_schedule.py:10 — and the dispatcher trusted one of them.
        import daily_schedule
        import pipeline_controller as pc
        self.assertEqual(pc.DAILY_SLOT_COUNT, daily_schedule.DAILY_SLOT_COUNT)

    def test_register_pipeline_cli_offers_every_bronze_silver_cadence(self):
        import register_pipeline
        self.assertEqual(
            set(register_pipeline.SCHEDULABLE_FREQUENCIES),
            set(sv.BRONZE_SILVER_FREQUENCIES),
        )

    def test_set_frequency_cli_offers_every_bronze_silver_cadence(self):
        import set_pipeline_frequency
        self.assertEqual(
            set(set_pipeline_frequency.VALID_FREQUENCIES),
            set(sv.BRONZE_SILVER_FREQUENCIES),
        )

    def test_dispatcher_error_text_lists_the_whole_vocabulary(self):
        # The handler's ValueError enumerated the accepted keys by hand and
        # omitted gold-hourly.
        import scheduler_dispatcher
        text = scheduler_dispatcher.FREQUENCY_HELP
        for freq in sv.DISPATCHABLE_FREQUENCIES:
            self.assertIn(freq, text, f"{freq} missing from the help text")


class TestTypeScriptMirrorDoesNotDrift(unittest.TestCase):
    """`ui/src/lib/scheduleVocabulary.ts` hand-copies these lists.

    A mirror across a language boundary cannot be imported, so it gets asserted
    instead. `dailySchedule.ts` set the mirroring precedent but shipped with no
    parity test, which is why `DAILY_SLOT_COUNT = 12` existed in three places at
    once — two Python, one TypeScript.
    """

    _TS = (
        Path(__file__).resolve().parents[2]
        / "ui" / "src" / "lib" / "scheduleVocabulary.ts"
    )

    def _ts_array(self, name: str) -> list[str]:
        text = self._TS.read_text(encoding="utf-8")
        m = re.search(rf"{name}[^=]*=\s*\[(.*?)\]", text, re.DOTALL)
        self.assertIsNotNone(m, f"could not find {name} in {self._TS.name}")
        return re.findall(r"'([^']+)'", m.group(1))

    def test_the_mirror_exists(self):
        self.assertTrue(self._TS.is_file(), f"missing {self._TS}")

    def test_bronze_silver_matches(self):
        self.assertEqual(
            list(sv.BRONZE_SILVER_FREQUENCIES),
            self._ts_array("BRONZE_SILVER_FREQUENCIES"),
        )

    def test_reconcile_matches(self):
        self.assertEqual(
            list(sv.RECONCILE_FREQUENCIES), self._ts_array("RECONCILE_FREQUENCIES")
        )

    def test_high_cadence_matches(self):
        # The divergence that actually shipped: the UI's copy of the sizing
        # guard omitted 4hr, so the API warned where the UI stayed silent.
        self.assertEqual(
            list(sv.HIGH_CADENCE_FREQUENCIES),
            self._ts_array("HIGH_CADENCE_FREQUENCIES"),
        )

    def test_daily_slot_count_matches_the_typescript_mirror(self):
        import daily_schedule
        ts = (
            Path(__file__).resolve().parents[2]
            / "ui" / "src" / "lib" / "dailySchedule.ts"
        ).read_text(encoding="utf-8")
        m = re.search(r"DAILY_SLOT_COUNT\s*=\s*(\d+)", ts)
        self.assertIsNotNone(m, "DAILY_SLOT_COUNT not found in dailySchedule.ts")
        self.assertEqual(daily_schedule.DAILY_SLOT_COUNT, int(m.group(1)))


if __name__ == "__main__":
    unittest.main()
