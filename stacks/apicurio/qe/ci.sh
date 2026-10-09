#!/usr/bin/env bash
#
# The single definition of this stack's build/test/scan gate.
#
# Called by two things, so the gate is stated once and cannot drift:
#   .github/workflows/stacks-ci.yml   fails the pull request
#   an FDE before pushing            same checks, same order, same verdict
#
# That second caller is the point. The root CLAUDE.md's requirement is not an
# editor, it is that "whatever you use can run the same checks CI enforces
# before you push" -- which is only true if there is one script to run. The
# same reasoning as .githooks/lint-commits.sh, which three callers share.
#
# Usage:
#   qe/ci.sh                        every gate; a missing tool is a FAILURE
#   qe/ci.sh --allow-missing-tools  local use; a missing tool is reported and
#                                   skipped, and the summary says which
#
# CI must never pass --allow-missing-tools. A skipped scanner that reports
# success is worse than no scanner, because it looks like coverage.
#
# Exit 0 clean, 1 on any failure.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

ALLOW_MISSING=0
[ "${1:-}" = "--allow-missing-tools" ] && ALLOW_MISSING=1

FAILED=()
SKIPPED=()
PASSED=()

# ruff and pytest are not reliably on PATH even when installed -- ruff in
# particular is commonly present only as a Python module. Resolve each to a
# runnable form rather than assuming the bare name works.
resolve_python() {
  if command -v python3 >/dev/null 2>&1; then echo "python3"; return 0; fi
  if command -v python  >/dev/null 2>&1; then echo "python";  return 0; fi
  return 1
}

PY="$(resolve_python || true)"

have_module() {
  [ -n "$PY" ] && "$PY" -c "import $1" >/dev/null 2>&1
}

run_gate() {
  # run_gate <name> <command...>
  local name="$1"; shift
  echo ""
  echo "──────── $name ────────"
  if "$@"; then
    PASSED+=("$name")
  else
    echo "FAILED: $name"
    FAILED+=("$name")
  fi
}

missing_tool() {
  # missing_tool <name> <what to install>
  local name="$1" hint="$2"
  if [ "$ALLOW_MISSING" = "1" ]; then
    echo "SKIPPED: $name is not installed ($hint)"
    SKIPPED+=("$name")
    return 0
  fi
  echo "FAILED: $name is not installed ($hint). CI must have it; pass --allow-missing-tools only locally."
  FAILED+=("$name (not installed)")
  return 0
}

# ── Python ────────────────────────────────────────────────────────────────────

if [ -z "$PY" ]; then
  missing_tool "python" "python3 on PATH"
