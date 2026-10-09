#!/usr/bin/env bash
# Assemble the release manifest carried in the annotated tag's message.
#
# The manifest is the release's evidence: it is what QE signs off against and
# what Ops rolls back to. So this script's real job is refusal. It will not
# write a manifest that records a mutable tag where a digest belongs, a
# truncated digest, or a gate that did not pass -- because a manifest that
# lies is worse than none at all, looking exactly like evidence.
#
# Every input arrives in the environment. BUILT_AT is overridable so the
# output is reproducible under test.

set -euo pipefail

: "${TAG:?TAG is required}"
: "${GIT_COMMIT:?GIT_COMMIT is required}"
: "${GATE_RUN_ID:?GATE_RUN_ID is required}"
: "${GATE_CONCLUSION:?GATE_CONCLUSION is required}"
: "${BFF_REPOSITORY:?BFF_REPOSITORY is required}"
: "${BFF_DIGEST:?BFF_DIGEST is required}"
: "${UI_SHA256:?UI_SHA256 is required}"
: "${UI_ARCHIVE:?UI_ARCHIVE is required}"
: "${UI_VERSION_ID:?UI_VERSION_ID is required}"
: "${REGISTRY_REFERENCE:?REGISTRY_REFERENCE is required}"
: "${REGISTRY_DIGEST:?REGISTRY_DIGEST is required}"

die() { echo "error: $*" >&2; exit 1; }

# Lowercase hex only: ECR and quay both return lowercase, and accepting
# uppercase would let two spellings of one digest into the record.
for pair in "BFF_DIGEST=${BFF_DIGEST}" "REGISTRY_DIGEST=${REGISTRY_DIGEST}"; do
  name="${pair%%=*}"; value="${pair#*=}"
  printf '%s' "$value" | grep -Eq '^sha256:[0-9a-f]{64}$' \
    || die "${name} must be a full digest of the form sha256:<64 lowercase hex>, got '${value}'"
done

printf '%s' "$UI_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
  || die "UI_SHA256 must be 64 lowercase hex characters with no sha256: prefix, got '${UI_SHA256}'"

[ "$GATE_CONCLUSION" = "success" ] \
  || die "the stacks-ci gate concluded '${GATE_CONCLUSION}', refusing to write a manifest claiming it passed"

# Quoted below: an unquoted all-digit or ISO-8601-shaped scalar is implicitly
# retyped by a YAML parser (int or timestamp), and every one of these fields
# is an opaque identifier that can legitimately be all-digit -- a commit sha,
# an S3 version id, a bare hash, a build timestamp. Silently changing type is
# exactly the "manifest that lies" failure this script exists to prevent.
# run_id is deliberately left unquoted: it is meant to be read back as an
# integer, not an opaque string.
cat <<YAML
${TAG}:
  git_commit: "${GIT_COMMIT}"
  built_at: "${BUILT_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  gate:
    check: stacks-ci gate
    run_id: ${GATE_RUN_ID}
    conclusion: ${GATE_CONCLUSION}
  artifacts:
    bff_image:
      repository: ${BFF_REPOSITORY}
      digest: ${BFF_DIGEST}
    ui_bundle:
      sha256: "${UI_SHA256}"
      archive: ${UI_ARCHIVE}
      version_id: "${UI_VERSION_ID}"
    registry_image:
      reference: ${REGISTRY_REFERENCE}
      digest: ${REGISTRY_DIGEST}
YAML
