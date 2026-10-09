# test_infra_converge_workflow.py
#
# Guards the ORDER and the SAFETY LATCHES in deploy-echo-infra.yml.
#
# WHY A TEST FOR A WORKFLOW. Nothing else can see these. actionlint checks that
# the YAML is a valid workflow, not that the steps are in an order that works,
# and the only other way to find out is to run a converge against a real
# environment -- which is the one thing you cannot do cheaply. Every assertion
# below corresponds to a failure that has already happened somewhere in this
# repo, or to one the Terraform itself refuses at plan time:
#
#   order    the gold root LOOKS UP uif-echo-{env}-glue-role with
#            data.aws_iam_role, and a data source resolving to nothing fails the
#            PLAN -- so a first converge with gold before the main root cannot
#            produce a plan at all
#   copy     terraform_data.artifact_shas_present refuses to plan when the
#            artifact keys carry the missing-hash sentinel, and its own note
#            says a promoted environment gets them from the promoted digests,
#            never from staging (UIFPE-608)
#   stem     the bare envs/{env}.tfvars belongs to the retiring echo-{env}-*
#            stack; naming is the only isolation between the two stacks in one
#            account, and UIFPE-355 already produced that failure once
#   latch    an unset ECHO_<ENV>_INFRA must resolve to `skip`, because naming a
#            GitHub Environment that does not exist creates it with NO reviewer
#            and NO tag policy
#   plan     plan and apply in one job, no -auto-approve, and the plan file
#            never uploaded -- a saved plan carries every generated value
import os
import re
import unittest

import yaml


HERE = os.path.dirname(__file__)
REPO = os.path.join(HERE, "..", "..", "..", "..")
WF = os.path.join(REPO, ".github", "workflows", "deploy-echo-infra.yml")
APICURIO_WF = os.path.join(REPO, ".github", "workflows", "deploy-apicurio-infra.yml")

MAIN_ROOT = "stacks/echo/infrastructure/terraform"
GOLD_ROOT = "stacks/echo/infrastructure/terraform-gold"


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load(path):
    return yaml.safe_load(_text(path))


def _uncommented(path):
    """The file with comment lines removed.

    A forbidden string is forbidden as an INVOCATION, and this file explains at
    length why it does not use some of them -- so a whole-text grep matches the
    explanation and fails. test_copy_artifacts.py has the same helper for the
    same reason: "grepping the whole file cannot tell an argument from an
    argument somebody commented out".
    """
    return "\n".join(
        line for line in _text(path).split("\n")
        if not line.lstrip().startswith("#")
    )


def _steps(doc, job):
    return doc["jobs"][job]["steps"]


def _index_of(steps, predicate, label):
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matches {label}")


def _run(step):
    return step.get("run", "") or ""


class TestTheWorkflowIsWellFormed(unittest.TestCase):
    def setUp(self):
        self.doc = _load(WF)
        self.text = _text(WF)

    def test_it_has_the_two_jobs_and_both_triggers(self):
        self.assertEqual(sorted(self.doc["jobs"]), ["converge", "resolve"])
        # `on` parses as the boolean True in YAML 1.1, which is why this reads
        # oddly. Both spellings are checked so a PyYAML change cannot make the
        # assertion vacuous.
        on = self.doc.get("on", self.doc.get(True))
        self.assertIn("push", on)
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(on["push"]["tags"], ["echo/v*"])

    def test_only_actions_already_used_elsewhere(self):
        # The enterprise Actions policy rejects anything else, and referencing a
        # rejected action fails the ENTIRE workflow at startup with no job logs.
        mine = {s["uses"] for j in self.doc["jobs"].values() for s in j["steps"] if "uses" in s}
        theirs = set(re.findall(r"uses:\s*(\S+)", _text(APICURIO_WF)))
        self.assertTrue(
            mine <= theirs,
            f"these actions are not already used by deploy-apicurio-infra.yml, "
            f"so their policy standing is unproven: {sorted(mine - theirs)}",
        )


