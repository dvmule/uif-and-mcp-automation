# export_config.py
# Loading + validation for the Beacon export config (render/package/deliver
# side of a job definition). The transform side lives in a separate
# dynamic-mapping engine config referenced by `mapping_config_s3_path` and is
# validated by the engine itself — this module treats it as an opaque pointer.
#
# Follows the gold_config.py convention: validate(body) returns None when the
# body is valid, else a human-readable error string naming the first problem.

from __future__ import annotations

import json
import re

from beacon_export import renderers

REQUIRED_TOP = ("version", "job_id", "mapping_config_s3_path",
                "canonical_s3_prefix", "quality_gates", "renderings",
                "packaging")

SUPPORTED_VERSIONS = ("1.0",)
DELIVERY_CHANNELS = ("sftp", "s3", "email")
# "paramiko" and "ses_link" stay VALID here on purpose (UIFPE-438/444):
# immutable pre-migration versions carry them, and their configs must still
# load and validate — the deliver Lambda is what refuses to RUN them, with a
# republish hint.
DELIVERY_ADAPTERS = {"sftp": ("paramiko", "transfer_family"),
                     "s3": ("s3_copy",),
                     "email": ("ses_link", "pearson_notifications")}
UPLOAD_PATTERNS = ("tmp-then-rename",)
CHECKSUM_ALGOS = ("sha256",)
EXTRACTION_MODES = ("full", "incremental")

# the watermark column is spliced into a source predicate — a plain column
# identifier is the whole allowed grammar
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load(text: str) -> dict:
    """Parse config JSON and validate; raises ValueError with the reason."""
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"export config is not valid JSON: {e}") from e
    err = validate(body)
    if err:
        raise ValueError(f"invalid export config: {err}")
    return body


def validate(body: dict) -> str | None:
    if not isinstance(body, dict):
        return "config body must be a JSON object"
    for key in REQUIRED_TOP:
        if key not in body:
            return f"missing required field: {key}"

    if body["version"] not in SUPPORTED_VERSIONS:
        return f"unsupported config version {body['version']!r} (supported: {SUPPORTED_VERSIONS})"
    if not isinstance(body["job_id"], str) or not body["job_id"]:
        return "job_id must be a non-empty string"
    if not str(body["mapping_config_s3_path"]).startswith("s3://"):
        return "mapping_config_s3_path must be an s3:// URI"
    canonical = str(body["canonical_s3_prefix"])
    if not canonical.startswith("s3://"):
        return "canonical_s3_prefix must be an s3:// URI"
    if "{batch_id}" not in canonical:
        return ("canonical_s3_prefix must contain {batch_id} — batch-scoped "
                "staging is what makes transform retries safe (N1). The "
                "mapping config's output target must write to the same prefix.")

    err = _validate_gates(body["quality_gates"])
    if err:
        return err

    rl = body["renderings"]
    if not isinstance(rl, list) or not rl:
        return "renderings must be a non-empty list"
    if len(rl) != 1:
        return ("exactly one rendering is required — an export delivers a "
                "single output format (same data in another format = another "
                "job with its own gates, destination and schedule)")
    for i, rendering in enumerate(rl):
        err = renderers.validate_rendering(rendering)
        if err:
            return f"renderings[{i}]: {err}"

    err = _validate_packaging(body["packaging"])
    if err:
        return err

    # UIFPE-170: one export may deliver to several targets. `delivery`
    # (single object) is the original shape and stays valid forever —
    # published pre-feature versions never change meaning (the UIFPE-172
    # convention). `deliveries` (non-empty list) is the multi-target shape.
    # Exactly one of the two: a config carrying both is ambiguous about
    # which one the runtime honors, so it is refused rather than guessed at.
    if "delivery" in body and "deliveries" in body:
        return ("config carries both 'delivery' and 'deliveries' — use "
                "'delivery' (one target) or 'deliveries' (several), not both")
    if "delivery" in body:
        err = _validate_delivery(body["delivery"])
        if err:
            return err
    elif "deliveries" in body:
        targets = body["deliveries"]
        if not isinstance(targets, list) or not targets:
            return "deliveries must be a non-empty list of delivery objects"
        for i, target in enumerate(targets):
            err = _validate_delivery(target)
            if err:
                return f"deliveries[{i}]: {err}"
        err = _validate_no_duplicate_targets(targets)
        if err:
            return err
    else:
        return "missing required field: delivery (or deliveries)"

    return _validate_extraction(body.get("extraction"))


def delivery_targets(cfg: dict) -> list[dict]:
    """The delivery targets of a validated config, always as a list —
    `deliveries` when present, else the single `delivery` wrapped. Every
    reader of the delivery block goes through here so the two shapes stay
    one code path (UIFPE-170)."""
    return list(cfg.get("deliveries") or [cfg["delivery"]])


def _target_identity(dlv: dict) -> tuple:
    """What makes two targets 'the same place': delivering one batch there
    twice is never intended, so publish and validation refuse it."""
    channel = dlv.get("channel")
    if channel == "s3":
        return ("s3", str(dlv.get("destination_s3_prefix", "")).rstrip("/"))
    if channel == "email":
        return ("email", tuple(sorted(dlv.get("recipients") or [])))
    return ("sftp", dlv.get("secret_ref") or dlv.get("connector_id"),
            dlv.get("remote_dir"))


