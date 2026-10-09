#!/usr/bin/env bash
# Shared helpers for uif-echo-*-deploy.sh scripts.
# Source this file; do not execute directly.
set -euo pipefail

: "${AWS_REGION:=eu-west-1}"
: "${TF_DIR:=infrastructure/terraform}"

# This file lives at stacks/echo/infrastructure/deploy/
STACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$STACK_ROOT"

BOLD='\033[1m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}▶ $*${RESET}"; }
ok()    { echo -e "${GREEN}✓ $*${RESET}"; }
warn()  { echo -e "${YELLOW}! $*${RESET}"; }
die()   { echo -e "${RED}✗ $*${RESET}" >&2; exit 1; }

# The five environments, and they are five rather than three because a uif
# stack coexists with the legacy stack it replaces: dev beside dev-uif, uat
# beside uat-uif. Each has its own tfvars and its own backend key, so no two
# can affect each other.
#
# Kept as an explicit list rather than a `*-uif` pattern. A typo like
# `uay-uif` has to be rejected here, not carried into `envs/uay-uif.tfvars`
# where Terraform reports a missing file and says nothing about the env name.
# The Makefile's ENVS list is the same set for the same reason; the two must
# be changed together.
uif_echo_validate_env() {
  local env="${1:-}"
  [[ "$env" =~ ^(dev|dev-uif|uat|uat-uif|prod)$ ]] \
    || die "ENV must be dev, dev-uif, uat, uat-uif, or prod. Got: ${env:-<empty>}"
}

uif_echo_preflight_aws() {
  for cmd in aws terraform; do
    command -v "$cmd" &>/dev/null || die "'$cmd' not found in PATH"
  done
  aws sts get-caller-identity --region "$AWS_REGION" --output text --query 'Arn' \
    | xargs -I{} echo "AWS identity: {}" \
    || die "AWS credentials not active."
}

# CI runners are ephemeral — null_resource may skip re-download while .cache/jars
# is empty, breaking aws_s3_object.gcp_glue_jars on apply.
uif_echo_prefetch_gcp_glue_jars() {
  local cache_dir="$REPO_ROOT/$TF_DIR/.cache/jars"
  mkdir -p "$cache_dir"
  local spec=(
    "gcs-connector-hadoop3-2.2.22-shaded.jar|https://repo1.maven.org/maven2/com/google/cloud/bigdataoss/gcs-connector/hadoop3-2.2.22/gcs-connector-hadoop3-2.2.22-shaded.jar"
    "iceberg-gcp-bundle-1.6.1.jar|https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-gcp-bundle/1.6.1/iceberg-gcp-bundle-1.6.1.jar"
  )
  local entry name url dest
  for entry in "${spec[@]}"; do
    name="${entry%%|*}"
    url="${entry#*|}"
    dest="$cache_dir/$name"
    if [[ -f "$dest" ]]; then
      ok "GCP Glue jar cached: $name"
    else
      step "Download GCP Glue jar: $name"
      curl -fsSL "$url" -o "$dest"
      ok "Downloaded $name"
    fi
  done
}

uif_echo_preflight_ui() {
  for cmd in node npm; do
    command -v "$cmd" &>/dev/null || die "'$cmd' not found in PATH"
  done
}

uif_echo_tf_out() {
  local key="$1" fallback="${2:-}" val
  val=$(terraform -chdir="$REPO_ROOT/$TF_DIR" output -raw "$key" 2>/dev/null | head -1 || true)
  if [[ -z "$val" ]]; then
    [[ -n "$fallback" ]] && { echo "$fallback"; return; }
    die "Terraform output '$key' is empty — run uif-echo-infra-deploy.sh first."
  fi
  echo "$val"
}

uif_echo_glue_scripts_prefix() {
  local env="${1:-dev}"
  [[ "$env" == *-uif ]] && echo "code" || echo "jobs"
}

