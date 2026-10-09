#!/usr/bin/env bash
# Publish or update a schema in the Pearson Schema Registry sandbox.
#
# Auto-detects new artifact vs new version by probing the BFF. When updating,
# runs the compatibility check first and picks bump=minor or bump=major
# from the recommendation (override with --bump).
#
# Usage:
#   publish.sh \
#     --schema FILE \
#     --service-urn URN \
#     --artifact-id ID \
#     [--group default] \
#     [--classification public|internal|confidential|restricted] \
#     [--pii FIELD]... \
#     [--lifecycle active|deprecated] \
#     [--bump minor|major] \
#     [--type-override JSON|AVRO|...] \
#     [--api-base URL] \
#     [--check-only] \
#     [--force-new] \
#     [-q|--quiet]
#
# Requires: curl, jq.

set -euo pipefail

# No default here on purpose (see below) — an empty/unset PUBLISH_API_BASE
# used to silently fall back to a hardcoded sandbox execute-api URL, which
# meant a misconfigured environment would quietly publish schemas against
# the wrong registry instead of failing. --api-base can still supply it
# after arg parsing, so the emptiness check happens after the while loop.
API_BASE="${PUBLISH_API_BASE:-}"
SCHEMA=""
SERVICE_URN=""
ARTIFACT_ID=""
GROUP_ID="default"
CLASSIFICATION=""
LIFECYCLE="active"
BUMP=""
TYPE_OVERRIDE=""
CHECK_ONLY=0
FORCE_NEW=0
QUIET=0
PII_FIELDS=()

die() { echo "ERROR: $*" >&2; exit 1; }
log() { [[ $QUIET -eq 1 ]] || echo "$*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --schema)         SCHEMA="$2"; shift 2 ;;
    --service-urn)    SERVICE_URN="$2"; shift 2 ;;
    --artifact-id)    ARTIFACT_ID="$2"; shift 2 ;;
    --group)          GROUP_ID="$2"; shift 2 ;;
    --classification) CLASSIFICATION="$2"; shift 2 ;;
    --pii)            PII_FIELDS+=("$2"); shift 2 ;;
    --lifecycle)      LIFECYCLE="$2"; shift 2 ;;
    --bump)           BUMP="$2"; shift 2 ;;
    --type-override)  TYPE_OVERRIDE="$2"; shift 2 ;;
    --api-base)       API_BASE="$2"; shift 2 ;;
    --check-only)     CHECK_ONLY=1; shift ;;
    --force-new)      FORCE_NEW=1; shift ;;
    -q|--quiet)       QUIET=1; shift ;;
    -h|--help)        sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                die "Unknown flag: $1" ;;
  esac
done

[[ -n "$API_BASE" ]]      || die "PUBLISH_API_BASE is not set and --api-base was not passed — there is no default; export PUBLISH_API_BASE (e.g. the repo variable vars.PUBLISH_API_BASE in CI) or pass --api-base URL"
[[ -n "$SCHEMA" ]]         || die "--schema is required"
[[ -f "$SCHEMA" ]]         || die "Schema file not found: $SCHEMA"
[[ -n "$ARTIFACT_ID" ]]    || die "--artifact-id is required"
[[ -n "$SERVICE_URN" ]]    || die "--service-urn is required (try: GET $API_BASE/api/v1/services)"

if [[ -z "$CLASSIFICATION" ]]; then
  # Fall back to the schema's own metadata block if it carries one.
  CLASSIFICATION=$(jq -r '.metadata.dataClassification // .info["x-pearson-metadata"].dataClassification // empty' "$SCHEMA" 2>/dev/null || true)
  [[ -n "$CLASSIFICATION" ]] || die "--classification is required (no metadata.dataClassification in schema body)"
fi

# Probe to decide endpoint. Only a clean 404 means "not found, register as
# new" — any other status (5xx, other 4xx) or a network failure (curl exit
# non-zero, code "000") is an unexpected condition and must not be silently
# treated as "not found", or we'd end up double-registering an artifact that
# actually exists behind a flaky/erroring probe.
ARTIFACT_EXISTS=0
if [[ $FORCE_NEW -eq 0 ]]; then
  PROBE_CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
    "$API_BASE/api/v1/schemas/$GROUP_ID/$ARTIFACT_ID" || echo "000")
  case "$PROBE_CODE" in
    200)
      ARTIFACT_EXISTS=1
      log "→ artifact exists ($PROBE_CODE) — will publish a new version"
      ;;
    404)
      log "→ artifact not found (404) — will register a new artifact"
      ;;
    *)
      die "Existence probe failed unexpectedly for $GROUP_ID/$ARTIFACT_ID (HTTP $PROBE_CODE) — not proceeding; this is not a 'not found' response, check network/registry health"
      ;;
  esac
