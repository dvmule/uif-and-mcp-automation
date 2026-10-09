# test_gold_telemetry.py
# UIFPE-543: the gold layer's CloudWatch Embedded Metric Format documents.
# The builders are tested directly. The writers that print them (transform,
# reconcile, dispatcher) run only under Spark / Lambda, so their wiring is
# pinned on source, like test_gold_bookmark_runtime; the Terraform that has to
# deliver the module to BOTH runtimes is pinned the same way, because a library
# missing from --extra-py-files or the zip is an ImportError at run time and
# nothing else fails first.

import ast
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import gold_cdc
import gold_telemetry as gt
from gold_frequencies import WEEKLY_FREQUENCY, interval_minutes

STACK = Path(__file__).resolve().parents[2]
REPO = STACK.parents[1]


def _read(rel):
    return (STACK / rel).read_text(encoding="utf-8")


TRANSFORM = _read("src/apps/pipeline/generic_gold_transform.py")
RECONCILE = _read("src/apps/pipeline/generic_gold_reconcile.py")
DISPATCHER = _read("src/apps/lambdas/gold_dispatcher.py")
SYNC = _read("src/apps/cli/sync_pipelines.py")
CONTROLLER = _read("src/apps/control_panel/pipeline_controller.py")
LIBRARY = _read("src/libs/gold_telemetry.py")
GLUE_TF = _read("infrastructure/terraform-gold/glue_jobs.tf")
DISPATCHER_TF = _read("infrastructure/terraform-gold/dispatcher.tf")
WORKFLOW = (REPO / ".github" / "workflows" / "deploy-echo-gold-dev.yml"
            ).read_text(encoding="utf-8")

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def _assert_valid_emf(test, doc):
    """What CloudWatch needs to extract metrics: one namespace block, every
    dimension key present as a top-level string, every metric present as a
    top-level number."""
    aws = doc["_aws"]
    test.assertEqual(aws["Timestamp"], int(NOW.timestamp() * 1000))
    (block,) = aws["CloudWatchMetrics"]
    test.assertEqual(block["Namespace"], "UIF/Gold")
    for keys in block["Dimensions"]:
        for key in keys:
            test.assertIsInstance(doc.get(key), str, f"dimension {key}")
    for metric in block["Metrics"]:
        value = doc.get(metric["Name"])
        test.assertIsInstance(value, (int, float), f"metric {metric['Name']}")
        test.assertNotIsInstance(value, bool)
    return block


class RunDocumentTest(unittest.TestCase):

    def test_is_valid_emf_with_a_fleet_rollup(self):
        doc = gt.run_document("dev", "product", "SUCCEEDED", rows_read=10,
                              rows_written=4, affected_keys=7,
                              duration_seconds=95, now=NOW)
        block = _assert_valid_emf(self, doc)
        # per-entity AND fleet-wide: one alarm on RunCount{Status=FAILED}
        self.assertEqual(block["Dimensions"],
                         [["Env", "GoldId", "Status"], ["Env", "Status"]])
        self.assertEqual([m["Name"] for m in block["Metrics"]],
                         ["RunCount", "RowsRead", "RowsWritten",
                          "AffectedKeys", "DurationSeconds"])
        self.assertEqual((doc["RunCount"], doc["RowsRead"], doc["RowsWritten"],
                          doc["AffectedKeys"], doc["DurationSeconds"]),
                         (1, 10, 4, 7, 95))
        self.assertEqual((doc["Env"], doc["GoldId"], doc["Status"]),
                         ("dev", "product", "SUCCEEDED"))

    def test_status_is_a_finite_dimension(self):
        # the reconcile writes "SKIPPED (no orphans)"; a dimension value must
        # not carry free text, but the detail is still in the log line
        doc = gt.run_document("dev", "product", "SKIPPED (no orphans)", now=NOW)
        self.assertEqual(doc["Status"], "SKIPPED")
        self.assertEqual(doc["StatusDetail"], "SKIPPED (no orphans)")
        for junk in ("", "   ", None, 7):
            with self.subTest(status=junk):
                self.assertEqual(gt.run_document("dev", "x", junk, now=NOW)["Status"],
                                 "UNKNOWN")

    def test_error_is_truncated_and_absent_when_none(self):
        doc = gt.run_document("dev", "product", "FAILED", error="x" * 1000, now=NOW)
        self.assertEqual(len(doc["ErrorMessage"]), gt._ERROR_CHARS)
        self.assertNotIn("ErrorMessage",
                         gt.run_document("dev", "product", "SUCCEEDED", now=NOW))

    def test_counts_tolerate_none_and_decimal(self):
        doc = gt.run_document("dev", "product", "SUCCEEDED", rows_read=None,
                              rows_written=Decimal("12"), now=NOW)
        self.assertEqual((doc["RowsRead"], doc["RowsWritten"]), (0, 12))
        _assert_valid_emf(self, doc)


