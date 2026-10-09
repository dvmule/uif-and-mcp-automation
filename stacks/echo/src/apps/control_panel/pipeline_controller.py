# pipeline_controller.py
# Lambda function backing the React UI admin panel via API Gateway.
# Handles all pipeline config CRUD, manual job triggers, run history queries,
# and synchronous Oracle table analysis (POST /analyze).
#
# API Gateway routes (HTTP API with Lambda proxy integration):
#   POST   /pipelines                        → create_pipeline
#   GET    /pipelines                        → list_pipelines
#   GET    /pipelines/{pipeline_id}          → get_pipeline
#   PUT    /pipelines/{pipeline_id}          → update_pipeline
#   DELETE /pipelines/{pipeline_id}?cascade=true → delete_pipeline + cascade cleanup
#   PATCH  /pipelines/{pipeline_id}/toggle   → toggle_pipeline (enable/disable)
#   POST   /pipelines/{pipeline_id}/run      → trigger_run
#   GET    /pipelines/{pipeline_id}/runs     → list_runs
#   GET    /pipelines/{pipeline_id}/runs/{run_id} → get_run
#   POST   /analyze                          → analyze_table (synchronous Oracle analysis)

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
import boto3
import decimal
from datetime import datetime, timezone, timedelta, date as _date
from boto3.dynamodb.conditions import Attr, Key

from pipeline_profiles import (
    resolve_glue  as pp_resolve_glue,
    resolve_phase as pp_resolve_phase,
)
import sizing_policy as sp
import schedule_vocabulary
from schedule_vocabulary import HIGH_CADENCE_FREQUENCIES

# DAILY_SLOT_COUNT was defined here as well as in daily_schedule, and the
# dispatcher trusted daily_schedule's copy. One definition now.
from daily_schedule import DAILY_SLOT_COUNT

from echo_resources import echo, echo_legacy
import gold_resources
# The one cadence/start-time authority (UIFPE-149); packaged into this zip
# by lambda.tf so the API and the dispatcher cannot disagree on 'HH:MM'.
from gold_frequencies import (
    DEFAULT_FREQUENCY,
    DEFAULT_WEEKLY_DAYS,
    TRANSFORM_FREQUENCIES,
    WEEKDAYS,
    interval_minutes,
    is_transform_frequency,
    is_weekly,
    normalize,
    parse_time_of_day,
    parse_weekly_days,
)
# The statistics arithmetic, shared with the nightly Glue job (UIFPE-544), so a
# tile on the Overview and a row in daily_stats cannot define a word two ways.
import gold_stats
from bq_partition_ddl import validate_silver_bq_partition

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION                  = os.environ.get("REGION",                  "eu-west-1")
CONFIG_TABLE            = os.environ.get("CONFIG_TABLE",            echo_legacy("glue-job-config"))
METRICS_TABLE           = os.environ.get("METRICS_TABLE",           echo_legacy("glue-job-metrics"))
EXPORT_METRICS_TABLE    = os.environ.get("EXPORT_METRICS_TABLE",    echo_legacy("partner-export-metrics"))
DQ_METRICS_TABLE        = os.environ.get("DQ_METRICS_TABLE",        echo_legacy("dq-check-metrics"))
HOME_WINDOW_HOURS       = int(os.environ.get("HOME_WINDOW_HOURS",   "24"))
GLUE_JOB_NAME           = os.environ.get("GLUE_JOB_NAME",            echo("bronze-ingest"))
SILVER_JOB_NAME         = os.environ.get("SILVER_JOB_NAME",          echo("silver-transform"))
SILVER_JOB_NAME_GCP     = os.environ.get("SILVER_JOB_NAME_GCP",      echo("silver-transform-gcs-iceberg"))
SILVER_JOB_NAME_GCP_BQ  = os.environ.get("SILVER_JOB_NAME_GCP_BQ",   echo("silver-transform-bq-native"))
RECONCILE_JOB_NAME      = os.environ.get("RECONCILE_JOB_NAME",      echo("silver-reconcile"))
RECONCILE_JOB_NAME_GCP  = os.environ.get("RECONCILE_JOB_NAME_GCP",  echo("silver-reconcile-gcs"))
ANALYZE_GLUE_JOB_NAME   = os.environ.get("ANALYZE_GLUE_JOB_NAME",   echo("analyze-table"))
ANALYSIS_TABLE          = os.environ.get("ANALYSIS_TABLE",          echo_legacy("pipeline-analysis"))
# Gold is the one part of this stack that was renamed (ADR-0018), so its names
# come from gold_resources rather than this stack's prefix. Terraform passes
# all three; the fallback resolves through the same authority and yields ""
# rather than guessing an environment, so a misconfigured function fails its
# gold routes loudly instead of reading dev's estate from every environment.
def _gold_fallback(resolve):
    try:
        return resolve()
    except RuntimeError:
        return ""


GOLD_CONFIG_TABLE       = os.environ.get("GOLD_CONFIG_TABLE")       or _gold_fallback(gold_resources.gold_config_table)
GOLD_JOB_NAME           = os.environ.get("GOLD_JOB_NAME")           or _gold_fallback(gold_resources.gold_transform_job)
GOLD_RECONCILE_JOB_NAME = os.environ.get("GOLD_RECONCILE_JOB_NAME") or _gold_fallback(gold_resources.gold_reconcile_job)
GOLD_ARTIFACTS_BUCKET   = os.environ.get("GOLD_ARTIFACTS_BUCKET",   "")
# UIFPE-543: who is emailed when a gold alarm changes state, and the alarms
# themselves. Both live in the gold root; the names resolve the same way.
ALERT_SUBSCRIBERS_TABLE = os.environ.get("ALERT_SUBSCRIBERS_TABLE") or _gold_fallback(gold_resources.gold_alert_subscribers_table)
GOLD_ALARM_PREFIX       = os.environ.get("GOLD_ALARM_PREFIX")       or _gold_fallback(gold_resources.gold_alarm_prefix)
API_URL                = os.environ.get("API_URL",                "")
SECRET_TAG_KEY         = os.environ.get("SECRET_TAG_KEY",         "app")
SECRET_TAG_VAL         = os.environ.get("SECRET_TAG_VAL",         "echo")

dynamo       = boto3.resource("dynamodb", region_name=REGION)
glue         = boto3.client("glue",          region_name=REGION)
glue_catalog = boto3.client("glue",          region_name=REGION)
lakeformation = boto3.client("lakeformation", region_name=REGION)
s3_client    = boto3.client("s3",            region_name=REGION)
cloudwatch   = boto3.client("cloudwatch",    region_name=REGION)
secrets      = boto3.client("secretsmanager", region_name=REGION)

GLUE_ROLE_ARN = os.environ.get("GLUE_ROLE_ARN", "")
ANALYST_ROLE_ARN = os.environ.get("ANALYST_ROLE_ARN", "")

# Oracle Instant Client thick mode — initialised once per cold start.
# The layer places native .so files under /opt/lib.
# Import the oracledb Python module at load time (fast — loads small C extension only).
# The 84 MB libclntsh.so is loaded lazily on the first POST /analyze call via
# _init_oracle_client(), so cold starts for all other routes stay fast.
try:
    import oracledb as _oracledb
    _oracledb_available = True
except ImportError:
    _oracledb_available = False

# google-auth is required for GCP-cloud pipeline registration
# (ensure_bq_dataset). Imported lazily — same try/except pattern as oracledb so
# AWS-only deployments keep working even if the slim controller-bq Lambda
# layer hasn't been attached yet. When the import is missing, ensure_bq_dataset
# logs a warning and returns without raising; the GCP pipeline registration
# still succeeds (the BQ dataset will need to be pre-created out-of-band).
#
# Note: we deliberately do NOT depend on google-cloud-bigquery here. The full
# BQ client library + grpc + cryptography (~40 MB unzipped) would push the
# pipeline_controller Lambda over Lambda's 250 MiB layer ceiling when combined
# with oracle_instant_client (~218 MiB unzipped). Instead we hit the BigQuery
# REST API directly via urllib3 (already in Lambda runtime via boto3), and
# sign JWTs with the pure-Python `rsa` library that google-auth falls back to
# when `cryptography` is absent. See infra/Makefile target
# `build-controller-bq-layer` for the layer build recipe.
try:
    from google.oauth2 import service_account as _gcp_service_account
    _bigquery_available = True
except ImportError:
    _bigquery_available = False

try:
    import urllib3 as _urllib3
except ImportError:
    _urllib3 = None


# Minimal google.auth.transport.Request adapter backed by urllib3. We avoid
# google.auth.transport.urllib3 because that module imports `packaging` at
# load time, which isn't in our slim controller-bq layer. Implementing the
# Request callable directly here keeps the layer tiny (no packaging, no
# certifi, no version probing) and the contract is simple: pass any object
# with a `request(...)` returning .status/.data and `creds.refresh()` works.
class _Urllib3Request:
    def __init__(self, http):
        self._http = http

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **_):
        r = self._http.request(method, url, body=body, headers=headers, timeout=timeout)
        return _Urllib3Response(r)


class _Urllib3Response:
    def __init__(self, r):
        self.status = r.status
        self.data = r.data
        self.headers = dict(r.headers)

_oracle_ready = False
_oracle_error = None


def _init_oracle_client():
    """Load Oracle Instant Client thick-mode libs on first /analyze call."""
    global _oracle_ready, _oracle_error
    if _oracle_ready:
        return
    if not _oracledb_available:
        raise RuntimeError("oracledb not in Lambda layer")
    _oracledb.init_oracle_client(lib_dir="/opt/lib")
    _oracle_ready = True


# ── response helpers ─────────────────────────────────────────────

class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)

def ok(body):
    return {"statusCode": 200, "body": json.dumps(body, cls=_DecimalEncoder)}

def created(body):
    return {"statusCode": 201, "body": json.dumps(body, cls=_DecimalEncoder)}

def bad_request(msg):
    return {"statusCode": 400, "body": json.dumps({"error": msg})}

def unauthorized(msg):
    return {"statusCode": 401, "body": json.dumps({"error": msg})}

def conflict(msg):
    return {"statusCode": 409, "body": json.dumps({"error": msg})}

def not_found(msg):
    return {"statusCode": 404, "body": json.dumps({"error": msg})}

def server_error(msg):
    return {"statusCode": 500, "body": json.dumps({"error": msg})}


class TableNotFoundError(ValueError):
    """Raised when a requested Oracle table does not exist or is not accessible."""
    pass


_SECRET_ARN_RE = re.compile(r"^arn:aws:secretsmanager:[a-z0-9\-]+:\d{12}:secret:.+$")


# ── Destination cloud helpers ────────────────────────────────────

def _dest_cloud(body: dict) -> str:
    """Return normalised destination.cloud — defaults to 'aws' when block absent."""
    return ((body.get("destination") or {}).get("cloud") or "aws").lower()


def _silver_job_name_for(cfg: dict) -> str:
    """Route manual Silver triggers the same way Bronze chains post-extract."""
    cloud = _dest_cloud(cfg)
    if cloud != "gcp":
        return SILVER_JOB_NAME
    write_mode = ((cfg.get("silver") or {}).get("write_mode") or "iceberg_biglake").lower()
    if write_mode == "bq_native":
        return SILVER_JOB_NAME_GCP_BQ
    return SILVER_JOB_NAME_GCP


# ── Metadata helpers (domain inference + auto-init) ─────────────
# Drives the UI filter dropdowns (Cloud / Domain / Source / Tags / Status).
# Prefix → canonical Oracle EBS domain. Keep in sync with
# ui/src/lib/domain.ts; both must produce the same label for a given table.

_DOMAIN_PREFIX_MAP = (
    ("MTL_", "Inventory"),
    ("EGO_", "Catalog"),
    ("BOM_", "Manufacturing"),
    ("AP_",  "Payables"),
    ("AR_",  "Receivables"),
    ("GL_",  "General Ledger"),
    ("PO_",  "Purchasing"),
    ("ONT_", "Order Management"),
    ("OE_",  "Order Management"),
    ("HR_",  "HR"),
)


def _infer_domain(table_name: str | None) -> str:
    """Map an Oracle table name to a canonical domain by leading prefix."""
    if not table_name:
        return "Other"
    # Strip leading schema (e.g. "APPS.MTL_SYSTEM_ITEMS_B" → "MTL_SYSTEM_ITEMS_B")
    bare = table_name.split(".")[-1].upper()
    for prefix, domain in _DOMAIN_PREFIX_MAP:
        if bare.startswith(prefix):
            return domain
    return "Other"


def _apply_metadata_defaults(item: dict) -> dict:
    """Auto-init the metadata block in place so downstream nested writes never
    hit ValidationException, and the UI never sees an undefined filter value.
    Returns the same dict for chaining.

    Defaults:
      - registered_at  → now (ISO-8601 UTC)
      - source_system  → 'Oracle EBS' (only source today)
      - domain         → inferred from extract.table prefix
      - tags           → [] (free-form, populated later via PUT)
    """
    meta = item.get("metadata") or {}
    meta.setdefault("registered_at", datetime.now(timezone.utc).isoformat())
    meta.setdefault("source_system", "Oracle EBS")
    meta.setdefault("domain", _infer_domain((item.get("extract") or {}).get("table")))
    meta.setdefault("tags", [])
    item["metadata"] = meta
    return item


# Metadata sub-fields that the server writes (Glue jobs, toggle handler) and
# the client must NEVER overwrite via a PUT body. On update_pipeline, these
# are always sourced from the existing DynamoDB record; the incoming body's
# values for these keys are discarded. See feedback_api_design.md.
_METADATA_AUDIT_FIELDS = (
    "registered_at",     # set once at POST; preserved across all PUTs
    "last_run_id",       # written by Glue jobs on success/failure
    "last_run_at",       # written by Glue jobs on success/failure
    "last_transform_at", # written by the transform only (UIFPE-543 freshness)
    "last_toggled_at",   # written by /toggle handler
    "deleted_at",        # written by soft-delete handler
)


def _is_soft_deleted(item: dict | None) -> bool:
    if not item:
        return False
    return bool((item.get("metadata") or {}).get("deleted_at"))


def _pipeline_has_in_flight_run(job_name: str, pipeline_id: str) -> bool:
    """True when Glue already has a RUNNING/STARTING/STOPPING run for pipeline_id."""
    in_flight_states = {"RUNNING", "STARTING", "STOPPING", "WAITING"}
    paginator = glue.get_paginator("get_job_runs")
    try:
        for page in paginator.paginate(JobName=job_name, MaxResults=50):
            for run in page.get("JobRuns", []):
                if run.get("JobRunState") not in in_flight_states:
                    continue
                if (run.get("Arguments") or {}).get("--pipeline_id") == pipeline_id:
                    return True
    except Exception as e:
        logger.warning(
            f"[IN-FLIGHT CHECK] job={job_name} pipeline={pipeline_id} error={e}"
        )
    return False


def _merge_metadata_with_existing(body: dict, existing: dict | None) -> None:
    """Mutate `body.metadata` so audit-only fields are sourced from `existing`
    and user-editable fields are sourced from the incoming PUT.

    When existing is None (first-time PUT — rare but possible if create was
    skipped), no-op: the regular _apply_metadata_defaults path will set
    registered_at to now() and leave the audit fields absent.
    """
    if not existing:
        return
    existing_meta = existing.get("metadata") or {}
    incoming_meta = dict(body.get("metadata") or {})  # copy — don't mutate caller's dict
    # Audit fields: existing wins regardless of what the client sent.
    for key in _METADATA_AUDIT_FIELDS:
        if key in existing_meta:
            incoming_meta[key] = existing_meta[key]
    body["metadata"] = incoming_meta


# ── Two-profile (initial vs incremental) validation ──────────────
# Guards against the SPEA9I anti-pattern: heavy initial-load tuning replayed
# on a 30 min incremental schedule. Lenient mode (default) logs warnings but
# allows writes through; strict mode rejects. Toggle via env var
# STRICT_PROFILE_VALIDATION=true after the migration sweep completes.

HEAVY_INCREMENTAL_PREDICATE_THRESHOLD = int(
    os.environ.get("HEAVY_INCREMENTAL_PREDICATE_THRESHOLD", "4")
)
STRICT_PROFILE_VALIDATION = os.environ.get(
    "STRICT_PROFILE_VALIDATION", "false"
).lower() == "true"


def _profile_violations(body: dict) -> list[str]:
    """Return a list of human-readable rule violations for the incremental
    profile. Empty list = config is safe.

    Operator opt-out: set extract.incremental.allow_heavy=true to bypass all
    rules (persisted in DynamoDB — no need to re-pass on every PUT).
    """
    ext = body.get("extract") or {}
    inc = ext.get("incremental") or {}
    g   = body.get("glue") or {}
    g_inc = g.get("incremental") or {}
    sched_freq = ((body.get("schedule") or {}).get("frequency") or "").lower()

    if inc.get("allow_heavy") is True:
        return []

    violations: list[str] = []

    preds = inc.get("jdbc_predicates")
    if isinstance(preds, list) and len(preds) > HEAVY_INCREMENTAL_PREDICATE_THRESHOLD:
        violations.append(
            f"extract.incremental.jdbc_predicates has {len(preds)} entries "
            f"(limit={HEAVY_INCREMENTAL_PREDICATE_THRESHOLD}). Heavy parallelism on "
            f"incremental cadence caused INC-2026-06-08 (120+ Oracle sessions). "
            f"Set jdbc_predicates=null or extract.incremental.allow_heavy=true."
        )

    nw = g_inc.get("num_workers")
    if isinstance(nw, (int, float)) and nw > 4 and sched_freq in HIGH_CADENCE_FREQUENCIES:
        violations.append(
            f"glue.incremental.num_workers={int(nw)} with schedule.frequency={sched_freq} "
            f"multiplies concurrent Oracle sessions. Reduce to <=4 or set "
            f"extract.incremental.allow_heavy=true."
        )

    wt = g_inc.get("worker_type")
    if wt in ("G.4X", "G.8X"):
        violations.append(
            f"glue.incremental.worker_type={wt} is oversized for incremental phase "
            f"(lean default: G.1X). Override with extract.incremental.allow_heavy=true."
        )

    parts = inc.get("jdbc_partitions")
    if isinstance(parts, (int, float)) and parts > 8:
        violations.append(
            f"extract.incremental.jdbc_partitions={int(parts)} on incremental phase "
            f"is heavy. Set to null/<=8 or extract.incremental.allow_heavy=true."
        )

    return violations


def _profile_warnings(body: dict) -> list[str]:
    """Soft, log-only checks regardless of strict mode."""
    g_init = (body.get("glue") or {}).get("initial") or {}
    warns: list[str] = []
    nw_init = g_init.get("num_workers")
    if isinstance(nw_init, (int, float)) and nw_init > 24:
        warns.append(
            f"glue.initial.num_workers={int(nw_init)} is high — verify backfill is "
            f"intentional. Soft warning only."
        )
    return warns


_ORACLE_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")

# The token Bronze renders inside extract.custom_sql. The job's copy is
# bronze_extract_sql.WATERMARK_TOKEN, in a different artifact (the Glue
# --extra-py-files, not this Lambda's zip), so it is repeated here;
# qe/control_panel/test_pipeline_controller.py fails if the two differ.
_WATERMARK_TOKEN = "{{WATERMARK}}"
# Anything that looks like the token but is not exactly it. The job matches
# the token exactly, so `{{ watermark }}` would be read as "no token" — on an
# incremental run that is a full read or a failed run, never what was meant.
_WATERMARK_NEAR_MISS_RE = re.compile(r"\{+\s*watermark\s*\}+", re.IGNORECASE)


