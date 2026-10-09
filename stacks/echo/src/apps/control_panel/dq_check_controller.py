# dq_check_controller.py
# Lambda backing /dq/* routes via API Gateway HTTP API. Mirrors the
# pipeline_controller style: routes are matched in handler(), all responses
# are wrapped through ok/bad_request/not_found/server_error.
#
# Tables:
#   dq_check_config_{env}  (PK=check_id, SK=version)
#   glue_job_metrics_{env} (reused for run history — items written by
#                           generic_silver_quality_check.py)
#
# Routes:
#   GET    /dq/checks                            list_checks
#   GET    /dq/checks/{check_id}                 get_check
#   PUT    /dq/checks/{check_id}                 upsert_check  (validates kind+params)
#   DELETE /dq/checks/{check_id}?cascade=true    delete_check
#   POST   /dq/checks/{check_id}/trigger-run     trigger_run
#   GET    /dq/checks/{check_id}/runs            list_runs
#   GET    /dq/kinds                             list_kinds  (read-only catalog)

import json
import logging
import os
import re
import boto3
import decimal
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Attr, Key

from echo_resources import echo, echo_legacy

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION           = os.environ.get("REGION",           "eu-west-1")
DQ_CHECK_TABLE   = os.environ.get("DQ_CHECK_TABLE",   echo_legacy("dq-check-config"))
METRICS_TABLE    = os.environ.get("METRICS_TABLE",    echo_legacy("glue-job-metrics"))
DQ_GLUE_JOB_NAME = os.environ.get("DQ_GLUE_JOB_NAME", echo("silver-quality-check"))

dynamo = boto3.resource("dynamodb", region_name=REGION)
glue   = boto3.client("glue",        region_name=REGION)


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def ok(body):           return {"statusCode": 200, "body": json.dumps(body, cls=_DecimalEncoder)}
def created(body):      return {"statusCode": 201, "body": json.dumps(body, cls=_DecimalEncoder)}
def bad_request(msg):   return {"statusCode": 400, "body": json.dumps({"error": msg})}
def not_found(msg):     return {"statusCode": 404, "body": json.dumps({"error": msg})}
def server_error(msg):  return {"statusCode": 500, "body": json.dumps({"error": msg})}


KNOWN_KINDS = {"reconcile", "null-rate", "row-count", "freshness",
               "drift", "uniqueness", "custom-sql"}
LIVE_KINDS  = {"reconcile", "null-rate"}
SECRET_ARN_RE = re.compile(r"^arn:aws:secretsmanager:[a-z0-9\-]+:\d{12}:secret:.+$")


# ─── Validation ──────────────────────────────────────────────────────
def _validate_subject(subject):
    if not isinstance(subject, dict):
        return "subject must be an object"
    if subject.get("layer") not in ("silver", "gold"):
        return "subject.layer must be 'silver' or 'gold'"
    if not subject.get("database"):
        return "subject.database is required"
    if not subject.get("table"):
        return "subject.table is required"
    return None


def _validate_schedule(schedule):
    if not isinstance(schedule, dict):
        return "schedule must be an object"
    freq = schedule.get("frequency")
    if freq not in ("hourly", "daily", "weekly", "cron"):
        return "schedule.frequency must be 'hourly' | 'daily' | 'weekly' | 'cron'"
    if freq == "cron" and not schedule.get("cron"):
        return "schedule.cron expression is required when frequency='cron'"
    return None


