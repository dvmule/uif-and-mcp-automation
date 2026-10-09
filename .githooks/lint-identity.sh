#!/usr/bin/env bash
#
# The single definition of the commit-identity rule.
#
# Called by three things, so the rule is stated once and cannot drift:
#   .githooks/pre-commit                 refuses to make the commit
#   .githooks/pre-push                   refuses the push
#   .github/workflows/commit-identity.yml   fails the pull request
#
# Jira runs every smart-commit action as the commit author, matching the
# address literally against licensed users. On no match it discards the command
# without logging anything — no comment, no transition, no error. Plus-addressed
# aliases (first.last+x@pearson.com) match nothing.
#
# Usage:
#   lint-identity.sh --email <address>    check one address (pre-commit)
#   lint-identity.sh --range <a>..<b>     check every non-merge commit's author
#
# Exit 0 clean, 1 on any failure.

set -uo pipefail

DOC="capabilities/delivery/jira-github-integration.md"
BAD=0

emit_error() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::error::$*"
  else
    echo "  REJECTED  $*" >&2
  fi
  BAD=$((BAD + 1))
}

check_email() {
  local label="$1" email="$2" lower
  lower=$(printf '%s' "$email" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *+*@pearson.com)
      emit_error "$label — plus-addressed author <$email>. Jira does not strip the +alias, so smart commits from this address are silently discarded."
      ;;
    *@pearson.com) ;;
    *)
      emit_error "$label — <$email> is not a @pearson.com address, so it cannot match a licensed Jira user."
      ;;
  esac
}

print_how_to_fix() {
  cat >&2 <<'TXT'

Set your own identity in this clone:

  git config user.name  "<Your Name>"
  git config user.email <your-exact-Jira-account-email>

Every commit is authored by the engineer who wrote it — never a shared or team
address, never a colleague's, never one invented for an agent. AI-assisted
commits are authored by the engineer who ran the session, with the agent
credited in a Co-Authored-By: trailer.

On a machine more than one engineer uses, remove the fallback so git fails
loudly instead of inheriting whoever configured it last:

  git config --global --unset user.email
  git config --global --unset user.name
  git config --global user.useConfigOnly true
TXT
  echo "" >&2
  echo "Convention: $DOC" >&2
}

case "${1:---help}" in
  --email)
    check_email "commit author" "${2:?--email needs an address}"
    if [ "$BAD" -gt 0 ]; then
      print_how_to_fix
      exit 1
    fi
    ;;

  --range)
    shift
    [ "$#" -gt 0 ] || { echo "--range needs revisions" >&2; exit 2; }
    while IFS='|' read -r sha email; do
      [ -n "$sha" ] || continue
      check_email "${sha:0:8}" "$email"
    done < <(git log --no-merges --format='%H|%ae' "$@")
    if [ "$BAD" -gt 0 ]; then
      echo "" >&2
      echo "$BAD commit(s) above are authored by an address Jira cannot match." >&2
      print_how_to_fix
      echo "" >&2
      echo "Rewrite them with:" >&2
      echo "  git rebase <base> --exec 'git commit --amend --no-edit --reset-author'" >&2
      exit 1
    fi
    echo "Commit identity: OK"
    ;;

  *)
    sed -n '3,20p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
    exit 2
    ;;
esac
