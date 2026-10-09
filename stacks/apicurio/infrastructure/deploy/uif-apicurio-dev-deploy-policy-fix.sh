#!/usr/bin/env bash
# Applies capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json as a
# new default version of the live uif-apicurio-dev-deploy-policy managed
# policy. Closes the two gaps the first real `infra=apply` run hit on
# 2026-09-15 (run 34955881761):
#
#   1. s3:GetBucketAcl on the UI bucket -- the aws_s3_bucket resource's own
#      post-create read calls GetBucketAcl to populate its computed `grant`
#      attribute. The bucket was created (verified: `aws s3api head-bucket`
#      succeeded) and Terraform DID keep it in state despite the read
#      failing, but every future plan/apply refreshes this resource and will
#      hit the same AccessDenied until this is granted.
#   2. kms:DescribeKey / kms:CreateGrant on the account's default RDS key
#      (alias/aws/rds, key 586f7909-a547-4870-aac0-03f2eae2ce72) -- AWS
#      requires the calling principal to have these on the CMK for
#      storage_encrypted = true, even when using the default (unnamed) key.
#      Its absence surfaced as CreateDBInstance returning
#      "KMSKeyNotAccessibleFault: The specified key [null]", which reads like
#      a missing/invalid key rather than a missing grant on the caller.
#      kms:ListGrants and kms:RevokeGrant are added alongside DescribeKey
#      (no grant condition needed for read-only calls); CreateGrant alone
#      carries kms:GrantIsForAWSResource = true, since RDS is the one calling
#      CreateGrant on this principal's behalf, not this principal calling it
#      directly for its own use.
#
# UPDATE 2026-09-15 (run 34957923464): the same grants are also required on
# the account's default secretsmanager key (53b27019-ed92-4ad9-9d7a-fa71b051e1f0),
# because manage_master_user_password = true has RDS create a Secrets
# Manager secret inside the same CreateDBInstance call, encrypted with that
# key since no master_user_secret_kms_key_id is set. Both keys are now
# checked below.
#
# UPDATE 2026-09-15 (run 34959950071): once the KMS grants were live, the
# same CreateDBInstance call failed again with a much more direct error --
# "The user isn't authorized to create a secret in AWS Secrets Manager" --
# because the CALLER's own identity needs secretsmanager:CreateSecret too,
# and the existing CiSecretContainerNoValueAccess statement only scoped that
# action to this stack's own uif/apicurio/dev/auth/* path. RDS's managed
# secret gets an AWS-assigned name (rds!db-<resource-id>, per rds.tf's own
# comment) that doesn't match that prefix. RdsManagedSecretNoValueAccess
# grants the identical action set (no GetSecretValue -- same "no value
# access" posture) scoped to rds!db-* instead.
#
# UPDATE 2026-09-15 (run 34961459171): CreateDBInstance itself finally
# succeeded (confirmed live: the instance reached "available"), but
# Terraform's own post-create read failed: "reading RDS DB Instance
# (uif-apicurio-dev-registry-db): ... not authorized to perform:
# rds:DescribeDBInstances on resource: arn:...:db:*" -- note the resource
# IAM evaluated the call against is a bare `db:*`, not the specific
# instance ARN the existing scoped Rds statement already covers. That
# means the call itself carries no instance identifier IAM can attribute a
# scoped resource to, the same class of problem this policy already has a
# precedent for (EcrAuthUnscopable, CloudFrontCreateWithTagsUnscopable).
# rds:DescribeDBSubnetGroups did NOT hit this (its own create+read cycle
# succeeded earlier in the same run under the scoped Rds statement) --
# so this is specific to DescribeDBInstances, not a "give up on scoping
# RDS reads" precedent. RdsDescribeUnscopable grants only this one action,
# Resource: "*", read-only.
#
# UPDATE 2026-09-15 (UI WAF): drafted ahead of the first apply this time,
# not reactively after a failure -- waf.tf adds a CLOUDFRONT-scope
# aws_wafv2_web_acl + aws_wafv2_ip_set restricting the UI's distribution to
# var.api_allowed_cidrs, and this role had zero wafv2:* permissions
# (confirmed implicitDeny before drafting). UiWaf grants the same
# create/get/update/delete/tag lifecycle every other resource type in this
# policy gets, scoped to the two real ARNs
# (arn:...:wafv2:us-east-1:...:global/{webacl,ipset}/uif-apicurio-dev-ui*/*)
# -- format confirmed against an existing WebACL already in this account,
# not assumed.
#
# UPDATE 2026-09-15 (Pearson WAF & IP Sanctions compliance ruleset): adds
# the sanctions rule group (Firewall-Manager-seeded, looked up by name --
# confirmed implicitDeny on wafv2:ListRuleGroups against "*" and
# wafv2:GetRuleGroup against its specific ARN before drafting either
# grant, same discipline as every other fix here) and WAF logging to a new
# CloudWatch log group. logs:CreateLogGroup/DeleteLogGroup/
# PutRetentionPolicy/TagResource/ListTagsForResource were ALREADY granted
# by some other policy attached to this role (confirmed allowed against
# the real log group ARN) -- only wafv2:{Put,Get,Delete}LoggingConfiguration
# on the web ACL and the two rule-group-lookup actions were missing.
# logs:DescribeLogGroups looked denied against the specific log group ARN
# on a first test, but that is the same "list action needs Resource: *"
# trap ecr:GetAuthorizationToken and rds:DescribeDBInstances already hit
# in this policy -- simulating it against literal "*" showed allowed, so
# no new grant was needed there.
#
# UPDATE 2026-09-15 (the same ruleset's first real apply): UpdateWebACL
# failed anyway -- "not authorized to perform: wafv2:UpdateWebACL on
# resource: arn:...:global/managedruleset/*/*". A web ACL that REFERENCES
# an AWS managed rule group or an external rule group (the PCM sanctions
# group here) needs Create/Update/GetWebACL granted against THAT
# referenced resource type too, separately from the ACL's own ARN --
# confirmed implicitDeny on both the managedruleset wildcard and the
# sanctions rule group's own ARN before drafting UiWafReferencedRuleGroups.
# ip_set_reference_statement never hit this (the earlier apply that added
# allow-configured-cidrs worked with no such error) -- it is specific to
# rule groups, AWS-managed or not, not a pattern every statement type in
# a web ACL triggers.
#
# This policy is NOT Terraform-managed (same as the first fix on this
# branch -- verified again: still no aws_iam_policy resource for it anywhere
# in this repo) -- it's attached by hand to the shared echo-dev-github-actions
# CI role, so the fix is this script, not `terraform apply`.
#
# Requires: aws-runas (credentials for the dev account), jq.
# Run from anywhere; paths below are resolved relative to this script.
#
# Usage:
#   ./uif-apicurio-dev-deploy-policy-fix.sh            # show the diff, ask, then apply
#   ./uif-apicurio-dev-deploy-policy-fix.sh --dry-run  # show the diff and stop

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_FILE="$SCRIPT_DIR/../../../../capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json"
POLICY_NAME="uif-apicurio-dev-deploy-policy"
ROLE_NAME="echo-dev-github-actions"
AWS_RUNAS="/Users/raj9karu/.aws/aws-runas"
PROFILE="pearson-esb-dev"
RDS_KMS_KEY_ARN="arn:aws:kms:eu-west-1:283556840805:key/586f7909-a547-4870-aac0-03f2eae2ce72"
SECRETSMANAGER_KMS_KEY_ARN="arn:aws:kms:eu-west-1:283556840805:key/53b27019-ed92-4ad9-9d7a-fa71b051e1f0"
UI_BUCKET_ARN="arn:aws:s3:::uif-apicurio-dev-ui"
RDS_MANAGED_SECRET_ARN="arn:aws:secretsmanager:eu-west-1:283556840805:secret:rds!db-test"
UI_WAF_WEBACL_ARN="arn:aws:wafv2:us-east-1:283556840805:global/webacl/uif-apicurio-dev-ui/4c7a4050-dfc4-433b-8a0d-a41c0dc9f9d1"
UI_WAF_IPSET_ARN="arn:aws:wafv2:us-east-1:283556840805:global/ipset/uif-apicurio-dev-ui-allowed-ips/test"
PCM_SANCTIONS_RULEGROUP_ARN="arn:aws:wafv2:us-east-1:283556840805:global/rulegroup/PCMSanctionsRuleGroupCloudFront/75faf7da-d3d7-489c-a0d5-3d61bd78db03"
DRY_RUN="${1:-}"

