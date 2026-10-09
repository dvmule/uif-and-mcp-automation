# test_gold_dispatcher.py
# Unit tests for src/apps/lambda_dispatcher/gold_dispatcher.py — no AWS needed
# (boto3 mocked).

import contextlib
import importlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

# Every handler-level test runs at this frozen instant (UIFPE-264): every
# seeded run's before/after-slot relationship clears its grid by >= 4 minutes
# (06:30/07:15/08:30 daily, :45 hourly, the 5-minute rung at 12:00) — PR #184
# failed at 08:32Z because a "5 minutes ago" refresh run predated the 08:30
# slot. 12:02 IS on the gold-10m grid; fine while no 10m test seeds runs, but
# re-audit the margins here before pairing started_min_ago with a new cadence.
# DueNessTest passes explicit nows instead; same principle, unit level.
FROZEN_NOW = datetime(2026, 9, 6, 12, 2, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    """The real datetime class with now() pinned — arithmetic, comparisons
    and isinstance checks against run timestamps all still work."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else \
            FROZEN_NOW.replace(tzinfo=None)

# src/apps/lambda_dispatcher/ is on sys.path via qe/conftest.py. Its modules
# load by BARE name (not as lambda_dispatcher.gold_dispatcher) to match the
# flat Lambda zip layout the deployed function uses.


def load_module():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"):
        if "gold_dispatcher" in sys.modules:
            del sys.modules["gold_dispatcher"]
        return importlib.import_module("gold_dispatcher")


def make_cfg(gold_id="product", enabled=True, frequency=None,
             reconcile_enabled=False, glue=None, target=True,
             sources=None, snapshots=None, metadata=None,
             snapshot_gate=None, start_time=None, cooldown=None,
             weekly_days=None):
    cfg = {
        "gold_id": gold_id,
        "version": "latest",
        "enabled": enabled,
        "reconcile": {"enabled": reconcile_enabled},
    }
    if target:
        cfg["target"] = {"database": "uif_echo_{env}_gold_product",
                         "table": gold_id}
    if frequency is not None or start_time is not None \
            or cooldown is not None or weekly_days is not None:
        cfg["schedule"] = {}
        if frequency is not None:
            cfg["schedule"]["frequency"] = frequency
        if start_time is not None:
            cfg["schedule"]["start_time"] = start_time
        if cooldown is not None:
            cfg["schedule"]["failure_cooldown_minutes"] = cooldown
        if weekly_days is not None:
            cfg["schedule"]["weekly_days"] = weekly_days
    if glue is not None:
        cfg["glue"] = glue
    if sources is not None:
        cfg["sources"] = sources
    if snapshots is not None:
        cfg["watermark"] = {"silver_snapshots": snapshots}
    if metadata is not None:
        cfg["metadata"] = metadata
    if snapshot_gate is not None:
        cfg["snapshot_gate"] = snapshot_gate
    return cfg


SRC = [{"alias": "items",
        "silver_table": "uif_echo_{env}_silver_mdm.mtl_system_items_b"},
       {"alias": "items_ext",
        "silver_table": "uif_echo_{env}_silver_mdm.ego_mtl_sy_items_ext_b"}]


def table_response(metadata_location=None):
    params = {}
    if metadata_location:
        params["metadata_location"] = metadata_location
    return {"Table": {"Parameters": params}}


class EntityNotFoundException(Exception):
    """What the Glue client raises for a missing table. _target_table_exists
    matches the exception by class NAME, so a real class is required: the old
    `e.__class__.__name__ = ...` trick on a bare Exception raises TypeError on
    Python 3.12, which the helper then read as "catalog check errored" — every
    'table absent' test was silently exercising the wrong branch (UIFPE-265
    review)."""


class DispatcherHarness:
    """Mocked-boto3 fixture + seeding helpers, with NO tests of its own.

    Extracted from GoldDispatcherTest by UIFPE-149. The subclasses used to
    inherit the fixture from a class that also held ~28 tests, so every new
    subclass re-ran all of them — three copies before this change, and seven
    once the cadence-ladder classes landed. A mixin gives the subclasses the
    fixture without the duplicate executions."""

    def setUp(self):
        self.gd = load_module()
        # Pin the dispatcher's clock to FROZEN_NOW (UIFPE-264). _run() builds
        # timestamps from the same instant, so due-ness, guard-horizon and
        # cooldown arithmetic never depend on when the suite happens to run.
        # Drop the patched module afterwards so no later `import
        # gold_dispatcher` elsewhere inherits the frozen clock.
        self.gd.datetime = FrozenDatetime
        self.addCleanup(sys.modules.pop, "gold_dispatcher", None)
        # The deployed Lambda always has ENV set (dispatcher.tf). Resource-name
        # resolution now hard-fails without it (finding #15), so the tests set
        # it explicitly — see MissingEnvTest for the failure path.
        self.env_patch = mock.patch.dict(os.environ, {"ENV": "dev"}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.table = mock.MagicMock()
        self.gd.dynamo = mock.MagicMock()
        self.gd.dynamo.Table.return_value = self.table
        self.gd.glue = mock.MagicMock()
        # no in-flight runs by default
        self.gd.glue.get_paginator.return_value.paginate.return_value = []
        self.gd.glue.start_job_run.return_value = {"JobRunId": "jr_1"}
        # Default: target table exists, no stored snapshots → gate dispatches,
        # CDC profile selected. Individual tests override get_table.
        self.gd.glue.get_table.return_value = table_response()

    def _scan(self, items):
        """Single-page scan (no LastEvaluatedKey)."""
        self.table.scan.return_value = {"Items": items}

    def _paginated_scan(self, pages):
        """Multi-page scan: `pages` is a list of item-lists. Every page but the
        last carries a LastEvaluatedKey, as DynamoDB does."""
        responses = []
        for idx, items in enumerate(pages):
            resp = {"Items": items}
            if idx < len(pages) - 1:
                resp["LastEvaluatedKey"] = {"gold_id": f"page{idx}",
                                            "version": "latest"}
            responses.append(resp)
        self.table.scan.side_effect = responses

    def _in_flight(self, *runs):
        """Seed get_job_runs with the given run dicts for every job name."""
        self.gd.glue.get_paginator.return_value.paginate.return_value = [
            {"JobRuns": list(runs)}
        ]

    def _pages(self, *pages):
        """Multi-page get_job_runs response — one dict per page, newest first."""
        self.gd.glue.get_paginator.return_value.paginate.return_value = \
            [{"JobRuns": list(p)} for p in pages]

    def _gold_absent(self):
        """Target table missing from the catalog (silver tables still resolve)
        — the first-load shape."""
        def route(DatabaseName=None, Name=None):
            if "gold" in DatabaseName:
                raise EntityNotFoundException("not found")
            return table_response()
        self.gd.glue.get_table.side_effect = route

    @staticmethod
    def _run(state="SUCCEEDED", gold_id="product", started_min_ago=None,
             ended_min_ago=None, refresh=False, rebuild=False,
             recompute=False):
        """One JobRun dict. Timestamps are OMITTED unless asked for, because
        their absence is a real case the guard has to handle (a WAITING run
        carries no StartedOn). refresh/rebuild mark it as MAINTENANCE, which
        blocks the guard but must never satisfy a schedule slot; recompute is
        the deliberate opposite — a scheduled run that DOES satisfy its slot. Ages are
        relative to FROZEN_NOW — the same clock the dispatcher reads."""
        now = FROZEN_NOW
        run = {"JobRunState": state, "Arguments": {"--gold_id": gold_id}}
        if refresh:
            run["Arguments"]["--refresh_columns"] = "colA"
        if recompute:
            # Deliberately NOT maintenance: the recompute leg rides a scheduled
            # run and has to satisfy that run's slot (ADR-0020 decision 5).
            run["Arguments"]["--recompute_columns"] = "colA"
        if rebuild:
            run["Arguments"]["--rebuild"] = "true"
        if started_min_ago is not None:
            run["StartedOn"] = now - timedelta(minutes=started_min_ago)
        if ended_min_ago is not None:
            run["CompletedOn"] = now - timedelta(minutes=ended_min_ago)
        return run


class GoldDispatcherTest(DispatcherHarness, unittest.TestCase):
    def test_handler_rejects_unknown_frequency(self):
        with self.assertRaises(ValueError):
            self.gd.handler({"frequency": "hourly"})
        with self.assertRaises(ValueError):
            self.gd.handler({})

    def test_gold_daily_triggers_default_frequency_configs(self):
        self._scan([make_cfg()])  # no schedule block → defaults to "gold"
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual(args["Arguments"], {"--gold_id": "product"})
        self.assertEqual(args["WorkerType"], "G.2X")
        self.assertEqual(args["NumberOfWorkers"], 16)
        # Fallback raised from 60 (review F2): a first-ever incremental
        # against a large source under a 60-minute ceiling is an outage.
        self.assertEqual(args["Timeout"], 180)

    def test_gold_hourly_is_routed_and_matched(self):
        """The upstream bug this port fixed: relay-echo's hourly rule emitted
        "gold-hourly" and its handler never routed it, so hourly gold configs
        were silently never dispatched.

        UIFPE-149 changed WHAT the payload means — every transform payload is now
        one sweep, and the pipelines carry their own cadences — but the guarantee
        this test exists for is unchanged and still worth pinning: a gold-hourly
        payload must DISPATCH, not raise and not no-op."""
        self._scan([make_cfg(frequency="gold-hourly"),
                    make_cfg(gold_id="other", frequency="gold")])
        out = self.gd.handler({"frequency": "gold-hourly"})
        # Both are due (neither has ever run), and a sweep serves every pipeline
        # rather than only those "on" the payload's cadence.
        self.assertEqual(sorted(t["gold_id"] for t in out["triggered"]),
                         ["other", "product"])

    def test_frequency_mismatch_skips(self):
        """Kept under its original name because the SKIP it guards still exists —
        but the reason changed. There is no longer a payload/cadence mismatch to
        detect: a pipeline is skipped when its own slot has not arrived, which is
        what "this tick is not for you" now means."""
        # A run that started JUST NOW is after the most recent slot whatever the
        # wall clock says. "One minute ago on a 5-minute cadence" would straddle
        # the slot boundary about one time in five and make this flaky.
        self._scan([make_cfg(frequency="gold-24h", start_time="06:30")])
        self._pages([self._run("SUCCEEDED", started_min_ago=0, ended_min_ago=0)])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])

    def test_reconcile_fires_only_reconcile_enabled(self):
        self._scan([make_cfg(reconcile_enabled=False),
                    make_cfg(gold_id="with_rec", reconcile_enabled=True)])
        out = self.gd.handler({"frequency": "gold-reconcile"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["with_rec"])
        # reconcile default sizing is smaller
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual(args["NumberOfWorkers"], 4)

    def test_in_flight_gold_id_is_skipped(self):
        self._in_flight({"JobRunState": "RUNNING",
                         "Arguments": {"--gold_id": "product"}})
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")

    def test_in_flight_check_fails_open(self):
        """POSTURE INVERTED by UIFPE-149 — kept under its original name so the
        change shows up in the diff of this test rather than looking like a new
        one appearing beside an untouched old one.

        It used to assert that a blind guard dispatches anyway, on the in-code
        rationale "a missed cadence is worse than a rare stacked run". That was
        correct when a missed tick meant a missed RUN: the rule fired once a day
        and there was no second chance.

        Slot-based due-ness removed the dilemma. A skipped sweep does not lose the
        slot — the next sweep, five minutes later, computes the same previous_slot
        and dispatches. So refusing now costs minutes of lateness, while
        dispatching blind still risks two writers on one Gold table and
        watermark."""
        self.gd.glue.get_paginator.return_value.paginate.side_effect = \
            Exception("throttled")
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])
        self.gd.glue.start_job_run.assert_not_called()

    def test_fail_open_logs_distinct_alarmable_warning(self):
        """#8: the guard's degraded path must be alarmable, not silent. Still
        true — only the marker changed with the posture (see above)."""
        self.gd.glue.get_paginator.return_value.paginate.side_effect = \
            Exception("ThrottlingException")
        self._scan([make_cfg()])
        with self.assertLogs(self.gd.logger, level="WARNING") as captured:
            self.gd.handler({"frequency": "gold"})
        self.assertTrue(
            any("GOLD_DISPATCH_GUARD_FAIL_CLOSED" in line
                for line in captured.output),
            f"expected the alarmable marker in {captured.output}")

    # ── #8: STARTING / WAITING count as in-flight ────────────────────────
    def test_starting_run_counts_as_in_flight(self):
        self._in_flight({"JobRunState": "STARTING",
                         "Arguments": {"--gold_id": "product"}})
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")

    def test_waiting_run_counts_as_in_flight(self):
        self._in_flight({"JobRunState": "WAITING",
                         "Arguments": {"--gold_id": "product"}})
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])

    def test_terminal_states_do_not_block(self):
        self._in_flight(
            {"JobRunState": "SUCCEEDED", "Arguments": {"--gold_id": "product"}},
            {"JobRunState": "FAILED",    "Arguments": {"--gold_id": "product"}},
            {"JobRunState": "TIMEOUT",   "Arguments": {"--gold_id": "product"}},
            {"JobRunState": "STOPPED",   "Arguments": {"--gold_id": "product"}},
        )
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    # ── #8 / #1: transform and reconcile are mutually exclusive per gold_id ──
    def test_running_transform_blocks_reconcile_for_same_gold_id(self):
        """The reconcile/transform race: reconcile must not anti-join Gold
        while a transform for that gold_id is still writing it."""
        self._in_flight({"JobRunState": "RUNNING",
                         "Arguments": {"--gold_id": "product"}})
        self._scan([make_cfg(reconcile_enabled=True)])
        out = self.gd.handler({"frequency": "gold-reconcile"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")

    def test_running_reconcile_blocks_transform_for_same_gold_id(self):
        self._in_flight({"JobRunState": "RUNNING",
                         "Arguments": {"--gold_id": "product"}})
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])

    def test_guard_checks_both_job_names(self):
        self._scan([make_cfg()])
        self.gd.handler({"frequency": "gold"})
        scanned = {c.kwargs.get("JobName") for c in
                   self.gd.glue.get_paginator.return_value.paginate.call_args_list}
        self.assertEqual(scanned, {"uif-echo-dev-gold-transform",
                                   "uif-echo-dev-gold-reconcile"})

    def test_other_gold_id_in_flight_does_not_block(self):
        self._in_flight({"JobRunState": "RUNNING",
                         "Arguments": {"--gold_id": "other"}})
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])

    # ── #7: the config scan must paginate ───────────────────────────────
    def test_scan_follows_last_evaluated_key(self):
        self._paginated_scan([
            [make_cfg(gold_id="p1")],
            [make_cfg(gold_id="p2")],
            [make_cfg(gold_id="p3")],
        ])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]],
                         ["p1", "p2", "p3"])
        self.assertEqual(self.table.scan.call_count, 3)
        # page 2 and 3 must resume from the previous page's key
        self.assertEqual(
            self.table.scan.call_args_list[1].kwargs["ExclusiveStartKey"],
            {"gold_id": "page0", "version": "latest"})

    def test_scan_stops_when_no_last_evaluated_key(self):
        self._scan([make_cfg()])
        self.gd.handler({"frequency": "gold"})
        self.assertEqual(self.table.scan.call_count, 1)
        self.assertNotIn("ExclusiveStartKey",
                         self.table.scan.call_args_list[0].kwargs)

    def test_empty_filtered_page_still_continues(self):
        """A page whose items all fail the FilterExpression comes back empty
        but WITH a LastEvaluatedKey — the loop must not stop there."""
        self._paginated_scan([[], [], [make_cfg(gold_id="late")]])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["late"])

    def test_glue_sizing_overrides_respected(self):
        self._scan([make_cfg(glue={"worker_type": "G.4X", "num_workers": 32,
                                   "timeout_minutes": 120})])
        self.gd.handler({"frequency": "gold"})
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual(args["WorkerType"], "G.4X")
        self.assertEqual(args["NumberOfWorkers"], 32)
        self.assertEqual(args["Timeout"], 120)

    def test_start_failure_recorded_not_fatal(self):
        self.gd.glue.start_job_run.side_effect = Exception("AccessDenied")
        self._scan([make_cfg()])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])
        self.assertIn("AccessDenied", out["skipped"][0]["reason"])


class SnapshotGateTest(DispatcherHarness, unittest.TestCase):
    """F1: the dispatcher skips StartJobRun only on positive evidence that no
    Silver commit happened since the last successful run. Every uncertain
    branch dispatches, and gate errors NEVER block dispatch."""

    def _get_table_router(self, gold_exists=True, silver_locs=None,
                          silver_raises=None, gold_raises=None):
        """Route get_table by database: gold target vs silver sources."""
        silver_locs = silver_locs or {}
        def route(DatabaseName=None, Name=None):
            if "gold" in DatabaseName:
                if gold_raises:
                    raise gold_raises
                if gold_exists:
                    return table_response()
                raise EntityNotFoundException("not found")
            if silver_raises:
                raise silver_raises
            return table_response(silver_locs.get(Name))
        self.gd.glue.get_table.side_effect = route

    def test_all_snapshots_match_skips_the_run(self):
        self._get_table_router(silver_locs={
            "mtl_system_items_b": "s3://m/1.json",
            "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([make_cfg(sources=SRC, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "no_silver_change")

    def test_one_moved_pointer_dispatches(self):
        self._get_table_router(silver_locs={
            "mtl_system_items_b": "s3://m/2.json",   # moved
            "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([make_cfg(sources=SRC, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_no_stored_map_dispatches(self):
        self._scan([make_cfg(sources=SRC)])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_alias_missing_from_map_dispatches(self):
        # A source added to the config since the map was written: the new
        # alias has no entry — skip-forever would be the failure mode here.
        self._get_table_router(silver_locs={
            "mtl_system_items_b": "s3://m/1.json",
            "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([make_cfg(sources=SRC,
                             snapshots={"items": "s3://m/1.json"})])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_gate_error_fails_open_and_dispatches(self):
        self._get_table_router(silver_raises=Exception("Throttling"))
        self._scan([make_cfg(sources=SRC, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_denied_target_probe_logs_the_alarmable_marker(self):
        """UIFPE-498: a denied target probe makes `exists` None, which skips the
        gate entirely rather than erroring inside it — so the marker operators
        are told to alarm on (dispatcher.tf) never fired, and a stale IAM scope
        disabled the gate estate-wide for days unnoticed."""
        self._get_table_router(
            gold_raises=Exception("AccessDeniedException: glue:GetTable"),
            silver_locs={"mtl_system_items_b": "s3://m/1.json",
                         "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        # last_run_id present: the pipeline HAS run, so the gate was applicable.
        self._scan([make_cfg(sources=SRC,
                             metadata={"last_run_id": "jr_prior"}, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        with self.assertLogs(self.gd.logger, level="WARNING") as captured:
            out = self.gd.handler({"frequency": "gold"})
        self.assertTrue(
            any("GOLD_GATE_FAIL_OPEN" in line for line in captured.output),
            f"expected the alarmable marker in {captured.output}")
        # and it still dispatches — the gate is a cost optimization, never a
        # correctness guard, so uncertainty must not stall the pipeline
        self.assertEqual(len(out["triggered"]), 1)

    def test_a_working_probe_does_not_log_fail_open(self):
        """Control for the test above: with the probe answering, matching
        snapshots must skip the run and stay quiet. Without this, the marker
        assertion would pass just as well on a warning logged unconditionally."""
        self._get_table_router(silver_locs={
            "mtl_system_items_b": "s3://m/1.json",
            "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([make_cfg(sources=SRC,
                             metadata={"last_run_id": "jr_prior"}, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        with self.assertLogs(self.gd.logger, level="INFO") as captured:
            out = self.gd.handler({"frequency": "gold"})
        self.assertFalse(
            any("GOLD_GATE_FAIL_OPEN" in line for line in captured.output),
            f"fail-open marker logged on a healthy probe: {captured.output}")
        self.assertEqual(out["skipped"][0]["reason"], "no_silver_change")

    def test_gate_opt_out_dispatches_without_catalog_calls(self):
        self._scan([make_cfg(sources=SRC, snapshot_gate=False, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_reconcile_frequency_is_ungated(self):
        self._get_table_router(silver_locs={
            "mtl_system_items_b": "s3://m/1.json",
            "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([make_cfg(reconcile_enabled=True, sources=SRC, snapshots={
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"})])
        out = self.gd.handler({"frequency": "gold-reconcile"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_malformed_config_skips_that_pipeline_only(self):
        # Per-pipeline isolation: the broken item is recorded, the healthy
        # one still dispatches.
        broken = make_cfg(gold_id="broken",
                          glue={"num_workers": "sixteen"})
        self._scan([broken, make_cfg(gold_id="healthy")])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]],
                         ["healthy"])
        self.assertEqual(out["skipped"][0]["gold_id"], "broken")

    def test_env_token_parity_with_runtime(self):
        import gold_cdc
        raw = "uif_echo_{env}_silver_mdm.mtl_system_items_b"
        self.assertEqual(self.gd._resolve_env_token(raw),
                         gold_cdc.resolve_env(raw, "dev"))


class CapacityProfileTest(DispatcherHarness, unittest.TestCase):
    """F2: profile selection. Table existence is authoritative; last_run_id
    is only a heavy hint when the check errors; full_load falls back to the
    heavy constants, never the lean block."""

    def test_first_run_uses_full_load_profile(self):
        self._gold_absent()
        self._scan([make_cfg(glue={
            "worker_type": "G.1X", "num_workers": 4, "timeout_minutes": 45,
            "full_load": {"worker_type": "G.2X", "num_workers": 16,
                          "timeout_minutes": 180}})])
        self.gd.handler({"frequency": "gold"})
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual((args["WorkerType"], args["NumberOfWorkers"],
                          args["Timeout"]), ("G.2X", 16, 180))

    def test_first_run_without_full_load_block_gets_heavy_constants(self):
        # The undersized-CTAS trap: missing full_load must NEVER fall back to
        # the lean block.
        self._gold_absent()
        self._scan([make_cfg(glue={"worker_type": "G.1X", "num_workers": 2,
                                   "timeout_minutes": 30})])
        self.gd.handler({"frequency": "gold"})
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual((args["WorkerType"], args["NumberOfWorkers"],
                          args["Timeout"]), ("G.2X", 16, 180))

    def test_cdc_run_uses_lean_profile(self):
        self._scan([make_cfg(glue={
            "worker_type": "G.1X", "num_workers": 4, "timeout_minutes": 45,
            "full_load": {"worker_type": "G.2X", "num_workers": 16,
                          "timeout_minutes": 180}})])
        self.gd.handler({"frequency": "gold"})
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual((args["WorkerType"], args["NumberOfWorkers"],
                          args["Timeout"]), ("G.1X", 4, 45))

    def test_target_check_error_with_null_last_run_id_goes_heavy(self):
        def route(DatabaseName=None, Name=None):
            if "gold" in DatabaseName:
                raise Exception("Throttling")
            return table_response()
        self.gd.glue.get_table.side_effect = route
        self._scan([make_cfg(glue={"worker_type": "G.1X", "num_workers": 2,
                                   "timeout_minutes": 30})])
        self.gd.handler({"frequency": "gold"})
        self.assertEqual(
            self.gd.glue.start_job_run.call_args.kwargs["WorkerType"], "G.2X")

    def test_target_check_error_with_prior_run_stays_lean(self):
        def route(DatabaseName=None, Name=None):
            if "gold" in DatabaseName:
                raise Exception("Throttling")
            return table_response()
        self.gd.glue.get_table.side_effect = route
        self._scan([make_cfg(glue={"worker_type": "G.1X", "num_workers": 2,
                                   "timeout_minutes": 30},
                             metadata={"last_run_id": "jr_prior"})])
        self.gd.handler({"frequency": "gold"})
        self.assertEqual(
            self.gd.glue.start_job_run.call_args.kwargs["WorkerType"], "G.1X")

    def test_reconcile_uses_lean_profile_with_its_own_worker_fallback(self):
        self._scan([make_cfg(reconcile_enabled=True, glue={})])
        self.gd.handler({"frequency": "gold-reconcile"})
        args = self.gd.glue.start_job_run.call_args.kwargs
        self.assertEqual(args["NumberOfWorkers"], 4)


class NamingTest(unittest.TestCase):
    def test_names_follow_the_standard(self):
        from gold_resources import gold_database, gold_transform_job
        self.assertEqual(gold_transform_job(env="dev"), "uif-echo-dev-gold-transform")
        self.assertEqual(gold_database("product", env="dev"), "uif_echo_dev_gold_product")

    def test_explicit_env_never_consults_environment(self):
        """An explicit env= must work even with ENV unset (#15)."""
        from gold_resources import gold_database, gold_transform_job
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gold_transform_job(env="prod"), "uif-echo-prod-gold-transform")
            self.assertEqual(gold_database("product", env="prod"), "uif_echo_prod_gold_product")


class MissingEnvTest(unittest.TestCase):
    """#15 — a missing/empty ENV must be a hard failure, never a silent
    fallback to dev. The dev default meant a Lambda deployed without ENV
    addressed dev resources from every environment and looked healthy."""

    def test_names_raise_when_env_unset(self):
        from gold_resources import gold_config_table, gold_database
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                gold_config_table()
            self.assertIn("ENV", str(ctx.exception))
            with self.assertRaises(RuntimeError):
                gold_database("product")

    def test_names_raise_when_env_is_empty_or_whitespace(self):
        from gold_resources import gold_transform_job
        for value in ("", "   "):
            with mock.patch.dict(os.environ, {"ENV": value}, clear=True):
                with self.assertRaises(RuntimeError):
                    gold_transform_job()

    def test_legacy_environment_var_still_honoured(self):
        from gold_resources import gold_transform_job
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "uat"}, clear=True):
            self.assertEqual(gold_transform_job(), "uif-echo-uat-gold-transform")

    def test_handler_fails_the_invocation_not_the_import(self):
        """Resolution is deferred: importing the module must stay safe (tests
        and cold-start tooling depend on it), while an invocation that needs a
        resource name fails loudly."""
        with mock.patch.dict(os.environ, {}, clear=True):
            gd = load_module()          # import must NOT raise
            gd.dynamo = mock.MagicMock()
            gd.glue = mock.MagicMock()
            with self.assertRaises(RuntimeError):
                gd.handler({"frequency": "gold"})

    def test_explicit_env_vars_override_and_avoid_eds(self):
        """With the resource names supplied by Terraform, ENV is not needed."""
        with mock.patch.dict(
            os.environ,
            {"GOLD_JOB_NAME": "j-transform",
             "GOLD_RECONCILE_JOB_NAME": "j-reconcile",
             "GOLD_CONFIG_TABLE": "t-config"},
            clear=True,
        ):
            gd = load_module()
            table = mock.MagicMock()
            table.scan.return_value = {"Items": [make_cfg()]}
            gd.dynamo = mock.MagicMock()
            gd.dynamo.Table.return_value = table
            gd.glue = mock.MagicMock()
            gd.glue.get_paginator.return_value.paginate.return_value = []
            gd.glue.start_job_run.return_value = {"JobRunId": "jr_1"}
            out = gd.handler({"frequency": "gold"})
            self.assertEqual(out["job_name"], "j-transform")
            gd.dynamo.Table.assert_called_once_with("t-config")


