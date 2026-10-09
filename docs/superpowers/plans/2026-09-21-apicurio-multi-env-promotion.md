# Apicurio Multi-Environment Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Apicurio two deployment workflows — one that converges infrastructure per environment, one that promotes application artifacts by digest — both taking their identity from the build tag's release manifest, so `uat` (and later `prod`) can be deployed at all.

**Architecture:** All non-trivial logic is extracted into shell scripts under `stacks/apicurio/scripts/` and unit-tested with pytest, following `copy-image.sh` and `read-manifest.sh`. The two workflow files stay small because they delegate. `deploy-apicurio-dev.yml` is not touched.

**Tech Stack:** GitHub Actions, Terraform (OpenTofu-compatible AWS provider), bash, pytest, `crane`, AWS CLI v2.

**Spec:** `docs/superpowers/specs/2026-09-21-apicurio-multi-env-promotion-design.md`

**Jira:** UIFPE-486 (parent epic UIFPE-32)

## Corrections applied during execution

This plan is now a historical record, not a live spec: it was executed, reviewed and
fixed task by task (see the ledger at
`.superpowers/sdd/2026-09-21-apicurio-multi-env-promotion/progress.md`), and this plan's
own embedded copies of the workflows, scripts and runbook were **not** re-synced after
every fix round — a direct instance of the "link, don't duplicate" rule this repository's
`CLAUDE.md` states and this plan violated by embedding verbatim copies in the first place.
Where a correction below is not reflected in this plan's code blocks, **the shipped file
is the authority**, not this document:

- **The `read-manifest.sh` outputs in Task 5 / promote-apicurio.yml's manifest step**
  (Global Constraint "absent must be an error, never empty" applies here too, and this
  plan's own code block violated it). `echo "field=$("$reader" ...)"` inside a
  `{ ... } >> "$GITHUB_OUTPUT"` block does not fail under `set -e` when the inner command
  substitution fails — `echo`'s own exit status is what `-e` observes — so a build tag
  whose CI gate never passed produced six empty outputs and a promotion that continued
  anyway instead of stopping. Fixed by assigning each field to a bare variable
  (`v="$(reader ...)"`) before the `GITHUB_OUTPUT` block; the code block below now shows
  the corrected form.
- **`DEPLOY-UAT.md` phase 3.** This plan originally told the operator to edit
  `envs/uat.tfvars`, merge to `develop`, and "re-run" the phase-2 run with
  `APICURIO_UAT_INFRA=apply`. That cannot work: `envs/uat.tfvars` is git-tracked, so its
  contents are frozen at the tag's own commit, and `actions/checkout@v4` defaults to
  `github.sha` — the tag's commit for a `push: tags` run — so no re-run of an existing tag
  can ever see the merged edit. Phase 3 must **cut a new build tag** instead, the same
  action as phase 1. The corrected phase table and the "Re-running rather than re-tagging"
  section below reflect this; the general rule is **changing a variable costs a re-run,
  changing a file costs a new tag.**
- **Ruff.** The Global Constraint below originally read "ruff must report 'All checks
  passed'." That is factually wrong: `infrastructure/terraform/authorizer/index.py`
  carries 7 pre-existing ruff findings, verified identical on `origin/develop`, i.e.
  baseline and not introduced by this work. The constraint is corrected to "ruff
  introduces no NEW findings."

Left as noted rather than rewritten, because each is harmless and rewriting every code
block in a 1,800-line historical plan to match is not a good trade: the weaker
`has("resource_changes")` guard in Task 2's original code block (~line 489, tightened in
the shipped `assert-app-only-plan.sh` to check the value is an array, not merely present),
the dead `allowed_actions` block this plan's Task 2 code originally showed before Ruling
R3 omitted it from the start (~line 495/528), and the absent `actions/setup-python` step
in Task 5's original promote-apicurio.yml code block (~line 528 area of Task 5; the
shipped workflow pins `actions/setup-python@v5` at `"3.12"` ahead of tier 2's dependency
install, matching `deploy-apicurio-dev.yml`).

## Global Constraints

- **Every commit message must carry `UIFPE-486` first on its own line**, followed by `#comment <one line>`. A commit hook rejects messages without an issue key. A line starting with `#` is stripped by git, so `#comment` must never lead the line.
- **`tf.plan` is never uploaded as an artifact, and `terraform show -json` never reaches a log or an artifact.** A saved plan carries generated values in clear. Plan and apply stay in the same job. Piping `terraform show -json` into `jq` and printing only resource **addresses** is the one permitted read.
- **No `-auto-approve` when applying a plan file.** It is not a valid pairing and is not needed.
- **Every `uses:` must resolve to an enterprise-allow-listed owner**: `actions`, `github`, `hashicorp`, `aws-actions`, `aquasecurity`, `sonarsource`, `opentofu`, `pearson*`. One disallowed action fails the entire workflow at startup with no logs.
- **Environment vocabulary is closed: `{dev, uat, prod}`.** `t_environment` maps `uat → STG`.
- **Bash must be portable to BSD and GNU `sed`.** `\?` is GNU-only. `die` inside `$( )` exits only the subshell — end command substitutions with `|| true` where a failing `grep` would otherwise kill the script silently, as `read-tfvar.sh` and `read-manifest.sh` do.
- **Absent must be an error, never empty.** `terraform apply -var bff_image_digest=` is a variable that is set and wrong.
- **Presence is not shape.** Validate the *form* of every resolved value (digest, sha256, tag), not merely that it is non-empty. `gh api` prints its 404 body to stdout, so a non-empty string can be an error document.
- **Terraform working directory** is `stacks/apicurio/infrastructure/terraform` for every Terraform step.
- **Python tests** live in `stacks/apicurio/qe/tests/release/` and are run with `pytest`. Ruff must introduce no NEW findings (see "Corrections applied during execution" above — a baseline of 7 pre-existing findings in `infrastructure/terraform/authorizer/index.py` predates this plan and is out of scope for it).

---

### Task 1: `resolve-deploy-mode.sh` — the fail-closed latch resolver

Both workflows need the same decision: what does this run actually do? Dispatch inputs win when present, otherwise the repository variable decides, and **anything unrecognised becomes the safe default** rather than being passed through to a step that might act on it.

**Files:**
- Create: `stacks/apicurio/scripts/resolve-deploy-mode.sh`
- Test: `stacks/apicurio/qe/tests/release/test_resolve_deploy_mode_sh.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `resolve-deploy-mode.sh --event <name> --input <value> --var <value> --allowed <csv> --default <value>` prints one token from `--allowed` on stdout and exits 0; exits 1 with a message on stderr for a malformed invocation (unknown flag, missing value, `--default` not in `--allowed`).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for scripts/resolve-deploy-mode.sh.

Both deployment workflows ask the same question -- what does this run do? --
and the dangerous answer is a value nobody listed. A latch that passes an
unrecognised token through hands it to a step that may act on it, so every
unknown value must collapse to the default. The default itself is the safe
one: the environment-changing latches default to skip.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "resolve-deploy-mode.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash on PATH")

ALLOWED = "skip,plan,apply,bootstrap"


def run(*args):
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True
    )


def resolve(event, input_value, var_value, allowed=ALLOWED, default="skip"):
    return run(
        "--event", event,
        "--input", input_value,
        "--var", var_value,
        "--allowed", allowed,
        "--default", default,
    )


def test_dispatch_prefers_the_input_over_the_variable():
    r = resolve("workflow_dispatch", "apply", "plan")
    assert r.returncode == 0
    assert r.stdout.strip() == "apply"


def test_push_ignores_the_input_and_uses_the_variable():
    # A dispatch input is empty on a push event, but even if a stale value
    # arrived it must not decide a push run.
    r = resolve("push", "apply", "plan")
    assert r.returncode == 0
    assert r.stdout.strip() == "plan"


def test_empty_input_on_dispatch_falls_back_to_the_default():
    r = resolve("workflow_dispatch", "", "apply")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_empty_variable_on_push_falls_back_to_the_default():
    r = resolve("push", "", "")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_unrecognised_value_collapses_to_the_default_not_itself():
    r = resolve("push", "", "APPLY")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"
    assert "APPLY" in r.stderr


def test_a_value_that_merely_contains_an_allowed_token_is_rejected():
    # Substring matching would accept "no-apply" as "apply".
    r = resolve("push", "", "no-apply")
    assert r.returncode == 0
    assert r.stdout.strip() == "skip"


def test_a_non_default_default_is_honoured():
    # The smoke latch defaults ON, unlike the environment-changing latches.
    r = resolve("push", "", "", allowed="run,skip", default="run")
    assert r.returncode == 0
    assert r.stdout.strip() == "run"


def test_default_outside_allowed_is_a_malformed_invocation():
    r = resolve("push", "", "", allowed="run,skip", default="apply")
    assert r.returncode == 1
    assert "not one of" in r.stderr


def test_unknown_flag_is_refused_rather_than_ignored():
    r = run("--event", "push", "--mode", "apply")
    assert r.returncode == 1
    assert "unknown" in r.stderr.lower()


def test_a_flag_with_no_value_is_refused():
    r = run("--event")
    assert r.returncode == 1


def test_no_arguments_prints_usage_and_fails():
    r = run()
    assert r.returncode == 1
    # Assert on the stderr message, not merely the word "Usage:" -- every
    # comment line in the script starts with "# Usage:" and would satisfy a
    # looser check even if the script had stopped printing anything.
    assert r.stderr.strip().startswith("resolve-deploy-mode.sh:")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd stacks/apicurio && pytest qe/tests/release/test_resolve_deploy_mode_sh.py -v`
Expected: every test FAILS — the script does not exist, so bash exits 127.

- [ ] **Step 3: Write the minimal implementation**

