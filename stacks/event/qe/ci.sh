#!/usr/bin/env bash
#
# The single definition of the event stack's test gate.
#
# Called by two things, so the gate is stated once and cannot drift:
#   .github/workflows/stacks-ci.yml   fails the pull request
#   an FDE before pushing             same suite, same verdict
#
# WHAT THIS GATES: the unit suites for all four shared components — the
# pipeline controller, the publish Glue job (event_fabric_processor), the
# subscription provisioner and the DLQ recorder. They are plain
# boto3+stdlib modules imported directly (no packaging), so pytest is the
# whole gate.
#
# WHAT THIS DOES NOT GATE, stated exactly: terraform fmt/validate run in
# deploy-event-dev.yml's plan step (and Product CI's repo-wide terraform
# validate covers the domain-side module call); there is no image to scan
# (src/NO-IMAGE.md).

set -euo pipefail
cd "$(dirname "$0")/.."

python -m pytest qe -q