def _validate_parameters(kind, params):
    if not isinstance(params, dict):
        return "parameters must be an object"
    if kind == "reconcile":
        for f in ("pk_cols", "max_deletes_per_run", "dry_run",
                  "oracle_secret_arn", "jdbc_url"):
            if f not in params:
                return f"reconcile.parameters.{f} is required"
        if not isinstance(params["pk_cols"], list) or not params["pk_cols"]:
            return "reconcile.parameters.pk_cols must be a non-empty list of column names"
        if not SECRET_ARN_RE.match(params["oracle_secret_arn"]):
            return "reconcile.parameters.oracle_secret_arn must be a Secrets Manager ARN"
        if not isinstance(params["jdbc_url"], str) or not params["jdbc_url"].startswith("jdbc:"):
            return "reconcile.parameters.jdbc_url must start with 'jdbc:'"
        return None
    if kind == "null-rate":
        if not params.get("column"):
            return "null-rate.parameters.column is required"
        t = params.get("threshold_pct")
        if t is None or not isinstance(t, (int, float)) or not (0 <= t <= 1):
            return "null-rate.parameters.threshold_pct must be a float between 0 and 1"
        return None
    if kind == "row-count":
        if params.get("min") is None and params.get("max") is None:
            return "row-count.parameters requires at least one of min/max"
        return None
    if kind == "freshness":
        if not params.get("ts_col"):
            return "freshness.parameters.ts_col is required"
        if not isinstance(params.get("max_lag_minutes"), int):
            return "freshness.parameters.max_lag_minutes must be an integer"
        return None
    if kind == "drift":
        if not params.get("reference_run_id"):
            return "drift.parameters.reference_run_id is required"
        cols = params.get("columns")
        if not isinstance(cols, list) or not cols:
            return "drift.parameters.columns must be a non-empty list"
        return None
    if kind == "uniqueness":
        cols = params.get("columns")
        if not isinstance(cols, list) or not cols:
            return "uniqueness.parameters.columns must be a non-empty list"
        return None
    if kind == "custom-sql":
        if not params.get("sql") or not isinstance(params["sql"], str):
            return "custom-sql.parameters.sql is required"
        return None
    return f"unknown kind '{kind}'"


def _validate_check(body):
    if not body.get("check_id"):
        return "check_id is required"
    kind = body.get("kind")
    if kind not in KNOWN_KINDS:
        return f"kind must be one of {sorted(KNOWN_KINDS)}"
    err = _validate_subject(body.get("subject"))
    if err: return err
    err = _validate_schedule(body.get("schedule"))
    if err: return err
    err = _validate_parameters(kind, body.get("parameters"))
    if err: return err
    return None


# ─── DynamoDB helpers ────────────────────────────────────────────────
def _to_dynamo_safe(obj):
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo_safe(i) for i in obj]
    return obj


def _subject_match(check, subject_filter):
    if not subject_filter:
        return True
    parts = subject_filter.split(".")
    if len(parts) != 3:
        return False
    layer, db, tbl = parts
    s = check.get("subject") or {}
    return s.get("layer") == layer and s.get("database") == db and s.get("table") == tbl


# ─── Route handlers ──────────────────────────────────────────────────
def list_checks(query):
    resp = dynamo.Table(DQ_CHECK_TABLE).scan(
        FilterExpression=Attr("version").eq("latest")
    )
    items = resp.get("Items", [])
    subject_filter = (query or {}).get("subject")
    if subject_filter:
        items = [c for c in items if _subject_match(c, subject_filter)]
    return ok({"checks": items, "count": len(items)})


def get_check(check_id):
    resp = dynamo.Table(DQ_CHECK_TABLE).get_item(
        Key={"check_id": check_id, "version": "latest"}
    )
    if "Item" not in resp:
        return not_found(f"Check '{check_id}' not found")
    return ok(resp["Item"])


def upsert_check(check_id, body):
    # URL path is canonical — body.check_id is overwritten so the client never
    # has to set both fields. Mirrors pipeline_controller's PUT semantics.
    body["check_id"] = check_id
    err = _validate_check(body)
    if err:
        return bad_request(err)

    now = datetime.now(timezone.utc).isoformat()
    body.setdefault("enabled", True)
    body["version"] = "latest"

    existing = dynamo.Table(DQ_CHECK_TABLE).get_item(
        Key={"check_id": check_id, "version": "latest"}
    ).get("Item")
    meta = body.get("metadata") or {}
    if existing and existing.get("metadata", {}).get("created_at"):
        meta["created_at"] = existing["metadata"]["created_at"]
    else:
        meta.setdefault("created_at", now)
    meta.setdefault("owner", meta.get("owner") or "unknown")
    body["metadata"] = meta

    dynamo.Table(DQ_CHECK_TABLE).put_item(Item=_to_dynamo_safe(body))
    logger.info(f"[DQ UPSERT] check_id={check_id} kind={body['kind']}")
    return created(body) if not existing else ok(body)


def delete_check(check_id, cascade):
    if not cascade:
        return bad_request(
            "DELETE /dq/checks/{check_id} requires ?cascade=true to confirm "
            "deletion of the check config (run history in glue_job_metrics is "
            "left intact for audit)."
        )
    existing = dynamo.Table(DQ_CHECK_TABLE).get_item(
        Key={"check_id": check_id, "version": "latest"}
    ).get("Item")
    if not existing:
        return not_found(f"Check '{check_id}' not found")
    dynamo.Table(DQ_CHECK_TABLE).delete_item(
        Key={"check_id": check_id, "version": "latest"}
    )
    logger.info(f"[DQ DELETE cascade=true] check_id={check_id}")
    return ok({"deleted": check_id, "notes": [
        "Run history (glue_job_metrics rows) preserved for audit — TTL will expire after 90 days."
    ]})


