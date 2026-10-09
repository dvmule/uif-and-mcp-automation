# fargate_entrypoint.py
# The shared batch entrypoint for a Beacon export: arg parsing + main(), which
# loads the export config, takes an injected Spark session, and calls
# beacon_export_job.run(). The name survives from the ECS Fargate era (deleted
# at UIFPE-215's probation exit) so history stays greppable; since UIFPE-209
# the ONLY caller is glue_entrypoint.py, which supplies the Glue session and
# translates Glue's underscore parameters into this argv contract.
#
# Contract with the runtime:
#   - parameters arrive as command args:
#       --export-config-s3-path s3://.../export.json  --batch-id b-0143
#   - the run summary JSON is the LAST line on stdout;
#   - the return code is the branch signal: 0 = delivered, non-zero = failed
#     (a quality-gate breach fails -> the state machine's failure path
#     fires and the deliver Lambda never runs).
#
# Factories are injectable so tests exercise this file without Spark or AWS.

from __future__ import annotations

import argparse
import json
import traceback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beacon export batch (Fargate)")
    parser.add_argument("--export-config-s3-path", required=True,
                        help="s3:// URI of the export config")
    parser.add_argument("--batch-id", required=True,
                        help="unique batch id (threads through every artifact)")
    parser.add_argument("--previous-output-rows", type=int, default=None,
                        help="output_rows of the last successful batch "
                             "(enables the row-count delta warning)")
    parser.add_argument("--skip-delivery", action="store_true",
                        help="stop after packaging — the Step Functions "
                             "deliver Lambda owns the delivery stage")
    parser.add_argument("--watermark", default=None,
                        help="incremental window lower bound, exclusive "
                             "(UIFPE-168): the stored high-water mark. "
                             "Empty/absent = full extract (the baseline run "
                             "for an incremental feed)")
    return parser.parse_args(argv)


def _default_clients():
    import boto3
    return boto3.client("s3"), boto3.client("secretsmanager")


def main(argv: list[str] | None = None, *,
         session_factory,
         clients_factory=_default_clients,
         runner=None) -> int:
    # session_factory is REQUIRED: the runtime owns the Spark session (Glue's
    # comes from glue_entrypoint._glue_session). The Fargate local[*] factory —
    # and its heap/CDS/vectorization workarounds for the container JVM
    # (UIFPE-165/193) — was deleted with the pipeline at UIFPE-215; none of
    # those crashes ever reproduced on Glue's managed runtime.
    args = parse_args(argv)
    if runner is None:
        from beacon_export.beacon_export_job import run as runner  # noqa: PLC0415
    from beacon_export.beacon_export_job import _read_s3_text
    from beacon_export import export_config

    try:
        s3, secrets = clients_factory()
        cfg = export_config.load(_read_s3_text(s3, args.export_config_s3_path))
        spark = session_factory()
        summary = runner(spark, s3, secrets, cfg, args.batch_id,
                         args.previous_output_rows,
                         deliver=not args.skip_delivery,
                         # "" arrives when a starter has no stored watermark —
                         # the same baseline/full semantics as absent
                         watermark=args.watermark or None)
        print(json.dumps(summary))  # last stdout line = run report of record
        return 0
    except Exception:
        traceback.print_exc()
        return 1
