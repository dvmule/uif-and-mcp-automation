# beacon_export_job.py
# Stage driver for a Beacon export — generic across domains; the domain and
# dataset come entirely from the configs:
#   transform (dynamic-mapping engine) -> quality gate -> render (CSV/JSON)
#   -> package (names, manifest, checksums) -> deliver (SFTP tmp-then-rename)
#
# The only Spark-aware module in beacon/. Entry is fargate_entrypoint.py
# (ECS Fargate PySpark container, invoked by the Step Functions state
# machine), which loads the config and calls run() below.
#
# The dynamic-mapping engine is vendored alongside this package
# (src/libs/dynamic_mapping/) and COPY'd into the same
# image. The engine writes the canonical dataset (parquet, batch-scoped
# staging prefix) and the rejected rows itself; this job reads canonical back
# for rendering — the materialized handoff that lets a render retry skip the
# transform.
#
# Volume note: delivery buffers each packaged file in memory (product-catalog
# scale, single-digit MB). The scale-out path is documented in the design doc
# (packaging S3 multipart concatenation + Transfer Family direct-from-S3).

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone

from beacon_export import (export_config, packaging, quality_gate, renderers,
                    s3_delivery)

# The watermark is spliced into a Spark SQL source predicate, so its whole
# allowed grammar is one timestamp literal (UTC, the format the engine's
# high_water_mark reports and the state table stores). \Z, not $ — Python's
# $ accepts one trailing newline, and this guard tolerates zero slack.
_WATERMARK_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,6})?\Z")