run() { "$AWS_RUNAS" "$PROFILE" "$@" </dev/null; }

if [ ! -f "$POLICY_FILE" ]; then
  echo "ERROR: $POLICY_FILE not found." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required." >&2
  exit 1
fi

echo "==> Validating $POLICY_FILE is well-formed JSON"
jq empty "$POLICY_FILE"

echo "==> Resolving policy ARN for $POLICY_NAME"
POLICY_ARN=$(run aws iam list-policies --scope Local \
  --query "Policies[?PolicyName=='${POLICY_NAME}'].Arn" --output text)
if [ -z "$POLICY_ARN" ] || [ "$POLICY_ARN" = "None" ]; then
  echo "ERROR: policy $POLICY_NAME not found." >&2
  exit 1
fi
echo "    $POLICY_ARN"

CURRENT_VERSION=$(run aws iam get-policy --policy-arn "$POLICY_ARN" \
  --query "Policy.DefaultVersionId" --output text)
echo "==> Current default version: $CURRENT_VERSION"

CURRENT_DOC=$(mktemp)
run aws iam get-policy-version --policy-arn "$POLICY_ARN" \
  --version-id "$CURRENT_VERSION" --query "PolicyVersion.Document" --output json \
  > "$CURRENT_DOC"

echo
echo "==> Diff: live ($CURRENT_VERSION) vs $POLICY_FILE"
echo "----------------------------------------------------------------"
diff <(jq -S . "$CURRENT_DOC") <(jq -S . "$POLICY_FILE") && \
  { echo "No differences -- nothing to apply."; rm -f "$CURRENT_DOC"; exit 0; } || true
