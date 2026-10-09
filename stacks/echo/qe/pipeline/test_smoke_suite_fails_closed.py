# test_smoke_suite_fails_closed.py
#
# Runs qe/integration as a subprocess to prove it FAILS, not skips, when it was
# required and cannot run.
#
# WHY A BEHAVIOURAL TEST AND NOT A TEXT ASSERTION. There already is a text
# assertion -- test_promote_echo_workflow.py checks that promote-echo.yml sets
# UIF_ECHO_SMOKE_REQUIRED and that qe/integration/conftest.py mentions it. That
# was not enough, and mutation testing is what showed it: replacing the guard's
# condition with `if False:` left the string in the file (in the constant and in
# comments), so every text assertion still passed -- while the suite exited 0
# with everything skipped.
#
# Exit 0 is the whole problem. promote-echo.yml cuts a release tag if and only
# if the smoke step succeeds, and pytest exits 0 on a skip. A guard that has
# stopped working is therefore invisible: the step is green, the tag is cut, and
# the tag's claim -- that smoke passed -- is false. Nothing downstream can tell
# the difference between "verified" and "skipped".
#
# HOW DISCOVERY IS MADE TO FAIL DETERMINISTICALLY. The suite must fail here on
# any machine, including one with working credentials and an initialised
# Terraform root where discovery would legitimately succeed. So this passes an
# override that cannot be valid: UIF_ECHO_SMOKE_EXPECT_SHA that is not 64 hex
# characters. _discover() rejects it, which is itself a guard worth having --
# a truncated or empty sha turns the only assertion that proves anything about
# a promotion into a substring match every script location satisfies.
#
# Both directions are pinned. Required and undiscoverable must be non-zero;
# not required and undiscoverable must be zero, or a laptop run of
# `pytest stacks/echo/qe` would fail on an environment nobody has.
import os
import subprocess
import sys
import unittest


HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SUITE = "stacks/echo/qe/integration"

# Not hex, and not 64 characters. Either alone would be rejected; both together
# make the intent unmistakable to anyone reading a failure.
UNUSABLE_SHA = "not-a-sha"


def _run(extra_env):
    env = dict(os.environ)
    # Cleared so a developer's own exports cannot make this test pass or fail
    # for the wrong reason.
    for key in list(env):
        if key.startswith("UIF_ECHO_SMOKE"):
            del env[key]
    env["UIF_ECHO_SMOKE_EXPECT_SHA"] = UNUSABLE_SHA
    env.update(extra_env)
    return subprocess.run(
        # -rs so skip REASONS print; -q alone hides them, and a
        # reason is what these assertions read.
        [sys.executable, "-m", "pytest", SUITE, "-q", "-rs"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )


class TestTheSmokeSuiteFailsClosed(unittest.TestCase):
    def test_required_and_undiscoverable_is_a_failure(self):
        proc = _run({"UIF_ECHO_SMOKE_REQUIRED": "1"})
        self.assertNotEqual(
            0, proc.returncode,
            "qe/integration exited 0 while UIF_ECHO_SMOKE_REQUIRED was set and "
            "its target was undiscoverable. promote-echo.yml would read that as "
            "green and cut a release tag certifying a promotion nothing "
            "verified.\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (proc.stdout[-1500:], proc.stderr[-1500:]),
        )

    def test_the_failure_names_the_variable(self):
        # So whoever hits it in CI knows why the suite refused rather than
        # thinking the environment is broken.
        proc = _run({"UIF_ECHO_SMOKE_REQUIRED": "1"})
        combined = proc.stdout + proc.stderr
        self.assertIn(
            "UIF_ECHO_SMOKE_REQUIRED", combined,
            "the refusal must say which variable made it fatal",
        )

    def test_not_required_and_undiscoverable_is_a_skip(self):
        # The other direction, and it matters just as much: this suite is part
        # of `pytest stacks/echo/qe`, which runs on machines with no deployed
        # environment at all.
        proc = _run({})
        self.assertEqual(
            0, proc.returncode,
            "without UIF_ECHO_SMOKE_REQUIRED an undiscoverable target must "
            "skip, not fail -- otherwise a local run of the whole Echo suite "
            "breaks on an environment nobody has.\n--- stdout ---\n%s"
            % proc.stdout[-1500:],
        )
        self.assertIn("skipped", proc.stdout)

    def test_an_unusable_sha_is_rejected_on_its_own_merits(self):
        # Independent of the variable: a sha that is not 64 hex characters must
        # never reach the assertion, where `in` would make it a substring match
        # that passes against anything.
        proc = _run({})
        self.assertIn(
            "64 lowercase hex", proc.stdout + proc.stderr,
            "the sha's shape must be checked at discovery, not at the assertion",
        )
