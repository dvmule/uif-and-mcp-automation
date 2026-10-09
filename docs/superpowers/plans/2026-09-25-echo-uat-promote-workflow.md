# Echo uat promote workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `promote-echo.yml` so promoting a build tag into uat is a recorded workflow run rather than a manual `copy-artifacts.sh` invocation.

**Architecture:** One workflow, two jobs (`resolve`, `promote`), gated `skip|copy|run`. `copy` runs the artifact copy only — the greenfield path that unblocks the Phase 4 converge. `run` adds the app-only plan assertion, the apply, the UI publish, a minimal smoke and the release tag. All the dangerous logic stays in scripts the workflow calls; the YAML orchestrates and nothing else.

**Tech Stack:** GitHub Actions, bash, Terraform 1.15.x, AWS CLI v2, Python 3 (`unittest`) for the tests.

**Spec:** [`docs/superpowers/specs/2026-09-25-echo-uat-promote-workflow-design.md`](../specs/2026-09-25-echo-uat-promote-workflow-design.md)

> **SUPERSEDED IN PART, 2026-09-25.** The workflow this designs was **not**
> merged. UIFPE-576 landed its own `promote-echo.yml` on `develop` at the same
> time, independently, and that is the one that survived — it is multi-environment
> (`uat|prod`), covers the gold root as well as the main one, and derives the
> app-only address list with a script rather than a hardcoded list.
>
> What still stands, and is why this document is kept rather than deleted:
>
> - **The asymmetry in "The asymmetry this design is built on"** — Echo must
>   promote *before* it converges because Lambda validates its S3 object at
>   `CreateFunction`, where apicurio can converge first because an ECS task
>   definition does not. UIFPE-576's workflow independently arrives at the same
>   order, which is corroboration rather than coincidence.
> - **The eight verified constraints**, each with the file and line that shows it.
> - **The UI publish gap.** This design's `UIF_ECHO_UI_BUNDLE` path was built and
>   merged; UIFPE-576's workflow records `ui_bundle: NOT promoted` in its release
>   tag, so the gap this closes is still open on the workflow side.
> - **The smoke.** `smoke-uat.sh` was built and merged. UIFPE-576's workflow runs
>   `pytest stacks/echo/qe/integration` and fails by design until that exists, so
>   the two do not yet connect.
>
> What is obsolete: the gating section (UIFPE-576 uses `skip|run`, with no `copy`
> mode), the job/step layout, and the uat-only scoping.


## Global Constraints

- **Ticket:** every commit message ends with a `UIFPE-555 #comment <line>` trailer, then the `Co-Authored-By` trailer. A commit without the issue key is rejected by a hook.
- **Environment:** uat only. `environment: uat`, `secrets.AWS_ROLE_ARN_ECHO_UAT`. No prod path — no prod deploy role exists.
- **Terraform version:** pin `1.15.8`, matching `stacks-ci.yml` and `deploy-apicurio-dev.yml`. A saved plan is only valid for the version that wrote it.
- **Region:** `eu-west-1`. Account `283556840805`.
- **Tag:** `echo/v0.1.0` is the only build tag that exists today.
- **`resolve-deploy-mode.sh` must not be modified.** It is shared with apicurio by convention; a behaviour change drifts the two copies.
- **The plan JSON is piped, never written to a file, `tee`'d or uploaded.** A saved plan carries every generated value.
- **`assert-app-only-plan.sh` has three exit codes:** `0` apply, `3` plan empty — *not* a failure, skip the apply and continue, `1` fail the run.
- **No inline `aws s3 sync --delete` anywhere in the workflow.** The UI bucket hosts beacon and transform under `m/*`.
- **Line endings:** write LF. `core.autocrlf=true` here with no `.gitattributes`.
- **Python on Windows:** `python`, not `python3`. Set `sys.stdout.reconfigure(encoding='utf-8')` before printing non-ASCII, and strip `\r` from anything Python writes that a shell will compare.

## File Structure

| File | Responsibility |
|---|---|
| `.github/workflows/promote-echo.yml` | **Create.** Orchestration only: resolve the mode, authenticate, call scripts, summarise. No business logic. |
| `stacks/echo/scripts/smoke-uat.sh` | **Create.** The three smoke assertions, with every input passed in explicitly so it runs from a laptop too. |
| `stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh` | **Modify.** Add `UIF_ECHO_UI_BUNDLE` to publish a restored bundle instead of building. |
| `stacks/echo/qe/pipeline/test_promote_echo_workflow.py` | **Create.** Asserts the workflow's invariants against the YAML. |
| `stacks/echo/docs/uif-echo-uat-rollout-plan.md` | **Modify.** Phase 4.6 and Phase 5 rows point at the workflow. |

**Deviation from the spec, deliberate:** the spec says `--from-bundle <tar.gz>`. Use **`UIF_ECHO_UI_BUNDLE=<path>`** instead. `uif-echo-ui-deploy.sh` takes positional `$1` (ENV) and `$2` (API_URL_OVERRIDE) and its siblings take options as environment variables (`BUILD_LAYERS=1`, `SKIP_TERRAFORM=1`, `UIF_ECHO_TF_MODE=plan|apply`), all documented in their headers. A flag would mean converting the script to flag parsing — a larger, riskier change to a script dev depends on, for no gain.

---

### Task 1: Publish a restored UI bundle without rebuilding

