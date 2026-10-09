# generic_gold_reconcile.py
# Nightly hard-delete reconciliation job for Gold Iceberg tables.
# Anti-joins Silver source-of-truth PKs against the Gold table to identify
# orphan rows (rows whose source has been deleted from Silver), and deletes
# them with safety caps and dry-run support.
#
# Source of truth = the Silver table marked `is_source_of_truth: true` in the
# mapping manifest. For Product domain that's mtl_system_items_b. The natural
# keys used for the anti-join come from target.primary_key_cols in the gold
# config, mapped back to source columns via the SQL conventions in the mapping
# (see ADR-7).
#
# Mirrors generic_silver_reconcile.py — see that file for the original pattern.
#
# Job arguments (beyond Glue's own JOB_NAME / JOB_RUN_ID):
#   --gold_id              (required) pipeline id in the gold config table
#   --env                  (required) environment token; NO default — see #15
#   --dry-run              "true"/"false" string, not a bare flag — see #11
#   --orphan_grace_hours   never delete rows Gold touched this recently
#                          (default 1; per-pipeline override:
#                          reconcile.orphan_grace_hours) — see #1b

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import boto3
import yaml

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext

import gold_cdc  # --extra-py-files on Glue; src/apps/pipeline on sys.path in tests
import gold_telemetry  # UIFPE-543: EMF documents, delivered like gold_cdc

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
# No default for --env: silently falling back to "dev" would point a prod run
# at the dev Glue databases (review finding #15). Terraform sets --env in the
# job's default_arguments, so a missing value means a broken deployment and
# must fail loudly.
_p.add_argument("--env",        default=None)
# String, NOT action="store_true" (review finding #11): Glue passes
# `--dry-run false`, which a store_true flag would read as "enabled".
_p.add_argument("--dry-run",    default="false")
# Safety window for orphan detection (review finding #1b).
_p.add_argument("--orphan_grace_hours", default="1")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(
    sys.argv, ["gold_id", "CONFIG_TABLE", "METRICS_TABLE", "GOLD_WAREHOUSE"]
)
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
GOLD_ID     = args["gold_id"]
if not (_glue_defaults.env or "").strip():
    raise ValueError(
        "--env is required and was not supplied (or was empty). Refusing to "
        "default to 'dev': the value selects the Gold/Silver Glue databases, "
        "so guessing it would silently reconcile the wrong environment. "
        "Terraform sets it in the Glue job's default_arguments — check the "
        "job definition."
    )
ENV         = _glue_defaults.env.strip()
DRY_RUN     = gold_cdc.parse_bool_arg(_glue_defaults.dry_run, "dry-run")
ORPHAN_GRACE_HOURS = float(_glue_defaults.orphan_grace_hours)
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

# Iceberg warehouse root, passed by Terraform from the stack's own bucket
# variable. REQUIRED, not defaulted: a derived s3://echo-{env}-... value
# silently writes this stack's tables into the other stack's bucket, which
# is what it was doing before UIFPE-355 phase 2 was completed.
GOLD_WAREHOUSE = args["GOLD_WAREHOUSE"]

dynamo     = boto3.resource("dynamodb", region_name=REGION)
s3         = boto3.client("s3", region_name=REGION)
started_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────
def to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dynamo_safe(i) for i in obj]
    return obj


def load_config() -> dict:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"gold_id": GOLD_ID, "version": "latest"}
    )
    if "Item" not in resp:
        raise ValueError(f"No gold config found for gold_id={GOLD_ID} version=latest")
    return resp["Item"]


def s3_read_text(s3_uri: str) -> str:
    p = urlparse(s3_uri)
    if p.scheme != "s3":
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    obj = s3.get_object(Bucket=p.netloc, Key=p.path.lstrip("/"))
    return obj["Body"].read().decode("utf-8")


