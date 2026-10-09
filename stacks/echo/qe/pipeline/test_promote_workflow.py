# test_promote_workflow.py
#
# Guards promote-echo.yml and the address list it refuses plans against.
#
# WHY. A promote's one job is to change WHICH VERSION runs and nothing else, and
# the only thing enforcing that is assert-app-only-plan.sh being handed the
# right addresses. Two ways that goes wrong, neither visible anywhere else:
#
#   too few addresses   the promote REFUSES a legitimate release. Fail-closed,
#                       but the cause is invisible weeks after the Lambda that
#                       caused it was added -- which is why the list is derived
#                       rather than written down
#   too many addresses  the promote APPLIES something outside the application
#                       layer, which is the whole thing this file exists to stop
#
# The derivation is only valid while every resource a promote can meet has a
# PLAIN address. One does use count -- aws_glue_job.phase0_iceberg_gcp_probe,
# `count = var.environment == "dev" && !local.is_uif_stack ? 1 : 0` -- and it
# is harmless precisely because that gate makes it absent from every
# environment a promote targets. A counted resource WITHOUT such a gate would
# have the address name[0] while the script prints name, the exact match would
# fail, and the promote would refuse every plan touching it: fail-closed, but
# invisible in the error. That is the invariant the test below pins.
import os
import re
import subprocess
import unittest

import yaml


HERE = os.path.dirname(__file__)
STACK = os.path.join(HERE, "..", "..")
REPO = os.path.join(STACK, "..", "..")
WF = os.path.join(REPO, ".github", "workflows", "promote-echo.yml")
CONVERGE_WF = os.path.join(REPO, ".github", "workflows", "deploy-echo-infra.yml")
SCRIPT = os.path.join(STACK, "scripts", "app-only-addresses.sh")

ROOTS = {
    "main": os.path.join(STACK, "infrastructure", "terraform"),
    "gold": os.path.join(STACK, "infrastructure", "terraform-gold"),
}

# The types whose plan diff on a new release is their artifact pointer and
# nothing else. Everything else is infrastructure.
APP_TYPES = ("aws_lambda_function", "aws_lambda_layer_version", "aws_glue_job")

# Tripwires. Not the authoritative check -- the set comparison below is -- but
# a number nobody can change by accident.
EXPECTED = {"main": 20, "gold": 13}


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _uncommented(path):
    """The file with comment lines removed.

    This file's own comments quote the strings it forbids, so a whole-text grep
    matches the explanation. test_copy_artifacts.py has the same helper for the
    same reason.
    """
    return "\n".join(
        line for line in _text(path).split("\n") if not line.lstrip().startswith("#")
    )


def _declared(root):
    """{address: body} for every application-layer resource a root declares."""
    out = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".tf"):
            continue
        src = _text(os.path.join(root, name))
        for t in APP_TYPES:
            for m in re.finditer(
                r'resource\s+"(%s)"\s+"(\w+)"\s*\{(.*?)\n\}' % t, src, re.S
            ):
                out[f"{m.group(1)}.{m.group(2)}"] = m.group(3)
    return out


def _derived(root):
    r = subprocess.run(["bash", SCRIPT, root], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"app-only-addresses.sh failed on {root}: {r.stderr}")
    return set(r.stdout.split())


