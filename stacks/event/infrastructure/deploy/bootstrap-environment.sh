#!/usr/bin/env bash
# Bootstrap what the event stack needs in an environment BEFORE the first
# deploy-event-infra.yml run can converge it. dev and uat; prod is not bootstrapped.
#
# DEV IS HERE TOO, since UIFPE-744 gave the stack its own release bucket. dev
# differs in two ways only: it attaches to the shared OIDC role rather than an
# env-scoped one, and it owns no state bucket, so step 2 is skipped.
#
# Run BY HAND, ONCE, by someone with IAM admin rights in 283556840805. Nothing in
# CI calls it: every action is a one-time privileged change that a person should
# make deliberately and read the result of. Modelled on
# stacks/echo/infrastructure/deploy/bootstrap-environment.sh, cut to what the
# event stack actually needs:
#
#   1. Preflight  -- the credentials are for 283556840805, and the deploy role
#                    exists.
#   2. State      -- the Terraform state bucket uif-event-uat-data (versioned,
#                    encrypted, never public). `terraform init` needs it before
#                    Terraform can evaluate anything, so Terraform cannot own it.
#   3. Policy     -- DERIVED from the Terraform by required-actions.py, so it
#                    cannot drift from what an apply needs. Every ARN is pinned
#                    to uif-event-uat-*, plus the state bucket, this env's
#                    release bucket, a READ of dev's release bucket (the
#                    promotion source), and the event-domain uploads under
#                    uif-echo-uat-gold-artifacts/event-pipeline/*.
#   4. Attach     -- as a customer-managed policy, uif-event-uat-deploy-policy,
#                    on ECHO'S uat deploy role. Owner decision 2026-09-30: extend
#                    uif-echo-uat-deploy-role rather than create a second role,
#                    so deploy-event-infra.yml uses the existing
#                    AWS_ROLE_ARN_ECHO_UAT secret. A SEPARATE managed policy, not
#                    an edit to echo's derived one: echo's bootstrap regenerates
#                    its own policy wholesale, and a statement added inside it
#                    would be silently dropped on echo's next run.
#   5. Verify     -- simulate-principal-policy per statement against the
#                    ATTACHED role. A green result is necessary, not sufficient:
#                    apicurio needed five apply-and-fix rounds and echo nine,
#                    because a derivation is blind to post-create reads.
#
# WHAT IT DOES NOT DO: create a role, change a trust policy, touch the GitHub
# `uat` environment (it already admits event/v* tags), or write any secret
# (AWS_ROLE_ARN_ECHO_UAT already exists, environment-scoped on uat).
#
# Usage:
#   ./bootstrap-environment.sh <dev|uat> [--role <name>] [--render-policy] [--dry-run] [--verify-only]
#
#   --role <name>      the role to attach to; default echo-dev-github-actions for
#                      dev, uif-echo-<env>-deploy-role otherwise
#   --render-policy    derive and print the policy, then stop. Changes nothing.
#   --dry-run          say what would change; change nothing
#   --verify-only      run step 5 only
#
# Idempotent: re-running after a map correction updates the policy in place (a
# new default policy version) and re-verifies. Exit 0 = ready for the first
# deploy-event-infra.yml run; anything else = stop.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ACCOUNT="283556840805"
REGION="eu-west-1"

step() { echo ""; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*" >&2; }
die()  { echo "  ✗ $*" >&2; exit 1; }
would(){ echo "  … would $*"; }

ENV_NAME="${1:-}"
[ -n "$ENV_NAME" ] || die "usage: bootstrap-environment.sh <dev|uat> [--role <name>] [--render-policy] [--dry-run] [--verify-only]"
shift
# dev WAS refused here, on the assumption that the shared dev role already
# allowed everything an event apply needs. True until this stack owned a bucket:
# UIFPE-744 moved the artifacts into uif-event-dev-release-artifacts, and the
# shared role's S3 grants are scoped to uif-echo-dev-*, so the first apply died
# with AccessDenied on s3:CreateBucket. simulate-principal-policy confirmed every
# bucket-level action denied for uif-event-dev-* and allowed for uif-echo-dev-*.
# apicurio already does exactly this on the same role
# (uif-apicurio-dev-deploy-policy is attached to it), so dev takes the same
# derived-policy treatment as uat.
case "$ENV_NAME" in
  dev|uat) ;;
  prod) die "prod is not bootstrapped yet: it is a different account (not ${ACCOUNT}) and its role decision has not been taken. See docs/uif-event-uat-rollout-plan.md." ;;
  *) die "unknown environment '${ENV_NAME}'. Known: dev, uat" ;;
esac

