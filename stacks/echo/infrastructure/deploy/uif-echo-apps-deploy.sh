#!/usr/bin/env bash
# Deploy Echo application code (Glue scripts, Lambda zips, layer artifacts).
#
# Terraform tracks Lambda deployment packages and some S3 objects via filemd5(),
# so this script runs plan+apply after syncing sources. For a Glue-only hotfix
# with no Lambda or layer changes, set SKIP_TERRAFORM=1.
#
# Usage: ./uif-echo-apps-deploy.sh [ENV]
#   ENV defaults to "dev". Use "dev-uif" for the uif-echo-dev-* stack.
#   Staging is dev-uif only: every other uif stack takes its artifacts by
#   promotion, and this script refuses to build into one.
#
# Optional environment variables:
#   UIF_ECHO_TF_MODE=plan|apply   Default apply; CI uses plan for dry-run
#   BUILD_LAYERS=1                Rebuild gcs/silver-bq/controller-bq layer zips
#   SKIP_TERRAFORM=1              Only sync Glue scripts (skip plan/apply)
#
# Credentials must already be active.
set -euo pipefail

ENV="${1:-dev}"
BUILD_LAYERS="${BUILD_LAYERS:-0}"
SKIP_TERRAFORM="${SKIP_TERRAFORM:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_uif-echo-deploy-common.sh
source "$SCRIPT_DIR/_uif-echo-deploy-common.sh"

step "Preflight — apps deploy (ENV=$ENV)"
uif_echo_validate_env "$ENV"
uif_echo_validate_tf_mode
uif_echo_preflight_aws
ok "REGION=$AWS_REGION  TF_MODE=$UIF_ECHO_TF_MODE  BUILD_LAYERS=$BUILD_LAYERS  SKIP_TERRAFORM=$SKIP_TERRAFORM"

if [[ "$BUILD_LAYERS" == "1" ]]; then
  uif_echo_build_layers
  uif_echo_upload_layers
fi

step "Ensure Terraform state is initialised"
make -C "$REPO_ROOT/$TF_DIR" init ENV="$ENV"

# BEFORE the plan, not between plan and apply. These hashes decide every
# script_location and every Lambda s3_key, so a plan produced without them
# shows every job pointing at ARTIFACT-SHA-NOT-SUPPLIED. No-op on a legacy
# echo-* stack. (UIFPE-515)
uif_echo_stage_release_artifacts

if [[ "$SKIP_TERRAFORM" == "1" ]]; then
  step "Upload Glue scripts (SKIP_TERRAFORM=1)"
  uif_echo_sync_glue_scripts
  warn "SKIP_TERRAFORM=1 — Lambda zip changes were NOT applied."
  exit 0
fi

# The same prefetch uif-echo-infra-deploy.sh does. This script also runs a FULL
# terraform plan+apply, so it needs the GCP Glue jars on disk too:
# aws_s3_object.gcp_glue_jars reads its source file, and a CI runner starts with
# an empty .cache/jars. Without this, `apps=apply` with `infra=skip` fails at
#   opening S3 object source (./.cache/jars/gcs-connector-...jar): no such file
# which is what happened on run 34783384579.
uif_echo_prefetch_gcp_glue_jars

step "Terraform plan (pick up Lambda / layer / S3 object changes)"
make -C "$REPO_ROOT/$TF_DIR" plan ENV="$ENV"

if [[ "$UIF_ECHO_TF_MODE" == "plan" ]]; then
  ok "Plan complete (UIF_ECHO_TF_MODE=plan — no script sync or apply)."
  exit 0
fi

step "Upload Glue scripts"
uif_echo_sync_glue_scripts

step "Terraform apply"
make -C "$REPO_ROOT/$TF_DIR" apply ENV="$ENV"

step "Apps deploy complete"
ok "Glue scripts synced; Lambda and tracked S3 objects updated."
