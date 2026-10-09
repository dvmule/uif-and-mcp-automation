# test_uif_env_gates.py
#
# Pins the env gates in the deploy scripts: every `-uif` stack must be admitted
# wherever one is, and the accepted-env list must be the same set in the two
# places that hold it.
#
# WHY A TEST. Echo's deploy path grew around ONE uif stack, so `dev-uif`
# appeared as a literal in nine places. Adding uat-uif meant finding all nine,
# and the misses were silent in three different ways:
#
#   1. A hard stop with a misleading message. uif_echo_validate_env and the
#      Makefile's ENVS held the same list independently and disagreed, so
#      `uif-echo-infra-deploy.sh uat-uif` died at preflight talking about ENV
#      while `make plan ENV=uat-uif` died later talking about ENVS.
#   2. A helper silently not running. uif_echo_import_phase5_artifacts was
#      parameterised to accept any -uif env, but its only caller still gated on
#      dev-uif -- so the fix was invisible and the first uat apply would have
#      failed BucketAlreadyOwnedByYou on a bucket the bootstrap had just made.
#      A callee that handles a case its caller never passes it is not fixed,
#      and nothing about the code looks wrong.
#   3. A wrong path taken successfully. uif_echo_sync_glue_scripts returned
#      early for dev-uif because a uif stack's scripts are release artifacts;
#      uat-uif fell through and would have synced Glue scripts into
#      uif-echo-uat-data/jobs/ -- the state bucket -- and reported success.
#
# The catch-all case below is the one that matters most. It fails on ANY new
# `== "dev-uif"` equality test in the deploy scripts, so the next person who
# adds one has to come here and say why it is dev-only rather than discovering
# the omission from a broken apply.
#
# STAGING IS DELIBERATELY DEV-ONLY, and asserted as such. Build identity is
# established once in dev; every environment downstream runs the same bytes,
# proven by an unchanged digest. Staging into uat would rebuild from whatever
# tree is checked out and leave the release tag attesting to bytes nothing
# runs. So uif_echo_stage_release_artifacts must REFUSE a non-dev uif stack --
# not treat it as legacy, which is what it did while gated on `!= dev-uif`.
import os
import re
import unittest


DEPLOY_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "deploy"
)
TF_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "terraform"
)
COMMON = os.path.join(DEPLOY_DIR, "_uif-echo-deploy-common.sh")
MAKEFILE = os.path.join(TF_DIR, "Makefile")

ENVS_EXPECTED = {"dev", "dev-uif", "uat", "uat-uif", "prod"}