# dev deploys under the shared OIDC role every stack uses, which is not named for
# an environment; uat has echo's env-scoped role (owner decision 2026-09-30).
if [ "$ENV_NAME" = "dev" ]; then
  ROLE="echo-dev-github-actions"
else
  ROLE="uif-echo-${ENV_NAME}-deploy-role"
fi
RENDER=0 DRY=0 VERIFY_ONLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --role)          [ "$#" -ge 2 ] || die "--role needs a value"; ROLE="$2"; shift 2 ;;
    --render-policy) RENDER=1; shift ;;
    --dry-run)       DRY=1; shift ;;
    --verify-only)   VERIFY_ONLY=1; shift ;;
    -h|--help)       sed -n '2,48p' "$0"; exit 0 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

STATE_BUCKET="uif-event-${ENV_NAME}-data"
POLICY_BASE="uif-event-${ENV_NAME}-deploy-policy"

# An interpreter that actually RUNS -- on Windows a `python3` on PATH can be the
# Microsoft Store alias, which prints "Python was not found" (echo's lesson).
PY=""
for c in python3 py python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import json" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "no working python on PATH (tried python3, py, python)"

# aws --output text read through this: a trailing CR on Windows makes an ARN
# compare unequal to itself (echo's lesson, 169 falsely-denied actions).
awstext() { tr -d '\r'; }

WORK="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/event-bootstrap-$$")"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

# ── 3 (render) first, because --render-policy needs nothing else ──────────────
render() {
  "$PY" "$HERE/required-actions.py" --env "$ENV_NAME" --account "$ACCOUNT" \
    --region "$REGION" --mode policies --out-dir "$WORK/policies" > "$WORK/policy-files"
}

if [ "$RENDER" -eq 1 ]; then
  render
  while IFS= read -r f; do f="$(printf '%s' "$f" | awstext)"; echo "── ${f##*/}"; cat "$f"; done < "$WORK/policy-files"
  exit 0
fi

# ── 1. Preflight ──────────────────────────────────────────────────────────────
step "1/5 preflight"
caller="$(aws sts get-caller-identity --query Account --output text 2>/dev/null | awstext)" \
  || die "no usable AWS credentials"
[ "$caller" = "$ACCOUNT" ] \
  || die "these credentials are for account ${caller}, not ${ACCOUNT}. Nothing has been changed."
ok "credentials are for ${ACCOUNT}"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text 2>/dev/null | awstext)" \
  || die "role ${ROLE} does not exist. This script extends an existing role; it does not create one."
ok "role ${ROLE_ARN}"

verify() {
  step "5/5 verify (simulate-principal-policy against ${ROLE})"
  "$PY" "$HERE/required-actions.py" --env "$ENV_NAME" --account "$ACCOUNT" \
    --region "$REGION" --mode batches > "$WORK/batches"
  local denied=0 total=0 sid actions arns pattern res allowed missing n
  while IFS=$'\t' read -r sid actions arns; do
    sid="$(printf '%s' "$sid" | awstext)"; actions="$(printf '%s' "$actions" | awstext)"; arns="$(printf '%s' "$arns" | awstext)"
    [ -n "$sid" ] || continue
    # ONE CALL PER ARN PATTERN, and an action counts as allowed if ANY of its
    # statement's patterns allows it. A statement mixes bucket-level and
    # object-level ARNs (and bus vs rule ARNs); simulating s3:CreateBucket
    # against an object ARN is a false deny, not a finding.
    : > "$WORK/allowed"
    while IFS= read -r pattern; do
      [ -n "$pattern" ] || continue
      # A wildcard is not a resource: probe with a concrete name the pattern
      # matches. A bare "*" (unscopable action) is passed through as is.
      if [ "$pattern" = "*" ]; then res="*"; else res="$(printf '%s' "$pattern" | sed 's/\*/probe/g')"; fi
      # shellcheck disable=SC2046
      aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
        --action-names $(printf '%s' "$actions" | tr ',' ' ') \
        --resource-arns "$res" \
        --query 'EvaluationResults[?EvalDecision==`allowed`].EvalActionName' \
        --output text 2>/dev/null | awstext | tr '\t' '\n' >> "$WORK/allowed" \
        || { warn "${sid}: simulate call failed for ${res}"; }
    done <<< "$(printf '%s' "$arns" | tr ',' '\n')"
    missing="$(printf '%s' "$actions" | tr ',' '\n' | grep -vxF -f "$WORK/allowed" || true)"
    n="$(printf '%s' "$actions" | tr ',' '\n' | grep -c .)"
    total=$((total + n))
    if [ -n "$missing" ]; then
      warn "${sid}: NOT allowed on any of its resources ->"; printf '%s\n' "$missing" | sed 's/^/      /' >&2
      denied=$((denied + $(printf '%s\n' "$missing" | grep -c .)))
    else
      ok "${sid}: ${n} action(s) allowed"
    fi
  done < "$WORK/batches"
  [ "$denied" -eq 0 ] || die "${denied} action(s) not allowed of ${total}. Fix the map (or a boundary/SCP on the role) and re-run."
  ok "all ${total} simulated actions allowed. Necessary, not sufficient -- the first apply is the real test."
}

