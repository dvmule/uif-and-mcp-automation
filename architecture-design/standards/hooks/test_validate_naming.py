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


def test_plain_uif_bucket_is_now_compliant():
    # The account-id prefix is gone from the standard.
    assert vn.check_name("uif-echo-dev-bronze", "aws_s3_bucket") is None
    assert vn.check_name("uif-echo-dev-ui", "aws_s3_bucket") is None


def test_account_prefixed_bucket_is_now_rejected():
    msg = vn.check_name("283556840805-s3-uif-echo-dev-ui", "aws_s3_bucket")
    assert msg is not None and "uif-" in msg


def test_secret_slash_form_passes():
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


def test_zero_padded_instance_is_not_a_ticket_key():
    # Regression: the joined-token check used to deny these.
    assert vn.check_name("uif-echo-dev-ingest-01", "aws_glue_job") is None
    assert vn.check_name("uif-echo-dev-slot-00", "aws_glue_job") is None


def test_uppercase_ticket_key_still_rejected():
    assert vn.check_name("uif-echo-dev-UIFPE-254", "aws_glue_job") is not None


def test_dlq_does_not_satisfy_another_types_suffix():
    assert vn.check_name("uif-echo-dev-dispatch-dlq", "aws_iam_role") is not None
    assert vn.check_name("uif-echo-dev-dispatch-dlq", "aws_lb") is not None


def test_sqs_dlq_still_passes():
    # aws_sqs_queue has no required suffix, so a DLQ name needs no escape.
    assert vn.check_name("uif-echo-dev-dispatch-dlq", "aws_sqs_queue") is None


def test_secret_with_legacy_gip_segment_is_rejected():
    msg = vn.check_name("uif/echo/dev/gip/db/tep", "aws_secretsmanager_secret")
    assert msg is not None


def test_secret_five_segment_form_passes():
    assert vn.check_name("uif/echo/dev/db/tep", "aws_secretsmanager_secret") is None
    assert vn.check_name("uif/echo/uat/cloud/gcp", "aws_secretsmanager_secret") is None


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
    "aws_glue_catalog_table": "mtl_system_items_b",
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
    # No aws_vpc_endpoint entry: it is named through a `Name` tag the hook cannot read, so
    # it carries no PATTERNS row and an example here would imply enforcement that does not
    # exist. networking.md still states the convention.
    "aws_cloudfront_origin_access_control": "uif-apicurio-dev-ui",
    "aws_apigatewayv2_vpc_link": "uif-apicurio-dev-bff-vpc-link",
    "aws_apigatewayv2_authorizer": "uif-apicurio-dev-bff-authorizer",
    "aws_db_instance": "uif-apicurio-dev-registry-db",
    "aws_db_subnet_group": "uif-apicurio-dev-registry-db-subnet-group",
    "aws_iam_role": "uif-echo-dev-glue-role",
    "aws_iam_policy": "uif-echo-dev-glue-policy",
    "aws_iam_role_policy": "uif-apicurio-dev-bff-task-policy",
    "aws_secretsmanager_secret": "uif/echo/dev/db/tep",
    "aws_cloudwatch_log_group": "/aws/lambda/uif-echo-dev-dispatch",
    "aws_cloudwatch_dashboard": "uif-echo-dev-silver-bq-replication",
    "aws_cloudwatch_metric_alarm": "uif-echo-dev-silver-bq-replicator-failed",
}


@pytest.mark.parametrize("rtype", sorted(vn.PATTERNS))
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


def test_every_destructive_type_has_a_reason():
    for rtype, spec in vn.PATTERNS.items():
        if spec["rename"]:
            assert rtype in vn.RENAME_REASON, "no rename reason for %s" % rtype


def test_destructive_types_include_the_data_stores():
    destructive = {k for k, v in vn.PATTERNS.items() if v["rename"] == "destructive"}
    assert {"aws_s3_bucket", "aws_dynamodb_table", "aws_sqs_queue",
            "aws_ecr_repository", "aws_secretsmanager_secret",
            "aws_glue_catalog_database"} <= destructive


def test_rename_guard_covers_more_than_the_original_two():
    guarded = {k for k, v in vn.PATTERNS.items() if v["rename"]}
    assert len(guarded) >= 20
    assert "aws_iam_role" in guarded
    assert "aws_security_group" in guarded


def test_types_with_no_rename_posture_are_not_guarded():
    unguarded = {k for k, v in vn.PATTERNS.items() if not v["rename"]}
    assert "aws_cloudwatch_log_group" in unguarded
    assert "aws_glue_connection" in unguarded


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


def core_slot_tokens(slot):
    """The closed vocabulary 00-core.md's slot table lists for `slot`."""
    core = rule_text("00-core.md")
    row = [ln for ln in core.splitlines() if ln.startswith("| `%s` |" % slot)]
    assert len(row) == 1, \
        "expected exactly one `%s` slot row in 00-core.md, got %d" % (slot, len(row))
    # The first backtick-quoted group after the slot name is the vocabulary; prose may
    # follow it in the same cell (`cadence` does exactly that).
    listed = row[0].split("|")[2]
    tokens = {t.strip(" `") for t in listed.split("`")[1].split(",")}
    assert tokens, "could not parse the %s tokens out of %r" % (slot, row[0])
    return tokens