else
  if have_module ruff; then
    run_gate "ruff" "$PY" -m ruff check src/ qe/
  else
    missing_tool "ruff" "pip install ruff"
  fi

  # LOCKFILE SYNC, and specifically the failure this gate did not have.
  #
  # src/pyproject.toml's `version` is recorded in src/uv.lock too, so editing
  # one without the other desyncs them. That is invisible to ruff, to pytest
  # and to every other gate here -- but Snyk runs with --all-projects, reads
  # src/uv.lock, and refuses the whole Python project with "uv.lock is out of
  # sync with pyproject.toml". One line of drift then arrives as
  # "security scan -- SCA failed", which reads as a vulnerability and is not
  # one. It cost a full CI round trip to find out, which is exactly what this
  # script exists to prevent.
  #
  # `uv lock --check` and not `uv lock`: a gate must report, never mutate. CI
  # pins uv==0.12.16 (stacks-ci.yml); resolve it the same two ways as ruff,
  # since pip installs both a console script and an importable module.
  if [ -f src/uv.lock ]; then
    if command -v uv >/dev/null 2>&1; then
      run_gate "uv lock --check" uv --project src lock --check
    elif have_module uv; then
      run_gate "uv lock --check" "$PY" -m uv --project src lock --check
    else
      missing_tool "uv lock --check" "pip install 'uv==0.12.16', the version stacks-ci.yml pins"
    fi
  fi

  if have_module pytest; then
    # The integration suite is deliberately excluded from EXECUTION. It needs a
    # reachable registry at REGISTRY_URL and a reachable BFF at BFF_URL, and the
    # registry ALB is internal -- a GitHub-hosted runner cannot see it. Running
    # it here produces a skip that reads as a pass. DEPLOY-DEV.md's tier 2
    # covers what CI can actually reach, from the public API Gateway.
    run_gate "pytest (unit)" "$PY" -m pytest qe/ -q --ignore=qe/tests/integration

    # ...but it is NOT excluded from being IMPORTED, which is a different thing
    # and the gap that let two bugs reach tier 2 on 2026-09-20. Excluding the
    # directory outright meant nothing here ever imported those modules, so:
    #   - a missing import-time dependency was invisible. The deploy smoke job
    #     installed only pytest/pytest-asyncio/httpx while qe/tests/conftest.py
    #     imports app.clients.cmdb_client, and tier 2 died at COLLECTION on
    #     `ModuleNotFoundError: No module named 'structlog'` (PR #381).
    #   - a broken conftest or a syntax error in these files would equally have
    #     waited for a deploy to surface.
    #
    # --collect-only imports every module and evaluates decorators (including
    # the os.getenv in test_api_gateway.py's skipif) WITHOUT running a test
    # body or opening a socket, so it needs none of the environment the
    # execution gate legitimately lacks. Verified with every relevant variable
    # unset -- BFF_URL, REGISTRY_URL, UI_ORIGIN, APICURIO_CI_KEY,
    # RUN_INTEGRATION_TESTS -- which still collects all 15 tests.
    #
    # No marker filter on purpose: tier 2 deselects requires_seed, so filtering
    # here would reproduce exactly the blind spot that hid two latent failures
    # in the requires_seed tests until PR #382.
    #
    # Gated on structlog rather than run unconditionally, following this
    # script's own have_module/missing_tool convention. Collecting these
    # modules needs the app's runtime dependencies on the path, which the
    # stacks-ci `ci` job installs with `pip install -e src/` (stacks-ci.yml:235)
    # but a laptop may not have. structlog is the proxy because it is the first
    # third-party import the chain reaches
    # (qe/tests/conftest.py -> app.clients.cmdb_client -> app.core.logging).
    # So: SKIPPED locally under --allow-missing-tools with a hint, and FAILED in
    # CI, where ALLOW_MISSING is 0 and a missing tool is a failure by this
    # script's own rule.
    if have_module structlog; then
      run_gate "pytest (integration imports)" \
        "$PY" -m pytest qe/tests/integration --collect-only -q
    else
      missing_tool "pytest (integration imports)" \
        "pip install -e src/, which the stacks-ci ci job does before running this"
    fi
  else
    missing_tool "pytest" "pip install pytest"
  fi
fi

# ── Node / TypeScript ─────────────────────────────────────────────────────────

if [ -f ui/package.json ]; then
  if command -v npm >/dev/null 2>&1; then
    run_gate "npm ci" npm --prefix ui ci

    # `npm run` and `npm exec` differ in a way that bit this script: run
    # executes the script with cwd set to the package root, exec does not. With
    # exec, vite resolved index.html against stacks/apicurio/ and failed with
    # "Could not resolve entry module" -- a script bug that reads exactly like
    # a broken build. Use the package's own scripts, which also means the gate
    # cannot disagree with what `npm run build` does locally.
    # Lint before the tests: it is the fastest of the three and catches the
    # class of fault the other two cannot see at all — a hook rule broken, a
    # ref written during render. `npm run lint` existed in package.json from
    # the day the app was scaffolded and failed on invocation for want of an
    # eslint install and a config, so this gate is new here even though the
    # script is not.
    run_gate "eslint" npm --prefix ui run lint

    run_gate "vitest" npm --prefix ui run test -- --run

    # ui/package.json's build script is `tsc -b && vite build`, so this one
    # gate covers both, and tsc runs first. That is the point: esbuild strips
    # types without checking them, so vite build alone proves nothing about
    # types. Keeping the project's own definition rather than restating it
    # here is what stops the two drifting.
    run_gate "npm run build (tsc -b && vite build)" npm --prefix ui run build
  else
    missing_tool "npm" "Node 20"
  fi
