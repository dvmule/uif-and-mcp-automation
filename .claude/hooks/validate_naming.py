#!/usr/bin/env python3
"""PreToolUse hook: UIF resource naming guard.

Rule files are context and cannot block anything. This hook is the enforcement layer.
It denies three classes of edit:

  1. Renames of deployed resources (aws_s3_bucket bucket/name, aws_glue_job name,
     *_bucket values in *.tfvars). Renaming an S3 bucket destroys it; renaming a Glue
     job forces replacement.
  2. Newly introduced literal resource names that break the stem, the closed
     vocabularies, or an Appendix A length limit.
  3. Newly introduced data-lake paths / dataset names that break Rule 8's cloud split.

Values that already exist in the file on disk are treated as pre-existing (legacy by
design) and are never judged. Anything that does not look like a UIF layout is a no-op,
so the hook stays inert in unrelated projects. Any internal error fails open.
"""
import json
import os
import re
import sys

# Mirrors the `stack` slot in rules/naming/00-core.md; adding a token means editing both.
# apicurio was sanctioned in the rule file on 2026-09-08 (owner decision, ADR-0012) and
# omitted here, and the guard then denied every uif-apicurio-* name as an unknown stack while
# the standard said it was legal. The pair is now checked in both directions by
# test_core_carries_the_vocabularies and test_every_stack_token_in_core_is_enforced, so that
# particular drift fails CI rather than going quiet.
STACKS = {"echo", "beacon", "event", "harbor", "apicurio", "orchestrator"}
ENVS = {"dev", "uat", "prod"}
# Throwaway namespaces (00-core.md "Throwaway namespaces", UIFPE-473): under
# delivery-lab/ the env slot must be the reserved lab token, never a real
# environment, so a lab can never collide with the estate - and everywhere
# else the lab token is rejected like any other unknown env. main() picks
# the active set per file; module scope defaults to the estate.
LAB_ENVS = {"poc"}
LAB_MARKER = re.compile(r"(^|/)delivery-lab/")
ACTIVE_ENVS = ENVS
CADENCES = {"30min", "4hr", "daily", "hourly"}
BANNED = {"phase0", "batch2", "fix12", "spike", "tmp", "new", "old", "v2"}

# Subscription rules and subscription queues carry a {version} token matching the
# registered event contract (eventing.md). A bare `v<N>` there is a contract
# reference, not the phase marker BANNED exists to catch.
VERSIONED_TYPES = {"aws_cloudwatch_event_rule", "aws_sqs_queue"}
VERSION_TOKEN = re.compile(r"^v\d+$")
CADENCE_SHAPED = re.compile(
    r"^(\d+(min|hr|hour|h|d|day)s?|weekly|nightly|monthly|yearly|annually)$")
TICKET_KEY = re.compile(r"^[A-Za-z]{2,}-\d+$")

# A real ticket key survives token-splitting only when rejoined, and is always
# upper-case (UIFPE-254). Lower-case joins are legitimate operation tokens
# (`ingest-01`, `slot-00`) and must pass.
TICKET_KEY_JOINED = re.compile(r"^[A-Z]{2,}-\d+$")