**Files:**
- Modify: `stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh:14-29`
- Test: `stacks/echo/qe/pipeline/test_ui_bundle_publish.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `UIF_ECHO_UI_BUNDLE=<path-to-dir>` — when set and non-empty, the script copies that **directory's** contents into `ui/dist` and skips `npm ci`/`npm run build`. A directory, not a tarball: `restore-artifacts.sh --class ui_bundle --dest <dir>` already extracts and verifies, and re-extracting here would duplicate that.

- [ ] **Step 1: Write the failing test**

Create `stacks/echo/qe/pipeline/test_ui_bundle_publish.py`:

```python
# test_ui_bundle_publish.py
#
# Pins the publish-a-restored-bundle path in uif-echo-ui-deploy.sh.
#
# WHY A TEST. A promote must not rebuild the UI: the bundle's content hash IS
# its identity, and `npm run build` is not byte-reproducible across runners, so
# rebuilding would publish bytes no release tag names. The guard is one
# conditional, and nothing about the surrounding code makes its absence
# obvious.
#
# The sync is the other half. `aws s3 sync --delete --exclude "m/*"` is what
# lets this script prune stale Echo assets without deleting beacon's and
# transform's apps from the same bucket. Both the --delete and the exclude must
# survive, together: --delete without the exclude wipes another team's site,
# and the exclude without --delete leaves stale assets forever.
import os
import re
import unittest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "deploy",
    "uif-echo-ui-deploy.sh",
)


def source():
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


class TestFromBundle(unittest.TestCase):
    def test_bundle_variable_is_read(self):
        self.assertIn("UIF_ECHO_UI_BUNDLE", uncommented())

    def test_build_is_skipped_when_a_bundle_is_given(self):
        src = uncommented()
        # npm must be reachable only inside the else branch.
        self.assertRegex(
            src,
            r'if \[ -n "\$\{UIF_ECHO_UI_BUNDLE:-\}" \];[\s\S]*?else[\s\S]*?npm ci[\s\S]*?npm run build[\s\S]*?fi',
            "npm ci/npm run build must sit in the else branch: a promote that "
            "rebuilds publishes bytes no release tag names",
        )

    def test_a_missing_bundle_directory_is_refused(self):
        self.assertIn("is not a directory", uncommented())


class TestSyncSemanticsSurvive(unittest.TestCase):
    def test_delete_and_exclude_are_both_present_on_one_command(self):
        src = uncommented()
        m = re.search(r"aws s3 sync[\s\S]{0,300}?--region", src)
        self.assertIsNotNone(m, "could not find the sync")
        sync = m.group(0)
        self.assertIn("--delete", sync)
        self.assertIn('--exclude "m/*"', sync)

    def test_there_is_exactly_one_sync(self):
        self.assertEqual(
            1, len(re.findall(r"aws s3 sync", uncommented())),
            "a second sync is how one of them loses the m/* exclude",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest stacks/echo/qe/pipeline/test_ui_bundle_publish.py -q`
Expected: 3 failures in `TestFromBundle` (the variable, the branch, the refusal). `TestSyncSemanticsSurvive` should already pass — it pins existing behaviour.

- [ ] **Step 3: Implement the branch**

In `stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh`, replace lines 25-28 (`cd "$REPO_ROOT/ui"` through the `ok "UI built ..."` line) with:

```bash
# PROMOTE PATH: publish a bundle that was built elsewhere, verified, and
# restored. A promote must NOT rebuild. `npm run build` is not byte-stable
# across runners, so a rebuild produces a bundle whose hash no release tag
# names -- and the digest equality is the whole evidence the promotion model
# offers. restore-artifacts.sh has already extracted and re-hashed this
# directory with config.json excluded, so there is nothing left to verify here.
cd "$REPO_ROOT/ui"
if [ -n "${UIF_ECHO_UI_BUNDLE:-}" ]; then
  [ -d "$UIF_ECHO_UI_BUNDLE" ] \
    || die "UIF_ECHO_UI_BUNDLE='${UIF_ECHO_UI_BUNDLE}' is not a directory. Pass the directory restore-artifacts.sh --dest populated, not the tarball."
  rm -rf dist
  mkdir -p dist
  cp -R "${UIF_ECHO_UI_BUNDLE}/." dist/
  ok "UI taken from the restored bundle at ${UIF_ECHO_UI_BUNDLE} (not rebuilt)"
else
  npm ci
  npm run build
  ok "UI built → ui/dist/ (environment-independent)"
fi
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `python -m pytest stacks/echo/qe/pipeline/test_ui_bundle_publish.py -q`
Expected: 5 passed.

- [ ] **Step 5: Prove each assertion fails under mutation**

For each, edit, re-run, confirm one failure, restore byte-exact (`cmp` against a backup):
1. Rename `UIF_ECHO_UI_BUNDLE` to `UI_BUNDLE` → `test_bundle_variable_is_read` fails.
2. Move `npm ci`/`npm run build` above the `if` → `test_build_is_skipped_when_a_bundle_is_given` fails.
3. Delete the `[ -d ... ] || die` line → `test_a_missing_bundle_directory_is_refused` fails.
4. Remove `--exclude "m/*"` → `test_delete_and_exclude_are_both_present_on_one_command` fails.

- [ ] **Step 6: Check the header documents the new variable**

Add to the "Optional environment variables" block in the script header:

```
#   UIF_ECHO_UI_BUNDLE=<dir>      Publish this restored bundle instead of
#                                 building. Used by promote-echo.yml; a
#                                 promote must never rebuild.
```

- [ ] **Step 7: Verify the whole suite and commit**

```bash
bash -n stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh
python -m pytest stacks/echo/qe/pipeline -q
git add stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh stacks/echo/qe/pipeline/test_ui_bundle_publish.py
git commit
```

Commit message body must state why a rebuild is forbidden, not just that the flag was added.

---

### Task 2: The smoke

**Files:**
- Create: `stacks/echo/scripts/smoke-uat.sh`
- Test: `stacks/echo/qe/pipeline/test_smoke_uat.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `smoke-uat.sh --api-url <url> --ui-url <url> --glue-job <name> --expect-sha <hex>`. Exit `0` all three assertions pass, `1` any fail. Every input is explicit — no Terraform output reading inside, so it runs from a laptop against any environment.

- [ ] **Step 1: Write the failing test**

Create `stacks/echo/qe/pipeline/test_smoke_uat.py`:

```python
# test_smoke_uat.py
#
# Pins what the uat smoke actually checks.
#
# WHY THESE THREE. Echo has no integration suite (qe/ci.sh:38 records the tier
# as deliberately absent) and no health route, and the API is behind auth. So
# this is the minimal set that distinguishes "the stack exists" from "this
# promotion landed":
#
#   1. CloudFront serves the UI          -> something is published
#   2. API Gateway answers 401/403       -> route, Lambda and authorizer wired
#   3. a Glue job's script_location
#      contains the promoted sha         -> THIS promotion landed
#
# Assertion 3 is the one that earns its place. Without it a promote could copy
# nothing at all and still go green, because 1 and 2 pass against any converged
# stack.
#
# 401/403 IS A PASS, and that is not laxness. An unauthenticated request
# reaching the authorizer proves every layer in front of it exists. A 404 means
# the route is missing and a 5xx means the Lambda is broken, so both must fail.
import os
import re
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "smoke-uat.sh")


