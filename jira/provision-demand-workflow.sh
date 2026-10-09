#!/usr/bin/env bash
#
# provision-demand-workflow.sh — create the Jira configuration the Demand
# workflow needs, via the Jira Cloud REST API.
#
# Spec: jira/demand-workflow.md
#
# This writes PROJECT-LEVEL CONFIGURATION, not issues. Blast radius is much
# wider than creating a ticket: custom fields and statuses are global objects
# in a company-managed site. It is therefore DRY-RUN BY DEFAULT and needs an
# explicit --apply. Point it at a sandbox site before UIFPE.
#
# What it does (idempotent — skips anything that already exists by name):
#   1. the seven custom fields from demand-workflow.md §3
#   2. the option values for the three select/multi-select/checkbox fields
#   3. the "Demand" issue type
#   4. the eight statuses from demand-workflow.md §1
#   5. optionally places the fields on a screen tab (--screen-id)
#
# What it deliberately does NOT do:
#   - create the workflow, its transitions or its validators. The workflow
#     REST API exists, but its validator rule keys vary by instance and a
#     wrong key silently produces a workflow with no enforcement. Build the
#     workflow in the UI against §4a, where the validator picker shows you
#     what your instance actually supports.
#   - create Automation rules. There is no supported public API for them.
#     Build the four rules in §4c by hand.
#
# Usage:
#   export JIRA_BASE_URL="https://pearson.atlassian.net"
#   export JIRA_USER_EMAIL="you@pearson.com"
#   export JIRA_API_TOKEN="..."          # needs site-admin scope
#
#   ./jira/provision-demand-workflow.sh                    # dry run
#   ./jira/provision-demand-workflow.sh --apply            # write
#   ./jira/provision-demand-workflow.sh --apply --screen-id 10001
#
set -euo pipefail

APPLY=0
SCREEN_ID=""
TAB_ID=""

usage() {
  cat <<'USAGE'
provision-demand-workflow.sh [--apply] [--screen-id <id> [--tab-id <id>]]

  --apply            Actually write. Without it, prints the calls and exits.
  --screen-id <id>   Also place the created fields on this screen.
  --tab-id <id>      Tab within that screen. Defaults to the first tab.
  -h, --help         This message.

Requires JIRA_BASE_URL, JIRA_USER_EMAIL, JIRA_API_TOKEN in the environment,
plus curl and jq on PATH.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)     APPLY=1; shift ;;
    --screen-id) SCREEN_ID="${2:-}"; shift 2 ;;
    --tab-id)    TAB_ID="${2:-}"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for cmd in curl jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command not found: $cmd" >&2; exit 1; }
done

: "${JIRA_BASE_URL:?set JIRA_BASE_URL, e.g. https://pearson.atlassian.net}"
: "${JIRA_USER_EMAIL:?set JIRA_USER_EMAIL}"
: "${JIRA_API_TOKEN:?set JIRA_API_TOKEN}"

BASE="${JIRA_BASE_URL%/}"
AUTH="${JIRA_USER_EMAIL}:${JIRA_API_TOKEN}"

if [ "$APPLY" -eq 0 ]; then
  echo "=== DRY RUN — no writes. Re-run with --apply to execute. ==="
else
  echo "=== APPLY — writing configuration to ${BASE} ==="
fi
echo

# --- HTTP helpers -----------------------------------------------------------

# GET always runs, in both modes: reads are how idempotency is decided.
api_get() {
  curl -sS -u "$AUTH" -H "Accept: application/json" -X GET "${BASE}$1"
}

# Writes are suppressed in dry-run and echoed instead.
api_write() {
  method="$1"; path="$2"; body="$3"
  if [ "$APPLY" -eq 0 ]; then
    echo "    WOULD ${method} ${path}"
    echo "      ${body}"
    return 0
  fi
  curl -sS -u "$AUTH" \
    -H "Accept: application/json" -H "Content-Type: application/json" \
    -X "$method" "${BASE}${path}" --data "$body"
}

# --- 1. custom fields -------------------------------------------------------
# Columns: name <TAB> field type key <TAB> searcher key
# Type keys are the standard Jira Cloud customfieldtypes; see §3 for which
# gate each field makes checkable.

FIELDS_TSV=$(cat <<'TSV'
Business Outcome	textarea	textsearcher
Consuming System	textfield	textsearcher
Reuse Outcome	select	multiselectsearcher
Satisfying Contract	textfield	textsearcher
Capability Span	multiselect	multiselectsearcher
HLD Link	url	exacttextsearcher
Security Posture	textarea	textsearcher
Observability Posture	textarea	textsearcher
Invalid Reason	textarea	textsearcher
Principles Reviewed	multicheckboxes	multiselectsearcher
Review Outcome	select	multiselectsearcher
Rework Count	float	exactnumber
TSV
)