def _validate_extract_config(body: dict) -> str | None:
    """Validate extract.watermark_* fields at the API boundary."""
    ext = body.get("extract") or {}
    mode = (ext.get("mode") or "incremental").lower()
    custom = (ext.get("custom_sql") or "").strip()
    wm_col = ext.get("watermark_col")
    wm_col = wm_col.strip() if isinstance(wm_col, str) and wm_col.strip() else None
    wm_fb = ext.get("watermark_fallback_col")
    wm_fb = wm_fb.strip() if isinstance(wm_fb, str) and wm_fb.strip() else None

    if wm_col and not _ORACLE_COL_RE.match(wm_col):
        return f"extract.watermark_col invalid Oracle identifier: {wm_col!r}"
    if wm_fb and not _ORACLE_COL_RE.match(wm_fb):
        return f"extract.watermark_fallback_col invalid Oracle identifier: {wm_fb!r}"

    if custom:
        # Checked in every mode: a full config can be flipped to incremental
        # later without its SQL being looked at again.
        if ";" in custom:
            return ("extract.custom_sql must not contain ';' — the SQL is wrapped "
                    "as (<custom_sql>) t, where a semicolon is ORA-00911")
        near = [m for m in _WATERMARK_NEAR_MISS_RE.findall(custom)
                if m != _WATERMARK_TOKEN]
        if near:
            return (f"extract.custom_sql contains {near[0]!r}; the watermark token "
                    f"is exactly {_WATERMARK_TOKEN} (upper case, two braces, no spaces)")

    if mode == "incremental" and not wm_col:
        return "extract.watermark_col is required when extract.mode=incremental"

    if mode == "incremental" and custom and _WATERMARK_TOKEN not in custom:
        return (f"extract.custom_sql needs the {_WATERMARK_TOKEN} token when "
                f"extract.mode=incremental, where the watermark filter belongs "
                f"(e.g. `... WHERE <your filter> AND {_WATERMARK_TOKEN}`); without "
                f"it every run would read the whole query")

    if wm_fb:
        if mode != "incremental":
            return "extract.watermark_fallback_col is only valid when extract.mode=incremental"
        if not wm_col:
            return "extract.watermark_col is required when extract.watermark_fallback_col is set"
        if wm_fb.upper() == wm_col.upper():
            return "extract.watermark_fallback_col must differ from extract.watermark_col"

    return None


def _validate_schedule_config(body: dict, existing: dict | None = None) -> str | None:
    """Validate schedule.frequency and optional daily_slot.

    Until 2026-09-10 this function did only the second half: it returned None
    on its third line whenever daily_slot was absent, so schedule.frequency was
    never checked on any write path and any string was accepted. That is how a
    live enabled dev pipeline came to hold `"12 hr"` — a payload no EventBridge
    rule sends, matched by the dispatcher with `!=`, so it has never dispatched
    once and the API reported success every time.

    ``existing`` is the stored record on the update path, and enables one
    deliberate leniency: a bad frequency that is being *re-sent unchanged* is
    warned about, not rejected, so editing an unrelated field on an
    already-broken pipeline still works. Creates, and any change of the value,
    are strict. uat and prod configs have not been surveyed, and grandfathering
    cannot break an environment we cannot see; a blanket reject could.
    """
    sched = body.get("schedule") or {}
    freq = sched.get("frequency")

    err = schedule_vocabulary.unknown_frequency_error(freq)
    if err:
        prior = ((existing or {}).get("schedule") or {}).get("frequency")
        if existing is not None and prior == freq:
            logger.warning(
                "[SCHEDULE grandfathered] pipeline=%s schedule.frequency=%r is "
                "not dispatched by any rule and this pipeline does not run; "
                "left as-is because the value is unchanged by this update",
                body.get("pipeline_id"), freq,
            )
        else:
            return err

    raw = sched.get("daily_slot")
    if raw is None:
        return None
    if freq != "daily":
        return "schedule.daily_slot is only valid when schedule.frequency is daily"
    try:
        slot = int(raw)
    except (TypeError, ValueError):
        return "schedule.daily_slot must be an integer"
    if slot < 0 or slot >= DAILY_SLOT_COUNT:
        return f"schedule.daily_slot must be between 0 and {DAILY_SLOT_COUNT - 1}"
    return None


def _ensure_daily_slot(item: dict) -> None:
    """Auto-assign daily_slot for new daily pipelines when omitted."""
    sched = item.setdefault("schedule", {})
    if sched.get("frequency") == "daily" and sched.get("daily_slot") is None:
        pid = item.get("pipeline_id") or ""
        sched["daily_slot"] = abs(hash(pid)) % DAILY_SLOT_COUNT


def _validate_extract_and_glue_profiles(body: dict) -> str:
    """Validate the two-profile config shape.

    Returns an error string when STRICT_PROFILE_VALIDATION is on and rules
    are violated. Otherwise logs warnings and returns None.
    """
    warns = _profile_warnings(body)
    for w in warns:
        logger.warning(f"[PROFILE WARN] pipeline={body.get('pipeline_id')} {w}")

    violations = _profile_violations(body)
    if not violations:
        return None

    summary = "; ".join(violations)
    if STRICT_PROFILE_VALIDATION:
        return summary
    logger.warning(
        f"[PROFILE VIOLATION lenient] pipeline={body.get('pipeline_id')} {summary}"
    )
    return None


# ── Silver config validation ────────────────────────────────────

def _validate_silver_config(body: dict) -> str:
    """Return error string if silver config is invalid, else None.

    Guards against the 2026-05-28 EGO_MTL_SY_ITEMS_EXT_B incident: full-replace
    PUT/POST with `silver.primary_key_cols` missing or empty silently produces a
    Window.partitionBy([]) — global window — in the silver transform, collapsing
    every Bronze row to a single Silver row. Reject at the API boundary.

    AWS-cloud rules only — GCP-cloud pipelines have their silver block shape
    validated by `_validate_destination_config` (gcs_path / bq_project /
    bq_dataset instead of database / s3_path).
    """
    silver = body.get("silver")
    if not silver or not silver.get("enabled"):
        return None
    if _dest_cloud(body) == "gcp":
        return None  # GCP silver fields validated by _validate_destination_config
    required = ("database", "table", "s3_path", "primary_key_cols")
    missing  = [f for f in required if not silver.get(f)]
    if missing:
        return f"silver.{','.join(missing)} required when silver.enabled=true"
    if not isinstance(silver["primary_key_cols"], list) or len(silver["primary_key_cols"]) == 0:
        return "silver.primary_key_cols must be a non-empty list when silver.enabled=true"
    return None


def _validate_replication_config(body: dict) -> str:
    """Return error string if replication.gcs config is invalid, else None.

    `replication` is optional. If omitted or `gcs.enabled` is falsy, pipelines
    behave exactly as before (S3-only Bronze). When enabled, every field needed
    by the replicator Lambda must be present at the API boundary so failures
    surface at registration time, not at run time.
    """
    rep = body.get("replication")
    if not rep:
        return None
    gcs = rep.get("gcs")
    if not gcs or not gcs.get("enabled"):
        return None
    bucket = (gcs.get("bucket") or "").strip()
    if not bucket:
        return "replication.gcs.bucket is required when replication.gcs.enabled=true"
    prefix = gcs.get("prefix")
    if not isinstance(prefix, str) or not prefix or not prefix.endswith("/"):
        return "replication.gcs.prefix must be a non-empty string ending with '/'"
    secret_arn = gcs.get("credentials_secret_arn") or ""
    if not _SECRET_ARN_RE.match(secret_arn):
        return "replication.gcs.credentials_secret_arn must be a valid Secrets Manager ARN"
    return None


_BQ_LOCATIONS = {"US", "EU", "us-east1", "us-east4", "us-east5", "us-west1",
                 "us-west2", "us-west3", "us-west4", "us-central1",
                 "europe-west1", "europe-west2", "europe-west3", "europe-west4",
                 "europe-west6", "europe-west9", "europe-north1",
                 "asia-east1", "asia-east2", "asia-northeast1",
                 "asia-southeast1", "asia-south1", "australia-southeast1"}


def _validate_destination_config(body: dict) -> str:
    """Return error string if `destination` block is invalid, else None.

    `destination.cloud` selects between the AWS-native flow (S3 Bronze + S3
    Iceberg Silver via Glue Catalog) and the GCP-native flow (GCS Bronze +
    GCS-Iceberg Silver via BigQuery metastore). Default is 'aws' to keep
    pre-existing pipelines behaving unchanged.

    For 'gcp':
      - destination.gcp_credentials_secret_arn required (Secrets Manager ARN
        for the GCP service-account JSON the Glue jobs use to authenticate)
      - target.gcs_path required (gs:// URI); target.s3_path must be absent
      - silver block, if enabled, must carry gcs_path / bq_project / bq_dataset
      - replication.gcs / replication.silver_bq must NOT be enabled (no
        self-replication: GCP is already the destination, not a mirror)
    """
    dest = body.get("destination") or {}
    cloud = (dest.get("cloud") or "aws").lower()
    if cloud not in ("aws", "gcp"):
        return f"destination.cloud must be 'aws' or 'gcp', got '{cloud}'"
    if cloud == "aws":
        return None  # AWS path validated by _validate_silver_config etc.

    # GCP-cloud validation
    secret_arn = dest.get("gcp_credentials_secret_arn") or ""
    if not _SECRET_ARN_RE.match(secret_arn):
        return ("destination.gcp_credentials_secret_arn must be a valid "
                "Secrets Manager ARN when destination.cloud='gcp'")

    target = body.get("target") or {}
    gcs_path = target.get("gcs_path")
    if not gcs_path or not isinstance(gcs_path, str) or not gcs_path.startswith("gs://"):
        return ("target.gcs_path is required and must start with 'gs://' "
                "when destination.cloud='gcp'")
    if target.get("s3_path"):
        return ("target.s3_path must be absent when destination.cloud='gcp' "
                "(use target.gcs_path instead)")

    silver = body.get("silver") or {}
    if silver.get("enabled"):
        write_mode = (silver.get("write_mode") or "iceberg_biglake").lower()
        if write_mode not in ("iceberg_biglake", "bq_native"):
            return (f"silver.write_mode '{write_mode}' is invalid — "
                    "expected 'iceberg_biglake' (default) or 'bq_native'")

        if write_mode == "iceberg_biglake":
            # Existing behaviour: Iceberg snapshots on GCS, BigLake external table.
            sgcs = silver.get("gcs_path")
            if not sgcs or not isinstance(sgcs, str) or not sgcs.startswith("gs://"):
                return ("silver.gcs_path is required and must start with 'gs://' "
                        "when silver.enabled=true and destination.cloud='gcp' "
                        "and silver.write_mode='iceberg_biglake'")
        else:
            # bq_native: BQ-managed native table, no GCS warehouse for Silver.
            if silver.get("gcs_path"):
                return ("silver.gcs_path must be absent when "
                        "silver.write_mode='bq_native' (Silver lives in BQ "
                        "storage, not GCS)")
            # Bronze BigLake external table over Bronze GCS Parquet lives in a
            # separate BQ dataset alongside target.gcs_path. Required so the
            # Bronze tier surface in BQ stays separable from Silver for
            # IAM/governance — and to prevent the original setup-pipeline
            # mistake where silver.bq_dataset got copied across both tiers.
            bronze_dataset = (target.get("bq_dataset") or "").strip()
            if not bronze_dataset:
                return ("target.bq_dataset is required when "
                        "silver.write_mode='bq_native' — names the BQ dataset "
                        "where the BigLake external table over Bronze GCS "
                        "Parquet lives. Must differ from silver.bq_dataset.")
            if bronze_dataset == (silver.get("bq_dataset") or "").strip():
                return ("target.bq_dataset must differ from silver.bq_dataset — "
                        "Bronze external table and Silver managed table belong "
                        "in different datasets for tier separation.")

        if not (silver.get("bq_project") or "").strip():
            return "silver.bq_project is required when silver.enabled=true and destination.cloud='gcp'"
        if not (silver.get("bq_dataset") or "").strip():
            return "silver.bq_dataset is required when silver.enabled=true and destination.cloud='gcp'"
        loc = (silver.get("bq_location") or "").strip()
        if loc and loc not in _BQ_LOCATIONS:
            return f"silver.bq_location '{loc}' not in allowed set"
        if not silver.get("table"):
            return "silver.table is required when silver.enabled=true"
        pks = silver.get("primary_key_cols")
        if not isinstance(pks, list) or len(pks) == 0:
            return "silver.primary_key_cols must be a non-empty list when silver.enabled=true"
        if silver.get("s3_path"):
            return ("silver.s3_path must be absent when destination.cloud='gcp' "
                    "(use silver.gcs_path instead)")
        # bq_partition_field, if present, must be a non-empty string or
        # explicit null (null disables partitioning for small lookups).
        part_field = silver.get("bq_partition_field", "_silver_updated_at")
        if part_field is not None and (
            not isinstance(part_field, str) or not part_field.strip()
        ):
            return ("silver.bq_partition_field must be a non-empty string or "
                    "null (got empty/invalid value)")
        part_err = validate_silver_bq_partition(silver)
        if part_err:
            return part_err

    rep = body.get("replication") or {}
    if (rep.get("gcs") or {}).get("enabled"):
        return ("replication.gcs.enabled must be false when destination.cloud='gcp' "
                "(GCP is already the primary destination — no self-replication)")
    if (rep.get("silver_bq") or {}).get("enabled"):
        return ("replication.silver_bq.enabled must be false when destination.cloud='gcp' "
                "(GCP is already the primary destination — no self-replication)")
    return None


def _validate_silver_bq_config(body: dict) -> str:
    """Return error string if replication.silver_bq config is invalid, else None.

    Only relevant when both `silver.enabled=true` and `replication.silver_bq.enabled=true`.
    Mirrors the GCS-block guarantee: every field the silver_bq_replicator Lambda
    needs is present at the API boundary so a misconfigured pipeline can't reach
    runtime.
    """
    rep = body.get("replication")
    if not rep:
        return None
    bq = rep.get("silver_bq")
    if not bq or not bq.get("enabled"):
        return None
    silver = body.get("silver") or {}
    if not silver.get("enabled"):
        return "replication.silver_bq.enabled=true requires silver.enabled=true"

    bucket = (bq.get("bucket") or "").strip()
    if not bucket:
        return "replication.silver_bq.bucket is required when enabled=true"
    prefix = bq.get("prefix")
    if not isinstance(prefix, str) or not prefix or not prefix.endswith("/"):
        return "replication.silver_bq.prefix must be a non-empty string ending with '/'"
    secret_arn = bq.get("credentials_secret_arn") or ""
    if not _SECRET_ARN_RE.match(secret_arn):
        return "replication.silver_bq.credentials_secret_arn must be a valid Secrets Manager ARN"
    if not (bq.get("bq_project") or "").strip():
        return "replication.silver_bq.bq_project is required"
    if not (bq.get("bq_dataset") or "").strip():
        return "replication.silver_bq.bq_dataset is required"
    location = (bq.get("bq_location") or "").strip()
    if location and location not in _BQ_LOCATIONS:
        return f"replication.silver_bq.bq_location '{location}' not in allowed set"
    return None


# ── Silver database auto-setup ──────────────────────────────────

def ensure_silver_database(database_name: str) -> None:
    """Create Glue database + Lake Formation grants if they don't exist."""
    try:
        glue_catalog.get_database(Name=database_name)
        return  # already exists
    except glue_catalog.exceptions.EntityNotFoundException:
        pass
    except Exception as e:
        logger.warning(f"[SILVER] get_database failed: {e}")
        return

    try:
        glue_catalog.create_database(DatabaseInput={"Name": database_name})
        logger.info(f"[SILVER] created database: {database_name}")
    except Exception as e:
        logger.warning(f"[SILVER] create_database failed: {e}")
        return

    if not GLUE_ROLE_ARN:
        logger.warning("[SILVER] GLUE_ROLE_ARN not set, skipping LF grants")
        return

    try:
        # Grant Glue execution role database-level permissions
        lakeformation.grant_permissions(
            Principal={"DataLakePrincipalIdentifier": GLUE_ROLE_ARN},
            Resource={"Database": {"Name": database_name}},
            Permissions=["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"],
        )
        logger.info(f"[SILVER] granted Glue role db permissions on {database_name}")

        # Grant Glue execution role table-level permissions (wildcard)
        lakeformation.grant_permissions(
            Principal={"DataLakePrincipalIdentifier": GLUE_ROLE_ARN},
            Resource={"Table": {"DatabaseName": database_name, "TableWildcard": {}}},
            Permissions=["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"],
        )
        logger.info(f"[SILVER] granted Glue role table permissions on {database_name}.*")

        # Grant analyst role (PCMPowerUser) if configured
        if ANALYST_ROLE_ARN:
            lakeformation.grant_permissions(
                Principal={"DataLakePrincipalIdentifier": ANALYST_ROLE_ARN},
                Resource={"Database": {"Name": database_name}},
                Permissions=["DESCRIBE"],
            )
            lakeformation.grant_permissions(
                Principal={"DataLakePrincipalIdentifier": ANALYST_ROLE_ARN},
                Resource={"Table": {"DatabaseName": database_name, "TableWildcard": {}}},
                Permissions=["SELECT", "DESCRIBE"],
            )
            logger.info(f"[SILVER] granted analyst role permissions on {database_name}")
    except Exception as e:
        logger.warning(f"[SILVER] LF grant_permissions failed: {e}")


def ensure_bq_dataset(bq_project: str, bq_dataset: str, bq_location: str,
                      secret_arn: str) -> None:
    """Auto-create the BigQuery dataset for GCP-cloud Silver pipelines.

    Sibling of `ensure_silver_database` (which auto-creates the Glue database +
    Lake Formation grants for AWS pipelines). Same contract: idempotent (no-op
    if the dataset exists), failures are logged but do NOT fail the pipeline
    registration request — pipelines can be registered before the BQ side is
    ready, and the first Silver run will surface any lingering BQ misconfig
    via the BigLake DDL step in generic_silver_transform_gcp_iceberg.py.

    `bq_location` should be a BigQuery location code (US, EU, europe-west1…).
    Default to "EU" at the call-site if absent, since the Pearson dev project
    is EU-resident.
    """
    if not _bigquery_available or _urllib3 is None:
        logger.warning(
            "[BQ] google-auth/urllib3 not available in this Lambda layer — "
            f"cannot auto-create dataset {bq_project}.{bq_dataset}. "
            "Pre-create the dataset out-of-band before the first Silver run."
        )
        return

    if not secret_arn:
        logger.warning(
            f"[BQ] ensure_bq_dataset called without secret_arn — skipping "
            f"auto-create for {bq_project}.{bq_dataset}"
        )
        return

    try:
        secret_str = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
        sa_info = json.loads(secret_str)
        creds = _gcp_service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/bigquery"]
        )
    except Exception as e:
        logger.warning(f"[BQ] failed to load GCP credentials from {secret_arn}: {e}")
        return

    try:
        http = _urllib3.PoolManager()
        creds.refresh(_Urllib3Request(http))
        auth_headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        }
    except Exception as e:
        logger.warning(f"[BQ] failed to mint GCP access token: {e}")
        return

    fqdn = f"{bq_project}.{bq_dataset}"
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{bq_project}/datasets"

    try:
        r = http.request("GET", f"{base}/{bq_dataset}", headers=auth_headers)
        if 200 <= r.status < 300:
            logger.info(f"[BQ] dataset already exists: {fqdn}")
            return
        if r.status != 404:
            logger.warning(
                f"[BQ] get_dataset {fqdn} returned status={r.status}: "
                f"{r.data[:300].decode('utf-8', errors='replace')}"
            )
    except Exception as e:
        logger.warning(f"[BQ] get_dataset network error for {fqdn}: {e}")
        # Fall through and try create anyway — the create call will surface
        # any persistent network/permission issue.

    body = json.dumps({
        "datasetReference": {"projectId": bq_project, "datasetId": bq_dataset},
        "location": bq_location or "EU",
    }).encode("utf-8")
    try:
        r = http.request("POST", base, headers=auth_headers, body=body)
        if 200 <= r.status < 300:
            logger.info(f"[BQ] created dataset: {fqdn} (location={bq_location or 'EU'})")
        elif r.status == 409:
            # Conflict = already exists (race between get + create). Idempotent.
            logger.info(f"[BQ] dataset already exists (409 on create): {fqdn}")
        else:
            # Log + continue. The pipeline row is already written; either the BQ
            # dataset will be created on first run via the same DDL helper, or
            # the operator will need to create it manually.
            logger.warning(
                f"[BQ] create_dataset failed for {fqdn} status={r.status}: "
                f"{r.data[:300].decode('utf-8', errors='replace')}"
            )
    except Exception as e:
        logger.warning(f"[BQ] create_dataset network error for {fqdn}: {e}")


