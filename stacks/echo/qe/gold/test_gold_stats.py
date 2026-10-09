# test_gold_stats.py
# UIFPE-544: the arithmetic behind one row of daily gold statistics per entity.
#
# Every number here ends up in a leadership pack, so each definition is pinned:
# which runs count towards a day, how a percentile is taken, what a success rate
# is when nothing terminal ran, when an entity counts as fresh, and that a row
# carries a zero rather than going missing. The Glue job around it is pinned on
# source (it runs only under Spark), and the Terraform that schedules it is
# pinned too - a nightly job nobody triggers is the decoy this Story exists to
# replace.

import re
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import gold_stats as gs

STACK = Path(__file__).resolve().parents[2]
REPO = STACK.parents[1]
JOB = (STACK / "src/apps/pipeline/gold_ops_summarize.py").read_text(encoding="utf-8")
TF = (STACK / "infrastructure/terraform-gold/ops_stats.tf").read_text(encoding="utf-8")
# Every .tf in the gold root. The upload half of test_the_job_gets_the_libraries_it_imports
# used to read ops_stats.tf alone, where it matched the --extra-py-files LITERAL
# rather than an upload block -- so it passed for gold_telemetry, whose upload is
# in glue_jobs.tf. Routing the list through local.gold_glue_uri removed that
# literal and exposed it (UIFPE-576 1b).
GOLD_TF_ALL = chr(10).join(
    p.read_text(encoding="utf-8")
    for p in sorted((STACK / "infrastructure/terraform-gold").glob("*.tf"))
)
ACCESS_TF = (STACK / "infrastructure/terraform-gold/gold_executor_access.tf").read_text(encoding="utf-8")
WORKFLOW = (REPO / ".github/workflows/deploy-echo-gold-dev.yml").read_text(encoding="utf-8")

DAY = date(2026, 9, 23)


def run(gold_id="product", status="SUCCEEDED", at="06:30", duration=120,
        rows_read=0, rows_written=0, affected=0, day="2026-09-23", **extra):
    row = {"gold_id": gold_id, "status": status,
           "started_at": f"{day}T{at}:00+00:00", "duration_sec": duration,
           "rows_read": rows_read, "rows_written": rows_written,
           "affected_keys": affected}
    row.update(extra)
    return row


class DayArithmeticTest(unittest.TestCase):

    def test_previous_day_is_yesterday_utc(self):
        self.assertEqual(gs.previous_day(datetime(2026, 9, 24, 4, 30, tzinfo=timezone.utc)), DAY)
        # 00:30 UTC on the 24th still summarises the 23rd
        self.assertEqual(gs.previous_day(datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)), DAY)

    def test_day_bounds_are_inclusive_start_exclusive_end(self):
        self.assertEqual(gs.day_bounds(DAY),
                         ("2026-09-23T00:00:00+00:00", "2026-09-24T00:00:00+00:00"))

    def test_days_back_is_oldest_first_so_a_partial_backfill_leaves_no_gap(self):
        self.assertEqual([d.isoformat() for d in gs.days_back(DAY, 3)],
                         ["2026-09-21", "2026-09-22", "2026-09-23"])
        self.assertEqual(gs.days_back(DAY, 1), [DAY])
        self.assertEqual(gs.days_back(DAY, 0), [DAY])      # never empty

    def test_parse_day(self):
        self.assertEqual(gs.parse_day("2026-09-23"), DAY)
        self.assertEqual(gs.parse_day(DAY), DAY)
        for bad in ("23-09-2026", "2026-9-3x", "", None, 20260923, "2026-13-01"):
            with self.subTest(bad=bad):
                self.assertIsNone(gs.parse_day(bad))


class PercentileTest(unittest.TestCase):

    def test_nearest_rank_never_invents_a_value(self):
        for values, p50 in [([], 0), ([5], 5), ([30, 120], 30),
                            ([10, 20, 30], 20), (list(range(1, 11)), 5)]:
            with self.subTest(values=values):
                self.assertEqual(gs.percentile(values, 0.5), p50)
                if values:
                    self.assertIn(gs.percentile(values, 0.5), [int(v) for v in values])

    def test_p90_and_max(self):
        values = list(range(1, 11))
        self.assertEqual(gs.percentile(values, 0.9), 9)
        self.assertEqual(gs.percentile(values, 1.0), 10)

    def test_two_runs_do_not_depend_on_parity(self):
        """round() would make this depend on banker's rounding; ceil does not."""
        self.assertEqual(gs.percentile([30, 120], 0.5), 30)
        self.assertEqual(gs.percentile([31, 121], 0.5), 31)

    def test_decimal_and_junk_tolerated(self):
        self.assertEqual(gs.percentile([Decimal("7"), None, "x"], 1.0), 7)


class StatusTest(unittest.TestCase):

    def test_first_token_upper(self):
        self.assertEqual(gs.status_of({"status": "SKIPPED (no orphans)"}), "SKIPPED")
        self.assertEqual(gs.status_of({"status": " succeeded "}), "SUCCEEDED")
        for junk in (None, "", "   ", 7):
            with self.subTest(junk=junk):
                self.assertEqual(gs.status_of({"status": junk}), "UNKNOWN")


