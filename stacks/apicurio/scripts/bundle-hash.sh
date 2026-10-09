#!/usr/bin/env bash
# Content hash of a built UI bundle -- the bundle's identity in the release
# manifest.
#
# Two properties this has to have, and one it must NOT have.
#
# Must: depend on every file's path AND contents (a renamed chunk is a
# different bundle), and be identical for identical contents regardless of
# where dist/ happens to sit on disk -- release-apicurio.yml and
# deploy-apicurio-dev.yml both call this and have to agree.
#
# Must not: depend on ./config.json. That file is environment state, written
# per environment at deploy time, and including it would make the hash
# environment-specific -- which would make one bundle unpromotable and defeat
# the reason the runtime-config change exists at all.
#
# Usage: bundle-hash.sh <dist-dir>
# Prints: 64 lowercase hex characters.

set -euo pipefail

dist="${1:?usage: bundle-hash.sh <dist-dir>}"

if [ ! -d "$dist" ]; then
  echo "error: ${dist} is not a directory" >&2
  exit 1
fi

# cd first so every path fed to sha256sum is relative (./index.html), which is
# what makes the hash independent of the directory's location.
cd "$dist"

count="$(find . -type f ! -path './config.json' | wc -l | tr -d '[:space:]')"
if [ "$count" -eq 0 ]; then
  echo "error: no files under ${dist} to hash" >&2
  exit 1
fi

# -print0 / sort -z / xargs -0 so a filename containing a space or newline
# cannot split into two entries. LC_ALL=C fixes the collation order, which
# otherwise varies by locale and would give a different hash on a different
# machine for the same bundle.
find . -type f ! -path './config.json' -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  | sha256sum \
  | cut -d' ' -f1