class TickDocumentTest(unittest.TestCase):

    def _tick(self, **kw):
        base = dict(frequency="gold-24h", interval_minutes=1440,
                    last_success_at=None, bookmark=None, dispatched=False,
                    now=NOW)
        base.update(kw)
        return gt.tick_document("dev", "product", **base)

    def test_is_valid_emf_with_a_fleet_rollup(self):
        doc = self._tick(last_success_at=_ago(minutes=30), dispatched=True)
        block = _assert_valid_emf(self, doc)
        self.assertEqual(block["Dimensions"], [["Env", "GoldId"], ["Env"]])
        self.assertEqual(doc["Dispatched"], 1)

    def test_never_run_omits_the_ages_rather_than_faking_them(self):
        doc = self._tick(last_success_at=None)
        self.assertEqual(doc["NeverRun"], 1)
        names = [m["Name"] for m in doc["_aws"]["CloudWatchMetrics"][0]["Metrics"]]
        for absent in ("MinutesSinceLastSuccess", "FreshnessBreached",
                       "BookmarkAgeMinutes"):
            self.assertNotIn(absent, names)
            self.assertNotIn(absent, doc)
        _assert_valid_emf(self, doc)

    def test_fresh_entity(self):
        doc = self._tick(last_success_at=_ago(minutes=30))
        self.assertEqual(doc["NeverRun"], 0)
        self.assertEqual(doc["MinutesSinceLastSuccess"], 30)
        self.assertEqual(doc["FreshnessBreached"], 0)
        self.assertEqual(doc["FreshnessLimitMinutes"], 2160)  # 1.5 x daily

    def test_breach_is_strictly_past_the_limit(self):
        at_limit = self._tick(last_success_at=_ago(minutes=2160))
        past = self._tick(last_success_at=_ago(minutes=2161))
        self.assertEqual(at_limit["FreshnessBreached"], 0)
        self.assertEqual(past["FreshnessBreached"], 1)

    def test_weekly_limit_is_a_week_and_a_half(self):
        doc = self._tick(frequency=WEEKLY_FREQUENCY,
                         interval_minutes=interval_minutes(WEEKLY_FREQUENCY),
                         last_success_at=_ago(days=8))
        self.assertEqual(doc["FreshnessLimitMinutes"], 15120)
        self.assertEqual(doc["FreshnessBreached"], 0)

    def test_unknown_cadence_reports_age_but_no_breach(self):
        doc = self._tick(interval_minutes=None, last_success_at=_ago(minutes=5))
        self.assertEqual(doc["MinutesSinceLastSuccess"], 5)
        self.assertNotIn("FreshnessBreached", doc)
        self.assertIsNone(doc["FreshnessLimitMinutes"])

    def test_bookmark_age(self):
        doc = self._tick(bookmark=_ago(hours=2))
        self.assertEqual(doc["BookmarkAgeMinutes"], 120)
        self.assertNotIn("BookmarkAgeMinutes", self._tick(bookmark="not a date"))

    def test_freshness_source_is_recorded(self):
        doc = self._tick(last_success_at=_ago(minutes=1),
                         freshness_source="last_transform_at")
        self.assertEqual(doc["FreshnessSource"], "last_transform_at")


