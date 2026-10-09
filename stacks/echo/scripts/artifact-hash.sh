#!/usr/bin/env bash
# Content hash of one build artifact -- its identity in the release manifest,
# and the S3 key it is stored under.
#
# Echo's artifacts are file trees (a Lambda's source set, a layer's contents,
# the UI bundle) and single files (a Glue script). Both hash to the same shape:
# 64 lowercase hex characters.
#
# WHY THE TREE AND NOT THE ARCHIVE. A zip or tar embeds timestamps, permissions
# and entry order, so hashing the archive gives a different hash for identical
# source on a different machine or a different run. The promotion model needs
# four consecutive build tags of unchanged source to produce identical hashes,
# so the hash has to come from the content itself.
# stacks/apicurio/scripts/bundle-hash.sh is the precedent.
#
# Three properties this must have, each with a failure behind it:
#
#   1. Independent of where the tree sits on disk. release-echo.yml and a
#      developer's laptop both call this and have to agree, and they do not
#      share a checkout path. Achieved by cd'ing in first so every path fed to
#      sha256sum is relative (./handler.py).
#   2. Stable under any filename. -print0 / sort -z / xargs -0 so a name
#      containing a space or a newline cannot split into two entries -- which
#      would silently change the hash of a tree nobody edited.
#   3. Stable under any locale. LC_ALL=C fixes the collation order; without it
#      the same tree hashes differently on a machine with a different locale,
#      and the reproducibility check fails for a reason nobody can see.
#
# One exclusion, and it is load-bearing: --exclude config.json, for the UI
# bundle. config.json is environment state written at publish time from the
# target environment's api_endpoint. Including it would make the hash
# environment-specific, which would make one bundle unpromotable and defeat the
# reason the runtime-config change exists at all.
#
# Usage:
#   artifact-hash.sh <path>                    a file, or a directory tree
#   artifact-hash.sh <dir> --exclude <name>    repeatable; matched on the
#                                              relative path inside <dir>
#
# Prints: 64 lowercase hex characters.
# Exit: 0 on success, 1 on any refusal.

set -euo pipefail

die() { printf '%s: %s\n' "artifact-hash.sh" "$1" >&2; exit 1; }

target="${1:-}"
[ -n "$target" ] || die "usage: artifact-hash.sh <path> [--exclude <name>]..."
shift

excludes=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --exclude)
      [ "$#" -ge 2 ] || die "--exclude needs a value"
      excludes+=("$2")
      shift 2
      ;;
    *) die "unknown flag '$1'" ;;
  esac
done

# A single file hashes to its own content. No path component is involved: a
# Glue script's identity is its bytes, and the filename is carried separately
# in the manifest's `file` field.
if [ -f "$target" ]; then
  [ "${#excludes[@]}" -eq 0 ] \
    || die "--exclude is meaningless for a single file (${target})"
  sha256sum "$target" | cut -d' ' -f1
  exit 0
fi

[ -d "$target" ] \
  || die "${target} is neither a file nor a directory"

cd "$target"

# Build the find expression. Each exclusion is matched against the relative
# path, so `--exclude config.json` excludes ./config.json at the root and not a
# nested assets/config.json -- the latter would be a real part of the bundle.
find_args=(. -type f)
for ex in "${excludes[@]+"${excludes[@]}"}"; do
  find_args+=(! -path "./${ex}")
done

count="$(find "${find_args[@]}" -print | wc -l | tr -d '[:space:]')"
if [ "$count" -eq 0 ]; then
  die "no files to hash under ${target} (after ${#excludes[@]} exclusion(s)). An empty artifact is a build failure, not a hash of nothing."
fi

# The inner sha256sum emits "<hash>  <relative path>" per file, so the outer
# one depends on every file's contents AND its path -- a renamed file is a
# different artifact.
find "${find_args[@]}" -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  | sha256sum \
  | cut -d' ' -f1
