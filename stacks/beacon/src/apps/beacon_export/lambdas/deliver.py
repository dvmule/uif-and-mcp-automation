# deliver.py — Step Functions deliver stage (runs ONLY on the success branch:
# a gate breach in the ECS task means this function is never invoked).
#
# Event (state input):
#   {"export_config_s3_path": "s3://.../export.json", "batch_id": "b-0143"}
#
# Self-sufficient by design: it reads the export config, lists the batch's
# pickup prefix, and copies via beacon.s3_delivery — no file list is plumbed
# through the state machine, so a retry after a partial delivery just
# re-lists and re-copies (idempotent, batch-unique names).

from __future__ import annotations

import json
import os

from beacon_export import (email_delivery, export_config, s3_delivery,
                           transfer_family_delivery)


def handler(event: dict, context=None, *, s3_client=None,
            secrets_client=None, transfer_client=None) -> dict:
    # UIFPE-440: the s3 connection test runs HERE so it executes under THIS
    # function's role — the identity partners grant in their bucket policy;
    # a test under any other role would prove nothing about delivery. The
    # sftp test stays with Transfer Family (UIFPE-438) and a stray legacy
    # invoke still fails loudly.
    if event.get("action") == "connection_test":
        if event.get("channel") == "s3":
            if s3_client is None:
                import boto3
                s3_client = boto3.client("s3")
            return _connection_test_s3(event["destination_s3_prefix"], s3_client)
        if event.get("channel") == "email":
            # UIFPE-444: a real mail through the real pipes — "verified"
            # means the Notifications API accepted the event
            return _email_adapter(secrets_client).send_test(event["recipients"])
        raise NotImplementedError(
            "the in-Lambda sftp connection test was retired with the "
            "Paramiko path (UIFPE-438) — connector destinations test via "
            "the Transfer Family TestConnection API")

    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")

    config_uri = event["export_config_s3_path"]
    batch_id = event["batch_id"]

    bucket, key = s3_delivery.split_uri(config_uri)
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    cfg = export_config.load(body)

    pickup_prefix = cfg["packaging"]["pickup_s3_prefix"].replace("{batch_id}", batch_id)
    pickup_bucket, pickup_key_prefix = s3_delivery.split_uri(
        pickup_prefix.rstrip("/") + "/")
    listing = s3_client.list_objects_v2(Bucket=pickup_bucket,
                                        Prefix=pickup_key_prefix)
    names = [obj["Key"].removeprefix(pickup_key_prefix)
             for obj in listing.get("Contents", [])]
    if not names:
        raise RuntimeError(f"nothing to deliver: pickup prefix {pickup_prefix} "
                           "is empty — did the packaging stage run?")
    # Deterministic order with the manifest LAST: a partner polling the remote
    # dir must never see a manifest describing files that have not landed yet
    # (S3 listings are alphabetical, which would often put it first).
    names.sort(key=lambda n: (n.endswith(".manifest.json"), n))

    # UIFPE-170: one batch, one or many targets. Every target gets attempted —
    # one partner's outage must not hide the others' deliveries — and the
    # batch fails loudly if ANY target failed (no silent partial success).
    # A retry re-delivers every target: idempotent by construction (batch-
    # unique names, sftp tmp-then-rename, s3 copies overwrite).
    targets = export_config.delivery_targets(cfg)
    outcomes = []
    total_results: list = []
    for index, dlv in enumerate(targets):
        label = {"target": index, "channel": dlv.get("channel")}
        if dlv.get("destination_id"):
            label["destination_id"] = dlv["destination_id"]
        try:
            results = _deliver_one(
                dlv, cfg=cfg, batch_id=batch_id, names=names,
                pickup_prefix=pickup_prefix, pickup_bucket=pickup_bucket,
                pickup_key_prefix=pickup_key_prefix, s3_client=s3_client,
                secrets_client=secrets_client,
                transfer_client=transfer_client)
        except Exception as e:  # noqa: BLE001 — recorded per target, re-raised below
            outcomes.append({**label, "status": "failed",
                             "error": f"{type(e).__name__}: {e}"})
        else:
            total_results.extend(results)
            outcomes.append({**label, "status": "delivered",
                             "delivered": len(results)})

    failed = [o for o in outcomes if o["status"] == "failed"]
    if failed:
        raise RuntimeError(
            f"delivery failed for {len(failed)} of {len(targets)} target(s): "
            + json.dumps(outcomes))

    return {"job_id": cfg["job_id"], "batch_id": batch_id,
            "delivered": len(total_results), "targets": outcomes,
            "delivery": total_results}


