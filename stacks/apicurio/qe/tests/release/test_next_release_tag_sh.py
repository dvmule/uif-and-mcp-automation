"""Unit tests for scripts/next-release-tag.sh.

The release tag records that a version ran in an environment and passed, so
two releases must never collide on one name and the counter must not reset
while same-day tags exist. The interesting cases are all ordering: 9 -> 10
must not sort as 1 -> 2, and another environment's tags must not advance this
environment's counter.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "next-release-tag.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="needs bash and git on PATH"
)

DATE = "2026-09-21"


@pytest.fixture
def repo(tmp_path):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.email", "t@e.st"), ("user.name", "Test")):
        subprocess.run([GIT, "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run([GIT, "-C", str(tmp_path), "add", "f"], check=True)
    subprocess.run(
        [GIT, "-C", str(tmp_path), "commit", "-qm", "init"], check=True
    )
    return tmp_path


def tag(repo, name):
    subprocess.run([GIT, "-C", str(repo), "tag", name], check=True)


def run(repo, *args):
    return subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def test_first_release_of_the_day_is_one(repo):
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_second_release_of_the_day_is_two(repo):
    tag(repo, f"release/uat/{DATE}.1")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.2"


def test_counter_is_numeric_not_lexical(repo):
    # The bug this catches: sorting as text gives .10 < .9, so the next tag
    # would be .10 again and collide.
    for n in range(1, 10):
        tag(repo, f"release/uat/{DATE}.{n}")
    tag(repo, f"release/uat/{DATE}.10")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.11"


def test_another_environment_does_not_advance_this_counter(repo):
    tag(repo, f"release/dev/{DATE}.1")
    tag(repo, f"release/dev/{DATE}.2")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_another_date_does_not_advance_this_counter(repo):
    tag(repo, "release/uat/2026-09-20.7")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_a_build_tag_does_not_advance_the_counter(repo):
    tag(repo, "apicurio/v0.1.1")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_a_non_numeric_suffix_does_not_advance_the_counter(repo):
    # A tag like "...1-rc" shares the prefix but is not a release this script
    # ever produced. It must be ignored, not treated as suffix "1-rc" and fed
    # into arithmetic -- that either miscounts or crashes the whole script.
    tag(repo, f"release/uat/{DATE}.1-rc")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_a_gap_in_the_sequence_still_advances_past_the_highest(repo):
    # Deleting .2 must not hand out .2 again -- the tag is evidence, and
    # reusing a name makes two different releases share one identity.
    tag(repo, f"release/uat/{DATE}.1")
    tag(repo, f"release/uat/{DATE}.3")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.4"


def test_an_unknown_environment_is_refused(repo):
    r = run(repo, "staging", DATE)
    assert r.returncode == 1
    assert "staging" in r.stderr


def test_a_malformed_date_is_refused(repo):
    r = run(repo, "uat", "21-09-2026")
    assert r.returncode == 1


def test_no_environment_argument_is_refused(repo):
    r = run(repo)
    assert r.returncode == 1


def test_outside_a_git_work_tree_is_refused(tmp_path):
    r = subprocess.run(
        [BASH, str(SCRIPT), "uat", DATE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "git" in r.stderr.lower()
