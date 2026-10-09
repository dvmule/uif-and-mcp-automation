# publisher.py
# The seam between the control plane and the pipeline: on approval, the
# published mapping config is written to S3 where the pipeline reads it,
# and the catalog can trigger/observe runs.
#
# Configuration is environment-driven so the same code runs everywhere:
#   BEACON_DATA_BUCKET       e.g. gps-dynmap-dev-061206535585 (unset =
#                            publishing to S3 disabled; approve still updates
#                            the catalog)
#   BEACON_CONFIG_PREFIX     default "configs" ("beacon-demo/configs" in the demo)
#   BEACON_STATE_MACHINE_ARN the export state machine for Run-now (unset =
#                            runs disabled)
#   AWS_PROFILE              local dev only (e.g. gps-sandbox); Fargate uses
#                            the task role — no credentials in code either way.
#
# Clients are lazy and injectable (tests pass fakes; no AWS in CI).

from __future__ import annotations

import json
import os
import re

_s3 = None
_sfn = None
_glue = None


def _clients():
    global _s3, _sfn
    if _s3 is None:
        import boto3
        session = boto3.Session()
        _s3 = session.client("s3")
        _sfn = session.client("stepfunctions")
    return _s3, _sfn


def _glue_client():
    global _glue
    if _glue is None:
        import boto3
        _glue = boto3.Session().client("glue")
    return _glue


def set_clients_for_testing(s3, sfn=None, glue=None) -> None:
    global _s3, _sfn, _glue
    _s3, _sfn, _glue = s3, sfn, glue


def enabled() -> bool:
    return bool(os.environ.get("BEACON_DATA_BUCKET"))


def runs_enabled() -> bool:
    """Run-now targets the Step Functions export flow — the ONLY run path
    (the Glue sandbox-demo fallback is gone)."""
    return bool(os.environ.get("BEACON_STATE_MACHINE_ARN"))


# ---- in-flight runs (UIFPE-561) ----------------------------------------------
# The runs table is written by record_run ONLY, at the end of a run — so a
# refreshed page has no row for a run still executing. Step Functions IS the
# running state, so the list borrows exactly one thing from it: which
# executions are RUNNING right now. Two guards: a short per-instance cache
# (ListExecutions is rate-limited by AWS; several people refreshing at once
# must not throttle us) and FAIL OPEN — any AWS error returns [], the
# recorded history still lists, never an error page.

_inflight_cache: dict = {"at": 0.0, "rows": []}
_INFLIGHT_CACHE_TTL_S = 5.0


def reset_inflight_cache_for_testing() -> None:
    _inflight_cache["at"], _inflight_cache["rows"] = 0.0, []


def in_flight_runs(job_id: str | None = None) -> list[dict]:
    """Runs currently executing, shaped like runs-table rows (status
    "running"). Empty when the state machine is not configured (local dev,
    tests) and on any AWS failure — the merge is an enrichment, never a
    dependency."""
    if not runs_enabled():
        return []
    import time
    now = time.monotonic()
    if now - _inflight_cache["at"] >= _INFLIGHT_CACHE_TTL_S:
        rows: list[dict] = []
        try:
            _, sfn = _clients()
            page = sfn.list_executions(
                stateMachineArn=os.environ["BEACON_STATE_MACHINE_ARN"],
                statusFilter="RUNNING", maxResults=25)
            for ex in page.get("executions", []):
                started = ex.get("startDate")
                row = {"batch_id": ex["name"], "job_id": None,
                       "status": "running", "trigger": "manual",
                       # UIFPE-583: the panel re-attaches to a batch it did
                       # not start (a reload, or coming back to this export)
                       # and polls/stops it by execution ARN — run_status and
                       # stop_run take nothing else
                       "run_id": ex["executionArn"],
                       # the When column reads recorded_at; for a live run the
                       # honest time is its start
                       "recorded_at": started.isoformat() if started else None,
                       "started_at": started.isoformat() if started else None}
                try:  # which export? the execution's own input says
                    desc = sfn.describe_execution(executionArn=ex["executionArn"])
                    payload = json.loads(desc.get("input") or "{}")
                    # UIFPE-577: the id comes from the config PATH, the same
                    # way record_run and emit_event resolve it. `job_config`
                    # in this input is the Glue SIZING row (worker type,
                    # workers, timeout) and has never carried an export id —
                    # reading it there left every row unattributed, so the
                    # per-export filter below dropped them all.
                    from beacon_export.lambdas.job_identity import resolve_job_id
                    resolved = resolve_job_id(payload)
                    row["job_id"] = None if resolved == "unknown" else resolved
                    row["trigger"] = payload.get("trigger") or "manual"
                except Exception:  # noqa: BLE001 — keep the row with what we know
                    pass
                rows.append(row)
        except Exception:  # noqa: BLE001 — fail open
            rows = []
        _inflight_cache["at"], _inflight_cache["rows"] = now, rows
    matched = _inflight_cache["rows"]
    if job_id:
        matched = [r for r in matched if r.get("job_id") == job_id]
    return [dict(r) for r in matched]


