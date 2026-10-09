#!/usr/bin/env bash
# Bootstrap everything an apicurio environment needs BEFORE it can be
# provisioned or promoted to.
#
# Run this by hand, once per environment, before the first `terraform apply`
# for that environment and before any workflow names it. Nothing in CI calls
# it: every action here is a one-time privileged change (IAM, repository
# settings) that should be made deliberately by a human who can read the
# result.
#
# WHAT IT FULFILS, and why each item blocks an environment rather than merely
# improving it:
#
#   1. The IAM deploy policy, env-scoped. The dev policy pins 10 of its 18
#      resource ARNs to `uif-apicurio-dev-*`, so an `env=uat` apply 403s on
#      ECR, ECS, IAM, RDS, S3 and both WAF resources -- in the SAME account.
#      Same account does not mean same permissions.
#
#   2. The GitHub Environment, with an approver and a tag policy. Referencing
#      `environment: uat` from a workflow before the environment exists makes
#      GitHub create it implicitly with NO protection: no reviewer, no policy.
#      That is how "restricted release authority" becomes a sentence in a
#      document. Creating it here, first and deliberately, is the only ordering
#      that works.
#
#   3. The deploy role's ARN as a secret, since `AWS_ROLE_ARN_<ENV>` is what
#      every job's OIDC step reads and only `AWS_ROLE_ARN_DEV` exists today.
#
#   4. A report on the Terraform configuration, which it does NOT create. See
#      the Terraform step for why fabricating that file would be worse than
#      leaving it absent.
#
# WHAT IT REFUSES TO INVENT. It will not name a reviewer for you, it will not
# guess an IAM role name outside dev, and it will not write network values.
# Each of those is a decision with an owner, and a bootstrap script that
# silently picks one produces an environment nobody agreed to.
#
# Usage:
#   ./bootstrap-environment.sh <env> [options]
#
#   <env>                     dev | uat | prod
#   --role <name>             IAM role the policy attaches to. Required outside
#                             dev, where it defaults to the shared
#                             echo-dev-github-actions.
#   --reviewer <login|org/team>
#                             Required approver for the environment. Required
#                             outside dev. Repeatable.
#   --role-arn <arn>          Value for the AWS_ROLE_ARN_<ENV> secret. Derived
#                             from --role and the account when omitted.
#   --create-role             Create the IAM role named by --role if it does not
#                             exist, with an OIDC trust policy for this
#                             environment. Off by default: creating a role is a
#                             bigger step than attaching a policy to one.
#   --render-policy           Generate the env's policy document from dev's,
#                             print the diff, and STOP. Nothing is applied.
#   --allow-self-review       Let the named reviewer approve their own dispatch.
#                             Needed when the reviewer list has exactly one
#                             person: prevent_self_review then blocks the only
#                             approver there is, and every deployment queues
#                             forever. The approval click remains either way.
#                             Add a second reviewer and drop this.
#   --dry-run                 Say what would change; change nothing.
#   --verify-only             Check and report current state; change nothing.
#   --rollback                Undo what this script created, in reverse order.
#   -h, --help                This text.
#
# Typical first run for a new environment:
#   ./bootstrap-environment.sh uat --render-policy          # review, then commit
#   ./bootstrap-environment.sh uat --role <r> --reviewer <l> --dry-run
#   ./bootstrap-environment.sh uat --role <r> --reviewer <l>
#
# Exit 0 means the environment is ready for `terraform apply`. Any non-zero
# means nothing downstream should proceed.

set -euo pipefail

# ── Account map ───────────────────────────────────────────────────────────────
# dev and uat share an account; prod does not. Owner decision 2026-09-20,
# matching stacks/echo/infrastructure/terraform/{envs,backends}/, which already
# carry this topology. Hardcoded rather than discovered because the whole point
# of the check below is to catch credentials for the WRONG account, and a value
# read from those same credentials could not.
account_for() {
  case "$1" in
    dev|uat) echo "283556840805" ;;
    prod)    echo "216064088366" ;;
  esac
}

REGION="${AWS_REGION:-eu-west-1}"
REPO_SLUG="${REPO_SLUG:-pearson-dnt-coe/universal-Integration-framework}"

# Build tags only. This is what makes uat and prod tag-based deployments
# enforced by GitHub rather than by a workflow `if:` a dispatch can sidestep.
TAG_POLICY="apicurio/v*"

MODE=run
ENV_NAME=""
ROLE=""
ROLE_ARN=""
CREATE_ROLE=0
# Whether the named reviewer may approve their own dispatch. See step 6/7.
ALLOW_SELF_REVIEW=0
REVIEWERS=()

step() { echo ""; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*" >&2; }
die()  { echo "  ✗ $*" >&2; exit 1; }
would() { echo "  … would $*"; }
note() { echo "    $*"; }

# ── Arguments ─────────────────────────────────────────────────────────────────

[ $# -gt 0 ] || { sed -n '38,63p' "$0" | sed -E 's/^# ?//'; exit 2; }

case "${1:-}" in
  -h|--help) sed -n '2,63p' "$0" | sed -E 's/^# ?//'; exit 0 ;;
esac

ENV_NAME="$1"; shift

# The same closed vocabulary infrastructure/terraform/variables.tf validates
# and validate_naming.py enforces. A value outside it would also produce names
# the naming guard denies, so accepting one here would build an environment
# that cannot be applied.
case "$ENV_NAME" in
  dev|uat|prod) ;;
  *) die "env must be exactly one of dev, uat or prod, got '${ENV_NAME}'" ;;
esac

while [ $# -gt 0 ]; do
  case "$1" in
    --role)           ROLE="${2:?--role needs a value}"; shift 2 ;;
    --role-arn)       ROLE_ARN="${2:?--role-arn needs a value}"; shift 2 ;;
    --reviewer)       REVIEWERS+=("${2:?--reviewer needs a value}"); shift 2 ;;
    --create-role)    CREATE_ROLE=1; shift ;;
    --render-policy)  MODE=render; shift ;;
    --dry-run)        MODE=dry; shift ;;
    --allow-self-review) ALLOW_SELF_REVIEW=1; shift ;;
    --verify-only)    MODE=verify; shift ;;
    --rollback)       MODE=rollback; shift ;;
    -h|--help)        sed -n '2,63p' "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

