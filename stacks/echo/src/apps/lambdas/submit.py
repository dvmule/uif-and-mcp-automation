"""Wizard submission service (UIFPE-218).

POST — turn a finished Transform-wizard task into a pull request:
  {ticket, author:{name,email}, branch, files:[{path,content}],
   commitMessage, prTitle, prBody}
  -> {number, url}
The service creates the branch from develop, writes every file in ONE
commit, and opens the PR. Closes the ADR-0006 loop: the UI writes pull
requests, never live state — nothing this service does changes anything
until a human reviews and merges.

GET — "My submissions": ?author=<email> (optional filter)
  -> {submissions: [{number, url, title, ticket, status, ...}]}
  status ladder: submitted -> in review -> approved -> merged ->
  deploying/deployed/deploy failed -> first run succeeded/failed.
  The author never needs GitHub to know where their change stands.

Identity (the load-bearing rule): every commit is AUTHORED by the engineer
who submitted, at their exact @pearson.com address — the same rule
.githooks/lint-identity.sh enforces on every PR, and the rule Smart Commits
silently depend on (Jira matches the author email literally; an unmatched
author means the #comment is discarded without an error). The service is
credited in a Co-Authored-By trailer, exactly as an AI-assisted commit
credits its agent. The GitHub credential only TRANSPORTS the commit.

Credential (the decision parked since UIFPE-101): a fine-grained PAT scoped
to this one repository with contents:write + pull_requests:write, stored in
Secrets Manager — not a GitHub App. Two reasons. The identity rule above
means the credential never appears as an author, so an App's main benefit
(a non-personal identity on the commits) buys nothing here; and an App adds
a JWT/installation-token exchange (and a crypto dependency in this Lambda)
for the same two API scopes. Revisit if the PAT's owner leaves or the
platform auth story (Entra) lands. Machine branches are `intake/UIFPE-<n>-
<slug>` — namespaced so branch protection, CI triggers, and cleanup can
target them as a family.

Like ai_draft.py: no import-time AWS calls — every pure function here is
unit-testable without credentials (qe/test_submit_service.py).
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPO",
                      "pearson-dnt-coe/universal-Integration-framework")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "develop")
SECRET_NAME = os.environ.get("GITHUB_TOKEN_SECRET", "uif/echo/dev/submit/github")
# The workflow FILE, not its display name: runs are listed per workflow so a
# busy develop (CI, schema gates, other deploys) cannot push the deploy run
# out of a shared 50-row window.
DEPLOY_WORKFLOW_FILE = os.environ.get("DEPLOY_WORKFLOW_FILE",
                                      "deploy-product-dev.yml")
GLUE_JOB_NAME = os.environ.get("GLUE_JOB_NAME", "uif-echo-dev-gold-transform")
API = "https://api.github.com"

# re.ASCII: a plain \d matches any Unicode digit, which lint-pr's [0-9] and
# Jira's literal matching do not — a ticket that only LOOKS numeric must be
# refused here, not discovered as an unmergeable PR.
TICKET_RE = re.compile(r"^UIFPE-\d+$", re.ASCII)
BRANCH_RE = re.compile(r"^intake/UIFPE-\d+-[a-z0-9-]+$", re.ASCII)
# The only tree the service will write into: authored schema/intake JSON.
# Bounds the blast radius of a stolen or replayed request — the credential
# can write more, the service will not.
PATH_RE = re.compile(r"^domains/[a-z]+/src/schemas/[A-Za-z0-9_/.-]+\.json$",
                     re.ASCII)
# How a submission tells the service which entity it materializes — written
# into the PR body by the wizard, read back for the first-run check. The
# branch name is a lossy slug (underscores become dashes), so it is never
# parsed for identity.
ENTITY_MARKER_RE = re.compile(r"<!--\s*uif:entity=([A-Za-z0-9_.]+)\s*-->",
                              re.ASCII)


# ---------------------------------------------------------------- validation

def validate_submission(p: dict) -> list[str]:
    """Refuse anything the repository's own gates would refuse — before a
    branch exists, not minutes later in CI where the user cannot read it.
    Shapes are checked before fields: a malformed payload earns a 400 with a
    reason, never a 500 from an AttributeError."""
    out: list[str] = []
    ticket = str(p.get("ticket", "")).strip()
    if not TICKET_RE.fullmatch(ticket):
        out.append("ticket must look like UIFPE-218")

    author = p.get("author")
    if not isinstance(author, dict):
        out.append("author must be {name, email}")
        author = {}
    email = str(author.get("email", "")).strip().lower()
    # mirror .githooks/lint-identity.sh exactly: @pearson.com, no +alias
    if not email.endswith("@pearson.com") or "+" in email:
        out.append("author.email must be your @pearson.com address (no +alias)")
    if not str(author.get("name", "")).strip():
        out.append("author.name is required")

    branch = str(p.get("branch", ""))
    if not BRANCH_RE.fullmatch(branch):
        out.append("branch must look like intake/UIFPE-218-add-product")
    elif not branch.startswith(f"intake/{ticket}-"):
        out.append("branch must carry the same ticket as the submission")

    files = p.get("files")
    if not isinstance(files, list) or not files:
        out.append("files is empty — nothing to submit")
        files = []
    for f in files:
        if not isinstance(f, dict):
            out.append("each file must be {path, content}")
            continue
        path = str(f.get("path", ""))
        if not PATH_RE.fullmatch(path) or ".." in path:
            out.append(f"refusing path outside the schema tree: {path}")
        try:
            json.loads(f.get("content", ""))
        except (json.JSONDecodeError, TypeError):
            out.append(f"{path or '(file)'} is not valid JSON")

    # the same floors .githooks/lint-pr.sh enforces on the PR itself: after
    # the key, a title needs >=15 chars and >=3 words of real description
    title = str(p.get("prTitle", ""))
    rest = title[len(ticket):].strip() if title.startswith(f"{ticket} ") else ""
    if len(rest) < 15 or len(rest.split()) < 3:
        out.append("prTitle must start with the ticket and describe the change")
    body = str(p.get("prBody", ""))
    if "## Summary" not in body or "## Verification" not in body:
        out.append("prBody must carry Summary and Verification sections")

    msg = str(p.get("commitMessage", ""))
    if not msg.startswith(f"{ticket} #comment "):
        out.append("commitMessage must start with '<ticket> #comment ' "
                    "(the Smart Commit that reaches the ticket)")
    return out


# ------------------------------------------------------------- status ladder

def entity_from_branch(branch: str) -> str | None:
    """intake/UIFPE-218-add-product -> product (best effort, for run
    matching). The verb prefixes are the ones buildSubmissionPlan writes.
    LOSSY — slugging turns underscores into dashes — so this is only the
    fallback when the PR body carries no entity marker."""
    m = re.match(r"^intake/UIFPE-\d+-(?:add|change|new-table)-(.+)$", branch,
                 re.ASCII)
    return m.group(1) if m else None


def entity_of(pr: dict) -> str | None:
    """The entity a submission materializes: the wizard writes it into the
    PR body as an HTML comment, exactly (underscores intact), because the
    branch slug cannot round-trip it."""
    m = ENTITY_MARKER_RE.search(pr.get("body") or "")
    return m.group(1) if m else entity_from_branch(
        pr.get("head", {}).get("ref", ""))


def ladder(pr: dict, reviews: list[dict], comments: list[dict],
           deploy_runs: list[dict], glue_runs: list[dict]) -> dict:
    """One PR -> where the change stands, in the author's words. Pure —
    every input is plain GitHub/Glue API JSON. `comments` is the PR's
    issue-comments LIST (the pulls list payload carries no activity counts,
    so activity is judged from data actually fetched)."""
    # approval = each reviewer's LATEST verdict; GitHub keeps every review
    # object, so an early APPROVED must not outlive a later CHANGES_REQUESTED
    latest_by_user: dict = {}
    for r in reviews:  # oldest first from the API
        login = (r.get("user") or {}).get("login")
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest_by_user[login] = r.get("state")
    approved = ("APPROVED" in latest_by_user.values()
                and "CHANGES_REQUESTED" not in latest_by_user.values())

    if not pr.get("merged_at"):
        if pr.get("state") == "closed":
            status = "closed without merging"
        elif approved:
            status = "approved — waiting to merge"
        elif reviews or comments:
            status = "in review"
        else:
            status = "submitted"
        return {"status": status, "approved": approved}

    # merged: did the deploy that carries it succeed?
    merge_sha = pr.get("merge_commit_sha")
    run = next((r for r in deploy_runs if r.get("head_sha") == merge_sha), None)
    if run is None:
        return {"status": "merged — deploy not started yet", "approved": True}
    if run.get("status") != "completed":
        return {"status": "deploying", "approved": True}
    if run.get("conclusion") != "success":
        return {"status": "deploy failed — the data team has been paged by CI",
                "approved": True}

    # deployed: the FIRST scheduled run since the merge is the verdict — a
    # later green run must not paper over the first one failing
    entity = entity_of(pr)
    merged_at = pr.get("merged_at") or ""
    since = [g for g in glue_runs
             if g.get("Arguments", {}).get("--gold_id") == entity
             and str(g.get("StartedOn", "")) > merged_at]
    if since:
        first = min(since, key=lambda g: str(g.get("StartedOn", "")))
        state = first.get("JobRunState")
        if state == "SUCCEEDED":
            return {"status": "deployed — first run succeeded",
                    "approved": True, "firstRun": "succeeded"}
        if state in ("FAILED", "ERROR", "TIMEOUT", "STOPPED"):
            return {"status": "deployed — first run failed",
                    "approved": True, "firstRun": "failed"}
        return {"status": "deployed — first run in progress",
                "approved": True, "firstRun": "running"}
    return {"status": "deployed — waiting for the next scheduled run",
            "approved": True}


def summarize_comments(reviews: list[dict], comments: list[dict]) -> list[dict]:
    """The reviewer's words, surfaced in the UI (newest last, capped)."""
    merged = ([{"author": (r.get("user") or {}).get("login"),
                "when": r.get("submitted_at"), "text": r.get("body") or "",
                "verdict": r.get("state")} for r in reviews]
              + [{"author": (c.get("user") or {}).get("login"),
                  "when": c.get("created_at"), "text": c.get("body") or ""}
                 for c in comments])
    merged = [m for m in merged if m["text"].strip()]
    merged.sort(key=lambda m: m["when"] or "")
    return merged[-5:]


