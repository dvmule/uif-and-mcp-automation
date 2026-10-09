# Wizard submission service (UIFPE-218) — the pure half of lambda_submit.
#
# validate_submission mirrors the repository's own gates (lint-pr title/body
# floors, lint-identity author rules, the intake path fence), and ladder()
# is the whole "My submissions" promise: the author never needs GitHub to
# know where their change stands. Both are pure functions over plain JSON,
# so they are tested here without credentials, network, or boto3.
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MOD = REPO / "stacks/echo/src/apps/lambdas/submit.py"
spec = importlib.util.spec_from_file_location("submit", MOD)
submit = importlib.util.module_from_spec(spec)
sys.modules["submit"] = submit
spec.loader.exec_module(submit)


GOOD = {
    "ticket": "UIFPE-218",
    "author": {"name": "Reyas Ahamed Habib Rahuman",
               "email": "reyasahamed.habibrahman@pearson.com"},
    "branch": "intake/UIFPE-218-add-product-20260828120000",
    "files": [{"path": "domains/product/src/schemas/intake/submitted/UIFPE-218-t.json",
               "content": "{\"task\": \"t\"}"}],
    "commitMessage": "UIFPE-218 #comment add returnsWindow — submitted from the Transform wizard",
    "prTitle": "UIFPE-218 Add returnsWindow to product (Transform wizard submission)",
    "prBody": "## Summary\n\nwords\n\n## Verification\n\nmore words",
}


def with_(**over):
    return {**{k: (dict(v) if isinstance(v, dict) else v) for k, v in GOOD.items()},
            **over}


class TestValidateSubmission:
    def test_a_complete_submission_passes(self):
        assert submit.validate_submission(GOOD) == []

    def test_identity_rules_match_lint_identity(self):
        # the exact two shapes .githooks/lint-identity.sh refuses
        for email in ("someone@gmail.com", "first.last+alias@pearson.com", ""):
            p = with_(author={"name": "X", "email": email})
            assert any("pearson.com" in m for m in submit.validate_submission(p)), email

    def test_ticket_and_branch_must_agree(self):
        p = with_(branch="intake/UIFPE-999-add-product")
        assert any("same ticket" in m for m in submit.validate_submission(p))

    def test_branch_outside_the_intake_namespace_is_refused(self):
        for branch in ("develop", "main", "feature/UIFPE-218-x",
                       "intake/../develop", "intake/UIFPE-218-CAPS"):
            p = with_(branch=branch)
            assert submit.validate_submission(p), branch

    def test_paths_outside_the_schema_tree_are_refused(self):
        # the write fence: the credential could write anywhere; the service
        # must not — not workflows, not hooks, not code
        for path in (".github/workflows/x.yml", ".githooks/lint-pr.sh",
                     "domains/product/src/apps/x.py",
                     "domains/product/src/schemas/../apps/x.json",
                     "domains/product/src/schemas/x.yaml"):
            p = with_(files=[{"path": path, "content": "{}"}])
            assert submit.validate_submission(p), path

    def test_a_new_table_submission_carries_two_files(self):
        p = with_(files=GOOD["files"] + [
            {"path": "domains/product/src/schemas/gold/order/v1/master.json",
             "content": "{}"}])
        assert submit.validate_submission(p) == []

    def test_non_json_content_is_refused(self):
        p = with_(files=[{"path": GOOD["files"][0]["path"], "content": "not json"}])
        assert any("not valid JSON" in m for m in submit.validate_submission(p))

    def test_title_body_and_smart_commit_floors(self):
        assert submit.validate_submission(with_(prTitle="UIFPE-218 x"))
        assert submit.validate_submission(with_(prTitle="Add stuff to product wizard"))
        # lint-pr floors: >=15 chars AND >=3 words after the key
        assert submit.validate_submission(
            with_(prTitle="UIFPE-218 reconfiguration"))  # 1 word, 15 chars
        assert submit.validate_submission(with_(prBody="## Summary\nonly"))
        assert submit.validate_submission(with_(commitMessage="UIFPE-218 add x"))

    def test_unicode_digit_lookalikes_are_refused(self):
        # \d without re.ASCII matches Arabic-Indic digits, which lint-pr's
        # [0-9] and Jira's literal matching never would
        assert submit.validate_submission(with_(ticket="UIFPE-٢١٨"))

    def test_malformed_shapes_are_a_reason_not_a_crash(self):
        assert submit.validate_submission(with_(author="me"))
        assert submit.validate_submission(with_(files=["x"]))
        assert submit.validate_submission(with_(files="not a list"))