class TestTheSafetyLatch(unittest.TestCase):
    """An unset repository variable must mean this workflow does nothing."""

    def setUp(self):
        self.doc = _load(WF)
        self.text = _text(WF)

    def test_the_mode_defaults_to_skip(self):
        mode = [s for s in _steps(self.doc, "resolve") if s.get("id") == "mode"]
        self.assertEqual(len(mode), 1, "no step with id: mode")
        run = _run(mode[0])
        self.assertIn('--default "skip"', run,
                      "the infra mode must default to skip, or a tag push on an "
                      "unbootstrapped environment applies something")

    def test_the_converge_job_is_skipped_on_skip(self):
        cond = self.doc["jobs"]["converge"]["if"]
        self.assertIn("infra != 'skip'", cond.replace('"', "'"),
                      "the converge job must not run when the mode is skip -- that "
                      "is what keeps its `environment:` reference unevaluated, and "
                      "an unevaluated reference is what stops GitHub creating an "
                      "unprotected environment")

    def test_the_environment_is_gated_and_serialised(self):
        job = self.doc["jobs"]["converge"]
        self.assertIn("environment", job, "the converge job must be environment-gated")
        self.assertEqual(job["concurrency"]["cancel-in-progress"], False,
                         "a cancelled apply can leave the state lock held")
        self.assertIn("echo-deploy-", job["concurrency"]["group"],
                      "the group must be the one promote-echo.yml also uses, so an "
                      "infra apply and a promote cannot interleave on one state")


class TestTheOrderOfOperations(unittest.TestCase):
    def setUp(self):
        self.steps = _steps(_load(WF), "converge")

    def _first(self, needle):
        return _index_of(self.steps, lambda s: needle in _run(s)
                         or needle in (s.get("working-directory") or ""), needle)

    def test_the_artifacts_are_copied_before_the_first_plan(self):
        copy = self._first("copy-artifacts.sh")
        plan = _index_of(self.steps, lambda s: "terraform plan" in _run(s), "terraform plan")
        self.assertLess(
            copy, plan,
            "a promoted environment has no staging step to fall back on: the "
            "hashes must come from the digests of what was actually copied, or "
            "the artifact guard refuses the plan",
        )

    def test_nothing_stages_from_source(self):
        joined = "\n".join(_run(s) for s in self.steps)
        self.assertNotIn(
            "stage-artifacts.sh", joined,
            "staging from source in a promoted environment would make the release "
            "tag attest to bytes nothing is running (UIFPE-608's note)",
        )

    def test_the_main_root_is_applied_before_the_gold_root_is_planned(self):
        main_apply = _index_of(
            self.steps,
            lambda s: "terraform apply" in _run(s) and (s.get("working-directory") == MAIN_ROOT),
            "apply of the main root",
        )
        gold_init = _index_of(
            self.steps,
            lambda s: "terraform init" in _run(s) and (s.get("working-directory") == GOLD_ROOT),
            "init of the gold root",
        )
        self.assertLess(
            main_apply, gold_init,
            "the gold root looks up the shared Glue role the main root creates, "
            "and a data source that resolves to nothing fails the PLAN",
        )

    def test_both_roots_files_are_checked_before_anything_is_applied(self):
        check = _index_of(
            self.steps,
            lambda s: "backends/${STEM}.hcl" in _run(s) and "envs/${STEM}.tfvars" in _run(s),
            "the preflight that both roots carry this environment's files",
        )
        first_apply = _index_of(
            self.steps, lambda s: "terraform apply" in _run(s), "the first apply"
        )
        self.assertLess(
            check, first_apply,
            "half a converge is worse than none: the main root applies, the gold "
            "root then fails at init on a backend nobody wrote, and the "
            "environment is left describing two different releases",
        )


