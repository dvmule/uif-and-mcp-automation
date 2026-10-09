#!/usr/bin/env bash
#
# The single definition of what makes a pull request reviewable.
#
# Called by .github/workflows/pull-request.yml, and runnable by hand before you
# open the PR:
#
#   .githooks/lint-pr.sh --title "UIFPE-39 · Product Data API" --body-file pr.md
#
# There is no git hook for this one. A pull request is not a local object, so
# unlike the commit rules there is nothing to refuse at commit or push time —
# the check runs on the PR itself and reruns on every edit to it.
#
# A PR is the only artifact in the chain a reviewer reads before the diff, and
# the only one the PM and QE Lead read at all. Three things are required:
#
#   UIFPE-<n> in the title     links the PR to the work, and is what a reader
#                              searching Jira for "what shipped" finds
#   a descriptive title        "Product Data API", not "updates"
#   a body with a summary      what changed and why
#   and a verification section how you know it works
#
# The floors below are floors, not quality judgements — the same split as the
# commit gate. CI checks that a summary and a verification exist and are prose;
# whether the reasoning is any good is the reviewer's call. See
# capabilities/delivery/guidelines.md § 2.
#
# Exit 0 clean, 1 on any failure.

set -uo pipefail

TITLE_MIN_CHARS=15
TITLE_MIN_WORDS=3
SUMMARY_MIN_CHARS=40
SUMMARY_MIN_WORDS=8
VERIFY_MIN_CHARS=20
VERIFY_MIN_WORDS=4

# Words that describe nothing on their own. A title made only of these clears a
# length floor while telling a reviewer no more than the branch name does.
FILLER="wip update updates updated fix fixes fixed change changes changed
misc various minor small quick cleanup cleaned tweak tweaks refactor
stuff things file files code work done new some more few and the a an of to
for in on with"

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

emit_warning() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning::$*"
  else
    echo "  warning   $*" >&2
  fi
}

# Measure what a reader actually reads: no markdown syntax, no HTML comments
# (the template's own guidance lives in those, and must not count as content),
# no checklist boxes, no bare URLs.
readable_text() {
  perl -0pe 's/<!--.*?-->//gs' \
    | sed -E 's/https?:\/\/[^ )]*//g' \
    | sed -E 's/^[[:space:]]*[-*+][[:space:]]*\[[ xX]\][[:space:]]*//' \
    | sed -E 's/^[[:space:]]*#+[[:space:]]*//' \
    | sed -E 's/[`*_>|#-]+/ /g'
}

count_words() { printf '%s' "$1" | wc -w | tr -d ' '; }

# Everything under a heading whose text matches $2, up to the next heading.
# Case-insensitive via tolower(), not IGNORECASE — that is a gawk extension, and
# both BSD awk and the runner's mawk ignore it silently, which matches nothing.
section_body() {
  local body="$1" pattern="$2"
  printf '%s\n' "$body" | awk -v pat="$pattern" '
    BEGIN { want = 0 }
    /^[[:space:]]*#+[[:space:]]/ {
      want = (tolower($0) ~ pat) ? 1 : 0
      next
    }
    want { print }
  '
}

lint_title() {
  local title="$1" rest chars words substantive w lower

  if ! printf '%s' "$title" | grep -Eqi 'UIFPE-[0-9]+'; then
    emit_error "Title has no UIFPE-<n> issue key: \"$title\". Nobody reading Jira can find what shipped for the issue."
  fi

  # Judge the part that is supposed to describe the change, not the key.
  rest=$(printf '%s' "$title" \
    | sed -E 's/UIFPE-[0-9]+//gi' \
    | sed -E 's/^[[:space:]]*[·:|/,-]+[[:space:]]*//' \
    | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')

  chars=${#rest}
  words=$(count_words "$rest")

  if [ "$chars" -lt "$TITLE_MIN_CHARS" ] || [ "$words" -lt "$TITLE_MIN_WORDS" ]; then
    emit_error "Title says nothing beyond the issue key: \"$rest\" ($chars chars, $words words; minimum $TITLE_MIN_CHARS and $TITLE_MIN_WORDS)."
    return
  fi

  substantive=0
  for w in $rest; do
    lower=$(printf '%s' "$w" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]-')
    [ -n "$lower" ] || continue
    case " $(printf '%s' "$FILLER" | tr '\n' ' ') " in
      *" $lower "*) ;;
      *) substantive=$((substantive + 1)) ;;
    esac
  done

  if [ "$substantive" -lt 2 ]; then
    emit_error "Title is filler: \"$rest\". Name the thing you changed — \"Product Data API\", not \"updates\"."
  fi
}

