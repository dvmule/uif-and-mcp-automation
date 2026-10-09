"""Which Silver job a finished Bronze run should trigger, and whether to fire.

A module of its own, with no Spark, boto3 or Glue imports, for one reason:
`bronze-ingest.py` does heavy work at import time (getResolvedOptions,
resolve_credentials), so a test cannot import it. The routing logic therefore
used to be **copied** into qe/pipeline/test_bronze_ingest.py, with a comment
asking whoever changed one to keep the other in sync.

That is why the UIFPE-355 job renames shipped broken and 477 tests stayed
green: the rename changed Terraform's job names, the copy in the test agreed
with the stale original, and nothing compared either to the infrastructure.
One implementation now, imported by both, and
qe/pipeline/test_job_name_drift.py compares the slugs here against glue.tf.

Job names are built from a prefix the job is GIVEN, never from `--env`.
Deriving `echo-{env}-silver-transform` from the environment is what made the
uif-echo-dev stack's Bronze trigger the echo-dev stack's Silver: that job name
exists, so StartJobRun succeeded against the wrong stack and Silver reported
SUCCEEDED over an empty read.
"""

from __future__ import annotations

# Slugs as Terraform names them. Changing one here without changing glue.tf
# (or the reverse) fails test_job_name_drift.
SILVER_TRANSFORM = "silver-transform"
SILVER_TRANSFORM_GCS_ICEBERG = "silver-transform-gcs-iceberg"
SILVER_TRANSFORM_BQ_NATIVE = "silver-transform-bq-native"

SILVER_JOB_SLUGS = (
    SILVER_TRANSFORM,
    SILVER_TRANSFORM_GCS_ICEBERG,
    SILVER_TRANSFORM_BQ_NATIVE,
)


def resolve_silver_trigger(
    *, dest_cloud: str, write_mode: str | None, resource_prefix: str, rows_read: int
) -> tuple[str, bool]:
    """Return (silver_job_name, should_trigger).

    Routing:
      AWS                          → {prefix}-silver-transform
      GCP + iceberg_biglake (def.) → {prefix}-silver-transform-gcs-iceberg
      GCP + bq_native              → {prefix}-silver-transform-bq-native

    0-row trigger policy:
      - bq_native ALWAYS triggers. Python Shell on BQ slots is cheap (~$0.0005,
        ~2s at 0 rows), and firing on empty runs lets Silver recover from a
        prior trigger that errored — an IAM gap or a transient BQ failure.
        The original 0-row gate stranded Silver permanently in that case.
      - The Spark variants skip 0 rows, to avoid a G.2X x 16 cold start for
        nothing. Same gate as the historic behaviour.

    `resource_prefix` is required and must be the running stack's prefix, e.g.
    "uif-echo-dev". It is never derived from the environment name.
    """
    if not resource_prefix:
        raise ValueError(
            "resource_prefix is required — without it the job name cannot "
            "identify which stack to trigger, and a wrong guess starts the "
            "other stack's Silver job against the other stack's lake"
        )

    if dest_cloud == "gcp":
        write_mode = (write_mode or "iceberg_biglake").lower()
        if write_mode == "bq_native":
            return f"{resource_prefix}-{SILVER_TRANSFORM_BQ_NATIVE}", True
        return f"{resource_prefix}-{SILVER_TRANSFORM_GCS_ICEBERG}", rows_read > 0

    return f"{resource_prefix}-{SILVER_TRANSFORM}", rows_read > 0
