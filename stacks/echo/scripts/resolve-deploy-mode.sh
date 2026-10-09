#!/usr/bin/env bash
# Resolve what a deployment run will do, failing closed.
#
# Usage:
#   resolve-deploy-mode.sh --event <name> --input <value> --var <value> \
#                          --allowed <csv> --default <value>
#
# Prints one token from --allowed on stdout. A dispatch input wins when the
# event is workflow_dispatch; otherwise the repository variable decides.
#
# WHY UNRECOGNISED VALUES COLLAPSE TO THE DEFAULT rather than failing the run:
# these latches are read on every tag push, and a typo in a repository
# variable should not break every push -- it should deploy nothing and say so.
# The value is echoed to stderr so the typo is visible in the log.
#
# Matching is exact against the whole token. Substring matching would read
# "no-apply" as "apply", which is the opposite of what it says.
set -euo pipefail

die() { printf '%s: %s\n' "resolve-deploy-mode.sh" "$1" >&2; exit 1; }

event="" input="" var="" allowed="" default=""

[ "$#" -gt 0 ] || die "no arguments. Usage: --event <name> --input <value> --var <value> --allowed <csv> --default <value>"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --event)   [ "$#" -ge 2 ] || die "--event needs a value";   event="$2";   shift 2 ;;
    --input)   [ "$#" -ge 2 ] || die "--input needs a value";   input="$2";   shift 2 ;;
    --var)     [ "$#" -ge 2 ] || die "--var needs a value";     var="$2";     shift 2 ;;
    --allowed) [ "$#" -ge 2 ] || die "--allowed needs a value"; allowed="$2"; shift 2 ;;
    --default) [ "$#" -ge 2 ] || die "--default needs a value"; default="$2"; shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$allowed" ] || die "--allowed is required"
[ -n "$default" ] || die "--default is required"

# Exact membership test against a comma-separated list, with no glob or
# substring semantics. `set -f` is not needed because nothing is unquoted.
in_allowed() {
  local needle="$1" rest="${allowed}," item
  while [ -n "$rest" ]; do
    item="${rest%%,*}"
    rest="${rest#*,}"
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

in_allowed "$default" || die "--default '${default}' is not one of '${allowed}'"

if [ "$event" = "workflow_dispatch" ]; then
  candidate="$input"
else
  candidate="$var"
fi

if [ -z "$candidate" ]; then
  printf '%s\n' "$default"
  exit 0
fi

if in_allowed "$candidate"; then
  printf '%s\n' "$candidate"
else
  printf "%s: '%s' is not one of '%s'; using '%s'\n" \
    "resolve-deploy-mode.sh" "$candidate" "$allowed" "$default" >&2
  printf '%s\n' "$default"
fi
