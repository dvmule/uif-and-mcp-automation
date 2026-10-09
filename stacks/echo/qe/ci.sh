#!/usr/bin/env bash
#
# The single definition of the Echo stack's test gate.
#
# Called by two things, so the gate is stated once and cannot drift:
#   .github/workflows/stacks-ci.yml   fails the pull request
#   an FDE before pushing            same suites, same order, same verdict
#
# Same reasoning as stacks/apicurio/qe/ci.sh and .githooks/lint-commits.sh: the
# root CLAUDE.md's requirement is not an editor, it is that a contributor "can
# run the same checks CI enforces before you push", which is only true if
# there is one script to run.
#
# WHY THIS EXISTS AT ALL (UIFPE-469). Until now Echo ran NO Python on a pull
# request. stacks-ci probed for `src/pyproject.toml` to decide whether a stack
# needs the Python toolchain, and Echo has Python everywhere but no packaged
# `src/`, so the probe said "no Python" and the `ci (echo)` check reported
# success having executed none of these 49 test files. The suites ran only in
# the deploy workflows, after the merge - and `qe/gold`, the largest of them,
# ran only in deploy-echo-gold-dev.yml.
#
# WHAT THIS GATES. Every Python suite, plus the whole non-Python surface of the
# release unit: infrastructure/terraform/, infrastructure/terraform-gold/, ui/
# and transform-ui/. The scope note at the non-Python gates below records how the
# last two were added and what it cost.
#
# The three non-Python groups were added by UIFPE-515, when this stack started
# cutting build tags: a build tag's evidence IS this gate, so a tag cut against
# a Python-only gate is weak evidence for a promotion nothing re-tests. What
# this file used to say was accurate at the time and is kept here because it
# explains why the gates exist rather than reading as ceremony:
#
#   "for this stack these checks run NOWHERE. Grepped across
#    deploy-echo-dev.yml, deploy-echo-gold-dev.yml and deploy-uif-echo-dev.yml
#    - no vitest, no `terraform fmt`, no `terraform validate`, no tflint, no
#    trivy, at any point in the lifecycle."
#
# Still NOT gated here: the integration tier, which needs a reachable registry
# and deployed endpoints. That runs post-deploy, not on a pull request.
#
# Usage:
#   qe/ci.sh                        every suite; a missing tool is a FAILURE
#   qe/ci.sh --allow-missing-tools  local use; a missing tool is reported and
#                                   skipped, and the summary says which
#
# CI must never pass --allow-missing-tools. A skipped suite that reports
# success is worse than no suite, because it looks like coverage.
#
# Exit 0 clean, 1 on any failure.

set -uo pipefail

# Run from the REPO ROOT, not the stack dir. pytest.ini at the root defines the
# rootdir, and the paths below must be repo-relative - which is also exactly how
# the deploy workflows invoke these suites, and those are the invocations known
# to pass. (qe/conftest.py does the central sys.path setup; its claim to be the
# ONLY editor is aspirational - a dozen test files still insert ../lambda paths
# themselves. None of it depends on the working directory.)
cd "$(dirname "$0")/../../.." || exit 1

ALLOW_MISSING=0
[ "${1:-}" = "--allow-missing-tools" ] && ALLOW_MISSING=1

# PER-SUITE, ONE PROCESS EACH. Do not "simplify" this to a single
# `pytest stacks/echo/qe` - that is the whole point of the loop, and
# deploy-echo-dev.yml carries the same warning:
#
#   1. Per-suite is what deploy-echo-dev.yml's own loop already runs, and what
#      stacks/echo/CHECKLIST.md records as the working arrangement. NOT
#      pytest.ini: that file only sets --import-mode=importlib, and its header
#      argues the opposite case - that a combined multi-suite run collects
#      cleanly. The misattribution is inherited from deploy-echo-dev.yml's
#      comment; do not propagate it further.
#   2. A single process over all files dies with `INTERNALERROR> MemoryError`.
#      The working hypothesis is interference between moto's botocore patching
#      and each test file independently swapping boto3.resource/client for a
#      MagicMock at import time. Per-suite is clean and reproducible; the
#      combined run is not. Sourced: deploy-echo-dev.yml, ADR-0015 and
#      stacks/echo/CHECKLIST.md all record it.
#
# gold is listed LAST and is the largest (668 tests): a failure in the cheap
# suites surfaces sooner that way.
SUITES=(pipeline control_panel lambdas libs gold)