echo "----------------------------------------------------------------"

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo "Dry run only -- not applying."
  rm -f "$CURRENT_DOC"
  exit 0
fi

echo
read -r -p "Apply this as the new default version of $POLICY_NAME? [y/N] " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "Aborted."
  rm -f "$CURRENT_DOC"
  exit 1
fi

# A managed policy keeps at most 5 versions. Prune the oldest non-default one
# if we're already at the cap, or create-policy-version fails with
# LimitExceeded instead of a clear "delete something first" message.
VERSION_COUNT=$(run aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
  --query "length(Versions)" --output text)
if [ "$VERSION_COUNT" -ge 5 ]; then
  OLDEST_NON_DEFAULT=$(run aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
    --query "Versions[?IsDefaultVersion==\`false\`] | sort_by(@, &CreateDate)[0].VersionId" \
    --output text)
  echo "==> At the 5-version cap; deleting oldest non-default version $OLDEST_NON_DEFAULT"
  run aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$OLDEST_NON_DEFAULT"
fi

echo "==> Creating new default policy version"
NEW_VERSION=$(run aws iam create-policy-version --policy-arn "$POLICY_ARN" \
  --policy-document "file://$POLICY_FILE" --set-as-default \
  --query "PolicyVersion.VersionId" --output text)
echo "    New default version: $NEW_VERSION"

echo
echo "==> Verifying the two gaps are closed"

