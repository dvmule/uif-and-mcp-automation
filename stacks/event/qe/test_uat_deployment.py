"""The uat deployment path (UIFPE-744; capabilities/delivery/stack-promotion-model.md).

Pins the properties UIFPE-692's acceptance criteria name, so a later edit that
quietly loses one fails here rather than in a deploy window:

  - uat is deployed from a TAG, with the model's latch EVENT_UAT_INFRA
    (skip|plan|apply|bootstrap), unset or unrecognised resolving to skip;
  - plan and apply stay in ONE job and the saved plan is never uploaded
    (stack-promotion-model.md section 3; UIFPE-692 asked for a split, the
    model and echo's working converge do not);
  - the deploy policy is DERIVED from the Terraform, covers every resource
    type the root and the event-domain module declare, stays inside the
    event prefix, and includes the promotion-source read the derivation
    cannot see.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import yaml

STACK = Path(__file__).resolve().parent.parent
REPO = STACK.parent.parent
TF = STACK / "infrastructure" / "terraform"
DEPLOY = STACK / "infrastructure" / "deploy"
WF = REPO / ".github" / "workflows"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load_required_actions():
    spec = importlib.util.spec_from_file_location("required_actions", DEPLOY / "required-actions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RA = _load_required_actions()
INFRA = yaml.safe_load(_read(WF / "deploy-event-infra.yml"))
# PyYAML reads the bare key `on` as True.
INFRA_ON = INFRA.get("on", INFRA.get(True))


# ── uat files ───────────────────────────────────────────────────────────────

def test_uat_tfvars_and_backend_exist_where_both_workflows_look():
    assert (TF / "envs" / "uat.tfvars").is_file()
    backend = _read(TF / "backends" / "uat.s3.tfbackend")
    assert 'bucket       = "uif-event-uat-data"' in backend, "uat state lives in the event stack's own uat bucket"
    assert "use_lockfile = true" in backend


def test_uat_tfvars_reads_uat_gold_buckets_and_enables_the_trigger():
    tfvars = _read(TF / "envs" / "uat.tfvars")
    assert 'environment           = "uat"' in tfvars
    assert "uif-echo-uat-gold" in tfvars and "uif-echo-dev" not in tfvars
    assert "enable_gold_merge_trigger = true" in tfvars


def test_no_import_workaround_the_release_bucket_is_created_by_bootstrap_mode():
    body = _read(TF / "release.tf")
    assert "import {" not in body, "the model's bootstrap mode creates the bucket; no import blocks"


# ── The workflow: the stack promotion model, section 3 and 5 ─────────────────

WF_TEXT = _read(WF / "deploy-event-infra.yml")
CONVERGE = INFRA["jobs"]["converge"]
CONVERGE_RUNS = "\n".join(s.get("run", "") for s in CONVERGE["steps"])


def test_triggered_by_a_build_tag_push_or_dispatch():
    assert INFRA_ON["push"] == {"tags": ["event/v*"]}
    assert "workflow_dispatch" in INFRA_ON


def test_latch_is_event_uat_infra_resolved_fail_closed_to_skip():
    mode = next(s for s in INFRA["jobs"]["resolve"]["steps"] if s.get("id") == "mode")
    assert mode["env"]["VAR_UAT"] == "${{ vars.EVENT_UAT_INFRA }}"
    assert '--allowed "skip,plan,apply,bootstrap" --default "skip"' in mode["run"]
    assert "resolve-deploy-mode.sh" in mode["run"]
    assert INFRA["jobs"]["converge"]["if"] == "needs.resolve.outputs.infra != 'skip'"
    assert "EVENT_UAT_DEPLOY_ENABLED" not in WF_TEXT


def test_a_branch_dispatch_is_refused_before_any_environment():
    resolve = INFRA["jobs"]["resolve"]
    assert "environment" not in resolve
    guard = next(s for s in resolve["steps"] if s.get("name") == "Dispatched from an event build tag")
    assert guard["if"] == "steps.mode.outputs.infra != 'skip'"
    assert "refs/tags/event/v*)" in guard["run"] and "exit 1" in guard["run"]


def test_plan_and_apply_stay_in_one_job_and_the_plan_is_never_uploaded():
    assert set(INFRA["jobs"]) == {"resolve", "converge"}
    assert "terraform plan -input=false" in CONVERGE_RUNS
    assert "terraform apply -input=false tf.plan" in CONVERGE_RUNS
    assert "upload-artifact" not in WF_TEXT and "download-artifact" not in WF_TEXT
    apply = next(s for s in CONVERGE["steps"] if s.get("name") == "Apply")
    assert apply["if"] == "env.INFRA == 'apply' || env.INFRA == 'bootstrap'"


def test_targeted_apply_only_in_bootstrap_mode():
    step = next(s for s in CONVERGE["steps"] if s.get("name") == "Release bucket")["run"]
    bootstrap_branch = step[step.index("bootstrap)"):step.index(";;", step.index("bootstrap)"))]
    assert "-target=aws_s3_bucket.release_artifacts" in bootstrap_branch
    assert WF_TEXT.count("terraform apply -auto-approve") == 1


def test_uses_echos_uat_role_and_proves_it():
    assert WF_TEXT.count("secrets.AWS_ROLE_ARN_ECHO_UAT") == 1
    assert "/uif-echo-${ENV_NAME}-deploy-role/" in WF_TEXT


PROMOTE_TEXT = _read(WF / "promote-event.yml")


def test_promote_names_a_role_secret_that_exists_and_proves_the_same_role():
    """Promote assumed a role nobody had, then checked for a role nobody has.

    It named secrets.AWS_ROLE_ARN_EVENT_UAT, which is set on no environment,
    so configure-aws-credentials received an empty role-to-assume; and it then
    asserted the caller arn contained "uif-event-<env>", which is not what any
    deploy role is called. Fixing either one alone still fails, because the
    secret supplies echo's role and the proof has to accept echo's role.

    Pinned against deploy-event-infra.yml rather than against a literal, so the
    two workflows that converge and promote the same environment cannot drift
    apart over which principal does it.
    """
    for env in ("UAT", "PROD"):
        assert PROMOTE_TEXT.count(f"secrets.AWS_ROLE_ARN_ECHO_{env}") == 1, env
    assert "AWS_ROLE_ARN_EVENT_" not in PROMOTE_TEXT, (
        "a secret set on no environment; an empty role-to-assume fails the step")
    proof = "/uif-echo-${ENV_NAME}-deploy-role/"
    assert proof in PROMOTE_TEXT and proof in WF_TEXT, (
        "promote and converge must prove the same principal")


def test_shares_promote_events_concurrency_group():
    group = "event-deploy-${{ needs.resolve.outputs.environment }}"
    assert CONVERGE["concurrency"]["group"] == group
    assert f"group: {group}" in _read(WF / "promote-event.yml")
    assert CONVERGE["concurrency"]["cancel-in-progress"] is False


def test_every_job_has_a_timeout():
    for name, job in INFRA["jobs"].items():
        assert "timeout-minutes" in job, name


def test_deploy_uat_runbook_has_the_models_recovery_section():
    body = _read(STACK / "DEPLOY-UAT.md")
    assert "## Recovery" in body
    for case in ("Locked state", "Partial apply"):
        assert case in body, case
    assert "## Deviations from the model, recorded" in body


# ── The derived deploy policy ───────────────────────────────────────────────

def test_every_declared_type_is_mapped():
    m = json.loads(_read(DEPLOY / "terraform-iam-map.json"))
    resources, data = RA.tf_types()
    assert resources - set(m["resources"]) == set()
    assert data - set(m["data"]) == set()
    # The domain module's types are in the set: product's uat Terraform uses
    # the same role for its event-domain instance.
    assert {"aws_cloudwatch_event_bus", "aws_s3_object"} <= resources


def test_uat_policy_fits_and_stays_inside_the_event_prefix():
    stmts = RA.statements("uat", "283556840805", "eu-west-1")
    docs = RA.pack(stmts)
    for d in docs:
        assert len(json.dumps(d, separators=(",", ":"))) <= 6144
    arns = [a for s in stmts for a in s["Resource"]]
    allowed = re.compile(
        r"^(\*|arn:aws:[a-z0-9-]+:(eu-west-1)?:(283556840805)?:?.*uif-event-uat-.*"
        r"|arn:aws:s3:::uif-event-(uat|dev)-.*"
        r"|arn:aws:s3:::uif-echo-uat-gold-artifacts(/event-pipeline/\*)?)$")
    bad = [a for a in arns if not allowed.match(a)]
    assert bad == [], f"a deploy grant outside the event prefix: {bad}"
    assert not any("uif-echo-uat-" in a and "gold-artifacts" not in a for a in arns)


def test_uat_policy_carries_the_three_non_derivable_grants():
    sids = {s["Sid"]: s for s in RA.statements("uat", "283556840805", "eu-west-1")}
    assert "arn:aws:s3:::uif-event-uat-data/tfstate/*" in sids["BackendState"]["Resource"]
    assert "arn:aws:s3:::uif-event-uat-release-artifacts/*" in sids["ReleaseCopy"]["Resource"]
    src = sids["PromotionSource"]
    assert "arn:aws:s3:::uif-event-dev-release-artifacts/*" in src["Resource"]
    assert not any(a.startswith("s3:Put") or a.startswith("s3:Delete") for a in src["Action"]), \
        "the promotion source is read-only"


def test_dev_has_no_promotion_source():
    assert "PromotionSource" not in {s["Sid"] for s in RA.statements("dev", "283556840805", "eu-west-1")}


#: How a domain declares a row in one of this stack's config tables. Read by
#: splitting on literals rather than a regex: the table name is a fixed
#: interpolation, and a literal survives review better than an escaped pattern.
ITEM_MARKER = 'resource "aws_dynamodb_table_item"'
TABLE_TOKEN = 'table_name = "${local.event_stack_prefix}-'


def _config_seeds(env="uat"):
    return next(s for s in RA.statements(env, "283556840805", "eu-west-1")
                if s["Sid"] == "ConfigSeeds")


def _tables_domains_seed():
    """Every config table suffix a domain writes rows into."""
    files = sorted((REPO / "domains").glob("*/infrastructure/terraform/*_events.tf"))
    assert files, "no domain event Terraform found; the glob has stopped matching"
    found = set()
    for f in files:
        for chunk in _read(f).split(ITEM_MARKER)[1:]:
            block = []
            for line in chunk.splitlines():
                if line.strip() == "}":
                    break
                block.append(line)
            hit = next((ln for ln in block if TABLE_TOKEN in ln), None)
            assert hit is not None, (
                f"{f.name} declares an aws_dynamodb_table_item whose table_name this "
                "test cannot read. Teach it the new form rather than dropping the case.")
            found.add(hit.split(TABLE_TOKEN, 1)[1].split('"')[0])
    return found


def test_config_seeds_covers_every_table_a_domain_seeds():
    """The grant product's first uat apply died on, pinned to its real source.

    `aws_dynamodb_table_item` is declared in a DOMAIN's Terraform, never in this
    stack's, so required-actions.py cannot derive item-level actions for it: its
    loop sees only the tables this root declares, and produced table-level
    actions alone. ConfigSeeds is therefore written out by hand, which means it
    can fall behind the domains. This reads them and fails if one seeds a table
    the grant does not cover.

    stacks/event/ci-watch lists the path these files live under, so a domain
    change selects event's matrix and this actually runs (UIFPE-728).
    """
    seeded = _tables_domains_seed()
    assert seeded, "no aws_dynamodb_table_item found at all; this test would be vacuous"
    granted = {r.rsplit("/", 1)[-1] for r in _config_seeds()["Resource"]}
    missing = sorted(s for s in seeded if "uif-event-uat-" + s not in granted)
    assert not missing, (
        f"a domain seeds rows into {missing}, which ConfigSeeds does not cover. "
        "Add the table in required-actions.py; no derivation can see these rows.")


def test_config_seeds_reaches_the_config_tables_and_nothing_else():
    cs = _config_seeds()
    assert "dynamodb:PutItem" in cs["Action"], "the action product's first uat apply died on"
    assert all(r.endswith(("-pipeline-configs", "-subscription-configs"))
               for r in cs["Resource"]), (
        "widening this past the config tables would let a deploy role write rows "
        "into a metrics table")


def test_config_seeds_applies_to_dev_too():
    """dev's rows predate this policy, so a dev apply that had to recreate one
    would fail exactly as uat's did. Not a uat-only grant."""
    assert "ConfigSeeds" in {s["Sid"] for s in RA.statements("dev", "283556840805", "eu-west-1")}


def test_bootstrap_extends_echos_uat_role_and_creates_only_the_state_bucket():
    body = _read(DEPLOY / "bootstrap-environment.sh")
    assert 'ROLE="uif-echo-${ENV_NAME}-deploy-role"' in body
    assert 'STATE_BUCKET="uif-event-${ENV_NAME}-data"' in body
    assert "RELEASE_BUCKET" not in body, "the release bucket is Terraform's, created in bootstrap mode"
    assert "attach-role-policy" in body and "create-role" not in body


# ── copy-artifacts.sh is echo's, unchanged below its header ─────────────────

def test_copy_artifacts_is_echos_script_below_the_header():
    ours = _read(STACK / "scripts" / "copy-artifacts.sh")
    theirs = _read(REPO / "stacks" / "echo" / "scripts" / "copy-artifacts.sh")
    cut = "set -euo pipefail\n"
    assert ours[ours.index(cut):] == theirs[theirs.index(cut):]