class RunsForTest(unittest.TestCase):

    def test_filters_by_entity_and_day_and_sorts_by_start(self):
        rows = [run(at="08:30"), run(at="06:30"),
                run(gold_id="price", at="07:30"),
                run(day="2026-09-22", at="06:30"),
                {"gold_id": "product"}]                       # no started_at
        got = gs.runs_for(rows, "product", DAY)
        self.assertEqual([r["started_at"][11:16] for r in got], ["06:30", "08:30"])

    def test_a_run_is_counted_on_the_day_it_STARTED(self):
        """A run that crosses midnight belongs to the day it began, which is
        also how the Syncs page orders it."""
        crossing = run(at="23:50", duration=1200)
        crossing["completed_at"] = "2026-09-24T00:10:00+00:00"
        self.assertEqual(len(gs.runs_for([crossing], "product", DAY)), 1)
        self.assertEqual(gs.runs_for([crossing], "product", date(2026, 9, 24)), [])


class SummariseRunsTest(unittest.TestCase):

    def test_an_empty_day_is_zeros_not_a_missing_row(self):
        s = gs.summarise_runs([], "product", DAY)
        self.assertEqual(s["runs"], 0)
        for key in ("succeeded", "failed", "skipped", "duration_p50_sec",
                    "duration_p90_sec", "duration_max_sec", "rows_read",
                    "rows_written", "affected_keys"):
            with self.subTest(key=key):
                self.assertEqual(s[key], 0)
        self.assertIsNone(s["last_success_at"])
        self.assertIsNone(gs.success_rate(s))

    def test_counters_durations_and_last_success(self):
        rows = [
            run(at="06:30", duration=120, rows_read=99, rows_written=10, affected=3),
            run(at="07:30", status="FAILED", duration=30),
            run(at="08:30", status="SKIPPED (no silver change)", duration=0),
            run(at="09:30", duration=60, rows_written=5),
            run(gold_id="price", at="06:30", duration=9999),
        ]
        s = gs.summarise_runs(rows, "product", DAY)
        self.assertEqual((s["runs"], s["succeeded"], s["failed"], s["skipped"]), (4, 2, 1, 1))
        # durations come from TERMINAL runs only: the skip's 0 must not drag p50 down
        self.assertEqual(s["duration_p50_sec"], 60)
        self.assertEqual(s["duration_max_sec"], 120)
        self.assertEqual((s["rows_read"], s["rows_written"], s["affected_keys"]), (99, 15, 3))
        self.assertEqual(s["last_success_at"], "2026-09-23T09:30:00+00:00")
        self.assertEqual(gs.success_rate(s), 0.6667)

    def test_decimal_counters_from_dynamodb(self):
        s = gs.summarise_runs([run(rows_written=Decimal("1200"), duration=Decimal("90"))],
                              "product", DAY)
        self.assertEqual(s["rows_written"], 1200)
        self.assertEqual(s["duration_p50_sec"], 90)

    def test_a_day_of_only_skips_has_no_success_rate(self):
        s = gs.summarise_runs([run(status="SKIPPED", at="06:30")], "product", DAY)
        self.assertEqual((s["runs"], s["skipped"]), (1, 1))
        self.assertIsNone(gs.success_rate(s))

    def test_reconcile_summary(self):
        rows = [run(status="SUCCEEDED", rows_deleted=Decimal("4"), orphan_count=Decimal("4")),
                run(status="SKIPPED (no orphans)", at="03:31")]
        r = gs.summarise_reconcile(rows, "product", DAY)
        self.assertEqual(r, {"reconcile_runs": 2, "rows_deleted": 4, "orphans_found": 4})


class FreshnessTest(unittest.TestCase):

    def test_met_and_breached_against_one_and_a_half_cadences(self):
        # daily: limit 2160 minutes at day end
        met = gs.freshness("2026-09-23T06:30:00+00:00", DAY, 1440)
        self.assertEqual(met["freshness_limit_minutes"], 2160)
        self.assertEqual(met["minutes_since_success_at_day_end"], 1050)
        self.assertIs(met["fresh_at_day_end"], True)
        stale = gs.freshness("2026-09-21T06:30:00+00:00", DAY, 1440)
        self.assertIs(stale["fresh_at_day_end"], False)

    def test_never_run_is_unknown_not_a_pass(self):
        never = gs.freshness(None, DAY, 1440)
        self.assertIsNone(never["fresh_at_day_end"])
        self.assertIsNone(never["minutes_since_success_at_day_end"])

    def test_no_cadence_is_unknown_not_a_pass(self):
        self.assertIsNone(gs.freshness("2026-09-23T06:30:00+00:00", DAY, None)["fresh_at_day_end"])

    def test_the_limit_matches_the_alarm_rule(self):
        import gold_telemetry as gt
        for interval in (5, 60, 1440, 7 * 1440):
            with self.subTest(interval=interval):
                self.assertEqual(gs.freshness(None, DAY, interval)["freshness_limit_minutes"],
                                 gt.freshness_limit_minutes(interval))


class ChangesTest(unittest.TestCase):

    def test_by_op_is_not_exploded(self):
        sql = gs.changes_by_op_sql("glue_catalog.`db`.`t_changes`", DAY)
        self.assertIn("GROUP BY op", sql)
        self.assertNotIn("explode", sql)
        self.assertIn("change_date = DATE '2026-09-23'", sql)

    def test_by_channel_explodes_the_array(self):
        sql = gs.changes_by_channel_sql("glue_catalog.`db`.`t_changes`", DAY)
        self.assertIn("explode(changed_channels)", sql)
        self.assertIn("GROUP BY channel", sql)

    def test_counts_from_rows(self):
        self.assertEqual(gs.counts_from_rows(
            [{"op": "UPSERT", "n": Decimal("7")}, {"op": "DELETE", "n": 2},
             {"op": None, "n": 9}, {"op": "REFRESH", "n": -1}], "op"),
            {"DELETE": 2, "REFRESH": 0, "UPSERT": 7})
        self.assertEqual(gs.counts_from_rows(None, "op"), {})


