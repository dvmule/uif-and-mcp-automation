# test_artifact_guard.py
# Under a uif stack nothing runs the code in this repository: every Lambda,
# layer and Glue script reads it from a release-artifact key named after a
# content hash, and only the deploy scripts' staging step supplies those hashes.
# Skip staging and every key resolves to a sentinel path no object is at --
# applying that deletes the layer versions and repoints the Glue jobs at a
# missing script, which broke dev twice on 2026-09-24.
#
# Two things stop it, and this file pins both (UIFPE-608):
#   1. artifact_guard.tf fails the PLAN when any key still carries the sentinel.
#   2. uif-echo-infra-deploy.sh stages before it plans, as the apps script does.
#
# Text assertions, not `terraform plan` -- plan needs a backend and credentials.
import unittest
from pathlib import Path

ECHO = Path(__file__).resolve().parents[2]
TF = ECHO / "infrastructure" / "terraform"
DEPLOY = ECHO / "infrastructure" / "deploy"

SENTINEL = "ARTIFACT-SHA-NOT-SUPPLIED"
STAGE = "uif_echo_stage_release_artifacts"


def _read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path):
    """The script with whole-line comments dropped.

    These scripts explain themselves at length and name the staging function in
    that prose, so a substring or index assertion over the raw text is satisfied
    by the comment and keeps passing after the CALL is deleted or moved. Both of
    those mutations slipped through on the first run of this file's known-bad
    battery. Assert on what executes.
    """
    return "\n".join(
        line for line in _read(path).splitlines()
        if not line.lstrip().startswith("#")
    )


class ScriptOrder(unittest.TestCase):
    """Ordering assertions, via an index that refuses to be -1.

    str.find returns -1 for a line that is not there, and -1 is less than every
    real index -- so a plain `assertLess(find(a), find(b))` passes when a has
    been DELETED. That is the exact regression this file exists to catch, so
    the lookup asserts presence first.
    """

    def index(self, text, needle, where):
        i = text.find(needle)
        self.assertNotEqual(i, -1, f"not found in {where}: {needle!r}")
        return i

    def assertComesBefore(self, text, first, second, where):
        self.assertLess(
            self.index(text, first, where),
            self.index(text, second, where),
            f"{first!r} must come before {second!r} in {where}",
        )


class TheSentinelIsDefinedOnce(unittest.TestCase):
    LOCALS = _read(TF / "locals.tf")

    def test_one_definition(self):
        self.assertEqual(self.LOCALS.count(f'sha_sentinel = "{SENTINEL}"'), 1)

    def test_no_lookup_hardcodes_it(self):
        # A hardcoded default is a key the guard cannot see: artifacts_missing_sha
        # compares against local.sha_sentinel, so a literal that drifts from it
        # would pass the guard and still point at nothing.
        for line in self.LOCALS.splitlines():
            if "lookup(var.artifact_shas" in line:
                with self.subTest(line=line.strip()):
                    self.assertIn("local.sha_sentinel", line)
                    self.assertNotIn(SENTINEL, line)

    def test_all_three_key_maps_use_it(self):
        for local_name in ("glue_script_uri", "lambda_s3_key", "layer_s3_key"):
            with self.subTest(local=local_name):
                self.assertRegex(
                    self.LOCALS,
                    local_name + r"\s*=\s*\{(?:.|\n)*?local\.sha_sentinel",
                )


class TheOffenderListIsDerived(unittest.TestCase):
    LOCALS = _read(TF / "locals.tf")

    def test_declared(self):
        self.assertIn("artifacts_missing_sha = local.is_uif_stack ?", self.LOCALS)

    def test_reads_every_key_map(self):
        # Derived from the maps rather than a hand-kept list, so a new Lambda,
        # layer or Glue script is covered the day it is added. A list that named
        # today's artifacts would silently stop covering tomorrow's.
        block = self.LOCALS.split("artifacts_missing_sha = ")[1].split("\n  }")[0]
        for local_name in ("local.glue_script_uri", "local.lambda_s3_key", "local.layer_s3_key"):
            with self.subTest(local=local_name):
                self.assertIn(local_name, block)
        self.assertEqual(block.count("strcontains("), 3)

    def test_empty_for_the_legacy_stacks(self):
        # echo-dev / echo-uat / eds-prod build at plan time and have no release
        # artifacts, so `artifact_shas = {}` is correct there and the guard must
        # stay inert -- otherwise this fix breaks the three live stacks.
        self.assertRegex(self.LOCALS, r"artifacts_missing_sha = local\.is_uif_stack \?(?:.|\n)*?\)\) : \[\]")