# Hive-style partition segments are part of the key, not a taxonomy segment:
# `_extract_date=2026-09-04`. They legitimately carry an underscore and an `=`,
# neither of which the kebab-case rule permits elsewhere.
PARTITION_SEG = re.compile(r"^_[a-z][a-z0-9_]*=[^/]+$")

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
    # Read from `identifier`, not `name`. 63 is the RDS cap (CreateDBInstance:
    # DBInstanceIdentifier, 1-63). Renaming it replaces the instance and the data goes
    # with it, so the posture is destructive rather than replace.
    "aws_db_instance":           {"delimiter": "hyphen", "suffix": "db", "limit": 63, "rename": "destructive", "rule": "storage.md"},
    # No documented AWS cap for DBSubnetGroupName, so no limit is asserted here rather
    # than a guessed one — an invented cap either denies a legal name or enforces nothing.
    "aws_db_subnet_group":       {"delimiter": "hyphen", "suffix": "db-subnet-group", "limit": None, "rename": "replace", "rule": "storage.md"},
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
    # aws_vpc_endpoint is deliberately ABSENT. It is named through a `Name` tag, not a name
    # argument, so ATTR_RE never matches it and the row it used to have could not fire —
    # inert config that made the guard look broader than it is. networking.md already says
    # the endpoint rule is convention-only, and that is now the whole truth.
    "aws_cloudfront_origin_access_control": {"delimiter": "hyphen", "suffix": None, "limit": 64, "rename": None, "rule": "networking.md"},
    # No documented cap for either apigatewayv2 name; see the aws_db_subnet_group note.
    "aws_apigatewayv2_vpc_link": {"delimiter": "hyphen", "suffix": "vpc-link", "limit": None, "rename": None, "rule": "networking.md"},
    "aws_apigatewayv2_authorizer": {"delimiter": "hyphen", "suffix": "authorizer", "limit": None, "rename": None, "rule": "networking.md"},
    # -- identity & secrets --
    "aws_iam_role":              {"delimiter": "hyphen", "suffix": "role", "limit": 64, "rename": "replace", "rule": "identity-secrets.md"},
    "aws_iam_policy":            {"delimiter": "hyphen", "suffix": "policy", "limit": 128, "rename": "replace", "rule": "identity-secrets.md"},
    # Inline role policies take the same form as managed ones, so the role/policy pairing
    # rule holds for both. Same 128-char PolicyName cap (PutRolePolicy). Renaming one is a
    # delete-and-create: no data is lost, but the role briefly holds neither policy during
    # the apply, so the posture is replace rather than None.
    "aws_iam_role_policy":       {"delimiter": "hyphen", "suffix": "policy", "limit": 128, "rename": "replace", "rule": "identity-secrets.md"},
    "aws_secretsmanager_secret": {"delimiter": "slash", "suffix": None, "limit": 512, "rename": "destructive", "rule": "identity-secrets.md"},
    # -- observability --
    "aws_cloudwatch_log_group":  {"delimiter": "platform", "suffix": None, "limit": 512, "rename": None, "rule": "observability.md"},
    "aws_cloudwatch_dashboard":  {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": None, "rule": "observability.md"},
    "aws_cloudwatch_metric_alarm": {"delimiter": "hyphen", "suffix": None, "limit": 255, "rename": None, "rule": "observability.md"},
}

# Why a rename is refused, per type. The generic message ("forces replacement")
# is true but unhelpful; an engineer needs to know what they would lose.
RENAME_REASON = {
    "aws_s3_bucket": "Terraform destroys and recreates the bucket — its data is lost.",
    "aws_dynamodb_table": "Terraform destroys and recreates the table — every item is lost.",
    "aws_sqs_queue": "The queue is recreated — in-flight messages are lost.",
    "aws_ecr_repository": "The repository is recreated — every stored image is lost.",
    "aws_secretsmanager_secret": "The secret is recreated under a new name, breaking every IAM statement scoped to the old path and every stored ARN that references it.",
    "aws_db_instance": "The instance is destroyed and recreated under the new identifier — the database and every row in it is lost, and a final snapshot is only taken if skip_final_snapshot is false.",
    "aws_db_subnet_group": "The subnet group is recreated; an instance still attached to it blocks the destroy, so the apply fails half-done.",
    "aws_iam_role_policy": "The inline policy is deleted and recreated, so the role holds neither during the apply — anything assuming it mid-apply loses those permissions.",
    "aws_glue_catalog_database": "The database is recreated — its table metadata is lost.",
    "aws_glue_catalog_table": "The table's catalog metadata is recreated.",
    "aws_glue_job": "The job is destroyed and recreated, losing its run history.",
    "aws_lambda_function": "The function is recreated — its version history and any published aliases go with it.",
    "aws_ecs_cluster": "The cluster is recreated; services registered to it must be recreated too.",
    "aws_ecs_service": "The service is recreated, which cycles every running task.",
    "aws_sfn_state_machine": "The state machine is recreated — execution history is lost.",
    "aws_cloudwatch_event_bus": "The bus is recreated; every rule attached to it must be recreated.",
    "aws_cloudwatch_event_rule": "The rule is recreated; its targets must be re-attached.",
    "aws_sns_topic": "The topic is recreated — every subscription is dropped.",
    "aws_lb": "The load balancer is recreated with a new DNS name, breaking anything pointing at the old one.",
    "aws_lb_target_group": "The target group is recreated and must be re-attached to its listener.",
    "aws_apigatewayv2_api": "The API is recreated with a new endpoint.",
    "aws_cloudfront_function": "The function is recreated and must be re-associated with its distribution.",
    "aws_security_group": "The group is recreated; every rule referencing it by id breaks.",
    "aws_iam_role": "The role is recreated — every trust policy and every ARN reference to it breaks.",
    "aws_iam_policy": "The policy is recreated; attachments must be re-made.",
}

