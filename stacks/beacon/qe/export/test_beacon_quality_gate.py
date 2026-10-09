# test_beacon_quality_gate.py
# Unit tests for beacon/quality_gate.py — the fail-closed batch gate between
# transform and rendering/delivery.

import unittest

from beacon_export import quality_gate

GATES = {"min_rows": 10, "max_rejection_pct": 2.0, "row_count_delta_pct_warn": 40}


def report(status="succeeded", output_rows=100, rejected_rows=0):
    return {"status": status, "output_rows": output_rows,
            "rejected_rows": rejected_rows}


class TestEvaluate(unittest.TestCase):
    def test_clean_run_passes(self):
        result = quality_gate.evaluate(report(rejected_rows=2), GATES)
        self.assertTrue(result.passed)
        self.assertEqual(result.breaches, [])

    def test_failed_transform_breaches(self):
        result = quality_gate.evaluate(report(status="failed"), GATES)
        self.assertFalse(result.passed)
        self.assertIn("status", result.breaches[0])

    def test_skipped_empty_input_breaches(self):
        result = quality_gate.evaluate(report(status="skipped_empty_input"), GATES)
        self.assertFalse(result.passed)

    def test_row_floor(self):
        result = quality_gate.evaluate(report(output_rows=9), GATES)
        self.assertFalse(result.passed)
        self.assertIn("row floor", result.breaches[0])

    def test_rejection_ceiling(self):
        # 3 rejected of 103 total = 2.91% > 2.0%
        result = quality_gate.evaluate(report(rejected_rows=3), GATES)
        self.assertFalse(result.passed)
        self.assertIn("rejection ceiling", result.breaches[0])

    def test_rejection_at_threshold_passes(self):
        # 2 of 100 = 2.0% == threshold: not a breach
        result = quality_gate.evaluate(report(output_rows=98, rejected_rows=2), GATES)
        self.assertTrue(result.passed)

    def test_all_rows_rejected_breaches_both(self):
        result = quality_gate.evaluate(report(output_rows=0, rejected_rows=50), GATES)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.breaches), 2)

    def test_delta_warns_but_does_not_fail(self):
        result = quality_gate.evaluate(report(output_rows=100), GATES,
                                       previous_output_rows=200)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("50.0%", result.warnings[0])

    def test_delta_within_threshold_no_warning(self):
        result = quality_gate.evaluate(report(output_rows=100), GATES,
                                       previous_output_rows=90)
        self.assertEqual(result.warnings, [])

    def test_no_previous_count_no_delta_check(self):
        result = quality_gate.evaluate(report(), GATES, previous_output_rows=None)
        self.assertEqual(result.warnings, [])

    def test_to_dict_round_trip(self):
        d = quality_gate.evaluate(report(output_rows=0), GATES).to_dict()
        self.assertFalse(d["passed"])
        self.assertIsInstance(d["breaches"], list)


class TestIncrementalMode(unittest.TestCase):
    # UIFPE-168: a windowed (changed-rows-only) extract has legitimately
    # small or empty output — the full-snapshot gates must not call that a
    # breach, while genuine data-quality signals still fire.

    def test_empty_increment_passes_the_row_floor(self):
        result = quality_gate.evaluate(report(output_rows=0), GATES,
                                       incremental=True)
        self.assertTrue(result.passed)
        self.assertEqual(result.breaches, [])

    def test_row_floor_still_applies_to_full_loads(self):
        # incremental=False is the default — the baseline full extract of an
        # incremental feed goes through the normal floor
        result = quality_gate.evaluate(report(output_rows=0), GATES)
        self.assertFalse(result.passed)

    def test_rejection_ceiling_still_applies(self):
        # 3 rejected of 13 total — bad rows in a small window are still an
        # upstream regression
        result = quality_gate.evaluate(
            report(output_rows=10, rejected_rows=3), GATES, incremental=True)
        self.assertFalse(result.passed)
        self.assertIn("rejection ceiling", result.breaches[0])

    def test_failed_transform_still_breaches(self):
        result = quality_gate.evaluate(report(status="failed"), GATES,
                                       incremental=True)
        self.assertFalse(result.passed)

    def test_delta_warning_suppressed(self):
        # calibrated against full snapshots — meaningless for a window
        result = quality_gate.evaluate(report(output_rows=5), GATES,
                                       previous_output_rows=200,
                                       incremental=True)
        self.assertTrue(result.passed)
        self.assertEqual(result.warnings, [])
