#!/usr/bin/env bash
# Build and deploy the Echo control-panel UI (data-sync module and siblings).
#
# Usage: ./uif-echo-ui-deploy.sh [ENV] [API_URL_OVERRIDE]
#   ENV defaults to "dev". Use "dev-uif" or "uat-uif" for the uif-echo-*-*
#   stacks.
#   API_URL_OVERRIDE optional — defaults to Terraform api_endpoint output.
#
# The UI bucket may also host sibling apps under m/* (beacon, transform).
# This script preserves those keys while pruning stale Echo root assets.
#
# Optional environment variables:
#   UIF_ECHO_UI_BUNDLE=<dir>      Publish this restored bundle instead of
#                                 building. Used by promote-echo.yml; a
#                                 promote must never rebuild.
#
# Credentials must already be active.
set -euo pipefail

ENV="${1:-dev}"
API_URL_OVERRIDE="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_uif-echo-deploy-common.sh
source "$SCRIPT_DIR/_uif-echo-deploy-common.sh"

step "Preflight — UI deploy (ENV=$ENV)"
uif_echo_validate_env "$ENV"
uif_echo_preflight_aws
uif_echo_preflight_ui

make -C "$REPO_ROOT/$TF_DIR" init ENV="$ENV" >/dev/null

step "Build UI"
API_URL="${API_URL_OVERRIDE:-$(uif_echo_tf_out api_endpoint)}"

# NO VITE_API_URL AND NO VITE_ECHO_ENV (UIFPE-515). The origin used to be baked
# into the bundle at build time, which made the bundle environment-specific: a
# dev build carried dev's API URL, so it could not be promoted, and its content
# hash differed per environment -- which breaks the digest equality the
# promotion model rests on. It now arrives at runtime in config.json, written
# below, so ONE bundle serves every environment.
cd "$REPO_ROOT/ui"

# PROMOTE PATH: publish a bundle that was built elsewhere, verified, and
# restored. A promote must NOT rebuild. `npm run build` is not byte-stable
# across runners, so a rebuild produces a bundle whose hash no release tag
# names -- and the digest equality is the whole evidence the promotion model
# offers. restore-artifacts.sh has already extracted and re-hashed this
# directory with config.json excluded, so there is nothing left to verify here.
if [ -n "${UIF_ECHO_UI_BUNDLE:-}" ]; then
  [ -d "$UIF_ECHO_UI_BUNDLE" ] \
    || die "UIF_ECHO_UI_BUNDLE='${UIF_ECHO_UI_BUNDLE}' is not a directory. Pass the directory restore-artifacts.sh --dest populated, not the tarball."
  rm -rf dist
  mkdir -p dist
  cp -R "${UIF_ECHO_UI_BUNDLE}/." dist/
  ok "UI taken from the restored bundle at ${UIF_ECHO_UI_BUNDLE} (not rebuilt)"
else
  npm ci
  npm run build
  ok "UI built → ui/dist/ (environment-independent)"
fi

# The app env is one of dev|uat|prod -- what the UI labels itself and what
# gates the non-production badge. It is NOT the deploy ENV: `dev-uif` is a
# Terraform environment name, and the uif-echo-dev-* stack is still a dev
# stack. envConfig.ts refuses an unknown value rather than defaulting to dev,
# because defaulting is how a prod deployment ends up quietly labelled DEV.
case "$ENV" in
  dev|dev-uif) APP_ENV=dev ;;
  uat|uat-uif) APP_ENV=uat ;;
  prod)        APP_ENV=prod ;;
  *)           die "cannot map ENV '$ENV' to an app env (dev|uat|prod)" ;;
esac

step "Write this environment's config.json"
# Written AFTER the build and never committed: it is environment state, and it
# is excluded from the artifact hash (scripts/artifact-hash.sh) so the bundle
# stays promotable. Presence is not shape -- an empty apiUrl would sync a UI
# that calls its own origin for the API and fails in the browser, not here.
[[ -n "$API_URL" ]] || die "api_endpoint is empty; has this environment been applied? A UI published without an apiUrl cannot reach its API."
printf '{\n  "apiUrl": "%s",\n  "env": "%s"\n}\n' "$API_URL" "$APP_ENV" > "$REPO_ROOT/ui/dist/config.json"
ok "config.json → apiUrl=$API_URL env=$APP_ENV"

step "Deploy UI to S3"
cd "$REPO_ROOT"
UI_BUCKET=$(uif_echo_tf_out ui_bucket_name)
aws s3 sync ui/dist/ "s3://${UI_BUCKET}/" \
  --delete \
  --exclude "m/*" \
  --region "$AWS_REGION"
ok "Synced to s3://${UI_BUCKET}/ (m/* preserved)"

step "Invalidate CloudFront cache"
CF_ID=$(uif_echo_tf_out ui_cloudfront_id)
aws cloudfront create-invalidation \
  --distribution-id "$CF_ID" \
  --paths "/*" \
  --output text --query 'Invalidation.Id' \
  | xargs -I{} echo "Invalidation ID: {}"

step "UI deploy complete"
echo -e "  API : ${BOLD}${API_URL}${RESET}"
echo -e "  UI  : ${BOLD}$(uif_echo_tf_out ui_cloudfront_url "—")${RESET}"