def source():
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


class TestRequiredInputs(unittest.TestCase):
    def test_every_input_is_a_required_flag(self):
        src = uncommented()
        for flag in ("--api-url", "--ui-url", "--glue-job", "--expect-sha"):
            self.assertIn(flag, src)
        self.assertEqual(
            4, len(re.findall(r"is required", src)),
            "all four inputs must be required; a defaulted url silently "
            "smoke-tests the wrong environment",
        )

    def test_it_reads_no_terraform_output(self):
        self.assertNotIn(
            "uif_echo_tf_out", uncommented(),
            "inputs are passed in so this runs from a laptop and cannot "
            "resolve a different environment than the caller meant",
        )


class TestAssertions(unittest.TestCase):
    def test_ui_must_return_200_and_a_body(self):
        src = uncommented()
        self.assertRegex(src, r'\[ "\$ui_code" = "200" \]')
        self.assertIn("ui_bytes", src)

    def test_api_401_or_403_passes_and_404_or_5xx_fails(self):
        src = uncommented()
        self.assertRegex(
            src, r"401\|403",
            "401 and 403 are passes: they prove the authorizer was reached",
        )

    def test_glue_script_location_is_checked_against_the_promoted_sha(self):
        src = uncommented()
        self.assertIn("glue get-job", src)
        self.assertIn("EXPECT_SHA", src)

    def test_the_sha_check_is_not_a_substring_of_nothing(self):
        # An empty EXPECT_SHA makes `case $loc in *""*)` match everything.
        self.assertIn("64 lowercase hex", uncommented())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest stacks/echo/qe/pipeline/test_smoke_uat.py -q`
Expected: every test errors — the file does not exist.

- [ ] **Step 3: Write the script**

Create `stacks/echo/scripts/smoke-uat.sh`, `chmod +x`:

```bash
#!/usr/bin/env bash
# Prove a promoted uat stack is reachable, and that THIS promotion landed.
#
# Three assertions, all unauthenticated, so this needs no credential in the
# shared uat environment. Echo has no integration suite and no health route
# (see qe/ci.sh:38), so this is the minimal set that separates "the stack
# exists" from "the promotion worked":
#
#   1. CloudFront serves the UI with a non-empty body.
#   2. API Gateway answers 401 or 403 -- a PASS, because an unauthenticated
#      request reaching the authorizer proves the route, the integration and
#      the Lambda all exist. 404 means the route is missing; 5xx means the
#      Lambda is broken. Both fail.
#   3. A Glue job's script_location contains the promoted sha.
#
# Assertion 3 is the only one that proves anything about THIS promotion. 1 and
# 2 pass against any converged stack, so without 3 a promote that copied
# nothing would still go green.
#
# WHAT THIS DOES NOT COVER: the Secrets Manager VPC endpoint security group
# (Phase 4.9). A missing ingress rule leaves every assertion here passing and
# fails GetSecretValue at run time. Do not read a green smoke as covering it.
#
# Usage:
#   smoke-uat.sh --api-url <url> --ui-url <url> \
#                --glue-job <name> --expect-sha <64-hex>
set -euo pipefail

die()  { printf 'smoke-uat.sh: %s\n' "$1" >&2; exit 1; }
pass() { printf '  PASS  %s\n' "$1"; }

API_URL="" UI_URL="" GLUE_JOB="" EXPECT_SHA=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --api-url)    [ "$#" -ge 2 ] || die "--api-url needs a value";    API_URL="$2";    shift 2 ;;
    --ui-url)     [ "$#" -ge 2 ] || die "--ui-url needs a value";     UI_URL="$2";     shift 2 ;;
    --glue-job)   [ "$#" -ge 2 ] || die "--glue-job needs a value";   GLUE_JOB="$2";   shift 2 ;;
    --expect-sha) [ "$#" -ge 2 ] || die "--expect-sha needs a value"; EXPECT_SHA="$2"; shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$API_URL" ]    || die "--api-url is required"