# ── UIFPE-149: the cadence ladder ───────────────────────────────────────────

class CadenceLadderTest(DispatcherHarness, unittest.TestCase):
    """Eleven cadences instead of two, and the rename of both originals.

    The dangerous failure mode here is SILENT: a payload and a config that no
    longer agree produce `[SKIPPED] schedule.frequency=... != payload=...` and
    nothing else. Product's live 06:30 run would simply stop happening."""

    def test_every_rung_dispatches_its_own_configs(self):
        from gold_frequencies import TRANSFORM_FREQUENCIES
        for freq in TRANSFORM_FREQUENCIES:
            with self.subTest(freq=freq):
                self.gd.glue.start_job_run.reset_mock()
                self._scan([make_cfg(frequency=freq)])
                out = self.gd.handler({"frequency": freq})
                self.assertEqual([t["gold_id"] for t in out["triggered"]],
                                 ["product"])

    def test_one_sweep_serves_every_cadence_at_once(self):
        """The point of the model change: cadence stopped being a property of
        the RULE. A single sweep considers every pipeline, whatever rung it is
        on, and due-ness decides which ones start."""
        self._scan([make_cfg(gold_id="fast", frequency="gold-5m"),
                    make_cfg(gold_id="slow", frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(sorted(x["gold_id"] for x in out["triggered"]),
                         ["fast", "slow"])

    # ── the deploy-order skew window: all four combinations must dispatch ──
    def test_new_dispatcher_matches_a_legacy_gold_config(self):
        """THE regression this guards: Terraform is applied at step 2 but
        configs are not re-synced until step 4, so a new dispatcher reads
        `frequency: gold` items for a while. Product's daily run depends on
        this one passing."""
        self._scan([make_cfg(frequency="gold")])
        out = self.gd.handler({"frequency": "gold-24h"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])

    def test_new_dispatcher_matches_a_legacy_hourly_config(self):
        self._scan([make_cfg(frequency="gold-hourly")])
        out = self.gd.handler({"frequency": "gold-1h"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])

    def test_a_legacy_payload_matches_a_migrated_config(self):
        """The other direction: a still-deployed EventBridge rule emitting
        `gold` must dispatch a config already migrated to `gold-24h`, or a
        partially-applied Terraform run silently stops the cadence."""
        self._scan([make_cfg(frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertEqual(out["requested_frequency"], "gold")

    def test_gold_60m_is_an_alias_for_one_hour(self):
        self._scan([make_cfg(frequency="gold-60m")])
        out = self.gd.handler({"frequency": "gold-1h"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])

    def test_missing_schedule_block_still_means_daily(self):
        self._scan([make_cfg()])          # no schedule block at all
        out = self.gd.handler({"frequency": "gold-24h"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])


class GuardFailClosedTest(DispatcherHarness, unittest.TestCase):
    """The fail-open/closed inversion. Failing open is right at daily cadence
    (a skipped tick costs a day) and wrong below it (a skipped tick costs
    minutes; a stacked run puts two writers on one table and watermark).

    The two pre-existing fail-open tests use `gold`, which normalizes to
    gold-24h, so they still pass unchanged — the inversion applies only where
    the original rationale no longer holds."""

    def _guard_unavailable(self):
        self.gd.glue.get_paginator.return_value.paginate.side_effect = \
            Exception("ThrottlingException")

    def test_sub_hourly_cadence_dispatches_nothing_when_the_guard_is_blind(self):
        self._guard_unavailable()
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"],
                         [{"gold_id": "product", "reason": "guard_unavailable"}])
        self.gd.glue.start_job_run.assert_not_called()

    def test_fail_closed_logs_a_distinct_alarmable_marker(self):
        self._guard_unavailable()
        self._scan([make_cfg(frequency="gold-30m")])
        with self.assertLogs(self.gd.logger, level="WARNING") as captured:
            self.gd.handler({"frequency": "gold-tick"})
        self.assertTrue(
            any("GOLD_DISPATCH_GUARD_FAIL_CLOSED" in l for l in captured.output),
            f"expected the fail-closed marker in {captured.output}")

    def test_fail_closed_reports_every_pipeline_the_sweep_could_not_serve(self):
        """A blind sweep cannot tell WHICH pipelines were due — due-ness is read
        from the same run history the walk failed to fetch. Reporting every
        in-scope pipeline is the honest answer rather than a guess."""
        self._guard_unavailable()
        self._scan([make_cfg(gold_id="fast", frequency="gold-5m"),
                    make_cfg(gold_id="slow", frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(sorted(s["gold_id"] for s in out["skipped"]),
                         ["fast", "slow"])
        self.assertTrue(all(s["reason"] == "guard_unavailable"
                            for s in out["skipped"]))

    def test_the_daily_fail_open_exception_is_gone(self):
        """Before UIFPE-149 gold-24h dispatched blind, because a skipped daily
        tick cost a whole day. Slot-based due-ness made that unnecessary — the
        next sweep retries the same slot 5 minutes later — so there is no longer
        a cadence for which fail-open is the better trade."""
        self._guard_unavailable()
        self._scan([make_cfg(frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])

    def test_reconcile_fails_closed_despite_being_nightly(self):
        """Reconcile hard-DELETEs rows. An unguarded reconcile can anti-join
        Gold while a transform is still writing it, so it fails closed even
        though its interval is daily — a stale table is recoverable, deleted
        rows plus an emitted DELETE verdict are not."""
        self._guard_unavailable()
        self._scan([make_cfg(reconcile_enabled=True)])
        out = self.gd.handler({"frequency": "gold-reconcile"})
        self.assertEqual(out["triggered"], [])
        self.gd.glue.start_job_run.assert_not_called()

    def test_a_healthy_guard_dispatches_at_every_cadence(self):
        """Sanity check on the above: fail-closed must not become always-closed."""
        for freq in ("gold-5m", "gold-30m", "gold-24h"):
            with self.subTest(freq=freq):
                self.gd.glue.start_job_run.reset_mock()
                self._scan([make_cfg(frequency=freq)])
                out = self.gd.handler({"frequency": freq})
                self.assertEqual(len(out["triggered"]), 1)


class BoundedHistoryWalkTest(DispatcherHarness, unittest.TestCase):
    """The walk used to paginate the job's entire run history on every tick,
    twice. At ~628 ticks/day against a history that only grows, that is what
    throttles — and a throttled guard used to mean no guard at all."""

    def test_walk_stops_once_a_page_predates_the_horizon(self):
        """A page entirely older than the horizon ends the walk: nothing beyond
        it can still be running."""
        old = [self._run(started_min_ago=5000)]
        self._pages(old, [self._run("RUNNING", started_min_ago=1)])
        self._scan([make_cfg(frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold-24h"})
        # The RUNNING run on page 2 was never read, so dispatch proceeds.
        self.assertEqual(len(out["triggered"]), 1)

    def test_walk_continues_while_runs_are_inside_the_horizon(self):
        """Page 2's RUNNING run must be found, or the guard would let a second
        writer start. Page 1 holds a MAINTENANCE run: it never satisfies a slot,
        so the pipeline is unambiguously due and the in-flight guard is what
        stops it — which is exactly what this asserts."""
        page1 = [self._run("SUCCEEDED", started_min_ago=10, refresh=True)]
        self._pages(page1, [self._run("RUNNING", started_min_ago=20)])
        self._scan([make_cfg(frequency="gold-5m", start_time="00:00")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")

    def test_a_run_with_no_started_on_never_shortens_the_walk(self):
        """A WAITING run queued behind the concurrency limit may carry no
        StartedOn. Uncertainty must not end the walk early, and must not drop
        the run from the guard."""
        self._pages([self._run("WAITING")],
                    [self._run("RUNNING", started_min_ago=1)])
        self._scan([make_cfg(frequency="gold-24h")])
        out = self.gd.handler({"frequency": "gold-24h"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")

    def test_horizon_grows_with_the_configured_timeout(self):
        """A pipeline is free to raise its own timeout; a horizon shorter than a
        real timeout would stop the walk before reaching a live run."""
        self.assertEqual(
            self.gd._horizon_minutes([make_cfg(glue={"timeout_minutes": 600})]),
            600 + self.gd.GUARD_HORIZON_MARGIN_MINUTES)

    def test_horizon_floors_at_the_heavy_default(self):
        self.assertEqual(
            self.gd._horizon_minutes([make_cfg()]),
            self.gd.FULL_LOAD_DEFAULTS["timeout_minutes"]
            + self.gd.GUARD_HORIZON_MARGIN_MINUTES)

    def test_page_backstop_marks_the_guard_incomplete(self):
        """Hitting GUARD_MAX_PAGES must be treated as "guard unverified", never
        silently truncated into a false "nothing in flight"."""
        page = [self._run(started_min_ago=1)]
        self._pages(*[page] * (self.gd.GUARD_MAX_PAGES + 3))
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["skipped"][0]["reason"], "guard_unavailable")


class FailureCooldownTest(DispatcherHarness, unittest.TestCase):
    """Without a cooldown a deterministically broken pipeline on gold-5m starts
    288 runs a day, doubled by max_retries=1, at 8-16 DPU — and the first
    signal is the invoice. The in-flight guard cannot help: it blocks unfinished
    runs, never failed ones (test_terminal_states_do_not_block pins that)."""

    def test_recent_failure_holds_off_the_next_tick(self):
        self._pages([self._run("FAILED", started_min_ago=20, ended_min_ago=5)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "failure_cooldown")

    def test_cooldown_expires_so_a_transient_failure_self_heals(self):
        """A cooldown, NOT a circuit breaker: block-until-success would wedge a
        pipeline permanently after one transient failure."""
        self._pages([self._run("FAILED", started_min_ago=90, ended_min_ago=45)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_a_successful_newest_run_never_holds_off(self):
        self._pages([self._run("SUCCEEDED", started_min_ago=20,
                               ended_min_ago=1)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_configured_cooldown_wins_over_the_default(self):
        cfg = make_cfg(frequency="gold-5m")
        cfg["schedule"]["failure_cooldown_minutes"] = 5
        self._pages([self._run("FAILED", started_min_ago=30, ended_min_ago=10)])
        self._scan([cfg])
        # 10 min since the failure > the configured 5 min, so it may run again
        # even though the 30-minute default would still be holding it.
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_zero_disables_the_cooldown(self):
        cfg = make_cfg(frequency="gold-5m")
        cfg["schedule"]["failure_cooldown_minutes"] = 0
        self._pages([self._run("FAILED", started_min_ago=6, ended_min_ago=1)])
        self._scan([cfg])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_a_manual_stop_also_starts_the_cooldown(self):
        """A human who stops a runaway run does not want the next tick to
        restart it. The escape hatch is a manual start-job-run, which does not
        go through this Lambda."""
        self._pages([self._run("STOPPED", started_min_ago=20, ended_min_ago=2)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["skipped"][0]["reason"], "failure_cooldown")

    def test_a_failure_with_no_end_timestamp_does_not_hold_off(self):
        """Opposite posture to the in-flight guard, on purpose: the cooldown is
        a cost optimization, not a correctness guard, so with no evidence of
        WHEN the failure happened it must not stall a healthy pipeline."""
        self._pages([self._run("FAILED", started_min_ago=20)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)

    def test_another_gold_ids_failure_is_irrelevant(self):
        self._pages([self._run("FAILED", gold_id="other", started_min_ago=20,
                               ended_min_ago=1)])
        self._scan([make_cfg(frequency="gold-5m")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(len(out["triggered"]), 1)


class DueNessTest(DispatcherHarness, unittest.TestCase):
    """The scheduling decision itself (UIFPE-149). Tested against _due_skip with
    an EXPLICIT `now` rather than end-to-end, because a test whose outcome
    depends on the wall clock when CI happens to run is worse than no test."""

    @staticmethod
    def _at(iso):
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def _ran_at(self, iso, **kw):
        return dict(self._run(**kw), StartedOn=self._at(iso))

    def test_a_pipeline_that_has_never_run_is_due(self):
        cfg = make_cfg(frequency="gold-24h", start_time="06:30")
        self.assertIsNone(
            self.gd._due_skip(cfg, None, self._at("2026-08-12T00:01:00Z")))

    def test_due_once_the_slot_passes(self):
        cfg = make_cfg(frequency="gold-24h", start_time="06:30")
        # Ran yesterday morning; today's 06:30 slot has now passed.
        self.assertIsNone(self.gd._due_skip(
            cfg, self._ran_at("2026-08-11T06:31:00Z"),
            self._at("2026-08-12T06:30:00Z")))

    def test_not_due_before_the_slot(self):
        cfg = make_cfg(frequency="gold-24h", start_time="06:30")
        # 06:29 — yesterday's slot is the current one, and the last run is after
        # it. A tick that fires a minute early must not pull the run forward.
        self.assertIsNotNone(self.gd._due_skip(
            cfg, self._ran_at("2026-08-11T06:31:00Z"),
            self._at("2026-08-12T06:29:00Z")))

    def test_not_due_twice_for_one_slot(self):
        """The property that keeps a 5-minute sweep from starting a daily
        pipeline 288 times: once a slot is satisfied, it stays satisfied."""
        cfg = make_cfg(frequency="gold-24h", start_time="06:30")
        ran = self._ran_at("2026-08-12T06:30:05Z")
        for now in ("2026-08-12T06:35:00Z", "2026-08-12T12:00:00Z",
                    "2026-08-12T23:59:00Z"):
            with self.subTest(now=now):
                self.assertIsNotNone(
                    self.gd._due_skip(cfg, ran, self._at(now)))

    # ── gold-weekly (UIFPE-532): a day SET, one time, the same slot rule ──
    # 2026-09-22 is a Tuesday, 2026-09-24 a Thursday.

    def test_weekly_tue_thu_is_due_at_the_thursday_slot_and_not_before(self):
        cfg = make_cfg(frequency="gold-weekly", start_time="03:00",
                       weekly_days=["TUE", "THU"])
        ran_tue = self._ran_at("2026-09-22T03:02:00Z")
        self.assertIsNotNone(self.gd._due_skip(          # Wednesday: nothing due
            cfg, ran_tue, self._at("2026-09-23T15:00:00Z")))
        self.assertIsNotNone(self.gd._due_skip(          # 02:59 Thu: early tick
            cfg, ran_tue, self._at("2026-09-24T02:59:00Z")))
        self.assertIsNone(self.gd._due_skip(             # 03:00 Thu: due
            cfg, ran_tue, self._at("2026-09-24T03:00:00Z")))

    def test_weekly_skipped_tick_still_dispatches_late(self):
        cfg = make_cfg(frequency="gold-weekly", start_time="03:00",
                       weekly_days=["TUE", "THU"])
        ran_tue = self._ran_at("2026-09-22T03:02:00Z")
        # the 03:00 tick was lost; 03:05 computes the same Thursday slot
        self.assertIsNone(self.gd._due_skip(
            cfg, ran_tue, self._at("2026-09-24T03:05:00Z")))

    def test_weekly_not_due_twice_for_one_slot_across_the_long_gap(self):
        """Thu -> Tue is five days: once Thursday's slot is satisfied the entity
        stays quiet through the weekend and Monday, however many ticks fire."""
        cfg = make_cfg(frequency="gold-weekly", start_time="03:00",
                       weekly_days=["TUE", "THU"])
        ran_thu = self._ran_at("2026-09-24T03:02:00Z")
        for now in ("2026-09-24T03:05:00Z", "2026-09-26T12:00:00Z",
                    "2026-09-28T23:59:00Z", "2026-09-29T02:59:00Z"):
            with self.subTest(now=now):
                self.assertIsNotNone(
                    self.gd._due_skip(cfg, ran_thu, self._at(now)))
        self.assertIsNone(self.gd._due_skip(
            cfg, ran_thu, self._at("2026-09-29T03:00:00Z")))   # Tuesday again

    def test_weekly_uses_the_day_set_not_the_default_monday(self):
        """The known-bad shape: _due_skip ignoring weekly_days would make a
        Tue+Thu entity run on Mondays. 2026-09-21 is a Monday."""
        cfg = make_cfg(frequency="gold-weekly", start_time="03:00",
                       weekly_days=["TUE", "THU"])
        ran_thu = self._ran_at("2026-09-17T03:02:00Z")
        self.assertIsNotNone(self.gd._due_skip(          # Monday: not a run day
            cfg, ran_thu, self._at("2026-09-21T03:00:00Z")))
        self.assertIsNone(self.gd._due_skip(             # Tuesday: due
            cfg, ran_thu, self._at("2026-09-22T03:00:00Z")))

    def test_weekly_with_no_day_set_runs_on_mondays_at_the_default_clock(self):
        cfg = make_cfg(frequency="gold-weekly")
        ran = self._ran_at("2026-09-14T06:31:00Z")        # previous Monday
        self.assertIsNotNone(self.gd._due_skip(
            cfg, ran, self._at("2026-09-20T12:00:00Z")))  # Sunday
        self.assertIsNone(self.gd._due_skip(
            cfg, ran, self._at("2026-09-21T06:30:00Z")))  # Monday 06:30

    def test_two_daily_pipelines_with_different_start_times_run_at_different_times(self):
        """THE feature. Before UIFPE-149 both of these shared one EventBridge
        rule and could only run at the same time."""
        early = make_cfg(gold_id="early", frequency="gold-24h",
                         start_time="06:30")
        late = make_cfg(gold_id="late", frequency="gold-24h",
                        start_time="18:00")
        yesterday = self._ran_at("2026-08-11T20:00:00Z")
        at_seven = self._at("2026-08-12T07:00:00Z")
        # 07:00 is past 06:30 but not past 18:00.
        self.assertIsNone(self.gd._due_skip(early, yesterday, at_seven))
        self.assertIsNotNone(self.gd._due_skip(late, yesterday, at_seven))
        # …and after 18:00 the late one becomes due while the early one has
        # already been satisfied by its own morning run.
        at_nineteen = self._at("2026-08-12T19:00:00Z")
        self.assertIsNone(self.gd._due_skip(late, yesterday, at_nineteen))
        self.assertIsNotNone(self.gd._due_skip(
            early, self._ran_at("2026-08-12T06:30:10Z"), at_nineteen))

    def test_start_time_phases_the_grid_rather_than_delaying_the_first_run(self):
        """14:45 on a 4-hour cadence means 02:45/06:45/…/22:45, NOT "nothing
        before 14:45". Getting this wrong would leave a pipeline idle for most of
        its first day."""
        cfg = make_cfg(frequency="gold-4h", start_time="14:45")
        self.assertIsNone(self.gd._due_skip(
            cfg, self._ran_at("2026-08-12T02:50:00Z"),
            self._at("2026-08-12T06:46:00Z")))

    def test_a_maintenance_run_does_not_satisfy_a_slot(self):
        """A --refresh_columns repair recomputes one column; it is not the
        scheduled incremental. Letting it count would silently skip that slot's
        real run — and the guard already prevents the two from overlapping, so
        nothing is gained by conflating them."""
        cfg = make_cfg(frequency="gold-24h", start_time="06:30")
        # _job_run_facts is what filters these out, so assert at that level.
        self._pages([self._run("SUCCEEDED", started_min_ago=1, refresh=True),
                     self._run("SUCCEEDED", started_min_ago=3000)])
        _in_flight, newest, scheduled, maint, ok = self.gd._job_run_facts(
            "uif-echo-dev-gold-transform", 240)
        self.assertTrue(ok)
        self.assertIn("--refresh_columns", newest["product"]["Arguments"])
        self.assertNotIn("--refresh_columns", scheduled["product"]["Arguments"])
        # UIFPE-121: the run the incremental ignores is exactly the run the
        # REFRESH sweep schedules against — the two sets are complementary,
        # which is what stops each from satisfying the other's slot.
        self.assertIn("--refresh_columns", maint["product"]["Arguments"])
        self.assertIsNotNone(cfg)

    def test_a_rebuild_run_does_not_satisfy_a_slot_either(self):
        self._pages([self._run("SUCCEEDED", started_min_ago=1, rebuild=True)])
        _in_flight, newest, scheduled, maint, _ok = self.gd._job_run_facts(
            "uif-echo-dev-gold-transform", 240)
        self.assertIn("product", newest)
        self.assertNotIn("product", scheduled)
        self.assertIn("product", maint)

    def test_a_maintenance_run_still_blocks_the_guard(self):
        """The other half of the same decision: maintenance must not satisfy a
        slot, but it MUST block — two writers on one Gold table is the thing the
        guard exists for, whatever started them."""
        self._pages([self._run("RUNNING", started_min_ago=1, refresh=True)])
        in_flight, _newest, _scheduled, _maint, _ok = self.gd._job_run_facts(
            "uif-echo-dev-gold-transform", 240)
        self.assertIn("product", in_flight)


class ResolvedScheduleTest(DispatcherHarness, unittest.TestCase):
    """Defaults and malformed values. The dispatcher must never drop a pipeline
    out of the sweep over a bad field — gold_config.validate is where a bad value
    is REJECTED, at sync time, with a human watching."""

    def test_defaults_when_no_schedule_block_exists(self):
        freq, start, interval = self.gd._resolved_schedule(make_cfg())
        self.assertEqual((freq, start, interval), ("gold-24h", "06:30", 1440))

    def test_legacy_frequency_resolves_with_its_historical_clock(self):
        for legacy, want_freq, want_start in (("gold", "gold-24h", "06:30"),
                                              ("gold-hourly", "gold-1h", "00:45"),
                                              ("gold-60m", "gold-1h", "00:45")):
            with self.subTest(legacy=legacy):
                freq, start, _iv = self.gd._resolved_schedule(
                    make_cfg(frequency=legacy))
                self.assertEqual((freq, start), (want_freq, want_start))

    def test_unknown_frequency_falls_back_rather_than_dropping_the_pipeline(self):
        freq, start, interval = self.gd._resolved_schedule(
            make_cfg(frequency="gold-fortnightly"))
        self.assertEqual((freq, start, interval), ("gold-24h", "06:30", 1440))

    def test_weekly_is_a_rung_of_its_own_with_its_day_set(self):
        """UIFPE-532: gold-weekly resolves (it is no longer the 'unknown'
        example above), keeps its 06:30 default, and carries a day set that
        defaults to Monday, drops junk, and is None for every other rung."""
        freq, start, interval = self.gd._resolved_schedule(
            make_cfg(frequency="gold-weekly"))
        self.assertEqual((freq, start, interval), ("gold-weekly", "06:30", 10080))
        self.assertEqual(self.gd._resolved_weekly_days(
            make_cfg(frequency="gold-weekly")), ("MON",))
        self.assertEqual(self.gd._resolved_weekly_days(
            make_cfg(frequency="gold-weekly", weekly_days=["thu", "TUE"])),
            ("TUE", "THU"))
        for bad in ([], ["FUNDAY"], "TUE", 3):
            with self.subTest(bad=bad):
                self.assertEqual(self.gd._resolved_weekly_days(
                    make_cfg(frequency="gold-weekly", weekly_days=bad)), ("MON",))
        self.assertIsNone(self.gd._resolved_weekly_days(
            make_cfg(frequency="gold-24h", weekly_days=["TUE"])))

    def test_malformed_start_time_falls_back_to_the_cadence_default(self):
        for bad in ("25:00", "half past six", "", ":30", None):
            with self.subTest(bad=bad):
                _f, start, _iv = self.gd._resolved_schedule(
                    make_cfg(frequency="gold-4h", start_time=bad))
                self.assertEqual(start, "00:49")

    def test_an_explicit_start_time_wins(self):
        _f, start, _iv = self.gd._resolved_schedule(
            make_cfg(frequency="gold-4h", start_time="14:45"))
        self.assertEqual(start, "14:45")


if __name__ == "__main__":
    unittest.main()


class RecomputeLegTest(DispatcherHarness, unittest.TestCase):
    """ADR-0020 decision 5: the recompute leg rides the entity's OWN scheduled
    run, once every N of them. Replaces RefreshSweepTest, whose separate sweep,
    separate payload and separate slot grid this retires.

    The leg exists for what detection cannot see by construction: a value
    defined as "effective now", which moves when a date boundary passes, and a
    hard delete, which stamps nothing. No timestamp scan finds either.

    Three properties are load-bearing:
      * it is NOT maintenance — --recompute_columns deliberately stays out of
        MAINTENANCE_ARGS, so the run still satisfies its slot and still
        announces. Reusing --refresh_columns would break all three at once.
      * it bypasses the snapshot gate, because it repairs staleness that no
        pointer move ever signals.
      * N scales the grid to (interval x N). Getting that wrong makes the
        declared ratio silently 1, which shows up only on the invoice."""

    @staticmethod
    def _cfg(cols=("effectiveListPrice",), every=8, frequency="gold-24h",
             start_time="07:15", marker=None, ran_before=True, **kw):
        cfg = make_cfg(frequency=frequency, start_time=start_time,
                       metadata={"last_run_id": "jr_0"} if ran_before else None,
                       **kw)
        cfg["schedule"]["recompute"] = {
            "undetectable_columns": cols if isinstance(cols, str) else list(cols),
            "every_n_runs": every}
        if marker is not None:
            cfg.setdefault("watermark", {})["last_recompute_at"] = marker
        return cfg

    def _args(self):
        return self.gd.glue.start_job_run.call_args.kwargs["Arguments"]

    # -- due-ness -----------------------------------------------------------

    def test_an_absent_marker_makes_the_leg_due(self):
        """Also how a newly-declared leg gets its first pass."""
        self._scan([self._cfg(cols=("effectiveListPrice", "activeIndicator"))])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertEqual(self._args()["--recompute_columns"],
                         "effectiveListPrice,activeIndicator")

    def test_a_marker_at_or_after_the_slot_holds_the_leg(self):
        """The run still happens — it is the entity's own scheduled run. Only
        the leg is withheld, which is the whole point of folding it in."""
        self._scan([self._cfg(marker="2026-09-01T00:00:00+00:00")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertNotIn("--recompute_columns", self._args())

    def test_a_marker_before_the_slot_makes_the_leg_due_again(self):
        self._scan([self._cfg(marker="2026-08-20T07:15:00+00:00")])
        self.gd.handler({"frequency": "gold-tick"})
        self.assertIn("--recompute_columns", self._args())

    def test_no_recompute_block_means_the_leg_never_fires(self):
        """Most entities declare none. They must never be dispatched with an
        empty --recompute_columns, which costs a full stored-Gold read to
        recompute nothing."""
        self._scan([make_cfg(frequency="gold-24h", start_time="07:15",
                             metadata={"last_run_id": "jr_0"})])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertNotIn("--recompute_columns", self._args())

    def test_every_n_runs_scales_the_grid_to_interval_times_n(self):
        """THE ratio, pinned on an explicit clock. One marker, two values of N:
        four days after a daily entity's last leg, N=8 is not yet due (its grid
        is eight days) while N=1 is. Without this a wrong grid still passes
        every other test here — it just fires eight times too often."""
        now = datetime(2026, 9, 6, 12, 2, tzinfo=timezone.utc)
        marker = "2026-09-02T00:00:00+00:00"
        self.assertFalse(
            self.gd._recompute_due(self._cfg(every=8), marker, now),
            "N=8 on a daily entity must put the slot 8 days back")
        self.assertTrue(
            self.gd._recompute_due(self._cfg(every=1), marker, now),
            "N=1 must be the schedule itself")

    def test_an_unparseable_marker_repairs_rather_than_skipping(self):
        """A corrupt marker must not silently retire the leg forever."""
        self._scan([self._cfg(marker="not-a-timestamp")])
        self.gd.handler({"frequency": "gold-tick"})
        self.assertIn("--recompute_columns", self._args())

    def test_a_naive_marker_is_read_as_utc(self):
        """Everything else in the estate stamps UTC; a marker written without
        an offset must not raise on comparison and drop the leg."""
        self._scan([self._cfg(marker="2026-09-01T00:00:00")])
        self.gd.handler({"frequency": "gold-tick"})
        self.assertNotIn("--recompute_columns", self._args())

    # -- the leg is not maintenance -----------------------------------------

    def test_the_leg_is_not_marked_maintenance(self):
        """--refresh_columns would have cost three things at once: the run would
        stop satisfying its own slot (maintenance runs never do), the transform
        would return before detection ran, and the announce would be suppressed.
        So the leg's argument is deliberately NOT in MAINTENANCE_ARGS."""
        self._scan([self._cfg()])
        self.gd.handler({"frequency": "gold-tick"})
        args = self._args()
        self.assertIn("--recompute_columns", args)
        self.assertNotIn("--refresh_columns", args)
        self.assertNotIn("--rebuild", args)
        # ...and a run carrying it counts as SCHEDULED, so it satisfies the slot
        self.assertFalse(self.gd._is_maintenance(
            {"Arguments": {"--gold_id": "product",
                           "--recompute_columns": "effectiveListPrice"}}))
        # ...while the manual repair tool's argument still does not
        self.assertTrue(self.gd._is_maintenance(
            {"Arguments": {"--gold_id": "product",
                           "--refresh_columns": "effectiveListPrice"}}))

    def test_the_leg_runs_on_the_transform_job_not_a_separate_one(self):
        self._scan([self._cfg()])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["job_name"], "uif-echo-dev-gold-transform")

    def test_a_leg_run_satisfies_the_entitys_next_slot(self):
        """The consequence of the above, at sweep level: a leg run must not
        leave the slot it served looking unserved — otherwise an entity that
        recomputed would run twice for that slot, every time."""
        self._pages([self._run("SUCCEEDED", started_min_ago=5, recompute=True)])
        self._scan([self._cfg(marker="2026-09-06T11:57:00+00:00")])
        with self.assertLogs(level="INFO") as caught:
            out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        # A not-due pipeline is logged, not summarised — being off-slot is the
        # normal case, so it is deliberately absent from summary["skipped"].
        self.assertTrue(
            any("not due" in line and "is at or after the current" in line
                for line in caught.output),
            f"expected a not-due skip for the leg run, got {caught.output}")

    # -- the snapshot gate --------------------------------------------------

    def _snapshot_router(self, locs):
        def route(DatabaseName=None, Name=None):
            if "gold" in DatabaseName:
                return table_response()
            return table_response(locs.get(Name))
        self.gd.glue.get_table.side_effect = route

    def _gated_cfg(self, **kw):
        cfg = self._cfg(**kw)
        cfg["sources"] = SRC
        cfg.setdefault("watermark", {})["silver_snapshots"] = {
            "items": "s3://m/1.json", "items_ext": "s3://e/7.json"}
        return cfg

    def test_matching_snapshots_do_not_skip_a_due_leg(self):
        """Carried forward from the refresh sweep (PR review, 2026-09-07). The
        gate asks "has any source committed since the last run" — the right
        question for detection, the wrong one for the leg, which repairs
        staleness no Silver commit ever caused. Gate the leg on it and a
        date-boundary crossing is never picked up at all."""
        self._snapshot_router({"mtl_system_items_b": "s3://m/1.json",
                               "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([self._gated_cfg()])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertIn("--recompute_columns", self._args())

    def test_the_gate_still_skips_when_the_leg_is_not_due(self):
        """The discriminating control for the test above: the SAME config with
        the leg already served this slot is skipped by the gate as before, so
        the bypass is scoped to a due leg rather than disabling the gate."""
        self._snapshot_router({"mtl_system_items_b": "s3://m/1.json",
                               "ego_mtl_sy_items_ext_b": "s3://e/7.json"})
        self._scan([self._gated_cfg(marker="2026-09-01T00:00:00+00:00")])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "no_silver_change")

    # -- first load, in-flight, degradation ---------------------------------

    def test_the_leg_never_rides_a_first_load(self):
        """A first load computes every column from scratch, so a leg inside it
        is pure waste. Note what changed from the refresh sweep: there is no
        first_load_pending skip any more, because with one sweep there is no
        second dispatcher left to race the first load — the run goes ahead, the
        leg simply does not ride it."""
        self._gold_absent()
        self._scan([self._cfg(ran_before=False)])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual([t["gold_id"] for t in out["triggered"]], ["product"])
        self.assertNotIn("--recompute_columns", self._args())

    def test_an_in_flight_run_blocks_the_leg(self):
        """Whatever is running, the leg must not join it — same Gold table.

        The run carries no StartedOn, so due-ness cannot refuse it and the
        guard is genuinely the thing under test. With one grid a run that DOES
        carry a recent StartedOn is refused as off-slot before the guard is
        reached; both refuse to dispatch, but only this shape proves the
        guard."""
        self._pages([self._run("RUNNING")])
        self._scan([self._cfg()])
        out = self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(out["triggered"], [])
        self.assertEqual(out["skipped"][0]["reason"], "in_flight")
        self.gd.glue.start_job_run.assert_not_called()

    def test_a_malformed_every_n_runs_falls_back_rather_than_dropping_it(self):
        """gold_config rejects these at sync time; one that reaches the
        dispatcher anyway must degrade, never crash the sweep for everyone."""
        for bad in ("loud", 0, -3, None, 2.5):
            with self.subTest(every=bad):
                self.gd.glue.start_job_run.reset_mock()
                self._scan([self._cfg(every=bad)])
                out = self.gd.handler({"frequency": "gold-tick"})
                self.assertEqual(len(out["triggered"]), 1)
                self.assertIn("--recompute_columns", self._args())

    def test_a_comma_string_of_columns_is_tolerated(self):
        self._scan([self._cfg(cols="effectiveListPrice, activeIndicator")])
        self.gd.handler({"frequency": "gold-tick"})
        self.assertEqual(self._args()["--recompute_columns"],
                         "effectiveListPrice,activeIndicator")

    def test_a_blank_column_list_is_no_leg_at_all(self):
        for empty in ([], [""], ["  "], "", ","):
            with self.subTest(cols=empty):
                self.gd.glue.start_job_run.reset_mock()
                self._scan([self._cfg(cols=empty)])
                self.gd.handler({"frequency": "gold-tick"})
                self.assertNotIn("--recompute_columns", self._args())

    def test_the_retired_refresh_payload_is_rejected_not_aliased(self):
        """The old rule may still fire in the minutes between the code update
        and Terraform deleting it. It must raise rather than be treated as a
        transform sweep: a loud failure on a rule that is being deleted reads
        more cheaply than a second sweep nobody knows is running."""
        self._scan([self._cfg()])
        with self.assertRaises(ValueError):
            self.gd.handler({"frequency": "gold-refresh"})
        self.gd.glue.start_job_run.assert_not_called()


class TickTelemetryTest(DispatcherHarness, unittest.TestCase):
    """UIFPE-543: one EMF document per enabled transform entity per sweep,
    PRINTED so CloudWatch extracts it. Freshness reads the transform's own
    heartbeat (metadata.last_transform_at), never the reconcile's stamp."""

    def _sweep(self, payload="gold-tick"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = self.gd.handler({"frequency": payload})
        docs = [json.loads(line) for line in buf.getvalue().splitlines()
                if line.startswith('{"_aws"')]
        return out, docs

    def test_every_enabled_entity_gets_one_document(self):
        self._scan([make_cfg(gold_id="a"), make_cfg(gold_id="b")])
        _out, docs = self._sweep()
        self.assertEqual(sorted(d["GoldId"] for d in docs), ["a", "b"])
        for doc in docs:
            self.assertEqual(doc["_aws"]["CloudWatchMetrics"][0]["Namespace"],
                             "UIF/Gold")
            self.assertEqual(doc["Env"], "dev")

    def test_a_first_run_is_dispatched_and_never_run(self):
        self._scan([make_cfg()])
        out, docs = self._sweep()
        self.assertEqual(len(out["triggered"]), 1)
        (doc,) = docs
        self.assertEqual((doc["Dispatched"], doc["NeverRun"]), (1, 1))
        self.assertNotIn("MinutesSinceLastSuccess", doc)
        self.assertNotIn("FreshnessBreached", doc)

    def test_freshness_reads_the_transform_heartbeat_not_the_reconcile(self):
        # heartbeat 30 minutes old; the shared last_run_at is three days old
        # and must be ignored when the heartbeat exists
        self._scan([make_cfg(metadata={
            "last_transform_at": (FROZEN_NOW - timedelta(minutes=30)).isoformat(),
            "last_run_at": (FROZEN_NOW - timedelta(days=3)).isoformat()})])
        # today's 06:30 slot already served -> not due, not dispatched
        self._in_flight(self._run(started_min_ago=300, ended_min_ago=280))
        out, docs = self._sweep()
        self.assertEqual(out["triggered"], [])
        (doc,) = docs
        self.assertEqual(doc["Dispatched"], 0)
        self.assertEqual(doc["MinutesSinceLastSuccess"], 30)
        self.assertEqual(doc["FreshnessBreached"], 0)
        self.assertEqual(doc["FreshnessSource"], "last_transform_at")
        self.assertEqual(doc["FreshnessLimitMinutes"], 2160)  # 1.5 x daily

    def test_rows_without_the_heartbeat_fall_back_and_say_so(self):
        self._scan([make_cfg(metadata={
            "last_run_at": (FROZEN_NOW - timedelta(days=3)).isoformat()})])
        _out, docs = self._sweep()
        (doc,) = docs
        self.assertEqual(doc["FreshnessSource"], "last_run_at")
        self.assertEqual(doc["MinutesSinceLastSuccess"], 3 * 1440)
        self.assertEqual(doc["FreshnessBreached"], 1)

    def test_bookmark_age_is_reported(self):
        cfg = make_cfg()
        cfg["watermark"] = {
            "bookmark": (FROZEN_NOW - timedelta(hours=2)).isoformat()}
        self._scan([cfg])
        _out, docs = self._sweep()
        self.assertEqual(docs[0]["BookmarkAgeMinutes"], 120)

    def test_the_reconcile_sweep_emits_nothing(self):
        self._scan([make_cfg(reconcile_enabled=True)])
        _out, docs = self._sweep(self.gd.RECONCILE_FREQUENCY)
        self.assertEqual(docs, [])

    def test_a_fail_closed_sweep_still_measures(self):
        """Dispatching nothing is exactly when the freshness signal has to
        keep counting."""
        self.gd.glue.get_paginator.return_value.paginate.side_effect = \
            Exception("ThrottlingException")
        self._scan([make_cfg(frequency="gold-5m")])
        out, docs = self._sweep()
        self.assertEqual(out["triggered"], [])
        (doc,) = docs
        self.assertEqual(doc["Dispatched"], 0)

    def test_a_telemetry_failure_never_costs_a_dispatch(self):
        self._scan([make_cfg()])
        with mock.patch.object(self.gd, "tick_document",
                               side_effect=RuntimeError("boom")):
            with self.assertLogs(self.gd.logger, level="WARNING") as captured:
                out, docs = self._sweep()
        self.assertEqual(len(out["triggered"]), 1)
        self.assertEqual(docs, [])
        self.assertTrue(any("TELEMETRY SKIPPED" in line
                            for line in captured.output))

    def test_documents_are_printed_not_logged(self):
        """A logger prefix in front of the JSON stops CloudWatch parsing it."""
        self._scan([make_cfg()])
        with self.assertLogs(self.gd.logger, level="INFO") as captured:
            _out, docs = self._sweep()
        self.assertEqual(len(docs), 1)
        self.assertFalse(any('"_aws"' in line for line in captured.output))