def publish_version(version: dict) -> dict:
    """Write a published catalog version to S3 as an IMMUTABLE config pair:
    configs/<job_id>/v<N>/{mapping.json, export.json} (D22).

    No live pointer — a mutable shared key meant an approve could silently
    become the next batch's config (or clobber another job's). Runs reference
    the versioned export.json explicitly. The export config is GENERATED from
    the draft's wizard settings and validated with the pipeline's own
    validator before writing — an unrunnable version is never published.
    """
    if not enabled():
        return {"published": False,
                "reason": "BEACON_DATA_BUCKET not configured — catalog-only publish"}
    bucket = os.environ["BEACON_DATA_BUCKET"]
    prefix = os.environ.get("BEACON_CONFIG_PREFIX", "configs").strip("/")
    s3, _ = _clients()

    base_key = f"{prefix}/{version['job_id']}/v{version['version']}"
    mapping_key = f"{base_key}/mapping.json"
    export_key = f"{base_key}/export.json"

    # Immutability guard (defense in depth after the catalog-reset incident):
    # a version key that already exists in S3 is NEVER overwritten. With the
    # persistent catalog the version counter can't regress, but if anything
    # ever drifts, we refuse loudly instead of silently replacing history.
    try:
        s3.head_object(Bucket=bucket, Key=export_key)
        return {"published": False,
                "reason": f"refusing to overwrite existing immutable version "
                          f"s3://{bucket}/{export_key} — version counter "
                          "drift; investigate before republishing"}
    except Exception:
        pass  # 404 = the key is free, proceed

    export_cfg, error = build_export_config(
        version, mapping_uri=f"s3://{bucket}/{mapping_key}")
    if error:
        return {"published": False,
                "reason": f"generated export config is invalid: {error}"}

    # UIFPE-558: the partner's schema travels with the version. Required
    # elements must be bound, and the staged file is frozen beside the config
    # pair BEFORE the pair is written — a version either carries its whole
    # contract or is not published at all.
    unknown_schema_targets: list[str] = []
    schema_meta = (version.get("settings") or {}).get("partner_schema")
    if isinstance(schema_meta, dict) and schema_meta.get("id"):
        from control_panel import partner_schema
        content, s_err = partner_schema.read_upload(str(schema_meta["id"]))
        if s_err:
            return {"published": False, "reason": f"partner schema: {s_err}"}
        parsed, s_err = partner_schema.parse_xsd(content)
        if s_err:
            return {"published": False, "reason": f"partner schema: {s_err}"}
        targets = [t for m in version["mapping_config"].get("mappings", [])
                   for t in ([m["target"]] if m.get("target")
                             else list(m.get("targets") or []))]
        missing = partner_schema.missing_required(parsed, targets)
        if missing:
            shown = ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")
            return {"published": False,
                    "reason": ("the partner schema requires element(s) the "
                               f"mapping does not bind: {shown}")}
        # UIFPE-587: the other direction. Elements the schema never declares
        # are a WARNING, not a refusal — the author may be deliberately ahead
        # of a partner's schema — but a strict xs:sequence rejects a document
        # carrying them, so the publish result says so instead of nothing.
        unknown_schema_targets = partner_schema.unknown_targets(parsed, targets)
        frozen, s_err = partner_schema.freeze(
            version["job_id"], version["version"], str(schema_meta["id"]),
            schema_meta.get("sha256"))
        if s_err:
            return {"published": False, "reason": f"partner schema: {s_err}"}
        if frozen.get("frozen"):
            export_cfg["partner_schema"] = {
                "s3_uri": f"s3://{bucket}/{frozen['s3_key']}",
                "sha256": frozen["sha256"]}

    # Normalize environment-owned paths: authors define WHAT (sources,
    # mappings, validations); WHERE the pipeline stages data is this
    # environment's concern. This also guarantees the mapping's canonical
    # output and the export config's canonical prefix always agree (N1).
    mapping_cfg = dict(version["mapping_config"])
    mapping_cfg["rejected_path"] = f"s3://{bucket}/rejected/${{batch_id}}/"
    mapping_cfg["output"] = {"targets": [{
        "format": "parquet", "mode": "overwrite",
        "path": f"s3://{bucket}/staging/${{batch_id}}/canonical/",
    }]}

    s3.put_object(Bucket=bucket, Key=mapping_key,
                  Body=json.dumps(mapping_cfg, indent=2).encode("utf-8"),
                  ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=export_key,
                  Body=json.dumps(export_cfg, indent=2).encode("utf-8"),
                  ContentType="application/json")
    result = {"published": True,
              "export_config_uri": f"s3://{bucket}/{export_key}",
              "uris": [f"s3://{bucket}/{mapping_key}",
                       f"s3://{bucket}/{export_key}"]}
    # UIFPE-587: recorded on the version, not just shown once in the wizard —
    # whoever reads this version later should see what the partner's schema
    # did not declare at the time it was published.
    if unknown_schema_targets:
        result["schema_warnings"] = [
            "the mapping writes element(s) the partner schema does not "
            "declare, which a strict schema rejects: "
            + ", ".join(unknown_schema_targets[:8])
            + (" …" if len(unknown_schema_targets) > 8 else "")]
    return result


