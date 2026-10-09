#!/usr/bin/env bash
#
# The single definition of the Beacon stack's test gate.
#
# Called by two things, so the gate is stated once and cannot drift:
#   .github/workflows/stacks-ci.yml   fails the pull request
#   an FDE before pushing            same suites, same verdict
#
# WHY THIS EXISTS AT ALL (UIFPE-469). Until now Beacon ran NO Python on a pull
# request. stacks-ci probed for `src/pyproject.toml` to decide whether a stack
# needs the Python toolchain, and Beacon has Python everywhere but no packaged
# `src/`, so the probe said "no Python", the `ci (beacon)` check reported
# success having executed none of these 35 test files, and the suites ran only
# in deploy-beacon-dev.yml - after the merge.
#
# WHAT THIS DOES NOT GATE, stated exactly rather than generously, because the
# post-merge backstop is thinner than it looks:
#   ui/ (vitest)      DOES run post-merge - deploy-beacon-dev.yml's test gate
#                     runs `npm ci && npm test` on every push to develop,
#                     regardless of the deploy latches. Not on a pull request.
#   terraform validate runs post-merge only when BEACON_INFRA is plan or apply.
#                     The resolver defaults to skip when the variable is unset;
#                     the live value is `apply`, so today it does run.
#   terraform fmt, tflint, trivy/checkov
#                     run NOWHERE for this stack. Zero matches in
#                     deploy-beacon-dev.yml, and .pre-commit-config.yaml's
#                     terraform hooks are scoped to ^domains/ so they never
#                     match stacks/. apicurio's gate covers all three; this one
#                     covers Python only.
#
# AND the spark-marked engine tests, which are covered by NOTHING today.
# qe/engine/test_engine.py and test_incremental.py both open with
# `pytest.importorskip("pyspark")`, and neither CI nor this stack's own
# requirements files install pyspark - so both modules skip wherever the gate
# runs, taking 18 tests with them. That is why -rs is on and why the summary
# counts skips: a green run names them instead of folding them into a digit.
# Whether to install pyspark in CI is a real decision - a ~300 MB install per
# pull request, for tests never yet run on Linux - and is left open.
#
# On a machine that DOES have pyspark, 9 of those tests fail on Windows with
# `Python worker exited unexpectedly (crashed)` - a worker-launch problem of the
# local Spark install, not a verdict about the code. The suite already skips its
# write-dependent tests without HADOOP_HOME; this is a separate, unguarded case.
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
# rootdir, and the path below must be repo-relative - which is also exactly how
# deploy-beacon-dev.yml invokes this suite, and that is the invocation known to
# pass. (qe/conftest.py does the stack-level sys.path setup; note its own claim
# to be "the ONLY place that edits sys.path" is not quite true -
# qe/control_panel/conftest.py adds its own directory as well, under importlib
# import mode. Neither depends on the working directory.)
cd "$(dirname "$0")/../../.." || exit 1

ALLOW_MISSING=0
[ "${1:-}" = "--allow-missing-tools" ] && ALLOW_MISSING=1

# ONE PROCESS OVER ALL OF qe/, unlike stacks/echo/qe/ci.sh's per-suite loop.
# Not an inconsistency: echo's combined run dies with `INTERNALERROR>
# MemoryError`, Beacon's does not, and this is the exact invocation
# deploy-beacon-dev.yml has been running green. Matching the proven caller
# matters more here than matching echo's shape.
SUITE="stacks/beacon/qe"

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
    echo "SUMMARY: 0 passed, 0 failed, 1 NOT RUN (no python)"
    echo "NOTE: this is NOT a pass. The suite did not execute."
    exit 0
  fi
  echo "::error::no working python on PATH - the gate cannot run its suites"
  exit 1
}

"$PY" -m pytest --version >/dev/null 2>&1 || {
  if [ "$ALLOW_MISSING" -eq 1 ]; then
    echo "SKIP  pytest - not importable by $PY"
    echo
    echo "SUMMARY: 0 passed, 0 failed, 1 NOT RUN (pytest missing)"
    echo "NOTE: this is NOT a pass. The suite did not execute."
    exit 0
  fi
  # src/requirements.txt FIRST, and both qe files say so in their own headers:
  # it carries the runtime deps the tests import through the app, so omitting it
  # fails at collection with ModuleNotFoundError rather than at assert time.
  echo "::error::pytest is not importable by $PY - install this stack's"
  echo "::error::requirements, in this order: stacks/beacon/src/requirements.txt,"
  echo "::error::then stacks/beacon/qe/control_panel/requirements-dev.txt and"
  echo "::error::stacks/beacon/qe/export/requirements-dev.txt"
  exit 1
}

LOG="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/beacon-ci-$$.log")"
trap 'rm -f "$LOG"' EXIT

# -rs reports the REASON for every skip. Teed rather than captured so the output
# still streams during a two-minute suite.
echo "::group::pytest $SUITE"
"$PY" -m pytest "$SUITE" -q -rs 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
echo "::endgroup::"

# One line per distinct skip, so this counts skip ENTRIES - a module-level skip
# is one entry covering every test in that module, which is exactly the pyspark
# case above: 2 entries, 18 tests.
SKIPS=$(grep -cE '^SKIPPED ' "$LOG" 2>/dev/null || true)
SKIPS=${SKIPS:-0}

echo
if [ "$rc" -eq 0 ]; then
  echo "SUMMARY: 1 suite(s) passed, 0 failed, $SKIPS skip entries"
  echo "  passed: $SUITE"
else
  echo "SUMMARY: 0 suite(s) passed, 1 failed, $SKIPS skip entries"
  echo "  FAILED: $SUITE"
  # Outside the group: a collapsed ::group:: hides the failure output, and with
  # a single group there would otherwise be nothing visible to point at.
  echo "::error::$SUITE FAILED (pytest exit $rc) - expand the \"pytest $SUITE\" group above for the output"
fi
if [ "$SKIPS" -gt 0 ]; then
  echo "NOTE: $SKIPS skip entries above. A skip is not a pass - the -rs lines inside"
  echo "      the group name the reason, and a module-level skip covers every test in that file."
fi
exit "$rc"
