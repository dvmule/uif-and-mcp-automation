#!/usr/bin/env bash
#
# The single definition of the Smart Commit convention.
#
# Called by three things, so the rule is stated once and cannot drift:
#   .githooks/commit-msg              refuses the commit as it is written
#   .githooks/pre-push                refuses the push
#   .github/workflows/smart-commit.yml   fails the pull request
#
# Every non-merge commit must carry:
#   UIFPE-<n>          the issue key, so the commit attaches to the work
#   #comment <text>    a description, posted onto the Jira issue
#
# A #<transition> token is optional per commit — status changes belong at
# lifecycle points, and Jira rejects a transition that is not legal from the
# issue's current status. Unknown tokens warn rather than fail, because Jira
# discards a typo'd token silently.
#
# Merge commits are exempt: their message is generated, not written.
#
# History imported from another repository is exempt too, but only when it is
# recorded. Those commits predate this convention and were authored under
# another repository's rules; rewriting them to add keys would destroy the
# history the import was performed to preserve. Record the tip of each imported
# history in .githooks/import-boundaries and its ancestors are excluded. Nothing
# authored in this repository is ever excluded by that file.
#
# Usage:
#   lint-commits.sh --message <file>      lint one message file (commit-msg)
#   lint-commits.sh --range <a>..<b>      lint every non-merge commit in a range
#
# Exit 0 clean, 1 on any failure.

set -uo pipefail

MIN_CHARS=20
MIN_WORDS=4
KNOWN="comment in-progress blocked code-review ready-for-qa qa-in-progress ready-for-uat uat-in-progress ready-for-build done canceled to-do time close"
DOC="capabilities/delivery/jira-github-integration.md"
BOUNDARIES="${BOUNDARIES:-.githooks/import-boundaries}"

BAD=0

emit_error() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::error::$*"
  else
    echo "  REJECTED  $*" >&2
  fi
  BAD=$((BAD + 1))
}

emit_warning() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning::$*"
  else
    echo "  warning   $*" >&2
  fi
}

# Reproduce what Jira sees. Git strips its own template commentary ("# Please
# enter the commit message", "#\tmodified: ...") — every such line is a hash
# followed by whitespace, whereas a `#comment` token is not, so this drops the
# commentary without touching the tokens.
strip_git_commentary() {
  grep -v '^#[[:space:]]' || true
}

# Jira reads #comment text up to the next command token or end of line.
extract_comment() {
  grep -oE '#comment[[:space:]]+.*' | head -1 \
    | sed -E 's/^#comment[[:space:]]+//' \
    | sed -E 's/[[:space:]]+#[a-zA-Z][a-zA-Z-]*.*$//' \
    | sed -E 's/[[:space:]]+$//'
}

# Emit `--not <sha>` for each recorded import boundary, so `git log` drops that
# commit and its ancestors. A SHA that is not in the repository warns and is
# skipped, so a stale line degrades to a no-op instead of widening the
# exemption. Output is whitespace-separated and contains only SHAs and flags.
import_exclusions() {
  local sha rest
  [ -r "$BOUNDARIES" ] || return 0
  while read -r sha rest; do
    case "$sha" in '' | \#*) continue ;; esac
    if git cat-file -e "${sha}^{commit}" 2>/dev/null; then
      printf '%s %s ' "--not" "$sha"
    else
      emit_warning "import boundary '$sha' is not a commit in this repository; ignoring it. ($BOUNDARIES)"
    fi
  done < "$BOUNDARIES"
}

lint_message() {
  local label="$1" msg="$2" subject comment chars words tok

  subject=$(printf '%s\n' "$msg" | head -1 | cut -c1-72)

  # A merge commit's message is generated, not written, and carries no tokens.
  case "$subject" in
    Merge\ *) return 0 ;;
    fixup!*|squash!*) return 0 ;;   # collapses into its target on rebase
  esac

  if ! printf '%s' "$msg" | grep -Eqi 'UIFPE-[0-9]+'; then
    emit_error "$label — no UIFPE-<n> issue key: \"$subject\". The commit cannot be traced to the work it implements."
    return 1
  fi

  if ! printf '%s' "$msg" | grep -Eq '#comment[[:space:]]+[^[:space:]]'; then
    emit_error "$label — issue key but no '#comment <text>': \"$subject\". A bare key posts nothing on the issue."
    return 1
  fi

  comment=$(printf '%s' "$msg" | extract_comment)
  chars=${#comment}
  words=$(printf '%s' "$comment" | wc -w | tr -d ' ')

  if [ "$chars" -lt "$MIN_CHARS" ] || [ "$words" -lt "$MIN_WORDS" ]; then
    emit_error "$label — '#comment $comment' is not a description ($chars chars, $words words; minimum $MIN_CHARS and $MIN_WORDS). It is read by people who will not open the diff."
    return 1
  fi

  for tok in $(printf '%s' "$msg" | grep -oE '#[a-zA-Z][a-zA-Z-]*' | tr -d '#' | tr '[:upper:]' '[:lower:]' | sort -u); do
    case " $KNOWN " in
      *" $tok "*) ;;
      *) emit_warning "$label — unrecognised token '#$tok'. Jira discards an unknown token silently; check it against $DOC." ;;
    esac
  done

  return 0
}

print_how_to_fix() {
  cat >&2 <<'TXT'

Required shape:

  <what changed and why, for the engineer reading git log>

  UIFPE-<n> #comment <one line describing the change, for the Jira issue> [#<transition>]

Keep the issue key first on its line. A line that starts with "#" is stripped
from the message by git, so a leading "#comment" never reaches Jira.

Don't compose it by hand: ask GitHub Copilot or Claude to draft the message
from your staged diff, then check that it says what you actually did. You stay
the commit author; credit the assistant in a Co-Authored-By: trailer.
TXT
  echo "" >&2
  echo "Convention: $DOC" >&2
}

case "${1:---help}" in
  --message)
    file="${2:?--message needs a file}"
    msg=$(strip_git_commentary < "$file")
    lint_message "commit message" "$msg"
    if [ "$BAD" -gt 0 ]; then
      print_how_to_fix
      exit 1
    fi
    ;;

  --range)
    shift
    [ "$#" -gt 0 ] || { echo "--range needs revisions" >&2; exit 2; }
    EXCLUDE=$(import_exclusions)
    # shellcheck disable=SC2086  # deliberate word splitting: only SHAs and flags
    for sha in $(git log --no-merges --format='%H' "$@" $EXCLUDE); do
      lint_message "${sha:0:8}" "$(git log -1 --format='%B' "$sha")"
    done
    if [ "$BAD" -gt 0 ]; then
      echo "" >&2
      echo "$BAD commit(s) above are not Smart Commits." >&2
      print_how_to_fix
      echo "" >&2
      echo "Reword them with:  git rebase -i <the commit before the first bad one>" >&2
      exit 1
    fi
    echo "Smart Commit convention: OK"
    ;;

  *)
    sed -n '3,25p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
    exit 2
    ;;
esac