@pytest.mark.parametrize("slot,enforced", [
    ("stack", "STACKS"),
    ("env", "ENVS"),
    ("cadence", "CADENCES"),
])
def test_every_closed_vocabulary_token_in_core_is_enforced(slot, enforced):
    """The reverse of test_core_carries_the_vocabularies, which only checks hook -> rules.

    A token added to 00-core.md's slot table and omitted from the hook's set passes that
    test while the guard denies every name using it — the standard says legal, the hook
    says unknown, and nothing fails loudly. That is what happened to `apicurio`, added by
    owner decision on 2026-09-08 (ADR-0012) and absent from STACKS until 2026-09-14. The
    same hole existed for `env` and `cadence`; all three are asserted both ways now, so
    none of the closed vocabularies can drift in either direction.
    """
    missing = sorted(core_slot_tokens(slot) - getattr(vn, enforced))
    assert not missing, (
        "00-core.md sanctions %s as `%s` token(s) that %s does not enforce — every new "
        "name using one would be denied as unknown" % (missing, slot, enforced))


def test_setup_guide_counts_match_the_hook():
    """Setup-Guide.md states three counts about PATTERNS. Nothing recomputed them.

    All three were stale the moment this change added six types, and a reader has no way
    to tell a stale count from a true one. Deriving them here means the guide fails CI
    instead of quietly misdescribing the guard.
    """
    import re
    guide_path = os.path.join(os.path.dirname(__file__), os.pardir, "Setup-Guide.md")
    with open(guide_path, encoding="utf-8") as fh:
        guide = fh.read()

    judged = len(vn.PATTERNS)
    renameable = sum(1 for s in vn.PATTERNS.values() if s["rename"])
    destructive = sum(1 for s in vn.PATTERNS.values() if s["rename"] == "destructive")

    for label, actual, pattern in (
        ("judged types", judged, r"—\s*(\d+)\s+resource types"),
        ("renameable types", renameable, r"replacement\s+—\s*\n?\s*(\d+)\s+types"),
        ("destructive types", destructive, r"The\s+(\d+)\s+types whose\s*\n?\s*rename"),
    ):
        m = re.search(pattern, guide)
        assert m, "could not find the %s count in Setup-Guide.md (pattern %r)" % (label, pattern)
        assert int(m.group(1)) == actual, \
            "Setup-Guide.md says %s %s; PATTERNS has %d" % (m.group(1), label, actual)


def test_no_retired_rule_files_remain():
    for retired in ("aws-resources.md", "data-lake.md", "secrets.md"):
        assert retired not in all_rule_text(), \
            "%s was retired; its content moved elsewhere" % retired


def test_no_denial_message_cites_a_retired_rule_file():
    with open(_HOOK, encoding="utf-8") as fh:
        hook_src = fh.read()
    # "secrets.md" is a substring of "identity-secrets.md", which the hook cites
    # legitimately throughout PATTERNS (the current, non-retired rule file) — a
    # bare substring check on "secrets.md" would trip on those legitimate
    # citations. Match the qualified form instead so the assertion catches only
    # an actual citation of the retired secrets.md.
    for retired in ("data-lake.md", "aws-resources.md"):
        assert retired not in hook_src, \
            "a denial message cites the retired rule file %s" % retired
    assert "naming/secrets.md" not in hook_src, \
        "a denial message cites the retired rule file secrets.md"


def test_the_nine_rule_files_exist():
    expected = {
        "00-core.md", "catalog.md", "compute-etl.md", "dynamodb-attributes.md",
        "eventing.md", "identity-secrets.md", "networking.md",
        "observability.md", "storage.md",
    }
    assert set(all_rule_text()) == expected


def test_versioned_subscription_names_are_allowed():
    ok_rule = "uif-event-dev-on-product-product-created-v2"
    assert vn.check_name(ok_rule, "aws_cloudwatch_event_rule") is None
    ok_queue = "uif-event-dev-pmc-product-product-created-v2"
    assert vn.check_name(ok_queue, "aws_sqs_queue") is None


def test_version_token_still_banned_on_other_types():
    assert vn.check_name("uif-echo-dev-transform-v2", "aws_glue_job") is not None


def test_attr_re_reads_every_named_argument():
    for attr in ("name", "bucket", "function_name", "layer_name", "family",
                 "dashboard_name", "alarm_name"):
        line = '  %s = "uif-echo-dev-thing"' % attr
        assert vn.ATTR_RE.match(line), "ATTR_RE does not read %s" % attr


def test_lambda_name_is_now_reachable():
    tf = 'resource "aws_lambda_function" "x" {\n  function_name = "nope-echo-dev-dispatch"\n}\n'
    found = vn.tf_names(tf)
    assert ("aws_lambda_function", "x", "function_name") in found
    assert vn.check_name(found[("aws_lambda_function", "x", "function_name")],
                         "aws_lambda_function") is not None