```bash
#!/usr/bin/env bash
# Resolve what a deployment run will do, failing closed.
#
# Usage:
#   resolve-deploy-mode.sh --event <name> --input <value> --var <value> \
#                          --allowed <csv> --default <value>
#
# Prints one token from --allowed on stdout. A dispatch input wins when the
# event is workflow_dispatch; otherwise the repository variable decides.
#
# WHY UNRECOGNISED VALUES COLLAPSE TO THE DEFAULT rather than failing the run:
# these latches are read on every tag push, and a typo in a repository
# variable should not break every push -- it should deploy nothing and say so.
# The value is echoed to stderr so the typo is visible in the log.
#
# Matching is exact against the whole token. Substring matching would read
# "no-apply" as "apply", which is the opposite of what it says.
set -euo pipefail

die() { printf '%s: %s\n' "resolve-deploy-mode.sh" "$1" >&2; exit 1; }

event="" input="" var="" allowed="" default=""

[ "$#" -gt 0 ] || die "no arguments. Usage: --event <name> --input <value> --var <value> --allowed <csv> --default <value>"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --event)   [ "$#" -ge 2 ] || die "--event needs a value";   event="$2";   shift 2 ;;
    --input)   [ "$#" -ge 2 ] || die "--input needs a value";   input="$2";   shift 2 ;;
    --var)     [ "$#" -ge 2 ] || die "--var needs a value";     var="$2";     shift 2 ;;
    --allowed) [ "$#" -ge 2 ] || die "--allowed needs a value"; allowed="$2"; shift 2 ;;
    --default) [ "$#" -ge 2 ] || die "--default needs a value"; default="$2"; shift 2 ;;
    *) die "unknown flag '$1'" ;;
  esac
done

[ -n "$allowed" ] || die "--allowed is required"
[ -n "$default" ] || die "--default is required"

# Exact membership test against a comma-separated list, with no glob or
# substring semantics. `set -f` is not needed because nothing is unquoted.
in_allowed() {
  local needle="$1" rest="${allowed}," item
  while [ -n "$rest" ]; do
    item="${rest%%,*}"
    rest="${rest#*,}"
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

in_allowed "$default" || die "--default '${default}' is not one of '${allowed}'"

if [ "$event" = "workflow_dispatch" ]; then
  candidate="$input"
else
  candidate="$var"
fi

if [ -z "$candidate" ]; then
  printf '%s\n' "$default"
  exit 0
fi

if in_allowed "$candidate"; then
  printf '%s\n' "$candidate"
else
  printf "%s: '%s' is not one of '%s'; using '%s'\n" \
    "resolve-deploy-mode.sh" "$candidate" "$allowed" "$default" >&2
  printf '%s\n' "$default"
fi
```

- [ ] **Step 4: Make it executable and run the tests**

Run:
```bash
chmod +x stacks/apicurio/scripts/resolve-deploy-mode.sh
cd stacks/apicurio && pytest qe/tests/release/test_resolve_deploy_mode_sh.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Mutation-test the guard**

Every script in this chain had a real bug or a real test gap found by mutating it. Apply each mutation, confirm at least one test fails, then revert:

1. Change `in_allowed "$candidate"` to `[ -n "$candidate" ]` → `test_unrecognised_value_collapses_to_the_default_not_itself` must fail.
2. Change the exact comparison `[ "$item" = "$needle" ]` to `case "$needle" in *"$item"*) return 0 ;; esac` → `test_a_value_that_merely_contains_an_allowed_token_is_rejected` must fail.
3. Delete the `in_allowed "$default"` check → `test_default_outside_allowed_is_a_malformed_invocation` must fail.
4. Change `if [ "$event" = "workflow_dispatch" ]` to `if [ -n "$input" ]` → `test_push_ignores_the_input_and_uses_the_variable` must fail.

Any mutation that survives means a test is missing. Add it before moving on.

- [ ] **Step 6: Lint and commit**

```bash
cd stacks/apicurio && ruff check qe/tests/release/test_resolve_deploy_mode_sh.py
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/scripts/resolve-deploy-mode.sh \
        stacks/apicurio/qe/tests/release/test_resolve_deploy_mode_sh.py
git commit -F - <<'MSG'
Resolve a deployment run's mode in one fail-closed place

Both new workflows ask the same question -- what does this run do -- and an
unrecognised latch value is the dangerous answer: passed through, it reaches a
step that may act on it. This collapses anything not on the allowed list to
the default and echoes the offending value to stderr, so a typo in a
repository variable deploys nothing and says why instead of breaking every
push.

Matching is exact against the whole token. Substring matching would read
"no-apply" as "apply".

UIFPE-486 #comment Add resolve-deploy-mode.sh, the fail-closed latch resolver both promotion workflows share, with 11 unit tests and four mutations verified.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 2: `assert-app-only-plan.sh` — prove a plan's blast radius

Promotion must only ever move the BFF task definition and service. `deploy-apicurio-dev.yml` proves this inline; this task extracts the same logic into a tested script that reads `terraform show -json` on **stdin**, so the plan document never touches a file, a log, or an artifact.

**Files:**
- Create: `stacks/apicurio/scripts/assert-app-only-plan.sh`
- Test: `stacks/apicurio/qe/tests/release/test_assert_app_only_plan_sh.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `assert-app-only-plan.sh <allowed-address>...` reading plan JSON on stdin. Exit `0` = app-only confirmed, apply it. Exit `3` = the plan is empty, there is nothing to apply. Exit `1` = the plan touches something else, or the input was not usable. Prints only resource **addresses**, never values.

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for scripts/assert-app-only-plan.sh.

A promotion may move the BFF task definition and service and nothing else. The
script's job is to refuse everything wider -- including the case that looks
harmless, an out-of-band infrastructure change riding along inside an
unrelated apply.

Its second job is discretion. A Terraform plan contains every generated value,
so this reads the JSON on stdin and prints only resource addresses. A test
that allowed a value to be echoed would be asserting the wrong thing.

Three exits, because "nothing to do" and "confirmed" need different handling
by the caller: 0 = apply it, 3 = empty, 1 = refuse.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "assert-app-only-plan.sh"

BASH = shutil.which("bash")
JQ = shutil.which("jq")
pytestmark = pytest.mark.skipif(
    BASH is None or JQ is None, reason="needs bash and jq on PATH"
)

ALLOWED = ["aws_ecs_task_definition.bff", "aws_ecs_service.bff"]


def plan_json(*changes):
    """Build a terraform show -json document from (address, actions) pairs."""
    return json.dumps(
        {
            "resource_changes": [
                {"address": addr, "change": {"actions": actions}}
                for addr, actions in changes
            ]
        }
    )


def run(document, allowed=ALLOWED):
    return subprocess.run(
        [BASH, str(SCRIPT), *allowed],
        input=document,
        capture_output=True,
        text=True,
    )


def test_task_definition_and_service_only_is_confirmed():
    r = run(plan_json(
        ("aws_ecs_task_definition.bff", ["create"]),
        ("aws_ecs_service.bff", ["update"]),
    ))
    assert r.returncode == 0
    assert "aws_ecs_task_definition.bff" in r.stdout


def test_an_empty_plan_exits_three_not_zero():
    # The caller must distinguish "already deployed" from "apply this".
    r = run(plan_json())
    assert r.returncode == 3


def test_a_plan_of_only_no_ops_exits_three():
    r = run(plan_json(("aws_db_instance.main", ["no-op"])))
    assert r.returncode == 3


def test_an_rds_change_is_refused():
    r = run(plan_json(
        ("aws_ecs_task_definition.bff", ["create"]),
        ("aws_db_instance.main", ["update"]),
    ))
    assert r.returncode == 1
    assert "aws_db_instance.main" in r.stderr


def test_a_deletion_of_an_allowed_resource_is_still_refused():
    # Address membership is not enough: destroying the service is not a
    # promotion, even though the address is on the list.
    r = run(plan_json(("aws_ecs_service.bff", ["delete"])))
    assert r.returncode == 1
    assert "delete" in r.stderr


def test_an_address_that_merely_starts_with_an_allowed_one_is_refused():
    # aws_ecs_service.bff_canary must not pass as aws_ecs_service.bff.
    r = run(plan_json(("aws_ecs_service.bff_canary", ["update"])))
    assert r.returncode == 1
    assert "bff_canary" in r.stderr


def test_no_allowed_addresses_given_is_a_malformed_invocation():
    r = subprocess.run(
        [BASH, str(SCRIPT)], input=plan_json(), capture_output=True, text=True
    )
    assert r.returncode == 1
    assert "at least one" in r.stderr


def test_malformed_json_is_refused_rather_than_read_as_empty():
    # The dangerous failure: unparseable input yielding an empty change list,
    # which would look exactly like "nothing to do" and let an apply proceed.
    r = run("{not json")
    assert r.returncode == 1
    assert r.returncode != 3


def test_empty_stdin_is_refused_rather_than_read_as_empty():
    r = run("")
    assert r.returncode == 1


def test_json_without_a_resource_changes_key_is_refused():
    r = run(json.dumps({"format_version": "1.2"}))
    assert r.returncode == 1


def test_no_generated_value_is_ever_printed():
    # A plan carries generated values. Only addresses may be echoed.
    document = json.dumps(
        {
            "resource_changes": [
                {
                    "address": "aws_ecs_task_definition.bff",
                    "change": {
                        "actions": ["create"],
                        "after": {"secret_token": "s3cret-generated-value"},
                    },
                }
            ]
        }
    )
    r = run(document)
    assert r.returncode == 0
    assert "s3cret-generated-value" not in r.stdout
    assert "s3cret-generated-value" not in r.stderr
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd stacks/apicurio && pytest qe/tests/release/test_assert_app_only_plan_sh.py -v`
Expected: every test FAILS with exit 127.

- [ ] **Step 3: Write the minimal implementation**

