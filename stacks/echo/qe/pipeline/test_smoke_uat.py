# test_smoke_uat.py
#
# Pins what the uat smoke actually checks.
#
# WHY THESE THREE. Echo has no integration suite (qe/ci.sh:38 records the tier
# as deliberately absent) and no health route, and the API is behind auth. So
# this is the minimal set that distinguishes "the stack exists" from "this
# promotion landed":
#
#   1. CloudFront serves the UI          -> something is published
#   2. API Gateway answers 200/401/403   -> route, integration and Lambda wired
#   3. a Glue job's script_location
#      contains the promoted sha         -> THIS promotion landed
#
# Assertion 3 is the one that earns its place. Without it a promote could copy
# nothing at all and still go green, because 1 and 2 pass against any converged
# stack.
#
# 200, 401 AND 403 ARE ALL A PASS, and that is not laxness. This assertion
# checks REACHABILITY, not enforcement: any response in that set proves the
# route, the integration and the Lambda all exist. Whether the authorizer
# actually enforces is a separate concern -- `enable_jwt_authorizer` is `false`
# in every environment today, so a 200 is the expected, correct answer, not a
# gap. A 404 means the route is missing, a 5xx means the Lambda is broken and
# an unreachable host is 000, so those fail.
import os
import re
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "smoke-uat.sh")


def source():
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


class TestRequiredInputs(unittest.TestCase):
    def test_every_input_is_a_required_flag(self):
        src = uncommented()
        for flag in ("--api-url", "--ui-url", "--glue-job", "--expect-sha"):
            self.assertIn(flag, src)
        self.assertEqual(
            4, len(re.findall(r"is required", src)),
            "all four inputs must be required; a defaulted url silently "
            "smoke-tests the wrong environment",
        )

    def test_it_reads_no_terraform_output(self):
        self.assertNotIn(
            "uif_echo_tf_out", uncommented(),
            "inputs are passed in so this runs from a laptop and cannot "
            "resolve a different environment than the caller meant",
        )


class TestAssertions(unittest.TestCase):
    def test_ui_must_return_200_and_a_body(self):
        src = uncommented()
        self.assertRegex(src, r'\[ "\$ui_code" = "200" \]')
        self.assertIn("ui_bytes", src)

    def test_api_200_401_or_403_passes(self):
        # This is the CRITICAL-1 fix: all five envs' tfvars set
        # enable_jwt_authorizer = false, so an unauthenticated GET /pipelines
        # returns 200 on every real environment. A smoke that failed 200
        # could never pass a real run.
        src = uncommented()
        self.assertRegex(
            src, r"200\|401\|403",
            "200, 401 and 403 must all be accepted passes: any of the three "
            "proves the route, integration and Lambda are reachable",
        )

    def test_404_5xx_and_unreachable_still_fail(self):
        src = uncommented()
        self.assertIn('404) die', src)
        self.assertIn('000) die', src)
        self.assertRegex(src, r'5\*\)\s*die')

    def test_the_2xx_fails_arm_is_gone(self):
        # The bug this pins: a `2*) die ...` arm made every 2xx (including the
        # 200 every environment actually returns) a hard failure, so the
        # smoke could never pass a real run. That arm must not come back.
        src = uncommented()
        self.assertNotRegex(
            src, r'2\*\)\s*die',
            "a 2xx-fails arm makes the smoke unpassable, because every "
            "environment's tfvars sets enable_jwt_authorizer = false",
        )
        self.assertNotIn("is not enforcing", src)

    def test_glue_script_location_is_checked_against_the_promoted_sha(self):
        src = uncommented()
        self.assertIn("glue get-job", src)
        self.assertIn("EXPECT_SHA", src)

    def test_the_sha_check_is_not_a_substring_of_nothing(self):
        # An empty EXPECT_SHA makes `case $loc in *""*)` match everything.
        self.assertIn("64 lowercase hex", uncommented())

    def test_curl_failure_does_not_double_write_the_http_code(self):
        # curl already prints its own %{http_code} via -w -- "000" on a
        # connect failure or timeout -- and STILL exits non-zero in that
        # case. `curl ... -w '%{http_code}' ... || echo 000` therefore runs
        # `echo 000` in ADDITION to curl's own output, with no newline
        # between them, so the captured code becomes the garbled "000000"
        # rather than "000". This is a presence/absence check on that exact
        # shape; it catches the pattern coming back verbatim (e.g. via a
        # careless revert) but it cannot catch every rewrite that
        # reintroduces the same bug with different spelling -- it does not
        # execute the script or a real curl failure, so a logically
        # equivalent but differently-worded double-write would slip past
        # it. The unreachable-host run in the smoke's own verification
        # report is what actually exercises the failure path.
        src = uncommented()
        self.assertNotRegex(
            src, r"curl[^\n]*\|\|\s*echo\s+000",
            "curl already writes its own http_code (000) via -w on a "
            "connect/timeout failure and still exits non-zero, so "
            "`|| echo 000` on the same line appends a second 000 with no "
            "separating newline, producing a garbled code like '000000'",
        )


if __name__ == "__main__":
    unittest.main()