echo "-- s3:GetBucketAcl, against the UI bucket"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names s3:GetBucketAcl \
  --resource-arns "$UI_BUCKET_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- kms:DescribeKey / kms:CreateGrant, against the default RDS key AND the"
echo "   default secretsmanager key (manage_master_user_password's secret uses"
echo "   the latter, and needs the same caller grants as the storage key)."
echo "   (CreateGrant needs its own --context-entries: the condition checks"
echo "   kms:GrantIsForAWSResource, which is only present when a service --"
echo "   here, RDS -- makes the grant on this principal's behalf. Omitting it"
echo "   evaluates the condition as unmet and shows a false 'implicitDeny'.)"
for KEY_ARN in "$RDS_KMS_KEY_ARN" "$SECRETSMANAGER_KMS_KEY_ARN"; do
  run aws iam simulate-principal-policy \
    --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
    --action-names kms:DescribeKey \
    --resource-arns "$KEY_ARN" \
    --query 'EvaluationResults[].{Action:EvalActionName,Resource:EvalResourceName,Decision:EvalDecision}' --output table
  run aws iam simulate-principal-policy \
    --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
    --action-names kms:CreateGrant \
    --resource-arns "$KEY_ARN" \
    --context-entries "ContextKeyName=kms:GrantIsForAWSResource,ContextKeyType=boolean,ContextKeyValues=true" \
    --query 'EvaluationResults[].{Action:EvalActionName,Resource:EvalResourceName,Decision:EvalDecision}' --output table
done

echo "-- secretsmanager:CreateSecret, against RDS's own managed-secret name prefix"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names secretsmanager:CreateSecret \
  --resource-arns "$RDS_MANAGED_SECRET_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- rds:DescribeDBInstances, unscoped (the SDK's own poll call carries no"
echo "   instance identifier IAM can attribute a specific resource to)"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names rds:DescribeDBInstances \
  --resource-arns "arn:aws:rds:eu-west-1:283556840805:db:uif-apicurio-dev-registry-db" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- wafv2:CreateWebACL / CreateIPSet, against the UI's WAF resources (us-east-1)"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:CreateWebACL \
  --resource-arns "$UI_WAF_WEBACL_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:CreateIPSet \
  --resource-arns "$UI_WAF_IPSET_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- wafv2:PutLoggingConfiguration, against the UI web ACL"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:PutLoggingConfiguration \
  --resource-arns "$UI_WAF_WEBACL_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- wafv2:GetRuleGroup, against PCM's Firewall-Manager-seeded sanctions group"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:GetRuleGroup \
  --resource-arns "$PCM_SANCTIONS_RULEGROUP_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- wafv2:UpdateWebACL, against BOTH the AWS-managed-ruleset wildcard AND the"
echo "   PCM rule group -- a web ACL referencing either needs a SEPARATE grant"
echo "   against that referenced resource type, not just against the ACL's own ARN"
echo "   (surfaced live: UpdateWebACL denied with the failing resource named as"
echo "   arn:...:global/managedruleset/*/* in the error, not the ACL's ARN)"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:UpdateWebACL \
  --resource-arns "arn:aws:wafv2:us-east-1:283556840805:global/managedruleset/AWSManagedRulesCommonRuleSet/AWS" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:UpdateWebACL \
  --resource-arns "$PCM_SANCTIONS_RULEGROUP_ARN" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

echo "-- wafv2:ListRuleGroups, unscoped (a list action -- must be simulated against"
echo "   literal \"*\" or it shows a false implicitDeny, same trap as"
echo "   ecr:GetAuthorizationToken and rds:DescribeDBInstances above)"
run aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::283556840805:role/${ROLE_NAME}" \
  --action-names wafv2:ListRuleGroups \
  --resource-arns "*" \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --output table

rm -f "$CURRENT_DOC"
echo
echo "Done. Every row above should read 'allowed'."
