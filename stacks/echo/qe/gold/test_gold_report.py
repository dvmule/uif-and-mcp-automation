# test_gold_report.py
# UIFPE-544 PR 3: the monthly gold leadership pack.
#
# Every figure here is one somebody quotes in a meeting, so each definition is
# pinned: which month a day belongs to, what a success rate is when nothing
# reached a terminal state, what a freshness attainment percentage divides BY,
# that a count of tables is not a count of rows, and that the domain rows in
# the file equal the sum of the entity rows beneath them. The Glue job around
# it is pinned on source (it runs only under Spark) and the Terraform that
# schedules it is pinned too - a monthly job nobody triggers reports nothing.

import re
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import gold_report as gr
import gold_stats as gs

STACK = Path(__file__).resolve().parents[2]
REPO = STACK.parents[1]
JOB = (STACK / "src/apps/pipeline/gold_ops_export.py").read_text(encoding="utf-8")
TF = (STACK / "infrastructure/terraform-gold/ops_report.tf").read_text(encoding="utf-8")
WORKFLOW = (REPO / ".github/workflows/deploy-echo-gold-dev.yml").read_text(encoding="utf-8")

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
MONTH = "2026-09"


def day_row(day="2026-09-01", gold_id="product", domain="product", runs=1,
            succeeded=1, failed=0, skipped=0, rows_read=0, rows_written=100,
            changes=0, reconcile=0, deleted=0, duration=120, fresh=True):
    return {"day": day, "gold_id": gold_id, "domain": domain, "runs": runs,
            "succeeded": succeeded, "failed": failed, "skipped": skipped,
            "rows_read": rows_read, "rows_written": rows_written,
            "changes_total": changes, "reconcile_runs": reconcile,
            "rows_deleted": deleted, "total_duration_sec": duration,
            "fresh_at_day_end": fresh}


def only(pack, grain, name):
    key = "gold_id" if grain == gr.ENTITY_GRAIN else "domain"
    found = [r for r in pack if r["grain"] == grain and r[key] == name]
    assert len(found) == 1, f"expected one {grain} row for {name}, got {len(found)}"
    return found[0]


class MonthArithmeticTest(unittest.TestCase):

    def test_previous_month_is_the_one_that_just_ended(self):
        self.assertEqual(gr.previous_month(NOW), "2026-09")

    def test_it_crosses_the_year_without_month_arithmetic(self):
        self.assertEqual(
            gr.previous_month(datetime(2027, 1, 1, 6, tzinfo=timezone.utc)), "2026-12")

    def test_a_run_late_in_a_month_still_reports_the_previous_one(self):
        """A re-run on the 20th must not silently report a part-month."""
        self.assertEqual(
            gr.previous_month(datetime(2026, 10, 20, 6, tzinfo=timezone.utc)), "2026-09")

    def test_month_key_reads_a_date_a_datetime_and_a_string(self):
        self.assertEqual(gr.month_key(date(2026, 9, 3)), "2026-09")
        self.assertEqual(gr.month_key(datetime(2026, 9, 3, 12, tzinfo=timezone.utc)), "2026-09")
        self.assertEqual(gr.month_key("2026-09-03"), "2026-09")
        for junk in (None, "", "x", 7):
            with self.subTest(junk=junk):
                self.assertEqual(gr.month_key(junk), "")

    def test_parse_month(self):
        self.assertEqual(gr.parse_month(" 2026-09 "), "2026-09")
        for bad in ("2026-9", "2026-13", "2026-00", "1999-01", "2026-09-01",
                    "", None, "abcd-ef"):
            with self.subTest(bad=bad):
                self.assertIsNone(gr.parse_month(bad))

    def test_month_bounds_covers_the_whole_month(self):
        for month, last in [("2026-01", 31), ("2026-04", 30), ("2026-02", 28),
                            ("2028-02", 29), ("2026-12", 31)]:
            with self.subTest(month=month):
                first, end = gr.month_bounds(month)
                self.assertEqual(first.day, 1)
                self.assertEqual(end.day, last)
                self.assertEqual((first.year, first.month), (end.year, end.month))

    def test_the_query_is_bounded_at_both_ends(self):
        """Unbounded, a table holding years would be read whole every month."""
        sql = gr.month_rows_sql("db.daily_stats", "2026-09")
        self.assertIn("BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'", sql)


