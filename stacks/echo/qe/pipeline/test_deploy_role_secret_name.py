# test_deploy_role_secret_name.py
#
# Pins which GitHub secret holds Echo's deploy role ARN, and the scope it is set
# at. Two rules: uat and prod are stack-qualified (AWS_ROLE_ARN_ECHO_<ENV>), and
# every write is --env, never repository-wide.
#
# WHY A TEST. The wrong name here works flawlessly for Echo and breaks somebody
# else, which is the failure mode no amount of testing Echo can catch.
#
# The `uat` GitHub Environment is shared. Three secrets on it name three
# different roles owned by three different stacks:
#
#   AWS_ROLE_ARN            apicurio's, environment-scoped (ADR-0012's promotion)
#   AWS_ROLE_ARN_UAT        product's, REPOSITORY-scoped, read by
#                           deploy-product-uat.yml under `environment: uat`
#   AWS_ROLE_ARN_ECHO_UAT   Echo's, environment-scoped -- what this test pins
#
# GitHub resolves an environment-scoped secret ahead of a repository-scoped one
# of the same name. So scoping `AWS_ROLE_ARN_UAT` to `uat` -- which an earlier
# bootstrap-environment.sh did, deriving the name as AWS_ROLE_ARN_${ENV_UPPER}
# -- silently pointed product's uat deploy at Echo's role. Nothing broke only
# because deploy-product-uat.yml is absent from the default branch and
# workflow_dispatch cannot reach it there. That reprieve ends when develop
# merges.
#
# And the collision is the worst shape to diagnose. The two roles overlap
# PARTIALLY: `glue:UpdateJob` on uif-echo-uat-bronze-ingest comes back allowed
# while `s3:PutObject` on the same prefix is an implicitDeny, so the run dies
# mid-promote with an error naming a bucket rather than an identity.
#
# DEV IS DELIBERATELY NOT QUALIFIED, and that asymmetry is asserted rather than
# tolerated. dev's role is not Echo's: `echo-dev-github-actions` is a shared
# enterprise role that eleven workflows across apicurio, beacon, event, price,
# product, program and stock assume from AWS_ROLE_ARN_DEV. Echo borrows it.
# Renaming a secret eight other stacks read is not this stack's call, so a test
# that demanded symmetry would be demanding the wrong thing.
#
# The last case guards a file that does not exist yet. promote-echo.yml is
# Phase 5 of the uat rollout, it is the first Echo workflow to authenticate
# against uat, and reaching for `secrets.AWS_ROLE_ARN_UAT` there is the natural
# mistake -- it is the name the rollout plan carried until 2026-09-24.
import os
import re
import unittest


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
BOOTSTRAP = os.path.join(
    REPO_ROOT, "stacks", "echo", "infrastructure", "deploy",
    "bootstrap-environment.sh",
)
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")

# Secrets on the shared environments that belong to another stack. An Echo
# workflow reading any of these is either hijacking or being hijacked.
FOREIGN_SECRETS = ("AWS_ROLE_ARN_UAT", "AWS_ROLE_ARN_PROD")


def bootstrap_source():
    with open(BOOTSTRAP, encoding="utf-8") as fh:
        return fh.read()


