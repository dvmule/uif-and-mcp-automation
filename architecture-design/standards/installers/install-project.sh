#!/usr/bin/env bash
# Install the UIF Claude naming standards into a repository's .claude/ (PROJECT scope).
#
# install.sh installs to ~/.claude — per machine, per person, invisible to the team and
# invisible to git. This variant installs the same package into the repository itself, so
# the rules that govern a repo travel with it: one `git pull` and every contributor has
# them, and a change to them is a reviewable diff rather than an instruction in a README
# that some people ran and others did not.
#
#   ./installers/install-project.sh                      install into the repo root's .claude/
#   ./installers/install-project.sh --wire-hook          also wire the PreToolUse guard
#   ./installers/install-project.sh --from-ref origin/develop
#                                                        install the package as it exists on
#                                                        another git ref, not this worktree
#   ./installers/install-project.sh --check              report what is installed, write nothing
#   ./installers/install-project.sh --verify             install, then prove the hook allows and denies
#
# --from-ref exists for a real case, not for symmetry: a long-lived feature branch can carry
# an older copy of this package than develop does, and installing 1.x rules gives guidance
# that is wrong rather than merely incomplete (see ../Setup-Guide.md, "Upgrading from 1.x").
# --from-ref lets you install the current standard while working on a branch that predates it.
#
# Re-running is the update path, exactly as with install.sh. rules/naming/ is REPLACED, so a
# file deleted upstream disappears here too.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FROM_REF=""
WIRE_HOOK=0
CHECK_ONLY=0
VERIFY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --wire-hook) WIRE_HOOK=1 ;;
    --check)     CHECK_ONLY=1 ;;
    --verify)    VERIFY=1 ;;
    --from-ref)  shift; FROM_REF="${1:-}"; [ -n "$FROM_REF" ] || { echo "--from-ref needs a git ref" >&2; exit 2; } ;;
    -h|--help)   sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# The project root is the git toplevel, not $PWD — the script must behave the same whether
# it is run from the repo root or from installers/. CLAUDE_PROJECT_DIR wins when set, so a
# session's own notion of the project is never contradicted.
REPO="$(git -C "$SRC" rev-parse --show-toplevel)"
ROOT="${CLAUDE_PROJECT_DIR:-$REPO}"

# Every `git show`/`ls-tree` below runs anchored at REPO, and PKG is this package's path
# relative to it. `git -C <subdir> show <ref>:<path>` resolves <path> against the CURRENT
# directory, not the repo root, so running from installers/ silently looked for
# <pkg>/<pkg>/rules/naming and found nothing — an empty install with a zero exit code.
PKG="${SRC#"$REPO"/}"
DEST="${ROOT}/.claude"
RULES_DEST="${DEST}/rules/naming"
HOOKS_DEST="${DEST}/hooks"
SETTINGS="${DEST}/settings.json"

PYBIN="python3"
command -v python3 >/dev/null 2>&1 || PYBIN="python"
command -v "${PYBIN}" >/dev/null 2>&1 || { echo "No python interpreter on PATH." >&2; exit 1; }

# ── source: this worktree, or a git ref ──────────────────────────────────────────────────
# Reading from a ref goes through `git show` rather than a checkout, so the worktree is
# never disturbed — the whole point is to install a version you are NOT currently on.
STAGE=""
if [ -n "$FROM_REF" ]; then
  git -C "$REPO" rev-parse --verify --quiet "$FROM_REF" >/dev/null \
    || { echo "Not a valid git ref: $FROM_REF" >&2; exit 1; }
  STAGE="$(mktemp -d)"
  trap 'rm -rf "$STAGE"' EXIT
  mkdir -p "$STAGE/rules/naming" "$STAGE/hooks"
  git -C "$REPO" show "${FROM_REF}:${PKG}/VERSION" > "$STAGE/VERSION"
  # `for` over a command substitution, not `ls-tree | while read`: the pipeline puts the
  # loop in a subshell, and a write failure there is invisible to `set -e` in the parent —
  # which is exactly how this staged an empty rules/ directory once. Rule filenames are
  # ASCII and space-free by convention, so word-splitting is safe here.
  RULE_FILES="$(git -C "$REPO" ls-tree --name-only "${FROM_REF}:${PKG}/rules/naming")"
  [ -n "$RULE_FILES" ] || { echo "No rule files at ${FROM_REF}:${PKG}/rules/naming" >&2; exit 1; }
  for f in $RULE_FILES; do
    git -C "$REPO" show "${FROM_REF}:${PKG}/rules/naming/${f}" > "$STAGE/rules/naming/${f}"
  done
  git -C "$REPO" show "${FROM_REF}:${PKG}/hooks/validate_naming.py" > "$STAGE/hooks/validate_naming.py"
  SRC_EFF="$STAGE"
  ORIGIN="${FROM_REF} ($(git -C "$REPO" rev-parse --short "$FROM_REF"))"