def build_export_config(version: dict, *, mapping_uri: str) -> tuple[dict | None, str | None]:
    """The render/package/deliver contract, generated from the wizard
    settings stored on the draft. Returns (config, None) or (None, error)."""
    from beacon_export import export_config as beacon_export_config
    from beacon_export import renderers

    bucket = os.environ["BEACON_DATA_BUCKET"]
    settings = version.get("settings") or {}
    job_id = version["job_id"]

    gates_in = settings.get("gates", {})
    gates = {"min_rows": int(gates_in.get("min_rows", 1)),
             "max_rejection_pct": float(gates_in.get("max_rejection_pct", 2.0))}
    if gates_in.get("row_count_delta_pct_warn") not in (None, ""):
        gates["row_count_delta_pct_warn"] = float(gates_in["row_count_delta_pct_warn"])

    default_options = {
        "csv": {"delimiter": ",", "header": True, "quote_all": True,
                "encoding": "UTF-8", "formula_escape": True},
        "json": {"style": "jsonlines"},
        "xml": {},
    }
    # UIFPE-171: author-chosen options (settings.format_options, keyed by
    # format id) override the safe defaults. The field is author-controlled
    # JSON, so its shape is guarded like every other invalid-settings case.
    # Symbolic delimiter names (comma/tab/pipe) are resolved to their literal
    # characters HERE: the persisted config carries the character, so pipeline
    # images that predate the names still render it correctly.
    format_options = settings.get("format_options") or {}
    if not isinstance(format_options, dict) or not all(
            isinstance(v, dict) for v in format_options.values()):
        return None, ("settings.format_options must map format ids to "
                      "option objects")
    renderings = []
    for f in settings.get("formats", ["csv"]):
        options = {**default_options.get(f, {}), **format_options.get(f, {})}
        delimiter = options.get("delimiter")
        if isinstance(delimiter, str) and delimiter in renderers.DELIMITERS:
            options["delimiter"] = renderers.DELIMITERS[delimiter]
        renderings.append({"format": f, "options": options})

    # UIFPE-172: zip is DEFAULT-ON for newly published versions, opt-out per
    # feed. The generated config always carries an explicit zip block (same
    # resolve-at-publish rationale as the delimiter above): pre-feature
    # immutable configs lack the key and the engine treats that as
    # uncompressed, so history never changes shape on an engine upgrade.
    packaging_in = settings.get("packaging")
    if packaging_in is None:  # only MISSING means "apply the default" —
        packaging_in = {}     # falsy garbage (false/0/""/[]) must fail loudly,
    if not isinstance(packaging_in, dict) or not isinstance(
            packaging_in.get("zip", True), bool):
        return None, ('settings.packaging must be an object with a boolean '
                      '"zip" (use {"zip": false} to opt out)')
    zip_enabled = packaging_in.get("zip", True)
    # UIFPE-258: the manifest is OPT-IN per feed (owner decision 2026-09-04
    # after the first real SFTP delivery — most partners want only the data
    # file). Same author-controlled-boolean guard as zip. The generated
    # config always carries an explicit block; the engine defaults a MISSING
    # key to enabled, so immutable pre-258 versions keep their manifest (D22).
    if not isinstance(packaging_in.get("manifest", False), bool):
        return None, ('settings.packaging.manifest must be a boolean '
                      '(true delivers a .manifest.json with checksums)')
    manifest_enabled = packaging_in.get("manifest", False)

    # UIFPE-170: one export may deliver to several destinations. The wizard
    # sends `settings.deliveries` (a list of delivery objects, each carrying
    # its own destination_id); `settings.delivery` (single object) remains
    # the original contract. Exactly one of the two — both at once is
    # ambiguous and fails loudly, like every other invalid-settings case.
    deliveries_in = settings.get("deliveries")
    if deliveries_in is not None:
        if settings.get("delivery"):
            return None, ("settings carries both delivery and deliveries — "
                          "send one target as delivery or several as "
                          "deliveries, not both")
        if not isinstance(deliveries_in, list) or not deliveries_in or not all(
                isinstance(t, dict) for t in deliveries_in):
            return None, ("settings.deliveries must be a non-empty list of "
                          "delivery objects")
        targets = [dict(t) for t in deliveries_in]
    else:
        targets = [settings.get("delivery") or {
            "channel": "s3", "adapter": "s3_copy",
            "destination_s3_prefix": f"s3://{bucket}/partner-drop/inbound/{job_id}",
        }]

    # UIFPE-259: for sftp, the pinned host key is embedded into the published
    # config from the DESTINATION RECORD (server-side, never trusted from the
    # client payload). It is the server's public fingerprint — safe in config,
    # immutable per version (D22) — and it cannot live in the secret because
    # Keeper Universal Secret Sync overwrites the secret one-way from the
    # vault. A destination re-test after a partner rotates their host key
    # needs a re-publish to take effect, which is the D22-consistent behavior.
    # Per target since UIFPE-170: each sftp target pins ITS OWN server's key
    # (the destination_id rides inside the target; the single-target wizard
    # keeps sending it at the settings top level).
    def _with_host_key(target: dict) -> dict:
        # UIFPE-444: email rides the Pearson Notifications API — the adapter
        # is stamped SERVER-side at publish (like the connector override
        # below), so whatever the client payload said cannot pick a retired
        # path. The export name rides along for the mail's subject/body.
        if target.get("channel") == "email":
            return {**target, "adapter": "pearson_notifications",
                    "export_name": str(version.get("name") or job_id)}
        if target.get("channel") != "sftp":
            return target
        from control_panel import registries
        dest = registries.get_destination(str(
            target.get("destination_id") or settings.get("destination_id") or ""))
        if dest and dest.get("host_key"):
            target = {**target, "host_key": dest["host_key"],
                      "host_key_type": dest.get("host_key_type", "ssh-rsa")}
        # UIFPE-339: a destination that rides a Transfer Family connector
        # publishes with the transfer_family adapter + its connector id —
        # resolved from the RECORD (server-side), never trusted from the
        # client payload, exactly like the host key above. Every sftp
        # destination has a connector since UIFPE-438; a target that somehow
        # resolves none publishes without an adapter override and the deliver
        # Lambda refuses it loudly with a republish hint.
        if dest and dest.get("connector_id"):
            target = {**target, "adapter": "transfer_family",
                      "connector_id": dest["connector_id"]}
        return target

    targets = [_with_host_key(t) for t in targets]

    # Delivered filenames carry the export id AND the name (UIFPE-196):
    # names may repeat across exports, so two same-named feeds delivering to
    # one destination stay distinguishable by id. job_id == export id string.
    name_slug = re.sub(r"[^A-Za-z0-9]+", "_", str(version.get("name", job_id))).strip("_").lower()
    file_stem = f"{job_id}_{name_slug}" if name_slug and name_slug != job_id else job_id

    # UIFPE-168: extraction mode, resolved at publish like zip/delimiter —
    # the generated config always carries an explicit block, so a version
    # WITHOUT one is unambiguously pre-feature (= full). The watermark COLUMN
    # is platform-owned: authors never see _-prefixed metadata columns, so it
    # comes from the source registry, never from the draft.
    extraction_in = settings.get("extraction")
    if extraction_in is None:  # only MISSING means "full applies" — falsy
        extraction_in = {}     # garbage must fail loudly, like packaging
    if not isinstance(extraction_in, dict) or \
            extraction_in.get("mode", "full") not in ("full", "incremental"):
        return None, ('settings.extraction must be an object with mode '
                      '"full" or "incremental"')
    if extraction_in.get("mode") == "incremental":
        extraction, error = _incremental_extraction_block(version, extraction_in)
        if error:
            return None, error
    else:
        extraction = {"mode": "full"}

    cfg = {
        "version": "1.0",
        "job_id": job_id,
        "mapping_config_s3_path": mapping_uri,
        "canonical_s3_prefix": f"s3://{bucket}/staging/{{batch_id}}/canonical/",
        "quality_gates": gates,
        "renderings": renderings,
        "packaging": {
            "filename_pattern": f"{file_stem}_{{yyyyMMdd}}_{{batch_id}}.{{ext}}",
            "single_file": True,
            "pickup_s3_prefix": f"s3://{bucket}/pickup/{{batch_id}}/",
            "manifest": {"enabled": manifest_enabled, "checksum": "sha256"},
            "zip": {"enabled": zip_enabled},
        },
        "extraction": extraction,
    }
    # One target publishes as `delivery` — the exact pre-170 shape, so
    # single-target configs stay byte-identical; several publish as
    # `deliveries`. Duplicate targets are refused by the validator below.
    if len(targets) == 1:
        cfg["delivery"] = targets[0]
    else:
        cfg["deliveries"] = targets
    error = beacon_export_config.validate(cfg)
    return (None, error) if error else (cfg, None)


