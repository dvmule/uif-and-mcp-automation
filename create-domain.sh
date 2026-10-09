#!/usr/bin/env bash
#
# create-domain.sh — stamp a new UIF domain from the mould (_domain-template/).
#
# A domain is a deployable, ownable business unit and builds to ONE container
# image (the single build/deploy contract). The domain root stays minimal
# (README, CLAUDE skill, checklist); code lives under src/, tests under qe/,
# experience under ui/ + ux/, and everything that runs the image under
# infrastructure/. Capability surfaces (api, schemas + opt-in events, mcp, …)
# live under src/ so one Dockerfile builds them all.
#
# Usage:
#   ./create-domain.sh <domain-name> [--caps <c1,c2,...>|all] \
#       [--owner-fde @h] [--owner-ux @h] [--owner-qe @h] [--owner-pm @h]
#
# Examples:
#   ./create-domain.sh legal                       # core only (apps + api + schemas)
#   ./create-domain.sh product --caps events,mcp,agents
#   ./create-domain.sh hr --caps all
#
# Opt-in capabilities (added under src/): events semantics knowledge-graph mcp a2a agents
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAINS_DIR="$ROOT/domains"

# ---- args -------------------------------------------------------------------
DOMAIN="${1:-}"
if [[ -z "$DOMAIN" || "$DOMAIN" == -* ]]; then
  echo "error: domain name required. usage: ./create-domain.sh <name> [--caps ...]" >&2
  exit 1
fi
shift || true

CAPS_ARG=""
OWNER_FDE="@uif-fde"; OWNER_UX="@uif-ux"; OWNER_QE="@uif-qe"; OWNER_PM="@uif-pm"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --caps)      CAPS_ARG="${2:-}"; shift 2 ;;
    --owner-fde) OWNER_FDE="${2:-}"; shift 2 ;;
    --owner-ux)  OWNER_UX="${2:-}"; shift 2 ;;
    --owner-qe)  OWNER_QE="${2:-}"; shift 2 ;;
    --owner-pm)  OWNER_PM="${2:-}"; shift 2 ;;
    *) echo "error: unknown flag '$1'" >&2; exit 1 ;;
  esac
done

TARGET="$DOMAINS_DIR/$DOMAIN"

