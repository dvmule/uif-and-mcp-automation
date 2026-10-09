#!/usr/bin/env bash
# Install the UIF Claude naming standards into ~/.claude (macOS / Linux).
#
# Idempotent: re-running is the documented update path (git pull && ./install.sh).
# Plain copies only — no symlinks, so the same model works on every platform.
#
#   ./installers/install.sh              copy rules + hook, then print the snippet to paste
#   ./installers/install.sh --wire-hook  also merge the snippet into ~/.claude/settings.json
#
# Runnable from anywhere: SRC resolves to the standards/ directory one level up.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HOME}/.claude"
RULES_DEST="${DEST}/rules/naming"
HOOKS_DEST="${DEST}/hooks"
VERSION="$(tr -d ' \t\r\n' < "${SRC}/VERSION")"
WIRE_HOOK=0
[ "${1:-}" = "--wire-hook" ] && WIRE_HOOK=1

SHA="$(git -C "${SRC}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
DATE="$(date +%Y-%m-%d)"

# The snippet ships with `python`; use whichever interpreter this machine actually has.
PYBIN="python3"
command -v python3 >/dev/null 2>&1 || PYBIN="python"
command -v "${PYBIN}" >/dev/null 2>&1 || { echo "No python interpreter on PATH." >&2; exit 1; }
snippet() { sed "s|\"python |\"${PYBIN} |" "${SRC}/settings/hooks.snippet.json"; }

# 1. rules — replace, not merge, so a removed rule file disappears on update
rm -rf "${RULES_DEST}"
mkdir -p "${RULES_DEST}"
cp -R "${SRC}/rules/naming/." "${RULES_DEST}/"

# 2. hook
mkdir -p "${HOOKS_DEST}"
cp "${SRC}/hooks/validate_naming.py" "${HOOKS_DEST}/validate_naming.py"
chmod +x "${HOOKS_DEST}/validate_naming.py"

# 3. version stamp
printf 'version=%s\ncommit=%s\ninstalled=%s\n' "${VERSION}" "${SHA}" "${DATE}" \
  > "${RULES_DEST}/.installed-version"

echo "UIF naming rules ${VERSION} (${SHA}) installed ${DATE}"
echo "  rules -> ${RULES_DEST}"
find "${RULES_DEST}" -name '*.md' | sed "s|${DEST}/|    ~/.claude/|"
echo "  hook  -> ${HOOKS_DEST}/validate_naming.py"

if [ "${WIRE_HOOK}" = "1" ]; then
  SNIPPET_TMP="$(mktemp)"; snippet > "${SNIPPET_TMP}"
  "${PYBIN}" - "$DEST/settings.json" "${SNIPPET_TMP}" <<'PY'
import json, os, sys
settings_path, snippet_path = sys.argv[1], sys.argv[2]
snippet = json.load(open(snippet_path))
settings = {}
if os.path.isfile(settings_path):
    with open(settings_path) as fh:
        try:
            settings = json.load(fh)
        except ValueError:
            sys.exit("settings.json is not valid JSON — not touching it. Paste the snippet by hand.")
    with open(settings_path + ".bak", "w") as fh:
        json.dump(settings, fh, indent=2)
hooks = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
new = snippet["hooks"]["PreToolUse"][0]
cmd = new["hooks"][0]["command"]
if any(cmd == h.get("command") for entry in hooks for h in entry.get("hooks", [])):
    print("  hook already wired in settings.json — nothing to do")
else:
    hooks.append(new)
    with open(settings_path, "w") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    print("  hook wired into %s (previous version saved as settings.json.bak)" % settings_path)
PY
  rm -f "${SNIPPET_TMP}"
else
  echo
  echo "The rules are active now. To enable the naming guard hook, paste this into"
  echo "${DEST}/settings.json (merge into an existing \"hooks\" block; do not overwrite it):"
  echo
  snippet
  echo
  echo "Or re-run with --wire-hook to merge it programmatically."
fi
