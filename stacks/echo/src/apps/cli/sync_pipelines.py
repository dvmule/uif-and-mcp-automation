#!/usr/bin/env python3
# sync_pipelines.py
# Config-as-code control plane for Gold pipelines (ADR-0004).
#
# Reads pipelines/base/{gold_id}.yaml deep-merged with
# pipelines/overlays/{env}/{gold_id}.yaml, validates + auto-initializes
# (scripts/gold_config.py), then plans/applies against the DynamoDB gold
# config table.
#
# Safety properties:
#   - Plan by default; writes require --apply (repo rule: destructive/state-
#     changing operations need an explicit flag).
#   - RUNTIME-OWNED fields are always preserved from the existing item and
#     never overwritten by config: watermarks, channel registry,
#     materialized_properties, publish_cursors, reconcile run stats, and
#     registration/run metadata. The transform owns those. The CONSOLE owns
#     `enabled` and `schedule.start_time` (UIFPE-513); a sync never rolls
#     them back either. Cadence (schedule.frequency) stays Git-owned.
#   - Pipelines present in DynamoDB but absent from Git are REPORTED, never
#     deleted (deletion is a deliberate manual act).
#
# Usage (from the monorepo root):
#   py stacks/echo/src/apps/cli/sync_pipelines.py --env dev          # plan only
#   py stacks/echo/src/apps/cli/sync_pipelines.py --env dev --apply  # write changes
#
# REPO_ROOT below resolves to domains/product/src/apps/ — pipelines/ and
# environments.json sit alongside this scripts/ dir, so the relative paths in
# the comments above and in load_desired() are unchanged by the migration.

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

from env_config import ENVIRONMENTS, DEFAULT_ENV
import gold_config

REGION = "eu-west-1"
# The domain whose pipelines/ to sync. Product is the default only because its
# deploy predates --repo-root; every other domain passes it explicitly.
REPO_ROOT = Path(__file__).resolve().parents[5] / "domains" / "product" / "src" / "apps"

# ── Gold-database Lake Formation tag ────────────────────────────────────────
# Consumers' grants (Beacon, the marketing-copy sync, the grain probe, the
# shared Glue role) are tag-based, so every sync ensures every managed
# pipeline's gold database carries the layer tag for its environment. The
# database is created by the transform's first run, so a just-registered
# pipeline reports "not created yet" and is tagged on the next sync. Tag
# problems WARN and never fail the sync: an untagged database blocks only the
# consuming stack's Terraform, which refuses with its own readable error.
from gold_resources import GOLD_LAYER_TAG_KEY  # noqa: E402

# Dotted paths owned by the runtime (transform/reconcile jobs) — carried
# over from the existing item on every sync, regardless of Git content.
RUNTIME_PATHS = [
    "watermark",
    "channels.registered",
    "materialized_properties",
    "publish_cursors",
    "reconcile.last_reconciled_at",
    "reconcile.last_orphans_deleted",
    "metadata.registered_at",
    "metadata.last_run_id",
    "metadata.last_run_at",
    "metadata.last_transform_at",
    # Console-owned: set from the Syncs page, so YAML must not overwrite them.
    # UIFPE-564 (owner decision 2026-09-24) added schedule.frequency, which had
    # been deliberately Git-owned: an operator now changes a table's cadence
    # from the console, and the YAML value is the seed for a NEW entity only.
    # A domain team changing the cadence in YAML for an entity that already
    # exists will therefore see no effect - change it in the console.
    "schedule.frequency",
    "schedule.start_time",
    # UIFPE-532: the weekly rung's day set is set from the same dialog.
    "schedule.weekly_days",
    "enabled",
]