class TestTheAddressListIsDerivedCorrectly(unittest.TestCase):
    def test_the_parser_sees_what_we_think_it_does(self):
        # A regex that stopped matching would make every assertion below pass
        # against two empty sets.
        for label, root in ROOTS.items():
            with self.subTest(root=label):
                self.assertEqual(
                    len(_declared(root)), EXPECTED[label],
                    f"parsed {len(_declared(root))} application-layer resources in "
                    f"the {label} root, expected {EXPECTED[label]}",
                )

    def test_the_script_and_the_terraform_agree_both_ways(self):
        for label, root in ROOTS.items():
            with self.subTest(root=label):
                declared = set(_declared(root))
                derived = _derived(root)
                self.assertEqual(
                    derived, declared,
                    f"the {label} root: only-in-script={sorted(derived - declared)}, "
                    f"only-in-terraform={sorted(declared - derived)}. A missing "
                    f"address makes the promote refuse a legitimate release; an "
                    f"extra one lets it apply something it should not.",
                )

    def test_every_listed_resource_really_carries_an_artifact_pointer(self):
        # The justification for allowing a type at all. If a type's resources
        # reference no artifact pointer, it is infrastructure and does not
        # belong in a promote's plan.
        pointers = ("s3_key", "script_location", "source_code_hash", "extra-py-files")
        for label, root in ROOTS.items():
            for addr, body in sorted(_declared(root).items()):
                with self.subTest(root=label, address=addr):
                    self.assertTrue(
                        any(p in body for p in pointers),
                        f"{addr} references none of {pointers}, so a promote has no "
                        f"business changing it",
                    )

    def test_anything_counted_cannot_exist_where_a_promote_runs(self):
        # THE INVARIANT THE DERIVATION RESTS ON, and it is not "nothing uses
        # count". One resource does: aws_glue_job.phase0_iceberg_gcp_probe is
        # `count = var.environment == "dev" && !local.is_uif_stack ? 1 : 0`, so
        # in every environment a promote targets the count is 0, the resource
        # does not exist, and no address for it can appear in a plan. Naming it
        # without an index is harmless.
        #
        # A counted resource WITHOUT that gate is the real problem: its address
        # would be name[0], the script would keep printing name, and the exact
        # match would fail -- so the promote refuses every plan touching it.
        # Fail-closed, but invisible in the error, which is why this is pinned.
        for label, root in ROOTS.items():
            for addr, body in sorted(_declared(root).items()):
                m = re.search(r"^\s+(count|for_each)\s*=\s*(.+)$", body, re.M)
                if m is None:
                    continue
                with self.subTest(root=label, address=addr):
                    self.assertEqual(
                        m.group(1), "count",
                        f"{addr} uses for_each, whose addresses carry string keys "
                        f"the script cannot derive at all",
                    )
                    self.assertIn(
                        "!local.is_uif_stack", m.group(2),
                        f"{addr} is counted but not gated off for a uif stack, so "
                        f"it CAN exist where a promote runs and its real address "
                        f"is {addr}[0]. app-only-addresses.sh prints {addr}, the "
                        f"exact match fails, and the promote refuses every plan "
                        f"touching it. Teach the script to expand indices, or "
                        f"gate the resource.",
                    )

    def test_a_root_with_none_is_an_error_not_an_empty_list(self):
        r = subprocess.run(["bash", SCRIPT, os.path.join(STACK, "scripts")],
                           capture_output=True, text=True)
        self.assertNotEqual(
            r.returncode, 0,
            "an empty allow-list refuses every plan, which reads like a plan "
            "problem rather than a missing argument",
        )