class RollUpTest(unittest.TestCase):

    def test_only_the_reported_month_counts(self):
        pack = gr.roll_up([day_row(day="2026-09-01"),
                           day_row(day="2026-08-31", rows_written=999999),
                           day_row(day="2026-10-01", rows_written=999999)],
                          MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, "product")["rows_written"], 100)

    def test_a_row_without_an_entity_is_dropped(self):
        pack = gr.roll_up([day_row(), {"day": "2026-09-02", "runs": 9}], MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, "product")["runs"], 1)

    def test_both_grains_are_present_domain_first(self):
        pack = gr.roll_up([day_row(gold_id="product"),
                           day_row(gold_id="marketing_copy")], MONTH, NOW)
        grains = [r["grain"] for r in pack]
        self.assertEqual(grains, [gr.DOMAIN_GRAIN, gr.ENTITY_GRAIN, gr.ENTITY_GRAIN])

    def test_rows_are_ordered_so_two_months_diff_cleanly(self):
        """Fed in an order whose REVERSAL is not also sorted, and asserted
        against the expected lists rather than against their own sorted copy.
        The first version of this test used two domains fed in an order where
        reversing them happened to sort them, so it passed while the ordering
        was broken - a control caught that, not a reviewer."""
        pack = gr.roll_up([day_row(gold_id="price", domain="price"),
                           day_row(gold_id="stock", domain="stock"),
                           day_row(gold_id="product", domain="product"),
                           day_row(gold_id="marketing_copy", domain="product")],
                          MONTH, NOW)
        self.assertEqual([r["domain"] for r in pack if r["grain"] == gr.DOMAIN_GRAIN],
                         ["price", "product", "stock"])
        self.assertEqual([r["gold_id"] for r in pack if r["grain"] == gr.ENTITY_GRAIN],
                         ["marketing_copy", "price", "product", "stock"])

    def test_a_domain_row_equals_the_sum_of_its_entity_rows(self):
        """The whole point of one file with two grains: they cannot disagree.

        Driven off ADDITIVE_COLUMNS rather than a hand-listed tuple, so a new
        additive column is covered the day it is added - the hand-listed
        version is how `compute_minutes` went unchecked."""
        rows = [day_row(gold_id="product", runs=3, succeeded=2, failed=1,
                        rows_written=1000, duration=300),
                day_row(gold_id="marketing_copy", rows_written=10, duration=30),
                day_row(day="2026-09-02", gold_id="product", rows_written=5)]
        pack = gr.roll_up(rows, MONTH, NOW)
        domain = only(pack, gr.DOMAIN_GRAIN, "product")
        entities = [r for r in pack if r["grain"] == gr.ENTITY_GRAIN]
        self.assertGreaterEqual(len(gr.ADDITIVE_COLUMNS), 15)
        for field in gr.ADDITIVE_COLUMNS:
            with self.subTest(field=field):
                self.assertEqual(domain[field], sum(e[field] for e in entities))

    def test_an_entity_with_no_domain_is_grouped_not_dropped(self):
        pack = gr.roll_up([day_row(gold_id="stock", domain=None)], MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, gr.UNASSIGNED)["entities"], 1)
        self.assertEqual(only(pack, gr.ENTITY_GRAIN, "stock")["domain"], gr.UNASSIGNED)

    def test_counters_survive_dynamodb_decimals(self):
        pack = gr.roll_up([day_row(runs=Decimal("2"), rows_written=Decimal("50"),
                                   duration=Decimal("600"))], MONTH, NOW)
        row = only(pack, gr.DOMAIN_GRAIN, "product")
        self.assertEqual((row["runs"], row["rows_written"]), (2, 50))
        self.assertEqual(row["compute_minutes"], 10.0)

    def test_nothing_to_report_is_an_empty_pack_not_an_error(self):
        self.assertEqual(gr.roll_up([], MONTH, NOW), [])
        self.assertEqual(gr.roll_up(None, MONTH, NOW), [])

    def test_days_covered_counts_distinct_days(self):
        pack = gr.roll_up([day_row(day="2026-09-01"), day_row(day="2026-09-01"),
                           day_row(day="2026-09-05")], MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, "product")["days_covered"], 2)

    def test_it_is_stamped_and_carries_both_schema_versions(self):
        row = gr.roll_up([day_row()], MONTH, NOW)[0]
        self.assertTrue(row["generated_at"].endswith("+00:00"))
        self.assertEqual(row["schema_version"], gr.REPORT_SCHEMA_VERSION)
        self.assertEqual(row["daily_schema_version"], gs.SCHEMA_VERSION,
                         "the pack must name the daily schema it was built from")