if [ "$VERIFY_ONLY" -eq 1 ]; then verify; exit 0; fi

# ── 2. State bucket ───────────────────────────────────────────────────────────
# dev owns no state bucket: backends/dev.s3.tfbackend points at
# pearson-ingestion-framework-tfstate-283556840805, the bucket the product domain
# and beacon share, which the shared dev role already reaches. Creating
# uif-event-dev-data would make a bucket nothing uses, so the step is skipped.
if [ "$ENV_NAME" = "dev" ]; then
  step "2/5 state bucket -- skipped"
  ok "dev uses the shared estate bucket (backends/dev.s3.tfbackend); nothing to create"
else
  step "2/5 state bucket s3://${STATE_BUCKET}"
  if aws s3api head-bucket --bucket "$STATE_BUCKET" >/dev/null 2>&1; then
    ok "exists"
  elif [ "$DRY" -eq 1 ]; then
    would "create ${STATE_BUCKET} (versioned, SSE-S3, public access blocked)"
  else
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
    ok "created"
  fi
  if [ "$DRY" -eq 0 ]; then
    aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --versioning-configuration Status=Enabled
    aws s3api put-public-access-block --bucket "$STATE_BUCKET" --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    aws s3api put-bucket-encryption --bucket "$STATE_BUCKET" --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    aws s3api put-bucket-tagging --bucket "$STATE_BUCKET" --tagging \
      "TagSet=[{Key=Project,Value=event},{Key=Environment,Value=${ENV_NAME}},{Key=ManagedBy,Value=bootstrap-environment.sh}]"
    ok "versioning Enabled, public access blocked, SSE-S3, tagged"
  fi
fi

# ── 3. Policy ─────────────────────────────────────────────────────────────────
step "3/5 derive the deploy policy"
render
n_docs="$(grep -c . "$WORK/policy-files")"
ok "${n_docs} policy document(s) derived from the event root + modules/event-domain"

# ── 4. Attach ─────────────────────────────────────────────────────────────────
step "4/5 upsert and attach to ${ROLE}"
i=0
while IFS= read -r f; do
  f="$(printf '%s' "$f" | awstext)"
  i=$((i + 1))
  name="$POLICY_BASE"; [ "$i" -eq 1 ] || name="${POLICY_BASE}-${i}"
  arn="arn:aws:iam::${ACCOUNT}:policy/${name}"
  if [ "$DRY" -eq 1 ]; then
    would "create or update ${arn} from ${f##*/}, and attach it to ${ROLE}"
    continue
  fi
  if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
    # IAM keeps at most five versions: drop the oldest non-default first.
    count="$(aws iam list-policy-versions --policy-arn "$arn" --query 'length(Versions)' --output text | awstext)"
    if [ "$count" -ge 5 ]; then
      oldest="$(aws iam list-policy-versions --policy-arn "$arn" \
        --query 'Versions[?IsDefaultVersion==`false`] | sort_by(@,&CreateDate)[0].VersionId' --output text | awstext)"
      aws iam delete-policy-version --policy-arn "$arn" --version-id "$oldest"
    fi
    aws iam create-policy-version --policy-arn "$arn" --policy-document "file://${f}" --set-as-default >/dev/null
    ok "${name}: new default version"
  else
    aws iam create-policy --policy-name "$name" --policy-document "file://${f}" \
      --description "Event stack (uif-event-${ENV_NAME}-*) deploy permissions, derived by stacks/event/infrastructure/deploy/required-actions.py" \
      --tags Key=Project,Value=event Key=Environment,Value="$ENV_NAME" >/dev/null
    ok "${name}: created"
  fi
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$arn"
  ok "${name}: attached to ${ROLE}"
done < "$WORK/policy-files"

if [ "$DRY" -eq 1 ]; then
  echo ""; echo "DRY RUN -- nothing changed. Step 5 (verify) needs the policy attached; run without --dry-run."
  exit 0
fi

verify

echo ""
echo "Ready. Next (stacks/event/DEPLOY-UAT.md): dispatch deploy-event-infra.yml"
echo "from an event/v* tag that carries artifacts, environment=uat, infra=plan;"
echo "read the plan; then re-run from the same tag with infra=bootstrap (first"
echo "converge only -- every later run is infra=apply)."