class TestThePromoteWorkflow(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(_text(WF))
        self.live = _uncommented(WF)
        self.steps = self.doc["jobs"]["promote"]["steps"]

    def _index(self, predicate, label):
        for i, s in enumerate(self.steps):
            if predicate(s):
                return i
        raise AssertionError(f"no step matches {label}")

    def test_the_mode_defaults_to_skip_and_the_job_is_skipped_on_it(self):
        mode = [s for s in self.doc["jobs"]["resolve"]["steps"] if s.get("id") == "mode"]
        self.assertEqual(len(mode), 1)
        self.assertIn('--default "skip"', mode[0]["run"])
        self.assertIn("promote == 'run'", self.doc["jobs"]["promote"]["if"].replace('"', "'"))

    def test_it_shares_the_converge_s_concurrency_group(self):
        mine = self.doc["jobs"]["promote"]["concurrency"]
        theirs = yaml.safe_load(_text(CONVERGE_WF))["jobs"]["converge"]["concurrency"]
        self.assertEqual(
            mine["group"], theirs["group"],
            "a promote and an infra apply must never interleave on one state file",
        )
        self.assertEqual(mine["cancel-in-progress"], False)

    def test_the_addresses_are_derived_not_written_down(self):
        self.assertIn("app-only-addresses.sh", self.live)
        for t in APP_TYPES:
            self.assertNotIn(
                f"{t}.", self.live,
                f"a literal {t} address in the workflow is a list that goes stale; "
                f"it must come from app-only-addresses.sh",
            )

    def test_both_roots_are_asserted_app_only_before_being_applied(self):
        for root, blast in (("terraform", "blast_main"), ("terraform-gold", "blast_gold")):
            wd = f"stacks/echo/infrastructure/{root}"
            assert_i = self._index(
                lambda s, w=wd, b=blast: s.get("id") == b and s.get("working-directory") == w,
                f"the app-only assert for {root}",
            )
            apply_i = self._index(
                lambda s, w=wd: "terraform apply" in (s.get("run") or "")
                and s.get("working-directory") == w,
                f"the apply for {root}",
            )
            with self.subTest(root=root):
                self.assertLess(assert_i, apply_i)
                self.assertIn(
                    f"steps.{blast}.outputs.apply == 'true'",
                    self.steps[apply_i].get("if", ""),
                    "the apply must be conditional on the assert, or an empty plan "
                    "(exit 3) still applies",
                )

    def test_the_plan_is_piped_and_never_written_anywhere(self):
        self.assertIn("terraform show -json tf.plan |", self.live,
                      "a plan carries generated values in clear: only its resource "
                      "addresses may be printed, and only by being piped")
        self.assertNotIn("upload-artifact", self.live)
        for step in self.steps:
            run = step.get("run") or ""
            self.assertNotIn("-auto-approve", run)
            self.assertNotRegex(run, r"show -json[^|]*>", )

    def test_the_release_tag_comes_after_smoke_and_only_on_success(self):
        smoke = self._index(lambda s: s.get("name") == "Smoke", "the smoke step")
        tag = self._index(lambda s: "next-release-tag.sh" in (s.get("run") or ""),
                          "the release-tag step")
        self.assertLess(smoke, tag,
                        "cutting the tag before smoke would make it a record of an "
                        "intention rather than evidence")
        self.assertEqual(self.steps[tag].get("if"), "success()")

    def test_the_smoke_step_runs_the_suite_and_cannot_be_bypassed(self):
        # REWRITTEN AS ITS OWN PREDECESSOR ASKED. This case used to assert that
        # qe/integration did NOT exist -- Echo had no tier-2 suite, so the
        # promote had to stop rather than tag anyway -- and it carried the
        # instruction to "become an assertion that the suite runs" once the
        # directory appeared. UIFPE-555 added it, so here that is.
        #
        # A test that fails the moment its premise stops holding, and says what
        # it should become, is worth more than one that quietly keeps passing
        # against a stale assumption. This is the second such handoff in this
        # file and both worked.
        smoke = self.steps[self._index(lambda s: s.get("name") == "Smoke", "smoke")]
        run = smoke["run"]

        self.assertTrue(
            os.path.isdir(os.path.join(STACK, "qe", "integration")),
            "qe/integration has gone away; the promote can no longer certify "
            "anything and this case should go back to asserting the step fails",
        )
        self.assertRegex(
            run, r'pytest "\$SMOKE_DIR"',
            "the step must actually run the suite, not merely check it is there",
        )
        # The absent-directory branch stays: if the suite is missing from a
        # checkout -- a build tag cut before it landed, say -- this must stop
        # rather than tag on nothing.
        self.assertIn("exit 1", run)
        self.assertNotIn("continue-on-error", str(smoke))
        # And a skip must not read as a pass. See
        # qe/pipeline/test_smoke_suite_fails_closed.py, which proves the
        # behaviour rather than the spelling.
        self.assertIn("UIF_ECHO_SMOKE_REQUIRED", str(smoke.get("env", {})))

    def test_only_the_promote_job_can_write_to_the_repository(self):
        self.assertEqual(self.doc["jobs"]["resolve"].get("permissions"), None)
        self.assertEqual(self.doc["jobs"]["promote"]["permissions"]["contents"], "write")
        self.assertEqual(self.doc["permissions"]["contents"], "read")

    def test_the_role_secrets_are_named_literally(self):
        self.assertNotIn("secrets[", self.live)
        self.assertIn("secrets.AWS_ROLE_ARN_ECHO_UAT", self.live)
        self.assertIn("secrets.AWS_ROLE_ARN_ECHO_PROD", self.live)

    def test_the_terraform_pin_agrees_with_the_converge(self):
        mine = re.search(r'TF_VERSION:\s*"([^"]+)"', _text(WF))
        theirs = re.search(r'TF_VERSION:\s*"([^"]+)"', _text(CONVERGE_WF))
        self.assertIsNotNone(mine)
        self.assertIsNotNone(theirs)
        self.assertEqual(mine.group(1), theirs.group(1),
                         "the two workflows apply against one state, and a plan file "
                         "is only valid for the version that wrote it")


if __name__ == "__main__":
    unittest.main()
