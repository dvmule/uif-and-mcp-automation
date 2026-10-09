"""Does the deployed stack respond, and did THIS promotion land?

Three assertions, scoped deliberately at "responds" rather than "responds
correctly". The distinction matters and is worth stating plainly rather than
leaving a reader to infer it:

  RESPONDS          the surface is reachable and this promotion's bytes are the
                    ones deployed. That is what these tests check.
  RESPONDS CORRECTLY the promoted code behaves. That needs known data in the
                    environment -- a registered pipeline, a gold record to read
                    back -- and uat has no pipeline configs yet; that is Phase 6.

A release tag cut on this suite therefore certifies something narrow and true:
the stack is up and running the artifacts the build tag names. It does NOT
certify that a pipeline produces correct output. Widening the claim without
widening the evidence is the one thing a smoke suite must never do.

WHY ASSERTION 3 CARRIES THE WEIGHT. Tests 1 and 2 pass against any converged
stack, including one where the promote copied nothing at all. Only the Glue
script-location check distinguishes "the environment exists" from "this
promotion happened". If that test is ever removed or weakened, this suite stops
being a promote gate and becomes an uptime check.

NOT COVERED, and do not read a green run as covering it: the Secrets Manager VPC
endpoint security group (rollout plan 4.9). A missing ingress rule leaves all
three assertions here passing and fails GetSecretValue at run time, inside a Glue
job, in an environment nobody is watching.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest



def _get(url, timeout=30):
    """(status, body) for a GET, treating an HTTP error as a real response.

    urllib raises on 4xx/5xx, but a 401 IS the answer we want from the API --
    it proves the route, the integration and the Lambda all exist. Swallowing
    the exception and returning the status keeps the assertion about
    reachability rather than about which status arrived.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "echo-smoke/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        pytest.fail(
            "%s is unreachable (%s). A converged stack answers something; "
            "nothing at all means DNS, CloudFront or the API never came up."
            % (url, exc.reason)
        )


class TestTheUiIsServed:
    def test_cloudfront_returns_a_non_empty_page(self, target):
        status, body = _get(target["ui_url"])
        assert status == 200, (
            "%s returned %d. CloudFront serving a non-200 for the root means "
            "the distribution exists but its origin or default root object is "
            "wrong -- the bucket sync is the first thing to check."
            % (target["ui_url"], status)
        )
        # Presence is not shape, but emptiness is unambiguous: a 200 with no
        # body is a distribution serving an empty bucket, which is what a
        # failed or excluded UI sync looks like from outside.
        assert body.strip(), (
            "%s returned 200 with an empty body, so the distribution is up and "
            "the bucket has nothing in it" % target["ui_url"]
        )


class TestTheApiIsReachable:
    # 200, 401 and 403 all PASS. The assertion's purpose is reachability, not
    # enforcement: any of the three proves the route, the integration and the
    # Lambda exist, because a request had to traverse all of them to get a
    # response. enable_jwt_authorizer is false in every environment's tfvars
    # today, so apigateway.tf resolves authorization_type to "NONE" and an
    # unauthenticated 200 is the correct answer -- failing on it would make
    # this suite unpassable on every environment that exists. The 401/403 arm
    # is kept so this still passes unchanged if the authorizer is switched on.
    REACHABLE = (200, 401, 403)

    def test_api_gateway_answers(self, target):
        url = "%s/pipelines" % target["api_url"]
        status, _ = _get(url)
        assert status in self.REACHABLE, (
            "%s returned %d. 404 means the route is missing, so the API exists "
            "but this deployment did not configure it; 5xx means the Lambda is "
            "there and broken. Neither is a reachable API."
            % (url, status)
        )


class TestThisPromotionLanded:
    """The only assertion that says anything about THIS promotion."""

    def test_the_glue_script_location_carries_the_promoted_sha(self, target, glue_script_location):
        location = glue_script_location(target["glue_job"])
        assert target["expect_sha"] in location, (
            "%s points at %s, which does not contain the sha the build tag "
            "names (%s). The script key IS its content hash, so a mismatch "
            "means the job is running a different script than this promotion "
            "copied -- either the copy did not happen or Terraform applied a "
            "stale artifacts.auto.tfvars.json."
            % (target["glue_job"], location, target["expect_sha"])
        )

    def test_the_location_is_not_the_unsupplied_sentinel(self, target, glue_script_location):
        # artifact_guard.tf is supposed to stop this at plan time, but the
        # guard lives in the main root and a Glue job takes script_location as
        # a plain string -- so a sentinel applies clean and only shows up when
        # a job runs. Checking it here costs nothing and names the cause.
        location = glue_script_location(target["glue_job"])
        assert "ARTIFACT-SHA-NOT-SUPPLIED" not in location, (
            "%s points at the sentinel key, so the apply ran without the "
            "promoted hashes. The job will fail the moment it is dispatched."
            % target["glue_job"]
        )
