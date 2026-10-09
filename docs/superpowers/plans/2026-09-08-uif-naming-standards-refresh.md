# UIF Naming Standards Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `architecture-design/standards/` into line with the UIF Resource Name Lookup — nine rule files, a pattern-driven `PreToolUse` hook covering 29 resource types, and a test that stops the two from drifting again.

**Architecture:** The hook's scattered constants collapse into one `PATTERNS` table keyed by Terraform resource type; each entry declares delimiter form, required suffix, length limit, rename posture and the rule file to cite. Rule files mirror the standard's six groups plus core, catalog and DynamoDB attributes. A test asserts every pattern the hook enforces is written down in a rule file and vice versa.

**Tech Stack:** Python 3 standard library only (the hook runs as `python <path>` with no install step), Markdown with YAML front matter for rule files, pytest for the test, PowerShell/Bash for the installers.

**Spec:** [`docs/superpowers/specs/2026-09-08-uif-naming-standards-refresh-design.md`](../specs/2026-09-08-uif-naming-standards-refresh-design.md)

## Global Constraints

- **The hook must never block legitimate work.** `main()` is wrapped in a bare `except` that exits 0. Keep it. Every new check fails open.
- **Standard library only.** No third-party imports in `validate_naming.py` — it runs from `~/.claude/hooks/` with no virtualenv.
- **Values already on disk are never judged.** A name that exists in the pre-edit file text is pre-existing and legal by design, however non-compliant.
- **No deployed resource is renamed.** Not in this repo, not in any example. The legacy freeze stays.
- **Interpolated values are skipped.** Any literal containing `${` is not judged — Terraform resolves it at plan time.
- **Stack vocabulary:** `echo, beacon, event, harbor`. **Env vocabulary:** `dev, uat, prod`. **Cadence vocabulary:** `30min, 4hr, daily, hourly`.
- **Banned tokens:** `phase0, batch2, fix12, spike, tmp, new, old, v2`, and anything matching `^[A-Za-z]{2,}-\d+$` (ticket keys).
- **Python style:** match the existing file — module-level constants, plain functions, `%`-style formatting, no classes, no f-strings.

---

## File Structure

| Path | Responsibility |
|---|---|
| `architecture-design/standards/hooks/validate_naming.py` | Modify. The enforcement layer. Gains `PATTERNS`, layer-aware path checks, expanded rename guard. |
| `architecture-design/standards/hooks/test_validate_naming.py` | Create. Behavioural tests plus the three anti-drift assertions. |
| `architecture-design/standards/rules/naming/00-core.md` | Modify. Stem, vocabularies, delimiters, banned tokens, cadence, zero-padding, length limits, legacy freeze. |
| `architecture-design/standards/rules/naming/compute-etl.md` | Create. Glue jobs and connections, Lambda functions and layers, ECS family, ECR, Step Functions. |
| `architecture-design/standards/rules/naming/storage.md` | Create. S3 buckets, the three key-prefix shapes, DynamoDB tables. |
| `architecture-design/standards/rules/naming/catalog.md` | Modify. Glue catalog databases and tables, preserved BigQuery forms. |
| `architecture-design/standards/rules/naming/eventing.md` | Create. EventBridge bus and rules, SQS, SNS. |
| `architecture-design/standards/rules/naming/networking.md` | Create. ALB, target group, API Gateway, CloudFront, security group, VPC endpoint. |
| `architecture-design/standards/rules/naming/identity-secrets.md` | Create (replaces `secrets.md`). IAM role/policy, Secrets Manager. |
| `architecture-design/standards/rules/naming/observability.md` | Create. CloudWatch log group, dashboard, alarm. |
| `architecture-design/standards/rules/naming/dynamodb-attributes.md` | Create. Scalar, map, list and boolean attribute conventions. |
| `architecture-design/standards/rules/naming/aws-resources.md` | Delete. Content redistributed. |
| `architecture-design/standards/rules/naming/data-lake.md` | Delete. Content moves to `storage.md`. |
| `architecture-design/standards/rules/naming/secrets.md` | Delete. Content moves to `identity-secrets.md`. |
| `architecture-design/standards/VERSION` | Modify. `1.0.0` → `2.0.0`. |
| `architecture-design/standards/Setup-Guide.md` | Modify. Contents table, hook-refusal list, troubleshooting examples. |
| `.github/workflows/standards-ci.yml` | Create. Runs the test on changes under `architecture-design/standards/**`. |
| `architecture-design/decisions/0014-naming-standard-refresh.md` | Create. Governance record. |

Tasks 1 and 2 are shippable on their own and fix a live defect. Tasks 3–5 rebuild the hook. Task 6 creates the test that then drives Tasks 7–11. Tasks 12–14 are packaging and governance.

---

### Task 1: Unblock compliant Bronze paths

The hook denies any S3 path segment containing an underscore, so the standard's Hive-style partition segment `_extract_date=YYYY-MM-DD` is refused. This is the only task that fixes something actively broken; do it first and ship it alone if you like.

**Files:**
- Modify: `architecture-design/standards/hooks/validate_naming.py:175-211` (`check_paths`)
- Test: `architecture-design/standards/hooks/test_validate_naming.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PARTITION_SEG` module constant (`re.Pattern`), matching a Hive partition segment. Tasks 2 uses it.

- [ ] **Step 1: Write the failing test**

Create `architecture-design/standards/hooks/test_validate_naming.py`:

```python
"""Tests for the UIF naming guard."""
import importlib.util
import os

_HOOK = os.path.join(os.path.dirname(__file__), "validate_naming.py")
_spec = importlib.util.spec_from_file_location("validate_naming", _HOOK)
vn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vn)


def test_bronze_partition_segment_is_allowed():
    new = {
        "destination.cloud": "aws",
        "target.s3_path": "s3://uif-echo-dev-bronze/tep/product/"
                          "mtl-system-items-b/_extract_date=2026-09-04/",
    }
    assert vn.check_paths(new, {}) is None


def test_underscore_in_a_non_partition_segment_is_still_rejected():
    new = {
        "destination.cloud": "aws",
        "target.s3_path": "s3://uif-echo-dev-bronze/tep/product_domain/tbl/",
    }
    assert vn.check_paths(new, {}) is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: `test_bronze_partition_segment_is_allowed` FAILS — `check_paths` returns a message complaining `_extract_date=2026-09-04` is not kebab-case. The second test passes already.

- [ ] **Step 3: Add the partition-segment constant**

In `validate_naming.py`, after the `TICKET_KEY` constant (line 28):

```python
# Hive-style partition segments are part of the key, not a taxonomy segment:
# `_extract_date=2026-09-04`. They legitimately carry an underscore and an `=`,
# neither of which the kebab-case rule permits elsewhere.
PARTITION_SEG = re.compile(r"^_[a-z][a-z0-9_]*=[^/]+$")
```

- [ ] **Step 4: Exempt partition segments from the kebab check**

In `check_paths`, replace the S3 branch (the `else` at line 189-193):

```python
        else:
            bad = [s for s in segs
                   if not PARTITION_SEG.match(s) and ("_" in s or s != s.lower())]
            if bad:
                return ("`%s` = `%s` — S3 key segments are lower-case and "
                        "hyphenated; `%s` is not (storage.md)." % (field, v, bad[0]))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add architecture-design/standards/hooks/validate_naming.py architecture-design/standards/hooks/test_validate_naming.py
git commit -m "fix: stop the naming hook denying compliant Bronze partition paths

The Hive-style partition segment the standard requires
(_extract_date=YYYY-MM-DD) contains an underscore, which check_paths
rejected as non-kebab-case. Compliant new Bronze paths were being denied."
```

---

### Task 2: Layer-aware data-lake path grammar

The standard replaced one two-segment shape with three, distinguished by layer. Bronze gains a leading source-system segment; Gold drops the domain.

**Files:**
- Modify: `architecture-design/standards/hooks/validate_naming.py` (`check_paths`)
- Test: `architecture-design/standards/hooks/test_validate_naming.py`

**Interfaces:**
- Consumes: `PARTITION_SEG` from Task 1.
- Produces: `layer_of(bucket_or_field)` returning `"bronze" | "silver" | "gold" | None`; `PREFIX_SHAPES` dict mapping layer to `(min_segments, max_segments, description)`.

- [ ] **Step 1: Write the failing tests**

Append to `test_validate_naming.py`:

```python
def test_silver_path_three_segments_passes():
    new = {"destination.cloud": "aws",
           "silver.s3_path": "s3://uif-echo-dev-silver/tep/product/mtl-system-items-b/"}
    assert vn.check_paths(new, {}) is None


def test_gold_path_is_entity_only():
    new = {"destination.cloud": "aws",
           "target.s3_path": "s3://uif-echo-dev-gold/product/"}
    assert vn.check_paths(new, {}) is None


def test_gold_path_with_a_domain_segment_is_rejected():
    new = {"destination.cloud": "aws",
           "target.s3_path": "s3://uif-echo-dev-gold/tep/product/"}
    msg = vn.check_paths(new, {})
    assert msg is not None and "gold" in msg.lower()


def test_bronze_path_missing_source_system_is_rejected():
    new = {"destination.cloud": "aws",
           "target.s3_path": "s3://uif-echo-dev-bronze/product/mtl-system-items-b/"}
    msg = vn.check_paths(new, {})
    assert msg is not None and "source_system" in msg


def test_layer_of_reads_the_bucket_token():
    assert vn.layer_of("s3://uif-echo-dev-bronze/x/") == "bronze"
    assert vn.layer_of("s3://uif-echo-dev-gold/x/") == "gold"
    assert vn.layer_of("s3://uif-echo-dev-glue-scripts/x/") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: the four new path tests and `test_layer_of_reads_the_bucket_token` FAIL — `layer_of` is not defined.

- [ ] **Step 3: Add the layer resolver and shape table**