def _write_skipped_metrics(reason: str = ""):
    completed_at = datetime.now(timezone.utc)
    status_msg = f"SKIPPED ({reason})" if reason else "SKIPPED"
    dynamo.Table(METRICS_TABLE).put_item(Item={
        "job_name":     JOB_NAME,
        "run_id":       RUN_ID,
        "gold_id":      GOLD_ID,
        "status":       status_msg,
        "started_at":   started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_sec": 0,
        "ttl":          int(time.time()) + (90 * 86400),
    })
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, GOLD_ID, status_msg, job_name=JOB_NAME, run_id=RUN_ID,
        now=completed_at))


def write_metrics(run_status: str, orphan_count: int = 0, rows_deleted: int = 0, error: str = None):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":      JOB_NAME,
        "run_id":        RUN_ID,
        "gold_id":       GOLD_ID,
        "status":        run_status,
        "orphan_count":  orphan_count,
        "rows_deleted":  rows_deleted,
        "error_message": error,
        "started_at":    started_at.isoformat(),
        "completed_at":  completed_at.isoformat(),
        "duration_sec":  int((completed_at - started_at).total_seconds()),
        "ttl":           int(time.time()) + (90 * 86400),
    }
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, GOLD_ID, run_status, job_name=JOB_NAME, run_id=RUN_ID,
        rows_written=rows_deleted, affected_keys=orphan_count,
        duration_seconds=item["duration_sec"], error=error, now=completed_at))
    print(
        f"[METRICS WRITTEN] status={run_status} orphans={orphan_count} "
        f"rows_deleted={rows_deleted} duration={item['duration_sec']}s"
    )


