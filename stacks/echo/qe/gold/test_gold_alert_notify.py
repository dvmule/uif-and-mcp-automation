# test_gold_alert_notify.py
# UIFPE-543: the gold alert notifier - what it accepts (an alarm invoking it
# directly, or the SNS envelope), which entity the alarm is about, who it tells
# (enabled, well-formed rows whose entity filter admits the alarm), what it says,
# and that it never mails when nobody is listed. boto3 is mocked; the
# Notifications client is replaced with a recorder. Then the Terraform that wires
# every gold alarm to it is pinned - per entity, from the domain YAMLs.

import importlib
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import gold_telemetry

STACK = Path(__file__).resolve().parents[2]
REPO = STACK.parents[1]
TF = STACK / "infrastructure" / "terraform-gold"
ALERTS_TF = (TF / "alerts.tf").read_text(encoding="utf-8")
ALL_TF = "\n".join(p.read_text(encoding="utf-8") for p in sorted(TF.glob("*.tf")))

ENV = {
    "ENV": "dev",
    "ALERT_SUBSCRIBERS_TABLE": "uif-echo-dev-alert-subscribers",
    "ALERT_NOTIFICATIONS_SECRET": "uif/beacon/dev/notifications/api",
    "ALERT_NOTIFICATIONS_API_URL": "https://notifications-api.example",
    "ALERT_NOTIFICATIONS_TOKEN_URL": "https://pi.example/tokens",
}


def load_module():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"):
        sys.modules.pop("gold_alert_notify", None)
        return importlib.import_module("gold_alert_notify")


def direct_alarm(name="uif-echo-dev-gold-product-run-failed", gold_id="product", state="ALARM",
                 previous="OK", reason="Threshold Crossed: 1 datapoint [1.0] was greater than 0.",
                 description="The product gold run FAILED."):
    dims = {"Env": "dev", "Status": "FAILED"}
    if gold_id:
        dims["GoldId"] = gold_id
    return {
        "source": "aws.cloudwatch", "region": "eu-west-1", "time": "2026-09-23T16:00:00Z",
        "alarmArn": f"arn:aws:cloudwatch:eu-west-1:1:alarm:{name}",
        "alarmData": {
            "alarmName": name,
            "state": {"value": state, "reason": reason, "timestamp": "2026-09-23T16:00:01.000+0000"},
            "previousState": {"value": previous},
            "configuration": {
                "description": description,
                "metrics": [{"id": "m", "metricStat": {"metric": {
                    "namespace": "UIF/Gold", "name": "RunCount", "dimensions": dims},
                    "period": 300, "stat": "Sum"}, "returnData": True}],
            },
        },
    }


def fleet_alarm(name="uif-echo-dev-gold-dispatch-silent", state="ALARM"):
    event = direct_alarm(name=name, gold_id=None, state=state,
                         description="The gold dispatcher has produced no sweep telemetry.")
    event["alarmData"]["configuration"]["metrics"][0]["metricStat"]["metric"]["dimensions"] = {"Env": "dev"}
    return event


def sns_envelope(name="uif-echo-dev-gold-price-freshness-breached", gold_id="price", state="ALARM"):
    message = {"AlarmName": name, "NewStateValue": state, "OldStateValue": "OK",
               "NewStateReason": "stale", "StateChangeTime": "2026-09-23T16:00:00.000+0000",
               "AlarmDescription": "The price table is stale.", "Region": "EU (Ireland)",
               "Trigger": {"MetricName": "FreshnessBreached", "Namespace": "UIF/Gold",
                           "Dimensions": [{"name": "Env", "value": "dev"},
                                          {"name": "GoldId", "value": gold_id}]}}
    return {"Records": [{"Sns": {"Message": json.dumps(message)}}]}


