#!/usr/bin/env bash
# Verify one downloaded artifact against what the release recorded, and refuse
# unless they match.
#
# The promotion path never rebuilds: it takes the artifacts the build tag names
# and publishes those exact bytes. So the only question worth asking here is
# whether what came out of S3 is still what the manifest identified. Copying is
# `aws s3 cp`, a one-liner that needs no script; deciding whether the result is
# the release is this.
#
# THREE ARTIFACT SHAPES, THREE CHECKS. Echo's artifacts are not one kind of
# thing, and using one check for all three would either be wrong or need
# extraction it does not need:
#
#   glue_scripts  a single .py file. Its identity IS its file hash, so hash the
#                 file and compare to sha256. Nothing to extract.
#   lambdas       a deterministic zip. zip-artifact.sh fixes the timestamp,
#                 mode and entry order, so the archive's bytes and its tree are
#                 one-to-one -- comparing code_sha256 is exact and costs no
#                 extraction. sha256 (the tree hash) is the identity; this
#                 proves the stored archive still carries that tree.
#   layers        same as lambdas.
#   ui_bundle     a tar.gz whose identity is the hash of the tree INSIDE it,
#                 with config.json excluded. Comparing the tarball's own bytes
#                 would prove a byte-copy happened; it would not prove the
#                 bundle inside is the bundle the release identified. So this
#                 one extracts and re-hashes -- the same artifact-hash.sh call
#                 release-echo.yml made, so the two cannot disagree about what
#                 the bundle's identity is.
#
# Usage:
#   restore-artifacts.sh --class glue_scripts --file <path> --sha256 <hex>
#   restore-artifacts.sh --class lambdas|layers --file <zip> --code-sha256 <b64>
#   restore-artifacts.sh --class ui_bundle --file <tar.gz> --dest <dir> --sha256 <hex>
#
# Prints the verified value. Exits non-zero, saying which check failed, on
# anything else. For ui_bundle the destination is left populated ONLY on
# success, so a failed restore cannot be published by a later step that forgot
# to check.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ARTIFACT_HASH="${ARTIFACT_HASH:-${HERE}/artifact-hash.sh}"

die() { echo "error: $*" >&2; exit 1; }

class="" file="" dest="" sha256="" code_sha256=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --class)       [ "$#" -ge 2 ] || die "--class needs a value";       class="$2";       shift 2 ;;
    --file)        [ "$#" -ge 2 ] || die "--file needs a value";        file="$2";        shift 2 ;;
    --dest)        [ "$#" -ge 2 ] || die "--dest needs a value";        dest="$2";        shift 2 ;;
    --sha256)      [ "$#" -ge 2 ] || die "--sha256 needs a value";      sha256="$2";      shift 2 ;;
    --code-sha256) [ "$#" -ge 2 ] || die "--code-sha256 needs a value"; code_sha256="$2"; shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$class" ] || die "--class is required (glue_scripts | lambdas | layers | ui_bundle)"
[ -n "$file" ]  || die "--file is required"
[ -f "$file" ]  || die "${file} does not exist. Download it from the release-artifacts bucket first (aws s3 cp), then verify."
[ -x "$ARTIFACT_HASH" ] || die "artifact-hash.sh not found or not executable at ${ARTIFACT_HASH}"

# Shape-check the pins BEFORE doing any work, so a malformed value fails in a
# second rather than after a tree is on disk.
if [ -n "$sha256" ]; then
  printf '%s' "$sha256" | grep -Eq '^[0-9a-f]{64}$' \
    || die "--sha256 must be 64 lowercase hex characters with no prefix, got '${sha256}'"
fi
if [ -n "$code_sha256" ]; then
  printf '%s' "$code_sha256" | grep -Eq '^[A-Za-z0-9+/]{43}=$' \
    || die "--code-sha256 must be base64 of a sha256 digest (43 chars + '='), got '${code_sha256}'. That is the form the manifest stores and Terraform expects."
fi

b64_of_file() {
  # Portable: openssl is present on every runner and on git-bash. `base64 -w0`
  # is a GNU extension and wraps on macOS, which would make the comparison
  # fail on a laptop for a reason that has nothing to do with the artifact.
  openssl dgst -sha256 -binary "$1" | openssl base64 -A
}