fi

# ── Terraform ─────────────────────────────────────────────────────────────────

TF_DIR="infrastructure/terraform"
if [ -d "$TF_DIR" ]; then
  if command -v terraform >/dev/null 2>&1; then
    run_gate "terraform fmt" terraform -chdir="$TF_DIR" fmt -check -recursive

    # `terraform validate` needs the providers, so init has to run first, but
    # it must not need credentials.
    #
    # TF_DATA_DIR points at a throwaway directory deliberately. -backend=false
    # is not sufficient on its own: if a previous `init -backend-config=...`
    # recorded the S3 backend in .terraform/terraform.tfstate, a later
    # -backend=false init still reaches for credentials and fails with "No
    # valid credential sources found". A fresh CI checkout never hits that;
    # a developer who has run a real plan hits it every time, which would make
    # this gate fail locally for a reason that has nothing to do with the code.
    export TF_DATA_DIR=".terraform-ci"
    # Share the provider download rather than duplicating it per data dir. The
    # AWS provider is ~600 MB unpacked; without this, the isolated data dir
    # above means a second full copy on disk and a second download. The
    # directory persists between runs, so the cost is paid once.
    export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
    mkdir -p "$TF_PLUGIN_CACHE_DIR"
    run_gate "terraform init (no backend)" \
      terraform -chdir="$TF_DIR" init -backend=false -input=false

    # One release, Terraform 1.15.0, validated `backend` block attributes
    # statically (hashicorp/terraform#38021). That fails every module using
    # partial backend config -- which this one does, and beacon and product do
    # too -- with "Missing required argument: bucket/key", however the backend
    # was initialized. It was reported within hours of release, called an
    # oversight by its own author, and partially reverted in 1.15.1
    # (hashicorp/terraform#38466, #38468).
    #
    # So the gate runs validate, and skips it loudly on exactly that one
    # version rather than reporting a failure that is not this repo's. CI pins
    # 1.15.8, so CI never takes this branch -- only a laptop that happens to
    # have 1.15.0 installed does. The version is NOT excluded in
    # required_version: the bug affects validate alone, and blocking init,
    # plan and apply over a broken static gate would be out of proportion.
    TF_VER="$(terraform version -json 2>/dev/null | "${PY:-cat}" -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null || echo unknown)"
    if [ "$TF_VER" = "1.15.0" ]; then
      echo "SKIPPED: terraform validate -- Terraform 1.15.0 rejects partial backend config (hashicorp/terraform#38466, fixed in 1.15.1). Upgrade; CI pins 1.15.8."
      SKIPPED+=("terraform validate (Terraform 1.15.0 bug)")
    else
      run_gate "terraform validate" terraform -chdir="$TF_DIR" validate
    fi

    if command -v tflint >/dev/null 2>&1; then
      run_gate "tflint" tflint --chdir="$TF_DIR" --recursive
    else
      missing_tool "tflint" "brew install tflint"
    fi
  else
    missing_tool "terraform" "terraform >= 1.10 (backends/*.hcl uses use_lockfile)"
  fi

  # Two targets, not one. Pointing trivy at the terraform directory scans
  # terraform only -- it does not walk up to src/Dockerfile, so the image's own
  # misconfigurations were invisible to a gate that also runs `docker build`.
  # Scanning the stack root instead would work but walks ui/node_modules and
  # ui/dist, so the targets are named.
  #
  # The suppressions live as `# trivy:ignore:AVD-...` comments beside the
  # resources they excuse, each with its reasoning. Two things about that
  # mechanism, both established by testing rather than read off the docs:
  # the directive must be the LAST comment line before the block (with prose
  # after it, it is silently not applied, which is worse than no comment
  # because the file reads as though it were); and a .trivyignore file would
  # not work here at all -- trivy reads it from the CWD, not the scan target,
  # and it can only suppress a check estate-wide, never one resource.
  if command -v trivy >/dev/null 2>&1; then
    run_gate "trivy config (terraform)" trivy config --exit-code 1 --severity HIGH,CRITICAL "$TF_DIR"
    if [ -f src/Dockerfile ]; then
      run_gate "trivy config (src image)" trivy config --exit-code 1 --severity HIGH,CRITICAL src/
    fi
  elif command -v checkov >/dev/null 2>&1; then
    run_gate "checkov" checkov -d "$TF_DIR" --compact --quiet
  else
    missing_tool "trivy config" "brew install trivy, or pip install checkov"
  fi