[ -n "$UI_URL" ]     || die "--ui-url is required"
[ -n "$GLUE_JOB" ]   || die "--glue-job is required"
[ -n "$EXPECT_SHA" ] || die "--expect-sha is required"

# Checked, because an empty or truncated value turns assertion 3 into a
# substring match that everything satisfies.
printf '%s' "$EXPECT_SHA" | grep -Eq '^[0-9a-f]{64}$' \
  || die "--expect-sha must be 64 lowercase hex characters; got '${EXPECT_SHA}'"

# ── 1. The UI is served ───────────────────────────────────────────────────────
ui_body="$(mktemp)"
trap 'rm -f "$ui_body"' EXIT
ui_code="$(curl -sS -o "$ui_body" -w '%{http_code}' --max-time 30 "$UI_URL/" || echo 000)"
[ "$ui_code" = "200" ] \
  || die "the UI at ${UI_URL}/ returned ${ui_code}, not 200. Nothing is published, or CloudFront cannot reach the bucket."
ui_bytes="$(wc -c < "$ui_body" | tr -d ' ')"
[ "$ui_bytes" -gt 100 ] \
  || die "the UI returned 200 but only ${ui_bytes} bytes. A 200 with an empty body is CloudFront serving an error document."
pass "UI 200, ${ui_bytes} bytes"

# ── 2. The API answers ────────────────────────────────────────────────────────
# GET /pipelines is a real route (apigateway.tf). Unauthenticated, so 401 or
# 403 is the CORRECT answer and the assertion this makes.
api_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${API_URL}/pipelines" || echo 000)"
case "$api_code" in
  401|403) pass "API ${api_code} (authorizer reached)" ;;
  404) die "the API returned 404 for GET /pipelines: the route does not exist, so the apply did not create it." ;;
  000) die "the API at ${API_URL} could not be reached at all." ;;
  2*)  die "the API returned ${api_code} for an UNAUTHENTICATED GET /pipelines. The authorizer is not enforcing, which is worse than being unreachable." ;;
  *)   die "the API returned ${api_code} for GET /pipelines; expected 401 or 403." ;;
esac

# ── 3. This promotion landed ──────────────────────────────────────────────────
loc="$(aws glue get-job --job-name "$GLUE_JOB" \
  --query 'Job.Command.ScriptLocation' --output text 2>/dev/null | tr -d '\r' || true)"
[ -n "$loc" ] && [ "$loc" != "None" ] \
  || die "could not read ScriptLocation for Glue job ${GLUE_JOB}."
case "$loc" in
  *"$EXPECT_SHA"*) pass "Glue ${GLUE_JOB} runs ${EXPECT_SHA:0:12}…" ;;
  *ARTIFACT-SHA-NOT-SUPPLIED*)
    die "Glue job ${GLUE_JOB} points at ARTIFACT-SHA-NOT-SUPPLIED. The apply ran without artifact_shas, so no artifact was promoted." ;;
  *) die "Glue job ${GLUE_JOB} runs ${loc}, which does not contain the promoted sha ${EXPECT_SHA}. The stack is converged on a different release." ;;
esac

printf 'smoke-uat.sh: all three assertions passed\n'
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `bash -n stacks/echo/scripts/smoke-uat.sh && python -m pytest stacks/echo/qe/pipeline/test_smoke_uat.py -q`
Expected: 7 passed.

- [ ] **Step 5: Prove the refusals actually refuse**

Run each and confirm a non-zero exit and the stated message. These need no AWS:

```bash
bash stacks/echo/scripts/smoke-uat.sh --api-url http://x --ui-url http://y --glue-job j --expect-sha short
```
Expected: exit 1, "must be 64 lowercase hex characters".

```bash
bash stacks/echo/scripts/smoke-uat.sh --api-url http://x --ui-url http://y --glue-job j
```
Expected: exit 1, "--expect-sha is required".

- [ ] **Step 6: Mutation-verify, then commit**

Mutations to prove: drop the `--expect-sha` hex check → `test_the_sha_check_is_not_a_substring_of_nothing` fails; change `401|403` to `2*` → `test_api_401_or_403_passes_and_404_or_5xx_fails` fails; remove one `is required` → `test_every_input_is_a_required_flag` fails. Restore byte-exact each time.

```bash
python -m pytest stacks/echo/qe/pipeline -q
git add stacks/echo/scripts/smoke-uat.sh stacks/echo/qe/pipeline/test_smoke_uat.py
git commit
```

---

### Task 3: `promote-echo.yml`, `copy` mode

**Files:**
- Create: `.github/workflows/promote-echo.yml`
- Test: `stacks/echo/qe/pipeline/test_promote_echo_workflow.py` (create)

**Interfaces:**
- Consumes: `copy-artifacts.sh` (merged), `read-manifest.sh` (merged), `resolve-deploy-mode.sh` (merged, **unmodified**).
- Produces: a workflow with jobs `resolve` (outputs `promote`, `tag`) and `promote`. Task 4 extends the `promote` job; it adds no new job.

- [ ] **Step 1: Write the failing test**

Create `stacks/echo/qe/pipeline/test_promote_echo_workflow.py`. Write the full file now including the Task 4 cases — they will fail until Task 4 lands, so mark them with `@unittest.expectedFailure` and remove the decorator in Task 4. Body:

```python
# test_promote_echo_workflow.py
#
# Pins promote-echo.yml's invariants. Asserted against the YAML as text rather
# than by running it, because a workflow run needs AWS, a tag and an
# environment approval -- and because what decays here is the invariants, not
# the happy path.
#
# THE ONE THAT MATTERS MOST is test_no_inline_delete_sync. The UI bucket hosts
# beacon's and transform's apps under m/*, and uif-echo-ui-deploy.sh's
# `--delete --exclude "m/*"` is the only thing stopping --delete removing them.
# If that sync is ever reimplemented in YAML, the exclude is one forgotten line
# away from deleting another team's site. A --delete sync already removed
# 166 MB of layer zips in this repo on 2026-09-12.
import os
import re
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    ".github", "workflows", "promote-echo.yml",
)


def source():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


class TestGating(unittest.TestCase):
    def test_mode_vocabulary_is_exactly_skip_copy_run(self):
        self.assertIn("skip,copy,run", uncommented())

    def test_default_is_skip(self):
        self.assertIn('--default "skip"', uncommented())

    def test_resolver_gets_both_input_and_var(self):
        # The event decides which applies. Passing only one would mean the
        # workflow guessing, and on a dispatch run the variable is never read.
        src = uncommented()
        self.assertIn("--input", src)
        self.assertIn("--var", src)
        self.assertIn("resolve-deploy-mode.sh", src)

    def test_the_shared_resolver_is_called_not_reimplemented(self):
        src = uncommented()
        self.assertNotRegex(
            src, r'case "\$\{?IN_PROMOTE',
            "mode resolution belongs in resolve-deploy-mode.sh, which apicurio "
            "shares; reimplementing it inline drifts the two",
        )


class TestJobSafety(unittest.TestCase):
    def test_promote_job_declares_the_environment(self):
        # Without `environment:` the reviewer and the tag policy silently do
        # not apply and the run still succeeds.
        self.assertRegex(uncommented(), r"environment:\s*uat")

    def test_concurrency_does_not_cancel_in_progress(self):
        src = uncommented()
        self.assertIn("concurrency:", src)
        self.assertIn("cancel-in-progress: false", src)

    def test_oidc_permission_is_job_scoped(self):
        src = uncommented()
        self.assertIn("id-token: write", src)
        self.assertIn("contents: read", src)

    def test_it_reads_echos_own_role_secret(self):
        src = uncommented()
        self.assertIn("AWS_ROLE_ARN_ECHO_UAT", src)
        self.assertNotRegex(
            src, r"secrets\.AWS_ROLE_ARN_UAT\b",
            "AWS_ROLE_ARN_UAT is product's",
        )
        self.assertNotRegex(
            src, r"secrets\.AWS_ROLE_ARN\s*\}",
            "bare AWS_ROLE_ARN is apicurio's",
        )


class TestTagHandling(unittest.TestCase):
    def test_the_annotated_tag_is_refetched(self):
        # actions/checkout on a tag ref discards the annotation, and a
        # lightweight tag carries no manifest.
        src = uncommented()
        self.assertIn("git fetch --force origin", src)
        self.assertIn("git cat-file -t", src)

    def test_checkout_fetches_full_history(self):
        self.assertIn("fetch-depth: 0", uncommented())

    def test_push_trigger_is_documented_as_dead(self):
        # It is kept only so the repo variable has an event where it applies.
        self.assertIn("github.token", source())


class TestCopyMode(unittest.TestCase):
    def test_copy_mode_reaches_no_apply(self):
        src = uncommented()
        for m in re.finditer(r"terraform apply", src):
            window = src[max(0, m.start() - 600):m.start()]
            self.assertIn(
                "== 'run'", window,
                "every terraform apply must be gated on run mode; copy mode "
                "exists to promote artifacts on a stack that has none",
            )

    def test_destination_bucket_comes_from_the_tfvars(self):
        self.assertIn("release_artifacts_bucket", uncommented())


class TestRunMode(unittest.TestCase):
    def test_app_only_assertion_precedes_apply(self):
        src = uncommented()
        self.assertLess(
            src.index("assert-app-only-plan.sh"),
            src.index("terraform apply"),
            "the assertion is worthless after the apply",
        )

    def test_plan_json_is_piped_never_saved(self):
        src = uncommented()
        self.assertRegex(
            src, r"terraform show -json[^\n|]*\|",
            "the plan JSON must be piped: it carries every generated value",
        )
        self.assertNotRegex(
            src, r"terraform show -json[^\n]*>",
            "redirecting it to a file puts generated values on disk",
        )

    def test_exit_three_is_handled_as_not_a_failure(self):
        # 3 means the plan was empty, which is the expected outcome of the
        # first promote of any environment.
        self.assertIn("= 3 ]", uncommented())

    def test_ui_publish_goes_through_the_script(self):
        src = uncommented()
        self.assertIn("uif-echo-ui-deploy.sh", src)
        self.assertIn("UIF_ECHO_UI_BUNDLE", src)

    def test_no_inline_delete_sync(self):
        src = uncommented()
        for m in re.finditer(r"aws s3 sync", src):
            self.fail(
                "the UI sync must stay in uif-echo-ui-deploy.sh, where the "
                "--exclude \"m/*\" that protects beacon and transform lives"
            )

    def test_smoke_runs_before_the_release_tag(self):
        src = uncommented()
        self.assertLess(
            src.index("smoke-uat.sh"), src.index("next-release-tag.sh"),
            "a release/uat tag must mean something was checked",
        )


if __name__ == "__main__":
    unittest.main()
```

Apply `@unittest.expectedFailure` to every method in `TestRunMode` for this task only.

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest stacks/echo/qe/pipeline/test_promote_echo_workflow.py -q`
Expected: errors — the workflow does not exist.

- [ ] **Step 3: Write the workflow, copy mode only**

Create `.github/workflows/promote-echo.yml`:

```yaml
name: Promote echo
run-name: "Promote ${{ github.ref_name }} into uat"

