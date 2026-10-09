# lifecycle.py
# Drafts, publish lifecycle and job catalog:
# Draft -> Validate -> Submit (= publish) -> Published.
#
# STORAGE: DynamoDB when BEACON_TABLE_DRAFTS / BEACON_TABLE_CATALOG are set
# (the deployed service), in-memory otherwise (local dev, unit tests). The
# in-memory-only era caused a real incident: a service roll wiped the
# catalog, the version counter restarted, and a fresh publish silently
# overwrote the immutable v1 in S3. Persistence + the publisher's
# no-overwrite guard make that impossible now.
#
# Governance:
#   - a draft publishes only if engine validation passes and it has no
#     unresolved items (D12) — the validation gate IS the eligibility gate;
#   - the four-eyes approval step was removed by platform-owner decision
#     (UIFPE-195, 2026-08-20): validated => eligible for export. The revert
#     path is this file's git history (submit/approve/reject as of develop
#     @ UIFPE-193) — reinstating it is a lifecycle change, not a UI toggle;
#   - published versions are IMMUTABLE — publishing appends the next
#     version; the catalog put is conditional (no overwrite, ever).

from __future__ import annotations

import itertools
import json
import os
import uuid
from datetime import datetime, timezone

from control_panel import validation

EDITABLE = ("draft", "rejected")

# Export identity (UIFPE-196): the NAME is a repeatable display label — two
# authors may pick the same one. Identity is a system-minted 6-digit integer
# export id, monotonic from 100000 (an integer can never carry leading
# zeros, so six digits are guaranteed for the next 900k exports). Everywhere
# downstream (state machine, lambdas, S3 config paths, run records) the
# existing job_id field CARRIES the export id as a string — the pipes are
# unchanged, only what flows through them.
EXPORT_ID_START = 100000
_COUNTER_KEY = "__export-id-counter__"   # reserved catalog item, version 0

# DynamoDB caps an item at 400 KB, and a draft/version is ONE item whose
# `data` attribute is json.dumps(record) (ASCII-escaped, so len == bytes);
# 16 KB of headroom covers the key attributes. A version is never larger than
# the draft it came from, so guarding drafts guards versions. Applied in
# memory mode too, so local dev and the tests see the deployed behaviour.
MAX_RECORD_BYTES = 384 * 1024

# -- storage layer -----------------------------------------------------------
_seq = itertools.count(1)
_mem_export_seq = itertools.count(EXPORT_ID_START)
_mem_drafts: dict[str, dict] = {}
_mem_catalog: dict[str, list[dict]] = {}   # job_id -> [versions, v1 first]
_mem_retired: set[str] = set()             # UIFPE-446: retired job ids

_ddb = None
_ddb_initialized = False


def set_ddb_for_testing(client) -> None:
    global _ddb, _ddb_initialized
    _ddb = client
    _ddb_initialized = client is not None


def _tables() -> tuple[str, str] | None:
    drafts = os.environ.get("BEACON_TABLE_DRAFTS")
    catalog = os.environ.get("BEACON_TABLE_CATALOG")
    return (drafts, catalog) if drafts and catalog else None


def _client():
    global _ddb, _ddb_initialized
    if not _ddb_initialized:
        import boto3
        _ddb = boto3.client("dynamodb")
        _ddb_initialized = True
    return _ddb


def _new_draft_id() -> str:
    if _tables():
        return f"draft-{uuid.uuid4().hex[:10]}"
    return f"draft-{next(_seq):04d}"


def _next_export_id() -> int:
    """Mint the next export id. DynamoDB mode: an atomic ADD on a reserved
    catalog item — ids survive restarts and concurrent mints never collide
    (the version-counter incident's lesson, applied to identity). Memory
    mode: a process counter, reset() restarts it (tests only)."""
    tables = _tables()
    if tables:
        resp = _client().update_item(
            TableName=tables[1],
            Key={"job_id": {"S": _COUNTER_KEY}, "version": {"N": "0"}},
            UpdateExpression="ADD n :one",
            ExpressionAttributeValues={":one": {"N": "1"}},
            ReturnValues="UPDATED_NEW")
        return EXPORT_ID_START + int(resp["Attributes"]["n"]["N"]) - 1
    return next(_mem_export_seq)


def _save_draft(draft: dict) -> None:
    tables = _tables()
    if tables:
        _client().put_item(TableName=tables[0], Item={
            "id": {"S": draft["id"]},
            "status": {"S": draft["status"]},
            "data": {"S": json.dumps(draft)},
        })
    else:
        _mem_drafts[draft["id"]] = draft