# Resolve an interpreter that can actually RUN, not merely one whose NAME is on
# PATH. On Windows the Microsoft Store app-execution aliases put a `python3`
# and a `python` on PATH that are not Python at all - both print "Python was
# not found" and exit 49 - so a presence-only check returns a stub, the `py`
# candidate is never reached, and the gate blames this stack's requirements for
# a pytest it never actually looked for. Preferring a candidate that can import
# pytest keeps this resolution and the tool check below in agreement.
resolve_python() {
  local c
  for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -m pytest --version >/dev/null 2>&1 || continue
    echo "$c"; return 0
  done
  # Nothing has pytest. Fall back to a candidate that at least executes, so the
  # error below names the real cause rather than a missing interpreter.
  for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c "" >/dev/null 2>&1 || continue
    echo "$c"; return 0
  done
  return 1
}

PY="$(resolve_python)" || {
  if [ "$ALLOW_MISSING" -eq 1 ]; then
    echo "SKIP  pytest - no working python on PATH"
    echo
    echo "SUMMARY: 0 passed, 0 failed, ${#SUITES[@]} NOT RUN (no python)"
    echo "NOTE: this is NOT a pass. None of the ${#SUITES[@]} suites executed."
    exit 0
  fi
  echo "::error::no working python on PATH - the gate cannot run its suites"
  exit 1
}

"$PY" -m pytest --version >/dev/null 2>&1 || {
  if [ "$ALLOW_MISSING" -eq 1 ]; then
    echo "SKIP  pytest - not importable by $PY"
    echo
    echo "SUMMARY: 0 passed, 0 failed, ${#SUITES[@]} NOT RUN (pytest missing)"
    echo "NOTE: this is NOT a pass. None of the ${#SUITES[@]} suites executed."
    exit 0
  fi
  # src/requirements.txt FIRST: it carries the runtime deps the tests import
  # through the app, and both qe files say so in their own headers. Omitting it
  # fails at collection with ModuleNotFoundError, not at assert time.
  echo "::error::pytest is not importable by $PY - install this stack's"
  echo "::error::requirements, in this order: stacks/echo/src/requirements.txt,"
  echo "::error::then stacks/echo/qe/control_panel/requirements-dev.txt and"
  echo "::error::stacks/echo/qe/gold/requirements-dev.txt"
  exit 1
}

PASSED=()
FAILED=()
SKIPS=0
rc=0

LOG="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/echo-ci-$$.log")"
trap 'rm -f "$LOG"' EXIT

# Every suite runs even if an earlier one fails, so a red build shows the full
# picture rather than only the first casualty.
for suite in "${SUITES[@]}"; do
  path="stacks/echo/qe/$suite"
  [ -d "$path" ] || { echo "::error::$path does not exist"; rc=1; continue; }
  # -rs reports the REASON for every skip, so a green run names what it did not
  # run rather than folding it into a count. Teed rather than captured so the
  # output still streams during a three-minute suite.
  echo "::group::pytest $path"
  "$PY" -m pytest "$path" -q -rs 2>&1 | tee "$LOG"
  suite_rc=${PIPESTATUS[0]}
  echo "::endgroup::"

  # One line per distinct skip, so this counts skip ENTRIES - a module-level
  # skip is one entry covering every test in that module.
  n=$(grep -cE '^SKIPPED ' "$LOG" 2>/dev/null || true)
  SKIPS=$(( SKIPS + ${n:-0} ))

  if [ "$suite_rc" -eq 0 ]; then
    PASSED+=("$suite")
  else
    FAILED+=("$suite")
    rc=1
    # Outside the group: a collapsed ::group:: hides the failure output, so the
    # reader needs a visible pointer to the one they have to expand.
    echo "::error::stacks/echo/qe/$suite FAILED (pytest exit $suite_rc) - expand the \"pytest $path\" group above for the output"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# NON-PYTHON GATES (UIFPE-515)
