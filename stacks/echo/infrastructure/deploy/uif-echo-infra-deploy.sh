#!/usr/bin/env bash
# Deploy Echo AWS infrastructure (Terraform only).
#
# Creates or updates API Gateway, Lambda shells, Glue jobs, EventBridge rules,
# IAM, S3 buckets, DynamoDB tables, CloudFront, etc. Does NOT build the UI —
# use uif-echo-ui-deploy.sh for that.
#
# For a legacy echo-* stack it also does NOT upload Glue job Python sources;
# use uif-echo-apps-deploy.sh. For dev-uif it stages every release artifact
# (Glue scripts included) before planning, because those hashes are plan
# inputs — see the staging step below. A promoted uif env supplies them in
# artifacts.auto.tfvars.json instead.
#
# Usage: ./uif-echo-infra-deploy.sh [ENV]
#   ENV defaults to "dev" (live echo-dev-* stack).
#   Use "dev-uif" or "uat-uif" for the parallel uif-echo-*-* greenfield stacks.
#
# Optional: UIF_ECHO_TF_MODE=plan|apply (default apply). CI sets plan.
#
# Credentials must already be active (aws-runas, aws sso login, etc.).
set -euo pipefail

ENV="${1:-dev}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_uif-echo-deploy-common.sh
source "$SCRIPT_DIR/_uif-echo-deploy-common.sh"

step "Preflight — infra deploy (ENV=$ENV)"
uif_echo_validate_env "$ENV"
uif_echo_validate_tf_mode
uif_echo_preflight_aws
ok "REGION=$AWS_REGION  STACK=stacks/echo  MAKE_ENV=$ENV  TF_MODE=$UIF_ECHO_TF_MODE"

step "Terraform init"
make -C "$REPO_ROOT/$TF_DIR" init ENV="$ENV"

# Stage the release artifacts BEFORE the plan, and in plan mode too (UIFPE-608).
#
# These hashes are plan INPUTS: they decide every script_location and every
# Lambda s3_key. Without them every key resolves to ARTIFACT-SHA-NOT-SUPPLIED,
# a path no object is at, and applying that deletes each layer version without
# republishing it and repoints the Glue jobs at a missing script. It broke dev
# twice on 2026-09-24.
#
# This script used to leave staging to uif-echo-apps-deploy.sh, which cannot
# work: deploy-uif-echo-dev.yml runs apps AFTER infra and skips it when infra
# fails, so infra could never repair what it broke. Staging is
# content-addressed and uploads only what is absent, so doing it here as well
# as there is idempotent.
#
# dev-uif ONLY, deliberately. Every other uif env takes its artifacts by
# promotion (UIFPE-555), and uif_echo_stage_release_artifacts refuses to stage
# into one -- building there would produce different hashes for the same
# release tag. Their artifact_shas arrive in a promoted
# artifacts.auto.tfvars.json instead, and artifact_guard.tf is what fails the
# plan if that file is missing.
if [[ "$ENV" == "dev-uif" ]]; then
  uif_echo_stage_release_artifacts
fi

# EVERY uif stack, not just dev-uif (UIFPE-555). The three helpers below were
# each parameterised to accept any `-uif` env, but this caller still gated on
# dev-uif -- so for uat-uif none of them ran and the fix was invisible. A
# callee that handles a case its only caller never passes it is not fixed.
#
# What that cost: uif_echo_import_phase5_artifacts imports the data bucket
# bootstrap-environment.sh pre-creates, because `terraform init` needs it to
# exist before Terraform can evaluate the resource that declares it. Skip the
# import and the first apply fails BucketAlreadyOwnedByYou, naming the bucket
# and nothing naming the missing import.
if [[ "$ENV" == *-uif && "$UIF_ECHO_TF_MODE" == "apply" ]]; then
  uif_echo_import_phase5_artifacts "$ENV"
  uif_echo_import_phase6_drift "$ENV"
  uif_echo_prefetch_gcp_glue_jars
fi

step "Terraform plan"
# Terraform exits 2 when changes are pending — expected for greenfield plan-only CI.
if ! make -C "$REPO_ROOT/$TF_DIR" plan ENV="$ENV"; then
  plan_ec=$?
  if [[ "$UIF_ECHO_TF_MODE" == "plan" && "$plan_ec" -eq 2 ]]; then
    ok "Plan complete with pending changes (exit 2 — normal for greenfield)."
  else
    exit "$plan_ec"
  fi
fi

if [[ "$UIF_ECHO_TF_MODE" == "plan" ]]; then
  ok "Plan complete (UIF_ECHO_TF_MODE=plan — no apply)."
  exit 0
fi

step "Terraform apply"
make -C "$REPO_ROOT/$TF_DIR" apply ENV="$ENV"

step "Infra deploy complete"
API_URL=$(uif_echo_tf_out api_endpoint "—")
UI_URL=$(uif_echo_tf_out ui_cloudfront_url "—")
echo -e "  API : ${BOLD}${API_URL}${RESET}"
echo -e "  UI  : ${BOLD}${UI_URL}${RESET}  (static assets unchanged until uif-echo-ui-deploy.sh)"
if [[ "$ENV" == *-uif ]]; then
  ok "Glue scripts came from the release artifacts read by this plan, not an s3 sync."
else
  warn "Run uif-echo-apps-deploy.sh next — Glue scripts are not uploaded by Terraform."
fi