class TablesAreNotRowsTest(unittest.TestCase):
    """Owner decision 2026-09-24: two figures, never their product."""

    def test_entities_and_rows_are_separate_columns(self):
        pack = gr.roll_up([day_row(gold_id="product", rows_written=1000),
                           day_row(gold_id="marketing_copy", rows_written=10)],
                          MONTH, NOW)
        domain = only(pack, gr.DOMAIN_GRAIN, "product")
        self.assertEqual(domain["entities"], 2)
        self.assertEqual(domain["rows_written"], 1010)
        self.assertNotIn("rows_served", domain)

    def test_a_table_that_never_ran_is_counted_but_not_as_active(self):
        """A paused table must not read like a busy one."""
        pack = gr.roll_up([day_row(gold_id="product", runs=1),
                           day_row(gold_id="stock", domain="product", runs=0,
                                   succeeded=0, rows_written=0, duration=0)],
                          MONTH, NOW)
        domain = only(pack, gr.DOMAIN_GRAIN, "product")
        self.assertEqual(domain["entities"], 2)
        self.assertEqual(domain["entities_with_runs"], 1)


class PercentagesTest(unittest.TestCase):

    def test_run_success_is_over_terminal_runs_only(self):
        """A gate-skip is not a failure, so it must not sit in the denominator."""
        pack = gr.roll_up([day_row(runs=4, succeeded=2, failed=1, skipped=1)],
                          MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, "product")["run_success_pct"],
                         round(100 * 2 / 3, 1))

    def test_a_month_with_nothing_terminal_has_no_success_rate(self):
        """None, never 0%: 0% reads as an outage, and a skip-only month is not."""
        pack = gr.roll_up([day_row(runs=2, succeeded=0, failed=0, skipped=2)],
                          MONTH, NOW)
        self.assertIsNone(only(pack, gr.DOMAIN_GRAIN, "product")["run_success_pct"])

    def test_freshness_attainment_divides_by_the_JUDGED_days(self):
        """The metrics rows expire after 90 days, so deep history has no
        evidence either way. Counting an unknown day as a miss would invent an
        outage; counting it as a pass would hide one."""
        pack = gr.roll_up([day_row(day="2026-09-01", fresh=True),
                           day_row(day="2026-09-02", fresh=False),
                           day_row(day="2026-09-03", fresh=None),
                           day_row(day="2026-09-04", fresh=None)], MONTH, NOW)
        row = only(pack, gr.DOMAIN_GRAIN, "product")
        self.assertEqual(row["entity_days"], 4)
        self.assertEqual(row["freshness_met"], 1)
        self.assertEqual(row["freshness_unknown"], 2)
        self.assertEqual(row["freshness_attainment_pct"], 50.0)

    def test_the_unknown_count_is_published_beside_the_percentage(self):
        """100% over 2 judged days of 30 is not a good month, and a reader can
        only see that if the coverage is in the file."""
        pack = gr.roll_up([day_row(day=f"2026-09-{n:02d}", fresh=None)
                           for n in range(1, 29)]
                          + [day_row(day="2026-09-29", fresh=True),
                             day_row(day="2026-09-30", fresh=True)], MONTH, NOW)
        row = only(pack, gr.DOMAIN_GRAIN, "product")
        self.assertEqual(row["freshness_attainment_pct"], 100.0)
        self.assertEqual(row["freshness_unknown"], 28)

    def test_a_month_with_no_judged_day_has_no_attainment(self):
        pack = gr.roll_up([day_row(fresh=None)], MONTH, NOW)
        self.assertIsNone(
            only(pack, gr.DOMAIN_GRAIN, "product")["freshness_attainment_pct"])

    def test_compute_is_reported_in_minutes(self):
        pack = gr.roll_up([day_row(duration=90), day_row(day="2026-09-02", duration=30)],
                          MONTH, NOW)
        self.assertEqual(only(pack, gr.DOMAIN_GRAIN, "product")["compute_minutes"], 2.0)

    def test_the_pack_reports_no_cost(self):
        """Owner decision 2026-09-24: capacity varies per run and lives only in
        Glue's history, and a price in code goes stale. A follow-up covers it."""
        row = gr.roll_up([day_row()], MONTH, NOW)[0]
        for absent in ("dpu_hours", "cost", "estimated_cost", "cost_usd"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, row)


