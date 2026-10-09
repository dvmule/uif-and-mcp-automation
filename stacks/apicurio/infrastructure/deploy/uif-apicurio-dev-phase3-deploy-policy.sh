#!/usr/bin/env bash
# Phase 3 — the one IAM change the Apicurio dev deploy needs before terraform apply.
#
# Creates the managed policy uif-apicurio-dev-deploy-policy from the tracked
# document and attaches it to the shared role echo-dev-github-actions, then
# proves the result by simulating against the ATTACHED role.
#
# Why this exists as a script rather than three pasted commands: it is
# idempotent, it refuses to run against the wrong account, and it verifies
# both that the intended actions became allowed AND that the ones that were
# meant to stay denied still are. The second half is the part a paste omits.
#
# What it does NOT do: touch Terraform, touch any other policy on the role, or
# add any Deny. The document is purely additive — see --rollback.
#
# Usage:
#   eval $(aws-runas -e dev)          # or: /Users/raj9karu/.aws/aws-runas pearson-esb-dev bash ./<this>
#   ./uif-apicurio-dev-phase3-deploy-policy.sh              # create, attach, verify
#   ./uif-apicurio-dev-phase3-deploy-policy.sh --dry-run    # say what it would do, change nothing
#   ./uif-apicurio-dev-phase3-deploy-policy.sh --verify-only # simulate only, change nothing
#   ./uif-apicurio-dev-phase3-deploy-policy.sh --rollback    # detach, then delete
#
# Exit 0 clean. Any non-zero means nothing downstream should proceed.
set -euo pipefail

REGION="${AWS_REGION:-eu-west-1}"
ACCOUNT="${AWS_ACCOUNT_ID:-283556840805}"
ROLE="${DEPLOY_ROLE:-echo-dev-github-actions}"
POLICY_NAME="uif-apicurio-dev-deploy-policy"
POLICY_ARN="arn:aws:iam::${ACCOUNT}:policy/${POLICY_NAME}"

MODE=run
case "${1:-}" in
  "")            MODE=run ;;
  --dry-run)     MODE=dry ;;
  --verify-only) MODE=verify ;;
  --rollback)    MODE=rollback ;;
  -h|--help)     sed -n '2,25p' "$0"; exit 0 ;;
  *)             echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac

