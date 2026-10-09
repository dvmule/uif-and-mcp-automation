# test_gold_lambda_alarms.py
# Every Lambda the gold Terraform root declares must be watched by an Errors
# alarm. Terraform cannot enumerate its own resources, so lambda_alarms.tf
# carries a hand-written map -- and a hand-written list is exactly what fell
# eleven behind before UIFPE-725. This test is the completeness guarantee that
# makes the map safe: add a Lambda without an alarm and it fails here.
#
# WHY IT EARNS ITS PLACE: an unwatched Lambda fails silently in the most
# literal sense. UIFPE-723's compile_preview returned Runtime.ImportModuleError
# on EVERY invocation for fifteen days. The AWS/Lambda Errors metric counted
# all of them -- 20 errors from 20 invocations in one five-minute window, by a
# real user -- and no alarm was pointed at it, so nobody was told. The metric
# was never the gap; the alarm was.

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GOLD_TF = REPO / "stacks" / "echo" / "infrastructure" / "terraform-gold"

LAMBDA_RE = re.compile(r'^resource\s+"aws_lambda_function"\s+"(\w+)"', re.M)
FN_REF_RE = re.compile(r"aws_lambda_function\.(\w+)\b")


def tf_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(GOLD_TF.glob("*.tf")))


def block_at(text: str, start: int) -> str:
    """The braced block beginning at or after `start`, by brace counting.

    A regex cannot do this: alarm blocks contain nested `dimensions {` and
    `join(...)` calls, so a non-greedy match to the first `}` truncates and a
    greedy one swallows the next resource.
    """
    i = text.index("{", start)
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    raise AssertionError("unbalanced braces in the Terraform")


def declared_lambdas(text: str) -> set:
    return set(LAMBDA_RE.findall(text))


def error_alarmed(text: str) -> set:
    """Functions covered by an Errors alarm, from both mechanisms in this root:
    the bespoke per-function alarms, and the map lambda_alarms.tf iterates."""
    covered = set()

    # 1. bespoke alarms: an alarm block that names Errors and a function
    for m in re.finditer(r'^resource\s+"aws_cloudwatch_metric_alarm"\s+"(\w+)"',
                         text, re.M):
        body = block_at(text, m.end())
        if re.search(r'metric_name\s*=\s*"Errors"', body):
            covered |= set(FN_REF_RE.findall(body))

    # 2. the derived set: every function referenced inside the map. Sound only
    #    because the resource consuming it is asserted to be an Errors alarm by
    #    test_the_derived_alarm_really_watches_errors below.
    m = re.search(r"^\s*lambda_error_alarms\s*=", text, re.M)
    if m:
        covered |= set(FN_REF_RE.findall(block_at(text, m.end())))
    return covered


class TestGoldLambdaAlarms(unittest.TestCase):

    def test_every_declared_lambda_has_an_errors_alarm(self):
        text = tf_text()
        declared = declared_lambdas(text)
        covered = error_alarmed(text)
        missing = sorted(declared - covered)
        self.assertEqual(
            [], missing,
            "these Lambdas in the gold root have no Errors alarm, so they fail "
            "silently -- add each to local.lambda_error_alarms in "
            f"lambda_alarms.tf: {missing}")

    def test_the_parse_actually_found_the_root(self):
        """Guards against a green pass on an empty read -- a moved directory or
        a reformatted resource header would otherwise assert nothing."""
        text = tf_text()
        declared = declared_lambdas(text)
        self.assertGreaterEqual(len(declared), 9,
                                f"only parsed {len(declared)} Lambdas: {sorted(declared)}")
        for expected in ("compile_preview", "gold_dispatcher", "gold_alert_notify"):
            self.assertIn(expected, declared, f"{expected} not parsed")

    def test_the_derived_alarm_really_watches_errors(self):
        """The map is credited as Errors coverage, so the resource iterating it
        must actually be an Errors alarm. Without this the coverage claim in
        error_alarmed() would be unfounded."""
        text = tf_text()
        m = re.search(r'^resource\s+"aws_cloudwatch_metric_alarm"\s+"lambda_errors"',
                      text, re.M)
        self.assertIsNotNone(m, "the derived lambda_errors alarm is gone")
        body = block_at(text, m.end())
        self.assertRegex(body, r'metric_name\s*=\s*"Errors"')
        self.assertRegex(body, r'namespace\s*=\s*"AWS/Lambda"')
        self.assertRegex(body, r"for_each\s*=\s*local\.lambda_error_alarms")
        self.assertRegex(body, r"alarm_actions\s*=\s*local\.alert_targets",
                         msg="an alarm with no action tells nobody")
        # notBreaching matters: these are on-demand functions with empty
        # periods, and insufficient-data would park them all out of OK.
        self.assertRegex(body, r'treat_missing_data\s*=\s*"notBreaching"')

    def test_the_detector_catches_an_unwatched_lambda(self):
        """Known-bad control. A detector that silently matched nothing would
        make the main assertion pass forever."""
        synthetic = 'resource "aws_lambda_function" "totally_unwatched" {\n  x = 1\n}\n'
        declared = declared_lambdas(synthetic)
        self.assertEqual({"totally_unwatched"}, declared)
        self.assertEqual(set(), error_alarmed(synthetic))
        self.assertEqual({"totally_unwatched"}, declared - error_alarmed(synthetic))

    def test_block_parser_handles_nesting(self):
        """block_at must not truncate at a nested closing brace, or a bespoke
        alarm's function reference after `dimensions { }` would be missed and
        read as uncovered."""
        sample = 'resource "x" "y" {\n  dimensions {\n    a = 1\n  }\n  z = 2\n}\n'
        body = block_at(sample, sample.index('"y"') + 3)
        self.assertIn("z = 2", body)
        self.assertEqual(body.count("{"), body.count("}"))


if __name__ == "__main__":
    unittest.main()