class CsvTest(unittest.TestCase):

    def test_the_header_is_always_written(self):
        """An empty file with a header says the export ran and found nothing.
        An empty file says nothing, and somebody has to go and find out."""
        self.assertEqual(gr.render_csv([]).strip(), ",".join(gr.COLUMNS))
        self.assertEqual(gr.render_csv(None).strip(), ",".join(gr.COLUMNS))

    def test_the_columns_are_the_header_in_order(self):
        text = gr.render_csv(gr.roll_up([day_row()], MONTH, NOW))
        lines = text.splitlines()
        self.assertEqual(lines[0].split(","), list(gr.COLUMNS))
        self.assertEqual(len(lines[1].split(",")), len(gr.COLUMNS))

    def test_a_null_percentage_renders_empty_not_zero(self):
        """A spreadsheet averages an empty cell out; a 0 drags the average."""
        pack = gr.roll_up([day_row(runs=1, succeeded=0, failed=0, skipped=1,
                                   fresh=None)], MONTH, NOW)
        row = gr.render_csv(pack).splitlines()[1].split(",")
        columns = list(gr.COLUMNS)
        self.assertEqual(row[columns.index("run_success_pct")], "")
        self.assertEqual(row[columns.index("freshness_attainment_pct")], "")

    def test_a_row_missing_a_column_is_refused(self):
        with self.assertRaises(KeyError):
            gr.render_csv([{"month": MONTH}])

    def test_every_report_row_fills_exactly_the_columns(self):
        for row in gr.roll_up([day_row(), day_row(gold_id="price", domain="price")],
                              MONTH, NOW):
            with self.subTest(grain=row["grain"], id=row["gold_id"]):
                self.assertEqual(set(row), set(gr.COLUMNS))

    def test_one_object_per_month_plus_a_stable_latest(self):
        self.assertEqual(gr.report_key("2026-09"), "reports/monthly/2026-09.csv")
        self.assertEqual(gr.latest_key(), "reports/monthly/latest.csv")
        for key in (gr.report_key("2026-09"), gr.latest_key()):
            with self.subTest(key=key):
                self.assertFalse(key.startswith("/"))
                self.assertTrue(key.endswith(".csv"))


