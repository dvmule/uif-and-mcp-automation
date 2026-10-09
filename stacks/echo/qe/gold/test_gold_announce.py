# test_gold_announce.py
# Unit tests for src/apps/lambdas/gold_announce.py — no AWS needed (boto3 mocked).

import importlib
import json
import sys
import unittest
from unittest import mock

TRANSFORM = "uif-echo-dev-gold-transform"
RECONCILE = "uif-echo-dev-gold-reconcile"
BUS = "uif-event-dev-bus"
CONFIG_TABLE = "uif-echo-dev-gold-pipeline-config"
RUN_ID = "jr_d4cdbda26c230f416ea76d524507130fb28701a1862b4a449447198c6a565f5a"

ENV = {
    "ENV": "dev",
    "REGION": "eu-west-1",
    "GOLD_JOB_NAME": TRANSFORM,
    "GOLD_RECONCILE_JOB_NAME": RECONCILE,
    "GOLD_CONFIG_TABLE": CONFIG_TABLE,
    "EVENT_BUS_NAME": BUS,
}


def load_module():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"):
        if "gold_announce" in sys.modules:
            del sys.modules["gold_announce"]
        return importlib.import_module("gold_announce")


def glue_event(job_name=TRANSFORM, state="SUCCEEDED", run_id=RUN_ID):
    """The native Glue Job State Change event, as EventBridge delivers it."""
    detail = {"jobName": job_name, "state": state, "severity": "INFO",
              "message": "Job run succeeded"}
    if run_id is not None:
        detail["jobRunId"] = run_id
    return {"source": "aws.glue", "detail-type": "Glue Job State Change",
            "detail": detail}


def fakes(arguments=None, item=None, failed=0):
    """glue / ddb / events doubles. `item` None means no config row."""
    glue = mock.Mock()
    glue.get_job_run.return_value = {
        "JobRun": {"Arguments": arguments if arguments is not None
                   else {"--gold_id": "product"}}
    }
    table = mock.Mock()
    table.get_item.return_value = {"Item": item} if item is not None else {}
    ddb = mock.Mock()
    ddb.Table.return_value = table
    events = mock.Mock()
    events.put_events.return_value = {"FailedEntryCount": failed,
                                      "Entries": [{"ErrorCode": "x"}] if failed
                                      else [{"EventId": "1"}]}
    return glue, ddb, events


OPTED_IN = {"gold_id": "product", "domain": "product", "announce": True}


class AnnounceTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mod = load_module()

    def run_handler(self, event=None, **kw):
        glue, ddb, events = fakes(**kw)
        result = self.mod.handler(event or glue_event(),
                                  glue=glue, ddb=ddb, events=events)
        return result, events

    def sent(self, events):
        """The single PutEvents entry, with Detail parsed."""
        entries = events.put_events.call_args.kwargs["Entries"]
        self.assertEqual(len(entries), 1)
        entry = dict(entries[0])
        entry["Detail"] = json.loads(entry["Detail"])
        return entry

    # ── the contract ────────────────────────────────────────────────────────

    def test_publishes_the_four_field_contract(self):
        result, events = self.run_handler(item=OPTED_IN)
        self.assertTrue(result["announced"])
        entry = self.sent(events)
        self.assertEqual(entry["EventBusName"], BUS)
        self.assertEqual(entry["DetailType"], "GoldMergeCompleted")
        self.assertEqual(entry["Source"], TRANSFORM)
        self.assertEqual(entry["Detail"], {
            "goldRunId": RUN_ID, "goldId": "product",
            "domain": "product", "status": "Success",
        })

    def test_reconcile_announces_under_its_own_source(self):
        _, events = self.run_handler(glue_event(job_name=RECONCILE),
                                     item=OPTED_IN)
        self.assertEqual(self.sent(events)["Source"], RECONCILE)

    def test_domain_comes_from_config_not_from_gold_id(self):
        """The marketing_copy shape: gold_id and domain differ, and the event
        must carry BOTH — the entity to resolve the change log, the domain to
        route. Inferring either from the other is the bug this guards."""
        _, events = self.run_handler(
            arguments={"--gold_id": "marketing_copy"},
            item={"gold_id": "marketing_copy", "domain": "product",
                  "announce": True})
        detail = self.sent(events)["Detail"]
        self.assertEqual(detail["goldId"], "marketing_copy")
        self.assertEqual(detail["domain"], "product")

    # ── the announce opt-in ─────────────────────────────────────────────────

    def test_absent_announce_does_not_publish(self):
        result, events = self.run_handler(
            item={"gold_id": "product", "domain": "product"})
        self.assertFalse(result["announced"])
        events.put_events.assert_not_called()

    def test_false_announce_does_not_publish(self):
        result, events = self.run_handler(
            item={"gold_id": "product", "domain": "product",
                  "announce": False})
        self.assertFalse(result["announced"])
        events.put_events.assert_not_called()

    def test_string_true_counts_as_opted_in(self):
        result, _ = self.run_handler(
            item={"gold_id": "product", "domain": "product",
                  "announce": "true"})
        self.assertTrue(result["announced"])

    # ── runs that must not announce ─────────────────────────────────────────

    def test_failed_run_is_skipped(self):
        result, events = self.run_handler(glue_event(state="FAILED"),
                                          item=OPTED_IN)
        self.assertFalse(result["announced"])
        events.put_events.assert_not_called()

    def test_other_job_is_skipped(self):
        result, events = self.run_handler(
            glue_event(job_name="uif-echo-dev-silver-transform"), item=OPTED_IN)
        self.assertFalse(result["announced"])
        events.put_events.assert_not_called()

    def test_missing_run_id_is_skipped(self):
        result, events = self.run_handler(glue_event(run_id=None),
                                          item=OPTED_IN)
        self.assertFalse(result["announced"])
        events.put_events.assert_not_called()

    def test_run_without_gold_id_is_skipped(self):
        """A console-started run carries no --gold_id: no entity owns it."""
        result, events = self.run_handler(arguments={}, item=OPTED_IN)
        self.assertEqual(result["skipped"], "run carries no --gold_id")
        events.put_events.assert_not_called()

    # ── what gets announced turns on the CHANGE LOG, not on "maintenance" ──
    # A --refresh_columns run in emit mode appends op=REFRESH rows
    # (generic_gold_transform, the REFRESH branch), so its merge has something
    # to publish. Skipping it was why a refresh-detected change reached no
    # channel at all, for price, product and program alike.

    def test_emit_refresh_is_announced(self):
        result, events = self.run_handler(
            arguments={"--gold_id": "product",
                       "--refresh_columns": "list_price",
                       "--refresh_mode": "emit"},
            item=OPTED_IN)
        self.assertTrue(result["announced"])
        events.put_events.assert_called_once()

    def test_refresh_without_a_mode_argument_is_announced(self):
        """The transform DEFAULTS --refresh_mode to emit, so an absent
        argument means emit and the run does write a change log. This is the
        drift guard: if the transform's default ever changes, this test and
        gold_resources.DEFAULT_REFRESH_MODE_ARG must move together."""
        result, events = self.run_handler(
            arguments={"--gold_id": "product",
                       "--refresh_columns": "list_price"},
            item=OPTED_IN)
        self.assertTrue(result["announced"])
        events.put_events.assert_called_once()

    def test_silent_refresh_is_skipped(self):
        result, events = self.run_handler(
            arguments={"--gold_id": "product",
                       "--refresh_columns": "list_price",
                       "--refresh_mode": "silent"},
            item=OPTED_IN)
        self.assertEqual(result["skipped"], "run writes no change log")
        events.put_events.assert_not_called()

    def test_rebuild_is_skipped(self):
        """--rebuild REPLACEs the table and writes no change log, by design —
        that is the whole reason a targeted refresh exists."""
        result, events = self.run_handler(
            arguments={"--gold_id": "product", "--rebuild": "true"},
            item=OPTED_IN)
        self.assertEqual(result["skipped"], "run writes no change log")
        events.put_events.assert_not_called()

    def test_rebuild_wins_over_an_emit_refresh_mode(self):
        """A run carrying both is a rebuild: it writes no change log whatever
        --refresh_mode says."""
        result, events = self.run_handler(
            arguments={"--gold_id": "product", "--rebuild": "true",
                       "--refresh_mode": "emit"},
            item=OPTED_IN)
        self.assertEqual(result["skipped"], "run writes no change log")
        events.put_events.assert_not_called()

    # ── loud failures ───────────────────────────────────────────────────────

    def test_missing_config_item_raises(self):
        with self.assertRaises(ValueError) as caught:
            self.run_handler(item=None)
        self.assertIn(CONFIG_TABLE, str(caught.exception))

    def test_config_without_domain_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            self.run_handler(item={"gold_id": "product", "announce": True})

    def test_rejected_put_events_raises_so_the_dlq_sees_it(self):
        with self.assertRaises(RuntimeError):
            self.run_handler(item=OPTED_IN, failed=1)

    # ── wiring ──────────────────────────────────────────────────────────────

    def test_reads_the_run_it_was_told_about(self):
        glue, ddb, events = fakes(item=OPTED_IN)
        self.mod.handler(glue_event(), glue=glue, ddb=ddb, events=events)
        glue.get_job_run.assert_called_once_with(JobName=TRANSFORM, RunId=RUN_ID)
        ddb.Table.assert_called_once_with(CONFIG_TABLE)
        ddb.Table.return_value.get_item.assert_called_once_with(
            Key={"gold_id": "product", "version": "latest"})


if __name__ == "__main__":
    unittest.main()
