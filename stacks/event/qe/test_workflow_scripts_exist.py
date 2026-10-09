"""Every script an event workflow invokes must exist at the path it invokes it by.

This suite exists because the same defect landed twice. promote-event.yml was
written before its scripts were ported, and shipped calling four that were not
in the tree:

  resolve-deploy-mode.sh    fixed by UIFPE-744's first pass, which recorded
                            that "every promote run failed" because of it
  app-only-addresses.sh     still missing after that pass
  assert-app-only-plan.sh   still missing -- and it is the guard the promotion
                            model mandates by name, the one that refuses a
                            promote whose plan touches infrastructure
  next-release-tag.sh       still missing

Nothing caught the remaining three, because no test read a workflow and asked
whether the thing it shells out to is there. A missing script fails at the step
that needs it, which for promote-event.yml is AFTER copy-artifacts.sh has
already put bytes in the target environment -- so the failure lands half way
through a promotion rather than before it starts.

THE PART THAT IS EASY TO GET WRONG, and the reason this is a test rather than a
glance: the path in the `run:` block is relative to that STEP's
working-directory, not to the repo root. promote-event.yml's assert step runs
in stacks/event/infrastructure/terraform and calls
`../../scripts/app-only-addresses.sh`, which is stacks/event/scripts/. Reading
the string without resolving it against the working-directory gives the wrong
answer -- it names a directory that has no scripts in it at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WF = REPO / ".github" / "workflows"

#: The event workflows. A new one belongs here the day it is added; the
#: discovery below would otherwise silently cover less than it appears to.
WORKFLOWS = [
    "deploy-event-dev.yml",
    "deploy-event-infra.yml",
    "promote-event.yml",
    "release-event.yml",
]

#: A script invocation inside a `run:` block. Deliberately narrow: it matches a
#: path ending .sh or .py that contains a slash, which is every way these
#: workflows call one and no way they refer to one in prose.
INVOCATION = re.compile(r"(?<![\w./-])((?:\.\./|\./)*[\w./-]*/[\w.-]+\.(?:sh|py))\b")

#: Paths that are created during the run rather than committed, so their
#: absence from the tree is correct. Keep this empty unless something really is
#: generated -- an entry here is a hole in the check.
GENERATED: set[str] = set()


def _invocations() -> list[tuple[str, str, str, Path]]:
    """(workflow, step name, literal path, resolved path) for every invocation."""
    out: list[tuple[str, str, str, Path]] = []
    for name in WORKFLOWS:
        doc = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
        wf_default = ((doc.get("defaults") or {}).get("run") or {}).get("working-directory")
        for job in (doc.get("jobs") or {}).values():
            job_default = ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
            for step in job.get("steps") or []:
                run = step.get("run")
                if not run:
                    continue
                # Precedence is step, then job, then workflow -- the same order
                # the runner uses.
                wd = step.get("working-directory") or job_default or wf_default or "."
                for m in INVOCATION.finditer(run):
                    literal = m.group(1)
                    out.append((name, step.get("name") or "(unnamed)", literal,
                                (REPO / wd / literal).resolve()))
    return out


ALL = _invocations()


def test_discovery_actually_found_invocations():
    """A regex that matches nothing would make every assertion below vacuous."""
    assert len(ALL) >= 10, f"only {len(ALL)} invocations found; the regex has stopped matching"
    assert {w for w, _, _, _ in ALL} == set(WORKFLOWS), \
        "at least one event workflow contributed no script invocation at all"


def test_the_assert_step_resolves_outside_the_terraform_directory():
    """Pins the resolution rule this suite exists to get right.

    promote-event.yml's assert step runs in the Terraform directory and reaches
    back out with ../../. If a refactor moves the step or drops its
    working-directory, the same literal would resolve somewhere else and this
    test says so rather than the promote failing in a deploy window.
    """
    assert_steps = [r for r in ALL if r[2].endswith("assert-app-only-plan.sh")]
    assert assert_steps, "promote-event.yml no longer invokes assert-app-only-plan.sh"
    for _, _, literal, resolved in assert_steps:
        assert resolved == (REPO / "stacks/event/scripts/assert-app-only-plan.sh").resolve(), (
            f"{literal} resolves to {resolved}, not stacks/event/scripts/. "
            "The promotion model mandates this guard by name; a path that "
            "resolves elsewhere means the promote runs without it."
        )


@pytest.mark.parametrize(
    "workflow,step,literal,resolved",
    [pytest.param(*r, id=f"{r[0]}::{r[2]}") for r in ALL],
)
def test_every_invoked_script_exists(workflow, step, literal, resolved):
    if literal in GENERATED:
        pytest.skip(f"{literal} is generated during the run")
    assert resolved.is_file(), (
        f"{workflow} step {step!r} invokes {literal}, which resolves to "
        f"{resolved.relative_to(REPO) if REPO in resolved.parents else resolved} "
        "and does not exist. A workflow cannot call a script that is not in the tree."
    )


def test_the_three_scripts_this_suite_was_written_for_are_present():
    """Named explicitly, so a regex change cannot quietly stop covering them."""
    for name in ("app-only-addresses.sh", "assert-app-only-plan.sh", "next-release-tag.sh"):
        assert (REPO / "stacks/event/scripts" / name).is_file(), f"{name} is missing again"
