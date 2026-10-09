#!/usr/bin/env bash
# Validate this stack's declared version and print the build tag it implies.
#
# The version is DECLARED, not derived (spec decision 1): semver on a
# capability is a claim about whether consumers break, and only a human who
# understands the change can make that claim. This script's whole job is to
# stop the three mistakes that claim invites -- a malformed number, a number
# that has already shipped, and a number that goes backwards.
#
# Usage: check-version.sh <path-to-VERSION> [--against <ref>]
# Prints: <COMPONENT>/v<version> on success. Diagnostic on stderr, exit 1 otherwise.

set -euo pipefail

version_file=""
against=""
while [ $# -gt 0 ]; do
  case "$1" in
    --against) against="${2:?--against needs a ref}"; shift 2 ;;
    -*) echo "error: unknown option '$1'" >&2; exit 1 ;;
    *) version_file="$1"; shift ;;
  esac
done
: "${version_file:?usage: check-version.sh <path-to-VERSION> [--against <ref>]}"
# REQUIRED, never defaulted: every caller already sets it, and a default let a
# stack that forgot it cut a tag named after another stack (UIFPE-730). A
# per-stack default is also why three copies of this script drifted.
: "${COMPONENT:?COMPONENT must name the release component, e.g. COMPONENT=echo}"
component="$COMPONENT"

if [ ! -f "$version_file" ]; then
  echo "error: ${version_file} does not exist" >&2
  exit 1
fi

# tr, not $(cat), so a stray CR from a Windows editor cannot produce a tag
# with an invisible character in it.
version="$(tr -d '[:space:]' < "$version_file")"

if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "error: VERSION must be bare MAJOR.MINOR.PATCH, got '${version}'" >&2
  exit 1
fi

tag="${component}/v${version}"

if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null 2>&1; then
  echo "error: tag ${tag} already exists -- bump VERSION to release again" >&2
  exit 1
fi

# A DECLARED VERSION IS NOT A LOCK, and losing that race is SILENT. The check
# above refuses a version whose TAG exists -- and while two pull requests are
# both open, it does not. The first to merge cuts the tag. The second merge
# then writes the SAME BYTES to VERSION, so git merges it with no conflict,
# the release workflow's `paths: VERSION` filter sees no change, it never
# fires, and that branch's code lands on the base with no build tag naming it.
# Measured 2026-09-27: echo/v0.7.0 was cut on #613 and does not contain #614.
#
# --against <ref> asks the same question before the tag exists: does the branch
# we would merge INTO already declare this number? It does whenever a branch
# was rebased onto a base that has since claimed it, and -- the case above --
# whenever the bump merged but the release run has not reached its tag push.
#
# What this does NOT close: the answer is only as fresh as the run, and a base
# branch moving does not re-trigger a pull_request check. Requiring a branch to
# be up to date before merging closes that, and it is a repository setting
# rather than this script's job.
if [ -n "$against" ]; then
  if ! git rev-parse -q --verify "${against}^{commit}" >/dev/null 2>&1; then
    echo "error: --against '${against}' does not resolve to a commit. Refusing" \
         "rather than skipping: a guard that passes when it cannot look is" \
         "worse than no guard." >&2
    exit 1
  fi
  # --full-name so an absolute or ./-relative argument still names the path
  # git wants. Absent on that ref is fine -- it declares nothing.
  rel="$(git ls-files --full-name -- "$version_file" 2>/dev/null | head -1 || true)"
  [ -n "$rel" ] || rel="$version_file"
  theirs="$(git show "${against}:${rel}" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ "$theirs" = "$version" ]; then
    echo "error: ${against} already declares VERSION ${version}. Merging would" \
         "change no bytes in ${rel}, so the release workflow would not fire and" \
         "${tag} would not name this commit. Bump to the next version." >&2
    exit 1
  fi
fi

# -k1,1n -k2,2n -k3,3n sorts the three fields NUMERICALLY. A plain sort is
# lexical, which puts 0.9.0 above 0.10.0 and would refuse every release after
# the ninth minor.
latest="$(
  git tag --list "${component}/v*" \
    | sed "s|^${component}/v||" \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -t. -k1,1n -k2,2n -k3,3n \
    | tail -1 || true
)"

if [ -n "$latest" ]; then
  highest="$(printf '%s\n%s\n' "$latest" "$version" \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
  # The equality check is separate because a tie leaves `highest` equal to
  # `version`, which the inequality below would wave through.
  if [ "$version" = "$latest" ] || [ "$highest" != "$version" ]; then
    echo "error: VERSION ${version} is not greater than the latest release ${latest}" >&2
    exit 1
  fi
fi

printf '%s\n' "$tag"