uif_echo_sync_glue_scripts() {
  local bucket prefix

  # A uif stack's scripts are staged by uif_echo_stage_release_artifacts at a
  # content-addressed key BEFORE the plan, and its Glue jobs read them from the
  # release bucket. Syncing them to the old shared prefix as well would upload
  # bytes nothing reads (UIFPE-515).
  #
  # EVERY uif stack (UIFPE-555). Gated on dev-uif, uat-uif fell through to the
  # sync below -- and that sync resolves its bucket from the artifacts_bucket
  # output, which for uat-uif is uif-echo-uat-data. So it would not merely
  # upload bytes nothing reads, it would write them into the state bucket of a
  # stack whose Glue jobs read from the release bucket instead.
  if [[ "${ENV:-}" == *-uif ]]; then
    ok "ENV=${ENV} — scripts are staged as release artifacts, not synced."
    return 0
  fi

  bucket=$(uif_echo_tf_out artifacts_bucket)
  prefix=$(uif_echo_glue_scripts_prefix "${ENV:-dev}")
  # --delete removed 2026-09-16: this prefix is shared by every engineer
  # deploying Echo. --delete makes whichever checkout runs last win completely
  # — it deletes scripts a colleague added that aren't in the local working
  # tree, not just stale ones. The Gold team already hit this exact class of
  # risk and worked around it by moving to a separate `gold/` prefix
  # (locals.tf: "would purge anything of ours under glue_jobs/"). Dropping
  # --delete here removes the deletion hazard for everyone else. It does not
  # fully solve staleness — an old checkout can still overwrite a file that
  # was updated since — the complete fix is a versioned prefix per artifact
  # (tracked in ADR-0019, not yet built). Orphaned old scripts left behind by
  # dropping --delete are a cleanup cost, not a data-loss one.
  aws s3 sync "$STACK_ROOT/src/apps/pipeline/" "s3://${bucket}/${prefix}/" \
    --exclude "*.pyc" \
    --exclude "__pycache__/*" \
    --region "$AWS_REGION"
  ok "Synced Glue scripts to s3://${bucket}/${prefix}/"
}

uif_echo_build_layers() {
  step "Build Lambda layers (BUILD_LAYERS=1)"
  make -C "$REPO_ROOT/$TF_DIR" build-gcs-layer
  make -C "$REPO_ROOT/$TF_DIR" build-silver-bq-layer
  make -C "$REPO_ROOT/$TF_DIR" build-controller-bq-layer
  ok "Lambda layer zips rebuilt under infrastructure/lambda/"
}

# Upload layer zips under code/lambda-layers/ (UIF) or lambda-layers/ (legacy).
# by key only; content updates require upload then -replace on layer versions.
uif_echo_upload_layers() {
  local bucket src_dir="${1:-}" z
  bucket=$(uif_echo_tf_out artifacts_bucket 2>/dev/null || true)
  if [[ -z "$bucket" && -n "${UIF_ECHO_ARTIFACTS_BUCKET:-}" ]]; then
    bucket="$UIF_ECHO_ARTIFACTS_BUCKET"
  fi
  [[ -n "$bucket" ]] || die "artifacts_bucket unknown — run infra deploy first or set UIF_ECHO_ARTIFACTS_BUCKET"

  if [[ -z "$src_dir" ]]; then
    if [[ -f "$SCRIPT_DIR/layers/gcs_layer.zip" ]]; then
      src_dir="$SCRIPT_DIR/layers"
    else
      src_dir="$REPO_ROOT/$TF_DIR/../lambda"
    fi
  fi

  # Every uif stack uses the code/ prefix (UIFPE-555); only the legacy stacks
  # keep the bare one. Gated on dev-uif, uat-uif would have written to the
  # legacy prefix in a bucket that has no legacy layout.
  local layer_prefix="lambda-layers"
  [[ "${ENV:-}" == *-uif ]] && layer_prefix="code/lambda-layers"

  for z in oracle_instant_client gcs_layer silver_bq_layer controller_bq_layer; do
    if [[ -f "$src_dir/$z.zip" ]]; then
      aws s3 cp "$src_dir/$z.zip" "s3://${bucket}/${layer_prefix}/$z.zip" --region "$AWS_REGION"
      ok "Uploaded ${layer_prefix}/$z.zip"
    else
      warn "Skip missing $src_dir/$z.zip"
    fi
  done
}