class TestEntityIdentity:
    def test_the_marker_wins_and_round_trips_underscores(self):
        # the branch slug is lossy (product_history -> product-history), so
        # entity identity rides the PR body marker, exactly as written
        p = {"body": "<!-- uif:entity=product_history -->\n## Summary",
             "head": {"ref": "intake/UIFPE-9-new-table-product-history"}}
        assert submit.entity_of(p) == "product_history"

    def test_branch_parsing_is_only_the_fallback(self):
        f = submit.entity_from_branch
        assert f("intake/UIFPE-218-add-product") == "product"
        assert f("intake/UIFPE-218-change-product") == "product"
        assert f("intake/UIFPE-300-new-table-order") == "order"
        assert f("intake/UIFPE-1-something-else") is None
        assert f("feature/UIFPE-218-add-product") is None
        assert submit.entity_of(
            {"body": "no marker", "head": {"ref": "intake/UIFPE-2-add-sku"}}
        ) == "sku"


def pr(**over):
    return {**{"state": "open", "merged_at": None, "merge_commit_sha": None,
               "head": {"ref": "intake/UIFPE-218-add-product"},
               "body": "<!-- uif:entity=product -->"}, **over}


class TestLadder:
    def test_fresh_pr_is_submitted(self):
        assert submit.ladder(pr(), [], [], [], [])["status"] == "submitted"

    def test_any_review_activity_means_in_review(self):
        assert submit.ladder(pr(), [{"state": "COMMENTED"}], [], [], [])["status"] \
            == "in review"
        # conversation-tab comments create no review object — the fetched
        # comments LIST is the signal (the pulls list payload has no counts)
        assert submit.ladder(pr(), [], [{"body": "why this filter?"}], [], [])["status"] \
            == "in review"

    def test_approval_shows_before_merge(self):
        got = submit.ladder(pr(), [{"user": {"login": "raj"}, "state": "APPROVED"}],
                            [], [], [])
        assert got["status"] == "approved — waiting to merge"
        assert got["approved"] is True

    def test_approval_is_not_sticky_after_changes_requested(self):
        # GitHub keeps every review object; the reviewer's LATEST verdict wins
        reviews = [{"user": {"login": "raj"}, "state": "APPROVED"},
                   {"user": {"login": "raj"}, "state": "CHANGES_REQUESTED"}]
        got = submit.ladder(pr(), reviews, [], [], [])
        assert got["approved"] is False
        assert got["status"] == "in review"

    def test_closed_unmerged_is_said_plainly(self):
        assert submit.ladder(pr(state="closed"), [], [], [], [])["status"] \
            == "closed without merging"

    def test_merge_walks_the_deploy_ladder(self):
        merged = pr(state="closed", merged_at="2026-08-28T10:00:00Z",
                    merge_commit_sha="abc123")
        assert submit.ladder(merged, [], [], [], [])["status"] \
            == "merged — deploy not started yet"
        running = [{"head_sha": "abc123", "status": "in_progress"}]
        assert submit.ladder(merged, [], [], running, [])["status"] == "deploying"
        failed = [{"head_sha": "abc123", "status": "completed",
                   "conclusion": "failure"}]
        assert "deploy failed" in submit.ladder(merged, [], [], failed, [])["status"]

    def test_deployed_then_first_run_verdicts(self):
        merged = pr(state="closed", merged_at="2026-08-28T10:00:00Z",
                    merge_commit_sha="abc123")
        ok = [{"head_sha": "abc123", "status": "completed", "conclusion": "success"}]
        assert "waiting for the next scheduled run" \
            in submit.ladder(merged, [], [], ok, [])["status"]

        run = {"Arguments": {"--gold_id": "product"},
               "StartedOn": "2026-08-28T11:00:00Z", "JobRunState": "SUCCEEDED"}
        assert submit.ladder(merged, [], [], ok, [run])["firstRun"] == "succeeded"
        assert submit.ladder(merged, [], [], ok,
                             [{**run, "JobRunState": "FAILED"}])["firstRun"] == "failed"
        # a run BEFORE the merge is the previous logic, not this change's run
        early = {**run, "StartedOn": "2026-08-28T09:00:00Z"}
        assert "waiting" in submit.ladder(merged, [], [], ok, [early])["status"]
        # another entity's run says nothing about this one
        other = {**run, "Arguments": {"--gold_id": "program"}}
        assert "waiting" in submit.ladder(merged, [], [], ok, [other])["status"]

    def test_the_first_run_is_the_verdict_not_the_newest(self):
        # a later green run must not paper over the first one failing —
        # glue_runs arrive newest first, so this catches a max/first bug
        merged = pr(state="closed", merged_at="2026-08-28T10:00:00Z",
                    merge_commit_sha="abc123")
        ok = [{"head_sha": "abc123", "status": "completed", "conclusion": "success"}]
        newest_green = {"Arguments": {"--gold_id": "product"},
                        "StartedOn": "2026-08-28T13:00:00Z",
                        "JobRunState": "SUCCEEDED"}
        first_failed = {"Arguments": {"--gold_id": "product"},
                        "StartedOn": "2026-08-28T11:00:00Z",
                        "JobRunState": "FAILED"}
        got = submit.ladder(merged, [], [], ok, [newest_green, first_failed])
        assert got["firstRun"] == "failed"


