# glue_entrypoint.py — UIFPE-205 spike: run the Beacon transform as a Glue job.
#
# Deliberately thin: everything the Fargate entrypoint does stays in
# beacon_export.fargate_entrypoint.main(), which takes an injectable
# session_factory — this wrapper only supplies a Glue-flavoured Spark session
# and translates Glue's underscore job parameters to the entrypoint's dashed
# argv. The engine (dynamic_mapping + beacon_export) arrives unmodified via
# --extra-py-files.
#
# What is intentionally ABSENT versus the Fargate session (all of it is
# Fargate-container plumbing Glue owns or the spike wants to re-test):
#   master("local[*]"), spark.driver.memory, -Xshare:off, ErrorFile,
#   MaxDirectMemorySize, the S3A filesystem overrides, and the Iceberg
#   vectorization=false workaround (UIFPE-193) — Glue's own Spark/Iceberg
#   build is part of what this spike evaluates.
#
# Lake Formation fine-grained access (FGAC) is OFF and the session is built
# here, after four runs proved the alternative unreachable:
#   004  hand-built session + FGAC -> "Did not receive system driver address
#        within timeout of 300000ms" (only Glue's own bootstrap completes that
#        handshake, so the wrapper moved to GlueContext)
#   005  GlueContext + FGAC -> handshake starts, then "system driver failed
#        after launch" inside Glue's own component, 41s in, with no
#        diagnostics available in our account
# Consequence recorded on UIFPE-205: gold is read with the job role's OWN credentials,
# exactly like Fargate today, so the raw bucket grant stays and the
# governance argument does not hold as written. Reaching FGAC needs the gold
# location registered with Lake Formation and confirmation that Glue FGAC
# supports Iceberg via Iceberg's SparkCatalog — both owned by the data-lake
# team, tracked as a follow-up question, not by more spike runs.

import sys


def _glue_session():
    from pyspark.sql import SparkSession
    return (SparkSession.builder
            .appName("beacon-export-glue-spike")
            .config("spark.sql.extensions",
                    "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
            .config("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
            .config("spark.sql.catalog.glue_catalog.catalog-impl",
                    "org.apache.iceberg.aws.glue.GlueCatalog")
            .config("spark.sql.catalog.glue_catalog.io-impl",
                    "org.apache.iceberg.aws.s3.S3FileIO")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate())


def _translate(argv):
    """Glue passes custom parameters as --key value with underscores and
    injects its own (--JOB_NAME etc.); keep only ours, in dashed form."""
    out, skip_delivery, i = [], False, 0
    while i < len(argv):
        key = argv[i]
        if key == "--export_config_s3_path":
            out += ["--export-config-s3-path", argv[i + 1]]
            i += 2
        elif key == "--batch_id":
            out += ["--batch-id", argv[i + 1]]
            i += 2
        elif key == "--skip_delivery":
            skip_delivery = argv[i + 1].lower() != "false"
            i += 2
        elif key == "--watermark":
            # UIFPE-168/227: the state machine passes the key unconditionally;
            # empty means no stored watermark (full/baseline) and is dropped.
            # Glue does NOT deliver an empty value as an argv token — the flag
            # arrives BARE, directly followed by the next argument's key
            # (UIFPE-227 broke every run on exactly this), so "next token
            # missing or option-shaped" must read as "no value", one token
            # consumed, not two.
            value = argv[i + 1] if i + 1 < len(argv) else ""
            if value.startswith("--") or not value:
                i += 1 if value.startswith("--") else 2
            else:
                out += ["--watermark", value]
                i += 2
        else:
            i += 1
    if skip_delivery:
        out.append("--skip-delivery")
    return out


if __name__ == "__main__":
    from beacon_export import fargate_entrypoint
    rc = fargate_entrypoint.main(_translate(sys.argv[1:]),
                                 session_factory=_glue_session)
    if rc != 0:
        raise RuntimeError("beacon export batch failed — see traceback above")