# ── Pipeline cleanup (cascade delete) ────────────────────────────

def cleanup_pipeline(pipeline_cfg: dict) -> dict:
    """Delete pipeline + associated Glue DB, S3 data, and LF grants. Requires explicit cascade=true.

    Bronze replication targets (GCS) are intentionally NOT purged — cross-cloud
    data is owned by the downstream consumer team. Surfaces a note in the result
    so the UI can warn the operator that GCS objects remain.
    """
    pipeline_id = pipeline_cfg.get("pipeline_id", "unknown")
    results = {"pipeline_id": pipeline_id, "deleted": [], "notes": []}

    # GCP-destination pipelines have no AWS-side data to clean up (Bronze in GCS,
    # Silver in BigQuery metastore). The DynamoDB config row is the only AWS-side
    # artifact. GCS/BQ data is consumer-owned and intentionally left intact —
    # mirrors how `replication.silver_bq` cleanup behaves today.
    dest_cloud = _dest_cloud(pipeline_cfg)
    if dest_cloud == "gcp":
        target = pipeline_cfg.get("target") or {}
        silver = pipeline_cfg.get("silver") or {}
        if target.get("gcs_path"):
            results["notes"].append(
                f"Bronze GCS data {target['gcs_path']} left intact (consumer-owned)"
            )
        if silver.get("enabled"):
            bq_ref = f"{silver.get('bq_project','')}.{silver.get('bq_dataset','')}.{(silver.get('table') or '').lower()}"
            results["notes"].append(
                f"Silver BigQuery table `{bq_ref}` and GCS warehouse "
                f"{silver.get('gcs_path','')} left intact (consumer-owned)"
            )
        try:
            dynamo.Table(CONFIG_TABLE).delete_item(
                Key={"pipeline_id": pipeline_id, "version": "latest"}
            )
            results["deleted"].append("dynamodb:config")
            logger.info(f"[CLEANUP] deleted DynamoDB config: {pipeline_id} (cloud=gcp)")
        except Exception as e:
            logger.warning(f"[CLEANUP] failed to delete DynamoDB config: {e}")
            results["deleted"].append("dynamodb:config:ERROR")
        return results

    # 1. Delete Silver Glue database if it exists (optional - may fail if Lake Formation blocks it)
    # Since the table is now orphaned (no pipeline pointing to it), leaving the empty database
    # behind is acceptable. Deletion may fail due to LF permissions, which is fine.
    silver_cfg = pipeline_cfg.get("silver")
    if silver_cfg and silver_cfg.get("enabled"):
        db_name = silver_cfg.get("database")
        if db_name:
            try:
                glue_catalog.delete_database(Name=db_name, CatalogId=None)
                results["deleted"].append(f"glue_database:{db_name}")
                logger.info(f"[CLEANUP] deleted Glue database: {db_name}")
            except glue_catalog.exceptions.EntityNotFoundException:
                logger.info(f"[CLEANUP] Glue database not found: {db_name}")
            except Exception as e:
                logger.info(f"[CLEANUP] Glue database deletion skipped (may be blocked by Lake Formation): {e}")
                results["deleted"].append(f"glue_database:{db_name}:skipped_lf")

        # 2. Delete Silver S3 data
        s3_path = silver_cfg.get("s3_path")
        if s3_path:
            try:
                bucket, prefix = _parse_s3_path(s3_path)
                _delete_s3_prefix(bucket, prefix)
                results["deleted"].append(f"s3:{s3_path}")
                logger.info(f"[CLEANUP] deleted Silver S3 data: {s3_path}")
            except Exception as e:
                logger.warning(f"[CLEANUP] failed to delete Silver S3 data {s3_path}: {e}")
                results["deleted"].append(f"s3:{s3_path}:ERROR")

    # 3. Delete Bronze S3 data
    target_cfg = pipeline_cfg.get("target", {})
    bronze_s3_path = target_cfg.get("s3_path")
    if bronze_s3_path:
        try:
            bucket, prefix = _parse_s3_path(bronze_s3_path)
            _delete_s3_prefix(bucket, prefix)
            results["deleted"].append(f"s3:{bronze_s3_path}")
            logger.info(f"[CLEANUP] deleted Bronze S3 data: {bronze_s3_path}")
        except Exception as e:
            logger.warning(f"[CLEANUP] failed to delete Bronze S3 data {bronze_s3_path}: {e}")
            results["deleted"].append(f"s3:{bronze_s3_path}:ERROR")

    # 4. Note GCS replication target (intentionally left intact)
    gcs_cfg = (pipeline_cfg.get("replication") or {}).get("gcs") or {}
    if gcs_cfg.get("enabled"):
        gcs_dst = f"gs://{gcs_cfg.get('bucket', '')}/{gcs_cfg.get('prefix', '')}"
        note = f"GCS replication target {gcs_dst} left intact (cross-cloud data owned by consumer team)"
        results["notes"].append(note)
        logger.info(f"[CLEANUP] {note}")

    # 4b. Note Silver→BQ replication targets (BQ external table + GCS prefix
    # both intentionally left intact — BQ table drop requires BQ API access we
    # don't grant to this Lambda, and the GCS data follows the same
    # consumer-owned policy as the Bronze GCS prefix above).
    bq_cfg = (pipeline_cfg.get("replication") or {}).get("silver_bq") or {}
    if bq_cfg.get("enabled"):
        bq_proj    = bq_cfg.get("bq_project", "")
        bq_ds      = bq_cfg.get("bq_dataset", "")
        bq_tbl     = (bq_cfg.get("bq_table") or
                      ((pipeline_cfg.get("silver") or {}).get("table") or "")).lower()
        gcs_dst    = f"gs://{bq_cfg.get('bucket', '')}/{bq_cfg.get('prefix', '')}"
        note_bq    = (f"BigQuery external table `{bq_proj}.{bq_ds}.{bq_tbl}` left intact "
                       f"(drop manually with `bq rm -f {bq_proj}:{bq_ds}.{bq_tbl}`)")
        note_gcs   = f"Silver→BQ GCS mirror {gcs_dst} left intact (consumer team owns this prefix)"
        results["notes"].append(note_bq)
        results["notes"].append(note_gcs)
        logger.info(f"[CLEANUP] {note_bq}")
        logger.info(f"[CLEANUP] {note_gcs}")

    # 5. Delete from DynamoDB config table
    try:
        dynamo.Table(CONFIG_TABLE).delete_item(
            Key={"pipeline_id": pipeline_id, "version": "latest"}
        )
        results["deleted"].append("dynamodb:config")
        logger.info(f"[CLEANUP] deleted DynamoDB config: {pipeline_id}")
    except Exception as e:
        logger.warning(f"[CLEANUP] failed to delete DynamoDB config: {e}")
        results["deleted"].append("dynamodb:config:ERROR")

    return results


def _parse_s3_path(s3_path: str) -> tuple:
    """Parse s3://bucket/prefix to (bucket, prefix)."""
    if not s3_path.startswith("s3://"):
        raise ValueError(f"Invalid S3 path: {s3_path}")
    path_parts = s3_path[5:].split("/", 1)
    bucket = path_parts[0]
    prefix = path_parts[1] if len(path_parts) > 1 else ""
    return bucket, prefix


def _delete_s3_prefix(bucket: str, prefix: str) -> None:
    """Delete all objects under an S3 prefix."""
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    delete_objects = []
    for page in pages:
        if "Contents" not in page:
            continue
        for obj in page["Contents"]:
            delete_objects.append({"Key": obj["Key"]})

    if delete_objects:
        s3_client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": delete_objects}
        )
        logger.info(f"[CLEANUP] deleted {len(delete_objects)} objects from s3://{bucket}/{prefix}")


# ── route handlers ───────────────────────────────────────────────

def create_pipeline(body: dict):
    pipeline_id = body.get("pipeline_id")
    if not pipeline_id:
        return bad_request("pipeline_id is required")

    # _validate_destination_config first — its result gates how the AWS-cloud
    # validators interpret the rest of the body (gcp pipelines short-circuit
    # silver/replication checks because their shape differs).
    err = _validate_destination_config(body)
    if err:
        return bad_request(err)
    err = _validate_silver_config(body)
    if err:
        return bad_request(err)
    err = _validate_replication_config(body)
    if err:
        return bad_request(err)
    err = _validate_silver_bq_config(body)
    if err:
        return bad_request(err)
    err = _validate_extract_config(body)
    if err:
        return bad_request(err)
    err = _validate_extract_and_glue_profiles(body)
    if err:
        return bad_request(err)
    err = _validate_schedule_config(body)
    if err:
        return bad_request(err)

    item = _apply_metadata_defaults({**body, "version": "latest"})
    _ensure_daily_slot(item)
    dynamo.Table(CONFIG_TABLE).put_item(Item=item)
    logger.info(f"[CREATE] pipeline_id={pipeline_id}")

    # Auto-create Silver database if Silver is enabled for this pipeline.
    # AWS pipelines: Glue database + Lake Formation grants.
    # GCP pipelines: BigQuery dataset (idempotent get-or-create). Requires the
    # google-cloud-bigquery library on the Lambda layer; if missing,
    # ensure_bq_dataset degrades to a logged warning and registration still
    # succeeds (operator pre-creates the dataset).
    silver_cfg = body.get("silver")
    if silver_cfg and silver_cfg.get("enabled"):
        cloud = _dest_cloud(body)
        if cloud == "aws":
            db_name = silver_cfg.get("database")
            if db_name:
                ensure_silver_database(db_name)
        elif cloud == "gcp":
            gcp_secret_arn = (body.get("destination") or {}).get("gcp_credentials_secret_arn")
            ensure_bq_dataset(
                silver_cfg["bq_project"],
                silver_cfg["bq_dataset"],
                silver_cfg.get("bq_location") or "EU",
                gcp_secret_arn,
            )

    return created({"pipeline_id": pipeline_id, "version": "latest"})


def list_pipelines():
    result = dynamo.Table(CONFIG_TABLE).scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("version").eq("latest")
    )
    items = [
        _apply_metadata_defaults(it)
        for it in result.get("Items", [])
        if not _is_soft_deleted(it)
    ]
    return ok({"pipelines": items, "count": len(items)})


def get_pipeline(pipeline_id: str):
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    if "Item" not in resp or _is_soft_deleted(resp["Item"]):
        return not_found(f"Pipeline '{pipeline_id}' not found")
    return ok(_apply_metadata_defaults(resp["Item"]))


def update_pipeline(pipeline_id: str, body: dict):
    err = _validate_destination_config(body)
    if err:
        return bad_request(err)
    err = _validate_silver_config(body)
    if err:
        return bad_request(err)
    err = _validate_replication_config(body)
    if err:
        return bad_request(err)
    err = _validate_silver_bq_config(body)
    if err:
        return bad_request(err)
    err = _validate_extract_config(body)
    if err:
        return bad_request(err)
    err = _validate_extract_and_glue_profiles(body)
    if err:
        return bad_request(err)
    # Read the stored record BEFORE validating. Two callers need it: the
    # metadata merge below, and _validate_schedule_config, which grandfathers a
    # bad-but-unchanged schedule.frequency rather than blocking an unrelated
    # field edit. One get_item serves both — this is a reorder, not a new read.
    existing = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    ).get("Item")

    err = _validate_schedule_config(body, existing)
    if err:
        return bad_request(err)

    # Merge metadata audit fields from the existing record BEFORE put_item.
    # PUT is a full-record replace, so a body that omits the metadata block
    # would otherwise wipe registered_at + last_run_* + last_toggled_at. We
    # source those four fields from the DB and let the incoming body win on
    # user-editable fields (owner, tags, domain, source_system).
    _merge_metadata_with_existing(body, existing)

    # Auto-init any still-missing metadata defaults (registered_at if this is
    # a first-time PUT with no prior record; source_system, domain, tags).
    item = _apply_metadata_defaults(
        {**body, "pipeline_id": pipeline_id, "version": "latest"}
    )
    _ensure_daily_slot(item)
    dynamo.Table(CONFIG_TABLE).put_item(Item=item)
    logger.info(f"[UPDATE] pipeline_id={pipeline_id}")

    # Mirror the create_pipeline auto-init block (L728-742). PUT is also the
    # entry point for first-time GCP smoke registrations (the original
    # create_pipeline-only call meant first-PUT silently skipped BQ dataset
    # auto-create). Both ensure_* helpers are idempotent — safe on every PUT.
    silver_cfg = body.get("silver")
    if silver_cfg and silver_cfg.get("enabled"):
        cloud = _dest_cloud(body)
        if cloud == "aws":
            db_name = silver_cfg.get("database")
            if db_name:
                ensure_silver_database(db_name)
        elif cloud == "gcp":
            gcp_secret_arn = (body.get("destination") or {}).get("gcp_credentials_secret_arn")
            ensure_bq_dataset(
                silver_cfg["bq_project"],
                silver_cfg["bq_dataset"],
                silver_cfg.get("bq_location") or "EU",
                gcp_secret_arn,
            )

    return ok({"pipeline_id": pipeline_id, "updated": True})


def delete_pipeline(pipeline_id: str, cascade: bool = False):
    """Soft-delete by default (hide from UI, retain config for restore).

    cascade=true performs hard delete + infrastructure cleanup (admin only).
    """
    cfg_resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    if "Item" not in cfg_resp:
        return not_found(f"Pipeline '{pipeline_id}' not found")

    pipeline_cfg = cfg_resp["Item"]
    if _is_soft_deleted(pipeline_cfg) and not cascade:
        return ok({"pipeline_id": pipeline_id, "deleted": True, "soft": True})

    if cascade:
        cleanup_result = cleanup_pipeline(pipeline_cfg)
        logger.info(f"[DELETE] pipeline_id={pipeline_id} cascade=true result: {cleanup_result}")
        return ok(cleanup_result)

    merged_meta = dict(pipeline_cfg.get("metadata") or {})
    merged_meta["deleted_at"] = datetime.now(timezone.utc).isoformat()
    dynamo.Table(CONFIG_TABLE).update_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"},
        UpdateExpression="SET enabled = :e, #meta = :meta",
        ExpressionAttributeNames={"#meta": "metadata"},
        ExpressionAttributeValues={
            ":e":    False,
            ":meta": merged_meta,
        },
    )
    logger.info(f"[SOFT DELETE] pipeline_id={pipeline_id}")
    return ok({
        "pipeline_id": pipeline_id,
        "deleted":     True,
        "soft":        True,
        "deleted_at":  merged_meta["deleted_at"],
    })


def toggle_pipeline(pipeline_id: str, body: dict):
    enabled = body.get("enabled")
    if enabled is None:
        return bad_request("Body must contain 'enabled' (true or false)")

    # Older configs may lack a `metadata` map, so nested SET on
    # metadata.last_toggled_at fails with ValidationException. Read the
    # existing metadata, merge, and write the map as a whole.
    existing = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    if "Item" not in existing:
        return not_found(f"Pipeline '{pipeline_id}' not found")
    if _is_soft_deleted(existing["Item"]):
        return not_found(f"Pipeline '{pipeline_id}' not found")

    merged_meta = dict(existing["Item"].get("metadata") or {})
    merged_meta["last_toggled_at"] = datetime.now(timezone.utc).isoformat()
    merged_meta.setdefault(
        "registered_at",
        existing["Item"].get("created_at")
        or datetime.now(timezone.utc).isoformat(),
    )

    dynamo.Table(CONFIG_TABLE).update_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"},
        UpdateExpression="SET enabled = :e, #meta = :meta",
        ExpressionAttributeNames={"#meta": "metadata"},
        ExpressionAttributeValues={
            ":e":    bool(enabled),
            ":meta": merged_meta,
        },
    )
    action = "enabled" if enabled else "disabled"
    logger.info(f"[TOGGLE] pipeline_id={pipeline_id} {action}")
    return ok({"pipeline_id": pipeline_id, "enabled": bool(enabled)})


def trigger_run(pipeline_id: str, body: dict, layer: str = "bronze"):
    """Start a Glue job for the given pipeline.

    layer="bronze"    → eds-{env}-bronze-ingest (the default, used by the
                        list page's Run button and scheduled triggers).
    layer="silver"    → eds-{env}-silver-transform (AWS) or GCP sibling jobs,
                        routed via destination.cloud + silver.write_mode.
    layer="reconcile" → eds-{env}-silver-reconcile (AWS) or
                        eds-{env}-silver-reconcile-gcp (GCP), routed via
                        destination.cloud on the config. Used by the
                        Reconciliation page's manual trigger button.
    """
    cfg_resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    if "Item" not in cfg_resp:
        return not_found(f"Pipeline '{pipeline_id}' not found")

    cfg     = cfg_resp["Item"]
    if _is_soft_deleted(cfg):
        return not_found(f"Pipeline '{pipeline_id}' not found")
    if not cfg.get("enabled", True):
        return bad_request(f"Pipeline '{pipeline_id}' is disabled")

    if layer == "bronze" and _pipeline_has_in_flight_run(GLUE_JOB_NAME, pipeline_id):
        return conflict(
            f"Pipeline '{pipeline_id}' already has an in-flight Bronze run on {GLUE_JOB_NAME}"
        )

    silver_cfg = cfg.get("silver") or {}
    if layer == "silver":
        if not silver_cfg.get("enabled"):
            return bad_request(f"Pipeline '{pipeline_id}' does not have Silver enabled")
        silver_job = _silver_job_name_for(cfg)
        if _pipeline_has_in_flight_run(silver_job, pipeline_id):
            return conflict(
                f"Pipeline '{pipeline_id}' already has an in-flight Silver run on {silver_job}"
            )

    phase   = pp_resolve_phase(cfg)
    g_phase = pp_resolve_glue(cfg, phase)

    if layer == "reconcile":
        cloud = _dest_cloud(cfg)
        job_name = RECONCILE_JOB_NAME_GCP if cloud == "gcp" else RECONCILE_JOB_NAME
    elif layer == "silver":
        job_name = _silver_job_name_for(cfg)
    elif layer == "bronze":
        job_name = GLUE_JOB_NAME
    else:
        return bad_request(f"Unsupported layer '{layer}' — expected 'bronze', 'silver', or 'reconcile'")

    worker_type = body.get("worker_type", g_phase.get("worker_type", "G.1X"))
    num_workers = int(body.get("num_workers", g_phase.get("num_workers", 2)))
    timeout_min = int(g_phase.get("timeout_minutes", 60))

    response = glue.start_job_run(
        JobName=job_name,
        Arguments={
            "--pipeline_id": pipeline_id,
            "--env":         os.environ.get("ENV", os.environ.get("ENVIRONMENT", "dev")),
        },
        WorkerType=worker_type,
        NumberOfWorkers=num_workers,
        Timeout=timeout_min,
    )
    run_id = response["JobRunId"]
    logger.info(
        f"[MANUAL TRIGGER] pipeline_id={pipeline_id} layer={layer} "
        f"job={job_name} run_id={run_id} phase={phase} "
        f"workers={num_workers}/{worker_type}"
    )
    return ok({
        "pipeline_id": pipeline_id,
        "run_id":      run_id,
        "layer":       layer,
        "job_name":    job_name,
        "phase":       phase,
        "started_at":  datetime.now(timezone.utc).isoformat(),
    })


