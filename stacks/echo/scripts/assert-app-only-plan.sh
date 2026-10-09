#!/usr/bin/env bash
# Prove a Terraform plan touches only the application layer.
#
# DIVERGED FROM stacks/apicurio/scripts/assert-app-only-plan.sh ON 2026-09-25.
# The two files were byte-identical before that date and are NOT any more. Do
# not "re-sync" them back to identical: this copy permits a ForceNew
# replacement for aws_lambda_layer_version addresses (see the create,delete
# arm below), which Echo's promotion requires and apicurio's must not have.
#
# Why Echo needs it: every argument of aws_lambda_layer_version is ForceNew and
# lambda.tf keys s3_key off the promoted artifact digest with no
# create_before_destroy, so a promoted layer plans as create+delete. Without
# the exception the first artifact-changing promote is refused, and refused with
# a message blaming resources that are in fact allowed.
#
# Why apicurio must not have it: it ships a container image, and an ECS task
# definition updates in place. A replace there is the drift this script exists
# to catch.
#
# A shared script with a stack conditional was considered and rejected -- the
# condition would have to name a stack, which is exactly the coupling keeping
# them as separate copies avoids. If you change one, decide deliberately
# whether the other wants the same change, and update both headers.
#
# Usage:
#   terraform show -json tf.plan | assert-app-only-plan.sh <allowed-address>...
#
# Exit codes, because "nothing to do" and "confirmed" need different handling:
#   0  app-only confirmed -- apply the plan
#   3  the plan is empty -- nothing to apply, and that is not a failure
#   1  the plan touches something else, or the input was unusable
#
# WHY STDIN. A saved plan contains every generated value, so `terraform show
# -json` must never reach a log or an artifact. Piping it straight in means the
# document exists only in memory, and only resource ADDRESSES are ever printed.
#
# WHY AN UNPARSEABLE DOCUMENT IS EXIT 1 AND NOT EXIT 3. An empty change list
# and a document jq could not read look identical downstream -- both yield no
# addresses. Treating the second as "nothing to do" would let an apply proceed
# on the strength of a parse failure, so the two are told apart explicitly.
#
# WHY ACTIONS ARE CHECKED, NOT JUST ADDRESSES. Destroying aws_ecs_service.bff
# is not a promotion, even though its address is on the allowed list.
set -euo pipefail

die() { printf '%s: %s\n' "assert-app-only-plan.sh" "$1" >&2; exit 1; }

[ "$#" -ge 1 ] || die "needs at least one allowed resource address. Usage: terraform show -json tf.plan | assert-app-only-plan.sh <address>..."

document="$(cat)"
[ -n "$document" ] || die "no plan JSON on stdin. Pipe 'terraform show -json tf.plan' into this script."

# Refuse a document jq cannot read, and one whose resource_changes is missing
# or not an array -- presence of the key is not the same as having the right
# shape, and all of these would otherwise present as an empty plan once
# `.resource_changes[]` below fails and is swallowed by `|| true`.
printf '%s' "$document" | jq -e 'type == "object" and (.resource_changes | type == "array")' >/dev/null 2>&1 \
  || die "stdin is not a Terraform plan document with a resource_changes array. Refusing, because an unreadable plan and an empty plan are indistinguishable downstream."

# address<TAB>actions, one per line, for every change that is not a no-op.
changed="$(
  printf '%s' "$document" \
    | jq -r '.resource_changes[]
             | select(.change.actions != ["no-op"])
             | .address + "\t" + (.change.actions | join(","))' \
    || true
)"

if [ -z "$changed" ]; then
  printf 'Plan is empty — the running task definition already has this digest.\n'
  exit 3
fi

printf 'Plan changes:\n'
unexpected=""
while IFS="$(printf '\t')" read -r address actions; do
  [ -n "$address" ] || continue
  printf '  %s (%s)\n' "$address" "$actions"

  # Exact address match. A `case` glob would let aws_ecs_service.bff_canary
  # pass as aws_ecs_service.bff.
  address_ok="no"
  for allowed in "$@"; do
    if [ "$address" = "$allowed" ]; then
      address_ok="yes"
      break
    fi
  done

  # Only the action verbs a promotion may perform. A create or update of the
  # task definition and service is a new revision rolling out; a delete or
  # replace is not. join(",") produces "create" or "delete,create"; only
  # single safe verbs pass -- WITH ONE SCOPED EXCEPTION below.
  case "$actions" in
    create|update|no-op) action_ok="yes" ;;
    create,delete|delete,create)
      # A ForceNew replacement. Ordinarily this stays refused, same as any
      # other replace -- EXCEPT for a Lambda layer version, where it is the
      # expected shape of a promotion rather than a sign of drift. Every
      # argument of aws_lambda_layer_version is ForceNew, and the promoted
      # digest is keyed into s3_key with no create_before_destroy, so a
      # promoted layer plans as exactly create+delete. Scoped to the
      # address's resource type, not opened up for actions in general: a
      # Lambda function or a Glue job replacing itself is still exactly the
      # kind of drift this script exists to catch.
      case "$address" in
        aws_lambda_layer_version.*) action_ok="yes" ;;
        *)                          action_ok="no" ;;
      esac
      ;;
    *) action_ok="no" ;;
  esac

  if [ "$address_ok" != "yes" ] || [ "$action_ok" != "yes" ]; then
    unexpected="${unexpected} ${address}(${actions})"
  fi
done <<EOF
${changed}
EOF

if [ -n "$unexpected" ]; then
  die "this plan is not an app-only deploy and will NOT be applied unattended:${unexpected}. That is either real infrastructure work or drift introduced out of band, and both need a human reading the plan. Re-run with the infra latch on 'plan' to read it, then 'apply' to apply it deliberately."
fi

printf 'App-only deploy confirmed — nothing but the listed application resources change.\n'
exit 0