echo "--- 1/4 custom fields ---"

EXISTING_FIELDS=$(api_get "/rest/api/3/field")
CREATED_FIELD_IDS=""

while IFS=$'\t' read -r fname ftype fsearcher; do
  [ -n "$fname" ] || continue

  existing_id=$(printf '%s' "$EXISTING_FIELDS" \
    | jq -r --arg n "$fname" '[.[] | select(.name == $n and .custom == true)][0].id // empty')

  if [ -n "$existing_id" ]; then
    echo "  = ${fname} (exists: ${existing_id})"
    CREATED_FIELD_IDS="${CREATED_FIELD_IDS} ${existing_id}"
    continue
  fi

  echo "  + ${fname} (${ftype})"
  body=$(jq -n \
    --arg name "$fname" \
    --arg type "com.atlassian.jira.plugin.system.customfieldtypes:${ftype}" \
    --arg searcher "com.atlassian.jira.plugin.system.customfieldtypes:${fsearcher}" \
    --arg desc "Demand workflow gate field — see jira/demand-workflow.md §3" \
    '{name: $name, description: $desc, type: $type, searcherKey: $searcher}')

  result=$(api_write POST "/rest/api/3/field" "$body")
  if [ "$APPLY" -eq 1 ]; then
    new_id=$(printf '%s' "$result" | jq -r '.id // empty')
    if [ -z "$new_id" ]; then
      echo "    ! failed: ${result}" >&2
    else
      CREATED_FIELD_IDS="${CREATED_FIELD_IDS} ${new_id}"
    fi
  fi
done <<TSV_INPUT
${FIELDS_TSV}
TSV_INPUT

echo

# --- 2. option values -------------------------------------------------------
# Only meaningful once the parent field exists, so this is a no-op in dry-run
# for fields that do not exist yet. Capability Span's values are the thirteen
# capability names, exactly as in CLAUDE.md — do not introduce synonyms.

echo "--- 2/4 option values ---"

add_options() {
  field_name="$1"; shift

  field_id=$(printf '%s' "$EXISTING_FIELDS" \
    | jq -r --arg n "$field_name" '[.[] | select(.name == $n and .custom == true)][0].id // empty')

  if [ -z "$field_id" ]; then
    echo "  ~ ${field_name}: field not created yet; re-run after --apply to add options"
    return 0
  fi

  context_id=$(api_get "/rest/api/3/field/${field_id}/context" | jq -r '.values[0].id // empty')
  if [ -z "$context_id" ]; then
    echo "  ! ${field_name}: no context found" >&2
    return 0
  fi

  # Newline-delimited throughout: option values contain spaces, so space is
  # not usable as a separator, and grep -Fxq gives an exact whole-line match.
  existing_opts=$(api_get "/rest/api/3/field/${field_id}/context/${context_id}/option" \
    | jq -r '.values[].value')

  to_add=""
  for opt in "$@"; do
    if printf '%s\n' "$existing_opts" | grep -Fxq "$opt"; then
      continue
    fi
    to_add="${to_add}${opt}
"
  done

  if [ -z "$to_add" ]; then
    echo "  = ${field_name}: all options present"
    return 0
  fi

  echo "  + ${field_name}: $(printf '%s' "$to_add" | tr '\n' ' ')"
  body=$(printf '%s' "$to_add" | jq -R -s \
    'split("\n") | map(select(length > 0) | {value: ., disabled: false}) | {options: .}')
  api_write POST "/rest/api/3/field/${field_id}/context/${context_id}/option" "$body" >/dev/null
}

add_options "Reuse Outcome" "Reuse" "Partial" "None"
add_options "Review Outcome" "Approved" "Redesign"
add_options "Principles Reviewed" "Principles reviewed against architecture-design/principles"
add_options "Capability Span" \
  "API" "Event" "Data" "Schema" "Semantics" "Knowledge Graph" "MCP" "A2A" \
  "Agent" "Infrastructure" "Security" "Observability" "Delivery"

echo

# --- 3. the Demand issue type ----------------------------------------------

echo "--- 3/4 issue type ---"

if api_get "/rest/api/3/issuetype" | jq -e '[.[] | select(.name == "Demand")] | length > 0' >/dev/null; then
  echo "  = Demand (exists)"
else
  echo "  + Demand"
  body=$(jq -n '{
    name: "Demand",
    description: "A request for new integration work. Validated, designed and architecture-reviewed before it becomes delivery work. See jira/demand-workflow.md.",
    type: "standard",
    hierarchyLevel: 0
  }')
  api_write POST "/rest/api/3/issuetype" "$body" >/dev/null