def _load_draft(draft_id: str) -> dict | None:
    tables = _tables()
    if tables:
        item = _client().get_item(TableName=tables[0],
                                  Key={"id": {"S": draft_id}}).get("Item")
        return json.loads(item["data"]["S"]) if item else None
    return _mem_drafts.get(draft_id)


def _all_drafts() -> list[dict]:
    tables = _tables()
    if tables:
        items = _client().scan(TableName=tables[0]).get("Items", [])
        return [json.loads(i["data"]["S"]) for i in items]
    return list(_mem_drafts.values())


def _save_version(version: dict) -> str | None:
    """Append a catalog version. Conditional put: an existing (job, version)
    is NEVER overwritten — the immutability guarantee at the store level."""
    tables = _tables()
    if tables:
        try:
            _client().put_item(
                TableName=tables[1],
                Item={"job_id": {"S": version["job_id"]},
                      "version": {"N": str(version["version"])},
                      "data": {"S": json.dumps(version)}},
                ConditionExpression="attribute_not_exists(job_id)")
        except Exception:
            return ("concurrent publish detected for this job — the version "
                    "number was taken; retry the approval")
    else:
        _mem_catalog.setdefault(version["job_id"], []).append(version)
    return None


def _update_version(version: dict) -> None:
    """Persist post-publish metadata (the S3 URIs) onto a stored version."""
    tables = _tables()
    if tables:
        _client().put_item(TableName=tables[1], Item={
            "job_id": {"S": version["job_id"]},
            "version": {"N": str(version["version"])},
            "data": {"S": json.dumps(version)}})
    # memory: the caller mutated the shared dict already


def _versions(job_id: str) -> list[dict]:
    tables = _tables()
    if tables:
        items = _client().query(
            TableName=tables[1],
            KeyConditionExpression="job_id = :j",
            ExpressionAttributeValues={":j": {"S": job_id}}).get("Items", [])
        # items without `data` are reserved markers (the retire flag at
        # version 0, UIFPE-446), not versions
        return sorted((json.loads(i["data"]["S"]) for i in items if "data" in i),
                      key=lambda v: v["version"])
    return _mem_catalog.get(job_id, [])


def _all_versions() -> dict[str, list[dict]]:
    return _scan_catalog()[0]


def _scan_catalog() -> tuple[dict[str, list[dict]], set[str]]:
    """One pass over the catalog table: version rows (they carry `data`)
    grouped per job, plus the set of retired job ids (their marker items
    carry `retired` instead — UIFPE-446). The export-id counter item carries
    neither and falls through both."""
    tables = _tables()
    if tables:
        items = _client().scan(TableName=tables[1]).get("Items", [])
        grouped: dict[str, list[dict]] = {}
        retired: set[str] = set()
        for item in items:
            if "data" in item:
                v = json.loads(item["data"]["S"])
                grouped.setdefault(v["job_id"], []).append(v)
            elif item.get("retired", {}).get("BOOL"):
                retired.add(item["job_id"]["S"])
        return ({k: sorted(vs, key=lambda v: v["version"])
                 for k, vs in grouped.items()}, retired)
    return _mem_catalog, set(_mem_retired)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reset() -> None:
    """Test hook (memory mode only)."""
    global _mem_export_seq
    _mem_drafts.clear()
    _mem_catalog.clear()
    _mem_retired.clear()
    _mem_export_seq = itertools.count(EXPORT_ID_START)


# -- lifecycle ---------------------------------------------------------------

def _oversize(record: dict) -> str | None:
    """The store's size limit as a readable refusal, or None when it fits.
    Row-filter identifier lists are the usual cause: a 5,000-value list costs
    about 165 KB (the compiled filter plus the wizard's own copy of the list
    in settings.filter_model), so at most two fit in one export."""
    size = len(json.dumps(record))
    if size <= MAX_RECORD_BYTES:
        return None
    return (f"draft is {size // 1024} KB — the store's limit is "
            f"{MAX_RECORD_BYTES // 1024} KB. Row-filter identifier lists are the "
            "usual cause: a 5,000-value list costs about 165 KB (the compiled "
            "filter plus its copy in settings.filter_model), so at most two fit "
            "in one export")