class Harness:
    def setUp(self):
        self.mod = load_module()
        self.addCleanup(sys.modules.pop, "gold_alert_notify", None)
        self.env = mock.patch.dict(os.environ, ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.table = mock.MagicMock()
        self.table.scan.return_value = {"Items": [{"email": "a@pearson.com"}]}
        self.mod.dynamo = mock.MagicMock()
        self.mod.dynamo.Table.return_value = self.table
        self.sent = []
        client = mock.MagicMock()
        client.send_email.side_effect = lambda **kw: (self.sent.append(kw) or {"eventId": f"evt-{len(self.sent)}"})
        self.client_factory = mock.patch.object(self.mod, "NotificationsClient", return_value=client)
        self.client_factory.start()
        self.addCleanup(self.client_factory.stop)


class ParseTest(Harness, unittest.TestCase):
    def test_direct_alarm_invocation_carries_the_entity(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm())
        self.assertEqual(alarm["name"], "uif-echo-dev-gold-product-run-failed")
        self.assertEqual(alarm["gold_id"], "product")
        self.assertEqual((alarm["state"], alarm["previous"]), ("ALARM", "OK"))
        self.assertIn("Threshold Crossed", alarm["reason"])
        self.assertEqual(alarm["region"], "eu-west-1")

    def test_entity_comes_from_the_dimension_not_the_name(self):
        # marketing_copy: the NAME hyphenates it, the dimension keeps the id
        (alarm,) = self.mod.parse_alarms(direct_alarm(
            name="uif-echo-dev-gold-marketing-copy-run-failed", gold_id="marketing_copy"))
        self.assertEqual(alarm["gold_id"], "marketing_copy")

    def test_fleet_alarm_has_no_entity(self):
        (alarm,) = self.mod.parse_alarms(fleet_alarm())
        self.assertIsNone(alarm["gold_id"])

    def test_sns_envelope_carries_the_entity(self):
        (alarm,) = self.mod.parse_alarms(sns_envelope())
        self.assertEqual(alarm["name"], "uif-echo-dev-gold-price-freshness-breached")
        self.assertEqual(alarm["gold_id"], "price")
        self.assertEqual(alarm["description"], "The price table is stale.")

    def test_junk_yields_nothing_never_raises(self):
        for junk in (None, {}, [], "x", {"Records": [{"Sns": {"Message": "not json"}}]},
                     {"Records": [{"Sns": {"Message": json.dumps({"hello": 1})}}]},
                     {"source": "aws.cloudwatch"}):
            with self.subTest(junk=junk):
                self.assertEqual(self.mod.parse_alarms(junk), [])


class SubscribersTest(Harness, unittest.TestCase):
    def test_enabled_by_default_disabled_skipped_malformed_skipped_deduped(self):
        self.table.scan.return_value = {"Items": [
            {"email": "A@Pearson.com"},
            {"email": "a@pearson.com", "enabled": True},
            {"email": "off@pearson.com", "enabled": False},
            {"email": "not-an-address"},
            {"email": ""},
            {"enabled": True},
            {"email": "b@pearson.com"},
        ]}
        with self.assertLogs(self.mod.logger, level="WARNING") as logs:
            out = self.mod.subscribers(self.table, "product")
        self.assertEqual(out, ["a@pearson.com", "b@pearson.com"])
        self.assertEqual(sum("SUBSCRIBER SKIPPED" in line for line in logs.output), 3)

    def test_entity_filter_self_service(self):
        """A business user subscribed to one table hears about that table only;
        a row without a filter (an operator) hears everything, fleet included."""
        self.table.scan.return_value = {"Items": [
            {"email": "ops@pearson.com"},
            {"email": "product-owner@pearson.com", "gold_ids": {"product"}},
            {"email": "pricing@pearson.com", "gold_ids": ["price", "stock"]},
        ]}
        self.assertEqual(self.mod.subscribers(self.table, "product"),
                         ["ops@pearson.com", "product-owner@pearson.com"])
        self.assertEqual(self.mod.subscribers(self.table, "stock"),
                         ["ops@pearson.com", "pricing@pearson.com"])
        self.assertEqual(self.mod.subscribers(self.table, "program"), ["ops@pearson.com"])
        # fleet-level alarms carry no entity: operators only
        self.assertEqual(self.mod.subscribers(self.table, None), ["ops@pearson.com"])

    def test_paginates(self):
        self.table.scan.side_effect = [
            {"Items": [{"email": "a@x.io"}], "LastEvaluatedKey": {"email": "a@x.io"}},
            {"Items": [{"email": "b@x.io"}]},
        ]
        self.assertEqual(self.mod.subscribers(self.table, "product"), ["a@x.io", "b@x.io"])
        self.assertEqual(self.table.scan.call_args_list[1].kwargs["ExclusiveStartKey"], {"email": "a@x.io"})


class RenderTest(Harness, unittest.TestCase):
    def test_subject_names_the_entity_first(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm())
        subject, body = self.mod.render(alarm, "dev")
        self.assertEqual(subject, "[UIF gold dev] product: run failed")
        self.assertIn("The product gold run FAILED.", body)
        self.assertIn("(was OK)", body)
        self.assertIn("alarmsV2:alarm/uif-echo-dev-gold-product-run-failed", body)
        self.assertIn("Notifications page", body)

    def test_hyphenated_name_still_yields_the_condition(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm(
            name="uif-echo-dev-gold-marketing-copy-freshness-breached", gold_id="marketing_copy"))
        subject, _ = self.mod.render(alarm, "dev")
        self.assertEqual(subject, "[UIF gold dev] marketing_copy: freshness breached")

    def test_fleet_alarm_subject(self):
        (alarm,) = self.mod.parse_alarms(fleet_alarm())
        subject, body = self.mod.render(alarm, "dev")
        self.assertEqual(subject, "[UIF gold dev] gold: dispatch silent")
        self.assertIn("fleet-level, no entity", body)

    def test_recovery_reads_as_recovered(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm(state="OK", previous="ALARM"))
        subject, _ = self.mod.render(alarm, "dev")
        self.assertEqual(subject, "[UIF gold dev] product: run failed - recovered")

    def test_alarm_text_is_escaped(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm(reason="<script>x</script> & more"))
        _, body = self.mod.render(alarm, "dev")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; more", body)


class BrandShellTest(Harness, unittest.TestCase):
    """The mail carries the Pearson brand shell, not bare HTML.

    Before this, the alert mail was an unstyled Arial table. The design is the
    one beacon's export mail uses, so these pin the shell rather than the exact
    markup: a palette token, the font, the logo and the card. Restyling within
    the brand stays green; reverting to bare HTML does not.
    """

    def body(self, **kw):
        (alarm,) = self.mod.parse_alarms(direct_alarm(**kw))
        _, body = self.mod.render(alarm, "dev")
        return body

    def test_carries_the_pearson_brand_shell(self):
        body = self.body()
        for token in ("#EDECF6", "#C1BFFF", "#0D004D", "Plus Jakarta Sans",
                      "PEARSON_LOGO", "Pearson Education Ltd"):
            self.assertIn(token, body, token)

    def test_no_unsubstituted_template_tokens(self):
        """The classic template bug: a renamed placeholder ships as literal text.

        string.Template leaves `${foo}` untouched when the mapping lacks it, so
        a mail can go out with `${cta_url}` printed in it and every other test
        still passing.
        """
        body = self.body()
        self.assertNotIn("${", body)

    def test_the_state_pill_distinguishes_the_three_states(self):
        """Severity has to be visible in the body, not only in the subject.

        The old mail coloured its heading by state; this keeps that signal in a
        pill, because the brand top bar is the same purple in every state.
        """
        seen = {}
        for state in ("ALARM", "OK", "INSUFFICIENT_DATA"):
            body = self.body(state=state)
            pill = [c for c in ("#B00020", "#1B7F3B", "#FFCE00") if c in body]
            self.assertEqual(len(pill), 1, f"{state}: expected one pill colour, got {pill}")
            seen[state] = pill[0]
        self.assertEqual(len(set(seen.values())), 3, f"states share a colour: {seen}")

    def test_an_unknown_state_still_renders(self):
        body = self.body(state="WEIRD_NEW_STATE")
        self.assertNotIn("${", body)
        self.assertIn("WEIRD_NEW_STATE", body)

    def test_the_cta_links_to_the_alarm_and_is_attribute_escaped(self):
        (alarm,) = self.mod.parse_alarms(direct_alarm())
        _, body = self.mod.render(alarm, "dev")
        self.assertIn(self.mod.console_link(alarm).replace("&", "&amp;"), body)
        self.assertIn("Open in CloudWatch", body)

    def test_a_blank_field_is_dropped_not_rendered_empty(self):
        body = self.body(description="")
        self.assertNotIn("What it means", body)

    def test_the_last_row_has_no_divider(self):
        """Design fidelity: beacon's card ends flush, without a trailing rule.

        Counted, not located. The first version of this asserted that the last
        row's label appeared after the final divider — which stays true when
        EVERY row gets a divider, so it passed the mutation that gave the last
        row one. N rows must yield exactly N-1 dividers.
        """
        body = self.body()
        rows = body.count('width="150"')
        dividers = body.count("border-bottom: 1px solid #FFFFFF")
        self.assertGreater(rows, 1, "expected several rows to compare against")
        self.assertEqual(dividers, rows - 1,
                         f"{rows} rows should have {rows - 1} dividers, found {dividers}")


class HandlerTest(Harness, unittest.TestCase):
    def test_mails_every_matching_subscriber_once_per_alarm(self):
        self.table.scan.return_value = {"Items": [
            {"email": "a@pearson.com"}, {"email": "b@pearson.com", "gold_ids": {"product"}},
            {"email": "c@pearson.com", "gold_ids": {"price"}}]}
        out = self.mod.handler(direct_alarm())
        self.assertEqual(out["sent"], 1)
        (mail,) = self.sent
        self.assertEqual(mail["recipients"], ["a@pearson.com", "b@pearson.com"])
        self.assertEqual(mail["subject"], "[UIF gold dev] product: run failed")
        self.assertEqual(out["alarms"][0]["gold_id"], "product")
        self.assertEqual(out["alarms"][0]["event_id"], "evt-1")

    def test_client_is_built_from_this_deployments_env(self):
        self.mod.handler(direct_alarm())
        kwargs = self.mod.NotificationsClient.call_args.kwargs
        self.assertEqual(kwargs["secret_ref"], "uif/beacon/dev/notifications/api")
        self.assertEqual(kwargs["api_url"], "https://notifications-api.example")
        self.assertEqual(kwargs["token_url"], "https://pi.example/tokens")

    def test_no_subscribers_is_loud_and_sends_nothing(self):
        self.table.scan.return_value = {"Items": [{"email": "off@x.io", "enabled": False},
                                                  {"email": "pricing@x.io", "gold_ids": {"price"}}]}
        with self.assertLogs(self.mod.logger, level="WARNING") as logs:
            out = self.mod.handler(direct_alarm())
        self.assertEqual(out, {"sent": 0, "alarms": [], "reason": "no subscribers",
                               "unheard": ["uif-echo-dev-gold-product-run-failed"]})
        self.assertEqual(self.sent, [])
        self.assertTrue(any("NO SUBSCRIBERS" in line and "gold_id=product" in line for line in logs.output))
        self.mod.NotificationsClient.assert_not_called()

    def test_no_alarm_in_event_sends_nothing(self):
        with self.assertLogs(self.mod.logger, level="WARNING"):
            out = self.mod.handler({"detail-type": "something else"})
        self.assertEqual(out["sent"], 0)
        self.table.scan.assert_not_called()

    def test_sns_envelope_is_mailed_too(self):
        out = self.mod.handler(sns_envelope())
        self.assertEqual(out["sent"], 1)
        self.assertEqual(self.sent[0]["subject"], "[UIF gold dev] price: freshness breached")

    def test_missing_wiring_refuses_honestly(self):
        with mock.patch.dict(os.environ, {"ALERT_NOTIFICATIONS_SECRET": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.handler(direct_alarm())
        self.assertIn("ALERT_NOTIFICATIONS_SECRET", str(ctx.exception))

    def test_api_refusal_propagates_to_the_dlq(self):
        # an exception is how Lambda's retry + on_failure destination engage
        self.mod.NotificationsClient.return_value.send_email.side_effect = RuntimeError("HTTP 500")
        with self.assertRaises(RuntimeError):
            self.mod.handler(direct_alarm())


class TerraformWiringTest(unittest.TestCase):
    """The decoy PR 1 found was an alarm nobody hears. Every alarm in the gold
    root must act, and act on the notifier - and since the owner's 2026-09-23
    feedback, per entity."""

    def _alarm_blocks(self):
        return re.findall(r'resource "aws_cloudwatch_metric_alarm" "(\w+)" \{(.*?)\n\}', ALL_TF, re.S)

    def test_every_gold_alarm_acts_on_the_notifier(self):
        blocks = self._alarm_blocks()
        self.assertGreaterEqual(len(blocks), 7)
        for name, body in blocks:
            with self.subTest(alarm=name):
                self.assertRegex(body, r"alarm_actions\s*=\s*(local\.alert_targets|\[aws_lambda_function\.gold_alert_notify\.arn\])")
                self.assertRegex(body, r"ok_actions\s*=\s*(local\.alert_targets|\[aws_lambda_function\.gold_alert_notify\.arn\])")
                self.assertIn("alarm_description", body)

    def test_entity_alarms_are_per_entity_from_the_domain_yamls(self):
        """A new entity's base YAML is what makes it exist; the same file makes
        its alarms exist. Filename must therefore equal gold_id."""
        self.assertIn('for f in fileset("${path.module}/../../../..", "domains/*/src/apps/pipelines/base/*.yaml")',
                      ALERTS_TF)
        blocks = dict(self._alarm_blocks())
        for name in ("gold_run_failed", "gold_freshness_breached", "gold_run_too_long"):
            with self.subTest(alarm=name):
                self.assertIn("for_each            = local.gold_entities", blocks[name])
                self.assertIn("GoldId = each.key", blocks[name])
                self.assertIn('replace(each.key, "_", "-")', blocks[name])
        yamls = sorted(REPO.glob("domains/*/src/apps/pipelines/base/*.yaml"))
        self.assertGreaterEqual(len(yamls), 5)
        for path in yamls:
            with self.subTest(yaml=path.name):
                text = path.read_text(encoding="utf-8")
                gold_id = re.search(r"^gold_id:\s*(\S+)", text, re.M).group(1)
                self.assertEqual(gold_id, path.stem)
        # and the gold root redeploys when one of those files changes
        workflow = (REPO / ".github" / "workflows" / "deploy-echo-gold-dev.yml").read_text(encoding="utf-8")
        self.assertIn('- "domains/*/src/apps/pipelines/base/*.yaml"', workflow)

    def test_alarms_read_metrics_the_library_emits(self):
        """Every UIF/Gold alarm must read a metric something DEMONSTRABLY
        produces, or it sits green forever and alarms on nothing.

        Two producers, not one. gold_telemetry prints EMF documents; a
        CloudWatch log metric filter turns a log line into a metric, which is
        how a RETURNED 500 becomes alarmable at all — Lambda's Errors metric
        only counts raised exceptions. Both are read from source here rather
        than exempted, so an alarm on a metric neither produces still fails.
        """
        emitted = set()
        # Metrics declared by log metric filters in the same namespace.
        for block in re.split(r'resource "aws_cloudwatch_log_metric_filter"',
                              ALL_TF)[1:]:
            block = re.split(r"^\}", block, flags=re.M)[0]
            if '"UIF/Gold"' not in block:
                continue
            # The name INSIDE metric_transformation, not the filter's own.
            _, _, transform = block.partition("metric_transformation")
            found = re.search(r'name\s*=\s*"(\w+)"', transform)
            if found:
                emitted.add(found.group(1))
        for doc in (gold_telemetry.run_document("dev", "x", "SUCCEEDED"),
                    gold_telemetry.tick_document("dev", "x", frequency="gold-24h", interval_minutes=1440,
                                                 last_success_at="2026-01-01T00:00:00+00:00", bookmark=None,
                                                 dispatched=True,
                                                 now=gold_telemetry.parse_iso("2026-01-02T00:00:00Z"))):
            emitted |= {m["Name"] for m in doc["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
        for name, body in self._alarm_blocks():
            # By REGEX, not by exact spacing. terraform fmt aligns `=` to the
            # longest key in each contiguous group, so an alarm whose block has
            # a different longest key gets a different number of spaces and an
            # exact-string check skips it SILENTLY — which is a hole in a guard
            # whose entire job is "no alarm reads a metric nothing emits".
            # Caught when a new alarm landed one space out and this test passed
            # by not looking at it.
            if not re.search(r'namespace\s*=\s*"UIF/Gold"', body):
                continue
            metric = re.search(r'metric_name\s*=\s*"(\w+)"', body).group(1)
            with self.subTest(alarm=name, metric=metric):
                self.assertIn(metric, emitted)
        self.assertIn('Status = "FAILED"', ALERTS_TF)

    def test_alarms_invoke_the_function_directly_not_via_sns(self):
        self.assertNotIn("aws_sns_topic", ALL_TF)
        self.assertIn('principal      = "lambda.alarms.cloudwatch.amazonaws.com"', ALERTS_TF)
        self.assertIn("source_account = data.aws_caller_identity.current.account_id", ALERTS_TF)
        self.assertRegex(ALERTS_TF, r'source_arn\s*=\s*"arn:aws:cloudwatch:\$\{var\.aws_region\}:\$\{data\.aws_caller_identity\.current\.account_id\}:alarm:\$\{local\.name_prefix\}-\*"')

    def test_missing_data_breaches_only_for_the_sweep_itself(self):
        """A disabled entity emits nothing, so per-entity freshness must not
        breach on silence; the dispatcher going silent is its own alarm."""
        blocks = dict(self._alarm_blocks())
        self.assertIn('treat_missing_data = "notBreaching"', blocks["gold_freshness_breached"])
        silent = blocks["gold_dispatch_silent"]
        self.assertIn('treat_missing_data  = "breaching"', silent)
        self.assertIn('metric_name         = "Dispatched"', silent)
        self.assertIn('statistic           = "SampleCount"', silent)
        self.assertIn('comparison_operator = "LessThanThreshold"', silent)
        self.assertNotIn("GoldId", silent)

    def test_notifier_reads_only_and_reuses_beacons_secret(self):
        policy = re.search(r'resource "aws_iam_role_policy" "gold_alert_notify" \{.*?\n\}', ALERTS_TF, re.S).group(0)
        self.assertIn('Action   = ["dynamodb:Scan"]', policy)
        self.assertNotRegex(policy, r"dynamodb:(PutItem|DeleteItem|UpdateItem)")
        self.assertIn('alert_notifications_secret = "uif/beacon/${var.environment}/notifications/api"', ALERTS_TF)
        self.assertIn("secretsmanager:GetSecretValue", policy)

    def test_seed_row_is_created_once_then_left_to_the_console(self):
        seed = re.search(r'resource "aws_dynamodb_table_item" "alert_seed_subscriber" \{.*?\n\}', ALERTS_TF, re.S).group(0)
        self.assertIn("for_each   = toset(var.alert_seed_subscribers)", seed)
        self.assertIn("ignore_changes = [item]", seed)
        self.assertIn("enabled  = { BOOL = true }", seed)
        tfvars = (TF / "envs" / "dev.tfvars").read_text(encoding="utf-8")
        self.assertIn('alert_seed_subscribers = ["reyasahamed.habibrahman@pearson.com"]', tfvars)

    def test_notifier_runs_inside_the_vpc_like_beacons_deliver_lambda(self):
        fn = re.search(r'resource "aws_lambda_function" "gold_alert_notify" \{.*?\n\}', ALERTS_TF, re.S).group(0)
        self.assertRegex(fn, r"vpc_config \{\s*subnet_ids\s*=\s*var\.private_subnet_ids\s*"
                             r"security_group_ids\s*=\s*\[aws_security_group\.gold_alert_notify\.id\]")
        self.assertIn("depends_on = [aws_iam_role_policy_attachment.gold_alert_notify_vpc]", fn)
        sg = re.search(r'resource "aws_security_group" "gold_alert_notify" \{.*?\n\}', ALERTS_TF, re.S).group(0)
        self.assertIn('name   = "${local.name_prefix}-gold-alert-notify-sg"', sg)
        self.assertNotIn("ingress", sg)  # egress-only
        self.assertIn('policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"', ALERTS_TF)
        seat = re.search(r'resource "aws_vpc_security_group_ingress_rule" "gold_alert_notify_secretsmanager_access" \{.*?\n\}',
                         ALERTS_TF, re.S).group(0)
        self.assertIn("referenced_security_group_id = aws_security_group.gold_alert_notify.id", seat)
        self.assertIn("security_group_id            = var.secretsmanager_endpoint_sg_id", seat)
        self.assertIn("replace_triggered_by = [aws_security_group.gold_alert_notify.id]", seat)
        tfvars = (TF / "envs" / "dev.tfvars").read_text(encoding="utf-8")
        beacon = (TF.parents[2] / "beacon" / "infrastructure" / "terraform" / "envs" / "dev.tfvars").read_text(encoding="utf-8")
        for token in ("vpc-0287e62dc748ffab8", "subnet-059a43797031bfb59", "subnet-0084a87a82e01e8d8", "sg-0b45c554b51638786"):
            with self.subTest(token=token):
                self.assertIn(token, tfvars)
                self.assertIn(token, beacon)

    def test_dashboard_lists_every_alarm_and_names_follow_the_standard(self):
        self.assertIn('dashboard_name = "${local.name_prefix}-gold"', ALERTS_TF)
        arns = re.search(r"gold_alarm_arns = concat\((.*?)\n  \)", ALERTS_TF, re.S).group(1)
        for ref in ("aws_cloudwatch_metric_alarm.gold_run_failed :", "aws_cloudwatch_metric_alarm.gold_freshness_breached :",
                    "aws_cloudwatch_metric_alarm.gold_run_too_long :", "gold_dispatch_silent.arn",
                    "gold_dispatch_errors.arn", "gold_announce_dlq_not_empty.arn", "gold_alert_notify_dlq_not_empty.arn"):
            with self.subTest(ref=ref):
                self.assertIn(ref, arns)
        for name in ("run-failed", "freshness-breached", "run-too-long"):
            with self.subTest(alarm=name):
                self.assertIn(f'alarm_name          = "${{local.name_prefix}}-gold-${{replace(each.key, "_", "-")}}-{name}"', ALERTS_TF)
        for name in ("gold-dispatch-silent", "gold-dispatch-errors", "gold-alert-notify-dlq-not-empty"):
            with self.subTest(alarm=name):
                self.assertIn(f'alarm_name          = "${{local.name_prefix}}-{name}"', ALERTS_TF)


if __name__ == "__main__":
    unittest.main()