class TestSummarizeComments:
    def test_reviewer_words_surface_newest_last_and_capped(self):
        reviews = [{"user": {"login": "raj"}, "state": "CHANGES_REQUESTED",
                    "submitted_at": "2026-08-28T09:00:00Z",
                    "body": "tighten the filter"}]
        comments = [{"user": {"login": "satya"},
                     "created_at": f"2026-08-28T1{i}:00:00Z", "body": f"c{i}"}
                    for i in range(6)]
        got = submit.summarize_comments(reviews, comments)
        assert len(got) == 5
        assert got[-1]["text"] == "c5"
        assert all(m["text"] for m in got)

    def test_empty_review_bodies_are_dropped(self):
        # GitHub sends an empty body for a bare approval click
        got = submit.summarize_comments(
            [{"user": {"login": "raj"}, "state": "APPROVED",
              "submitted_at": "2026-08-28T09:00:00Z", "body": ""}], [])
        assert got == []


class TestHandler:
    def test_validation_failures_return_400_with_the_reasons(self):
        r = submit.handler({"requestContext": {"http": {"method": "POST"}},
                            "body": "{}"})
        assert r["statusCode"] == 400
        assert "problems" in r["body"]

    def test_bad_json_is_a_400_not_a_500(self):
        r = submit.handler({"requestContext": {"http": {"method": "POST"}},
                            "body": "not json"})
        assert r["statusCode"] == 400

    def test_no_cors_headers_from_the_handler(self):
        # the function URL's cors{} config injects them; doubled headers make
        # the browser reject the response (ai_draft.py's hard-won lesson)
        r = submit.handler({"requestContext": {"http": {"method": "POST"}},
                            "body": "{}"})
        assert not any(k.lower().startswith("access-control")
                       for k in r["headers"])