ACCOUNT="$(account_for "$ENV_NAME")"
ENV_UPPER="$(printf '%s' "$ENV_NAME" | tr '[:lower:]' '[:upper:]')"
POLICY_NAME="uif-apicurio-${ENV_NAME}-deploy-policy"
POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${POLICY_NAME}"
# The delta above is NOT sufficient on its own -- see the base policy step.
BASE_POLICY_NAME="uif-apicurio-${ENV_NAME}-base-policy"
BASE_POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${BASE_POLICY_NAME}"
# A THIRD policy, and the reason it is separate from both of the above.
# Terraform's S3 backend reads its state file before it evaluates anything
# else, and the state bucket is NOT a resource this stack manages -- so
# required-actions.py cannot derive it from the terraform, and the base policy
# (which is compared for exact equality against that derivation) cannot carry
# it. The delta policy could in principle, but it is near the 6144-character
# managed-policy limit. Its own document, tracked and reviewable, is the
# honest home for "what this role needs in order to function" as distinct
# from "what it is allowed to create".
BACKEND_POLICY_NAME="uif-apicurio-${ENV_NAME}-backend-policy"
BACKEND_POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${BACKEND_POLICY_NAME}"
SECRET_NAME="AWS_ROLE_ARN_${ENV_UPPER}"

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
TEMPLATE_DOC="${REPO_ROOT}/capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json"
ENV_DOC="${REPO_ROOT}/capabilities/infrastructure/uif-apicurio-${ENV_NAME}-deploy.policy.json"
BASE_DOC="${REPO_ROOT}/capabilities/infrastructure/uif-apicurio-${ENV_NAME}-base.policy.json"
BACKEND_DOC="${REPO_ROOT}/capabilities/infrastructure/uif-apicurio-${ENV_NAME}-backend.policy.json"
BACKEND_HCL="${REPO_ROOT}/stacks/apicurio/infrastructure/terraform/backends/${ENV_NAME}.hcl"
REQUIRED_ACTIONS="$(dirname "$0")/required-actions.py"
TF_DIR="${REPO_ROOT}/stacks/apicurio/infrastructure/terraform"

# dev's shared role is known; nothing else is. Guessing a role name outside dev
# would attach a policy to something, succeed, and leave the real deploy role
# unchanged -- a failure that looks exactly like success.
if [ -z "$ROLE" ]; then
  if [ "$ENV_NAME" = "dev" ]; then
    ROLE="echo-dev-github-actions"
  elif [ "$MODE" != "render" ]; then
    die "--role is required for ${ENV_NAME}. Only dev's deploy role (echo-dev-github-actions) is known to this repository; naming the wrong one attaches the policy to nothing that deploys."
  fi
fi
[ -n "$ROLE_ARN" ] || ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE}"

echo "apicurio environment bootstrap"
echo "  environment : ${ENV_NAME}"
echo "  account     : ${ACCOUNT}"
echo "  region      : ${REGION}"
echo "  iam role    : ${ROLE:-<unset>}"
echo "  policies    : ${POLICY_NAME} + ${BASE_POLICY_NAME}"
echo "  repository  : ${REPO_SLUG}"
echo "  mode        : ${MODE}"

# ── Render the policy document ────────────────────────────────────────────────
# Rendering is a SEPARATE, explicit step that applies nothing. An IAM document
# generated and attached in one motion is a privileged change no human read.

render_policy() {
  python3 - "$TEMPLATE_DOC" "$ENV_NAME" "$ACCOUNT" <<'PY'
import json, sys

template, env, account = sys.argv[1], sys.argv[2], sys.argv[3]
with open(template) as fh:
    doc = json.load(fh)

src_env, src_account = "dev", "283556840805"
before = json.dumps(doc, sort_keys=True)

def retarget(value):
    # Only these two substitutions. Everything else -- every Sid, action,
    # condition and unscopable "*" resource -- is carried across untouched, so
    # the rendered document differs from the reviewed one in exactly the two
    # dimensions an environment differs in.
    return value.replace(f"uif-apicurio-{src_env}", f"uif-apicurio-{env}").replace(
        src_account, account
    )

def walk(node):
    if isinstance(node, dict):
        return {k: walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [walk(v) for v in node]
    if isinstance(node, str):
        return retarget(node)
    return node

doc = walk(doc)

# s3:GetObjectVersion, added here rather than left for later: a release is
# rolled back by restoring a specific object VERSION from the release-artifacts
# bucket, and without this the rollback path 403s at the moment it is needed
# most. UIFPE-481. Added to the object-level statement only -- the bucket-level
# one has no object actions.
added = []
for st in doc["Statement"]:
    if st.get("Sid") == "UiBucketObjects":
        actions = st["Action"]
        if "s3:GetObjectVersion" not in actions:
            actions.append("s3:GetObjectVersion")
            st["Action"] = sorted(actions)
            added.append("s3:GetObjectVersion -> UiBucketObjects")

# Structural equivalence, checked rather than assumed. A retarget that dropped
# a statement would produce a valid policy that silently cannot deploy.
with open(template) as fh:
    original = json.load(fh)
assert len(doc["Statement"]) == len(original["Statement"]), (
    "statement count changed during render: "
    f"{len(original['Statement'])} -> {len(doc['Statement'])}"
)
sids_before = [s.get("Sid") for s in original["Statement"]]
sids_after = [s.get("Sid") for s in doc["Statement"]]
assert sids_before == sids_after, "statement Sids or their order changed during render"

for a, b in zip(original["Statement"], doc["Statement"]):
    la = a["Action"] if isinstance(a["Action"], list) else [a["Action"]]
    lb = b["Action"] if isinstance(b["Action"], list) else [b["Action"]]
    extra = len(added) if a.get("Sid") == "UiBucketObjects" else 0
    assert len(lb) == len(la) + extra, f"action count changed in {a.get('Sid')}"
    ra = a["Resource"] if isinstance(a["Resource"], list) else [a["Resource"]]
    rb = b["Resource"] if isinstance(b["Resource"], list) else [b["Resource"]]
    assert len(ra) == len(rb), f"resource count changed in {a.get('Sid')}"

if before == json.dumps(doc, sort_keys=True) and env != "dev":
    raise SystemExit(
        "error: rendering produced a document identical to dev's, which means "
        "no substitution took place. Refusing to emit it."
    )

for line in added:
    print(f"# added: {line}", file=sys.stderr)
print(json.dumps(doc, indent=2))
PY
}

if [ "$MODE" = "render" ]; then
  step "Render ${POLICY_NAME} from dev's reviewed document"
  [ -f "$TEMPLATE_DOC" ] || die "template not found at ${TEMPLATE_DOC}"
  rendered="$(render_policy)"
  if [ -f "$ENV_DOC" ]; then
    if printf '%s\n' "$rendered" | diff -u "$ENV_DOC" - >/dev/null; then
      ok "${ENV_DOC##*/} is already current; nothing to write."
      exit 0
    fi
    warn "${ENV_DOC##*/} exists and differs. Diff (existing -> rendered):"
    printf '%s\n' "$rendered" | diff -u "$ENV_DOC" - || true
  fi
  printf '%s\n' "$rendered" > "$ENV_DOC"
  ok "wrote ${ENV_DOC#"$REPO_ROOT"/}"
  echo ""
  echo "Nothing was applied. Read the document, commit it, then re-run without"
  echo "--render-policy to create and attach the managed policy."
  exit 0
fi

# ── Preflight ─────────────────────────────────────────────────────────────────

step "Preflight"
command -v aws >/dev/null || die "aws CLI not found"
command -v gh  >/dev/null || die "gh CLI not found (needed for the GitHub Environment)"
command -v python3 >/dev/null || die "python3 not found"
ok "aws, gh and python3 present"

gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login)"
gh api "repos/${REPO_SLUG}" --jq '.permissions.admin' 2>/dev/null | grep -qx true \
  || die "this token lacks admin on ${REPO_SLUG}; creating an Environment with protection rules needs it. Without admin the API creates the environment BARE, which is worse than not creating it."
ok "gh authenticated with admin on ${REPO_SLUG}"