class ParsingTest(unittest.TestCase):

    def test_iso_variants(self):
        expect = datetime(2026, 9, 23, 6, 30, tzinfo=timezone.utc)
        for text in ("2026-09-23T06:30:00+00:00", "2026-09-23T06:30:00Z",
                     "2026-09-23T06:30:00", "2026-09-23T08:30:00+02:00"):
            with self.subTest(text=text):
                self.assertEqual(gt.parse_iso(text), expect)

    def test_junk_is_none_never_an_exception(self):
        for junk in (None, "", "  ", "yesterday", 42, Decimal("1")):
            with self.subTest(junk=junk):
                self.assertIsNone(gt.parse_iso(junk))
                self.assertIsNone(gt.minutes_between(junk, NOW))

    def test_a_clock_ahead_of_ours_reads_zero_not_negative(self):
        self.assertEqual(gt.minutes_between(_ago(minutes=-10), NOW), 0)

    def test_limit_needs_a_positive_interval(self):
        self.assertIsNone(gt.freshness_limit_minutes(None))
        self.assertIsNone(gt.freshness_limit_minutes(0))
        self.assertEqual(gt.freshness_limit_minutes(60), 90)


class EmitTest(unittest.TestCase):

    def test_one_compact_line_starting_with_the_marker(self):
        doc = gt.run_document("dev", "product", "SUCCEEDED", now=NOW)
        line = gt.render(doc)
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith('{"_aws":'))
        self.assertEqual(json.loads(line), doc)

    def test_emit_writes_exactly_once_and_survives_decimals(self):
        written = []
        doc = gt.run_document("dev", "product", "SUCCEEDED", now=NOW)
        doc["Extra"] = Decimal("1.5")
        out = gt.emit(doc, write=written.append)
        self.assertEqual(written, [out])
        self.assertEqual(json.loads(out)["Extra"], "1.5")

    def test_library_is_stdlib_only(self):
        """It ships to Glue via --extra-py-files AND to Lambda in a flat zip;
        a boto3 or gold_frequencies import would tie it to one of them."""
        tree = ast.parse(LIBRARY)
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertEqual(names, {"json", "datetime", "__future__"})


class WritersArePinnedTest(unittest.TestCase):
    """The Glue scripts and the Lambda cannot be imported here; the calls are."""

    def test_transform_emits_beside_both_metrics_rows(self):
        self.assertIn("import gold_telemetry", TRANSFORM)
        self.assertEqual(TRANSFORM.count("gold_telemetry.emit(gold_telemetry.run_document("), 2)
        # the real outcome, the real error, the real end-of-run clock
        self.assertRegex(TRANSFORM, r"run_document\(\s*ENV, GOLD_ID, run_status,"
                                    r"[^)]*error=error, now=completed_at\)")
        self.assertRegex(TRANSFORM, r'run_document\(\s*ENV, GOLD_ID, "SKIPPED",')

    def test_reconcile_emits_beside_both_metrics_rows(self):
        self.assertIn("import gold_telemetry", RECONCILE)
        self.assertEqual(RECONCILE.count("gold_telemetry.emit(gold_telemetry.run_document("), 2)
        self.assertRegex(RECONCILE, r"run_document\(\s*ENV, GOLD_ID, run_status,"
                                    r"[^)]*error=error, now=completed_at\)")
        self.assertRegex(RECONCILE, r"run_document\(\s*ENV, GOLD_ID, status_msg,")

    def test_dispatcher_measures_on_both_return_paths(self):
        # the fail-closed return AND the normal one: a sweep that dispatches
        # nothing is when freshness has to keep counting
        calls = re.findall(r"^\s+_emit_tick_telemetry\(items, frequency, now,",
                           DISPATCHER, re.M)
        self.assertEqual(len(calls), 2)
        self.assertIn("from gold_telemetry import emit, tick_document", DISPATCHER)
        # printed, not logged: a logger prefix breaks EMF extraction
        self.assertIn("emit(tick_document(", DISPATCHER)
        self.assertNotRegex(DISPATCHER, r"logger\.\w+\(\s*(?:render|json\.dumps)\(")

    def test_dispatcher_reads_the_transform_heartbeat_first(self):
        self.assertIn('source = ("last_transform_at" if meta.get("last_transform_at")',
                      DISPATCHER)
        self.assertIn('else "last_run_at")', DISPATCHER)

    def test_transform_stamps_its_own_heartbeat(self):
        """metadata.last_run_at is also written by the reconcile, so the
        freshness signal would see a nightly reconcile as a healthy transform.
        The transform's state write carries a second stamp only it writes."""
        for bookmark in ("2026-09-23T06:30:00+00:00", None):
            with self.subTest(bookmark=bookmark):
                args = gold_cdc.build_bookmark_update_args(bookmark, "jr_1", "2026-09-23T06:45:00+00:00")
                self.assertIn("#meta.last_transform_at = :ts", args["UpdateExpression"])
                self.assertIn("#meta.last_run_at = :ts", args["UpdateExpression"])

    def test_heartbeat_survives_a_sync_and_a_put(self):
        block = re.search(r"RUNTIME_PATHS = \[(.*?)\n\]", SYNC, re.S).group(1)
        self.assertIn('"metadata.last_transform_at",', block)
        block = re.search(r"_METADATA_AUDIT_FIELDS = \((.*?)\n\)", CONTROLLER, re.S).group(1)
        self.assertIn('"last_transform_at",', block)


