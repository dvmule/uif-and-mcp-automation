"""Unit tests for scripts/resolve-deploy-mode.sh.

Both deployment workflows ask the same question -- what does this run do? --
and the dangerous answer is a value nobody listed. A latch that passes an
unrecognised token through hands it to a step that may act on it, so every
unknown value must collapse to the default. The default itself is the safe
one: the environment-changing latches default to skip.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "resolve-deploy-mode.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash on PATH")

ALLOWED = "skip,plan,apply,bootstrap"


def run(*args):
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True
    )


def resolve(event, input_value, var_value, allowed=ALLOWED, default="skip"):
    return run(
        "--event", event,
        "--input", input_value,
        "--var", var_value,
        "--allowed", allowed,
        "--default", default,
    )


def test_dispatch_prefers_the_input_over_the_variable():
    r = resolve("workflow_dispatch", "apply", "plan")
    assert r.returncode == 0
    assert r.stdout.strip() == "apply"


def test_push_ignores_the_input_and_uses_the_variable():
    # A dispatch input is empty on a push event, but even if a stale value
    # arrived it must not decide a push run.
    r = resolve("push", "apply", "plan")
    assert r.returncode == 0
    assert r.stdout.strip() == "plan"


def test_empty_input_on_dispatch_falls_back_to_the_default():
    r = resolve("workflow_dispatch", "", "apply")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_empty_variable_on_push_falls_back_to_the_default():
    r = resolve("push", "", "")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_unrecognised_value_collapses_to_the_default_not_itself():
    r = resolve("push", "", "APPLY")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"
    assert "APPLY" in r.stderr


def test_a_value_that_merely_contains_an_allowed_token_is_rejected():
    # Substring matching would accept "no-apply" as "apply".
    r = resolve("push", "", "no-apply")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_a_non_default_default_is_honoured():
    # The smoke latch defaults ON, unlike the environment-changing latches.
    r = resolve("push", "", "", allowed="run,skip", default="run")
    assert r.returncode == 0
    assert r.stdout.strip() == "run"


def test_default_outside_allowed_is_a_malformed_invocation():
    r = resolve("push", "", "", allowed="run,skip", default="apply")
    assert r.returncode == 1
    assert "not one of" in r.stderr


def test_unknown_flag_is_refused_rather_than_ignored():
    r = run("--event", "push", "--mode", "apply")
    assert r.returncode == 1
    assert "unknown" in r.stderr.lower()


def test_a_flag_with_no_value_is_refused():
    r = run("--event")
    assert r.returncode == 1


def test_no_arguments_prints_usage_and_fails():
    r = run()
    assert r.returncode == 1
    # Assert on the stderr message, not merely the word "Usage:" -- every
    # comment line in the script starts with "# Usage:" and would satisfy a
    # looser check even if the script had stopped printing anything.
    assert r.stderr.strip().startswith("resolve-deploy-mode.sh:")