```bash
#!/usr/bin/env bash
# Prove a Terraform plan touches only the application layer.
#
# Usage:
#   terraform show -json tf.plan | assert-app-only-plan.sh <allowed-address>...
#
# Exit codes, because "nothing to do" and "confirmed" need different handling:
#   0  app-only confirmed -- apply the plan
#   3  the plan is empty -- nothing to apply, and that is not a failure
#   1  the plan touches something else, or the input was unusable
#
# WHY STDIN. A saved plan contains every generated value, so `terraform show
# -json` must never reach a log or an artifact. Piping it straight in means the
# document exists only in memory, and only resource ADDRESSES are ever printed.
#
# WHY AN UNPARSEABLE DOCUMENT IS EXIT 1 AND NOT EXIT 3. An empty change list
# and a document jq could not read look identical downstream -- both yield no
# addresses. Treating the second as "nothing to do" would let an apply proceed
# on the strength of a parse failure, so the two are told apart explicitly.
#
# WHY ACTIONS ARE CHECKED, NOT JUST ADDRESSES. Destroying aws_ecs_service.bff
# is not a promotion, even though its address is on the allowed list.
set -euo pipefail

die() { printf '%s: %s\n' "assert-app-only-plan.sh" "$1" >&2; exit 1; }

[ "$#" -ge 1 ] || die "needs at least one allowed resource address. Usage: terraform show -json tf.plan | assert-app-only-plan.sh <address>..."

document="$(cat)"
[ -n "$document" ] || die "no plan JSON on stdin. Pipe 'terraform show -json tf.plan' into this script."

# Refuse a document jq cannot read, and one with no resource_changes key at
# all -- both would otherwise present as an empty plan.
printf '%s' "$document" | jq -e 'type == "object" and has("resource_changes")' >/dev/null 2>&1 \
  || die "stdin is not a Terraform plan document with a resource_changes array. Refusing, because an unreadable plan and an empty plan are indistinguishable downstream."

# Only the action verbs a promotion may perform. A create or update of the task
# definition and service is a new revision rolling out; a delete or replace is
# not.
allowed_actions='["create"],["update"],["no-op"]'

# address<TAB>actions, one per line, for every change that is not a no-op.
changed="$(
  printf '%s' "$document" \
    | jq -r '.resource_changes[]
             | select(.change.actions != ["no-op"])
             | .address + "\t" + (.change.actions | join(","))' \
    || true
)"

if [ -z "$changed" ]; then
  printf 'Plan is empty — the running task definition already has this digest.\n'
  exit 3
fi

printf 'Plan changes:\n'
unexpected=""
while IFS="$(printf '\t')" read -r address actions; do
  [ -n "$address" ] || continue
  printf '  %s (%s)\n' "$address" "$actions"

  # Exact address match. A `case` glob would let aws_ecs_service.bff_canary
  # pass as aws_ecs_service.bff.
  address_ok="no"
  for allowed in "$@"; do
    if [ "$address" = "$allowed" ]; then
      address_ok="yes"
      break
    fi
  done

  action_ok="no"
  case ",${allowed_actions}," in
    *",[\"${actions}\"],"*) action_ok="yes" ;;
  esac
  # join(",") produces "create" or "delete,create"; only single safe verbs pass.
  case "$actions" in
    create|update|no-op) action_ok="yes" ;;
    *) action_ok="no" ;;
  esac

  if [ "$address_ok" != "yes" ] || [ "$action_ok" != "yes" ]; then
    unexpected="${unexpected} ${address}(${actions})"
  fi
done <<EOF
${changed}
EOF

if [ -n "$unexpected" ]; then
  die "this plan is not an app-only deploy and will NOT be applied unattended:${unexpected}. That is either real infrastructure work or drift introduced out of band, and both need a human reading the plan. Re-run with the infra latch on 'plan' to read it, then 'apply' to apply it deliberately."
fi

printf 'App-only deploy confirmed — nothing but the listed application resources change.\n'
exit 0
```

- [ ] **Step 4: Run the tests**

Run:
```bash
chmod +x stacks/apicurio/scripts/assert-app-only-plan.sh
cd stacks/apicurio && pytest qe/tests/release/test_assert_app_only_plan_sh.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Remove the dead `allowed_actions` branch**

The implementation above contains two action checks — a `case` on `,${allowed_actions},` and a second `case` on `$actions`. The first is dead: the second overwrites `action_ok` unconditionally. Delete the `allowed_actions` variable and its `case` block, then re-run the tests. They must still all pass; if any test only passed because of the dead branch, that test was asserting nothing.

- [ ] **Step 6: Mutation-test the guard**

1. Change `exit 3` on the empty path to `exit 0` → `test_an_empty_plan_exits_three_not_zero` must fail.
2. Remove the `jq -e ... has("resource_changes")` check → `test_malformed_json_is_refused_rather_than_read_as_empty` must fail.
3. Change the exact address comparison to `case "$address" in "$allowed"*)` → `test_an_address_that_merely_starts_with_an_allowed_one_is_refused` must fail.
4. Drop the action check (set `action_ok="yes"` always) → `test_a_deletion_of_an_allowed_resource_is_still_refused` must fail.
5. Change the `jq -r` selector to emit `.change.after` as well → `test_no_generated_value_is_ever_printed` must fail.

- [ ] **Step 7: Lint and commit**

```bash
cd stacks/apicurio && ruff check qe/tests/release/test_assert_app_only_plan_sh.py
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/scripts/assert-app-only-plan.sh \
        stacks/apicurio/qe/tests/release/test_assert_app_only_plan_sh.py
git commit -F - <<'MSG'
Prove a promotion's plan touches only the application layer

A promotion may move the BFF task definition and service and nothing else.
deploy-apicurio-dev.yml already proves this inline; this is the same argument
as a tested script, so the promote workflow does not carry a second untested
copy of it.

Two properties the inline version leaves implicit are explicit here. An
unparseable plan document exits 1, not 3: an empty change list and a parse
failure both yield no addresses, and treating the second as "nothing to do"
would let an apply proceed on the strength of a broken read. And the action
verbs are checked, not just the addresses -- destroying aws_ecs_service.bff is
not a promotion even though its address is on the list.

Reads the document on stdin and prints only resource addresses, so a plan's
generated values never reach a log or an artifact.

UIFPE-486 #comment Add assert-app-only-plan.sh with 11 unit tests and five mutations verified; refuses unparseable plans, destructive actions and prefix-matching addresses.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 3: `next-release-tag.sh` — name the release tag

The release tag is the evidence a version ran somewhere and passed. It is `release/<env>/<date>.<n>`, where `<n>` counts same-day releases to that environment.

**Files:**
- Create: `stacks/apicurio/scripts/next-release-tag.sh`
- Test: `stacks/apicurio/qe/tests/release/test_next_release_tag_sh.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `next-release-tag.sh <env> [<date>]` prints one tag name, e.g. `release/uat/2026-09-21.1`. `<date>` defaults to today in UTC. Exits 1 for an environment outside `{dev,uat,prod}` or a malformed date. Must be run inside a git work tree.

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for scripts/next-release-tag.sh.

The release tag records that a version ran in an environment and passed, so
two releases must never collide on one name and the counter must not reset
while same-day tags exist. The interesting cases are all ordering: 9 -> 10
must not sort as 1 -> 2, and another environment's tags must not advance this
environment's counter.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = STACK_ROOT / "scripts" / "next-release-tag.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(
    BASH is None or GIT is None, reason="needs bash and git on PATH"
)

DATE = "2026-09-21"


