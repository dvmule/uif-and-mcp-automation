# test_scheduler_infra.py
# Asserts the EventBridge rules in scheduler.tf are gated the way the parallel
# build needs them to be.
#
# The uif-echo-dev-* stack is built from scratch beside the running echo-dev-*
# one. Both read pipeline configs and dispatch by schedule.frequency, so if
# both had enabled schedule rules every pipeline would fire twice per tick:
# two Bronze runs against the same Oracle source, double the JDBC sessions,
# and two writers landing into different lakes. The new stack therefore sets
# event_rule_state = "DISABLED" and cutover flips it.
#
# The two event-PATTERN rules are the deliberate exception. Their patterns are
# scoped to `jobName = [aws_glue_job.X.name]`, a Terraform reference, so each
# stack's rule only ever matches its own jobs. Gating those would leave the new
# stack's Bronze->GCS and Silver->BQ replication untestable before cutover.

from __future__ import annotations

import re
import unittest
from pathlib import Path

_TF = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform"
_SCHEDULER = _TF / "scheduler.tf"

_EVENT_PATTERN_RULES = {"bronze_succeeded_replicate", "silver_succeeded_bq"}


def _rule_states() -> dict[str, str]:
    """{resource name: whatever `state` is set to} for every event rule."""
    text = _SCHEDULER.read_text(encoding="utf-8")
    states: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r'resource "aws_cloudwatch_event_rule" "(\w+)"', line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r"^\s*state\s*=\s*(\S+)\s*$", line)
        if m and current:
            states[current] = m.group(1).strip('"')
            current = None
    return states


class TestScheduleRulesAreGated(unittest.TestCase):
    def setUp(self):
        self.states = _rule_states()

    def test_the_file_parsed(self):
        self.assertTrue(self._schedule_rules(), "parsed no schedule rules")

    def _schedule_rules(self):
        return {
            k: v for k, v in self.states.items()
            if k not in _EVENT_PATTERN_RULES
        }

    def test_every_schedule_rule_reads_its_gated_state(self):
        # Each rule reads local.schedule_rule_state["<its own name>"], which is
        # var.event_rule_state unless var.event_rule_state_overrides names it.
        ungated = {
            name: state for name, state in self._schedule_rules().items()
            if state != f'local.schedule_rule_state["{name}"]'
        }
        self.assertEqual(
            {}, ungated,
            "these schedule rules bypass the gate and would fire in a parallel "
            f"stack: {sorted(ungated)}",
        )

    def test_the_gate_is_the_variable_unless_overridden(self):
        text = _SCHEDULER.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"rule\s*=>\s*lookup\(var\.event_rule_state_overrides,\s*rule,\s*var\.event_rule_state\)",
        )

    def test_the_gate_lists_exactly_the_schedule_rules(self):
        text = _SCHEDULER.read_text(encoding="utf-8")
        listed = set(re.findall(r'"(\w+)"', text.split("for rule in [", 1)[1].split("]", 1)[0]))
        self.assertEqual(set(self._schedule_rules()), listed)

    def test_there_are_six_of_them(self):
        # 30min, hourly, 4hr, the daily Bronze slots, weekly and the reconcile
        # slots. Two of those are for_each over 12 slots, so six resources are
        # 30 rules in AWS. Gold's rules are the gold dispatcher's (ADR-0018).
        self.assertEqual(6, len(self._schedule_rules()))

    def test_the_job_scoped_event_rules_stay_enabled(self):
        for name in _EVENT_PATTERN_RULES:
            self.assertEqual(
                "ENABLED", self.states.get(name),
                f"{name} matches on its own stack's job name, so gating it "
                f"only makes the new stack untestable",
            )

    def test_those_two_really_are_scoped_by_job_name(self):
        # The claim the exemption rests on. If a pattern stopped naming a
        # specific Glue job, both stacks' rules would see each other's events.
        text = _SCHEDULER.read_text(encoding="utf-8")
        for name in _EVENT_PATTERN_RULES:
            block = text.split(f'"aws_cloudwatch_event_rule" "{name}"', 1)[1]
            block = block.split("\nresource ", 1)[0]
            self.assertRegex(
                block, r"jobName\s*=\s*\[aws_glue_job\.\w+\.name\]",
                f"{name} is not scoped to a Terraform job reference",
            )


class TestTheVariableFailsClosed(unittest.TestCase):
    def test_it_defaults_to_enabled(self):
        # Every existing env omits it and must keep working unchanged.
        text = (_TF / "variables.tf").read_text(encoding="utf-8")
        block = text.split('variable "event_rule_state"', 1)[1].split("\n}", 1)[0]
        self.assertRegex(block, r'default\s*=\s*"ENABLED"')

    def test_it_rejects_anything_but_the_two_values(self):
        text = (_TF / "variables.tf").read_text(encoding="utf-8")
        block = text.split('variable "event_rule_state"', 1)[1]
        self.assertIn('contains(["ENABLED", "DISABLED"], var.event_rule_state)', block)


def _overrides(tfvars: str) -> dict[str, str]:
    """event_rule_state_overrides as set in envs/<tfvars>, {} when absent."""
    text = (_TF / "envs" / tfvars).read_text(encoding="utf-8")
    m = re.search(r"^event_rule_state_overrides\s*=\s*\{(.*?)^\}", text, re.M | re.S)
    if not m:
        return {}
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"(\w+)"', m.group(1), re.M))


class TestPerRuleOverrides(unittest.TestCase):
    """UIFPE-665: UAT keeps its 4-hourly rule ENABLED ahead of cutover. The
    override must stay the narrow exception it was agreed as."""

    def _block(self):
        text = (_TF / "variables.tf").read_text(encoding="utf-8")
        return text.split('variable "event_rule_state_overrides"', 1)[1].split("\n}\n", 1)[0]

    def test_it_defaults_to_no_overrides(self):
        self.assertRegex(self._block(), r"default\s*=\s*\{\}")

    def test_it_rejects_unknown_rules_and_values(self):
        block = self._block()
        self.assertIn('contains(["ENABLED", "DISABLED"], v)', block)
        for rule in ("half_hourly", "hourly", "four_hourly", "daily_bronze_slot", "weekly", "reconcile_slot"):
            self.assertIn(f'"{rule}"', block)

    def test_uat_overrides_only_the_four_hourly_rule(self):
        self.assertEqual({"four_hourly": "ENABLED"}, _overrides("uat-uif.tfvars"))

    def test_uat_default_is_still_disabled(self):
        text = (_TF / "envs" / "uat-uif.tfvars").read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^event_rule_state\s*=\s*"DISABLED"')

    def test_no_other_environment_overrides_anything(self):
        for env in ("dev-uif.tfvars", "dev.tfvars", "uat.tfvars", "prod.tfvars"):
            with self.subTest(env=env):
                self.assertEqual({}, _overrides(env))


if __name__ == "__main__":
    unittest.main()
