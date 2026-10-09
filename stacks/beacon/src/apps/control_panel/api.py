# api.py
# FastAPI surface implementing openapi.yaml (contract-first: the yaml is the
# registered contract; this file implements it). Run locally with:
#   py -m uvicorn control_panel.api:app --reload --port 8802
#
# AuthN/AuthZ: designed to sit behind the RELAY API Gateway's Entra ID JWT
# authorizer; RBAC enforcement (role registry, D15) is the next slice and
# hooks in as FastAPI dependencies on these routes. No route below performs
# a state change that bypasses that plan — destinations POST is the only
# mutation and is domain-scoped by design.

from __future__ import annotations

import os

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from control_panel import (auth, converter, drift, extraction_state,
                           lifecycle, notifications, partner_schema, preview,
                           publisher, registries, runs, scheduler, validation)

app = FastAPI(title="Beacon Control Panel API", version="0.1.0")

# UIFPE-487: every request needs a valid RELAY shell session token (see
# auth.py). Added BEFORE the CORS middleware so CORS wraps it — Starlette
# applies the LAST added middleware outermost, which keeps preflight OPTIONS
# answered by CORS and puts CORS headers on our 401s.
app.add_middleware(auth.ShellSessionMiddleware)

# Cross-origin callers. Same-origin deployments (UI and API behind one
# CloudFront) need none of this. Two cases do:
#   - local dev: the Vite dev server on another port
#   - the shared RELAY distribution (/m/beacon/ deploy), whose UI calls
#     this API on a different host — set BEACON_UI_ORIGINS to a comma-
#     separated origin list on the API task (e.g. https://d2qua7....cloudfront.net)
_extra_origins = [o.strip() for o in
                  os.environ.get("BEACON_UI_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   *_extra_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok",
            "env": os.environ.get("BEACON_ENV", "local"),
            "auth_enabled": auth.enabled(),
            "engine_available": validation.ENGINE_AVAILABLE,
            "s3_publish_enabled": publisher.enabled(),
            "runs_enabled": publisher.runs_enabled(),
            "runs_history_enabled": runs.enabled(),
            "scheduling_enabled": scheduler.enabled(),
            "extraction_state_enabled": extraction_state.enabled(),
            "preview_enabled": preview.enabled()}


# ---- gold catalog discovery (UIFPE-229) --------------------------------------
# Domain first, then that domain's tables — live from the Glue catalog, so a
# new gold table appears in the wizard with no registry edit and no deploy.

@app.get("/registries/domains")
def get_domains() -> dict:
    return {"domains": registries.gold_domains()}


@app.get("/registries/domains/{domain}/tables")
def get_domain_tables(domain: str) -> dict:
    try:
        tables = registries.gold_tables(domain)
    except Exception as e:  # catalog unreachable — tell the author, don't 200-empty
        raise HTTPException(503, "the gold catalog is unavailable right now "
                                 f"({type(e).__name__}) — try again shortly")
    if tables is None:
        raise HTTPException(404, f"unknown domain {domain!r}")
    return {"domain": domain, "tables": tables}


@app.get("/registries/relationships")
def get_relationships() -> dict:
    """UIFPE-580: how Gold tables relate — the joins (and list spreads) the
    wizard fills in when a related table is added to a feed (UIFPE-581)."""
    return {"relationships": registries.relationships()}


@app.get("/registries/sources")
def get_sources(formats: str | None = Query(default=None)) -> list[dict]:
    wanted = [f.strip() for f in formats.split(",") if f.strip()] if formats else None
    if wanted:
        unknown = set(wanted) - registries.format_ids()
        if unknown:
            raise HTTPException(422, f"unknown format(s): {sorted(unknown)}")
    return registries.sources(wanted)


@app.get("/registries/sources/{source_id}/columns")
def get_source_columns(source_id: str) -> dict:
    """The source's attributes for the mapping tab — introspected LIVE
    (Glue catalog / CSV header), registry fallback when offline."""
    result = registries.source_columns(source_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/registries/sources/{source_id}/columns/{column}/values")
def get_column_values(source_id: str, column: str,
                      limit: int = Query(default=preview.VALUES_LIMIT, ge=1,
                                         le=preview.VALUES_LIMIT)) -> dict:
    """UIFPE-441: the column's most common values, live from Gold via Athena
    — the Filter step's suggestions. A column that cannot be suggested
    (identifiers by name, text over the cardinality cap per the persisted
    profile, dates, structured types, preview off) answers with a `reason`,
    not an error."""
    try:
        return preview.column_values(source_id, column, limit)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


# ---- the cardinality profile (UIFPE-514) -----------------------------------
# Asynchronous and persisted: POST starts one Athena scan (or answers the
# fresh persisted profile), GET /profile/{id} polls it and persists the
# result, GET /profile serves the persisted profile and never starts Athena.
# The wizard POSTs when a source is picked so the profile is usually done by
# the Filter step, whose picker holds text attributes back until it lands.

@app.get("/registries/sources/{source_id}/profile")
def get_source_profile(source_id: str) -> dict:
    """{status: done, ...} from the persisted profile, {status: absent} when
    none is fresh, {status: unavailable, reason} when the table cannot be
    profiled. Never starts Athena."""
    try:
        return preview.column_profile(source_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


@app.post("/registries/sources/{source_id}/profile", status_code=202)
def post_source_profile(source_id: str):
    """202 {status: running, profile_id} after starting the scan; 200
    {status: done, ...} when a fresh persisted profile already exists
    (idempotent); 200 {status: unavailable, reason} when it cannot be
    measured; 503 when the preview is not configured."""
    try:
        result = preview.start_profile(source_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)
    if result.get("status") != "running":
        return JSONResponse(status_code=200, content=result)
    return result


@app.get("/registries/sources/{source_id}/profile/{profile_id}")
def get_source_profile_status(source_id: str, profile_id: str) -> dict:
    try:
        return preview.profile_status(source_id, profile_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


@app.get("/registries/formats")
def get_formats() -> list[dict]:
    return registries.formats()


@app.get("/registries/channels")
def get_channels() -> list[dict]:
    return registries.channels()


# UIFPE-546: the verified shell username the auth middleware stashed on the
# request — None when auth is off (local dev, tests), which switches all
# visibility filtering and ownership guards off.
def _caller(request: Request) -> str | None:
    return getattr(request.state, "username", None) or None


@app.get("/destinations")
def get_destinations(request: Request) -> list[dict]:
    # UIFPE-440: an external s3 destination carries the exact bucket-policy
    # statement the partner must apply — computed per response, never stored
    # (copies below), so a role rename is picked up automatically.
    # UIFPE-546: private destinations are the owner's business — everyone
    # else sees public rows only.
    username = _caller(request)
    rows = []
    for row in registries.destinations():
        if not registries.destination_visible(row, username):
            continue
        snippet = registries.partner_policy(row)
        rows.append({**row, "partner_policy": snippet} if snippet else row)
    return rows


@app.post("/destinations", status_code=201)
def post_destination(request: Request, body: dict = Body(...)) -> dict:
    record, error = registries.add_destination(body, owner=_caller(request))
    if error:
        raise HTTPException(422, error)
    snippet = registries.partner_policy(record)
    return {**record, "partner_policy": snippet} if snippet else record


@app.put("/destinations/{dest_id}")
def put_destination(dest_id: str, request: Request, body: dict = Body(...)) -> dict:
    """UIFPE-337: edit a destination. Channel immutable; the name is editable
    and must stay unique (UIFPE-606); sftp edits drop
    the status to pending_connection_test (host/port changes clear the pin).
    UIFPE-546: owner-only when auth is on."""
    record, error, status = registries.update_destination(
        dest_id, body, username=_caller(request))
    if error:
        raise HTTPException(status, error)
    return record


@app.delete("/destinations/{dest_id}")
def delete_destination(dest_id: str, request: Request) -> dict:
    """UIFPE-337: remove a destination and (sftp) schedule its credential
    secret's deletion — refused while a published export delivers here.
    UIFPE-546: owner-only when auth is on."""
    result, error, status = registries.delete_destination(
        dest_id, username=_caller(request))
    if error:
        raise HTTPException(status, error)
    return result


# ---- notifications credentials (UIFPE-444) ----------------------------------
# The Pearson Notifications OAuth client, managed through the API because no
# human or deploy role can touch Secrets Manager. Same trust model as
# destination credentials: accepted, stored, never returned.

@app.get("/notifications/credentials")
def get_notifications_credentials() -> dict:
    return notifications.status()


@app.put("/notifications/credentials")
def put_notifications_credentials(body: dict = Body(...)) -> dict:
    result, error, status = notifications.store_credentials(body)
    if error:
        raise HTTPException(status, error)
    return result


@app.post("/destinations/{dest_id}/test")
def post_destination_test(dest_id: str, request: Request) -> dict:
    """UIFPE-241: connection test for an sftp destination — connects from the
    deliver Lambda (the VPC/NAT egress deliveries use), verifies the remote
    dir is writable, pins the host key on first contact, flips to verified.
    UIFPE-546: owner-only when auth is on."""
    result, error, status = registries.test_destination(
        dest_id, username=_caller(request))
    if error:
        raise HTTPException(status, error)
    return result


# ---- partner XML schemas (UIFPE-558) -----------------------------------------
# The XSD travels beside the export: staged on upload, referenced by the
# draft (id + hash only — a draft is one DynamoDB item), frozen into the
# immutable version folder at publish.

@app.post("/schemas", status_code=201)
def post_schema(body: dict = Body(...)) -> dict:
    """Accept and stage a partner XSD. Returns the reference the draft keeps
    plus the parsed tree the wizard's mapping guide renders."""
    try:
        record, error = partner_schema.stage_upload(
            str(body.get("filename") or ""), body.get("content") or "")
    except Exception:
        # storage refused the write (an unhandled failure here would surface
        # as a bare 500 without CORS headers — the browser then reports a
        # misleading CORS error instead of the real one)
        raise HTTPException(503, "the schema could not be stored — try "
                                 "again, or ask the platform team to check "
                                 "the panel's storage access")
    if error:
        raise HTTPException(422, error)
    return record


@app.post("/schemas/infer")
def post_schema_infer(body: dict = Body(...)) -> dict:
    """UIFPE-660: build an XSD from a sample XML document. Nothing is stored;
    the wizard's drawer shows what was read and offers the file, and "Use it
    for this export" sends that file through POST /schemas like any XSD."""
    record, error = partner_schema.infer_schema(
        str(body.get("filename") or ""), body.get("content") or "")
    if error:
        raise HTTPException(422, error)
    return record


@app.get("/schemas/{schema_id}")
def get_schema(schema_id: str) -> dict:
    """The staged schema's parsed tree — the wizard re-reads it when an
    export with an attached schema is edited."""
    record, error = partner_schema.describe_upload(schema_id)
    if error:
        raise HTTPException(404, error)
    return record


@app.post("/convert")
def post_convert(body: dict = Body(...)) -> dict:
    sql = body.get("sql", "")
    if not sql.strip():
        raise HTTPException(422, "sql is required")
    try:
        return converter.convert(sql, body.get("dialect"))
    except converter.ConversionError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/validate/export-config")
def post_validate_export(body: dict = Body(...)) -> dict:
    return validation.validate_export_config(body)


# ---- drafts / publish lifecycle (design doc §10.4) ------------------------

def _or_422(record, error):
    if error:
        raise HTTPException(422, error)
    return record


@app.get("/drafts")
def get_drafts() -> list[dict]:
    return lifecycle.list_drafts()


@app.post("/drafts", status_code=201)
def post_draft(request: Request, body: dict = Body(...)) -> dict:
    # UIFPE-611: the author is the verified login, never the request body
    return _or_422(*lifecycle.create_draft(body, caller=_caller(request)))


@app.get("/drafts/{draft_id}")
def get_draft(draft_id: str) -> dict:
    draft = lifecycle.get_draft(draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    return draft


@app.put("/drafts/{draft_id}")
def put_draft(draft_id: str, body: dict = Body(...)) -> dict:
    return _or_422(*lifecycle.update_draft(draft_id, body))


@app.post("/drafts/{draft_id}/submit")
def post_submit(draft_id: str, request: Request) -> dict:
    """Validated => published (UIFPE-195): submit runs engine validation and,
    when clean, publishes the next immutable version directly — the approval
    queue is gone."""
    version = _or_422(*lifecycle.submit(draft_id, caller=_caller(request)))
    # the seam: the published version's config lands where the pipeline reads
    # it — and the outcome is PERSISTED on the version so Run-now can resolve
    # the config after any restart
    lifecycle.attach_publish_result(version, publisher.publish_version(version))
    return version


@app.get("/catalog")
def get_catalog(include_retired: bool = Query(default=False)) -> list[dict]:
    """UIFPE-446: retired exports are hidden unless asked for explicitly —
    only the Exports screen's "show retired" toggle asks."""
    return lifecycle.catalog(include_retired)


@app.get("/catalog/{job_id}/versions")
def get_catalog_versions(job_id: str) -> list[dict]:
    versions = lifecycle.catalog_versions(job_id)
    if not versions:
        raise HTTPException(404, "no published versions for this job")
    # UIFPE-446: the detail page learns the retired state from its own load
    if lifecycle.is_retired(job_id):
        versions = [{**v, "retired": True} for v in versions]
    return versions


@app.post("/catalog/{job_id}/retire")
def post_retire(job_id: str) -> dict:
    """UIFPE-446: soft delete. Versions, run history, and delivered files all
    stay (D22); the export just leaves the catalog. Reversible."""
    if scheduler.get_schedule(job_id):
        raise HTTPException(409, "this export still has a schedule — delete "
                                 "the schedule first, so a retired export can "
                                 "never keep firing invisibly")
    if (error := lifecycle.set_retired(job_id, True)):
        raise HTTPException(404, error)
    return {"job_id": job_id, "retired": True}


@app.post("/catalog/{job_id}/unretire")
def post_unretire(job_id: str) -> dict:
    if (error := lifecycle.set_retired(job_id, False)):
        raise HTTPException(404, error)
    return {"job_id": job_id, "retired": False}


@app.post("/catalog/{job_id}/run")
def post_run(job_id: str, body: dict = Body(default={})) -> dict:
    """Run a published export. D22: executes the promoted version's own
    config pair — latest by default, or an explicit {"version": N} for
    pinning/rollback."""
    versions = lifecycle.catalog_versions(job_id)
    if not versions:
        raise HTTPException(404, "no published versions for this job")
    if lifecycle.is_retired(job_id):
        raise HTTPException(409, "this export is retired — un-retire it "
                                 "before running")
    if body.get("version"):
        wanted = [v for v in versions if v["version"] == int(body["version"])]
        if not wanted:
            raise HTTPException(404, f"version {body['version']} not found")
        version = wanted[0]
    else:
        version = versions[-1]

    from datetime import datetime, timezone
    batch_id = body.get("batch_id") or \
        f"b-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    config_uri = (version.get("s3") or {}).get("export_config_uri")
    result = publisher.start_run(
        job_id, batch_id, export_config_uri=config_uri,
        # UIFPE-168: the RUNNING version's extraction settings decide whether
        # the stored watermark rides the execution input
        extraction=(version.get("settings") or {}).get("extraction"))
    if not result.get("started"):
        raise HTTPException(503, result.get("reason", "runs disabled"))
    result["version"] = version["version"]
    return result


# ---- gold drift (UIFPE-220) -------------------------------------------------
# "Discovery is live, execution is frozen": these answer whether each export's
# LATEST published config still matches the live gold catalog — on the panel,
# before the 00:30 schedule finds out the hard way.

@app.get("/drift")
def get_drift() -> dict:
    return drift.check_all()


@app.get("/catalog/{job_id}/drift")
def get_job_drift(job_id: str) -> dict:
    result = drift.check_job(job_id)
    if result is None:
        raise HTTPException(404, "no published versions for this job")
    return result


@app.post("/catalog/{job_id}/edit")
def post_edit(job_id: str, request: Request, body: dict = Body(default={})) -> dict:
    """UIFPE-199: start an edit of a published export. Returns a draft that
    carries the EXISTING export id — submitting it publishes the next
    version. Existing versions stay immutable (D22)."""
    if lifecycle.is_retired(job_id):
        raise HTTPException(409, "this export is retired — un-retire it "
                                 "before editing")
    draft, error = lifecycle.create_edit_draft(job_id, body, caller=_caller(request))
    if error:
        raise HTTPException(404, error)
    return draft


# ---- per-export schedule (UIFPE-206) ---------------------------------------
# Lives OUTSIDE the immutable versions (D22): setting or changing a schedule
# never publishes anything. PUT is a full replace; pause = PUT enabled=false.

@app.get("/catalog/{job_id}/schedule")
def get_schedule(job_id: str) -> dict:
    if not lifecycle.catalog_versions(job_id):
        raise HTTPException(404, "no published versions for this job")
    record = scheduler.get_schedule(job_id)
    if record is None:
        raise HTTPException(404, "this export has no schedule")
    return record


@app.put("/catalog/{job_id}/schedule")
def put_schedule(job_id: str, body: dict = Body(...)) -> dict:
    if not lifecycle.catalog_versions(job_id):
        raise HTTPException(404, "no published versions for this job")
    # UIFPE-446: retiring refuses while a schedule exists; the reverse hole —
    # scheduling an already-retired export — is closed here
    if lifecycle.is_retired(job_id):
        raise HTTPException(409, "this export is retired — un-retire it "
                                 "before scheduling")
    record, error = scheduler.put_schedule(job_id, body)
    if error:
        raise HTTPException(422, error)
    return record


@app.get("/schedules")
def get_schedules() -> dict:
    """All schedule records in one call — the Exports list shows one per row
    (UIFPE-207) and must not issue a GET per export."""
    return {"schedules": scheduler.list_schedules()}


@app.delete("/catalog/{job_id}/schedule", status_code=204)
def delete_schedule(job_id: str) -> None:
    if not lifecycle.catalog_versions(job_id):
        raise HTTPException(404, "no published versions for this job")
    ok, error = scheduler.delete_schedule(job_id)
    if not ok:
        raise HTTPException(503, error)


# ---- per-export extraction state (UIFPE-168) --------------------------------
# The MODE is versioned authoring config (settings.extraction); the WATERMARK
# is runtime state living outside versions (D22), advanced by the record_run
# Lambda on the success branch. The API reads it and resets it — never writes.

@app.get("/catalog/{job_id}/extraction")
def get_extraction(job_id: str) -> dict:
    """The export's extraction mode (from the latest published version) and
    its stored incremental watermark, if any."""
    versions = lifecycle.catalog_versions(job_id)
    if not versions:
        raise HTTPException(404, "no published versions for this job")
    settings = versions[-1].get("settings") or {}
    mode = (settings.get("extraction") or {}).get("mode", "full")
    state = extraction_state.get_state(job_id)
    return {"job_id": job_id, "mode": mode,
            "state_enabled": extraction_state.enabled(),
            "watermark": (state or {}).get("watermark"),
            "watermark_set_at": (state or {}).get("updated_at"),
            "watermark_batch_id": (state or {}).get("batch_id")}


@app.delete("/catalog/{job_id}/extraction/watermark", status_code=204)
def delete_extraction_watermark(job_id: str) -> None:
    """Reset the stored watermark: the next incremental run re-baselines with
    a full extract — the operator affordance for a Gold rebuild or a partner
    resync (UIFPE-168)."""
    if not lifecycle.catalog_versions(job_id):
        raise HTTPException(404, "no published versions for this job")
    ok, error = extraction_state.reset(job_id)
    if not ok:
        raise HTTPException(503, error)


@app.get("/catalog/{job_id}/batches/{batch_id}/files")
def get_batch_files(job_id: str, batch_id: str) -> dict:
    """Presigned download links for a completed batch's files (15-min TTL)."""
    if not lifecycle.catalog_versions(job_id):
        raise HTTPException(404, "no published versions for this job")
    result = publisher.batch_files(job_id, batch_id)
    if not result.get("enabled"):
        raise HTTPException(503, result.get("reason", "downloads disabled"))
    if not result["files"]:
        raise HTTPException(404, f"no files found for batch {batch_id!r}")
    return result


@app.get("/runs")
def get_runs(job_id: str | None = Query(default=None),
             limit: int = Query(default=50, le=200)) -> dict:
    """Run history from the DynamoDB runs table (written by the record_run
    Lambda). Says so explicitly when the table isn't configured.
    UIFPE-561: runs still executing are merged in from Step Functions (the
    only place that knows), so a page refresh keeps showing them; the
    recorded row wins the moment it exists."""
    result = runs.list_runs(job_id, limit)
    if result.get("enabled"):
        recorded = {r["batch_id"] for r in result["runs"]}
        running = [r for r in publisher.in_flight_runs(job_id)
                   if r["batch_id"] not in recorded]
        if running:
            result["runs"] = running + result["runs"]
    return result


@app.get("/runs/summary")
def get_runs_summary(since: str | None = Query(default=None),
                     until: str | None = Query(default=None)) -> dict:
    """UIFPE-610: counts by status of every recorded run in [since, until)
    (ISO date-times; default the last 7 days) — the Overview's Run outcomes
    and KPIs. Declared before /runs/{run_id} so "summary" is not a run id."""
    start, end, error = runs.parse_window(since, until)
    if error:
        raise HTTPException(422, error)
    return runs.summarize_runs(start, end)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return publisher.run_status(run_id)


@app.post("/runs/{run_id}/stop")
def post_stop_run(run_id: str) -> dict:
    """Stop a RUNNING batch (UIFPE-203): aborts the execution and its ECS
    task, then records the run as `stopped` — deliberately not `failed`, so
    an operator-stopped batch never flips the export's health."""
    result = publisher.stop_run(run_id)
    if not result.get("stopped"):
        raise HTTPException(409, result.get("reason", "cannot stop this run"))
    result["recorded"] = runs.record_stop(
        result["job_id"], result["batch_id"], "stopped from the control panel",
        started_at=result.get("started_at"))
    return result


@app.post("/validate/mapping-config")
def post_validate_mapping(body: dict = Body(...), format: str | None = Query(default=None)) -> dict:
    # UIFPE-609: the export's rendering format, when the caller knows it —
    # a bare list/record column is refused for csv
    return validation.validate_mapping_config(body, fmt=format)


# ---- live filter preview on Athena (UIFPE-441) ------------------------------
# Asynchronous: API Gateway caps this Lambda at 29 s and a Gold scan takes
# longer. POST plans + starts the executions (202), GET reads their state,
# DELETE stops them. Athena is the state store — the preview id is the
# executions' ids, and status/cancel refuse an id that is not ours.

@app.post("/preview/filter", status_code=202)
def post_preview_filter(body: dict = Body(...)):
    """Body: {mapping_config (the draft's, stub mapping allowed), lists:
    [{column: "alias.col", values: [...]}]}. 202 + preview_id when started;
    200 {status: refused, issues} when a clause cannot run on Athena (the
    validator's issue shape, so the wizard routes it to the clause)."""
    try:
        result = preview.start_preview(body)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)
    if result.get("status") == "refused":
        return JSONResponse(status_code=200, content=result)
    return result


@app.get("/preview/{preview_id}")
def get_preview(preview_id: str) -> dict:
    try:
        return preview.preview_status(preview_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


@app.delete("/preview/{preview_id}")
def delete_preview(preview_id: str) -> dict:
    try:
        return preview.cancel_preview(preview_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


# ---- the matched rows as one CSV (UIFPE-490) -------------------------------
# Same body and fences as the preview, one execution: Athena writes every
# matched row (capped) to the preview/ prefix, and a finished poll answers
# with a 15-minute presigned link the author clicks. Nothing is downloaded
# for them, and the API never streams the file itself (29 s, 6 MB).

@app.post("/preview/download", status_code=202)
def post_preview_download(body: dict = Body(...)):
    """Body as POST /preview/filter. 202 + download_id when started; 200
    {status: refused, issues} when the config cannot run on Athena."""
    try:
        result = preview.start_download(body)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)
    if result.get("status") == "refused":
        return JSONResponse(status_code=200, content=result)
    return result


@app.get("/preview/download/{download_id}")
def get_preview_download(download_id: str) -> dict:
    try:
        return preview.download_status(download_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)


@app.delete("/preview/download/{download_id}")
def delete_preview_download(download_id: str) -> dict:
    try:
        return preview.cancel_download(download_id)
    except preview.PreviewError as e:
        raise HTTPException(e.code, e.message)