_IN_FLIGHT_GLUE_STATES = frozenset({"RUNNING", "STARTING", "STOPPING", "WAITING"})

# How far back the in-flight walk reads (UIFPE-565). A run cannot still be in
# flight once it has outlived its Glue timeout, and the largest timeout in this
# estate is 240 minutes, so anything that started more than a day ago is
# finished whatever its state field says. Glue returns runs newest first: the
# walk stops at the first page whose NEWEST run predates the horizon. Without
# the bound every call paged the job's whole history - 13 pages and ~12 s for
# bronze-ingest, once per pipeline row, every 30 s.
IN_FLIGHT_HORIZON_MINUTES = 24 * 60


def _in_flight_glue_runs(job_name: str, entity_id, *,
                         arg_key: str = "--pipeline_id",
                         id_field: str = "pipeline_id") -> list:
    """Glue runs not yet in metrics — same source as get_run's fallback.

    The layers name a run with different Glue arguments: bronze and silver with
    --pipeline_id, gold with --gold_id. entity_id None means every run on the job.
    Bounded to IN_FLIGHT_HORIZON_MINUTES of history (see above).
    """
    runs = []
    horizon = datetime.now(timezone.utc) - timedelta(minutes=IN_FLIGHT_HORIZON_MINUTES)
    paginator = glue.get_paginator("get_job_runs")
    try:
        for page in paginator.paginate(JobName=job_name, MaxResults=100):
            page_runs = page.get("JobRuns", [])
            for run in page_runs:
                if run.get("JobRunState") not in _IN_FLIGHT_GLUE_STATES:
                    continue
                run_entity = (run.get("Arguments") or {}).get(arg_key)
                if entity_id is not None and run_entity != entity_id:
                    continue
                runs.append({
                    "run_id":        run["Id"],
                    id_field:        run_entity or entity_id,
                    "job_name":      job_name,
                    "status":        run["JobRunState"],
                    "started_at":    str(run.get("StartedOn", "")),
                    "completed_at":  str(run.get("CompletedOn", "")),
                    "duration_sec":  run.get("ExecutionTime", 0),
                    "error_message": run.get("ErrorMessage"),
                })
            # Stop once even the newest run on this page is older than the
            # horizon. A run with no StartedOn (a WAITING one) never stops the
            # walk: it is recent by definition.
            starts = [r.get("StartedOn") for r in page_runs if r.get("StartedOn")]
            if page_runs and starts and max(starts) < horizon:
                break
    except Exception as e:
        logger.warning(
            f"[in-flight] Glue lookup failed entity={entity_id} job={job_name}: {e}"
        )
    return runs


def list_runs(pipeline_id: str):
    resp = dynamo.Table(METRICS_TABLE).query(
        IndexName="pipeline_id-index",
        KeyConditionExpression=Key("pipeline_id").eq(pipeline_id),
        ScanIndexForward=False,
        Limit=20,
    )
    metrics_runs = resp.get("Items", [])
    known_ids = {r.get("run_id") for r in metrics_runs if r.get("run_id")}

    in_flight = [
        r for r in _in_flight_glue_runs(GLUE_JOB_NAME, pipeline_id)
        if r["run_id"] not in known_ids
    ]
    # In-flight runs are always newer than completed metrics rows.
    return ok({"pipeline_id": pipeline_id, "runs": in_flight + metrics_runs})