@pytest.fixture
def repo(tmp_path):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.email", "t@e.st"), ("user.name", "Test")):
        subprocess.run([GIT, "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run([GIT, "-C", str(tmp_path), "add", "f"], check=True)
    subprocess.run(
        [GIT, "-C", str(tmp_path), "commit", "-qm", "init"], check=True
    )
    return tmp_path


def tag(repo, name):
    subprocess.run([GIT, "-C", str(repo), "tag", name], check=True)


def run(repo, *args):
    return subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def test_first_release_of_the_day_is_one(repo):
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_second_release_of_the_day_is_two(repo):
    tag(repo, f"release/uat/{DATE}.1")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.2"


def test_counter_is_numeric_not_lexical(repo):
    # The bug this catches: sorting as text gives .10 < .9, so the next tag
    # would be .10 again and collide.
    for n in range(1, 10):
        tag(repo, f"release/uat/{DATE}.{n}")
    tag(repo, f"release/uat/{DATE}.10")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.11"


def test_another_environment_does_not_advance_this_counter(repo):
    tag(repo, f"release/dev/{DATE}.1")
    tag(repo, f"release/dev/{DATE}.2")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_another_date_does_not_advance_this_counter(repo):
    tag(repo, "release/uat/2026-09-20.7")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_a_build_tag_does_not_advance_the_counter(repo):
    tag(repo, "apicurio/v0.1.1")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.1"


def test_a_gap_in_the_sequence_still_advances_past_the_highest(repo):
    # Deleting .2 must not hand out .2 again -- the tag is evidence, and
    # reusing a name makes two different releases share one identity.
    tag(repo, f"release/uat/{DATE}.1")
    tag(repo, f"release/uat/{DATE}.3")
    r = run(repo, "uat", DATE)
    assert r.returncode == 0
    assert r.stdout.strip() == f"release/uat/{DATE}.4"


def test_an_unknown_environment_is_refused(repo):
    r = run(repo, "staging", DATE)
    assert r.returncode == 1
    assert "staging" in r.stderr


def test_a_malformed_date_is_refused(repo):
    r = run(repo, "uat", "21-09-2026")
    assert r.returncode == 1


def test_no_environment_argument_is_refused(repo):
    r = run(repo)
    assert r.returncode == 1


def test_outside_a_git_work_tree_is_refused(tmp_path):
    r = subprocess.run(
        [BASH, str(SCRIPT), "uat", DATE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "git" in r.stderr.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd stacks/apicurio && pytest qe/tests/release/test_next_release_tag_sh.py -v`
Expected: every test FAILS with exit 127.

- [ ] **Step 3: Write the minimal implementation**

```bash
#!/usr/bin/env bash
# Print the next release tag for an environment: release/<env>/<date>.<n>
#
# Usage:
#   next-release-tag.sh <env> [<date>]
#
# <date> defaults to today in UTC. The counter is the highest existing <n> for
# that environment and date, plus one.
#
# WHY THE HIGHEST AND NOT THE COUNT. If release/uat/<date>.2 is deleted, a
# count would hand out .2 again and two different releases would share one
# identity. The tag is evidence; a reused name destroys it.
#
# WHY NUMERIC SORT. Lexically, .10 sorts below .9, so a text sort would hand
# out .10 a second time. `sort -n` on the suffix alone avoids version-sort
# portability differences between BSD and GNU sort.
set -euo pipefail

die() { printf '%s: %s\n' "next-release-tag.sh" "$1" >&2; exit 1; }

env_name="${1:-}"
[ -n "$env_name" ] || die "needs an environment. Usage: next-release-tag.sh <env> [<date>]"

case "$env_name" in
  dev|uat|prod) ;;
  *) die "'${env_name}' is not a known environment. The vocabulary is closed: dev, uat, prod." ;;
esac

date_arg="${2:-$(date -u +%Y-%m-%d)}"
printf '%s' "$date_arg" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
  || die "'${date_arg}' is not a date of the form YYYY-MM-DD."

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git work tree, so existing release tags cannot be read."

prefix="release/${env_name}/${date_arg}."

# `|| true` because a failing grep under `set -e` with pipefail would kill the
# script silently -- the exact failure mode read-manifest.sh was bitten by.
highest="$(
  git tag -l "${prefix}*" \
    | sed -n "s|^${prefix}\([0-9][0-9]*\)$|\1|p" \
    | sort -n \
    | tail -1 \
    || true
)"

if [ -z "$highest" ]; then
  next=1
else
  next=$((highest + 1))
fi

printf '%s%s\n' "$prefix" "$next"
```

- [ ] **Step 4: Run the tests**

Run:
```bash
chmod +x stacks/apicurio/scripts/next-release-tag.sh
cd stacks/apicurio && pytest qe/tests/release/test_next_release_tag_sh.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Mutation-test the guard**

1. Replace `sort -n | tail -1` with `sort | tail -1` → `test_counter_is_numeric_not_lexical` must fail.
2. Replace the highest-plus-one with a count (`git tag -l "${prefix}*" | wc -l`) → `test_a_gap_in_the_sequence_still_advances_past_the_highest` must fail.
3. Loosen the `sed` pattern to `s|^${prefix}\(.*\)$|\1|p` → add a tag `release/uat/<date>.1-rc` and confirm a test fails; if none does, add one.
4. Drop the `case` on `env_name` → `test_an_unknown_environment_is_refused` must fail.
5. Drop the `is-inside-work-tree` check → `test_outside_a_git_work_tree_is_refused` must fail.

- [ ] **Step 6: Lint and commit**

```bash
cd stacks/apicurio && ruff check qe/tests/release/test_next_release_tag_sh.py
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/scripts/next-release-tag.sh \
        stacks/apicurio/qe/tests/release/test_next_release_tag_sh.py
git commit -F - <<'MSG'
Name the release tag from the highest existing one, not a count

release/<env>/<date>.<n> records that a version ran in an environment and
passed, so two releases must never share a name. Two ways to get that wrong
are both guarded here: counting existing tags hands out .2 again after .2 is
deleted, and sorting them as text hands out .10 twice because .10 sorts below
.9.

Another environment's tags and another date's tags do not advance this
counter, and a build tag never does.

UIFPE-486 #comment Add next-release-tag.sh with 11 unit tests and five mutations verified; counter is the highest existing suffix plus one, sorted numerically.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 4: `deploy-apicurio-infra.yml` — converge infrastructure per environment

**Files:**
- Create: `.github/workflows/deploy-apicurio-infra.yml`

**Interfaces:**
- Consumes: `resolve-deploy-mode.sh` (Task 1), `read-manifest.sh` (already on `develop`).
- Produces: a deployed stack in the target environment. No outputs other tasks consume.

- [ ] **Step 1: Write the workflow**

```yaml
name: Deploy apicurio — infrastructure

# Converges the Apicurio stack's INFRASTRUCTURE in one environment. It does not
# promote application artifacts -- promote-apicurio.yml does that. The split
# follows a real asymmetry: an application artifact promotes (copy the bytes,
# prove the digest did not change), while infrastructure converges (apply the
# same Terraform root with that environment's tfvars). There is no infra
# artifact to carry.
#
# IDENTITY. Every run resolves its image digests from the pushed build tag's
# release manifest, so even a full apply lands a known application version
# rather than whatever happened to be latest. `latest` is never resolved here.
#
# WHY `push: tags` AND NOT workflow_dispatch. Two constraints, both verified:
#   1. main is ~1,019 commits behind develop and carries 2 of 29 workflows.
#      workflow_dispatch only fires for workflows on the default branch, so a
#      dispatch-driven design cannot run at all today. (deploy-apicurio-dev.yml
#      has the same dead dispatch block, and its own comment says so.)
#   2. The uat GitHub Environment accepts tag refs matching apicurio/v* only.
#      A dispatch run on develop targeting environment: uat is rejected.
# A push-triggered run uses the workflow file from the PUSHED REF, so this
# needs nothing on main. The dispatch block below is inert until main catches
# up, and costs nothing meanwhile.
#
# THE HUMAN GATE is the uat Environment's required reviewer, which blocks the
# job before it starts. Approving a deployment therefore approves the INTENT --
# this tag, this environment -- not the plan text. To read the plan first, run
# with the latch on `plan`, read it in the log, then set `apply` and re-run.
# The tag is immutable, so the second run deploys the same identity.
#
# REPO VARIABLES (Settings -> Secrets and variables -> Actions -> Variables)
#   APICURIO_UAT_INFRA   unset|skip = do nothing (DEFAULT)
#                        plan       = plan only, changes nothing
#                        apply      = apply the plan this run produced
#                        bootstrap  = FIRST APPLY ONLY: create the ECR
#                                     repository, then apply everything
#   APICURIO_PROD_INFRA  same vocabulary, for prod
#
# Leave these unset outside a deploy window. They are a deploy-window latch,
# not configuration -- the same discipline BEACON_INFRA and GOLD_INFRA
# document.

on:
  push:
    tags:
      - "apicurio/v*"
  workflow_dispatch:
    inputs:
      environment:
        description: "Target environment"
        type: choice
        options: [uat, prod]
        default: uat
      infra:
        description: "Terraform action"
        type: choice
        options: [plan, apply, bootstrap, skip]
        default: plan

permissions:
  contents: read

env:
  AWS_REGION: eu-west-1

jobs:
  resolve:
    name: What this run will do
    runs-on: ubuntu-latest
    outputs:
      environment: ${{ steps.mode.outputs.environment }}
      infra: ${{ steps.mode.outputs.infra }}
      bff_digest: ${{ steps.manifest.outputs.bff_digest }}
      registry_digest: ${{ steps.manifest.outputs.registry_digest }}
    steps:
      # fetch-depth 0 and fetch-tags: read-manifest.sh reads the ANNOTATED TAG
      # message, which a shallow clone does not carry.
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - id: mode
        env:
          EVENT: ${{ github.event_name }}
          IN_ENV: ${{ inputs.environment }}
          IN_INFRA: ${{ inputs.infra }}
          VAR_UAT: ${{ vars.APICURIO_UAT_INFRA }}
          VAR_PROD: ${{ vars.APICURIO_PROD_INFRA }}
        run: |
          set -euo pipefail
          resolver=stacks/apicurio/scripts/resolve-deploy-mode.sh

          # A push carries no environment input. uat is the only environment a
          # tag push targets today; prod is reached by dispatch once main
          # carries this file, or by setting APICURIO_PROD_INFRA and dispatching.
          environment="$("$resolver" \
            --event "$EVENT" --input "${IN_ENV:-}" --var "uat" \
            --allowed "uat,prod" --default "uat")"

          case "$environment" in
            uat)  var_value="${VAR_UAT:-}" ;;
            prod) var_value="${VAR_PROD:-}" ;;
          esac

          infra="$("$resolver" \
            --event "$EVENT" --input "${IN_INFRA:-}" --var "$var_value" \
            --allowed "skip,plan,apply,bootstrap" --default "skip")"

          echo "environment=$environment" >> "$GITHUB_OUTPUT"
          echo "infra=$infra" >> "$GITHUB_OUTPUT"
          echo "::notice::environment=$environment infra=$infra (tag ${GITHUB_REF_NAME})"

          if [ "$infra" = "skip" ]; then
            echo "::notice::The infra latch is skip, so this run changes nothing. Set APICURIO_${environment}_INFRA to plan, apply or bootstrap to open a deploy window."
          fi

      # Read the digests even on a skip run: it costs one script call and makes
      # the log say what WOULD have been deployed.
      - id: manifest
        env:
          TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          reader=stacks/apicurio/scripts/read-manifest.sh

          # read-manifest.sh refuses any field from a manifest whose gate did
          # not pass, and refuses a truncated, uppercase or mutable digest. So
          # a value that comes back at all is already the right shape.
          bff="$("$reader" "$TAG" bff.digest)"
          registry="$("$reader" "$TAG" registry.digest)"

          echo "bff_digest=$bff" >> "$GITHUB_OUTPUT"
          echo "registry_digest=$registry" >> "$GITHUB_OUTPUT"
          echo "::notice::bff=$bff registry=$registry"

  # ONE-TIME BOOTSTRAP, and deliberately narrow. `terraform apply -target=` is
  # an escape hatch: a targeted apply skips the rest of the configuration, so
  # the resulting state does not correspond to the whole config. It is
  # acceptable here for the same three reasons deploy-apicurio-dev.yml gives:
  #   1. aws_ecr_repository.bff derives only from var.env, so it has no
  #      dependents and no dependencies -- nothing else is half-created.
  #   2. The guard below makes it a no-op the moment the repository exists.
  #   3. The terraform job immediately reconciles everything else.
  # This is NOT a pattern to reuse for ordinary changes.
  #
  # It exists because promote-apicurio.yml must push an image somewhere, and
  # Terraform is what creates the repository.
  ecr:
    name: Bootstrap the ECR repository (${{ needs.resolve.outputs.environment }})
    needs: resolve
    if: needs.resolve.outputs.infra == 'bootstrap'
    runs-on: ubuntu-latest
    environment: ${{ needs.resolve.outputs.environment }}
    permissions:
      contents: read
      id-token: write
    defaults:
      run:
        working-directory: stacks/apicurio/infrastructure/terraform
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: eu-west-1

      - uses: hashicorp/setup-terraform@v3
        with:
          # Same pin as deploy-apicurio-dev.yml and stacks-ci.yml. A saved
          # plan is only valid for the version that wrote it, so this must
          # not drift from the workflow that applies alongside it.
          terraform_version: "1.15.8"
          terraform_wrapper: false

      - name: Create the BFF repository if it does not exist
        env:
          ENV_NAME: ${{ needs.resolve.outputs.environment }}
          REGISTRY_DIGEST: ${{ needs.resolve.outputs.registry_digest }}
          BFF_DIGEST: ${{ needs.resolve.outputs.bff_digest }}
        run: |
          set -euo pipefail
          repo="uif-apicurio-${ENV_NAME}-bff-ecr-repo"
          if aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
            echo "$repo already exists — no bootstrap needed."
            exit 0
          fi
          echo "::notice::$repo does not exist — bootstrapping just that resource."
          terraform init -reconfigure -input=false \
            -backend-config="backends/${ENV_NAME}.hcl"
          terraform apply -input=false -auto-approve \
            -var-file="envs/${ENV_NAME}.tfvars" \
            -var "bff_image_digest=${BFF_DIGEST}" \
            -var "registry_image_digest=${REGISTRY_DIGEST}" \
            -target=aws_ecr_repository.bff

  terraform:
    name: Terraform (${{ needs.resolve.outputs.environment }}, ${{ needs.resolve.outputs.infra }})
    needs: [resolve, ecr]
    # `always()` so a skipped ecr job does not skip this one, but a FAILED ecr
    # job does. Without it, `needs: ecr` makes every non-bootstrap run skip.
    if: >-
      always()
      && needs.resolve.result == 'success'
      && needs.ecr.result != 'failure'
      && needs.ecr.result != 'cancelled'
      && needs.resolve.outputs.infra != 'skip'
    runs-on: ubuntu-latest
    environment: ${{ needs.resolve.outputs.environment }}
    permissions:
      contents: read
      id-token: write
    # One deploy at a time per environment, so an infra apply and a promote
    # cannot interleave on one state file. Never cancel in progress: a
    # cancelled apply can leave state locked.
    concurrency:
      group: apicurio-deploy-${{ needs.resolve.outputs.environment }}
      cancel-in-progress: false
    defaults:
      run:
        working-directory: stacks/apicurio/infrastructure/terraform
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: eu-west-1

      - uses: hashicorp/setup-terraform@v3
        with:
          # Same pin as deploy-apicurio-dev.yml and stacks-ci.yml. A saved
          # plan is only valid for the version that wrote it, so this must
          # not drift from the workflow that applies alongside it.
          terraform_version: "1.15.8"
          terraform_wrapper: false

      # -reconfigure: with `backend "s3" {}` plus -backend-config, init would
      # otherwise try to migrate state between differently-configured backends.
      - name: Init
        env:
          ENV_NAME: ${{ needs.resolve.outputs.environment }}
        run: |
          set -euo pipefail
          terraform init -reconfigure -input=false \
            -backend-config="backends/${ENV_NAME}.hcl"

      - name: Plan
        env:
          ENV_NAME: ${{ needs.resolve.outputs.environment }}
          BFF_DIGEST: ${{ needs.resolve.outputs.bff_digest }}
          REGISTRY_DIGEST: ${{ needs.resolve.outputs.registry_digest }}
        run: |
          set -euo pipefail
          terraform plan -input=false \
            -var-file="envs/${ENV_NAME}.tfvars" \
            -var "bff_image_digest=${BFF_DIGEST}" \
            -var "registry_image_digest=${REGISTRY_DIGEST}" \
            -out=tf.plan

      # THREE CONSTRAINTS, all load-bearing and all inherited from
      # deploy-apicurio-dev.yml:
      #  1. Plan and apply are in the SAME job -- same runner, same filesystem,
      #     nothing persisted. A saved plan is only valid for the Terraform
      #     version that wrote it, and it must not leave the runner.
      #  2. tf.plan is NEVER uploaded as an artifact, and `terraform show -json`
      #     never reaches a log or an artifact. A saved plan contains every
      #     generated value; grep finds nothing in it only because it is
      #     compressed. RDS-managed credentials keep the DB password out, but
      #     authorizer.tf still generates values.
      #  3. No -auto-approve: not a valid pairing with a plan file, and not
      #     needed, because Terraform does not prompt when handed one.
      - name: Apply the reviewed plan
        if: needs.resolve.outputs.infra == 'apply' || needs.resolve.outputs.infra == 'bootstrap'
        run: terraform apply -input=false tf.plan

      - name: Report the stack's URLs
        if: needs.resolve.outputs.infra == 'apply' || needs.resolve.outputs.infra == 'bootstrap'
        env:
          ENV_NAME: ${{ needs.resolve.outputs.environment }}
        run: |
          set -euo pipefail
          api="$(terraform output -raw bff_api_url 2>/dev/null || echo "")"
          ui="$(terraform output -raw ui_url 2>/dev/null || echo "")"
          echo "### apicurio ${ENV_NAME}" >> "$GITHUB_STEP_SUMMARY"
          echo "- API: ${api:-<none>}" >> "$GITHUB_STEP_SUMMARY"
          echo "- UI:  ${ui:-<none>}" >> "$GITHUB_STEP_SUMMARY"

          # The first apply of any environment cannot know its own CloudFront
          # domain, because the distribution does not exist until this apply
          # has run -- and the BFF only allows origins it was configured with.
          if [ -n "$ui" ] && ! grep -q "$ui" "envs/${ENV_NAME}.tfvars"; then
            echo "::notice::cors_origins in envs/${ENV_NAME}.tfvars does not name $ui yet. Add it and apply once more, or the UI cannot call the API. This is the documented second pass."
          fi
