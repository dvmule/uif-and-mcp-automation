"""Unit tests for infrastructure/deploy/required-actions.py.

This exists because a per-stack deploy role was created holding only the
additive apicurio policy, and the bootstrap's six hand-picked simulate checks
all passed -- they were drawn from that same policy. A verification built from
the thing it verifies confirms nothing, and the role could not have applied the
stack.

So the action list is derived from the Terraform, and the property that keeps it
honest is that an UNMAPPED RESOURCE TYPE IS A HARD FAILURE. Most of what is
tested here is that refusal, in both directions: a type the Terraform declares
and the map does not know, and a map entry matching nothing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = STACK_ROOT / "infrastructure" / "deploy"
SCRIPT = DEPLOY_DIR / "required-actions.py"
MAP_FILE = DEPLOY_DIR / "terraform-iam-map.json"
TF_DIR = STACK_ROOT / "infrastructure" / "terraform"

PY = shutil.which("python3")
pytestmark = pytest.mark.skipif(PY is None, reason="python3 not on PATH")

DEV_ACCOUNT = "283556840805"


def _run(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [PY, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd or DEPLOY_DIR
    )


def _policy(env: str = "uat", account: str = DEV_ACCOUNT) -> dict:
    r = _run("--mode", "policy", "--env", env, "--account", account)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _actions(env: str = "uat", account: str = DEV_ACCOUNT) -> list[tuple[str, str]]:
    r = _run("--mode", "actions", "--env", env, "--account", account)
    assert r.returncode == 0, r.stderr
    return [tuple(line.split("\t")) for line in r.stdout.splitlines() if line.strip()]


@pytest.fixture
def sandbox(tmp_path):
    """A copy of the deploy dir and the terraform, so a test can mutate either."""
    d = tmp_path / "infrastructure" / "deploy"
    d.mkdir(parents=True)
    shutil.copy(SCRIPT, d / SCRIPT.name)
    shutil.copy(MAP_FILE, d / MAP_FILE.name)
    tf = tmp_path / "infrastructure" / "terraform"
    tf.mkdir(parents=True)
    for f in TF_DIR.glob("*.tf"):
        shutil.copy(f, tf / f.name)
    return d, tf


# ── The staleness guard, which is the point ────────────────────────────────────


def test_refuses_a_resource_type_the_map_does_not_know(sandbox):
    d, tf = sandbox
    (tf / "extra.tf").write_text(
        'resource "aws_dynamodb_table" "new_thing" {\n  name = "x"\n}\n', newline="\n"
    )
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert r.returncode != 0
    assert "does not map" in r.stderr
    assert "aws_dynamodb_table" in r.stderr
    assert r.stdout.strip() == "", "no policy may be emitted when a type is unmapped"


def test_the_refusal_says_why_it_matters(sandbox):
    d, tf = sandbox
    (tf / "extra.tf").write_text('resource "aws_sqs_queue" "q" {}\n', newline="\n")
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert "cannot apply the stack" in r.stderr


def test_refuses_an_unmapped_data_source(sandbox):
    d, tf = sandbox
    (tf / "extra.tf").write_text('data "aws_availability_zones" "az" {}\n', newline="\n")
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert r.returncode != 0
    assert "aws_availability_zones" in r.stderr


def test_warns_when_a_map_entry_matches_nothing(sandbox):
    # Quieter than the other direction, but still a wrong policy: a grant for a
    # resource the stack no longer creates.
    d, tf = sandbox
    spec = json.loads((d / MAP_FILE.name).read_text())
    spec["resources"]["aws_kinesis_stream"] = {
        "scope": "_in_delta", "actions": [], "unscopable_actions": []
    }
    (d / MAP_FILE.name).write_text(json.dumps(spec, indent=2))
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert r.returncode == 0, "a stale entry is a warning, not a refusal"
    assert "matches nothing" in r.stderr
    assert "aws_kinesis_stream" in r.stderr


def test_refuses_a_scope_token_that_is_not_defined(sandbox):
    d, _ = sandbox
    spec = json.loads((d / MAP_FILE.name).read_text())
    spec["resources"]["aws_lambda_function"]["scope"] = "no_such_scope"
    (d / MAP_FILE.name).write_text(json.dumps(spec, indent=2))
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert r.returncode != 0
    assert "_scopes does not define" in r.stderr


def test_refuses_unscopable_actions_with_nowhere_to_put_them(sandbox):
    # Caught a real inconsistency in the map when it was first written.
    d, _ = sandbox
    spec = json.loads((d / MAP_FILE.name).read_text())
    spec["resources"]["aws_lambda_function"]["unscopable_actions"] = ["lambda:ListFunctions"]
    (d / MAP_FILE.name).write_text(json.dumps(spec, indent=2))
    r = subprocess.run(
        [PY, str(d / SCRIPT.name), "--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT],
        capture_output=True, text=True, cwd=d,
    )
    assert r.returncode != 0
    assert "no extra_scope" in r.stderr


# ── What it produces ───────────────────────────────────────────────────────────


def test_every_terraform_type_is_mapped_today():
    # The guard as an assertion about the current tree, so this fails when
    # someone adds a resource without a permissions decision.
    r = _run("--mode", "policy", "--env", "uat", "--account", DEV_ACCOUNT)
    assert r.returncode == 0, r.stderr
    assert "does not map" not in r.stderr
    assert "matches nothing" not in r.stderr, "the map carries an entry for nothing"


def test_grants_the_services_the_delta_omits():
    # The specific gap that caused this: a role with only the delta cannot
    # create a security group, a load balancer, a lambda, an api or a log group.
    services = {a.split(":")[0] for a, _ in _actions()}
    omitted = (
        "ec2", "elasticloadbalancing", "lambda",
        "apigateway", "cloudfront", "logs", "iam",
    )
    for needed in omitted:
        assert needed in services, f"{needed} missing -- the apply would 403 on it"


def test_grants_the_two_actions_that_create_a_security_group_rule():
    # aws_vpc_security_group_ingress_rule calls Authorize, not Modify. The delta
    # grants only ModifySecurityGroupRules, which is why the apply would have
    # failed on the Echo-owned endpoint group.
    actions = {a for a, _ in _actions()}
    assert "ec2:AuthorizeSecurityGroupIngress" in actions
    assert "ec2:CreateSecurityGroup" in actions


def test_scopes_by_env_where_scopable():
    uat = {arn for _, arn in _actions("uat")}
    assert any("uif-apicurio-uat" in a for a in uat)
    assert not any("uif-apicurio-dev" in a for a in uat)


def test_prod_uses_the_prod_account():
    prod = {arn for _, arn in _actions("prod", "216064088366")}
    assert not any(DEV_ACCOUNT in a for a in prod)
    assert any("216064088366" in a for a in prod)


def test_the_policy_fits_iams_managed_policy_limit():
    # 6144 characters, whitespace excluded. It sits close enough that this is
    # worth asserting: the escape hatch is a second policy, as echo has.
    doc = _policy()
    size = len(json.dumps(doc, separators=(",", ":")))
    assert size <= 6144, (
        f"the base policy is {size} characters, over IAM's 6144 limit. Split it "
        "into two managed policies, as echo-dev-deploy-1/2 already are."
    )


def test_every_statement_has_a_sid_and_a_resource():
    for st in _policy()["Statement"]:
        assert st.get("Sid"), "a Sid makes a CloudTrail denial traceable to the map entry"
        assert st["Action"], st["Sid"]
        assert st["Resource"], st["Sid"]


def test_the_committed_uat_policy_matches_a_fresh_derivation():
    committed = json.loads(
        (STACK_ROOT.parents[1] / "capabilities" / "infrastructure"
         / "uif-apicurio-uat-base.policy.json").read_text()
    )
    assert committed == _policy("uat", DEV_ACCOUNT), (
        "the committed base policy is stale; regenerate it with "
        "required-actions.py --mode policy"
    )


def test_actions_mode_emits_tab_separated_pairs():
    pairs = _actions()
    assert len(pairs) > 100, f"only {len(pairs)} actions derived"
    for action, arns in pairs:
        assert ":" in action and not action.startswith("arn:")
        for arn in arns.split(","):
            assert arn == "*" or arn.startswith("arn:aws:"), arn


def test_each_action_carries_every_arn_in_its_scope():
    """Not just the first, which is what made the first uat run report 11
    false denials.

    An action evaluated against the wrong resource type is an implicitDeny
    however fully it is granted: ecs:CreateService applies to service/*, not
    cluster/*. The caller tries each candidate, so the emitted list has to
    contain all of them.
    """
    by_action = dict(_actions())
    ecs = by_action["ecs:CreateService"].split(",")
    assert any(":service/" in a for a in ecs), "the service ARN must be a candidate"
    assert any(":cluster/" in a for a in ecs)
    assert any(":task-definition/" in a for a in ecs)

    elb = by_action["elasticloadbalancing:CreateTargetGroup"].split(",")
    assert any(":targetgroup/" in a for a in elb), "the targetgroup ARN must be a candidate"
    assert any(":loadbalancer/" in a for a in elb)