# Deliberately specific: a bare `src/` or `schemas/` directory is not evidence of a UIF
# layout, and a hook that fires in unrelated repos is worse than one that misses.
LAYOUT_MARKERS = (
    re.compile(r"(^|/)ingestion-framework/"),
    re.compile(r"(^|/)infra/"),
    re.compile(r"(^|/)(drafts|glue_jobs|glue-jobs)/"),
    re.compile(r"(^|/)lambda/[^/]+\.py$"),
    re.compile(r"(^|/)stacks/[^/]+/(infrastructure|src|domains)/"),
    re.compile(r"(^|/)domains/[^/]+/infrastructure/"),
    # Labs are inspected too (UIFPE-473) - not to hold them to estate names,
    # but to enforce the opposite: a real env token inside delivery-lab is a
    # collision waiting to happen and is refused.
    re.compile(r"(^|/)delivery-lab/"),
    re.compile(r"^infrastructure/"),
)
RESOURCE_RE = re.compile(r'^\s*resource\s+"([a-z0-9_]+)"\s+"([A-Za-z0-9_.\-]+)"')
# Most resource types name themselves with `name` or `bucket`, but five PATTERNS
# types use a different argument entirely (aws_lambda_function's `function_name`,
# aws_lambda_layer_version's `layer_name`, aws_ecs_task_definition's `family`,
# aws_cloudwatch_dashboard's `dashboard_name`, aws_cloudwatch_metric_alarm's
# `alarm_name`). Without them here, tf_names() never captures a value for these
# types and check_name()/the rename guard can never fire for them. Reading a few
# extra argument names for types absent from PATTERNS is harmless: tf_names() keys
# on (rtype, label, attr) and check_name() returns None for anything PATTERNS
# doesn't list, so e.g. a DB parameter group's `family = "mysql8.0"` is read and
# then ignored.
# `identifier` is here for aws_db_instance, whose name is neither `name` nor an alias of it.
# Safe to add globally: the comment above applies — check_name() returns None for any type
# PATTERNS does not list, so an `identifier` on some other resource is read and then ignored.
NAME_ATTRS = ("name|bucket|function_name|layer_name|family|dashboard_name|alarm_name"
              "|identifier")
COMMENT = r"(?:#|//)"      # both HCL line-comment forms
ATTR_RE = re.compile(r'^\s*(' + NAME_ATTRS + r')\s*=\s*"([^"]*)"\s*$')
TFVAR_RE = re.compile(r'^\s*([A-Za-z0-9_]*bucket)\s*=\s*"([^"]*)"\s*$')

# 00-core.md permits a rename "through an explicit, planned migration". This is how a
# migration declares itself: a comment on its OWN line, either directly above the
# resource block or inside it. It must not go on the `name` line — ATTR_RE anchors to
# end-of-line, so a trailing comment stops the name being read at all and the guard
# would fall silent for the wrong reason.
#
#   # uif:rename-approved UIFPE-364
#   resource "aws_iam_role" "dispatch" {
#     name = "uif-echo-${var.environment}-dispatch-role"
#
# A `destructive` posture needs the data question answered explicitly as well, so the
# author cannot authorise data loss with the same marker used for a safe replacement:
#
#   # uif:rename-approved UIFPE-364 data:copied
#
# Known limitation: a marker left behind after its migration keeps that one resource
# renameable. It is committed and shows in review, so removing it is the reviewer's
# call — the gate forces a deliberate, traceable act, not a good plan.
APPROVAL_RE = re.compile(
    r'^\s*' + COMMENT + r'\s*uif:rename-approved\s+'
    r'(?P<ticket>[A-Za-z]{2,}-\d+)(?P<rest>.*)$')
# Closed vocabulary, and anchored to whitespace rather than a word boundary. A free
# `data:<word>` let prose satisfy the gate — `\b` matches after a hyphen, so a tail
# reading "replace-only, no-data:impact" authorised destroying a table. An
# authorisation grammar has to be checkable, so this is a closed set like `cadence`.
DATA_OUTCOMES = ("copied", "empty", "accepted-loss")
DATA_CLAUSE_RE = re.compile(
    r"(?:^|\s)data:(?:" + "|".join(DATA_OUTCOMES) + r")(?![\w-])")

# The one way to get this wrong that would otherwise pass in silence: a marker parked
# on the `name` line stops ATTR_RE matching, so the name goes untracked and the rename
# sails through unexamined. Caught explicitly and refused rather than left to chance.
MISPLACED_APPROVAL_RE = re.compile(
    r'^\s*(?:' + NAME_ATTRS + r')\s*=\s*"[^"]*"\s*' + COMMENT
    + r'.*uif:rename-approved')


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def looks_like_uif(path):
    p = path.replace("\\", "/")
    if not p.endswith((".tf", ".tfvars", ".json")):
        return False
    if p.endswith(".json"):  # only pipeline drafts carry the Rule 8 fields
        return bool(re.search(r"(^|/)drafts/", p)
                    or re.search(r"(^|/)stacks/[^/]+/domains/", p))
    return any(m.search(p) for m in LAYOUT_MARKERS)