def _get_path(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _set_path(d: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def preserve_runtime_fields(desired: dict, existing: dict) -> dict:
    """Copy runtime-owned values from the existing item into the desired
    config so a sync never rolls back watermarks or registries."""
    for path in RUNTIME_PATHS:
        val, found = _get_path(existing, path)
        if found:
            _set_path(desired, path, val)
    return desired


def to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dynamo_safe(i) for i in obj]
    return obj


def from_dynamo(obj):
    """Decimal → int/float for stable comparison against YAML-loaded config."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_dynamo(i) for i in obj]
    return obj


def load_desired(env: str, repo_root: Path = REPO_ROOT) -> dict:
    """{gold_id: merged+validated+initialized config} from pipelines/."""
    base_dir = repo_root / "pipelines" / "base"
    overlay_dir = repo_root / "pipelines" / "overlays" / env
    desired = {}
    for base_path in sorted(base_dir.glob("*.yaml")):
        gold_id = base_path.stem
        cfg = yaml.safe_load(base_path.read_text()) or {}
        overlay_path = overlay_dir / base_path.name
        if overlay_path.exists():
            cfg = gold_config.deep_merge(
                cfg, yaml.safe_load(overlay_path.read_text()) or {})
        else:
            print(f"[WARN] no {env} overlay for '{gold_id}' "
                  f"({overlay_path}) — skipping this pipeline for {env}")
            continue
        err = gold_config.validate(cfg)
        if err:
            sys.exit(f"[FAIL] {gold_id}: {err}")
        if cfg["gold_id"] != gold_id:
            sys.exit(f"[FAIL] {base_path.name}: gold_id '{cfg['gold_id']}' "
                     f"must match filename stem '{gold_id}'")
        desired[gold_id] = gold_config.auto_init(cfg)
    return desired


def _aws_error_code(e: Exception) -> str:
    if isinstance(e, ClientError):
        return e.response.get("Error", {}).get("Code") or type(e).__name__
    return type(e).__name__


def ensure_gold_db_tag(database: str, env: str, apply: bool, lf, glue) -> str:
    """Ensure one gold database carries the LF layer tag; returns the plan
    line. Never raises — see the module note on why tag problems WARN. The
    outer except is deliberately broad: a network error here must not block
    the config writes this script exists to make."""
    try:
        try:
            glue.get_database(Name=database)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") \
                    == "EntityNotFoundException":
                return (f"SKIP      {database}  (not created yet — the first "
                        "transform run creates it; tagged on a later sync)")
            raise
        got = lf.get_resource_lf_tags(Resource={"Database": {"Name": database}})
        for t in got.get("LFTagOnDatabase") or []:
            if t.get("TagKey") == GOLD_LAYER_TAG_KEY \
                    and env in (t.get("TagValues") or []):
                return f"OK        {database}  (already tagged)"
        if not apply:
            return f"TAG       {database}  (would tag on --apply)"
        resp = lf.add_lf_tags_to_resource(
            Resource={"Database": {"Name": database}},
            LFTags=[{"TagKey": GOLD_LAYER_TAG_KEY, "TagValues": [env]}])
        failures = resp.get("Failures") or []
        if failures:
            return (f"WARN      {database}  tagging failed ({failures}) — "
                    "consuming stacks' gold grants refuse until tagged")
        return f"TAGGED    {database}"
    except Exception as e:
        # tag key absent in this account (uat/prod pre-setup), AccessDenied,
        # LF endpoint unreachable, …
        return (f"WARN      {database}  {GOLD_LAYER_TAG_KEY} tag check failed "
                f"({_aws_error_code(e)}) — one-time account setup or "
                "deploy-role permission missing; see "
                "capabilities/data/gold-control-plane.md")


def diff_keys(desired: dict, existing: dict) -> list:
    """Top-level keys whose values differ (order-insensitive JSON compare)."""
    keys = set(desired) | set(existing)
    changed = []
    for k in sorted(keys):
        if json.dumps(desired.get(k), sort_keys=True, default=str) != \
           json.dumps(existing.get(k), sort_keys=True, default=str):
            changed.append(k)
    return changed


def main():
    ap = argparse.ArgumentParser(description="Sync Git gold configs to DynamoDB")
    ap.add_argument("--env", default=DEFAULT_ENV, choices=ENVIRONMENTS)
    ap.add_argument("--config-table", default=None,
                    help="Override the gold config table name "
                         "(default: gold_config.default_gold_config_table(env))")
    ap.add_argument("--apply", action="store_true",
                    help="Write changes (default is plan-only)")
    # Explicit override for the repo root used to locate pipelines/; default
    # matches the previous hardcoded parents[1] behavior unchanged.
    ap.add_argument("--repo-root", default=None,
                    help="Override the repo root used to locate pipelines/ "
                         "(default: parent of this script's directory)")
    args = ap.parse_args()

    # The plan output below uses box-drawing rules and em dashes, which the
    # Windows console (cp1252) cannot encode — the UnicodeEncodeError landed on
    # the plan header, i.e. AFTER the scan but BEFORE any put_item, so --apply
    # aborted with a traceback and wrote nothing. Never let formatting decide
    # whether a sync happens.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    table_name = args.config_table or gold_config.default_gold_config_table(args.env)
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)

    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT
    desired_all = load_desired(args.env, repo_root)
    if not desired_all:
        sys.exit("[FAIL] no pipeline definitions found under pipelines/base/")

    plan = {"create": [], "update": [], "unchanged": [], "unmanaged": []}
    writes = []

    existing_items = {}
    scan_kwargs = {}
    while True:
        scan = table.scan(**scan_kwargs)
        for i in scan.get("Items", []):
            if i.get("version") == "latest":
                existing_items[i["gold_id"]] = from_dynamo(i)
        if "LastEvaluatedKey" not in scan:
            break
        scan_kwargs["ExclusiveStartKey"] = scan["LastEvaluatedKey"]
    existing_ids = set(existing_items)

    for gold_id, desired in desired_all.items():
        existing = existing_items.get(gold_id)
        if existing is None:
            plan["create"].append(gold_id)
            writes.append(desired)
            continue
        desired = preserve_runtime_fields(desired, existing)
        changed = diff_keys(desired, existing)
        if changed:
            # Gate reset (F1): a config change can alter what a run would
            # produce (mapping paths, sources, schema version) with NO Silver
            # commit — the snapshot gate would otherwise defer it until
            # unrelated Silver activity. Dropping the stored map forces
            # exactly one ungated dispatch; the transform re-captures it.
            # This is the one deliberate exception to "sync never touches
            # runtime fields", and it errs in the safe direction (an extra
            # run the hash gate no-ops), never toward a wrong skip.
            (desired.get("watermark") or {}).pop("silver_snapshots", None)
            plan["update"].append(f"{gold_id}  (fields: {', '.join(changed)})")
            writes.append(desired)
        else:
            plan["unchanged"].append(gold_id)

    plan["unmanaged"] = sorted(existing_ids - set(desired_all))

    print(f"\n═══ Gold pipeline sync plan — env={args.env} table={table_name} ═══")
    for action in ("create", "update", "unchanged"):
        for entry in plan[action]:
            print(f"  {action.upper():9} {entry}")
    for gold_id in plan["unmanaged"]:
        print(f"  UNMANAGED {gold_id}  (in DynamoDB, not in Git — NOT touched; "
              f"delete manually if intended)")

    # Gold-database LF tag — every managed pipeline, every sync (including
    # unchanged ones: a database created since the last sync needs its tag).
    lf = boto3.client("lakeformation", region_name=REGION)
    glue = boto3.client("glue", region_name=REGION)
    print(f"\n  Gold-database Lake Formation tag "
          f"({GOLD_LAYER_TAG_KEY}={args.env}):")
    for cfg in desired_all.values():
        db = ((cfg.get("target") or {}).get("database") or "") \
            .replace("{env}", args.env)
        if db:
            print(f"  {ensure_gold_db_tag(db, args.env, args.apply, lf, glue)}")

    if not writes:
        print("\nNothing to do.")
        return

    if not args.apply:
        print(f"\nPlan only — {len(writes)} write(s) pending. "
              f"Re-run with --apply to write.")
        return

    for cfg in writes:
        table.put_item(Item=to_dynamo_safe(cfg))
        print(f"[WRITTEN] {cfg['gold_id']}")
    print(f"\nApplied {len(writes)} write(s) to {table_name}.")


if __name__ == "__main__":
    main()
