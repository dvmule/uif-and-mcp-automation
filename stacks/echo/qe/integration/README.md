# `qe/integration` — Echo's smoke tier

This is the suite `promote-echo.yml` runs before it cuts a release tag. It makes
real network and AWS calls against a **deployed** environment, so it is different
in kind from Echo's other five suites and is not run the same way.

## What it asserts, and what it deliberately does not

| | |
|---|---|
| **Responds** | the surface is reachable, and this promotion's bytes are what is deployed. **This is the current scope.** |
| **Responds correctly** | the promoted code behaves — a known pipeline returns the expected shape, a gold record reads back. **Not yet.** |

Four assertions:

1. CloudFront serves the UI with a non-empty body.
2. API Gateway answers 200, 401 or 403 — all three pass. The purpose is
   *reachability*: any of them proves the route, the integration and the Lambda
   exist, because a request had to traverse all three to get an answer at all.
   404 means the route is missing; 5xx means the Lambda is broken.
3. The Bronze Glue job's `script_location` contains the sha the build tag names.
4. That location is not the `ARTIFACT-SHA-NOT-SUPPLIED` sentinel.

**Assertions 3 and 4 carry the weight.** 1 and 2 pass against any converged
stack, including one where the promote copied nothing. Only the script-location
check distinguishes *"the environment exists"* from *"this promotion happened"*.
Weakening them turns this from a promote gate into an uptime check.

A release tag cut on this suite certifies something narrow and true: the stack is
up and running the artifacts the build tag names. It does **not** certify that a
pipeline produces correct output. Widening that claim without widening the
evidence is the one thing a smoke suite must never do — correctness assertions
need known data in the environment, and uat has no pipeline configs until
Phase 6.

## Not covered

The Secrets Manager VPC endpoint security group (rollout plan §4.9). A missing
ingress rule leaves every assertion here passing and fails `GetSecretValue` at
run time, inside a Glue job. Do not read a green run as covering it.

## Why `qe/ci.sh` does not run it

`ci.sh`'s `SUITES` list is `(pipeline control_panel lambdas libs gold)` — unit
suites that run anywhere with no credentials. This one needs AWS credentials, an
applied Terraform state and a build tag. Adding it there would fail every merge.

## Running it by hand

Everything is discovered from `terraform output` and the tag's manifest, so with
the main root initialised and credentials active:

```bash
TAG=echo/v0.8.0 python -m pytest stacks/echo/qe/integration -v
```

Each value has an override, so it can be pointed anywhere:

```
UIF_ECHO_SMOKE_API_URL  UIF_ECHO_SMOKE_UI_URL
UIF_ECHO_SMOKE_GLUE_JOB UIF_ECHO_SMOKE_EXPECT_SHA
UIF_ECHO_SMOKE_TAG      (when TAG is not already set)
```

## The one thing to understand before changing this

**`UIF_ECHO_SMOKE_REQUIRED` is load-bearing.** Without a deployed target the
suite skips, so a laptop run of `pytest stacks/echo/qe` does not fail. pytest
exits 0 on a skip — so in CI, where a skip would leave the smoke step green and
let a release tag be cut on no evidence, `promote-echo.yml` sets that variable
and `conftest.py` raises instead of skipping.

Getting it backwards is worse than having no suite at all: no suite fails
visibly at the workflow's `[ ! -d "$SMOKE_DIR" ]` guard, while a vacuously
skipping suite is indistinguishable from a passing one in the step summary.
`qe/pipeline/test_promote_echo_workflow.py` asserts the variable is still set.