In `validate_naming.py`, after `PARTITION_SEG`:

```python
LAYERS = ("bronze", "silver", "gold")

# (min_segments, max_segments, human description) — segment counts exclude the
# bucket and any trailing empty string from the required trailing slash.
PREFIX_SHAPES = {
    "bronze": (3, 4, "{source_system}/{data_domain}/{table}/"
                     "[_extract_date=YYYY-MM-DD/]"),
    "silver": (3, 3, "{source_system}/{data_domain}/{table}/"),
    "gold":   (1, 1, "{entity}/"),
}


def layer_of(uri):
    """Medallion layer from the bucket's trailing {layer} token, else None."""
    m = re.match(r"^[a-z0-9]+://([^/]+)/", uri or "")
    if not m:
        return None
    bucket = m.group(1)
    for layer in LAYERS:
        if bucket.endswith("-" + layer):
            return layer
    return None
```

- [ ] **Step 4: Enforce the shape inside `check_paths`**

In `check_paths`, immediately after the trailing-slash check and before the GCS/S3 case split, insert:

```python
        segs = re.sub(r"^[a-z0-9]+://[^/]+/", "", v).strip("/").split("/")
        segs = [s for s in segs if s]
        layer = layer_of(v)
        if layer and not field.endswith("gcs_path"):
            low, high, shape = PREFIX_SHAPES[layer]
            if not (low <= len(segs) <= high):
                return ("`%s` = `%s` — a %s prefix is `{bucket}/%s`; found %d "
                        "segment(s) (storage.md)." % (field, v, layer, shape, len(segs)))
            if layer == "bronze" and len(segs) >= 1 and PARTITION_SEG.match(segs[0]):
                return ("`%s` = `%s` — a bronze prefix opens with "
                        "{source_system}, not a partition segment "
                        "(storage.md)." % (field, v))
```

Then delete the now-duplicated `segs = ...` line further down in the existing S3/GCS branch so the variable is computed once.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS. If `test_bronze_path_missing_source_system_is_rejected` fails, the segment count is 2 which is below `bronze`'s minimum of 3 — check the message includes the literal `{source_system}` from `PREFIX_SHAPES`.

- [ ] **Step 6: Commit**

```bash
git add architecture-design/standards/hooks/validate_naming.py architecture-design/standards/hooks/test_validate_naming.py
git commit -m "feat: layer-aware data-lake prefix grammar in the naming hook

Bronze/Silver/Gold now have three distinct prefix shapes per the standard;
the layer comes from the bucket's trailing token."
```

---

### Task 3: Replace the four constants with a `PATTERNS` table

`STACKS`, `ENVS`, `CADENCES` and `LIMITS` become one table keyed by Terraform resource type. This task builds the table for the ten types already covered and keeps every current behaviour green, so it is a refactor with no behavioural change except the three superseded forms.

**Files:**
- Modify: `architecture-design/standards/hooks/validate_naming.py:23-56` (constants), `135-172` (`check_hyphen_name`)
- Test: `architecture-design/standards/hooks/test_validate_naming.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PATTERNS` dict — keys are Terraform resource type strings, values are dicts with keys `delimiter` (`"hyphen"|"underscore"|"slash"|"platform"`), `suffix` (str or `None`), `limit` (int or `None`), `rename` (`None|"destructive"|"replace"`), `rule` (rule filename). Also `check_name(value, rtype)` replacing `check_hyphen_name`, dispatching on `delimiter`.

- [ ] **Step 1: Write the failing tests**

Append to `test_validate_naming.py`:

```python
def test_plain_uif_bucket_is_now_compliant():
    # The account-id prefix is gone from the standard.
    assert vn.check_name("uif-echo-dev-bronze", "aws_s3_bucket") is None
    assert vn.check_name("uif-echo-dev-ui", "aws_s3_bucket") is None


def test_account_prefixed_bucket_is_now_rejected():
    msg = vn.check_name("283556840805-s3-uif-echo-dev-ui", "aws_s3_bucket")
    assert msg is not None and "uif-" in msg


def test_secret_uses_slash_form_without_gip():
    assert vn.check_name("uif/echo/dev/db/tep", "aws_secretsmanager_secret") is None
    assert vn.check_name("uif/echo/uat/cloud/gcp", "aws_secretsmanager_secret") is None


def test_hyphen_in_a_secret_segment_is_rejected():
    msg = vn.check_name("uif/echo/uat/cloud/gcp-ipm", "aws_secretsmanager_secret")
    assert msg is not None


def test_glue_catalog_database_uses_underscores():
    assert vn.check_name("uif_echo_dev_silver_tep", "aws_glue_catalog_database") is None
    assert vn.check_name("uif-echo-dev-silver-tep", "aws_glue_catalog_database") is not None


def test_banned_tokens_still_rejected():
    assert vn.check_name("uif-echo-dev-phase0-probe", "aws_glue_job") is not None
    assert vn.check_name("uif-echo-dev-UIFPE-254", "aws_glue_job") is not None


def test_cadence_vocabulary_still_closed():
    ok = "uif-echo-dev-schedule-bronze-ingest-daily-slot-00"
    assert vn.check_name(ok, "aws_cloudwatch_event_rule") is None
    bad = "uif-echo-dev-schedule-bronze-ingest-weekly"
    assert vn.check_name(bad, "aws_cloudwatch_event_rule") is not None


def test_zero_padding_still_enforced():
    bad = "uif-echo-dev-schedule-bronze-ingest-daily-slot-0"
    assert vn.check_name(bad, "aws_cloudwatch_event_rule") is not None


def test_length_limit_still_enforced():
    long_name = "uif-echo-dev-" + ("x" * 60)
    assert vn.check_name(long_name, "aws_lambda_function") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: every new test FAILS — `check_name` is not defined.

- [ ] **Step 3: Write the `PATTERNS` table**

Replace lines 23-42 of `validate_naming.py` (`STACKS` through `RENAME_GUARDED`) with:

```python
STACKS = {"echo", "beacon", "event", "harbor"}
ENVS = {"dev", "uat", "prod"}
CADENCES = {"30min", "4hr", "daily", "hourly"}
BANNED = {"phase0", "batch2", "fix12", "spike", "tmp", "new", "old", "v2"}
CADENCE_SHAPED = re.compile(
    r"^(\d+(min|hr|hour|h|d|day)s?|weekly|nightly|monthly|yearly|annually)$")
TICKET_KEY = re.compile(r"^[A-Za-z]{2,}-\d+$")

