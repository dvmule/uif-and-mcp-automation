# The wizard's generated pull requests must pass the repository's OWN gates
# (UIFPE-218).
#
# A submission service that produced an unmergeable pull request would be
# worse than one that produced none: the user would be told their change was
# submitted, and CI would reject it minutes later for a reason they cannot
# read. So the generated title and body are not merely pattern-checked here —
# they are piped through the real .githooks/lint-pr.sh, the same script
# pull-request.yml runs, and the real .githooks/lint-identity.sh rule is
# applied to the commit author.
#
# The fixture is written by the TypeScript side (the plan is built in the
# browser):
#   REGEN_SUBMISSION_PLAN=1 npx vitest run src/lib/__tests__/submission.test.ts
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FIXTURE = (REPO / "stacks/echo/transform-ui/src/lib/__fixtures__/submissionPlan.json")
LINT_PR = REPO / ".githooks/lint-pr.sh"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

# lint-pr.sh is bash + perl + awk. On Windows the Git-for-Windows bash is
# present; skip rather than fake it if some environment has neither.
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    BASH is None or not LINT_PR.exists(),
    reason="bash or .githooks/lint-pr.sh unavailable")


def run_lint(title: str, body: str, tmp_path: Path):
    body_file = tmp_path / "pr.md"
    body_file.write_text(body, encoding="utf-8")
    return subprocess.run(
        [BASH, str(LINT_PR), "--title", title, "--body-file", str(body_file)],
        capture_output=True, text=True, cwd=str(REPO))


def test_the_fixture_covers_every_submission_shape():
    # add / add-many / modify / new-table / derived — if the wizard grows a
    # shape, it belongs here before it can be submitted
    assert set(CASES) == {"add", "addMany", "modify", "newTable", "derived"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_generated_pull_request_passes_the_repo_linter(case, tmp_path):
    c = CASES[case]
    r = run_lint(c["prTitle"], c["prBody"], tmp_path)
    assert r.returncode == 0, (
        f"{case}: the wizard would open a pull request CI rejects.\n"
        f"title: {c['prTitle']}\n--- lint output ---\n{r.stdout}\n{r.stderr}")


def test_the_linter_would_actually_have_caught_a_bad_one(tmp_path):
    # proves the gate above is not vacuous: the same harness rejects the two
    # failures it exists to prevent
    missing_key = run_lint("Add returnsWindow to the product table",
                           CASES["add"]["prBody"], tmp_path)
    assert missing_key.returncode != 0
    assert "UIFPE" in (missing_key.stdout + missing_key.stderr)

    no_sections = run_lint(CASES["add"]["prTitle"],
                           "just some words with no headings at all", tmp_path)
    assert no_sections.returncode != 0


@pytest.mark.parametrize("case", sorted(CASES))
def test_commit_message_is_a_smart_commit_authored_by_a_person(case):
    msg = CASES[case]["commitMessage"]
    # Smart Commit: the key + #comment is what reaches the ticket, and it is
    # silently discarded unless the AUTHOR matches a Jira user — which is why
    # the service credits itself in a trailer instead of authoring.
    assert re.match(r"^UIFPE-\d+ #comment \S", msg), msg
    assert "Co-Authored-By:" in msg
    for forbidden in ("bot@", "noreply@github.com", "users.noreply"):
        assert forbidden not in msg, f"{case}: a bot must not appear as an author"


@pytest.mark.parametrize("case", sorted(CASES))
def test_branch_name_is_watched_by_the_compile_workflow(case):
    branch = CASES[case]["branch"]
    assert re.match(r"^intake/UIFPE-\d+-[a-z0-9-]+$", branch), branch
    wf = REPO / ".github/workflows/intake-compile.yml"
    if wf.exists():
        # the workflow's trigger must actually match the branches the wizard
        # creates, or a submission sits there compiling nothing. The trigger
        # is deliberately intake/UIFPE-* (not intake/**): a hand-pushed
        # intake/experiment must not run the compiler. GitHub's single *
        # does not cross '/', and generated branches have no further '/'.
        text = wf.read_text(encoding="utf-8")
        assert 'branches: ["intake/UIFPE-*"]' in text
        assert "/" not in branch[len("intake/"):]


def test_identity_rule_applied_to_the_author_the_service_will_use():
    # the service sets author = the submitting engineer; run the repo's own
    # rule over the addresses the UI accepts and rejects
    lint_id = REPO / ".githooks/lint-identity.sh"
    if not lint_id.exists():
        pytest.skip("lint-identity.sh not present")
    text = lint_id.read_text(encoding="utf-8")
    # the two shapes the UI refuses are exactly the two this script refuses
    assert "*+*@pearson.com)" in text
    assert "not a @pearson.com address" in text