# REVIEWERS ARE RESOLVED HERE, IN PREFLIGHT, AND IN THE MAIN SHELL. Two
# separate reasons, both learned the hard way on 2026-09-20:
#
#   1. Ordering. This used to resolve at step 4, so an unresolvable login left
#      the role and the policy created and the environment absent -- a half-done
#      bootstrap. Nothing should be created until every input is known good.
#   2. `die` inside a command substitution exits only the SUBSHELL. The lookup
#      used to run as `pair="$(resolve_reviewer "$r")"`, so a failed lookup
#      returned an empty string, the caller built `{"type":"","id":}`, and the
#      run died on a JSON traceback instead of saying "could not resolve
#      'raj9karu'". That is the silent-failure class this script exists to
#      prevent, written into the script.
RESOLVED_REVIEWERS=()
for r in ${REVIEWERS[@]+"${REVIEWERS[@]}"}; do
  rid=""
  if [ "${r}" != "${r#*/}" ]; then
    rtype=Team
    rid="$(gh api "orgs/${r%%/*}/teams/${r##*/}" --jq '.id' 2>/dev/null || true)"
  else
    rtype=User
    rid="$(gh api "users/${r}" --jq '.id' 2>/dev/null || true)"
  fi
  # NUMERIC, not merely non-empty. `gh api` prints its error body to STDOUT,
  # so a 404 puts {"message":"Not Found",...} into rid -- which is non-empty,
  # passed an emptiness check, and was reported as a successful resolution
  # before being spliced into the JSON body as an id. Checking the shape is the
  # only check that distinguishes an id from an error that looks like one.
  if ! printf '%s' "$rid" | grep -Eq '^[0-9]+$'; then
    die "$(printf '%s\n' \
      "could not resolve reviewer '${r}'." \
      "  This needs a GitHub LOGIN or org/team slug -- not a local username and" \
      "  not an email address. Your own login is:" \
      "    gh api user --jq .login" \
      "  Nothing has been created; re-run with the right name.")"
  fi
  ok "reviewer ${r} resolves to ${rtype} ${rid}"
  RESOLVED_REVIEWERS+=("{\"type\":\"${rtype}\",\"id\":${rid}}")
done

# The sharpest guard in the script. Running the uat bootstrap with prod
# credentials would create a uat-named policy in the prod account and attach it
# there, and every command would succeed.
caller_account="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[ -n "$caller_account" ] \
  || die "$(printf '%s\n' \
    "no AWS credentials, and none are configured -- this is not an expired session." \
    "  Run this script THROUGH aws-runas, which wraps the command rather than" \
    "  exporting into your shell:" \
    "    /Users/raj9karu/.aws/aws-runas pearson-esb-dev $(basename "$0") ${ENV_NAME} ..." \
    "  Use that absolute path: /Users/raj9karu/bin/aws-runas is a LINUX binary and" \
    "  fails with 'Exec format error', and the pearson_awsrunas profile in" \
    "  ~/.aws/config resolves aws-runas through PATH so it hits that same decoy." \
    "  \`eval \$(aws-runas -e ...)\` does not work here either: it fails with" \
    "  'Cannot call GetSessionToken with session credentials'." \
    "  Note the local role is PCMPowerUser, which is NOT the CI principal -- so a" \
    "  step succeeding here is not evidence that GitHub Actions can do it. That is" \
    "  what the simulate step below is for.")"
[ "$caller_account" = "$ACCOUNT" ] \
  || die "$(printf '%s\n' \
    "credentials are for account ${caller_account}, but ${ENV_NAME} is ${ACCOUNT}." \
    "  Refusing: every command below would succeed against the wrong account and" \
    "  leave the right one untouched.")"
ok "credentials are for ${ACCOUNT}, which is ${ENV_NAME}'s account"

# An approver is the whole substance of item 2, so its absence stops the run
# rather than degrading it.
if [ "${#REVIEWERS[@]}" -eq 0 ] && [ "$ENV_NAME" != "dev" ] && [ "$MODE" = "run" ]; then
  die "$(printf '%s\n' \
    "--reviewer is required for ${ENV_NAME}." \
    "  An environment with a tag policy and no approver still lets anyone who can" \
    "  dispatch a workflow promote to it. Until a release owner is named, the" \
    "  restriction is a sentence in a document -- which is what UIFPE-479 is about." \
    "  Pass a GitHub login, or org/team for a team.")"
fi

# ── Rollback ──────────────────────────────────────────────────────────────────