fi

echo

# --- 4. statuses ------------------------------------------------------------
# Columns: name <TAB> category <TAB> description
# Note there is no Redesign status by design — it is a transition back into
# Design In Progress. See demand-workflow.md §1.

STATUSES_TSV=$(cat <<'TSV'
Demand Received	TODO	Request raised. Needs requester, business outcome, consuming system and target date.
Demand Validation	IN_PROGRESS	Being validated. Reuse search and capability span required to leave.
Invalid	DONE	Terminal, reopenable. Reason recorded in Invalid Reason.
Met by Existing Capability	DONE	Terminal. Satisfied by an already-registered contract named in Satisfying Contract.
Design In Progress	IN_PROGRESS	Design underway. HLD, security posture and observability posture required to leave.
Design Complete	IN_PROGRESS	Design drafted and reviewable in version control. Awaiting architecture review.
Architecture Review	IN_PROGRESS	Under architecture review. Exits to Ready for Build, or back via Redesign.
Ready for Build	TODO	Approved. Domain Epic and Stories to be created; hands off to the delivery backlog.
TSV
)

echo "--- 4/4 statuses ---"

while IFS=$'\t' read -r sname scat sdesc; do
  [ -n "$sname" ] || continue

  found=$(api_get "/rest/api/3/statuses/search?searchString=$(printf '%s' "$sname" | jq -sRr @uri)" \
    | jq -r --arg n "$sname" '[.values[]? | select(.name == $n)] | length')

  if [ "${found:-0}" -gt 0 ]; then
    echo "  = ${sname} (exists)"
    continue
  fi

  echo "  + ${sname} [${scat}]"
  body=$(jq -n --arg n "$sname" --arg c "$scat" --arg d "$sdesc" \
    '{scope: {type: "GLOBAL"}, statuses: [{name: $n, statusCategory: $c, description: $d}]}')
  api_write POST "/rest/api/3/statuses" "$body" >/dev/null
done <<TSV_INPUT
${STATUSES_TSV}
TSV_INPUT

echo

# --- optional: place fields on a screen ------------------------------------
# A field that exists but is not on the Demand issue type's create and edit
# screens is invisible, and gets reported as missing. This is the single most
# common way the workflow appears broken after provisioning.

if [ -n "$SCREEN_ID" ]; then
  echo "--- screen placement (screen ${SCREEN_ID}) ---"

  if [ -z "$TAB_ID" ]; then
    TAB_ID=$(api_get "/rest/api/3/screens/${SCREEN_ID}/tabs" | jq -r '.[0].id // empty')
    [ -n "$TAB_ID" ] || { echo "  ! no tabs found on screen ${SCREEN_ID}" >&2; exit 1; }
    echo "  using first tab: ${TAB_ID}"
  fi

  for fid in $CREATED_FIELD_IDS; do
    echo "  + ${fid} -> screen ${SCREEN_ID} tab ${TAB_ID}"
    api_write POST "/rest/api/3/screens/${SCREEN_ID}/tabs/${TAB_ID}/fields" \
      "$(jq -n --arg id "$fid" '{fieldId: $id}')" >/dev/null
  done
  echo
else
  echo "--- screen placement skipped (no --screen-id) ---"
  echo "  Fields not on the Demand create/edit screens are invisible in the UI."
  echo "  List candidate screens with:"
  echo "    curl -sS -u \"\$JIRA_USER_EMAIL:\$JIRA_API_TOKEN\" \\"
  echo "      \"${BASE}/rest/api/3/screens?maxResults=100\" | jq '.values[] | {id, name}'"
  echo
fi

# --- next steps -------------------------------------------------------------

cat <<'NEXT'
=== Remaining steps — UI only, in this order (demand-workflow.md §7) ===

  1. Build the workflow: the 8 statuses above + the transitions in §1.
     Add the field-required validators per transition from §4a. The validator
     picker shows what this instance actually supports — do not assume.
  2. PUBLISH THE WORKFLOW DRAFT. An unpublished draft has no effect while the
     board looks entirely correct.
  3. Associate the workflow with the Demand issue type via the project's
     workflow scheme.
  4. Build the four Automation rules in §4c (no public API exists for these):
       - Ready for Build            -> create/link the Domain Epic
       - Redesign                   -> increment Rework Count
       - Architecture Review entered-> notify the Solution Architect
       - plus any §4b revert fallbacks for gates with no available validator
     In rule details, leave "Allow rule to be triggered by other rules"
     UNCHECKED, or the revert rules will re-trigger themselves.
  5. Walk one throwaway Demand through every transition, including Invalid
     and Redesign, and confirm each gate actually refuses an incomplete move.
NEXT