class TestThePlanDiscipline(unittest.TestCase):
    def setUp(self):
        self.doc = _load(WF)
        self.text = _text(WF)
        self.steps = _steps(self.doc, "converge")

    def test_plan_and_apply_share_one_job(self):
        jobs_with_plan = {
            name for name, job in self.doc["jobs"].items()
            if any("terraform plan" in _run(s) for s in job["steps"])
        }
        jobs_with_apply = {
            name for name, job in self.doc["jobs"].items()
            if any("terraform apply" in _run(s) for s in job["steps"])
        }
        self.assertEqual(
            jobs_with_plan, jobs_with_apply,
            "a saved plan is only valid for the Terraform version that wrote it "
            "and must not leave the runner, so the two belong in one job",
        )

    def test_the_plan_file_never_leaves_the_runner(self):
        live = _uncommented(WF)
        self.assertNotIn("upload-artifact", live,
                         "a saved plan carries every generated value; grep finds "
                         "nothing in it only because it is compressed")
        self.assertNotIn("terraform show -json", live)

    def test_no_auto_approve(self):
        for step in self.steps:
            self.assertNotIn("-auto-approve", _run(step),
                             "not a valid pairing with a plan file, and not needed: "
                             "Terraform does not prompt when handed one")

    def test_every_root_is_addressed_by_the_uif_stem(self):
        # The bare envs/<env>.tfvars is the LEGACY stack's. A step may name the
        # stem directly, or a backend var built from it -- the two roots use
        # different backend filename conventions (.hcl and .s3.tfbackend), so the
        # path cannot be one literal.
        allowed = ("${STEM}", "${MAIN_BACKEND}", "${GOLD_BACKEND}")
        for step in self.steps:
            run = _run(step)
            if "terraform plan" in run or "terraform init" in run:
                with self.subTest(step=step.get("name", run.strip()[:40])):
                    self.assertTrue(
                        any(a in run for a in allowed),
                        f"this step addresses a root without the -uif stem, so it "
                        f"would converge the retiring echo-<env>-* stack: "
                        f"{run.strip()[:90]}",
                    )

    def test_the_backend_paths_are_built_from_the_stem(self):
        # What keeps the assertion above honest: if these stopped being derived
        # from the stem, every init could name the legacy stack while the test
        # above still passed.
        env = self.doc["jobs"]["converge"]["env"]
        for key, suffix in (("MAIN_BACKEND", ".hcl"),
                            ("GOLD_BACKEND", ".s3.tfbackend")):
            with self.subTest(var=key):
                value = env[key]
                self.assertIn("needs.resolve.outputs.stem", value)
                self.assertTrue(value.endswith(suffix), f"{key} is {value}")

    def test_the_terraform_version_is_pinned_and_agrees_with_apicurio(self):
        mine = re.search(r'TF_VERSION:\s*"([^"]+)"', self.text)
        self.assertIsNotNone(mine, "the Terraform version must be pinned")
        theirs = re.search(r'terraform_version:\s*"([^"]+)"', _text(APICURIO_WF))
        self.assertIsNotNone(theirs)
        self.assertEqual(
            mine.group(1), theirs.group(1),
            "a plan file is only valid for the version that wrote it, so this pin "
            "must not drift from the workflow that applies alongside it",
        )

    def test_the_role_secrets_are_named_literally(self):
        # bootstrap-environment.sh chose AWS_ROLE_ARN_ECHO_<ENV> so the file
        # answers "whose role is this" by itself. Indexing secrets[] by a
        # computed name defeats that; an && / || ternary is worse, because an
        # empty secret is falsy and prod would fall through to uat's role.
        live = _uncommented(WF)
        self.assertNotIn("secrets[", live)
        self.assertIn("secrets.AWS_ROLE_ARN_ECHO_UAT", live)
        self.assertIn("secrets.AWS_ROLE_ARN_ECHO_PROD", live)

    def test_the_assumed_identity_is_asserted(self):
        self.assertTrue(
            any("get-caller-identity" in _run(s) for s in self.steps),
            "an absent secret does not fail configure-aws-credentials in a way "
            "that names the cause, and a wrong role fails later on a permission "
            "that reads like a policy gap",
        )


if __name__ == "__main__":
    unittest.main()