class JobSourceTest(unittest.TestCase):
    """The export runs under Spark, so it is pinned on source."""

    def test_env_is_required_with_no_default(self):
        self.assertIn('_p.add_argument("--env",        default=None)', JOB)
        self.assertIn("--env is required and was not supplied", JOB)

    def test_it_reports_the_month_that_just_ended_by_default(self):
        self.assertIn("gold_report.previous_month(started_at)", JOB)
        self.assertIn("gold_report.parse_month", JOB)

    def test_it_reads_only_the_month_it_reports(self):
        self.assertIn("gold_report.month_rows_sql(STATS_TABLE, MONTH)", JOB)

    def test_it_writes_the_month_key_and_the_latest_key(self):
        self.assertIn("gold_report.report_key(MONTH)", JOB)
        self.assertIn("gold_report.latest_key()", JOB)
        self.assertIn('ContentType="text/csv"', JOB)

    def test_it_reconciles_the_two_grains_before_it_is_believed(self):
        """Nobody re-derives this file by hand, so nobody would notice the two
        grains disagreeing unless the job says so. The comparison itself now
        lives in the library, where it is unit-tested; the job's part is to
        call it and refuse to report success when it finds anything."""
        self.assertIn("RECONCILED", JOB)
        self.assertIn("problems = gold_report.reconcile(pack)", JOB)
        self.assertIn("raise ValueError", JOB)
        self.assertLess(JOB.index("gold_report.reconcile(pack)"),
                        JOB.index('status = "SUCCEEDED"'))
        # The readable message is the library's now, not the job's.
        source = (STACK / "src/libs/gold_report.py").read_text(encoding="utf-8")
        self.assertIn("row(s) sum to", source)

    def test_an_empty_month_still_writes_a_header(self):
        self.assertIn("EMPTY MONTH", JOB)
        self.assertNotIn("if not pack:\n        return", JOB)

    def test_a_failure_raises_rather_than_exiting_zero(self):
        self.assertIn('if status != "SUCCEEDED":', JOB)
        self.assertIn("raise RuntimeError", JOB)

    def test_it_emits_the_same_run_document_as_every_other_gold_job(self):
        self.assertIn("gold_telemetry.emit(gold_telemetry.run_document(", JOB)
        self.assertIn('"_ops_report"', JOB)