def _validate_no_duplicate_targets(targets: list[dict]) -> str | None:
    seen: dict[tuple, int] = {}
    for i, target in enumerate(targets):
        identity = _target_identity(target)
        if identity in seen:
            return (f"deliveries[{i}] duplicates deliveries[{seen[identity]}] "
                    "— the same destination cannot appear twice")
        seen[identity] = i
    return None


def _validate_extraction(extraction) -> str | None:
    """UIFPE-168: OPTIONAL block. A missing key means today's full-load
    behavior — published pre-feature configs never change meaning on an
    engine upgrade (the UIFPE-172 zip convention). The per-run watermark
    VALUE never appears here: published versions are immutable (D22), so it
    arrives through the execution input instead."""
    if extraction is None:
        return None
    if not isinstance(extraction, dict):
        return "extraction must be an object"
    mode = extraction.get("mode")
    if mode not in EXTRACTION_MODES:
        return f"extraction.mode must be one of {EXTRACTION_MODES}"
    if mode == "full":
        return None
    source = extraction.get("source")
    if not isinstance(source, str) or not source:
        return ("extraction.source (the mapping source the watermark "
                "predicate applies to) is required for incremental mode")
    column = extraction.get("watermark_column")
    if not isinstance(column, str) or not _IDENTIFIER.match(column):
        return ("extraction.watermark_column must be a plain column "
                "identifier — it is spliced into the source predicate")
    return None


def _validate_gates(gates: dict) -> str | None:
    if not isinstance(gates, dict):
        return "quality_gates must be an object"
    min_rows = gates.get("min_rows")
    if not isinstance(min_rows, int) or min_rows < 0:
        return "quality_gates.min_rows must be a non-negative integer"
    max_rej = gates.get("max_rejection_pct")
    if not isinstance(max_rej, (int, float)) or not 0 <= max_rej <= 100:
        return "quality_gates.max_rejection_pct must be a number in [0, 100]"
    return None


def _validate_packaging(pkg: dict) -> str | None:
    if not isinstance(pkg, dict):
        return "packaging must be an object"
    pattern = pkg.get("filename_pattern", "")
    if "{batch_id}" not in pattern:
        return "packaging.filename_pattern must contain {batch_id} (idempotent re-delivery requires batch-unique names)"
    if "{ext}" not in pattern:
        return "packaging.filename_pattern must contain {ext}"
    pickup = str(pkg.get("pickup_s3_prefix", ""))
    if not pickup.startswith("s3://"):
        return "packaging.pickup_s3_prefix must be an s3:// URI"
    if "{batch_id}" not in pickup:
        return "packaging.pickup_s3_prefix must contain {batch_id}"
    manifest = pkg.get("manifest", {})
    if manifest.get("enabled", True):
        algo = manifest.get("checksum", "sha256")
        if algo not in CHECKSUM_ALGOS:
            return f"packaging.manifest.checksum {algo!r} not supported (supported: {CHECKSUM_ALGOS})"
    zip_cfg = pkg.get("zip", {})
    if not isinstance(zip_cfg, dict) or not isinstance(
            zip_cfg.get("enabled", False), bool):
        return "packaging.zip must be an object with a boolean 'enabled'"
    return None


def _validate_delivery(dlv: dict) -> str | None:
    if not isinstance(dlv, dict):
        return "delivery must be an object"
    channel = dlv.get("channel")
    if channel not in DELIVERY_CHANNELS:
        return f"delivery.channel must be one of {DELIVERY_CHANNELS}"
    allowed_adapters = DELIVERY_ADAPTERS[channel]
    adapter = dlv.get("adapter", allowed_adapters[0])
    if adapter not in allowed_adapters:
        return (f"delivery.adapter {adapter!r} is not valid for channel "
                f"{channel!r} (allowed: {allowed_adapters})")

    if channel == "s3":
        dest = str(dlv.get("destination_s3_prefix", ""))
        if not dest.startswith("s3://"):
            return "delivery.destination_s3_prefix must be an s3:// URI"
        return None

    if channel == "email":
        recipients = dlv.get("recipients")
        if not isinstance(recipients, list) or not recipients:
            return "delivery.recipients must be a non-empty list of addresses"
        for r in recipients:
            if not isinstance(r, str) or "@" not in r or "." not in r.split("@")[-1]:
                return f"delivery.recipients contains an invalid address: {r!r}"
        expiry = dlv.get("link_expiry_hours", 24)
        if not isinstance(expiry, int) or not 1 <= expiry <= 168:
            return "delivery.link_expiry_hours must be an integer in [1, 168]"
        sender = dlv.get("from_address")
        if sender is not None and ("@" not in str(sender)):
            return "delivery.from_address must be an email address (SES-verified)"
        return None

    # sftp
    if not dlv.get("remote_dir", "").startswith("/"):
        return "delivery.remote_dir must be an absolute remote path"
    if dlv.get("upload_pattern", "tmp-then-rename") not in UPLOAD_PATTERNS:
        return f"delivery.upload_pattern must be one of {UPLOAD_PATTERNS}"
    if adapter == "paramiko" and not dlv.get("secret_ref"):
        return "delivery.secret_ref (Secrets Manager name for host/credentials) is required for the paramiko adapter"
    if adapter == "transfer_family" and not dlv.get("connector_id"):
        return "delivery.connector_id is required for the transfer_family adapter"
    return None
