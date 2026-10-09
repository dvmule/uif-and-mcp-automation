#!/usr/bin/env bash
# Prove a promoted uat stack is reachable, and that THIS promotion landed.
#
# Three assertions, all unauthenticated, so this needs no credential in the
# shared uat environment. Echo has no integration suite and no health route
# (see qe/ci.sh:38), so this is the minimal set that separates "the stack
# exists" from "the promotion worked":
#
#   1. CloudFront serves the UI with a non-empty body.
#   2. API Gateway answers 200, 401 or 403 -- all three are a PASS. The
#      assertion's purpose is REACHABILITY, not enforcement: any of the three
#      proves the route, the integration and the Lambda all exist, because a
#      request had to pass through all of them to get a response at all. 404
#      means the route is missing; 5xx means the Lambda is broken; an
#      unreachable host is 000. All three fail.
#
#      Whether the authorizer enforces is a SEPARATE concern that does not
#      belong in a smoke test. `enable_jwt_authorizer` is `false` in every
#      environment's tfvars today (see infrastructure/terraform/envs/*.tfvars),
#      so apigateway.tf's `authorization_type` resolves to "NONE" and an
#      unauthenticated 200 is the expected, correct answer -- not a finding.
#      Failing on 200 here would make this smoke unpassable on every real
#      environment that exists. The 401/403 arm is kept only so this still
#      passes unchanged if the authorizer is ever switched on.
#   3. A Glue job's script_location contains the promoted sha.
#
# Assertion 3 is the only one that proves anything about THIS promotion. 1 and
# 2 pass against any converged stack, so without 3 a promote that copied
# nothing would still go green.
#
# WHAT THIS DOES NOT COVER: the Secrets Manager VPC endpoint security group
# (Phase 4.9). A missing ingress rule leaves every assertion here passing and
# fails GetSecretValue at run time. Do not read a green smoke as covering it.
#
# Usage:
#   smoke-uat.sh --api-url <url> --ui-url <url> \
#                --glue-job <name> --expect-sha <64-hex>
set -euo pipefail

die()  { printf 'smoke-uat.sh: %s\n' "$1" >&2; exit 1; }
pass() { printf '  PASS  %s\n' "$1"; }

API_URL="" UI_URL="" GLUE_JOB="" EXPECT_SHA=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --api-url)    [ "$#" -ge 2 ] || die "--api-url needs a value";    API_URL="$2";    shift 2 ;;
    --ui-url)     [ "$#" -ge 2 ] || die "--ui-url needs a value";     UI_URL="$2";     shift 2 ;;
    --glue-job)   [ "$#" -ge 2 ] || die "--glue-job needs a value";   GLUE_JOB="$2";   shift 2 ;;
    --expect-sha) [ "$#" -ge 2 ] || die "--expect-sha needs a value"; EXPECT_SHA="$2"; shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$API_URL" ]    || die "--api-url is required"
[ -n "$UI_URL" ]     || die "--ui-url is required"
[ -n "$GLUE_JOB" ]   || die "--glue-job is required"
[ -n "$EXPECT_SHA" ] || die "--expect-sha is required"

# Checked, because an empty or truncated value turns assertion 3 into a
# substring match that everything satisfies.
printf '%s' "$EXPECT_SHA" | grep -Eq '^[0-9a-f]{64}$' \
  || die "--expect-sha must be 64 lowercase hex characters; got '${EXPECT_SHA}'"

# -- 1. The UI is served -------------------------------------------------------
ui_body="$(mktemp)"
trap 'rm -f "$ui_body"' EXIT
if ! ui_code="$(curl -sS -o "$ui_body" -w '%{http_code}' --max-time 30 "$UI_URL/")"; then
  # curl already writes its own %{http_code} (000 on a connect/timeout
  # failure) via -w, and still exits non-zero. `|| echo 000` on the same
  # line would run in ADDITION to that, appending a second "000" with no
  # separating newline and leaving ui_code as the garbled "000000". Checking
  # curl's own exit status here instead keeps ui_code exactly "000".
  ui_code="000"
fi
[ "$ui_code" = "200" ] \
  || die "the UI at ${UI_URL}/ returned ${ui_code}, not 200. Nothing is published, or CloudFront cannot reach the bucket."
ui_bytes="$(wc -c < "$ui_body" | tr -d ' ')"
[ "$ui_bytes" -gt 100 ] \
  || die "the UI returned 200 but only ${ui_bytes} bytes. A 200 with an empty body is CloudFront serving an error document."
pass "UI 200, ${ui_bytes} bytes"

# -- 2. The API answers --------------------------------------------------------
# GET /pipelines is a real route (apigateway.tf). 200, 401 and 403 all PASS --
# see the header comment for why enforcement is deliberately not asserted
# here.
if ! api_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${API_URL}/pipelines")"; then
  # Same fix as the UI check above: check curl's own exit status rather
  # than `|| echo 000` on the same line, so a real unreachable host lands
  # on the "000" case arm below with a clean value instead of "000000".
  api_code="000"
fi
case "$api_code" in
  200|401|403) pass "API ${api_code} (route, integration and Lambda are reachable)" ;;
  404) die "the API returned 404 for GET /pipelines: the route does not exist, so the apply did not create it." ;;
  000) die "the API at ${API_URL} could not be reached at all." ;;
  5*)  die "the API returned ${api_code} for GET /pipelines: the Lambda integration is broken." ;;
  *)   die "the API returned ${api_code} for GET /pipelines; expected 200, 401 or 403." ;;
esac

# -- 3. This promotion landed --------------------------------------------------
loc="$(aws glue get-job --job-name "$GLUE_JOB" \
  --query 'Job.Command.ScriptLocation' --output text 2>/dev/null | tr -d '\r' || true)"
[ -n "$loc" ] && [ "$loc" != "None" ] \
  || die "could not read ScriptLocation for Glue job ${GLUE_JOB}."
case "$loc" in
  *"$EXPECT_SHA"*) pass "Glue ${GLUE_JOB} runs ${EXPECT_SHA:0:12}..." ;;
  *ARTIFACT-SHA-NOT-SUPPLIED*)
    die "Glue job ${GLUE_JOB} points at ARTIFACT-SHA-NOT-SUPPLIED. The apply ran without artifact_shas, so no artifact was promoted." ;;
  *) die "Glue job ${GLUE_JOB} runs ${loc}, which does not contain the promoted sha ${EXPECT_SHA}. The stack is converged on a different release." ;;
esac

printf 'smoke-uat.sh: all three assertions passed\n'