class StatsRowTest(unittest.TestCase):

    def _row(self, **over):
        cfg = {"domain": "product", "enabled": True,
               "schedule": {"frequency": "gold-24h"}, "_interval_minutes": 1440,
               "watermark": {"bookmark": "2026-09-23T06:31:00+00:00"}}
        cfg.update(over.pop("cfg", {}))
        kwargs = dict(
            gold_id="product", day=DAY, cfg=cfg,
            runs=gs.summarise_runs([run(at="06:30", duration=120, rows_written=10)], "product", DAY),
            reconcile=gs.summarise_reconcile([], "product", DAY),
            changes_by_op={"UPSERT": 7}, changes_by_channel={"api": 7, "event": 5},
            computed_at=datetime(2026, 9, 24, 4, 30, tzinfo=timezone.utc),
            source_run_id="jr_1")
        kwargs.update(over)
        return gs.stats_row(**kwargs)

    def test_fills_every_column_and_nothing_else(self):
        row = self._row()
        self.assertEqual(set(row), {name for name, _ in gs.COLUMNS})
        self.assertEqual(len(gs.as_tuple(row)), len(gs.COLUMNS))

    def test_the_day_is_a_date_so_athena_needs_no_cast(self):
        self.assertEqual(self._row()["day"], DAY)
        self.assertNotIsInstance(self._row()["day"], str)

    def test_carries_the_numbers_and_the_provenance(self):
        row = self._row()
        self.assertEqual((row["gold_id"], row["domain"], row["frequency"]),
                         ("product", "product", "gold-24h"))
        self.assertEqual((row["runs"], row["succeeded"], row["rows_written"]), (1, 1, 10))
        self.assertEqual(row["changes_total"], 7)              # ops, not channels
        self.assertEqual(row["changes_by_channel"], {"api": 7, "event": 5})
        self.assertIs(row["fresh_at_day_end"], True)
        self.assertEqual(row["bookmark_at_day_end"], "2026-09-23T06:31:00+00:00")
        self.assertEqual(row["bookmark_age_minutes"], 1049)
        self.assertEqual(row["computed_at"], "2026-09-24T04:30:00+00:00")
        self.assertEqual(row["source_run_id"], "jr_1")
        self.assertEqual(row["schema_version"], gs.SCHEMA_VERSION)

    def test_a_disabled_entity_still_gets_a_row_of_zeros(self):
        """The fleet's denominator must not change when someone pauses a table."""
        row = self._row(cfg={"enabled": False}, runs=gs.summarise_runs([], "x", DAY),
                        changes_by_op={}, changes_by_channel={})
        self.assertIs(row["enabled"], False)
        self.assertEqual((row["runs"], row["changes_total"]), (0, 0))
        self.assertEqual(row["changes_by_op"], {})
        self.assertIsNone(row["success_rate"])

    def test_as_tuple_is_positional_and_refuses_a_wrong_shape(self):
        row = self._row()
        self.assertEqual(gs.as_tuple(row)[:2], (DAY, "product"))
        with self.assertRaises(KeyError):
            gs.as_tuple({k: v for k, v in row.items() if k != "runs"})
        with self.assertRaises(KeyError):
            gs.as_tuple({**row, "surprise": 1})

    def test_as_tuple_follows_COLUMNS_not_the_dict_order(self):
        """The frame is built positionally against spark_schema(), so a row
        whose keys arrive in another order - a future caller, a JSON round trip
        that sorts them - must still land in the right columns. Reading
        row.values() would pass every other test in this file and write the
        numbers into the wrong columns."""
        row = self._row()
        shuffled = {k: row[k] for k in sorted(row)}
        self.assertNotEqual(list(shuffled), [name for name, _ in gs.COLUMNS])
        self.assertEqual(gs.as_tuple(shuffled), gs.as_tuple(row))
        by_name = dict(zip([name for name, _ in gs.COLUMNS], gs.as_tuple(shuffled)))
        self.assertEqual(by_name["gold_id"], "product")
        self.assertEqual(by_name["day"], DAY)
        self.assertEqual(by_name["runs"], row["runs"])

    def test_spark_schema_matches_the_columns(self):
        schema = gs.spark_schema()
        for name, sql_type in gs.COLUMNS:
            with self.subTest(name=name):
                self.assertIn(f"`{name}` {sql_type}", schema)
        self.assertEqual(schema.count(","), len(gs.COLUMNS) - 1 + 2)  # two map<,> commas


class DdlTest(unittest.TestCase):

    def test_iceberg_partitioned_by_month(self):
        ddl = gs.create_table_ddl("glue_catalog.`db`.`daily_stats`", "s3://b/daily_stats/")
        self.assertIn("CREATE TABLE IF NOT EXISTS", ddl)
        self.assertIn("USING iceberg", ddl)
        self.assertIn("PARTITIONED BY (months(day))", ddl)
        self.assertIn("LOCATION 's3://b/daily_stats/'", ddl)
        self.assertIn("'format-version' = '2'", ddl)
        for name, sql_type in gs.COLUMNS:
            with self.subTest(name=name):
                self.assertIn(f"`{name}` {sql_type}", ddl)

    def test_a_rerun_replaces_the_day_rather_than_appending(self):
        self.assertEqual(gs.delete_day_sql("t", DAY), "DELETE FROM t WHERE `day` = DATE '2026-09-23'")


