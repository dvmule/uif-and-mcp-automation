# job_identity.py — shared by record_run and emit_event (UIFPE-200).
# The state machine's failure branch fires for ANY error, including ones
# where the export config was never read — so it cannot pass a real job_id.
# But the execution input always carries the config's S3 path, and the
# immutable layout (D22) puts the job id right in it:
#   s3://<bucket>/<prefix>/<job_id>/v<N>/export.json

from __future__ import annotations

import re


def resolve_job_id(event: dict) -> str:
    """The event's own job_id when it carries a real one; otherwise the id
    derived from export_config_s3_path; "unknown" only when neither works."""
    job_id = event.get("job_id")
    if job_id and job_id != "unknown":
        return job_id
    parts = [p for p in str(event.get("export_config_s3_path") or "").split("/") if p]
    if len(parts) >= 3 and re.fullmatch(r"v\d+", parts[-2]):
        return parts[-3]
    return job_id or "unknown"