if [ "$MODE" = "rollback" ]; then
  step "Rollback: detach and delete ${POLICY_NAME}"
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    if aws iam list-entities-for-policy --policy-arn "$POLICY_ARN" \
        --query 'PolicyRoles[].RoleName' --output text 2>/dev/null | grep -qw "$ROLE"; then
      aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$POLICY_ARN"
      ok "detached from ${ROLE}"
    fi
    # Non-default versions must go before the policy can be deleted.
    # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
    for v in $(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
      aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v"
    done
    aws iam delete-policy --policy-arn "$POLICY_ARN"
    ok "deleted ${POLICY_NAME}"
  else
    ok "${POLICY_NAME} does not exist; nothing to detach or delete"
  fi

  step "Rollback: detach and delete ${BACKEND_POLICY_NAME}"
  if aws iam get-policy --policy-arn "$BACKEND_POLICY_ARN" >/dev/null 2>&1; then
    if aws iam list-entities-for-policy --policy-arn "$BACKEND_POLICY_ARN" \
        --query 'PolicyRoles[].RoleName' --output text 2>/dev/null | grep -qw "$ROLE"; then
      aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$BACKEND_POLICY_ARN"
      ok "detached from ${ROLE}"
    fi
    # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
    for v in $(aws iam list-policy-versions --policy-arn "$BACKEND_POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
      aws iam delete-policy-version --policy-arn "$BACKEND_POLICY_ARN" --version-id "$v"
    done
    aws iam delete-policy --policy-arn "$BACKEND_POLICY_ARN"
    ok "deleted ${BACKEND_POLICY_NAME}"
  else
    ok "${BACKEND_POLICY_NAME} does not exist; nothing to detach or delete"
  fi

  step "Rollback: the GitHub Environment is NOT deleted"
  note "Deleting an environment destroys its deployment history and any secrets"
  note "scoped to it, which is not a safe automatic undo. Remove ${ENV_NAME} by hand"
  note "at https://github.com/${REPO_SLUG}/settings/environments if that is what"
  note "you want."
  exit 0
fi

# ── 1. The deploy role ────────────────────────────────────────────────────────
# A policy attaches to a role, so the role has to exist first. Creating it is
# behind --create-role rather than automatic: attaching a reviewed policy to an
# existing role is a small change, minting a new AWS identity that GitHub can
# assume is not.
#
# THE TRUST POLICY IS WHERE THIS GOES WRONG SILENTLY. This repository issues
# IMMUTABLE (id-qualified) OIDC subjects -- verified 2026-09-20 from
# repos/.../actions/oidc/customization/sub, which returns
# use_immutable_subject: true with
#   sub_claim_prefix = repo:pearson-dnt-coe@183024320/universal-Integration-framework@1308482856
# so every token's `sub` is that prefix plus `:environment:<env>`. The
# unqualified form `repo:<org>/<repo>:environment:<env>` NEVER matches, and the
# failure is an opaque "Not authorized to perform sts:AssumeRoleWithWebIdentity"
# at the first deploy, a long way from the role that caused it.
#
# The prefix is READ FROM THE API rather than hardcoded here, so a repository
# transfer or rename -- which changes neither id -- cannot leave this script
# writing a subject that no longer matches.
#
# NOTE for anyone reaching for the house module instead:
# infrastructure/terraform/modules/github-oidc-deploy-role/ builds
# `repo:${github_org}/${github_repo}:...` with no ids, so a role created from it
# would never match on this repository. That module needs fixing; this script
# does not use it.

step "1/7  Deploy role ${ROLE}"

role_exists() { aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; }

if role_exists; then
  ok "${ROLE} exists"
  # Reported, not enforced: the role may legitimately trust more than this
  # environment (dev's shared role trusts refs/heads/develop as well). Saying
  # what it trusts is useful; refusing on the basis of it would be wrong.
  trusted="$(aws iam get-role --role-name "$ROLE" \
    --query 'Role.AssumeRolePolicyDocument' --output json 2>/dev/null \
    | python3 -c '
import json,sys
try:
    doc = json.load(sys.stdin)
except Exception:
    raise SystemExit("")
subs = []
for st in doc.get("Statement", []):
    cond = st.get("Condition", {})
    for op in cond.values():
        for key, val in op.items():
            if key.endswith(":sub"):
                subs += [val] if isinstance(val, str) else list(val)
print("\n".join(subs))
' || true)"
  if [ -n "$trusted" ]; then
    printf '%s\n' "$trusted" | sed 's/^/    trusts: /'
    if printf '%s\n' "$trusted" | grep -q ":environment:${ENV_NAME}\b"; then
      ok "trust policy admits environment:${ENV_NAME}"
    else
      warn "trust policy does NOT admit environment:${ENV_NAME}; every OIDC step for this environment will fail with 'Not authorized to perform sts:AssumeRoleWithWebIdentity'"
    fi
  else
    warn "could not read ${ROLE}'s trust policy"
  fi
elif [ "$CREATE_ROLE" != "1" ]; then
  die "$(printf '%s\n' \
    "role ${ROLE} does not exist in account ${ACCOUNT}." \
    "  A policy cannot be attached to a role that is not there. Either name an" \
    "  existing role, or pass --create-role to have this script mint it with the" \
    "  correct id-qualified OIDC trust policy for environment:${ENV_NAME}." \
    "  Do NOT hand-write that trust policy: this repository issues immutable" \
    "  subjects, so the unqualified repo:<org>/<repo>:... form silently never" \
    "  matches.")"
elif [ "$MODE" = "verify" ]; then
  warn "${ROLE} does not exist"
elif [ "$MODE" = "dry" ]; then
  would "create role ${ROLE} trusting environment:${ENV_NAME} via id-qualified OIDC subject"
else
  prefix="$(gh api "repos/${REPO_SLUG}/actions/oidc/customization/sub" --jq '.sub_claim_prefix' 2>/dev/null || true)"
  [ -n "$prefix" ] && [ "$prefix" != "null" ] \
    || die "could not read this repository's OIDC sub_claim_prefix from the API, so the trust policy's subject cannot be written correctly. Refusing to guess it -- an unqualified subject produces a role that never matches."
  ok "OIDC subject prefix: ${prefix}"

  provider_arn="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
  aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$provider_arn" >/dev/null 2>&1 \
    || die "the account has no GitHub Actions OIDC provider at ${provider_arn}. It is a one-per-account resource (infrastructure/terraform/modules/github-oidc-provider/); create it before any deploy role."
  ok "account trusts token.actions.githubusercontent.com"

  # environment:<env> only, and deliberately no branch subject. uat and prod are
  # tag-based: a role that also trusted a branch ref would let a push-triggered
  # workflow assume it and bypass the environment's reviewer entirely.
  trust_doc="$(python3 -c '
import json, sys
prefix, env = sys.argv[1], sys.argv[2]
print(json.dumps({
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": sys.argv[3]},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": f"{prefix}:environment:{env}",
      }
    },
  }],
}, indent=2))' "$prefix" "$ENV_NAME" "$provider_arn")"

  printf '%s' "$trust_doc" | aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document file:///dev/stdin \
    --description "Apicurio ${ENV_NAME} deploy role; trusts only environment:${ENV_NAME} on ${REPO_SLUG}" \
    >/dev/null \
    || die "creating role ${ROLE} failed."
  ok "created ${ROLE}, trusting ${prefix}:environment:${ENV_NAME}"

  # Read back: a role that exists with the wrong subject is worse than none,
  # because it fails at the first deploy rather than here.
  aws iam get-role --role-name "$ROLE" --query 'Role.AssumeRolePolicyDocument' --output json \
    | grep -qF ":environment:${ENV_NAME}" \
    || die "${ROLE} was created but its trust policy does not name environment:${ENV_NAME} on read-back."
  ok "read-back confirms the trust subject"
fi

# ── 2. IAM deploy policy ──────────────────────────────────────────────────────

step "2/7  IAM deploy policy ${POLICY_NAME}"

if [ ! -f "$ENV_DOC" ]; then
  die "$(printf '%s\n' \
    "no policy document at ${ENV_DOC#"$REPO_ROOT"/}." \
    "  Generate it, read it, and commit it before anything is applied:" \
    "    $(basename "$0") ${ENV_NAME} --render-policy" \
    "  Refusing to render and attach in one motion: that is a privileged IAM" \
    "  change nobody reviewed.")"
fi
ok "document present: ${ENV_DOC#"$REPO_ROOT"/}"

python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$ENV_DOC" \
  || die "${ENV_DOC##*/} is not valid JSON"

# Catches the copy-paste failure: a document for the wrong environment attaches
# cleanly and grants nothing the target environment needs.
grep -q "uif-apicurio-${ENV_NAME}-" "$ENV_DOC" \
  || die "${ENV_DOC##*/} contains no uif-apicurio-${ENV_NAME}- resource, so it is not this environment's policy"

# A blanket "no uif-apicurio-dev- anywhere" grep would also reject the one
# case that legitimately needs it: promote-apicurio.yml copies the BFF image
# and UI bundle FROM dev's ECR repository and release-artifacts bucket into
# every other environment, because those are the bytes that passed the build
# gate -- not a rebuild. So the ONLY statement allowed to name a dev-scoped
# resource is the one Sid that exists to grant exactly that read,
# "PromotionSourceRead" (UIFPE-486). Anything else naming
# uif-apicurio-dev- is still the copy-paste failure this guard exists to
# catch, so it walks every statement rather than grepping the whole file, and
# fails by name rather than by count.
#
# NOTE for whoever next runs --render-policy for a non-dev environment: the
# render is a straight retarget of dev's OWN reviewed document and knows
# nothing about PromotionSourceRead, so re-rendering will propose dropping
# this statement. Do not accept that diff; edit this file by hand instead.
if [ "$ENV_NAME" != "dev" ]; then
  offending_sids="$(python3 - "$ENV_DOC" <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    doc = json.load(fh)


def references_dev(node):
    if isinstance(node, dict):
        return any(references_dev(v) for v in node.values())
    if isinstance(node, list):
        return any(references_dev(v) for v in node)
    if isinstance(node, str):
        return "uif-apicurio-dev-" in node
    return False


for st in doc.get("Statement", []):
    if references_dev(st) and st.get("Sid") != "PromotionSourceRead":
        print(st.get("Sid", "<statement with no Sid>"))
PY
  )"
  if [ -n "$offending_sids" ]; then
    die "$(printf '%s\n' \
      "${ENV_DOC##*/} references uif-apicurio-dev- resources outside PromotionSourceRead, in:" \
      "$(printf '    %s\n' $offending_sids)" \
      "  Promotion legitimately reads dev's build artefacts through the single" \
      "  PromotionSourceRead statement; every other reference to a dev- resource" \
      "  is the copy-paste failure this guard exists to catch. Re-render, or fix" \
      "  the statement(s) named above by hand.")"
  fi