def _incremental_extraction_block(version: dict, extraction_in: dict) -> tuple[dict | None, str | None]:
    """Resolve the incremental extraction block (UIFPE-168): which mapping
    source the window predicate applies to (default: the first source), and
    that source's registry-owned watermark column. A source with no
    registered watermark_column cannot run incrementally — refused at
    publish, never discovered at run time. Multi-source feeds are refused
    outright (UIFPE-167): the window applies to ONE source before the join,
    so changes in the others would never ship — the wizard already forces
    full load for them, and this makes an API-authored draft honour the
    same rule until per-source watermarks land (UIFPE-238)."""
    from control_panel import registries

    sources_cfg = (version.get("mapping_config") or {}).get("sources") or []
    if not sources_cfg:
        return None, "incremental extraction needs at least one mapping source"
    if len(sources_cfg) > 1:
        names = ", ".join(str(s.get("name")) for s in sources_cfg)
        return None, (f"incremental extraction is single-source only (UIFPE-238): "
                      f"this feed reads {len(sources_cfg)} sources ({names}) — "
                      "publish it as a full load until multi-source watermarks land")
    alias = extraction_in.get("source") or sources_cfg[0].get("name")
    src = next((s for s in sources_cfg if s.get("name") == alias), None)
    if src is None:
        return None, (f"extraction.source {alias!r} is not one of the "
                      "mapping config's sources")
    # UIFPE-229: registry row first (sources.yaml), else detected live from
    # the Glue catalog (the platform CDC column) for catalog-picked tables
    watermark_column = registries.watermark_column_for_path(src.get("path"))
    if not watermark_column:
        return None, (f"source {alias!r} ({src.get('path')}) does not support "
                      "incremental extraction — no watermark column is "
                      "registered for it or present in the gold catalog")
    return {"mode": "incremental", "source": alias,
            "watermark_column": watermark_column}, None