class JobIsPinnedTest(unittest.TestCase):
    """The Glue script runs only under Spark; these pin the parts that decide
    whether the numbers are right."""

    def test_idempotent_by_construction_delete_then_append(self):
        delete = JOB.index("delete_day_sql")
        append = JOB.index("writeTo(STATS_TABLE).append()")
        self.assertLess(delete, append, "the day must be deleted BEFORE the append")
        # both inside the per-day loop, so one bad day cannot corrupt another
        loop = JOB.index("for one_day in DAYS:")
        self.assertLess(loop, delete)

    def test_values_are_written_positionally(self):
        self.assertIn("[gold_stats.as_tuple(r) for r in rows]", JOB)
        self.assertIn("schema=gold_stats.spark_schema()", JOB)

    def test_every_entity_is_summarised_enabled_or_not(self):
        self.assertIn('FilterExpression=Key("version").eq("latest")', JOB)
        self.assertNotIn('Attr("enabled").eq(True)', JOB)

    def test_env_is_required_and_never_guessed(self):
        self.assertIn("--env is required and was not supplied", JOB)
        self.assertIn('_p.add_argument("--env",        default=None)', JOB)

    def test_a_missing_change_log_is_zero_not_a_failure(self):
        """An entity that has never written a change has no `_changes` table,
        and Spark raises AnalysisException for it - not KeyError. Catching
        anything narrower fails the whole night over one young entity."""
        body = re.search(r"def changes_counts\(cfg, one_day\):.*?\n        return \(gold_stats",
                         JOB, re.S).group(0)
        self.assertRegex(body, r"except Exception as \w+:")
        self.assertIn("[CHANGES UNAVAILABLE]", body)
        self.assertIn("return {}, {}", body)

    def test_emits_the_same_emf_run_document_as_every_gold_job(self):
        self.assertIn("gold_telemetry.emit(gold_telemetry.run_document(", JOB)
        self.assertIn("error=error_msg", JOB)

    def test_a_failure_raises_so_the_alarm_sees_it(self):
        self.assertIn('if status != "SUCCEEDED":', JOB)
        self.assertIn("raise RuntimeError", JOB)


class TerraformIsPinnedTest(unittest.TestCase):
    """A nightly job nobody triggers is exactly the decoy this Story replaces."""

    def test_the_database_and_table_follow_the_catalog_rules(self):
        self.assertIn('name        = "${local.gold_db_prefix}_gold_ops"', TF)
        self.assertIn('ops_stats_table    = "daily_stats"', TF)
        # one segment under the warehouse, per storage.md's gold prefix shape
        self.assertIn('ops_stats_location = "s3://${var.gold_bucket}/daily_stats/"', TF)

    def test_the_database_carries_the_gold_layer_tag(self):
        block = re.search(r'resource "aws_lakeformation_resource_lf_tags" "gold_ops_database" \{.*?\n\}',
                          TF, re.S)
        self.assertIsNotNone(block)
        self.assertIn("local.gold_layer_tag_key", block.group(0))
        self.assertIn("value = var.environment", block.group(0))

    def test_a_scheduled_trigger_runs_it_nightly_after_the_reconcile(self):
        block = re.search(r'resource "aws_glue_trigger" "gold_ops_summarize_nightly" \{.*?\n\}', TF, re.S)
        self.assertIsNotNone(block, "no trigger: the job would never run")
        self.assertIn('type        = "SCHEDULED"', block.group(0))
        self.assertIn('schedule    = "cron(30 4 * * ? *)"', block.group(0))
        self.assertIn("enabled     = true", block.group(0))
        self.assertIn("job_name = aws_glue_job.gold_ops_summarize.name", block.group(0))

    def test_the_job_gets_the_libraries_it_imports(self):
        """Pinned against the extra-py-files LIST, not the file: an S3 object
        block mentions the same filename, so a library dropped from the list
        while its upload survives would otherwise look wired and ImportError
        at 04:30."""
        py_files = re.search(r"ops_stats_py_files = join\(\",\", \[(.*?)\]\)", TF, re.S).group(1)
        uploads = GOLD_TF_ALL
        # every module the job imports by bare name must be BOTH uploaded and listed
        # `(?:#.*)?$` matters: `import gold_telemetry  # ...` is how the job
        # explains itself, and a regex anchored without it silently checks one
        # library fewer - which is how this test first passed while broken.
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
                self.assertIn(f"/{module}.py", uploads,
                              f"{module} is listed but never uploaded to S3")
                checked += 1
        self.assertGreaterEqual(checked, 3)   # gold_stats, gold_telemetry, gold_frequencies
        self.assertIn('"--extra-py-files"                   = local.ops_stats_py_files', TF)

    def test_the_job_is_told_every_name_it_reads(self):
        """Read from the LIVE default_arguments, with comment lines stripped.
        Grepping the whole file cannot tell an argument from an argument
        somebody commented out - the same trap the --extra-py-files pin fell
        into on PR 1."""
        block = re.search(r"default_arguments = \{(.*?)\n  \}", TF, re.S).group(1)
        live = "\n".join(line for line in block.split("\n")
                         if not line.lstrip().startswith("#"))
        for arg in ("--CONFIG_TABLE", "--METRICS_TABLE", "--GOLD_WAREHOUSE",
                    "--TRANSFORM_JOB_NAME", "--RECONCILE_JOB_NAME",
                    "--STATS_DATABASE", "--STATS_TABLE", "--STATS_LOCATION",
                    "--SUMMARY_BUCKET", "--env"):
            with self.subTest(arg=arg):
                self.assertIn(f'"{arg}"', live)

    def test_no_glue_argument_carries_an_empty_default(self):
        """Glue DROPS an empty value instead of passing it as an argv token,
        so the flag is left adjacent to the orphaned value of a Glue-consumed
        --enable-* directive and argparse binds that. `"--day" = ""` cost every
        nightly run (jr_203e4d89..., 2026-09-24: ValueError got 'true').

        Written as a rule over ALL arguments, not a pin on --day: the trap is
        the empty string, not the flag that happened to hit it."""
        block = re.search(r"default_arguments = \{(.*?)\n  \}", TF, re.S).group(1)
        # Comment lines stripped: the comment explaining this rule quotes the
        # very string the rule forbids, and a whole-block grep cannot tell the
        # explanation from the defect.
        live = "\n".join(line for line in block.split("\n")
                         if not line.lstrip().startswith("#"))
        empty = re.findall(r'"(--[\w-]+)"\s*=\s*""', live)
        self.assertEqual(empty, [], f"empty Glue defaults are dropped, not passed: {empty}")

    def test_the_runtime_role_can_read_every_config_row(self):
        """The job needs EVERY entity, so it Scans where the other gold jobs
        Query. The shared Glue role held no Scan and the job died at its first
        fetch (jr_747cb8bd..., line 236, 2026-09-24). Simulating the DEPLOY
        role proves Terraform can create things, never that the job can run."""
        block = re.search(
            r'Action\s*=\s*\[([^\]]*dynamodb[^\]]*)\]', ACCESS_TF).group(1)
        self.assertIn("dynamodb:Scan", block)
        self.assertIn("aws_dynamodb_table.gold_pipelines_config.arn", ACCESS_TF)

    def test_the_job_name_is_a_verb_on_the_standard(self):
        self.assertIn('name         = "${local.name_prefix}-gold-summarize"', TF)

    def test_one_run_at_a_time(self):
        self.assertIn("max_concurrent_runs = 1", TF)

    def test_an_edit_to_the_job_or_its_library_redeploys(self):
        for path in ("stacks/echo/src/apps/pipeline/gold_ops_summarize.py",
                     "stacks/echo/src/libs/gold_stats.py"):
            with self.subTest(path=path):
                self.assertIn(f'- "{path}"', WORKFLOW)