def apply_edit(text, tool_input, tool_name):
    """Return the post-edit file text, or None if it cannot be reconstructed."""
    if tool_name == "Write":
        return tool_input.get("content", "")
    edits = tool_input.get("edits") or [tool_input]
    out = text
    for e in edits:
        old, new = e.get("old_string"), e.get("new_string")
        if old is None or new is None:
            return None
        if e.get("replace_all"):
            out = out.replace(old, new)
        else:
            out = out.replace(old, new, 1)
    return out


HEREDOC_RE = re.compile(r"<<[-~]?([A-Za-z_][A-Za-z0-9_]*)")
STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/")


def _depth_text(line):
    """The line with string bodies and comments removed, so brace counting is honest.
    Without this, `"${local.name_prefix}-x"` reads as a block opening and closing (108
    name values in this repo interpolate), and a comment can hide a brace."""
    s = STRING_RE.sub('""', line)
    s = BLOCK_COMMENT_RE.sub("", s)
    for lead in ("#", "//", "/*"):
        cut = s.find(lead)
        if cut != -1:
            s = s[:cut]
    return s


def _tf_lines(text):
    """Yield (line, rtype, label, depth) per line, where depth is the brace depth the
    line sits INSIDE: 1 is a resource's own body, 2+ is a nested block.

    Heredoc bodies and `/* */` comment bodies are skipped entirely, and neither can be
    read as code. Both matter: one of this repo's heredocs holds JavaScript, and a
    marker quoted inside a block comment — even one whose surrounding prose says the
    rename is NOT approved — would otherwise read as a live authorisation, while a
    stray brace in either would silently shift every following attribute's depth."""
    depth, rtype, label, heredoc, commented = 0, None, None, None, False
    for line in text.splitlines():
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        if commented:
            # Only the text after `*/` is live again. Anything before it is comment,
            # and an attribute or marker sharing that line is not read — they anchor
            # to the start of the line, so they could not be reached anyway.
            end = STRING_RE.sub('""', line).find("*/")
            if end == -1:
                continue
            commented = False
            depth += _brace_delta(line[end + 2:])
            continue
        yield line, rtype, label, depth
        bare = _depth_text(line)
        m = RESOURCE_RE.match(line)
        if m and depth == 0:
            rtype, label = m.group(1), m.group(2)
        depth += _brace_delta(bare)
        if depth <= 0:
            depth, rtype, label = 0, None, None
        h = HEREDOC_RE.search(bare)
        if h:
            heredoc = h.group(1)
            continue
        stripped = BLOCK_COMMENT_RE.sub("", STRING_RE.sub('""', line))
        # A `/*` inside a # or // comment opens nothing. Without this cut, prose
        # like `# paths outside domains/*/src/**` put the scanner into
        # block-comment mode for the rest of the file, so every name below it
        # vanished from tf_names() — 12 files in this repo were partly parsed.
        # _depth_text() already cuts line comments for the same reason.
        for lead in ("#", "//"):
            at = stripped.find(lead)
            if at != -1:
                stripped = stripped[:at]
        if "/*" in stripped:
            commented = True


def _brace_delta(s):
    return s.count("{") - s.count("}")


# ---------------------------------------------------------------- interpolation
# Almost every deployed name in this repo is built from a local, not typed as a
# literal — `"${local.uif_prefix}-gold-transform"`. check_name() used to return
# None for any value containing `${`, so the whole interpolated estate was
# invisible to the gate and drifted without CI noticing (ADR-0014 records the
# limitation). Resolving the locals makes those names judgeable.

# A trailing # or // comment must not hide the assignment: one idiomatic
# `# FROZEN until UIFPE-358` on a prefix line would drop that local from the map
# and silently switch resolution off for the whole directory.
LOCAL_ASSIGN_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"\s*(?:(?:#|//).*)?$')
LOCAL_INLINE_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
LOCALS_OPEN_RE = re.compile(r"^\s*locals\s*\{")
# Terraform's escape for a literal dollar-brace: $${x} renders as ${x} and is
# not a reference. Neutralised before matching so it is never substituted.
DOLLAR_ESCAPE_RE = re.compile(r"\$\$\{")
INTERP_RE = re.compile(r"\$\{([^{}]*)\}")
RESOLVE_PASSES = 8      # cycle guard: local.a -> local.b -> local.a never ends
RESOLVE_MAX_LEN = 4096  # a cycle with fan-out grows w**8 chars before passes run out