def _email_adapter(secrets_client) -> "email_delivery.PearsonNotificationsAdapter":
    """UIFPE-444: the Notifications adapter, wired from this deployment's
    env. Refuses honestly when the wiring is absent (local dev, or an env
    where the notifications integration is not configured yet)."""
    for var in ("BEACON_NOTIFICATIONS_SECRET", "BEACON_NOTIFICATIONS_API_URL",
                "BEACON_NOTIFICATIONS_TOKEN_URL", "BEACON_PANEL_URL"):
        if not os.environ.get(var):
            raise RuntimeError(
                f"email delivery is not configured on this deployment ({var} "
                "is unset) — the Pearson Notifications wiring comes from "
                "Terraform (UIFPE-444)")
    if secrets_client is None:
        import boto3
        secrets_client = boto3.client("secretsmanager")
    return email_delivery.PearsonNotificationsAdapter(
        secrets_client,
        secret_ref=os.environ["BEACON_NOTIFICATIONS_SECRET"],
        api_url=os.environ["BEACON_NOTIFICATIONS_API_URL"],
        token_url=os.environ["BEACON_NOTIFICATIONS_TOKEN_URL"],
        panel_url=os.environ["BEACON_PANEL_URL"])


def _connection_test_s3(destination_s3_prefix: str, s3_client) -> dict:
    """UIFPE-440: write-then-delete a marker under the destination prefix —
    proves the partner's bucket policy admits this role BEFORE the first real
    batch finds out. Failures come back as readable text, never a stack
    trace; a write that never succeeded leaves nothing behind."""
    bucket, key_prefix = s3_delivery.split_uri(
        destination_s3_prefix.rstrip("/") + "/")
    marker = key_prefix + ".beacon-connection-test"
    try:
        s3_client.put_object(Bucket=bucket, Key=marker,
                             Body=b"beacon connection test - safe to delete")
    except Exception as e:  # noqa: BLE001 — the error IS the result
        return {"ok": False, "error": _s3_test_error("write to", bucket, e)}
    try:
        s3_client.delete_object(Bucket=bucket, Key=marker)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": (
            _s3_test_error("clean up the test marker in", bucket, e)
            + f" — {marker!r} was written but not removed; the partner's "
              "policy must allow s3:DeleteObject as well as s3:PutObject")}
    return {"ok": True}


def _s3_test_error(verb: str, bucket: str, e: Exception) -> str:
    detail = f"{type(e).__name__}: {e}"
    if "kms" in detail.lower():
        hint = (" (the bucket enforces a KMS key this role cannot use — "
                "cross-account KMS buckets are not supported yet)")
    elif "AccessDenied" in detail or "Forbidden" in detail or "403" in detail:
        hint = (" (usually the partner's bucket policy does not allow the "
                "Beacon deliver role yet — apply the policy snippet from the "
                "destination's page)")
    else:
        hint = ""
    return f"could not {verb} bucket {bucket!r}: {detail}{hint}"


def _deliver_one(dlv: dict, *, cfg: dict, batch_id: str, names: list,
                 pickup_prefix: str, pickup_bucket: str,
                 pickup_key_prefix: str, s3_client,
                 secrets_client, transfer_client=None) -> list:
    """Deliver one batch's files to ONE target; returns the per-file results.
    Exactly the pre-170 single-destination behavior, called per target."""
    if dlv["channel"] not in ("s3", "email", "sftp"):
        raise NotImplementedError(
            f"deliver Lambda supports channels 's3'/'email'/'sftp' "
            f"(got {dlv['channel']!r})")

    if dlv["channel"] == "sftp":
        # UIFPE-339: connector destinations hand the S3 paths to Transfer
        # Family and poll for completion — this Lambda does not speak SFTP.
        # Idempotent: a retry re-sends the same batch-unique names, which
        # overwrite at the destination. The in-Lambda Paramiko adapter was
        # retired at UIFPE-438 (owner call, probation waived) — an immutable
        # pre-connector version that still says paramiko fails loudly here;
        # the fix is a republish, which picks up the destination's connector.
        if dlv.get("adapter") != "transfer_family":
            raise NotImplementedError(
                f"sftp adapter {dlv.get('adapter', 'paramiko')!r} was retired "
                "(UIFPE-438) — republish the export so its delivery rides the "
                "destination's Transfer Family connector")
        if transfer_client is None:
            import boto3
            transfer_client = boto3.client("transfer")
        return transfer_family_delivery.deliver(
            transfer_client, connector_id=dlv["connector_id"],
            pickup_bucket=pickup_bucket,
            pickup_key_prefix=pickup_key_prefix, names=names,
            remote_dir=dlv["remote_dir"])

    if dlv["channel"] == "email":
        # UIFPE-444: email rides the Pearson Notifications API. The SES
        # ses_link adapter is retired — an immutable pre-444 version that
        # still says ses_link fails loudly here; the fix is a republish,
        # which stamps the pearson_notifications adapter.
        if dlv.get("adapter", "ses_link") != "pearson_notifications":
            raise NotImplementedError(
                f"email adapter {dlv.get('adapter', 'ses_link')!r} (SES) was "
                "retired (UIFPE-444) — republish the export so its delivery "
                "rides the Pearson Notifications API")
        return _email_adapter(secrets_client).execute(
            names, recipients=dlv["recipients"], job_id=cfg["job_id"],
            export_name=dlv.get("export_name") or cfg["job_id"],
            batch_id=batch_id)

    plan = s3_delivery.plan_delivery(
        names, pickup_s3_prefix=pickup_prefix,
        destination_s3_prefix=dlv["destination_s3_prefix"])
    return s3_delivery.S3Adapter(s3_client).execute(plan)
