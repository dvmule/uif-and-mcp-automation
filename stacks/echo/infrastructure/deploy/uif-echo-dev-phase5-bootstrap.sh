#!/usr/bin/env bash
# Phase 5 — one-time AWS bootstrap for uif-echo-dev-* (before first terraform apply).
#
# Creates:
#   s3://uif-echo-dev-data                    Shared data bucket (versioned, encrypted)
#   s3://uif-echo-dev-data/code/lambda-layers/*.zip   (copied from live echo-dev)
#
# Layout after bootstrap:
#   tfstate/     Terraform remote state (written on first init/apply)
#   code/        Glue scripts, layers, jars (layers pre-staged here)
#   configs/     Pipeline YAML/JSON (when wired)
#
# Backend uses S3 native locking (use_lockfile in backends/dev-uif.hcl).
#
# Usage:
#   eval $(aws-runas -e dev)
#   ./uif-echo-dev-phase5-bootstrap.sh
#
# On Windows (PowerShell):
#   aws-runas -E dev bash ./uif-echo-dev-phase5-bootstrap.sh
set -euo pipefail

REGION="${AWS_REGION:-eu-west-1}"
ACCOUNT="${AWS_ACCOUNT_ID:-283556840805}"
DATA_BUCKET="uif-echo-dev-data"
SOURCE_LAYERS="s3://echo-dev-glue-scripts/lambda-layers"
LAYER_PREFIX="code/lambda-layers"
LAYER_ZIPS=(oracle_instant_client gcs_layer silver_bq_layer controller_bq_layer)

step() { echo -e "\n▶ $*"; }
ok()   { echo "✓ $*"; }
die()  { echo "✗ $*" >&2; exit 1; }

step "Preflight"
command -v aws >/dev/null || die "aws CLI not found"
aws sts get-caller-identity --region "$REGION" --output text --query 'Account' \
  | grep -qx "$ACCOUNT" \
  || die "Expected account $ACCOUNT — run: eval \$(aws-runas -e dev)"

_create_bucket() {
  local name="$1"
  if aws s3api head-bucket --bucket "$name" --region "$REGION" 2>/dev/null; then
    ok "Bucket exists: $name"
    return 0
  fi
  step "Create s3://$name"
  aws s3api create-bucket \
    --bucket "$name" \
    --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION"
  aws s3api put-bucket-versioning \
    --bucket "$name" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption \
    --bucket "$name" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block \
    --bucket "$name" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  ok "Created s3://$name"
}

_create_bucket "$DATA_BUCKET"

step "Copy layer zips from live echo-dev stack"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
for z in "${LAYER_ZIPS[@]}"; do
  aws s3 cp "${SOURCE_LAYERS}/${z}.zip" "${tmpdir}/${z}.zip" --region "$REGION"
  aws s3 cp "${tmpdir}/${z}.zip" "s3://${DATA_BUCKET}/${LAYER_PREFIX}/${z}.zip" --region "$REGION"
  ok "${LAYER_PREFIX}/${z}.zip"
done

step "Verify"
aws s3 ls "s3://${DATA_BUCKET}/${LAYER_PREFIX}/" --region "$REGION" --human-readable

echo ""
ok "Phase 5 bootstrap complete."
echo "  Data bucket  : s3://${DATA_BUCKET}/"
echo "  TF state key : s3://${DATA_BUCKET}/tfstate/infra-terraform.tfstate (created on first apply)"
echo "  Layer zips   : s3://${DATA_BUCKET}/${LAYER_PREFIX}/"
echo ""
echo "Next: Phase 6 — GitHub workflow with UIF_ECHO_DEV_INFRA=plan"