```

- [ ] **Step 2: Verify the workflow lints**

Run:
```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
actionlint -shellcheck= -pyflakes= .github/workflows/deploy-apicurio-infra.yml
```
Expected: no output. Install actionlint first if absent, using the pinned version and checksum from `.github/workflows/workflow-lint.yml`.

- [ ] **Step 3: Verify every `uses:` is on the enterprise allow-list**

Run:
```bash
grep -hE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[^[:space:]]' \
  .github/workflows/deploy-apicurio-infra.yml \
  | sed -E 's/.*uses:[[:space:]]*//' | cut -d@ -f1 | cut -d/ -f1 | sort -u
```
Expected exactly: `actions`, `aws-actions`, `hashicorp`. Any other owner fails the whole workflow at startup with no logs.

- [ ] **Step 4: Confirm `AWS_ROLE_ARN` resolves per environment**

The workflow uses `secrets.AWS_ROLE_ARN`, not `secrets.AWS_ROLE_ARN_UAT`, so the same file works for every environment. That requires an **environment-scoped** secret named `AWS_ROLE_ARN` on each environment. Verify and create:

```bash
gh secret list --env uat
# If AWS_ROLE_ARN is absent, mirror the repo-level value:
gh secret set AWS_ROLE_ARN --env uat \
  --body "arn:aws:iam::283556840805:role/uif-apicurio-uat-deploy-role"
gh secret list --env uat
```
Expected: `AWS_ROLE_ARN` listed against the `uat` environment. The repo-level `AWS_ROLE_ARN_UAT` stays as it is — `deploy-product-uat.yml` uses it.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy-apicurio-infra.yml
git commit -F - <<'MSG'
Add an infrastructure workflow that can target any environment

Apicurio could be deployed to dev and nowhere else. This converges the
Terraform root in one environment, selected by data -- backends/<env>.hcl and
envs/<env>.tfvars -- because the root is already environment-agnostic and
differs between dev and uat in four lines.

Triggered by a build-tag push rather than workflow_dispatch, for two verified
reasons: main is ~1,019 commits behind develop so dispatch cannot fire at all,
and the uat Environment accepts tag refs only. A push-triggered run uses the
workflow file from the pushed ref, so this needs nothing on main. uat's
required reviewer is the human gate.

Digests come from the pushed tag's release manifest, so even a full apply lands
a known application version. Plan and apply stay in one job and tf.plan is
never uploaded -- a saved plan carries generated values in clear.

The bootstrap path creates only aws_ecr_repository.bff, guarded so it is a
no-op once the repository exists, because promotion needs somewhere to push
and Terraform is what creates it.

UIFPE-486 #comment Add deploy-apicurio-infra.yml: per-environment Terraform convergence, digests resolved from the tag manifest, ECR bootstrap guarded, plan and apply in one job.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 5: `promote-apicurio.yml` — promote the artifacts and cut the release tag

**Files:**
- Create: `.github/workflows/promote-apicurio.yml`

**Interfaces:**
- Consumes: `resolve-deploy-mode.sh` (Task 1), `assert-app-only-plan.sh` (Task 2), `next-release-tag.sh` (Task 3), plus `read-manifest.sh`, `copy-image.sh` and `restore-bundle.sh` already on `develop`.
- Produces: a `release/<env>/<date>.<n>` tag, pushed only on green smoke.

- [ ] **Step 1: Write the workflow**

```yaml
name: Promote apicurio

# Promotes a build tag's ARTIFACTS into one environment and records the result
# as a release tag. It does not converge infrastructure --
# deploy-apicurio-infra.yml does that.
#
# THE TWO-TAG MODEL. A build tag (apicurio/vX.Y.Z) says what code went in; its
# evidence is the CI gate. A release tag (release/<env>/<date>.<n>) says what
# was deployed where; its evidence is smoke. Building never implies deploying,
# so the release tag is cut only on green smoke and never speculatively.
#
# WHY COPY RATHER THAN REBUILD. The image digest and bundle hash in the build
# tag's manifest are the bytes that passed the gate. Rebuilding would produce
# different bytes -- the UI bundle is reproducible but the Docker image is not
# -- so promotion copies and then proves the digest did not change.
#
# REPO VARIABLES
#   APICURIO_UAT_PROMOTE   unset|skip = do nothing (DEFAULT)
#                          run        = promote this tag into uat
#   APICURIO_PROD_PROMOTE  same vocabulary, for prod