# ── The Overview's two halves (PR 2) ────────────────────────────────────────


def cfg(gold_id="product", enabled=True, interval=1440,
        transform_at=None, run_at=None):
    meta = {}
    if transform_at:
        meta["last_transform_at"] = transform_at
    if run_at:
        meta["last_run_at"] = run_at
    return {"gold_id": gold_id, "enabled": enabled,
            "_interval_minutes": interval, "metadata": meta}


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class LiveWindowTest(unittest.TestCase):
    """The "right now" half: what the fleet did in the last 24 hours, and how
    many entities are inside their freshness target as of this instant."""

    def test_only_runs_that_started_inside_the_window_count(self):
        rows = [run(at="11:00", day="2026-09-24"),                 # 1h ago
                run(at="13:00", day="2026-09-23"),                 # 23h ago
                run(at="11:00", day="2026-09-23")]                 # 25h ago - out
        live = gs.live_window(rows, [], NOW)
        self.assertEqual(live["runs"], 2)

    def test_the_window_is_configurable_and_never_zero(self):
        rows = [run(at="11:00", day="2026-09-24")]
        self.assertEqual(gs.live_window(rows, [], NOW, hours=2)["runs"], 1)
        self.assertEqual(gs.live_window(rows, [], NOW, hours=0)["window_hours"], 1)

    def test_outcomes_use_the_same_definitions_as_the_daily_row(self):
        rows = [run(at="09:00", day="2026-09-24", status="SUCCEEDED", rows_written=10),
                run(at="09:30", day="2026-09-24", status="FAILED"),
                run(at="10:00", day="2026-09-24", status="SKIPPED (no changes)"),
                run(at="10:30", day="2026-09-24", status="RUNNING")]
        live = gs.live_window(rows, [], NOW)
        self.assertEqual((live["succeeded"], live["failed"], live["skipped"]), (1, 1, 1))
        self.assertEqual(live["runs"], 4)          # RUNNING is a run, not an outcome
        self.assertEqual(live["rows_written"], 10)

    def test_every_entity_is_counted_and_the_enabled_ones_named_separately(self):
        live = gs.live_window([], [cfg("product"), cfg("price", enabled=False)], NOW)
        self.assertEqual((live["entities"], live["enabled"]), (2, 1))

    def test_freshness_prefers_the_transform_stamp_over_the_run_stamp(self):
        """metadata.last_run_at is ALSO written by the nightly reconcile, so a
        transform that has failed for a day would read as fresh off it."""
        stale_transform = cfg(interval=60,
                              transform_at="2026-09-23T06:00:00+00:00",
                              run_at="2026-09-24T11:55:00+00:00")
        live = gs.live_window([], [stale_transform], NOW)
        self.assertEqual(live["stale"], 1)
        self.assertEqual(live["stale_entities"], ["product"])
        self.assertEqual(gs.last_success_of(stale_transform)[1], "last_transform_at")

    def test_the_run_stamp_is_the_fallback_when_there_is_no_transform_stamp(self):
        row = cfg(interval=60, run_at="2026-09-24T11:55:00+00:00")
        self.assertEqual(gs.last_success_of(row),
                         ("2026-09-24T11:55:00+00:00", "last_run_at"))
        self.assertEqual(gs.live_window([], [row], NOW)["fresh"], 1)

    def test_never_run_or_no_cadence_is_unknown_not_fresh(self):
        live = gs.live_window([], [cfg("product"),                       # never run
                                   cfg("price", interval=None,
                                       transform_at="2026-09-24T11:00:00+00:00")], NOW)
        self.assertEqual((live["fresh"], live["stale"], live["unknown"]), (0, 0, 2))
        self.assertEqual(live["stale_entities"], [])

    def test_the_freshness_limit_is_the_alarms_rule_and_not_a_second_one(self):
        import gold_telemetry
        for interval in (30, 60, 240, 1440):
            with self.subTest(interval=interval):
                limit = gold_telemetry.freshness_limit_minutes(interval)
                inside = NOW - timedelta(minutes=limit)
                outside = NOW - timedelta(minutes=limit + 1)
                self.assertEqual(gs.live_window(
                    [], [cfg(interval=interval,
                             transform_at=inside.isoformat())], NOW)["fresh"], 1)
                self.assertEqual(gs.live_window(
                    [], [cfg(interval=interval,
                             transform_at=outside.isoformat())], NOW)["stale"], 1)

    def test_an_entity_with_no_id_is_not_counted(self):
        live = gs.live_window([], [{"enabled": True}, cfg("product")], NOW)
        self.assertEqual(live["entities"], 1)

    def test_it_reports_as_of_in_utc(self):
        self.assertTrue(gs.live_window([], [], NOW)["as_of"].endswith("+00:00"))

    def test_empty_input_is_zeros_not_an_error(self):
        live = gs.live_window(None, None, NOW)
        self.assertEqual(live["runs"], 0)
        self.assertEqual(live["entities"], 0)