def tf_locals(text):
    """{local_name: raw_value} for string assignments in `locals {}`.

    Goes through _tf_lines() rather than scanning lines itself, so heredoc
    bodies and `/* */` comment bodies are skipped and brace depth stays honest.
    That matters here as much as in tf_names(): the map is last-writer-wins, so
    a commented-out `locals {}` block kept for a migration note would otherwise
    override the live prefix and the gate would deny compliant names against a
    value that is not in effect.

    Only depth 1 of a locals block: a nested block's attribute belongs to that
    block, not to the locals map.

    A local whose value is an EXPRESSION rather than a string is deliberately
    not captured — the echo stack's prefix is
        name_prefix = var.x_override != "" ? var.x_override : "echo-${var.environment}"
    and its real value comes from envs/*.tfvars, which differ per environment.
    Reading only the fallback branch would resolve dev to `echo-dev-*` and flag
    names that are in fact correct there, so those names stay unjudged."""
    found = {}
    in_locals = False
    for line, rtype, label, depth in _tf_lines(text):
        if depth == 0:
            # Only a `locals {` line opens a locals block; anything else at
            # depth 0 closes whatever we were in.
            m = LOCALS_OPEN_RE.match(line)
            in_locals = bool(m)
            if m and "}" in line[m.end():]:
                # single-line form: locals { name_prefix = "echo-dev" }
                inner = line[m.end():].rsplit("}", 1)[0]
                for k, v in LOCAL_INLINE_RE.findall(inner):
                    found[k] = v
                in_locals = False
            continue
        if not in_locals or rtype is not None or depth != 1:
            continue
        a = LOCAL_ASSIGN_RE.match(line)
        if a:
            found[a.group(1)] = a.group(2)
    return found


def resolve_interpolated(value, locals_map, env):
    """`value` with ${local.*} and ${var.environment} substituted, or None.

    None means "do not judge this name" — the same fail-open posture the gate
    had for every interpolated value. Anything we cannot resolve (${each.key},
    a var we do not know, a local declared in another module) leaves a `${`
    behind and disqualifies the whole name, because a partially resolved name
    would be judged against tokens the engineer never wrote."""
    if not value:
        return None
    # $${x} is Terraform's escape for a literal ${x}; blanking the marker stops
    # INTERP_RE seeing a reference that does not exist. Nothing downstream reads
    # these characters — an unresolved leftover only disqualifies the name.
    out = DOLLAR_ESCAPE_RE.sub("\x00{", value)
    for _ in range(RESOLVE_PASSES):
        if "${" not in out:
            # Terraform renders $${x} as the literal ${x}, so that is what the
            # deployed name contains. _check_literal_name leaves a value holding
            # `${` unjudged, which is the right answer for a name this strange.
            return out.replace("\x00{", "${")
        if len(out) > RESOLVE_MAX_LEN:
            # substitution can EXPAND each pass (a local referencing two locals),
            # so passes alone do not bound the work: a fan-out cycle reaches
            # w**8 chars and the hook hangs for tens of seconds
            return None
        def sub(m):
            ref = m.group(1).strip()
            if ref == "var.environment":
                return env
            if ref.startswith("local."):
                return locals_map.get(ref[len("local."):], m.group(0))
            return m.group(0)
        nxt = INTERP_RE.sub(sub, out)
        if nxt == out:          # nothing left that we know how to resolve
            return None
        out = nxt
    return None                 # ran out of passes: treat as unresolvable


def already_deployed(key, value, old_n, module_names):
    """Whether `value` pre-exists, so this edit is not introducing it.

    Three ways it can pre-exist: unchanged on this resource, present on another
    resource in this file, or present anywhere else in the Terraform module.
    The third is what makes moving a block between sibling .tf files a no-op to
    the gate, as it is to `terraform plan`. Extracted from main() so the suite
    can pin it — file-scoped was a blocker."""
    return (old_n.get(key) == value or value in old_n.values()
            or value in module_names)


def needs_module_context(old_n, new_n):
    """Whether this edit introduces any name that will be CHECKED.

    Reading every sibling .tf is the only real cost this gate adds, and it
    blocks the edit, so it is skipped when no name needs judging. The condition
    mirrors the caller's skip exactly: a name that is unchanged, or that merely
    moved between resources in this file, is never checked. Extracted from
    main() so the suite can pin it — a regression to "never" would switch
    resolution off and blind the gate again."""
    return any(old_n.get(k) != v and v not in old_n.values()
               for k, v in new_n.items())


def tf_dir_context(path, new_text):
    """(locals, names) visible to `path` from the rest of its Terraform module.

    Both come from one pass over the sibling .tf files, because both questions
    are module-scoped and Terraform's are too:

    * locals — a resource and the local naming it almost never share a file;
      the prefix lives in locals.tf, so one file's text resolves nothing.
    * names — "is this name already deployed?" cannot be answered from the
      edited file alone. Moving a resource block verbatim into a sibling file
      is a `terraform plan` no-op, but to a file-scoped check every name in it
      looks brand new, and the gate would refuse a pure refactor with a
      complaint about a convention the engineer never breached.

    Any read failure degrades to what we already have, never raises: this runs
    in a blocking hook, and a disk hiccup must not refuse an engineer's edit."""
    found, names = {}, set()
    try:
        directory = os.path.dirname(os.path.abspath(path))
        edited = os.path.basename(path)
        for fn in sorted(os.listdir(directory)):
            if not fn.endswith(".tf") or fn == edited:
                continue
            try:
                with open(os.path.join(directory, fn), "r",
                          encoding="utf-8", errors="replace") as fh:
                    sibling = fh.read()
            except (OSError, ValueError):
                continue
            found.update(tf_locals(sibling))
            names.update(tf_names(sibling).values())
    except (OSError, ValueError):
        pass
    found.update(tf_locals(new_text))   # the edit under review wins
    return found, names