def get_run(pipeline_id: str, run_id: str, job_name: str = None):
    """One run of this pipeline, whichever layer produced it (UIFPE-505).

    A run id does not identify a metrics partition: job_name IS the partition
    key, and one pipeline's runs span several jobs — bronze ingest, the silver
    transforms, silver reconcile and the replicators. So the completed-run
    lookup goes through pipeline_id-index, which every one of those rows
    carries, instead of a GetItem pinned to the bronze job.

    job_name is an optional hint. It short-circuits to an exact GetItem, and it
    is the only way to serve the in-flight Glue fallback below, which needs a
    JobName that no API can infer from a run id alone. list_runs returns
    job_name on every row, so a caller that has a run already has it.
    """
    table = dynamo.Table(METRICS_TABLE)

    if job_name:
        resp = table.get_item(Key={"job_name": job_name, "run_id": run_id})
        if "Item" in resp:
            return ok(resp["Item"])
    else:
        # run_id is not a key on the index, so it has to be filtered within the
        # pipeline's own partition. Limit is applied BEFORE FilterExpression,
        # so paginate rather than capping.
        start_key = None
        while True:
            kwargs = {
                "IndexName": "pipeline_id-index",
                "KeyConditionExpression": Key("pipeline_id").eq(pipeline_id),
                "FilterExpression": Attr("run_id").eq(run_id),
                "ScanIndexForward": False,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            resp = table.query(**kwargs)
            items = resp.get("Items", [])
            if items:
                return ok(items[0])
            start_key = resp.get("LastEvaluatedKey")
            if not start_key:
                break

    # In-progress run: not yet written to metrics table — query Glue directly
    try:
        glue_resp = glue.get_job_run(JobName=job_name or GLUE_JOB_NAME, RunId=run_id)
        run = glue_resp["JobRun"]
        return ok({
            "run_id":         run_id,
            "pipeline_id":    pipeline_id,
            "status":         run["JobRunState"],
            "started_at":     str(run.get("StartedOn", "")),
            "completed_at":   str(run.get("CompletedOn", "")),
            "duration_sec":   run.get("ExecutionTime", 0),
            "error_message":  run.get("ErrorMessage"),
        })
    except Exception as e:
        return not_found(f"Run '{run_id}' not found: {e}")


# ── Partition sizing (cost-aware policy in sizing_policy.py) ─────

def _two_profile_recommendation(rec: dict) -> dict:
    """Derive the {initial, incremental} profile view from a flat sizing rec.

    Initial profile mirrors today's analyze recommendation (fat — worker_type,
    num_workers, predicates, partition columns). Incremental profile is the
    lean default (G.1X / 1 worker / no predicates) regardless of table size,
    because incremental runs only touch the watermark delta. See ADR-tbd
    on two-profile config.
    """
    initial = {
        "worker_type":        rec.get("worker_type", "G.1X"),
        "num_workers":        rec.get("num_workers", 2),
        "timeout_minutes":    240,
        "jdbc_partition_col": rec.get("jdbc_partition_col"),
        "jdbc_lower_bound":   rec.get("jdbc_lower_bound"),
        "jdbc_upper_bound":   rec.get("jdbc_upper_bound"),
        "jdbc_partitions":    rec.get("jdbc_partitions"),
        "jdbc_predicates":    rec.get("jdbc_predicates"),
    }
    incremental = {
        "worker_type":        "G.1X",
        "num_workers":        2,
        "timeout_minutes":    60,
        "jdbc_partition_col": None,
        "jdbc_lower_bound":   None,
        "jdbc_upper_bound":   None,
        "jdbc_partitions":    None,
        "jdbc_predicates":    None,
    }
    return {"initial": initial, "incremental": incremental}


def _build_apply_command(pipeline_id, rec):
    """Emit a copy-pasteable shell command that writes the analyze
    recommendation into the new two-profile config shape.

    Output writes both extract.initial / extract.incremental and
    glue.initial / glue.incremental in one PUT. Source-contract keys
    (table, mode, watermark_*) at extract.* root are preserved by jq.
    """
    url      = f"{API_URL}/pipelines/{pipeline_id}"
    profiles = _two_profile_recommendation(rec)
    init_g   = {k: profiles["initial"][k] for k in ("worker_type", "num_workers", "timeout_minutes")}
    inc_g    = profiles["incremental"]

    # Initial extract block — only non-null JDBC knobs to keep the payload tidy
    init_ext = {k: v for k, v in profiles["initial"].items()
                if k not in ("worker_type", "num_workers", "timeout_minutes") and v is not None}
    inc_ext  = {
        "jdbc_partition_col": None,
        "jdbc_lower_bound":   None,
        "jdbc_upper_bound":   None,
        "jdbc_partitions":    None,
        "jdbc_predicates":    None,
    }

    strategy = rec.get("partition_strategy")
    has_partition = strategy == "numeric_range" and rec.get("jdbc_partition_col")
    has_preds     = bool(rec.get("jdbc_predicates"))

    if not (has_partition or has_preds):
        return "No viable partition column found — manual config required."

    new_extract_initial = json.dumps(init_ext)
    new_extract_incremental = json.dumps(inc_ext)
    new_glue_initial = json.dumps({
        "worker_type":     init_g["worker_type"],
        "num_workers":     init_g["num_workers"],
        "timeout_minutes": 240,
    })
    new_glue_incremental = json.dumps(inc_g)

    if has_preds:
        preds_json = json.dumps(rec["jdbc_predicates"])
        # Save predicates to disk because Bash + jq + a large list of strings
        # is a quoting hazard. Stable: a temp file beats a 200kb argv literal.
        return (
            f"# Step 1 — save predicates:\n"
            f"python3 -c \"import json; open('/tmp/predicates.json','w').write(json.dumps({preds_json}))\"\n\n"
            f"# Step 2 — apply via API (two-profile shape):\n"
            f"CURRENT=$(curl -s {url}) && \\\n"
            f"echo $CURRENT | jq \\\n"
            f"  --argjson preds \"$(cat /tmp/predicates.json)\" \\\n"
            f"  --argjson glue_init {json.dumps(new_glue_initial)} \\\n"
            f"  --argjson glue_inc  {json.dumps(new_glue_incremental)} \\\n"
            f"  --argjson ext_inc   {json.dumps(new_extract_incremental)} \\\n"
            f"  '.glue.initial     = ($glue_init | fromjson) |\n"
            f"   .glue.incremental = ($glue_inc  | fromjson) |\n"
            f"   .extract.initial.jdbc_predicates    = $preds |\n"
            f"   .extract.initial.jdbc_partition_col = null   |\n"
            f"   .extract.initial.jdbc_lower_bound   = null   |\n"
            f"   .extract.initial.jdbc_upper_bound   = null   |\n"
            f"   .extract.initial.jdbc_partitions    = null   |\n"
            f"   .extract.incremental = ($ext_inc | fromjson) |\n"
            f"   del(.glue.worker_type, .glue.num_workers, .glue.timeout_minutes,\n"
            f"       .extract.jdbc_partition_col, .extract.jdbc_lower_bound,\n"
            f"       .extract.jdbc_upper_bound, .extract.jdbc_partitions,\n"
            f"       .extract.jdbc_predicates)\n"
            f"  ' | curl -s -X PUT {url} -H 'Content-Type: application/json' -d @-"
        )

    col, lo, hi, parts = (rec["jdbc_partition_col"], rec["jdbc_lower_bound"],
                           rec["jdbc_upper_bound"], rec["jdbc_partitions"])
    return (
        f"CURRENT=$(curl -s {url}) && \\\n"
        f"echo $CURRENT | jq \\\n"
        f"  --argjson glue_init {json.dumps(new_glue_initial)} \\\n"
        f"  --argjson glue_inc  {json.dumps(new_glue_incremental)} \\\n"
        f"  --argjson ext_inc   {json.dumps(new_extract_incremental)} \\\n"
        f"  '.glue.initial     = ($glue_init | fromjson) |\n"
        f"   .glue.incremental = ($glue_inc  | fromjson) |\n"
        f"   .extract.initial.jdbc_partition_col = \"{col}\" |\n"
        f"   .extract.initial.jdbc_lower_bound   = {lo}   |\n"
        f"   .extract.initial.jdbc_upper_bound   = {hi}   |\n"
        f"   .extract.initial.jdbc_partitions    = {parts}|\n"
        f"   .extract.initial.jdbc_predicates    = null   |\n"
        f"   .extract.incremental = ($ext_inc | fromjson) |\n"
        f"   del(.glue.worker_type, .glue.num_workers, .glue.timeout_minutes,\n"
        f"       .extract.jdbc_partition_col, .extract.jdbc_lower_bound,\n"
        f"       .extract.jdbc_upper_bound, .extract.jdbc_partitions,\n"
        f"       .extract.jdbc_predicates)\n"
        f"  ' | curl -s -X PUT {url} -H 'Content-Type: application/json' -d @-"
    )


# ── Oracle analysis (synchronous) ────────────────────────────────

def _resolve_credentials(secret_arn: str):
    raw   = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    creds = json.loads(raw)
    dsn   = creds.get("dsn") or "{host}:{port}/{service}".format(**creds)
    return creds["username"], creds["password"], dsn


def _oracle_query(conn, sql: str) -> list:
    with conn.cursor() as cur:
        cur.execute(sql)
        col_names = [d[0] for d in cur.description]
        return [dict(zip(col_names, row)) for row in cur.fetchall()]


def _fetch_constraint_cols(conn, schema, table_name, owner=None):
    owner = owner or schema
    try:
        rows = _oracle_query(conn, f"""
            SELECT c.CONSTRAINT_TYPE, cc.COLUMN_NAME, cc.POSITION
            FROM   ALL_CONSTRAINTS c
            JOIN   ALL_CONS_COLUMNS cc
                ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
            WHERE  c.OWNER = '{owner}' AND c.TABLE_NAME = '{table_name}'
              AND  c.CONSTRAINT_TYPE IN ('P', 'U')
            ORDER BY c.CONSTRAINT_TYPE, cc.POSITION
        """)
        pk_cols     = [r["COLUMN_NAME"] for r in rows if r["CONSTRAINT_TYPE"] == "P"]
        unique_cols = [r["COLUMN_NAME"] for r in rows if r["CONSTRAINT_TYPE"] == "U"]
    except Exception as e:
        logger.warning(f"[ANALYZE] constraint query skipped (role-restricted?): {e}")
        pk_cols = unique_cols = []
    logger.info(f"[ANALYZE] pk_cols={pk_cols}")
    return {"pk_cols": pk_cols, "unique_cols": unique_cols}


def _resolve_actual_owner(conn, schema, table_name):
    """Resolve actual table owner — handles Oracle EBS synonyms (e.g., APPS → INV)."""
    try:
        rows = _oracle_query(conn, f"""
            SELECT TABLE_OWNER FROM ALL_SYNONYMS
            WHERE SYNONYM_NAME = '{table_name}'
              AND (OWNER = '{schema}' OR OWNER = 'PUBLIC')
              AND ROWNUM = 1
        """)
        if rows and rows[0].get("TABLE_OWNER"):
            actual = rows[0]["TABLE_OWNER"]
            logger.info(f"[ANALYZE] synonym {schema}.{table_name} → actual owner: {actual}")
            return actual
    except Exception as e:
        logger.warning(f"[ANALYZE] synonym resolution failed: {e}")
    return schema


def _fetch_date_cols(conn, schema, table_name, owner=None):
    owner = owner or schema
    try:
        rows = _oracle_query(conn, f"""
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
            FROM   ALL_TAB_COLUMNS
            WHERE  OWNER = '{owner}' AND TABLE_NAME = '{table_name}'
              AND  DATA_TYPE IN ('DATE', 'TIMESTAMP', 'TIMESTAMP(6)')
            ORDER BY COLUMN_ID
        """)
        result = [{"column": r["COLUMN_NAME"], "data_type": r["DATA_TYPE"],
                   "nullable": r.get("NULLABLE", "Y")} for r in rows]
    except Exception as e:
        logger.warning(f"[ANALYZE] date_cols query skipped (role-restricted?): {e}")
        result = []
    logger.info(f"[ANALYZE] date_cols={[d['column'] for d in result]}")
    return result


def _probe_date_bounds(conn, schema, table_name, date_col):
    try:
        b = _oracle_query(conn,
            f"SELECT MIN({date_col}) AS LO, MAX({date_col}) AS HI "
            f"FROM {schema}.{table_name}")
        if not b or b[0].get("LO") is None:
            return None
        def _to_date(v):
            if v is None: return None
            if isinstance(v, datetime): return v.date()
            if isinstance(v, _date): return v
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try: return datetime.strptime(str(v), fmt).date()
                except ValueError: continue
            return None
        min_d, max_d = _to_date(b[0]["LO"]), _to_date(b[0]["HI"])
        if min_d and max_d:
            logger.info(f"[ANALYZE] date_bounds {date_col}: {min_d} – {max_d}")
            return {"min_date": min_d, "max_date": max_d}
    except Exception as e:
        logger.warning(f"[ANALYZE] date bounds probe failed for {date_col}: {e}")
    return None


def _fetch_all_columns(conn, schema, table_name, owner=None):
    """Return all columns for the UI wizard schema review."""
    owner = owner or schema
    try:
        rows = _oracle_query(conn, f"""
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
            FROM   ALL_TAB_COLUMNS
            WHERE  OWNER = '{owner}' AND TABLE_NAME = '{table_name}'
            ORDER BY COLUMN_ID
        """)
        return [
            {"name": r["COLUMN_NAME"], "data_type": r["DATA_TYPE"],
             "nullable": r.get("NULLABLE", "Y") == "Y"}
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[ANALYZE] column fetch failed: {e}")
        return []


def _fetch_numeric_candidates(conn, schema, table_name, no_stats, owner=None):
    owner = owner or schema
    if not no_stats:
        col_rows = _oracle_query(conn, f"""
            SELECT s.COLUMN_NAME, s.NUM_NULLS, s.NUM_DISTINCT,
                   UTL_RAW.CAST_TO_NUMBER(s.LOW_VALUE)  AS LOW_VAL,
                   UTL_RAW.CAST_TO_NUMBER(s.HIGH_VALUE) AS HIGH_VAL
            FROM USER_TAB_COL_STATISTICS s
            JOIN USER_TAB_COLUMNS c
                ON c.TABLE_NAME = s.TABLE_NAME AND c.COLUMN_NAME = s.COLUMN_NAME
            WHERE s.TABLE_NAME = '{table_name}'
              AND c.DATA_TYPE = 'NUMBER' AND c.NULLABLE = 'N'
              AND s.LOW_VALUE IS NOT NULL AND s.HIGH_VALUE IS NOT NULL
            ORDER BY s.NUM_NULLS ASC, s.NUM_DISTINCT DESC NULLS LAST
        """)
        if not col_rows:
            col_rows = _oracle_query(conn, f"""
                SELECT s.COLUMN_NAME, s.NUM_NULLS, s.NUM_DISTINCT,
                       UTL_RAW.CAST_TO_NUMBER(s.LOW_VALUE)  AS LOW_VAL,
                       UTL_RAW.CAST_TO_NUMBER(s.HIGH_VALUE) AS HIGH_VAL
                FROM ALL_TAB_COL_STATISTICS s
                JOIN ALL_TAB_COLUMNS c
                    ON c.OWNER = s.OWNER AND c.TABLE_NAME = s.TABLE_NAME
                   AND c.COLUMN_NAME = s.COLUMN_NAME
                WHERE s.OWNER = '{owner}' AND s.TABLE_NAME = '{table_name}'
                  AND c.DATA_TYPE = 'NUMBER' AND c.NULLABLE = 'N'
                  AND s.LOW_VALUE IS NOT NULL AND s.HIGH_VALUE IS NOT NULL
                ORDER BY s.NUM_NULLS ASC, s.NUM_DISTINCT DESC NULLS LAST
            """)
        return [{"column": r["COLUMN_NAME"], "min": int(r["LOW_VAL"]),
                 "max": int(r["HIGH_VAL"]), "nulls": int(r.get("NUM_NULLS") or 0),
                 "distinct": int(r.get("NUM_DISTINCT") or 0)}
                for r in col_rows if r.get("LOW_VAL") is not None]

    col_meta = _oracle_query(conn, f"""
        SELECT COLUMN_NAME, NULLABLE FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = '{table_name}' AND DATA_TYPE = 'NUMBER' ORDER BY COLUMN_ID
    """)
    if not col_meta:
        col_meta = _oracle_query(conn, f"""
            SELECT COLUMN_NAME, NULLABLE FROM ALL_TAB_COLUMNS
            WHERE OWNER = '{schema}' AND TABLE_NAME = '{table_name}'
              AND DATA_TYPE = 'NUMBER' ORDER BY COLUMN_ID
        """)

    def _score(r):
        return (2 if r["COLUMN_NAME"].endswith("_ID") else 0) + \
               (1 if r.get("NULLABLE") == "N" else 0)

    for col_row in sorted(col_meta, key=_score, reverse=True)[:3]:
        best_col = col_row["COLUMN_NAME"]
        try:
            logger.info(f"[ANALYZE] MIN/MAX probe: {best_col}")
            b = _oracle_query(conn,
                f"SELECT MIN({best_col}) AS LO, MAX({best_col}) AS HI "
                f"FROM {schema}.{table_name}")
            if b and b[0].get("LO") is not None:
                return [{"column": best_col, "min": int(b[0]["LO"]),
                         "max": int(b[0]["HI"]), "nulls": 0, "distinct": 0}]
        except Exception as e:
            logger.warning(f"[ANALYZE] MIN/MAX failed for {best_col}: {e}")
    return []


def _analyze_oracle_table(secret_arn: str, table: str,
                           partition_col: str = None,
                           date_col: str = None,
                           hash_col: str = None,
                           id_col: str = None,
                           date_start: str = None,
                           date_end: str = None,
                           hash_buckets: int = 8,
                           pipeline_id: str = None) -> dict:
    started_at = datetime.now(timezone.utc)

    parts = table.split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Table must be SCHEMA.TABLE, got: {table}")
    schema, table_name = parts[0], parts[1]
    for val in (schema, table_name):
        if not all(c.isalnum() or c == "_" for c in val):
            raise ValueError(f"Invalid identifier: {val}")

    logger.info("[ANALYZE] step=resolve_credentials")
    user, password, dsn = _resolve_credentials(secret_arn)
    logger.info("[ANALYZE] step=oracle_connect dsn_prefix=%s", dsn[:30])
    conn = _oracledb.connect(user=user, password=password, dsn=dsn)
    logger.info("[ANALYZE] step=connected")
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD\"T\"HH24:MI:SS'")
            cur.execute("ALTER SESSION SET TIME_ZONE='UTC'")

        # 1. Table-level stats
        table_rows = _oracle_query(conn, f"""
            SELECT NUM_ROWS, AVG_ROW_LEN,
                   TO_CHAR(LAST_ANALYZED, 'YYYY-MM-DD') AS LAST_ANALYZED
            FROM   USER_TABLES WHERE TABLE_NAME = '{table_name}'
        """)
        if not table_rows:
            table_rows = _oracle_query(conn, f"""
                SELECT NUM_ROWS, AVG_ROW_LEN,
                       TO_CHAR(LAST_ANALYZED, 'YYYY-MM-DD') AS LAST_ANALYZED
                FROM   ALL_TABLES
                WHERE  OWNER = '{schema}' AND TABLE_NAME = '{table_name}'
            """)

        if table_rows:
            r               = table_rows[0]
            num_rows        = r.get("NUM_ROWS")
            avg_row_len     = r.get("AVG_ROW_LEN")
            last_analyzed_s = r.get("LAST_ANALYZED")
            last_analyzed   = (datetime.strptime(last_analyzed_s, "%Y-%m-%d")
                               if last_analyzed_s else None)
        else:
            num_rows = avg_row_len = last_analyzed = None

        no_stats       = num_rows is None
        num_rows       = int(num_rows or 0)
        avg_row_len    = int(avg_row_len or 500)
        stats_age_days = (datetime.now() - last_analyzed).days if last_analyzed else None
        logger.info(f"[ANALYZE] rows={num_rows} avg_row_bytes={avg_row_len} "
                    f"stats_age_days={stats_age_days} no_stats={no_stats}")

        stats = {"num_rows": num_rows, "avg_row_len": avg_row_len,
                 "stats_age_days": stats_age_days, "no_stats": no_stats}
        total_gb = sp.stats_total_gb(stats)

        # 1a. Resolve actual owner (handles Oracle EBS synonyms)
        actual_owner = _resolve_actual_owner(conn, schema, table_name)

        # 2. All columns (for UI wizard schema review)
        all_columns = _fetch_all_columns(conn, schema, table_name, owner=actual_owner)
        logger.info(f"[ANALYZE] all_columns={len(all_columns)}")

        if not all_columns:
            raise TableNotFoundError(
                f"Table {schema}.{table_name} not found — verify the schema prefix "
                f"and confirm the Oracle user has SELECT access to ALL_TAB_COLUMNS."
            )

        # 3. Constraint + date col detection (gracefully handles role-restricted views)
        has_hints = bool(partition_col or date_col)
        if has_hints:
            pk_info, date_cols_detected = {"pk_cols": [], "unique_cols": []}, []
        else:
            pk_info            = _fetch_constraint_cols(conn, schema, table_name, owner=actual_owner)
            date_cols_detected = _fetch_date_cols(conn, schema, table_name, owner=actual_owner)

        if date_col and not date_cols_detected:
            date_cols_detected = [{"column": date_col, "data_type": "DATE", "nullable": "N"}]

        # 3. Numeric candidates
        if date_col and id_col:
            candidates = []
            try:
                b = _oracle_query(conn, f"SELECT MIN({id_col}) AS LO, MAX({id_col}) AS HI "
                                        f"FROM {schema}.{table_name}")
                if b and b[0].get("LO") is not None:
                    candidates = [{"column": id_col, "min": int(b[0]["LO"]),
                                   "max": int(b[0]["HI"]), "nulls": 0, "distinct": 0}]
            except Exception as e:
                logger.warning(f"[ANALYZE] id_col MIN/MAX failed: {e}")
        elif partition_col:
            candidates = []
            try:
                b = _oracle_query(conn, f"SELECT MIN({partition_col}) AS LO, MAX({partition_col}) AS HI "
                                        f"FROM {schema}.{table_name}")
                if b and b[0].get("LO") is not None:
                    candidates = [{"column": partition_col, "min": int(b[0]["LO"]),
                                   "max": int(b[0]["HI"]), "nulls": 0, "distinct": 0}]
            except Exception as e:
                logger.warning(f"[ANALYZE] partition_col MIN/MAX failed: {e}")
        else:
            candidates = _fetch_numeric_candidates(conn, schema, table_name, no_stats, owner=actual_owner)

        logger.info(f"[ANALYZE] {len(candidates)} partition candidates")

        # 4. Strategy selection + caller overrides
        strategy_cfg = sp.choose_partition_strategy(
            partition_col, pk_info, date_cols_detected, candidates, total_gb=total_gb,
        )
        if hash_col and strategy_cfg["strategy"] == "date_hash":
            strategy_cfg = {**strategy_cfg, "hash_col": hash_col}
        if date_col and hash_col and not id_col and strategy_cfg["strategy"] != "date_hash":
            strategy_cfg = {**strategy_cfg, "strategy": "date_hash",
                            "date_col": date_col, "hash_col": hash_col,
                            "id_col": None, "numeric_col": None}
        if date_col and id_col and strategy_cfg["strategy"] != "date_id_range":
            strategy_cfg = {**strategy_cfg, "strategy": "date_id_range",
                            "date_col": date_col, "id_col": id_col, "numeric_col": None}

        # Extra probe if PK column not yet in candidates
        numeric_col = strategy_cfg.get("numeric_col")
        if (strategy_cfg["strategy"] == "numeric_range" and numeric_col
                and not any(c["column"] == numeric_col for c in candidates)):
            try:
                b = _oracle_query(conn, f"SELECT MIN({numeric_col}) AS LO, MAX({numeric_col}) AS HI "
                                        f"FROM {schema}.{table_name}")
                if b and b[0].get("LO") is not None:
                    candidates = [{"column": numeric_col, "min": int(b[0]["LO"]),
                                   "max": int(b[0]["HI"]), "nulls": 0, "distinct": 0}]
            except Exception as e:
                logger.warning(f"[ANALYZE] extra probe failed for {numeric_col}: {e}")

        # 5. Date bounds
        date_bounds = None
        if strategy_cfg["strategy"] != "numeric_range" and strategy_cfg.get("date_col"):
            if date_start or date_end:
                d_start = (datetime.strptime(date_start, "%Y-%m-%d").date()
                           if date_start else _date(2000, 1, 1))
                d_end   = (datetime.strptime(date_end,   "%Y-%m-%d").date()
                           if date_end else _date.today())
                date_bounds = {"min_date": d_start, "max_date": d_end}
            else:
                date_bounds = _probe_date_bounds(conn, schema, table_name, strategy_cfg["date_col"])

        # 6. Sizing + predicates
        sizing     = sp.compute_partition_sizing(stats, strategy_cfg, date_bounds, hash_buckets)
        predicates = []
        _id_col    = strategy_cfg.get("id_col")
        id_candidate = None
        if strategy_cfg["strategy"] != "numeric_range" and date_bounds:
            id_candidate = next((c for c in candidates if c["column"] == _id_col), None)

            if id_candidate is None and _id_col and strategy_cfg["strategy"] == "date_id_range":
                try:
                    logger.info(f"[ANALYZE] ID bounds not found, probing live for {_id_col}")
                    b = _oracle_query(conn, f"SELECT MIN({_id_col}) AS LO, MAX({_id_col}) AS HI "
                                            f"FROM {schema}.{table_name}")
                    if b and b[0].get("LO") is not None:
                        id_candidate = {"column": _id_col, "min": int(b[0]["LO"]),
                                       "max": int(b[0]["HI"]), "nulls": 0, "distinct": 0}
                        logger.info(f"[ANALYZE] ID bounds {_id_col}: {id_candidate['min']} – {id_candidate['max']} (live probe)")
                except Exception as e:
                    logger.warning(f"[ANALYZE] ID bounds live probe failed: {e}")

            effective_buckets = sizing.get("effective_hash_buckets") or hash_buckets
            predicates = sp.generate_predicates(
                strategy     = strategy_cfg["strategy"],
                date_col     = strategy_cfg["date_col"],
                date_bounds  = date_bounds,
                total_gb     = sizing["total_gb"],
                id_col       = _id_col,
                id_min       = id_candidate["min"] if id_candidate else None,
                id_max       = id_candidate["max"] if id_candidate else None,
                id_splits    = sizing["id_splits"] or 4,
                hash_col     = strategy_cfg.get("hash_col"),
                hash_buckets = effective_buckets,
            )

        # 7. Best numeric candidate for numeric_range output
        best = None
        if strategy_cfg["strategy"] == "numeric_range" and candidates:
            target = strategy_cfg.get("numeric_col")
            best   = next((c for c in candidates if c["column"] == target), None) \
                     or max(candidates, key=sp.score_numeric_candidate)

        strategy = strategy_cfg["strategy"]
        rec = {
            "partition_strategy":    strategy,
            "worker_type":           sizing["worker_type"],
            "num_workers":           sizing["num_workers"],
            "jdbc_partitions":       sizing["jdbc_partitions"],
            "jdbc_partition_col":    best["column"] if best else None,
            "jdbc_lower_bound":      best["min"]    if best else None,
            "jdbc_upper_bound":      best["max"]    if best else None,
            "jdbc_predicates":       predicates if predicates else None,
            "jdbc_predicates_count": len(predicates) if predicates else None,
            "partition_date_col":    strategy_cfg.get("date_col"),
            "partition_id_col":      strategy_cfg.get("id_col"),
            "partition_hash_col":    strategy_cfg.get("hash_col"),
            "date_min":              str(date_bounds["min_date"]) if date_bounds else None,
            "date_max":              str(date_bounds["max_date"]) if date_bounds else None,
            "id_min":                id_candidate["min"] if id_candidate and strategy_cfg.get("id_col") else None,
            "id_max":                id_candidate["max"] if id_candidate and strategy_cfg.get("id_col") else None,
            "total_gb":              round(sizing["total_gb"], 2),
        }
        warning = None
        if no_stats:
            warning = (f"No Oracle stats — sizing estimates unavailable. "
                       f"Run: exec DBMS_STATS.gather_table_stats('{schema}', '{table_name}');")
        elif stats_age_days is not None and stats_age_days > 7:
            warning = f"Oracle stats are {stats_age_days} days old."
        if warning:
            rec["warning"] = warning
            if id_candidate:
                rec["warning"] += " (bounds from live MIN/MAX probe)"

        sp.enrich_recommendations(rec, sizing, date_bounds)
        rec["profiles"] = _two_profile_recommendation(rec)

        pid = pipeline_id or f"pearson-product-{table_name}"
        best_candidate = next(
            (c for c in candidates if c["column"] == rec.get("jdbc_partition_col")), None
        )
        return {
            # UI wizard fields
            "table":         table,
            "row_count":     num_rows,
            "columns":       all_columns,
            "min_pk":        best_candidate["min"] if best_candidate else None,
            "max_pk":        best_candidate["max"] if best_candidate else None,
            # Legacy / CLI fields
            "table_name":           table,
            "analyzed_at":          started_at.isoformat(),
            "status":               "COMPLETED",
            "stats": {
                "row_count":      num_rows,
                "avg_row_bytes":  avg_row_len,
                "total_size_gb":  round(sizing["total_gb"], 2),
                "stats_age_days": stats_age_days,
            },
            "pk_cols":              pk_info.get("pk_cols", []),
            "date_cols":            [d["column"] for d in date_cols_detected],
            "partition_candidates": candidates,
            "recommendations":      rec,
            "apply_command":        _build_apply_command(pid, rec),
        }
    finally:
        conn.close()


def trigger_analysis(body: dict):
    """
    POST /analyze
    Body: {
      "secret_arn":    "...",
      "table":         "SCHEMA.TABLE",
      "pipeline_id":   "...",          # optional — used in apply_command
      "partition_col": "COL",          # optional — force numeric_range
      "date_col":      "COL",          # optional — force date-based strategy
      "hash_col":      "COL",          # optional — ORA_HASH column
      "id_col":        "COL",          # optional — numeric ID for date_id_range
      "date_start":    "YYYY-MM-DD",   # optional — skip MIN probe
      "date_end":      "YYYY-MM-DD",   # optional — skip MAX probe
      "hash_buckets":  8               # optional — default 8
    }
    """
    secret_arn    = body.get("secret_arn")
    table         = body.get("table")
    if not secret_arn or not table:
        return bad_request("Body must contain 'secret_arn' and 'table'")

    def _upper(v):
        return v.upper() if v else None

    logger.info("[ANALYZE] step=init_oracle_client")
    try:
        _init_oracle_client()
    except Exception as e:
        return server_error(f"Oracle client init failed: {e}")

    logger.info("[ANALYZE] step=analyze table=%s", table)
    try:
        result = _analyze_oracle_table(
            secret_arn    = secret_arn,
            table         = table.upper(),
            partition_col = _upper(body.get("partition_col")),
            date_col      = _upper(body.get("date_col")),
            hash_col      = _upper(body.get("hash_col")),
            id_col        = _upper(body.get("id_col")),
            date_start    = body.get("date_start"),
            date_end      = body.get("date_end"),
            hash_buckets  = int(body.get("hash_buckets", 8)),
            pipeline_id   = body.get("pipeline_id"),
        )
        logger.info(f"[ANALYZE] table={table} status=COMPLETED "
                    f"strategy={result['recommendations']['partition_strategy']}")
        return ok(result)
    except TableNotFoundError as e:
        logger.warning(f"[ANALYZE] table not found: {table}")
        return not_found(str(e))
    except Exception as e:
        logger.exception(f"Oracle analysis failed for {table}")
        return server_error(str(e))


# ── Connections (Secrets Manager tag discovery) ──────────────────

def list_connections():
    """
    GET /connections
    Returns all Secrets Manager secrets tagged app=echo.
    """
    paginator = secrets.get_paginator("list_secrets")
    results = []
    for page in paginator.paginate(
        Filters=[{"Key": "tag-key", "Values": [SECRET_TAG_KEY]},
                 {"Key": "tag-value", "Values": [SECRET_TAG_VAL]}]
    ):
        for s in page.get("SecretList", []):
            results.append({
                "name":        s.get("Name"),
                "arn":         s.get("ARN"),
                "description": s.get("Description", ""),
            })
    logger.info(f"[CONNECTIONS] found={len(results)}")
    return ok(results)


def list_gcs_connections():
    """
    GET /connections/gcs
    Returns Secrets Manager secrets tagged purpose=gcs-replication — the tag
    that scripts/upload_gcp_secret.py applies to GCP service-account JSON
    secrets so the wizard can populate a dropdown without typing ARNs.
    """
    return ok(_list_purpose_tagged_secrets("gcs-replication"))


def list_silver_bq_connections():
    """
    GET /connections/silver-bq
    Returns Secrets Manager secrets tagged purpose=silver-bq-replication OR
    purpose=gcs-replication. The OR is deliberate: a single GCP SA can serve
    both Bronze→GCS and Silver→BQ when granted the additional BigQuery roles
    (dataEditor + jobUser). AWS tag keys are unique per resource so the SA
    can't carry both purpose values — including gcs-replication secrets here
    lets operators reuse a single SA without tag gymnastics.
    """
    return ok(_list_purpose_tagged_secrets(["silver-bq-replication", "gcs-replication"]))


def _list_purpose_tagged_secrets(purpose) -> list:
    """Return secrets tagged purpose=<value> for any value in `purpose`.

    Accepts a string (single value) or a list of strings (OR-match). Secrets
    Manager's tag-value filter does OR semantics within a single Values list,
    so the multi-value form is one ListSecrets call, not N.
    """
    values = [purpose] if isinstance(purpose, str) else list(purpose)
    paginator = secrets.get_paginator("list_secrets")
    results: list = []
    for page in paginator.paginate(
        Filters=[{"Key": "tag-key", "Values": ["purpose"]},
                 {"Key": "tag-value", "Values": values}]
    ):
        for s in page.get("SecretList", []):
            results.append({
                "name":        s.get("Name"),
                "arn":         s.get("ARN"),
                "description": s.get("Description", ""),
            })
    logger.info(f"[CONNECTIONS] purpose={values} found={len(results)}")
    return results


# ── Gold notifications (UIFPE-543) ───────────────────────────────────
#
# Who is emailed when a gold alarm changes state is a row in the gold root's
# alert-subscribers table, self-service from the console: a business user
# subscribes THEMSELVES to one table from its detail page. A row without
# `gold_ids` hears everything, fleet alarms included (the operator row); a row
# with `gold_ids` hears only those tables. The notifier Lambda reads the same
# table. The rules are the CloudWatch alarms themselves, read live and
# read-only: they are Terraform's, not the console's.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GOLD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SUBSCRIBER_FIELDS = ("email", "enabled", "gold_ids", "added_by", "added_at", "updated_at")


def _normalise_email(value) -> str | None:
    address = (value or "").strip().lower() if isinstance(value, str) else ""
    if not address or len(address) > 254 or not _EMAIL_RE.match(address):
        return None
    return address


def _parse_gold_ids(value, *, field="gold_ids"):
    """(ids, error). A list of known gold ids, deduplicated, sorted; None when
    the field is absent or null (= everything)."""
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, f"{field} must be a list of gold ids"
    ids = []
    for raw in value:
        if not isinstance(raw, str) or not _GOLD_ID_RE.match(raw.strip()):
            return None, f"{field}: {raw!r} is not a gold id"
        gid = raw.strip()
        if gid not in ids:
            ids.append(gid)
    if not ids:
        return None, None
    unknown = [g for g in ids if not _gold_entity_exists(g)]
    if unknown:
        return None, f"{field}: unknown gold entity {unknown[0]!r}"
    return sorted(ids), None


def _gold_entity_exists(gold_id: str) -> bool:
    try:
        resp = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
            Key={"gold_id": gold_id, "version": "latest"},
            ProjectionExpression="gold_id")
        return "Item" in resp
    except Exception as e:  # noqa: BLE001 - a lookup failure must read as "unknown", not 500
        logger.warning(f"[NOTIFICATIONS] gold entity lookup failed for {gold_id!r}: {e}")
        return False