else
  SRC_EFF="$SRC"
  ORIGIN="worktree ($(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown))"
fi

VERSION="$(tr -d ' \t\r\n' < "${SRC_EFF}/VERSION")"

if [ "$CHECK_ONLY" = "1" ]; then
  echo "project root : ${ROOT}"
  echo "package      : ${VERSION} from ${ORIGIN}"
  # Report the FILES, then the stamp — never the stamp alone. A hand-made or partial install
  # has rules on disk and no .installed-version, and "nothing installed" would be a false
  # report about a guard that is actually live.
  N_RULES=0
  [ -d "$RULES_DEST" ] && N_RULES="$(find "$RULES_DEST" -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "rules        : ${N_RULES} rule file(s) at ${RULES_DEST}"
  if [ -f "${HOOKS_DEST}/validate_naming.py" ]; then
    echo "hook file    : present$([ -x "${HOOKS_DEST}/validate_naming.py" ] || echo ' (NOT executable)')"
  else
    echo "hook file    : absent"
  fi
  if [ -f "${RULES_DEST}/.installed-version" ]; then
    echo "stamp        :"; sed 's/^/    /' "${RULES_DEST}/.installed-version"
  elif [ "$N_RULES" != "0" ]; then
    echo "stamp        : MISSING — rules are installed but their version is unrecorded, so"
    echo "               the 1.x-vs-2.x check in ../Setup-Guide.md cannot be made. Re-run"
    echo "               the installer to stamp them."
  else
    echo "stamp        : none (nothing installed)"
  fi
  if [ -f "$SETTINGS" ]; then
    "$PYBIN" - "$SETTINGS" <<'PY'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception as exc:
    sys.exit("settings.json is not valid JSON (%s) — ALL settings from this file are ignored" % exc)
cmds = [h.get("command", "") for e in s.get("hooks", {}).get("PreToolUse", []) for h in e.get("hooks", [])]
hit = [c for c in cmds if "validate_naming.py" in c]
print("hook         : " + (hit[0] if hit else "NOT wired in settings.json"))
PY
  else
    echo "hook         : no .claude/settings.json"
  fi
  exit 0
fi

# ── install ──────────────────────────────────────────────────────────────────────────────
rm -rf "${RULES_DEST}"
mkdir -p "${RULES_DEST}" "${HOOKS_DEST}"
cp -R "${SRC_EFF}/rules/naming/." "${RULES_DEST}/"
cp "${SRC_EFF}/hooks/validate_naming.py" "${HOOKS_DEST}/validate_naming.py"
chmod +x "${HOOKS_DEST}/validate_naming.py"

# The hook's denial messages point readers at ~/.claude/rules/naming/, which is the wrong
# path once the rules live in the repo — and a denial that misdirects the person reading it
# is worse than a terse one. Mechanical substitution, not a policy change: the shipped
# package stays authoritative about WHAT is denied.
sed 's|~/\.claude/rules/naming|.claude/rules/naming|g' \
  "${HOOKS_DEST}/validate_naming.py" > "${HOOKS_DEST}/.validate_naming.py.tmp"
mv "${HOOKS_DEST}/.validate_naming.py.tmp" "${HOOKS_DEST}/validate_naming.py"
chmod +x "${HOOKS_DEST}/validate_naming.py"

printf 'version=%s\ncommit=%s\ninstalled=%s\nscope=project\nsource=%s\n' \
  "${VERSION}" "$(echo "$ORIGIN" | sed 's/.*(\(.*\))/\1/')" "$(date +%Y-%m-%d)" "${ORIGIN}" \
  > "${RULES_DEST}/.installed-version"

echo "UIF naming rules ${VERSION} installed at PROJECT scope"
echo "  from  : ${ORIGIN}"
echo "  rules : ${RULES_DEST}"
echo "  hook  : ${HOOKS_DEST}/validate_naming.py"

# ── consistency check: the desync that has bitten this repo once already ─────────────────
# 00-core.md sanctions the {stack} tokens; the hook enforces them from its own hardcoded
# STACKS set; stacks/ is where the directories actually are. Three lists that must agree and
# have no mechanism keeping them in agreement. When apicurio was added to the rule file by
# owner decision (2026-09-08, ADR-0012), STACKS was not updated with it, so every new
# uif-apicurio-* name was denied by a guard enforcing a vocabulary the standard had already
# widened. Nothing failed loudly; names were simply refused. This prints that, cheaply.
SLOT="$(sed -n 's/^| *`stack` *| *`\([^`]*\)`.*/\1/p' "${RULES_DEST}/00-core.md" | tr -d ' ' | tr ',' ' ')"
HOOK_SET="$(sed -n 's/^STACKS *= *{\(.*\)}.*/\1/p' "${HOOKS_DEST}/validate_naming.py" | tr -d ' "' | tr ',' ' ')"
DIRS=""
[ -d "${ROOT}/stacks" ] && DIRS="$(cd "${ROOT}/stacks" && ls -d */ 2>/dev/null | tr -d '/' | tr '\n' ' ')"