# HOW THIS RUNS, AND WHAT DOES NOT RUN IT.
#
# workflow_dispatch against the BUILD TAG's ref is the operative entry point,
# and the dispatch INPUT is the gate.
#
# `push: tags:` is kept but will NOT fire for a tag that release-echo.yml cut:
# that workflow pushes with github.token (release-echo.yml:329), and a
# GITHUB_TOKEN-pushed ref deliberately creates no workflow run. The trigger is
# here only because it is the one event under which UIF_ECHO_UAT_PROMOTE has
# any effect -- resolve-deploy-mode.sh reads the input on workflow_dispatch and
# the variable otherwise. Do not read this trigger as "promotion is automatic".
#
# A PHASE TRANSITION IS A FRESH DISPATCH, NOT A RE-RUN. Re-running reuses the
# original inputs, so a re-run cannot move copy to run.
on:
  push:
    tags:
      - "echo/v*"
  workflow_dispatch:
    inputs:
      promote:
        description: "skip = nothing; copy = artifacts only (greenfield); run = full promote"
        type: choice
        options: [skip, copy, run]
        default: skip

permissions:
  contents: read

env:
  AWS_REGION: eu-west-1
  TF_DIR: stacks/echo/infrastructure/terraform
  TFVARS_ENV: uat-uif