def tf_names(text):
    """{(resource_type, resource_label, attr): value} for literal name/bucket attrs
    declared in a resource's OWN body.

    An attribute inside a nested block belongs to that block, not to the resource.
    Without the depth test it overwrote the parent's key: across this repo that put a
    Glue command name (`glueetl`, `pythonshell`) where a job name belongs and a
    DynamoDB key attribute where a table name belongs, on 19 resources whose type
    carries a rename posture — so the guard compared a value the engineer never
    renamed, and protected nothing real."""
    found = {}
    for line, rtype, label, depth in _tf_lines(text):
        if rtype is None or depth != 1:
            continue
        a = ATTR_RE.match(line)
        if a:
            found[(rtype, label, a.group(1))] = a.group(2)
    return found


def tf_rename_approvals(text):
    """{(resource_type, resource_label): {"ticket": str, "data": bool}} for blocks
    carrying a rename-approved marker, either directly above or inside the block."""
    out, pending = {}, None
    for line, rtype, label, depth in _tf_lines(text):
        a = APPROVAL_RE.match(line)
        if a:
            claim = {"ticket": a.group("ticket"),
                     "data": bool(DATA_CLAUSE_RE.search(a.group("rest") or ""))}
            # Depth 1 is the resource's own body. Anywhere else at file scope is a
            # marker waiting for the NEXT block — it must never bind backward to the
            # previous one, which would let one marker authorise two renames and leak
            # a `data:` clause to a resource it was not written for. A marker buried in
            # a nested block binds to nothing, so the rename is denied. Fail closed.
            if rtype is not None and depth == 1:
                out[(rtype, label)] = claim
            elif depth == 0:
                pending = claim
            continue
        m = RESOURCE_RE.match(line)
        if m and depth == 0:
            if pending:
                out[(m.group(1), m.group(2))] = dict(pending)
            pending = None
            continue
        # Any other content breaks an "immediately above" run; a blank line does not,
        # so a marker separated from its block by whitespace still counts.
        if line.strip():
            pending = None
    return out


def rename_violation(rtype, old, new, approval):
    """Reason this rename must be refused, or None if it may proceed."""
    posture = (PATTERNS.get(rtype) or {}).get("rename")
    if not posture or old == new:
        return None
    loss = RENAME_REASON.get(rtype, "Renaming forces replacement.")
    if approval is None:
        return ("Blocked: this edit renames a deployed %s (`%s` -> `%s`).\n%s\n"
                "Existing names predate the UIF convention and are non-compliant by "
                "design until an explicit, planned migration (00-core.md).\n"
                "If this IS that migration, declare it on its own comment line above "
                "the resource block:\n  # uif:rename-approved <TICKET>%s"
                % (rtype, old, new, loss,
                   " data:<outcome>" if posture == "destructive" else ""))
    if posture == "destructive" and not approval["data"]:
        return ("Blocked: renaming a %s destroys data, so `uif:rename-approved %s` is "
                "not enough on its own.\n%s\nAnswer the data question in the same "
                "marker with one of `data:%s`, e.g. "
                "`# uif:rename-approved %s data:copied`."
                % (rtype, approval["ticket"], loss,
                   "`, `data:".join(DATA_OUTCOMES), approval["ticket"]))
    return None


def tfvar_buckets(text):
    return {m.group(1): m.group(2)
            for m in (TFVAR_RE.match(l) for l in text.splitlines()) if m}


def json_fields(text):
    try:
        doc = json.loads(text)
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}
    keys = [("target", "s3_path"), ("target", "gcs_path"), ("target", "bq_dataset"),
            ("silver", "s3_path"), ("silver", "bq_dataset"), ("silver", "database"),
            ("silver", "table")]
    out = {}
    for parent, child in keys:
        blk = doc.get(parent)
        if isinstance(blk, dict) and isinstance(blk.get(child), str):
            out["%s.%s" % (parent, child)] = blk[child]
    cloud = (doc.get("destination") or {}).get("cloud")
    if isinstance(cloud, str):
        out["destination.cloud"] = cloud
    return out