#: Fallback transform sizing when the config table is absent or empty. These
#: mirror the Glue job definition's own settings, so a run behaves the same
#: whether or not the table is reachable — the table exists to retune WITHOUT
#: a deploy (BEACON_TABLE_GLUE_CONFIG), not to be a hard dependency.
DEFAULT_JOB_CONFIG = {"worker_type": "G.1X", "workers": 2, "timeout_minutes": 60}


def job_config(config_key: str = "default") -> dict:
    """Transform sizing for a batch: worker type, worker count, timeout.

    Read at run-start and passed into the Glue job run as StartJobRun
    overrides. An operator retunes a slow export by editing the item; nothing
    is redeployed. A missing table, missing item or unreadable value falls
    back to DEFAULT_JOB_CONFIG rather than failing the run — sizing is an
    optimization, not a correctness input.
    """
    table = os.environ.get("BEACON_TABLE_GLUE_CONFIG")
    if not table:
        return dict(DEFAULT_JOB_CONFIG)
    try:
        import boto3
        item = boto3.client("dynamodb").get_item(
            TableName=table,
            Key={"config_key": {"S": config_key}}).get("Item") or {}
        if not item and config_key != "default":
            return job_config()  # per-export row absent -> the default row
        return {
            "worker_type": item.get("worker_type", {}).get(
                "S", DEFAULT_JOB_CONFIG["worker_type"]),
            "workers": int(item.get("workers", {}).get(
                "N", DEFAULT_JOB_CONFIG["workers"])),
            "timeout_minutes": int(item.get("timeout_minutes", {}).get(
                "N", DEFAULT_JOB_CONFIG["timeout_minutes"])),
        }
    except Exception:
        return dict(DEFAULT_JOB_CONFIG)