on:
  push:
    tags:
      - "apicurio/v*"
  workflow_dispatch:
    inputs:
      environment:
        description: "Target environment"
        type: choice
        options: [uat, prod]
        default: uat
      promote:
        description: "Promote this tag's artifacts"
        type: choice
        options: [run, skip]
        default: skip

permissions:
  contents: read

env:
  AWS_REGION: eu-west-1

jobs:
  resolve:
    name: What this run will do
    runs-on: ubuntu-latest
    outputs:
      environment: ${{ steps.mode.outputs.environment }}
      promote: ${{ steps.mode.outputs.promote }}
    steps:
      - uses: actions/checkout@v4

      - id: mode
        env:
          EVENT: ${{ github.event_name }}
          IN_ENV: ${{ inputs.environment }}
          IN_PROMOTE: ${{ inputs.promote }}
          VAR_UAT: ${{ vars.APICURIO_UAT_PROMOTE }}
          VAR_PROD: ${{ vars.APICURIO_PROD_PROMOTE }}
        run: |
          set -euo pipefail
          resolver=stacks/apicurio/scripts/resolve-deploy-mode.sh

          environment="$("$resolver" \
            --event "$EVENT" --input "${IN_ENV:-}" --var "uat" \
            --allowed "uat,prod" --default "uat")"

          case "$environment" in
            uat)  var_value="${VAR_UAT:-}" ;;
            prod) var_value="${VAR_PROD:-}" ;;
          esac

          promote="$("$resolver" \
            --event "$EVENT" --input "${IN_PROMOTE:-}" --var "$var_value" \
            --allowed "skip,run" --default "skip")"

          echo "environment=$environment" >> "$GITHUB_OUTPUT"
          echo "promote=$promote" >> "$GITHUB_OUTPUT"
          echo "::notice::environment=$environment promote=$promote (tag ${GITHUB_REF_NAME})"

  promote:
    name: Promote ${{ github.ref_name }} into ${{ needs.resolve.outputs.environment }}
    needs: resolve
    if: needs.resolve.outputs.promote == 'run'
    runs-on: ubuntu-latest
    environment: ${{ needs.resolve.outputs.environment }}
    permissions:
      contents: write
      id-token: write
    # Shares the infra workflow's group name on purpose: a promote and an infra
    # apply must never interleave on one state file.
    concurrency:
      group: apicurio-deploy-${{ needs.resolve.outputs.environment }}
      cancel-in-progress: false
    env:
      ENV_NAME: ${{ needs.resolve.outputs.environment }}
      TAG: ${{ github.ref_name }}
    steps:
      # fetch-depth 0: read-manifest.sh reads the annotated tag message, and
      # next-release-tag.sh reads existing release tags.
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: eu-west-1

      - uses: hashicorp/setup-terraform@v3
        with:
          # Same pin as deploy-apicurio-dev.yml and stacks-ci.yml. A saved
          # plan is only valid for the version that wrote it, so this must
          # not drift from the workflow that applies alongside it.
          terraform_version: "1.15.8"
          terraform_wrapper: false

      # Every input comes from the manifest, which refuses to yield any field
      # at all if the build tag's gate did not pass. So a promotion of a tag
      # that never passed CI stops here rather than at a later assertion --
      # which is exactly why each field is assigned to a variable BEFORE it is
      # written to GITHUB_OUTPUT. Under `set -e`, a failing command
      # substitution used as an argument to `echo` does not abort the script:
      # `echo` itself still succeeds, and its exit status is what `-e`
      # observes, so the inner failure is swallowed. A bare assignment
      # (`v="$(cmd)"`) propagates the failure correctly. Do not "simplify"
      # this back into `echo "field=$("$reader" ...)"`.
      - name: Read the release manifest
        id: manifest
        run: |
          set -euo pipefail
          reader=stacks/apicurio/scripts/read-manifest.sh

          commit="$("$reader" "$TAG" git_commit)"
          bff_repository="$("$reader" "$TAG" bff.repository)"
          bff_digest="$("$reader" "$TAG" bff.digest)"
          registry_digest="$("$reader" "$TAG" registry.digest)"
          ui_sha256="$("$reader" "$TAG" ui.sha256)"
          ui_archive="$("$reader" "$TAG" ui.archive)"

          {
            echo "commit=$commit"
            echo "bff_repository=$bff_repository"
            echo "bff_digest=$bff_digest"
            echo "registry_digest=$registry_digest"
            echo "ui_sha256=$ui_sha256"
            echo "ui_archive=$ui_archive"
          } >> "$GITHUB_OUTPUT"

      - name: Install crane
        env:
          CRANE_VERSION: v0.20.2
        run: |
          set -euo pipefail
          # go-containerregistry is not an allow-listed Actions owner, so crane
          # is fetched as a binary rather than via its setup action.
          curl -fsSL -o /tmp/crane.tgz \
            "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz"
          tar -xzf /tmp/crane.tgz -C /tmp crane
          sudo mv /tmp/crane /usr/local/bin/crane
          crane version

      - name: Log in to both ECR registries
        run: |
          set -euo pipefail
          account="$(aws sts get-caller-identity --query Account --output text)"
          aws ecr get-login-password --region "$AWS_REGION" \
            | crane auth login --username AWS --password-stdin \
              "${account}.dkr.ecr.${AWS_REGION}.amazonaws.com"

      # copy-image.sh is manifest-verbatim: it reads the manifest digest with
      # `crane digest`, checks the expected digest BEFORE copying, and reads
      # the destination digest back AFTER. `docker pull && tag && push` changes
      # the digest of a multi-architecture index; crane does not.
      - name: Copy the BFF image into this environment's registry
        env:
          SOURCE_REPO: ${{ steps.manifest.outputs.bff_repository }}
          EXPECTED_DIGEST: ${{ steps.manifest.outputs.bff_digest }}
        run: |
          set -euo pipefail
          account="$(aws sts get-caller-identity --query Account --output text)"
          dest="${account}.dkr.ecr.${AWS_REGION}.amazonaws.com/uif-apicurio-${ENV_NAME}-bff-ecr-repo"
          # copy-image.sh takes POSITIONAL arguments:
          #   copy-image.sh <source-ref> <dest-ref> [expected-digest]
          stacks/apicurio/scripts/copy-image.sh \
            "${SOURCE_REPO}@${EXPECTED_DIGEST}" \
            "${dest}:${TAG##*/}" \
            "$EXPECTED_DIGEST"

      # The archive lives in the BUILD environment's release-artifacts bucket,
      # because release-apicurio.yml's artifacts job runs with environment: dev.
      # Copying it here gives this environment its own retrievable copy, and its
      # own version_id -- which differs from the manifest's by construction.
      # That is data, not drift.
      - name: Copy the UI archive into this environment's artifact store
        id: archive
        env:
          SOURCE_ARCHIVE: ${{ steps.manifest.outputs.ui_archive }}
          UI_SHA256: ${{ steps.manifest.outputs.ui_sha256 }}
        run: |
          set -euo pipefail
          dest="s3://uif-apicurio-${ENV_NAME}-release-artifacts/${UI_SHA256}.tar.gz"
          aws s3 cp "$SOURCE_ARCHIVE" "$dest"
          aws s3 cp "$dest" /tmp/bundle.tar.gz
          echo "local=/tmp/bundle.tar.gz" >> "$GITHUB_OUTPUT"
          echo "::notice::archive copied to $dest"

      # restore-bundle.sh removes the destination on ANY failure, refuses to
      # extract over an existing directory, refuses a config.json inside the
      # archive, and refuses absolute or parent-directory paths. It re-hashes
      # the extracted tree and refuses unless it matches ui.sha256.
      - name: Restore and verify the UI bundle
        env:
          LOCAL_ARCHIVE: ${{ steps.archive.outputs.local }}
          UI_SHA256: ${{ steps.manifest.outputs.ui_sha256 }}
        run: |
          set -euo pipefail
          # restore-bundle.sh takes POSITIONAL arguments:
          #   restore-bundle.sh <archive.tar.gz> <dest-dir> <expected-sha256>
          # It refuses to extract over an existing directory, so /tmp/dist must
          # not exist yet.
          stacks/apicurio/scripts/restore-bundle.sh \
            "$LOCAL_ARCHIVE" /tmp/dist "$UI_SHA256"

      - name: Init Terraform
        working-directory: stacks/apicurio/infrastructure/terraform
        run: |
          set -euo pipefail
          terraform init -reconfigure -input=false \
            -backend-config="backends/${ENV_NAME}.hcl"

      # Plan the FULL configuration, then prove it changes nothing but the BFF
      # task definition and service. No -target: a full plan that contains only
      # application changes already IS an app-only deploy, so proving the
      # precondition is better than narrowing the apply. Platform drift becomes
      # a stop that names itself, rather than a passenger inside an unrelated
      # apply.
      - name: Plan
        working-directory: stacks/apicurio/infrastructure/terraform
        env:
          BFF_DIGEST: ${{ steps.manifest.outputs.bff_digest }}
          REGISTRY_DIGEST: ${{ steps.manifest.outputs.registry_digest }}
        run: |
          set -euo pipefail
          terraform plan -input=false \
            -var-file="envs/${ENV_NAME}.tfvars" \
            -var "bff_image_digest=${BFF_DIGEST}" \
            -var "registry_image_digest=${REGISTRY_DIGEST}" \
            -out=tf.plan

      # `terraform show -json` is piped straight into the script and never
      # written to a file, a log or an artifact: a plan carries generated
      # values in clear, and only resource addresses may be printed.
      #
      # Exit 3 means the plan is empty -- this digest is already deployed, so
      # there is nothing to apply and that is not a failure.
      - name: Assert the plan touches only the application layer
        id: blast
        working-directory: stacks/apicurio/infrastructure/terraform
        run: |
          set -euo pipefail
          set +e
          terraform show -json tf.plan \
            | ../../../../stacks/apicurio/scripts/assert-app-only-plan.sh \
                aws_ecs_task_definition.bff aws_ecs_service.bff
          status=$?
          set -e
          case "$status" in
            0) echo "apply=true"  >> "$GITHUB_OUTPUT" ;;
            3) echo "apply=false" >> "$GITHUB_OUTPUT"
               echo "::notice::This digest is already deployed — nothing to apply." ;;
            *) exit "$status" ;;
          esac

      - name: Apply the reviewed plan
        if: steps.blast.outputs.apply == 'true'
        working-directory: stacks/apicurio/infrastructure/terraform
        run: terraform apply -input=false tf.plan

      # This environment's own config.json, never the source environment's.
      # The bundle is byte-identical across environments; the API URL is not,
      # so it is written at publish time rather than baked into the build.
      - name: Publish the UI bundle with this environment's config
        working-directory: stacks/apicurio/infrastructure/terraform
        run: |
          set -euo pipefail
          api="$(terraform output -raw bff_api_url 2>/dev/null || echo "")"
          bucket="$(terraform output -raw ui_bucket 2>/dev/null || echo "")"
          dist_id="$(terraform output -raw ui_distribution_id 2>/dev/null || echo "")"

          # Presence is not shape: an empty output would sync a UI that calls
          # its own origin for the API and fails in the browser, not here.
          for pair in "api=$api" "bucket=$bucket" "dist=$dist_id"; do
            [ -n "${pair#*=}" ] || { echo "::error::Terraform output ${pair%%=*} is empty — has this environment been applied?"; exit 1; }
          done

          printf '{\n  "apiUrl": "%s"\n}\n' "$api" > /tmp/dist/config.json

          # --delete: the bundle is content-hashed, so stale hashed assets
          # would accumulate forever otherwise.
          aws s3 sync /tmp/dist "s3://${bucket}" --delete

          # config.json is unhashed and its name never changes, so it is the
          # one file that must be invalidated by name along with the entry
          # points. Everything else is content-addressed.
          aws cloudfront create-invalidation --distribution-id "$dist_id" \
            --paths '/' '/index.html' '/config.json'

      # QE tier 2. The integration suite cannot run on a pull request -- the
      # ALBs are internal and a PR runner cannot reach them -- so this is the
      # only place it executes against this environment.
      # Read the endpoints tier 2 needs from state, once, rather than per step.
      - name: Resolve the deployed endpoints
        id: tf
        working-directory: stacks/apicurio/infrastructure/terraform
        run: |
          set -euo pipefail
          for o in bff_api_url ui_url authorizer_ci_header; do
            v="$(terraform output -raw "$o" 2>/dev/null || echo "")"
            [ -n "$v" ] || { echo "::error::Terraform output $o is empty — has ${ENV_NAME} been applied?"; exit 1; }
            echo "$o=$v" >> "$GITHUB_OUTPUT"
          done

      # `-e src/` rather than naming a module: the failure is a class, not one
      # package. Tier 1 cannot catch it, because qe/ci.sh runs pytest with
      # `--ignore=qe/tests/integration`, so these files are never imported
      # there. That stayed invisible until tier 2 first ran on 2026-09-20.
      - name: Install the integration suite's dependencies
        working-directory: stacks/apicurio
        run: |
          set -euo pipefail
          python -m pip install --quiet --upgrade pip
          python -m pip install --quiet -e src/
          python -m pip install --quiet 'pytest>=8.3' pytest-asyncio httpx

      - name: Smoke (QE tier 2)
        id: smoke
        working-directory: stacks/apicurio
        env:
          RUN_INTEGRATION_TESTS: "1"
          BFF_URL: ${{ steps.tf.outputs.bff_api_url }}
          UI_ORIGIN: ${{ steps.tf.outputs.ui_url }}
          APICURIO_CI_KEY: ${{ secrets.APICURIO_CI_KEY }}
          APICURIO_CI_HEADER: ${{ steps.tf.outputs.authorizer_ci_header }}
          # A runner is never IP-allowlisted, so the authorizer MUST deny an
          # uncredentialled request from here.
          EXPECT_AUTHORIZER_DENY: "1"
        run: |
          set -euo pipefail
          if [ -z "${APICURIO_CI_KEY:-}" ]; then
            echo "::error::APICURIO_CI_KEY is empty for environment ${ENV_NAME}. The authorizer compares it against uif/apicurio/${ENV_NAME}/auth/ci in Secrets Manager. Set an environment-scoped secret of that name on ${ENV_NAME} and put the same value in Secrets Manager. See Task 6."
            exit 1
          fi
          # `-m "not requires_seed"` is load-bearing: a freshly deployed
          # registry is legitimately empty, and without it the seeded-data
          # assertions fail against exactly the deploy this job verifies.
          python -m pytest qe/tests/integration -v -m "not requires_seed"

      # The release tag is cut ONLY here, after green smoke. It is the evidence
      # that this version ran in this environment and passed; cutting it before
      # smoke would make it a record of an intention.
      - name: Cut the release tag
        if: success()
        run: |
          set -euo pipefail
          name="$(stacks/apicurio/scripts/next-release-tag.sh "$ENV_NAME")"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git tag -a "$name" -m "$(printf '%s:\n  promoted_from: %s\n  environment: %s\n  bff_digest: %s\n  ui_sha256: %s\n  smoke: passed\n' \
            "$name" "$TAG" "$ENV_NAME" \
            "${{ steps.manifest.outputs.bff_digest }}" \
            "${{ steps.manifest.outputs.ui_sha256 }}")"
          git push origin "$name"
          echo "::notice::cut $name"
          echo "### Released" >> "$GITHUB_STEP_SUMMARY"
          echo "- \`$name\` from \`$TAG\`" >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Verify the workflow lints**