def _vocab_violation(value, tokens, rule, rtype=None):
    """Stack/env/banned/cadence/zero-pad checks shared by all four delimiter forms."""
    if len(tokens) >= 3:
        if tokens[1] not in STACKS:
            return "`%s` — `%s` is not a stack (%s) (%s)." % (
                value, tokens[1], ", ".join(sorted(STACKS)), rule)
        if tokens[2] not in ACTIVE_ENVS:
            if ACTIVE_ENVS is LAB_ENVS:
                return ("`%s` — `%s` in the env slot inside delivery-lab. A "
                        "lab uses the reserved throwaway token (%s) so it can "
                        "never collide with the estate (00-core.md "
                        "\"Throwaway namespaces\", UIFPE-473)." % (
                            value, tokens[2], ", ".join(sorted(LAB_ENVS))))
            return "`%s` — `%s` is not an env (%s) (%s)." % (
                value, tokens[2], ", ".join(sorted(ACTIVE_ENVS)), rule)
    for t in tokens:
        if VERSION_TOKEN.match(t) and rtype in VERSIONED_TYPES:
            continue
        if t in BANNED or TICKET_KEY.match(t):
            return ("`%s` contains the banned token `%s` — names describe "
                    "function, not phase, ticket, status or version "
                    "(00-core.md)." % (value, t))
    if TICKET_KEY_JOINED.match("-".join(tokens[3:])):
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


def check_name(value, rtype, locals_map=None):
    """Violation string for a newly introduced name, else None.

    A name built from locals is resolved first (see resolve_interpolated). With
    no locals_map the interpolated case stays unjudged, which is what every
    caller got before resolution existed."""
    if not value:
        return None
    if "${" not in value:
        return _check_literal_name(value, rtype)
    if not locals_map:
        return None
    # A name valid in dev can still break prod on length, so judge every env
    # and report the first failure rather than a representative one.
    for env in sorted(ACTIVE_ENVS):
        resolved = resolve_interpolated(value, locals_map, env)
        if resolved is None:
            return None
        bad = _check_literal_name(resolved, rtype)
        if bad:
            return ("`%s` resolves to `%s` for env `%s`:\n  %s"
                    % (value, resolved, env, bad))
    return None


def _check_literal_name(value, rtype):
    """Violation string for a fully literal name, else None."""
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
        if len(segs) != 5:
            return ("`%s` has %d segments; the secret form is exactly "
                    "`uif/{stack}/{env}/{system-type}/{system}` (5 segments) "
                    "(%s)." % (value, len(segs), rule))
        bad = _vocab_violation(value, segs, rule, rtype)
        if bad:
            return bad
    elif form == "underscore":
        if "-" in value:
            return ("`%s` — this form uses underscores, e.g. "
                    "`uif_echo_dev_silver_tep` (%s)." % (value, rule))
        if rtype == "aws_glue_catalog_table":
            if value != value.lower() or "-" in value:
                return ("`%s` — a catalog table name is the entity only, in "
                        "snake_case, never hyphenated (%s)." % (value, rule))
        else:
            if not value.startswith("uif_"):
                return ("`%s` does not open with the mandatory `uif_` stem (%s)."
                        % (value, rule))
            bad = _vocab_violation(value, value.split("_"), rule, rtype)
            if bad:
                return bad
    elif form == "platform":
        if "/uif-" not in value:
            return ("`%s` — AWS owns the prefix and the UIF stem follows it, "
                    "e.g. `/aws/lambda/uif-echo-dev-dispatch` (%s)."
                    % (value, rule))
        bad = _vocab_violation(value, value.rsplit("/", 1)[-1].split("-"), rule, rtype)
        if bad:
            return bad
    else:
        base = value[:-5] if value.endswith(".fifo") else value
        if not base.startswith("uif-"):
            return ("`%s` does not open with the mandatory `uif-` stem "
                    "`uif-{stack}-{env}-{operation}` (%s)." % (value, rule))
        suffix = spec["suffix"]
        if suffix and not base.endswith("-" + suffix):
            return ("`%s` — this resource's name ends with `-%s` (%s)."
                    % (value, suffix, rule))
        bad = _vocab_violation(value, base.split("-"), rule, rtype)
        if bad:
            return bad

    limit = spec["limit"]
    if limit and len(value) > limit:
        return "`%s` is %d chars; the limit for %s is %d (00-core.md)." % (
            value, len(value), rtype, limit)
    return None