class DeliveryIsPinnedTest(unittest.TestCase):
    """Terraform has to carry the module to both runtimes."""

    def test_library_is_an_s3_object_from_the_libs_dir(self):
        block = re.search(r'resource "aws_s3_object" "gold_telemetry_lib" \{.*?\n\}',
                          GLUE_TF, re.S)
        self.assertIsNotNone(block)
        self.assertIn('source = "${path.module}/../../src/libs/gold_telemetry.py"',
                      block.group(0))
        self.assertIn('key    = "${local.gold_scripts_prefix}/gold_telemetry.py"',
                      block.group(0))

    def test_both_jobs_import_both_libraries(self):
        join = re.search(r"gold_extra_py_files = join\(\",\", \[(.*?)\]\)", GLUE_TF, re.S)
        self.assertIsNotNone(join)
        # gold_glue_uri lookups, not literal paths, since UIFPE-576 1b.
        self.assertIn('local.gold_glue_uri["gold_cdc"]', join.group(1))
        self.assertIn('local.gold_glue_uri["gold_telemetry"]', join.group(1))
        self.assertEqual(
            len(re.findall(r'"--extra-py-files"\s*=\s*local\.gold_extra_py_files', GLUE_TF)), 2)
        # no job may still point at the single-file string
        self.assertNotRegex(GLUE_TF, r'"--extra-py-files"\s*=\s*"s3://')
        self.assertEqual(GLUE_TF.count("aws_s3_object.gold_telemetry_lib,"), 2)

    def test_glue_observability_flags_on_both_jobs(self):
        for flag in ("--enable-observability-metrics", "--enable-job-insights",
                     "--enable-continuous-cloudwatch-log"):
            with self.subTest(flag=flag):
                self.assertEqual(
                    len(re.findall(rf'"{flag}"\s*=\s*"true"', GLUE_TF)), 2,
                    f"{flag} must be true on the transform AND the reconcile")

    def test_dispatcher_zip_carries_the_module(self):
        archive = re.search(r'data "archive_file" "gold_dispatcher".*?\n\}', DISPATCHER_TF, re.S)
        self.assertIn('filename = "gold_telemetry.py"', archive.group(0))
        self.assertIn('file("${path.module}/../../src/libs/gold_telemetry.py")', archive.group(0))

    def test_an_edit_to_the_library_redeploys_the_gold_root(self):
        self.assertIn('- "stacks/echo/src/libs/gold_telemetry.py"', WORKFLOW)


if __name__ == "__main__":
    unittest.main()