fi
ok "document targets ${ENV_NAME}"

if [ "$MODE" = "verify" ]; then
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    ok "${POLICY_NAME} exists"
    aws iam list-entities-for-policy --policy-arn "$POLICY_ARN" \
      --query 'PolicyRoles[].RoleName' --output text 2>/dev/null \
      | tr '\t' '\n' | sed 's/^/    attached to: /'
  else
    warn "${POLICY_NAME} does not exist"
  fi
elif [ "$MODE" = "dry" ]; then
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    would "create a new default version of ${POLICY_NAME} from ${ENV_DOC##*/}"
  else
    would "create ${POLICY_NAME} from ${ENV_DOC##*/}"
  fi
  would "attach it to role ${ROLE}"
else
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    # IAM allows five versions per policy; the sixth create fails. Prune the
    # non-default ones first so a bootstrap re-run does not start failing after
    # the fifth time, which is a confusing failure a long way from its cause.
    # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
    versions="$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
      --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)"
    count=0; for _ in $versions; do count=$((count + 1)); done
    if [ "$count" -ge 4 ]; then
      # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
      oldest="$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
        --query 'sort_by(Versions[?IsDefaultVersion==`false`], &CreateDate)[0].VersionId' \
        --output text)"
      aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$oldest"
      ok "pruned oldest policy version ${oldest} (IAM allows five)"
    fi
    aws iam create-policy-version --policy-arn "$POLICY_ARN" \
      --policy-document "file://${ENV_DOC}" --set-as-default >/dev/null
    ok "updated ${POLICY_NAME} to a new default version"
  else
    aws iam create-policy --policy-name "$POLICY_NAME" \
      --policy-document "file://${ENV_DOC}" \
      --description "Apicurio ${ENV_NAME} deploy permissions; document tracked at capabilities/infrastructure/" >/dev/null
    ok "created ${POLICY_NAME}"
  fi

  if aws iam list-attached-role-policies --role-name "$ROLE" \
      --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null \
      | grep -qw "$POLICY_ARN"; then
    ok "already attached to ${ROLE}"
  else
    aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$POLICY_ARN"
    ok "attached to ${ROLE}"
  fi
fi

# ── 3. The base deploy policy ─────────────────────────────────────────────────
# THE DELTA POLICY IS NOT SUFFICIENT ON ITS OWN, and discovering that late is
# what this step exists to prevent.
#
# uif-apicurio-<env>-deploy.policy.json is additive by design: its own script
# calls it "the one IAM change the Apicurio dev deploy needs", because dev
# attaches it to the SHARED echo-dev-github-actions role, which already carried
# ec2, elbv2, lambda, apigateway, cloudfront, logs and iam grants from three
# other policies. A per-stack role has no such base, so it needs those too --
# and a role holding only the delta cannot apply the stack at all, while
# simulating the delta's own actions against it reports everything allowed.
#
# So the base document is DERIVED from the terraform rather than recalled:
# required-actions.py reads every resource and data block and looks each type up
# in terraform-iam-map.json, refusing outright on a type it does not know.
#
# The two policies are complements, not alternatives: neither is sufficient
# alone, and this step checks both are attached for that reason. The base
# deliberately omits ecr, s3, rds and wafv2, which the delta already grants --
# absorbing them would put the document over IAM's 6144-character managed-policy
# limit, which it currently sits 220 characters under.

step "3/7  Base deploy policy ${BASE_POLICY_NAME}"

if [ ! -f "$BASE_DOC" ]; then
  die "$(printf '%s\n' \
    "no base policy document at ${BASE_DOC#"$REPO_ROOT"/}." \
    "  Generate it, read it, and commit it before anything is applied:" \
    "    ${REQUIRED_ACTIONS##*/} --mode policy --env ${ENV_NAME} --account ${ACCOUNT} \\" \
    "      > capabilities/infrastructure/uif-apicurio-${ENV_NAME}-base.policy.json" \
    "  Without it the role holds only the additive delta and cannot apply the" \
    "  stack -- and the simulate step below would still pass, because the delta" \
    "  covers the actions it grants.")"
fi
ok "document present: ${BASE_DOC#"$REPO_ROOT"/}"

# Regenerated and compared, not trusted. A committed base policy that no longer
# matches the terraform is the same failure as not having one, arriving later.
if command -v python3 >/dev/null && [ -x "$REQUIRED_ACTIONS" ]; then
  if fresh="$("$REQUIRED_ACTIONS" --mode policy --env "$ENV_NAME" --account "$ACCOUNT" --region "$REGION" 2>/tmp/ra.err)"; then
    if printf '%s\n' "$fresh" | python3 -c '
import json,sys
committed = json.load(open(sys.argv[1]))
fresh = json.load(sys.stdin)
sys.exit(0 if committed == fresh else 1)
' "$BASE_DOC"; then
      ok "matches what the terraform currently requires"
    else
      die "$(printf '%s\n' \
        "${BASE_DOC##*/} no longer matches what the terraform requires." \
        "  The stack has gained or lost resources since it was generated. Regenerate," \
        "  read the diff and commit it:" \
        "    ${REQUIRED_ACTIONS##*/} --mode policy --env ${ENV_NAME} --account ${ACCOUNT} \\" \
        "      > capabilities/infrastructure/uif-apicurio-${ENV_NAME}-base.policy.json")"
    fi
  else
    die "$(printf '%s\n' "required-actions.py refused to derive the action list:" "$(sed 's/^/    /' /tmp/ra.err)")"
  fi
fi

if [ "$MODE" = "verify" ]; then
  if aws iam get-policy --policy-arn "$BASE_POLICY_ARN" >/dev/null 2>&1; then
    ok "${BASE_POLICY_NAME} exists"
  else
    warn "${BASE_POLICY_NAME} does not exist -- the role cannot apply the stack"
  fi
elif [ "$MODE" = "dry" ]; then
  would "create or update ${BASE_POLICY_NAME} from ${BASE_DOC##*/} and attach it to ${ROLE}"