# ── Release artifacts for a uif stack (UIFPE-515) ────────────────────────────
#
# Hash every artifact, upload it at its content-addressed key, and write the
# tfvars the apply consumes. Delegates to scripts/stage-artifacts.sh, which
# release-echo.yml also calls -- the artifact set is defined once, because two
# copies drift and the failure is invisible: a function whose source list is
# stale ships without a module it imports, which fails at RUNTIME, not at apply.
#
# ORDER MATTERS, AND IT CHANGED. uif_echo_sync_glue_scripts ran AFTER the plan.
# These hashes are plan INPUTS -- they decide every script_location and every
# Lambda s3_key -- so staging has to happen BEFORE the plan, not between plan
# and apply. A plan produced without them shows every job pointing at
# ARTIFACT-SHA-NOT-SUPPLIED.
#
# The file is named *.auto.tfvars.json so Terraform loads it for plan and apply
# alike, without a Makefile change. It is regenerated on every run and is
# gitignored: it describes one build, and a stale copy would silently pin an
# apply to artifacts from an earlier one.
uif_echo_stage_release_artifacts() {
  local release_bucket layer_bucket tfvars

  # THREE cases, not two, and the middle one used to be mislabelled as the
  # first (UIFPE-555). `!= dev-uif` sent uat-uif down the legacy branch, which
  # printed "ENV=uat-uif is a legacy stack" -- a uif stack told it was legacy,
  # then returned 0 as though there were nothing to do. The apply that followed
  # would have had no artifact_shas at all.
  #
  # 1. The legacy echo-* stacks keep their plan-time zips and their s3-sync'd
  #    script prefix until they retire; there is genuinely nothing to stage.
  case "${ENV:-}" in
    dev|uat|prod)
      ok "ENV=${ENV} is a legacy stack — no release artifacts to stage."
      return 0
      ;;
  esac

  # 2. Every uif stack EXCEPT dev-uif takes its artifacts by PROMOTION, and
  #    refusing here is the promotion model's central invariant rather than a
  #    missing feature. Build identity is established once, in dev, and an
  #    environment downstream of it runs the same bytes -- proven by an
  #    unchanged digest. Staging into uat would rebuild from whatever source
  #    tree happens to be checked out, produce different hashes, and leave the
  #    release tag attesting to bytes nothing is running.
  #
  #    Loud, not silent. Returning 0 here leaves artifacts.auto.tfvars.json
  #    unwritten, so artifact_shas defaults to {} and every key becomes
  #    ARTIFACT-SHA-NOT-SUPPLIED. Glue would accept that string and fail at run
  #    time; Lambda validates the object at CreateFunction and fails the apply
  #    with NoSuchKey, naming a key and nothing explaining why it is a sentinel.
  if [[ "${ENV:-}" != "dev-uif" ]]; then
    die "$(printf '%s\n' \
      "ENV=${ENV:-} takes its artifacts by promotion, not by staging." \
      "  Build identity is established in dev and promoted unchanged; building" \
      "  here would produce different hashes for the same release tag." \
      "  Promote into s3://uif-echo-${ENV%-uif}-release-artifacts instead, then" \
      "  regenerate artifacts.auto.tfvars.json from the PROMOTED digests." \
      "  Only dev-uif stages from source.")"
  fi

  # 3. dev-uif: stage from source. This is where build identity is created.
  release_bucket="uif-echo-dev-release-artifacts"
  layer_bucket="uif-echo-dev-data"
  tfvars="$REPO_ROOT/$TF_DIR/artifacts.auto.tfvars.json"

  # The bucket has to exist before anything can be uploaded into it, and
  # Terraform creates it -- which is circular on a greenfield environment, the
  # same chicken-and-egg apicurio solves with a targeted ECR bootstrap. Fail
  # with the command rather than guessing: creating it here would leave
  # Terraform to fail on the next apply with BucketAlreadyOwnedByYou, and the
  # import dance that follows is exactly what uif_echo_import_phase5_artifacts
  # already exists to clean up for the data bucket.
  if ! aws s3api head-bucket --bucket "$release_bucket" --region "$AWS_REGION" 2>/dev/null; then
    die "$(printf '%s