class ThePlanRefusesAMissingHash(unittest.TestCase):
    GUARD = _read(TF / "artifact_guard.tf")

    def test_resource_present(self):
        self.assertIn('resource "terraform_data" "artifact_shas_present"', self.GUARD)

    def test_only_on_a_uif_stack(self):
        self.assertIn("count = local.is_uif_stack ? 1 : 0", self.GUARD)

    def test_it_is_a_precondition_so_it_fails_the_plan(self):
        # A precondition is evaluated during plan, before anything is changed.
        # A check block or a validation would report and carry on.
        self.assertRegex(
            self.GUARD,
            r"lifecycle\s*\{\s*precondition\s*\{\s*condition\s*=\s*"
            r"length\(local\.artifacts_missing_sha\)\s*==\s*0",
        )

    def test_the_message_names_the_offenders_and_both_fixes(self):
        # The hashes arrive two ways -- staged from source in dev-uif, promoted
        # everywhere else -- and the wrong remedy in a promoted env is worse
        # than none, so the message has to name both.
        message = self.GUARD.split("error_message")[1]
        self.assertIn("local.artifacts_missing_sha)}", message)   # the list itself
        self.assertIn("Nothing has been changed", message)
        self.assertIn("uif-echo-infra-deploy.sh", message)
        self.assertIn("artifacts.auto.tfvars.json", message)


class TheInfraScriptStagesBeforeItPlans(ScriptOrder):
    CODE = _code(DEPLOY / "uif-echo-infra-deploy.sh")
    WHERE = "uif-echo-infra-deploy.sh (comments stripped)"
    GATED = f'if [[ "$ENV" == "dev-uif" ]]; then\n  {STAGE}\nfi'

    def test_it_stages_at_all(self):
        self.assertIn(self.GATED, self.CODE)

    def test_it_stages_before_the_plan(self):
        # These hashes are plan INPUTS. Staging after the plan -- which is where
        # the Glue sync used to live -- produces a plan of something nobody
        # intends to apply.
        self.assertComesBefore(self.CODE, STAGE, 'plan ENV="$ENV"', self.WHERE)

    def test_staging_is_not_gated_on_apply(self):
        # The one line that matters most. Gated on apply, `infra=plan` still
        # reports every artifact as changing and the plan cannot be trusted;
        # and the import preflight below it IS apply-only, so it is easy to
        # tuck this into that block by mistake.
        self.assertComesBefore(
            self.CODE, STAGE,
            'if [[ "$ENV" == *-uif && "$UIF_ECHO_TF_MODE" == "apply" ]]', self.WHERE,
        )

    def test_it_stages_for_dev_uif_only(self):
        # Every uif env except dev-uif takes its artifacts by PROMOTION, and
        # uif_echo_stage_release_artifacts dies rather than build there --
        # staging into uat would produce different hashes for the same release
        # tag (UIFPE-555). So an UNGATED call here would not merely be untidy,
        # it would break every promoted env's infra deploy at the first step.
        self.assertIn(self.GATED, self.CODE)
        self.assertNotIn(f"\n{STAGE}\n", self.CODE)   # i.e. never at column 0
        self.assertEqual(self.CODE.count(STAGE), 1)


class BothDeployPathsAgree(ScriptOrder):
    # infra and apps run the same Terraform root, so whichever one runs has to
    # supply the hashes. The workflow declares `apps: needs: [..., infra]`, so
    # apps runs AFTER infra and is skipped when infra fails -- which is why
    # leaving staging to apps alone meant infra could never repair what it broke.
    def test_the_apps_script_still_stages_before_its_plan(self):
        apps = _code(DEPLOY / "uif-echo-apps-deploy.sh")
        self.assertComesBefore(
            apps, f"\n{STAGE}\n", 'plan ENV="$ENV"',
            "uif-echo-apps-deploy.sh (comments stripped)")

    def test_apps_runs_after_infra_in_the_dev_workflow(self):
        workflow = _read(ECHO.parents[1] / ".github" / "workflows" / "deploy-uif-echo-dev.yml")
        apps_job = workflow.split("\n  apps:\n")[1].split("\n  ui:\n")[0]
        self.assertIn("needs: [resolve, tests, infra]", apps_job)


if __name__ == "__main__":
    unittest.main()