step() { echo ""; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*" >&2; }
die()  { echo "  ✗ $*" >&2; exit 1; }
would() { echo "  … would $*"; }

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
DOC="${REPO}/capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json"

# ── Preflight ─────────────────────────────────────────────────────────────────
# Every one of these has a specific failure it prevents, so none is ceremony.

step "Preflight"
command -v aws >/dev/null || die "aws CLI not found"
command -v python3 >/dev/null || die "python3 not found (used to compare policy documents)"

[ -f "$DOC" ] || die "policy document not found at $DOC"
python3 -c "
import json,sys
d=json.load(open('$DOC'))
assert d.get('Version')=='2012-10-17', 'unexpected Version'
st=d['Statement']
bad=[s.get('Sid') for s in st if s.get('Effect')!='Allow']
assert not bad, 'non-Allow statement(s): %r -- this script will not attach a Deny' % bad
n=len(json.dumps(d,separators=(',',':')))
assert n<=6144, 'document is %d bytes, over the 6144 managed-policy limit' % n
print('  ✓ document valid: %d statements, %d bytes minified' % (len(st), n))
" || die "policy document failed validation"

# The account check is not paranoia: this role name exists in more than one
# Pearson account, and attaching an apicurio-scoped policy to the wrong one
# would be silent -- it grants nothing that account uses, so nothing breaks
# and nothing tells you.
actual="$(aws sts get-caller-identity --query Account --output text)"
[ "$actual" = "$ACCOUNT" ] || die "wrong account: credentials are for $actual, expected $ACCOUNT. Run: eval \$(aws-runas -e dev)"
ok "account $ACCOUNT"

aws iam get-role --role-name "$ROLE" >/dev/null 2>&1 || die "role $ROLE not found in $ACCOUNT"
ok "role $ROLE exists"

# ── Rollback ──────────────────────────────────────────────────────────────────

if [ "$MODE" = rollback ]; then
  step "Rollback: detach then delete"
  # Order matters: a policy with attachments cannot be deleted.
  if aws iam list-attached-role-policies --role-name "$ROLE" \
       --query "AttachedPolicies[?PolicyName=='${POLICY_NAME}'] | length(@)" --output text | grep -qx 1; then
    aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$POLICY_ARN"
    ok "detached from $ROLE"
  else
    ok "not attached to $ROLE (nothing to detach)"
  fi
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    # Non-default versions must go first, or delete-policy refuses.
    for v in $(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
                 --query 'Versions[?!IsDefaultVersion].VersionId' --output text); do
      aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v"
      ok "deleted non-default version $v"
    done
    aws iam delete-policy --policy-arn "$POLICY_ARN"
    ok "deleted $POLICY_NAME"
  else
    ok "$POLICY_NAME does not exist (nothing to delete)"
  fi
  echo ""
  echo "Rolled back. Nothing else on $ROLE changed — the document contained no Deny,"
  echo "so this only un-granted the apicurio-scoped actions."
  exit 0
fi

# ── Create or update ──────────────────────────────────────────────────────────

if [ "$MODE" != verify ]; then
  step "Policy $POLICY_NAME"
  if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    live_ver="$(aws iam get-policy --policy-arn "$POLICY_ARN" --query 'Policy.DefaultVersionId' --output text)"
    aws iam get-policy-version --policy-arn "$POLICY_ARN" --version-id "$live_ver" \
      --query 'PolicyVersion.Document' --output json > /tmp/uif-apicurio-live-policy.json
    if python3 -c "
import json,sys
live=json.load(open('/tmp/uif-apicurio-live-policy.json'))
want=json.load(open('$DOC'))
sys.exit(0 if json.dumps(live,sort_keys=True)==json.dumps(want,sort_keys=True) else 1)
"; then
      ok "exists and already matches the tracked document ($live_ver)"
    else
      warn "exists but differs from the tracked document"
      if [ "$MODE" = dry ]; then
        would "create a new default version from $DOC"
      else
        # Five versions is the hard limit. Prune the oldest non-default first
        # rather than failing on the sixth edit.
        count="$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" --query 'length(Versions)' --output text)"
        # Assert it is a number. `[ "$count" -ge 5 ]` on a non-numeric value
        # returns false rather than failing, so a malformed count would skip
        # the prune silently and the create below would then fail with
        # LimitExceeded -- an error that says nothing about the real cause.
        case "$count" in
          ''|*[!0-9]*) die "could not read the policy version count (got: '$count')" ;;
        esac
        if [ "$count" -ge 5 ]; then
          oldest="$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
                     --query 'Versions[?!IsDefaultVersion] | [-1].VersionId' --output text)"
          aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$oldest"
          ok "pruned oldest non-default version $oldest (limit is 5)"
        fi
        newver="$(aws iam create-policy-version --policy-arn "$POLICY_ARN" \
                    --policy-document "file://$DOC" --set-as-default \
                    --query 'PolicyVersion.VersionId' --output text)"
        ok "new default version $newver"
      fi
    fi
  else
    if [ "$MODE" = dry ]; then
      would "create $POLICY_NAME from $DOC"
    else
      aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document "file://$DOC" \
        --description "Apicurio dev stack deploy grants for ${ROLE}. Closes the seven gaps in stacks/apicurio/DEPLOY-DEV.md Phase 3 (UIFPE-36)." \
        --tags Key=Project,Value=uif-apicurio Key=Env,Value=dev \
               Key=ManagedBy,Value=manual Key=Issue,Value=UIFPE-36 \
        --query 'Policy.Arn' --output text >/dev/null
      ok "created $POLICY_ARN"
    fi
  fi

  step "Attachment to $ROLE"
  attached="$(aws iam list-attached-role-policies --role-name "$ROLE" \
               --query "AttachedPolicies[?PolicyName=='${POLICY_NAME}'] | length(@)" --output text)"
  if [ "$attached" = "1" ]; then
    ok "already attached"
  elif [ "$MODE" = dry ]; then
    would "attach $POLICY_ARN to $ROLE"
  else
    aws iam attach-role-policy --role-name "$ROLE" --policy-arn "$POLICY_ARN"
    ok "attached"
  fi

  total="$(aws iam list-attached-role-policies --role-name "$ROLE" --query 'length(AttachedPolicies)' --output text)"
  # Read the quota rather than asserting it. The AWS default is 10 and this
  # account is raised to 20; hardcoding 10 understated the headroom by half,
  # which is the wrong direction for a number someone might plan against.
  quota="$(aws iam get-account-summary --query 'SummaryMap.AttachedPoliciesPerRoleQuota' --output text 2>/dev/null || echo '?')"
  # In dry-run the attach has not happened, so `total` is the state BEFORE, not
  # after. Saying "now carries" in that mode reads as an outcome and is wrong by
  # one -- exactly the kind of off-by-one that makes someone think a slot was
  # consumed when it was not.
  if [ "$MODE" = dry ]; then
    ok "$ROLE currently carries $total of $quota managed-policy slots; would become $((total + 1))"
  else
    ok "$ROLE now carries $total of $quota managed-policy slots"
  fi

  if [ "$MODE" = dry ]; then
    echo ""
    echo "Dry run — nothing was changed. Re-run without --dry-run to apply."
    exit 0
  fi