def trigger_run(check_id):
    existing = dynamo.Table(DQ_CHECK_TABLE).get_item(
        Key={"check_id": check_id, "version": "latest"}
    ).get("Item")
    if not existing:
        return not_found(f"Check '{check_id}' not found")
    response = glue.start_job_run(
        JobName=DQ_GLUE_JOB_NAME,
        Arguments={"--check_id": check_id},
        WorkerType="G.1X",
        NumberOfWorkers=2,
        Timeout=60,
    )
    return ok({
        "check_id":   check_id,
        "run_id":     response["JobRunId"],
        "job_name":   DQ_GLUE_JOB_NAME,
        "started_at": datetime.now(timezone.utc).isoformat(),
    })


def list_runs(check_id):
    """List runs for one check by querying glue_job_metrics's pipeline_id-index.
    Glue writes pipeline_id=f'dq:{check_id}' so the index lookup is cheap."""
    try:
        resp = dynamo.Table(METRICS_TABLE).query(
            IndexName="pipeline_id-index",
            KeyConditionExpression=Key("pipeline_id").eq(f"dq:{check_id}"),
            ScanIndexForward=False,
            Limit=50,
        )
        return ok({"check_id": check_id, "runs": resp.get("Items", [])})
    except Exception as e:
        logger.warning(f"[DQ list_runs] failed: {e}")
        return ok({"check_id": check_id, "runs": []})


def list_kinds():
    return ok({"kinds": [
        {"kind": "reconcile", "status": "live",
         "parameters": ["pk_cols", "pk_partition_col", "max_deletes_per_run",
                        "dry_run", "oracle_secret_arn", "jdbc_url"]},
        {"kind": "null-rate", "status": "live",
         "parameters": ["column", "threshold_pct"]},
        {"kind": "row-count", "status": "coming-soon",
         "parameters": ["min", "max"]},
        {"kind": "freshness", "status": "coming-soon",
         "parameters": ["ts_col", "max_lag_minutes"]},
        {"kind": "drift", "status": "coming-soon",
         "parameters": ["reference_run_id", "columns"]},
        {"kind": "uniqueness", "status": "coming-soon",
         "parameters": ["columns"]},
        {"kind": "custom-sql", "status": "coming-soon",
         "parameters": ["sql", "expected_rows"]},
    ]})


def _check_id_from(event, raw_path, trim_suffix=None):
    path_params = event.get("pathParameters") or {}
    if path_params.get("check_id"):
        return path_params["check_id"]
    parts = raw_path.rstrip("/").split("/")
    if trim_suffix and parts and parts[-1] == trim_suffix:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def handler(event, context):
    method   = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    raw_path = event.get("rawPath", "/")
    query    = event.get("queryStringParameters") or {}
    body     = {}
    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except Exception:
            return bad_request("Invalid JSON body")

    logger.info(f"[DQ REQUEST] {method} {raw_path}")
    try:
        if method == "GET" and raw_path == "/dq/checks":
            return list_checks(query)
        if method == "GET" and raw_path == "/dq/kinds":
            return list_kinds()
        if method == "POST" and raw_path.endswith("/trigger-run"):
            return trigger_run(_check_id_from(event, raw_path, trim_suffix="trigger-run"))
        if method == "GET" and raw_path.endswith("/runs"):
            return list_runs(_check_id_from(event, raw_path, trim_suffix="runs"))
        if method == "GET" and raw_path.startswith("/dq/checks/"):
            return get_check(_check_id_from(event, raw_path))
        if method == "PUT" and raw_path.startswith("/dq/checks/"):
            return upsert_check(_check_id_from(event, raw_path), body)
        if method == "DELETE" and raw_path.startswith("/dq/checks/"):
            cascade = (query.get("cascade") or "").lower() == "true"
            return delete_check(_check_id_from(event, raw_path), cascade)
        return bad_request(f"Unknown route: {method} {raw_path}")
    except Exception as e:
        logger.exception("Unhandled error")
        return server_error(str(e))