'       "s3://${release_bucket} does not exist, so artifacts cannot be staged."       "  It is a one-time bootstrap, and Terraform owns it:"       "    make -C ${TF_DIR} init ENV=${ENV}"       "    terraform -chdir=${TF_DIR} apply -var-file=envs/${ENV}.tfvars \\"       "      -target=aws_s3_bucket.release_artifacts \\"       "      -target=aws_s3_bucket_versioning.release_artifacts"       "  Then re-run this script. A targeted apply is an escape hatch, not a"       "  pattern: that bucket has no dependencies and nothing else is"       "  half-created by it.")"
  fi

  # compile_preview's artifact is a BUILT TREE (handler + pip-installed
  # sqlglot), and stage-artifacts.sh refuses when the tree is missing.
  # release-echo.yml builds it before staging; this path did not, so every dev
  # deploy failed at this step from UIFPE-576 until UIFPE-672. build-preview
  # wipes and rebuilds the tree, so it always matches the checked-out source.
  # qe/pipeline/test_stage_artifacts_callers.py fails if a caller drops it.
  step "Build compile_preview tree (make build-preview)"
  make -C "$STACK_ROOT/infrastructure/terraform-gold" build-preview

  step "Stage release artifacts (content-addressed)"
  bash "$STACK_ROOT/scripts/stage-artifacts.sh"     --bucket "$release_bucket"     --layer-source-bucket "$layer_bucket"     --tfvars "$tfvars"
  ok "Artifacts staged; hashes in $(basename "$tfvars")"
}

# The data bucket is pre-created before the first apply -- it holds the
# Terraform state, so `terraform init` needs it to exist before Terraform can
# evaluate the resource that declares it. Import so the apply does not fail
# with BucketAlreadyOwnedByYou.
#
# PARAMETERISED FOR ANY uif STACK (UIFPE-555). This was hardcoded to dev-uif
# and to uif-echo-dev-data, so for uat-uif it returned 0 immediately and did
# NOTHING -- the first uat apply would have failed on a bucket that
# bootstrap-environment.sh had just created, with an error naming the bucket
# and nothing naming the missing import. The bucket name now comes from the
# environment's own tfvars via stack_naming, which is the same single authority
# required-actions.py reads; deriving it here a second way is how the two
# would disagree.
uif_echo_import_phase5_artifacts() {
  local env="${1:-}"
  # Every uif stack, not just dev. The legacy echo-* envs keep their state in
  # the shared pearson-ingestion-framework-tfstate-* bucket, which no Terraform
  # in this repo declares, so they have no circularity and nothing to import.
  [[ "$env" == *-uif ]] || return 0

  # Read from the tfvars with sed, not through stack_naming. The library is the
  # single authority for names it DERIVES, and artifacts_bucket is not derived
  # -- it is a literal in that same file, which is the file stack_naming itself
  # parses. Going through Python would add an interpreter dependency to a deploy
  # script that has none, and `python3` does not exist on every machine that
  # runs these: the bootstrap had to grow a resolver for exactly that.
  local tfvars_path="$REPO_ROOT/$TF_DIR/envs/${env}.tfvars"
  local bucket
  bucket="$(sed -nE 's/^[[:space:]]*artifacts_bucket[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
    "$tfvars_path" 2>/dev/null | head -1)"
  if [[ -z "$bucket" ]]; then
    warn "no artifacts_bucket in envs/${env}.tfvars; skipping the state-bucket import"
    warn "The first apply will fail with BucketAlreadyOwnedByYou if the bucket exists."
    return 0
  fi

  aws s3api head-bucket --bucket "$bucket" --region "$AWS_REGION" 2>/dev/null \
    || return 0

  local tfvars="envs/${env}.tfvars"
  local tf=(terraform -chdir="$REPO_ROOT/$TF_DIR")

  if ! "${tf[@]}" state show aws_s3_bucket.artifacts &>/dev/null; then
    step "Import Phase 5 pre-created artifacts bucket"
    "${tf[@]}" import -var-file="$tfvars" aws_s3_bucket.artifacts "$bucket"
    ok "Imported aws_s3_bucket.artifacts"
  fi

  if ! "${tf[@]}" state show aws_s3_bucket_versioning.artifacts &>/dev/null; then
    if "${tf[@]}" import -var-file="$tfvars" aws_s3_bucket_versioning.artifacts "$bucket" 2>/dev/null; then
      ok "Imported aws_s3_bucket_versioning.artifacts"
    else
      warn "Could not import aws_s3_bucket_versioning.artifacts (may be created on apply)"
    fi
  fi
}