fi

# ── Verify against the attached role ─────────────────────────────────────────
# Against the ROLE, not against the document. The document was already proven
# with --policy-input-list before anything was attached; what is unproven until
# here is that attaching it had the effect the simulation predicted.

step "Verify: actions that must now be ALLOWED"
must_allow=(
  "rds:CreateDBInstance|arn:aws:rds:${REGION}:${ACCOUNT}:db:uif-apicurio-dev-registry-db"
  "rds:CreateDBSubnetGroup|arn:aws:rds:${REGION}:${ACCOUNT}:subgrp:uif-apicurio-dev-registry-db-subnet-group"
  "ecs:CreateService|arn:aws:ecs:${REGION}:${ACCOUNT}:service/uif-apicurio-dev-cluster/uif-apicurio-dev-registry-service"
  "ecr:CreateRepository|arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-dev-bff-ecr-repo"
  "ecr:PutImage|arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-dev-bff-ecr-repo"
  # Added 2026-09-20. The push side was already covered; reading a pushed
  # image's digest back was not, and its absence failed the first real release:
  # deploy-apicurio-dev.yml built, scanned and pushed the image, then could not
  # describe it, and release-apicurio.yml reported the image as missing. Three
  # call sites need it -- two in the deploy, one in the release.
  "ecr:DescribeImages|arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-dev-bff-ecr-repo"
  # Added when PassApicurioTaskRoles/GetDownloadUrlForLayer closed Phase 7's IAM
  # blockers (2026-09-15) -- the base-python mirror repo, not the bff repo
  # checked above, since this is specifically the pull-side action `docker
  # build FROM` needs against the SIF mirror.
  "ecr:GetDownloadUrlForLayer|arn:aws:ecr:${REGION}:${ACCOUNT}:repository/uif-apicurio-dev-base-python-ecr-repo"
  "s3:CreateBucket|arn:aws:s3:::uif-apicurio-dev-ui"
  "s3:PutObject|arn:aws:s3:::uif-apicurio-dev-ui/*"
  "cloudfront:CreateDistributionWithTags|*"
  "secretsmanager:CreateSecret|arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:uif/apicurio/dev/auth/ci-abcdef"
)
allow_fails=0
deny_fails=0
for pair in "${must_allow[@]}"; do
  action="${pair%%|*}"; res="${pair##*|}"
  d="$(aws iam simulate-principal-policy \
        --policy-source-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
        --action-names "$action" --resource-arns "$res" \
        --query 'EvaluationResults[0].EvalDecision' --output text)"
  if [ "$d" = "allowed" ]; then ok "$action"
  else echo "  ✗ $action -> $d  ($res)" >&2; allow_fails=$((allow_fails+1)); fi
done

# iam:PassRole is checked separately, not folded into must_allow above: the
# PassApicurioTaskRoles statement is conditional on iam:PassedToService, and
# simulate-principal-policy only evaluates that condition when it is supplied
# via --context-entries. Without it, the condition reads as unmet and this
# would show a false implicitDeny even with the grant live -- omitting the
# entry here is not a safe simplification, it is a wrong test.
step "Verify: iam:PassRole (conditional on iam:PassedToService)"
pr_role="arn:aws:iam::${ACCOUNT}:role/uif-apicurio-dev-bff-execution-role"
d="$(aws iam simulate-principal-policy \
      --policy-source-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
      --action-names iam:PassRole --resource-arns "$pr_role" \
      --context-entries "ContextKeyName=iam:PassedToService,ContextKeyType=string,ContextKeyValues=ecs-tasks.amazonaws.com" \
      --query 'EvaluationResults[0].EvalDecision' --output text)"
if [ "$d" = "allowed" ]; then ok "iam:PassRole (to ecs-tasks.amazonaws.com)"
else echo "  ✗ iam:PassRole -> $d  ($pr_role)" >&2; allow_fails=$((allow_fails+1)); fi