class TerraformIsPinnedTest(unittest.TestCase):

    def test_the_job_name_is_a_verb_on_the_standard(self):
        self.assertIn('name         = "${local.name_prefix}-gold-export"', TF)

    def test_a_scheduled_trigger_runs_it_after_the_nightly(self):
        block = re.search(r'resource "aws_glue_trigger" "gold_ops_export_monthly" \{.*?\n\}',
                          TF, re.S)
        self.assertIsNotNone(block, "no trigger: the pack would never be written")
        self.assertIn('type        = "SCHEDULED"', block.group(0))
        # 06:00 on the 1st, after the 04:30 nightly run has written the month's
        # last day.
        self.assertIn('schedule    = "cron(0 6 1 * ? *)"', block.group(0))
        self.assertIn("enabled     = true", block.group(0))
        self.assertIn("job_name = aws_glue_job.gold_ops_export.name", block.group(0))

    def test_no_glue_argument_carries_an_empty_default(self):
        """Glue drops an empty value rather than passing it as an argv token,
        which cost the nightly job every one of its runs. Comment lines are
        stripped because the comment explaining the rule names the flag."""
        block = re.search(r"default_arguments = \{(.*?)\n  \}", TF, re.S).group(1)
        live = "\n".join(line for line in block.split("\n")
                         if not line.lstrip().startswith("#"))
        empty = re.findall(r'"(--[\w-]+)"\s*=\s*""', live)
        self.assertEqual(empty, [], f"empty Glue defaults are dropped: {empty}")

    def test_the_job_is_told_every_name_it_reads(self):
        block = re.search(r"default_arguments = \{(.*?)\n  \}", TF, re.S).group(1)
        live = "\n".join(line for line in block.split("\n")
                         if not line.lstrip().startswith("#"))
        for arg in ("--GOLD_WAREHOUSE", "--STATS_DATABASE", "--STATS_TABLE",
                    "--REPORT_BUCKET", "--env"):
            with self.subTest(arg=arg):
                self.assertIn(f'"{arg}"', live)

    def test_the_pack_goes_to_the_artifacts_bucket(self):
        live = "\n".join(line for line in TF.split("\n")
                         if not line.lstrip().startswith("#"))
        self.assertIn('"--REPORT_BUCKET" = var.gold_artifacts_bucket', live)

    def test_the_job_gets_the_libraries_it_imports(self):
        """Pinned against the extra-py-files LIST: gold_report imports
        gold_stats, which imports gold_telemetry, so all three must ship even
        though this file only uploads one of them."""
        py_files = re.search(r"ops_report_py_files = join\(\",\", \[(.*?)\]\)",
                             TF, re.S).group(1)
        imported = set(re.findall(r"^\s*import (\w+)\s*(?:#.*)?$", JOB, re.M)) | \
            set(re.findall(r"^\s*from (\w+)[\w.]* import", JOB, re.M))
        stdlib_or_glue = {"json", "sys", "traceback", "boto3", "argparse",
                          "datetime", "awsglue", "pyspark"}
        checked = 0
        for module in sorted(imported - stdlib_or_glue):
            with self.subTest(module=module):
                # The list carries a gold_glue_uri lookup rather than a
                # literal path now (UIFPE-576 1b). The artifact name is the
                # module name for every library, and
                # qe/pipeline/test_glue_artifact_sets.py proves the map
                # resolves that key to that file.
                self.assertIn(f'local.gold_glue_uri["{module}"]', py_files,
                              f"{module} is imported but not in --extra-py-files")
                checked += 1
        self.assertGreaterEqual(checked, 2)      # gold_report, gold_telemetry
        # gold_stats is imported by the LIBRARY, not the job, so it is not in
        # the loop above - and it is exactly the one a reader would forget.
        self.assertIn('local.gold_glue_uri["gold_stats"]', py_files)
        self.assertIn("from gold_stats import", (STACK / "src/libs/gold_report.py")
                      .read_text(encoding="utf-8"))

    def test_the_new_script_and_library_are_uploaded(self):
        for resource in ("gold_ops_export_script", "gold_report_lib"):
            with self.subTest(resource=resource):
                self.assertIn(f'resource "aws_s3_object" "{resource}"', TF)

    def test_one_run_at_a_time(self):
        self.assertIn("max_concurrent_runs = 1", TF)

    def test_an_edit_to_the_job_or_its_library_redeploys(self):
        for path in ("stacks/echo/src/apps/pipeline/gold_ops_export.py",
                     "stacks/echo/src/libs/gold_report.py"):
            with self.subTest(path=path):
                self.assertIn(f'- "{path}"', WORKFLOW)


class AdditiveTest(unittest.TestCase):
    """A rounded per-row float cannot be summed, so it is not the figure the
    file reconciles on. Found on the first real pack: a reader added the
    compute column up and got a different total from the domain row."""

    # The exact seconds from that pack, which is why these numbers and not
    # tidier ones: 15,236 + 1,118 = 16,354 adds, and 253.9 + 18.6 does not.
    LIVE = (15236, 1118)

    def pack(self):
        return gr.roll_up([day_row(gold_id="product", duration=self.LIVE[0]),
                           day_row(gold_id="marketing_copy", duration=self.LIVE[1])],
                          MONTH, NOW)

    def test_seconds_are_the_additive_figure(self):
        pack = self.pack()
        domain = only(pack, gr.DOMAIN_GRAIN, "product")
        entities = [r for r in pack if r["grain"] == gr.ENTITY_GRAIN]
        self.assertEqual(domain["compute_seconds"], sum(self.LIVE))
        self.assertEqual(domain["compute_seconds"],
                         sum(e["compute_seconds"] for e in entities))

    def test_minutes_are_for_reading_and_do_not_add(self):
        """Documented, not fixed: rounding to a tenth cannot be additive. The
        test exists so nobody 'corrects' the reconciliation by adding minutes
        to it and then has to explain a failure."""
        pack = self.pack()
        domain = only(pack, gr.DOMAIN_GRAIN, "product")
        entities = [r for r in pack if r["grain"] == gr.ENTITY_GRAIN]
        self.assertEqual(domain["compute_minutes"], 272.6)
        self.assertEqual(round(sum(e["compute_minutes"] for e in entities), 1), 272.5)
        self.assertNotIn("compute_minutes", gr.ADDITIVE_COLUMNS)

    def test_minutes_still_agree_with_the_seconds_on_the_same_row(self):
        for row in self.pack():
            with self.subTest(grain=row["grain"], id=row["gold_id"]):
                self.assertEqual(row["compute_minutes"],
                                 round(row["compute_seconds"] / 60.0, 1))

    def test_only_real_sums_are_declared_additive(self):
        for column in gr.ADDITIVE_COLUMNS:
            with self.subTest(column=column):
                self.assertIn(column, gr.COLUMNS)
        for absent, why in (("days_covered", "distinct days overlap"),
                            ("run_success_pct", "a ratio is not a sum"),
                            ("freshness_attainment_pct", "a ratio is not a sum"),
                            ("compute_minutes", "rounded per row"),
                            ("month", "not a quantity"),
                            ("grain", "not a quantity"),
                            ("schema_version", "not a quantity")):
            with self.subTest(absent=absent, why=why):
                self.assertNotIn(absent, gr.ADDITIVE_COLUMNS)

    def test_the_column_change_moved_the_pack_version(self):
        self.assertGreaterEqual(gr.REPORT_SCHEMA_VERSION, 2)