def echo_workflows():
    """Workflow files that deploy or release Echo, including gold."""
    out = {}
    for name in sorted(os.listdir(WORKFLOW_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        if "echo" not in name:
            continue
        with open(os.path.join(WORKFLOW_DIR, name), encoding="utf-8") as fh:
            out[name] = fh.read()
    return out


class TestSecretNameDerivation(unittest.TestCase):
    """The name bootstrap-environment.sh writes."""

    def test_non_dev_is_stack_qualified(self):
        src = bootstrap_source()
        self.assertRegex(
            src,
            r'SECRET_NAME="AWS_ROLE_ARN_ECHO_\$\{ENV_UPPER\}"',
            "the non-dev branch must derive a stack-qualified name; an "
            "unqualified AWS_ROLE_ARN_<ENV> scoped to a shared environment "
            "shadows another stack's secret",
        )

    def test_unqualified_form_is_gone(self):
        src = bootstrap_source()
        # The exact assignment the script used until 2026-09-24. Matching the
        # assignment and not the bare name, because the comments explain the
        # hazard and must keep naming it.
        self.assertNotRegex(
            src,
            r'SECRET_NAME="AWS_ROLE_ARN_\$\{ENV_UPPER\}"',
            "this derivation produces AWS_ROLE_ARN_UAT, which is product's",
        )

    def test_dev_keeps_the_shared_name(self):
        src = bootstrap_source()
        self.assertRegex(
            src,
            r'dev\)\s+SECRET_NAME="AWS_ROLE_ARN_DEV"',
            "dev must keep the shared enterprise name -- Echo borrows "
            "echo-dev-github-actions and does not own it",
        )


class TestSecretScope(unittest.TestCase):
    """Repository-wide writes are the whole hazard; there must be none."""

    def test_every_secret_write_is_environment_scoped(self):
        src = bootstrap_source()
        offenders = []
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not re.search(r"gh secret (set|delete)", stripped):
                continue
            if "--env" not in stripped:
                offenders.append(stripped)
        self.assertEqual(
            [], offenders,
            "every gh secret set/delete must pass --env. A repository-scoped "
            "write is readable on any ref, which undoes the environment's "
            "reviewer; a repository-scoped DELETE would remove product's "
            "AWS_ROLE_ARN_UAT outright",
        )

    def test_rollback_removes_both_names(self):
        src = bootstrap_source()
        self.assertIn(
            'rollback_secrets+=("AWS_ROLE_ARN_${ENV_UPPER}")', src,
            "rollback must also remove the unqualified name this script used "
            "previously, or it leaves the one piece of state that affects "
            "another team",
        )

    def test_stale_shadow_is_warned_about(self):
        src = bootstrap_source()
        self.assertRegex(
            src,
            r'grep -q "\^AWS_ROLE_ARN_\$\{ENV_UPPER\}',
            "the script must detect an unqualified secret left scoped to this "
            "environment; it is invisible from the workflow that suffers for it",
        )


class TestEchoWorkflows(unittest.TestCase):
    """No Echo workflow may read another stack's role secret."""

    def test_workflows_were_found(self):
        # Guards the glob. A typo in "echo" would make every case below pass
        # against an empty set.
        self.assertGreaterEqual(
            len(echo_workflows()), 2,
            "expected at least deploy-uif-echo-dev.yml and release-echo.yml",
        )

    def test_no_echo_workflow_reads_a_foreign_role_secret(self):
        offenders = []
        for name, body in echo_workflows().items():
            for secret in FOREIGN_SECRETS:
                # \b so AWS_ROLE_ARN_UAT does not match AWS_ROLE_ARN_ECHO_UAT.
                if re.search(r"secrets\.%s\b" % secret, body):
                    offenders.append("%s reads %s" % (name, secret))
        self.assertEqual(
            [], offenders,
            "AWS_ROLE_ARN_UAT is product's and AWS_ROLE_ARN_PROD is not "
            "Echo's; Echo's uat and prod roles are AWS_ROLE_ARN_ECHO_<ENV>",
        )

    def test_no_echo_workflow_reads_the_unsuffixed_secret(self):
        offenders = []
        for name, body in echo_workflows().items():
            if re.search(r"secrets\.AWS_ROLE_ARN\s*\}", body):
                offenders.append(name)
        self.assertEqual(
            [], offenders,
            "the unsuffixed AWS_ROLE_ARN is apicurio's environment-scoped "
            "secret on uat; an Echo workflow reading it would assume "
            "apicurio's deploy role",
        )


if __name__ == "__main__":
    unittest.main()