# ------------------------------------------------------------------- GitHub

def _gh(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def open_pull_request(token: str, p: dict) -> dict:
    """Branch from develop, ONE commit carrying every file (Git Data API —
    the Contents API is one-file-per-commit), then the PR.

    Failure discipline: the ref is the first write, so any later failure
    deletes it again — an orphaned branch would turn every retry into a
    misleading "already submitted". A branch that already exists is only
    "already submitted" when an OPEN pull request actually carries it;
    otherwise it is debris from a torn submission and gets reused."""
    who = {"name": p["author"]["name"], "email": p["author"]["email"]}
    owner = REPO.split("/")[0]
    base = _gh(token, "GET", f"/repos/{REPO}/git/ref/heads/{BASE_BRANCH}")
    base_sha = base["object"]["sha"]
    base_commit = _gh(token, "GET", f"/repos/{REPO}/git/commits/{base_sha}")

    reusing = False
    try:
        _gh(token, "POST", f"/repos/{REPO}/git/refs",
            {"ref": f"refs/heads/{p['branch']}", "sha": base_sha})
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
        existing = _gh(token, "GET",
                       f"/repos/{REPO}/pulls?state=open"
                       f"&head={owner}:{p['branch']}")
        if existing:
            raise SubmitError(409, "This change was already submitted — "
                              f"pull request #{existing[0]['number']}: "
                              f"{existing[0]['html_url']}")
        reusing = True  # leftover branch, no PR: a previous attempt tore

    try:
        tree = _gh(token, "POST", f"/repos/{REPO}/git/trees",
                   {"base_tree": base_commit["tree"]["sha"],
                    "tree": [{"path": f["path"], "mode": "100644",
                              "type": "blob", "content": f["content"]}
                             for f in p["files"]]})
        commit = _gh(token, "POST", f"/repos/{REPO}/git/commits",
                     {"message": p["commitMessage"], "tree": tree["sha"],
                      "parents": [base_sha],
                      # author AND committer = the engineer: lint-identity
                      # checks authors; a bot committer is machine noise
                      "author": who, "committer": who})
        _gh(token, "PATCH", f"/repos/{REPO}/git/refs/heads/{p['branch']}",
            {"sha": commit["sha"], "force": reusing})

        pr = _gh(token, "POST", f"/repos/{REPO}/pulls",
                 {"title": p["prTitle"], "body": p["prBody"],
                  "head": p["branch"], "base": BASE_BRANCH})
        return {"number": pr["number"], "url": pr["html_url"]}
    except Exception:
        try:  # best effort — a failed cleanup must not mask the real error
            _gh(token, "DELETE", f"/repos/{REPO}/git/refs/heads/{p['branch']}")
        except Exception:
            pass
        raise


def list_submissions(token: str, author_email: str | None) -> dict:
    """Every intake/** PR, newest first, with its ladder position. Calls are
    bounded: one PR list, one deploy-run list, one Glue list, plus
    reviews+comments for at most the 10 newest."""
    pulls = _gh(token, "GET",
                f"/repos/{REPO}/pulls?state=all&per_page=50&sort=created"
                f"&direction=desc&base={BASE_BRANCH}")
    mine = [pr for pr in pulls
            if pr.get("head", {}).get("ref", "").startswith("intake/")]
    if author_email:
        # the author is in the PR body the wizard wrote ("by NAME (email)")
        mine = [pr for pr in mine
                if author_email.lower() in (pr.get("body") or "").lower()]
    mine = mine[:10]

    deploy_runs: list[dict] = []
    glue_runs: list[dict] = []
    if any(pr.get("merged_at") for pr in mine):
        # per-WORKFLOW runs: a busy develop (CI, schema gates, other deploys)
        # must not push the deploy run out of a shared listing window
        runs = _gh(token, "GET",
                   f"/repos/{REPO}/actions/workflows/{DEPLOY_WORKFLOW_FILE}"
                   f"/runs?branch={BASE_BRANCH}&event=push&per_page=50")
        deploy_runs = runs.get("workflow_runs", [])
        glue_runs = fetch_glue_runs()

    out = []
    for pr in mine:
        n = pr["number"]
        reviews = _gh(token, "GET", f"/repos/{REPO}/pulls/{n}/reviews")
        comments = _gh(token, "GET", f"/repos/{REPO}/issues/{n}/comments")
        m = re.search(r"UIFPE-\d+", pr.get("title") or "", re.ASCII)
        out.append({
            "number": n, "url": pr["html_url"], "title": pr["title"],
            "ticket": m.group(0) if m else None,
            "submittedAt": pr.get("created_at"),
            "comments": summarize_comments(reviews, comments),
            **ladder(pr, reviews, comments, deploy_runs, glue_runs),
        })
    return {"submissions": out}


def fetch_glue_runs() -> list[dict]:
    """First-run evidence. Isolated so tests never need boto3 wiring, and a
    Glue outage degrades the ladder to 'deployed', never to an error."""
    try:
        import boto3
        from datetime import timezone
        runs = boto3.client("glue").get_job_runs(
            JobName=GLUE_JOB_NAME, MaxResults=100)["JobRuns"]
        # boto3 datetimes are LOCAL-tz aware; convert before stamping Z, or
        # the string comparison against GitHub's UTC timestamps skews
        return [{"Arguments": r.get("Arguments", {}),
                 "StartedOn": r["StartedOn"].astimezone(timezone.utc)
                                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "JobRunState": r.get("JobRunState")} for r in runs]
    except Exception:
        return []


# ------------------------------------------------------------------ handler

class SubmitError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


_token_cache: dict = {}
_TOKEN_TTL_SECONDS = 300  # a rotated PAT is picked up within minutes, not
                          # whenever the warm container happens to recycle


def github_token() -> str:
    import time
    now = time.monotonic()
    if "t" not in _token_cache or now - _token_cache.get("at", 0) > _TOKEN_TTL_SECONDS:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(
            SecretId=SECRET_NAME)["SecretString"]
        try:
            _token_cache["t"] = json.loads(raw)["token"]
        except (json.JSONDecodeError, KeyError):
            _token_cache["t"] = raw.strip()
        _token_cache["at"] = now
    return _token_cache["t"]


def _response(code: int, body: dict) -> dict:
    # no CORS headers here — the function URL's cors{} config injects them;
    # doubling up makes the browser reject the response (see ai_draft.py)
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def handler(event: dict, _context=None) -> dict:
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    try:
        if method == "GET":
            qs = event.get("queryStringParameters") or {}
            return _response(200, list_submissions(github_token(),
                                                   qs.get("author")))
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        payload = json.loads(raw)
        problems = validate_submission(payload)
        if problems:
            return _response(400, {"problems": problems})
        return _response(201, open_pull_request(github_token(), payload))
    except SubmitError as e:
        return _response(e.code, {"problems": [e.message]})
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400] if hasattr(e, "read") else ""
        return _response(502, {"problems": [
            f"GitHub refused the request ({e.code}). {detail}"]})
    except json.JSONDecodeError:
        return _response(400, {"problems": ["request body is not JSON"]})
    except Exception as e:  # pragma: no cover — last resort, never a stack
        return _response(500, {"problems": [f"submission service error: {e}"]})