step "Verify: boundaries THIS policy is responsible for"
# The point of a least-privilege document. If one of these flips to allowed,
# this document is wider than intended -- that is a blocking failure, because
# it is ours to fix.
must_deny_own=(
  # This one was contested and is not any more. deploy-apicurio-dev.yml's smoke
  # job used to read the value here, which would have needed GetSecretValue on
  # this role; rather than widen the policy to make a test pass, the job now
  # takes CI's copy of the key from the APICURIO_CI_KEY Actions secret and does
  # not call Secrets Manager at all. So nothing in the repo now needs this
  # denial flipped, and a future request to flip it should be read as a request
  # to re-open that decision.
  "secretsmanager:GetSecretValue|arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:uif/apicurio/dev/auth/ci-abcdef|the CI authorizer secret -- only the function's own role may read it"
  "ec2:ModifySecurityGroupRules|arn:aws:ec2:${REGION}:${ACCOUNT}:security-group/sg-0b45c554b51638786|the shared Echo group -- keeps Phase 0b's replace_triggered_by load-bearing"
)
for triple in "${must_deny_own[@]}"; do
  action="${triple%%|*}"; rest="${triple#*|}"; res="${rest%%|*}"; why="${rest##*|}"
  d="$(aws iam simulate-principal-policy \
        --policy-source-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
        --action-names "$action" --resource-arns "$res" \
        --query 'EvaluationResults[0].EvalDecision' --output text)"
  case "$d" in
    implicitDeny|explicitDeny) ok "$action still $d — $why" ;;
    *) echo "  ✗ $action is $d and should not be — $why" >&2; deny_fails=$((deny_fails+1)) ;;
  esac
done

step "Report: pre-existing posture of the shared role (not blocking)"
# These were true before this policy existed and this policy cannot revoke
# them -- it adds no Deny and no iam: action at all. They are reported every
# run, with the granting policy named, because a shared CI role that can
# delete itself is worth knowing about; they do not fail the gate, because
# blocking the Apicurio deploy on somebody else's policy helps nobody and
# would only teach people to skip the verification.
#
# The first version of this script asserted these as blocking, which was the
# wrong call: it made the gate fail forever on a condition it had no power to
# change.
inherited=(
  "iam:DeleteRole|arn:aws:iam::${ACCOUNT}:role/${ROLE}|this role can delete itself"
  "iam:PutRolePolicy|arn:aws:iam::${ACCOUNT}:role/${ROLE}|this role can rewrite its own inline policies"
  "iam:DeleteRole|arn:aws:iam::${ACCOUNT}:role/uif-beacon-dev-anything|it can delete any uif-*-dev* role, including other stacks'"
)
for triple in "${inherited[@]}"; do
  action="${triple%%|*}"; rest="${triple#*|}"; res="${rest%%|*}"; why="${rest##*|}"
  d="$(aws iam simulate-principal-policy \
        --policy-source-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
        --action-names "$action" --resource-arns "$res" \
        --query 'EvaluationResults[0].EvalDecision' --output text)"
  if [ "$d" = "allowed" ]; then
    src="$(aws iam simulate-principal-policy \
            --policy-source-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
            --action-names "$action" --resource-arns "$res" \
            --query 'EvaluationResults[0].MatchedStatements[].SourcePolicyId' --output text 2>/dev/null | tr '\t' ',')"
    warn "$action allowed on ${res##*/} — $why. Granted by: ${src:-unknown}"
    inherited_findings=$((${inherited_findings:-0}+1))
  else
    ok "$action is $d on ${res##*/} — not granted"
  fi
done

echo ""
if [ "$allow_fails" -ne 0 ] || [ "$deny_fails" -ne 0 ]; then
  echo "✗ $allow_fails not-allowed, $deny_fails not-denied."
  echo ""
  if [ "$allow_fails" -ne 0 ]; then
    echo "Something the apply NEEDS is still denied. Do not widen the policy to make it"
    echo "pass: the likeliest cause is a resource name that moved since the document was"
    echo "written, so re-run terraform plan and compare the real names against the ARNs"
    echo "above. Second likeliest is an SCP or a resource-based policy, neither of which"
    echo "simulate-principal-policy evaluates."
  fi
  if [ "$deny_fails" -ne 0 ]; then
    echo ""
    echo "A boundary THIS document is responsible for has opened. That is the more"
    echo "serious direction, and unlike the pre-existing grants reported separately it is"
    echo "ours to fix: --rollback, narrow the offending statement, re-run. Check whether"
    echo "the resource ARN pattern widened -- the likeliest cause is a wildcard matching"
    echo "more than intended, which is how the security-group-rule companion statement"
    echo "silently granted the shared group during development."
  fi
  exit 1
fi
echo "✓ Phase 3 is done; Phase 4 (apply) is unblocked."
echo ""
if [ "${inherited_findings:-0}" -ne 0 ]; then
  echo "${inherited_findings} pre-existing finding(s) reported above, NOT introduced by this"
  echo "policy and not fixable by it — this document adds no Deny and no iam: action at all."
  echo "They belong in the infra remediation backlog against the shared role, not here."
  echo ""
fi
echo "Note the limits of this verification, so a green result is not over-read:"
echo "  - simulate-principal-policy evaluates identity policies only. SCPs and"
echo "    resource-based policies are not covered."
echo "  - ec2:ModifySecurityGroupRules is not exercised by the first apply at all;"
echo "    68 creates means every rule goes through Authorize. It earns its place on"
echo "    the second apply."