def _subscriber_view(item: dict) -> dict:
    gold_ids = item.get("gold_ids")
    return {
        "email":      item.get("email"),
        "enabled":    item.get("enabled", True) is not False,
        "gold_ids":   sorted(str(g) for g in gold_ids) if gold_ids else None,
        "added_by":   item.get("added_by"),
        "added_at":   item.get("added_at"),
        "updated_at": item.get("updated_at"),
    }


class _NotificationsUnconfigured(RuntimeError):
    """The env fallback resolved to "" - this function is not wired for gold."""


def _subscribers_table():
    if not ALERT_SUBSCRIBERS_TABLE:
        raise _NotificationsUnconfigured("ALERT_SUBSCRIBERS_TABLE is not configured for this environment")
    return dynamo.Table(ALERT_SUBSCRIBERS_TABLE)


def list_alert_subscribers():
    """GET /notifications/subscribers"""
    try:
        table = _subscribers_table()
    except _NotificationsUnconfigured as e:
        return server_error(str(e))
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    rows = sorted((_subscriber_view(i) for i in items), key=lambda r: r["email"] or "")
    return ok({"items": rows, "total": len(rows)})


def add_alert_subscriber(body: dict):
    """POST /notifications/subscribers - an UPSERT that merges.

    New address -> 201 with the row. Existing address -> 200: the given
    gold_ids are ADDED to its set and the row is re-enabled (the self-service
    card on a second table must not overwrite the first), or, when no gold_ids
    are given, the filter is removed and the address hears everything."""
    email = _normalise_email(body.get("email"))
    if not email:
        return bad_request("email must be a valid address")
    gold_ids, err = _parse_gold_ids(body.get("gold_ids"))
    if err:
        return bad_request(err)
    try:
        table = _subscribers_table()
    except _NotificationsUnconfigured as e:
        return server_error(str(e))
    now = datetime.now(timezone.utc).isoformat()
    added_by = (body.get("added_by") or "console").strip()[:64]
    existing = table.get_item(Key={"email": email}).get("Item")
    if not existing:
        item = {"email": email, "enabled": True, "added_by": added_by, "added_at": now}
        if gold_ids:
            item["gold_ids"] = set(gold_ids)
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(email)")
        logger.info(f"[NOTIFICATIONS] subscribed {email} gold_ids={gold_ids or 'all'}")
        return created(_subscriber_view(item))
    names = {"#e": "enabled", "#u": "updated_at"}
    values = {":t": True, ":ts": now}
    if gold_ids:
        names["#g"] = "gold_ids"
        values[":g"] = set(gold_ids)
        expr = "SET #e = :t, #u = :ts ADD #g :g"
    else:
        names["#g"] = "gold_ids"
        expr = "SET #e = :t, #u = :ts REMOVE #g"
    resp = table.update_item(Key={"email": email}, UpdateExpression=expr,
                             ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                             ReturnValues="ALL_NEW")
    logger.info(f"[NOTIFICATIONS] merged {email} gold_ids+={gold_ids or 'all'}")
    return ok(_subscriber_view(resp.get("Attributes") or {}))


def set_alert_subscriber(email_param: str, body: dict):
    """PATCH /notifications/subscribers/{email}: enabled, gold_ids (replace;
    null = everything) and/or remove_gold_ids. Removing the LAST table is
    refused: an empty set would silently mean 'everything' - remove the
    address instead."""
    email = _normalise_email(email_param)
    if not email:
        return bad_request("email must be a valid address")
    if not isinstance(body, dict) or not body:
        return bad_request("body must carry enabled, gold_ids or remove_gold_ids")
    unknown_keys = set(body) - {"enabled", "gold_ids", "remove_gold_ids"}
    if unknown_keys:
        return bad_request(f"unknown field(s): {sorted(unknown_keys)}")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        return bad_request("enabled must be a JSON boolean")
    if "gold_ids" in body and "remove_gold_ids" in body:
        return bad_request("send gold_ids (replace) or remove_gold_ids, not both")
    replace = "gold_ids" in body
    gold_ids, err = _parse_gold_ids(body.get("gold_ids")) if replace else (None, None)
    if err:
        return bad_request(err)
    remove, err = _parse_gold_ids(body.get("remove_gold_ids"), field="remove_gold_ids") \
        if "remove_gold_ids" in body else (None, None)
    if err:
        return bad_request(err)
    try:
        table = _subscribers_table()
    except _NotificationsUnconfigured as e:
        return server_error(str(e))
    existing = table.get_item(Key={"email": email}).get("Item")
    if not existing:
        return not_found(f"{email} is not subscribed")
    current = {str(g) for g in (existing.get("gold_ids") or set())}
    if remove:
        if not current:
            return conflict(f"{email} is subscribed to everything; set gold_ids to narrow it")
        left = current - set(remove)
        if not left:
            return conflict(f"removing {sorted(remove)} would leave {email} with no table, which "
                            "means everything - remove the address instead")
        gold_ids, replace = sorted(left), True
    sets, names, values, removes = ["#u = :ts"], {"#u": "updated_at"}, {":ts": datetime.now(timezone.utc).isoformat()}, []
    if "enabled" in body:
        names["#e"] = "enabled"; values[":e"] = body["enabled"]; sets.append("#e = :e")
    if replace:
        names["#g"] = "gold_ids"
        if gold_ids:
            values[":g"] = set(gold_ids); sets.append("#g = :g")
        else:
            removes.append("#g")
    expr = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
    resp = table.update_item(Key={"email": email}, UpdateExpression=expr,
                             ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                             ConditionExpression="attribute_exists(email)", ReturnValues="ALL_NEW")
    logger.info(f"[NOTIFICATIONS] updated {email} {json.dumps(body)}")
    return ok(_subscriber_view(resp.get("Attributes") or {}))


def remove_alert_subscriber(email_param: str):
    """DELETE /notifications/subscribers/{email}"""
    email = _normalise_email(email_param)
    if not email:
        return bad_request("email must be a valid address")
    try:
        table = _subscribers_table()
    except _NotificationsUnconfigured as e:
        return server_error(str(e))
    try:
        table.delete_item(Key={"email": email}, ConditionExpression="attribute_exists(email)")
    except dynamo.meta.client.exceptions.ConditionalCheckFailedException:
        return not_found(f"{email} is not subscribed")
    logger.info(f"[NOTIFICATIONS] removed {email}")
    return ok({"email": email, "removed": True})


def _alarm_gold_id(alarm: dict) -> str | None:
    for dim in alarm.get("Dimensions") or []:
        if dim.get("Name") == "GoldId":
            return dim.get("Value")
    return None


def _alarm_condition(name: str, gold_id: str | None) -> str:
    rest = name[len(GOLD_ALARM_PREFIX):] if GOLD_ALARM_PREFIX and name.startswith(GOLD_ALARM_PREFIX) else name
    if gold_id:
        token = gold_id.replace("_", "-") + "-"
        if rest.startswith(token):
            rest = rest[len(token):]
    return rest.replace("-", " ")


def list_alert_rules():
    """GET /notifications/rules - the gold alarms, live and read-only."""
    if not GOLD_ALARM_PREFIX:
        return server_error("GOLD_ALARM_PREFIX is not configured for this environment")
    alarms, kwargs = [], {"AlarmNamePrefix": GOLD_ALARM_PREFIX, "AlarmTypes": ["MetricAlarm"]}
    while True:
        page = cloudwatch.describe_alarms(**kwargs)
        alarms.extend(page.get("MetricAlarms", []))
        if not page.get("NextToken"):
            break
        kwargs["NextToken"] = page["NextToken"]
    rows = []
    for a in alarms:
        gold_id = _alarm_gold_id(a)
        updated = a.get("StateUpdatedTimestamp")
        rows.append({
            "name":             a.get("AlarmName"),
            "gold_id":          gold_id,
            "condition":        _alarm_condition(a.get("AlarmName", ""), gold_id),
            "state":            a.get("StateValue"),
            "state_reason":     a.get("StateReason"),
            "state_updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
            "description":      a.get("AlarmDescription"),
            "metric":           a.get("MetricName"),
            "namespace":        a.get("Namespace"),
            "threshold":        a.get("Threshold"),
            "comparison":       a.get("ComparisonOperator"),
            "period_seconds":   a.get("Period"),
            "evaluation_periods": a.get("EvaluationPeriods"),
            "emails":           bool(a.get("AlarmActions")),
        })
    rows.sort(key=lambda r: (r["gold_id"] or "~", r["name"] or ""))
    return ok({"items": rows, "total": len(rows)})


# ── Async Oracle table analysis (via Glue) ────────────────────────

def trigger_async_analysis(body: dict):
    """
    POST /analyze-async
    Triggers the analyze-table Glue job and returns run_id immediately.
    Client polls GET /analyze-async/{run_id} for results.
    """
    secret_arn = body.get("secret_arn")
    table      = body.get("table")
    if not secret_arn or not table:
        return bad_request("Body must contain 'secret_arn' and 'table'")

    args = {
        "--secret_arn": secret_arn,
        "--table":      table.upper(),
    }
    # Optional hints passed through
    for key in ("partition_col", "date_col", "hash_col", "id_col",
                "date_start", "date_end", "hash_buckets"):
        val = body.get(key)
        if val is not None:
            args[f"--{key}"] = str(val)

    try:
        response = glue.start_job_run(
            JobName=ANALYZE_GLUE_JOB_NAME,
            Arguments=args,
        )
        run_id = response["JobRunId"]
        logger.info(f"[ANALYZE-ASYNC] started table={table} run_id={run_id}")
        return ok({
            "run_id":      run_id,
            "table":       table.upper(),
            "status":      "RUNNING",
            "status_url":  f"/analyze-async/{run_id}",
        })
    except Exception as e:
        logger.exception(f"[ANALYZE-ASYNC] failed to start job for {table}")
        return server_error(str(e))


def get_async_analysis(run_id: str, table: str = None):
    """
    GET /analyze-async/{run_id}?table=SCHEMA.TABLE
    Checks Glue job run status and returns DynamoDB results when complete.
    """
    try:
        job_run = glue.get_job_run(JobName=ANALYZE_GLUE_JOB_NAME, RunId=run_id)
        glue_status = job_run["JobRun"]["JobRunState"]
    except Exception as e:
        logger.warning(f"[ANALYZE-ASYNC] job not found: {run_id}")
        return not_found(f"Run ID not found: {run_id}")

    if glue_status in ("RUNNING", "STARTING", "STOPPING"):
        return ok({"run_id": run_id, "status": "RUNNING"})

    if glue_status != "SUCCEEDED":
        return ok({"run_id": run_id, "status": "FAILED",
                   "glue_status": glue_status})

    # Job succeeded — fetch results from DynamoDB
    # If table is provided, use it as part of the lookup key
    if not table:
        return ok({"run_id": run_id, "status": "SUCCEEDED", "result": None})

    try:
        resp = dynamo.Table(ANALYSIS_TABLE).get_item(
            Key={"table_name": table.upper(), "run_id": run_id}
        )
        item = resp.get("Item")
        if item:
            return ok({"run_id": run_id, "status": "SUCCEEDED", "result": item})
        return ok({"run_id": run_id, "status": "SUCCEEDED", "result": None})
    except Exception as e:
        logger.exception(f"[ANALYZE-ASYNC] failed to fetch result for {run_id}")
        return server_error(f"Failed to fetch analysis result: {str(e)}")


# ── Lambda entrypoint ────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# GOLD PIPELINES (see ADR-0001)
# Routes: /gold-pipelines, /gold-pipelines/{gold_id}, /toggle, /run
# Separate config table because grain differs from Silver pipelines.
# ─────────────────────────────────────────────────────────────────

_GOLD_REQUIRED_TOP_FIELDS = ("gold_id", "domain", "schema", "mapping", "sources", "target")
_GOLD_REQUIRED_TARGET_FIELDS = ("database", "table", "s3_path", "primary_key_cols")
_GOLD_REQUIRED_SCHEMA_FIELDS = ("master_s3_path", "version")
_GOLD_REQUIRED_MAPPING_FIELDS = ("manifest_s3_path", "sql_s3_path")


def _validate_gold_config(body: dict) -> str:
    """Return error string if gold config is invalid, else None."""
    for f in _GOLD_REQUIRED_TOP_FIELDS:
        if f not in body:
            return f"missing required top-level field: {f}"

    target = body["target"]
    for f in _GOLD_REQUIRED_TARGET_FIELDS:
        if f not in target:
            return f"missing required target.{f}"
    pks = target.get("primary_key_cols")
    if not isinstance(pks, list) or not pks:
        return "target.primary_key_cols must be a non-empty list"

    schema = body["schema"]
    for f in _GOLD_REQUIRED_SCHEMA_FIELDS:
        if f not in schema:
            return f"missing required schema.{f}"
    if not schema["master_s3_path"].startswith("s3://"):
        return "schema.master_s3_path must be an s3:// URI"

    mapping = body["mapping"]
    for f in _GOLD_REQUIRED_MAPPING_FIELDS:
        if f not in mapping:
            return f"missing required mapping.{f}"
    for f in ("manifest_s3_path", "sql_s3_path"):
        if not mapping[f].startswith("s3://"):
            return f"mapping.{f} must be an s3:// URI"

    sources = body["sources"]
    if not isinstance(sources, list) or not sources:
        return "sources must be a non-empty list"
    for i, src in enumerate(sources):
        if "silver_table" not in src:
            return f"sources[{i}].silver_table is required"

    # UIFPE-513: mirror gold_config.validate. Without this a bad cadence or
    # start time is accepted here and silently defaulted by the dispatcher.
    schedule = body.get("schedule") or {}
    if not isinstance(schedule, dict):
        return "schedule must be a map"
    freq = schedule.get("frequency")
    if freq is not None and not is_transform_frequency(freq):
        return f"schedule.frequency is not a gold cadence: {freq!r}"
    start_time = schedule.get("start_time")
    if start_time is not None and parse_time_of_day(start_time) is None:
        return f"schedule.start_time must be 'HH:MM' (UTC), got {start_time!r}"
    days = schedule.get("weekly_days")
    if days is not None:
        if not is_weekly(freq or DEFAULT_FREQUENCY):
            return "schedule.weekly_days is only valid when schedule.frequency is gold-weekly"
        if parse_weekly_days(days) is None:
            return f"schedule.weekly_days must be a non-empty list of {WEEKDAYS}, got {days!r}"

    return None


def _s3_object_exists(s3_uri: str) -> bool:
    if not s3_uri.startswith("s3://"):
        return False
    rest = s3_uri[5:]
    bucket, _, key = rest.partition("/")
    if not key:
        return False
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def ensure_gold_database(database_name: str) -> None:
    """Create the Glue Gold database if it doesn't exist (idempotent)."""
    try:
        glue_catalog.get_database(Name=database_name)
        logger.info(f"[GOLD DB EXISTS] {database_name}")
    except glue_catalog.exceptions.EntityNotFoundException:
        glue_catalog.create_database(
            DatabaseInput={
                "Name": database_name,
                "Description": f"Gold layer database (auto-created by pipeline_controller)",
            }
        )
        logger.info(f"[GOLD DB CREATED] {database_name}")


def create_gold_pipeline(body: dict):
    err = _validate_gold_config(body)
    if err:
        return bad_request(err)

    # Verify the schema + mapping artifacts actually exist in S3.
    for label, uri in (
        ("schema.master_s3_path",      body["schema"]["master_s3_path"]),
        ("mapping.manifest_s3_path",   body["mapping"]["manifest_s3_path"]),
        ("mapping.sql_s3_path",        body["mapping"]["sql_s3_path"]),
    ):
        if not _s3_object_exists(uri):
            return bad_request(f"{label} does not exist in S3: {uri}")

    gold_id = body["gold_id"]

    # Check for existing
    existing = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
        Key={"gold_id": gold_id, "version": "latest"}
    )
    if "Item" in existing:
        return bad_request(f"gold pipeline '{gold_id}' already exists — use PUT to update")

    # Auto-init metadata and watermark.
    now = datetime.now(timezone.utc).isoformat()
    body.setdefault("enabled", True)
    body["version"] = "latest"
    body.setdefault("watermark", {})
    body.setdefault("reconcile", {"enabled": False, "dry_run": True, "max_deletes_per_run": 5000})
    body.setdefault("metadata", {})
    body["metadata"]["registered_at"] = now
    body["metadata"].setdefault("owner", "unknown")
    body["metadata"].setdefault("last_run_id", None)
    body["metadata"].setdefault("last_run_at", None)

    # Auto-create Glue Gold database (Lambda has LF permissions).
    ensure_gold_database(body["target"]["database"])

    dynamo.Table(GOLD_CONFIG_TABLE).put_item(Item=_to_dynamo_safe(body))
    return created(body)


# A gold run carries gold_id, never pipeline_id, so gold rows are absent from
# the pipeline_id-index list_runs queries — a GSI indexes only items holding
# its key. Hence this separate read path rather than a second index.
GOLD_RUN_SCAN_CAP = 2000
GOLD_RUN_DEFAULT_LIMIT = 100
GOLD_RUN_MAX_LIMIT = 500