# Partial applies can create Glue catalog DBs before state records them.
#
# EVERY uif STACK, not just dev-uif (UIFPE-555). The legacy echo-* stacks are
# excluded because their Glue databases predate Terraform and are not in this
# root's state by design. But the drift this repairs is caused by a PARTIAL
# APPLY, and a partial apply is likeliest on a stack's first convergence --
# which for uat-uif has not happened yet. Gating on dev-uif meant the one
# environment most likely to need this was the one that skipped it.
uif_echo_import_phase6_drift() {
  local env="${1:-}"
  [[ "$env" == *-uif ]] || return 0

  local tfvars="envs/${env}.tfvars"
  local tf=(terraform -chdir="$REPO_ROOT/$TF_DIR")
  local account_id
  account_id=$(aws sts get-caller-identity --query Account --output text)

  uif_echo_try_import_glue_db() {
    local addr="$1" db_name="$2"
    if "${tf[@]}" state show "$addr" &>/dev/null; then
      return 0
    fi
    if aws glue get-database --name "$db_name" --region "$AWS_REGION" &>/dev/null; then
      step "Import drifted Glue database $db_name"
      if "${tf[@]}" import -var-file="$tfvars" "$addr" "${account_id}:${db_name}"; then
        ok "Imported $addr"
      else
        warn "Could not import $addr"
      fi
    fi
  }

  uif_echo_lf_grant_deploy_role_on_db() {
    local db_name="$1"
    aws glue get-database --name "$db_name" --region "$AWS_REGION" &>/dev/null || return 0
    aws lakeformation grant-permissions \
      --region "$AWS_REGION" \
      --principal "DataLakePrincipalIdentifier=arn:aws:iam::${account_id}:role/echo-dev-github-actions" \
      --permissions ALTER DROP DESCRIBE \
      --resource "{\"Database\":{\"Name\":\"${db_name}\"}}" \
      2>/dev/null || true
  }

  uif_echo_try_import_glue_db 'aws_glue_catalog_database.silver_tep[0]' "uif_echo_dev_silver_tep"
  uif_echo_try_import_glue_db 'aws_glue_catalog_database.silver_mdm[0]' "uif_echo_dev_silver_mdm"

  for db in uif_echo_dev_silver_tep uif_echo_dev_silver_mdm; do
    uif_echo_lf_grant_deploy_role_on_db "$db"
  done
}


# plan | apply — default apply for local shells; GitHub Actions sets plan.
: "${UIF_ECHO_TF_MODE:=apply}"
uif_echo_validate_tf_mode() {
  case "$UIF_ECHO_TF_MODE" in
    plan|apply) ;;
    *) die "UIF_ECHO_TF_MODE must be plan or apply. Got: $UIF_ECHO_TF_MODE" ;;
  esac
}
