"""Unit tests for scripts/check-version.sh.

These shell out to bash and to git, both present on the ubuntu-latest runners
this script actually runs on. Each test builds a throwaway git repo in tmp_path
so tag state is controlled rather than inherited from the checkout.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "check-version.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="bash and git must be on PATH"
)


def _repo(tmp_path: Path, version: str, tags: list[str]) -> Path:
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "VERSION").write_text(version + "\n", newline="\n")
    subprocess.run([GIT, "add", "VERSION"], cwd=tmp_path, check=True)
    subprocess.run([GIT, "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)
    for tag in tags:
        subprocess.run([GIT, "tag", tag], cwd=tmp_path, check=True)
    return tmp_path


def _run(repo: Path, component: str | None = "apicurio"):
    """COMPONENT is REQUIRED by the script (UIFPE-730); these tests used to
    lean on a default of "apicurio", which is exactly the footgun that was
    removed. Pass component=None to exercise the refusal."""
    env = {**os.environ}
    if component is None:
        env.pop("COMPONENT", None)
    else:
        env["COMPONENT"] = component
    return subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION")],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_prints_the_tag_for_a_first_release(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", []))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.1.0"


def test_tolerates_trailing_whitespace_in_the_file(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0  ", []))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.1.0"


@pytest.mark.parametrize("bad", ["0.1", "v0.1.0", "0.1.0-rc1", "1.0", "", "abc"])
def test_rejects_anything_that_is_not_bare_semver(tmp_path, bad):
    result = _run(_repo(tmp_path, bad, []))
    assert result.returncode == 1
    assert "MAJOR.MINOR.PATCH" in result.stderr


def test_rejects_a_version_whose_tag_already_exists(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["apicurio/v0.1.0"]))
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_rejects_a_version_not_greater_than_the_latest(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["apicurio/v0.2.0"]))
    assert result.returncode == 1
    assert "not greater" in result.stderr


def test_compares_numerically_not_lexically(tmp_path):
    # 0.10.0 IS greater than 0.9.0. A lexical `sort` says the opposite, which
    # would refuse every release after the ninth minor.
    result = _run(_repo(tmp_path, "0.10.0", ["apicurio/v0.9.0"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.10.0"


def test_ignores_tags_belonging_to_other_components(tmp_path):
    result = _run(_repo(tmp_path, "0.1.0", ["echo/v9.9.9"]))
    assert result.returncode == 0, result.stderr


def test_fails_clearly_when_the_file_is_missing(tmp_path):
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True)
    result = subprocess.run(
        [BASH, str(SCRIPT), str(tmp_path / "VERSION")],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "COMPONENT": "apicurio"},
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


# ── --against <ref>: the version-race guard ────────────────────────────────
#
# A declared version is not a lock. Two open pull requests can each declare
# the same number, because the tag check above cannot refuse a tag that does
# not exist yet. The first to merge cuts it; the second merge writes the same
# bytes to VERSION, git merges it with no conflict, the release workflow's
# `paths: VERSION` filter sees no change, and that branch ships untagged.
# Measured on this repository 2026-09-27: echo/v0.7.0 was cut on PR #613 and
# does not contain PR #614 at all.
#
# These build a base branch as a real ref so the window being tested is the
# exact one that bit: the base ALREADY DECLARES the version and NO TAG EXISTS.


def _with_base(tmp_path: Path, base_version: str, branch_version: str,
               tags: list[str] | None = None) -> Path:
    """A repo shaped like the race: one shared ancestor, then `base` and the
    checked-out branch each bumping VERSION independently.

    Three commits, not two. Both sides declaring the SAME number is the whole
    point, and a fixture that writes the ancestor's own value has nothing to
    commit — which is how this helper failed before the shape was right.
    No tags unless asked: the window that bites is the one before the tag.
    """
    repo = _repo(tmp_path, "0.0.1", tags or [])
    mine = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()
    for branch, version in (("base", base_version), (mine, branch_version)):
        if branch == "base":
            subprocess.run([GIT, "switch", "-qc", "base"], cwd=repo, check=True)
        else:
            subprocess.run([GIT, "switch", "-q", mine], cwd=repo, check=True)
        (repo / "VERSION").write_text(version + "\n", newline="\n")
        subprocess.run([GIT, "commit", "-qam", f"declare {version}"],
                       cwd=repo, check=True)
    return repo


def _run_against(repo: Path, ref: str = "base"):
    return subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION"), "--against", ref],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "COMPONENT": "apicurio"},
    )


def test_refuses_a_version_the_base_already_declares_with_no_tag_yet(tmp_path):
    """THE RACE, replayed. Both sides declare 0.7.0 and no tag exists, so
    every other check in this script passes it through."""
    repo = _with_base(tmp_path, "0.7.0", "0.7.0")
    assert _run(repo).returncode == 0, "the other checks do NOT catch this"
    result = _run_against(repo)
    assert result.returncode == 1
    assert "already declares VERSION 0.7.0" in result.stderr
    assert "would not fire" in result.stderr


def test_allows_a_version_the_base_has_not_claimed(tmp_path):
    repo = _with_base(tmp_path, "0.7.0", "0.8.0")
    result = _run_against(repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apicurio/v0.8.0"


def test_allows_it_when_the_file_does_not_exist_on_the_base(tmp_path):
    """A stack declaring its first version has nothing to collide with."""
    repo = _with_base(tmp_path, "0.1.0", "0.2.0")
    subprocess.run([GIT, "rm", "-q", "--cached", "VERSION"], cwd=repo, check=True)
    subprocess.run([GIT, "commit", "-qm", "untrack"], cwd=repo, check=True)
    subprocess.run([GIT, "branch", "-f", "empty", "HEAD"], cwd=repo, check=True)
    result = _run_against(repo, "empty")
    assert result.returncode == 0, result.stderr


def test_refuses_rather_than_skipping_when_the_ref_cannot_be_resolved(tmp_path):
    """Fail closed. A guard that passes when it cannot look is worse than no
    guard, because it reports success — exactly the fail-open shape this
    repository has been bitten by before.

    The assertions are specific on purpose. A control round turned a
    fail-open mutation into `against=""` plus a warning that ALSO said "does
    not resolve", and execution then fell through to the comparison with an
    empty ref — which reads the index and refuses for an unrelated reason. A
    looser test passed that mutation with the right exit code for the wrong
    cause, so the refusal must be the resolve one and nothing else.
    """
    repo = _with_base(tmp_path, "0.7.0", "0.7.0")
    result = _run_against(repo, "origin/nope")
    assert result.returncode == 1
    assert "does not resolve to a commit" in result.stderr
    assert "Refusing" in result.stderr
    assert "already declares" not in result.stderr, (
        "it fell through to the comparison instead of refusing outright")


def test_the_bases_value_is_normalised_the_same_way_as_ours(tmp_path):
    """Both sides go through `tr -d '[:space:]'`, so a VERSION carrying
    whitespace on one side still collides. The file next door already
    tolerates a trailing space in our own VERSION; a guard that tolerates it
    in ours but not the base's lets the race through on that difference alone.

    A trailing space rather than a stray CR deliberately: `$(...)` strips
    trailing newlines but not spaces on every platform, whereas MSYS also eats
    a CR — so a CR version of this test would pass vacuously on Windows and
    only mean something on the runner.
    """
    repo = _repo(tmp_path, "0.0.1", [])
    mine = subprocess.run([GIT, "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run([GIT, "switch", "-qc", "base"], cwd=repo, check=True)
    (repo / "VERSION").write_text("0.7.0 \n", newline="\n")
    subprocess.run([GIT, "commit", "-qam", "declare with a trailing space"],
                   cwd=repo, check=True)
    subprocess.run([GIT, "switch", "-q", mine], cwd=repo, check=True)
    (repo / "VERSION").write_text("0.7.0\n", newline="\n")
    subprocess.run([GIT, "commit", "-qam", "declare 0.7.0"], cwd=repo, check=True)

    result = _run_against(repo)
    assert result.returncode == 1, (
        "the base declares the same version with a trailing space and it was "
        "not recognised as a collision")
    assert "already declares VERSION 0.7.0" in result.stderr


def test_the_guard_is_opt_in_so_the_release_workflow_is_unaffected(tmp_path):
    """release-echo.yml and release-apicurio.yml call the script with one
    argument, AFTER the bump has merged — where the base is this very commit
    and the check would refuse every real release."""
    repo = _with_base(tmp_path, "0.7.0", "0.8.0")
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_rejects_an_unknown_option(tmp_path):
    repo = _repo(tmp_path, "0.1.0", [])
    result = subprocess.run(
        [BASH, str(SCRIPT), str(repo / "VERSION"), "--nope"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_the_two_stacks_carry_an_identical_copy_of_this_script():
    """echo and apicurio each ship their own copy and there is no shared
    location for it. They are identical today; a fix applied to one only is
    the drift this asserts against.

    This reads every stack's copy, so it is coupled to files outside apicurio.
    stacks/apicurio/ci-watch declares that pattern, which is what makes a
    change to another stack's copy run this suite at all (UIFPE-728).

    Line endings are normalised, because git already normalises them and this
    assertion has to mean the same thing it does. Raw bytes made it fail once
    for no drift at all: one copy had been rewritten LF while the other stayed
    CRLF, which `git diff` reports as no change under core.autocrlf.
    """
    peers = sorted(SCRIPT.parents[2].glob("*/scripts/check-version.sh"))
    assert len(peers) >= 2, f"expected two or more copies, found {peers}"
    bodies = {p.read_bytes().replace(b"\r\n", b"\n") for p in peers}
    assert len(bodies) == 1, (
        "check-version.sh has drifted between stacks: "
        + ", ".join(str(p.relative_to(SCRIPT.parents[3])) for p in peers))
def test_an_unset_component_variable_is_refused_rather_than_defaulted(tmp_path):
    """The defect UIFPE-730 removed. This script used to default to
    "apicurio" -- convenient here and dangerous everywhere else, since a stack
    whose workflow forgot COMPONENT would cut an apicurio tag. It also made the
    per-stack copies impossible to keep identical.
    """
    result = _run(_repo(tmp_path, "0.1.0", []), component=None)
    assert result.returncode != 0, (
        "an unset COMPONENT produced a tag instead of refusing: "
        f"{result.stdout.strip()!r}")
    assert "COMPONENT" in result.stderr
    assert "apicurio/v" not in result.stdout
