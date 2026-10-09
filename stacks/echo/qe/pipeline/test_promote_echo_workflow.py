# test_promote_echo_workflow.py
#
# Invariants for Echo's promote workflow, .github/workflows/promote-echo.yml.
#
# WHY THIS FILE LOOKS LIKE A REWRITE. It is. An earlier version of this file
# pinned a promote workflow written under UIFPE-555, asserting its exact shape:
# its mode vocabulary, its step wording, the layout of its app-only assertion.
# UIFPE-576 landed a different and more complete promote workflow on develop at
# the same time -- multi-environment, with a gold root as well as the main one --
# and that is the file that survived the merge. Assertions about the other
# implementation's shape went with it.
#
# What is left is deliberately implementation-agnostic: five properties that
# should hold for Echo's promote workflow no matter who writes it or how. A test
# that pins one author's step names is a test that fails the next refactor and
# teaches nothing; these five each describe a way the workflow could be wrong
# that no reviewer would notice by reading the diff.
#
# WHAT THIS CANNOT CATCH. Text matching against YAML proves the workflow's
# source says something, never that a run behaves that way. The promote workflow
# has never been executed -- it needs AWS OIDC, a real build tag and an
# environment approval -- so nothing here is a substitute for its first real
# dispatch.
import os
import re
import unittest


WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    ".github", "workflows", "promote-echo.yml",
)


def source():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    """Source with whole-line comments dropped.

    Every property below is also explained in a comment beside the code that
    implements it, so a naive substring search would pass on the explanation
    alone. Assertions about what the workflow DOES run against this.
    """
    return "\n".join(
        line for line in source().splitlines()
        if not line.strip().startswith("#")
    )


class TestNoInlineUiSync(unittest.TestCase):
    """The highest-value case in this file."""

    def test_no_inline_aws_s3_sync(self):
        # uif-echo-ui-deploy.sh publishes the UI with
        # `aws s3 sync --delete --exclude "m/*"`. The UI bucket also hosts
        # beacon's and transform's apps under m/*, and that exclude is the only
        # thing stopping --delete from removing another team's site. A
        # --delete sync already destroyed 166 MB of layer zips in this repo on
        # 2026-09-12.
        #
        # So the sync belongs in that one script, where the exclude lives beside
        # it. Reimplementing it in workflow YAML puts the exclude one forgotten
        # line away from deleting beacon and transform.
        self.assertNotIn(
            "aws s3 sync", uncommented(),
            "the UI sync must stay in uif-echo-ui-deploy.sh, where the "
            '--exclude "m/*" that protects beacon and transform lives',
        )


class TestTheUiBundleIsPromotedNotRebuilt(unittest.TestCase):
    """The UI must be published from the bundle the build tag names.

    WHY IT MATTERS MORE THAN IT LOOKS. `npm run build` is not byte-stable across
    runners, so rebuilding in the target environment produces a bundle whose
    hash no release tag names -- and digest equality between what dev built and
    what uat runs is the entire evidence the promotion model offers. A rebuilt
    UI leaves the tag making a claim about bytes nobody can check.

    It was genuinely not promoted until UIFPE-555, and the workflow said so in a
    comment that deferred to UIFPE-509 -- "the two UI build paths disagreeing
    about VITE_ECHO_ENV ... wants settling first". That prerequisite had already
    been met: UIFPE-515 deleted VITE_ECHO_ENV and VITE_API_URL, so the origin
    arrives at runtime in config.json and one bundle serves every environment.
    The deferral outlived its reason, which is its own small lesson -- a comment
    naming a blocker should name the ticket that clears it.
    """

    def _publish_step(self):
        """The publish step's body, so a match elsewhere cannot satisfy these."""
        src = uncommented()
        i = src.index("Publish the promoted UI bundle")
        j = src.index("- name:", i + 10)
        return src[i:j]

    def test_the_step_exists(self):
        self.assertIn(
            "Publish the promoted UI bundle", uncommented(),
            "the promote must publish the UI, or the release tag cannot claim it",
        )

    def test_it_sets_the_bundle_variable_rather_than_building(self):
        step = self._publish_step()
        self.assertIn(
            "UIF_ECHO_UI_BUNDLE=", step,
            "without that variable uif-echo-ui-deploy.sh runs npm ci && npm run "
            "build, which is the thing this step exists to avoid",
        )
        self.assertNotIn("npm run build", step)
        self.assertNotIn("npm ci", step)

    def test_it_verifies_before_publishing(self):
        # restore-artifacts.sh re-hashes the extracted tree with config.json
        # excluded and populates --dest only on success. Publishing a directory
        # it did not vouch for would put unverified bytes behind a tag that
        # says they were verified.
        step = self._publish_step()
        self.assertRegex(
            step, r"restore-artifacts\.sh[\s\\]+--class ui_bundle",
            "the bundle must go through restore-artifacts.sh, not straight from "
            "the tarball to the publish",
        )
        self.assertIn("--sha256", step)
        self.assertLess(
            step.index("restore-artifacts.sh"), step.index("UIF_ECHO_UI_BUNDLE="),
            "verification must precede the publish, not follow it",
        )

    def test_it_reads_this_environments_bucket_not_devs(self):
        # The copy step has already put the bundle in this environment's bucket
        # at the version the manifest names. Reading dev's would prove dev's
        # bytes are intact and say nothing about what this environment serves.
        step = self._publish_step()
        self.assertIn('uif-echo-${ENV_NAME}-release-artifacts', step)
        self.assertNotIn("uif-echo-dev-release-artifacts", step)

    def test_it_passes_the_stem_not_the_bare_environment(self):
        # uif-echo-ui-deploy.sh inits Terraform for the env it is handed, so
        # `uat` reads the LEGACY echo-uat state and would sync this bundle into
        # echo-uat's UI bucket -- publishing the new stack's UI over the live
        # one. `uat-uif` is this stack. The two names differing by a suffix is
        # exactly the mistake that is invisible in review.
        step = self._publish_step()
        self.assertRegex(
            step, r'uif-echo-ui-deploy\.sh "\$STEM"',
            "pass $STEM; $ENV_NAME is the GitHub environment and resolves to "
            "the legacy stack's Terraform state",
        )

    def test_it_runs_before_the_smoke(self):
        src = uncommented()
        self.assertLess(
            src.index("Publish the promoted UI bundle"),
            src.index("- name: Smoke"),
            "the smoke suite must exercise what was promoted, not whatever the "
            "runner last built",
        )

    def test_the_release_tag_records_the_bundle_sha(self):
        src = uncommented()
        self.assertIn("ui_bundle: %s", src)
        self.assertIn("${UI_BUNDLE_SHA}", src)
        self.assertNotIn(
            "ui_bundle: NOT promoted", src,
            "the tag said NOT promoted while the UI was rebuilt; now that it is "
            "promoted the tag must carry the sha instead",
        )

    def test_the_sha_is_exported_by_the_publish_step(self):
        # A tag claiming a sha that no step set would record an empty string,
        # which reads as "promoted" to anyone skimming.
        self.assertRegex(
            self._publish_step(), r'UI_BUNDLE_SHA=\$\{?sha\}?" >> "\$GITHUB_ENV"',
            "the publish step must export the sha the tag step interpolates",
        )