# One row per Terraform resource type whose name this hook can judge.
#   delimiter — which form the name takes
#   suffix    — required trailing token, or None
#   limit     — AWS length cap (a platform fact, not a convention choice)
#   rename    — "destructive" (data or references lost) | "replace" | None
#   rule      — rule file cited in the denial message
PATTERNS = {
    # -- compute & ETL --
    "aws_glue_job":              {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": "replace", "rule": "compute-etl.md"},
    "aws_glue_connection":       {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": None, "rule": "compute-etl.md"},
    "aws_lambda_function":       {"delimiter": "hyphen", "suffix": None, "limit": 64, "rename": "replace", "rule": "compute-etl.md"},
    "aws_lambda_layer_version":  {"delimiter": "hyphen", "suffix": "layer", "limit": 64, "rename": None, "rule": "compute-etl.md"},
    "aws_ecs_cluster":           {"delimiter": "hyphen", "suffix": "cluster", "limit": 255, "rename": "replace", "rule": "compute-etl.md"},
    "aws_ecs_service":           {"delimiter": "hyphen", "suffix": "service", "limit": 255, "rename": "replace", "rule": "compute-etl.md"},
    "aws_ecs_task_definition":   {"delimiter": "hyphen", "suffix": "task-definition", "limit": 255, "rename": None, "rule": "compute-etl.md"},
    "aws_ecr_repository":        {"delimiter": "hyphen", "suffix": "ecr-repo", "limit": 256, "rename": "destructive", "rule": "compute-etl.md"},
    "aws_sfn_state_machine":     {"delimiter": "hyphen", "suffix": None, "limit": 80, "rename": "replace", "rule": "compute-etl.md"},
    # -- catalog --
    "aws_glue_catalog_database": {"delimiter": "underscore", "suffix": None, "limit": 255, "rename": "destructive", "rule": "catalog.md"},
    "aws_glue_catalog_table":    {"delimiter": "underscore", "suffix": None, "limit": 255, "rename": "destructive", "rule": "catalog.md"},
    # -- storage --
    "aws_s3_bucket":             {"delimiter": "hyphen", "suffix": None, "limit": 63, "rename": "destructive", "rule": "storage.md"},
    "aws_dynamodb_table":        {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": "destructive", "rule": "storage.md"},
    # -- eventing --
    "aws_cloudwatch_event_bus":  {"delimiter": "hyphen", "suffix": "bus", "limit": 256, "rename": "replace", "rule": "eventing.md"},
    "aws_cloudwatch_event_rule": {"delimiter": "hyphen", "suffix": None, "limit": 64, "rename": "replace", "rule": "eventing.md"},
    "aws_sqs_queue":             {"delimiter": "hyphen", "suffix": None, "limit": 80, "rename": "destructive", "rule": "eventing.md"},
    "aws_sns_topic":             {"delimiter": "hyphen", "suffix": "alert", "limit": 256, "rename": "replace", "rule": "eventing.md"},
    # -- networking & edge --
    "aws_lb":                    {"delimiter": "hyphen", "suffix": "alb", "limit": 32, "rename": "replace", "rule": "networking.md"},
    "aws_lb_target_group":       {"delimiter": "hyphen", "suffix": "tg", "limit": 32, "rename": "replace", "rule": "networking.md"},
    "aws_apigatewayv2_api":      {"delimiter": "hyphen", "suffix": "api", "limit": 128, "rename": "replace", "rule": "networking.md"},
    "aws_cloudfront_function":   {"delimiter": "hyphen", "suffix": None, "limit": 64, "rename": "replace", "rule": "networking.md"},
    "aws_security_group":        {"delimiter": "hyphen", "suffix": "sg", "limit": 255, "rename": "replace", "rule": "networking.md"},
    "aws_vpc_endpoint":          {"delimiter": "hyphen", "suffix": "endpoint", "limit": 255, "rename": None, "rule": "networking.md"},
    # -- identity & secrets --
    "aws_iam_role":              {"delimiter": "hyphen", "suffix": "role", "limit": 64, "rename": "replace", "rule": "identity-secrets.md"},
    "aws_iam_policy":            {"delimiter": "hyphen", "suffix": "policy", "limit": 128, "rename": "replace", "rule": "identity-secrets.md"},
    "aws_secretsmanager_secret": {"delimiter": "slash", "suffix": None, "limit": 512, "rename": "destructive", "rule": "identity-secrets.md"},
    # -- observability --
    "aws_cloudwatch_log_group":  {"delimiter": "platform", "suffix": None, "limit": 512, "rename": None, "rule": "observability.md"},
    "aws_cloudwatch_dashboard":  {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": None, "rule": "observability.md"},
    "aws_cloudwatch_metric_alarm": {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": None, "rule": "observability.md"},
}
```

`aws_cloudfront_distribution` is deliberately absent: it has no name attribute, so there is nothing for the hook to read. `networking.md` covers it as guidance only.

- [ ] **Step 4: Write `check_name` and its three form checkers**

Replace `check_hyphen_name` (lines 135-172) with:

```python
def _vocab_violation(value, tokens, rule):
    """Stack/env/banned/cadence/zero-pad checks shared by the hyphen form."""
    if len(tokens) >= 3:
        if tokens[1] not in STACKS:
            return "`%s` — `%s` is not a stack (%s) (%s)." % (
                value, tokens[1], ", ".join(sorted(STACKS)), rule)
        if tokens[2] not in ENVS:
            return "`%s` — `%s` is not an env (%s) (%s)." % (
                value, tokens[2], ", ".join(sorted(ENVS)), rule)
    for t in tokens:
        if t in BANNED or TICKET_KEY.match(t):
            return ("`%s` contains the banned token `%s` — names describe "
                    "function, not phase, ticket, status or version "
                    "(00-core.md)." % (value, t))
    if TICKET_KEY.match("-".join(tokens[3:])):
        return ("`%s` embeds a ticket key — names describe function "
                "(00-core.md)." % value)
    if "-schedule-" in value:
        for t in tokens:
            if CADENCE_SHAPED.match(t) and t not in CADENCES:
                return ("`%s` — `%s` is not in the cadence vocabulary (%s) "
                        "(00-core.md). Do not invent a cadence token."
                        % (value, t, ", ".join(sorted(CADENCES))))
    if re.search(r"-slot-\d$", value):
        return ("`%s` — numeric instances are zero-padded to two digits, "
                "e.g. `-slot-00` (00-core.md)." % value)
    return None


def check_name(value, rtype):
    """Violation string for a newly introduced literal name, else None."""
    if not value or "${" in value:
        return None
    spec = PATTERNS.get(rtype)
    if spec is None:
        return None
    rule, form = spec["rule"], spec["delimiter"]

    if form == "slash":
        if not value.startswith("uif/"):
            return ("`%s` does not open with the mandatory `uif/` stem "
                    "`uif/{stack}/{env}/...` (%s)." % (value, rule))
        segs = value.split("/")
        if any("-" in s for s in segs):
            return ("`%s` — every token boundary in the slash form is a `/`; "
                    "a hyphen drops the secret out of every wildcard-scoped "
                    "IAM policy (%s)." % (value, rule))
        bad = _vocab_violation(value, segs, rule)
        if bad:
            return bad
    elif form == "underscore":
        if "-" in value:
            return ("`%s` — this form uses underscores, e.g. "
                    "`uif_echo_dev_silver_tep` (%s)." % (value, rule))
        if not value.startswith("uif_"):
            return ("`%s` does not open with the mandatory `uif_` stem (%s)."
                    % (value, rule))
        bad = _vocab_violation(value, value.split("_"), rule)
        if bad:
            return bad
    elif form == "platform":
        if "/uif-" not in value:
            return ("`%s` — AWS owns the prefix and the UIF stem follows it, "
                    "e.g. `/aws/lambda/uif-echo-dev-dispatch` (%s)."
                    % (value, rule))
        bad = _vocab_violation(value, value.rsplit("/", 1)[-1].split("-"), rule)
        if bad:
            return bad
    else:
        base = value[:-5] if value.endswith(".fifo") else value
        if not base.startswith("uif-"):
            return ("`%s` does not open with the mandatory `uif-` stem "
                    "`uif-{stack}-{env}-{operation}` (%s)." % (value, rule))
        suffix = spec["suffix"]
        if suffix and not base.endswith("-" + suffix) and not base.endswith("-dlq"):
            return ("`%s` — this resource's name ends with `-%s` (%s)."
                    % (value, suffix, rule))
        bad = _vocab_violation(value, base.split("-"), rule)
        if bad:
            return bad

    limit = spec["limit"]
    if limit and len(value) > limit:
        return "`%s` is %d chars; the limit for %s is %d (00-core.md)." % (
            value, len(value), rtype, limit)
    return None
```

The `-dlq` escape in the suffix check exists because a dead-letter queue is named for the queue it backs (`uif-echo-dev-dispatch-dlq`) and an SNS alert DLQ would otherwise fail the `-alert` suffix.

- [ ] **Step 5: Update the two `main()` call sites**

In `main()`, change both `check_hyphen_name(...)` calls to `check_name(...)`, and change the `.tfvars` branch's denial text to cite `storage.md` rather than the old rule file:

```python
            bad = check_name(value, "aws_s3_bucket")
            if bad:
                deny("Blocked: new bucket name violates the UIF naming "
                     "convention.\n%s\nSee ~/.claude/rules/naming/storage.md."
                     % bad)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS. If `test_account_prefixed_bucket_is_now_rejected` fails, confirm the `^\d{12}-s3-` special case was fully removed — the new standard has no account-prefixed form.

- [ ] **Step 7: Commit**

```bash
git add architecture-design/standards/hooks/validate_naming.py architecture-design/standards/hooks/test_validate_naming.py
git commit -m "refactor: drive the naming hook from one PATTERNS table

Replaces the four scattered constants with a per-resource-type table and
one check_name dispatcher. Adopts the standard's three superseded forms:
plain uif- buckets, gip-less secret paths, concept-named DynamoDB tables."
```

---

### Task 4: Cover the resource types that had no checks

`PATTERNS` now lists 29 types but `main()` only ever looked at names it happened to parse. Verify every type is reachable and its suffix and limit bite.

**Files:**
- Test: `architecture-design/standards/hooks/test_validate_naming.py`
- Modify: `architecture-design/standards/hooks/validate_naming.py` only if a test exposes a gap.

**Interfaces:**
- Consumes: `PATTERNS`, `check_name` from Task 3.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing tests**

Append to `test_validate_naming.py`:

```python
import pytest

COMPLIANT = {
    "aws_glue_job": "uif-echo-dev-silver-transform-bq",
    "aws_glue_connection": "uif-echo-dev-vpc-network",
    "aws_lambda_function": "uif-echo-dev-dispatch",
    "aws_lambda_layer_version": "uif-echo-dev-oracle-layer",
    "aws_ecs_cluster": "uif-beacon-dev-cluster",
    "aws_ecs_service": "uif-beacon-dev-pipeline-service",
    "aws_ecs_task_definition": "uif-beacon-dev-pipeline-task-definition",
    "aws_ecr_repository": "uif-beacon-dev-pipeline-ecr-repo",
    "aws_sfn_state_machine": "uif-beacon-dev-export",
    "aws_glue_catalog_database": "uif_echo_dev_gold_product",
    "aws_glue_catalog_table": "uif_echo_dev_gold_product",
    "aws_s3_bucket": "uif-echo-dev-bronze",
    "aws_dynamodb_table": "uif-echo-dev-pipeline-config",
    "aws_cloudwatch_event_bus": "uif-event-dev-bus",
    "aws_cloudwatch_event_rule": "uif-echo-dev-on-silver-succeeded-replicate-bq",
    "aws_sqs_queue": "uif-echo-dev-dispatch",
    "aws_sns_topic": "uif-echo-dev-pipeline-alert",
    "aws_lb": "uif-echo-dev-api-alb",
    "aws_lb_target_group": "uif-echo-dev-api-tg",
    "aws_apigatewayv2_api": "uif-echo-dev-authentication-api",
    "aws_cloudfront_function": "uif-beacon-dev-strip-api-prefix",
    "aws_security_group": "uif-echo-dev-eni-sg",
    "aws_vpc_endpoint": "uif-echo-dev-secretsmanager-endpoint",
    "aws_iam_role": "uif-echo-dev-glue-role",
    "aws_iam_policy": "uif-echo-dev-glue-policy",
    "aws_secretsmanager_secret": "uif/echo/dev/db/tep",
    "aws_cloudwatch_log_group": "/aws/lambda/uif-echo-dev-dispatch",
    "aws_cloudwatch_dashboard": "uif-echo-dev-silver-bq-replication",
    "aws_cloudwatch_metric_alarm": "uif-echo-dev-silver-bq-replicator-failed",
}


@pytest.mark.parametrize("rtype", sorted(PATTERNS_KEYS := list(vn.PATTERNS)))
def test_every_pattern_has_a_compliant_example(rtype):
    assert rtype in COMPLIANT, "add a compliant example for %s" % rtype
    assert vn.check_name(COMPLIANT[rtype], rtype) is None


@pytest.mark.parametrize("rtype,suffix", [
    (k, v["suffix"]) for k, v in vn.PATTERNS.items() if v["suffix"]])
def test_missing_required_suffix_is_rejected(rtype, suffix):
    stripped = COMPLIANT[rtype].rsplit("-" + suffix, 1)[0]
    assert vn.check_name(stripped, rtype) is not None


def test_alb_thirty_two_char_cap_bites():
    too_long = "uif-beacon-prod-partner-export-alb"   # 34
    assert vn.check_name(too_long, "aws_lb") is not None


def test_fifo_suffix_does_not_break_parsing():
    assert vn.check_name("uif-echo-dev-dispatch.fifo", "aws_sqs_queue") is None


def test_dlq_names_for_the_queue_they_back():
    assert vn.check_name("uif-echo-dev-dispatch-dlq", "aws_sqs_queue") is None


def test_unknown_resource_type_is_not_judged():
    assert vn.check_name("whatever-you-like", "aws_instance") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: `test_alb_thirty_two_char_cap_bites` and the suffix parametrisation FAIL if Task 3's suffix logic or the ALB limit is wrong. `test_every_pattern_has_a_compliant_example` FAILS for any `PATTERNS` key missing from `COMPLIANT`.

- [ ] **Step 3: Fix whatever the tests expose**

No speculative changes. Make only the edits the failures demand — a missing limit, a wrong suffix string, a form misassigned. If `aws_glue_catalog_table` fails because its compliant example is a database name, change `COMPLIANT["aws_glue_catalog_table"]` to `"mtl_system_items_b"` and add to `check_name`'s underscore branch, before the stem check:

```python
        if rtype == "aws_glue_catalog_table":
            if value != value.lower() or "-" in value:
                return ("`%s` — a catalog table name is the entity only, in "
                        "snake_case, never hyphenated (%s)." % (value, rule))
            return None
```

A table name is the bare entity, so it carries no `uif_` stem — that is the one type in the underscore form which must skip the stem check.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS, 29 parametrised cases plus the suffix cases.

- [ ] **Step 5: Commit**

```bash
git add architecture-design/standards/hooks/validate_naming.py architecture-design/standards/hooks/test_validate_naming.py
git commit -m "test: a compliant example and a suffix case for all 29 patterns"
```

---

### Task 5: Expand the rename guard from two types to eight

Renaming is destructive or replacement-forcing for far more than S3 buckets and Glue jobs. Each type gets its own reason so the denial explains the actual consequence.

**Files:**
- Modify: `architecture-design/standards/hooks/validate_naming.py:42` (`RENAME_GUARDED`), `240-258` (the `.tf` branch of `main`)
- Test: `architecture-design/standards/hooks/test_validate_naming.py`

**Interfaces:**
- Consumes: `PATTERNS` from Task 3 (the `rename` key).
- Produces: `RENAME_REASON` dict mapping resource type to a consequence sentence.

- [ ] **Step 1: Write the failing test**

Append to `test_validate_naming.py`:

```python
def test_every_destructive_type_has_a_reason():
    for rtype, spec in vn.PATTERNS.items():
        if spec["rename"]:
            assert rtype in vn.RENAME_REASON, "no rename reason for %s" % rtype


def test_destructive_types_include_the_data_stores():
    destructive = {k for k, v in vn.PATTERNS.items() if v["rename"] == "destructive"}
    assert {"aws_s3_bucket", "aws_dynamodb_table", "aws_sqs_queue",
            "aws_ecr_repository", "aws_secretsmanager_secret",
            "aws_glue_catalog_database"} <= destructive
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py::test_every_destructive_type_has_a_reason -v`

Expected: FAIL — `RENAME_REASON` is not defined.

- [ ] **Step 3: Add the reason table**

After `PATTERNS` in `validate_naming.py`:

```python
# Why a rename is refused, per type. The generic message ("forces replacement")
# is true but unhelpful; an engineer needs to know what they would lose.
RENAME_REASON = {
    "aws_s3_bucket": "Terraform destroys and recreates the bucket — its data is lost.",
    "aws_dynamodb_table": "Terraform destroys and recreates the table — every item is lost.",
    "aws_sqs_queue": "The queue is recreated — in-flight messages are lost.",
    "aws_ecr_repository": "The repository is recreated — every stored image is lost.",
    "aws_secretsmanager_secret": "The secret is recreated under a new name, breaking every IAM statement scoped to the old path and every stored ARN that references it.",
    "aws_glue_catalog_database": "The database is recreated — its table metadata is lost.",
    "aws_glue_catalog_table": "The table's catalog metadata is recreated.",
    "aws_glue_job": "The job is destroyed and recreated, losing its run history.",
    "aws_lambda_function": "The function is recreated — its version history and any published aliases go with it.",
    "aws_ecs_cluster": "The cluster is recreated; services registered to it must be recreated too.",
    "aws_ecs_service": "The service is recreated, which cycles every running task.",
    "aws_sfn_state_machine": "The state machine is recreated — execution history is lost.",
    "aws_cloudwatch_event_bus": "The bus is recreated; every rule attached to it must be recreated.",
    "aws_sns_topic": "The topic is recreated — every subscription is dropped.",
    "aws_lb": "The load balancer is recreated with a new DNS name, breaking anything pointing at the old one.",
    "aws_lb_target_group": "The target group is recreated and must be re-attached to its listener.",
    "aws_apigatewayv2_api": "The API is recreated with a new endpoint.",
    "aws_cloudfront_function": "The function is recreated and must be re-associated with its distribution.",
    "aws_security_group": "The group is recreated; every rule referencing it by id breaks.",
    "aws_iam_role": "The role is recreated — every trust policy and every ARN reference to it breaks.",
    "aws_iam_policy": "The policy is recreated; attachments must be re-made.",
}
```

- [ ] **Step 4: Replace `RENAME_GUARDED` with a `PATTERNS` lookup**

Delete the `RENAME_GUARDED` constant. In `main()`'s `.tf` branch, replace the rename check:

```python
            spec = PATTERNS.get(rtype) or {}
            if key in old_n and old_n[key] != value and spec.get("rename"):
                deny(
                    "Blocked: this edit renames a deployed %s (`%s` -> `%s`) "
                    "in %s.\n%s\nExisting `echo-{env}-*` names predate the UIF "
                    "convention and are non-compliant by design until an "
                    "explicit, planned migration. Leave the name as it is."
                    % (rtype, old_n[key], value, os.path.basename(path),
                       RENAME_REASON.get(rtype, "Renaming forces replacement.")))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add architecture-design/standards/hooks/validate_naming.py architecture-design/standards/hooks/test_validate_naming.py
git commit -m "feat: rename guard covers all 21 replacement-forcing types

Each denial now names the actual consequence rather than saying
'forces replacement'."
```

---

### Task 6: The anti-drift test

This is the harness for Tasks 7–11. It fails now, listing every resource type with no rule file, and each rule-file task makes more of it pass.

**Files:**
- Modify: `architecture-design/standards/hooks/test_validate_naming.py`

**Interfaces:**
- Consumes: `PATTERNS` from Task 3.
- Produces: `RULES_DIR` and `rule_text()` helpers used by later assertions.

- [ ] **Step 1: Write the failing test**

Append to `test_validate_naming.py`:

```python
RULES_DIR = os.path.join(os.path.dirname(__file__), "..", "rules", "naming")


def rule_text(name):
    with open(os.path.join(RULES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def all_rule_text():
    out = {}
    for fn in sorted(os.listdir(RULES_DIR)):
        if fn.endswith(".md"):
            out[fn] = rule_text(fn)
    return out


def test_every_pattern_is_documented_in_its_rule_file():
    texts = all_rule_text()
    missing = []
    for rtype, spec in vn.PATTERNS.items():
        target = spec["rule"]
        if target not in texts:
            missing.append("%s -> %s (file absent)" % (rtype, target))
        elif rtype not in texts[target]:
            missing.append("%s not named in %s" % (rtype, target))
    assert not missing, "undocumented patterns:\n" + "\n".join(missing)


def test_core_carries_the_vocabularies():
    core = rule_text("00-core.md")
    for token in sorted(vn.STACKS) + sorted(vn.ENVS) + sorted(vn.CADENCES):
        assert token in core, "`%s` missing from 00-core.md" % token
    for token in sorted(vn.BANNED):
        assert token in core, "banned token `%s` missing from 00-core.md" % token


def test_no_retired_rule_files_remain():
    for retired in ("aws-resources.md", "data-lake.md", "secrets.md"):
        assert retired not in all_rule_text(), \
            "%s was retired; its content moved elsewhere" % retired
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -k drift -v`

Expected: `test_every_pattern_is_documented_in_its_rule_file` FAILS listing ~29 entries (six rule files absent). `test_no_retired_rule_files_remain` FAILS on all three. `test_core_carries_the_vocabularies` may pass — the current `00-core.md` already names the vocabularies.

- [ ] **Step 3: Commit the failing test deliberately**

```bash
git add architecture-design/standards/hooks/test_validate_naming.py
git commit -m "test: assert rules and hook patterns agree (fails until rules land)

Deliberately red. Tasks 7-11 write the rule files that turn it green."
```

---

### Task 7: `00-core.md`

The file every other rule cites. It carries what the lookup table does not tabulate: vocabularies, banned tokens, cadence, zero-padding, length limits, and the legacy freeze.

**Files:**
- Modify: `architecture-design/standards/rules/naming/00-core.md` (full rewrite)

**Interfaces:**
- Consumes: nothing.
- Produces: the vocabularies `test_core_carries_the_vocabularies` asserts.

- [ ] **Step 1: Rewrite the file**

Replace the entire contents with:

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "**/infra/**/*.tfvars"
  - "**/drafts/**/*.json"
  - "**/glue_jobs/**/*.py"
  - "**/lambda/**/*.py"
  - "**/schemas/**"
  - "**/scripts/*.py"
  - "stacks/*/infrastructure/**"
  - "stacks/*/src/**"
  - "stacks/*/domains/**"
  - "domains/*/infrastructure/**"
---
# UIF naming — core vocabulary

Name every **newly created** resource `uif-{stack}-{env}-{operation}`: `uif-`, then the
stack that owns it, then the environment, then what the resource does. Per-resource
forms live in the sibling rule files; this file is the vocabulary they all share.

✅ `uif-echo-dev-bronze-ingest`, `uif-beacon-dev-export`, `uif-echo-dev-pipeline-config`
❌ `echo-dev-bronze-ingest` (no framework prefix) · `uif-echo-bronze-ingest` (no env)

## Legacy names — do not rename

Existing `echo-{env}-*` resources predate this convention. **Never change the `name`
of a deployed resource in Terraform** — it forces replacement, and for S3 buckets,
DynamoDB tables, SQS queues, ECR repositories and Secrets Manager secrets that means
data loss. Apply this convention to newly created resources only. Renames happen
through an explicit, planned migration.

Non-compliant live names are non-compliant **by design** until migrated. Do not offer
to fix them.

## Vocabularies

| Slot | Values |
|---|---|
| `stack` | `beacon, echo, event, harbor` — the module that owns the resource |
| `env` | `dev, prod, uat` |
| `layer` | `bronze, silver, gold` — medallion layers only |
| `operation` | `ingest, transform, reconcile, quality-check, analyze, replicate, dispatch, deliver, control, export` |
| `target` | `s3, gcs, bq` — always explicit, no unmarked default |
| `cadence` | `30min, 4hr, daily, hourly` — closed. Flag `weekly`, `nightly`, `2hr` as outside the vocabulary rather than inventing a token. |
| `instance` | `slot-NN` — zero-padded to two digits |

✅ `uif-echo-dev-schedule-bronze-ingest-daily-slot-00`
❌ `uif-echo-dev-schedule-bronze-ingest-nightly` · `...-slot-0`

## Banned tokens — no phase, ticket, status or version words

Never use: `batch2`, `fix12`, `new`, `old`, `phase0`, `spike`, `tmp`, `v2`, or ticket
keys such as `UIFPE-254`.

✅ `uif-echo-dev-probe-gcs-iceberg` ❌ `uif-echo-dev-phase0-iceberg-gcp-probe`

Exception: EventBridge and SQS subscription names carry a `{version}` token to match the
registry — see `eventing.md`.

## Delimiters — the platform decides

| Delimiter | Form | Used by |
|---|---|---|
| hyphen | `uif-echo-dev-silver-transform-bq` | most AWS resources |
| underscore | `uif_echo_dev_silver_tep` | Glue catalog databases and tables |
| slash | `uif/echo/dev/db/tep` | Secrets Manager, S3 key prefixes |
| platform-prefixed | `/aws/lambda/uif-echo-dev-dispatch` | CloudWatch log groups |
| upper-underscore | `UIF_ECHO_DEV_SILVER_IPM` | BigQuery datasets — retained, not tabulated (see `catalog.md`) |

## Length limits

AWS platform caps, not convention choices. The 32-char ALB and target-group limits and
the 63-char bucket limit bite first — count before committing to a name, and shorten the
`operation`, never drop `env`.

| Artefact | Limit | Artefact | Limit |
|---|---|---|---|
| ALB, ALB target group | 32 | Glue job, ECS, DynamoDB, Glue database | 255 |
| S3 bucket | 63 | ECR repo, SNS topic, EventBridge bus | 256 |
| EventBridge rule, IAM role, Lambda, CloudFront function | 64 | Secrets Manager secret, CloudWatch log group | 512 |
| Step Functions, SQS queue | 80 | API Gateway, IAM policy | 128 |
```

- [ ] **Step 2: Run the core test to verify it passes**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py::test_core_carries_the_vocabularies -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add architecture-design/standards/rules/naming/00-core.md
git commit -m "docs: rewrite 00-core.md against the new naming standard

Drops the account-prefixed S3 form, adds the full length-limit table,
keeps the vocabularies and the legacy freeze."
```

---

### Task 8: `compute-etl.md` and `catalog.md`

The Glue, Lambda, ECS, ECR and Step Functions forms, plus catalog databases and tables. Two files because their file surfaces differ — compute names live in Terraform, catalog names in Python and schemas.

**Files:**
- Create: `architecture-design/standards/rules/naming/compute-etl.md`
- Modify: `architecture-design/standards/rules/naming/catalog.md`

**Interfaces:**
- Consumes: `PATTERNS` entries whose `rule` is `compute-etl.md` (9 types) or `catalog.md` (2 types).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Create `compute-etl.md`**

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Compute and ETL resource names

Vocabulary, length limits and the legacy-rename prohibition live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| Glue job | `aws_glue_job` | `uif-{stack}-{env}-[layer]-{operation}[-{target}]` | `uif-echo-dev-silver-transform-bq` |
| Glue connection — network | `aws_glue_connection` | `uif-{stack}-{env}-{type}` | `uif-echo-dev-vpc-network` |
| Glue connection — per source | `aws_glue_connection` | `uif-{stack}-{env}-{system-type}-{system}` | `uif-echo-dev-db-tep` |
| Lambda function | `aws_lambda_function` | `uif-{stack}-{env}-{operation}` or `uif-{stack}-{env}-{subject}-controller` | `uif-echo-dev-dispatch`, `uif-echo-dev-pipeline-controller` |
| Lambda layer | `aws_lambda_layer_version` | `uif-{stack}-{env}-{purpose}-layer` | `uif-echo-dev-oracle-layer` |
| ECS cluster | `aws_ecs_cluster` | `uif-{stack}-{env}-cluster` | `uif-beacon-dev-cluster` |
| ECS service | `aws_ecs_service` | `uif-{stack}-{env}-[{component}-]service` | `uif-beacon-dev-pipeline-service` |
| ECS task definition | `aws_ecs_task_definition` | `uif-{stack}-{env}-{component}-task-definition` | `uif-beacon-dev-pipeline-task-definition` |
| ECS container | — | `{component}` — bare, scoped to its task definition | `pipeline` |
| ECR repository | `aws_ecr_repository` | `uif-{stack}-{env}-{component}-ecr-repo` | `uif-beacon-dev-pipeline-ecr-repo` |
| Step Functions | `aws_sfn_state_machine` | `uif-{stack}-{env}-{operation}` | `uif-beacon-dev-export` |

## One cluster per stack, component tokens once there are several

An ECS cluster carries no component token — there is one per stack. A service takes one
as soon as a stack runs more than a single service. A container name is bare: it is
already scoped by its task definition, so repeating the stack and env there is noise.

## Name the target explicitly

Any resource that could write to more than one storage destination names that
destination. Required only where sibling resources differ by destination.

✅ `uif-echo-dev-silver-transform-bq` and `uif-echo-dev-silver-transform-gcs`
❌ `uif-echo-dev-silver-bq-native` — hides that it is a transform.

## Step Functions are named by operation, not by pattern

✅ `uif-beacon-dev-export` ❌ `uif-beacon-dev-export-state-machine` — the type is
already in the ARN.
```

- [ ] **Step 2: Rewrite `catalog.md`**

Replace the whole file:

```markdown
---
paths:
  - "**/glue_jobs/**/*.py"
  - "**/schemas/**"
  - "**/scripts/register_*.py"
  - "stacks/*/src/**/*.py"
---
# Catalog and dataset names

Vocabulary and the legacy-rename prohibition live in `00-core.md`.

| Object | Terraform type | Form | Example |
|---|---|---|---|
| Glue database — Silver | `aws_glue_catalog_database` | `uif_{stack}_{env}_{layer}_{source_system}` | `uif_echo_dev_silver_tep`, `uif_echo_dev_silver_mdm` |
| Glue database — Gold | `aws_glue_catalog_database` | `uif_{stack}_{env}_{layer}_{domain}` | `uif_echo_dev_gold_product` |
| Glue / Iceberg table | `aws_glue_catalog_table` | `{entity}` or `api_{entity}_view` | `mtl_system_items_b`, `api_product_view` |

**Silver is keyed by source system; Gold is keyed by business domain.** This is the one
place the two layers differ in how their database is named, and it follows from what each
layer is: Silver mirrors a source, Gold models an entity.

## A table name is the entity only

Never repeat what the database already states, and never carry a hyphen.

✅ `uif_echo_dev_gold_product.product`, `uif_echo_dev_gold_product.api_product_view`
❌ `uif_echo_dev_gold_product.gold_product_view` — restates the layer and domain
❌ `uif_echo_dev_gold_product.mtl-system-items-b` — a hyphenated S3 key segment carried
into a table name; convert to snake_case

Gold view names describe the tier (`api_product_view`), not the pipeline that built them.

## Where this lands in code and config

| Field | Rule |
|---|---|
| `silver.database` | `uif_{stack}_{env}_silver_{source_system}` |
| `silver.table` | entity only — no domain, no layer, no hyphen |
| `schemas/gold/{domain}/*.json` | table names inside a schema follow the entity-only rule |
| databases auto-created at registration | built from the same `{layer}_{key}` pair, so the draft must be right before registering |

## BigQuery — retained, not tabulated

The current standard does not cover BigQuery or GCS. These forms are **retained from the
previous convention** because GCP-native pipelines exist and would otherwise be
ungoverned. They are not ratified by the current standard and should be confirmed with
its owner.

| Object | Form | Example |
|---|---|---|
| BigQuery dataset | upper-underscore | `UIF_ECHO_DEV_SILVER_IPM` |
| BigQuery table | snake_case entity only | `fpi_par_lines` |
```

- [ ] **Step 3: Run the drift test to confirm progress**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py::test_every_pattern_is_documented_in_its_rule_file -v`

Expected: still FAILS, but the 11 compute-etl and catalog types no longer appear in the missing list. Confirm by reading the assertion output.

- [ ] **Step 4: Commit**

```bash
git add architecture-design/standards/rules/naming/compute-etl.md architecture-design/standards/rules/naming/catalog.md
git commit -m "docs: compute/ETL and catalog naming rules

Adds ECS, ECR, Step Functions, Lambda layers and Glue connections.
Silver databases are now keyed by source system, Gold by domain."
```

---

### Task 9: `storage.md` and `dynamodb-attributes.md`

Buckets, the three key-prefix shapes, DynamoDB tables, and the new attribute conventions.

**Files:**
- Create: `architecture-design/standards/rules/naming/storage.md`
- Create: `architecture-design/standards/rules/naming/dynamodb-attributes.md`
- Delete: `architecture-design/standards/rules/naming/data-lake.md`

**Interfaces:**
- Consumes: `PATTERNS` entries whose `rule` is `storage.md` (2 types).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Create `storage.md`**

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "**/infra/**/*.tfvars"
  - "stacks/*/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tfvars"
  - "domains/*/infrastructure/**/*.tf"
  - "**/drafts/**/*.json"
  - "stacks/*/domains/**/*.json"
---
# Storage resource names and key prefixes

Vocabulary, length limits and the legacy-rename prohibition live in `00-core.md`.

## S3 buckets

| Purpose | Terraform type | Form | Example |
|---|---|---|---|
| Data lake | `aws_s3_bucket` | `uif-{stack}-{env}-{layer}` | `uif-echo-dev-bronze` |
| Supporting | `aws_s3_bucket` | `uif-{stack}-{env}-{purpose}` | `uif-echo-dev-glue-scripts` |
| UI | `aws_s3_bucket` | `uif-{stack}-{env}-ui` | `uif-echo-dev-ui` |

There is **no account-id prefix**. The previous convention put the account id first
(`283556840805-s3-uif-echo-dev-ui`); the current standard does not, and this is
confirmed rather than an omission. Bucket names are globally unique across AWS, so the
`uif-{stack}-{env}-` stem is what carries that uniqueness — do not add a suffix or an
account segment to work around a collision. Raise one with the standard's owner instead.

## Key prefixes — three shapes, one per layer

```
Bronze   {bucket}/{source_system}/{data_domain}/{table}/_extract_date=YYYY-MM-DD/
Silver   {bucket}/{source_system}/{data_domain}/{table}/
Gold     {bucket}/{entity}/
```

✅ `tep/product/mtl-system-items-b/_extract_date=2026-09-04/`
✅ `tep/product/mtl-system-items-b/`
✅ `product/`
❌ `product/mtl-system-items-b/` on a Bronze bucket — no `{source_system}`
❌ `tep/product/` on a Gold bucket — Gold carries no domain segment

Bronze is append-only with one partition per run; the `_extract_date=` segment is part
of the key, not a taxonomy segment. Silver and Gold prefixes are Iceberg roots, with
`data/` and `metadata/` beneath them. Segments are lower-case and hyphenated. A trailing
slash is required on every path field.

## DynamoDB tables

| Terraform type | Form | Example |
|---|---|---|
| `aws_dynamodb_table` | `uif-{stack}-{env}-{subject}-{kind}` | `uif-echo-dev-pipeline-config`, `uif-echo-dev-pipeline-metrics`, `uif-echo-dev-pipeline-analysis` |

**Name the concept, not the engine.** The subject is what the rows are about, not what
writes them.

✅ `uif-echo-dev-pipeline-metrics` ❌ `uif-echo-dev-glue-job-metrics` — `glue-job` names
the engine that happens to produce the rows today.

## GCS — retained, not tabulated

The current standard does not cover GCS. Retained from the previous convention because
GCP-native pipelines exist: GCS keys follow Oracle schema names in snake_case
(`fipm/fpi_par_sales/`), never a business domain. Not ratified by the current standard.
```

- [ ] **Step 2: Create `dynamodb-attributes.md`**

```markdown
---
paths:
  - "**/drafts/**/*.json"
  - "stacks/*/domains/**/*.json"
  - "**/schemas/**"
---
# DynamoDB attribute names

Item attributes, not resource names. Table names live in `storage.md`.

| Kind | Form | Examples |
|---|---|---|
| scalar | `{concept}_{type}` | `pipeline_id`, `last_processed_at`, `watermark_col` |
| map | `{concept}` — singular, no suffix | `extract`, `silver`, `reconcile` |
| list | `{concept}_{type}s` — plural marks the list | `primary_key_cols`, `date_cols`, `tags` |
| boolean | bare predicate — no `is_`, no `has_` | `enabled`, `dry_run` |

## The scalar type suffix is closed

`_arn`, `_at`, `_col`, `_count`, `_date`, `_id`, `_mode`, `_name`, `_path`.

A scalar names its concept and then its type, so a reader knows what they will get before
they read the value. `last_processed_at` is a timestamp; `watermark_col` is a column name,
not the value in it.

## Why maps are singular and bare

A map is read *into*, not read *as* a value — `cfg["extract"]["mode"]`, never
`cfg["extract_map"]`. Adding a type suffix to a map implies it holds a single value.

A list is plural, and its suffix names the *element*: `primary_key_cols` is a list of
column names. `primary_keys_col` and `primary_key_col_list` are both wrong — the first
pluralises the wrong token, the second restates the container.

## Booleans carry no prefix

✅ `enabled`, `dry_run` ❌ `is_enabled`, `has_dry_run` — the type is already evident from
the value, and the prefix reads as noise in every conditional that uses it.

> This file is guidance only. Unlike resource names, attribute names are not
> mechanically enforced by the hook: judging arbitrary JSON keys produces false
> positives on legitimate drafts, and a hook that denies correct work is worse than one
> that misses. Enforcement is a follow-up once the false-positive rate is understood.
```

- [ ] **Step 3: Delete the retired file**

```bash
git rm architecture-design/standards/rules/naming/data-lake.md
```

- [ ] **Step 4: Run the drift test to confirm progress**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -k drift -v`

Expected: `test_every_pattern_is_documented_in_its_rule_file` still FAILS on eventing, networking, identity and observability types only. `test_no_retired_rule_files_remain` still fails on `aws-resources.md` and `secrets.md`.

- [ ] **Step 5: Commit**

```bash
git add architecture-design/standards/rules/naming/storage.md architecture-design/standards/rules/naming/dynamodb-attributes.md
git commit -m "docs: storage and DynamoDB attribute naming rules

Drops the account-id bucket prefix, adds the three layer-specific key
prefix shapes, and introduces attribute conventions as guidance."
```

---

### Task 10: `eventing.md`, `networking.md`, `observability.md`

Three files, all Terraform-scoped, covering the twelve remaining hyphen-form types.

**Files:**
- Create: `architecture-design/standards/rules/naming/eventing.md`
- Create: `architecture-design/standards/rules/naming/networking.md`
- Create: `architecture-design/standards/rules/naming/observability.md`

**Interfaces:**
- Consumes: `PATTERNS` entries whose `rule` is one of these three files (4 + 6 + 3 types).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Create `eventing.md`**

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Eventing and messaging names

Vocabulary, cadence set and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| EventBridge bus | `aws_cloudwatch_event_bus` | `uif-{stack}-{env}-[{domain}-]bus` | `uif-event-dev-bus`, `uif-event-dev-product-bus` |
| Rule — schedule | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-schedule-{layer}-{operation}-{cadence}[-slot-NN]` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-00` |
| Rule — event | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-on-{source}-{event}-{operation}[-{target}]` | `uif-echo-dev-on-silver-succeeded-replicate-bq` |
| Rule — subscription | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-on-{domain}-{subdomain}-{operation}-{version}` | `uif-event-dev-on-product-product-created-v1` |
| SQS queue | `aws_sqs_queue` | `uif-{stack}-{env}-{operation}[.{type}]` | `uif-echo-dev-dispatch`, `uif-echo-dev-dispatch.fifo` |
| SQS queue — subscription | `aws_sqs_queue` | `uif-{stack}-{env}-{consumer}-{domain}-{subdomain}-{operation}-{version}[.{type}]` | `uif-event-dev-pmc-product-product-created-v1` |
| SQS dead-letter queue | `aws_sqs_queue` | `{queue-it-backs}-dlq` | `uif-echo-dev-dispatch-dlq` |
| SNS topic | `aws_sns_topic` | `uif-{stack}-{env}-{subject}-alert` | `uif-echo-dev-pipeline-alert` |

## A rule declares its trigger type

`schedule-` sends whoever is debugging to the cron expression; `on-` to the event pattern
and the emitting service. Use exactly one.

❌ `uif-echo-dev-reconcile-slot-0` — no layer, no cadence, no trigger type
❌ `uif-echo-dev-silver-succeeded-bq` — a destination sitting in the operation slot

Token breakdown of `uif-echo-dev-on-silver-succeeded-replicate-bq`: `echo` stack, `dev`
env, `on` trigger, `silver` **source** — the emitting job, not the layer slot —
`succeeded` event, `replicate` operation, `bq` target.

## The account bus stays `default`

An EventBridge bus takes a `{domain}` token only once domains need isolating. The
account's own bus is named `default` by AWS and cannot be renamed; do not try.

## FIFO and dead-letter suffixes

Standard queues are unmarked. FIFO takes AWS's mandatory `.fifo` suffix — it is a
platform requirement, not a UIF token. A dead-letter queue appends `-dlq` to the name of
the queue it backs, so the pairing is readable at a glance:
`uif-event-dev-pmc-product-product-created-v1-dlq`.

## Version tokens are allowed here

`00-core.md` bans version words. Subscription rules and subscription queues are the
exception: their `{version}` token matches the registered event contract's version, so a
`-v1` there is a contract reference, not a phase marker.
```

- [ ] **Step 2: Create `networking.md`**

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Networking and edge names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| Application Load Balancer | `aws_lb` | `uif-{stack}-{env}-{purpose}-alb` | `uif-echo-dev-api-alb` |
| ALB target group | `aws_lb_target_group` | `uif-{stack}-{env}-{purpose}-tg` | `uif-echo-dev-api-tg` |
| API Gateway HTTP API | `aws_apigatewayv2_api` | `uif-{stack}-{env}-[{purpose}-]api` | `uif-echo-dev-authentication-api` |
| CloudFront distribution | `aws_cloudfront_distribution` | `uif-{stack}-{env}-{purpose}` | `uif-beacon-dev-ui` |
| CloudFront function | `aws_cloudfront_function` | `uif-{stack}-{env}-{operation}` | `uif-beacon-dev-strip-api-prefix` |
| Security group | `aws_security_group` | `uif-{stack}-{env}-{purpose}-sg` | `uif-echo-dev-eni-sg` |
| VPC endpoint | `aws_vpc_endpoint` | `uif-{stack}-{env}-{service}-endpoint` | `uif-echo-dev-secretsmanager-endpoint` |

## The 32-character cap bites here first

`aws_lb` and `aws_lb_target_group` are capped at **32 characters**, the tightest limit in
the estate. `uif-` plus stack, env, purpose and `-alb` leaves roughly fourteen characters
for `{purpose}` once the stack is `beacon` and the env is `prod`. Count before committing,
and shorten the purpose — never drop `env`.

## Two resources have no name attribute

A **CloudFront distribution** has no `name`; put the name in its `comment` field. A **VPC
endpoint** is named through its `Name` tag, not a name argument. The hook cannot read
either, so these two are convention-only — the rule is the enforcement.
```

- [ ] **Step 3: Create `observability.md`**

```markdown
---
paths:
  - "**/infra/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Observability names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| CloudWatch log group | `aws_cloudwatch_log_group` | `/{aws-path}/uif-{stack}-{env}[-{component}]` | `/aws/lambda/uif-echo-dev-dispatch`, `/ecs/uif-beacon-dev-pipeline`, `/aws/apigateway/uif-echo-dev` |
| CloudWatch dashboard | `aws_cloudwatch_dashboard` | `uif-{stack}-{env}-{subject}` | `uif-echo-dev-silver-bq-replication` |
| CloudWatch alarm | `aws_cloudwatch_metric_alarm` | `uif-{stack}-{env}-{subject}-{condition}` | `uif-echo-dev-silver-bq-replicator-failed` |

## AWS owns the log-group prefix

A log group's leading path is dictated by the service that writes to it — `/aws/lambda/`,
`/aws/apigateway/`, `/ecs/`. The UIF stem follows it rather than replacing it. Do not
invent a prefix, and do not omit one to make the name look like the others.

## An alarm names the condition, not the severity

The `{condition}` token says what happened, so the alarm reads as a sentence in a
notification.

✅ `uif-echo-dev-silver-bq-replicator-failed` ❌ `uif-echo-dev-silver-bq-critical` —
severity belongs in the alarm's configuration, not its name.
```

- [ ] **Step 4: Run the drift test**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -k drift -v`

Expected: `test_every_pattern_is_documented_in_its_rule_file` now FAILS only on the three `identity-secrets.md` types. `test_no_retired_rule_files_remain` still fails on two files.

- [ ] **Step 5: Commit**

```bash
git add architecture-design/standards/rules/naming/eventing.md architecture-design/standards/rules/naming/networking.md architecture-design/standards/rules/naming/observability.md
git commit -m "docs: eventing, networking and observability naming rules

Adds SQS, SNS, EventBridge bus and subscription rules, ALB/TG/API GW,
CloudFront, security groups, VPC endpoints, log groups and alarms."
```

---

### Task 11: `identity-secrets.md`, and retire the last two files

The final three patterns, and the deletions that turn the drift test green.

**Files:**
- Create: `architecture-design/standards/rules/naming/identity-secrets.md`
- Delete: `architecture-design/standards/rules/naming/secrets.md`
- Delete: `architecture-design/standards/rules/naming/aws-resources.md`

**Interfaces:**
- Consumes: `PATTERNS` entries whose `rule` is `identity-secrets.md` (3 types).
- Produces: a green `test_every_pattern_is_documented_in_its_rule_file`.

- [ ] **Step 1: Create `identity-secrets.md`**

```markdown
---
paths:
  - "**/infra/iam*.tf"
  - "stacks/*/infrastructure/**/iam*.tf"
  - "domains/*/infrastructure/**/iam*.tf"
  - "**/scripts/*secret*.py"
  - "**/lambda/**/*.py"
---
# Identity and secret names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| IAM role | `aws_iam_role` | `uif-{stack}-{env}-{service}-role` | `uif-echo-dev-glue-role` |
| IAM policy | `aws_iam_policy` | `uif-{stack}-{env}-{service}-policy` | `uif-echo-dev-glue-policy` |
| Secrets Manager secret | `aws_secretsmanager_secret` | `uif/{stack}/{env}/{system-type}/{system}` | `uif/echo/dev/db/tep`, `uif/echo/uat/cloud/gcp` |

## Keep the role/policy pairing

A role and the policy attached to it differ by their last token only, so the pair is
obvious in a console listing and in a diff. `uif-echo-dev-glue-role` takes
`uif-echo-dev-glue-policy` — not `uif-echo-dev-glue-job-policy`.

## Every token boundary in a secret is a slash

```
uif/{stack}/{env}/{system-type}/{system}
```

✅ `uif/echo/dev/db/tep` · `uif/echo/uat/cloud/gcp`
❌ `uif/echo/uat/cloud/gcp-ipm` — a policy scoped to `.../cloud/*` no longer matches it

A hyphen where a slash belongs silently drops the secret out of every wildcard-scoped IAM
policy. Nothing fails at creation — the secret resolves by full name — so it fails later
at `GetSecretValue`, from a job, in an environment nobody was watching.

There is **no `gip` segment.** The previous convention had one
(`uif/echo/dev/gip/db/tep`); the current standard does not.

## When adding a secret

1. Name it in slash form, one token per segment.
2. Check the resource ARNs in the stack's IAM policy actually cover the new path prefix.
   Existing statements scope by prefix — a new `uif/echo/*` secret is not covered by a
   statement scoped to `secret:echo/*` until one is added.
3. Reference it from config by ARN, never by reconstructing the name in code.
```

- [ ] **Step 2: Delete the two retired files**

```bash
git rm architecture-design/standards/rules/naming/secrets.md architecture-design/standards/rules/naming/aws-resources.md
```

- [ ] **Step 3: Run the whole suite to verify it is green**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS, including both drift assertions. If `test_every_pattern_is_documented_in_its_rule_file` still fails, the assertion output names the exact resource type and file — add the missing table row.

- [ ] **Step 4: Commit**

```bash
git add -A architecture-design/standards/rules/naming/
git commit -m "docs: identity and secret naming rules; retire two rule files

Secret paths lose the gip segment. aws-resources.md and data-lake.md are
retired, their content redistributed. The anti-drift test is now green."
```

---

### Task 12: Give the anti-drift test a runner

A test nobody runs prevents nothing — and an unrun check is what produced the drift being fixed. No existing workflow fires on `architecture-design/standards/**`: `standards-review.yml` is scoped to `domains/**`, advisory-only, and inert (its `ANTHROPIC_API_KEY` is absent and will not be provisioned).

**Files:**
- Create: `.github/workflows/standards-ci.yml`

**Interfaces:**
- Consumes: `architecture-design/standards/hooks/test_validate_naming.py` from Tasks 1–11.
- Produces: a required status check on changes to the standards package.

- [ ] **Step 1: Create the workflow**

```yaml
name: Standards CI

# The naming rules and the hook that enforces them must agree. They drifted
# once already — three rule assertions became the opposite of the standard and
# one hook check began denying compliant work — because nothing checked them.
# This job is that check. Unlike standards-review.yml it is mechanical, needs
# no API key, and blocks.

on:
  pull_request:
    paths:
      - 'architecture-design/standards/**'
  push:
    branches: [develop]
    paths:
      - 'architecture-design/standards/**'

permissions:
  contents: read

jobs:
  naming-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install pytest
        run: python -m pip install --quiet pytest

      - name: Rules and hook agree
        run: python -m pytest architecture-design/standards/hooks/test_validate_naming.py -v
```

- [ ] **Step 2: Verify the workflow parses**

Run: `py -c "import yaml,sys; yaml.safe_load(open('.github/workflows/standards-ci.yml')); print('ok')"`

Expected: `ok`. If PyYAML is unavailable, run `git add` and rely on GitHub's own validation on the PR — but prefer checking locally.

- [ ] **Step 3: Run the test exactly as CI will**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS. This is the same command the workflow runs, so a green local run is a green CI run.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/standards-ci.yml
git commit -m "ci: run the naming anti-drift test on standards changes

standards-review.yml is scoped to domains/** and inert without an API key;
this job is mechanical, blocks, and fires on the standards package."
```

---

### Task 13: Version bump and setup guide

The package version and the document engineers actually read when installing.

**Files:**
- Modify: `architecture-design/standards/VERSION`
- Modify: `architecture-design/standards/Setup-Guide.md`

**Interfaces:**
- Consumes: the nine rule files from Tasks 7–11.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Bump the version**

Replace the contents of `VERSION` with:

```
2.0.0
```

Major, not minor: names that were compliant no longer are, and the hook's verdicts change. Anyone still on `1.0.0` gets actively wrong guidance, not merely incomplete guidance.

- [ ] **Step 2: Replace the contents table in `Setup-Guide.md`**

Replace the table under `## Contents` with:

```markdown
| Path | Loads when you open |
|---|---|
| `rules/naming/00-core.md` | any UIF file — stem, vocabularies, banned tokens, delimiters, length limits, legacy freeze |
| `rules/naming/compute-etl.md` | Terraform — Glue, Lambda, ECS, ECR, Step Functions |
| `rules/naming/storage.md` | Terraform, `*.tfvars`, drafts — S3 buckets, key prefixes, DynamoDB tables |
| `rules/naming/catalog.md` | Glue jobs, `schemas/**`, registration scripts — catalog databases and tables |
| `rules/naming/eventing.md` | Terraform — EventBridge, SQS, SNS |
| `rules/naming/networking.md` | Terraform — ALB, target group, API Gateway, CloudFront, security groups, VPC endpoints |
| `rules/naming/identity-secrets.md` | `iam*.tf`, secret scripts, Lambda — IAM roles and policies, Secrets Manager |
| `rules/naming/observability.md` | Terraform — log groups, dashboards, alarms |
| `rules/naming/dynamodb-attributes.md` | drafts, `schemas/**` — item attribute naming (guidance only) |
| `hooks/validate_naming.py` | `PreToolUse` guard on `Edit`/`Write`/`MultiEdit` |
```

- [ ] **Step 3: Update the "what the hook refuses" list**

Replace that section's bullets with:

```markdown
- renaming a deployed resource whose rename destroys data or forces replacement —
  21 types, including S3 buckets, DynamoDB tables, SQS queues, ECR repositories,
  Secrets Manager secrets and Glue catalog databases. Each denial names what would
  be lost.
- newly introduced literal names that break the stem, a required suffix, a closed
  vocabulary, or an AWS length limit — 29 resource types
- draft key prefixes that break the layer-specific shapes in `storage.md`

Values already present in the file on disk are treated as pre-existing and never
judged. The hook cannot see interpolated values (`local.name`, `var.name_prefix`), so
it catches literals only — the rule files remain the primary mechanism and the hook a
backstop. It no-ops outside a UIF-shaped layout and fails open on any internal error.
```

- [ ] **Step 4: Fix the troubleshooting examples**

In the Troubleshooting section, replace the `aws-resources.md` reference (that file no longer exists) with:

```markdown
- Path-scoped rules that load everywhere are also a failure. `compute-etl.md` should
  load for `infra/glue_jobs.tf` and *not* for `lambda/pipeline_controller.py`;
  `catalog.md` should do the opposite.
```

- [ ] **Step 5: Add an upgrade note after `## Update`**

```markdown
### Upgrading from 1.x

1.x rules assert two forms the current standard has replaced: the account-id-prefixed
S3 bucket (`283556840805-s3-uif-echo-dev-ui`) and the `gip` segment in secret paths
(`uif/echo/dev/gip/db/tep`). They also name DynamoDB tables after the engine
(`glue-job-metrics`) rather than the concept (`pipeline-metrics`). Until you re-run the
installer you are being given guidance that is wrong, not merely incomplete. Check
`~/.claude/rules/naming/.installed-version`.
```

- [ ] **Step 6: Verify the guide describes what exists**

Run: `py -c "import os,re; d='architecture-design/standards/rules/naming'; files=set(f for f in os.listdir(d) if f.endswith('.md')); guide=open('architecture-design/standards/Setup-Guide.md',encoding='utf-8').read(); cited=set(re.findall(r'rules/naming/([a-z0-9-]+\.md)', guide)); print('missing from guide:', files-cited); print('cited but absent:', cited-files)"`

Expected: both sets empty.

- [ ] **Step 7: Commit**

```bash
git add architecture-design/standards/VERSION architecture-design/standards/Setup-Guide.md
git commit -m "docs: bump standards to 2.0.0 and refresh the setup guide

Nine rule files, the expanded hook refusal list, and an upgrade note
warning that 1.x asserts two now-superseded forms."
```

---

### Task 14: ADR-0014

`standards/README.md` requires an ADR for any change to a standard here. `0013` is taken, so this is `0014`.

**Files:**
- Create: `architecture-design/decisions/0014-naming-standard-refresh.md`

**Interfaces:**
- Consumes: the design spec and everything Tasks 1–13 built.
- Produces: the governance record.

- [ ] **Step 1: Write the ADR**

```markdown
# ADR-0014 — UIF Resource Name Lookup Becomes the Naming Standard

**Status:** Proposed
**Date:** 2026-09-08
**Author:** vaibhav.yenorkar@pearson.com
**Deciders:** framework owner
**Scope:** Which document is the naming standard of record, the three assertions it
supersedes, and what happens to the rules it does not tabulate. Does **not** rename any
deployed resource.
**Supersedes:** the naming convention encoded in `standards/` v1.0.0.

---

## Context

`architecture-design/standards/` shipped machine-readable rules and a `PreToolUse` hook
derived from an earlier naming convention. The **UIF Resource Name Lookup** is now the
standard, and the package had drifted from it in three ways: three rule assertions became
the opposite of the standard, roughly twenty-five resource types had no coverage, and one
hook check began denying edits the standard requires — a compliant Bronze key prefix
carries a `_extract_date=` partition segment, which the kebab-case check rejected.

Drift was possible because nothing tied the rules to the hook, and nothing ran a check on
either.

## Decision

1. The lookup table is the standard of record. Where it and the v1 rules disagree, it
   wins. Three assertions are superseded: S3 buckets lose the account-id prefix, secret
   paths lose the `gip` segment, and DynamoDB tables are named for the concept rather
   than the engine. Silver Glue databases are keyed by source system, not domain, and
   data-lake key prefixes take three layer-specific shapes.
2. Rule files mirror the standard's six groups, plus core, catalog and DynamoDB
   attributes — nine files. `catalog.md` stays separate from `compute-etl.md`, where the
   standard groups it, because catalog names appear in Python and schema files while
   compute names appear in Terraform; merging them would mis-scope both.
3. The hook is driven by one `PATTERNS` table covering 29 resource types, and a test
   asserts every pattern is documented in a rule file and every vocabulary matches. A CI
   job runs it on changes to the package.
4. **BigQuery and GCS rules are retained, not ratified.** The standard does not cover
   them; GCP-native pipelines exist and would otherwise be ungoverned. They are marked as
   retained pending an owner decision.
5. **Banned tokens, the closed cadence set, zero-padding and the AWS length limits are
   kept.** The lookup table does not tabulate them and does not contradict them. Length
   limits in particular are platform facts — dropping the 63-character bucket check would
   let the hook approve names AWS rejects.

## Consequences

**Becomes easier.** A resource type maps to one rule file and one hook pattern. Twenty-five
previously ungoverned types get both. The drift that prompted this becomes a test failure
rather than a discovery.

**Becomes harder.** Nine files to maintain instead of five. Two forms the estate already
uses are now non-compliant for new resources, so engineers must read the upgrade note
rather than pattern-matching on what they see nearby.

**Accepted.** The hook cannot judge interpolated names, and much of the estate is built
from `local.name` — so its reach is narrower than 29 patterns suggests; the rule files
remain the primary mechanism. Retained BigQuery and GCS rules are unsourced until an owner
ratifies or retires them.

## Confirmed during review

**S3 bucket global uniqueness.** Dropping the account-id prefix means a bucket name must
be unique across all of AWS, not merely within the account — which is what an account-id
prefix conventionally guarantees. Raised with the standard's owner and **confirmed
deliberate**: the `uif-{stack}-{env}-` stem carries the uniqueness. No account segment
and no collision suffix.
```

- [ ] **Step 2: Verify the ADR number is still free**

Run: `git -C . ls-tree --name-only origin/develop:architecture-design/decisions | Select-String "0014"`

Expected: no output. If `0014` has been taken since this plan was written, use the next free number and update the filename and heading together.

- [ ] **Step 3: Run the full suite one last time**

Run: `py -m pytest architecture-design/standards/hooks/test_validate_naming.py -v`

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add architecture-design/decisions/0014-naming-standard-refresh.md
git commit -m "docs: ADR-0014 — the lookup table becomes the naming standard

Records the three superseded assertions, the nine-file structure, the
retained BigQuery/GCS rules, and the open S3 uniqueness question."
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the blocking defect → Task 1; the
prefix grammar → Task 2; `PATTERNS` and the three superseded forms → Task 3; the ~25
uncovered types and the new limits → Task 4; the rename guard → Task 5; the anti-drift
test → Task 6; the nine rule files → Tasks 7–11; the CI runner open item → Task 12;
version, guide and distribution → Task 13; governance → Task 14. The spec's two D3/D4
preservation decisions appear in `catalog.md`, `storage.md` and `00-core.md`. The S3
uniqueness risk is carried into the ADR as an open question rather than silently resolved.

**Placeholder scan.** No TBD/TODO. Every code step carries real code; every markdown step
carries the file's actual content. Task 4 Step 3 deliberately says "make only the edits the
failures demand" — that is a discipline instruction, not a placeholder, and the one
foreseeable fix (the catalog-table stem exemption) is written out.

**Type consistency.** `check_name(value, rtype)` is named identically in Tasks 3, 4 and 5.
`PATTERNS` keys are Terraform resource type strings throughout, and its five value keys
(`delimiter`, `suffix`, `limit`, `rename`, `rule`) are used consistently in Tasks 3, 4, 5
and 6. `PARTITION_SEG` is created in Task 1 and consumed in Task 2. `layer_of` and
`PREFIX_SHAPES` are created in Task 2 and used nowhere later. `RENAME_REASON` is created in
Task 5 and asserted in the same task. `rule_text` / `all_rule_text` are created in Task 6
and used by Tasks 8–11's verification steps.

One inconsistency found and fixed while reviewing: Task 5's commit message says "21
replacement-forcing types", which is the count of `RENAME_REASON` entries, not of `PATTERNS`
rows (29) — the two numbers are different on purpose, since eight types have `rename: None`.
