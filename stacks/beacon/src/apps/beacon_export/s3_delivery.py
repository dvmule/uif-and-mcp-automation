# s3_delivery.py
# S3 destination channel: copy packaged batch files from the pickup zone to
# a destination bucket/prefix ("deliver to another S3", diagram step 13's
# simplified target). At runtime this is invoked by the deliver Lambda in
# the Step Functions flow; the monolithic job entrypoint can also call it
# directly (demo/interim).
#
# Unlike SFTP, S3 PUT/copy is atomic per object — a consumer can never see a
# partial file, so there is no tmp-then-rename dance. Two properties still
# matter and are guaranteed here:
#   - the MANIFEST lands LAST (a consumer polling the destination must never
#     see the manifest before every file it describes);
#   - re-delivery of the same batch is idempotent (filenames are batch-unique
#     by the packaging contract; copies simply overwrite).
#
# plan_delivery() is pure and unit-tested; S3Adapter executes a plan with an
# injected client (Lambda passes its own boto3 client; tests pass a fake).

from __future__ import annotations

from dataclasses import dataclass

from beacon_export.packaging import is_manifest_name


def split_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/prefix -> (bucket, prefix). Raises on non-S3 URIs."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    if not bucket:
        raise ValueError(f"missing bucket in URI: {uri!r}")
    return bucket, key


@dataclass(frozen=True)
class CopyStep:
    file_name: str
    source_bucket: str
    source_key: str
    dest_bucket: str
    dest_key: str
    is_manifest: bool


def plan_delivery(file_names: list[str], *, pickup_s3_prefix: str,
                  destination_s3_prefix: str) -> list[CopyStep]:
    """Copy plan for one batch: data files in order, manifest(s) last.

    file_names come from the packaging stage in delivery order; the manifest
    is re-sorted to the end defensively rather than trusted positionally.
    Filenames carry the batch id (enforced by the export config contract),
    so a flat destination prefix stays collision-free and retry-idempotent.
    """
    src_bucket, src_prefix = split_uri(pickup_s3_prefix.rstrip("/") + "/")
    dst_bucket, dst_prefix = split_uri(destination_s3_prefix.rstrip("/") + "/")

    def step(name: str) -> CopyStep:
        return CopyStep(
            file_name=name,
            source_bucket=src_bucket,
            source_key=src_prefix + name,
            dest_bucket=dst_bucket,
            dest_key=dst_prefix + name,
            is_manifest=is_manifest_name(name),
        )

    steps = [step(n) for n in file_names]
    return [s for s in steps if not s.is_manifest] + [s for s in steps if s.is_manifest]


class S3Adapter:
    """Executes a copy plan and verifies sizes. The client is injected so the
    deliver Lambda, the job entrypoint, and unit tests share this code."""

    def __init__(self, s3_client):
        self._s3 = s3_client

    def execute(self, plan: list[CopyStep]) -> list[dict]:
        results = []
        for step in plan:
            self._s3.copy_object(
                Bucket=step.dest_bucket,
                Key=step.dest_key,
                CopySource={"Bucket": step.source_bucket, "Key": step.source_key},
            )
            src_size = self._s3.head_object(
                Bucket=step.source_bucket, Key=step.source_key)["ContentLength"]
            dst_size = self._s3.head_object(
                Bucket=step.dest_bucket, Key=step.dest_key)["ContentLength"]
            if src_size != dst_size:
                raise IOError(f"verify failed for {step.file_name}: source "
                              f"{src_size}B != destination {dst_size}B")
            results.append({"step": "copy", "file": step.file_name,
                            "dest": f"s3://{step.dest_bucket}/{step.dest_key}",
                            "bytes": dst_size, "ok": True})
        return results
