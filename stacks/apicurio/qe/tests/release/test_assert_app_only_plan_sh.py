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


def test_a_null_resource_changes_is_refused_rather_than_read_as_empty():
    # A key that is present but null still fails `.resource_changes[]`
    # downstream, which `|| true` swallows -- exercise the guard directly so
    # that failure never resurfaces as "nothing to apply".
    r = run(json.dumps({"resource_changes": None}))
    assert r.returncode == 1
    assert r.returncode != 3


def test_a_non_array_resource_changes_is_refused_rather_than_read_as_empty():
    r = run(json.dumps({"resource_changes": "oops"}))
    assert r.returncode == 1
    assert r.returncode != 3


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


# --- Task definition replacement (UIFPE-614) ---------------------------------
#
# An ECS task definition is immutable in AWS. The image digest lives inside
# container_definitions, which cannot be changed in place, so Terraform
# registers a new revision and deregisters the old one and reports it as a
# replacement. Every promotion that changes the digest therefore plans
# "delete,create" on the task definition -- the shape these tests had never
# covered, which is why promoting apicurio/v0.2.4 to uat was refused.


def test_a_task_definition_replacement_is_the_normal_promotion():
    """The exact plan that run 36122581436 refused.

    This is not an edge case: it is what a promotion looks like whenever the
    digest changes, which is every promotion that does any work at all.
    """
    r = run(plan_json(
        ("aws_ecs_task_definition.bff", ["delete", "create"]),
        ("aws_ecs_service.bff", ["update"]),
    ))
    assert r.returncode == 0, r.stderr
    assert "App-only deploy confirmed" in r.stdout


def test_a_task_definition_replacement_is_confirmed_under_create_before_destroy():
    # Adding a create_before_destroy lifecycle reverses the pair. It is the
    # same replacement and must read the same way.
    r = run(plan_json(("aws_ecs_task_definition.bff", ["create", "delete"])))
    assert r.returncode == 0, r.stderr


def test_replacing_the_service_is_still_refused():
    # A service is mutable: rolling a new revision onto it is an update.
    # Replacing one drops the running workload, so no verb pair is safe here.
    r = run(plan_json(("aws_ecs_service.bff", ["delete", "create"])))
    assert r.returncode == 1
    assert "aws_ecs_service.bff" in r.stderr


def test_a_bare_deletion_of_the_task_definition_is_still_refused():
    # Replacement is two verbs. A lone delete deregisters the revision the
    # service is running and puts nothing back.
    r = run(plan_json(("aws_ecs_task_definition.bff", ["delete"])))
    assert r.returncode == 1
    assert "aws_ecs_task_definition.bff" in r.stderr
