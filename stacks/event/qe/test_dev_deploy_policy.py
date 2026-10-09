"""dev needs a derived deploy policy too, and bootstrap-environment.sh now renders one.

WHY THIS EXISTS. UIFPE-744 moved the event stack's Lambdas and Glue scripts into
its own bucket, uif-event-dev-release-artifacts. The dev path was left alone on
the assumption the shared OIDC role already allowed whatever an apply needs --
bootstrap-environment.sh said so in as many words, refusing `dev` with "dev needs
no bootstrap". That assumption held right up until the stack owned a bucket.

The first real apply died at its first step:

    AccessDenied ... role/echo-dev-github-actions is not authorized to perform:
    s3:CreateBucket on "arn:aws:s3:::uif-event-dev-release-artifacts"

simulate-principal-policy then showed every bucket-level action DENIED for
uif-event-dev-* and ALLOWED for uif-echo-dev-*: the actions exist on the shared
role, scoped to echo's names. apicurio had already solved this on the same role
(uif-apicurio-dev-deploy-policy is attached to it), so dev takes the same
derived-policy treatment as uat.

These tests pin the three things that make dev different from uat, because each
one is a plausible thing for a later edit to "tidy" back:

  1. `dev` is accepted at all;
  2. it attaches to the shared role, which is NOT named for an environment;
  3. it owns no state bucket, so neither the step nor the Backend grants apply.

The uat side is already guarded by test_uat_deployment.py --
test_uat_policy_carries_the_three_non_derivable_grants asserts uat KEEPS the
Backend grants, which is what stops (3) from being implemented as "drop them".
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

STACK = Path(__file__).resolve().parent.parent
DEPLOY = STACK / "infrastructure" / "deploy"
ACCOUNT, REGION = "283556840805", "eu-west-1"

#: The bucket whose absence of grants broke the first apply.
RELEASE_BUCKET = "uif-event-dev-release-artifacts"
#: The shared OIDC role every stack's dev deploy assumes.
SHARED_DEV_ROLE = "echo-dev-github-actions"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load_required_actions():
    spec = importlib.util.spec_from_file_location("required_actions_dev",
                                                  DEPLOY / "required-actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RA = _load_required_actions()
BOOTSTRAP = _read(DEPLOY / "bootstrap-environment.sh")


# ── the script accepts dev, and attaches to the right role ──────────────────

def test_dev_is_accepted_rather_than_refused():
    assert "dev|uat) ;;" in BOOTSTRAP, "the env case no longer admits dev"
    assert 'dev needs no bootstrap' not in BOOTSTRAP.split("case \"$ENV_NAME\" in")[1], \
        "the old refusal is still live in the case statement"


def test_dev_attaches_to_the_shared_role_not_an_env_scoped_one():
    """uif-echo-dev-deploy-role does not exist; the shared OIDC role is the target."""
    assert f'ROLE="{SHARED_DEV_ROLE}"' in BOOTSTRAP
    assert 'ROLE="uif-echo-${ENV_NAME}-deploy-role"' in BOOTSTRAP, \
        "uat must still derive its role from the environment name"


def test_dev_skips_the_state_bucket_step():
    """dev's state lives in the shared estate bucket, which this stack does not own."""
    assert 'step "2/5 state bucket -- skipped"' in BOOTSTRAP
    assert "backends/dev.s3.tfbackend" in BOOTSTRAP, \
        "the skip should say where dev's state actually lives"


# ── the derived dev policy ──────────────────────────────────────────────────

def _dev_statements():
    return RA.statements("dev", ACCOUNT, REGION)


def test_dev_policy_grants_the_action_the_first_apply_died_on():
    stmts = _dev_statements()
    bucket_stmt = next((s for s in stmts if "s3:CreateBucket" in s["Action"]), None)
    assert bucket_stmt is not None, "s3:CreateBucket is absent; the apply would 403 again"
    assert "arn:aws:s3:::uif-event-dev-*" in bucket_stmt["Resource"], \
        f"CreateBucket must be scoped to cover {RELEASE_BUCKET}"


def test_dev_policy_omits_the_backend_grants():
    """Claiming the shared estate bucket from EVENT's policy would duplicate
    another owner's grant and assert this stack owns infrastructure it does not."""
    sids = {s["Sid"] for s in _dev_statements()}
    assert "Backend" not in sids
    assert "BackendState" not in sids
    assert "uif-event-dev-data" not in json.dumps(_dev_statements()), \
        "a bucket that does not exist and is not dev's state bucket"


def test_dev_policy_has_no_promotion_source_because_dev_is_the_source():
    sids = {s["Sid"] for s in _dev_statements()}
    assert "PromotionSource" not in sids


def test_dev_policy_can_write_its_own_release_bucket():
    sids = {s["Sid"]: s for s in _dev_statements()}
    assert f"arn:aws:s3:::{RELEASE_BUCKET}/*" in sids["ReleaseCopy"]["Resource"]


def test_dev_policy_stays_inside_the_event_prefix():
    """A derived policy that reaches outside uif-event-dev-* is a widening of the
    shared role every other stack also uses, so the blast radius is not ours alone.
    The one deliberate exception is echo's gold-artifacts prefix the event-domain
    module uploads into."""
    arns = [a for s in _dev_statements() for a in s["Resource"]]
    bad = [
        a for a in arns
        if a != "*"
        and "uif-event-dev-" not in a
        and not a.startswith("arn:aws:s3:::uif-echo-dev-gold-artifacts")
    ]
    assert bad == [], f"grants outside the event dev prefix: {bad}"


def test_dev_and_uat_policies_both_fit_the_size_ceiling():
    for env in ("dev", "uat"):
        for doc in RA.pack(RA.statements(env, ACCOUNT, REGION)):
            assert len(json.dumps(doc, separators=(",", ":"))) <= 6144, f"{env} policy too large"