fi

# ── Container ─────────────────────────────────────────────────────────────────

if [ -f src/Dockerfile ]; then
  # Dockerfile lint, before anything is built. The root CLAUDE.md names hadolint
  # among the checks "CI enforces before you push", and until now this gate did
  # not run it -- so the repo documented a check nobody was running.
  #
  # It is a separate gate from `trivy config (src image)`, which also reads the
  # Dockerfile: trivy judges security misconfiguration, hadolint judges Docker
  # correctness and shell usage in RUN lines. They overlap on almost nothing.
  #
  # One trap, the same shape as the `trivy:ignore` one documented above and
  # established the same way -- by testing, after a directive was silently
  # inert: a `# hadolint ignore=DLxxxx` comment is only honoured when it is the
  # LAST comment line before the instruction it excuses. Put prose after it and
  # hadolint drops it without a word, so the file reads as though the rule were
  # excused while the gate still fails on it.
  if command -v hadolint >/dev/null 2>&1; then
    run_gate "hadolint" hadolint src/Dockerfile
  else
    missing_tool "hadolint" "brew install hadolint"
  fi

  # CAN WE ACTUALLY PULL THE BASE THIS DOCKERFILE PINS?
  #
  # ARG BASE_IMAGE points at the SIF mirror in a PRIVATE ECR repository, so
  # `docker build` needs a registry login. The deploy path has one; the
  # PR-time gate does not, and cannot get one today: the dev environment's
  # deployment-branch policy admits only develop and main, and the deploy
  # role's trust needs refs/heads/develop OR environment:dev -- a pull-request
  # branch matches neither. Failing every pull request for a credential it is
  # not permitted to hold would make the gate unusable, so the two image gates
  # are recorded as SKIPPED, by name and with the reason, and the summary says
  # plainly that a skip is not a pass.
  #
  # This RELOCATES coverage rather than dropping it: deploy-apicurio-dev.yml's
  # bff-image job runs the same
  # `trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1`
  # against the same build, with real credentials, BEFORE it pushes. The
  # artifact that ships is always scanned; what a pull request cannot do is
  # scan it beforehand. The same reasoning the integration tier already uses
  # for the internal ALB (see the pytest gate above).
  #
  # Narrow on purpose. It skips only when the base is a private ECR reference
  # AND the login actually fails: a laptop holding credentials (aws-runas)
  # builds and scans exactly as before, and a public base is never skipped.
  #
  # The real fix is a CI-scoped, pull-only OIDC role whose trust admits
  # pull_request refs, which needs an IAM change and sign-off. Until that
  # exists, this is the honest state of the gate rather than a green tick over
  # a build that never happened.
  BASE_REACHABLE=yes
  base_registry=""
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    base_ref=$(sed -n 's/^ARG BASE_IMAGE=//p' src/Dockerfile | head -1)
    base_registry=${base_ref%%/*}
    case "$base_registry" in
      *.dkr.ecr.*.amazonaws.com)
        base_region=$(printf '%s' "$base_registry" | cut -d. -f4)
        if ! (command -v aws >/dev/null 2>&1 \
              && aws ecr get-login-password --region "$base_region" 2>/dev/null \
                 | docker login --username AWS --password-stdin "$base_registry" \
                   >/dev/null 2>&1); then
          BASE_REACHABLE=no
        fi
        ;;
    esac
  fi

  if [ "$BASE_REACHABLE" = "no" ]; then
    echo "SKIPPED: docker build, trivy image (CVEs) -- no credentials for $base_registry,"
    echo "         the private ECR mirror holding the SIF base image. The same trivy image"
    echo "         gate runs against this build in deploy-apicurio-dev.yml, with credentials,"
    echo "         before the push. See the comment above this check."
    SKIPPED+=("docker build (no ECR credentials for the SIF base)")
    SKIPPED+=("trivy image (CVEs) (no ECR credentials for the SIF base)")
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    IMG="uif-apicurio-ci:$$"

    # --platform=linux/amd64 matches the Dockerfile's own FROM lines and the
    # amd64 Fargate runtime. Without it, a build on an Apple Silicon laptop
    # produces an arm64 image and scans a different set of packages than CI
    # would -- the gate has to agree with itself across both callers.
    run_gate "docker build" docker build -q --platform=linux/amd64 -t "$IMG" src/

    # The image-layer CVE scan, which this gate did not have until now. Two
    # `trivy config` gates above scan the Dockerfile and terraform as TEXT for
    # misconfiguration; neither one opens the built image. That gap is why this
    # stack reported 0 misconfigurations while its Debian base shipped 205
    # HIGH/CRITICAL CVEs. capabilities/infrastructure/docker.md:86 requires
    # "`trivy image` clean of HIGH/CRITICAL" -- this is the gate that enforces it.
    #
    # Ordered deliberately after `docker build` and pointed at $IMG, the same
    # tag the build produced: `trivy image` cannot resolve a bare sha256 with no
    # tag, so the build has to leave a named reference behind. The build gate
    # used to `docker rmi` immediately, which left nothing to scan even though
    # it did tag; the removal now happens after this gate instead.
    #
    # --severity HIGH,CRITICAL --exit-code 1 matches the `trivy config` gates
    # above, so one severity policy covers config and image alike.
    # --scanners vuln keeps this to CVEs only; secret and licence scanning are
    # separate concerns and would change what a failure here means.
    # --skip-version-check suppresses the "new version available" notice, which
    # is noise in CI logs and, locally, advice about the developer's laptop.
    #
    # Local trivy and CI trivy are NOT the same version: CI pins v0.74.0 while
    # this was developed against 0.69.3. A newer scanner ships a newer
    # vulnerability DB and more detection logic, so CI can legitimately report
    # findings a local run did not. A green local run is evidence, not a
    # guarantee -- if CI fails this gate and your laptop passes it, believe CI.
    if command -v trivy >/dev/null 2>&1; then
      run_gate "trivy image (CVEs)" \
        trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 \
                    --skip-version-check "$IMG"
    else
      missing_tool "trivy image" "brew install trivy"
    fi

    docker rmi -f "$IMG" >/dev/null 2>&1 || true
  else
    missing_tool "docker build" "a running Docker daemon"
    # Deliberately reported separately rather than folded into the line above.
    # trivy image needs the image that `docker build` was going to produce, so a
    # missing daemon takes out two gates, not one. Naming only the build would
    # let the summary imply the CVE scan had run.
    missing_tool "trivy image" "a running Docker daemon (needs the built image)"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "════════ summary ════════"
printf 'passed   %d: %s\n' "${#PASSED[@]}"  "${PASSED[*]:-none}"
printf 'skipped  %d: %s\n' "${#SKIPPED[@]}" "${SKIPPED[*]:-none}"
printf 'failed   %d: %s\n' "${#FAILED[@]}"  "${FAILED[*]:-none}"

if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo ""
  echo "NOTE: ${#SKIPPED[@]} gate(s) did not run. This result does NOT mean they would pass."
fi

[ "${#FAILED[@]}" -eq 0 ] || exit 1
echo ""
echo "all gates green"