def list_gold_runs(query_params: dict = None):
    """Gold run history for every entity in one call (UIFPE-502).

    Read whole and sorted here because the range key is run_id, a random hash:
    DynamoDB can neither order by recency nor Limit usefully. Transform only —
    no entity enables reconcile, so that job has never written a row.
    """
    # An empty partition key is a ValidationException; name the cause instead.
    if not GOLD_JOB_NAME:
        return server_error("GOLD_JOB_NAME is not configured for this environment")

    params = query_params or {}
    wanted = (params.get("gold_id") or "").strip() or None
    try:
        limit = int(params.get("limit") or GOLD_RUN_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = GOLD_RUN_DEFAULT_LIMIT
    limit = max(1, min(limit, GOLD_RUN_MAX_LIMIT))

    table = dynamo.Table(METRICS_TABLE)
    items, start_key, capped = [], None, False
    while True:
        kwargs = {"KeyConditionExpression": Key("job_name").eq(GOLD_JOB_NAME)}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
        if len(items) >= GOLD_RUN_SCAN_CAP:
            capped = True
            break

    if wanted:
        items = [r for r in items if r.get("gold_id") == wanted]

    known_ids = {r.get("run_id") for r in items if r.get("run_id")}
    in_flight = [
        r for r in _in_flight_glue_runs(GOLD_JOB_NAME, wanted,
                                        arg_key="--gold_id", id_field="gold_id")
        if r["run_id"] not in known_ids
    ]

    items.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    # In-flight runs are always newer than completed metrics rows.
    runs = in_flight + items
    return ok({
        "runs": runs[:limit],
        "truncated": capped or len(runs) > limit,
    })


def list_gold_pipelines():
    resp = dynamo.Table(GOLD_CONFIG_TABLE).scan(
        FilterExpression=Key("version").eq("latest")
    )
    return ok(resp.get("Items", []))


def get_gold_pipeline(gold_id: str):
    resp = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
        Key={"gold_id": gold_id, "version": "latest"}
    )
    if "Item" not in resp:
        return not_found(f"gold pipeline '{gold_id}' not found")
    return ok(resp["Item"])


def update_gold_pipeline(gold_id: str, body: dict):
    err = _validate_gold_config(body)
    if err:
        return bad_request(err)
    if body.get("gold_id") != gold_id:
        return bad_request("gold_id in body must match path parameter")

    # Preserve registered_at if pre-existing
    existing = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
        Key={"gold_id": gold_id, "version": "latest"}
    ).get("Item")
    if existing:
        body.setdefault("metadata", {})
        body["metadata"]["registered_at"] = existing.get("metadata", {}).get(
            "registered_at",
            datetime.now(timezone.utc).isoformat()
        )

    body["version"] = "latest"
    dynamo.Table(GOLD_CONFIG_TABLE).put_item(Item=_to_dynamo_safe(body))
    return ok(body)


def delete_gold_pipeline(gold_id: str, cascade: bool = False):
    if not cascade:
        return bad_request(
            "delete requires ?cascade=true — refusing to delete config without "
            "explicit cascade (would orphan S3 + Iceberg metadata)"
        )
    existing = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
        Key={"gold_id": gold_id, "version": "latest"}
    ).get("Item")
    if not existing:
        return not_found(f"gold pipeline '{gold_id}' not found")

    # Best-effort cleanup of Gold S3 data; do not raise if it fails.
    target = existing.get("target", {})
    s3_path = target.get("s3_path")
    if s3_path:
        try:
            bucket, prefix = _parse_s3_path(s3_path)
            _delete_s3_prefix(bucket, prefix)
        except Exception as e:
            logger.warning(f"[GOLD CASCADE] s3 cleanup failed: {e}")

    dynamo.Table(GOLD_CONFIG_TABLE).delete_item(
        Key={"gold_id": gold_id, "version": "latest"}
    )
    return ok({"deleted": gold_id})


def toggle_gold_pipeline(gold_id: str, body: dict):
    if "enabled" not in body:
        return bad_request("body must include 'enabled' boolean")
    dynamo.Table(GOLD_CONFIG_TABLE).update_item(
        Key={"gold_id": gold_id, "version": "latest"},
        UpdateExpression="SET enabled = :e",
        ExpressionAttributeValues={":e": bool(body["enabled"])},
    )
    return ok({"gold_id": gold_id, "enabled": bool(body["enabled"])})


# The schedule fields the console owns, in the order they are written so the
# UpdateExpression is stable (its shape is pinned by tests).
_SCHEDULE_FIELDS = ("frequency", "start_time", "weekly_days")
_SCHEDULE_PLACEHOLDERS = {"frequency": ":f", "start_time": ":t", "weekly_days": ":d"}


def set_gold_schedule(gold_id: str, body: dict):
    """PATCH /gold-pipelines/{gold_id}/schedule - the console-owned schedule:
    `frequency` (UIFPE-564), `start_time` (UIFPE-513) and, on the weekly rung,
    `weekly_days` (UIFPE-532).

    The cadence used to live only in the domain YAML. Owner decision
    2026-09-24: an operator changes a table's cadence, interval and run time
    from the console, so `schedule.frequency` joined sync_pipelines'
    RUNTIME_PATHS and the YAML value is now the seed for a NEW entity only.

    Narrow like toggle, one UpdateExpression over only the fields sent, so the
    runtime-owned watermark and cursors on the row are never touched. Unlike
    toggle it is NOT an upsert: an unknown gold_id is a 404, never a stub row.
    The response echoes the stored schedule, which is how the console proves
    the change was applied by a deployment that understands these fields."""
    body = body or {}
    unknown = sorted(set(body) - set(_SCHEDULE_FIELDS))
    if unknown:
        return bad_request(
            f"unknown schedule field(s) {unknown}; this route takes "
            f"{list(_SCHEDULE_FIELDS)}")
    if not any(k in body for k in _SCHEDULE_FIELDS):
        return bad_request(
            "body must include frequency, start_time as 'HH:MM' (UTC) "
            "and/or weekly_days")
    updates = {}
    if "frequency" in body:
        raw = body.get("frequency")
        # Tolerant at the door, canonical in the row: the value stored is the
        # normalized token, so the dispatcher's own normalize() is a no-op and
        # a legacy alias ("gold", "gold-hourly") never reaches the config.
        freq = normalize(raw.strip().lower()) if isinstance(raw, str) else None
        if not freq or not is_transform_frequency(freq):
            return bad_request(
                f"frequency must be one of {list(TRANSFORM_FREQUENCIES)}, "
                f"got {raw!r}")
        updates["frequency"] = freq
    if "start_time" in body:
        raw = body.get("start_time")
        if parse_time_of_day(raw) is None:
            return bad_request(
                f"body must include start_time as 'HH:MM' (UTC), got {raw!r}")
        updates["start_time"] = raw.strip()
    if "weekly_days" in body:
        days = parse_weekly_days(body.get("weekly_days"))
        if days is None:
            return bad_request(
                f"weekly_days must be a non-empty list of {WEEKDAYS}, "
                f"got {body.get('weekly_days')!r}")
        updates["weekly_days"] = list(days)

    table = dynamo.Table(GOLD_CONFIG_TABLE)
    key = {"gold_id": gold_id, "version": "latest"}
    existing = table.get_item(Key=key).get("Item")
    if not existing:
        return not_found(f"gold pipeline '{gold_id}' not found")

    had_schedule = isinstance(existing.get("schedule"), dict)
    schedule = dict(existing["schedule"]) if had_schedule else {}
    stored_frequency = normalize(schedule.get("frequency") or DEFAULT_FREQUENCY)
    # The cadence the row will be ON after this write decides what a day set means.
    frequency = updates.get("frequency", stored_frequency)
    drop_weekly_days = False
    if is_weekly(frequency):
        # Arriving on the weekly rung without a day set would be a config the
        # dispatcher has to guess at; seed Monday, as the YAML scaffold does.
        if "weekly_days" not in updates and parse_weekly_days(
                schedule.get("weekly_days")) is None:
            updates["weekly_days"] = list(DEFAULT_WEEKLY_DAYS)
    else:
        if "weekly_days" in updates:
            return bad_request(
                f"weekly_days applies only to gold-weekly; '{gold_id}' "
                f"is on {frequency}. Send frequency=gold-weekly in the same "
                f"request to move it there.")
        # Leaving the weekly rung: a stale day set makes the row fail
        # gold_config.validate at the next sync, so it goes with the move.
        if parse_weekly_days(schedule.get("weekly_days")) is not None:
            drop_weekly_days = True
    old = {k: schedule.get(k) for k in updates}
    schedule.update(updates)
    if drop_weekly_days:
        old["weekly_days"] = schedule.pop("weekly_days", None)

    # Literal attribute names: none is a DynamoDB reserved word, and the
    # single-field form is the one the UIFPE-513 tests pin.
    ordered = [k for k in _SCHEDULE_FIELDS if k in updates]
    set_clauses = ", ".join(f"#s.{k} = {_SCHEDULE_PLACEHOLDERS[k]}" for k in ordered)
    values = {_SCHEDULE_PLACEHOLDERS[k]: updates[k] for k in ordered}
    remove_clause = " REMOVE #s.weekly_days" if drop_weekly_days else ""

    from botocore.exceptions import ClientError
    try:
        if had_schedule:
            table.update_item(
                Key=key,
                UpdateExpression=f"SET {set_clauses}{remove_clause}",
                ExpressionAttributeNames={"#s": "schedule"},
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(gold_id)",
            )
        else:
            # No schedule map yet: a nested SET would fail on the missing path.
            table.update_item(
                Key=key,
                UpdateExpression="SET #s = :m",
                ExpressionAttributeNames={"#s": "schedule"},
                ExpressionAttributeValues={":m": schedule},
                ConditionExpression="attribute_exists(gold_id)",
            )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return not_found(f"gold pipeline '{gold_id}' not found")
        raise

    # A moved clock or cadence is an operational event; there is no
    # authenticated actor to name yet (the JWT authorizer is off everywhere).
    for k in ordered:
        logger.info(f"[GOLD SCHEDULE] gold_id={gold_id} {k} {old[k]!r} -> {updates[k]!r}")
    if drop_weekly_days:
        logger.info(f"[GOLD SCHEDULE] gold_id={gold_id} weekly_days "
                    f"{old['weekly_days']!r} -> removed (left the weekly rung)")
    return ok({"gold_id": gold_id, "schedule": schedule})


def trigger_gold_run(gold_id: str, body: dict):
    cfg = dynamo.Table(GOLD_CONFIG_TABLE).get_item(
        Key={"gold_id": gold_id, "version": "latest"}
    ).get("Item")
    if not cfg:
        return not_found(f"gold pipeline '{gold_id}' not found")

    job_kind = (body or {}).get("kind", "transform")
    if job_kind not in ("transform", "reconcile"):
        return bad_request("kind must be 'transform' or 'reconcile'")
    job_name = GOLD_RECONCILE_JOB_NAME if job_kind == "reconcile" else GOLD_JOB_NAME

    # The two guards silver's trigger_run has had all along (UIFPE-516).
    if not cfg.get("enabled", True):
        return bad_request(f"gold pipeline '{gold_id}' is disabled")

    # A transform and a reconcile write the SAME Iceberg table, so a run of
    # either kind refuses while EITHER job has one in flight for this entity.
    # The dispatcher already refuses to dispatch over an in-flight run; this
    # manual route was the one writer without that check.
    for guarded_job in (GOLD_JOB_NAME, GOLD_RECONCILE_JOB_NAME):
        if not guarded_job:
            continue
        in_flight = _in_flight_glue_runs(
            guarded_job, gold_id, arg_key="--gold_id", id_field="gold_id")
        if in_flight:
            run = in_flight[0]
            return conflict(
                f"gold pipeline '{gold_id}' already has an in-flight run "
                f"{run['run_id']} ({run['status']}) on {guarded_job}")

    glue_cfg     = cfg.get("glue", {})
    worker_type  = glue_cfg.get("worker_type", "G.2X")
    num_workers  = int(glue_cfg.get("num_workers", 16 if job_kind == "transform" else 4))
    timeout_min  = int(glue_cfg.get("timeout_minutes", 60))

    job_args = {"--gold_id": gold_id}
    if job_kind == "transform" and (body or {}).get("rebuild"):
        job_args["--rebuild"] = "true"

    response = glue.start_job_run(
        JobName=job_name,
        Arguments=job_args,
        WorkerType=worker_type,
        NumberOfWorkers=num_workers,
        Timeout=timeout_min,
    )
    return ok({"gold_id": gold_id, "kind": job_kind, "run_id": response["JobRunId"], "rebuild": bool((body or {}).get("rebuild"))})


# Helper reused above — match Silver job's dynamo-safe encoder.
def _to_dynamo_safe(obj):
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo_safe(i) for i in obj]
    return obj


# ─────────────────────────────────────────────────────────────────
# /home/* — cross-module Operations Dashboard aggregations
# ─────────────────────────────────────────────────────────────────

