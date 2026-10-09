#!/usr/bin/env python3
"""Register draft configs preserving enabled/schedule from JSON (unlike register_eds_drafts.py)."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import sys

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402


def to_dynamo(obj):
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return Decimal(str(obj))
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [to_dynamo(v) for v in obj]
    return obj


def prepare(cfg: dict) -> dict:
    cfg = json.loads(json.dumps(cfg))
    cfg["version"] = "latest"
    meta = cfg.get("metadata") or {}
    meta.pop("draft_notes", None)
    meta["registered_at"] = meta.get("registered_at") or datetime.now(timezone.utc).isoformat()
    meta["tags"] = [t for t in meta.get("tags", []) if t != "draft"]
    cfg["metadata"] = meta
    return cfg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="dev", choices=list(sn.ENVIRONMENTS))
    p.add_argument("files", nargs="+")
    args = p.parse_args()
    table = boto3.resource("dynamodb", region_name="eu-west-1").Table(
        sn.load(args.env).config_table
    )
    for fpath in args.files:
        cfg = prepare(json.loads(Path(fpath).read_text(encoding="utf-8")))
        table.put_item(Item=to_dynamo(cfg))
        inc = (cfg.get("glue") or {}).get("incremental") or {}
        print(
            f"REGISTERED {cfg['pipeline_id']}: enabled={cfg.get('enabled')} "
            f"schedule={(cfg.get('schedule') or {}).get('frequency')} "
            f"inc={inc.get('worker_type')}x{inc.get('num_workers')}"
        )


if __name__ == "__main__":
    main()