# `== "dev-uif"` is legitimate in exactly one place: the staging refusal, which
# is dev-only by design (see the header). Anything else is presumed a miss.
EQUALITY_ALLOWED = {
    "_uif-echo-deploy-common.sh": 1,  # stage_release_artifacts' != "dev-uif"
    # The infra script's staging CALL, added by UIFPE-608 so a plan is never
    # produced without the hashes. It carries the same dev-only justification
    # as the refusal it calls, for the reason in this file's header: every
    # other uif env is promoted, and stage_release_artifacts dies rather than
    # rebuild there. An unconditional call would break every promoted
    # environment's infra deploy at its first step, so *-uif would be wrong
    # here -- this is the one gate that really is about dev.
    "uif-echo-infra-deploy.sh": 1,

    # The promotion-source grant and its verification, added 2026-09-26.
    # Both ask "is this environment the one that BUILDS", which is a question
    # about dev specifically and not about uif-ness. dev is the only
    # environment that builds, so dev is the only one that needs no
    # cross-environment read -- its own prefix scope already covers the source
    # bucket, because for dev the prefix and the source coincide.
    #
    # *-uif would be exactly wrong: dev-uif is a uif stack AND the source, so a
    # *-uif gate would grant dev-uif a read to itself and deny uat the read it
    # actually needs. That is the inverted case worth naming, because every
    # other gate in these scripts goes the other way.
    "bootstrap-environment.sh": 2,
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def deploy_scripts():
    return {
        n: read(os.path.join(DEPLOY_DIR, n))
        for n in sorted(os.listdir(DEPLOY_DIR))
        if n.endswith(".sh")
    }


class TestAcceptedEnvList(unittest.TestCase):
    """The two independent copies of the env list must agree."""

    def test_validate_env_accepts_all_five(self):
        m = re.search(r'\[\[ "\$env" =~ \^\(([^)]+)\)\$ \]\]', read(COMMON))
        self.assertIsNotNone(m, "could not find uif_echo_validate_env's regex")
        self.assertEqual(ENVS_EXPECTED, set(m.group(1).split("|")))

    def test_makefile_envs_accepts_all_five(self):
        m = re.search(r"^ENVS\s*:=\s*(.+)$", read(MAKEFILE), re.M)
        self.assertIsNotNone(m, "could not find ENVS in the Makefile")
        self.assertEqual(ENVS_EXPECTED, set(m.group(1).split()))

    def test_the_two_lists_are_the_same_set(self):
        # The real invariant. Either list alone can be right while the pair is
        # broken, and the pair being broken is what actually happened.
        regex = re.search(r'\[\[ "\$env" =~ \^\(([^)]+)\)\$ \]\]', read(COMMON))
        envs = re.search(r"^ENVS\s*:=\s*(.+)$", read(MAKEFILE), re.M)
        self.assertEqual(
            set(regex.group(1).split("|")), set(envs.group(1).split()),
            "uif_echo_validate_env and the Makefile's ENVS must hold the same "
            "set; they are two hand-maintained copies of one fact",
        )

    def test_a_typo_is_still_rejected(self):
        # An explicit list rather than a `*-uif` pattern, so that `uay-uif`
        # fails here instead of reaching envs/uay-uif.tfvars, where Terraform
        # reports a missing file and says nothing about the env name.
        m = re.search(r'\[\[ "\$env" =~ \^\(([^)]+)\)\$ \]\]', read(COMMON))
        accepted = set(m.group(1).split("|"))
        for typo in ("uay-uif", "uat-uf", "uif-uat", ""):
            self.assertNotIn(typo, accepted)


class TestHelperGates(unittest.TestCase):
    """Helpers and their callers must both admit any -uif stack."""

    def test_import_helpers_accept_any_uif_env(self):
        src = read(COMMON)
        for fn in ("uif_echo_import_phase5_artifacts",
                   "uif_echo_import_phase6_drift"):
            i = src.index(fn + "()")
            body = src[i:i + 900]
            self.assertRegex(
                body, r'\[\[ "\$env" == \*-uif \]\] \|\| return 0',
                "%s must accept every -uif env, not just dev-uif" % fn,
            )

    def test_infra_deploy_caller_accepts_any_uif_env(self):
        src = read(os.path.join(DEPLOY_DIR, "uif-echo-infra-deploy.sh"))
        self.assertRegex(
            src, r'\[\[ "\$ENV" == \*-uif && "\$UIF_ECHO_TF_MODE" == "apply" \]\]',
            "the caller must gate on *-uif; gating on dev-uif makes the "
            "helpers' own parameterisation unreachable for uat-uif",
        )

    def test_glue_script_sync_skips_any_uif_env(self):
        src = read(COMMON)
        i = src.index("uif_echo_sync_glue_scripts()")
        body = src[i:i + 1400]
        self.assertRegex(
            body, r'\[\[ "\$\{ENV:-\}" == \*-uif \]\]',
            "a uif stack's scripts are release artifacts; falling through "
            "syncs them into that stack's state bucket and reports success",
        )

    def test_layer_prefix_uses_code_for_any_uif_env(self):
        src = read(COMMON)
        self.assertRegex(
            src,
            r'\[\[ "\$\{ENV:-\}" == \*-uif \]\] && layer_prefix="code/lambda-layers"',
            "every uif stack uses the code/ prefix; only legacy stacks keep "
            "the bare one",
        )

    def test_ui_deploy_maps_every_uif_env_to_an_app_env(self):
        src = read(os.path.join(DEPLOY_DIR, "uif-echo-ui-deploy.sh"))
        self.assertRegex(src, r"dev\|dev-uif\)\s*APP_ENV=dev")
        self.assertRegex(
            src, r"uat\|uat-uif\)\s*APP_ENV=uat",
            "uat-uif is a uat stack, as dev-uif is a dev stack; unmapped it "
            "dies after the UI has already been built",
        )


class TestStagingIsDevOnly(unittest.TestCase):
    """Promotion, not staging, is how a downstream uif stack gets artifacts."""

    def test_staging_refuses_a_non_dev_uif_stack(self):
        src = read(COMMON)
        i = src.index("uif_echo_stage_release_artifacts()")
        body = src[i:i + 2600]
        self.assertIn(
            "takes its artifacts by promotion, not by staging", body,
            "a non-dev uif stack must be refused loudly. Returning 0 leaves "
            "artifacts.auto.tfvars.json unwritten, so every key becomes "
            "ARTIFACT-SHA-NOT-SUPPLIED and the apply fails at Lambda with "
            "NoSuchKey",
        )

    def test_staging_does_not_call_a_uif_stack_legacy(self):
        src = read(COMMON)
        i = src.index("uif_echo_stage_release_artifacts()")
        body = src[i:i + 2600]
        # The legacy branch must name the legacy envs explicitly rather than
        # catching everything that is not dev-uif.
        self.assertRegex(
            body, r"case \"\$\{ENV:-\}\" in\s*\n\s*dev\|uat\|prod\)",
            "the legacy branch must list dev|uat|prod; `!= dev-uif` told "
            "uat-uif it was a legacy stack and then did nothing",
        )


class TestNoStrayEqualityGates(unittest.TestCase):
    """The catch-all: any new `== \"dev-uif\"` has to be justified here."""

    def test_dev_uif_equality_gates_are_accounted_for(self):
        found = {}
        for name, body in deploy_scripts().items():
            hits = []
            for n, line in enumerate(body.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                # `=`, `==` and `!=`, all of them. An earlier version of this
                # matched only (==|!=) and so missed POSIX `[ "$x" = "dev-uif" ]`
                # entirely -- which is the MOST common form in these scripts.
                # Two real gates went uncounted until a mutation test showed a
                # third could be added without failing anything.
                #
                # The space after the operator is what separates a comparison
                # from an assignment: `[ "$x" = "dev-uif" ]` has one,
                # `VAR="dev-uif"` does not.
                if re.search(r'(?:==|!=|=) +"dev-uif"', s):
                    hits.append(n)
            if hits:
                found[name] = hits
        for name, hits in found.items():
            self.assertLessEqual(
                len(hits), EQUALITY_ALLOWED.get(name, 0),
                "unexpected `dev-uif` equality gate in %s at line(s) %s. If it "
                "is genuinely dev-only, say why and raise its count in "
                "EQUALITY_ALLOWED; otherwise it should be *-uif." % (name, hits),
            )


if __name__ == "__main__":
    unittest.main()