def _window_start_iso(hours: int = HOME_WINDOW_HOURS) -> str:
    """ISO-8601 timestamp `hours` ago in UTC.

    Note: metrics rows may use `start_time` (Glue) or `started_at` (Lambdas).
    Lexicographic comparison works because ISO-8601 sorts in chronological
    order. If the schema ever switches to epoch numbers, this comparison
    breaks silently — keep these in lockstep.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _is_replicator_job(job_name: str) -> bool:
    return bool(job_name) and (
        "gcs-replicator" in job_name or "silver-bq-replicator" in job_name
    )


def _is_ingest_job(job_name: str) -> bool:
    """Bronze/Silver Glue runs — excludes reconcile and mirror Lambdas."""
    if not job_name:
        return True
    if _is_replicator_job(job_name) or "reconcile" in job_name:
        return False
    return (
        "bronze-ingest" in job_name
        or "silver-transform" in job_name
        # Terraform renamed the job to silver-transform-bq-native, which does
        # NOT contain "silver-bq-native" as a substring — the old test matched
        # nothing after the rename and BQ-native runs were misclassified.
        or "silver-transform-bq-native" in job_name
    )


def _metrics_started_at(row: dict) -> str:
    return row.get("started_at") or row.get("start_time") or row.get("ts") or ""


def _normalize_metrics_status(row: dict) -> str:
    """Return upper-case status for home aggregation and activity feed."""
    status = (row.get("status") or "").upper()
    if status:
        return status
    rep = (row.get("replication_status") or "").lower()
    if rep == "replicated":
        return "SUCCEEDED"
    if rep == "skipped":
        return "SKIPPED"
    if rep in ("partial", "bq_failed"):
        return "WARNING"
    if rep == "failed":
        return "FAILED"
    return ""


def _metrics_in_window(row: dict, floor_iso: str) -> bool:
    ts = _metrics_started_at(row)
    return bool(ts) and ts > floor_iso


def _aggregate_metrics_24h(table_name: str, id_field: str = "pipeline_id") -> dict:
    """Scan `table_name` for rows in the 24h window, group by id_field, return
    pipeline-LEVEL counts.

    Returns {"ok": int, "fail": int, "in_24h": int}, where:
      - ok   = distinct ids whose runs in the window were ALL SUCCEEDED
      - fail = distinct ids whose runs in the window had AT LEAST ONE non-SUCCEEDED
      - in_24h = ok + fail (the two sets are disjoint by construction)

    This counts pipelines, not runs — see resolved decision #2 in the UX design
    spec. A pipeline with 5 SUCCEEDED + 1 FAILED counts as 1 fail.

    If the table doesn't exist (the partner_export and dq_check tables won't
    until Plans 6/7 ship), catch ResourceNotFoundException and return zeros —
    the home page must work day-1.
    """
    from botocore.exceptions import ClientError
    floor = _window_start_iso()
    ingest_only = table_name == METRICS_TABLE
    try:
        table = dynamo.Table(table_name)
        resp = table.scan()
        items = resp.get("Items", [])
        while resp.get("LastEvaluatedKey"):
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return {"ok": 0, "fail": 0, "in_24h": 0}
        logger.warning(f"[HOME] _aggregate_metrics_24h({table_name}) ClientError: {e}")
        return {"ok": 0, "fail": 0, "in_24h": 0}
    except Exception as e:
        logger.warning(f"[HOME] _aggregate_metrics_24h({table_name}) failed: {e}")
        return {"ok": 0, "fail": 0, "in_24h": 0}

    items = [r for r in items if _metrics_in_window(r, floor)]
    if ingest_only:
        items = [r for r in items if _is_ingest_job(r.get("job_name") or "")]

    # Pipeline-level grouping: track per-id whether any run in the window failed.
    pipeline_failed: dict[str, bool] = {}
    for r in items:
        pid = r.get(id_field) or ""
        if not pid:
            continue
        status = _normalize_metrics_status(r)
        had_failure = status != "SUCCEEDED"
        # Once a pipeline has any failure, it stays in the fail bucket.
        pipeline_failed[pid] = pipeline_failed.get(pid, False) or had_failure

    fail_count = sum(1 for failed in pipeline_failed.values() if failed)
    ok_count = len(pipeline_failed) - fail_count
    return {"ok": ok_count, "fail": fail_count, "in_24h": len(pipeline_failed)}


def home_summary():
    """GET /home/summary — pipeline-level 24h pass/fail counts per module."""
    return ok({
        "syncs":   _aggregate_metrics_24h(METRICS_TABLE,        "pipeline_id"),
        "exports": _aggregate_metrics_24h(EXPORT_METRICS_TABLE, "export_id"),
        "dq":      _aggregate_metrics_24h(DQ_METRICS_TABLE,     "check_id"),
        "window_hours": HOME_WINDOW_HOURS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Gold statistics (UIFPE-544) ──────────────────────────────────────
# The Overview asks two questions with different freshness needs, so they have
# two sources:
#
#   "right now"  - computed here, from the config rows and metrics rows this
#                  Lambda already reads. Never more than seconds stale.
#   "this month" - READ from one small file the nightly job writes. A Lambda
#                  cannot read an Iceberg table, and granting this role Athena
#                  for a ~400-row query would buy seconds of page latency and a
#                  wider blast radius for nothing. It holds no Athena grant
#                  today, and this route deliberately does not need one.


def _gold_configs_with_cadence() -> list:
    """Every gold entity with its cadence resolved to minutes, which is what
    freshness is judged against."""
    table = dynamo.Table(GOLD_CONFIG_TABLE)
    items, start = [], None
    while True:
        kwargs = {"FilterExpression": Key("version").eq("latest")}
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    out = []
    for cfg in items:
        enriched = dict(cfg)
        freq = normalize((cfg.get("schedule") or {}).get("frequency") or "")
        enriched["_interval_minutes"] = interval_minutes(freq)
        out.append(enriched)
    return out


def _gold_transform_runs() -> list:
    """Every gold transform run the metrics table still holds. One query on the
    partition key, as list_gold_runs does; the window is applied in gold_stats,
    which is where "inside the last 24 hours" is defined."""
    if not GOLD_JOB_NAME:
        return []
    table = dynamo.Table(METRICS_TABLE)
    items, start = [], None
    while True:
        kwargs = {"KeyConditionExpression": Key("job_name").eq(GOLD_JOB_NAME)}
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start or len(items) >= GOLD_RUN_SCAN_CAP:
            break
    return items


def _gold_stats_summary() -> tuple:
    """(the nightly file, None) or (None, why it is missing).

    A missing file is NOT an error the page fails on - on the day this ships
    the nightly job has not run yet - but it is never silently rendered as
    zeros either: the route says `trend_available: false` and carries the
    reason, so the page shows "not computed yet" and not a flat line.
    """
    if not GOLD_ARTIFACTS_BUCKET:
        return None, "GOLD_ARTIFACTS_BUCKET is not configured for this environment"
    try:
        obj = s3_client.get_object(Bucket=GOLD_ARTIFACTS_BUCKET,
                                   Key=gold_stats.SUMMARY_KEY)
        return json.loads(obj["Body"].read().decode("utf-8")), None
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"[GOLD STATS] summary unreadable: {type(e).__name__}: {e}")
        return None, f"{type(e).__name__}: {e}"


def gold_stats_overview(query_params: dict = None):
    """GET /gold-stats?days=30 - the fleet Overview."""
    params = query_params or {}
    try:
        days = int(params.get("days") or gold_stats.SUMMARY_DAYS)
    except (TypeError, ValueError):
        days = gold_stats.SUMMARY_DAYS

    now = datetime.now(timezone.utc)
    live = gold_stats.live_window(_gold_transform_runs(),
                                  _gold_configs_with_cadence(), now)

    summary, why = _gold_stats_summary()
    trend = gold_stats.trim_summary(summary, days) if summary else None
    generated_at = (summary or {}).get("generated_at")
    return ok({
        "live": live,
        "trend": trend,
        "trend_available": trend is not None,
        "trend_unavailable_reason": why,
        "trend_generated_at": generated_at,
        # How old the pre-computed half is, so the page can say so rather than
        # passing last week's numbers off as this morning's.
        "trend_age_minutes": gold_stats.minutes_between(generated_at, now),
        "generated_at": now.isoformat(),
    })


def _activity_subject(row: dict) -> str:
    """What a feed item is called: the pipeline for bronze/silver, the ENTITY
    for gold, which carries gold_id and no pipeline_id. Empty means the row
    cannot be named and must not reach the feed (UIFPE-504)."""
    return (row.get("pipeline_id") or row.get("export_id")
            or row.get("check_id") or row.get("gold_id") or "")


def _row_to_activity_item(row: dict, module: str) -> dict:
    """Map a DynamoDB metrics row into the home-feed contract.

    Server resolves `link_path` so the UI is dumb about routing.
    """
    pipeline_id = row.get("pipeline_id") or row.get("export_id") or row.get("check_id") or ""
    subject = _activity_subject(row)
    ts = _metrics_started_at(row)
    status = _normalize_metrics_status(row)

    if module == "sync" and not pipeline_id:
        # A gold run has no per-entity page: /syncs/{id} resolves a pipeline_id
        # and would 404 on a gold_id. The Job Runs list is where it is visible.
        link_path = "/m/data-sync/runs"
    elif module == "sync":
        link_path = f"/m/data-sync/syncs/{pipeline_id}"
    elif module == "export":
        link_path = f"/m/partner-export/exports/{pipeline_id}"
    elif module == "dq":
        link_path = f"/m/data-quality/checks/{pipeline_id}"
    else:
        link_path = "/"

    message = row.get("error_message") or ""
    if not message and status == "SUCCEEDED":
        rw = row.get("rows_written")
        if rw is not None:
            message = f"{rw} rows"
        elif row.get("files_replicated") is not None:
            message = f"{row.get('files_replicated')} files replicated"

    return {
        "ts": ts,
        "module": module,
        "subject": subject,
        "status": status,
        "message": message,
        "link_path": link_path,
    }


def _scan_recent(table_name: str, module: str, floor_iso: str) -> list[dict]:
    """Scan one metrics table and return activity items in the home-feed shape.

    Safe-zero on ResourceNotFoundException (Plans 6/7 haven't shipped the
    exports + dq tables yet).
    """
    from botocore.exceptions import ClientError
    try:
        table = dynamo.Table(table_name)
        resp = table.scan()
        items = resp.get("Items", [])
        while resp.get("LastEvaluatedKey"):
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
        items = [r for r in items if _metrics_in_window(r, floor_iso)]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return []
        logger.warning(f"[HOME] _scan_recent({table_name}) ClientError: {e}")
        return []
    except Exception as e:
        logger.warning(f"[HOME] _scan_recent({table_name}) failed: {e}")
        return []

    # A row nothing can name would render as a bare status with a dead link.
    return [_row_to_activity_item(r, module) for r in items if _activity_subject(r)]


def home_recent_activity(limit: int = 20):
    """GET /home/recent-activity?limit=N — chronological mixed feed."""
    floor = _window_start_iso()
    items = []
    items.extend(_scan_recent(METRICS_TABLE,        "sync",   floor))
    items.extend(_scan_recent(EXPORT_METRICS_TABLE, "export", floor))
    items.extend(_scan_recent(DQ_METRICS_TABLE,     "dq",     floor))
    items.sort(key=lambda x: x["ts"], reverse=True)
    return ok({"items": items[:limit], "count": min(len(items), limit)})


# ─────────────────────────────────────────────────────────────────
# /catalog/lineage — cross-module dataset lineage
# ─────────────────────────────────────────────────────────────────
#
# GET /catalog/lineage?dataset=<layer>.<cloud>.<database>.<table>
#
# Returns:
#   { "dataset", "upstream":           { "sync_id", "sync_name" } | null,
#                "downstream_exports": [ { "export_id", "name" }, ... ],
#                "downstream_checks":  [ { "check_id",  "name" }, ... ] }
#
# Dataset grammar (resolved decision #3): 4-part
#   <layer>.<cloud>.<database>.<table>
# Layer ∈ {bronze, silver, gold}; cloud ∈ {aws, gcp}; database is case-insensitive.
#
# UPSTREAM resolution:
#   layer=silver  → match destination.cloud == cloud
#                   AND silver.database (AWS) OR silver.bq_dataset (GCP) == database
#                   AND silver.table == table
#   layer=bronze  → match destination.cloud == cloud (default 'aws' when absent)
#                   AND extract.table SCHEMA.TABLE (case-insensitive) → database.table
#   layer=gold    → scan gold_pipelines_config; match target.database + target.table
#                   (gold is always AWS today; cloud must be 'aws')
#
# DOWNSTREAM tables (partner_export_config, dq_check_config) ship in Plans 6/7.
# When absent, ResourceNotFoundException is caught and empty arrays are returned.

PARTNER_EXPORT_CONFIG_TABLE = os.environ.get(
    "PARTNER_EXPORT_CONFIG_TABLE", echo_legacy("partner-export-config")
)
DQ_CHECK_CONFIG_TABLE = os.environ.get(
    "DQ_CHECK_TABLE", os.environ.get("DQ_CHECK_CONFIG_TABLE", echo_legacy("dq-check-config"))
)


def _parse_dataset_ref(ref: str):
    """Split `<layer>.<cloud>.<database>.<table>` → (layer, cloud, database, table).

    Returns None when input is malformed. Resolved decision #3: 4-part grammar.
    """
    if not ref or not isinstance(ref, str):
        return None
    parts = ref.split(".")
    if len(parts) != 4:
        return None
    layer, cloud, db, tbl = parts
    layer_lc = layer.lower()
    cloud_lc = cloud.lower()
    if layer_lc not in ("bronze", "silver", "gold"):
        return None
    if cloud_lc not in ("aws", "gcp"):
        return None
    return (layer_lc, cloud_lc, db, tbl)


def _scan_all_catalog(table_name: str):
    """Paginated scan. Caller must catch ResourceNotFoundException."""
    tbl = dynamo.Table(table_name)
    items = []
    kwargs = {}
    while True:
        resp = tbl.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _pipeline_cloud(p: dict) -> str:
    """Return 'aws' or 'gcp'. Default 'aws' when destination block missing."""
    return ((p.get("destination") or {}).get("cloud") or "aws").lower()


def _lineage_upstream(layer: str, cloud: str, database: str, table: str):
    """Find the sync producing this dataset. Returns {sync_id, sync_name} or None."""
    db_lc = database.lower()
    tbl_lc = table.lower()

    if layer == "gold":
        # Gold is AWS-only today.
        if cloud != "aws":
            return None
        try:
            golds = _scan_all_catalog(GOLD_CONFIG_TABLE)
        except Exception as e:
            logger.warning(f"[LINEAGE] gold scan failed: {e}")
            return None
        for g in golds:
            if g.get("version") != "latest":
                continue
            tgt = g.get("target") or {}
            if ((tgt.get("database") or "").lower() == db_lc
                    and (tgt.get("table") or "").lower() == tbl_lc):
                return {"sync_id": g["gold_id"], "sync_name": g["gold_id"]}
        return None

    try:
        pipelines = _scan_all_catalog(CONFIG_TABLE)
    except Exception as e:
        logger.warning(f"[LINEAGE] pipelines scan failed: {e}")
        return None

    for p in pipelines:
        if p.get("version") != "latest":
            continue
        if _pipeline_cloud(p) != cloud:
            continue
        pid = p.get("pipeline_id")
        if layer == "silver":
            silver = p.get("silver") or {}
            if not silver.get("enabled"):
                continue
            cand_db = (silver.get("database") or silver.get("bq_dataset") or "").lower()
            cand_tbl = (silver.get("table") or "").lower()
            if cand_db == db_lc and cand_tbl == tbl_lc:
                return {"sync_id": pid, "sync_name": pid}
        elif layer == "bronze":
            ext_tbl = (p.get("extract") or {}).get("table") or ""
            if "." in ext_tbl:
                schema, name = ext_tbl.split(".", 1)
                if schema.lower() == db_lc and name.lower() == tbl_lc:
                    return {"sync_id": pid, "sync_name": pid}
    return None


def _lineage_downstream_exports(dataset_ref: str):
    """Best-effort lookup. Returns [] if partner_export_config table missing."""
    try:
        exports = _scan_all_catalog(PARTNER_EXPORT_CONFIG_TABLE)
    except dynamo.meta.client.exceptions.ResourceNotFoundException:
        return []
    except Exception as e:
        logger.warning(f"[LINEAGE] partner_export_config scan failed: {e}")
        return []

    result = []
    ref_lc = dataset_ref.lower()
    for e in exports:
        if e.get("version") != "latest":
            continue
        src = (e.get("source") or {}).get("dataset") or ""
        if src.lower() == ref_lc:
            result.append({
                "export_id": e.get("export_id"),
                "name": e.get("name") or e.get("export_id"),
            })
    return result


def lookup_downstream_checks(layer: str, database: str, table: str) -> list:
    """Return all DQ checks whose subject matches (layer, database, table).

    Used by /catalog/lineage to populate downstream_checks. Plan 6 subject
    shape is {layer, database, table}; Plan 4 left a stub here.

    Safe-zero behaviour preserved when dq_check_config doesn't exist yet
    (mirrors scheduler_dispatcher._dispatch_dq)."""
    try:
        checks = _scan_all_catalog(DQ_CHECK_CONFIG_TABLE)
    except dynamo.meta.client.exceptions.ResourceNotFoundException:
        logger.warning(f"[LINEAGE] {DQ_CHECK_CONFIG_TABLE} not found — returning empty downstream_checks")
        return []
    except Exception as e:
        logger.warning(f"[LINEAGE] dq scan failed: {e}")
        return []

    result = []
    for c in checks:
        if c.get("version") != "latest":
            continue
        subj = c.get("subject") or {}
        if (subj.get("layer") == layer and
            subj.get("database") == database and
            subj.get("table") == table):
            result.append({
                "check_id": c.get("check_id"),
                "kind":     c.get("kind"),
                "enabled":  bool(c.get("enabled", False)),
                "schedule": c.get("schedule"),
                "last_run": c.get("last_run"),
            })
    return result


def _lineage_downstream_checks(dataset_ref: str):
    """Back-compat wrapper for the Plan 4 dataset-string signature.
    Parses '<layer>.<cloud>.<database>.<table>' and delegates."""
    parsed = _parse_dataset_ref(dataset_ref)
    if not parsed:
        return []
    layer, _cloud, database, table = parsed
    return lookup_downstream_checks(layer, database, table)


def get_catalog_lineage(query_params: dict):
    dataset = (query_params or {}).get("dataset")
    parsed = _parse_dataset_ref(dataset)
    if not parsed:
        return bad_request(
            "dataset query param required in form '<layer>.<cloud>.<database>.<table>' "
            "where layer ∈ bronze|silver|gold and cloud ∈ aws|gcp"
        )
    layer, cloud, database, table = parsed

    upstream = _lineage_upstream(layer, cloud, database, table)
    downstream_exports = _lineage_downstream_exports(dataset)
    downstream_checks = _lineage_downstream_checks(dataset)
    return ok({
        "dataset": dataset,
        "upstream": upstream,
        "downstream_exports": downstream_exports,
        "downstream_checks": downstream_checks,
    })


# ─────────────────────────────────────────────────────────────────
# HANDLER
# ─────────────────────────────────────────────────────────────────

# ── UI login (interim, pre-SSO) ────────────────────────────────────
# Temporary username/password gate for the admin console, to be replaced by
# Pearson SSO plus a JWT authorizer. The credential is checked HERE and never
# shipped in the UI bundle — a shared password harvested from a JS bundle gets
# tried against other systems, which is the real risk, not app access.
#
# Secret `echo/{env}/ui/login`:
#   {"users": {"<username>": "<password>"}, "token_secret": "<base64>"}
#
# An EMPTY stored password means the account is not provisioned yet and MUST
# NOT authenticate a blank submission — accounts are created with "" and given
# a password later via put-secret-value, with no code change.
#
# The token is HMAC-SHA256 over a compact base64url payload: stdlib only, so no
# PyJWT and no Lambda layer. Nothing validates it at the gateway yet (the API is
# protected at the network layer); `_verify_token` exists so a REQUEST
# authorizer can enforce these tokens later with no UI change.

_UI_ENV           = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "dev"))
UI_LOGIN_SECRET   = os.environ.get("UI_LOGIN_SECRET", f"echo/{_UI_ENV}/ui/login")
UI_TOKEN_TTL_SECS = int(os.environ.get("UI_TOKEN_TTL_SECS", 8 * 3600))


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _sign_token(payload: dict, token_secret: str) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig  = hmac.new(token_secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64u(sig)}"


def _verify_token(token: str, token_secret: str):
    """Payload when signature and expiry both hold, else None. Unused today."""
    body, _, sig = (token or "").partition(".")
    if not body or not sig:
        return None
    expected = _b64u(hmac.new(token_secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64u_decode(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    return payload


def login(body: dict):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username:
        return bad_request("username is required")

    try:
        cfg = json.loads(secrets.get_secret_value(SecretId=UI_LOGIN_SECRET)["SecretString"])
    except Exception:
        logger.exception(f"[LOGIN] could not read secret {UI_LOGIN_SECRET}")
        return server_error("Login is not configured")

    token_secret = cfg.get("token_secret") or ""
    stored       = (cfg.get("users") or {}).get(username)

    # One identical 401 for unknown user, unprovisioned account and wrong
    # password — the response must not reveal which. Reason goes to the log only,
    # never the password.
    if not isinstance(stored, str):
        logger.info(f"[LOGIN] denied user={username} reason=unknown_user")
        return unauthorized("Incorrect username or password")
    if stored == "":
        logger.info(f"[LOGIN] denied user={username} reason=not_provisioned")
        return unauthorized("Incorrect username or password")
    if not token_secret:
        logger.error(f"[LOGIN] token_secret missing from {UI_LOGIN_SECRET}")
        return server_error("Login is not configured")
    if not hmac.compare_digest(stored.encode(), password.encode()):
        logger.info(f"[LOGIN] denied user={username} reason=bad_password")
        return unauthorized("Incorrect username or password")

    exp = int(time.time()) + UI_TOKEN_TTL_SECS
    logger.info(f"[LOGIN] ok user={username} exp={exp}")
    return ok({
        "token":     _sign_token({"sub": username, "exp": exp}, token_secret),
        "username":  username,
        "expiresAt": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
    })


def handler(event, context):
    method       = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    raw_path     = event.get("rawPath", "/")
    path_params  = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    pipeline_id  = path_params.get("pipeline_id")
    gold_id      = path_params.get("gold_id")
    run_id       = path_params.get("run_id")
    subscriber   = path_params.get("email")

    body = {}
    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except Exception:
            return bad_request("Invalid JSON body")

    logger.info(f"[REQUEST] {method} {raw_path}")

    try:
        if method == "POST" and raw_path == "/auth/login":
            return login(body)

        if method == "POST" and raw_path == "/pipelines":
            return create_pipeline(body)

        if method == "GET" and raw_path == "/pipelines":
            return list_pipelines()

        if method == "GET" and pipeline_id and not run_id and not raw_path.endswith("/runs"):
            return get_pipeline(pipeline_id)

        if method == "PUT" and pipeline_id:
            return update_pipeline(pipeline_id, body)

        if method == "DELETE" and pipeline_id and not raw_path.endswith("/runs"):
            cascade = query_params.get("cascade", "").lower() == "true"
            return delete_pipeline(pipeline_id, cascade=cascade)

        if method == "PATCH" and pipeline_id and raw_path.endswith("/toggle"):
            return toggle_pipeline(pipeline_id, body)

        if method == "POST" and pipeline_id and raw_path.endswith("/run"):
            layer = (query_params.get("layer") or body.get("layer") or "bronze").lower()
            return trigger_run(pipeline_id, body, layer=layer)

        if method == "GET" and pipeline_id and raw_path.endswith("/runs") and not run_id:
            return list_runs(pipeline_id)

        if method == "GET" and pipeline_id and run_id:
            return get_run(pipeline_id, run_id,
                           job_name=(query_params or {}).get("job_name"))

        if method == "POST" and raw_path == "/analyze":
            return trigger_analysis(body)

        if method == "POST" and raw_path == "/analyze-async":
            return trigger_async_analysis(body)

        if method == "GET" and re.match(r"^/analyze-async/[^/]+$", raw_path):
            run_id = raw_path.split("/")[-1]
            table  = query_params.get("table")
            return get_async_analysis(run_id, table)

        if method == "GET" and raw_path == "/connections":
            return list_connections()

        if method == "GET" and raw_path == "/connections/gcs":
            return list_gcs_connections()

        if method == "GET" and raw_path == "/connections/silver-bq":
            return list_silver_bq_connections()

        if method == "GET" and raw_path == "/home/summary":
            return home_summary()

        if method == "GET" and raw_path == "/gold-stats":
            return gold_stats_overview(query_params)

        if method == "GET" and raw_path == "/catalog/lineage":
            return get_catalog_lineage(query_params)

        if method == "GET" and raw_path == "/home/recent-activity":
            try:
                limit = int((query_params or {}).get("limit") or 20)
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 100))
            return home_recent_activity(limit=limit)

        # ─── Gold notifications (UIFPE-543) ───────────────────────
        if raw_path == "/notifications/subscribers":
            if method == "GET":
                return list_alert_subscribers()
            if method == "POST":
                return add_alert_subscriber(body)

        if subscriber and raw_path.startswith("/notifications/subscribers/"):
            if method == "PATCH":
                return set_alert_subscriber(subscriber, body)
            if method == "DELETE":
                return remove_alert_subscriber(subscriber)

        if method == "GET" and raw_path == "/notifications/rules":
            return list_alert_rules()

        # ─── Gold pipeline routes (see ADR-0001) ──────────────────
        if method == "POST" and raw_path == "/gold-pipelines":
            return create_gold_pipeline(body)

        if method == "GET" and raw_path == "/gold-pipelines":
            return list_gold_pipelines()

        # Its own collection, NOT /gold-pipelines/{gold_id}/runs: a literal
        # segment shadows that id variable, and the PUT/DELETE branches below
        # carry no /runs exclusion, so they would swallow writes to it.
        if method == "GET" and raw_path == "/gold-runs":
            return list_gold_runs(query_params)

        if method == "GET" and gold_id and not run_id and not raw_path.endswith("/runs"):
            return get_gold_pipeline(gold_id)

        if method == "PUT" and gold_id:
            return update_gold_pipeline(gold_id, body)

        if method == "DELETE" and gold_id and raw_path.startswith("/gold-pipelines/"):
            cascade = query_params.get("cascade", "").lower() == "true"
            return delete_gold_pipeline(gold_id, cascade=cascade)

        if method == "PATCH" and gold_id and raw_path.endswith("/schedule"):
            return set_gold_schedule(gold_id, body)

        if method == "PATCH" and gold_id and raw_path.endswith("/toggle"):
            return toggle_gold_pipeline(gold_id, body)

        if method == "POST" and gold_id and raw_path.endswith("/run"):
            return trigger_gold_run(gold_id, body)

        return bad_request(f"Unknown route: {method} {raw_path}")

    except Exception as e:
        logger.exception("Unhandled error")
        return server_error(str(e))
