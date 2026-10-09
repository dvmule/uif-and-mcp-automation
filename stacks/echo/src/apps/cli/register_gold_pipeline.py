#!/usr/bin/env python3
# register_gold_pipeline.py
# CLI tool to register a Gold pipeline for ad-hoc/dev use (ADR-0004:
# independent control plane — writes DynamoDB directly, no platform API).
# The promoted path is config-as-code: pipelines/ + scripts/sync_pipelines.py.
#
# Checks whether the gold_id is already registered FIRST, then uploads the
# schema master + mapping artifacts to the gold-artifacts bucket, then
# validates + auto-initializes (scripts/gold_config.py) and conditionally
# puts the config item (fails if another process registered it meanwhile).
#
# Usage (paths relative to the monorepo root):
#   py stacks/echo/src/apps/cli/register_gold_pipeline.py \
#     --gold-id product \
#     --domain  product \
#     --schema-dir   domains/product/src/schemas/gold/product/v1 \
#     --mapping-dir  domains/product/src/schemas/mapping/product/v1 \
#     --gold-database gold_product \
#     --gold-table    product \
#     --owner         you@pearson.com \
#     --env           dev

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

import gold_config
import gold_resources  # on sys.path via gold_config
from env_config import ENVIRONMENTS, DEFAULT_ENV

REGION = "eu-west-1"


def _to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo_safe(i) for i in obj]
    return obj


def _upload_dir(s3, bucket: str, prefix: str, local_dir: Path) -> dict:
    """Upload every file under local_dir to s3://bucket/prefix/."""
    uploaded = {}
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        key = f"{prefix.rstrip('/')}/{rel}"
        s3.upload_file(str(path), bucket, key)
        uploaded[rel] = f"s3://{bucket}/{key}"
        print(f"  uploaded  s3://{bucket}/{key}")
    return uploaded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-id",        required=True, help="Gold pipeline id (e.g. 'product')")
    ap.add_argument("--domain",         required=True, help="Domain name (e.g. 'product')")
    ap.add_argument("--schema-dir",     required=True, help="Path to schemas/gold/<domain>/v1")
    ap.add_argument("--mapping-dir",    required=True, help="Path to schemas/mapping/<domain>/v1")
    ap.add_argument("--gold-database",  required=True, help="Glue database for Gold table")
    ap.add_argument("--gold-table",     required=True, help="Glue table name for Gold table")
    ap.add_argument("--owner",          required=True, help="Owner email")
    ap.add_argument("--env",            default=DEFAULT_ENV, choices=ENVIRONMENTS)
    ap.add_argument("--config-table",   default=None,
                    help="Override the gold config table name "
                         "(default: gold_config.default_gold_config_table(env))")
    ap.add_argument("--artifacts-bucket", default=None,
                    help="Override the gold artifacts bucket (default: gold_resources.gold_artifacts_bucket(env))")
    ap.add_argument("--gold-bucket",      default=None,
                    help="Override the Iceberg warehouse bucket (default: gold_resources.gold_data_bucket(env))")
    args = ap.parse_args()

    schema_dir  = Path(args.schema_dir).resolve()
    mapping_dir = Path(args.mapping_dir).resolve()
    if not (schema_dir / "master.json").exists():
        sys.exit(f"missing {schema_dir / 'master.json'}")
    if not (mapping_dir / "manifest.yaml").exists():
        sys.exit(f"missing {mapping_dir / 'manifest.yaml'}")

    artifacts_bucket = args.artifacts_bucket or gold_resources.gold_artifacts_bucket(args.env)
    gold_bucket      = args.gold_bucket      or gold_resources.gold_data_bucket(args.env)
    table_name       = args.config_table     or gold_config.default_gold_config_table(args.env)

    manifest = yaml.safe_load((mapping_dir / "manifest.yaml").read_text())

    # Existence check FIRST — before any S3 upload. Uploading (and
    # potentially overwriting live artifacts other pipelines already read)
    # before knowing whether this gold_id is already registered risked
    # clobbering a running pipeline's artifacts just to then discover the
    # registration itself must be rejected.
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
    existing = table.get_item(
        Key={"gold_id": args.gold_id, "version": "latest"}).get("Item")
    if existing:
        sys.exit(f"[FAIL] gold pipeline '{args.gold_id}' already exists in "
                 f"{table_name} — manage it via pipelines/ + sync_pipelines.py")

    s3 = boto3.client("s3", region_name=REGION)

    print(f"[UPLOAD] {schema_dir} -> s3://{artifacts_bucket}/schemas/gold/{args.domain}/v1/")
    _upload_dir(s3, artifacts_bucket,
                f"schemas/gold/{args.domain}/v1", schema_dir)

    print(f"[UPLOAD] {mapping_dir} -> s3://{artifacts_bucket}/mappings/{args.domain}/v1/")
    _upload_dir(s3, artifacts_bucket,
                f"mappings/{args.domain}/v1", mapping_dir)

    master_s3   = f"s3://{artifacts_bucket}/schemas/gold/{args.domain}/v1/master.json"
    manifest_s3 = f"s3://{artifacts_bucket}/mappings/{args.domain}/v1/manifest.yaml"
    sql_rel     = manifest["sql"]["file"]
    sql_s3      = f"s3://{artifacts_bucket}/mappings/{args.domain}/v1/{sql_rel}"

    body = {
        "gold_id": args.gold_id,
        "domain":  args.domain,
        "enabled": True,
        "schema": {
            "master_s3_path": master_s3,
            "version":        manifest.get("version", "v1"),
        },
        "mapping": {
            "manifest_s3_path": manifest_s3,
            "sql_s3_path":      sql_s3,
        },
        "sources": [
            {
                "alias":               src["alias"],
                "silver_table":        src["silver_table"],
                "watermark_col":       src.get("watermark_col", "_silver_updated_at"),
                "is_source_of_truth":  bool(src.get("is_source_of_truth", False)),
            }
            for src in manifest["sources"]
        ],
        "target": {
            "database":         args.gold_database,
            "table":            args.gold_table,
            "s3_path":          f"s3://{gold_bucket}/{args.gold_table}/",
            "primary_key_cols": manifest["target"]["primary_key_cols"],
        },
        "glue": {
            "worker_type":     "G.2X",
            "num_workers":     16,
            "timeout_minutes": 60,
            "max_affected_fraction": 0.5,
        },
        "metadata": {
            "owner":  args.owner,
            "domain": args.domain,
        },
    }

    # Same control-plane rules the sync tool applies (ADR-0004).
    err = gold_config.validate(body)
    if err:
        sys.exit(f"[FAIL] invalid gold config: {err}")
    gold_config.auto_init(body)

    # ConditionExpression closes the race the earlier get_item-then-put_item
    # check couldn't: two concurrent `register` runs for the same gold_id
    # could both pass the get_item check above before either writes, and the
    # second put_item would then clobber the first run's just-initialized
    # watermark. attribute_not_exists(gold_id) makes the write itself atomic
    # against that.
    try:
        table.put_item(
            Item=_to_dynamo_safe(body),
            ConditionExpression="attribute_not_exists(gold_id)",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            sys.exit(f"[FAIL] gold pipeline '{args.gold_id}' was registered "
                     f"concurrently by another process — aborting to avoid "
                     f"clobbering its watermark. Manage it via "
                     f"pipelines/ + sync_pipelines.py")
        raise
    print(f"[OK] registered '{args.gold_id}' in {table_name}")
    print(json.dumps(body, indent=2, default=str))


if __name__ == "__main__":
    main()