#
# Added because this stack now cuts build tags, and a build tag's evidence IS
# this gate. Before this, the header above was accurate: terraform fmt,
# terraform validate, tflint, trivy and vitest ran NOWHERE in this stack's
# lifecycle, so a green line meant "Python passed" and nothing more. A tag cut
# against that is weak evidence for a promotion nobody re-tests.
#
# SCOPE: ONE RELEASE UNIT -- WHICH NOW INCLUDES GOLD, AND THAT IS A GAP.
#
# READ THIS BEFORE TRUSTING THE GATE LIST BELOW.
#
# This note used to argue that gold was a SEPARATE release unit, quoting
# stage-artifacts.sh's explicit Glue list -- "which belong to
# infrastructure/terraform-gold -- a separate release unit" -- and concluding
# "so an echo/v* tag cannot carry gold". It then used that conclusion to
# justify leaving the gold Terraform root and transform-ui/ out of the
# non-Python gates.
#
# THE PREMISE IS NO LONGER TRUE. Owner decision 2026-09-24 (UIFPE-576): gold
# folds into echo/v* as ONE release unit, because `gold` is a {layer} in the
# naming vocabulary and not a {stack}, the promotion model is stack-scoped,
# and the two halves already share source -- pipeline_controller's zip vendors
# gold_resources, gold_frequencies, gold_stats and gold_telemetry. An echo/v*
# tag now carries 36 artifacts: 14 Lambda zips (5 silver + 9 gold), 17 Glue
# scripts, 4 vendored layers and one ui_bundle.
#
# THE EXCLUSION THIS NOTE USED TO DESCRIBE IS CLOSED. A build tag's evidence IS
# this gate, and the tag carries gold artifacts, so gold's surface is gated too:
# transform-ui/ by UIFPE-738 and the gold TERRAFORM root by UIFPE-742, both
# 2026-09-30. Kept as history because each half cost what the note predicted an
# ungated tree would cost.
#
# transform-ui/: first gated run failed five tests, every one pinned to domain
# data that had moved on since anybody looked.
#
# terraform-gold/: the deferral was always "findings unknown until it runs -- an
# unbounded change". Measured instead, with a report-only run: fmt, init and
# validate already passed, tflint was clean, and trivy found FOUR blocking
# findings, not an unbounded set. One was a real bug (a DLQ missing the
# encryption both its siblings had). The other three were rules this estate has
# already accepted by owner instruction in the MAIN root, so gold now carries
# the same inline directives for the same reasons -- see the comments above each
# one. Nothing was silenced by lowering a severity threshold.
#
# What IS gated here: every Python suite including qe/gold, both Terraform roots,
# ui/ and transform-ui/. That is the whole release unit.
# ─────────────────────────────────────────────────────────────────────────────

GATES_PASSED=()
GATES_FAILED=()
GATES_SKIPPED=()

# gate <name> <command...>
gate() {
  local name="$1"; shift
  echo "::group::$name"
  if "$@"; then
    GATES_PASSED+=("$name")
    echo "::endgroup::"
  else
    local grc=$?
    echo "::endgroup::"
    GATES_FAILED+=("$name")
    rc=1
    echo "::error::gate '$name' FAILED (exit $grc) - expand the \"$name\" group above"
  fi
}

# have_tool <binary> <name> <install hint>
# A missing tool is a FAILURE unless --allow-missing-tools, for the reason this
# file's header already gives: a scanner that reports success without running
# is worse than no scanner, because it looks like coverage.
have_tool() {
  local bin="$1" name="$2" hint="$3"
  if command -v "$bin" >/dev/null 2>&1; then
    return 0
  fi
  if [ "$ALLOW_MISSING" -eq 1 ]; then
    GATES_SKIPPED+=("$name")
    echo "SKIP  $name - $bin is not installed ($hint)"
  else
    GATES_FAILED+=("$name")
    rc=1
    echo "::error::$bin is not installed, so gate '$name' did not run ($hint). CI must not pass --allow-missing-tools."
  fi
  return 1
}