mismatch() {
  die "$(printf '%s\n' \
    "the restored ${class} artifact is not the one the release recorded." \
    "  expected ${1}" \
    "  actual   ${2}" \
    "  The object's contents have changed since the build tag was cut, or the" \
    "  wrong key was downloaded. Do NOT deploy an artifact this check rejected.")"
}

case "$class" in
  glue_scripts)
    [ -n "$sha256" ] || die "--sha256 is required for glue_scripts"
    [ -z "$code_sha256" ] || die "--code-sha256 is meaningless for a single file: its identity IS its file hash. Pass --sha256 only."
    actual="$("$ARTIFACT_HASH" "$file")"
    [ "$actual" = "$sha256" ] || mismatch "$sha256" "$actual"
    printf '%s\n' "$actual"
    ;;

  lambdas|layers)
    [ -n "$code_sha256" ] || die "--code-sha256 is required for ${class} -- it is the hash of the archive's own bytes, and what this verifies."
    actual="$(b64_of_file "$file")"
    [ "$actual" = "$code_sha256" ] || mismatch "$code_sha256" "$actual"
    printf '%s\n' "$actual"
    ;;

  ui_bundle)
    [ -n "$sha256" ] || die "--sha256 is required for ui_bundle"
    [ -n "$dest" ]   || die "--dest is required for ui_bundle: the identity is the hash of the tree inside the archive, so it has to be extracted to be checked."

    listing="$(tar -tzf "$file" 2>/dev/null)" \
      || die "${file} is not a readable gzipped tar. A truncated download looks exactly like this; check the object's size against the bucket."
    [ -n "$listing" ] || die "${file} is an empty archive, so there is no bundle to restore."

    # EXTRACTION SAFETY, checked against the listing before anything is
    # written. These archives are ones we produced, so this is defence in depth
    # rather than a live threat -- but an absolute path or a `..` component in a
    # tar entry writes outside the destination.
    while IFS= read -r entry; do
      case "$entry" in
        /*) die "${file} contains an absolute path (${entry}); refusing to extract." ;;
        # Every real traversal form, and no more: a bare `..`, a leading
        # `../`, an interior `/../`, or a trailing `/..`. Matching `*..*`
        # would also reject an innocent name like `chunk..map`.
        ..|../*|*/../*|*/..) die "${file} contains a parent-directory reference (${entry}); refusing to extract." ;;
      esac
    done <<EOF
$listing
EOF

    # config.json is environment state: release-echo.yml excludes it from the
    # archive precisely so one bundle can serve every environment, and the
    # publish writes the target environment's own copy after this step. An
    # archive carrying one was built wrong, and if the later write were ever
    # skipped the target would serve another environment's API origin -- which
    # surfaces as a CORS error a long way from this cause.
    if printf '%s\n' "$listing" | grep -Eq '^(\./)?config\.json$'; then
      die "${file} contains config.json, which is environment state and must not be in a promotable bundle. The archive was built without the exclusion; re-cut the build tag."
    fi

    # A fresh directory, so a stale tree from an earlier attempt cannot
    # contribute files to the hash -- which would make a wrong bundle verify.
    [ -e "$dest" ] && die "${dest} already exists; refusing to extract over it. Remove it first, or name a path that does not exist."
    mkdir -p "$dest"

    # Cleaned up on any failure below. A half-extracted or unverified tree left
    # on disk is the thing a later `aws s3 sync` would happily publish.
    trap 'rm -rf "$dest"' EXIT

    tar -xzf "$file" -C "$dest" \
      || die "extracting ${file} into ${dest} failed."

    actual="$("$ARTIFACT_HASH" "$dest" --exclude config.json)" \
      || die "could not hash the extracted tree at ${dest}."

    [ "$actual" = "$sha256" ] || mismatch "$sha256" "$actual"

    # Verified, so the tree is allowed to survive.
    trap - EXIT
    printf '%s\n' "$actual"
    ;;

  *) die "unknown --class '${class}'. Known: glue_scripts lambdas layers ui_bundle" ;;
esac