def start_run(job_id: str, batch_id: str,
              export_config_uri: str | None = None,
              extraction: dict | None = None) -> dict:
    """Trigger a pipeline batch via the Step Functions export state machine
    (execution name = batch id, so the whole flow shares it). D22: the caller
    passes the PUBLISHED VERSION's export config URI — what you approved is
    what runs. The env fallback (BEACON_EXPORT_CONFIG_URI) remains for
    pinned/demo jobs only. `extraction` is the version's settings.extraction
    (UIFPE-168) — an incremental feed's stored watermark rides the execution
    input; the scheduled_starter Lambda is the lockstep twin of that logic."""
    if not runs_enabled():
        return {"started": False,
                "reason": "no run target configured "
                          "(BEACON_STATE_MACHINE_ARN) — runs disabled"}
    config_uri = export_config_uri or os.environ.get("BEACON_EXPORT_CONFIG_URI")
    if not config_uri:
        return {"started": False,
                "reason": "this version has no published export config and no "
                          "BEACON_EXPORT_CONFIG_URI fallback is set"}
    _, sfn = _clients()

    # Transform sizing travels IN the execution input, so it is visible on the
    # execution itself when someone asks why a batch was slow, and the state
    # machine passes it straight through as StartJobRun overrides. Per-export
    # sizing later = key this on job_id instead of "default".
    sizing = job_config()
    # UIFPE-168: like job_config and trigger, the watermark key is always
    # supplied — "" means full extract (or an incremental feed's baseline).
    watermark = ""
    if (extraction or {}).get("mode") == "incremental":
        from control_panel import extraction_state
        watermark = extraction_state.watermark(job_id)
    response = sfn.start_execution(
        stateMachineArn=os.environ["BEACON_STATE_MACHINE_ARN"],
        name=batch_id,
        # trigger tells the run history WHO started this batch (UIFPE-206:
        # manual = an operator watching the screen, scheduled = the starter
        # Lambda). Part of the input contract like job_config — every starter
        # supplies it.
        input=json.dumps({"export_config_s3_path": config_uri,
                          "job_config": sizing,
                          "trigger": "manual",
                          "watermark": watermark}),
    )
    return {"started": True, "engine": "stepfunctions",
            "run_id": response["executionArn"], "batch_id": batch_id,
            "export_config": config_uri, "job_config": sizing,
            "watermark": watermark}


