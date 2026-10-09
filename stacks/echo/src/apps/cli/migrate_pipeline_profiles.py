#!/usr/bin/env python3
# migrate_pipeline_profiles.py
# One-shot migrator: rewrites legacy flat pipeline configs into the
# two-profile shape (extract.initial / extract.incremental and
# glue.initial / glue.incremental). Writes via the controller API so
# validators run and a malformed transform fails closed.
#
# Idempotent: skips pipelines that already have extract.initial.
# Snapshots pre-migration configs to migrations/backup_<UTC>.json for
# easy per-pipeline rollback (one PUT each).
#
# Usage:
#   eval $(/Users/raj9karu/.aws/aws-runas -e pearson-esb-dev)
#   python3 scripts/migrate_pipeline_profiles.py --env dev                # dry-run
#   python3 scripts/migrate_pipeline_profiles.py --env dev --apply        # write
#   python3 scripts/migrate_pipeline_profiles.py --env dev --apply \
#       --pipeline-id pearson-product-EGO_MTL_SY_ITEMS_EXT_B               # single
#
# Background: see /Users/raj9karu/.claude/plans/precious-wishing-penguin.md

import argparse
import difflib
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

# Was a hand-written map with no uat entry at all, so `--env uat` resolved
# to "" and the script failed on a request it should never have attempted.
API_URLS = sn.api_endpoints()

LEAN_INC_EXTRACT = {
    "jdbc_partition_col": None,
    "jdbc_lower_bound":   None,
    "jdbc_upper_bound":   None,
    "jdbc_partitions":    None,
    "jdbc_predicates":    None,
}
LEAN_INC_GLUE = {"worker_type": "G.1X", "num_workers": 1, "timeout_minutes": 60}

SOURCE_CONTRACT = {
    "table", "mode", "watermark_col", "watermark_fallback_col", "watermark_value", "columns", "custom_sql",
}
EXTRACT_JDBC_KEYS = {
    "jdbc_partition_col", "jdbc_lower_bound", "jdbc_upper_bound",
    "jdbc_partitions", "jdbc_predicates", "jdbc_fetchsize", "jdbc_read_timeout_ms",
}


def _curl(args: list[str]) -> str:
    """Run curl with --fail-with-body so a non-2xx surfaces the response body
    in stderr without us having to parse HTTP codes. Returns stdout text.

    Using curl rather than urllib because Python.org's macOS Python doesn't
    trust the system CA bundle by default — curl uses the OS trust store.
    """
    cmd = ["curl", "-sS", "--fail-with-body", *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"curl {' '.join(args)} failed: {out.stderr or out.stdout}")
    return out.stdout


def http_get(url: str) -> dict:
    return json.loads(_curl([url]))


def http_put(url: str, body: dict) -> dict:
    data = json.dumps(body)
    out = _curl([
        "-X", "PUT", url,
        "-H", "Content-Type: application/json",
        "--data-binary", data,
    ])
    return json.loads(out) if out.strip() else {}


def already_migrated(cfg: dict) -> bool:
    ext = cfg.get("extract") or {}
    g   = cfg.get("glue")    or {}
    return bool(ext.get("initial") or ext.get("incremental")
                or g.get("initial") or g.get("incremental"))