def stat(day="2026-09-23", gold_id="product", runs=1, failed=0,
         rows_written=0, fresh=True):
    return {"day": day, "gold_id": gold_id, "runs": runs, "failed": failed,
            "rows_written": rows_written, "fresh_at_day_end": fresh}


class OverviewHalvesTest(unittest.TestCase):
    """The "this month" half: the file the nightly job writes for the page,
    built from the table's own rows so the two cannot disagree."""

    def test_the_fleet_series_is_one_point_per_day_oldest_first(self):
        s = gs.overview_summary([stat(day="2026-09-24"), stat(day="2026-09-22"),
                                 stat(day="2026-09-23")], 30, NOW)
        self.assertEqual([p["day"] for p in s["fleet"]],
                         ["2026-09-22", "2026-09-23", "2026-09-24"])

    def test_a_day_sums_every_entity_on_it(self):
        s = gs.overview_summary([stat(gold_id="product", runs=2, failed=1, rows_written=5),
                                 stat(gold_id="price", runs=3, rows_written=7)], 30, NOW)
        self.assertEqual(s["fleet"][0], {"day": "2026-09-23", "entities": 2,
                                         "runs": 5, "failed": 1,
                                         "rows_written": 12, "fresh": 2})

    def test_a_date_and_a_string_day_land_on_the_same_point(self):
        """The table hands back a date; a JSON round trip hands back a string,
        and a file that split one day in two would double the fleet count."""
        s = gs.overview_summary([stat(day=date(2026, 9, 23), gold_id="product"),
                                 stat(day="2026-09-23", gold_id="price")], 30, NOW)
        self.assertEqual(len(s["fleet"]), 1)
        self.assertEqual(s["fleet"][0]["entities"], 2)

    def test_a_row_with_no_day_or_no_entity_is_dropped_not_counted(self):
        s = gs.overview_summary([stat(), {"gold_id": "x", "runs": 9},
                                 {"day": "2026-09-23", "runs": 9}], 30, NOW)
        self.assertEqual(s["fleet"][0]["runs"], 1)
        self.assertEqual(list(s["entities"]), ["product"])

    def test_an_unknown_freshness_verdict_is_not_counted_as_met(self):
        s = gs.overview_summary([stat(fresh=None), stat(gold_id="price", fresh=False)],
                                30, NOW)
        self.assertEqual(s["fleet"][0]["fresh"], 0)

    def test_an_unknown_verdict_stays_null_in_the_entity_series(self):
        """Per entity the page must be able to draw "never ran" differently
        from "was stale"; collapsing them to false would lose that."""
        s = gs.overview_summary([stat(fresh=None)], 30, NOW)
        self.assertIsNone(s["entities"]["product"][0]["fresh"])

    def test_each_entity_series_is_sorted_by_day(self):
        s = gs.overview_summary([stat(day="2026-09-24"), stat(day="2026-09-22")], 30, NOW)
        self.assertEqual([p["day"] for p in s["entities"]["product"]],
                         ["2026-09-22", "2026-09-24"])

    def test_the_file_is_stamped_and_versioned(self):
        s = gs.overview_summary([], 30, NOW)
        self.assertEqual(s["schema_version"], gs.SUMMARY_SCHEMA_VERSION)
        self.assertTrue(s["generated_at"].endswith("+00:00"))
        self.assertEqual((s["fleet"], s["entities"]), ([], {}))

    def test_counters_survive_dynamodb_decimals(self):
        s = gs.overview_summary([{"day": "2026-09-23", "gold_id": "product",
                                  "runs": Decimal("2"), "failed": Decimal("1"),
                                  "rows_written": Decimal("9"),
                                  "fresh_at_day_end": True}], 30, NOW)
        self.assertEqual(s["fleet"][0]["runs"], 2)
        self.assertEqual(s["fleet"][0]["rows_written"], 9)


