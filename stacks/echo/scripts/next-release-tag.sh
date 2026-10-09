#!/usr/bin/env bash
# Print the next release tag for an environment: release/<env>/<date>.<n>
#
# Usage:
#   next-release-tag.sh <env> [<date>]
#
# <date> defaults to today in UTC. The counter is the highest existing <n> for
# that environment and date, plus one.
#
# WHY THE HIGHEST AND NOT THE COUNT. If release/uat/<date>.2 is deleted, a
# count would hand out .2 again and two different releases would share one
# identity. The tag is evidence; a reused name destroys it.
#
# WHY NUMERIC SORT. Lexically, .10 sorts below .9, so a text sort would hand
# out .10 a second time. `sort -n` on the suffix alone avoids version-sort
# portability differences between BSD and GNU sort.
set -euo pipefail

die() { printf '%s: %s\n' "next-release-tag.sh" "$1" >&2; exit 1; }

env_name="${1:-}"
[ -n "$env_name" ] || die "needs an environment. Usage: next-release-tag.sh <env> [<date>]"

case "$env_name" in
  dev|uat|prod) ;;
  *) die "'${env_name}' is not a known environment. The vocabulary is closed: dev, uat, prod." ;;
esac

date_arg="${2:-$(date -u +%Y-%m-%d)}"
printf '%s' "$date_arg" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
  || die "'${date_arg}' is not a date of the form YYYY-MM-DD."

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git work tree, so existing release tags cannot be read."

prefix="release/${env_name}/${date_arg}."

# `|| true` because a failing grep/sed under `set -e` with pipefail would kill
# the script silently -- the exact failure mode read-manifest.sh was bitten by.
highest="$(
  git tag -l "${prefix}*" \
    | sed -n "s|^${prefix}\([0-9][0-9]*\)$|\1|p" \
    | sort -n \
    | tail -1 \
    || true
)"

if [ -z "$highest" ]; then
  next=1
else
  next=$((highest + 1))
fi

printf '%s%s\n' "$prefix" "$next"