def test_catalog_table_length_limit_is_enforced():
    assert vn.check_name("a" * 256, "aws_glue_catalog_table") is not None
    assert vn.check_name("mtl_system_items_b", "aws_glue_catalog_table") is None


# ── the rename guard's migration escape hatch ──────────────────────────────────
# 00-core.md permits a rename "through an explicit, planned migration". Before this
# existed the guard refused every rename unconditionally, so the migration it points
# at could not be performed. These pin both halves: still refused by default, and
# permitted only when a marker declares the migration.

def test_a_rename_is_still_refused_without_a_marker():
    assert vn.rename_violation("aws_iam_role", "echo-dev-x-role",
                               "uif-echo-dev-x-role", None) is not None


def test_a_marker_permits_a_replace_rename():
    ok = {"ticket": "UIFPE-364", "data": False}
    assert vn.rename_violation("aws_iam_role", "echo-dev-x-role",
                               "uif-echo-dev-x-role", ok) is None


def test_a_destructive_rename_needs_the_data_question_answered():
    bare = {"ticket": "UIFPE-364", "data": False}
    bad = vn.rename_violation("aws_s3_bucket", "echo-dev-gold",
                              "uif-echo-dev-gold", bare)
    assert bad is not None and "data:" in bad


def test_a_destructive_rename_passes_once_the_data_clause_is_present():
    answered = {"ticket": "UIFPE-364", "data": True}
    assert vn.rename_violation("aws_s3_bucket", "echo-dev-gold",
                               "uif-echo-dev-gold", answered) is None


def test_every_destructive_type_demands_the_data_clause():
    bare = {"ticket": "UIFPE-1", "data": False}
    for rtype, spec in vn.PATTERNS.items():
        if spec["rename"] != "destructive":
            continue
        assert vn.rename_violation(rtype, "old-name", "new-name", bare) is not None, \
            "%s accepts a marker with no data clause" % rtype


def test_an_unguarded_type_needs_no_marker():
    assert vn.rename_violation("aws_cloudwatch_log_group", "a", "b", None) is None


def test_an_unchanged_name_is_never_a_violation():
    assert vn.rename_violation("aws_s3_bucket", "same", "same", None) is None


