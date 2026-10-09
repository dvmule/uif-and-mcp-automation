#!/usr/bin/env bash
# Copy a container image from one repository to another, preserving its digest
# -- and refuse if it did not.
#
# This exists because artifact stores are per-environment (owner decision,
# 2026-09-20), so promoting a build tag to uat or prod means copying the BFF
# image into that environment's own ECR repository. The digest in the build
# tag's manifest is the artifact's identity, so a copy that changes it does not
# promote the release: it deploys a different, unrecorded image under the
# release's name.
#
# WHY THE ASSERTION IS THE POINT, NOT THE COPY. Measured against a local
# registry:2 on 2026-09-20, Docker 29.7.2 / overlay2:
#
#   single-architecture image   docker pull+tag+push  PRESERVED   crane PRESERVED
#   multi-architecture index    docker pull+tag+push  CHANGED     crane PRESERVED
#
# So for the shape this stack actually builds -- src/Dockerfile is built
# --platform=linux/amd64, one architecture -- plain docker happened to preserve
# the digest. That is a measurement on one daemon version, not a guarantee, and
# the multi-arch row shows what the failure looks like when it comes: docker
# pulls ONE platform, pushes it as a single manifest, and silently discards the
# rest. In that test 15 of 16 platforms disappeared and the digest changed, with
# no error from any command. A silent failure is exactly the kind a release
# pipeline must assert against rather than trust, which is why this script reads
# the destination digest back out of the registry and compares it.
#
# WHY CRANE AND NOT DOCKER, given both passed the single-arch case. Two
# reasons that hold regardless of the digest question: crane copies the
# manifest verbatim so the multi-arch row above cannot happen, and within one
# registry it MOUNTS existing blobs rather than re-uploading them -- observed in
# the same run -- so a same-account ECR copy transfers no layer bytes. docker
# pull+push downloads and re-uploads every layer.
#
# `aws ecr batch-get-image` + `put-image` is NOT an alternative. It moves the
# manifest but ECR stores layers per-repository, so put-image fails on a
# repository whose layers are absent -- which is every first copy.
#
# Install crane with curl in a `run:` step, never `uses:`. Only actions,
# github, hashicorp, aws-actions, aquasecurity, sonarsource, opentofu and
# pearson* pass the enterprise allow-list, and one disallowed `uses:` fails the
# whole workflow at startup with no job logs.
#
# Usage: copy-image.sh <source-ref> <dest-ref> [expected-digest]
#
# Passing the expected digest -- the one from the release manifest -- makes the
# script refuse to copy anything else, so a promotion cannot quietly ship a
# newer image that happens to sit under the same tag.
#
# Prints the digest on success. Exits non-zero, having said which check failed,
# on anything else.

set -euo pipefail

CRANE="${CRANE:-crane}"

# CRANE_INSECURE=1 adds --insecure, for a plain-HTTP registry. The tests set it
# against a local registry:2; nothing in a pipeline should.
insecure=()
[ "${CRANE_INSECURE:-}" = "1" ] && insecure=(--insecure)

src="${1:-}"
dst="${2:-}"
expected="${3:-}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$src" ] && [ -n "$dst" ] \
  || die "usage: copy-image.sh <source-ref> <dest-ref> [expected-digest]"

[ "$src" != "$dst" ] \
  || die "source and destination are the same reference (${src}); a copy onto itself proves nothing"

command -v "$CRANE" >/dev/null 2>&1 \
  || die "crane not found on PATH (set CRANE to override). Install it with curl from https://github.com/google/go-containerregistry/releases -- not a third-party action, which the enterprise allow-list would reject."

# Checked before the copy, so a malformed pin fails in under a second rather
# than after the layers have moved. Lowercase hex only: ECR returns lowercase,
# and accepting uppercase would let two spellings of one digest through.
if [ -n "$expected" ]; then
  printf '%s' "$expected" | grep -Eq '^sha256:[0-9a-f]{64}$' \
    || die "expected-digest must be sha256:<64 lowercase hex>, got '${expected}'"
fi

# The MANIFEST digest, which is what ECR reports and what the release manifest
# records. Not the image config id: `docker build -q` prints the config id, and
# during the 2026-09-20 spike those two differed for the same image
# (config 24808e4b…, manifest d797892a…). Treating one as the other is a
# comparison that fails for a reason that has nothing to do with the copy.
src_digest="$("$CRANE" ${insecure[@]+"${insecure[@]}"} digest "$src")" \
  || die "could not read a digest for the source ${src}. Whether it exists is unknown, so nothing was copied."

if [ -n "$expected" ] && [ "$src_digest" != "$expected" ]; then
  die "$(printf '%s\n' \
    "${src} is ${src_digest}, but the release expects ${expected}." \
    "  Nothing was copied. A tag that has moved under a pinned digest means the" \
    "  artifact under test is not the artifact being promoted -- re-cut the build" \
    "  tag rather than relaxing this check.")"
fi

"$CRANE" ${insecure[@]+"${insecure[@]}"} copy "$src" "$dst" \
  || die "crane copy ${src} -> ${dst} failed; see its output above."

# Read back from the registry rather than trusting the copy's own report. The
# failure this guards against is silent, so the only evidence worth having is
# what the destination registry now serves.
dst_digest="$("$CRANE" ${insecure[@]+"${insecure[@]}"} digest "$dst")" \
  || die "copied to ${dst}, but could not read its digest back, so whether identity survived is unknown. Treat this as a failed promotion."

if [ "$dst_digest" != "$src_digest" ]; then
  die "$(printf '%s\n' \
    "the copy did not preserve the digest." \
    "  source      ${src} = ${src_digest}" \
    "  destination ${dst} = ${dst_digest}" \
    "  ${dst} now holds a DIFFERENT artifact under the release's name. Delete it" \
    "  before retrying. If the source is a multi-architecture index, this is the" \
    "  flattening failure described at the top of this script -- the copy tool is" \
    "  wrong, not the image.")"
fi

echo "$dst_digest"