def transform(cfg: dict) -> dict:
    """Return a new config in the two-profile shape.

    extract.initial   <- current flat JDBC keys (predicates, partitions, ...)
    extract.incremental <- lean defaults (predicates=null, partitions=null,
                           but keep operator-set fetchsize/timeouts because
                           they're per-connection knobs unrelated to fan-out)
    glue.initial      <- current flat glue.*
    glue.incremental  <- lean defaults
    """
    new = json.loads(json.dumps(cfg))  # deep copy

    ext = new.get("extract") or {}
    new_ext: dict = {}
    initial_ext: dict = {}
    incremental_ext: dict = dict(LEAN_INC_EXTRACT)

    for k, v in ext.items():
        if k in SOURCE_CONTRACT:
            new_ext[k] = v
        elif k in EXTRACT_JDBC_KEYS:
            initial_ext[k] = v
            if k in ("jdbc_fetchsize", "jdbc_read_timeout_ms"):
                incremental_ext[k] = v
        else:
            new_ext[k] = v  # passthrough unknown keys

    new_ext["initial"]     = initial_ext
    new_ext["incremental"] = incremental_ext
    new["extract"] = new_ext

    g = new.get("glue") or {}
    new_glue: dict = {}
    initial_glue: dict = {}
    for k, v in g.items():
        if k in ("worker_type", "num_workers", "timeout_minutes"):
            initial_glue[k] = v
        else:
            new_glue[k] = v
    new_glue["initial"]     = initial_glue or {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60}
    new_glue["incremental"] = dict(LEAN_INC_GLUE)
    new["glue"] = new_glue

    return new


def pretty_diff(before: dict, after: dict, pid: str) -> str:
    b = json.dumps(before, indent=2, sort_keys=True, default=str).splitlines()
    a = json.dumps(after,  indent=2, sort_keys=True, default=str).splitlines()
    return "\n".join(difflib.unified_diff(b, a, fromfile=f"{pid} (before)",
                                          tofile=f"{pid} (after)", lineterm=""))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--env", choices=list(sn.ENVIRONMENTS), default="dev")
    p.add_argument("--apply", action="store_true",
                   help="actually PUT the new shape (default: dry-run)")
    p.add_argument("--pipeline-id", help="target a single pipeline_id (default: all)")
    p.add_argument("--backup-dir", default="migrations",
                   help="directory for pre-migration snapshots (default: migrations/)")
    p.add_argument("--api-url", help="override API base URL")
    args = p.parse_args()

    api = args.api_url or API_URLS.get(args.env)
    if not api:
        raise SystemExit(f"No API URL configured for env={args.env}")

    list_url = f"{api}/pipelines"
    print(f"GET  {list_url}", file=sys.stderr)
    listing = http_get(list_url)
    items = listing.get("pipelines") if isinstance(listing, dict) else listing
    if items is None:
        raise SystemExit(f"Unexpected list response: {json.dumps(listing)[:200]}")

    if args.pipeline_id:
        items = [c for c in items if c.get("pipeline_id") == args.pipeline_id]
        if not items:
            raise SystemExit(f"pipeline_id={args.pipeline_id} not found")

    backup_dir = pathlib.Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"backup_{args.env}_{stamp}.json"

    skipped: list[str] = []
    planned: list[str] = []
    applied: list[str] = []
    failed:  list[tuple[str, str]] = []
    snapshot: dict[str, dict] = {}

    for cfg in items:
        pid = cfg.get("pipeline_id")
        if not pid:
            continue
        if already_migrated(cfg):
            skipped.append(pid)
            print(f"[SKIP] {pid}: already migrated", file=sys.stderr)
            continue

        new_cfg = transform(cfg)
        diff = pretty_diff(cfg, new_cfg, pid)
        print(f"\n----- {pid} -----")
        print(diff or "(no changes)")

        if not args.apply:
            planned.append(pid)
            continue

        snapshot[pid] = cfg
        new_cfg.pop("metadata", None)
        put_url = f"{api}/pipelines/{pid}"
        try:
            http_put(put_url, new_cfg)
            applied.append(pid)
            print(f"[APPLIED] {pid}", file=sys.stderr)
        except SystemExit as e:
            failed.append((pid, str(e)))
            print(f"[FAILED]  {pid}: {e}", file=sys.stderr)

    if args.apply and snapshot:
        backup_path.write_text(json.dumps(snapshot, indent=2, default=str))
        print(f"\nPre-migration snapshot: {backup_path}", file=sys.stderr)

    print(
        f"\nDone. dry-run-planned={len(planned)} applied={len(applied)} "
        f"skipped={len(skipped)} failed={len(failed)}",
        file=sys.stderr,
    )
    if failed:
        for pid, err in failed:
            print(f"  - {pid}: {err}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
