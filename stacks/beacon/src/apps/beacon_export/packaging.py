# packaging.py
# Turns renderer output into partner-deliverable artifacts: batch-unique
# filenames from the configured pattern, and a manifest with per-file
# checksums plus the versions that produced the batch (auditability, N4).
# Pure functions only — the job supplies bytes/chunk iterators from S3.

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

FILENAME_TOKENS = ("{yyyyMMdd}", "{HHmmss}", "{batch_id}", "{job_id}", "{ext}")

# The manifest's {ext}. Every filename pattern ends ".{ext}" (enforced by the
# export config contract), so a rendered manifest name always ends
# ".manifest.json" — is_manifest_name() is the one predicate built on that,
# shared by delivery ordering (manifest last) and the control panel's
# download listing (manifest hidden, UIFPE-202) so the two can never drift.
MANIFEST_EXT = "manifest.json"


def is_manifest_name(name: str) -> bool:
    """True for a batch manifest named by manifest_filename()."""
    return name.endswith("." + MANIFEST_EXT)


def render_filename(pattern: str, *, job_id: str, batch_id: str, ext: str,
                    when: datetime | None = None) -> str:
    """Expand the configured filename pattern. `when` is injectable for tests;
    production passes the batch start time so every file in a batch shares
    one timestamp."""
    ts = when or datetime.now(timezone.utc)
    return (pattern
            .replace("{yyyyMMdd}", ts.strftime("%Y%m%d"))
            .replace("{HHmmss}", ts.strftime("%H%M%S"))
            .replace("{batch_id}", batch_id)
            .replace("{job_id}", job_id)
            .replace("{ext}", ext))


def zip_payload(inner_name: str, data: bytes, *,
                when: datetime | None = None) -> bytes:
    """Wrap one rendered file in a ZIP container (UIFPE-172). `when` (the
    batch start time) fixes the entry timestamp so re-runs of the same batch
    produce byte-identical archives — checksums stay stable across retries."""
    ts = when or datetime.now(timezone.utc)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(inner_name, date_time=ts.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data)
    return buf.getvalue()


def sha256_of_chunks(chunks) -> str:
    """Hex digest over an iterable of byte chunks (streams S3 bodies without
    holding a whole file in memory)."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def build_manifest(*, job_id: str, batch_id: str, files: list[dict],
                   versions: dict | None = None,
                   generated_at: str | None = None) -> dict:
    """files: [{"name": str, "bytes": int, "sha256": str}, ...] in delivery
    order. versions: config/engine version stamps for lineage."""
    return {
        "manifest_version": "1.0",
        "job_id": job_id,
        "batch_id": batch_id,
        "generated_at": generated_at or datetime.now(timezone.utc)
                                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": versions or {},
        "file_count": len(files),
        "files": files,
    }


def manifest_filename(pattern: str, *, job_id: str, batch_id: str,
                      when: datetime | None = None) -> str:
    """The manifest follows the same pattern as data files with ext=manifest.json,
    so partners can pair it by name."""
    return render_filename(pattern, job_id=job_id, batch_id=batch_id,
                           ext=MANIFEST_EXT, when=when)


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, indent=2, sort_keys=False).encode("utf-8")
