#!/usr/bin/env bash
# Prove a Terraform plan touches only the application layer.
#
# DIVERGED FROM stacks/echo/scripts/assert-app-only-plan.sh ON 2026-09-25.
# The two files were byte-identical before that date and are NOT any more.
# Echo's copy permits a ForceNew replacement for aws_lambda_layer_version
# addresses, because every argument of that resource is ForceNew and Echo keys
# the layer's s3_key off a promoted artifact digest -- so a promoted layer
# plans as create+delete and would otherwise be refused.
#
# THIS copy deliberately does not have that exception, and should not gain it.
# apicurio ships a container image; an ECS task definition updates in place, so
# a replacement here is the drift this script exists to catch.
#
# Do not "re-sync" the two files back to identical. If you change this one,
# decide deliberately whether Echo's wants the same change, and update both
# headers.
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

  # Only the action verbs a promotion may perform.
  #
  # An ECS TASK DEFINITION IS IMMUTABLE in AWS. The image digest lives inside
  # container_definitions, which cannot be changed in place, so Terraform
  # registers a new revision, deregisters the old one, and reports that as a
  # REPLACEMENT -- "delete,create", or "create,delete" under a
  # create_before_destroy lifecycle. That pair IS the new revision rolling
  # out, and it is what every promotion that changes the digest looks like.
  #
  # Refusing it refused every real deploy. This script's exit-0 path had never
  # once run: the only promotion that ever passed did so on the empty-plan
  # branch, with nothing to apply. See UIFPE-614.
  #
  # A SERVICE is mutable, so rolling a new revision onto it is an "update".
  # Replacing or destroying a service drops the running workload, so no verb
  # pair is acceptable there -- only the single safe verbs.
  #
  # The distinction is a property of the AWS RESOURCE TYPE rather than of this
  # stack, which is why it is decided here from the address rather than passed
  # in by the caller: a caller cannot get it wrong, and a future allowed
  # address inherits the right rule automatically.
  case "$address" in
    aws_ecs_task_definition.*) replaceable="yes" ;;
    *)                         replaceable="no" ;;
  esac

  # join(",") produces "create", "update", "delete", or a replacement pair.
  # A bare "delete" stays refused for both: it deregisters what is running and
  # puts nothing back.
  case "$actions" in
    create|update|no-op) action_ok="yes" ;;
    delete,create|create,delete) action_ok="$replaceable" ;;
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