# UIFPE-611: who did it. The API passes the verified login (the shell
# session's `sub`); a body-supplied author is only the fallback for a
# deployment with auth off (local dev, tests) — the wizard used to send a
# hardcoded "karthik" and every export showed it. Nothing is invented: with
# neither, the record says so.
UNKNOWN_USER = "unknown"


def _who(caller: str | None, body_author=None) -> str:
    return str(caller or body_author or UNKNOWN_USER)


def create_draft(body: dict, caller: str | None = None) -> tuple[dict | None, str | None]:
    # UIFPE-196: authors supply a NAME (repeatable label); identity is the
    # minted export id. `job_id` is accepted as a legacy alias for name.
    name = body.get("name") or body.get("job_id")
    for key, value in (("name", name), ("domain", body.get("domain")),
                       ("mapping_config", body.get("mapping_config"))):
        if not value:
            return None, f"missing required field: {key}"
    author = _who(caller, body.get("author"))
    export_id = _next_export_id()
    draft = {
        "id": _new_draft_id(),
        "export_id": export_id,
        "name": name,
        # job_id CARRIES the export id downstream — state machine, lambdas,
        # config paths and run records key on it unchanged
        "job_id": str(export_id),
        "domain": body["domain"],
        "author": author,
        "mapping_config": body["mapping_config"],
        "settings": body.get("settings", {}),
        "unresolved": body.get("unresolved", []),
        "status": "draft",
        "created_at": _now(),
        "updated_at": _now(),
        "history": [{"event": "created", "by": author, "at": _now()}],
    }
    if (error := _oversize(draft)):
        return None, error
    _save_draft(draft)
    return draft, None


def list_drafts() -> list[dict]:
    return _all_drafts()


def get_draft(draft_id: str) -> dict | None:
    return _load_draft(draft_id)


def update_draft(draft_id: str, body: dict) -> tuple[dict | None, str | None]:
    draft = _load_draft(draft_id)
    if draft is None:
        return None, "draft not found"
    if draft["status"] not in EDITABLE:
        return None, f"draft is {draft['status']} and cannot be edited"
    for key in ("mapping_config", "settings", "unresolved"):
        if key in body:
            draft[key] = body[key]
    draft["status"] = "draft"          # editing a rejected draft reopens it
    draft["updated_at"] = _now()
    if (error := _oversize(draft)):
        return None, error
    _save_draft(draft)
    return draft, None


def create_edit_draft(job_id: str, body: dict,
                      caller: str | None = None) -> tuple[dict | None, str | None]:
    """UIFPE-199: start an EDIT of a published export — a draft that carries
    the existing export id, so submitting it appends the next immutable
    version (v2, v3, …) instead of minting a new export. The caller supplies
    the edited content; anything omitted falls back to the latest published
    version. Versions themselves are never touched (D22)."""
    versions = _versions(job_id)
    if not versions:
        return None, "no published versions for this job"
    latest = versions[-1]
    # UIFPE-611: the AUTHOR is who created the export and never changes on an
    # edit; the person editing is recorded separately and becomes the new
    # version's published_by
    editor = _who(caller, body.get("author"))
    author = latest.get("author") or editor
    draft = {
        "id": _new_draft_id(),
        "export_id": latest.get("export_id"),
        "name": body.get("name") or latest.get("name") or job_id,
        "job_id": job_id,
        "domain": body.get("domain") or latest.get("domain"),
        "author": author,
        "editor": editor,
        "mapping_config": body.get("mapping_config") or latest.get("mapping_config"),
        "settings": body["settings"] if "settings" in body else latest.get("settings", {}),
        "unresolved": body.get("unresolved", []),
        "status": "draft",
        "created_at": _now(),
        "updated_at": _now(),
        "history": [{"event": f"edit of v{latest['version']}", "by": editor, "at": _now()}],
    }
    if (error := _oversize(draft)):
        return None, error
    _save_draft(draft)
    return draft, None


def _normalized_settings(settings):
    """Stamp the version record with what the publisher will emit for the
    keys that carry defaults, so a catalog version WITHOUT a key is strictly
    pre-feature: packaging (UIFPE-172 — missing = default-on zip, so write it
    explicitly) and extraction (UIFPE-168 — missing = full load). Non-dict
    shapes pass through untouched so the publisher's shape guards keep owning
    the error path."""
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        return settings
    normalized = dict(settings)
    if "packaging" not in normalized:
        normalized["packaging"] = {"zip": True}
    if "extraction" not in normalized:
        normalized["extraction"] = {"mode": "full"}
    return normalized