# Case-insensitive duplicate check: macOS/APFS is case-insensitive by default so
# `-e` alone catches "Product" vs "product" there, but Linux ext4 (CI runners)
# is case-sensitive and would silently allow both — check explicitly so the
# error is consistent on every OS.
DOMAIN_LOWER="$(echo "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
if [[ -d "$DOMAINS_DIR" ]]; then
  for existing in "$DOMAINS_DIR"/*/; do
    [[ -d "$existing" ]] || continue
    existing_name="$(basename "$existing")"
    existing_lower="$(echo "$existing_name" | tr '[:upper:]' '[:lower:]')"
    if [[ "$existing_lower" == "$DOMAIN_LOWER" ]]; then
      echo "error: domain '$DOMAIN' already exists as domains/$existing_name (case-insensitive match)" >&2
      exit 1
    fi
  done
fi

# ---- folder sets ------------------------------------------------------------
SRC_CORE=(apps api schemas)                                   # always, under src/
SRC_CAPS=(events semantics knowledge-graph mcp a2a agents)    # opt-in, under src/
INFRA_DIRS=(terraform kubernetes helm observability)          # always, under infrastructure/
TOP_DIRS=(qe ui ux)                                           # always, at domain root

purpose() {
  case "$1" in
    apps)            echo "Deployable application code for this domain." ;;
    api)             echo "API capability surface: openapi.yaml + handlers (../../../capabilities/api/)." ;;
    schemas)         echo "Registered contracts for this domain (../../../stacks/apicurio/standards/)." ;;
    events)          echo "Published/consumed events, asyncapi.yaml (../../../capabilities/event/)." ;;
    semantics)       echo "Domain semantic model / vocabulary (../../../capabilities/semantics/)." ;;
    knowledge-graph) echo "Domain knowledge-graph projections (../../../capabilities/knowledge-graph/)." ;;
    mcp)             echo "MCP servers this domain exposes (../../../capabilities/mcp/)." ;;
    a2a)             echo "Agent-to-agent endpoints (../../../capabilities/a2a/)." ;;
    agents)          echo "Domain AI agents; add prompts/ and evals/ (../../../capabilities/agent/)." ;;
    qe)              echo "Quality strategy, automation harness, and test suites (QE Lead)." ;;
    ui)              echo "Domain UI implementation (UX Lead)." ;;
    ux)              echo "UX specs, flows, and research for this domain (UX Lead)." ;;
    terraform)       echo "Infrastructure as code (../../../capabilities/infrastructure/)." ;;
    kubernetes)      echo "Kubernetes manifests." ;;
    helm)            echo "Helm chart." ;;
    observability)   echo "SLOs, dashboards, alerts (../../../capabilities/observability/)." ;;
    *)               echo "Domain folder." ;;
  esac
}

# ---- resolve selected capability folders -----------------------------------
SELECTED_CAPS=()
if [[ "$CAPS_ARG" == "all" ]]; then
  SELECTED_CAPS=("${SRC_CAPS[@]}")
elif [[ -n "$CAPS_ARG" ]]; then
  IFS=',' read -r -a REQ <<< "$CAPS_ARG"
  for c in "${REQ[@]}"; do
    c="$(echo "$c" | tr -d '[:space:]')"
    if printf '%s\n' "${SRC_CAPS[@]}" | grep -qx "$c"; then
      SELECTED_CAPS+=("$c")
    else
      echo "error: unknown capability '$c'. valid: ${SRC_CAPS[*]}" >&2
      exit 1
    fi
  done
fi

# ---- helpers ----------------------------------------------------------------
make_folder() {  # <relative-dir>
  local dir="$1"
  local leaf; leaf="$(basename "$dir")"
  mkdir -p "$TARGET/$dir"
  cat > "$TARGET/$dir/README.md" <<EOF
# $DOMAIN / $dir

$(purpose "$leaf")
EOF
}

# ---- stamp: root metadata ---------------------------------------------------
mkdir -p "$TARGET"
cp "$ROOT/_domain-template/DOMAIN-CHECKLIST.md" "$TARGET/DOMAIN-CHECKLIST.md"

cat > "$TARGET/README.md" <<EOF
# Domain: $DOMAIN

A deployable, ownable business unit of UIF. Builds to ONE container image
(\`src/Dockerfile\`) — the single build/deploy contract. Stamped from
\`../../_domain-template/\`. Standards for each capability live in
\`../../capabilities/<capability>/\`.

- Code + capability surfaces: \`src/\`
- Tests & quality: \`qe/\`  ·  Experience: \`ui/\`, \`ux/\`
- Run it: \`infrastructure/\` (terraform, kubernetes, helm, observability)

See \`CLAUDE.md\` for domain context and \`DOMAIN-CHECKLIST.md\` for the definition of done.

## Included capability surfaces (under src/)

apps, api, schemas${SELECTED_CAPS:+, ${SELECTED_CAPS[*]}}
EOF

cat > "$TARGET/CLAUDE.md" <<EOF
# $DOMAIN — domain skill

Context for engineers and AI assistants working in the \`$DOMAIN\` domain.

## What this domain owns
_Describe the bounded context: the business capability this domain is responsible
for, and the boundary it does NOT cross. Domains integrate through contracts
(APIs/events/schemas), not shared code._

## How it builds & deploys
- One container image from \`src/Dockerfile\` (any language / authorized library).
- CI runs at repo root (\`../../.github/workflows/domain-ci.yml\`) on \`domains/$DOMAIN/**\`.
- CD promotes Dev → QA → Stage → Prod via the reusable root workflow (\`../../.github/workflows/domain-cd.yml\`); no domain-local pipeline file.

## Conventions
- API First + Schema Registry First: register \`src/schemas/\` before implementing.
- Security & observability are design inputs (\`infrastructure/observability/\`, \`../../capabilities/security/\`).
- Follow the FDE workflow: \`../../architecture-design/operating-model/fde-build-deploy-workflow.md\`.

## Ownership (Four-in-a-Box)
FDE: \`src/\`, \`infrastructure/\` · QE Lead: \`qe/\` · UX Lead: \`ui/\`, \`ux/\` · PM: scope & Jira trace.
EOF

# ---- stamp: src/ (core + selected caps) + Dockerfile ------------------------
for d in "${SRC_CORE[@]}"; do make_folder "src/$d"; done
if ((${#SELECTED_CAPS[@]})); then
  for d in "${SELECTED_CAPS[@]}"; do make_folder "src/$d"; done
  if printf '%s\n' "${SELECTED_CAPS[@]}" | grep -qx "agents"; then
    mkdir -p "$TARGET/src/agents/prompts" "$TARGET/src/agents/evals"
  fi
fi

cat > "$TARGET/src/Dockerfile" <<EOF
# Dockerfile — the single build/deploy contract for the $DOMAIN domain.
# The platform is language-agnostic: it only requires this to produce ONE
# runnable image. Replace the body with your language's build.
#
# Example (Node):
#   FROM node:20-alpine
#   WORKDIR /app
#   COPY . .
#   RUN npm ci && npm run build
#   CMD ["npm","start"]
EOF

# ---- stamp: top-level work folders -----------------------------------------
for d in "${TOP_DIRS[@]}"; do make_folder "$d"; done

# ---- stamp: infrastructure/ (no domain-local pipeline file; CD is centralized
# in ../../.github/workflows/domain-cd.yml, same reusable-workflow shape as CI) --
for d in "${INFRA_DIRS[@]}"; do make_folder "infrastructure/$d"; done

# ---- CODEOWNERS suggestion --------------------------------------------------
echo
echo "Created domains/$DOMAIN (src: apps api schemas [${SELECTED_CAPS[*]:-none}])."
echo
echo "Ownership already covered by wildcard rules in .github/CODEOWNERS:"
echo "  /domains/$DOMAIN/          -> $OWNER_FDE (default: src/, infrastructure/)"
echo "  /domains/$DOMAIN/qe/       -> $OWNER_QE"
echo "  /domains/$DOMAIN/ui/,ux/   -> $OWNER_UX"
echo "  PM ($OWNER_PM) owns scope + the Jira trace."
