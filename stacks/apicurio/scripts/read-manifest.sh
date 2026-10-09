#!/usr/bin/env bash
# Read one field out of a build tag's release manifest.
#
# The manifest is the message of the annotated tag `release-apicurio.yml`
# creates, so a promotion's inputs come from the tag itself rather than from a
# second copy of them in a workflow. Two copies of a digest is a drift class,
# not a convenience -- the same reason read-tfvar.sh exists.
#
# WHY THIS IS A STRICT SHAPE READER AND NOT A YAML PARSER. The manifest has one
# fixed shape, written by release-manifest.sh, and a promotion that reads a
# manifest it does not fully understand is precisely the failure to avoid. A
# general parser would accept a manifest written by something else, in a shape
# this stack has never tested, and hand back plausible values. This accepts the
# shape release-manifest.sh writes and refuses everything else.
#
# It also avoids depending on PyYAML, which is NOT a declared dependency of
# src/pyproject.toml -- it is only transitively present in a local venv, so
# `import yaml` would work here and fail on a runner that installed only the
# declared set.
#
# WHY IT REFUSES A GATE THAT DID NOT PASS. release-manifest.sh already refuses
# to WRITE such a manifest, so a tag carrying one should not exist. Checking
# again on the read side costs one comparison and covers the case that matters:
# a tag created by hand, or by an older version of the writer, being promoted
# to an environment. Evidence is only evidence if it is checked where it is
# used.
#
# Usage: read-manifest.sh <tag> <field>
#
# Fields:
#   git_commit  gate.run_id  gate.conclusion
#   bff.repository  bff.digest
#   ui.sha256  ui.archive  ui.version_id
#   registry.reference  registry.digest
#
# Prints the value. Exits non-zero, saying which check failed, on anything else.

set -euo pipefail

tag="${1:-}"
field="${2:-}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$tag" ] && [ -n "$field" ] \
  || die "usage: read-manifest.sh <tag> <field>"

# An ANNOTATED tag or nothing. A lightweight tag is just a ref: it has no
# message, so it carries no manifest, and reading one would silently yield
# empty fields rather than saying the tag is the wrong kind of object.
object_type="$(git cat-file -t "$tag" 2>/dev/null || true)"
case "$object_type" in
  tag) ;;
  "")  die "${tag} does not exist here. Fetch tags first -- a shallow clone has none of them (release-apicurio.yml uses fetch-depth: 0 for this reason)." ;;
  commit) die "${tag} is a LIGHTWEIGHT tag, which has no message and therefore no manifest. Only the annotated tags release-apicurio.yml creates carry one." ;;
  *) die "${tag} resolves to a git ${object_type}, not a tag object." ;;
esac

manifest="$(git for-each-ref "refs/tags/${tag}" --format='%(contents)')"
[ -n "$manifest" ] || die "${tag} is annotated but its message is empty, so there is no manifest to read."

# The manifest's first line is `<tag>:`, which is both a sanity check that we
# are reading what we think we are and a guard against promoting a tag whose
# message was rewritten to describe a different release.
first_line="$(printf '%s\n' "$manifest" | head -1)"
[ "$first_line" = "${tag}:" ] \
  || die "$(printf '%s\n' \
    "${tag}'s manifest opens with '${first_line}', not '${tag}:'." \
    "  The message does not describe this tag, so nothing in it can be trusted" \
    "  as this release's evidence.")"

# Every field is a leaf scalar, and each leaf key appears exactly once in the
# shape release-manifest.sh writes -- so matching the key anchored to
# start-of-line-plus-indent is unambiguous without tracking nesting. `digest`
# appears twice (bff_image and registry_image), which is why those two are
# addressed by their parent block rather than by key alone.
leaf() {
  printf '%s\n' "$manifest" \
    | sed -n "/^    ${1}:\$/,/^    [a-z_]*:\$/p" \
    | grep -E "^      ${2}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^      ${2}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

top() {
  printf '%s\n' "$manifest" \
    | grep -E "^  ${1}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^  ${1}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

nested() {
  printf '%s\n' "$manifest" \
    | sed -n "/^  ${1}:\$/,/^  [a-z_]*:\$/p" \
    | grep -E "^    ${2}:[[:space:]]" \
    | head -1 \
    | sed -E "s/^    ${2}:[[:space:]]*//" \
    | sed -E 's/^"(.*)"$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
}

# Checked on EVERY read, not only when the caller asks for it. A promotion that
# reads the bff digest and never looks at the gate would deploy a build whose
# evidence says it failed.
conclusion="$(nested gate conclusion)"
[ "$conclusion" = "success" ] \
  || die "$(printf '%s\n' \
    "${tag}'s manifest records the stacks-ci gate as '${conclusion:-<absent>}', not 'success'." \
    "  Refusing to hand out any field from it: a build whose gate did not pass" \
    "  must not be promoted, and release-manifest.sh should never have written" \
    "  this. Re-cut the tag from a commit with a green gate.")"

case "$field" in
  git_commit)         value="$(top git_commit)" ;;
  gate.run_id)        value="$(nested gate run_id)" ;;
  gate.conclusion)    value="$conclusion" ;;
  bff.repository)     value="$(leaf bff_image repository)" ;;
  bff.digest)         value="$(leaf bff_image digest)" ;;
  ui.sha256)          value="$(leaf ui_bundle sha256)" ;;
  ui.archive)         value="$(leaf ui_bundle archive)" ;;
  ui.version_id)      value="$(leaf ui_bundle version_id)" ;;
  registry.reference) value="$(leaf registry_image reference)" ;;
  registry.digest)    value="$(leaf registry_image digest)" ;;
  *) die "unknown field '${field}'. Known: git_commit gate.run_id gate.conclusion bff.repository bff.digest ui.sha256 ui.archive ui.version_id registry.reference registry.digest" ;;
esac

# Absent is an ERROR, never an empty string. An empty digest passed to
# `terraform apply -var bff_image_digest=` is a variable that is set and wrong,
# and Terraform cannot tell it from a deliberate value.
[ -n "$value" ] \
  || die "${field} is absent from ${tag}'s manifest, or the manifest is not in the shape release-manifest.sh writes."

# The same shape rules the writer enforces, checked again here for the same
# reason the gate is: a manifest is only evidence where it is used.
case "$field" in
  bff.digest|registry.digest)
    printf '%s' "$value" | grep -Eq '^sha256:[0-9a-f]{64}$' \
      || die "${field} is '${value}', not a full sha256:<64 lowercase hex> digest. A truncated or mutable reference is not something an environment can be pinned to." ;;
  ui.sha256)
    printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' \
      || die "ui.sha256 is '${value}', not 64 lowercase hex characters with no sha256: prefix." ;;
  git_commit)
    printf '%s' "$value" | grep -Eq '^[0-9a-f]{40}$' \
      || die "git_commit is '${value}', not a full 40-character sha." ;;
esac

printf '%s\n' "$value"