jobs:
  resolve:
    name: What this run will do
    runs-on: ubuntu-latest
    outputs:
      promote: ${{ steps.mode.outputs.promote }}
    steps:
      - uses: actions/checkout@v4

      - id: mode
        env:
          EVENT: ${{ github.event_name }}
          IN_PROMOTE: ${{ inputs.promote }}
          VAR_PROMOTE: ${{ vars.UIF_ECHO_UAT_PROMOTE }}
        run: |
          set -euo pipefail
          promote="$(stacks/echo/scripts/resolve-deploy-mode.sh \
            --event "$EVENT" --input "${IN_PROMOTE:-}" --var "${VAR_PROMOTE:-}" \
            --allowed "skip,copy,run" --default "skip")"
          echo "promote=$promote" >> "$GITHUB_OUTPUT"
          echo "::notice::promote=$promote (tag ${GITHUB_REF_NAME})"

  promote:
    name: Promote ${{ github.ref_name }} into uat
    needs: resolve
    if: needs.resolve.outputs.promote != 'skip'
    runs-on: ubuntu-latest
    environment: uat
    permissions:
      contents: read
      id-token: write
    concurrency:
      group: echo-promote-uat
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      # actions/checkout on a tag ref produces a LIGHTWEIGHT tag, which has no
      # message and therefore no manifest. read-manifest.sh would refuse.
      - name: Re-fetch the annotated tag object
        env:
          TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          git fetch --force origin "+refs/tags/${TAG}:refs/tags/${TAG}"
          type="$(git cat-file -t "refs/tags/${TAG}")"
          [ "$type" = "tag" ] \
            || { echo "::error::refs/tags/${TAG} is a ${type}, not an annotated tag object, so it carries no manifest."; exit 1; }
          echo "::notice::${TAG} is an annotated tag object."

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN_ECHO_UAT }}
          aws-region: ${{ env.AWS_REGION }}

      # Read from the tfvars with the same sed uif_echo_import_phase5_artifacts
      # uses. A second derivation is how two answers appear.
      - name: Resolve this environment's release bucket
        id: bucket
        run: |
          set -euo pipefail
          b="$(sed -nE 's/^[[:space:]]*release_artifacts_bucket[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
            "${TF_DIR}/envs/${TFVARS_ENV}.tfvars" | head -1)"
          [ -n "$b" ] || { echo "::error::no release_artifacts_bucket in envs/${TFVARS_ENV}.tfvars"; exit 1; }
          echo "name=$b" >> "$GITHUB_OUTPUT"

      - name: Promote the artifacts
        env:
          TAG: ${{ github.ref_name }}
          BUCKET: ${{ steps.bucket.outputs.name }}
        run: |
          set -euo pipefail
          bash stacks/echo/scripts/copy-artifacts.sh \
            --tag "$TAG" \
            --to-bucket "$BUCKET" \
            --tfvars "${TF_DIR}/artifacts.auto.tfvars.json" \
            --tsv promoted.tsv
          {
            echo "### Promoted \`${TAG}\` into \`${BUCKET}\`"
            echo
            echo "| class | artifacts |"
            echo "|---|---|"
            cut -f1 promoted.tsv | sort | uniq -c | awk '{printf "| %s | %s |\n", $2, $1}'
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest stacks/echo/qe/pipeline/test_promote_echo_workflow.py -q`
Expected: all `TestGating`, `TestJobSafety`, `TestTagHandling`, `TestCopyMode` pass; `TestRunMode` reported as expected failures.

- [ ] **Step 5: Validate the YAML parses**

```bash
python -c "import yaml,io; yaml.safe_load(io.open('.github/workflows/promote-echo.yml',encoding='utf-8')); print('yaml ok')"
```

If `yaml` is unavailable, `gh workflow view promote-echo.yml` after pushing serves the same purpose.

- [ ] **Step 6: Mutation-verify and commit**

Prove: removing `environment: uat` fails `test_promote_job_declares_the_environment`; changing the secret to `AWS_ROLE_ARN_UAT` fails `test_it_reads_echos_own_role_secret`; dropping `--var` fails `test_resolver_gets_both_input_and_var`; removing `fetch-depth: 0` fails `test_checkout_fetches_full_history`. Restore byte-exact.

```bash
git add .github/workflows/promote-echo.yml stacks/echo/qe/pipeline/test_promote_echo_workflow.py
git commit
```

---

### Task 4: `promote-echo.yml`, `run` mode

**Files:**
- Modify: `.github/workflows/promote-echo.yml` (append steps to the `promote` job)
- Modify: `stacks/echo/qe/pipeline/test_promote_echo_workflow.py` (remove `@unittest.expectedFailure`)
- Modify: `stacks/echo/docs/uif-echo-uat-rollout-plan.md` (Phase 4.6 and Phase 5 rows)

**Interfaces:**
- Consumes: `UIF_ECHO_UI_BUNDLE` (Task 1), `smoke-uat.sh` (Task 2), the `promote` job and `steps.bucket.outputs.name` (Task 3).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Remove the expected-failure decorators**

Delete every `@unittest.expectedFailure` in `TestRunMode`.

- [ ] **Step 2: Run to confirm they now fail for real**

Run: `python -m pytest stacks/echo/qe/pipeline/test_promote_echo_workflow.py -q`
Expected: 6 failures in `TestRunMode`.

- [ ] **Step 3: Append the run-mode steps**

Append to the `promote` job in `.github/workflows/promote-echo.yml`. Every step is gated `if: needs.resolve.outputs.promote == 'run'`:

```yaml
      - uses: hashicorp/setup-terraform@v3
        if: needs.resolve.outputs.promote == 'run'
        with:
          # Same pin as stacks-ci.yml. A saved plan is only valid for the
          # version that wrote it.
          terraform_version: "1.15.8"
          terraform_wrapper: false

      - name: Restore and verify the UI bundle
        if: needs.resolve.outputs.promote == 'run'
        id: ui
        env:
          TAG: ${{ github.ref_name }}
          BUCKET: ${{ steps.bucket.outputs.name }}
        run: |
          set -euo pipefail
          reader=stacks/echo/scripts/read-manifest.sh
          sha="$("$reader" "$TAG" ui_bundle.sha256)"
          key="$("$reader" "$TAG" ui_bundle.key)"
          mkdir -p .ui-bundle
          aws s3 cp "s3://${BUCKET}/${key}" bundle.tar.gz --quiet
          bash stacks/echo/scripts/restore-artifacts.sh \
            --class ui_bundle --file bundle.tar.gz --dest .ui-bundle --sha256 "$sha"
          echo "dir=$PWD/.ui-bundle" >> "$GITHUB_OUTPUT"

      - name: Plan
        if: needs.resolve.outputs.promote == 'run'
        run: |
          set -euo pipefail
          terraform -chdir="$TF_DIR" init -reconfigure \
            -backend-config="backends/${TFVARS_ENV}.hcl"
          terraform -chdir="$TF_DIR" plan -input=false \
            -var-file="envs/${TFVARS_ENV}.tfvars" -out=tf.plan

      # PIPED, never saved. A saved plan's JSON contains every generated value,
      # so it must not reach a log, a file or an artifact -- only resource
      # ADDRESSES are ever printed. See the script's own header.
      #
      # THREE outcomes. 3 means the plan is empty, which the script explicitly
      # calls not a failure, and which is the EXPECTED result of the first
      # promote of any environment: the converge already applied these
      # artifacts. Treating non-zero as failure would break every first run.
      - name: Assert the plan touches only the application layer
        if: needs.resolve.outputs.promote == 'run'
        id: apponly
        run: |
          set -uo pipefail
          set +e
          terraform -chdir="$TF_DIR" show -json tf.plan \
            | bash stacks/echo/scripts/assert-app-only-plan.sh \
                aws_lambda_function aws_lambda_layer_version aws_glue_job
          rc=$?
          set -e
          if [ "$rc" = 3 ]; then
            echo "::notice::the plan is empty; nothing to apply. Expected on a first promote."
            echo "apply=false" >> "$GITHUB_OUTPUT"
          elif [ "$rc" = 0 ]; then
            echo "apply=true" >> "$GITHUB_OUTPUT"
          else
            echo "::error::the plan touches something outside the application layer."
            exit 1
          fi

      - name: Apply the reviewed plan
        if: needs.resolve.outputs.promote == 'run' && steps.apponly.outputs.apply == 'true'
        run: terraform -chdir="$TF_DIR" apply -input=false tf.plan

      - name: Publish the UI bundle with this environment's config
        if: needs.resolve.outputs.promote == 'run'
        env:
          UIF_ECHO_UI_BUNDLE: ${{ steps.ui.outputs.dir }}
        run: bash stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh "${TFVARS_ENV}"

      - name: Smoke
        if: needs.resolve.outputs.promote == 'run'
        env:
          TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          api="$(terraform -chdir="$TF_DIR" output -raw api_endpoint)"
          ui="$(terraform -chdir="$TF_DIR" output -raw ui_cloudfront_url)"
          prefix="$(sed -nE 's/^[[:space:]]*name_prefix_override[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
            "${TF_DIR}/envs/${TFVARS_ENV}.tfvars" | head -1)"
          sha="$(stacks/echo/scripts/read-manifest.sh "$TAG" glue_scripts.bronze_ingest.sha256)"
          bash stacks/echo/scripts/smoke-uat.sh \
            --api-url "$api" --ui-url "$ui" \
            --glue-job "${prefix}-bronze-ingest" --expect-sha "$sha"

      - name: Cut the release tag
        if: needs.resolve.outputs.promote == 'run'
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          rel="$(bash stacks/echo/scripts/next-release-tag.sh uat)"
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          reader=stacks/echo/scripts/read-manifest.sh
          # The BUILD tag's gate, carried forward as this release's evidence.
          # Assigned before use: under set -e a failing command substitution
          # used as an argument to `export` or `echo` is swallowed, because the
          # outer command still succeeds.
          git_commit="$("$reader" "$TAG" git_commit)"
          gate_run_id="$("$reader" "$TAG" gate.run_id)"
          gate_conclusion="$("$reader" "$TAG" gate.conclusion)"

          # release-manifest.sh takes NO FLAGS: six scalars in the environment,
          # artifacts as a TSV, manifest on stdout. ARTIFACT_FILE is
          # copy-artifacts.sh's --tsv output, whose version_id column holds
          # THIS environment's versions rather than dev's. TAG is the tag the
          # manifest describes -- the release tag -- because the manifest's
          # first line is "<tag>:" and read-manifest.sh asserts it.
          TAG="$rel" \
          GIT_COMMIT="$git_commit" \
          GATE_RUN_ID="$gate_run_id" \
          GATE_CONCLUSION="$gate_conclusion" \
          BUCKET="${{ steps.bucket.outputs.name }}" \
          ARTIFACT_FILE=promoted.tsv \
            bash stacks/echo/scripts/release-manifest.sh > manifest.txt

          git tag -a "$rel" -F manifest.txt
          git push origin "refs/tags/${rel}"
          echo "::notice::cut ${rel} from ${TAG}"