else
  if aws iam get-policy --policy-arn "$BASE_POLICY_ARN" >/dev/null 2>&1; then
    # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
    bversions="$(aws iam list-policy-versions --policy-arn "$BASE_POLICY_ARN" \
      --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)"
    bcount=0; for _ in $bversions; do bcount=$((bcount + 1)); done
    if [ "$bcount" -ge 4 ]; then
      # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
      boldest="$(aws iam list-policy-versions --policy-arn "$BASE_POLICY_ARN" \
        --query 'sort_by(Versions[?IsDefaultVersion==`false`], &CreateDate)[0].VersionId' --output text)"
      aws iam delete-policy-version --policy-arn "$BASE_POLICY_ARN" --version-id "$boldest"
      ok "pruned oldest base policy version ${boldest}"
    fi
    aws iam create-policy-version --policy-arn "$BASE_POLICY_ARN" \
      --policy-document "file://${BASE_DOC}" --set-as-default >/dev/null
    ok "updated ${BASE_POLICY_NAME} to a new default version"
  else
    aws iam create-policy --policy-name "$BASE_POLICY_NAME" \
      --policy-document "file://${BASE_DOC}" \
      --description "Apicurio ${ENV_NAME} base deploy permissions, derived from the terraform by required-actions.py. Complements uif-apicurio-${ENV_NAME}-deploy-policy; neither is sufficient alone." \
      >/dev/null
    ok "created ${BASE_POLICY_NAME}"
  fi

  if aws iam list-attached-role-policies --role-name "$ROLE" \
      --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null \
      | grep -qw "$BASE_POLICY_ARN"; then
    ok "already attached to ${ROLE}"
  else
    aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$BASE_POLICY_ARN"
    ok "attached to ${ROLE}"
  fi
fi

# ── 2. Verify the grant, against the attached role ────────────────────────────
# The half a paste omits. "The policy was attached" is not the same claim as
# "the role can now do the thing", and only the second one matters.

step "4/7  Terraform backend policy ${BACKEND_POLICY_NAME}"

# WHY THIS STEP EXISTS AT ALL. The first uat plan failed at `terraform init`
# with a 403 on HeadObject against its own state file, while the previous step
# had just reported "all 125 derived actions allowed". Both were true: the
# derived list covers what the stack MANAGES, and the state bucket is
# something it merely USES. No amount of deriving from the terraform can find
# it, so it is granted here and verified like everything else.
#
# TWO GRANTS, and the second one is not obvious. Object read/write on this
# environment's key prefix is the part everyone expects. `s3:ListBucket` on
# the bucket is the part that is easy to drop as over-permissive -- and
# dropping it breaks exactly one case, the one that matters on a first run:
# S3 answers HeadObject on a MISSING key with 403, not 404, unless the caller
# can list the bucket. Without it Terraform cannot tell "no state yet" from
# "access denied", which is precisely the 403 above. It cannot carry an
# s3:prefix condition either, because a HeadObject request sends no prefix.

[ -f "$BACKEND_DOC" ] || die "$(printf '%s\n' \
  "${BACKEND_DOC##*/} does not exist." \
  "  Terraform cannot read its own state without it, and it cannot be derived" \
  "  from the terraform because the state bucket is not a managed resource." \
  "  Write it with two statements -- object access on this environment's key" \
  "  prefix, and s3:ListBucket on the bucket -- modelled on uat's copy.")"

python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BACKEND_DOC" 2>/dev/null \
  || die "${BACKEND_DOC##*/} is not valid JSON"

# The backend config is the single source of truth for which bucket and key
# this environment uses, so the grant is checked against it rather than
# against a literal repeated here. A policy that names a different bucket than
# the backend will attach cleanly and grant nothing that is ever read.
[ -f "$BACKEND_HCL" ] || die "${BACKEND_HCL##*/} does not exist, so the grant cannot be checked against the configured backend"
hcl_bucket="$(sed -n 's/^[[:space:]]*bucket[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$BACKEND_HCL" | head -1)"
hcl_key="$(sed -n 's/^[[:space:]]*key[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$BACKEND_HCL" | head -1)"
[ -n "$hcl_bucket" ] || die "could not read the bucket from ${BACKEND_HCL##*/}"
[ -n "$hcl_key" ]    || die "could not read the key from ${BACKEND_HCL##*/}"
hcl_prefix="${hcl_key%/*}/"

grep -q "$hcl_bucket" "$BACKEND_DOC" \
  || die "${BACKEND_DOC##*/} does not name ${hcl_bucket}, which is the bucket ${BACKEND_HCL##*/} configures"
grep -q "${hcl_prefix}" "$BACKEND_DOC" \
  || die "${BACKEND_DOC##*/} does not name the key prefix ${hcl_prefix} that ${BACKEND_HCL##*/} configures"

# Same copy-paste guard the delta policy carries: a document rendered for
# another environment would attach cleanly and grant access to the wrong state.
for other in dev uat prod; do
  [ "$other" = "$ENV_NAME" ] && continue
  if grep -q "uif-apicurio/${other}/" "$BACKEND_DOC"; then
    die "${BACKEND_DOC##*/} references uif-apicurio/${other}/ -- that is another environment's state"
  fi
done
ok "grant matches the backend ${BACKEND_HCL##*/} configures (${hcl_bucket}, ${hcl_prefix})"

if [ "$MODE" = "verify" ]; then
  if aws iam get-policy --policy-arn "$BACKEND_POLICY_ARN" >/dev/null 2>&1; then
    ok "${BACKEND_POLICY_NAME} exists"
    if aws iam list-attached-role-policies --role-name "$ROLE" \
        --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null \
        | grep -qw "$BACKEND_POLICY_ARN"; then
      ok "attached to ${ROLE}"
    else
      warn "${BACKEND_POLICY_NAME} exists but is NOT attached to ${ROLE} -- terraform init will 403 on its own state"
    fi
  else
    warn "${BACKEND_POLICY_NAME} does not exist -- terraform init will 403 on its own state"
  fi
elif [ "$MODE" = "dry" ]; then
  would "create or update ${BACKEND_POLICY_NAME} from ${BACKEND_DOC##*/} and attach it to ${ROLE}"
else
  if aws iam get-policy --policy-arn "$BACKEND_POLICY_ARN" >/dev/null 2>&1; then
    # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
    kversions="$(aws iam list-policy-versions --policy-arn "$BACKEND_POLICY_ARN" \
      --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)"
    kcount=0; for _ in $kversions; do kcount=$((kcount + 1)); done
    if [ "$kcount" -ge 4 ]; then
      # shellcheck disable=SC2016  # `false` is JMESPath's literal, not a shell expansion
      koldest="$(aws iam list-policy-versions --policy-arn "$BACKEND_POLICY_ARN" \
        --query 'sort_by(Versions[?IsDefaultVersion==`false`], &CreateDate)[0].VersionId' --output text)"
      aws iam delete-policy-version --policy-arn "$BACKEND_POLICY_ARN" --version-id "$koldest"
      ok "pruned oldest backend policy version ${koldest}"
    fi
    aws iam create-policy-version --policy-arn "$BACKEND_POLICY_ARN" \
      --policy-document "file://${BACKEND_DOC}" --set-as-default >/dev/null
    ok "updated ${BACKEND_POLICY_NAME} to a new default version"
  else
    aws iam create-policy --policy-name "$BACKEND_POLICY_NAME" \
      --policy-document "file://${BACKEND_DOC}" \
      --description "Terraform S3 backend state access for apicurio ${ENV_NAME}. Separate from the deploy and base policies because the state bucket is not a resource this stack manages, so it cannot be derived from the terraform." \
      >/dev/null
    ok "created ${BACKEND_POLICY_NAME}"
  fi

  if aws iam list-attached-role-policies --role-name "$ROLE" \
      --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null \
      | grep -qw "$BACKEND_POLICY_ARN"; then
    ok "already attached to ${ROLE}"
  else
    aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$BACKEND_POLICY_ARN"
    ok "attached to ${ROLE}"
  fi
