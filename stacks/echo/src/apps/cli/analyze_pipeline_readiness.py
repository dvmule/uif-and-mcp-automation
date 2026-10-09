#!/usr/bin/env python3
"""Analyze DynamoDB pipelines for SPEARI, initial-load readiness, and clean GCS/BQ."""
from __future__ import annotations

import json
import sys

import boto3
from google.cloud import bigquery, storage
from google.oauth2 import service_account
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

REGION = "eu-west-1"
CONFIG_TABLE = sn.load("dev").config_table
GCP_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:283556840805:secret:gcp-ipm-replicator-dev-g9n3Nk"
)
SPEARI_SECRET = (
    "arn:aws:secretsmanager:eu-west-1:283556840805:secret:eds/dev/gip/db/tep-FShxr3"
)
# not-echo-managed: this Glue connection is not declared in this stack's
# Terraform, which owns "${local.name_prefix}-vpc-network" only. Its name
# is a fact about the account, not something the uif- rename touches.
SPEARI_CONNECTION = "eds-dev-gip-db-tep"  # not-echo-managed
INITIAL_WATERMARKS = {"", None, "1970-01-01T00:00:00"}
INITIAL_SILVER_DATES = {"", None, "1970-01-01", "1970-01-01T00:00:00"}
BQ_PROJECT = "pearson-rr"
BQ_SILVER_DATASET = "ENTERPRISE_IPM_SILVER"
BQ_BRONZE_DATASET = "ENTERPRISE_IPM_BRONZE"

TABLES = [
    "FPI_REP_PRD_IPC_IP_ADD_INFO_V",
    "FPI_REP_PRD_USAGE_LIM_RIGHT_V",
    "FPI_REP_PRD_IPC_IP_RELATION_V",
    "FPI_REP_PRD_IPC_BOOK_V",
    "FPI_REP_PRD_IPC_IP_V",
    "FPI_REP_PRD_IPC_EBOOK_V",
    "FPI_REP_PRD_IPC_TEXT_V",
    "FPI_REP_PRD_IPC_IMAGE_V",
    "FPI_REP_PRD_IPC_VERSION_V",
    "FPI_REP_PRD_IPC_IP_IDENTIF_V",
    "FPI_REP_PRD_IPC_IP_PARTY_V",
    "FPI_REP_PRD_IPC_AUDIO_V",
    "FPI_REP_PRD_IPC_VIDEO_V",
    "FPI_REP_PRD_IPC_PLACEMENT_V",
    "FPI_REP_PRD_IPC_IP_MILESTONE_V",
    "FPI_REP_ACC_POST_LIST_HIST_V",
    "FPI_REP_ADM_PARTY_HEADER_V",
    "FPI_REP_ADM_PARTY_CONTACT_V",
    "FPI_REP_ADM_PARTY_ADDRESS_V",
]


def parse_gs_uri(uri: str) -> tuple[str, str]:
    rest = uri[5:]
    bucket, _, prefix = rest.partition("/")
    return bucket, prefix.rstrip("/") + "/" if prefix else ""


def gcs_object_count(storage_client: storage.Client, gcs_uri: str) -> int:
    if not gcs_uri:
        return -1
    bucket_name, prefix = parse_gs_uri(gcs_uri)
    return sum(1 for _ in storage_client.list_blobs(bucket_name, prefix=prefix))


def bq_exists(bq_client: bigquery.Client, table_id: str) -> bool:
    try:
        bq_client.get_table(table_id)
        return True
    except Exception:
        return False


def on_speari(source: dict) -> bool:
    return (
        source.get("secret_arn") == SPEARI_SECRET
        and source.get("connection_name") == SPEARI_CONNECTION
    )


def initial_ready(extract: dict, silver: dict) -> tuple[bool, str]:
    wm = extract.get("watermark_value")
    lpd = silver.get("last_processed_date")
    if wm not in INITIAL_WATERMARKS:
        return False, f"watermark={wm!r}"
    if lpd not in INITIAL_SILVER_DATES:
        return False, f"silver.last_processed_date={lpd!r}"
    return True, "ok"