def check_paths(new, old):
    cloud = new.get("destination.cloud", "")
    for field in ("target.gcs_path", "target.s3_path", "silver.s3_path"):
        v = new.get(field)
        if not v or old.get(field) == v:
            continue
        if not v.endswith("/"):
            return "`%s` = `%s` — object-store prefixes are `{bucket}/{domain}/{table}/` and end with `/` (Rule 8)." % (field, v)
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
        if field.endswith("gcs_path"):
            bad = [s for s in segs if "-" in s or s != s.lower()]
            if bad:
                return ("`%s` = `%s` — GCS keys follow Oracle schema names in snake_case; "
                        "`%s` is not (Rule 8). Derive them from `extract.table`." % (field, v, bad[0]))
        else:
            bad = [s for s in segs
                   if not PARTITION_SEG.match(s) and ("_" in s or s != s.lower())]
            if bad:
                return ("`%s` = `%s` — S3 key segments are lower-case and "
                        "hyphenated; `%s` is not (storage.md)." % (field, v, bad[0]))
    for field in ("target.bq_dataset", "silver.bq_dataset"):
        v = new.get(field)
        if not v or old.get(field) == v:
            continue
        if not re.match(r"^[A-Z][A-Z0-9_]*$", v):
            return ("`%s` = `%s` — BigQuery datasets use the upper-underscore form, "
                    "e.g. `UIF_ECHO_DEV_SILVER_IPM` (Rule 7)." % (field, v))
    v = new.get("silver.table")
    if v and old.get("silver.table") != v and ("-" in v or v != v.lower()):
        return ("`silver.table` = `%s` — a catalog table name is the entity only, in "
                "snake_case, never hyphenated (Rule 9)." % v)
    v = new.get("silver.database")
    if v and old.get("silver.database") != v and "-" in v:
        return ("`silver.database` = `%s` — Glue databases use the underscore form, "
                "e.g. `uif_echo_dev_silver_product` (Rule 7)." % v)
    if cloud == "gcp" and new.get("target.s3_path") and not old.get("target.s3_path"):
        return "`destination.cloud` is `gcp` but `target.s3_path` is set — use one taxonomy per cloud (Rule 8)."
    return None


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return
    tool = payload.get("tool_name", "")
    if tool not in ("Edit", "Write", "MultiEdit"):
        return
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ""
    if not looks_like_uif(path):
        return

    # Labs judge the env slot against the lab vocabulary (UIFPE-473).
    global ACTIVE_ENVS
    ACTIVE_ENVS = LAB_ENVS if LAB_MARKER.search(path.replace("\\", "/")) else ENVS

    old_text = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                old_text = fh.read()
        except Exception:
            return
    new_text = apply_edit(old_text, ti, tool)
    if new_text is None or new_text == old_text:
        return

    p = path.replace("\\", "/")
    if p.endswith(".tf"):
        for line in new_text.splitlines():
            if MISPLACED_APPROVAL_RE.match(line):
                deny("Blocked: a `uif:rename-approved` marker on a name line stops the "
                     "name being read at all, so neither the rename guard nor the "
                     "new-name check can see it. Put the marker on its own comment line "
                     "above the resource block.\n(%s)" % os.path.basename(path))
        old_n, new_n = tf_names(old_text), tf_names(new_text)
        approvals = tf_rename_approvals(new_text)
        if needs_module_context(old_n, new_n):
            locals_map, module_names = tf_dir_context(path, new_text)
        else:
            locals_map, module_names = {}, set()
        for key, value in new_n.items():
            rtype = key[0]
            if key in old_n:
                bad = rename_violation(rtype, old_n[key], value,
                                       approvals.get((rtype, key[1])))
                if bad:
                    deny("%s\n(%s)" % (bad, os.path.basename(path)))
            if already_deployed(key, value, old_n, module_names):
                continue
            bad = check_name(value, rtype, locals_map)
            if bad:
                deny("Blocked: new %s name violates the UIF naming convention.\n%s\n"
                     "Stem: `uif-{stack}-{env}-{operation}`. See .claude/rules/naming/."
                     % (rtype, bad))
    elif p.endswith(".tfvars"):
        old_b, new_b = tfvar_buckets(old_text), tfvar_buckets(new_text)
        for key, value in new_b.items():
            if key in old_b and old_b[key] != value:
                deny(
                    "Blocked: this edit changes the deployed bucket name `%s` -> `%s` (`%s` "
                    "in %s). Terraform would destroy and recreate the bucket, losing its "
                    "data. Live `echo-{env}-*` buckets are non-compliant by design until an "
                    "explicit migration." % (old_b[key], value, key, os.path.basename(path)))
            if key in old_b:
                continue
            bad = check_name(value, "aws_s3_bucket")
            if bad:
                deny("Blocked: new bucket name violates the UIF naming "
                     "convention.\n%s\nSee .claude/rules/naming/storage.md."
                     % bad)
    elif p.endswith(".json"):
        bad = check_paths(json_fields(new_text), json_fields(old_text))
        if bad:
            deny("Blocked: draft config violates the UIF data-lake taxonomy.\n%s\n"
                 "See .claude/rules/naming/storage.md." % bad)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: a broken hook must never block legitimate work
    sys.exit(0)
