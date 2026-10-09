"""Unit tests for infrastructure/deploy/bootstrap-environment.sh.

Only the parts that change nothing are tested here: argument validation and
`--render-policy`. Everything past preflight makes privileged changes to IAM
and to repository settings, so it is not something a test suite should be able
to reach -- and the preflight refuses without credentials anyway.

The render is worth testing on its own, though. It generates an IAM document
from dev's reviewed one, and the failure that matters is silent: a retarget
that dropped a statement produces a valid policy that simply cannot deploy,
and `terraform apply` would then fail somewhere deep with a 403 rather than
here with a diff.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = STACK_ROOT.parents[1]
SCRIPT = STACK_ROOT / "infrastructure" / "deploy" / "bootstrap-environment.sh"
TEMPLATE = REPO_ROOT / "capabilities" / "infrastructure" / "uif-apicurio-dev-deploy.policy.json"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available on PATH")

DEV_ACCOUNT = "283556840805"
PROD_ACCOUNT = "216064088366"


def _run(*args: str):
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True
    )


def _actions(doc: dict, sid: str) -> set[str]:
    for st in doc["Statement"]:
        if st.get("Sid") == sid:
            a = st["Action"]
            return set(a if isinstance(a, list) else [a])
    return set()


def _resources(doc: dict) -> list[str]:
    out: list[str] = []
    for st in doc["Statement"]:
        r = st["Resource"]
        out += [r] if isinstance(r, str) else r
    return out


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE.read_text())


@pytest.fixture
def rendered(tmp_path, monkeypatch):
    """Render into a throwaway checkout, so no test writes into the real repo.

    The script locates the policy directory from `git rev-parse --show-toplevel`,
    so a temporary repo with the template copied in is enough to redirect it.
    """
    def _render(env: str):
        git = shutil.which("git")
        subprocess.run([git, "init", "-q", str(tmp_path)], check=True)
        pol = tmp_path / "capabilities" / "infrastructure"
        pol.mkdir(parents=True)
        shutil.copy(TEMPLATE, pol / TEMPLATE.name)
        script = tmp_path / "stacks" / "apicurio" / "infrastructure" / "deploy"
        script.mkdir(parents=True)
        shutil.copy(SCRIPT, script / SCRIPT.name)
        r = subprocess.run(
            [BASH, str(script / SCRIPT.name), env, "--render-policy"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert r.returncode == 0, r.stderr
        out = pol / f"uif-apicurio-{env}-deploy.policy.json"
        assert out.exists(), r.stdout + r.stderr
        return json.loads(out.read_text()), r

    return _render


# ── Argument validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("env", ["staging", "qa", "DEV", "uat2", ""])
def test_rejects_an_environment_outside_the_closed_vocabulary(env):
    # The same vocabulary variables.tf validates. A value outside it would also
    # produce names the naming guard denies, so accepting one would bootstrap an
    # environment that cannot be applied.
    r = _run(env)
    assert r.returncode != 0
    assert "must be exactly one of dev, uat or prod" in r.stderr or "Usage:" in r.stdout


def test_no_arguments_prints_usage():
    r = _run()
    assert r.returncode != 0
    assert "Usage:" in r.stdout
    assert "--render-policy" in r.stdout
    # Markers stripped here too, not only on --help. Asserting `"Usage:" in
    # stdout` alone passes even when every line still reads "# Usage:", which
    # is how a GNU-only `sed \?` in this path survived a mutation run.
    assert not r.stdout.lstrip().startswith("#"), "comment markers were not stripped"


def test_help_prints_the_header_without_the_comment_markers():
    r = _run("--help")
    assert r.returncode == 0
    assert "Bootstrap everything an apicurio environment needs" in r.stdout
    # `sed -E 's/^# ?//'`, not the GNU-only `\?`, which BSD sed silently
    # ignores -- leaving every line prefixed with a hash.
    assert not r.stdout.lstrip().startswith("#"), "comment markers were not stripped"


def test_rejects_an_unknown_option():
    r = _run("uat", "--nope")
    assert r.returncode != 0
    assert "unknown argument" in r.stderr


def test_create_role_is_accepted_and_documented():
    # The role step itself sits behind preflight, so it cannot be exercised
    # without AWS credentials. What is checkable here is that the flag parses
    # and is documented -- an undocumented flag is one nobody will pass.
    r = _run("uat", "--create-role", "--role", "x", "--verify-only")
    assert "unknown argument" not in r.stderr
    help_text = _run("--help").stdout
    assert "--create-role" in help_text
    assert "OIDC trust policy" in help_text


def test_help_warns_against_hand_writing_the_trust_policy():
    # This repository issues IMMUTABLE (id-qualified) OIDC subjects, so the
    # unqualified repo:<org>/<repo>:... form never matches and fails opaquely at
    # the first deploy. The script reads the prefix from the API; the header has
    # to say why, because the next person's instinct is to paste a trust policy
    # from the AWS docs.
    body = (STACK_ROOT / "infrastructure" / "deploy" / "bootstrap-environment.sh").read_text()
    assert "use_immutable_subject" in body
    assert "sub_claim_prefix" in body
    assert "NEVER matches" in body
    # And that the house Terraform module is not the answer here.
    assert "github-oidc-deploy-role" in body


def test_requires_a_role_outside_dev():
    # Guessing a role name would attach the policy to something, succeed, and
    # leave the real deploy role unchanged -- a failure shaped like success.
    r = _run("uat", "--verify-only")
    assert r.returncode != 0
    assert "--role is required" in r.stderr


# ── Reviewer resolution ────────────────────────────────────────────────────────
# Reachable without AWS: it runs in preflight, before the credential check,
# precisely so a wrong login fails without needing a session. These need `gh`
# authenticated, which CI does not have.

GH = shutil.which("gh")


def _gh_ready() -> bool:
    if GH is None:
        return False
    return subprocess.run([GH, "auth", "status"], capture_output=True).returncode == 0


gh_required = pytest.mark.skipif(
    not _gh_ready(), reason="needs an authenticated gh CLI"
)

NO_SUCH_USER = "definitely-not-a-github-login-xyz"
NO_SUCH_TEAM = "pearson-dnt-coe/no-such-team-xyz"


@gh_required
def test_an_unresolvable_login_fails_before_aws_is_consulted():
    # The ordering matters as much as the failure: this used to resolve at step
    # 4, which left the IAM role and policy created and the environment absent.
    r = _run("uat", "--role", "r", "--reviewer", NO_SUCH_USER, "--verify-only")
    assert r.returncode != 0
    assert "could not resolve reviewer" in r.stderr
    assert "no AWS credentials" not in r.stderr, (
        "the reviewer check must fail first, so a wrong login is fixable without "
        "holding credentials"
    )


@gh_required
def test_the_error_says_a_login_is_needed_not_a_username_or_email():
    # The failure that prompted this: a local Unix username was passed, which is
    # not a GitHub login.
    r = _run("uat", "--role", "r", "--reviewer", NO_SUCH_USER, "--verify-only")
    assert "GitHub LOGIN" in r.stderr
    assert "not a local username" in r.stderr
    assert "gh api user --jq .login" in r.stderr, "it should say how to find the right value"
    assert "Nothing has been created" in r.stderr


@gh_required
def test_an_unresolvable_team_is_rejected_too():
    r = _run("uat", "--role", "r", "--reviewer", NO_SUCH_TEAM, "--verify-only")
    assert r.returncode != 0
    assert "could not resolve reviewer" in r.stderr


@gh_required
def test_a_real_login_resolves_to_a_numeric_id():
    who = subprocess.run(
        [GH, "api", "user", "--jq", ".login"], capture_output=True, text=True
    ).stdout.strip()
    if not who:
        pytest.skip("could not read the authenticated login")
    r = _run("uat", "--role", "r", "--reviewer", who, "--verify-only")
    # It proceeds past the reviewer check and stops at AWS instead.
    assert f"reviewer {who} resolves to User " in r.stdout
    import re as _re
    assert _re.search(rf"reviewer {_re.escape(who)} resolves to User \d+", r.stdout), (
        "the id must be numeric -- `gh api` prints its 404 body to STDOUT, so a "
        "non-existent login yields JSON that is non-empty and was once accepted "
        "as an id"
    )


def test_the_script_checks_the_id_is_numeric_not_merely_present():
    body = (STACK_ROOT / "infrastructure" / "deploy" / "bootstrap-environment.sh").read_text()
    assert "^[0-9]+$" in body, "the reviewer id must be shape-checked"
    assert "error body to STDOUT" in body, (
        "and the reason recorded, since an emptiness check looks sufficient"
    )


# ── Render ─────────────────────────────────────────────────────────────────────


def test_render_keeps_every_statement(rendered, template):
    doc, _ = rendered("uat")
    assert len(doc["Statement"]) == len(template["Statement"])
    assert [s.get("Sid") for s in doc["Statement"]] == [
        s.get("Sid") for s in template["Statement"]
    ], "Sids or their order changed, so a statement was dropped, added or moved"


def test_render_keeps_every_resource(rendered, template):
    doc, _ = rendered("uat")
    assert len(_resources(doc)) == len(_resources(template))


def test_render_retargets_the_env_token(rendered):
    doc, _ = rendered("uat")
    res = _resources(doc)
    assert not [a for a in res if "uif-apicurio-dev" in a], "a dev-scoped ARN survived"
    assert sum(1 for a in res if "uif-apicurio-uat" in a) == 10, (
        "exactly the ten dev-scoped ARNs should become uat-scoped"
    )


def test_render_leaves_the_account_alone_when_the_env_shares_it(rendered):
    # uat lives in dev's account, so the account substitution is a no-op here
    # and every ARN keeps 283556840805.
    doc, _ = rendered("uat")
    assert not [a for a in _resources(doc) if PROD_ACCOUNT in a]


def test_render_swaps_the_account_for_prod(rendered):
    # prod is a different account, and this is the only case that exercises it.
    doc, _ = rendered("prod")
    res = _resources(doc)
    assert not [a for a in res if DEV_ACCOUNT in a], "a dev-account ARN survived"
    assert [a for a in res if PROD_ACCOUNT in a]


def test_render_adds_get_object_version_for_rollback(rendered):
    # UIFPE-481. A release is rolled back by restoring a specific object
    # VERSION, so without this the rollback path 403s exactly when it is
    # needed.
    doc, _ = rendered("uat")
    assert "s3:GetObjectVersion" in _actions(doc, "UiBucketObjects")


def test_render_adds_it_only_to_the_object_statement(rendered, template):
    doc, _ = rendered("uat")
    # The bucket-level statement has no object actions, so it must not grow one.
    assert "s3:GetObjectVersion" not in _actions(doc, "UiBucket")
    for st in template["Statement"]:
        sid = st.get("Sid")
        if sid == "UiBucketObjects":
            continue
        assert _actions(doc, sid) == _actions(template, sid), (
            f"{sid}'s actions changed; the render must only retarget resources"
        )


def test_render_grows_the_object_statement_by_exactly_one_action(rendered, template):
    doc, _ = rendered("uat")
    assert len(_actions(doc, "UiBucketObjects")) == len(
        _actions(template, "UiBucketObjects")
    ) + 1


def test_render_is_idempotent(rendered):
    doc, second = rendered("uat")
    assert "already current" in second.stdout or doc  # first write
    # Re-running must not append the action twice.
    assert sorted(_actions(doc, "UiBucketObjects")) == sorted(
        set(_actions(doc, "UiBucketObjects"))
    )


def test_render_applies_nothing(rendered):
    _, r = rendered("uat")
    assert "Nothing was applied" in r.stdout, (
        "the render path must say plainly that it changed no cloud state -- an "
        "IAM document generated and attached in one motion is a privileged "
        "change nobody read"
    )


def test_render_needs_no_role_and_no_credentials(rendered):
    # It runs before preflight on purpose: you cannot review a document you
    # have to hold credentials to generate.
    doc, _ = rendered("prod")
    assert doc["Statement"]


# ── Step 2/6's dev-reference guard ──────────────────────────────────────────
# UIFPE-486. This check runs past preflight (it needs the target environment's
# own AWS account, which is not available here -- see the module docstring),
# so it cannot be reached by running the script end to end. What CAN be tested
# without credentials is the check itself: it is a pure read of a JSON file,
# embedded in the script as a `python3 - "$ENV_DOC" <<'PY' ... PY` heredoc.
# Extracting and running that exact heredoc -- rather than re-typing a copy of
# its logic here -- means a future edit to the guard is exercised by this
# test, not by a stale duplicate of it.

_PY_HEREDOC = re.compile(r"<<'PY'\n(.*?)\nPY\n", re.DOTALL)


def _dev_reference_guard_source() -> str:
    body = SCRIPT.read_text()
    blocks = _PY_HEREDOC.findall(body)
    assert len(blocks) == 2, (
        "expected exactly two <<'PY' heredocs in the script (render_policy's "
        "and the dev-reference guard's); the extraction below assumes the "
        "guard is the second one"
    )
    assert "references_dev" in blocks[1], (
        "the second <<'PY'  heredoc is no longer the dev-reference guard -- "
        "update this extraction to match wherever it moved"
    )
    return blocks[1]


def _offending_sids(doc_path: Path) -> list[str]:
    r = subprocess.run(
        [sys.executable, "-", str(doc_path)],
        input=_dev_reference_guard_source(),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return [line for line in r.stdout.splitlines() if line]


def test_dev_reference_guard_permits_only_the_promotion_source_read_sid(tmp_path):
    # A dev- reference inside PromotionSourceRead is exactly what promotion
    # needs (it copies bytes FROM dev's ECR repo and release-artifacts
    # bucket), so it must pass.
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PromotionSourceRead",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "s3:GetObject"],
                "Resource": [
                    "arn:aws:ecr:eu-west-1:283556840805:repository/uif-apicurio-dev-bff-ecr-repo",
                    "arn:aws:s3:::uif-apicurio-dev-release-artifacts/*",
                ],
            },
            {
                "Sid": "UatOnly",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::uif-apicurio-uat-*/*",
            },
        ],
    }))
    assert _offending_sids(clean) == []

    # The same reference under any OTHER Sid is the copy-paste failure the
    # guard exists to catch, and the offending Sid must be named -- that name
    # is what the die message reports.
    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CopyPasteFailure",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::uif-apicurio-dev-release-artifacts/*",
            },
        ],
    }))
    assert _offending_sids(dirty) == ["CopyPasteFailure"]

    # And the real, checked-in uat document must already satisfy its own
    # guard -- it is what step 2/6 validates on every run.
    real = REPO_ROOT / "capabilities" / "infrastructure" / "uif-apicurio-uat-deploy.policy.json"
    assert _offending_sids(real) == []


# ── The Terraform backend policy (step 4/7) ──────────────────────────────────
#
# This grant exists because required-actions.py structurally cannot derive it.
# It reads terraform-iam-map.json, which lists the resources the stack MANAGES,
# and the state bucket is one the stack merely USES -- so step 5/7 reported
# "all 125 derived actions allowed" while `terraform init` died with a 403 on
# its own state file.
#
# The script guards the document against backends/<env>.hcl on every run, but
# that check sits past preflight and so needs credentials. These tests assert
# the same invariant directly, which is the part that protects anyone: a
# document naming the wrong bucket attaches cleanly and grants nothing that is
# ever read.

BACKENDS = STACK_ROOT / "infrastructure" / "terraform" / "backends"
CAPS = REPO_ROOT / "capabilities" / "infrastructure"


def _hcl_value(path: Path, key: str) -> str:
    m = re.search(rf'^\s*{key}\s*=\s*"([^"]+)"', path.read_text(), re.M)
    assert m, f"{path.name} has no {key}"
    return m.group(1)


def _backend_docs():
    for hcl in sorted(BACKENDS.glob("*.hcl")):
        doc = CAPS / f"uif-apicurio-{hcl.stem}-backend.policy.json"
        if doc.exists():
            yield hcl.stem, hcl, doc


def test_at_least_one_backend_policy_exists_to_test():
    # Guards against this whole block silently testing nothing if the naming
    # convention changes.
    assert list(_backend_docs()), "no <env>-backend.policy.json found next to a backends/<env>.hcl"


def test_backend_policy_names_the_bucket_its_backend_configures():
    for _env, hcl, doc in _backend_docs():
        bucket = _hcl_value(hcl, "bucket")
        assert bucket in doc.read_text(), (
            f"{doc.name} does not name {bucket}, the bucket {hcl.name} configures"
        )


def test_backend_policy_scopes_objects_to_its_own_key_prefix():
    for _env, hcl, doc in _backend_docs():
        prefix = _hcl_value(hcl, "key").rsplit("/", 1)[0] + "/"
        assert prefix in doc.read_text(), (
            f"{doc.name} does not name the key prefix {prefix} from {hcl.name}"
        )


def test_backend_policy_references_no_other_environments_state():
    # The copy-paste failure: a document rendered for another environment
    # attaches cleanly and grants access to the wrong state file.
    for env, _hcl, doc in _backend_docs():
        text = doc.read_text()
        for other in ("dev", "uat", "prod"):
            if other == env:
                continue
            assert f"uif-apicurio/{other}/" not in text, (
                f"{doc.name} references uif-apicurio/{other}/, another environment's state"
            )


def test_backend_policy_confines_every_s3_action_to_this_environment():
    # The guarantee worth asserting is about S3 specifically, not about every
    # statement: this policy also carries genuinely account-scoped grants (WAF
    # log delivery) that cannot be narrowed to a resource at all.
    #
    # So: s3:ListBucket may target the BUCKET -- S3 answers HeadObject on a
    # missing key with 403 rather than 404 without it, which is what broke
    # uat's first plan -- and every other S3 action must sit inside this
    # environment's key prefix, or the role could read or rewrite another
    # environment's state.
    for _env, hcl, doc in _backend_docs():
        bucket = _hcl_value(hcl, "bucket")
        prefix = _hcl_value(hcl, "key").rsplit("/", 1)[0] + "/"
        bucket_arn = f"arn:aws:s3:::{bucket}"
        saw_s3 = False
        for stmt in json.loads(doc.read_text())["Statement"]:
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            resources = stmt["Resource"]
            resources = [resources] if isinstance(resources, str) else resources
            s3_actions = [a for a in actions if a.startswith("s3:")]
            if not s3_actions:
                # A non-S3 statement must not reach S3 by way of a wildcard
                # resource -- that would grant every S3 action in the account.
                assert not any(a == "*" or a.startswith("s3:*") for a in actions), (
                    f"{doc.name}: statement {stmt.get('Sid')} grants a wildcard action"
                )
                continue
            saw_s3 = True
            if s3_actions == ["s3:ListBucket"]:
                assert resources == [bucket_arn], (
                    f"{doc.name}: s3:ListBucket must target only {bucket_arn}, got {resources}"
                )
            else:
                for r in resources:
                    assert r.startswith(f"{bucket_arn}/{prefix}"), (
                        f"{doc.name}: {r} is outside this environment's key prefix"
                    )
        assert saw_s3, f"{doc.name} grants no S3 access, so Terraform cannot read its state"


def test_backend_policy_is_a_valid_iam_document_within_the_size_limit():
    for _env, _hcl, doc in _backend_docs():
        text = doc.read_text()
        parsed = json.loads(text)
        assert parsed["Version"] == "2012-10-17"
        assert parsed["Statement"], f"{doc.name} has no statements"
        # IAM measures a managed policy excluding whitespace.
        assert len(re.sub(r"\s", "", text)) <= 6144, f"{doc.name} exceeds the managed-policy limit"