class TestASkipCannotPassForAPass(unittest.TestCase):
    """The smoke step must set UIF_ECHO_SMOKE_REQUIRED.

    THE WHOLE SAFETY DESIGN RESTS ON THIS ONE VARIABLE. qe/integration discovers
    its target from terraform outputs and the tag's manifest, and skips at
    fixture level when it cannot -- deliberately, so `pytest stacks/echo/qe` on
    a laptop does not fail on an environment that is not there.

    pytest exits 0 on a skip. So without this variable, a discovery failure in
    CI -- expired credentials, an uninitialised root, a renamed output -- leaves
    the smoke step GREEN and `Cut the release tag` runs. The tag would then
    certify a promotion that nothing verified, which is the single property the
    rest of this model is built on.

    With it set, qe/integration/conftest.py raises instead, the step fails, and
    no tag is cut. Losing the variable is silent in review and invisible in a
    green run, which is why it is asserted here rather than trusted.
    """

    def _smoke_step(self):
        src = uncommented()
        i = src.index("- name: Smoke")
        j = src.index("- name:", i + 10)
        return src[i:j]

    def test_the_smoke_step_requires_the_suite_to_run(self):
        self.assertIn(
            "UIF_ECHO_SMOKE_REQUIRED", self._smoke_step(),
            "without this, a skipped smoke suite exits 0 and a release tag is "
            "cut on no evidence",
        )

    def test_it_is_set_to_a_truthy_value(self):
        # conftest checks presence, not value, so anything non-empty works --
        # but an empty string would read as set and be falsy.
        self.assertRegex(
            self._smoke_step(), r'UIF_ECHO_SMOKE_REQUIRED:\s*"?[^"\s]+"?',
            "an empty value is indistinguishable from unset to os.environ.get",
        )

    def test_pytest_is_installed_before_the_smoke_step(self):
        # THE FIRST PROMOTE EVER DISPATCHED DIED HERE. The smoke step has run
        # `python -m pytest` since this workflow was written and nothing
        # installed pytest -- latent, because the step exited at the
        # absent-directory guard before reaching it. The moment qe/integration
        # existed:
        #
        #   /usr/bin/python: No module named pytest
        #
        # after the copy, both app-only assertions and the UI publish had all
        # succeeded (run 36345696040). A workflow that shells out to an
        # interpreter it never provisioned is a gap no reviewer sees, because
        # the line reads perfectly well.
        src = uncommented()
        for needle in ("actions/setup-python", "pip install"):
            self.assertIn(
                needle, src,
                "the promote runs `python -m pytest`; %r must appear before it"
                % needle,
            )
        self.assertLess(
            src.index("actions/setup-python"), src.index("- name: Smoke"),
            "python must be set up BEFORE the smoke step, not after",
        )
        self.assertLess(
            src.index("pip install"), src.index("- name: Smoke"),
            "pytest must be installed BEFORE the smoke step",
        )

    def test_the_python_version_matches_the_release_build(self):
        # The suite asserts against artifacts release-echo.yml built. Two
        # different interpreters is not a fault today -- qe/integration imports
        # only the standard library -- but the day it imports something from
        # src/ it would be, and the mismatch would be invisible until then.
        release = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "..",
            ".github", "workflows", "release-echo.yml",
        )
        with open(release, encoding="utf-8") as fh:
            want = re.search(r'python-version:\s*"([\d.]+)"', fh.read())
        self.assertIsNotNone(want, "release-echo.yml pins no python version")
        got = re.search(r'python-version:\s*"([\d.]+)"', uncommented())
        self.assertIsNotNone(got, "promote-echo.yml pins no python version")
        self.assertEqual(
            want.group(1), got.group(1),
            "the promote's python should match the release build's",
        )

    def test_the_suite_the_step_runs_is_the_one_that_honours_it(self):
        # A mismatch between SMOKE_DIR and where the guard lives would leave
        # the variable set and nothing reading it.
        self.assertIn("SMOKE_DIR: stacks/echo/qe/integration", uncommented())
        conftest = os.path.join(
            os.path.dirname(__file__), "..", "integration", "conftest.py"
        )
        self.assertTrue(
            os.path.isfile(conftest),
            "promote-echo.yml points SMOKE_DIR at qe/integration; that "
            "directory must hold the conftest that honours the variable",
        )
        with open(conftest, encoding="utf-8") as fh:
            self.assertIn("UIF_ECHO_SMOKE_REQUIRED", fh.read())