# Both roots of the one release unit. The gold root joined 2026-09-30 (UIFPE-742)
# once its findings were measured rather than guessed at; the five gate loops
# below all iterate this array, which is why it had to be all five at once.
TF_ROOTS=(
  stacks/echo/infrastructure/terraform
  stacks/echo/infrastructure/terraform-gold
)
# transform-ui joined 2026-09-30 (UIFPE-738). It is a DEPLOYED app -- it ships
# to /m/transform/ on the shared RELAY distribution -- and nothing checked that
# it compiled. Its first gated run found five failing tests, every one of them
# a test pinned to domain data that had since moved on. Same scripts as ui/
# (`build` is tsc -b && vite build, `test` is vitest run), so the loop below
# needs no change. Adds ~15s.
UI_TREES=(ui transform-ui)

if have_tool terraform "terraform" "https://developer.hashicorp.com/terraform/install"; then
  for root in "${TF_ROOTS[@]}"; do
    gate "terraform fmt ($(basename "$root"))" \
      terraform -chdir="$root" fmt -check -recursive
    # -backend=false: no credentials, no state, no network to the backend.
    # validate needs the providers resolved and nothing more.
    gate "terraform init ($(basename "$root"))" \
      terraform -chdir="$root" init -backend=false -input=false -no-color
    gate "terraform validate ($(basename "$root"))" \
      terraform -chdir="$root" validate -no-color
  done
fi

if have_tool tflint "tflint" "https://github.com/terraform-linters/tflint"; then
  for root in "${TF_ROOTS[@]}"; do
    gate "tflint ($(basename "$root"))" tflint --chdir="$root" --recursive
  done
fi

if have_tool trivy "trivy" "https://trivy.dev/latest/getting-started/installation/"; then
  for root in "${TF_ROOTS[@]}"; do
    gate "trivy config ($(basename "$root"))" \
      trivy config --exit-code 1 --severity HIGH,CRITICAL "$root"
  done
fi

if have_tool npm "npm" "Node 20+"; then
  for ui in "${UI_TREES[@]}"; do
    gate "npm ci ($ui)"    npm --prefix "stacks/echo/$ui" ci
    # tsc -b and vite build are ONE script here (`npm run build`), but they are
    # two checks: esbuild strips types without checking them, so a green vite
    # build proves nothing about types. The package script runs `tsc -b` first,
    # which is what makes this gate meaningful.
    gate "npm run build ($ui)" npm --prefix "stacks/echo/$ui" run build
    gate "vitest ($ui)"       npm --prefix "stacks/echo/$ui" run test -- --run
  done
fi

echo
echo "SUMMARY: ${#PASSED[@]} suite(s) passed, ${#FAILED[@]} failed, $SKIPS skip entries"
echo "GATES:   ${#GATES_PASSED[@]} passed, ${#GATES_FAILED[@]} failed, ${#GATES_SKIPPED[@]} skipped"
[ "${#GATES_FAILED[@]}" -gt 0 ] && echo "  GATES FAILED: ${GATES_FAILED[*]}"
if [ "${#GATES_SKIPPED[@]}" -gt 0 ]; then
  echo "  GATES SKIPPED: ${GATES_SKIPPED[*]}"
  echo "  NOTE: a skipped gate is NOT a pass. This result is not evidence those checks would pass."
fi
[ "${#PASSED[@]}" -gt 0 ] && echo "  passed: ${PASSED[*]}"
[ "${#FAILED[@]}" -gt 0 ] && echo "  FAILED: ${FAILED[*]}"
if [ "$SKIPS" -gt 0 ]; then
  echo "NOTE: $SKIPS skip entries above. A skip is not a pass - the -rs lines inside"
  echo "      each group name the reason, and a module-level skip covers every test in that file."
fi
exit "$rc"