lint_body() {
  local body="$1" summary verify chars words

  if [ -z "$(printf '%s' "$body" | readable_text | tr -d '[:space:]')" ]; then
    emit_error "The pull request body is empty. It is the only description of the change a reviewer reads before the diff, and the only one the PM and QE Lead read at all."
    return
  fi

  summary=$(section_body "$body" '(summary|what changed|context|why)')
  if [ -z "$summary" ]; then
    emit_error "No '## Summary' section. Say what changed and why, in prose."
  else
    summary=$(printf '%s' "$summary" | readable_text)
    chars=$(printf '%s' "$summary" | tr -d '[:space:]' | wc -c | tr -d ' ')
    words=$(count_words "$summary")
    if [ "$chars" -lt "$SUMMARY_MIN_CHARS" ] || [ "$words" -lt "$SUMMARY_MIN_WORDS" ]; then
      emit_error "The Summary section does not describe the change ($chars chars, $words words; minimum $SUMMARY_MIN_CHARS and $SUMMARY_MIN_WORDS). A reviewer opens the diff already knowing what you were trying to do, or not at all."
    fi
  fi

  verify=$(section_body "$body" '(verification|verified|test plan|testing|how i tested|evidence)')
  if [ -z "$verify" ]; then
    emit_error "No '## Verification' section. State how you know the change works — the command you ran, the CI job, the environment you hit."
  else
    verify=$(printf '%s' "$verify" | readable_text)
    chars=$(printf '%s' "$verify" | tr -d '[:space:]' | wc -c | tr -d ' ')
    words=$(count_words "$verify")
    if [ "$chars" -lt "$VERIFY_MIN_CHARS" ] || [ "$words" -lt "$VERIFY_MIN_WORDS" ]; then
      emit_error "The Verification section gives no evidence ($chars chars, $words words; minimum $VERIFY_MIN_CHARS and $VERIFY_MIN_WORDS). An unticked checklist is not evidence — name the command, job or environment."
    fi
  fi
}

# Warned, not failed: renaming a branch after the PR is open means closing and
# reopening it, and the PR title carries the key regardless. But
# .github/workflows/jira-transition.yml reads the key from the branch name, so a
# branch without one silently skips the transition.
lint_branch() {
  local branch="$1"
  [ -n "$branch" ] || return 0
  if ! printf '%s' "$branch" | grep -Eqi 'UIFPE-[0-9]+'; then
    emit_warning "Branch '$branch' has no UIFPE-<n> key. Convention is feature/UIFPE-<n>-<short-description>, and jira-transition.yml reads the key from the branch name."
  fi
}

print_how_to_fix() {
  cat >&2 <<'TXT'

Required shape:

  Title:  UIFPE-<n> · <what this delivers>

  Body:   ## Summary
          <what changed and why — the reasoning a reviewer needs before the diff>

          ## Verification
          <how you know it works: the command, the CI job, the environment>

The template at .github/PULL_REQUEST_TEMPLATE.md already has the headings; fill
them in. Its guidance lives in HTML comments and does not count as content.

Don't compose it by hand: ask GitHub Copilot or Claude to draft the summary from
the branch diff, then check that it says what you actually did.

Edit the title and body on the pull request itself — this check reruns on every
edit, with no new commit needed.
TXT
  echo "" >&2
  echo "Convention: $DOC" >&2
}

TITLE="" BODY="" BRANCH=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --title)     TITLE="${2:?--title needs a value}"; shift 2 ;;
    --body-file) BODY=$(cat "${2:?--body-file needs a file}"); shift 2 ;;
    --branch)    BRANCH="${2:-}"; shift 2 ;;
    *)
      sed -n '3,30p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
      exit 2 ;;
  esac
done

[ -n "$TITLE" ] || { echo "--title is required" >&2; exit 2; }

lint_title "$TITLE"
lint_body "$BODY"
lint_branch "$BRANCH"

if [ "$BAD" -gt 0 ]; then
  echo "" >&2
  echo "$BAD problem(s) above: this pull request is not reviewable as written." >&2
  print_how_to_fix
  exit 1
fi

echo "Pull request is reviewable: OK"
