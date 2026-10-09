#!/usr/bin/env bash
# Print every resource address a PROMOTE is allowed to change, for one root.
#
# assert-app-only-plan.sh takes the permitted addresses as arguments and
# refuses a plan that touches anything else. apicurio passes two by hand --
# aws_ecs_task_definition.bff and aws_ecs_service.bff -- because a container
# stack's whole application surface is those two resources.
#
# Echo's is 33 across two roots, and a hand-written list of 33 goes stale the
# first time somebody adds a Lambda. The stale direction is not the dangerous
# one -- assert-app-only-plan.sh matches addresses exactly, so a FORGOTTEN
# address makes the promote refuse rather than over-apply. It is still worth
# avoiding: a promote that refuses for a reason added two weeks earlier is a
# bad afternoon. Deriving the list removes the whole class.
#
# WHAT COUNTS AS THE APPLICATION LAYER. A resource whose plan diff on a new
# release is its artifact pointer and nothing else:
#
#   aws_lambda_function        s3_key + source_code_hash
#   aws_lambda_layer_version   s3_key + source_code_hash
#   aws_glue_job               script_location and --extra-py-files
#
# Everything else -- buckets, IAM, API Gateway, DynamoDB, EventBridge, alarms,
# Lake Formation -- is infrastructure, converges with the root, and must not
# move during a promote. The UI bundle is absent from this list on purpose: it
# is published to S3 by the deploy script, not declared in Terraform, so no
# address describes it.
#
# COUNT AND FOR_EACH. One resource uses count:
# aws_glue_job.phase0_iceberg_gcp_probe, as
#
#   count = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
#
# which is 0 in every environment a promote targets, so the resource is not
# there and printing it without an index costs nothing. Every other address is
# plain.
#
# A counted resource NOT gated off for a uif stack would be a different matter:
# its address is `name[0]`, this script would keep printing `name`, and the
# exact match would then FAIL -- the promote refuses rather than applying
# something unreviewed. The right direction to fail in, but not an obvious
# error, so qe/pipeline/test_promote_workflow.py pins the invariant.
#
# Usage:
#   app-only-addresses.sh <root-dir>
#
# Prints one address per line. Exits 1 if the root has none, because a promote
# with an empty allow-list refuses everything and would look like a plan
# problem rather than a missing argument.

set -euo pipefail

die() { printf 'app-only-addresses.sh: %s\n' "$1" >&2; exit 1; }

root="${1:?usage: app-only-addresses.sh <root-dir>}"
[ -d "$root" ] || die "${root} is not a directory"

# `resource "type" "name" {` is always one line in this repo's Terraform, and
# `terraform fmt` keeps it that way, so a line-wise match needs no HCL parser.
# Anchored at the start of the line: an occurrence inside a comment or a string
# is indented and must not match.
addresses="$(
  grep -hoE '^resource "(aws_lambda_function|aws_lambda_layer_version|aws_glue_job)" "[A-Za-z0-9_]+"' \
    "$root"/*.tf 2>/dev/null \
    | sed -E 's/^resource "([a-z0-9_]+)" "([A-Za-z0-9_]+)"$/\1.\2/' \
    | LC_ALL=C sort -u || true
)"

[ -n "$addresses" ] \
  || die "no application-layer resources found under ${root}. A promote with an empty allow-list refuses every plan, which reads like a plan problem rather than a missing argument."

printf '%s\n' "$addresses"