class TrimSummaryTest(unittest.TestCase):

    def setUp(self):
        self.summary = gs.overview_summary(
            [stat(day=f"2026-09-{d:02d}", gold_id=g)
             for d in (20, 21, 22, 23) for g in ("product", "price")], 30, NOW)

    def test_it_keeps_the_newest_days(self):
        trimmed = gs.trim_summary(self.summary, 2)
        self.assertEqual([p["day"] for p in trimmed["fleet"]],
                         ["2026-09-22", "2026-09-23"])
        self.assertEqual(trimmed["days"], 2)

    def test_the_entity_series_are_cut_by_the_same_days(self):
        """A fleet total covering more days than the rows beneath it is a
        mismatch nobody re-reads a page to check."""
        trimmed = gs.trim_summary(self.summary, 2)
        for gold_id, points in trimmed["entities"].items():
            with self.subTest(gold_id=gold_id):
                self.assertEqual([p["day"] for p in points],
                                 ["2026-09-22", "2026-09-23"])

    def test_it_clamps_to_the_file_and_to_at_least_one_day(self):
        self.assertEqual(gs.trim_summary(self.summary, 999)["days"], gs.SUMMARY_DAYS)
        self.assertEqual(len(gs.trim_summary(self.summary, 0)["fleet"]), 1)
        self.assertEqual(len(gs.trim_summary(self.summary, -5)["fleet"]), 1)

    def test_asking_for_more_days_than_the_file_holds_returns_what_it_holds(self):
        trimmed = gs.trim_summary(self.summary, 30)
        self.assertEqual(len(trimmed["fleet"]), 4)

    def test_it_does_not_mutate_the_file_it_was_given(self):
        gs.trim_summary(self.summary, 1)
        self.assertEqual(len(self.summary["fleet"]), 4)

    def test_nothing_to_trim_is_not_an_error(self):
        self.assertEqual(gs.trim_summary({}, 7)["fleet"], [])
        self.assertEqual(gs.trim_summary(None, 7)["entities"], {})


class SummaryPlacementTest(unittest.TestCase):

    def test_the_window_query_is_bounded_at_both_ends(self):
        """Unbounded below, a 90-day back-fill's older rows would inflate the
        file the page draws."""
        sql = gs.recent_days_sql("db.daily_stats", DAY, 3)
        self.assertIn("BETWEEN DATE '2026-09-21' AND DATE '2026-09-23'", sql)
        self.assertIn("ORDER BY `day`, gold_id", sql)

    def test_the_window_is_never_empty(self):
        self.assertIn("BETWEEN DATE '2026-09-23' AND DATE '2026-09-23'",
                      gs.recent_days_sql("t", DAY, 0))

    def test_the_key_is_one_prefix_and_a_file_with_no_leading_slash(self):
        self.assertFalse(gs.SUMMARY_KEY.startswith("/"))
        self.assertEqual(len(gs.SUMMARY_KEY.split("/")), 2)
        self.assertTrue(gs.SUMMARY_KEY.endswith(".json"))

    def test_the_uri_is_only_ever_for_reading_out_loud(self):
        self.assertEqual(gs.summary_s3_uri("uif-echo-dev-gold-artifacts"),
                         f"s3://uif-echo-dev-gold-artifacts/{gs.SUMMARY_KEY}")
        self.assertEqual(gs.summary_s3_uri("b/"), f"s3://b/{gs.SUMMARY_KEY}")


class SummaryJobSourceTest(unittest.TestCase):
    """The summary write runs under Spark, so it is pinned on source."""

    def test_it_reads_the_window_back_from_the_table_it_just_wrote(self):
        """Pinned on the CALLS, not the file: gold_stats.SUMMARY_DAYS appears
        on the next line too, so a whole-file grep passes while the query
        itself carries a hard-coded 30 - the same trap the --extra-py-files
        pin fell into on PR 1."""
        query = re.search(r"gold_stats\.recent_days_sql\((.*?)\)\)", JOB, re.S).group(1)
        for needed in ("STATS_TABLE", "newest", "gold_stats.SUMMARY_DAYS"):
            with self.subTest(needed=needed):
                self.assertIn(needed, query)
        built = re.search(r"gold_stats\.overview_summary\((.*?)\)", JOB, re.S).group(1)
        self.assertIn("gold_stats.SUMMARY_DAYS", built,
                      "the file would be labelled with a window it does not hold")

    def test_it_summarises_the_newest_day_not_the_oldest(self):
        self.assertIn("newest = DAYS[-1]", JOB)

    def test_it_writes_one_object_at_the_library_key(self):
        self.assertIn("Key=gold_stats.SUMMARY_KEY", JOB)
        self.assertIn("Bucket=SUMMARY_BUCKET", JOB)
        self.assertIn('ContentType="application/json"', JOB)

    def test_it_writes_after_every_day_has_landed(self):
        self.assertLess(JOB.index("[DAY WRITTEN]"), JOB.index("summary_rows = "))

    def test_it_writes_before_the_job_reports_success(self):
        self.assertLess(JOB.index("summary_rows = "), JOB.index('status = "SUCCEEDED"'))

    def test_a_failed_summary_fails_the_job(self):
        """Deliberately NOT swallowed: an Overview rendering last week's
        numbers as this morning's is the decoy failure UIFPE-543 found on the
        Alerts page, and the table write is idempotent so a re-run is cheap."""
        self.assertIn("\n    summary_rows = ", JOB)      # 4 spaces: the main try body
        self.assertNotIn("\n        summary_rows = ", JOB)

    def test_the_bucket_is_the_artifacts_bucket_the_controller_can_read(self):
        """The artifacts bucket, not the data bucket: the controller already
        holds that name and GetObject on it, so the Overview route needs no
        change to the latched echo root."""
        live = "\n".join(line for line in TF.split("\n")
                         if not line.lstrip().startswith("#"))
        self.assertIn('"--SUMMARY_BUCKET" = var.gold_artifacts_bucket', live)