def build_pk_conditions(primary_key_cols, alias_left="t", alias_right="o") -> str:
    # physical key names on both sides (N8) — one definition, in gold_cdc
    return gold_cdc.build_pk_conditions(primary_key_cols, alias_left, alias_right)


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def run():
    status        = "FAILED"
    error_msg     = None
    orphan_count  = 0
    rows_deleted  = 0
    spark         = None
    cached_dfs    = []

    cfg = load_config()
    print(f"[CONFIG LOADED] gold_id={GOLD_ID}")

    if not cfg.get("enabled", True):
        print(f"[SKIPPED] gold pipeline {GOLD_ID} is disabled")
        _write_skipped_metrics("disabled")
        return

    rec_cfg = cfg.get("reconcile", {})
    if not rec_cfg.get("enabled", False):
        print(f"[SKIPPED] reconciliation not enabled for {GOLD_ID}")
        _write_skipped_metrics("reconcile disabled")
        return

    target_cfg          = cfg["target"]
    mapping_cfg         = cfg["mapping"]
    primary_key_cols    = target_cfg["primary_key_cols"]
    gold_db             = gold_cdc.resolve_env(target_cfg["database"], ENV)
    gold_tbl            = target_cfg["table"]
    max_deletes_per_run = int(rec_cfg.get("max_deletes_per_run", 5000))
    # Per-pipeline override in the config wins (same precedence as
    # max_deletes_per_run); otherwise the --orphan_grace_hours job arg.
    grace_hours = float(rec_cfg.get("orphan_grace_hours", ORPHAN_GRACE_HOURS))

    # ─── Identify the Silver source-of-truth table from the manifest ─
    manifest      = yaml.safe_load(s3_read_text(mapping_cfg["manifest_s3_path"]))
    sot_sources   = [s for s in manifest["sources"] if s.get("is_source_of_truth")]
    if len(sot_sources) != 1:
        raise ValueError(
            f"mapping manifest for {GOLD_ID} must declare exactly one "
            f"source with is_source_of_truth: true (found {len(sot_sources)})"
        )
    sot              = sot_sources[0]
    # {env} token → env-qualified Glue database (uif_echo_dev_silver_mdm)
    silver_sot_table = gold_cdc.resolve_env(sot["silver_table"], ENV)
    sot_pk_mapping   = sot.get("sot_pk_mapping") or {}
    missing_pk_maps  = [c for c in primary_key_cols if c not in sot_pk_mapping]
    if missing_pk_maps:
        raise ValueError(
            f"mapping manifest for {GOLD_ID} is missing sot_pk_mapping entries "
            f"for primary key cols: {missing_pk_maps}. Add them under the "
            f"is_source_of_truth source."
        )
    print(f"[SOURCE OF TRUTH] {silver_sot_table} pk_mapping={sot_pk_mapping}")

    # ─── Init Spark + Iceberg ─────────────────────────────────────
    sc      = SparkContext()
    glueCtx = GlueContext(sc)
    spark   = glueCtx.spark_session
    job     = Job(glueCtx)
    job.init(JOB_NAME, args)

    spark.sparkContext.setLogLevel("WARN")
    glue_cfg = cfg.get("glue", {})
    spark.conf.set("spark.sql.shuffle.partitions",
                   str(glue_cfg.get("shuffle_partitions", 8)))
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)

    spark.conf.set("spark.sql.catalog.glue_catalog",
        "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl",
        "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl",
        "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse",
        GOLD_WAREHOUSE)

    print("[SPARK + ICEBERG INITIALISED]")

    try:
        gold_table_full = f"glue_catalog.`{gold_db}`.`{gold_tbl}`"
        sot_table_full  = gold_cdc.qualify_table(silver_sot_table)

        # ─── Read Gold PKs FIRST (review finding #1a) ────────────
        # READ ORDER IS LOAD-BEARING. Orphan = "in Gold, not in Silver", so
        # the candidate set is exactly the Gold snapshot. Whichever side is
        # read first fixes the candidate set, and the two orderings fail very
        # differently:
        #
        #   Gold first, then Silver (this order):
        #     - a row landing in GOLD after the Gold read is not in the
        #       candidate set at all, so it cannot be misclassified;
        #     - a row DELETED from Silver between the two reads is absent
        #       from the Silver snapshot but also absent from the Gold
        #       candidates only if it was never in Gold — if it was, it is a
        #       genuine orphan and deleting it is correct;
        #     - a row INSERTED into Silver between the reads is seen by the
        #       Silver read, so any matching Gold candidate is protected.
        #     Worst case: a Silver delete that lands just after the Silver
        #     read is simply caught by the next nightly cycle. Latency, not
        #     data loss.
        #
        #   Silver first, then Gold (the previous order — the bug):
        #     a row written to Gold by a concurrent transform between the two
        #     reads is absent from the Silver snapshot but PRESENT in the Gold
        #     candidates, so it is classified as an orphan and hard-deleted
        #     immediately after it was correctly materialised.
        #
        # `_gold_updated_at` is selected alongside the keys for the grace
        # window below. It is added to pre-ADR-0003 tables by the transform's
        # metadata migration (D12), so tolerate its absence.
        gold_cols = set(spark.table(gold_table_full).columns)
        has_updated_at = "_gold_updated_at" in gold_cols
        gold_select = [f"`{gold_cdc.physical_name(c)}`" for c in primary_key_cols]
        if has_updated_at:
            gold_select.append("`_gold_updated_at`")
        else:
            print("[WARN] gold table has no `_gold_updated_at` column — "
                  "orphan grace window cannot be applied this run; it starts "
                  "working once the transform's metadata migration has run")
        gold_pks = spark.sql(
            f"SELECT {', '.join(gold_select)} FROM {gold_table_full}"
        )
        gold_pks.cache()
        cached_dfs.append(gold_pks)
        # The cache() + count() pair is what makes the ordering real: Spark is
        # lazy, so without an action here the Gold scan would not run until the
        # anti-join — i.e. AFTER the Silver read — and the swap above would buy
        # nothing. Do not remove the count().
        gold_count = gold_pks.count()
        print(f"[GOLD PKs] count={gold_count}")

        # ─── THEN read Silver source-of-truth PKs (mapped to Gold PK names) ─
        # The mapping manifest declares how each Gold PK column projects from
        # the SoT Silver table (see ADR-7, sot_pk_mapping). Reconcile applies
        # the same projection here so the anti-join keys line up.
        # sot_filter (manifest) scopes the anti-join to the table's domain —
        # without it, a row reclassified out of the domain (e.g. SEGMENT1
        # A -> P) is never seen as an orphan and never deleted.
        sot_sql = gold_cdc.build_sot_pk_sql(
            sot_table_full, sot_pk_mapping, primary_key_cols,
            sot.get("sot_filter"))
        print(f"[SOT SQL] {sot_sql[:200]}")
        sot_pks = spark.sql(sot_sql)
        sot_pks.cache()
        cached_dfs.append(sot_pks)
        sot_count = sot_pks.count()
        print(f"[SOT PKs] count={sot_count}")

        # ─── Anti-join: gold rows not in SoT ─────────────────────
        sot_pks.createOrReplaceTempView("sot_pks")
        gold_pks.createOrReplaceTempView("gold_pks")

        candidates = spark.sql(f"""
            SELECT i.* FROM gold_pks i
            LEFT ANTI JOIN sot_pks o ON {build_pk_conditions(primary_key_cols, "i", "o")}
        """)
        candidates.cache()
        cached_dfs.append(candidates)
        candidate_count = candidates.count()
        print(f"[ORPHAN CANDIDATES] count={candidate_count}")

        # ─── Grace window (review finding #1b) ───────────────────
        # Belt-and-braces on top of the read order: never delete a row Gold
        # itself touched within the last `grace_hours`. A genuine orphan just
        # waits one more cycle; a row racing with a concurrent transform is
        # protected outright.
        if has_updated_at and grace_hours > 0:
            cutoff = gold_cdc.orphan_grace_cutoff(started_at, grace_hours)
            gold_cdc.validate_sql_literal(cutoff, "timestamp", "grace cutoff")
            orphans = candidates.filter(
                f"`_gold_updated_at` IS NULL "
                f"OR `_gold_updated_at` < TIMESTAMP '{cutoff}'"
            )
            orphans.cache()
            cached_dfs.append(orphans)
            orphan_count = orphans.count()
            print(f"[ORPHAN GRACE WINDOW] grace_hours={grace_hours} "
                  f"cutoff={cutoff} held_back={candidate_count - orphan_count} "
                  f"deletable={orphan_count}")
        else:
            orphans = candidates
            orphan_count = candidate_count

        print(f"[ORPHANS FOUND] count={orphan_count}")

        # ─── Safety cap check ────────────────────────────────────
        # Applied to the POST-grace-window count — i.e. exactly the set that
        # would be deleted — so the cap still bounds the blast radius of this
        # run. Rows held back by the grace window are not deletable this run
        # and must not push a run over the cap.
        if orphan_count > max_deletes_per_run:
            error_msg = (
                f"Orphan count ({orphan_count} deletable of "
                f"{candidate_count} candidates) exceeds safety cap "
                f"({max_deletes_per_run}). Aborting to prevent accidental "
                f"mass-delete. Increase max_deletes_per_run if intentional."
            )
            print(f"[SAFETY CAP EXCEEDED] {error_msg}")
            raise RuntimeError(error_msg)

        # ─── Dry-run mode ────────────────────────────────────────
        if DRY_RUN or rec_cfg.get("dry_run", False):
            print(f"[DRY RUN] found {orphan_count} orphans — skipping deletion")
            status = "SKIPPED"
            _write_skipped_metrics(f"dry-run (orphans={orphan_count})")
            job.commit()
            return

        # ─── Delete orphans via MERGE ────────────────────────────
        if orphan_count > 0:
            orphans.createOrReplaceTempView("orphans")

            # ADR-0003 D5/D6: append DELETE verdicts to the change log
            # BEFORE the delete MERGE (at-least-once, never silent loss).
            # The transform job owns the table's DDL; if it doesn't exist
            # yet (no change has ever landed) there is nothing downstream to
            # keep in step, so warn and continue.
            changes_table = gold_cdc.changes_table_name(gold_tbl)
            changes_full = f"glue_catalog.`{gold_db}`.`{changes_table}`"
            registered = cfg.get("channels", {}).get("registered", [])
            channels = sorted(set(registered) | {gold_cdc.CANONICAL})
            ch_array = ", ".join(
                f"'{c}'" for c in
                gold_cdc.validate_sql_literals(channels, "channel"))
            pk_select = ", ".join(f"`{gold_cdc.physical_name(c)}`" for c in primary_key_cols)
            run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            gold_cdc.validate_sql_literal(run_ts, "timestamp", "run_ts")
            gold_cdc.validate_sql_literal(RUN_ID, "run_id", "JOB_RUN_ID")
            try:
                spark.sql(f"""
                    INSERT INTO {changes_full}
                    SELECT {pk_select}, 'DELETE', array({ch_array}),
                           '{RUN_ID}', TIMESTAMP '{run_ts}',
                           to_date(TIMESTAMP '{run_ts}')
                    FROM orphans
                """)
                print(f"[CHANGE LOG] appended {orphan_count} DELETE rows")
            except Exception as e:
                # Review finding #9: the old `except Exception: warn` swallowed
                # EVERY failure and then deleted anyway. Deleting Gold rows
                # without emitting DELETE change-log rows silently breaks the
                # downstream contract (consumers never learn the row is gone),
                # so anything other than "the change-log table does not exist
                # yet" must ABORT before the MERGE. At-least-once is preserved:
                # aborting here means no delete happened, and the next run
                # re-detects the same orphans and retries the whole sequence.
                if not gold_cdc.is_missing_table_error(e, changes_table):
                    print(f"[FATAL] change-log append to {changes_full} failed "
                          f"— aborting BEFORE deleting {orphan_count} rows so "
                          f"Gold and the change log cannot diverge: "
                          f"{type(e).__name__}: {e}")
                    raise
                print(f"[WARN] change-log append skipped "
                      f"({changes_full} does not exist yet): {e}")

            pk_join = build_pk_conditions(primary_key_cols, "t", "o")
            spark.sql(f"""
                MERGE INTO {gold_table_full} t USING orphans o ON {pk_join}
                WHEN MATCHED THEN DELETE
            """)
            rows_deleted = orphan_count
            print(f"[DELETED] rows_deleted={rows_deleted}")
        else:
            print(f"[NO ORPHANS] no rows to delete")

        # ─── Update DynamoDB reconcile metadata ──────────────────
        dynamo.Table(CONFIG_TABLE).update_item(
            Key={"gold_id": GOLD_ID, "version": "latest"},
            UpdateExpression=(
                "SET reconcile.last_reconciled_at = :ts, "
                "reconcile.last_orphans_deleted = :deleted, "
                "#meta.last_run_id = :rid, "
                "#meta.last_run_at = :run_ts"
            ),
            ExpressionAttributeNames={"#meta": "metadata"},
            ExpressionAttributeValues={
                ":ts":      datetime.now(timezone.utc).isoformat(),
                ":deleted": rows_deleted,
                ":rid":     RUN_ID,
                ":run_ts":  datetime.now(timezone.utc).isoformat(),
            },
        )

        status = "SUCCEEDED"
        job.commit()

    except Exception:
        error_msg = traceback.format_exc()
        print(f"[FATAL ERROR]\n{error_msg}")

    finally:
        for df in cached_dfs:
            try:
                df.unpersist()
            except Exception:
                pass
        write_metrics(status, orphan_count=orphan_count,
                      rows_deleted=rows_deleted, error=error_msg)
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Gold reconciliation for {GOLD_ID} failed — see error_message in metrics table."
        )


run()