contains() { case " $1 " in *" $2 "*) return 0 ;; *) return 1 ;; esac; }
WARNED=0
for s in $SLOT; do
  contains "$HOOK_SET" "$s" || { echo "  !! '$s' is a sanctioned stack in 00-core.md but absent from the hook's STACKS — every new uif-${s}-* name will be DENIED"; WARNED=1; }
done
for s in $HOOK_SET; do
  contains "$SLOT" "$s" || { echo "  !! the hook enforces '$s' as a stack but 00-core.md does not list it"; WARNED=1; }
done
for d in $DIRS; do
  contains "$SLOT" "$d" || { echo "  ·  stacks/$d exists but '$d' is not a {stack} token — deliberate per 00-core.md (a directory does not earn a token), noted not warned"; }
done
[ "$WARNED" = "1" ] && echo "  Fix these in architecture-design/standards, not in .claude/ — the next install overwrites .claude/."

# ── settings.json ────────────────────────────────────────────────────────────────────────
if [ "$WIRE_HOOK" = "1" ]; then
  "$PYBIN" - "$SETTINGS" <<'PY'
import json, os, sys
path = sys.argv[1]
# $CLAUDE_PROJECT_DIR, not an absolute path: this file is committed, so it has to resolve
# on every contributor's machine, not just the one that ran the installer.
entry = {"matcher": "Edit|Write|MultiEdit",
         "hooks": [{"type": "command",
                    "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/validate_naming.py"',
                    "timeout": 10}]}
settings = {}
if os.path.isfile(path):
    with open(path) as fh:
        try:
            settings = json.load(fh)
        except ValueError:
            sys.exit("settings.json is not valid JSON — not touching it. Wire the hook by hand.")
    with open(path + ".bak", "w") as fh:
        json.dump(settings, fh, indent=2)
hooks = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
if any("validate_naming.py" in h.get("command", "") for e in hooks for h in e.get("hooks", [])):
    print("  hook already wired — nothing to do")
else:
    hooks.append(entry)
    with open(path, "w") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    print("  hook wired into %s%s" % (path, " (previous saved as settings.json.bak)" if os.path.isfile(path + ".bak") else ""))
PY
  echo
  echo "  Claude Code's settings watcher only watches directories that already had a settings"
  echo "  file when the session started. If .claude/settings.json is new, open /hooks once or"
  echo "  restart — otherwise the guard is on disk and inert."
else
  echo
  echo "  Rules are active on the next session. To enable the guard, re-run with --wire-hook,"
  echo "  or add this to ${SETTINGS} (merging into any existing \"hooks\" block):"
  cat <<'JSON'

  { "hooks": { "PreToolUse": [ { "matcher": "Edit|Write|MultiEdit",
      "hooks": [ { "type": "command",
                   "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/validate_naming.py\"",
                   "timeout": 10 } ] } ] } }
JSON
fi

# ── verify ───────────────────────────────────────────────────────────────────────────────
# Two real PreToolUse payloads through the installed hook. A hook that silently does nothing
# is the failure mode worth testing for, so the allow case asserts EMPTY output and the deny
# case asserts a deny decision — neither is inferred from an exit code, which is always 0
# (the hook fails open by design).
if [ "$VERIFY" = "1" ]; then
  echo
  echo "verifying the installed hook"
  TF="${ROOT}/stacks/.uif-naming-verify/infrastructure/terraform/verify.tf"
  probe() { # $1 = name to test
    printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"resource \\"aws_lb\\" \\"v\\" {\\n  name = \\"%s\\"\\n}\\n"}}' "$TF" "$1" \
      | "$PYBIN" "${HOOKS_DEST}/validate_naming.py"
  }
  FIRST="$(echo $SLOT | awk '{print $1}')"
  OK_NAME="uif-${FIRST}-dev-api-alb"
  BAD_NAME="uif-notastack-dev-api-alb"

  OUT="$(probe "$OK_NAME" || true)"
  if [ -z "$OUT" ]; then echo "  PASS  allow: ${OK_NAME}"
  else echo "  FAIL  allow: ${OK_NAME} was denied — ${OUT}"; exit 1; fi

  OUT="$(probe "$BAD_NAME" || true)"
  case "$OUT" in
    *'"permissionDecision": "deny"'*) echo "  PASS  deny : ${BAD_NAME}" ;;
    "") echo "  FAIL  deny : ${BAD_NAME} was allowed — the guard is not judging names"; exit 1 ;;
    *)  echo "  FAIL  deny : unexpected output — ${OUT}"; exit 1 ;;
  esac
  echo "  (no files were written; the probe path is synthetic and never created)"
fi