class TestRoleSecret(unittest.TestCase):
    """Three stacks' secrets share one GitHub Environment."""

    def test_reads_echos_own_stack_qualified_secret(self):
        self.assertRegex(
            uncommented(), r"secrets\.AWS_ROLE_ARN_ECHO_[A-Z]+",
            "the promote must assume Echo's own deploy role",
        )

    def test_never_reads_another_stacks_role_secret(self):
        # AWS_ROLE_ARN_UAT exists at repository level and belongs to the
        # product domain (deploy-product-uat.yml reads it). Bare AWS_ROLE_ARN is
        # apicurio's, environment-scoped on the same shared `uat` environment.
        # An environment-scoped secret shadows a repository-level one of the
        # same name, so the wrong name here assumes the wrong role -- and the
        # two roles overlap PARTIALLY, so the failure arrives mid-promote as an
        # implicitDeny naming a bucket rather than an identity.
        src = uncommented()
        self.assertNotRegex(
            src, r"secrets\.AWS_ROLE_ARN_(UAT|PROD)\b",
            "AWS_ROLE_ARN_UAT/_PROD are the product domain's",
        )
        self.assertNotRegex(
            src, r"secrets\.AWS_ROLE_ARN\s*\}",
            "bare AWS_ROLE_ARN is apicurio's",
        )


class TestEnvironmentIsDeclared(unittest.TestCase):
    def test_the_deploying_job_declares_an_environment(self):
        # Without `environment:` the GitHub Environment's required reviewer and
        # its tag policy silently do not apply -- and the run still succeeds.
        # The value may be literal or an expression; what matters is that one
        # is declared on a job that assumes a role.
        # (?m) is load-bearing: assertRegex uses re.search with no flags, so a
        # bare ^ anchors to the start of the whole file and this passes or
        # fails for the wrong reason. The same omission made an assertion in
        # test_mandatory_tags.py pass vacuously earlier in this ticket.
        src = uncommented()
        self.assertRegex(
            src, r"(?m)^\s{4}environment:\s*\S",
            "a job that promotes must name a GitHub Environment, or the "
            "reviewer and the tag policy are decoration",
        )


class TestEmptyPlanIsNotAFailure(unittest.TestCase):
    def test_exit_three_is_handled(self):
        # assert-app-only-plan.sh returns 0 (app-only, apply it), 3 (the plan
        # is empty -- its own header calls that NOT a failure) and 1 (it touched
        # something else). Exit 3 is the expected outcome of an environment's
        # first promote, where a converge already applied the artifacts. A
        # workflow treating any non-zero exit as failure breaks every first run,
        # and breaks it in the way that looks most like a real defect.
        src = uncommented()
        self.assertIn("assert-app-only-plan.sh", src)
        self.assertRegex(
            src, r"(^|\s)3\)",
            "exit 3 needs its own branch; it means nothing to apply, not a "
            "failure",
        )


class TestSmokePrecedesTheTag(unittest.TestCase):
    def test_the_release_tag_is_cut_after_the_smoke(self):
        # A release/<env>/* tag is the evidence a promote was checked. Cutting
        # it before the smoke makes it a record of an intention instead.
        src = uncommented()
        smoke = [m.start() for m in re.finditer(r"[Ss]moke", src)]
        tag = [m.start() for m in re.finditer(r"git tag -a", src)]
        self.assertTrue(smoke, "expected a smoke step")
        self.assertTrue(tag, "expected the release tag to be cut")
        self.assertLess(
            min(smoke), min(tag),
            "the smoke must run before the release tag is cut",
        )


if __name__ == "__main__":
    unittest.main()