Run:
```bash
actionlint -shellcheck= -pyflakes= .github/workflows/promote-apicurio.yml
```
Expected: no output.

- [ ] **Step 3: Verify the `uses:` allow-list**

Run:
```bash
grep -hE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[^[:space:]]' \
  .github/workflows/promote-apicurio.yml \
  | sed -E 's/.*uses:[[:space:]]*//' | cut -d@ -f1 | cut -d/ -f1 | sort -u
```
Expected exactly: `actions`, `aws-actions`, `hashicorp`. **Note that `crane` is installed as a binary, not via `google-github-actions` or any `go-containerregistry` action** — neither owner is allow-listed, and one disallowed `uses:` fails the whole workflow at startup with no logs.

- [ ] **Step 4: Verify the script paths resolve from the working directory**

The blast-radius step runs in `stacks/apicurio/infrastructure/terraform` and calls the script by a relative path. Confirm the path is right rather than trusting the `../` count:

```bash
cd stacks/apicurio/infrastructure/terraform
test -x ../../../../stacks/apicurio/scripts/assert-app-only-plan.sh \
  && echo "path resolves" || echo "PATH IS WRONG — fix the ../ count"
```
Expected: `path resolves`. If it does not, use `$GITHUB_WORKSPACE/stacks/apicurio/scripts/...` instead, which is immune to the working directory.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/promote-apicurio.yml
git commit -F - <<'MSG'
Promote a build tag's artifacts and record the result as a release tag

Completes the two-tag model. A build tag says what code went in, evidenced by
the CI gate; a release tag says what was deployed where, evidenced by smoke.
Building never implies deploying, so release/<env>/<date>.<n> is cut only after
tier 2 passes and never speculatively.

Promotion copies rather than rebuilds. The digest and bundle hash in the
manifest are the bytes that passed the gate, and the Docker image is not
reproducible even though the UI bundle is -- so copy-image.sh asserts the
digest before and after, and restore-bundle.sh re-hashes the extracted tree
against ui.sha256.

Every input comes through read-manifest.sh, which yields no field at all from
a tag whose gate did not pass, so promoting a tag that never passed CI stops
at the first step rather than at a later assertion.

The apply is proven app-only before it runs: a full plan containing only
application changes already IS an app-only deploy, so there is no -target and
no exception to the rule against it. Platform drift becomes a stop that names
itself.

crane is installed as a binary because go-containerregistry is not an
allow-listed Actions owner, and one disallowed uses: fails the whole workflow
at startup with no logs.

UIFPE-486 #comment Add promote-apicurio.yml: copies image and bundle with digest and sha256 assertions, app-only apply, tier 2 smoke, release tag cut only on green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 6: uat's smoke credential

Terraform creates `uif/apicurio/uat/auth/ci` but never its value, and `gh secret list --env uat` is empty. Without both halves, tier 2 has nothing to present and the promote job fails at its last gate.

**Files:**
- Modify: none. This task changes live configuration and records what it did.

**Interfaces:**
- Consumes: the uat environment from UIFPE-478.
- Produces: an environment-scoped `APICURIO_CI_KEY` on `uat` whose value matches `uif/apicurio/uat/auth/ci`.

- [ ] **Step 1: Confirm the secret exists in Secrets Manager**

Run:
```bash
AWS_REGION=eu-west-1 /Users/raj9karu/.aws/aws-runas pearson-esb-dev \
  aws secretsmanager describe-secret --secret-id uif/apicurio/uat/auth/ci
```
Expected: either a description, or `ResourceNotFoundException`. If not found, the uat stack has not been applied yet — `authorizer.tf` creates it. Stop here and return after Task 4's apply.

- [ ] **Step 2: Generate one value and put it in both places**

The value must be identical in Secrets Manager and in the Actions secret; the authorizer compares them. Generate it once:

```bash
key="$(openssl rand -hex 32)"

AWS_REGION=eu-west-1 /Users/raj9karu/.aws/aws-runas pearson-esb-dev \
  aws secretsmanager put-secret-value \
    --secret-id uif/apicurio/uat/auth/ci --secret-string "$key"

gh secret set APICURIO_CI_KEY --env uat --body "$key"

unset key
```

- [ ] **Step 3: Verify both halves are present**