def _split_s3(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def _read_s3_text(s3, uri: str) -> str:
    bucket, key = _split_s3(uri)
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def _single_part_file(s3, prefix_uri: str, suffix_hint: str) -> tuple[str, str, int]:
    """Locate the one part-* object under a coalesce(1) output prefix.
    Returns (bucket, key, size). Fails loudly on zero or many."""
    bucket, prefix = _split_s3(prefix_uri)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    parts = [o for o in resp.get("Contents", [])
             if "part-" in o["Key"] and not o["Key"].endswith("_SUCCESS")]
    if len(parts) != 1:
        raise RuntimeError(f"expected exactly 1 part file under {prefix_uri} "
                           f"({suffix_hint}), found {len(parts)}")
    return bucket, parts[0]["Key"], parts[0]["Size"]


def report_uri(cfg: dict, batch_id: str) -> str:
    """Where the batch's report.json lives: under the canonical prefix in an
    underscore-prefixed folder, so Spark's file index ignores it when the
    render stage reads canonical back (the same trick as _rendered/)."""
    canonical = cfg["canonical_s3_prefix"].replace("{batch_id}", batch_id)
    return canonical.rstrip("/") + "/_report/report.json"


def _write_report(s3, cfg: dict, batch_id: str, summary: dict) -> None:
    bucket, key = _split_s3(report_uri(cfg, batch_id))
    s3.put_object(Bucket=bucket, Key=key,
                  Body=json.dumps(summary).encode("utf-8"),
                  ContentType="application/json")


def _xml_nest(df):
    """Assemble path-shaped canonical columns into the nested structs
    spark-xml serializes (UIFPE-557). `A/B` becomes element B inside A;
    `A@x` becomes attribute x on A (spark-xml's `_x` field); an element with
    both a value and attributes carries its text as `_VALUE`. A frame with
    no path-shaped columns is returned untouched — the legacy fast path, so
    pre-feature exports render byte-identical."""
    from dynamic_mapping import xml_paths
    if not xml_paths.any_path(df.columns):
        return df
    from pyspark.sql import functions as F

    def assemble(node):
        fields = []
        for attr, column in sorted(node["attrs"].items()):
            fields.append(df[column].alias(xml_paths.ATTRIBUTE_PREFIX + attr))
        if node["value"] is not None and (node["attrs"] or node["children"]):
            fields.append(df[node["value"]].alias(xml_paths.VALUE_TAG))
        for seg, child in node["children"].items():
            if child["value"] is not None and not child["attrs"] and not child["children"]:
                fields.append(df[child["value"]].alias(seg))
            else:
                fields.append(assemble(child).alias(seg))
        return F.struct(*fields)

    plan = xml_paths.build_plan(df.columns)
    out = []
    for seg, child in plan["children"].items():
        if child["value"] is not None and not child["attrs"] and not child["children"]:
            out.append(df[child["value"]].alias(seg))
        else:
            out.append(assemble(child).alias(seg))
    return df.select(*out)


def _formula_escape(df, columns):
    """Prefix string cells that would execute as formulas in Excel (CSV only)."""
    from pyspark.sql import functions as F
    for name, dtype in df.dtypes:
        if dtype == "string" and name in columns:
            col = F.col(name)
            df = df.withColumn(
                name,
                F.when(col.rlike(r"^[=+\-@\t]"), F.concat(F.lit("'"), col))
                 .otherwise(col))
    return df


def run(spark, s3, secrets, cfg: dict, batch_id: str,
        previous_output_rows: int | None = None, deliver: bool = True,
        watermark: str | None = None) -> dict:
    """Drive all stages; returns the batch summary dict. Clients (s3, secrets)
    are injected so a test harness can fake them.

    deliver=False stops after packaging (strict Step Functions topology:
    the deliver Lambda owns the delivery stage; the monolithic path keeps
    deliver=True).

    watermark is the stored high-water mark for an incremental feed
    (UIFPE-168), supplied per run by the starter — never by the immutable
    config (D22). None/empty on an incremental feed = the baseline run: a
    full extract that still reports a high-water mark to seed the state."""
    import dynamic_mapping

    batch_started = datetime.now(timezone.utc)
    summary: dict = {"job_id": cfg["job_id"], "batch_id": batch_id,
                     "started_at": batch_started.strftime("%Y-%m-%dT%H:%M:%SZ")}

    # -- 1. transform: dynamic-mapping engine ------------------------------
    # UIFPE-168: a missing extraction key means today's full-load behavior
    # (the UIFPE-172 convention — published pre-feature configs never change
    # meaning). Incremental mode narrows the source read with a strictly-
    # greater watermark predicate and tracks max(watermark_column) so the
    # success branch can advance the stored state.
    extraction = cfg.get("extraction") or {}
    incremental_configured = extraction.get("mode") == "incremental"
    if watermark and not incremental_configured:
        raise ValueError("a watermark was supplied but this config's "
                         "extraction mode is not incremental")
    apply_window = bool(incremental_configured and watermark)
    if apply_window and not _WATERMARK_SHAPE.match(watermark):
        raise ValueError(f"watermark {watermark!r} is not a plain UTC "
                         "timestamp (YYYY-MM-DD HH:MM:SS[.ffffff]) — refusing "
                         "to splice it into the source predicate")
    source_filters = track_max = None
    if incremental_configured:
        track_max = {"source": extraction["source"],
                     "column": extraction["watermark_column"]}
        if apply_window:
            source_filters = {extraction["source"]:
                              f"{extraction['watermark_column']} > TIMESTAMP '{watermark}'"}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write(_read_s3_text(s3, cfg["mapping_config_s3_path"]))
        mapping_path = tmp.name
    mapping_cfg = dynamic_mapping.load_config(mapping_path)
    report = dynamic_mapping.run(spark, mapping_cfg, batch_id,
                                 config_path=cfg["mapping_config_s3_path"],
                                 source_filters=source_filters,
                                 track_max=track_max)
    summary["transform"] = report.to_dict()
    if incremental_configured:
        summary["extraction"] = {
            "mode": "incremental",
            # what this batch actually did: the windowed extract, or the
            # baseline full extract that seeds the watermark
            "applied": "incremental" if apply_window else "full_baseline",
            "watermark_in": watermark or None,
            "high_water_mark": report.high_water_mark,
        }

    # -- 2. quality gate: fail closed before anything renders --------------
    gate = quality_gate.evaluate(report.to_dict(), cfg["quality_gates"],
                                 previous_output_rows,
                                 incremental=apply_window)
    summary["quality_gate"] = gate.to_dict()
    if not gate.passed:
        summary["status"] = "aborted_gate_breach"
        print(json.dumps(summary))
        raise RuntimeError("quality gate breached; nothing rendered or "
                           f"delivered: {gate.breaches}")

    # -- 3. render: one canonical read, N formats --------------------------
    canonical_prefix = cfg["canonical_s3_prefix"].replace("{batch_id}", batch_id)
    canonical = spark.read.parquet(canonical_prefix)
    render_root = canonical_prefix.rstrip("/") + "/_rendered"
    rendered: list[dict] = []
    for rendering in cfg["renderings"]:
        fmt = rendering["format"]
        # UIFPE-237: a complex-typed canonical column would die inside
        # Spark's csv writer with an opaque AnalysisException — after the
        # gate has already passed. Refuse it here with the remedy instead.
        unrenderable = renderers.complex_typed_columns(fmt, canonical.dtypes)
        if unrenderable:
            raise RuntimeError(
                f"rendering format {fmt!r} cannot serialize complex-typed "
                f"column(s) {unrenderable} — map them through to_json(...) "
                "in the mapping config")
        out_prefix = f"{render_root}/{fmt}/"
        df = canonical
        if renderers.formula_escape_enabled(rendering):
            df = _formula_escape(df, set(df.columns))
        # UIFPE-557: path-shaped targets nest the XML — assembled here, right
        # before the writer, so the canonical stays flat for every other
        # format and for the report. No path targets = no reshaping at all.
        if fmt == "xml":
            df = _xml_nest(df)
        (df.coalesce(1)
           .write.mode("overwrite")
           .options(**renderers.spark_write_options(rendering))
           .format(renderers.REGISTRY[fmt]["spark_format"])
           .save(out_prefix))
        rendered.append({"rendering": rendering, "prefix": out_prefix})

    # -- 4. package: final names, checksums, manifest, pickup zone ---------
    pickup_prefix = cfg["packaging"]["pickup_s3_prefix"].replace("{batch_id}", batch_id)
    pickup_bucket, pickup_key_prefix = _split_s3(pickup_prefix.rstrip("/") + "/")
    pattern = cfg["packaging"]["filename_pattern"]
    # UIFPE-172: a missing zip key means today's uncompressed behavior —
    # published configs are immutable, so pre-feature versions must never
    # change shape on an engine upgrade. Default-on lives in the publisher.
    zip_enabled = bool(cfg["packaging"].get("zip", {}).get("enabled", False))
    files: list[dict] = []
    for item in rendered:
        ext = renderers.extension(item["rendering"])
        final_name = packaging.render_filename(
            pattern, job_id=cfg["job_id"], batch_id=batch_id, ext=ext,
            when=batch_started)
        src_bucket, src_key, size = _single_part_file(s3, item["prefix"], ext)
        if zip_enabled:
            # the archive is named <name>.<ext>.zip and contains the file
            # under its uncompressed name; hashed and appended here, so the
            # manifest and delivery describe the zip with no further changes.
            # Whole-file buffering (~2x file size peak, put_object 5 GiB cap)
            # is fine at catalog scale; the scale-out path is R2 multipart.
            data = s3.get_object(Bucket=src_bucket, Key=src_key)["Body"].read()
            payload = packaging.zip_payload(final_name, data, when=batch_started)
            final_name = packaging.render_filename(
                pattern, job_id=cfg["job_id"], batch_id=batch_id,
                ext=ext + ".zip", when=batch_started)
            s3.put_object(Bucket=pickup_bucket,
                          Key=pickup_key_prefix + final_name, Body=payload)
            files.append({"name": final_name, "bytes": len(payload),
                          "sha256": packaging.sha256_of_chunks([payload])})
            continue
        body = s3.get_object(Bucket=src_bucket, Key=src_key)["Body"]
        digest = packaging.sha256_of_chunks(iter(lambda: body.read(1 << 20), b""))
        s3.copy_object(Bucket=pickup_bucket,
                       Key=pickup_key_prefix + final_name,
                       CopySource={"Bucket": src_bucket, "Key": src_key})
        files.append({"name": final_name, "bytes": size, "sha256": digest})

    manifest_enabled = cfg["packaging"].get("manifest", {}).get("enabled", True)
    if manifest_enabled:
        manifest = packaging.build_manifest(
            job_id=cfg["job_id"], batch_id=batch_id, files=files,
            versions={"export_config_version": cfg["version"],
                      "mapping_config": cfg["mapping_config_s3_path"]})
        m_name = packaging.manifest_filename(pattern, job_id=cfg["job_id"],
                                             batch_id=batch_id, when=batch_started)
        m_bytes = packaging.manifest_bytes(manifest)
        s3.put_object(Bucket=pickup_bucket, Key=pickup_key_prefix + m_name,
                      Body=m_bytes)
        files.append({"name": m_name, "bytes": len(m_bytes),
                      "sha256": packaging.sha256_of_chunks([m_bytes])})
    summary["packaged_files"] = files

    # -- 5. deliver: channel adapter, manifest last -------------------------
    # NOTE: in the strict Step Functions topology this stage moves to the
    # deliver Lambda; the branch here keeps the monolithic entrypoint
    # runnable (demo/interim) using the same adapter modules the Lambda uses.
    if not deliver:
        summary["delivery"] = [{"step": "deferred", "to": "deliver-lambda",
                                "ok": True}]
        summary["status"] = "succeeded"
        # UIFPE-168: the report.json handoff through S3. The state machine
        # discards this task's output (ResultPath=null), so the success
        # branch re-reads the summary from here — record_run merges the row
        # counts into the run row and advances the incremental watermark.
        # Written only on the strict topology's success path: a failed batch
        # must never advance state, and the monolithic path has no Lambda to
        # read it.
        _write_report(s3, cfg, batch_id, summary)
        return summary

    # UIFPE-170: the multi-target fan-out lives in the deliver Lambda (the
    # strict topology). This monolithic branch is demo/interim only, so a
    # multi-target config here fails loudly rather than silently delivering
    # to one target of several.
    targets = export_config.delivery_targets(cfg)
    if len(targets) > 1:
        raise NotImplementedError(
            "multi-target delivery (deliveries list) runs in the deliver "
            "Lambda topology; the monolithic path delivers a single target")
    dlv = targets[0]
    names = [f["name"] for f in files]  # manifest is last by construction
    if dlv["channel"] == "s3":
        copy_plan = s3_delivery.plan_delivery(
            names, pickup_s3_prefix=pickup_prefix,
            destination_s3_prefix=dlv["destination_s3_prefix"])
        summary["delivery"] = s3_delivery.S3Adapter(s3).execute(copy_plan)
        summary["status"] = "succeeded"
        return summary

    if dlv["channel"] == "email":
        import os as _os
        import boto3 as _boto3
        from beacon_export import email_delivery
        adapter = email_delivery.EmailAdapter(
            s3, _boto3.client("ses"),
            from_address=dlv.get("from_address")
                         or _os.environ["BEACON_FROM_ADDRESS"])
        summary["delivery"] = adapter.execute(
            names, pickup_s3_prefix=pickup_prefix,
            recipients=dlv["recipients"], job_id=cfg["job_id"],
            batch_id=batch_id,
            expiry_hours=dlv.get("link_expiry_hours", 24))
        summary["status"] = "succeeded"
        return summary

    # UIFPE-438: the Paramiko adapter is retired — sftp rides the
    # destination's Transfer Family connector. The split pipeline's deliver
    # Lambda is the production path (D20); this monolithic branch keeps the
    # same behavior for in-job runs.
    if dlv["adapter"] != "transfer_family":
        raise NotImplementedError(
            f"sftp adapter {dlv.get('adapter')!r} was retired (UIFPE-438) — "
            "republish the export so its delivery rides the destination's "
            "Transfer Family connector")
    from beacon_export import transfer_family_delivery
    import boto3 as _boto3
    summary["delivery"] = transfer_family_delivery.deliver(
        _boto3.client("transfer"), connector_id=dlv["connector_id"],
        pickup_bucket=pickup_bucket, pickup_key_prefix=pickup_key_prefix,
        names=[f["name"] for f in files], remote_dir=dlv["remote_dir"])

    summary["status"] = "succeeded"
    return summary