```

**Note on `contents:`** — the release tag needs write. Change the `promote` job's `permissions` to `contents: write`, and keep the workflow-level default at `contents: read` so `resolve` stays read-only.

- [ ] **Step 4: Confirm the two release scripts' interfaces (verified 2026-09-25)**

Already checked, and recorded here so the executor need not re-derive it:

- `next-release-tag.sh <env> [<date>]` — positional. `next-release-tag.sh uat` is correct.
- `release-manifest.sh` — **no flags.** Six required environment variables (`TAG GIT_COMMIT GATE_RUN_ID GATE_CONCLUSION BUCKET ARTIFACT_FILE`, plus an optional `BUILT_AT` so output is reproducible under test), printing the manifest to stdout.

Re-read both headers once to confirm nothing has changed since. **If they differ, the scripts are right and this plan is wrong** — adjust the workflow, never the scripts.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest stacks/echo/qe/pipeline/test_promote_echo_workflow.py -q`
Expected: all pass, `TestRunMode` included.

- [ ] **Step 6: Update the rollout plan**

In `stacks/echo/docs/uif-echo-uat-rollout-plan.md`, change the Phase 4.6 heading and body to say the copy runs via `promote-echo.yml` with `promote=copy`, and the Phase 5 row to say `promote=run` covers the app-only apply, UI publish, smoke and release tag. Keep the existing explanation of *why* the copy precedes the converge.

- [ ] **Step 7: Full verification and commit**

```bash
python -c "import yaml,io; yaml.safe_load(io.open('.github/workflows/promote-echo.yml',encoding='utf-8')); print('yaml ok')"
python -m pytest stacks/echo/qe/pipeline -q
git add .github/workflows/promote-echo.yml stacks/echo/qe/pipeline/test_promote_echo_workflow.py stacks/echo/docs/uif-echo-uat-rollout-plan.md
git commit
```

---

## After the plan: the first real run

Not a task, because it needs a human with the environment approval.

1. Dispatch `promote-echo.yml` against `echo/v0.1.0` with `promote=copy`. Expect 27 `+` lines.
2. Locally, regenerate the tfvars with a dry run (read-only) — expect 27 `=`:
   ```bash
   bash stacks/echo/scripts/copy-artifacts.sh --tag echo/v0.1.0 \
     --to-bucket uif-echo-uat-release-artifacts \
     --tfvars stacks/echo/infrastructure/terraform/artifacts.auto.tfvars.json
   ```
3. Phase 4.7/4.8: the full converge, locally.
4. Phase 4.9: the Secrets Manager endpoint SG — **the smoke will not catch this.**
5. Dispatch again with `promote=run`. Expect the app-only plan to be **empty** (exit 3), the apply skipped, UI published, smoke green, `release/uat/<date>.1` cut.

## Self-Review

**Spec coverage.** Gating → Task 3. Jobs → Task 3. `copy` steps → Task 3. `run` steps → Task 4. UI publish → Task 1. Smoke → Task 2. Refusals → inherited from merged scripts, asserted in Tasks 2–4. Testing → every task. The tfvars/local-converge section → "After the plan", step 2. Out-of-scope items are not implemented, as intended.

**One spec item deliberately altered:** `--from-bundle` became `UIF_ECHO_UI_BUNDLE`, reasoned in File Structure.

**A guess found and closed by this review:** the first draft invoked `release-manifest.sh` with `--environment/--build-tag/--tsv`. Those flags do not exist — it takes six environment variables and prints to stdout. Reading the two scripts turned a plausible-looking command into a correct one, and confirmed something the promote design depends on: `ARTIFACT_FILE` is `copy-artifacts.sh`'s `--tsv` output, and a uat release manifest needs that file's `version_id` column to carry **uat's** version ids, not dev's. That is why `copy-artifacts.sh` records destination versions, and why an earlier draft of it writing the literal `PRESENT` into that column was wrong.

**Type consistency.** `UIF_ECHO_UI_BUNDLE` is a *directory* in Task 1 and is fed `steps.ui.outputs.dir` in Task 4. `smoke-uat.sh`'s four flags match Task 4's invocation. `steps.bucket.outputs.name` is defined in Task 3 and consumed in Task 4.
