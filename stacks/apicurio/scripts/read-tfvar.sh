#!/usr/bin/env bash
# Read one top-level assignment out of a .tfvars file.
#
# Exists so the release workflow pins the registry image and digest to the
# values Terraform will actually apply, rather than to a second copy of them
# declared in the workflow. Two copies of a digest is a drift class, not a
# convenience.
#
# Usage: read-tfvar.sh <tfvars-file> <name>

set -euo pipefail

file="${1:?usage: read-tfvar.sh <tfvars-file> <name>}"
name="${2:?usage: read-tfvar.sh <tfvars-file> <name>}"

if [ ! -f "$file" ]; then
  echo "error: ${file} does not exist" >&2
  exit 1
fi

# Anchored on start-of-line so a name appearing inside a comment or a nested
# block cannot match, and the name is followed by whitespace-then-= so
# `registry_image` cannot return `registry_image_digest`'s value.
value="$(
  grep -E "^[[:space:]]*${name}[[:space:]]*=" "$file" \
    | grep -v '^[[:space:]]*#' \
    | head -1 \
    | sed -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*//" \
    | sed -E 's/^"(.*)"[[:space:]]*$/\1/' \
    | sed -E 's/[[:space:]]+$//' || true
)"

if [ -z "$value" ]; then
  echo "error: ${name} is not set in ${file}" >&2
  exit 1
fi

printf '%s\n' "$value"
