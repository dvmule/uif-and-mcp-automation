# test_verify_step_fallback.py
#
# A batch the IAM simulator REFUSES must not be counted as a denial.
#
# WHAT HAPPENED. bootstrap-environment.sh verifies the rendered policy with one
# simulate-principal-policy call per scope -- ~23 calls instead of hundreds, as
# apicurio's per-action version took over ten minutes. Adding
# apigateway:TagResource and UntagResource to the api entry put them in the same
# scope as the HTTP-verb actions, and the simulator refused the call:
#
#   InvalidInput: Invalid Input Actions: [apigateway:DELETE,apigateway:GET,
#   apigateway:PATCH,apigateway:POST,apigateway:PUT] and
#   [apigateway:TagResource,apigateway:UntagResource] require different
#   authorization information.
#
# API Gateway has two action vocabularies and the simulator will not evaluate
# both in one request. Both sit legally in one IAM statement and AWS evaluates
# them fine at runtime -- only the simulator objects.
#
# The old code counted a failed call as `failed += ${#action_list[@]}`. So
# adding two actions turned five previously-verified actions into SEVEN reported
# failures, and the step died with:
#
#   7 of 238 action(s) are still not allowed ... Add the missing actions to
#   terraform-iam-map.json and re-run
#
# which is the one thing that could not have helped. A verification that reports
# a policy problem where none exists is worse than no verification: it blocks the
# apply and points at the wrong file. The whole reason this step exists is to be
# trusted when it says stop.
#
# PER ACTION, NOT PER VOCABULARY. The split is a property of the simulator, not
# of this map, so hard-coding which actions clash would be one more thing to
# keep in step with AWS. Retrying one action at a time needs to know nothing.
import os
import re
import unittest


HERE = os.path.dirname(__file__)
BOOTSTRAP = os.path.join(
    HERE, "..", "..", "infrastructure", "deploy", "bootstrap-environment.sh"
)


def source():
    with open(BOOTSTRAP, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


def _retry_block():
    """Just the per-action retry loop, from `for one_action` to its `done`.

    A character-count window is not good enough here: the text that follows the
    loop also increments `failed`, so a window even slightly too wide passes
    with the loop's own increment deleted.
    """
    src = uncommented()
    i = src.index("for one_action in")
    end = src.index("\n      done", i)
    return src[i:end]


class TestARefusedBatchIsRetried(unittest.TestCase):
    def test_a_failed_batch_does_not_add_to_the_failure_count(self):
        # The regression in one line. `failed=$((failed + ${#action_list[@]}))`
        # is what turned a refused call into seven denials.
        self.assertNotRegex(
            uncommented(),
            r"failed=\$\(\(failed \+ \$\{#action_list\[@\]\}\)\)",
            "a refused batch must be retried per action, not counted as a "
            "denial for every action in the scope",
        )

    def test_the_fallback_simulates_one_action_at_a_time(self):
        src = uncommented()
        self.assertRegex(
            src, r"for one_action in \"\$\{action_list\[@\]\}\"",
            "expected a per-action retry loop over the scope's actions",
        )
        self.assertRegex(
            src, r'--action-names "\$one_action"',
            "the retry must pass a single action, which is the whole point",
        )

    def test_the_retry_still_passes_every_arn(self):
        # An action is allowed if ANY of the scope's ARNs allows it, so the
        # retry has to keep the full ARN list. Narrowing it to one ARN would
        # report real grants as denials -- the glue scope alone carries five
        # ARN shapes and a job action denies against the database ARN.
        self.assertRegex(
            _retry_block(), r'--resource-arns "\$\{arn_list\[@\]\}"',
            "the per-action retry must keep the scope's whole ARN list",
        )

    def test_the_retry_is_reported_not_silent(self):
        self.assertRegex(
            uncommented(), r'warn "batched simulate refused scope',
            "a scope that fell back to per-action calls should say so; it is "
            "the signal that AWS split a vocabulary and the run got slower",
        )


class TestTheVerdictLogicIsSharedNotDuplicated(unittest.TestCase):
    """Both paths must decide 'allowed' the same way."""

    def test_the_verdict_parser_is_a_function(self):
        self.assertRegex(uncommented(), r"(?m)^\s*best_verdicts\(\) \{")

    def test_the_tally_is_a_function(self):
        self.assertRegex(uncommented(), r"(?m)^\s*tally\(\) \{")

    def test_both_paths_call_the_same_two_helpers(self):
        # Two copies of "allowed if any ARN allowed it" is how the batch path
        # and the retry path drift into disagreeing about the same policy.
        src = uncommented()
        self.assertEqual(
            2, len(re.findall(r'tally "\$scope" < <\(printf', src)),
            "expected exactly two call sites -- the batch path and the retry "
            "path -- both going through tally/best_verdicts",
        )

    def test_the_parser_prefers_allowed_over_a_deny(self):
        # The rule the whole count rests on. The API returns one result per
        # action-and-ARN pair, so a single allowed among denials is an allow.
        src = source()
        self.assertIn('if d == "allowed" or not best[a]:', src)


class TestTheStepStillRefusesRealDenials(unittest.TestCase):
    """The fallback must not have turned the gate off."""

    def test_it_still_dies_when_actions_are_denied(self):
        self.assertRegex(
            uncommented(),
            r'die "\$\{failed\} of \$\(\(allowed \+ failed\)\) action\(s\) are still not allowed',
            "the verify step must still stop the run on a real denial -- a "
            "half-built environment is the failure it exists to prevent",
        )

    def test_a_per_action_retry_that_also_fails_counts_as_one(self):
        # Not as zero. If the retry itself errors, that action is unverified,
        # and unverified is not allowed.
        #
        # SCOPED TO THE LOOP'S OWN TEXT, not to a character count. The first
        # version took src[i:i+1200] of the uncommented source, which runs past
        # the loop and into simulate_one's increment -- so it passed with the
        # retry's own increment deleted. Found by mutation, which is the only
        # way a window that is merely too wide ever shows up.
        self.assertRegex(
            _retry_block(), r"failed=\$\(\(failed \+ 1\)\)",
            "an action whose own simulate call fails must count as a failure",
        )


if __name__ == "__main__":
    unittest.main()