```bash
gh secret list --env uat
AWS_REGION=eu-west-1 /Users/raj9karu/.aws/aws-runas pearson-esb-dev \
  aws secretsmanager describe-secret --secret-id uif/apicurio/uat/auth/ci \
  --query 'VersionIdsToStages' --output json
```
Expected: `APICURIO_CI_KEY` listed against `uat`, and a version staged `AWSCURRENT`. Do not print the value.

**Why environment-scoped and not a new repo-level name.** An environment-scoped secret shadows the repository one for jobs declaring `environment: uat`, so the workflow refers to `secrets.APICURIO_CI_KEY` with no branching and dev's value is never sent to uat. A second repo-level name like `APICURIO_CI_KEY_UAT` would require an `if:` in the workflow for every environment added.

- [ ] **Step 4: Record it in the checklist**

Add to `stacks/apicurio/CHECKLIST.md` under the Build & Release section:

```markdown
- [x] **uat smoke credential set** — `uif/apicurio/uat/auth/ci` in Secrets
      Manager and an environment-scoped `APICURIO_CI_KEY` on the `uat`
      environment carry the same value. Environment-scoped rather than a
      second repo-level name, so the workflow needs no `if:` per environment
      and dev's key is never presented to uat.
```

- [ ] **Step 5: Commit**

```bash
git add stacks/apicurio/CHECKLIST.md
git commit -F - <<'MSG'
Record uat's smoke credential

Terraform creates uif/apicurio/<env>/auth/ci but never its value, so a fresh
environment has a secret with nothing in it and tier 2 has nothing to present.
Both halves are now set for uat, and the Actions secret is environment-scoped
so the workflow refers to one name and dev's key is never sent to uat.

UIFPE-486 #comment Set uat's CI key in Secrets Manager and as an environment-scoped Actions secret; recorded in the checklist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 7: `DEPLOY-UAT.md` — the runbook and the first-run order

The phase order matters and cannot be inferred from either workflow, because the first apply of any environment crosses both of them and has a documented second pass.

**Files:**
- Create: `stacks/apicurio/DEPLOY-UAT.md`
- Modify: `stacks/apicurio/CHECKLIST.md`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: the document a human follows for the first uat deployment.

- [ ] **Step 1: Write the runbook**

Create `stacks/apicurio/DEPLOY-UAT.md` covering exactly this, with no steps invented beyond it:

```markdown
# Deploying apicurio to uat

The first deployment of any environment is not the steady-state path. It
crosses both workflows and has a second pass, because the stack cannot know
its own CloudFront domain until the distribution exists. Steady state is one
promote run.

## Prerequisites

| | Check |
|---|---|
| Environment bootstrapped | `bootstrap-environment.sh uat --verify-only` reports all steps green (the environment is positional, not a flag) |
| Both workflows on `develop` | `deploy-apicurio-infra.yml` and `promote-apicurio.yml` |
| A build tag cut AFTER they landed | see "Why v0.1.1 cannot be used" |
| `AWS_ROLE_ARN` on the uat environment | `gh secret list --env uat` |

### Why `apicurio/v0.1.1` cannot be used

A push-triggered run uses the workflow file from the **pushed ref**.
`apicurio/v0.1.1`'s tree carries only `deploy-apicurio-dev.yml` and
`release-apicurio.yml`, so pushing it triggers nothing new. Cut a fresh build
tag once both workflows are on `develop`.

## First deployment

| Phase | Do | Expect |
|---|---|---|
| 1 | `gh variable set APICURIO_UAT_INFRA --body plan`, cut the build tag | a plan in the log, 77 to add. Read it. |
| 2 | `gh variable set APICURIO_UAT_INFRA --body bootstrap`, re-run the run | ECR repository created, then 77 resources applied |
| 3 | add the CloudFront domain from the run summary to `envs/uat.tfvars` `cors_origins`, merge to `develop`, then **cut a new build tag** (same action as phase 1 — not a re-run) and run plan/apply from that tag with `APICURIO_UAT_INFRA=apply` | a small plan, CORS-dominated, on the new tag's run |
| 4 | uat's smoke credential, if not already set (Secrets Manager value + environment-scoped Actions secret, per Task 6) | `gh secret list --env uat` shows `APICURIO_CI_KEY` |
| 5 | `gh variable set APICURIO_UAT_PROMOTE --body run`, re-run | image copied, bundle restored, app-only apply, tier 2 green, `release/uat/<date>.1` pushed |
| 6 | `gh variable unset APICURIO_UAT_INFRA APICURIO_UAT_PROMOTE` | the deploy window is closed again |

Phase 3 is a **new tag**, not a re-run of phase 2's. `envs/uat.tfvars` is
git-tracked, so its contents are frozen in whichever commit the run's tag
points at; merging the `cors_origins` edit to `develop` does not reach back
into a tag already pushed, and re-running phase 2's run re-checks-out that
same pre-merge tree. Re-running it would produce a plan with no
`cors_origins` change at all — not "CORS only", nothing — and phase 5 would
then promote with `cors_origins = "http://localhost:5173"` still in effect;
tier 2 would go green anyway, since smoke never exercises the browser's
origin, leaving a "green uat" whose UI cannot call its API. See "Re-running
rather than re-tagging" below for the general rule this follows.

Phase 6 is not optional. These variables are a deploy-window latch, not
configuration: left on, every build-tag push deploys uat unattended.

## Re-running rather than re-tagging

Under tag-only deployment, **changing a variable costs a re-run; changing a
file costs a new tag.** The two look similar — both are "run the workflow
again on the same tag" — but they answer different questions, and confusing
them is exactly how phase 3 goes wrong (above).

- **Latch variables are read at run time.** `APICURIO_UAT_INFRA` and
  `APICURIO_UAT_PROMOTE` are repository variables, not part of the checked-out
  tree, so "Re-run all jobs" on the most recent run picks up a new value with
  no new commit and no new tag. This is why phases 2 and 5 work by re-running.
- **Tracked file contents are pinned to the tag.** `envs/uat.tfvars`, either
  workflow file, any script — anything that lives in the repository tree — is
  frozen at whatever commit the tag points at. The tag is immutable and
  `actions/checkout@v4` defaults to `github.sha`, which for a `push: tags` run
  is the tag's own commit, so no re-run of an existing tag can ever see an
  edit to a tracked file. That needs a new build tag, the same action as
  phase 1.

This is a property of the design, not an exception to remember for phase 3
alone: the next phase anyone adds to this runbook needs the same check —
does it change a latch variable, or a tracked file? — before assuming a
re-run is enough.

## What uat does and does not prove

uat shares dev's VPC, both `private-css` subnets, dev's single-AZ Secrets
Manager endpoint, and dev's sizing defaults. Isolation is by resource name.
A green uat therefore exercises the application and says nothing about prod's
network or capacity shape.

The first apply also adds an ingress rule to `sg-0b45c554b51638786`, which
Echo owns.
```

- [ ] **Step 2: Link it from the checklist**

Add to `stacks/apicurio/CHECKLIST.md`, in the Build & Release section. Link rather than restate the phases — there is no build step to catch drift between two copies:

```markdown
- [ ] **First uat deployment** — runbook in [`DEPLOY-UAT.md`](DEPLOY-UAT.md).
      Crosses both workflows and has a documented second pass for
      `cors_origins`, because the CloudFront domain does not exist until the
      first apply has run.
```

- [ ] **Step 3: Verify the links resolve**

```bash
cd stacks/apicurio
grep -n "DEPLOY-UAT.md" CHECKLIST.md
test -f DEPLOY-UAT.md && echo "target exists"
```
Expected: the link line, and `target exists`.

- [ ] **Step 4: Run the whole suite**

Run:
```bash
cd stacks/apicurio && pytest qe/tests -q && ruff check .
```
Expected: the pre-existing 286 passed plus the 33 added by Tasks 1–3, so **319 passed, 16 skipped**, and ruff "All checks passed". The 16 skips are pre-existing (`pytest.mark.skipif` on tests needing `bash`, `git`, `jq` or `crane`); Tasks 1–3 add three more skipif guards, so a machine missing `jq` will report more skips and fewer passes. If the count differs for any other reason, reconcile it before committing rather than adjusting the expectation.

- [ ] **Step 5: Commit**

```bash
git add stacks/apicurio/DEPLOY-UAT.md stacks/apicurio/CHECKLIST.md
git commit -F - <<'MSG'
Write the uat deployment runbook

The first deployment of an environment is not the steady-state path: it
crosses both workflows and has a second pass, because cors_origins cannot name
the CloudFront domain until the distribution exists. Steady state is one
promote run, and neither workflow can express that ordering on its own.

Records why apicurio/v0.1.1 cannot drive the first promotion -- a
push-triggered run uses the workflow file from the pushed ref, and that tag
predates both files -- and that closing the deploy-window latches afterwards
is a step, not a courtesy.

UIFPE-486 #comment Add DEPLOY-UAT.md with the six-phase first-run order, the latch discipline, and what a green uat does not prove.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Deferred, deliberately

**The prod gate on uat's release tag.** The spec recommends prod promotion refusing a build tag with no green `release/uat/*`. It is not in this plan because prod does not exist yet and the gate cannot be tested without it. It belongs in the prod plan, as a script (`assert-released-in.sh`) with the same test treatment as Tasks 1–3.

**`prod.tfvars` sizing overrides.** `db.t4g.micro`, `bff_desired_count = 1` and `registry_desired_count = 2` come from variable defaults and are wrong for prod. Belongs in the prod plan.

**Dev's duplicated blast-radius assertion.** `deploy-apicurio-dev.yml` keeps its inline copy of the logic Task 2 extracts. The spec says dev is not modified, and that holds here: switching dev to the script is a change to the one workflow that currently works, and it should be its own commit with its own justification once `assert-app-only-plan.sh` has run in anger. Until then the duplication is known and deliberate, not an oversight.

**Adding `stacks-ci gate` to `develop`'s required checks.** Unrelated to this plan, still the cheapest open checklist item, and now passing on eight consecutive apicurio pull requests.