def batch_files(job_id: str, batch_id: str,
                expires_seconds: int = 900) -> dict:
    """Downloadable files for a completed batch: list the batch's pickup
    prefix and mint short-lived presigned GET URLs. This is the sanctioned
    browser-download path — the API authorizes and signs; the browser never
    holds AWS credentials (D19).

    The batch manifest is an internal packaging/lineage artifact
    (checksums + version stamps) for partner delivery and audit — it is
    excluded here entirely, so no presigned URL for it ever exists
    (UIFPE-202). Partner-facing delivery still ships it, manifest-last."""
    from beacon_export.packaging import is_manifest_name

    if not enabled():
        return {"enabled": False, "files": [],
                "reason": "BEACON_DATA_BUCKET not configured"}
    s3, _ = _clients()
    bucket = os.environ["BEACON_DATA_BUCKET"]
    prefix = os.environ.get("BEACON_PICKUP_PREFIX", "pickup").strip("/")
    key_prefix = f"{prefix}/{batch_id}/"
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=key_prefix)
    files = []
    for obj in listing.get("Contents", []):
        name = obj["Key"].removeprefix(key_prefix)
        if is_manifest_name(name):
            continue
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": obj["Key"]},
            ExpiresIn=expires_seconds)
        files.append({"name": name, "bytes": obj["Size"], "url": url,
                      "expires_seconds": expires_seconds})
    return {"enabled": True, "job_id": job_id, "batch_id": batch_id,
            "files": files}


def stop_run(run_id: str) -> dict:
    """Stop a RUNNING batch (UIFPE-203): abort the Step Functions execution
    AND the transform work it spawned — StopExecution alone leaves Spark
    running to completion on our bill. Delivery is fail-closed either way:
    deliver is a later state of the aborted execution, so nothing can reach
    the destination after a stop.

    The transform is a Glue job run (UIFPE-209); the ECS stop leg was removed
    with the Fargate pipeline at UIFPE-215's probation exit — no execution
    predating the switch can still be running."""
    if not runs_enabled():
        return {"stopped": False,
                "reason": "no run target configured "
                          "(BEACON_STATE_MACHINE_ARN) — runs disabled"}
    _, sfn = _clients()
    execution = sfn.describe_execution(executionArn=run_id)
    if execution["status"] != "RUNNING":
        return {"stopped": False,
                "reason": f"execution is {execution['status']} — only a "
                          "RUNNING batch can be stopped"}

    transform = _running_transform(sfn, run_id)
    sfn.stop_execution(executionArn=run_id, error="StoppedByUser",
                       cause="stopped from the control panel")
    task_stopped = False
    if transform:
        job_name, job_run_id = transform
        try:
            _glue_client().batch_stop_job_run(JobName=job_name,
                                              JobRunIds=[job_run_id])
            task_stopped = True
        except Exception:
            pass  # best effort — the aborted execution already fail-closes delivery

    # identity for the run record: the execution input carries the config path
    from beacon_export.lambdas.job_identity import resolve_job_id
    job_id = resolve_job_id(json.loads(execution.get("input") or "{}"))
    # UIFPE-720: the stopped row keeps the execution's start, like record_run's
    started = execution.get("startDate")
    return {"stopped": True, "task_stopped": task_stopped, "job_id": job_id,
            "batch_id": execution.get("name") or run_id.rsplit(":", 1)[-1],
            "started_at": started.isoformat() if hasattr(started, "isoformat") else started}


def _running_transform(sfn, run_id: str):
    """The Glue job run the .sync Transform state submitted, from the
    execution history (the describe call does not expose it). Returns
    (job_name, job_run_id), or None when nothing identifiable was
    submitted yet."""
    try:
        history = sfn.get_execution_history(executionArn=run_id,
                                            reverseOrder=True, maxResults=200)
        for event in history.get("events", []):
            details = event.get("taskSubmittedEventDetails") or {}
            if details.get("resourceType") == "glue":
                # StartJobRun's output carries only the run id, so the job
                # name comes from configuration (same pattern as every other
                # runtime target here), not from parsing the history.
                output = json.loads(details.get("output") or "{}")
                job_run = output.get("JobRunId") or output.get("Id")
                job_name = os.environ.get("BEACON_TRANSFORM_JOB_NAME")
                if job_run and job_name:
                    return job_name, job_run
    except Exception:
        pass  # best effort only
    return None


def run_status(run_id: str) -> dict:
    if not runs_enabled():
        return {"state": "UNKNOWN", "reason": "no run target configured"}
    _, sfn = _clients()
    execution = sfn.describe_execution(executionArn=run_id)
    state = execution["status"]  # RUNNING|SUCCEEDED|FAILED|TIMED_OUT|ABORTED
    return {"state": state, "error": execution.get("cause")}