def main() -> int:
    sm = boto3.client("secretsmanager", region_name=REGION)
    info = json.loads(sm.get_secret_value(SecretId=GCP_SECRET_ARN)["SecretString"])
    creds = service_account.Credentials.from_service_account_info(info)
    storage_client = storage.Client(project=info.get("project_id"), credentials=creds)
    bq_client = bigquery.Client(project=BQ_PROJECT, credentials=creds)

    table = boto3.resource("dynamodb", region_name=REGION).Table(CONFIG_TABLE)
    items = []
    kwargs = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    latest = [i for i in items if i.get("version") == "latest"]
    targets = {t.upper() for t in TABLES}
    grouped: dict[str, list] = {t: [] for t in TABLES}

    for item in latest:
        extract_table = ((item.get("extract") or {}).get("table") or "").upper()
        silver_table = ((item.get("silver") or {}).get("table") or "").upper()
        for name in targets:
            if name in extract_table or silver_table == name:
                src = item.get("source") or {}
                ext = item.get("extract") or {}
                slv = item.get("silver") or {}
                tgt = item.get("target") or {}
                gcs = tgt.get("gcs_path")
                silver_id = f"{BQ_PROJECT}.{BQ_SILVER_DATASET}.{slv.get('table', name)}"
                ext_id = f"{BQ_PROJECT}.{BQ_BRONZE_DATASET}.{slv.get('table', name)}_EXT"
                gcs_count = gcs_object_count(storage_client, gcs) if gcs else -1
                silver_exists = bq_exists(bq_client, silver_id)
                ext_exists = bq_exists(bq_client, ext_id)
                init_ok, init_note = initial_ready(ext, slv)
                grouped[name].append(
                    {
                        "pipeline_id": item.get("pipeline_id"),
                        "enabled": item.get("enabled"),
                        "extract_table": ext.get("table"),
                        "secret_arn": src.get("secret_arn"),
                        "connection_name": src.get("connection_name"),
                        "on_speari": on_speari(src),
                        "watermark_value": ext.get("watermark_value"),
                        "silver_last_processed_date": slv.get("last_processed_date"),
                        "initial_load_ready": init_ok,
                        "initial_load_note": init_note,
                        "gcs_path": gcs,
                        "gcs_objects": gcs_count,
                        "gcs_clear": gcs_count == 0,
                        "silver_bq": silver_id,
                        "silver_bq_exists": silver_exists,
                        "external_bq": ext_id,
                        "external_bq_exists": ext_exists,
                        "bq_clear": not silver_exists and not ext_exists,
                        "primary_key_cols": slv.get("primary_key_cols"),
                        "last_run_at": (item.get("metadata") or {}).get("last_run_at"),
                    }
                )

    summary = []
    print("=" * 100)
    print("PIPELINE READINESS ANALYSIS")
    print("=" * 100)
    for name in TABLES:
        entries = grouped[name]
        print(f"\n## {name}")
        if not entries:
            print("  STATUS: NO PIPELINE FOUND")
            summary.append(
                {
                    "table": name,
                    "status": "MISSING",
                    "pipeline_count": 0,
                    "all_checks_pass": False,
                }
            )
            continue
        if len(entries) > 1:
            print(f"  WARNING: {len(entries)} duplicate pipeline entries")
        for e in entries:
            checks = {
                "speari": e["on_speari"],
                "initial_load": e["initial_load_ready"],
                "gcs_clear": e["gcs_clear"],
                "bq_clear": e["bq_clear"],
            }
            all_pass = all(checks.values()) and e["enabled"]
            flag = "PASS" if all_pass else "FAIL"
            print(f"  [{flag}] {e['pipeline_id']}")
            print(f"    enabled={e['enabled']} extract={e['extract_table']}")
            print(f"    speari={checks['speari']} connection={e['connection_name']}")
            print(f"    initial_load={checks['initial_load']} ({e['initial_load_note']}) watermark={e['watermark_value']}")
            print(f"    silver_lpd={e['silver_last_processed_date']} last_run_at={e['last_run_at']}")
            print(f"    gcs_clear={checks['gcs_clear']} ({e['gcs_objects']} objects) -> {e['gcs_path']}")
            print(f"    bq_clear={checks['bq_clear']} silver_exists={e['silver_bq_exists']} ext_exists={e['external_bq_exists']}")
            if e.get("primary_key_cols"):
                print(f"    pk={e['primary_key_cols']}")
            summary.append(
                {
                    "table": name,
                    "pipeline_id": e["pipeline_id"],
                    "status": flag,
                    "pipeline_count": len(entries),
                    "duplicate": len(entries) > 1,
                    "enabled": e["enabled"],
                    "on_speari": checks["speari"],
                    "initial_load_ready": checks["initial_load"],
                    "gcs_clear": checks["gcs_clear"],
                    "gcs_objects": e["gcs_objects"],
                    "bq_clear": checks["bq_clear"],
                    "all_checks_pass": all_pass,
                }
            )

    passed = [s for s in summary if s.get("all_checks_pass")]
    failed = [s for s in summary if not s.get("all_checks_pass")]
    dupes = [s for s in summary if s.get("duplicate")]

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Tables requested: {len(TABLES)}")
    print(f"Pipeline entries analyzed: {len(summary)}")
    print(f"PASS (all checks): {len(passed)}")
    print(f"FAIL: {len(failed)}")
    print(f"Duplicates: {len(dupes)}")

    if failed:
        print("\nFailures:")
        for s in failed:
            pid = s.get("pipeline_id", "n/a")
            issues = []
            if s.get("status") == "MISSING":
                issues.append("no pipeline")
            else:
                if s.get("duplicate"):
                    issues.append("duplicate")
                if not s.get("enabled", True):
                    issues.append("disabled")
                if not s.get("on_speari", False):
                    issues.append("not_speari")
                if not s.get("initial_load_ready", False):
                    issues.append("not_initial_ready")
                if not s.get("gcs_clear", False):
                    issues.append(f"gcs_has_{s.get('gcs_objects')}objs")
                if not s.get("bq_clear", False):
                    issues.append("bq_exists")
            print(f"  - {s['table']} ({pid}): {', '.join(issues)}")

    out_path = "tasks/_pipeline_readiness_analysis.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": grouped}, f, indent=2, default=str)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
