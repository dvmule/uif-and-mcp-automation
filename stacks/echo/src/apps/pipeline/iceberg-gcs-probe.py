# _phase0_iceberg_gcp_probe.py
# Permanent canary for GCP-native classpath + auth.
#
# Originally a one-shot Phase 0 probe (commit 81f4d3e). Promoted to a
# permanent canary after the 2026-06-13 smoke discovered the auth/classpath
# fixes (see memory: feedback_gcp_glue_classpath_auth) — the old probe
# verified JAR presence but never actually wrote to GCS, so it returned
# SUCCEEDED for two weeks while the real path was broken.
#
# This version writes a 1-row parquet to GCS using the same inline-credentials
# pattern as the production Bronze/Silver jobs. Failure here = real failure.
#
# Required args (wired in glue_job_phase0_probe.tf):
#   --gcs_write_uri    Fully-qualified gs:// URI to overwrite each run.
#   --gcs_secret_arn   Secrets Manager ARN with the GCP SA JSON (project_id,
#                      client_email, private_key, private_key_id).
#
# Optional args:
#   --extra-jars / --datalake-formats / --user-jars-first — set in TF defaults.
#
# Submission:
#   aws glue start-job-run --job-name phase0-iceberg-gcp-probe-dev

import json
import sys
import traceback

import boto3
from awsglue.context import GlueContext
from pyspark.context import SparkContext


REGION = "eu-west-1"
secrets = boto3.client("secretsmanager", region_name=REGION)

sc = SparkContext()
glueCtx = GlueContext(sc)
spark = glueCtx.spark_session
jvm = spark.sparkContext._jvm


def probe_class(fqcn: str) -> tuple[bool, str]:
    try:
        jvm.java.lang.Class.forName(fqcn)
        return True, "OK"
    except Exception as e:
        return False, str(e).splitlines()[0][:200]


print("=" * 60)
print("[PROBE] Phase 0 — Iceberg/GCS classpath verification")
print("=" * 60)

# 1. Iceberg version Glue 5.1 auto-bundles via --datalake-formats=iceberg
try:
    iceberg_version = jvm.org.apache.iceberg.IcebergBuild.version()
    iceberg_full = jvm.org.apache.iceberg.IcebergBuild.fullVersion()
    print(f"[PROBE] Iceberg version:      {iceberg_version}")
    print(f"[PROBE] Iceberg full version: {iceberg_full}")
except Exception:
    print("[PROBE] Iceberg version:      NOT ON CLASSPATH")
    traceback.print_exc(limit=2)

# 2. Spark + Scala version (helps pick iceberg-spark-runtime artifact)
try:
    print(f"[PROBE] Spark version:        {spark.version}")
    print(f"[PROBE] Scala binary version: {jvm.scala.util.Properties.versionNumberString()}")
except Exception:
    pass

print("-" * 60)
print("[PROBE] Class availability:")
classes_to_probe = [
    # Hadoop GCS connector (filesystem layer)
    ("com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem",
     "GCS Hadoop connector"),
    ("com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS",
     "GCS Hadoop AbstractFileSystem"),
    # Iceberg GCP FileIO
    ("org.apache.iceberg.gcp.gcs.GCSFileIO",
     "Iceberg GCSFileIO"),
    # BigQuery Metastore catalog — current canonical class name (Iceberg 1.5+)
    ("org.apache.iceberg.gcp.bigquery.BigQueryMetastoreCatalog",
     "BigQueryMetastoreCatalog (preferred)"),
    # Older class name — fallback check
    ("org.apache.iceberg.gcp.bigquery.BigQueryCatalog",
     "BigQueryCatalog (legacy name)"),
    # Iceberg SparkCatalog (already on classpath via --datalake-formats=iceberg)
    ("org.apache.iceberg.spark.SparkCatalog",
     "Iceberg SparkCatalog"),
    # AWS Glue catalog (sanity check — confirms we're not breaking the existing path)
    ("org.apache.iceberg.aws.glue.GlueCatalog",
     "Iceberg GlueCatalog (AWS-native, sanity)"),
    # Iceberg S3FileIO (AWS-native, sanity)
    ("org.apache.iceberg.aws.s3.S3FileIO",
     "Iceberg S3FileIO (AWS-native, sanity)"),
]
for fqcn, label in classes_to_probe:
    ok, msg = probe_class(fqcn)
    flag = "AVAILABLE" if ok else "MISSING"
    print(f"  [{flag:>9}] {label:<45}  {fqcn}")
    if not ok:
        print(f"               reason: {msg}")

print("-" * 60)

# 3. GCS write probe — non-conditional. The whole point of promoting this
# from "throwaway" to "permanent canary" is that failure surfaces as job
# FAILED, not as a soft skip. Required args come from TF defaults.
gcs_uri = None
gcs_secret_arn = None
for i, arg in enumerate(sys.argv):
    if arg == "--gcs_write_uri" and i + 1 < len(sys.argv):
        gcs_uri = sys.argv[i + 1]
    elif arg == "--gcs_secret_arn" and i + 1 < len(sys.argv):
        gcs_secret_arn = sys.argv[i + 1]

if not gcs_uri or not gcs_secret_arn:
    raise RuntimeError(
        "PROBE missing required args: --gcs_write_uri and --gcs_secret_arn. "
        "These are wired in glue_job_phase0_probe.tf default_arguments — "
        "check the TF resource didn't drop them."
    )

# Load SA JSON inline — same pattern as the production Bronze/Silver jobs.
# /tmp keyfile not used: executors don't share the driver's /tmp filesystem.
print(f"[PROBE] Loading GCP SA from {gcs_secret_arn}")
sa_info = json.loads(secrets.get_secret_value(SecretId=gcs_secret_arn)["SecretString"])
gcp_project = sa_info["project_id"]
print(f"[PROBE] SA project={gcp_project} client_email={sa_info['client_email']}")

# Hadoop conf via _jsc.hadoopConfiguration() so the mutation reaches the
# live Hadoop Configuration and is serialised to executors. spark.conf.set
# with the spark.hadoop.* prefix is NOT reliable after SparkContext init.
print(f"[PROBE] Attempting GCS write: {gcs_uri}")
hconf = sc._jsc.hadoopConfiguration()
hconf.set("fs.gs.impl",
          "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
hconf.set("fs.AbstractFileSystem.gs.impl",
          "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
hconf.set("fs.gs.project.id", gcp_project)
hconf.set("fs.gs.auth.service.account.enable", "true")
hconf.set("google.cloud.auth.service.account.enable", "true")
hconf.set("fs.gs.auth.service.account.email",          sa_info["client_email"])
hconf.set("fs.gs.auth.service.account.private.key.id", sa_info["private_key_id"])
hconf.set("fs.gs.auth.service.account.private.key",    sa_info["private_key"])
hconf.set("google.cloud.auth.service.account.email",          sa_info["client_email"])
hconf.set("google.cloud.auth.service.account.private.key.id", sa_info["private_key_id"])
hconf.set("google.cloud.auth.service.account.private.key",    sa_info["private_key"])

df = spark.createDataFrame([(1, "probe")], ["id", "label"])
df.write.mode("overwrite").parquet(gcs_uri)
print(f"[PROBE] GCS write: SUCCEEDED → {gcs_uri}")

print("=" * 60)
print("[PROBE DONE]")
print("=" * 60)
