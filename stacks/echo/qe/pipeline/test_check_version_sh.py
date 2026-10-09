"""echo's copy of scripts/check-version.sh.

echo and apicurio each ship their OWN copy of this script and there is no
shared location for it. apicurio's suite
(stacks/apicurio/qe/tests/release/test_check_version_sh.py) carries the full
behavioural set; this file exists because stacks-ci runs each stack's gate
only when that stack is touched, so an edit to echo's copy alone would meet
none of apicurio's tests.

This test reads EVERY stack's copy, so it is coupled to files outside echo.
stacks/echo/ci-watch declares that pattern and is what makes a change to
another stack's copy select echo's CI at all -- UIFPE-690 added event's copy
and neither suite ran (UIFPE-730, mechanism fixed by UIFPE-728).

Two things are asserted here, and the first makes the second cheap: the copies
are byte-identical, so apicurio's coverage applies to echo's file too, and the
version-race guard demonstrably works when invoked as echo's release path
invokes it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[2]
STACKS = STACK_ROOT.parent
SCRIPT = STACK_ROOT / "scripts" / "check-version.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="bash and git must be on PATH"
)


def test_every_stack_carries_an_IDENTICAL_copy():
    """Line endings normalised, because git already normalises them and this
    assertion has to mean the same thing it does.

    Raw bytes made this fail once for no drift at all: one copy had been
    rewritten LF while the other stayed CRLF, which `git diff` reports as no
    change under core.autocrlf and a reviewer would never see. Comparing
    content keeps the check about the script and not about the checkout.
    """
    peers = sorted(STACKS.glob("*/scripts/check-version.sh"))
    assert len(peers) >= 2, f"expected two or more copies, found {peers}"
    bodies = {p.read_bytes().replace(b"\r\n", b"\n") for p in peers}
    assert len(bodies) == 1, (
        "check-version.sh has drifted between stacks: "
        + ", ".join(str(p.relative_to(STACKS.parent)) for p in peers))


def _race_repo(tmp_path: Path) -> Path:
    """One shared ancestor, then `base` and the branch each declaring 0.7.0
    independently — the shape that put PR #614's code on develop untagged."""
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "VERSION").write_text("0.6.0\n", newline="\n")
    subprocess.run([GIT, "add", "VERSION"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "commit", "-qm", "declare 0.6.0"], cwd=tmp_path, check=True)
    mine = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True).stdout.strip()
    for branch in ("base", mine):
        subprocess.run([GIT, "switch", "-q"] + (["-c"] if branch == "base" else [])
                       + [branch], cwd=tmp_path, check=True)
        (tmp_path / "VERSION").write_text("0.7.0\n", newline="\n")
        subprocess.run([GIT, "commit", "-qam", "declare 0.7.0"], cwd=tmp_path, check=True)
    return tmp_path


def _run(repo: Path, *extra):
    return subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION"), *extra],
        cwd=repo, capture_output=True, text=True, check=False,
        env={**os.environ, "COMPONENT": "echo"},
    )


def test_the_guard_refuses_the_race_that_the_other_checks_pass(tmp_path):
    repo = _race_repo(tmp_path)
    assert _run(repo).returncode == 0, (
        "without --against this is a clean release, which is exactly why "
        "echo/v0.7.0 was cut without PR #614 in it")
    result = _run(repo, "--against", "base")
    assert result.returncode == 1
    assert "already declares VERSION 0.7.0" in result.stderr


def test_the_tag_it_prints_carries_echos_component_name(tmp_path):
    """COMPONENT is what makes one identical script serve every stack. It is
    REQUIRED, not defaulted (UIFPE-730) -- a default let a stack that forgot it
    cut a tag named after another stack, and made the copies drift."""
    repo = _race_repo(tmp_path)
    assert _run(repo).stdout.strip() == "echo/v0.7.0"
def test_an_unset_component_variable_is_refused_rather_than_defaulted(tmp_path):
    """The defect UIFPE-730 removed: this script used to default the component
    to "apicurio", so a stack whose workflow forgot COMPONENT cut a tag named
    after another stack -- silently, and permanently once pushed. Refusing is
    the only safe behaviour, and every caller already sets it.
    """
    repo = _race_repo(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "COMPONENT"}
    result = subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION")],
        cwd=repo, capture_output=True, text=True, check=False, env=env,
    )
    assert result.returncode != 0, (
        "an unset COMPONENT produced a tag instead of refusing: "
        f"{result.stdout.strip()!r}")
    assert "COMPONENT" in result.stderr
    assert "apicurio" not in result.stdout, (
        "the old default leaked into the tag")
