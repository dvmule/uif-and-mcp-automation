#!/usr/bin/env bash
# Restore a released UI bundle from its archive, and refuse unless what came
# out is what the release recorded.
#
# The promotion path never rebuilds the UI: it takes the archive the build tag
# names and publishes those exact bytes. That is what makes one bundle
# promotable across environments, so the only question worth asking here is
# whether the extracted tree still hashes to the sha256 in the manifest.
#
# WHY RE-HASH RATHER THAN CHECKSUM THE TARBALL. Comparing the tarball's own
# bytes proves a byte-copy happened; it does not prove the bundle inside is the
# bundle the release identified. Re-running bundle-hash.sh over the extracted
# tree checks exactly the invariant the manifest asserts -- every file's path
# and contents -- and it is the same function release-apicurio.yml and
# deploy-apicurio-dev.yml both call, so the three cannot disagree about what
# the bundle's identity is.
#
# It also means this verification needs no S3 and no AWS credentials. Copying
# the archive between buckets is `aws s3 cp`, a one-liner that needs no script;
# deciding whether the result is the release is this.
#
# Usage: restore-bundle.sh <archive.tar.gz> <dest-dir> <expected-sha256>
#
# Prints the verified hash. Exits non-zero, saying which check failed, on
# anything else. The destination is left populated ONLY on success, so a
# failed restore cannot be published by a later step that forgot to check.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_HASH="${BUNDLE_HASH:-${HERE}/bundle-hash.sh}"

archive="${1:-}"
dest="${2:-}"
expected="${3:-}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$archive" ] && [ -n "$dest" ] && [ -n "$expected" ] \
  || die "usage: restore-bundle.sh <archive.tar.gz> <dest-dir> <expected-sha256>"

[ -f "$archive" ] || die "archive ${archive} does not exist. Download it from the release-artifacts bucket first (aws s3 cp), then restore."
[ -x "$BUNDLE_HASH" ] || die "bundle-hash.sh not found or not executable at ${BUNDLE_HASH}"

# 64 lowercase hex, no algorithm prefix -- the same shape release-manifest.sh
# writes and read-manifest.sh returns. Checked before extracting, so a
# malformed pin fails in a second rather than after the tree is on disk.
printf '%s' "$expected" | grep -Eq '^[0-9a-f]{64}$' \
  || die "expected-sha256 must be 64 lowercase hex characters with no sha256: prefix, got '${expected}'"

listing="$(tar -tzf "$archive" 2>/dev/null)" \
  || die "${archive} is not a readable gzipped tar. A truncated download looks exactly like this; check the object's size against the bucket."

[ -n "$listing" ] || die "${archive} is an empty archive, so there is no bundle to restore."

# EXTRACTION SAFETY, checked against the listing before anything is written.
# The archives this reads are ones we produced, so this is defence in depth
# rather than a live threat -- but an absolute path or a `..` component in a
# tar entry writes outside the destination, and the cost of checking is one
# pass over a list of four names.
while IFS= read -r entry; do
  case "$entry" in
    /*) die "${archive} contains an absolute path (${entry}); refusing to extract." ;;
    # Every real traversal form, and no more: a bare `..`, a leading `../`, an
    # interior `/../`, or a trailing `/..`. Matching `*..*` instead would also
    # reject an innocent name like `chunk..map`.
    ..|../*|*/../*|*/..) die "${archive} contains a parent-directory reference (${entry}); refusing to extract." ;;
  esac
done <<EOF
$listing
EOF

# config.json is environment state: release-apicurio.yml excludes it from the
# archive precisely so one bundle can serve every environment, and the
# promotion writes the target environment's own copy after this step. An
# archive carrying one was built wrong, and if the later write were ever
# skipped the target would serve another environment's API origin -- which
# fails as a CORS error a long way from this cause.
printf '%s\n' "$listing" | grep -Eq '^(\./)?config\.json$' \
  && die "${archive} contains config.json, which is environment state and must not be in a promotable bundle. The archive was built without --exclude; re-cut the build tag."

# A fresh directory, so a stale tree from an earlier attempt cannot contribute
# files to the hash -- which would make a wrong bundle verify.
[ -e "$dest" ] && die "${dest} already exists; refusing to extract over it. Remove it first, or name a path that does not exist."
mkdir -p "$dest"

# Cleaned up on any failure below. A half-extracted or unverified tree left on
# disk is the thing a later `aws s3 sync` would happily publish.
trap 'rm -rf "$dest"' EXIT

tar -xzf "$archive" -C "$dest" \
  || die "extracting ${archive} into ${dest} failed."

actual="$("$BUNDLE_HASH" "$dest")" \
  || die "could not hash the extracted tree at ${dest}."

if [ "$actual" != "$expected" ]; then
  die "$(printf '%s\n' \
    "the restored bundle is not the one the release recorded." \
    "  expected ${expected}" \
    "  actual   ${actual}" \
    "  The archive's contents have changed since the build tag was cut, or the" \
    "  wrong archive was downloaded. ${dest} has been removed; nothing is" \
    "  publishable. Do NOT sync a bundle this check rejected.")"
fi

# Verified, so the tree is allowed to survive.
trap - EXIT

echo "$actual"
