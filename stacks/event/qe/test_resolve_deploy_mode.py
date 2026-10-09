"""scripts/resolve-deploy-mode.sh -- the latch promote-event.yml resolves first.

Fails CLOSED: an unset or unrecognised value resolves to the default (skip),
and matching is on the whole token, so "no-run" is never read as "run".
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "resolve-deploy-mode.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not on PATH")


def resolve(event, input_, var, allowed="skip,run", default="skip"):
    r = subprocess.run([BASH, str(SCRIPT), "--event", event, "--input", input_,
                        "--var", var, "--allowed", allowed, "--default", default],
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


@pytest.mark.parametrize("event,input_,var,want", [
    ("workflow_dispatch", "run", "", "run"),     # dispatch input wins
    ("workflow_dispatch", "", "run", "skip"),    # ...and an empty input is not the variable
    ("push", "run", "", "skip"),                 # a push reads the variable only
    ("push", "", "run", "run"),
    ("push", "", "", "skip"),                    # unset -> closed
    ("push", "", "no-run", "skip"),              # whole-token match, not substring
    ("push", "", "RUN", "skip"),
])
def test_fails_closed(event, input_, var, want):
    assert resolve(event, input_, var) == (0, want)


def test_a_default_outside_the_allowed_set_is_refused():
    code, _ = resolve("push", "", "", default="apply")
    assert code != 0


def test_promote_event_calls_the_script_that_exists():
    wf = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "promote-event.yml").read_text(encoding="utf-8")
    assert "resolver=stacks/event/scripts/resolve-deploy-mode.sh" in wf
    assert SCRIPT.is_file()
