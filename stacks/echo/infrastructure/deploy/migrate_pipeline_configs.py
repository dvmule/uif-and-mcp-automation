#!/usr/bin/env python3
"""Migrate pipeline configs from echo-dev-glue-job-config to uif-echo-dev-pipeline-config.

Dry-run by default. Nothing is written without --apply.

  # the two pilots
  python migrate_pipeline_configs.py --pipeline-ids mdm-product-apps-mtl-parameters,tep-inventory-apps-mtl-parameters
  python migrate_pipeline_configs.py --pipeline-ids ... --apply

  # everything migratable, once the pilots are proven
  python migrate_pipeline_configs.py --all
  python migrate_pipeline_configs.py --all --apply

What it changes, and why each one matters
-----------------------------------------
silver.database      Keyed by SOURCE SYSTEM, not business domain — the standard's
                     rule and the decision of 2026-09-13. "Oracle MDM" -> _mdm,
                     "Oracle EBS" -> _tep. This is what resolves mtl_parameters
                     existing twice in the old catalog: the two copies come from
                     different source systems and now land in different databases.

target/silver s3     New buckets, and the prefix becomes {source}/{table}/ to match
                     the six configs already migrated. The domain segment is
                     dropped, as the pipeline_id convention already dropped it.

source.secret_arn    Repointed to uif/echo/dev/db/*. A Secrets Manager rename is a
                     create-and-delete, so the ARN suffix changes and the old ARN
                     cannot be pattern-rewritten — the map below is explicit.

watermarks           RESET TO EPOCH, and this is required rather than tidy. Moving
                     silver.s3_path with a live watermark in place fails SILENTLY:
                     Bronze writes to the new empty prefix, Silver's incremental
                     filter looks for _extract_date > the old watermark in a prefix
                     with no history, finds zero rows, reports SUCCEEDED — and the
                     real data sits at a path silver.s3_path no longer points to.
                     With the sentinels the first run is a clean full load.

enabled              Forced false. Run each pipeline by hand, check the row count,
                     then toggle. The new stack's schedule rules are DISABLED
                     anyway, because the old stack is still live and two enabled
                     sets would dispatch every pipeline twice.

server-side fields   metadata.last_run_at / last_run_id / last_toggled_at are
                     dropped; the controller owns them.

Attribute renames (--canonicalise-attributes) are OFF by default. The 12 Tier 3
renames require the code to read the new names, and that change has not landed.
Enabling this flag before it does will break every run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

REGION = "eu-west-1"
SOURCE_TABLE = "echo-dev-glue-job-config"
TARGET_TABLE = "uif-echo-dev-pipeline-config"

# metadata.source_system -> the Silver database suffix and the S3 prefix segment
SOURCE_SYSTEMS = {
    "Oracle MDM": "mdm",
    "Oracle EBS": "tep",
}

BRONZE_BUCKET = "uif-echo-dev-bronze"
SILVER_BUCKET = "uif-echo-dev-silver"
SILVER_DB = "uif_echo_dev_silver_{suffix}"

# Old secret name -> new ARN. Explicit because the ARN suffix is regenerated.
SECRET_ARNS = {
    "echo/dev/gip/db/mdm": "arn:aws:secretsmanager:eu-west-1:283556840805:secret:uif/echo/dev/db/mdm-2wurYI",
    "echo/dev/gip/db/tep": "arn:aws:secretsmanager:eu-west-1:283556840805:secret:uif/echo/dev/db/tep-115Osh",
}

EPOCH_TS = "1970-01-01T00:00:00"
EPOCH_DATE = "1970-01-01"

# Frequencies an EventBridge rule actually dispatches. A config carrying anything
# else validates, stores, and then never runs — which is how "12hr" reached
# eight live configs.
DISPATCHABLE = {
    "30min", "hourly", "4hr", "daily", "weekly",
    "reconcile", "gold", "gold-hourly", "gold-reconcile",
}

# Tier 3 attribute renames. timeout_minutes is deliberately absent — accepted
# as the unit form on 2026-09-13. version is exempt: it is the table's RANGE key.
ATTRIBUTE_RENAMES = {
    "num_workers": "worker_count",
    "jdbc_partitions": "jdbc_partition_count",
    "jdbc_fetchsize": "jdbc_fetch_count",
    "max_retries": "max_retry_count",
    "max_deletes_per_run": "max_delete_count",
    "pk_partitions": "pk_partition_count",
    "min_rows_expected": "min_row_count",
    "jdbc_extra_options": "jdbc_extra_option",
    "draft_notes": "draft_note",
}

DROP_SERVER_SIDE = ("last_run_at", "last_run_id", "last_toggled_at")


# ── helpers ───────────────────────────────────────────────────────

def scan_all(table):
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def colliding_ids(items):
    """pipeline_ids whose silver.table clashes inside its target database.

    Not migrated: per the decision of 2026-09-13 these are documented and left
    for a later call. They are old-convention / new-convention registrations of
    the same Oracle entity (mdm-product-* beside mdm-apps-*), so resolving them
    means choosing one, not merging two.
    """
    buckets: dict[tuple[str, str], list[str]] = {}
    for it in items:
        if (it.get("destination") or {}).get("cloud") != "aws":
            continue
        suffix = SOURCE_SYSTEMS.get((it.get("metadata") or {}).get("source_system"))
        table = (it.get("silver") or {}).get("table")
        if not suffix or not table:
            continue
        buckets.setdefault((suffix, table), []).append(it["pipeline_id"])
    out = set()
    for group in buckets.values():
        if len(group) > 1:
            out.update(group)
    return out


def rename_attributes(node):
    if isinstance(node, dict):
        return {
            ATTRIBUTE_RENAMES.get(k, k): rename_attributes(v)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [rename_attributes(v) for v in node]
    return node


def transform(item, *, canonicalise):
    """Return (new_item, [changes], [problems])."""
    changes, problems = [], []
    new = json.loads(json.dumps(item, default=str))
    pid = new["pipeline_id"]

    ss = (new.get("metadata") or {}).get("source_system")
    cloud = (new.get("destination") or {}).get("cloud")
    suffix = SOURCE_SYSTEMS.get(ss)

    if cloud == "aws" and not suffix:
        problems.append(f"source_system {ss!r} maps to no Silver database")
        return new, changes, problems

    def note(field, before, after):
        if before != after:
            changes.append((field, before, after))

    # ── Silver catalog + object storage ───────────────────────────
    if cloud == "aws":
        silver = new.setdefault("silver", {})
        table = silver.get("table")
        if not table:
            problems.append("silver.table is missing; cannot build a prefix")
        else:
            before = silver.get("database")
            silver["database"] = SILVER_DB.format(suffix=suffix)
            note("silver.database", before, silver["database"])

            before = silver.get("s3_path")
            silver["s3_path"] = f"s3://{SILVER_BUCKET}/{suffix}/{table}/"
            note("silver.s3_path", before, silver["s3_path"])

            target = new.setdefault("target", {})
            before = target.get("s3_path")
            target["s3_path"] = f"s3://{BRONZE_BUCKET}/{suffix}/{table}/"
            note("target.s3_path", before, target["s3_path"])
    else:
        # GCP pipelines move unchanged — their GCS buckets and BQ datasets are
        # out of scope for the uif- rename (ADR-0015 D6). Only the Oracle source
        # secret below is repointed, because Bronze still reads Oracle.
        changes.append(("(gcp)", "destination untouched", "destination untouched"))

    # ── Oracle source secret ──────────────────────────────────────
    src = new.setdefault("source", {})
    arn = src.get("secret_arn") or ""
    for old_name, new_arn in SECRET_ARNS.items():
        if f":secret:{old_name}-" in arn or arn.endswith(f":secret:{old_name}"):
            src["secret_arn"] = new_arn
            note("source.secret_arn", arn, new_arn)
            break
    else:
        name = arn.split(":secret:")[-1]
        # oracle-* and gcp-* secrets are outside the rename entirely: the
        # standard's slash form covers this stack's own secrets, and iam.tf
        # grants secret:oracle-* and secret:gcp-* directly. Eleven configs use
        # oracle-ppdebsdbsl001, which has no uif/echo/dev/db/* equivalent and
        # needs none.
        if arn and not name.startswith(("uif/echo/", "oracle-", "gcp-")):
            problems.append(f"source.secret_arn {name!r} has no mapping")
        elif name.startswith(("oracle-", "gcp-")):
            changes.append(("source.secret_arn", name, f"{name}  (outside the rename)"))

    # ── watermarks: a silent-failure guard, not housekeeping ──────
    extract = new.setdefault("extract", {})
    if "watermark_value" in extract:
        note("extract.watermark_value", extract["watermark_value"], EPOCH_TS)
        extract["watermark_value"] = EPOCH_TS
    if cloud == "aws" and "last_processed_date" in new.get("silver", {}):
        note("silver.last_processed_date", new["silver"]["last_processed_date"], EPOCH_DATE)
        new["silver"]["last_processed_date"] = EPOCH_DATE

    # ── disabled until verified by hand ───────────────────────────
    note("enabled", new.get("enabled"), False)
    new["enabled"] = False

    # ── server-side fields belong to the controller ───────────────
    meta = new.setdefault("metadata", {})
    for field in DROP_SERVER_SIDE:
        if field in meta:
            changes.append((f"metadata.{field}", meta[field], "(dropped)"))
            del meta[field]

    meta["draft_note" if canonicalise else "draft_notes"] = (
        f"Migrated from {SOURCE_TABLE} to {TARGET_TABLE} by "
        f"migrate_pipeline_configs.py under UIFPE-355. source_system {ss!r} -> "
        f"Silver database {SILVER_DB.format(suffix=suffix) if suffix else 'n/a'}. "
        f"Bronze and Silver prefixes moved to {suffix}/{{table}}/ on the new "
        f"buckets, matching the six configs migrated earlier. Watermarks reset to "
        f"epoch sentinels and enabled=false: the first run must be a full load at "
        f"the new location, because carrying a live watermark across a path change "
        f"makes Silver read an empty prefix, find nothing and report SUCCEEDED. "
        f"Run Bronze by hand, check the row count against the old stack, run "
        f"Silver, verify, then toggle."
    )

    # ── frequency has to be one a rule actually sends ─────────────
    freq = (new.get("schedule") or {}).get("frequency")
    if freq and freq not in DISPATCHABLE:
        problems.append(
            f"schedule.frequency {freq!r} is dispatched by no EventBridge rule — "
            f"this config would migrate and then never run"
        )

    if canonicalise:
        new = rename_attributes(new)
        changes.append(("(attributes)", "legacy names", "canonical names"))

    new["version"] = "latest"
    return new, changes, problems


# ── main ──────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pipeline-ids", help="comma-separated pipeline_ids")
    g.add_argument("--all", action="store_true", help="every migratable config")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--canonicalise-attributes", action="store_true",
                    help="apply the 12 Tier 3 renames — requires the code rename to have landed")
    ap.add_argument("--allow-collision", action="store_true",
                    help="migrate a pipeline whose silver.table clashes in its target database")
    ap.add_argument("--out", help="write the transformed items to this JSON file")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb", region_name=REGION)
    source = scan_all(dynamo.Table(SOURCE_TABLE))
    by_id = {i["pipeline_id"]: i for i in source if i.get("version") == "latest"}
    collide = colliding_ids(list(by_id.values()))

    if args.pipeline_ids:
        wanted = [p.strip() for p in args.pipeline_ids.split(",") if p.strip()]
        missing = [p for p in wanted if p not in by_id]
        if missing:
            sys.exit(f"not in {SOURCE_TABLE}: {', '.join(missing)}")
    else:
        wanted = sorted(by_id)

    print(f"source     {SOURCE_TABLE}  ({len(by_id)} configs at version=latest)")
    print(f"target     {TARGET_TABLE}")
    print(f"mode       {'APPLY — writes' if args.apply else 'DRY RUN — no writes'}")
    print(f"attributes {'CANONICAL' if args.canonicalise_attributes else 'legacy (code has not been renamed)'}")
    print(f"colliding  {len(collide)} configs, excluded unless --allow-collision")
    print()

    ok, skipped, blocked = [], [], []
    for pid in wanted:
        item = by_id[pid]
        if pid in collide and not args.allow_collision:
            skipped.append(pid)
            continue
        new, changes, problems = transform(item, canonicalise=args.canonicalise_attributes)
        head = f"{'=' * 78}\n{pid}"
        print(head)
        for field, before, after in changes:
            b = str(before)
            a = str(after)
            if len(b) > 64:
                b = b[:61] + "..."
            if len(a) > 64:
                a = a[:61] + "..."
            print(f"    {field:<30} {b}\n    {'':<30} -> {a}")
        for p in problems:
            print(f"    PROBLEM  {p}")
        print()
        (blocked if problems else ok).append((pid, new))

    print("=" * 78)
    print(f"migratable {len(ok)}   blocked {len(blocked)}   skipped as colliding {len(skipped)}")
    if skipped:
        print("\nskipped (documented, not migrated):")
        for pid in skipped:
            print(f"    {pid}")
    if blocked:
        print("\nblocked — fix the problem above, then re-run:")
        for pid, _ in blocked:
            print(f"    {pid}")

    if args.out:
        Path(args.out).write_text(
            json.dumps({pid: new for pid, new in ok}, indent=2, default=str),
            encoding="utf-8")
        print(f"\ntransformed items written to {args.out}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    if blocked:
        sys.exit("refusing to apply while any config is blocked")

    target = dynamo.Table(TARGET_TABLE)
    with target.batch_writer() as batch:
        for pid, new in ok:
            batch.put_item(Item=new)
    print(f"\nwrote {len(ok)} configs to {TARGET_TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