def test_a_marker_above_the_block_binds_to_it():
    tf = ('# uif:rename-approved UIFPE-364\n'
          'resource "aws_iam_role" "dispatch" {\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    got = vn.tf_rename_approvals(tf)
    assert got[("aws_iam_role", "dispatch")]["ticket"] == "UIFPE-364"


def test_a_marker_inside_the_block_binds_to_it():
    tf = ('resource "aws_iam_role" "dispatch" {\n'
          '  # uif:rename-approved UIFPE-364\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    assert ("aws_iam_role", "dispatch") in vn.tf_rename_approvals(tf)


def test_a_blank_line_does_not_break_the_binding():
    tf = ('# uif:rename-approved UIFPE-364\n\n'
          'resource "aws_iam_role" "dispatch" {\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    assert ("aws_iam_role", "dispatch") in vn.tf_rename_approvals(tf)


def test_intervening_code_does_break_the_binding():
    tf = ('# uif:rename-approved UIFPE-364\n'
          'locals { x = 1 }\n'
          'resource "aws_iam_role" "dispatch" {\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_a_marker_does_not_leak_to_the_next_resource():
    tf = ('# uif:rename-approved UIFPE-364\n'
          'resource "aws_iam_role" "first" {\n'
          '  name = "uif-echo-dev-first-role"\n}\n'
          'resource "aws_iam_role" "second" {\n'
          '  name = "uif-echo-dev-second-role"\n}\n')
    got = vn.tf_rename_approvals(tf)
    assert ("aws_iam_role", "first") in got
    assert ("aws_iam_role", "second") not in got


def test_a_marker_without_a_ticket_key_is_not_a_marker():
    tf = ('# uif:rename-approved\n'
          'resource "aws_iam_role" "x" {\n  name = "uif-echo-dev-x-role"\n}\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_the_data_clause_is_read_from_the_marker():
    tf = ('# uif:rename-approved UIFPE-364 data:copied\n'
          'resource "aws_s3_bucket" "gold" {\n  bucket = "uif-echo-dev-gold"\n}\n')
    assert vn.tf_rename_approvals(tf)[("aws_s3_bucket", "gold")]["data"] is True


# ── a nested block's name is not the resource's name ──────────────────────────
# tf_names had no depth tracking, so an attribute inside a nested block overwrote
# its parent's key. Across this repo that mis-recorded 22 keys, 19 of them on types
# carrying a rename posture — the guard compared a value nobody had renamed.

def test_a_dynamodb_key_attribute_is_not_the_tables_name():
    # The shape of stacks/beacon/infrastructure/terraform/dynamodb.tf.
    tf = ('resource "aws_dynamodb_table" "registries" {\n'
          '  name         = "uif-beacon-dev-registries"\n'
          '  hash_key     = "registry"\n\n'
          '  attribute {\n    name = "registry"\n  }\n\n'
          '  attribute {\n    name = "id"\n  }\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_dynamodb_table", "registries", "name")] == \
        "uif-beacon-dev-registries"


def test_a_glue_command_name_is_not_the_jobs_name():
    # The shape of domains/product/infrastructure/terraform/glue_jobs.tf, which used
    # to record the job's name as "glueetl".
    tf = ('resource "aws_glue_job" "transform" {\n'
          '  name         = "uif-echo-dev-gold-transform"\n\n'
          '  command {\n    name            = "glueetl"\n'
          '    script_location = "s3://x/y.py"\n  }\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_glue_job", "transform", "name")] == "uif-echo-dev-gold-transform"


def test_a_following_module_block_does_not_bind_to_the_previous_resource():
    # RESOURCE_RE does not match `module`, so without depth tracking the module's
    # name overwrote the resource key above it.
    tf = ('resource "aws_iam_policy" "read" {\n'
          '  name = "uif-echo-dev-read-policy"\n}\n\n'
          'module "irsa" {\n  name = "product-api-dev"\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_iam_policy", "read", "name")] == "uif-echo-dev-read-policy"
    assert not [k for k in got if k[0] == "module"]


def test_braces_inside_a_string_do_not_shift_depth():
    # A balanced `${...}` would survive a naive counter, so this uses an UNBALANCED
    # brace — the only shape that distinguishes stripping strings from not stripping
    # them. An unstripped `{` here pushes every later attribute to depth 2 and the
    # resource's own name stops being recorded at all.
    tf = ('resource "aws_cloudwatch_dashboard" "d" {\n'
          '  dashboard_name = "uif-echo-dev-overview"\n'
          '  dashboard_body = "{ unbalanced"\n'
          '  alarm_name     = "uif-echo-dev-late"\n}\n'
          'resource "aws_iam_role" "after" {\n'
          '  name = "uif-echo-dev-after-role"\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_cloudwatch_dashboard", "d", "dashboard_name")] == \
        "uif-echo-dev-overview"
    assert ("aws_cloudwatch_dashboard", "d", "alarm_name") in got
    # and the stray brace must not swallow the resource that follows
    assert got[("aws_iam_role", "after", "name")] == "uif-echo-dev-after-role"


def test_a_comment_cannot_hide_a_brace_from_the_depth_count():
    tf = ('resource "aws_iam_role" "r" {\n'
          '  name = "uif-echo-dev-r-role"  \n'
          '  # a commented brace { must not count\n'
          '  bucket = "uif-echo-dev-b"\n}\n')
    got = vn.tf_names(tf)
    assert ("aws_iam_role", "r", "bucket") in got


def test_a_heredoc_body_cannot_shift_depth():
    # This repo's three heredocs are all brace-balanced today, so the body is written
    # UNBALANCED here — that is the shape skipping actually defends against, and a
    # balanced body would pass whether or not the body is skipped.
    tf = ('resource "aws_cloudfront_function" "f" {\n'
          '  name = "uif-beacon-dev-strip-api-prefix"\n'
          '  code = <<-EOT\n'
          '    function handler(event) {\n'
          '      return event;\n'
          '  EOT\n'
          '  comment = "x"\n}\n'
          'resource "aws_iam_role" "after" {\n'
          '  name = "uif-echo-dev-after-role"\n}\n')
    got = vn.tf_names(tf)
    assert ("aws_cloudfront_function", "f", "name") in got
    # the stray `{` in the body must not swallow the resource that follows
    assert got[("aws_iam_role", "after", "name")] == "uif-echo-dev-after-role"


def test_a_jsonencode_object_is_nested_not_the_resource_body():
    tf = ('resource "aws_iam_role" "r" {\n'
          '  name = "uif-echo-dev-r-role"\n'
          '  assume_role_policy = jsonencode({\n'
          '    Statement = [{\n      name = "not-a-role-name"\n    }]\n'
          '  })\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_iam_role", "r", "name")] == "uif-echo-dev-r-role"


# ── block comments are not code, and the data clause is a closed set ──────────
# All three found by an adversarial bypass sweep against the first version of the
# marker grammar, each reproduced against the real hook before being fixed.

def test_a_marker_quoted_in_a_block_comment_is_not_an_approval():
    # The prose says the opposite of what the marker would grant.
    tf = ('resource "aws_dynamodb_table" "t" {\n'
          '  name = "echo-dev-ledger"\n'
          '  /* migration notes -- NOT yet approved:\n'
          '     # uif:rename-approved UIFPE-364 data:copied\n'
          '  */\n}\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_a_marker_in_a_single_line_block_comment_is_not_an_approval():
    tf = ('resource "aws_iam_role" "r" {\n'
          '  /* # uif:rename-approved UIFPE-364 */\n'
          '  name = "uif-echo-dev-r-role"\n}\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_a_brace_in_a_block_comment_does_not_shift_depth():
    tf = ('resource "aws_iam_role" "r" {\n'
          '  /* multi\n  line { brace\n  */\n'
          '  name = "uif-echo-dev-r-role"\n}\n'
          'resource "aws_s3_bucket" "b" {\n  bucket = "uif-echo-dev-b"\n}\n')
    got = vn.tf_names(tf)
    assert got[("aws_iam_role", "r", "name")] == "uif-echo-dev-r-role"
    assert got[("aws_s3_bucket", "b", "bucket")] == "uif-echo-dev-b"


def test_the_data_clause_is_a_closed_vocabulary():
    for outcome in vn.DATA_OUTCOMES:
        assert vn.DATA_CLAUSE_RE.search(" data:%s" % outcome), outcome


def test_prose_cannot_satisfy_the_data_clause():
    # `\b` used to match after a hyphen, so a tail reading "no-data:impact" — which
    # answers nothing — authorised destroying a table.
    for tail in (" no-data:impact", " replace-only, no-data:impact", " data:x",
                 " data:whatever", " metadata:only", " data:", " DATA:copied"):
        assert not vn.DATA_CLAUSE_RE.search(tail), tail


def test_a_marker_in_a_nested_block_binds_to_nothing():
    # Fail closed: a marker buried in a sub-block does not authorise the resource,
    # so the rename is refused rather than quietly allowed.
    tf = ('resource "aws_dynamodb_table" "t" {\n'
          '  name = "echo-dev-t"\n'
          '  attribute {\n'
          '    # uif:rename-approved UIFPE-364 data:copied\n'
          '    name = "id"\n  }\n}\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_a_marker_does_not_bind_backward_to_the_previous_resource():
    # The marker sits at file scope AFTER a block. Without the closing-brace reset it
    # bound to that block too, so one marker authorised two renames.
    tf = ('resource "aws_dynamodb_table" "config" {\n'
          '  name = "echo-dev-config"\n}\n'
          '# uif:rename-approved UIFPE-364\n'
          'resource "aws_iam_role" "dispatch" {\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    got = vn.tf_rename_approvals(tf)
    assert ("aws_iam_role", "dispatch") in got
    assert ("aws_dynamodb_table", "config") not in got


def test_a_data_clause_does_not_leak_to_a_resource_it_was_not_written_for():
    # Both keys used to hold the SAME dict, so `data:copied` written for the safe
    # rename below also satisfied the destructive gate for the table above it.
    tf = ('resource "aws_dynamodb_table" "config" {\n'
          '  name = "echo-dev-config"\n}\n'
          '# uif:rename-approved UIFPE-364 data:copied\n'
          'resource "aws_iam_role" "dispatch" {\n'
          '  name = "uif-echo-dev-dispatch-role"\n}\n')
    got = vn.tf_rename_approvals(tf)
    assert vn.rename_violation("aws_dynamodb_table", "echo-dev-config",
                               "uif-echo-dev-config",
                               got.get(("aws_dynamodb_table", "config"))) is not None


def test_a_trailing_marker_binds_to_nothing():
    tf = ('resource "aws_iam_role" "x" {\n  name = "uif-echo-dev-x-role"\n}\n'
          '# uif:rename-approved UIFPE-364 data:copied\n')
    assert vn.tf_rename_approvals(tf) == {}


def test_a_marker_inside_a_block_does_not_also_bind_to_the_next_one():
    tf = ('resource "aws_iam_role" "first" {\n'
          '  # uif:rename-approved UIFPE-364\n'
          '  name = "uif-echo-dev-first-role"\n}\n'
          'resource "aws_s3_bucket" "second" {\n'
          '  bucket = "uif-echo-dev-second"\n}\n')
    got = vn.tf_rename_approvals(tf)
    assert ("aws_iam_role", "first") in got
    assert ("aws_s3_bucket", "second") not in got


def test_both_hcl_comment_forms_are_recognised():
    for lead in ("#", "//"):
        tf = ('%s uif:rename-approved UIFPE-364\n'
              'resource "aws_iam_role" "x" {\n  name = "uif-echo-dev-x-role"\n}\n' % lead)
        assert ("aws_iam_role", "x") in vn.tf_rename_approvals(tf), \
            "%s form not recognised" % lead


def test_the_misplacement_check_covers_every_attribute_attr_re_reads():
    # The two alternations are built from one source, so they cannot drift apart.
    for attr in vn.NAME_ATTRS.split("|"):
        for lead in ("#", "//"):
            line = '  %s = "uif-echo-dev-thing" %s uif:rename-approved UIFPE-364' % (
                attr, lead)
            assert vn.MISPLACED_APPROVAL_RE.match(line), \
                "misplaced marker on a %s line (%s) is not caught" % (attr, lead)


def test_a_marker_on_the_name_line_is_caught_not_silently_obeyed():
    # ATTR_RE anchors to end-of-line, so a marker here would stop the name being
    # read and the rename would pass unexamined. Must be refused instead.
    line = '  name = "uif-echo-dev-dispatch-role" # uif:rename-approved UIFPE-364'
    assert vn.MISPLACED_APPROVAL_RE.match(line)
    assert vn.ATTR_RE.match(line) is None, \
        "ATTR_RE now tolerates trailing comments — the misplacement check is moot"


# --------------------------------------------------------- interpolated names
# Until UIFPE-358 the gate returned None for any value containing `${`, so the
# whole interpolated estate — which is nearly every deployed name in this repo —
# was invisible to it. These pin the resolution and, more importantly, the
# fail-open boundary: a false positive here refuses an engineer's edit.

_GOLD_LOCALS = {
    "name_prefix": "echo-${var.environment}",
    "uif_prefix": "uif-echo-${var.environment}",
}


def test_tf_locals_reads_a_locals_block():
    tf = ('locals {\n'
          '  name_prefix = "echo-${var.environment}"\n'
          '  uif_prefix  = "uif-echo-${var.environment}"\n'
          '}\n')
    assert vn.tf_locals(tf) == _GOLD_LOCALS


def test_tf_locals_ignores_a_nested_blocks_attributes():
    # mirrors tf_names(): an attribute inside a nested block is not a local, and
    # hoisting it would resolve names against a value that is not the prefix
    tf = ('locals {\n'
          '  name_prefix = "echo-dev"\n'
          '  tags = {\n'
          '    name_prefix = "WRONG"\n'
          '  }\n'
          '}\n')
    assert vn.tf_locals(tf) == {"name_prefix": "echo-dev"}


def test_locals_outside_a_locals_block_are_not_collected():
    tf = ('resource "aws_glue_job" "x" {\n  name_prefix = "not-a-local"\n}\n')
    assert vn.tf_locals(tf) == {}


def test_resolution_substitutes_locals_and_the_environment():
    assert vn.resolve_interpolated(
        "${local.uif_prefix}-gold-transform", _GOLD_LOCALS, "dev"
    ) == "uif-echo-dev-gold-transform"


def test_an_unresolvable_reference_disqualifies_the_whole_name():
    # ${each.key} can never be known here. A partially resolved name would be
    # judged against tokens the engineer never wrote, so it must not be judged.
    for value in ("${local.name_prefix}-gold-dispatch-${each.key}",
                  "${local.no_such_local}-x",
                  "${var.something_else}-x"):
        assert vn.resolve_interpolated(value, _GOLD_LOCALS, "dev") is None, value
        assert vn.check_name(value, "aws_glue_job", _GOLD_LOCALS) is None, value


def test_a_local_cycle_terminates_instead_of_hanging():
    cyclic = {"a": "${local.b}", "b": "${local.a}"}
    assert vn.resolve_interpolated("${local.a}-x", cyclic, "dev") is None


def test_without_a_locals_map_interpolated_names_stay_unjudged():
    # the posture every caller had before resolution existed
    assert vn.check_name("${local.name_prefix}-gold-transform", "aws_glue_job") is None


def test_a_legacy_prefix_name_is_now_caught_and_names_both_forms():
    bad = vn.check_name("${local.name_prefix}-gold-transform",
                        "aws_glue_job", _GOLD_LOCALS)
    assert bad, "the interpolated legacy name is still invisible to the gate"
    # the engineer never typed the resolved form; the message must show both or
    # it reads as a complaint about a name that is not in the file
    assert "${local.name_prefix}-gold-transform" in bad
    assert "echo-dev-gold-transform" in bad


def test_the_migration_targets_pass_the_gate():
    for rtype, value in (
        ("aws_glue_job", "${local.uif_prefix}-gold-transform"),
        ("aws_glue_job", "${local.uif_prefix}-gold-reconcile"),
        ("aws_lambda_function", "${local.uif_prefix}-gold-dispatch"),
        ("aws_lambda_function", "${local.uif_prefix}-gold-ai-draft"),
    ):
        assert vn.check_name(value, rtype, _GOLD_LOCALS) is None, \
            "%s is a UIFPE-358 target and must be accepted" % value


def test_every_environment_is_judged_not_just_one():
    # 51 chars past the prefix is exactly 64 in dev (legal) and 65 in prod.
    # Checking one representative env would ship the prod breach.
    value = "${local.uif_prefix}-" + "a" * 51
    assert vn.resolve_interpolated(value, _GOLD_LOCALS, "dev") is not None
    assert len(vn.resolve_interpolated(value, _GOLD_LOCALS, "dev")) == 64
    bad = vn.check_name(value, "aws_lambda_function", _GOLD_LOCALS)
    assert bad and "prod" in bad, \
        "a name legal in dev and over the cap in prod was accepted"


def test_dir_context_never_raises_on_an_unreadable_path():
    # runs inside a blocking hook: a disk problem must not refuse the edit
    assert vn.tf_dir_context(os.path.join("no", "such", "x.tf"), "") == ({}, set())


def test_the_locals_map_is_built_when_a_new_interpolated_name_appears():
    # if this predicate ever regresses to "never", resolution silently stops and
    # the gate is blind again — the exact failure the resolver exists to fix
    old = {}
    new = {("aws_glue_job", "x", "name"): "${local.uif_prefix}-gold-newthing"}
    assert vn.needs_module_context(old, new)


def test_the_module_context_is_skipped_when_no_name_is_introduced():
    k = ("aws_glue_job", "x", "name")
    unchanged = {k: "${local.name_prefix}-gold-transform"}
    assert not vn.needs_module_context(unchanged, unchanged), \
        "an unchanged name is never checked, so it must not trigger a dir read"
    # a name that merely moved to another resource is skipped by the caller too
    moved = {("aws_glue_job", "y", "name"): "${local.name_prefix}-gold-transform"}
    assert not vn.needs_module_context(unchanged, moved)
    # a brand-new LITERAL name DOES need it now: the module's other files are
    # what say whether that name already exists elsewhere in the module
    assert vn.needs_module_context({}, {k: "uif-echo-dev-gold-newthing"})

# ------------------------------------------- defects found by adversarial review
# Each of these reproduced against the first cut of the resolver. They are the
# reason tf_locals now goes through _tf_lines and "pre-existing" is module-scoped.

def test_a_trailing_comment_does_not_hide_a_local():
    # one idiomatic `# FROZEN` on the prefix line dropped the local from the map,
    # so resolution silently returned None and the gate went blind again
    for tail in ("# the standard stem", "// frozen until UIFPE-358", ""):
        tf = 'locals {\n  uif_prefix = "uif-echo-${var.environment}" %s\n}\n' % tail
        assert vn.tf_locals(tf) == {"uif_prefix": "uif-echo-${var.environment}"}, tail


def test_a_hash_inside_the_value_is_not_mistaken_for_a_comment():
    tf = 'locals {\n  weird = "a#b"\n}\n'
    assert vn.tf_locals(tf) == {"weird": "a#b"}


def test_a_commented_out_locals_block_does_not_override_the_live_one():
    # the map is last-writer-wins, so a block comment kept for a migration note
    # used to win and the gate denied names against a prefix not in effect
    tf = ('locals {\n  uif_prefix = "uif-echo-${var.environment}"\n}\n'
          '/*\nlocals {\n  uif_prefix = "echo-${var.environment}"\n}\n*/\n')
    assert vn.tf_locals(tf) == {"uif_prefix": "uif-echo-${var.environment}"}


def test_a_heredoc_inside_locals_injects_no_phantom_locals():
    tf = ('locals {\n'
          '  snippet = <<-EOT\n'
          '  def f(cfg):\n'
          '      return {"a": 1\n'
          '  EOT\n'
          '  uif_prefix = "uif-echo-${var.environment}"\n'
          '}\n')
    assert vn.tf_locals(tf) == {"uif_prefix": "uif-echo-${var.environment}"}


def test_a_single_line_locals_block_is_read():
    tf = 'locals { name_prefix = "echo-${var.environment}" }\n'
    assert vn.tf_locals(tf) == {"name_prefix": "echo-${var.environment}"}


def test_a_single_line_locals_block_does_not_leak_into_the_next_block():
    tf = ('locals { region_alias = "ew1" }\n'
          'resource "aws_glue_job" "x" {\n  name = "uif-echo-dev-gold-x"\n}\n')
    assert vn.tf_locals(tf) == {"region_alias": "ew1"}


def test_a_block_comment_marker_inside_a_line_comment_does_not_swallow_the_file():
    # prose like `# paths outside domains/*/src/**` put _tf_lines into
    # block-comment mode, so every name below it vanished from tf_names()
    tf = ('# note: paths outside domains/*/src/**.json are rejected\n'
          'resource "aws_glue_job" "x" {\n  name = "uif-echo-dev-gold-x"\n}\n')
    assert vn.tf_names(tf) == {("aws_glue_job", "x", "name"): "uif-echo-dev-gold-x"}, \
        "a /* inside a # comment still hides the rest of the file"


def test_a_name_already_used_elsewhere_in_the_module_is_not_judged():
    # THE BLOCKER the review found: moving a resource block verbatim into a
    # sibling .tf is a `terraform plan` no-op, but a file-scoped check saw every
    # name in it as new and refused the refactor over a convention the engineer
    # never breached -- with no marker able to get past it.
    legacy = "echo-dev-gold-transform"
    key = ("aws_glue_job", "generic_gold_transform", "name")
    assert vn.check_name(legacy, "aws_glue_job") is not None, \
        "vacuous unless the legacy name really is non-compliant"
    # brand new anywhere in the module -> judged
    assert not vn.already_deployed(key, legacy, {}, set())
    # present in another file of the same module -> not this edit's doing
    assert vn.already_deployed(key, legacy, {}, {legacy})
    # unchanged here, or moved within this file -> the posture that always held
    assert vn.already_deployed(key, legacy, {key: legacy}, set())
    assert vn.already_deployed(
        key, legacy, {("aws_glue_job", "other", "name"): legacy}, set())


def _run_hook(payload):
    import json
    import subprocess
    import sys
    r = subprocess.run([sys.executable, _HOOK], input=json.dumps(payload),
                       capture_output=True, text=True)
    return r.stdout + r.stderr


def _module(tmp_path):
    # looks_like_uif() gates the whole hook on the UIF layout, so a bare tmp_path
    # makes every end-to-end assertion vacuous. Its control test caught this.
    d = tmp_path / "domains" / "product" / "infrastructure" / "terraform"
    d.mkdir(parents=True)
    (d / "locals.tf").write_text(
        'locals {\n  name_prefix = "echo-${var.environment}"\n}\n',
        encoding="utf-8")
    return d


def test_moving_a_legacy_resource_to_a_sibling_file_is_allowed(tmp_path):
    # end to end, because the skip lives in main() and that is where it broke
    d = _module(tmp_path)
    block = ('resource "aws_glue_job" "generic_gold_transform" {\n'
             '  name = "${local.name_prefix}-gold-transform"\n}\n')
    (d / "glue_jobs.tf").write_text(block, encoding="utf-8")
    out = _run_hook({"tool_name": "Write",
                     "tool_input": {"file_path": str(d / "gold_transform.tf"),
                                    "content": block}})
    assert "deny" not in out, \
        "relocating an existing resource verbatim was refused:\n%s" % out


def test_a_genuinely_new_legacy_name_is_still_refused(tmp_path):
    # the control: the module-scoped skip must not become a blanket exemption
    d = _module(tmp_path)
    out = _run_hook({"tool_name": "Write",
                     "tool_input": {"file_path": str(d / "new_job.tf"),
                                    "content": 'resource "aws_glue_job" "fresh" {\n'
                                               '  name = "${local.name_prefix}-gold-fresh"\n}\n'}})
    assert "deny" in out, "a brand-new non-compliant name was allowed through"

def test_the_dollar_escape_is_not_treated_as_a_reference():
    # Terraform renders $${x} as the literal ${x}; substituting inside it
    # produced `$uif-echo-dev-x` and denied a string Terraform never emits
    out = vn.resolve_interpolated("$${local.uif_prefix}-x", _GOLD_LOCALS, "dev")
    assert out is None or "uif-echo-dev" not in out, out
    assert vn.check_name("$${local.uif_prefix}-x", "aws_glue_job", _GOLD_LOCALS) is None


def test_a_fan_out_cycle_terminates_promptly():
    # passes alone do not bound the work: each pass can multiply the length, so
    # a cycle with fan-out 10 reaches 10**8 chars before the pass count runs out
    import time
    fan = {"a": "${local.a}" * 10}
    t0 = time.time()
    assert vn.resolve_interpolated("${local.a}-x", fan, "dev") is None
    assert time.time() - t0 < 1.0, "resolution is not bounded by size"


# ---------------------------------------------------------------------------
# Throwaway namespaces (UIFPE-473): delivery-lab uses the reserved `poc` token
# in the env slot, both ways. Four controls: the sanctioned name passes, a real
# env inside a lab is refused, the lab token outside a lab is refused, and the
# rule is documented where the parity checks can see it.


def _lab_module(tmp_path):
    d = tmp_path / "delivery-lab" / "reference-implementations" / "sample-lab" / "terraform"
    d.mkdir(parents=True)
    return d


def test_a_lab_name_with_the_poc_token_is_allowed(tmp_path):
    d = _lab_module(tmp_path)
    out = _run_hook({"tool_name": "Write",
                     "tool_input": {"file_path": str(d / "main.tf"),
                                    "content": 'resource "aws_glue_job" "sample" {\n'
                                               '  name = "uif-event-poc-publish"\n}\n'}})
    assert "deny" not in out, "the sanctioned lab name was refused:\n%s" % out


def test_a_real_env_inside_delivery_lab_is_refused(tmp_path):
    # the collision half: a lab naming itself into dev is exactly what the
    # token exists to prevent
    d = _lab_module(tmp_path)
    out = _run_hook({"tool_name": "Write",
                     "tool_input": {"file_path": str(d / "main.tf"),
                                    "content": 'resource "aws_glue_job" "sample" {\n'
                                               '  name = "uif-event-dev-publish"\n}\n'}})
    assert "deny" in out, "a real env token inside delivery-lab was allowed:\n%s" % out
    assert "poc" in out, "the refusal does not name the token to use instead:\n%s" % out


def test_the_poc_token_outside_delivery_lab_is_refused(tmp_path):
    # the other direction: poc is not an environment and the estate must not
    # grow resources under it
    d = _module(tmp_path)
    out = _run_hook({"tool_name": "Write",
                     "tool_input": {"file_path": str(d / "new_job.tf"),
                                    "content": 'resource "aws_glue_job" "sample" {\n'
                                               '  name = "uif-echo-poc-dispatch"\n}\n'}})
    assert "deny" in out, "the lab token was accepted in the estate:\n%s" % out
    assert "not an env" in out, "unexpected refusal shape:\n%s" % out


def test_the_lab_token_is_documented_and_single(tmp_path):
    # same discipline as the STACKS/ENVS parity checks: the hook's vocabulary
    # and 00-core.md must not drift apart
    assert vn.LAB_ENVS == {"poc"}, "the lab vocabulary changed; update 00-core.md and these tests together"
    core = rule_text("00-core.md")
    assert "poc" in core and "Throwaway namespaces" in core, \
        "the lab token is enforced but no longer documented in 00-core.md"