class ReconcileTest(unittest.TestCase):

    def good(self):
        return gr.roll_up([day_row(gold_id="product", runs=3, succeeded=2,
                                   failed=1, rows_written=1000, duration=300),
                           day_row(gold_id="marketing_copy", rows_written=10,
                                   duration=30),
                           day_row(gold_id="price", domain="price")], MONTH, NOW)

    def test_a_pack_that_holds_together_reports_nothing(self):
        self.assertEqual(gr.reconcile(self.good()), [])
        self.assertEqual(gr.reconcile([]), [])
        self.assertEqual(gr.reconcile(None), [])

    def test_it_covers_every_declared_additive_column(self):
        """One mutation per column, so a column silently dropped from the
        check is a failing test rather than a quiet gap."""
        for column in gr.ADDITIVE_COLUMNS:
            with self.subTest(column=column):
                pack = [dict(r) for r in self.good()]
                target = next(r for r in pack if r["grain"] == gr.DOMAIN_GRAIN
                              and r["domain"] == "product")
                target[column] = as_int_or(target[column]) + 7
                problems = gr.reconcile(pack)
                self.assertTrue(any(column in p for p in problems),
                                f"{column} is declared additive but not checked")

    def test_the_message_names_the_domain_the_column_and_both_totals(self):
        pack = [dict(r) for r in self.good()]
        target = next(r for r in pack if r["grain"] == gr.DOMAIN_GRAIN
                      and r["domain"] == "product")
        target["rows_written"] = 999
        problems = gr.reconcile(pack)
        self.assertEqual(len(problems), 1)
        for needle in ("product", "rows_written", "999", "1010", "2 entity"):
            with self.subTest(needle=needle):
                self.assertIn(needle, problems[0])

    def test_a_domain_with_no_entity_rows_is_reported_not_ignored(self):
        """A domain row whose entity rows went missing would otherwise
        reconcile against zero and pass silently."""
        pack = [r for r in self.good()
                if r["grain"] == gr.DOMAIN_GRAIN or r["domain"] != "product"]
        self.assertTrue(gr.reconcile(pack))

    def test_the_job_asserts_it_over_the_declaration_not_its_own_list(self):
        self.assertIn("gold_report.reconcile(pack)", JOB)
        self.assertIn("the pack does not add up", JOB)
        self.assertIn("gold_report.ADDITIVE_COLUMNS", JOB)
        # No hand-listed field tuple left behind in the job.
        self.assertNotIn('for field in ("runs", "failed", "rows_written"', JOB)
        self.assertLess(JOB.index("gold_report.reconcile(pack)"),
                        JOB.index('status = "SUCCEEDED"'))


def as_int_or(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    unittest.main()