fi

# Build the pii_fields JSON array.
if [[ ${#PII_FIELDS[@]} -eq 0 ]]; then
  PII_JSON='[]'
else
  PII_JSON=$(printf '%s\n' "${PII_FIELDS[@]}" | jq -R . | jq -s .)
fi

build_body() {
  local include_artifact_id="$1"  # "yes" or "no"
  local include_bump="$2"         # "yes" or "no"
  jq -n \
    --rawfile content "$SCHEMA" \
    --arg service_urn "$SERVICE_URN" \
    --arg group_id    "$GROUP_ID" \
    --arg artifact_id "$ARTIFACT_ID" \
    --arg classification "$CLASSIFICATION" \
    --arg lifecycle   "$LIFECYCLE" \
    --arg bump        "${BUMP:-minor}" \
    --arg type_override "$TYPE_OVERRIDE" \
    --argjson pii "$PII_JSON" \
    --arg inc_aid     "$include_artifact_id" \
    --arg inc_bump    "$include_bump" \
    '
    {
      service_urn: $service_urn,
      group_id: $group_id,
      content: $content,
      data_classification: $classification,
      pii_fields: $pii,
      lifecycle: $lifecycle,
    }
    + (if $inc_aid == "yes" then { artifact_id: $artifact_id } else {} end)
    + (if $inc_bump == "yes" then { bump: $bump } else {} end)
    + (if $type_override != "" then { type_override: $type_override } else {} end)
    '
}

post_json() {
  local url="$1"
  local body="$2"
  local resp_body status

  resp_body=$(mktemp)
  status=$(curl -sS -o "$resp_body" -w "%{http_code}" \
    -X POST "$url" \
    -H 'Content-Type: application/json' \
    --data "$body")

  cat "$resp_body" | jq . 2>/dev/null || cat "$resp_body"
  echo
  rm -f "$resp_body"

  if [[ "$status" -ge 400 ]]; then
    echo "✗ HTTP $status" >&2
    return 1
  fi
  log "✓ HTTP $status"
}

# --- Compat check path -------------------------------------------------------
if [[ $ARTIFACT_EXISTS -eq 1 || $CHECK_ONLY -eq 1 ]]; then
  log "→ compatibility check"
  CHECK_BODY=$(jq -n --rawfile content "$SCHEMA" --arg t "$TYPE_OVERRIDE" \
    'if $t == "" then {content: $content} else {content: $content, type_override: $t} end')
  CHECK_RESP=$(mktemp)
  CHECK_CODE=$(curl -sS -o "$CHECK_RESP" -w "%{http_code}" \
    -X POST "$API_BASE/api/v1/publish/$GROUP_ID/$ARTIFACT_ID/compatibility" \
    -H 'Content-Type: application/json' \
    --data "$CHECK_BODY")
  cat "$CHECK_RESP" | jq .
  REC=$(jq -r '.recommendation // empty' "$CHECK_RESP" 2>/dev/null || true)
  rm -f "$CHECK_RESP"
  [[ "$CHECK_CODE" -lt 400 ]] || die "Compatibility check failed (HTTP $CHECK_CODE)"

  if [[ $CHECK_ONLY -eq 1 ]]; then
    log "→ --check-only set; exiting"
    exit 0
  fi

  if [[ -z "$BUMP" ]]; then
    BUMP="${REC:-minor}"
    log "→ bump auto-selected from recommendation: $BUMP"
  fi
fi

# --- Publish -----------------------------------------------------------------
if [[ $ARTIFACT_EXISTS -eq 1 ]]; then
  log "→ POST /api/v1/publish/$GROUP_ID/$ARTIFACT_ID (bump=$BUMP)"
  BODY=$(build_body no yes)
  post_json "$API_BASE/api/v1/publish/$GROUP_ID/$ARTIFACT_ID" "$BODY"
else
  log "→ POST /api/v1/publish (new artifact)"
  BODY=$(build_body yes no)
  post_json "$API_BASE/api/v1/publish" "$BODY"
fi
