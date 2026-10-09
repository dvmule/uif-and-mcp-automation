"""Discovery for Echo's integration tier, and the reason it can fail loudly.

Echo's other five suites (pipeline, control_panel, lambdas, libs, gold) are unit
suites: they run anywhere, need no credentials, and `qe/ci.sh` runs them on every
merge. This one is different in kind. It makes real network calls against a
deployed environment, so it needs AWS credentials, an applied Terraform state and
a build tag to compare against. `qe/ci.sh`'s SUITES list deliberately does not
include it.

THE DANGEROUS FAILURE MODE, and the reason for UIF_ECHO_SMOKE_REQUIRED.

`promote-echo.yml` cuts a release tag if and only if this suite passes, and a
release tag's whole meaning is that smoke passed. A suite that SKIPS when it
cannot find its configuration exits 0. pytest reports "skipped", the promote step
succeeds, and a tag is cut certifying a promotion nothing verified.

So the skip is opt-in, not the default reaction to trouble:

  * UIF_ECHO_SMOKE_REQUIRED set  -> discovery failure is an ERROR. Used by CI.
  * unset                        -> the module skips, so `pytest stacks/echo/qe`
                                    on a laptop does not fail on an environment
                                    that is not there.

Getting that backwards is worse than having no suite: no suite fails visibly at
the `[ ! -d "$SMOKE_DIR" ]` guard, while a vacuously-skipping suite is
indistinguishable from a passing one in the step summary.

WHAT IS DISCOVERED, AND FROM WHERE. Nothing is hardcoded per environment, and
every value has an override so this can be pointed at any stack by hand:

  api_url     terraform output api_endpoint      UIF_ECHO_SMOKE_API_URL
  ui_url      terraform output ui_cloudfront_url UIF_ECHO_SMOKE_UI_URL
  prefix      terraform output ui_bucket_name, minus its `-ui` suffix
  glue_job    "<prefix>-bronze-ingest"           UIF_ECHO_SMOKE_GLUE_JOB
  expect_sha  read-manifest.sh $TAG glue_scripts.bronze_ingest.sha256
                                                 UIF_ECHO_SMOKE_EXPECT_SHA

The prefix comes from a real output rather than being rebuilt from the
environment name, because the two legitimately differ: the tfvars env is
`uat-uif` and the prefix is `uif-echo-uat`. Deriving it by string surgery on the
env name is how a smoke test ends up asserting against the LEGACY `echo-uat`
stack and passing.

Terraform is read through `terraform output`, which needs the root to be
initialised. In the promote job it already is -- the plan and apply steps ran
first. By hand, run `make -C stacks/echo/infrastructure/terraform init ENV=<stem>`
or pass the overrides.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
MAIN_ROOT = REPO_ROOT / "stacks" / "echo" / "infrastructure" / "terraform"
READ_MANIFEST = REPO_ROOT / "stacks" / "echo" / "scripts" / "read-manifest.sh"

REQUIRED_ENV = "UIF_ECHO_SMOKE_REQUIRED"


class Unavailable(Exception):
    """Configuration could not be discovered. Fatal in CI, a skip locally."""


# A TF_LOG-enabled shell prefixes every line with a timestamp and a level, and
# terraform emits hundreds of them before the actual error. Truncating the raw
# stream to a few hundred characters then reports "using
# github.com/hashicorp/hcl" as the reason discovery failed, which is worse than
# saying nothing. Dropped here rather than by unsetting TF_LOG, because the
# operator set it on purpose and this suite has no business overriding it.
_TF_LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.+-]+ \[[A-Z]+\]")


def _diagnostic(proc):
    stream = proc.stderr or proc.stdout or ""
    lines = [
        l for l in stream.splitlines()
        if l.strip() and not _TF_LOG_LINE.match(l.strip())
    ]
    return " / ".join(lines[-4:])[:400] or "(no output)"


def _run(argv, cwd=None):
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise Unavailable(
            "%s exited %d: %s"
            % (" ".join(argv[:3]), proc.returncode, _diagnostic(proc))
        )
    return proc.stdout.strip()


def _tf_output(name):
    if not shutil.which("terraform"):
        raise Unavailable("terraform is not on PATH")
    value = _run(["terraform", "output", "-raw", name], cwd=str(MAIN_ROOT))
    if not value:
        raise Unavailable("terraform output %s is empty" % name)
    return value


def _manifest_field(field):
    tag = os.environ.get("TAG") or os.environ.get("UIF_ECHO_SMOKE_TAG")
    if not tag:
        raise Unavailable(
            "neither TAG nor UIF_ECHO_SMOKE_TAG is set, so there is no build "
            "tag to compare the deployed artifacts against"
        )
    if not shutil.which("bash"):
        raise Unavailable("bash is not on PATH")
    return _run(["bash", str(READ_MANIFEST), tag, field])


def _check_sha(value):
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        # A truncated or empty value turns the only test that proves anything
        # about THIS promotion into a substring match that every script
        # location satisfies.
        raise Unavailable(
            "expected sha is %r, not 64 lowercase hex characters" % value
        )
    return value


def _discover():
    """Resolve every value, preferring an explicit override.

    AN OVERRIDDEN SHA IS VALIDATED FIRST, before anything reaches the network.
    Not just for speed: it makes the rejection independent of whether this
    machine has Terraform state, which is what lets
    qe/pipeline/test_smoke_suite_fails_closed.py assert the guard exists
    anywhere. Validating it last meant that test only worked where discovery
    had already succeeded.
    """
    override_sha = os.environ.get("UIF_ECHO_SMOKE_EXPECT_SHA")
    if override_sha is not None:
        _check_sha(override_sha)

    api_url = os.environ.get("UIF_ECHO_SMOKE_API_URL") or _tf_output("api_endpoint")
    ui_url = os.environ.get("UIF_ECHO_SMOKE_UI_URL") or _tf_output("ui_cloudfront_url")

    glue_job = os.environ.get("UIF_ECHO_SMOKE_GLUE_JOB")
    if not glue_job:
        bucket = _tf_output("ui_bucket_name")
        if not bucket.endswith("-ui"):
            raise Unavailable(
                "ui_bucket_name is %r, which does not end in '-ui', so the "
                "stack prefix cannot be taken from it. Pass "
                "UIF_ECHO_SMOKE_GLUE_JOB explicitly." % bucket
            )
        glue_job = "%s-bronze-ingest" % bucket[: -len("-ui")]

    expect_sha = _check_sha(
        override_sha if override_sha is not None
        else _manifest_field("glue_scripts.bronze_ingest.sha256")
    )

    for name, value in (("api_url", api_url), ("ui_url", ui_url)):
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        if name == "api_url":
            api_url = value
        else:
            ui_url = value

    return {
        "api_url": api_url.rstrip("/"),
        "ui_url": ui_url.rstrip("/"),
        "glue_job": glue_job,
        "expect_sha": expect_sha,
    }


try:
    TARGET = _discover()
    REASON = None
except Unavailable as exc:
    TARGET = None
    REASON = str(exc)


# THE SKIP ITSELF LIVES IN THE TEST MODULE, not here. `pytest.skip(...,
# allow_module_level=True)` is unsupported inside a conftest: conftest is
# imported as a plugin, so raising Skipped from it is a collection ERROR, and a
# local `pytest stacks/echo/qe` exits 1 with a traceback instead of skipping
# quietly. Found by running it. This module computes; the test module decides.
SKIP_MESSAGE = (
    None if TARGET is not None else
    "Echo's integration tier needs a deployed environment: %s. Set %s=1 to "
    "make this an error instead of a skip (CI does)." % (REASON, REQUIRED_ENV)
)

if TARGET is None and os.environ.get(REQUIRED_ENV):
    # Raised from the conftest on purpose: a collection error is non-zero and
    # unmissable, which is exactly what is wanted when the suite was required
    # and could not run. See this module's header -- a skip here would let
    # promote-echo.yml cut a release tag certifying nothing.
    raise RuntimeError(
        "%s is set, so this suite must run and it cannot: %s"
        % (REQUIRED_ENV, REASON)
    )


@pytest.fixture(scope="session")
def target():
    """This environment's discovered endpoints, or a skip.

    THE SKIP IS HERE, on the fixture, and the two other places it could go are
    both wrong. In the conftest at module level, `pytest.skip` is a collection
    ERROR -- conftest is imported as a plugin, so a local run exits 1 with a
    traceback. In the test module, `from conftest import ...` does not resolve:
    the repo's pytest.ini sets --import-mode=importlib, so conftest is not a
    top-level module. Both were tried before this.

    A fixture skip is reported per test, which reads better than one module
    skip anyway: the run says which assertions did not happen.
    """
    if TARGET is None:
        pytest.skip(SKIP_MESSAGE)
    return TARGET


@pytest.fixture(scope="session")
def glue_script_location():
    """Callable: job name -> the deployed job's ScriptLocation."""
    return aws_glue_script_location


def pytest_report_header(config):
    if TARGET is None:
        return ["echo integration target: not discovered (suite will skip)"]
    return [
        "echo integration target:",
        "  api  %s" % TARGET["api_url"],
        "  ui   %s" % TARGET["ui_url"],
        "  glue %s expecting script sha %s" % (TARGET["glue_job"], TARGET["expect_sha"][:12]),
    ]


def aws_glue_script_location(job_name):
    """The deployed job's ScriptLocation, read through the aws CLI.

    The CLI rather than boto3 on purpose: the runner is guaranteed to have the
    CLI (every other step in the promote uses it) and is not guaranteed to have
    boto3 installed for the interpreter pytest happens to be running under.
    """
    if not shutil.which("aws"):
        raise Unavailable("aws CLI is not on PATH")
    raw = _run([
        "aws", "glue", "get-job", "--job-name", job_name,
        "--query", "Job.Command.ScriptLocation", "--output", "json",
    ])
    return json.loads(raw)