def submit(draft_id: str, caller: str | None = None) -> tuple[dict | None, str | None]:
    """Validate and, if clean, PUBLISH the draft as the next immutable
    version (UIFPE-195: validated => eligible; no approval queue). Returns
    the published VERSION, not the draft. UIFPE-611: `caller` — the verified
    login — is recorded as the version's published_by."""
    draft = _load_draft(draft_id)
    if draft is None:
        return None, "draft not found"
    if draft["status"] not in EDITABLE:
        return None, f"draft is {draft['status']}, only draft/rejected can be published"
    if draft["unresolved"]:
        return None, (f"{len(draft['unresolved'])} unresolved mapping item(s) "
                      "remain — resolve or remove them first (D12)")
    formats = (draft.get("settings") or {}).get("formats") or []
    result = validation.validate_mapping_config(draft["mapping_config"],
                                                fmt=formats[0] if formats else None)
    draft["last_validation"] = result
    if not result["valid"]:
        _save_draft(draft)
        return None, {"message": "engine validation failed", "issues": result["issues"]}
    existing = _versions(draft["job_id"])
    published_by = _who(caller, draft.get("editor") or draft["author"])
    version = {
        "job_id": draft["job_id"],
        "export_id": draft.get("export_id"),
        "name": draft.get("name", draft["job_id"]),
        "version": (existing[-1]["version"] + 1) if existing else 1,
        "domain": draft["domain"],
        "mapping_config": draft["mapping_config"],
        "settings": _normalized_settings(draft["settings"]),
        "author": draft["author"],
        # UIFPE-611: who published THIS version — the export page's
        # "Modified by" (author stays the creator across versions)
        "published_by": published_by,
        # field name kept for older records and every reader of the catalog;
        # the value records WHICH gate published it now that no human does
        "approved_by": "engine-validation",
        "published_at": _now(),
        "from_draft": draft["id"],
    }
    error = _save_version(version)
    if error:
        return None, error
    draft["status"] = "published"
    draft["history"].append({"event": "published", "by": published_by,
                             "at": _now(), "gate": "engine-validation"})
    draft["updated_at"] = _now()
    _save_draft(draft)
    return version, None


def attach_publish_result(version: dict, s3_result: dict) -> None:
    """Persist the S3 publish outcome (URIs) onto the stored version so
    Run-now can resolve the config after any restart."""
    version["s3"] = s3_result
    _update_version(version)


def catalog(include_retired: bool = False) -> list[dict]:
    """Latest version per job, with version count (full history retained).
    Retired exports (UIFPE-446) are hidden unless explicitly asked for —
    the dashboard, drift check, and pickers all read this."""
    grouped, retired = _scan_catalog()
    rows = [{**versions[-1], "versions": len(versions),
             "retired": versions[-1]["job_id"] in retired}
            for versions in grouped.values() if versions]
    if include_retired:
        return rows
    return [r for r in rows if not r["retired"]]


def catalog_versions(job_id: str) -> list[dict]:
    return _versions(job_id)


# -- retire (UIFPE-446): soft delete ------------------------------------------
# A retired export disappears from the catalog (and everything that reads it)
# but keeps every published version, run row, and delivered file — D22 stays
# intact and the flag is reversible. The marker is a reserved item at
# (job_id, version 0): real versions start at 1, so the slot is always free.

def is_retired(job_id: str) -> bool:
    tables = _tables()
    if tables:
        item = _client().get_item(
            TableName=tables[1],
            Key={"job_id": {"S": job_id}, "version": {"N": "0"}}).get("Item")
        return bool(item and item.get("retired", {}).get("BOOL"))
    return job_id in _mem_retired


def set_retired(job_id: str, retired: bool) -> str | None:
    """Flip the retire flag. Returns a readable refusal, or None on success."""
    if not _versions(job_id):
        return "no published versions for this job"
    tables = _tables()
    if tables:
        if retired:
            _client().put_item(TableName=tables[1], Item={
                "job_id": {"S": job_id},
                "version": {"N": "0"},
                "retired": {"BOOL": True},
                "retired_at": {"S": _now()}})
        else:
            _client().delete_item(
                TableName=tables[1],
                Key={"job_id": {"S": job_id}, "version": {"N": "0"}})
    else:
        (_mem_retired.add if retired else _mem_retired.discard)(job_id)
    return None