fi

step "5/7  Verify the role can act on ${ENV_NAME}'s resources"

if [ "$MODE" = "dry" ]; then
  would "simulate ecr, ecs, s3 and rds actions against ${ROLE}"
else
  role_arn_for_sim="arn:aws:iam::${ACCOUNT}:role/${ROLE}"
  failed=0
  allowed=0
  # ONE API CALL PER SCOPE, not one per action-and-ARN.
  # simulate-principal-policy accepts many --action-names and many
  # --resource-arns together, and an action counts as allowed if any resource
  # allows it. Per-action calls meant roughly 500 sequential round trips and a
  # run that exceeded ten minutes; this is 14 calls.
  #
  # `set -f` around the loop is load-bearing. The unscopable scopes carry a
  # single ARN of "*", and an UNQUOTED expansion of that under `IFS=,`
  # GLOB-EXPANDS to the working directory listing -- which is why the first run
  # of this reported "error on VERSION" for 32 actions, VERSION being a file in
  # stacks/apicurio. Those were not denials at all.
  set -f
  while IFS="$(printf '\t')" read -r scope actions arns; do
    [ -n "${scope:-}" ] || continue
    IFS=, read -r -a action_list <<<"$actions"
    IFS=, read -r -a arn_list <<<"$arns"

    if ! result="$(aws iam simulate-principal-policy \
        --policy-source-arn "$role_arn_for_sim" \
        --action-names "${action_list[@]}" \
        --resource-arns "${arn_list[@]}" \
        --output json 2>/tmp/sim.err)"; then
      warn "simulate failed for scope ${scope}: $(tr -d '\n' </tmp/sim.err | head -c 300)"
      failed=$((failed + ${#action_list[@]}))
      continue
    fi

    # Allowed if ANY resource allowed it -- the API returns one result per
    # action-and-resource pair, and an action evaluated against the wrong
    # resource TYPE is an implicitDeny however fully it is granted.
    verdicts="$(printf '%s' "$result" | python3 -c '
import json, sys, collections
best = collections.defaultdict(str)
for r in json.load(sys.stdin)["EvaluationResults"]:
    a, d = r["EvalActionName"], r["EvalDecision"]
    if d == "allowed" or not best[a]:
        best[a] = d
for a in sorted(best):
    print(f"{a}\t{best[a]}")
')"
    while IFS="$(printf '\t')" read -r action decision; do
      [ -n "${action:-}" ] || continue
      if [ "$decision" = "allowed" ]; then
        allowed=$((allowed + 1))
      else
        warn "${action} -> ${decision} (scope ${scope})"
        failed=$((failed + 1))
      fi
    done <<<"$verdicts"
  done <<DERIVED
$("$REQUIRED_ACTIONS" --mode batches --env "$ENV_NAME" --account "$ACCOUNT" --region "$REGION")
DERIVED
  set +f

  # The delta policy's own surface, which the derived list deliberately omits
  # because terraform-iam-map.json marks those types as already granted. Kept
  # explicit so both halves of the pair are exercised. s3:GetObjectVersion is
  # the rollback path: denied, a release cannot be restored when it matters.
  simulate_one() {
    local decision
    decision="$(aws iam simulate-principal-policy \
      --policy-source-arn "$role_arn_for_sim" \
      --action-names "$1" --resource-arns "$2" \
      --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null || echo error)"
    if [ "$decision" = "allowed" ]; then
      allowed=$((allowed + 1))
    else
      warn "$1 -> ${decision} on $2"
      failed=$((failed + 1))
    fi
  }
  simulate_one ecr:PutImage            "arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-${ENV_NAME}-bff-ecr-repo"
  simulate_one ecr:BatchGetImage       "arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-${ENV_NAME}-bff-ecr-repo"
  simulate_one s3:PutObject            "arn:aws:s3:::uif-apicurio-${ENV_NAME}-release-artifacts/x.tar.gz"
  simulate_one s3:GetObjectVersion     "arn:aws:s3:::uif-apicurio-${ENV_NAME}-release-artifacts/x.tar.gz"
  simulate_one rds:DescribeDBInstances "arn:aws:rds:${REGION}:${ACCOUNT}:db:uif-apicurio-${ENV_NAME}-registry"
  simulate_one wafv2:CreateWebACL      "arn:aws:wafv2:us-east-1:${ACCOUNT}:global/webacl/uif-apicurio-${ENV_NAME}-ui/*"

  if [ "$failed" -gt 0 ]; then
    if [ "$MODE" = "verify" ]; then
      warn "${failed} action(s) not allowed; re-run without --verify-only to apply the policy"
    else
      die "${failed} of $((allowed + failed)) action(s) are still not allowed after attaching both policies. The environment is NOT ready; do not run terraform apply -- it would fail part-way and leave ${ENV_NAME} half-built."
    fi
  else
    ok "all ${allowed} derived actions allowed"
  fi
  note "simulate-principal-policy evaluates identity policies only -- an SCP or"
  note "a permissions boundary can still deny at runtime."
fi

# ── 3. GitHub Environment ─────────────────────────────────────────────────────

step "6/7  GitHub Environment '${ENV_NAME}'"

env_exists() { gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}" >/dev/null 2>&1; }

if [ "$MODE" = "verify" ]; then
  if env_exists; then
    ok "environment exists"
    gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}" \
      --jq '"    reviewers: \([.protection_rules[]? | select(.type=="required_reviewers") | .reviewers[]?.reviewer.login // .reviewers[]?.reviewer.slug] | join(", ") // "NONE")"' 2>/dev/null || true
    gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}/deployment-branch-policies" \
      --jq '.branch_policies[] | "    policy: \(.name) (\(.type // "branch"))"' 2>/dev/null \
      || echo "    policy: none"
  else
    warn "environment '${ENV_NAME}' does not exist"
  fi
elif [ "$MODE" = "dry" ]; then
  if env_exists; then
    would "update environment '${ENV_NAME}'"
  else
    would "create environment '${ENV_NAME}'"
  fi
  for r in ${REVIEWERS[@]+"${REVIEWERS[@]}"}; do would "require review from ${r}"; done
  if [ "$ENV_NAME" = "dev" ]; then
    would "keep dev's existing branch policy (develop, main)"
  else
    would "set a deployment TAG policy of ${TAG_POLICY} and no branch policy"
  fi
else
  # Already resolved and validated in preflight, so this is only assembly --
  # no lookup here can fail and leave a malformed list.
  reviewer_json="[]"
  if [ "${#RESOLVED_REVIEWERS[@]}" -gt 0 ]; then
    reviewer_json="[$(IFS=,; echo "${RESOLVED_REVIEWERS[*]}")]"
  fi
  python3 -c 'import json,sys; json.loads(sys.argv[1])' "$reviewer_json" \
    || die "internal error: the reviewer list is not valid JSON (${reviewer_json})"

  # prevent_self_review is the difference between an approver and a formality:
  # without it whoever dispatches the promotion can approve their own.
  #
  # WHY --allow-self-review EXISTS, and why it is not merely a weakening. With
  # exactly ONE reviewer named, prevent_self_review is not a gate, it is a
  # lock: the only person who may approve is the only person who ever
  # dispatches, so every deployment queues forever with nobody able to release
  # it. That is exactly what uat's first dispatch did. The honest options are a
  # second reviewer or this flag; leaving the environment quietly unusable is
  # not one of them.
  #
  # Turning it off keeps the approval CLICK -- nothing reaches AWS without a
  # human acting -- and gives up only the separation of duties that a
  # one-person reviewer list never actually provided. Add a second reviewer and
  # drop the flag. Do not carry it to prod, where a second approver should
  # exist before anything deploys there at all.
  if [ "$ENV_NAME" = "dev" ] || [ "$ALLOW_SELF_REVIEW" = "1" ]; then
    body="$(python3 -c '
import json,sys
print(json.dumps({
  "reviewers": json.loads(sys.argv[1]),
  "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}))' "$reviewer_json")"
  else
    body="$(python3 -c '
import json,sys
print(json.dumps({
  "reviewers": json.loads(sys.argv[1]),
  "prevent_self_review": True,
  "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}))' "$reviewer_json")"
  fi

  printf '%s' "$body" | gh api -X PUT "repos/${REPO_SLUG}/environments/${ENV_NAME}" --input - >/dev/null
  ok "environment '${ENV_NAME}' created or updated"
  if [ "$ENV_NAME" = "dev" ]; then
    :
  elif [ "$ALLOW_SELF_REVIEW" = "1" ]; then
    warn "prevent_self_review NOT enabled (--allow-self-review): the named reviewer may approve their own dispatch. Add a second reviewer and drop the flag."
  else
    ok "prevent_self_review enabled"
  fi

  if [ "$ENV_NAME" != "dev" ]; then
    existing="$(gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}/deployment-branch-policies" \
      --jq '.branch_policies[]? | "\(.id) \(.name) \(.type // "branch")"' 2>/dev/null || true)"
    if printf '%s\n' "$existing" | grep -qF " ${TAG_POLICY} tag"; then
      ok "tag policy ${TAG_POLICY} already present"
    else
      gh api -X POST "repos/${REPO_SLUG}/environments/${ENV_NAME}/deployment-branch-policies" \
        -f "name=${TAG_POLICY}" -f "type=tag" >/dev/null \
        || die "could not create the deployment TAG policy ${TAG_POLICY}. Without it this environment accepts a deploy from any ref, which defeats tag-based promotion -- do not proceed."
      ok "tag policy ${TAG_POLICY} created"
    fi
    # Any branch policy left here would readmit branch deploys through the
    # side door, so they are removed rather than merely not added.
    printf '%s\n' "$existing" | while read -r id name type; do
      [ -n "${id:-}" ] || continue
      if [ "$type" = "branch" ]; then
        gh api -X DELETE "repos/${REPO_SLUG}/environments/${ENV_NAME}/deployment-branch-policies/${id}" >/dev/null 2>&1 \
          && warn "removed branch policy '${name}': ${ENV_NAME} is tag-only"
      fi
    done
  fi

  # Read back. The API accepting a PUT is not evidence that protection exists.
  got_reviewers="$(gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}" \
    --jq '[.protection_rules[]? | select(.type=="required_reviewers")] | length' 2>/dev/null || echo 0)"
  if [ "$ENV_NAME" != "dev" ] && [ "$got_reviewers" = "0" ]; then
    die "environment '${ENV_NAME}' exists but has NO required-reviewer rule after the update. It is currently unprotected; fix it before any workflow references it."
  fi
  [ "$got_reviewers" = "0" ] || ok "required-reviewer rule present"

  if [ "$ENV_NAME" != "dev" ]; then
    gh api "repos/${REPO_SLUG}/environments/${ENV_NAME}/deployment-branch-policies" \
      --jq '.branch_policies[] | select(.type=="tag") | .name' 2>/dev/null | grep -qF "$TAG_POLICY" \
      || die "the tag policy ${TAG_POLICY} is not present on read-back"
    ok "read-back confirms tag-only deployment from ${TAG_POLICY}"
  fi
fi

# ── 4. Deploy-role secret and the Terraform configuration ─────────────────────

step "7/7  ${SECRET_NAME} and the Terraform configuration"

if gh secret list --json name --jq '.[].name' 2>/dev/null | grep -qx "$SECRET_NAME"; then
  ok "${SECRET_NAME} is already set"
elif [ "$MODE" = "verify" ]; then
  warn "${SECRET_NAME} is not set; every OIDC step for ${ENV_NAME} will fail"
elif [ "$MODE" = "dry" ]; then
  would "set ${SECRET_NAME} to ${ROLE_ARN}"
else
  printf '%s' "$ROLE_ARN" | gh secret set "$SECRET_NAME" >/dev/null
  ok "set ${SECRET_NAME} to ${ROLE_ARN}"
fi

# REPORTED, NOT CREATED. envs/<env>.tfvars needs surveyed network values, and a
# file of placeholders is worse than no file: `terraform plan` would run against
# vpc-UPDATE-ME and fail deep in a provider call rather than at the first read.
# The repo's own rule is not to create stub files for completeness.
missing=0
for f in "backends/${ENV_NAME}.hcl" "envs/${ENV_NAME}.tfvars"; do
  if [ -f "${TF_DIR}/${f}" ]; then
    ok "${f} present"
  else
    warn "${f} is MISSING"
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo ""
  echo "  Terraform configuration for ${ENV_NAME} is incomplete. This script does not"
  echo "  write it: envs/${ENV_NAME}.tfvars needs values surveyed from the account, and"
  echo "  placeholders would turn a missing-value error into a provider error much"
  echo "  further from its cause."
  echo ""
  echo "  backends/${ENV_NAME}.hcl -- mechanical, mirroring Echo's echo/uat/ key:"
  echo "    bucket       = \"pearson-ingestion-framework-tfstate-${ACCOUNT}\""
  echo "    key          = \"uif-apicurio/${ENV_NAME}/terraform.tfstate\""
  echo "    region       = \"${REGION}\""
  echo "    use_lockfile = true"
  echo "    encrypt      = true"
  echo ""
  echo "  envs/${ENV_NAME}.tfvars -- six values to survey, none derivable from dev's:"
  echo "    vpc_id, private_subnet_ids, secretsmanager_endpoint_sg_id,"
  echo "    api_allowed_cidrs, vpn_client_cidrs, cors_origins"
  echo ""
  echo "  cors_origins is last on purpose: the CloudFront domain does not exist"
  echo "  until the first apply, so it takes the two-pass sequence in"
  echo "  DEPLOY-DEV.md Phase 4."
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
case "$MODE" in
  dry)    echo "Dry run complete. Nothing was changed." ;;
  verify) echo "Verification complete. Nothing was changed." ;;
  run)
    if [ "$missing" -gt 0 ]; then
      echo "Bootstrap applied, but ${ENV_NAME} is NOT ready: the Terraform configuration"
      echo "above is still missing. Add it, then re-run with --verify-only."
      exit 1
    fi
    echo "${ENV_NAME} is bootstrapped and ready for terraform apply."
    echo ""
    echo "  Next: terraform -chdir=stacks/apicurio/infrastructure/terraform \\"
    echo "          init -reconfigure -backend-config=backends/${ENV_NAME}.hcl"
    ;;
esac