class TotalDurationTest(unittest.TestCase):
    """UIFPE-544 PR 3: percentiles cannot be summed, so a monthly pack has no
    other way to say how much compute a domain used."""

    def test_it_totals_the_same_runs_the_percentiles_use(self):
        rows = [run(at="06:00", duration=120),
                run(at="07:00", status="FAILED", duration=30),
                run(at="08:00", status="SKIPPED (no change)", duration=0)]
        s = gs.summarise_runs(rows, "product", DAY)
        self.assertEqual(s["total_duration_sec"], 150)

    def test_a_gate_skip_contributes_nothing(self):
        """Consistent with the percentiles: a skip takes 0 seconds of compute,
        and counting a non-terminal run's elapsed time would overstate the bill."""
        rows = [run(at="06:00", status="SKIPPED", duration=900),
                run(at="07:00", duration=60)]
        self.assertEqual(gs.summarise_runs(rows, "product", DAY)["total_duration_sec"], 60)

    def test_a_day_with_no_runs_totals_zero(self):
        self.assertEqual(gs.summarise_runs([], "product", DAY)["total_duration_sec"], 0)

    def test_the_mean_it_implies_agrees_with_p50_on_one_run(self):
        s = gs.summarise_runs([run(duration=90)], "product", DAY)
        terminal = s["succeeded"] + s["failed"]
        self.assertEqual(s["total_duration_sec"] / terminal, s["duration_p50_sec"])

    def test_it_survives_dynamodb_decimals(self):
        rows = [run(at="06:00", duration=Decimal("90")),
                run(at="07:00", duration=Decimal("30"))]
        self.assertEqual(gs.summarise_runs(rows, "product", DAY)["total_duration_sec"], 120)

    def test_it_is_a_column_and_the_schema_version_moved(self):
        self.assertIn(("total_duration_sec", "bigint"), gs.COLUMNS)
        self.assertGreaterEqual(gs.SCHEMA_VERSION, 2,
                                "a new column must bump the schema version")


class MigrateTableTest(unittest.TestCase):
    """CREATE TABLE IF NOT EXISTS no-ops on a live table, so a column added to
    COLUMNS afterwards would only ever appear on a table built from scratch -
    and the first write would fail on a schema mismatch."""

    def test_a_missing_column_is_added(self):
        existing = [name for name, _ in gs.COLUMNS if name != "total_duration_sec"]
        self.assertEqual(gs.migrate_table_sql("t", existing),
                         ["ALTER TABLE t ADD COLUMN `total_duration_sec` bigint"])

    def test_an_up_to_date_table_needs_nothing(self):
        self.assertEqual(gs.migrate_table_sql("t", [n for n, _ in gs.COLUMNS]), [])

    def test_a_table_that_does_not_exist_yet_is_left_to_CREATE(self):
        """An empty column list means no table; emitting ALTER would fail."""
        self.assertEqual(gs.migrate_table_sql("t", []), [])
        self.assertEqual(gs.migrate_table_sql("t", None), [])

    def test_the_comparison_ignores_case(self):
        """Catalogs lower-case column names; a case-sensitive check would try
        to add every column again."""
        self.assertEqual(
            gs.migrate_table_sql("t", [n.upper() for n, _ in gs.COLUMNS]), [])

    def test_it_is_driven_off_COLUMNS_so_it_handles_the_NEXT_column_too(self):
        """Not a hand-kept list of migrations: whatever COLUMNS gains, this
        emits. Proved by asking for a table missing two of them."""
        missing = {"total_duration_sec", "changes_by_channel"}
        existing = [n for n, _ in gs.COLUMNS if n not in missing]
        statements = gs.migrate_table_sql("t", existing)
        self.assertEqual(len(statements), 2)
        for name in missing:
            with self.subTest(name=name):
                self.assertTrue(any(f"`{name}`" in s for s in statements))

    def test_the_types_match_the_table_definition(self):
        existing = [name for name, _ in gs.COLUMNS][:1]
        emitted = "\n".join(gs.migrate_table_sql("t", existing))
        for name, sql_type in gs.COLUMNS[1:]:
            with self.subTest(name=name):
                self.assertIn(f"ADD COLUMN `{name}` {sql_type}", emitted)

    def test_the_job_applies_it_from_the_catalog_and_not_a_guess(self):
        self.assertIn("gold_stats.migrate_table_sql(STATS_TABLE, existing)", JOB)
        self.assertIn("spark.table(STATS_TABLE).schema.fields", JOB)
        self.assertLess(JOB.index("create_table_ddl"), JOB.index("migrate_table_sql"))


if __name__ == "__main__":
    unittest.main()
