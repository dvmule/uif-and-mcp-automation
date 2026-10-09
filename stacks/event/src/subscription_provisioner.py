"""Subscription provisioner: materialise subscription-configs rows as AWS.

The uif-event-<env>-subscription-configs table is the source of truth; this
Lambda is the component that makes rows REAL, per the lifecycle contract in
docs/subscription-configs.md:

  PENDING   -> provision: create the managed DLQ + queue (names from the
               convention, in THIS account - the row's example target ARNs
               are corrected to the created queue's real ARN in the same
               update as target.target_arn, keeping the flat GSI copy in
               lockstep), write the queue policy's three Sids
               ({policy_sid} EventBridge delivery conditioned on the rule
               ARN, {policy_sid}r direct redrive-Lambda SendMessage - only
               when REDRIVE_ROLE_ARN is configured, {policy_sid}c consumer
               consume actions, never SendMessage), PutRule (detail-type
               filter on the domain bus) + PutTargets (Id = subscription_id,
               subscription DLQ + retry policy), then flip the row ACTIVE
               with the real rule_arn. Every step is idempotent - a crashed
               provision resumes from PENDING.
  SUSPENDED -> DisableRule (rule + target intact; EnableRule on resume).
  ACTIVE    -> (stream transition from SUSPENDED) EnableRule.
  DELETING  -> teardown: RemoveTargets -> DeleteRule -> strip the policy
               statements whose Sid starts with policy_sid -> tag queue +
               DLQ delete_after (+7d grace; a sweeper deletes later) ->
               DELETED tombstone.

Bus resolution (2026-09-25): a provision targets the bus the domain's
pipeline-configs row says the publish job emits to (processor.arguments.
event_bus_name), falling back to the uif-event-<env>-<domain> convention -
so a domain riding another domain's bus (price on the product bus while its
own is pending) gets rules where its events actually flow, and the delivery
DLQ follows the bus's owner. Teardown and suspend/resume use the bus parsed
from the row's STORED rule_arn, so a later config flip never strands a rule.

Triggers: the table's DynamoDB Stream (INSERT/MODIFY, partial-batch
responses), and a manual invoke with {} runs a full reconcile sweep over
PENDING/DELETING rows - the crash-recovery path.

Failure discipline: pre-flight problems mark the row FAILED with a
status_reason (NAME_TOO_LONG, TARGET_TYPE_UNSUPPORTED, RULE_QUOTA,
POLICY_WRITE_FAILED) - visible and resumable, never an invisible live rule.
One documented deviation: an unresolvable consumer principal does NOT fail
the row - the policy is written without the consume Sid and the row goes
ACTIVE with status_reason CONSUMER_PRINCIPAL_INVALID, so bus->queue
delivery works while the consume grant waits for a real principal (the
bootstrap defaults carry example principals).

Every table update is optimistic-locked on record_version; a concurrent
edit skips this pass (the stream/sweep will see the new state).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")
events = boto3.client("events")
kinesis = boto3.client("kinesis")

SUBSCRIPTION_CONFIGS_TABLE = os.environ["SUBSCRIPTION_CONFIGS_TABLE"]
EVENT_STACK_PREFIX = os.environ["EVENT_STACK_PREFIX"]  # uif-event-<env>
ACCOUNT_ID = os.environ["ACCOUNT_ID"]
# Where each domain's publish bus is CONFIGURED (the same row the shared
# publish job reads); conventional name as the default so an unset env var
# never breaks the Lambda.
PIPELINE_CONFIGS_TABLE = os.environ.get(
    "PIPELINE_CONFIGS_TABLE", f"{EVENT_STACK_PREFIX}-pipeline-configs")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
REDRIVE_ROLE_ARN = os.environ.get("REDRIVE_ROLE_ARN", "")
# The ONE shared role EventBridge assumes to put records to managed Kinesis
# streams (Kinesis targets require a RoleArn; stack-owned, domain-agnostic).
KINESIS_DELIVERY_ROLE_ARN = os.environ.get("KINESIS_DELIVERY_ROLE_ARN", "")

RULE_NAME_MAX = 64
QUEUE_NAME_MAX = 80
STREAM_NAME_MAX = 128

# What a KINESIS subscription's consumer principal may do on its managed
# stream (the {sid}c equivalent - read-side only, never PutRecord).
KINESIS_CONSUME_ACTIONS = [
    "kinesis:DescribeStream", "kinesis:DescribeStreamSummary",
    "kinesis:GetShardIterator", "kinesis:GetRecords",
    "kinesis:ListShards", "kinesis:SubscribeToShard",
]


# Per-invocation memo: one config read per domain per Lambda container.
_bus_cache: dict[str, str] = {}


def _domain_bus(domain: str) -> str:
    """The bus the domain's events are PUBLISHED to. The domain's
    pipeline-configs row is the source of truth (processor.arguments.
    event_bus_name - the exact value the shared publish job emits with), so
    rules always land where the events actually flow, including a domain
    riding another domain's bus while its own is pending (price on the
    product bus, 2026-09-25). The naming convention uif-event-<env>-<domain>
    is the fallback, so a missing or unreadable row never blocks
    provisioning - and it stays the only behavior for domains whose config
    carries no bus."""
    if domain in _bus_cache:
        return _bus_cache[domain]
    bus = f"{EVENT_STACK_PREFIX}-{domain}"
    try:
        # pipelineid convention: event-<env>-<domain> (the stack prefix
        # minus its uif- brand), e.g. event-dev-price.
        pipelineid = f"{EVENT_STACK_PREFIX.split('uif-', 1)[-1]}-{domain}"
        item = dynamodb.get_item(
            TableName=PIPELINE_CONFIGS_TABLE,
            Key={"pipelineid": {"S": pipelineid}, "version": {"S": "latest"}},
        ).get("Item") or {}
        configured = (item.get("processor", {}).get("M", {})
                          .get("arguments", {}).get("M", {})
                          .get("event_bus_name", {}).get("S"))
        if isinstance(configured, str) and configured:
            bus = configured
    except Exception:  # noqa: BLE001 - the fallback keeps provisioning alive
        logger.warning("pipeline-configs lookup failed for domain %s - using "
                       "the conventional bus %s", domain, bus, exc_info=True)
    _bus_cache[domain] = bus
    return bus


def _row_bus(row: dict[str, Any]) -> str:
    """The bus an EXISTING subscription's rule lives on: parsed from the
    stored rule_arn (arn:...:rule/<bus>/<name>), so teardown and
    suspend/resume keep working even after the domain's CONFIGURED bus
    flips (interim bus -> own bus). Rows that never activated carry no
    rule_arn and resolve like a fresh provision."""
    rule_arn = _s(row, "rule_arn") or ""
    parts = rule_arn.split("/")
    if len(parts) >= 3:
        return parts[-2]
    return _domain_bus(_s(row, "domain"))


def _bus_dlq_arn(bus: str) -> str:
    """The delivery DLQ paired with the bus (event-domain module):
    uif-event-<env>-<owner>-dlq, where <owner> is the bus's OWN domain - a
    domain riding another domain's bus dead-letters to that bus's DLQ, the
    one queue the shared DLQ recorder consumes (the recorder derives the
    true domain from each envelope, so lineage stays correct)."""
    prefix = f"{EVENT_STACK_PREFIX}-"
    owner = bus[len(prefix):] if bus.startswith(prefix) else bus
    return (f"arn:aws:sqs:{AWS_REGION}:{ACCOUNT_ID}:"
            f"{EVENT_STACK_PREFIX}-{owner}-dlq")

# The bus carries one copy of each event per delivery channel (the gold
# x-channels vocabulary), discriminated by the envelope's `channel` field;
# a rule matches detail-type + detail.channel so a subscription receives
# only its own channel's projection, chosen by its target_type.
TARGET_TYPE_CHANNEL = {"SQS": "event", "KINESIS": "message", "API": "api"}
DELETE_GRACE_DAYS = 7

CONSUME_ACTIONS = ["sqs:ReceiveMessage", "sqs:DeleteMessage",
                   "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes",
                   "sqs:GetQueueUrl"]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("Records"):
        return _handle_stream(event)
    return _sweep()


def _handle_stream(event: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event["Records"]:
        try:
            if record.get("eventName") not in ("INSERT", "MODIFY"):
                continue
            new = record["dynamodb"].get("NewImage") or {}
            old = record["dynamodb"].get("OldImage") or {}
            _route(new, _s(old, "status"))
        except Exception:  # noqa: BLE001 - fail only this record, loudly
            logger.exception("Stream record %s failed - returned to the stream",
                             record.get("eventID"))
            # For a DynamoDB stream the item identifier Lambda accepts is the
            # record's SequenceNumber, not its eventID. With the eventID,
            # Lambda logged "Sequence number in function response is invalid"
            # and retried the WHOLE batch - every record, not just the failed
            # one (found live 2026-10-01). eventID stays the fallback for a
            # hand-built test record without a sequence number.
            failures.append({"itemIdentifier":
                             record["dynamodb"].get("SequenceNumber")
                             or record["eventID"]})
    return {"batchItemFailures": failures}


def _sweep() -> dict[str, Any]:
    """Manual reconcile: resume every PENDING provision and DELETING
    teardown - the crash-recovery path the contract requires."""
    handled = 0
    kwargs: dict[str, Any] = {
        "TableName": SUBSCRIPTION_CONFIGS_TABLE,
        "FilterExpression": "#s IN (:p, :d)",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":p": {"S": "PENDING"},
                                      ":d": {"S": "DELETING"}},
    }
    while True:
        page = dynamodb.scan(**kwargs)
        for row in page.get("Items", []):
            _route(row, None)
            handled += 1
        if "LastEvaluatedKey" not in page:
            return {"handled": handled}
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _route(row: dict[str, Any], old_status: str | None) -> None:
    status = _s(row, "status")
    if status == "PENDING":
        _provision(row)
    elif status == "DELETING":
        _teardown(row)
    elif status == "SUSPENDED":
        _set_rule_state(row, enabled=False)
    elif status == "ACTIVE" and old_status == "SUSPENDED":
        _set_rule_state(row, enabled=True)
    # ACTIVE/FAILED/DELETED otherwise: nothing to do.


# ---------- provision (PENDING -> ACTIVE) ----------

def _provision(row: dict[str, Any]) -> None:
    customer = _s(row, "customer_id")
    domain = _s(row, "domain")
    detail_type = _s(row, "detail_type")
    sid = _s(row, "policy_sid")
    subscription_id = _s(row, "subscription_id")
    target_type = _s(row.get("target", {}).get("M", {}), "target_type")

    if target_type == "KINESIS":
        _provision_kinesis(row)
        return
    if target_type != "SQS":
        # Managed provisioning covers SQS and KINESIS (2026-09-24); API is
        # the bring-your-own path (docs) - visible, resumable failure.
        _fail(row, "TARGET_TYPE_UNSUPPORTED")
        return

    dashes = detail_type.replace(".", "-")
    rule_name = f"{EVENT_STACK_PREFIX}-on-{domain}-{dashes}-{customer}"
    queue_name = f"{EVENT_STACK_PREFIX}-{customer}-{domain}-{dashes}"
    dlq_name = f"{queue_name}-dlq"
    if len(rule_name) > RULE_NAME_MAX or len(dlq_name) > QUEUE_NAME_MAX:
        _fail(row, "NAME_TOO_LONG")
        return
    # The bus the domain PUBLISHES to: its pipeline-configs row, falling
    # back to the naming convention - derived from the ROW plus the domain's
    # own config, never from stack resources.
    bus = _domain_bus(domain)
    rule_arn = (f"arn:aws:events:{AWS_REGION}:{ACCOUNT_ID}:rule/"
                f"{bus}/{rule_name}")

    # 2-3. Queues (idempotent: an existing queue is reused).
    dlq_url, dlq_arn = _ensure_queue(dlq_name)
    queue_url, queue_arn = _ensure_queue(queue_name, redrive_dlq_arn=dlq_arn)

    # 4. Queue policy - the three Sids, prefix-scoped to this subscription.
    config = row.get("target", {}).get("M", {}).get("target_config", {}).get("M", {})
    principal = _s(config, "consumer_principal_arn")
    statements = [{
        "Sid": sid,
        "Effect": "Allow",
        "Principal": {"Service": "events.amazonaws.com"},
        "Action": "sqs:SendMessage",
        "Resource": queue_arn,
        "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
    }]
    if REDRIVE_ROLE_ARN:
        statements.append({
            "Sid": f"{sid}r",
            "Effect": "Allow",
            "Principal": {"AWS": REDRIVE_ROLE_ARN},
            "Action": "sqs:SendMessage",
            "Resource": queue_arn,
        })
    consume = {
        "Sid": f"{sid}c",
        "Effect": "Allow",
        "Principal": {"AWS": principal},
        "Action": CONSUME_ACTIONS,
        "Resource": queue_arn,
    } if principal else None

    reason = None
    try:
        _write_policy(queue_url, sid, statements + ([consume] if consume else []))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in (
                "InvalidAttributeValue", "MalformedPolicyDocument", "InvalidParameterValue"):
            _fail(row, "POLICY_WRITE_FAILED")
            raise
        # Documented deviation: an unresolvable consumer principal does not
        # fail the row - delivery works, the consume grant waits.
        logger.warning("Consumer principal %s rejected for %s/%s - policy "
                       "written without the consume Sid", principal,
                       customer, detail_type)
        _write_policy(queue_url, sid, statements)
        reason = "CONSUMER_PRINCIPAL_INVALID"

    # 5. Rule + target on the domain bus.
    retry = config.get("retry_policy", {}).get("M", {})
    try:
        events.put_rule(Name=rule_name, EventBusName=bus,
                        EventPattern=json.dumps({
                            "detail-type": [detail_type],
                            "detail": {"channel": [TARGET_TYPE_CHANNEL[target_type]]},
                        }),
                        State="ENABLED")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "LimitExceededException":
            _fail(row, "RULE_QUOTA")
            return
        raise
    events.put_targets(Rule=rule_name, EventBusName=bus, Targets=[{
        "Id": subscription_id,
        "Arn": queue_arn,
        # Deliver ONLY our CloudEvents envelope: InputPath strips
        # EventBridge's wrapper (version/id/detail-type/source/account/time/
        # region/resources), so consumers see one envelope and dedupe on the
        # CloudEvents id. Dead-lettered messages bypass this transform and
        # keep the full wrapper + attributes the DLQ recorder needs.
        "InputPath": "$.detail",
        # Exhausted DELIVERIES dead-letter to the DOMAIN DLQ - the one queue
        # the shared DLQ recorder consumes (event-domain module wires the ESM
        # and the events.amazonaws.com grant there). The per-subscription DLQ
        # (dlq_arn) is the SUBSCRIPTION QUEUE's own RedrivePolicy target -
        # consumer-side poison messages - and was wrong here: nothing reads
        # it, so failed deliveries were invisible (found live 2026-09-21).
        "DeadLetterConfig": {"Arn": _bus_dlq_arn(bus)},
        "RetryPolicy": {
            "MaximumRetryAttempts": int(_n(retry, "max_attempts", 3)),
            "MaximumEventAgeInSeconds": int(_n(retry, "max_age_seconds", 900)),
        },
    }])

    # 6. Activate - real ARNs written back, flat target_arn in the SAME
    # update as target.target_arn (the GSI-divergence rule).
    expr = ("SET #s = :active, rule_arn = :rule, target_arn = :qarn, "
            "#t.target_arn = :qarn, #t.target_config.dlq_arn = :dlq, "
            "status_reason = :reason, record_version = record_version + :one, "
            "updated_at = :now, updated_by = :who")
    _locked_update(row, expr, {
        ":active": {"S": "ACTIVE"},
        ":rule": {"S": rule_arn},
        ":qarn": {"S": queue_arn},
        ":dlq": {"S": dlq_arn},
        ":reason": {"S": reason} if reason else {"NULL": True},
        ":one": {"N": "1"},
        ":now": {"S": _utcnow()},
        ":who": {"S": "subscription-provisioner"},
    }, names={"#s": "status", "#t": "target"})
    logger.info("Provisioned %s / %s: rule=%s queue=%s%s", customer,
                detail_type, rule_name, queue_name,
                f" ({reason})" if reason else "")


def _provision_kinesis(row: dict[str, Any]) -> None:
    """PENDING -> ACTIVE for a managed KINESIS subscription (2026-09-24,
    first client: pdz). Same shape as the SQS path with the transport
    swapped: a managed ON_DEMAND stream instead of a queue, the shared
    stack-owned delivery role on the target (Kinesis targets require a
    RoleArn), the rule filtering detail-type + channel "message", and the
    consumer grant as the stream's resource policy ({sid}c, read-side
    actions only). PartitionKeyPath resolves against the ORIGINAL event, so
    $.detail.partitionkey keys shards by {businessKey}|{org} even though
    InputPath delivers only the envelope."""
    customer = _s(row, "customer_id")
    domain = _s(row, "domain")
    detail_type = _s(row, "detail_type")
    sid = _s(row, "policy_sid")
    subscription_id = _s(row, "subscription_id")
    config = row.get("target", {}).get("M", {}).get("target_config", {}).get("M", {})

    if not KINESIS_DELIVERY_ROLE_ARN:
        # The stack deploys the role; an empty env var means this Lambda
        # predates it - visible, resumable failure, never a crash.
        _fail(row, "KINESIS_DELIVERY_ROLE_MISSING")
        return

    dashes = detail_type.replace(".", "-")
    rule_name = f"{EVENT_STACK_PREFIX}-on-{domain}-{dashes}-{customer}"
    stream_name = f"{EVENT_STACK_PREFIX}-{customer}-{domain}-{dashes}"
    if len(rule_name) > RULE_NAME_MAX or len(stream_name) > STREAM_NAME_MAX:
        _fail(row, "NAME_TOO_LONG")
        return
    bus = _domain_bus(domain)
    rule_arn = (f"arn:aws:events:{AWS_REGION}:{ACCOUNT_ID}:rule/"
                f"{bus}/{rule_name}")

    stream_arn = _ensure_stream(stream_name)

    # Consumer grant: the stream's resource policy. Same deviation as SQS -
    # an unresolvable principal does not fail the row; delivery works, the
    # consume grant waits for real onboarding.
    principal = _s(config, "consumer_principal_arn")
    reason = None
    if principal:
        policy = {"Version": "2012-10-17", "Statement": [{
            "Sid": f"{sid}c",
            "Effect": "Allow",
            "Principal": {"AWS": principal},
            "Action": KINESIS_CONSUME_ACTIONS,
            "Resource": [stream_arn, f"{stream_arn}/*"],
        }]}
        try:
            kinesis.put_resource_policy(ResourceARN=stream_arn,
                                        Policy=json.dumps(policy))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("ValidationException", "InvalidArgumentException",
                            "MalformedPolicyDocument"):
                _fail(row, "POLICY_WRITE_FAILED")
                raise
            logger.warning("Consumer principal %s rejected for %s/%s - "
                           "stream policy skipped", principal, customer,
                           detail_type)
            reason = "CONSUMER_PRINCIPAL_INVALID"

    retry = config.get("retry_policy", {}).get("M", {})
    try:
        events.put_rule(Name=rule_name, EventBusName=bus,
                        EventPattern=json.dumps({
                            "detail-type": [detail_type],
                            "detail": {"channel": [TARGET_TYPE_CHANNEL["KINESIS"]]},
                        }),
                        State="ENABLED")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "LimitExceededException":
            _fail(row, "RULE_QUOTA")
            return
        raise
    partition_key_path = _s(config, "partition_key_path") or "$.detail.partitionkey"
    events.put_targets(Rule=rule_name, EventBusName=bus, Targets=[{
        "Id": subscription_id,
        "Arn": stream_arn,
        "RoleArn": KINESIS_DELIVERY_ROLE_ARN,
        # Bare envelope on the stream (same consumer contract as SQS);
        # PartitionKeyPath is evaluated on the pre-transform event.
        "InputPath": "$.detail",
        "KinesisParameters": {"PartitionKeyPath": partition_key_path},
        "DeadLetterConfig": {"Arn": _bus_dlq_arn(bus)},
        "RetryPolicy": {
            "MaximumRetryAttempts": int(_n(retry, "max_attempts", 3)),
            "MaximumEventAgeInSeconds": int(_n(retry, "max_age_seconds", 900)),
        },
    }])

    expr = ("SET #s = :active, rule_arn = :rule, target_arn = :qarn, "
            "#t.target_arn = :qarn, "
            "status_reason = :reason, record_version = record_version + :one, "
            "updated_at = :now, updated_by = :who")
    _locked_update(row, expr, {
        ":active": {"S": "ACTIVE"},
        ":rule": {"S": rule_arn},
        ":qarn": {"S": stream_arn},
        ":reason": {"S": reason} if reason else {"NULL": True},
        ":one": {"N": "1"},
        ":now": {"S": _utcnow()},
        ":who": {"S": "subscription-provisioner"},
    }, names={"#s": "status", "#t": "target"})
    logger.info("Provisioned %s / %s: rule=%s stream=%s%s", customer,
                detail_type, rule_name, stream_name,
                f" ({reason})" if reason else "")


def _ensure_stream(name: str) -> str:
    """Create-if-missing, ON_DEMAND (no shard sizing to manage per
    subscription), then wait ACTIVE. Idempotent: an existing stream is
    reused as-is."""
    try:
        kinesis.create_stream(StreamName=name,
                              StreamModeDetails={"StreamMode": "ON_DEMAND"})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceInUseException":
            raise
    for _ in range(30):
        summary = kinesis.describe_stream_summary(
            StreamName=name)["StreamDescriptionSummary"]
        if summary["StreamStatus"] == "ACTIVE":
            # A reused stream may still carry the delete_after a teardown
            # tagged it with (re-seed after DELETING - the bus-flip path,
            # live 2026-10-01). It is live again: clear the mark, or a
            # sweeper would delete a stream a subscription depends on.
            kinesis.remove_tags_from_stream(StreamName=name,
                                            TagKeys=["delete_after"])
            return summary["StreamARN"]
        time.sleep(2)
    raise TimeoutError(f"Stream {name} did not become ACTIVE")


def _ensure_queue(name: str, redrive_dlq_arn: str | None = None) -> tuple[str, str]:
    attrs = {"SqsManagedSseEnabled": "true",
             "MessageRetentionPeriod": str(4 * 86400)}
    if redrive_dlq_arn:
        attrs["RedrivePolicy"] = json.dumps(
            {"deadLetterTargetArn": redrive_dlq_arn, "maxReceiveCount": 5})
    try:
        url = sqs.create_queue(QueueName=name, Attributes=attrs)["QueueUrl"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "QueueNameExists":
            raise
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    # Same as the stream: a reused queue (and its DLQ) may still carry a
    # teardown's delete_after tag - it is live again, so clear the mark.
    # Untagging an absent key is a no-op, so this runs on every path.
    sqs.untag_queue(QueueUrl=url, TagKeys=["delete_after"])
    arn = sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    return url, arn


def _write_policy(queue_url: str, sid_prefix: str,
                  statements: list[dict[str, Any]]) -> None:
    """Merge this subscription's Sids into the queue policy, replacing only
    statements with our prefix - other subscriptions' and the customer's own
    statements stay untouched."""
    current = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["Policy"]).get("Attributes", {})
    policy = json.loads(current["Policy"]) if current.get("Policy") else {
        "Version": "2012-10-17", "Statement": []}
    kept = [s for s in policy.get("Statement", [])
            if not str(s.get("Sid", "")).startswith(sid_prefix)]
    policy["Statement"] = kept + statements
    # SQS rejects a policy whose Statement is empty (InvalidAttributeValue) -
    # exactly what a teardown produces on a queue only we ever wrote to. An
    # empty Policy attribute is the API's way of removing the policy. Found
    # live 2026-10-01: the price bus flip's pmc teardowns failed here and,
    # retried forever, blocked the stream shard behind them.
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={"Policy": json.dumps(policy) if policy["Statement"] else ""})


# ---------- teardown (DELETING -> DELETED) ----------

def _teardown(row: dict[str, Any]) -> None:
    customer = _s(row, "customer_id")
    domain = _s(row, "domain")
    detail_type = _s(row, "detail_type")
    sid = _s(row, "policy_sid")
    subscription_id = _s(row, "subscription_id")
    dashes = detail_type.replace(".", "-")
    rule_name = f"{EVENT_STACK_PREFIX}-on-{domain}-{dashes}-{customer}"
    queue_name = f"{EVENT_STACK_PREFIX}-{customer}-{domain}-{dashes}"

    # The bus from the STORED rule_arn, not a fresh resolution: the domain's
    # configured bus may have flipped since activation, and the rule lives
    # where it was created.
    bus = _row_bus(row)
    for call, kwargs in (
        (events.remove_targets, {"Rule": rule_name,
                                 "EventBusName": bus,
                                 "Ids": [subscription_id]}),
        (events.delete_rule, {"Name": rule_name,
                              "EventBusName": bus}),
    ):
        try:
            call(**kwargs)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise

    delete_after = (datetime.now(timezone.utc)
                    + timedelta(days=DELETE_GRACE_DAYS)).isoformat()
    target_type = _s(row.get("target", {}).get("M", {}), "target_type")
    if target_type == "KINESIS":
        # queue_name doubles as the stream name (same convention). Grace
        # period via tag, like queues - never a hard delete here.
        try:
            arn = kinesis.describe_stream_summary(
                StreamName=queue_name)["StreamDescriptionSummary"]["StreamARN"]
        except ClientError:
            arn = None  # never created, or already swept
        if arn:
            try:
                kinesis.delete_resource_policy(ResourceARN=arn)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                    raise
            kinesis.add_tags_to_stream(StreamName=queue_name,
                                       Tags={"delete_after": delete_after})
    else:
        for name in (queue_name, f"{queue_name}-dlq"):
            try:
                url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
            except ClientError:
                continue  # never created, or already swept
            _write_policy(url, sid, [])  # strip only our Sids
            sqs.tag_queue(QueueUrl=url, Tags={"delete_after": delete_after})

    _locked_update(row,
                   "SET #s = :deleted, deleted_at = :now, "
                   "record_version = record_version + :one, "
                   "updated_at = :now, updated_by = :who",
                   {":deleted": {"S": "DELETED"}, ":one": {"N": "1"},
                    ":now": {"S": _utcnow()},
                    ":who": {"S": "subscription-provisioner"}},
                   names={"#s": "status"})
    logger.info("Tore down %s / %s (queues tagged delete_after=%s)",
                customer, detail_type, delete_after)


# ---------- suspend / resume ----------

def _set_rule_state(row: dict[str, Any], *, enabled: bool) -> None:
    dashes = _s(row, "detail_type").replace(".", "-")
    rule_name = (f"{EVENT_STACK_PREFIX}-on-{_s(row, 'domain')}-{dashes}-"
                 f"{_s(row, 'customer_id')}")
    call = events.enable_rule if enabled else events.disable_rule
    try:
        # Same rationale as teardown: the rule lives on the bus recorded at
        # activation, whatever the domain's config says today.
        call(Name=rule_name, EventBusName=_row_bus(row))
        logger.info("%s rule %s", "Enabled" if enabled else "Disabled", rule_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        logger.info("Rule %s does not exist yet - nothing to %s", rule_name,
                    "enable" if enabled else "disable")


# ---------- helpers ----------

def _fail(row: dict[str, Any], reason: str) -> None:
    logger.error("Subscription %s / %s FAILED: %s", _s(row, "customer_id"),
                 _s(row, "detail_type"), reason)
    _locked_update(row,
                   "SET #s = :failed, status_reason = :reason, "
                   "record_version = record_version + :one, "
                   "updated_at = :now, updated_by = :who",
                   {":failed": {"S": "FAILED"}, ":reason": {"S": reason},
                    ":one": {"N": "1"}, ":now": {"S": _utcnow()},
                    ":who": {"S": "subscription-provisioner"}},
                   names={"#s": "status"})


def _locked_update(row: dict[str, Any], expression: str,
                   values: dict[str, Any],
                   names: dict[str, str] | None = None) -> None:
    """Optimistic lock on record_version: a concurrent edit means this pass's
    view is stale - skip; the stream/sweep sees the new state."""
    values[":expected"] = {"N": _s(row, "record_version") or "0"}
    try:
        dynamodb.update_item(
            TableName=SUBSCRIPTION_CONFIGS_TABLE,
            Key={"customer_id": row["customer_id"],
                 "domain_detail_type": row["domain_detail_type"]},
            UpdateExpression=expression,
            ConditionExpression="record_version = :expected",
            ExpressionAttributeNames=names or {},
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.warning("Row %s/%s changed concurrently - update skipped",
                           _s(row, "customer_id"), _s(row, "domain_detail_type"))
            return
        raise


def _s(image: dict[str, Any], key: str) -> str | None:
    value = image.get(key) or {}
    return value.get("S") or value.get("N")


def _n(image: dict[str, Any], key: str, default: int) -> int:
    value = image.get(key) or {}
    return int(value.get("N", default))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
